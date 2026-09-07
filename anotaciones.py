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
#
# El plate solving sigue siendo ASTAP sobre un FITS monocromo del apilado.

import os
import math
import socket
import logging
import subprocess
import configparser

import cv2
import numpy as np
from astropy.wcs import WCS
from astropy.io.fits import Header

import esta_noche
from ScopePower_ObjetosAnotados import generar_fits_monocromo_para_platesolve, obtener_objetos_simbad

log = logging.getLogger("scopepower.anotaciones")

TIMEOUT_ASTAP_S = 60
TIMEOUT_SIMBAD_S = 12
HOST_SIMBAD = ("simbad.u-strasbg.fr", 443)


def _ruta_astap():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    return cfg.get("General", "ruta_astap", fallback="")


def resolver_wcs(ruta_fits, timeout=TIMEOUT_ASTAP_S):
    """
    Plate solving del FITS apilado con ASTAP. Devuelve (wcs, escala_arcsec_px,
    angulo_camara_deg, ruta_ini) o None. El WCS está en píxeles de la imagen
    completa (la del FITS).
    """
    ruta_mono = os.path.splitext(ruta_fits)[0] + "_mono.fits"
    ruta_ini = os.path.splitext(ruta_mono)[0] + ".ini"
    generar_fits_monocromo_para_platesolve(ruta_fits, ruta_mono)
    for viejo in (ruta_ini, os.path.splitext(ruta_mono)[0] + ".wcs"):
        try:
            os.remove(viejo)
        except OSError:
            pass
    try:
        subprocess.run(f'"{_ruta_astap()}" -f "{ruta_mono}" -solve -r 50', shell=True,
                       capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("ASTAP no terminó en %s s (anotaciones)", timeout)
        return None
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
    return wcs, escala_arcsec, angulo_cam, ruta_ini


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


def hay_internet(host=HOST_SIMBAD, timeout=3.0):
    try:
        with socket.create_connection(host, timeout=timeout):
            return True
    except OSError:
        return False


def objetos_simbad(ruta_ini, ruta_fits, timeout=TIMEOUT_SIMBAD_S):
    """SIMBAD con espera corta; [] si no hay red o falla. Nunca usa la caché CSV antigua."""
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


def dibujar(img_bgr, wcs, escala_arcsec, angulo_cam, objetos, factor):
    """
    Dibuja elipses/círculos y etiquetas sobre img_bgr (ya reducida por `factor`
    respecto al FITS que resolvió ASTAP). Tamaño de letra y grosor proporcionales.
    """
    h, w = img_bgr.shape[:2]
    lado = max(h, w)
    grosor = max(1, int(round(lado / 700)))
    fuente = max(0.45, lado / 1500)
    fuente_px = int(round(22 * fuente))
    blanco, negro, amarillo = (255, 255, 255), (0, 0, 0), (120, 220, 255)
    n = 0
    ocupados = []          # rectángulos de etiquetas ya puestas (x0, y0, x1, y1)

    def _libre(rc):
        x0, y0, x1, y1 = rc
        return all(x1 < a or x0 > c or y1 < b or y0 > d for a, b, c, d in ocupados)

    # Primero el catálogo y los objetos grandes: son los que importan si hay que apretar
    objetos = sorted(objetos, key=lambda o: (o.get("fuente") != "catalogo", -float(o.get("tamano_arcsec") or 0)))
    for o in objetos:
        try:
            xw, yw = wcs.all_world2pix([o["ra"]], [o["dec"]], 0)
            x, y = int(round(float(xw[0]) * factor)), int(round(float(yw[0]) * factor))
            if not (0 <= x < w and 0 <= y < h):
                continue
            semi_maj = (o["tamano_arcsec"] / 2.0) / escala_arcsec * factor
            semi_min = (o.get("tamano_menor_arcsec", o["tamano_arcsec"]) / 2.0) / escala_arcsec * factor
            if not (math.isfinite(semi_maj) and math.isfinite(semi_min)):
                continue
            r_min = 6 * grosor
            semi_maj, semi_min = max(semi_maj, r_min), max(semi_min, r_min)
            color = amarillo if o.get("fuente") == "catalogo" else blanco
            ang = o.get("angulo_objeto")
            if ang is None:
                cv2.circle(img_bgr, (x, y), int(round(semi_maj)), color, grosor, cv2.LINE_AA)
            else:
                cv2.ellipse(img_bgr, (x, y), (int(round(semi_maj)), int(round(semi_min))), ang - angulo_cam,
                            0, 360, color, grosor, cv2.LINE_AA)
            n += 1
            mag = o.get("mag")
            nombre = " ".join(str(o["nombre"]).split())          # SIMBAD trae dobles espacios
            txt = f"{nombre} ({mag:.1f})" if mag is not None else nombre
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fuente, grosor)
            r = int(semi_maj) + 4 * grosor
            # Posiciones candidatas: debajo, encima, derecha, izquierda; la primera libre
            candidatas = [(x - tw // 2, y + r + th), (x - tw // 2, y - r), (x + r + 2, y + th // 2), (x - r - tw - 2, y + th // 2)]
            puesto = False
            for tx, ty in candidatas:
                tx = min(max(0, tx), w - tw - 1); ty = min(max(th, ty), h - 2)
                rc = (tx - 2, ty - th - 2, tx + tw + 2, ty + 2)
                if _libre(rc):
                    cv2.putText(img_bgr, txt, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, fuente, negro, grosor * 3, cv2.LINE_AA)
                    cv2.putText(img_bgr, txt, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, fuente, color, grosor, cv2.LINE_AA)
                    ocupados.append(rc); puesto = True
                    break
            if not puesto:
                pass       # sin sitio: se queda el círculo sin nombre antes que un texto ilegible
        except Exception as e:
            log.warning("No se pudo dibujar %s: %s", o.get("nombre", "?"), e)
    return n


def anotar(rgb_procesado, ruta_fits, usar_simbad=True):
    """
    Devuelve una copia de rgb_procesado (uint8 RGB, a cualquier escala del FITS)
    con los objetos del campo anotados, o None si no se pudo resolver el campo.
    """
    from astropy.io import fits as _fits
    with _fits.open(ruta_fits) as hdul:
        alto_fits, ancho_fits = hdul[0].data.shape[-2:]
    res = resolver_wcs(ruta_fits)
    if res is None:
        log.info("Anotaciones: el campo no se pudo resolver")
        return None
    wcs, escala_arcsec, angulo_cam, ruta_ini = res
    factor = rgb_procesado.shape[1] / float(ancho_fits)
    objetos = objetos_catalogo_en_campo(wcs, ancho_fits, alto_fits)
    if usar_simbad:
        objetos = _fusionar(objetos, objetos_simbad(ruta_ini, ruta_fits), wcs)
    if not objetos:
        log.info("Anotaciones: ningún objeto conocido en el campo")
    img = cv2.cvtColor(rgb_procesado, cv2.COLOR_RGB2BGR)
    n = dibujar(img, wcs, escala_arcsec, angulo_cam, objetos, factor)
    log.info("Anotaciones: %d objetos dibujados (%d del catálogo local)", n,
             sum(1 for o in objetos if o.get("fuente") == "catalogo"))
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
