# foto_actual.py
#
# La última foto que enseña la pantalla Cámara, venga de donde venga (foto de
# noche, foto de día, modo continuo de día o de noche, timelapse), para dos cosas:
#
#   - "Apunta aquí" de día (apuntado_teorico.py): hace falta saber qué parte del
#     sensor se ve (recorte del modo día, binning) para pasar el toque en la
#     pantalla a píxeles del sensor;
#   - "Guardar en la galería": revelar esa misma foto a resolución completa.
#
# Las fotos de día del continuo y del timelapse no se escriben en static/captura.fits
# (serían 18 MB por foto), así que el fotograma crudo se guarda aquí, en memoria (se
# sustituye con cada foto nueva). Tras reiniciar la app no hay nada en memoria y se
# usa la cabecera de static/captura.fits, que la foto de día suelta rellena con el
# recorte, el objetivo y el balance de color.

import threading
import time

_lock = threading.Lock()
_actual = None


def registrar(modo, ancho, alto, binning, campo=1.0, frame=None, **datos):
    """
    Anota la foto que se acaba de mostrar. `ancho` y `alto`: tamaño del fotograma
    completo en píxeles (ya con binning); `campo`: fracción visible en pantalla.
    `frame`: el crudo, si no está en static/captura.fits. `datos`: lo necesario para
    volver a revelarla (objetivo, balance, negro, exposicion, ganancia, prep...), el
    giro con que se enseña (encuadre, de giro_camara) y la imagen revelada sin girar
    (sin_girar), para volver a girarla si cambia el giro.
    """
    global _actual
    d = dict(datos, modo=modo, ancho=int(ancho), alto=int(alto), binning=int(binning or 1),
             campo=float(campo or 1.0), frame=frame, instante=time.time())
    with _lock:
        _actual = d


def actual():
    with _lock:
        return dict(_actual) if _actual else None


def actualizar(**datos):
    """Cambia datos de la foto anotada (p. ej. el encuadre, al cambiar el giro); nada si no hay ninguna."""
    with _lock:
        if _actual:
            _actual.update(datos)


def olvidar(modo):
    """
    Borra la foto anotada si es de ese modo. Al parar el modo en vivo la web vuelve a enseñar
    la última foto de verdad (static/captura.jpg): sin esto, "Apunta aquí", "Sincronizar con
    el Sol" y "Guardar en la galería" seguirían trabajando con el último fotograma del vivo.
    """
    global _actual
    with _lock:
        if _actual and _actual.get("modo") == modo:
            _actual = None


def desde_fits(ruta):
    """La foto de static/captura.fits descrita como registrar() (sin el crudo). None si no se puede leer."""
    try:
        from astropy.io import fits
        h = fits.getheader(ruta)
    except Exception:
        return None
    modo = "dia" if str(h.get("MODO", "")).strip().lower() == "dia" else "noche"
    d = {"modo": modo, "ancho": int(h.get("NAXIS1") or 0), "alto": int(h.get("NAXIS2") or 0),
         "binning": int(h.get("XBINNING") or 1), "campo": float(h.get("CAMPO") or 1.0), "frame": None,
         "objetivo": str(h.get("OBJETIVO") or "") or None, "exposicion": h.get("EXPTIME"),
         "fecha_obs": str(h.get("DATE-OBS") or ""), "desde_fits": True}
    if h.get("WB_R") and h.get("WB_B"):
        d["balance"] = {"r": float(h["WB_R"]), "b": float(h["WB_B"]), "origen": str(h.get("WB_ORIG") or "")}
    if h.get("NEGRO") is not None:
        d["negro"] = float(h["NEGRO"])
    if h.get("EV") is not None:
        d["ev"] = float(h["EV"])
    try:        # DATE-OBS de la app: hora local "AAAAMMDD_HHMMSS"
        from datetime import datetime
        d["instante"] = datetime.strptime(d["fecha_obs"], "%Y%m%d_%H%M%S").timestamp()
    except ValueError:
        pass
    return d
