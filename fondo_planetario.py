# fondo_planetario.py
#
# Fotos de la galería sobre el cielo del planetario: cada apilado o mosaico guardado
# se pinta como fondo, deformado a su huella real en el cielo y con transparencia,
# para ver qué está ya fotografiado y encajar el siguiente mosaico encima.
#
# De cada foto hace falta dónde cae cada esquina de su JPG en RA/Dec:
#   - El FITS hermano lleva el WCS de la referencia del apilado (desde el 14-oct-2026;
#     los mosaicos, el del propio mosaico). Las fotos anteriores solo entran si la
#     galería anotada ya las resolvió con ASTAP (datos/anotaciones_galeria.json).
#   - Del píxel del FITS al del JPG guardado se pasa con el mismo encuadre que usan las
#     anotaciones de la galería (galeria_anotada._matriz_al_jpg: zona, zoom, giro,
#     espejo); aquí se invierte para llevar las esquinas del JPG al FITS y de ahí al cielo.
# El resultado se guarda en datos/fondo_planetario.json (clave: tamaño y fecha del FITS y
# del JPG) y las imágenes se sirven reducidas (datos/planetario_fotos/) para la tablet.

import os
import json
import math
import logging
import threading

import numpy as np
from astropy.io import fits
from PIL import Image

import firma
import procesado
import galeria_anotada
from editor_fotos import rutas, partes_nombre, CARPETA_FOTOS

log = logging.getLogger("scopepower.fondo_planetario")

RUTA_CACHE = os.path.join("datos", "fondo_planetario.json")
CARPETA_MINIATURAS = os.path.join("datos", "planetario_fotos")
LADO_MINIATURA = 1024
SUFIJOS_FUERA = ("_comparacion", "_mas", "_stf", "_nucleo")
_lock = threading.Lock()


def _leer_cache():
    try:
        with open(RUTA_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _guardar_cache(cache):
    try:
        os.makedirs(os.path.dirname(RUTA_CACHE), exist_ok=True)
        with open(RUTA_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as e:
        log.warning("No se pudo guardar la caché de fotos del planetario: %s", e)


def _solucion_de(ruta_fits):
    """Solución del campo (como la de galeria_anotada) del WCS de la cabecera del FITS o de la caché de la galería anotada."""
    hdr = fits.getheader(ruta_fits)
    claves = ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CD1_1", "CD1_2", "CD2_1", "CD2_2")
    if all(k in hdr for k in claves):
        cd = [[float(hdr["CD1_1"]), float(hdr["CD1_2"])], [float(hdr["CD2_1"]), float(hdr["CD2_2"])]]
        det = cd[0][0] * cd[1][1] - cd[0][1] * cd[1][0]
        zona = procesado.leer_zona(hdr)
        return {"ancho": int(hdr["NAXIS1"]), "alto": int(hdr["NAXIS2"]), "zona": list(zona) if zona else None,
                "crpix": [float(hdr["CRPIX1"]), float(hdr["CRPIX2"])], "crval": [float(hdr["CRVAL1"]), float(hdr["CRVAL2"])],
                "cd": cd, "escala_arcsec": math.sqrt(abs(det)) * 3600.0,
                "angulo_cam": math.degrees(math.atan2(cd[0][1], cd[0][0]))}
    st = os.stat(ruta_fits)
    return galeria_anotada._leer_cache().get(f"{st.st_size}_{st.st_mtime_ns}")


def _huella(nombre):
    """{nombre, ancho, alto, esquinas [[ra, dec]×4], area_deg2, objeto, fecha} del JPG, o None si no se sabe dónde cae."""
    __, ruta_jpg, ruta_fits, __ = rutas(nombre)
    if not os.path.isfile(ruta_fits):
        return None
    solucion = _solucion_de(ruta_fits)
    if not solucion:
        return None
    wcs = galeria_anotada._wcs(solucion)
    meta = firma.meta_de_archivo(ruta_jpg) or {}
    M, ancho, alto = galeria_anotada._matriz_al_jpg(meta, solucion, ruta_jpg)
    inv = np.linalg.inv(np.vstack([M, [0.0, 0.0, 1.0]]))
    esquinas_jpg = np.array([[0, 0, 1], [ancho, 0, 1], [ancho, alto, 1], [0, alto, 1]], dtype=np.float64)
    px = (inv @ esquinas_jpg.T).T[:, :2]
    cielo = wcs.all_pix2world(px, 0)
    if not np.all(np.isfinite(cielo)):
        return None
    escala = float(solucion["escala_arcsec"]) / 3600.0
    factor = math.sqrt(abs(np.linalg.det(M[:, :2])))      # px del JPG por px del FITS
    area = (ancho / factor) * (alto / factor) * escala * escala
    return {"nombre": nombre, "ancho": int(ancho), "alto": int(alto),
            "esquinas": [[round(float(a), 5), round(float(b), 5)] for a, b in cielo],
            "area_deg2": round(area, 3), "objeto": meta.get("objeto") or partes_nombre(nombre)[0].replace("_", " "),
            "fecha": meta.get("fecha") or ""}


def lista():
    """Fotos de la galería que se pueden poner sobre el cielo, las grandes primero (los mosaicos debajo de las fotos sueltas)."""
    with _lock:
        cache = _leer_cache()
        salida, usadas, cambiado = [], set(), False
        for f in sorted(os.listdir(CARPETA_FOTOS)):
            if not f.lower().endswith(".jpg") or os.path.splitext(f)[0].endswith(SUFIJOS_FUERA):
                continue
            ruta_jpg = os.path.join(CARPETA_FOTOS, f)
            ruta_fits = os.path.splitext(ruta_jpg)[0] + ".fits"
            if not os.path.isfile(ruta_fits):
                continue
            try:
                sj, sf = os.stat(ruta_jpg), os.stat(ruta_fits)
                clave = f"{f}|{sj.st_size}_{sj.st_mtime_ns}|{sf.st_size}_{sf.st_mtime_ns}"
                usadas.add(clave)
                if clave in cache:
                    huella = cache[clave]
                else:
                    huella = _huella(f)
                    cache[clave] = huella
                    cambiado = True
                if huella:
                    salida.append(huella)
            except Exception as e:
                log.warning("Fondo del planetario: %s no se puede situar (%s)", f, e)
        if cambiado or len(cache) != len(usadas):
            _guardar_cache({k: v for k, v in cache.items() if k in usadas})
    salida.sort(key=lambda h: -h["area_deg2"])
    return salida


def miniatura(nombre, lado=LADO_MINIATURA):
    """Ruta de la versión reducida del JPG (se hace la primera vez y se guarda en datos/planetario_fotos)."""
    base, ruta_jpg, __, __ = rutas(nombre)
    st = os.stat(ruta_jpg)
    os.makedirs(CARPETA_MINIATURAS, exist_ok=True)
    destino = os.path.join(CARPETA_MINIATURAS, f"{os.path.basename(base)}_{st.st_mtime_ns}_{lado}.jpg")
    if not os.path.isfile(destino):
        with Image.open(ruta_jpg) as im:
            im = im.convert("RGB")
            im.thumbnail((lado, lado), Image.LANCZOS)
            im.save(destino, "JPEG", quality=82, optimize=True)
        # Versiones viejas de la misma foto
        for viejo in os.listdir(CARPETA_MINIATURAS):
            if viejo.startswith(os.path.basename(base) + "_") and viejo != os.path.basename(destino):
                try:
                    os.remove(os.path.join(CARPETA_MINIATURAS, viejo))
                except OSError:
                    pass
    return os.path.abspath(destino)
