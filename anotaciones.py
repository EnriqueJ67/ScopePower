# anotaciones.py
#
# Objetos anotados sobre la vista previa del apilado.
#
# Sustituye al flujo antiguo (ScopePower_ObjetosAnotados.resolver_y_generar_imagen_con_objetos),
# que estiraba la imagen por su cuenta con el STF viejo, a resolución completa, con
# letra de tamaño fijo y dependiendo de SIMBAD. Ahora:
#
#   1. Se dibuja SOBRE la vista previa ya procesada (estilo Seestar), a la escala que
#      tenga (normalmente la mitad), así los colores y el ruido son los de siempre.
#   2. Los objetos salen del catálogo local (catalogo_objetos.csv, el de "Esta noche"),
#      que no necesita Internet. Si hay red, SIMBAD añade los objetos menores del campo,
#      con un tiempo de espera corto para no retrasar la vista previa en el campo.
#   3. Letra y grosor proporcionales al tamaño de la imagen, con borde oscuro, para
#      que se lean en el móvil.
#   4. Además de los populares, los NGC e IC del catálogo OpenNGC que ya lleva el
#      planetario (static/atlas), hasta la magnitud de [Apilado] anotar_ngc_hasta_mag:
#      galaxias débiles como NGC 6207 junto a M13, sin Internet. Van en azul claro, con
#      su elipse orientada de verdad (ejes y ángulo de posición del catálogo).
#
# El plate solving sigue siendo ASTAP sobre un FITS monocromo del apilado.

import os
import math
import socket
import logging
import subprocess
import threading
import configparser

import cv2
import numpy as np
from PIL import Image, ImageDraw
from astropy import units as u
from astropy.io import fits
from astropy.wcs import WCS
from astropy.io.fits import Header
from astropy.coordinates import SkyCoord

import esta_noche
import firma
import procesado
import catalogo_offline
from ScopePower_ObjetosAnotados import generar_fits_monocromo_para_platesolve, obtener_objetos_simbad

log = logging.getLogger("scopepower.anotaciones")

TIMEOUT_ASTAP_S = 60
TOLERANCIA_REINTENTO = 0.01          # la de serie de ASTAP es 0,007
MAX_ERROR_ESCALA = 0.10              # la focal nominal no es exacta: en M 17 la escala real queda un 2,5 % por debajo
MAX_SEPARACION_MONTURA_DEG = 5.0     # OBJCTRA/OBJCTDEC son la montura en JNow; sin sincronizar se va unos 20′
TIMEOUT_SIMBAD_S = 12
HOST_SIMBAD = ("simbad.u-strasbg.fr", 443)
_lock_simbad = threading.Lock()


def _ruta_astap():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    return cfg.get("General", "ruta_astap", fallback="")


def resolver_wcs(ruta_fits, timeout=TIMEOUT_ASTAP_S, ruta_mono=None):
    """
    Plate solving del FITS apilado con ASTAP. Devuelve (wcs, escala_arcsec_px,
    angulo_camara_deg, ruta_ini) o None. El WCS está en píxeles de la imagen
    completa (la del FITS). ruta_mono: dónde dejar el FITS monocromo que resuelve
    ASTAP (y su .ini); por defecto, junto al FITS.
    """
    ruta_mono = ruta_mono or os.path.splitext(ruta_fits)[0] + "_mono.fits"
    ruta_ini = os.path.splitext(ruta_mono)[0] + ".ini"
    generar_fits_monocromo_para_platesolve(ruta_fits, ruta_mono)
    res = resolver_ini(ruta_mono, ruta_ini, timeout, nombre=os.path.basename(ruta_fits))
    if res is None:
        return None
    wcs, escala_arcsec, angulo_cam = res
    return wcs, escala_arcsec, angulo_cam, ruta_ini


def resolver_ini(ruta_mono, ruta_ini, timeout=TIMEOUT_ASTAP_S, nombre=None, ruta_astap=None):
    """
    ASTAP sobre un FITS monocromo (con FOCALLEN, XPIXSZ y OBJCTRA en la cabecera), con el
    reintento para campos densos. Deja la solución en ruta_ini y devuelve (wcs,
    escala_arcsec_px, angulo_camara_deg) o None. Lo usan la galería y el alineado del apilado.
    """
    nombre = nombre or os.path.basename(ruta_mono)
    if not _lanzar_astap(ruta_mono, ruta_ini, timeout, ruta_astap=ruta_astap):
        return None
    res = wcs_desde_ini(ruta_ini)
    if res is None:
        # En campos muy densos de la Vía Láctea (M 17, M 11) casi ningún cuadrilátero de
        # estrellas cuadra con la tolerancia de serie (0,007) y ASTAP no resuelve; con 0,01 sí.
        # Una tolerancia mayor admite más soluciones falsas: solo vale si cuadra con la cabecera.
        log.info("ASTAP no resolvió %s; se repite con tolerancia %s", nombre, TOLERANCIA_REINTENTO)
        if not _lanzar_astap(ruta_mono, ruta_ini, timeout, f" -t {TOLERANCIA_REINTENTO}", ruta_astap=ruta_astap):
            return None
        res = wcs_desde_ini(ruta_ini)
        if res is None:
            log.warning("ASTAP no resolvió %s ni con tolerancia %s", nombre, TOLERANCIA_REINTENTO)
            return None
        motivo = _solucion_dudosa(res[0], res[1], ruta_mono)
        if motivo:
            log.warning("Solución de ASTAP con tolerancia %s descartada para %s: %s", TOLERANCIA_REINTENTO, nombre, motivo)
            if os.path.exists(ruta_ini):
                os.remove(ruta_ini)     # que nadie lea la solución falsa del .ini
            return None
        log.info("ASTAP resolvió %s con tolerancia %s", nombre, TOLERANCIA_REINTENTO)
    return res


def _lanzar_astap(ruta_mono, ruta_ini, timeout, opciones="", ruta_astap=None):
    """Lanza ASTAP sobre ruta_mono, borrando antes el .ini y el .wcs viejos. False si no terminó a tiempo."""
    for viejo in (ruta_ini, os.path.splitext(ruta_mono)[0] + ".wcs"):
        try:
            os.remove(viejo)
        except OSError:
            pass
    try:
        subprocess.run(f'"{ruta_astap or _ruta_astap()}" -f "{ruta_mono}" -solve -r 50{opciones}', shell=True,
                       capture_output=True, timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        log.warning("ASTAP no terminó en %s s (anotaciones)", timeout)
        return False


def _solucion_dudosa(wcs, escala_arcsec, ruta_mono):
    """
    Por qué no fiarse de una solución de ASTAP, o None si cuadra con la cabecera: la escala
    de placa con FOCALLEN y XPIXSZ (ya con el binning) y el centro con OBJCTRA/OBJCTDEC, que
    es donde apuntaba la montura. Sin focal ni píxel no se puede comprobar y se descarta.
    """
    hdr = fits.getheader(ruta_mono)
    try:
        focal, pixel = float(hdr["FOCALLEN"]), float(hdr["XPIXSZ"])
        esperada = 206.265 * pixel / focal
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return "la cabecera no trae FOCALLEN y XPIXSZ para comprobar la escala"
    if abs(escala_arcsec / esperada - 1.0) > MAX_ERROR_ESCALA:
        return f"escala {escala_arcsec:.2f}\"/px y la cabecera da {esperada:.2f}\"/px"
    if hdr.get("OBJCTRA") and hdr.get("OBJCTDEC"):
        try:
            montura = SkyCoord(str(hdr["OBJCTRA"]), str(hdr["OBJCTDEC"]), unit=(u.hourangle, u.deg))
        except ValueError:
            return None
        ra, dec = wcs.all_pix2world([[(hdr["NAXIS1"] - 1) / 2.0, (hdr["NAXIS2"] - 1) / 2.0]], 0)[0]
        separacion = montura.separation(SkyCoord(ra * u.deg, dec * u.deg)).deg
        if separacion > MAX_SEPARACION_MONTURA_DEG:
            return f"centro a {separacion:.1f}° de donde apuntaba la montura"
    return None


def wcs_desde_ini(ruta_ini):
    """
    Lee el .ini que deja ASTAP y devuelve (wcs, escala_arcsec_px, angulo_camara_deg)
    o None si no existe o no resolvió. El WCS está en píxeles de la imagen que
    resolvió ASTAP, con el origen (0, 0) en la esquina superior izquierda tal como
    la guarda cv2 (all_pix2world / all_world2pix con origin=0).
    """
    if not os.path.exists(ruta_ini):
        return None
    valores = {}
    with open(ruta_ini) as f:
        for linea in f:
            if "=" in linea:
                k, v = linea.split("=", 1)
                valores[k.strip()] = v.strip()
    if valores.get("PLTSOLVD") != "T":
        return None
    try:
        hdr = Header()
        for k in ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CD1_1", "CD1_2", "CD2_1", "CD2_2"):
            hdr[k] = float(valores[k])
        hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN", "DEC--TAN"
        hdr["CUNIT1"], hdr["CUNIT2"] = "deg", "deg"
    except (KeyError, ValueError) as e:
        log.warning("INI de ASTAP incompleto: %s", e)
        return None
    wcs = WCS(hdr)
    det = hdr["CD1_1"] * hdr["CD2_2"] - hdr["CD1_2"] * hdr["CD2_1"]
    escala_arcsec = math.sqrt(abs(det)) * 3600.0
    angulo_cam = math.degrees(math.atan2(hdr["CD1_2"], hdr["CD1_1"]))
    return wcs, escala_arcsec, angulo_cam


def norte_arriba(wcs, ancho, alto):
    """
    (giro, espejo) que dejan una imagen de ancho × alto con ese WCS como en los
    atlas, con el norte arriba y el este a la izquierda: `espejo` es True si la
    imagen está en espejo respecto al cielo (hay que voltearla de izquierda a
    derecha antes de girar) y `giro` va en grados, horario, -180..180 (los de
    procesado.girar_campo). None si no se puede calcular.
    """
    try:
        cx, cy = (ancho - 1) / 2.0, (alto - 1) / 2.0
        ra, dec = (float(v) for v in wcs.all_pix2world([[cx, cy]], 0)[0])
        paso = 0.05 if dec < 89.9 else -0.05                    # un poco hacia el norte (o desde el sur, en el polo)
        xn, yn = (float(v) for v in wcs.all_world2pix([[ra, dec + paso]], 0)[0])
        xe, ye = (float(v) for v in wcs.all_world2pix([[ra + 0.05 / max(math.cos(math.radians(dec)), 1e-3), dec]], 0)[0])
    except Exception as e:
        log.warning("No se pudo calcular el norte de la imagen: %s", e)
        return None
    dx, dy = (xn - cx) * math.copysign(1, paso), (yn - cy) * math.copysign(1, paso)
    # El norte arriba y el oeste (lo contrario del este) a la derecha
    return procesado.encuadre_con_arriba(dx, dy, -(xe - cx), -(ye - cy))


def objetos_catalogo_en_campo(wcs, ancho, alto, margen=0.02):
    """Objetos de catalogo_objetos.csv cuyo centro cae dentro de la imagen (píxeles de la imagen completa)."""
    try:
        catalogo = esta_noche.cargar_catalogo()
    except Exception as e:
        log.warning("No se pudo leer el catálogo local: %s", e)
        return []
    if not catalogo:
        return []
    ra = np.array([o["ra_h"] * 15.0 for o in catalogo])
    dec = np.array([o["dec_deg"] for o in catalogo])
    try:
        px, py = wcs.all_world2pix(ra, dec, 0)
    except Exception as e:
        log.warning("Error proyectando el catálogo: %s", e)
        return []
    mx, my = ancho * margen, alto * margen
    dentro = []
    for o, x, y in zip(catalogo, px, py):
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        if -mx <= x < ancho + mx and -my <= y < alto + my:
            tam = (o["tamano_arcmin"] or 5.0) * 60.0
            etiqueta = o["nombre"] if o["nombre"] != o["id"] else o["id"]
            if o["nombre"] != o["id"] and not o["nombre"].startswith(o["id"].replace(" ", "")):
                etiqueta = f"{o['nombre']} ({o['id']})"
            dentro.append({"nombre": etiqueta, "ra": o["ra_h"] * 15.0, "dec": o["dec_deg"],
                           "tamano_arcsec": tam, "tamano_menor_arcsec": tam, "angulo_objeto": None,
                           "mag": o["mag"], "fuente": "catalogo"})
    return dentro


# ── NGC e IC del catálogo OpenNGC (el del planetario, sin Internet) ──
MAG_NGC_POR_DEFECTO = 14.0      # lo que llega a verse con el Evoguide 50 apilando fotos de 20 s
MAX_NGC = 60                    # en un campo rico (Virgo) no más de estos, los más brillantes
TAMANO_SIN_MAG_ARCMIN = 8.0     # sin magnitud (muchas nebulosas) solo si son grandes
TIPOS_NGC = {"G", "GPair", "GTrpl", "GGroup", "GCl", "OCl", "Cl+N", "PN", "Neb", "HII",
             "EmN", "RfN", "SNR", "DrkN"}   # fuera: estrellas, asociaciones, duplicados ("Other")
_ngc = {"objetos": None}
_lock_ngc = threading.Lock()


def magnitud_maxima_ngc():
    """[Apilado] anotar_ngc_hasta_mag de config.ini; 0 o menos = sin NGC/IC."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    try:
        return float(cfg.get("Apilado", "anotar_ngc_hasta_mag", fallback=str(MAG_NGC_POR_DEFECTO)))
    except ValueError:
        return MAG_NGC_POR_DEFECTO


def _catalogo_ngc():
    """NGC e IC de static/atlas/dso-catalog.js (se lee una vez, ~0,5 s), con arrays para filtrar rápido."""
    with _lock_ngc:
        if _ngc["objetos"] is None:
            lista = []
            try:
                for d in catalogo_offline._leer_js("dso-catalog.js", "DSO_CATALOG_DATA"):
                    if d["id"].split()[0] not in ("NGC", "IC") or d.get("typeCode") not in TIPOS_NGC:
                        continue
                    lista.append(d)
            except Exception as e:
                log.warning("No se pudo leer el catálogo NGC/IC: %s", e)
            _ngc["objetos"] = lista
            _ngc["ra"] = np.array([float(d["raDeg"]) for d in lista])
            _ngc["dec"] = np.array([float(d["decDeg"]) for d in lista])
            log.info("Catálogo NGC/IC para anotar: %d objetos", len(lista))
        return _ngc


def objetos_ngc_en_campo(wcs, ancho, alto, mag_max=None, maximo=MAX_NGC):
    """NGC e IC dentro de la imagen hasta mag_max, los más brillantes primero (como objetos_catalogo_en_campo)."""
    if mag_max is None:
        mag_max = magnitud_maxima_ngc()
    if mag_max <= 0:
        return []
    cat = _catalogo_ngc()
    if not cat["objetos"]:
        return []
    try:
        # Primero un corte grueso por distancia al centro del campo: proyectar 12 000 objetos sobra
        (ra0, dec0), = wcs.all_pix2world([[ancho / 2.0, alto / 2.0]], 0)
        (ra1, dec1), = wcs.all_pix2world([[0.0, 0.0]], 0)
        radio = _separacion_deg(ra0, dec0, ra1, dec1) * 1.2 + 0.2
        cerca = np.nonzero(_separacion_deg(ra0, dec0, cat["ra"], cat["dec"]) < radio)[0]
        if not len(cerca):
            return []
        px, py = wcs.all_world2pix(cat["ra"][cerca], cat["dec"][cerca], 0)
    except Exception as e:
        log.warning("Error proyectando el catálogo NGC/IC: %s", e)
        return []
    dentro = []
    for i, x, y in zip(cerca, px, py):
        if not (np.isfinite(x) and np.isfinite(y) and 0 <= x < ancho and 0 <= y < alto):
            continue
        d = cat["objetos"][i]
        mayor = float(d.get("major") or 0.0)
        mag = d.get("mag")
        if mag is None:
            if mayor < TAMANO_SIN_MAG_ARCMIN:
                continue
        elif float(mag) > mag_max:
            continue
        menor = float(d.get("minor") or mayor)
        pa = d.get("positionAngle")
        mayor = mayor or 1.0
        dentro.append({"nombre": d["id"], "ra": float(d["raDeg"]), "dec": float(d["decDeg"]),
                       "tamano_arcsec": mayor * 60.0, "tamano_menor_arcsec": (menor or mayor) * 60.0,
                       "pa": float(pa) if pa is not None and menor and menor < mayor * 0.9 else None,
                       "mag": float(mag) if mag is not None else None, "fuente": "ngc"})
    dentro.sort(key=lambda o: o["mag"] if o["mag"] is not None else 99.0)
    return dentro[:maximo]


def _separacion_deg(ra1, dec1, ra2, dec2):
    """Distancia angular en grados (admite arrays en ra2/dec2)."""
    r1, d1, r2, d2 = np.radians(ra1), np.radians(dec1), np.radians(ra2), np.radians(dec2)
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(r1 - r2)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def angulo_en_imagen(wcs, ra, dec, pa_deg, matriz):
    """
    Ángulo del eje mayor en la imagen (grados desde +x hacia +y, el de cv2.ellipse y el
    del SVG de la galería) de un objeto con ángulo de posición pa_deg (desde el norte
    hacia el este). Se mide con el WCS, así vale con cualquier giro y con la foto en espejo.
    """
    paso = 1.0 / 60.0
    pa = math.radians(pa_deg)
    dra = paso * math.sin(pa) / max(1e-6, math.cos(math.radians(dec)))
    xs, ys = wcs.all_world2pix([ra, ra + dra], [dec, dec + paso * math.cos(pa)], 0)
    vx, vy = float(xs[1] - xs[0]), float(ys[1] - ys[0])
    m = np.asarray(matriz, dtype=np.float64)
    return math.degrees(math.atan2(m[1, 0] * vx + m[1, 1] * vy, m[0, 0] * vx + m[0, 1] * vy))


def pa_de(o):
    """Ángulo de posición del objeto (grados, N→E) o None. SIMBAD llega guardado como 90 - PA."""
    if o.get("pa") is not None:
        return float(o["pa"])
    if o.get("angulo_objeto") is not None:
        return 90.0 - float(o["angulo_objeto"])
    return None


def orden_rotulos(o):
    """Orden en que se rotulan: populares (los grandes antes), luego NGC/IC y SIMBAD por brillo."""
    tam = -float(o.get("tamano_arcsec") or 0)
    if o.get("fuente") == "catalogo":
        return (0, 0.0, tam)
    mag = o.get("mag")
    return (1, float(mag) if mag is not None else 99.0, tam)


def hay_internet(host=HOST_SIMBAD, timeout=3.0):
    try:
        with socket.create_connection(host, timeout=timeout):
            return True
    except OSError:
        return False


def objetos_simbad(ruta_ini, ruta_fits, timeout=TIMEOUT_SIMBAD_S):
    """SIMBAD con espera corta; [] si no hay red o falla. Nunca usa la caché CSV antigua."""
    # De una en una: obtener_objetos_simbad deja su resultado en un CSV de la carpeta
    # de trabajo, y el apilado y la galería pueden pedir objetos de campos distintos
    with _lock_simbad:
        for cache in ("objetos_filtrados.csv", "objetos_descartados.csv"):
            try:
                if os.path.exists(cache):
                    os.remove(cache)
            except OSError:
                pass
        if not hay_internet():
            log.info("Sin Internet: anotaciones solo con el catálogo local")
            return []
        try:
            from astroquery.simbad import Simbad
            Simbad.TIMEOUT = timeout
            objetos = obtener_objetos_simbad(ruta_ini, ruta_fits, mag_max=14.0) or []
            for o in objetos:
                o["fuente"] = "simbad"
            return objetos
        except Exception as e:
            log.warning("SIMBAD no disponible (%s); anotaciones solo con el catálogo local", e)
            return []


def _fusionar(catalogo, simbad, wcs, tol_px=40):
    """Los del catálogo mandan (nombre en español); SIMBAD solo añade los que no estén ya cerca."""
    resultado = list(catalogo)
    if not simbad:
        return resultado
    if catalogo:
        cx, cy = wcs.all_world2pix([o["ra"] for o in catalogo], [o["dec"] for o in catalogo], 0)
    else:
        cx, cy = np.array([]), np.array([])
    for o in simbad:
        try:
            x, y = wcs.all_world2pix([o["ra"]], [o["dec"]], 0)
            x, y = float(x[0]), float(y[0])
        except Exception:
            continue
        if len(cx) and np.min(np.hypot(cx - x, cy - y)) < tol_px:
            continue
        resultado.append(o)
    return resultado


def dibujar(img_bgr, wcs, escala_arcsec, angulo_cam, objetos, matriz):
    """
    Dibuja elipses/círculos y etiquetas sobre img_bgr. Tamaño de letra y grosor
    proporcionales. matriz: afín 2×3 que lleva un píxel del FITS que resolvió
    ASTAP a img_bgr (reducción, recorte de zoom, giro y espejo del encuadre).
    """
    h, w = img_bgr.shape[:2]
    matriz = np.asarray(matriz, dtype=np.float64)
    factor = math.sqrt(abs(matriz[0, 0] * matriz[1, 1] - matriz[0, 1] * matriz[1, 0]))
    lado = max(h, w)
    grosor = max(1, int(round(lado / 700)))
    fuente = max(0.45, lado / 1500)
    fuente_px = int(round(22 * fuente))
    # Los nombres van con una fuente TrueType (la de la firma): la de OpenCV solo tiene ASCII
    # y "Gran Cúmulo de Hércules" salía "Gran C??mulo de H??rcules". Tamaño equivalente al
    # de antes: las mayúsculas de una TrueType miden ~0,7 del tamaño de la fuente.
    letra = firma._fuente(round(fuente_px / 0.7))
    borde = grosor + 1
    blanco, negro, amarillo, azul = (255, 255, 255), (0, 0, 0), (120, 220, 255), (255, 210, 150)
    colores = {"catalogo": amarillo, "ngc": azul}
    n = 0
    ocupados = []          # rectángulos de etiquetas ya puestas (x0, y0, x1, y1)
    etiquetas = []         # (texto, x, y de la línea base, color BGR): se escriben al final, de una vez

    def _medir(txt):
        x0, y0, x1, y1 = letra.getbbox(txt, anchor="ls", stroke_width=borde)
        return x1 - x0, -y0                 # ancho y alto sobre la línea base, como cv2.getTextSize

    def _libre(rc):
        x0, y0, x1, y1 = rc
        return all(x1 < a or x0 > c or y1 < b or y0 > d for a, b, c, d in ocupados)

    # Primero los populares; luego NGC/IC y SIMBAD por brillo: son los que importan si hay que apretar
    objetos = sorted(objetos, key=orden_rotulos)
    for o in objetos:
        try:
            xw, yw = wcs.all_world2pix([o["ra"]], [o["dec"]], 0)
            x = int(round(matriz[0, 0] * float(xw[0]) + matriz[0, 1] * float(yw[0]) + matriz[0, 2]))
            y = int(round(matriz[1, 0] * float(xw[0]) + matriz[1, 1] * float(yw[0]) + matriz[1, 2]))
            if not (0 <= x < w and 0 <= y < h):
                continue
            semi_maj = (o["tamano_arcsec"] / 2.0) / escala_arcsec * factor
            semi_min = (o.get("tamano_menor_arcsec", o["tamano_arcsec"]) / 2.0) / escala_arcsec * factor
            if not (math.isfinite(semi_maj) and math.isfinite(semi_min)):
                continue
            r_min = 6 * grosor
            semi_maj, semi_min = max(semi_maj, r_min), max(semi_min, r_min)
            color = colores.get(o.get("fuente"), blanco)
            pa = pa_de(o)
            if pa is None:
                cv2.circle(img_bgr, (x, y), int(round(semi_maj)), color, grosor, cv2.LINE_AA)
            else:
                ang_img = angulo_en_imagen(wcs, o["ra"], o["dec"], pa, matriz)
                cv2.ellipse(img_bgr, (x, y), (int(round(semi_maj)), int(round(semi_min))), ang_img,
                            0, 360, color, grosor, cv2.LINE_AA)
            n += 1
            mag = o.get("mag")
            nombre = " ".join(str(o["nombre"]).split())          # SIMBAD trae dobles espacios
            txt = f"{nombre} ({mag:.1f})" if mag is not None else nombre
            tw, th = _medir(txt)
            r = int(semi_maj) + 4 * grosor
            # Posiciones candidatas: debajo, encima, derecha, izquierda; la primera libre
            candidatas = [(x - tw // 2, y + r + th), (x - tw // 2, y - r), (x + r + 2, y + th // 2), (x - r - tw - 2, y + th // 2)]
            puesto = False
            for tx, ty in candidatas:
                tx = min(max(0, tx), w - tw - 1); ty = min(max(th, ty), h - 2)
                rc = (tx - 2, ty - th - 2, tx + tw + 2, ty + 2)
                if _libre(rc):
                    etiquetas.append((txt, tx, ty, color))
                    ocupados.append(rc); puesto = True
                    break
            if not puesto:
                pass       # sin sitio: se queda el círculo sin nombre antes que un texto ilegible
        except Exception as e:
            log.warning("No se pudo dibujar %s: %s", o.get("nombre", "?"), e)

    # Todas las etiquetas en una sola pasada por PIL (texto con borde oscuro, en el sitio)
    if etiquetas:
        try:
            lienzo = Image.fromarray(np.ascontiguousarray(img_bgr[:, :, ::-1]))
            dibujo = ImageDraw.Draw(lienzo)
            for txt, tx, ty, color in etiquetas:
                dibujo.text((tx, ty), txt, font=letra, anchor="ls", fill=tuple(reversed(color)),
                            stroke_width=borde, stroke_fill=negro)
            img_bgr[:] = np.asarray(lienzo)[:, :, ::-1]
        except Exception as e:
            log.warning("No se pudieron escribir los nombres de los objetos: %s", e)
    return n


def anotar(rgb_procesado, ruta_fits, usar_simbad=True, matriz=None):
    """
    Devuelve una copia de rgb_procesado (uint8 RGB, a cualquier escala del FITS)
    con los objetos del campo anotados, o None si no se pudo resolver el campo.
    matriz: afín 2×3 que lleva un píxel del FITS a rgb_procesado (la de
    procesado.girar_campo por la reducción del procesado); None si
    rgb_procesado es el campo completo, solo reducido.
    """
    from astropy.io import fits as _fits
    with _fits.open(ruta_fits) as hdul:
        alto_fits, ancho_fits = hdul[0].data.shape[-2:]
    res = resolver_wcs(ruta_fits)
    if res is None:
        log.info("Anotaciones: el campo no se pudo resolver")
        return None
    wcs, escala_arcsec, angulo_cam, ruta_ini = res
    if matriz is None:
        factor = rgb_procesado.shape[1] / float(ancho_fits)
        matriz = [[factor, 0.0, 0.0], [0.0, factor, 0.0]]
    objetos = objetos_catalogo_en_campo(wcs, ancho_fits, alto_fits)
    objetos = _fusionar(objetos, objetos_ngc_en_campo(wcs, ancho_fits, alto_fits), wcs)
    if usar_simbad:
        objetos = _fusionar(objetos, objetos_simbad(ruta_ini, ruta_fits), wcs)
    if not objetos:
        log.info("Anotaciones: ningún objeto conocido en el campo")
    img = cv2.cvtColor(rgb_procesado, cv2.COLOR_RGB2BGR)
    n = dibujar(img, wcs, escala_arcsec, angulo_cam, objetos, matriz)
    log.info("Anotaciones: %d objetos dibujados (%d populares, %d NGC/IC)", n,
             sum(1 for o in objetos if o.get("fuente") == "catalogo"),
             sum(1 for o in objetos if o.get("fuente") == "ngc"))
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
