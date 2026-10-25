# galeria_anotada.py
#
# Objetos anotados sobre las fotos de la galería: dónde cae en el JPG guardado
# cada objeto del campo (catálogo local y, si hay Internet, SIMBAD; los mismos
# que anota el apilado en vivo). La web los pinta encima de la foto como una
# capa que se enciende y se apaga, sin tocar el JPG.
#
#   1. El campo se resuelve con ASTAP sobre el FITS apilado que acompaña al JPG
#      (a campo completo resuelve siempre; un recorte de zoom, no). Tarda unos
#      segundos, así que la solución se guarda en datos/anotaciones_galeria.json
#      y la siguiente vez es inmediato. La clave es el tamaño y la fecha del
#      FITS, no su nombre: renombrar la foto en el editor no obliga a resolver
#      otra vez.
#   2. Del FITS al JPG se pasa con el encuadre con que se hizo el JPG (zoom,
#      recorte de bordes, giro y espejo), que está en su JSON: en "ajustes" si
#      se guardó desde el editor y en "encuadre" si viene del apilado.

import os
import json
import math
import time
import logging
import tempfile
import threading

from astropy.io import fits
from astropy.io.fits import Header
from astropy.wcs import WCS
from PIL import Image

import firma
import procesado
import anotaciones
from editor_fotos import ErrorEditor, rutas
from idioma import _

log = logging.getLogger("scopepower.galeria_anotada")

RUTA_CACHE = os.path.join("datos", "anotaciones_galeria.json")
CADA_INTERNET_S = 600          # sin red (en el campo) no se vuelve a comprobar en 10 minutos: son 3 s de espera
MAG_MAX_SIMBAD = 15.0          # SIMBAD cuela objetos de magnitud 21 (nebulosas planetarias de M32) que no salen en la foto
_lock = threading.Lock()
_internet = {"cuando": 0.0, "hay": False}


def _hay_internet():
    if time.time() - _internet["cuando"] > CADA_INTERNET_S:
        _internet.update({"cuando": time.time(), "hay": anotaciones.hay_internet()})
    return _internet["hay"]


def _leer_cache():
    try:
        with open(RUTA_CACHE, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def _guardar_en_cache(clave, solucion):
    cache = _leer_cache()
    cache[clave] = solucion
    try:
        os.makedirs(os.path.dirname(RUTA_CACHE), exist_ok=True)
        with open(RUTA_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as e:
        log.warning("No se pudo guardar la solución del campo: %s", e)


def _resolver(ruta_fits):
    """Plate solving del FITS y lo que hace falta para anotarlo, en un dict que se puede guardar en JSON."""
    with fits.open(ruta_fits) as hdul:
        datos = hdul[0].data
        if datos is None or datos.ndim != 3 or 3 not in (datos.shape[0], datos.shape[-1]):
            raise ErrorEditor(_("El FITS no es un apilado RGB de ScopePower"))
        if datos.shape[0] != 3:
            datos = datos.transpose(2, 0, 1)
        alto, ancho = datos.shape[1:]
        # La misma zona útil que usa el editor al hacer el JPG: la de la cabecera o, si no la trae, la detectada
        zona = procesado.leer_zona(hdul[0].header) or procesado.zona_automatica(datos)
    with tempfile.TemporaryDirectory(prefix="scopepower_anot_") as carpeta:
        res = anotaciones.resolver_wcs(ruta_fits, ruta_mono=os.path.join(carpeta, "foto_mono.fits"))
        if res is None:
            log.warning("No se pudo resolver el campo de %s para las anotaciones de la galería", os.path.basename(ruta_fits))
            raise ErrorEditor(_("ASTAP no ha podido resolver la foto (¿pocas estrellas?)"))
        wcs, escala_arcsec, angulo_cam, ruta_ini = res
        simbad = anotaciones.objetos_simbad(ruta_ini, ruta_fits) if _hay_internet() else None
    return {"ancho": int(ancho), "alto": int(alto), "zona": [int(v) for v in zona] if zona else None,
            "crpix": [float(v) for v in wcs.wcs.crpix], "crval": [float(v) for v in wcs.wcs.crval],
            "cd": [[float(v) for v in fila] for fila in wcs.wcs.cd],
            "escala_arcsec": float(escala_arcsec), "angulo_cam": float(angulo_cam),
            "simbad": simbad, "resuelta": time.strftime("%Y-%m-%d %H:%M:%S")}


def _wcs(solucion):
    hdr = Header()
    hdr["CRPIX1"], hdr["CRPIX2"] = solucion["crpix"]
    hdr["CRVAL1"], hdr["CRVAL2"] = solucion["crval"]
    (hdr["CD1_1"], hdr["CD1_2"]), (hdr["CD2_1"], hdr["CD2_2"]) = solucion["cd"]
    hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN", "DEC--TAN"
    hdr["CUNIT1"], hdr["CUNIT2"] = "deg", "deg"
    return WCS(hdr)


def solucion_de(ruta_fits):
    """(solución del campo, recién resuelta) del FITS: la guardada si vale, si no se resuelve ahora (unos segundos)."""
    try:
        st = os.stat(ruta_fits)
    except OSError:
        raise ErrorEditor(_("Esta foto no tiene FITS guardado; no se puede anotar"))
    clave = f"{st.st_size}_{st.st_mtime_ns}"
    if not _lock.acquire(blocking=False):
        raise ErrorEditor(_("Ya se está resolviendo una foto; espera a que termine"))
    try:
        guardada = _leer_cache().get(clave)
        # Si se resolvió sin Internet le faltan los objetos de SIMBAD: con red, se vuelve a resolver
        if guardada and (guardada.get("simbad") is not None or not _hay_internet()):
            return guardada, False
        solucion = _resolver(ruta_fits)
        _guardar_en_cache(clave, solucion)
        log.info("Campo de %s resuelto para las anotaciones de la galería", os.path.basename(ruta_fits))
        return solucion, True
    finally:
        _lock.release()


def _matriz_al_jpg(meta, solucion, ruta_jpg):
    """(M, ancho, alto): afín 2×3 del píxel del FITS al del JPG guardado, y el tamaño de ese JPG."""
    with Image.open(ruta_jpg) as im:
        ancho_jpg, alto_jpg = im.size
    zona = solucion.get("zona")
    enc = meta.get("ajustes") or meta.get("encuadre")
    if enc:
        fraccion = (100.0 - float(enc.get("zoom", 0) or 0)) / 100.0
        giro, espejo = float(enc.get("giro", 0) or 0), bool(enc.get("espejo"))
        margen = float(enc.get("bordes", 0) or 0) / 100.0
        centro = enc.get("centro") or None          # el editor puede centrar el zoom fuera del centro del campo
    else:
        # Foto anterior a que se guardara el encuadre: sin giro, y el zoom se deduce del ancho del JPG
        ancho_zona = zona[2] if zona else solucion["ancho"]
        fraccion, giro, espejo, margen, centro = min(1.0, ancho_jpg / float(ancho_zona)), 0.0, False, 0.0, None
    M, ancho, alto = procesado.matriz_campo(solucion["ancho"], solucion["alto"], fraccion, giro, zona, margen, espejo,
                                            centro=centro)
    # El JPG se guarda a resolución completa; si no (se quedó la vista previa), se lleva a su tamaño
    M = M.copy()
    M[0] *= ancho_jpg / float(ancho)
    M[1] *= alto_jpg / float(alto)
    return M, ancho_jpg, alto_jpg


def objetos_foto(nombre):
    """
    Objetos del campo de una foto de la galería en píxeles de su JPG: dict con
    ancho y alto del JPG y la lista de objetos (nombre, mag, x, y, semiejes a y b,
    ángulo de la elipse en grados o None si es un círculo, fuente). Primero los
    del catálogo local y los grandes, que es el orden en que se rotulan.
    """
    __, ruta_jpg, ruta_fits, __ = rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditor(_("Imagen no encontrada"))
    solucion, nueva = solucion_de(ruta_fits)
    wcs = _wcs(solucion)
    M, ancho, alto = _matriz_al_jpg(firma.meta_de_archivo(ruta_jpg), solucion, ruta_jpg)
    factor = math.sqrt(abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0]))
    escala, angulo_cam = solucion["escala_arcsec"], solucion["angulo_cam"]
    objetos = anotaciones.objetos_catalogo_en_campo(wcs, solucion["ancho"], solucion["alto"])
    objetos = anotaciones._fusionar(objetos, anotaciones.objetos_ngc_en_campo(wcs, solucion["ancho"], solucion["alto"]), wcs)
    objetos = anotaciones._fusionar(objetos, solucion.get("simbad") or [], wcs)
    objetos.sort(key=anotaciones.orden_rotulos)
    salida = []
    for o in objetos:
        try:
            xw, yw = (float(v[0]) for v in wcs.all_world2pix([o["ra"]], [o["dec"]], 0))
            x = M[0, 0] * xw + M[0, 1] * yw + M[0, 2]
            y = M[1, 0] * xw + M[1, 1] * yw + M[1, 2]
            if not (0 <= x < ancho and 0 <= y < alto):
                continue
            a = (float(o["tamano_arcsec"]) / 2.0) / escala * factor
            b = (float(o.get("tamano_menor_arcsec") or o["tamano_arcsec"]) / 2.0) / escala * factor
            if not (math.isfinite(a) and math.isfinite(b)):
                continue
            angulo = None
            pa = anotaciones.pa_de(o)
            if pa is not None:
                # Como anotaciones.dibujar: medido con el WCS, vale con el encuadre girado o en espejo
                angulo = round(anotaciones.angulo_en_imagen(wcs, o["ra"], o["dec"], pa, M), 1)
            mag = o.get("mag")
            if o.get("fuente") == "simbad" and mag is not None and float(mag) > MAG_MAX_SIMBAD:
                continue
            salida.append({"nombre": " ".join(str(o["nombre"]).split()), "x": round(x, 1), "y": round(y, 1),
                           "a": round(a, 1), "b": round(b, 1), "angulo": angulo,
                           "mag": round(float(mag), 1) if mag is not None and math.isfinite(float(mag)) else None,
                           "fuente": o.get("fuente", "simbad")})
        except Exception as e:
            log.warning("No se pudo situar %s: %s", o.get("nombre", "?"), e)
    return {"ok": True, "ancho": ancho, "alto": alto, "objetos": salida,
            "simbad": solucion.get("simbad") is not None, "resuelta_ahora": nueva}
