# grabador_video.py
#
# Grabación de los fotogramas del seguimiento de objetos (seguimiento.py) en el
# disco del mini PC, en SER y/o MP4.
#
# - SER es el formato de vídeo astronómico de siempre (AutoStakkert, PIPP,
#   SharpCap, Siril lo leen): cabecera de 178 bytes, fotogramas crudos uno tras
#   otro y, al final, la marca de tiempo UTC de cada fotograma. Sin pérdida: lo
#   que sale de la cámara es lo que se guarda, y se puede apilar la ISS después.
# - MP4 (códec mp4v de OpenCV) es para verlo en cualquier sitio; con pérdida.
#
# La escritura va en un hilo propio con una cola: el bucle de seguimiento solo
# encola una copia del fotograma y sigue; si el disco no da abasto se descartan
# fotogramas (se cuentan) antes que frenar el control de la montura. Se vigila
# el espacio libre y se para la grabación por debajo de un mínimo.
#
# Con binning 2 en una cámara de color el patrón Bayer desaparece (cada píxel
# suma los cuatro colores), así que el SER se marca como MONO; con binning 1 se
# marca con el patrón de la cámara y el MP4 se debayeriza.

import os
import queue
import struct
import shutil
import logging
import threading
from datetime import datetime, timezone

import numpy as np

log = logging.getLogger("scopepower.grabador_video")

CARPETA_VIDEOS = "videos"
DISCO_MINIMO_BYTES = 1_000_000_000
COLA_MAXIMA = 90

# ColorID del formato SER
SER_COLOR = {"MONO": 0, "RGGB": 8, "GRBG": 9, "GBRG": 10, "BGGR": 11, "RGB": 100, "BGR": 101}
_TICKS_EPOCA = 621355968000000000      # ticks de 100 ns entre 0001-01-01 y 1970-01-01


def _ticks(dt):
    """datetime → ticks .NET (100 ns desde 0001-01-01), lo que usa la cabecera SER."""
    return _TICKS_EPOCA + int(dt.timestamp() * 10_000_000)


def espacio_libre(ruta):
    r = os.path.abspath(ruta)
    while not os.path.isdir(r):
        padre = os.path.dirname(r)
        if padre == r:
            return None
        r = padre
    try:
        return shutil.disk_usage(r).free
    except OSError:
        return None


class GrabadorSER:
    def __init__(self, ruta, ancho, alto, bayer="MONO", bits=8, observador="", instrumento="", telescopio=""):
        self.ruta = ruta
        self.ancho, self.alto, self.bits = int(ancho), int(alto), int(bits)
        self.color = SER_COLOR.get(bayer.upper(), 0)
        self.frames = 0
        self.tiempos = []
        self._f = open(ruta, "wb")
        self._inicio = datetime.now()
        self._escribir_cabecera(observador, instrumento, telescopio)

    def _escribir_cabecera(self, observador, instrumento, telescopio):
        def campo(texto, n):
            return texto.encode("latin-1", errors="replace")[:n].ljust(n, b"\0")
        self._f.seek(0)
        self._f.write(b"LUCAM-RECORDER")
        self._f.write(struct.pack("<iiiiiii", 0, self.color, 0, self.ancho, self.alto, self.bits, self.frames))
        self._f.write(campo(observador, 40) + campo(instrumento, 40) + campo(telescopio, 40))
        self._f.write(struct.pack("<qq", _ticks(self._inicio), _ticks(self._inicio.astimezone(timezone.utc).replace(tzinfo=None))))

    def escribir(self, frame, cuando=None):
        datos = np.ascontiguousarray(frame)
        if datos.dtype == np.uint16 and self.bits == 8:
            datos = (datos >> 8).astype(np.uint8)
        self._f.write(datos.tobytes())
        self.frames += 1
        self.tiempos.append(_ticks((cuando or datetime.now()).astimezone(timezone.utc).replace(tzinfo=None)))

    def cerrar(self):
        if self._f is None:
            return
        # Marcas de tiempo al final y número de fotogramas en la cabecera
        self._f.seek(0, os.SEEK_END)
        for t in self.tiempos:
            self._f.write(struct.pack("<q", t))
        self._f.seek(38)
        self._f.write(struct.pack("<i", self.frames))
        self._f.close()
        self._f = None


class GrabadorMP4:
    def __init__(self, ruta, ancho, alto, fps, bayer="MONO"):
        import cv2
        self.cv2 = cv2
        self.ruta = ruta
        self.bayer = bayer.upper()
        self.frames = 0
        self._w = cv2.VideoWriter(ruta, cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (int(ancho), int(alto)))
        if not self._w.isOpened():
            raise RuntimeError(f"No se pudo abrir {ruta} para escribir MP4")

    def escribir(self, frame, cuando=None):
        cv2 = self.cv2
        f = frame
        if f.dtype == np.uint16:
            f = (f >> 8).astype(np.uint8)
        if f.ndim == 2:
            codigo = {"RGGB": cv2.COLOR_BayerBG2BGR, "BGGR": cv2.COLOR_BayerRG2BGR,
                      "GRBG": cv2.COLOR_BayerGB2BGR, "GBRG": cv2.COLOR_BayerGR2BGR}.get(self.bayer)
            f = cv2.cvtColor(f, codigo) if codigo else cv2.cvtColor(f, cv2.COLOR_GRAY2BGR)
        self._w.write(f)
        self.frames += 1

    def cerrar(self):
        if self._w is not None:
            self._w.release()
            self._w = None


class Grabacion:
    """
    Una grabación con uno o varios formatos, escrita desde un hilo propio.
    encolar(frame) copia el fotograma y vuelve enseguida.
    """

    def __init__(self, carpeta=CARPETA_VIDEOS, formatos=("ser",), ancho=512, alto=512, fps=15.0, bayer="MONO",
                 bits=8, nombre=None, instrumento="", telescopio="", disco_minimo=DISCO_MINIMO_BYTES):
        os.makedirs(carpeta, exist_ok=True)
        self.nombre = nombre or f"seguimiento_{datetime.now():%Y%m%d_%H%M%S}"
        self.carpeta = carpeta
        self.rutas = {}
        self._grabadores = []
        if "ser" in formatos:
            r = os.path.join(carpeta, self.nombre + ".ser")
            self._grabadores.append(GrabadorSER(r, ancho, alto, bayer, bits, "ScopePower", instrumento, telescopio))
            self.rutas["ser"] = r
        if "mp4" in formatos:
            r = os.path.join(carpeta, self.nombre + ".mp4")
            self._grabadores.append(GrabadorMP4(r, ancho, alto, fps, bayer))
            self.rutas["mp4"] = r
        if not self._grabadores:
            raise ValueError("Ningún formato de vídeo válido (ser, mp4)")
        self.disco_minimo = disco_minimo
        self.frames = 0
        self.descartados = 0
        self.bytes_frame = int(ancho) * int(alto) * (2 if bits == 16 else 1)
        self.error = ""
        self.activa = True
        self._cola = queue.Queue(maxsize=COLA_MAXIMA)
        self._parar = threading.Event()
        self._lock = threading.Lock()
        self._hilo = threading.Thread(target=self._escribir, name="grabacion-video", daemon=True)
        self._hilo.start()
        log.info("Grabación de vídeo iniciada: %s (%s, %dx%d, %s)", self.nombre, ", ".join(self.rutas), ancho, alto, bayer)

    def encolar(self, frame):
        if not self.activa:
            return False
        try:
            self._cola.put_nowait((np.array(frame, copy=True), datetime.now()))
            return True
        except queue.Full:
            with self._lock:
                self.descartados += 1
            return False

    def _escribir(self):
        ultimo_disco = 0
        while not (self._parar.is_set() and self._cola.empty()):
            try:
                frame, cuando = self._cola.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                for g in self._grabadores:
                    g.escribir(frame, cuando)
                with self._lock:
                    self.frames += 1
            except Exception as e:
                self.error = str(e)
                log.error("Grabación de vídeo: %s", e)
                self.activa = False
                break
            if self.frames - ultimo_disco >= 150:
                ultimo_disco = self.frames
                libre = espacio_libre(self.carpeta)
                if libre is not None and libre < self.disco_minimo:
                    self.error = f"Disco casi lleno ({libre / 1e9:.1f} GB libres): grabación detenida"
                    log.warning("Grabación de vídeo: %s", self.error)
                    self.activa = False
                    break
        for g in self._grabadores:
            try:
                g.cerrar()
            except Exception as e:
                log.warning("Grabación de vídeo: al cerrar: %s", e)

    def cerrar(self, timeout=10):
        self.activa = False
        self._parar.set()
        self._hilo.join(timeout)
        log.info("Grabación de vídeo cerrada: %s, %d fotogramas (%d descartados)", self.nombre, self.frames, self.descartados)

    def estado(self):
        with self._lock:
            return {"nombre": self.nombre, "rutas": dict(self.rutas), "frames": self.frames, "descartados": self.descartados,
                    "mb": round(self.frames * self.bytes_frame / 1e6, 1), "activa": self.activa, "error": self.error,
                    "libre_gb": None if (l := espacio_libre(self.carpeta)) is None else round(l / 1e9, 1)}


def leer_cabecera_ser(ruta):
    """Cabecera de un SER como dict (para comprobar grabaciones)."""
    with open(ruta, "rb") as f:
        cab = f.read(178)
    if cab[:14] != b"LUCAM-RECORDER":
        raise ValueError("No es un fichero SER")
    _, color, _, ancho, alto, bits, frames = struct.unpack("<iiiiiii", cab[14:42])
    tam = os.path.getsize(ruta)
    return {"color": color, "ancho": ancho, "alto": alto, "bits": bits, "frames": frames,
            "observador": cab[42:82].rstrip(b"\0").decode("latin-1"), "instrumento": cab[82:122].rstrip(b"\0").decode("latin-1"),
            "telescopio": cab[122:162].rstrip(b"\0").decode("latin-1"),
            "con_tiempos": tam == 178 + frames * ancho * alto * (2 if bits > 8 else 1) + 8 * frames}


if __name__ == "__main__":
    # Prueba: 30 fotogramas sintéticos en SER y MP4, y comprobación de la cabecera
    import tempfile, time
    carpeta = tempfile.mkdtemp()
    g = Grabacion(carpeta, ("ser", "mp4"), 128, 96, fps=15, bayer="MONO", nombre="prueba")
    for i in range(30):
        f = np.zeros((96, 128), dtype=np.uint8)
        f[40:56, 30 + i * 2:46 + i * 2] = 200
        g.encolar(f)
    time.sleep(0.5)
    g.cerrar()
    cab = leer_cabecera_ser(g.rutas["ser"])
    assert cab["frames"] == 30 and cab["ancho"] == 128 and cab["alto"] == 96 and cab["con_tiempos"], cab
    assert os.path.getsize(g.rutas["mp4"]) > 1000
    print("✅ grabador_video:", cab, "| mp4", os.path.getsize(g.rutas["mp4"]), "bytes")
    shutil.rmtree(carpeta, ignore_errors=True)
