# cola_estrellas.py — separar las estrellas de las fotos recién guardadas, sin estorbar
#
# Con [Procesado] estrellas_aparte = true, al guardar un apilado en la galería hay
# que pasarle StarNet2 (unos 2,5 minutos en el mini PC) y volver a procesar el
# objeto y las estrellas por separado. Si eso iba dentro del guardado, el guardado
# no terminaba hasta el final: el plan de la noche y el mosaico se quedaban
# esperando (hasta 3 minutos por objeto, con el telescopio parado) y no se podía
# guardar otra foto mientras tanto.
#
# Ahora el guardado termina con el JPG de siempre y deja aquí el trabajo de
# StarNet2: un solo hilo que hace las fotos de una en una, por orden, con la
# prioridad del hilo por debajo de lo normal (y StarNet2 también, ver
# estrellas_aparte.separar) para que la captura y el apilado vayan primero. Si se
# cierra ScopePower con fotos en la cola, esas fotos se quedan con el JPG normal
# y se pueden separar después desde el editor de la galería.

import logging
import os
import queue
import threading
import time

from idioma import _

log = logging.getLogger("scopepower.estrellas")

_cola = queue.Queue()
_lock = threading.Lock()
_hilo = None
# Para la web: la foto que se está separando, las que esperan y cómo fue la última
ESTADO = {"archivo": "", "mensaje": "", "pendientes": [], "hechas": 0, "ultima": "", "ultimo_ok": None}
THREAD_PRIORITY_BELOW_NORMAL = -1


def _prioridad_baja():
    """El hilo que llama, con prioridad por debajo de lo normal (solo Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
    except Exception as e:
        log.debug("No se pudo bajar la prioridad del hilo de la cola de estrellas: %s", e)


def encolar(archivo, trabajo):
    """
    Pone en la cola trabajo(progreso) para la foto `archivo` (nombre del JPG, para
    la web); progreso(texto) lo llama el trabajo para decir por dónde va. Vuelve al
    momento. Devuelve cuántas fotos hay por delante.
    """
    global _hilo
    with _lock:
        delante = len(ESTADO["pendientes"]) + (1 if ESTADO["archivo"] else 0)
        ESTADO["pendientes"].append(archivo)
        _cola.put((archivo, trabajo))
        if _hilo is None or not _hilo.is_alive():
            _hilo = threading.Thread(target=_bucle, name="cola-estrellas", daemon=True)
            _hilo.start()
    log.info("Estrellas aparte de %s en la cola (%d por delante)", archivo, delante)
    return delante


def _bucle():
    _prioridad_baja()
    while True:
        archivo, trabajo = _cola.get()
        with _lock:
            if archivo in ESTADO["pendientes"]:
                ESTADO["pendientes"].remove(archivo)
            ESTADO.update({"archivo": archivo, "mensaje": _("Separando las estrellas...")})
        inicio = time.time()

        def progreso(texto):
            ESTADO["mensaje"] = texto
        try:
            resultado = trabajo(progreso)
            ok, mensaje = True, resultado or _("Estrellas aparte de {archivo} en {s} s", archivo=archivo, s=int(time.time() - inicio))
        except Exception as e:
            log.exception("Error con las estrellas aparte de %s", archivo)
            ok, mensaje = False, _("No se pudieron separar las estrellas de {archivo}: {e}", archivo=archivo, e=e)
        with _lock:
            ESTADO.update({"archivo": "", "mensaje": "", "ultima": mensaje, "ultimo_ok": ok})
            if ok:
                ESTADO["hechas"] += 1
        _cola.task_done()


def estado():
    """Copia de ESTADO (con la lista de pendientes copiada) para la web."""
    with _lock:
        d = dict(ESTADO)
        d["pendientes"] = list(ESTADO["pendientes"])
    return d


def esperar(timeout=None):
    """Espera a que la cola se vacíe (para las pruebas). True si se vació."""
    fin = None if timeout is None else time.time() + timeout
    while True:
        with _lock:
            vacia = not ESTADO["pendientes"] and not ESTADO["archivo"]
        if vacia:
            return True
        if fin is not None and time.time() > fin:
            return False
        time.sleep(0.05)
