# visor_video.py
#
# Visor de los vídeos SER en la galería: ver un vídeo fotograma a fotograma desde la
# tablet y encontrar lo que ha cruzado por delante (un pájaro, un avión, una nube) sin
# tener que mirarlo entero.
#
#   - Fotogramas sueltos en JPG pequeño (superpíxel, con el negro y el balance de la
#     grabación). En campo la web va por el wifi del SynScan (0,5–2 Mbit/s): un MP4 no
#     llegaría, un JPG de ~50 KB sí. El blanco es el mismo para todo el vídeo (se mide una
#     vez en el fotograma del medio) para que al reproducir no parpadee.
#   - Miniatura para la tarjeta de la galería, guardada junto al SER (<nombre>.mini.jpg).
#   - Análisis en segundo plano, una vez por vídeo (<nombre>.visor.json):
#       · nitidez de cada fotograma (la misma medida que usa el apilado), para ver los
#         ratos de buen y mal seeing;
#       · sucesos: cada fotograma se reduce a 96 px y se compara con la mediana de los
#         ±15 de alrededor. Lo que no estaba antes ni está después (un pájaro tarda 0,2 s
#         en cruzar el Sol) sale como una zona más oscura; las manchas, el limbo y la
#         deriva lenta del disco están también en la mediana y se anulan. El borde del
#         disco se deja fuera (el seeing lo mueve).
#
# Probado con el vídeo del Sol del 25-oct-2026 (2398 fotogramas): el pájaro que cruza en
# 4 fotogramas sale con ~750 veces el ruido y otro lejano, un puntito, con ~60; los
# fotogramas sin nada no pasan de ~12 (el seeing).

import os
import json
import time
import logging
import threading

import numpy as np
from idioma import _

import apilado_video

log = logging.getLogger("scopepower.visor_video")

VERSION_ANALISIS = 2
LADO_MINIATURA = 360
LADO_FOTOGRAMA = 720            # lado mayor del JPG que se pide de serie
LADO_FOTOGRAMA_MAX = 1600
CALIDAD_JPG = 78
LADO_ANALISIS = 96              # el fotograma reducido con que se buscan los sucesos
VENTANA_MEDIANA = 31            # fotogramas (±15: 1,5 s a 20 fotos/s)
BLOQUE = 400                    # fotogramas por bloque de la mediana (la memoria no crece con la duración)
PICOS = 4                       # píxeles más oscurecidos que se promedian en cada fotograma
UMBRAL_SUCESO = 20.0            # veces el ruido (MAD) de la puntuación (25-oct: el seeing llega a ~12, un pájaro lejano 62)
HUECO_SUCESO = 3                # fotogramas sin nada que aún cuentan como el mismo suceso
MAX_SUCESOS = 60


class ErrorVisor(Exception):
    """El vídeo no se puede ver (el texto dice por qué)."""


def ruta_miniatura(ruta_ser):
    return os.path.splitext(ruta_ser)[0] + ".mini.jpg"


def ruta_analisis(ruta_ser):
    return os.path.splitext(ruta_ser)[0] + ".visor.json"


def archivos_auxiliares(ruta_ser):
    """Lo que el visor deja junto al SER (se borra con el vídeo)."""
    return [ruta_miniatura(ruta_ser), ruta_analisis(ruta_ser)]


# ─────────────────────────────────────────────────────────────────────────────
#  Fotogramas
# ─────────────────────────────────────────────────────────────────────────────
_revelados = {}
_lock_revelados = threading.Lock()


def _revelado(lector, meta):
    """{"negro", "balance", "blanco", "orientacion"} fijos para todo el vídeo (en memoria por ruta y fecha)."""
    clave = (lector.ruta, os.path.getmtime(lector.ruta))
    with _lock_revelados:
        if clave in _revelados:
            return _revelados[clave]
    import vivo_dia
    import foto_dia
    import orientacion_camara
    medio = lector.fotograma(lector.frames // 2)
    negro = meta.get("negro")
    if negro is None:
        negro = foto_dia.nivel_negro(medio, lector.blanco)
    balance = meta.get("balance") if lector.patron in vivo_dia.PATRONES else None
    img = _superpixel(medio, lector.patron, 1, negro, balance)
    blanco = max(float(np.percentile(img[::2, ::2], 99.9)), 1.0)
    r = {"negro": float(negro), "balance": balance, "blanco": blanco,
         "orientacion": meta.get("orientacion") or orientacion_camara.leer_sdk()["transformacion"]}
    with _lock_revelados:
        if len(_revelados) > 8:
            _revelados.clear()
        _revelados[clave] = r
    return r


def _superpixel(cfa, patron, paso, negro, balance):
    import vivo_dia
    if patron in vivo_dia.PATRONES:
        img = vivo_dia.superpixel_rgb(cfa, patron, paso) - float(negro)
        if balance:
            img[..., 0] *= float(balance["r"])
            img[..., 2] *= float(balance["b"])
        return img
    g = np.asarray(cfa, dtype=np.float32)[::2 * paso, ::2 * paso] - float(negro)
    return np.dstack([g, g, g])


def _abrir(ruta_ser):
    lector = apilado_video.LectorSER(ruta_ser)
    return lector, apilado_video.leer_meta(ruta_ser)


def fotograma_bgr(ruta_ser, i, lado=LADO_FOTOGRAMA, encuadre=None):
    """El fotograma `i` revelado (BGR uint8, orientación de ASCOM y giro de Cámara), con el lado mayor ≤ `lado`."""
    import cv2
    import vivo_dia
    import giro_camara
    lector, meta = _abrir(ruta_ser)
    try:
        if not 0 <= i < lector.frames:
            raise ErrorVisor(_("El vídeo no tiene el fotograma {i}", i=i))
        rev = _revelado(lector, meta)
        lado = max(64, min(LADO_FOTOGRAMA_MAX, int(lado)))
        paso = max(1, int((max(lector.ancho, lector.alto) / 2.0) // (lado * 1.15)) or 1)
        img = _superpixel(lector.fotograma(i), lector.patron, paso, rev["negro"], rev["balance"])
    finally:
        lector.cerrar()
    img = np.clip(img / rev["blanco"], 0.0, 1.0)
    img = (np.power(img, 1.0 / 1.6) * 255.0).astype(np.uint8)
    img = cv2.cvtColor(vivo_dia.a_orientacion_ascom(img, rev["orientacion"]), cv2.COLOR_RGB2BGR)
    img = giro_camara.aplicar(img, encuadre)
    h, w = img.shape[:2]
    if max(h, w) > lado:
        k = lado / float(max(h, w))
        img = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    return img


def a_jpg(img, calidad=CALIDAD_JPG):
    import cv2
    ok, datos = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(calidad)])
    if not ok:
        raise ErrorVisor(_("No se pudo codificar el JPG"))
    return datos.tobytes()


def miniatura(ruta_ser, encuadre=None):
    """Ruta de la miniatura de la galería (el fotograma del medio); se hace la primera vez."""
    ruta = ruta_miniatura(ruta_ser)
    if os.path.isfile(ruta) and os.path.getmtime(ruta) >= os.path.getmtime(ruta_ser):
        return ruta
    lector = apilado_video.LectorSER(ruta_ser)
    medio = lector.frames // 2
    lector.cerrar()
    with open(ruta, "wb") as f:
        f.write(a_jpg(fotograma_bgr(ruta_ser, medio, LADO_MINIATURA, encuadre), 75))
    return ruta


def fotograma_lineal(ruta_ser, i):
    """
    (RGB float32 a resolución completa con el negro restado y el balance aplicado, en la orientación
    del SDK; meta del vídeo; segundo UTC del fotograma o None) para guardar un fotograma en la galería.
    """
    lector, meta = _abrir(ruta_ser)
    try:
        if not 0 <= i < lector.frames:
            raise ErrorVisor(_("El vídeo no tiene el fotograma {i}", i=i))
        rev = _revelado(lector, meta)
        cfa = np.array(lector.fotograma(i))
        tiempos = lector.tiempos()
    finally:
        lector.cerrar()
    rgb = apilado_video.desmosaicar(cfa, lector.patron)
    instante = None
    if tiempos is not None and len(tiempos) > i:
        instante = float(tiempos[i])           # UTC; los vídeos con el desfase antiguo ya vienen corregidos
    elif meta.get("instante"):
        instante = float(meta["instante"])
    return rgb, dict(meta, negro=rev["negro"], balance=rev["balance"], orientacion=rev["orientacion"]), instante


# ─────────────────────────────────────────────────────────────────────────────
#  Análisis: nitidez y sucesos
# ─────────────────────────────────────────────────────────────────────────────
def analizar(ruta_ser, progreso=None, cancelar=None):
    """
    Recorre el vídeo una vez. Devuelve {"version", "frames", "fps", "nitidez": [0..1 por fotograma, de la peor a la mejor],
    "sucesos": [{"inicio", "fin", "pico", "z", "x", "y"}], "segundos"}; x e y son fracciones del
    fotograma en la orientación del SDK (se pasan a la pantalla al pedirlas).
    """
    import cv2
    inicio = time.time()
    avisar = progreso or (lambda f: None)
    parar = cancelar or (lambda: False)
    lector = apilado_video.LectorSER(ruta_ser)
    try:
        n = lector.frames
        nit = np.zeros(n, np.float32)
        minis = np.zeros((n, LADO_ANALISIS, LADO_ANALISIS), np.float16)
        for i in range(n):
            if parar():
                raise ErrorVisor(_("Análisis cancelado"))
            g = apilado_video.verde(lector.fotograma(i), lector.patron)
            nit[i], brillo = apilado_video.nitidez(g)
            m = cv2.resize(g, (LADO_ANALISIS, LADO_ANALISIS), interpolation=cv2.INTER_AREA)
            minis[i] = m / max(float(m.mean()), 1e-6)       # sin los cambios de exposición o de una nube fina
            if i % 20 == 0:
                avisar(0.9 * i / n)
        duracion = lector.duracion_s()
    finally:
        lector.cerrar()
    sucesos = buscar_sucesos(minis, progreso=lambda f: avisar(0.9 + 0.1 * f), cancelar=parar)

    # De lo peor (1 %) a lo mejor (99 %) del propio vídeo: en un mismo vídeo la nitidez cambia poco
    # (un 10-20 % con el seeing) y sobre cero la curva saldría plana
    bajo, alto = (float(np.percentile(nit, 1)), float(np.percentile(nit, 99))) if n else (0.0, 1.0)
    nitidez_rel = np.clip((nit - bajo) / max(alto - bajo, 1e-12), 0.0, 1.0)
    resultado = {"version": VERSION_ANALISIS, "frames": int(n),
                 "fps": round(n / duracion, 2) if duracion else None,
                 "nitidez": [round(float(v), 3) for v in nitidez_rel], "sucesos": sucesos,
                 "segundos": round(time.time() - inicio, 1)}
    log.info("Visor: %s analizado en %.0f s: %d fotogramas, %d sucesos", os.path.basename(ruta_ser),
             resultado["segundos"], n, len(sucesos))
    return resultado


def buscar_sucesos(minis, progreso=None, cancelar=None):
    """
    Los momentos en que algo oscurece una zona del disco solo un instante. `minis`: n × L × L
    con cada fotograma reducido y dividido por su brillo medio. Devuelve [{"inicio", "fin", "pico",
    "z", "x", "y"}] por orden de tiempo, con x e y en fracciones del fotograma reducido.
    """
    import cv2
    from scipy.ndimage import median_filter
    avisar = progreso or (lambda f: None)
    parar = cancelar or (lambda: False)
    n, lado = len(minis), minis.shape[1]

    # Zona útil: lo brillante de la mediana del vídeo, sin el borde (el seeing lo mueve)
    muestra = minis[::max(1, n // 200)].astype(np.float32)
    fondo = np.median(muestra, axis=0)
    zona = (fondo > 0.5 * float(np.percentile(fondo, 99.5))).astype(np.uint8)
    zona = cv2.erode(zona, np.ones((7, 7), np.uint8)) > 0
    if zona.sum() < 20:
        zona = np.ones_like(zona, bool)

    puntos = np.zeros(n, np.float32)
    donde = np.zeros((n, 2), np.float32)
    medio = VENTANA_MEDIANA // 2
    idx_zona = np.flatnonzero(zona.ravel())
    for a in range(0, n, BLOQUE):
        if parar():
            raise ErrorVisor(_("Análisis cancelado"))
        lo, hi = max(0, a - medio), min(n, a + BLOQUE + medio)
        trozo = minis[lo:hi].astype(np.float32)
        ref = median_filter(trozo, size=(min(VENTANA_MEDIANA, len(trozo)), 1, 1), mode="nearest")
        dif = (ref - trozo) / np.maximum(ref, 0.05)            # oscurecimiento relativo
        for k in range(a - lo, min(hi, a + BLOQUE) - lo):
            d = cv2.GaussianBlur(dif[k], (3, 3), 0).ravel()[idx_zona]
            orden = np.argpartition(d, -PICOS)[-PICOS:]
            puntos[lo + k] = float(d[orden].mean())
            p = idx_zona[orden[np.argmax(d[orden])]]
            donde[lo + k] = ((p % lado + 0.5) / lado, (p // lado + 0.5) / lado)
        avisar(min(1.0, (a + BLOQUE) / max(n, 1)))

    sucesos = []
    if n >= VENTANA_MEDIANA:
        centro = float(np.median(puntos))
        ruido = max(1.4826 * float(np.median(np.abs(puntos - centro))), 1e-4)
        z = (puntos - centro) / ruido
        marcados = np.flatnonzero(z > UMBRAL_SUCESO)
        grupo = []
        for i in list(marcados) + [None]:
            if grupo and (i is None or i - grupo[-1] > HUECO_SUCESO):
                pico = max(grupo, key=lambda j: z[j])
                sucesos.append({"inicio": int(grupo[0]), "fin": int(grupo[-1]), "pico": int(pico), "z": round(float(z[pico]), 1),
                                "x": round(float(donde[pico, 0]), 4), "y": round(float(donde[pico, 1]), 4)})
                grupo = []
            if i is not None:
                grupo.append(int(i))
        if len(sucesos) > MAX_SUCESOS:
            sucesos = sorted(sorted(sucesos, key=lambda s: -s["z"])[:MAX_SUCESOS], key=lambda s: s["inicio"])
    return sucesos


def leer_analisis(ruta_ser):
    """El análisis guardado, o None si no hay o es de otra versión o de otro SER."""
    try:
        with open(ruta_analisis(ruta_ser), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if d.get("version") != VERSION_ANALISIS or os.path.getmtime(ruta_analisis(ruta_ser)) < os.path.getmtime(ruta_ser):
        return None
    return d


def a_pantalla(fx, fy, orientacion, ancho, alto, encuadre=None):
    """Un punto (fracciones del fotograma del SDK, ancho × alto) en fracciones de la imagen que se ve."""
    import orientacion_camara
    import procesado
    op = orientacion_camara.TRANSFORMACIONES[orientacion]
    n = 64
    filas, cols = np.mgrid[0:n, 0:n]
    oc, of = op(cols), op(filas)
    objetivo = (fx * n - 0.5, fy * n - 0.5)
    d = (oc - objetivo[0]) ** 2 + (of - objetivo[1]) ** 2
    r, c = np.unravel_index(int(np.argmin(d)), d.shape)
    gx, gy = (c + 0.5) / d.shape[1], (r + 0.5) / d.shape[0]
    # Ancho y alto tras la orientación (las traspuestas los cambian)
    w, h = (alto, ancho) if op(np.zeros((2, 3))).shape == (3, 2) else (ancho, alto)
    e = encuadre or {}
    if not e.get("giro") and not e.get("espejo"):
        return gx, gy
    g = procesado.geometria_giro(w, h, 1.0, e.get("giro", 0.0), bool(e.get("espejo")))
    x, y, __ = g["T"] @ [gx * w - 0.5, gy * h - 0.5, 1.0]
    return float((x + 0.5) / g["ancho"]), float((y + 0.5) / g["alto"])


class TrabajoAnalisis:
    """Los vídeos se analizan de uno en uno, en el orden en que se piden."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cola = []
        self._actual = None
        self._progreso = 0.0
        self._errores = {}
        self._parar = threading.Event()
        self._hilo = None

    def estado(self, ruta_ser):
        if leer_analisis(ruta_ser) is not None:
            return {"estado": "hecho", "progreso": 1.0}
        with self._lock:
            if self._actual == ruta_ser:
                return {"estado": "en_curso", "progreso": round(self._progreso, 3)}
            if ruta_ser in self._cola:
                return {"estado": "en_cola", "progreso": 0.0}
            if ruta_ser in self._errores:
                return {"estado": "error", "progreso": 0.0, "error": self._errores[ruta_ser]}
        return {"estado": "sin_hacer", "progreso": 0.0}

    def ocupado_con(self, ruta_ser):
        with self._lock:
            return self._actual == ruta_ser

    def pedir(self, ruta_ser):
        with self._lock:
            self._errores.pop(ruta_ser, None)
            if self._actual != ruta_ser and ruta_ser not in self._cola:
                self._cola.append(ruta_ser)
            if self._hilo is None or not self._hilo.is_alive():
                self._parar.clear()
                self._hilo = threading.Thread(target=self._bucle, name="visor-video", daemon=True)
                self._hilo.start()

    def quitar(self, ruta_ser):
        """Saca el vídeo de la cola; si es el que se analiza, lo cancela y espera a que suelte el SER."""
        with self._lock:
            if ruta_ser in self._cola:
                self._cola.remove(ruta_ser)
            actual = self._actual == ruta_ser
            if actual:
                self._parar.set()
        if actual:
            for __ in range(100):
                if not self.ocupado_con(ruta_ser):
                    break
                time.sleep(0.05)

    def _avanzar(self, f):
        with self._lock:
            self._progreso = float(f)

    def _bucle(self):
        while True:
            with self._lock:
                if not self._cola:
                    self._actual = None
                    return
                self._actual = self._cola.pop(0)
                self._progreso = 0.0
                self._parar.clear()
                ruta = self._actual
            try:
                r = analizar(ruta, progreso=self._avanzar, cancelar=self._parar.is_set)
                with open(ruta_analisis(ruta), "w", encoding="utf-8") as f:
                    json.dump(r, f, ensure_ascii=False)
            except ErrorVisor as e:
                log.info("Visor: %s: %s", os.path.basename(ruta), e)
            except Exception as e:
                log.exception("Visor: error analizando %s", ruta)
                with self._lock:
                    self._errores[ruta] = str(e)
            finally:
                with self._lock:
                    self._actual = None


trabajo_analisis = TrabajoAnalisis()
