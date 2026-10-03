# giro_camara.py
#
# Giro y espejo de las fotos de la pantalla Cámara.
#
# La cámara va fija al tubo y la imagen llega tal como la entrega el driver (con
# la Saturn-C, boca abajo respecto a lo que se ve a simple vista). giro_camara
# (grados en horario) y espejo_camara, en [Dispositivos] de config.ini, dicen
# cómo se enseña: se aplican a la imagen ya revelada, justo antes de escribir el
# JPG, en todos los caminos de Cámara (foto de noche y de día, continuo, modo en
# vivo, timelapse, guardar en la galería). El FITS se guarda siempre sin girar.
#
# Como «Apunta aquí» recibe el toque sobre la imagen girada, cada foto que se
# enseña lleva anotado su encuadre (foto_actual) y original() deshace el giro.
#
# La imagen revelada se anota además sin girar (sin_girar): al cambiar el giro
# la foto que hay en pantalla se vuelve a girar sin repetir la captura.

import os
import configparser
import threading

import numpy as np

import procesado

RUTA_CONFIG = "config.ini"
SIN_GIRO = {"giro": 0.0, "espejo": False}

_lock = threading.Lock()
_cache = {"mtime": None, "encuadre": dict(SIN_GIRO)}


def leer():
    """{"giro": grados en horario (0..360), "espejo": bool} de config.ini (se relee solo si el fichero cambia)."""
    try:
        mtime = os.path.getmtime(RUTA_CONFIG)
    except OSError:
        return dict(SIN_GIRO)
    with _lock:
        if _cache["mtime"] != mtime:
            cfg = configparser.ConfigParser()
            cfg.read(RUTA_CONFIG)
            d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}
            try:
                giro = float(d.get("giro_camara", 0) or 0) % 360.0
            except ValueError:
                giro = 0.0
            _cache.update({"mtime": mtime, "encuadre": {
                "giro": giro, "espejo": str(d.get("espejo_camara", "false")).strip().lower() == "true"}})
        return dict(_cache["encuadre"])


def guardar(giro, espejo):
    """Escribe el giro (grados en horario) y el espejo en config.ini; devuelve lo guardado."""
    giro = float(giro)
    giro = (giro if np.isfinite(giro) else 0.0) % 360.0
    cfg = configparser.ConfigParser()
    cfg.read(RUTA_CONFIG)
    if "Dispositivos" not in cfg:
        cfg["Dispositivos"] = {}
    cfg["Dispositivos"]["giro_camara"] = f"{giro:g}"
    cfg["Dispositivos"]["espejo_camara"] = "true" if espejo else "false"
    with open(RUTA_CONFIG, "w") as f:
        cfg.write(f)
    return leer()


def aplicar(imagen, encuadre=None):
    """La imagen revelada (BGR o gris, uint8) con el giro y el espejo de `encuadre` (por defecto, los de config.ini)."""
    e = encuadre or leer()
    if not e.get("giro") and not e.get("espejo"):
        return imagen
    h, w = imagen.shape[:2]
    return procesado.girar_campo(imagen, 1.0, e["giro"], zona=(0, 0, w, h), espejo=bool(e.get("espejo")))[0]


def girar(imagen):
    """
    Para cada imagen que Cámara va a enseñar: (imagen girada, encuadre aplicado).
    El encuadre se anota con la foto (foto_actual.registrar(..., encuadre=...))
    para «Apunta aquí» y la galería.
    """
    e = leer()
    return aplicar(imagen, e), e


def tamano(ancho, alto, encuadre):
    """(ancho, alto) con que se ve, tras el giro, una imagen de ancho × alto."""
    e = encuadre or SIN_GIRO
    g = procesado.geometria_giro(ancho, alto, 1.0, e.get("giro", 0.0), bool(e.get("espejo")))
    return g["ancho"], g["alto"]


def original(fx, fy, ancho, alto, encuadre):
    """
    El punto (fx, fy) de la imagen que se ve (fracciones 0–1 del ancho y del alto,
    ya girada) como fracciones de la imagen sin girar, de ancho × alto píxeles.
    """
    e = encuadre or SIN_GIRO
    if not e.get("giro") and not e.get("espejo"):
        return fx, fy
    g = procesado.geometria_giro(ancho, alto, 1.0, e.get("giro", 0.0), bool(e.get("espejo")))
    x, y, _ = np.linalg.inv(g["T"]) @ [fx * g["ancho"] - 0.5, fy * g["alto"] - 0.5, 1.0]
    return float((x + 0.5) / ancho), float((y + 0.5) / alto)


def cenit_arriba(orientacion):
    """
    (giro, espejo) que dejan la imagen como se ve a simple vista, con el cénit
    arriba y el acimut creciendo hacia la derecha, a partir de la orientación de
    la cámara respecto a los ejes de la montura (orientacion_camara.ultima(): la
    matriz H de cualquier plate solving). None si no hay orientación.
    """
    if not orientacion or "H" not in orientacion:
        return None
    try:
        inv = np.linalg.inv(np.array(orientacion["H"], dtype=float))
    except np.linalg.LinAlgError:
        return None
    (ax, ay), (ux, uy) = inv @ [1.0, 0.0], inv @ [0.0, 1.0]      # hacia más acimut y hacia más altura, en píxeles
    return procesado.encuadre_con_arriba(float(ux), float(uy), float(ax), float(ay))
