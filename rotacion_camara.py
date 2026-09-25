# rotacion_camara.py
#
# Ángulo con el que está montada la cámara en el tubo, sacado del plate solving.
#
# ASTAP escribe en su .ini el WCS completo de la foto, y CROTA2 es el giro del
# sensor respecto al norte celeste (convención FITS: con CROTA2 = 0 el norte
# queda arriba y el este a la izquierda). El centrado, la foto de prueba y el
# apilado solo usaban el centro (CRVAL1/2); aquí se recoge también el ángulo de
# cada resolución que sale bien y se guarda en datos/rotacion_camara.json, para
# que el planetario dibuje el rectángulo de la cámara girado como está de verdad
# (mientras no se toque la cámara, el ángulo se mantiene de una noche a otra).

import json
import logging
import os
import threading
from datetime import datetime

log = logging.getLogger("scopepower.rotacion")

RUTA = os.path.join("datos", "rotacion_camara.json")
_lock = threading.Lock()
_cache = None          # último contenido leído/escrito, para no releer el fichero a cada /planetario/estado


def desde_ini(ruta_ini):
    """CROTA2 (grados) del .ini de ASTAP si la foto se resolvió; None si no."""
    try:
        with open(ruta_ini) as f:
            lineas = f.read().splitlines()
    except OSError:
        return None
    if not any(L.startswith("PLTSOLVD=T") for L in lineas):
        return None
    for L in lineas:
        if L.startswith("CROTA2="):
            try:
                return float(L.split("=", 1)[1]) % 360
            except ValueError:
                return None
    return None


def guardar(grados, origen=""):
    """Guarda el ángulo (grados, 0–360) con su origen y fecha; devuelve lo guardado."""
    global _cache
    datos = {"grados": round(float(grados) % 360, 2), "origen": origen,
             "fecha": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        try:
            os.makedirs(os.path.dirname(RUTA), exist_ok=True)
            with open(RUTA, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False)
            if _cache is None or abs(_cache.get("grados", -999) - datos["grados"]) > 0.5:
                log.info("Rotación de la cámara: %.1f° (%s)", datos["grados"], origen)
            _cache = datos
        except OSError as e:
            log.warning("No se pudo guardar la rotación de la cámara: %s", e)
    return datos


def anotar_ini(ruta_ini, origen=""):
    """
    Lee CROTA2 del .ini y lo guarda si la foto se resolvió. Devuelve el ángulo o None.
    Además guarda la orientación de la cámara respecto a los ejes de la montura
    (orientacion_camara.py), que calibra el seguimiento de objetos sin mover nada.
    """
    grados = desde_ini(ruta_ini)
    if grados is not None:
        guardar(grados, origen)
        try:
            import orientacion_camara
            orientacion_camara.registrar(ruta_ini, origen)
        except Exception as e:
            log.warning("Orientación de la cámara no registrada: %s", e)
    return grados


def leer():
    """{"grados", "origen", "fecha"} del último plate solving, o None si no hay ninguno."""
    global _cache
    with _lock:
        if _cache is None:
            try:
                with open(RUTA, encoding="utf-8") as f:
                    _cache = json.load(f)
            except (OSError, ValueError):
                return None
        return dict(_cache)
