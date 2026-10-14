# mosaico.py
#
# Modo Mosaico del apilado en tiempo real: una región más grande que el campo de
# la cámara se fotografía por teselas (una rejilla de filas × columnas de campos
# que se solapan un tanto por ciento), cada tesela se apila como siempre (el
# apilador normal, con ASTAP) y al final todas se proyectan sobre un mismo plano
# del cielo y se funden en una sola imagen, ya apilada y procesada.
#
# Cómo casa todo:
#   - Cada tesela apilada sale de apilado.py en el marco de su foto de referencia,
#     y ese FITS lleva desde ahora el WCS de la referencia (CRPIX/CRVAL/CD). Con
#     dos proyecciones tangentes del mismo cielo, el paso de los píxeles de una
#     tesela a los del mosaico es exactamente una homografía (proyectiva 3×3):
#     se ajusta con una rejilla de puntos pasados por los dos WCS y se aplica
#     con cv2.warpPerspective. La rotación de campo de la altacimutal (cada
#     tesela sale girada un ángulo distinto) y el espejo de la cámara van
#     dentro del WCS, así que se corrigen solos.
#   - Rejilla de teselas: se calcula en el plano tangente del centro del mosaico
#     a lo largo de los ejes de la CÁMARA, con el paso ancho·(1 − solape). El
#     ángulo de la cámara en el cielo cambia con el apuntado y la hora
#     (altacimutal): para la primera tesela se prevé con la orientación
#     respecto a los ejes (orientacion_camara.py) o el último plate solving, y
#     para las siguientes se usa el WCS real de la tesela anterior (de hace
#     unos minutos). Con el 20 % de solape por defecto sobra margen.
#   - Fusión: cada tesela entra con un peso que baja en pluma hacia sus bordes
#     (dentro de su zona cubierta por todas las fotos), el fondo de cada una se
#     iguala al del mosaico ya acumulado en la zona de solape (mediana de la
#     diferencia, insensible a las estrellas) y, si se pide, antes se le quita
#     su gradiente de fondo (procesado.quitar_gradiente), que es lo que más se
#     nota en las costuras. El resultado es Σ w·I / Σ w.
#     (Actualizado: en vez de la mediana y de quitar gradientes tesela a tesela,
#     que se comen las nebulosas grandes, se ajusta un PLANO de fondo por tesela
#     sobre las diferencias en los solapes, todas a la vez, como hace Montage:
#     el objeto es el mismo en las dos teselas y se cancela, el gradiente no.)
#   - Salida: static/resultado_mosaico.fits (RGB float32 con WCS, NSTACK, EXPTOT,
#     ZONA* y NORTEGIR/NORTEESP como cualquier apilado) y la vista previa
#     static/resultado_mosaico_preview.jpg con el mismo procesado, encuadre y
#     firma que el apilado normal. Las teselas quedan en static/mosaico/ y se
#     puede recombinar sin volver a fotografiar.
#
# El trabajo (TrabajoMosaico) va en un hilo, como el plan de la noche: GoTo al
# centro de cada tesela (con centrado por plate solving si se quiere), apilado
# hasta las N fotos, copia del FITS apilado como tesela, reinicio del apilado y
# siguiente. Después de cada tesela el mosaico se recombina en segundo plano
# para ir viéndolo crecer, y al terminar se guarda en la galería.

import os
import re
import json
import math
import time
import glob
import shutil
import logging
import warnings
import threading
import configparser
from collections import deque
from datetime import datetime

import numpy as np
import cv2
from astropy.io import fits
from astropy.io.fits import Header, PrimaryHDU
from astropy.wcs import WCS

log = logging.getLogger("scopepower.mosaico")

CARPETA = os.path.join("static", "mosaico")
RUTA_FITS = os.path.join("static", "resultado_mosaico.fits")
RUTA_PREVIEW = os.path.join("static", "resultado_mosaico_preview.jpg")
RUTA_DEFINICION = os.path.join(CARPETA, "mosaico.json")
RUTA_APILADO = os.path.join("static", "resultado_apilado.fits")

CLAVES_WCS = ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CD1_1", "CD1_2", "CD2_1", "CD2_2")


# ═════════════════════════════════════════════════════════════════════════════
# Configuración ([Mosaico] de config.ini)
# ═════════════════════════════════════════════════════════════════════════════
def leer_config(cfg=None):
    if cfg is None:
        cfg = configparser.ConfigParser()
        cfg.read("config.ini", encoding="utf-8")
    m = cfg["Mosaico"] if "Mosaico" in cfg else {}
    d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}

    def num(clave, defecto, minimo, maximo, entero=False):
        try:
            v = float(str(m.get(clave, defecto)).replace(",", "."))
        except (TypeError, ValueError):
            v = float(defecto)
        v = min(maximo, max(minimo, v))
        return int(round(v)) if entero else v

    def booleano(clave, defecto):
        return str(m.get(clave, defecto)).strip().lower() in ("true", "1", "si", "sí", "yes")

    try:
        fotos_defecto = int(float(d.get("numero_maximo_imagenes", 20)))
    except (TypeError, ValueError):
        fotos_defecto = 20
    fotos = num("fotos_por_tesela", 0, 0, 999, entero=True) or fotos_defecto
    return {
        "filas": num("filas", 2, 1, 6, entero=True),
        "columnas": num("columnas", 2, 1, 6, entero=True),
        "solape_pct": num("solape_pct", 20, 5, 60),
        "fotos_por_tesela": fotos,
        # Lado mayor del mosaico en píxeles: por encima se reduce (memoria del mini PC)
        "lado_maximo": num("lado_maximo", 4000, 1000, 12000, entero=True),
        "centrar": booleano("centrar", "true"),
        "minutos_sin_fotos": num("minutos_sin_fotos", 10, 2, 120),
        "guardar_galeria": booleano("guardar_galeria", "true"),
        # Anchura de la pluma de los bordes, en % del lado de la tesela (0 = la mitad del solape)
        "pluma_pct": num("pluma_pct", 0, 0, 40),
    }


def guardar_config(cambios):
    """Guarda en [Mosaico] de config.ini los valores que vienen (solo claves conocidas)."""
    permitidas = {"filas", "columnas", "solape_pct", "fotos_por_tesela", "lado_maximo", "centrar",
                  "minutos_sin_fotos", "guardar_galeria", "pluma_pct"}
    cfg = configparser.ConfigParser()
    cfg.read("config.ini", encoding="utf-8")
    if "Mosaico" not in cfg:
        cfg["Mosaico"] = {}
    for k, v in cambios.items():
        if k in permitidas and v is not None:
            cfg["Mosaico"][k] = ("true" if v else "false") if isinstance(v, bool) else str(v)
    with open("config.ini", "w", encoding="utf-8") as f:
        cfg.write(f)
    return leer_config(cfg)


# ═════════════════════════════════════════════════════════════════════════════
# Geometría: campo de la cámara, orientación prevista y rejilla de teselas
# ═════════════════════════════════════════════════════════════════════════════
def campo_camara(focal_mm=None):
    """
    Campo de la cámara: {ancho_px, alto_px, escala_arcsec, ancho_deg, alto_deg, origen}.
    Sale de la cabecera de la última foto (NAXIS, XPIXSZ, FOCALLEN); si no hay ninguna,
    de la última resolución guardada (orientacion_camara) y, como último recurso, de la
    focal de config.ini con el sensor de la Saturn-C (3008 px de 3,76 µm).
    """
    candidatos = [os.path.join("static", "captura.fits"), RUTA_APILADO]
    candidatos += sorted(glob.glob(os.path.join("capturas", "*.fits")), key=os.path.getmtime, reverse=True)[:1]
    for ruta in candidatos:
        try:
            if not os.path.exists(ruta):
                continue
            h = fits.getheader(ruta)
            ancho, alto = int(h["NAXIS1"]), int(h["NAXIS2"])
            if ancho < 100 or alto < 100:
                continue
            px = float(h.get("XPIXSZ") or 0)
            focal = float(focal_mm or h.get("FOCALLEN") or 0)
            if px <= 0 or focal <= 0:
                continue
            escala = 206.265 * px / focal
            return {"ancho_px": ancho, "alto_px": alto, "escala_arcsec": round(escala, 4),
                    "ancho_deg": round(ancho * escala / 3600.0, 4), "alto_deg": round(alto * escala / 3600.0, 4),
                    "origen": os.path.basename(ruta)}
        except Exception as e:
            log.debug("campo_camara: %s no sirve (%s)", ruta, e)
    try:
        import orientacion_camara
        o = orientacion_camara.ultima(buscar=False)
        if o and o.get("escala_arcsec") and o.get("ancho"):
            escala = float(o["escala_arcsec"])
            return {"ancho_px": int(o["ancho"]), "alto_px": int(o["alto"]), "escala_arcsec": round(escala, 4),
                    "ancho_deg": round(o["ancho"] * escala / 3600.0, 4), "alto_deg": round(o["alto"] * escala / 3600.0, 4),
                    "origen": "última resolución"}
    except Exception as e:
        log.debug("campo_camara: sin orientación guardada (%s)", e)
    if not focal_mm:
        try:
            cfg = configparser.ConfigParser()
            cfg.read("config.ini", encoding="utf-8")
            focal_mm = float(cfg["Dispositivos"].get("focal_len", 260))
        except Exception:
            focal_mm = 260.0
    escala = 206.265 * 3.76 / float(focal_mm)
    return {"ancho_px": 3008, "alto_px": 3008, "escala_arcsec": round(escala, 4),
            "ancho_deg": round(3008 * escala / 3600.0, 4), "alto_deg": round(3008 * escala / 3600.0, 4),
            "origen": "supuesto (Saturn-C)"}


def wcs_tangente(ra_deg, dec_deg, cd, ancho, alto):
    """WCS TAN con el centro de una imagen ancho × alto en (ra, dec) y la matriz CD dada (grados/px)."""
    hdr = Header()
    hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN", "DEC--TAN"
    hdr["CUNIT1"], hdr["CUNIT2"] = "deg", "deg"
    hdr["CRPIX1"], hdr["CRPIX2"] = (ancho - 1) / 2.0 + 1.0, (alto - 1) / 2.0 + 1.0   # FITS: 1-based
    hdr["CRVAL1"], hdr["CRVAL2"] = float(ra_deg), float(dec_deg)
    hdr["CD1_1"], hdr["CD1_2"] = float(cd[0][0]), float(cd[0][1])
    hdr["CD2_1"], hdr["CD2_2"] = float(cd[1][0]), float(cd[1][1])
    return WCS(hdr)


def cd_desde_angulo(escala_deg, angulo_deg):
    """Matriz CD estándar (norte arriba y este a la izquierda con 0°) girada CROTA2 = angulo."""
    a = math.radians(angulo_deg)
    s = float(escala_deg)
    return [[-s * math.cos(a), -s * math.sin(a)], [-s * math.sin(a), s * math.cos(a)]]


def wcs_de_cabecera(hdr):
    """WCS de una cabecera con CRPIX/CRVAL/CD (las teselas apiladas), o None si no lo lleva."""
    try:
        if any(k not in hdr for k in CLAVES_WCS):
            return None
        h = Header()
        for k in CLAVES_WCS:
            h[k] = float(hdr[k])
        h["CTYPE1"], h["CTYPE2"] = "RA---TAN", "DEC--TAN"
        h["CUNIT1"], h["CUNIT2"] = "deg", "deg"
        return WCS(h)
    except Exception as e:
        log.warning("Cabecera sin WCS válido: %s", e)
        return None


def cd_de(wcs):
    """Matriz CD 2×2 (lista) de un WCS astropy."""
    m = wcs.wcs.cd if wcs.wcs.has_cd() else np.diag(wcs.wcs.cdelt)
    return [[float(m[0][0]), float(m[0][1])], [float(m[1][0]), float(m[1][1])]]


def angulo_de(cd):
    """Giro (grados) del eje x de la imagen respecto al norte, sin la paridad (0..180)."""
    return math.degrees(math.atan2(cd[0][1], cd[0][0])) % 180.0


def wcs_previsto(ra_h, dec_deg, campo, lugar=None, cuando=None):
    """
    WCS que se espera que tenga la cámara apuntando a (ra, dec) ahora: (wcs, origen).
    1) Orientación respecto a los ejes de la montura (orientacion_camara.py) pasada al
       cielo con astropy para este apuntado y hora (lleva giro, escala y paridad).
    2) Si no la hay, el giro del último plate solving (rotacion_camara.py), que en la
       altacimutal solo vale cerca de donde se hizo, con paridad estándar.
    3) Si no, norte arriba.
    """
    ra_deg = float(ra_h) * 15.0
    escala_deg = campo["escala_arcsec"] / 3600.0
    ancho, alto = campo["ancho_px"], campo["alto_px"]
    try:
        import orientacion_camara
        o = orientacion_camara.ultima(buscar=False)
        if o and o.get("H") and lugar is not None:
            from astropy.coordinates import SkyCoord, EarthLocation, AltAz
            from astropy.time import Time
            import astropy.units as u
            lat, lon, alt_m = lugar
            t = cuando or Time.now()
            sitio = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
            marco = AltAz(obstime=t, location=sitio)
            c = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg).transform_to(marco)
            if c.alt.deg > 1.0:
                H = np.array(o["H"], dtype=float)          # (Δaz·cos alt, Δalt) en grados por (Δcol, Δfila)
                paso = 200.0
                ca = math.cos(math.radians(c.alt.deg))
                cols = []
                for v in ((paso, 0.0), (0.0, paso)):
                    d_azc, d_alt = H @ np.array(v)
                    p = SkyCoord(az=(c.az.deg + d_azc / max(ca, 1e-3)) * u.deg, alt=(c.alt.deg + d_alt) * u.deg,
                                 frame=marco).transform_to("icrs")
                    d_ra = ((p.ra.deg - ra_deg + 180.0) % 360.0 - 180.0) * math.cos(math.radians(dec_deg))
                    cols.append([d_ra / paso, (p.dec.deg - dec_deg) / paso])
                # CD lleva (Δra·cos δ, Δdec) por píxel; la escala real del campo (la de la
                # orientación puede ser de otro binning) se impone normalizando
                cd = np.array(cols).T
                f = escala_deg / math.sqrt(abs(np.linalg.det(cd)))
                return wcs_tangente(ra_deg, dec_deg, (cd * f).tolist(), ancho, alto), "orientación respecto a los ejes"
    except Exception as e:
        log.warning("No se pudo prever la orientación de la cámara: %s", e)
    try:
        import rotacion_camara
        r = rotacion_camara.leer()
        if r and r.get("grados") is not None:
            return (wcs_tangente(ra_deg, dec_deg, cd_desde_angulo(escala_deg, float(r["grados"])), ancho, alto),
                    f"último plate solving ({r.get('fecha', '')[:16]})")
    except Exception as e:
        log.warning("No se pudo leer la rotación de la cámara: %s", e)
    return wcs_tangente(ra_deg, dec_deg, cd_desde_angulo(escala_deg, 0.0), ancho, alto), "norte arriba (sin datos de la cámara)"


def calcular_teselas(wcs_centro, ancho_px, alto_px, filas, columnas, solape_pct):
    """
    Centros de las teselas (lista de dicts con i, fila, col, ra_h, dec_deg, dx_px, dy_px)
    en orden de serpentina (una fila de izquierda a derecha, la siguiente al revés), sobre
    el plano tangente de `wcs_centro`, que ha de estar centrado en el mosaico y con los
    ejes de la cámara. El paso entre teselas es el lado menos el solape.
    """
    paso_x = ancho_px * (1.0 - solape_pct / 100.0)
    paso_y = alto_px * (1.0 - solape_pct / 100.0)
    cx, cy = wcs_centro.wcs.crpix[0] - 1.0, wcs_centro.wcs.crpix[1] - 1.0
    teselas = []
    i = 0
    for fila in range(filas):
        cols = range(columnas) if fila % 2 == 0 else range(columnas - 1, -1, -1)
        for col in cols:
            dx = (col - (columnas - 1) / 2.0) * paso_x
            dy = (fila - (filas - 1) / 2.0) * paso_y
            ra, dec = (float(v) for v in wcs_centro.all_pix2world([[cx + dx, cy + dy]], 0)[0])
            teselas.append({"i": i, "fila": fila, "col": col, "ra_h": ra / 15.0 % 24.0, "dec_deg": dec,
                            "dx_px": round(dx, 1), "dy_px": round(dy, 1)})
            i += 1
    return teselas


def tamano_mosaico(campo, filas, columnas, solape_pct):
    """(ancho_deg, alto_deg) que cubre la rejilla, sin contar el giro de las teselas."""
    f = 1.0 - solape_pct / 100.0
    return (round(campo["ancho_deg"] * (1 + (columnas - 1) * f), 3),
            round(campo["alto_deg"] * (1 + (filas - 1) * f), 3))


# ═════════════════════════════════════════════════════════════════════════════
# Combinación de las teselas
# ═════════════════════════════════════════════════════════════════════════════
def homografia(wcs_src, wcs_dst, ancho, alto, n=5):
    """
    Matriz 3×3 que lleva los píxeles (origen 0) de una imagen ancho × alto con
    `wcs_src` a los de `wcs_dst`, ajustada sobre una rejilla n × n de puntos pasados
    por los dos WCS. Devuelve (H, residuo_px): entre dos proyecciones tangentes del
    mismo cielo es exacta (el residuo mide solo el redondeo).
    """
    xs = np.linspace(0, ancho - 1, n)
    ys = np.linspace(0, alto - 1, n)
    src = np.array([[x, y] for y in ys for x in xs], dtype=np.float64)
    cielo = wcs_src.all_pix2world(src, 0)
    dst = np.asarray(wcs_dst.all_world2pix(cielo, 0), dtype=np.float64)
    if not np.all(np.isfinite(dst)):
        raise ValueError("la tesela cae fuera del plano del mosaico")
    H, _ = cv2.findHomography(src, dst, 0)
    if H is None:
        raise ValueError("no se pudo ajustar la homografía")
    p = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    return H, float(np.max(np.hypot(*(p - dst).T)))


def pesos_tesela(ancho, alto, zona, pluma_px):
    """
    Peso (H, W) float32 de una tesela: 1 en el interior de su zona cubierta y en
    pluma lineal hasta 0 en los bordes de la zona (anchura pluma_px); 0 fuera.
    """
    x0, y0, w, h = zona if zona else (0, 0, ancho, alto)
    xs = np.arange(ancho, dtype=np.float32)
    ys = np.arange(alto, dtype=np.float32)
    dx = np.minimum(xs - x0, (x0 + w - 1) - xs)
    dy = np.minimum(ys - y0, (y0 + h - 1) - ys)
    d = np.minimum(dx[None, :], dy[:, None])
    peso = np.clip((d + 0.5) / max(float(pluma_px), 1.0), 0.0, 1.0)
    peso[d < 0] = 0.0
    return peso.astype(np.float32)


def leer_tesela(ruta):
    """(cubo (3,H,W) float32, cabecera, wcs, zona) de una tesela; ValueError si no sirve."""
    with fits.open(ruta) as hdul:
        hdr = hdul[0].header.copy()
        datos = hdul[0].data
        if datos is None or datos.ndim != 3 or datos.shape[0] != 3:
            raise ValueError(f"{os.path.basename(ruta)}: no es un apilado RGB")
        cubo = np.nan_to_num(datos.astype(np.float32))
    wcs = wcs_de_cabecera(hdr)
    if wcs is None:
        raise ValueError(f"{os.path.basename(ruta)}: el apilado no lleva WCS (ASTAP no resolvió la referencia)")
    import procesado
    zona = procesado.leer_zona(hdr)
    return cubo, hdr, wcs, zona


def ajustar_planos_fondo(proyecciones, ancho, alto, celda=12, minimo_celdas=6):
    """
    Plano de fondo (a·x + b·y + c por canal, con x e y normalizados -0,5..0,5 sobre el
    marco) que hay que SUMAR a cada tesela para que case con las demás en los solapes.
    proyecciones: lista de (imagen (H, W, 3), peso (H, W)) ya en el marco común.

    En cada solape entre dos teselas se toma la mediana de la diferencia por celdas
    (robusta a las estrellas) y se plantea P_i − P_j = −(I_i − I_j) en el centro de la
    celda; todas las teselas a la vez por mínimos cuadrados, con una regularización
    pequeña que deja la corrección media en cero (así no hay una tesela "ancla" y se
    reparte). Las celdas que se salen (un objeto brillante justo en el borde) se quitan
    y se vuelve a ajustar. Devuelve una lista de arrays (3 canales × [a, b, c]).
    """
    n = len(proyecciones)
    planos = [np.zeros((3, 3), dtype=np.float64) for _ in range(n)]
    if n < 2:
        return planos
    filas, pesos, difs = [], [], []
    h_c, w_c = alto // celda, ancho // celda
    for i in range(n):
        img_i, w_i = proyecciones[i]
        for j in range(i + 1, n):
            img_j, w_j = proyecciones[j]
            solape = (w_i > 0.3) & (w_j > 0.3)
            if np.count_nonzero(solape) < 4 * celda * celda:
                continue
            s_c = solape[:h_c * celda, :w_c * celda].reshape(h_c, celda, w_c, celda).mean(axis=(1, 3))
            d = (img_i - img_j)[:h_c * celda, :w_c * celda].reshape(h_c, celda, w_c, celda, 3)
            d = np.where(solape[:h_c * celda, :w_c * celda].reshape(h_c, celda, w_c, celda, 1), d, np.nan)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)       # celdas sin solape: todo NaN
                med = np.nanmedian(d, axis=(1, 3))                 # (h_c, w_c, 3)
            for cy, cx in zip(*np.nonzero(s_c >= 0.5)):
                x = (cx + 0.5) * celda / ancho - 0.5
                y = (cy + 0.5) * celda / alto - 0.5
                fila = np.zeros(3 * n)
                fila[3 * i:3 * i + 3] = (x, y, 1.0)
                fila[3 * j:3 * j + 3] = (-x, -y, -1.0)
                filas.append(fila)
                pesos.append(float(s_c[cy, cx]))
                difs.append(-med[cy, cx])
    if len(filas) < minimo_celdas:
        log.info("Mosaico: sin solape suficiente para igualar los fondos")
        return planos
    A = np.array(filas)
    W = np.sqrt(np.array(pesos))[:, None]
    B = np.array(difs)                                             # (m, 3)
    # Regularización: corrección media nula (una fila por coeficiente, peso pequeño)
    # Regularización: corrección media nula (peso pequeño para los niveles) y gradientes
    # moderados (más peso: un solape estrecho apenas informa del gradiente a través de él
    # y sin esto se extrapola cualquier cosa al resto de la tesela)
    lam_grad = float(os.environ.get("MOSAICO_LAM_GRAD", "0.001"))
    reg = np.diag(np.tile([lam_grad, lam_grad, 1e-3], n)) * math.sqrt(len(filas))
    for c in range(3):
        usar = np.ones(len(filas), dtype=bool)
        sol = None
        for _ in range(3):
            Aw = np.vstack([A[usar] * W[usar], reg])
            bw = np.concatenate([B[usar, c] * W[usar, 0], np.zeros(3 * n)])
            sol = np.linalg.lstsq(Aw, bw, rcond=None)[0]
            resid = A @ sol - B[:, c]
            mad = np.median(np.abs(resid[usar] - np.median(resid[usar]))) * 1.4826 + 1e-3
            nuevo = np.abs(resid) < 4.0 * mad
            if nuevo.sum() < minimo_celdas or np.array_equal(nuevo, usar):
                break
            usar = nuevo
        for i in range(n):
            planos[i][c] = sol[3 * i:3 * i + 3]
    return planos


def combinar(rutas, ra_c_h, dec_c_deg, conf=None, progreso=None, orden_angulo=None):
    """
    Combina las teselas (rutas de FITS apilados con WCS) en un mosaico centrado en
    (ra_c, dec_c). Devuelve (cubo (3,H,W) float32, cabecera, info). `progreso(texto)`
    informa de cada paso. El marco del mosaico lleva los ejes de la PRIMERA tesela (así
    el mosaico queda recto y el norte arriba se aplica después con NORTEGIR, como en
    cualquier apilado), reducido lo justo para no pasar de lado_maximo.
    """
    import procesado
    conf = conf or leer_config()
    avisar = progreso or (lambda t: None)
    teselas = []
    for ruta in rutas:
        try:
            cubo, hdr, wcs, zona = leer_tesela(ruta)
            teselas.append({"ruta": ruta, "cubo": cubo, "hdr": hdr, "wcs": wcs,
                            "zona": zona or (0, 0, cubo.shape[2], cubo.shape[1])})
        except Exception as e:
            log.warning("Tesela descartada: %s", e)
    if not teselas:
        raise ValueError("ninguna tesela utilizable")
    ra_c = float(ra_c_h) * 15.0

    # ── Marco de salida: ejes de la primera tesela, centro del mosaico ──
    cd0 = np.array(cd_de(teselas[0]["wcs"]), dtype=np.float64)
    escala_deg = math.sqrt(abs(np.linalg.det(cd0)))

    def marco(factor, crpix=(1.0, 1.0)):
        hdr = Header()
        hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN", "DEC--TAN"
        hdr["CUNIT1"], hdr["CUNIT2"] = "deg", "deg"
        hdr["CRPIX1"], hdr["CRPIX2"] = float(crpix[0]), float(crpix[1])
        hdr["CRVAL1"], hdr["CRVAL2"] = ra_c, float(dec_c_deg)
        cd = cd0 * factor
        hdr["CD1_1"], hdr["CD1_2"], hdr["CD2_1"], hdr["CD2_2"] = (float(cd[0, 0]), float(cd[0, 1]),
                                                                  float(cd[1, 0]), float(cd[1, 1]))
        return WCS(hdr), hdr

    def esquinas_en(wcs_out):
        pts = []
        for t in teselas:
            x0, y0, w, h = t["zona"]
            esq = np.array([[x0, y0], [x0 + w - 1, y0], [x0, y0 + h - 1], [x0 + w - 1, y0 + h - 1]], dtype=np.float64)
            pts.append(wcs_out.all_world2pix(t["wcs"].all_pix2world(esq, 0), 0))
        return np.vstack(pts)

    def marco_para(factor, lado_maximo=None):
        """(wcs, hdr, ancho, alto, factor) del marco de salida con esa reducción (subida si hace falta
        para no pasar de lado_maximo), con CRPIX tal que la esquina de las teselas caiga en el píxel 0."""
        w, _ = marco(factor)
        e = esquinas_en(w)
        if lado_maximo:
            lado = max(float(e[:, 0].max() - e[:, 0].min()), float(e[:, 1].max() - e[:, 1].min()))
            if lado > lado_maximo:
                factor *= lado / float(lado_maximo)
                w, _ = marco(factor)
                e = esquinas_en(w)
        margen = 2.0
        x_min, y_min = float(e[:, 0].min()) - margen, float(e[:, 1].min()) - margen
        ancho = int(math.ceil(float(e[:, 0].max()) - x_min + margen)) + 1
        alto = int(math.ceil(float(e[:, 1].max()) - y_min + margen)) + 1
        w, h = marco(factor, crpix=(1.0 - x_min, 1.0 - y_min))
        return w, h, ancho, alto, factor

    wcs_out, hdr_out, ancho, alto, factor = marco_para(1.0, conf["lado_maximo"])
    log.info("Mosaico de %d teselas: %d×%d px (reducción ×%.2f, %.2f\"/px)", len(teselas), ancho, alto,
             factor, escala_deg * factor * 3600.0)
    pluma_pct = conf["pluma_pct"] or conf["solape_pct"] / 2.0

    def proyectar(t, wcs_dst, ancho_dst, alto_dst, factor_dst):
        """(imagen (H, W, 3), peso (H, W)) de la tesela en el marco de salida."""
        cubo = t["cubo"]
        alto_t, ancho_t = cubo.shape[1], cubo.shape[2]
        img = np.ascontiguousarray(cubo.transpose(1, 2, 0))
        peso = pesos_tesela(ancho_t, alto_t, t["zona"], pluma_pct / 100.0 * min(ancho_t, alto_t))
        H, residuo = homografia(t["wcs"], wcs_dst, ancho_t, alto_t)
        t["residuo"] = round(residuo, 3)
        # Reducción previa (INTER_AREA conserva el ruido bajo) y homografía compuesta
        if factor_dst >= 1.5:
            f = 1.0 / factor_dst
            img = cv2.resize(img, (max(2, int(round(ancho_t * f))), max(2, int(round(alto_t * f)))), interpolation=cv2.INTER_AREA)
            peso = cv2.resize(peso, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
            sx, sy = ancho_t / float(img.shape[1]), alto_t / float(img.shape[0])
            H = H @ np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], dtype=np.float64)
        img_w = cv2.warpPerspective(img, H, (ancho_dst, alto_dst), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        peso_w = cv2.warpPerspective(peso, H, (ancho_dst, alto_dst), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        peso_w[peso_w < 0.01] = 0.0
        return img_w, peso_w

    # ── Pasada 1 (pequeña): un plano de fondo por tesela ajustado sobre las DIFERENCIAS
    #    en los solapes (el objeto es el mismo en las dos teselas y se cancela; lo que
    #    queda es la diferencia de fondos y gradientes, que sí es un plano) ──
    avisar("midiendo los fondos")
    wcs_p, _, ancho_p, alto_p, factor_p = marco_para(factor, 1200)
    peq = [proyectar(t, wcs_p, ancho_p, alto_p, factor_p) for t in teselas]
    planos = ajustar_planos_fondo(peq, ancho_p, alto_p)
    del peq
    for t, pl in zip(teselas, planos):
        t["plano"] = pl

    # ── Pasada 2 (a tamaño real): proyección, plano de fondo, pluma y suma ──
    acc = np.zeros((alto, ancho, 3), dtype=np.float32)
    wsum = np.zeros((alto, ancho), dtype=np.float32)
    cuenta = np.zeros((alto, ancho), dtype=np.uint8)
    xn = ((np.arange(ancho, dtype=np.float32) + 0.5) / ancho - 0.5)[None, :]
    yn = ((np.arange(alto, dtype=np.float32) + 0.5) / alto - 0.5)[:, None]
    residuos, offsets, nstacks, exptot = [], [], [], 0.0
    for k, t in enumerate(teselas):
        avisar(f"tesela {k + 1}/{len(teselas)}: proyectando")
        img_w, peso_w = proyectar(t, wcs_out, ancho, alto, factor)
        residuos.append(t["residuo"])
        pl = t["plano"]
        for c in range(3):
            a, b, c0 = (float(v) for v in pl[c])
            if a or b or c0:
                img_w[..., c] += a * xn + b * yn + c0
        offsets.append([round(float(pl[c][2]), 1) for c in range(3)])
        log.info("Mosaico: tesela %d (%s) plano de fondo por canal [a·x + b·y + c, x e y de -0,5 a 0,5]: %s",
                 k + 1, os.path.basename(t["ruta"]), [[round(float(v), 1) for v in pl[c]] for c in range(3)])
        acc += img_w * peso_w[..., None]
        wsum += peso_w
        cuenta += (peso_w > 0.0).astype(np.uint8)
        nstacks.append(int(t["hdr"].get("NSTACK", 1) or 1))
        exptot += float(t["hdr"].get("EXPTOT", 0) or 0)
        del img_w, peso_w
    avisar("fundiendo")
    con_datos = wsum > 0.0
    mosaico = np.zeros_like(acc)
    mosaico[con_datos] = acc[con_datos] / wsum[con_datos][:, None]
    del acc
    cubo_out = np.ascontiguousarray(mosaico.transpose(2, 0, 1))
    del mosaico

    # ── Cabecera: como cualquier apilado, más el WCS del mosaico ──
    import apilado
    import anotaciones
    zona = apilado.zona_cobertura(cuenta, 1, escala=4, margen=2)
    base = teselas[0]["hdr"]
    hdr = Header()
    for k in ("XPIXSZ", "YPIXSZ", "FOCALLEN", "XBINNING", "YBINNING", "GAIN", "EXPTIME", "EXPOSURE", "DATE-OBS",
              "DARKCORR", "FLATCORR", "OBJCTRA", "OBJCTDEC"):
        if k in base:
            hdr[k] = base[k]
    hdr["BITPIX"] = -32
    hdr["NAXIS"] = 3
    hdr["NAXIS1"], hdr["NAXIS2"], hdr["NAXIS3"] = ancho, alto, 3
    for k in CLAVES_WCS + ("CTYPE1", "CTYPE2", "CUNIT1", "CUNIT2"):
        hdr[k] = hdr_out[k]
    n_equiv = int(round(float(np.median(nstacks))))
    hdr["NSTACK"] = (n_equiv, "Fotos apiladas por tesela (mediana)")
    hdr["EXPTOT"] = (round(exptot, 1), "Exposicion total de todas las teselas (s)")
    hdr["NTESELAS"] = (len(teselas), "Teselas combinadas en el mosaico")
    hdr["MOSAICO"] = (f"{len(teselas)} teselas x{factor:.2f}", "Mosaico de ScopePower")
    hdr["DATE"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    hdr["CREATOR"] = ("ScopePower", "Real-time Power for Your Telescope")
    hdr["COMMENT"] = "Debayered RGB image"
    if zona:
        hdr["ZONAX0"] = (zona[0], "Zona cubierta por las teselas: x0")
        hdr["ZONAY0"] = (zona[1], "Zona cubierta por las teselas: y0")
        hdr["ZONAW"] = (zona[2], "Zona cubierta por las teselas: ancho")
        hdr["ZONAH"] = (zona[3], "Zona cubierta por las teselas: alto")
    norte = anotaciones.norte_arriba(wcs_out, ancho, alto)
    if norte:
        hdr["NORTEGIR"] = (norte[0], "Giro horario (grados) que deja el norte arriba")
        hdr["NORTEESP"] = (bool(norte[1]), "Espejo previo para dejar el este a la izquierda")
    info = {"ancho": ancho, "alto": alto, "teselas": len(teselas), "factor": round(factor, 3),
            "escala_arcsec": round(escala_deg * factor * 3600.0, 3), "residuos_px": residuos, "offsets": offsets,
            "zona": list(zona) if zona else None, "n_equiv": n_equiv, "exptot": round(exptot, 1),
            "cobertura_pct": round(100.0 * float(np.count_nonzero(con_datos)) / con_datos.size, 1),
            "nombres": [os.path.basename(t["ruta"]) for t in teselas]}
    return cubo_out, hdr, info


def publicar(cubo, hdr, nombre, filas, columnas, progreso=None, cfg=None):
    """
    Escribe el FITS del mosaico y su vista previa (mismo encuadre, estilo y firma que el
    apilado normal; el objeto de la firma lleva "· mosaico F×C"). Devuelve el nombre del JPG.
    """
    import procesado
    import apilado
    import firma
    avisar = progreso or (lambda t: None)
    os.makedirs(os.path.dirname(RUTA_FITS), exist_ok=True)
    PrimaryHDU(data=cubo, header=hdr).writeto(RUTA_FITS, overwrite=True)
    if cfg is None:
        cfg = configparser.ConfigParser()
        cfg.read("config.ini", encoding="utf-8")
    avisar("procesando la vista previa")
    n = int(hdr.get("NSTACK", 1) or 1)
    exptot = float(hdr.get("EXPTOT", 0) or 0)
    zona = procesado.leer_zona(hdr)
    encuadre = procesado.encuadre_vista(cfg, hdr)
    balance = procesado.balance_estrellas(cubo, zona)
    rec, _ = procesado.girar_campo(cubo, encuadre["fraccion"], encuadre["giro"], zona, espejo=encuadre["espejo"])
    lado = max(rec.shape[1], rec.shape[2])
    escala = 0.5 if lado > 1500 else 1.0
    rgb8 = procesado.procesar(rec, n, procesado.leer_config(), escala=escala, balance=balance)
    imagen_bgr = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
    try:
        n_fotos = int(hdr.get("NTESELAS", 1) or 1) * n
        meta = apilado.metadatos_firma(cfg, RUTA_FITS, n_fotos, round(exptot / n_fotos, 3) if n_fotos and exptot else 0)
        meta["objeto"] = f"{nombre} · mosaico {filas}×{columnas}"
        if meta["activa"]:
            imagen_bgr = firma.firmar(imagen_bgr, meta)
    except Exception as e:
        log.warning("No se pudo firmar la vista previa del mosaico: %s", e)
    cv2.imwrite(apilado.ruta_absoluta(RUTA_PREVIEW), imagen_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return RUTA_PREVIEW


def leer_definicion():
    try:
        with open(RUTA_DEFINICION, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def guardar_definicion(d):
    os.makedirs(CARPETA, exist_ok=True)
    with open(RUTA_DEFINICION, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def teselas_en_disco():
    return sorted(glob.glob(os.path.join(CARPETA, "tesela_*.fits")))


def limpiar():
    """Borra las teselas y el resultado del mosaico anterior."""
    for ruta in teselas_en_disco() + [RUTA_FITS, RUTA_PREVIEW, RUTA_DEFINICION]:
        try:
            if os.path.exists(ruta):
                os.remove(ruta)
        except OSError as e:
            log.warning("No se pudo borrar %s: %s", ruta, e)


# ═════════════════════════════════════════════════════════════════════════════
# El trabajo: GoTo → apilado → tesela, para cada tesela, y la combinación
# ═════════════════════════════════════════════════════════════════════════════
class MosaicoDetenido(Exception):
    pass


class TrabajoMosaico:
    """
    Ejecuta el mosaico en un hilo. `deps` (ver ScopePower._deps_mosaico) aporta:
      lugar() -> (lat, lon, alt_m); goto_sincrono(nombre, ra_h, dec_deg, fuente, centrar, cancelar, info, al_centrar);
      arrancar_apilado(usar_dark); detener_apilado(); estado_apilado() -> {total, procesando}; reiniciar_apilado();
      leer_dispositivos() -> dict; guardar_config(seccion, dict); anotar_objeto(nombre, ra, dec, tipo);
      guardar_galeria() -> dict; esperar_guardado() -> dict; otro_trabajo_en_curso() -> str|None;
      plan_en_marcha() -> str|None; bucle; decir(texto)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self._pausa = threading.Event()
        self._saltar = threading.Event()
        self._hilo = None
        self._hilo_combinar = None
        self._lock_combinar = threading.Lock()
        self._recombinar_pendiente = False
        self.deps = None
        self.version_mosaico = 0
        self.combinando = False
        self.mensaje_combinar = ""
        self.ultimo_combinado = None     # info de la última combinación (dict) o {"error": ...}
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.fase = ""              # goto | centrando | apilando | combinando | guardando | pausado | terminado
        self.nombre = ""
        self.tipo = ""
        self.ra_h = None
        self.dec_deg = None
        self.filas = 0
        self.columnas = 0
        self.solape_pct = 0.0
        self.fotos_por_tesela = 0
        self.usar_dark = True
        self.campo = None
        self.origen_angulo = ""
        self.angulo_deg = None
        self.teselas = []
        self.actual = None          # índice de la tesela en curso
        self.fotos = 0
        self.mensaje = ""
        self.eventos = deque(maxlen=30)
        self.inicio = None
        self.resultado = ""
        self.archivo_galeria = ""
        self.version_estado = 0
        self._ultimo_wcs = None

    # ── Control ───────────────────────────────────────────────────────
    def iniciar(self, deps, nombre, ra_h, dec_deg, filas, columnas, solape_pct, fotos_por_tesela,
                usar_dark=True, tipo="", centrar=None):
        conf = leer_config()
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya hay un mosaico en marcha")
            otro = deps.otro_trabajo_en_curso()
            if otro:
                raise RuntimeError(f"Espera a que termine el trabajo de {otro}")
            plan = deps.plan_en_marcha()
            if plan:
                raise RuntimeError(plan)
            filas, columnas = int(filas), int(columnas)
            if not (1 <= filas <= 6 and 1 <= columnas <= 6) or filas * columnas < 2:
                raise RuntimeError("El mosaico necesita entre 2 y 36 teselas (filas × columnas de 1 a 6)")
            self._reiniciar()
            self._cancelar.clear()
            self._pausa.clear()
            self._saltar.clear()
            self.deps = deps
            self.conf = conf
            if centrar is not None:
                self.conf["centrar"] = bool(centrar)
            self.nombre = nombre or "Mosaico"
            self.tipo = tipo or ""
            self.ra_h, self.dec_deg = float(ra_h), float(dec_deg)
            self.filas, self.columnas = filas, columnas
            self.solape_pct = float(solape_pct)
            self.fotos_por_tesela = max(1, int(fotos_por_tesela))
            self.usar_dark = bool(usar_dark)
            self.campo = campo_camara()
            self.en_curso = True
            self.inicio = datetime.now()
            self.fase = "goto"
            self.mensaje = "Preparando el mosaico..."
        self._hilo = threading.Thread(target=self._ejecutar, name="mosaico", daemon=True)
        self._hilo.start()
        return self._hilo

    def pausar(self):
        self._pausa.set()
        if self.fase == "apilando" and self.deps is not None:
            self.deps.bucle.pausar()
        self._evento("Mosaico pausado")

    def reanudar(self):
        self._pausa.clear()
        if self.deps is not None and self.deps.bucle.activo:
            self.deps.bucle.reanudar()
        self._evento("Mosaico reanudado")

    def detener(self):
        if not self.en_curso:
            return
        self._cancelar.set()
        self._pausa.clear()
        if self.deps is not None:
            try:
                self.deps.cancelar_centrado()
            except Exception:
                pass

    def saltar(self):
        if self.en_curso:
            self._saltar.set()

    @property
    def pausado(self):
        return self._pausa.is_set()

    def estado(self):
        with self._lock:
            d = {
                "en_curso": self.en_curso,
                "pausado": self._pausa.is_set(),
                "fase": self.fase,
                "nombre": self.nombre,
                "ra_h": self.ra_h, "dec_deg": self.dec_deg,
                "filas": self.filas, "columnas": self.columnas, "solape_pct": self.solape_pct,
                "fotos_por_tesela": self.fotos_por_tesela,
                "teselas": [dict(t) for t in self.teselas],
                "actual": self.actual,
                "fotos": self.fotos,
                "hechas": sum(1 for t in self.teselas if t["estado"] == "hecha"),
                "mensaje": self.mensaje,
                "eventos": list(self.eventos),
                "inicio": self.inicio.strftime("%H:%M") if self.inicio else "",
                "resultado": self.resultado,
                "archivo_galeria": self.archivo_galeria,
                "angulo_deg": self.angulo_deg, "origen_angulo": self.origen_angulo,
                "campo": self.campo,
                "version_estado": self.version_estado,
                "combinando": self.combinando,
                "mensaje_combinar": self.mensaje_combinar,
                "ultimo_combinado": self.ultimo_combinado,
                "version_mosaico": self.version_mosaico,
                "existe": os.path.exists(RUTA_PREVIEW),
            }
        if not self.en_curso and not self.nombre:
            # Sin trabajo en memoria (p. ej. tras reiniciar la app): lo que haya en disco
            defin = leer_definicion()
            if defin:
                d.update({k: defin.get(k) for k in ("nombre", "ra_h", "dec_deg", "filas", "columnas", "solape_pct")})
                d["teselas"] = defin.get("teselas", [])
                d["hechas"] = sum(1 for t in d["teselas"] if t.get("estado") == "hecha")
                d["resultado"] = defin.get("resultado", "")
        return d

    # ── Auxiliares ────────────────────────────────────────────────────
    def _evento(self, texto, decir=None):
        with self._lock:
            self.eventos.append({"hora": datetime.now().strftime("%H:%M"), "texto": texto})
            self.mensaje = texto
            self.version_estado += 1
        log.info("Mosaico: %s", texto)
        if decir and self.deps is not None:
            try:
                self.deps.decir(decir)
            except Exception:
                pass

    def _set(self, **campos):
        with self._lock:
            for k, v in campos.items():
                setattr(self, k, v)
            self.version_estado += 1

    def _tesela(self, i, **campos):
        with self._lock:
            self.teselas[i].update(campos)
            self.version_estado += 1

    def _comprobar(self):
        if self._cancelar.is_set():
            raise MosaicoDetenido()
        if self._pausa.is_set():
            self._esperar_reanudar()

    def _esperar_reanudar(self):
        fase = self.fase
        self._set(fase="pausado")
        while self._pausa.is_set():
            if self._cancelar.is_set():
                raise MosaicoDetenido()
            self._cancelar.wait(0.5)
        self._set(fase=fase)

    def _dormir(self, segundos):
        fin = time.time() + segundos
        while True:
            self._comprobar()
            resto = fin - time.time()
            if resto <= 0:
                return
            self._cancelar.wait(min(1.0, resto))

    def definicion(self):
        with self._lock:
            return {"nombre": self.nombre, "tipo": self.tipo, "ra_h": self.ra_h, "dec_deg": self.dec_deg,
                    "filas": self.filas, "columnas": self.columnas, "solape_pct": self.solape_pct,
                    "fotos_por_tesela": self.fotos_por_tesela, "teselas": [dict(t) for t in self.teselas],
                    "angulo_deg": self.angulo_deg, "origen_angulo": self.origen_angulo,
                    "inicio": self.inicio.isoformat(timespec="seconds") if self.inicio else "",
                    "resultado": self.resultado, "campo": self.campo}

    def _wcs_actual(self):
        """WCS con los ejes de la cámara (el real de la última tesela o el previsto) centrado en el mosaico."""
        campo = self.campo
        if self._ultimo_wcs is not None:
            hechas = any(t["estado"] == "hecha" for t in self.teselas)
            return (wcs_tangente(self.ra_h * 15.0, self.dec_deg, cd_de(self._ultimo_wcs), campo["ancho_px"], campo["alto_px"]),
                    "WCS de la tesela anterior" if hechas else "plate solving en el centro")
        try:
            lugar = self.deps.lugar()
        except Exception:
            lugar = None
        return wcs_previsto(self.ra_h, self.dec_deg, campo, lugar)

    def _medir_orientacion_en_el_centro(self):
        """
        GoTo al centro del mosaico con centrado por plate solving: la resolución deja el giro
        real de la cámara en ese apuntado (rotacion_camara.py) y con él se coloca la rejilla.
        Sin esto la primera tesela iría con el giro previsto, que puede estar lejos del real
        (comprobado con el simulador: 31° previstos frente a 90° reales, y la rejilla salió
        torcida). Si no se centra o no resuelve, se sigue con la previsión.
        """
        if not self.conf["centrar"]:
            return
        deps = self.deps
        self._set(fase="goto")
        self._evento(f"GoTo al centro del mosaico ({self.nombre}) para medir el giro de la cámara")
        antes = time.time()
        r = deps.goto_sincrono(self.nombre, self.ra_h, self.dec_deg, "Mosaico", centrar=True, cancelar=self._cancelar,
                               info={"type": self.tipo} if self.tipo else None, al_centrar=lambda: self._set(fase="centrando"))
        if self._cancelar.is_set():
            raise MosaicoDetenido()
        if not r.get("ok"):
            self._evento(f"El GoTo al centro ha fallado ({r.get('motivo')}); la rejilla va con el giro previsto")
            return
        try:
            import rotacion_camara
            rot = rotacion_camara.leer()
            fecha = datetime.fromisoformat(rot["fecha"]).timestamp() if rot and rot.get("fecha") else 0
            if rot and fecha >= antes - 1:
                campo = self.campo
                cd = cd_desde_angulo(campo["escala_arcsec"] / 3600.0, float(rot["grados"]))
                self._ultimo_wcs = wcs_tangente(self.ra_h * 15.0, self.dec_deg, cd, campo["ancho_px"], campo["alto_px"])
                self._evento(f"Giro de la cámara medido en el centro: {float(rot['grados']):.1f}°")
            else:
                self._evento("El centrado no resolvió ninguna foto; la rejilla va con el giro previsto")
        except Exception as e:
            log.warning("Mosaico: no se pudo leer el giro medido: %s", e)

    def _recalcular_teselas(self):
        """Coloca (o recoloca) las teselas pendientes con la orientación actual de la cámara."""
        wcs, origen = self._wcs_actual()
        nuevas = calcular_teselas(wcs, self.campo["ancho_px"], self.campo["alto_px"], self.filas, self.columnas, self.solape_pct)
        with self._lock:
            self.angulo_deg = round(angulo_de(cd_de(wcs)), 1)
            self.origen_angulo = origen
            if not self.teselas:
                self.teselas = [dict(t, estado="pendiente", fotos=0, motivo="", archivo="") for t in nuevas]
            else:
                for t, n in zip(self.teselas, nuevas):
                    if t["estado"] == "pendiente":
                        t.update({"ra_h": n["ra_h"], "dec_deg": n["dec_deg"]})
            self.version_estado += 1

    # ── Hilo ──────────────────────────────────────────────────────────
    def _ejecutar(self):
        deps = self.deps
        originales = deps.leer_dispositivos()
        resultado = "terminado"
        try:
            limpiar()
            os.makedirs(CARPETA, exist_ok=True)
            self._medir_orientacion_en_el_centro()
            self._recalcular_teselas()
            tam = tamano_mosaico(self.campo, self.filas, self.columnas, self.solape_pct)
            self._evento(f"Mosaico {self.filas}×{self.columnas} de {self.nombre}: {tam[0]:.1f}° × {tam[1]:.1f}°, "
                         f"{self.fotos_por_tesela} fotos por tesela, cámara a {self.angulo_deg}° ({self.origen_angulo})",
                         decir=f"Empieza el mosaico de {self.nombre}: {len(self.teselas)} teselas")
            guardar_definicion(self.definicion())
            deps.guardar_config("Dispositivos", {"numero_maximo_imagenes": self.fotos_por_tesela})
            for t in list(self.teselas):
                self._comprobar()
                self._fotografiar_tesela(t["i"])
                guardar_definicion(self.definicion())
            self._esperar_combinacion()
            hechas = [t for t in self.teselas if t["estado"] == "hecha"]
            if hechas and self.conf["guardar_galeria"] and os.path.exists(RUTA_FITS):
                self._set(fase="guardando")
                self._evento("Guardando el mosaico en la galería")
                try:
                    g = deps.guardar_galeria()
                    if g.get("ok", True) and g.get("en_curso"):
                        g = deps.esperar_guardado()
                    if g.get("ok"):
                        self._set(archivo_galeria=g.get("archivo", ""))
                except Exception as e:
                    log.exception("Mosaico: error guardando en la galería")
                    self._evento(f"No se pudo guardar el mosaico en la galería: {e}")
            texto = f"Mosaico terminado: {len(hechas)} de {len(self.teselas)} teselas"
            if self.archivo_galeria:
                texto += f", guardado como {self.archivo_galeria}"
            self._evento(texto, decir=f"Mosaico de {self.nombre} terminado, {len(hechas)} teselas")
        except MosaicoDetenido:
            resultado = "detenido"
            self._evento("Mosaico detenido por el usuario", decir="Mosaico detenido")
            self._esperar_combinacion()
        except Exception as e:
            resultado = "error"
            log.exception("Error en el mosaico")
            self._evento(f"Mosaico detenido por un error: {e}", decir="Mosaico detenido por un error")
        finally:
            try:
                if deps.bucle.activo:
                    deps.detener_apilado()
            except Exception as e:
                log.warning("Mosaico: no se pudo detener el apilado al terminar: %s", e)
            try:
                deps.guardar_config("Dispositivos", {"numero_maximo_imagenes": originales.get("numero_maximo_imagenes", "20")})
            except Exception as e:
                log.warning("Mosaico: no se pudo restaurar config.ini: %s", e)
            try:
                deps.anotar_objeto(self.nombre, self.ra_h, self.dec_deg, self.tipo)
            except Exception:
                pass
            with self._lock:
                self.en_curso = False
                self.fase = "terminado"
                self.actual = None
                self.resultado = resultado
                self.version_estado += 1
            try:
                guardar_definicion(self.definicion())
            except Exception:
                pass

    def _fotografiar_tesela(self, i):
        deps = self.deps
        n = len(self.teselas)
        self._recalcular_teselas()
        t = self.teselas[i]
        self._tesela(i, estado="en_curso", motivo="")
        self._set(actual=i, fotos=0, fase="goto")
        rotulo = f"{self.nombre} · tesela {i + 1}/{n}"
        t_inicio = time.time()
        self._evento(f"Tesela {i + 1}/{n} (fila {t['fila'] + 1}, columna {t['col'] + 1}): GoTo",
                     decir=f"Tesela {i + 1} de {n}")
        r = deps.goto_sincrono(rotulo, t["ra_h"], t["dec_deg"], "Mosaico", centrar=self.conf["centrar"],
                               cancelar=self._cancelar, info={"type": self.tipo} if self.tipo else None,
                               al_centrar=lambda: self._set(fase="centrando"))
        if self._cancelar.is_set():
            raise MosaicoDetenido()
        if not r.get("ok"):
            motivo = r.get("motivo") or "GoTo fallido"
            self._tesela(i, estado="fallida", motivo=motivo)
            self._evento(f"Tesela {i + 1}: {motivo}; se salta")
            self._dormir(2)
            return
        if r.get("centrado") in ("error", "cancelado"):
            # Sin centrar también vale: el WCS de la tesela dice dónde cayó de verdad
            self._evento(f"Tesela {i + 1}: centrado fallido ({r.get('detalle', '')}); se apila donde apunta")
        elif r.get("centrado"):
            self._evento(f"Tesela {i + 1}: {r.get('detalle') or r['centrado']}")

        self._set(fase="apilando")
        deps.arrancar_apilado(self.usar_dark)
        motivo = self._vigilar_apilado(i)
        deps.detener_apilado()
        self._esperar_procesado()
        total = int(deps.estado_apilado().get("total") or 0)
        self._set(fotos=total)
        archivo = ""
        if total >= 1 and os.path.exists(RUTA_APILADO):
            try:
                hdr = fits.getheader(RUTA_APILADO)
                if wcs_de_cabecera(hdr) is None:
                    raise ValueError("el apilado no lleva WCS")
                archivo = os.path.join(CARPETA, f"tesela_{i + 1:02d}.fits")
                shutil.copy(RUTA_APILADO, archivo)
                self._ultimo_wcs = wcs_de_cabecera(hdr)
                centro = self._ultimo_wcs.all_pix2world([[hdr["NAXIS1"] / 2.0, hdr["NAXIS2"] / 2.0]], 0)[0]
                self._tesela(i, estado="hecha", fotos=total, archivo=os.path.basename(archivo),
                             motivo="" if motivo == "completa" else motivo,
                             ra_real_h=float(centro[0]) / 15.0, dec_real_deg=float(centro[1]))
                # Para el log del campo: error de apuntado (previsto frente al WCS real), giro de la cámara y duración
                d_ra = ((float(centro[0]) - t["ra_h"] * 15.0 + 180.0) % 360.0 - 180.0) * math.cos(math.radians(t["dec_deg"]))
                error_arcmin = math.hypot(d_ra, float(centro[1]) - t["dec_deg"]) * 60.0
                log.info("Mosaico: tesela %d/%d apuntada a %.1f' del centro previsto, cámara a %.1f°, %.0f s (GoTo+centrado+%d fotos)",
                         i + 1, n, error_arcmin, angulo_de(cd_de(self._ultimo_wcs)), time.time() - t_inicio, total)
                self._evento(f"Tesela {i + 1} hecha: {total} fotos" + ("" if motivo == "completa" else f" ({motivo})"))
            except Exception as e:
                log.exception("Mosaico: no se pudo guardar la tesela")
                self._tesela(i, estado="fallida", fotos=total, motivo=str(e))
                self._evento(f"Tesela {i + 1}: no se pudo guardar ({e})")
        else:
            self._tesela(i, estado="fallida", fotos=total, motivo=motivo or "sin fotos")
            self._evento(f"Tesela {i + 1}: sin fotos apiladas ({motivo})")
        deps.reiniciar_apilado()
        if archivo:
            self._combinar_en_segundo_plano()
        if motivo == "detenida":
            raise MosaicoDetenido()

    def _vigilar_apilado(self, i):
        """Sigue el apilado de la tesela hasta que toque parar; devuelve el motivo."""
        deps = self.deps
        t_ultima = time.time()
        total_visto = 0
        while True:
            if self._cancelar.is_set():
                return "detenida"
            if self._saltar.is_set():
                self._saltar.clear()
                return "saltada a mano"
            e = deps.estado_apilado()
            total = int(e.get("total") or 0)
            if total > total_visto:
                total_visto = total
                t_ultima = time.time()
                self._set(fotos=total)
                self._tesela(i, fotos=total)
            if total >= self.fotos_por_tesela:
                return "completa"
            bucle = deps.bucle
            if self._pausa.is_set() or bucle.pausado:
                if self._pausa.is_set() and not bucle.pausado:
                    bucle.pausar()
                self._set(fase="pausado")
                t_ultima += 2.0
                self._cancelar.wait(2.0)
                if not self._pausa.is_set() and bucle.pausado:
                    bucle.reanudar()
                if not self._pausa.is_set() and not bucle.pausado:
                    self._set(fase="apilando")
                continue
            if not bucle.activo:
                # Lo paró alguien (errores de cámara, botón Detener del apilado): la tesela se da por
                # terminada con lo que tenga y el mosaico se queda en pausa para que el usuario decida
                motivo = f"apilado detenido ({bucle.ultimo_mensaje or 'sin motivo'})"
                self._evento(f"Tesela {i + 1}: {motivo}; mosaico en pausa", decir="Apilado detenido. Mosaico en pausa")
                self._pausa.set()
                return motivo
            if time.time() - t_ultima > self.conf["minutos_sin_fotos"] * 60:
                return f"sin fotos aceptadas en {self.conf['minutos_sin_fotos']:.0f} min"
            self._cancelar.wait(2.0)

    def _esperar_procesado(self, timeout=120):
        fin = time.time() + timeout
        while time.time() < fin:
            if not self.deps.estado_apilado().get("procesando"):
                return True
            time.sleep(1)
        return False

    # ── Combinación en segundo plano ──────────────────────────────────
    def _combinar_en_segundo_plano(self):
        with self._lock_combinar:
            if self._hilo_combinar is not None and self._hilo_combinar.is_alive():
                self._recombinar_pendiente = True
                return
            self._hilo_combinar = threading.Thread(target=self._bucle_combinar, name="mosaico-combinar", daemon=True)
            self._hilo_combinar.start()

    def _bucle_combinar(self):
        while True:
            self.combinar_ahora()
            with self._lock_combinar:
                if not self._recombinar_pendiente:
                    return
                self._recombinar_pendiente = False

    def _esperar_combinacion(self, timeout=600):
        h = self._hilo_combinar
        if h is not None and h.is_alive():
            self._set(fase="combinando")
            h.join(timeout)

    def combinar_ahora(self, rutas=None, definicion=None):
        """
        Combina las teselas que hay (en disco) y publica el FITS y la vista previa.
        Devuelve la info de la combinación; con error, {"error": texto}. Se puede llamar
        con el trabajo parado (botón Recombinar) a partir de la definición guardada.
        """
        d = definicion or (self.definicion() if self.nombre else leer_definicion())
        if not d or d.get("ra_h") is None:
            return {"error": "No hay ningún mosaico definido"}
        rutas = rutas or teselas_en_disco()
        if not rutas:
            return {"error": "No hay teselas apiladas"}
        with self._lock:
            self.combinando = True
            self.mensaje_combinar = "combinando..."
            self.version_estado += 1

        def progreso(texto):
            with self._lock:
                self.mensaje_combinar = texto
        t0 = time.time()
        try:
            cubo, hdr, info = combinar(rutas, d["ra_h"], d["dec_deg"], progreso=progreso)
            publicar(cubo, hdr, d.get("nombre") or "Mosaico", d.get("filas", 0), d.get("columnas", 0), progreso=progreso)
            del cubo
            info["segundos"] = round(time.time() - t0, 1)
            info["hora"] = datetime.now().strftime("%H:%M:%S")
            with self._lock:
                self.version_mosaico += 1
                self.ultimo_combinado = info
                self.mensaje_combinar = ""
                self.combinando = False
                self.version_estado += 1
            log.info("Mosaico combinado: %d teselas, %d×%d px en %.1f s (residuos %s px, fondos %s)", info["teselas"],
                     info["ancho"], info["alto"], info["segundos"], info["residuos_px"], info["offsets"])
            return info
        except Exception as e:
            log.exception("Error combinando el mosaico")
            with self._lock:
                self.ultimo_combinado = {"error": str(e), "hora": datetime.now().strftime("%H:%M:%S")}
                self.mensaje_combinar = ""
                self.combinando = False
                self.version_estado += 1
            return {"error": str(e)}


trabajo_mosaico = TrabajoMosaico()
