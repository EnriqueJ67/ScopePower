# hardware.py
#
# Acceso a la montura y a la cámara ASCOM desde una app Flask multihilo.
#
# Los objetos COM de ASCOM viven en un "apartamento" (STA) ligado al hilo que
# los creó. Si Flask atiende cada petición en un hilo distinto y todos usan el
# mismo objeto global, aparecen errores del tipo "la aplicación llamó a una
# interfaz serializada para otro hilo" o simplemente se cuelga. Ese fue el
# motivo de tener que usar threaded=False.
#
# Solución: cada dispositivo tiene un único hilo trabajador que es el ÚNICO que
# toca el objeto COM. Las peticiones web envían funciones a ese hilo y esperan
# el resultado. La montura y la cámara tienen trabajadores independientes, así
# que se puede parar la montura mientras la cámara está exponiendo.

import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import numpy as np
import pythoncom
import win32com.client

log = logging.getLogger("scopepower.hardware")


class ErrorHardware(Exception):
    """Fallo de comunicación con un dispositivo ASCOM (o dispositivo no conectado)."""


class CapturaCancelada(Exception):
    """El usuario canceló la exposición en curso."""


class TrabajadorCOM:
    """
    Hilo único propietario de un objeto COM ASCOM.

    Uso:
        montura = TrabajadorCOM("montura", lambda: DRIVER_TELESCOPIO)
        ra = montura.ejecutar(lambda tel: tel.RightAscension)
    """

    def __init__(self, nombre, obtener_driver, al_conectar=None):
        self.nombre = nombre
        self._obtener_driver = obtener_driver
        self._al_conectar = al_conectar
        self._dispositivo = None
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"com-{nombre}",
            initializer=pythoncom.CoInitialize,
        )
        self.ocupado = False
        self.ultimo_error = None
        self._lock_estado = threading.Lock()

    # ── Conexión ──────────────────────────────────────────────────────────
    def _conectar_en_hilo(self):
        if self._dispositivo is None:
            driver = self._obtener_driver()
            log.info("Conectando %s con driver %s", self.nombre, driver)
            dispositivo = win32com.client.Dispatch(driver)
            dispositivo.Connected = True
            if self._al_conectar:
                self._al_conectar(dispositivo)
            self._dispositivo = dispositivo
        return self._dispositivo

    def _desconectar_en_hilo(self):
        if self._dispositivo is not None:
            try:
                self._dispositivo.Connected = False
            except Exception as e:
                log.warning("Error al desconectar %s: %s", self.nombre, e)
            self._dispositivo = None

    def esta_conectado(self):
        return self._dispositivo is not None

    # ── Ejecución ─────────────────────────────────────────────────────────
    def ejecutar(self, fn, timeout=30, conectar=True):
        """
        Ejecuta fn(dispositivo) en el hilo propietario y devuelve su resultado.
        Lanza ErrorHardware si el dispositivo no se puede conectar, si la
        operación falla o si no responde en `timeout` segundos.
        """
        futuro = self.ejecutar_async(fn, conectar=conectar)
        try:
            return futuro.result(timeout=timeout)
        except FuturesTimeout:
            msg = f"{self.nombre}: la operación no respondió en {timeout} s"
            self.ultimo_error = msg
            log.error(msg)
            raise ErrorHardware(msg)

    def ejecutar_async(self, fn, conectar=True):
        """Como ejecutar() pero sin esperar: devuelve un Future."""
        def tarea():
            with self._lock_estado:
                self.ocupado = True
            try:
                if conectar:
                    dispositivo = self._conectar_en_hilo()
                else:
                    dispositivo = self._dispositivo
                resultado = fn(dispositivo)
                self.ultimo_error = None
                return resultado
            except CapturaCancelada:
                raise
            except ErrorHardware:
                raise
            except Exception as e:
                msg = f"{self.nombre}: {_mensaje_com(e)}"
                self.ultimo_error = msg
                log.error(msg)
                if self._dispositivo is None:
                    # Falló la propia conexión: no dejar un objeto a medias
                    self._dispositivo = None
                raise ErrorHardware(msg) from e
            finally:
                with self._lock_estado:
                    self.ocupado = False

        return self._executor.submit(tarea)

    def reconectar(self, timeout=30):
        """Desconecta y vuelve a conectar el dispositivo."""
        def tarea(_):
            self._desconectar_en_hilo()
            return self._conectar_en_hilo()
        return self.ejecutar(tarea, timeout=timeout, conectar=False)

    def desconectar(self, timeout=15):
        try:
            self.ejecutar(lambda _: self._desconectar_en_hilo(), timeout=timeout, conectar=False)
        except ErrorHardware:
            pass


def _mensaje_com(e):
    """Extrae un mensaje legible de una excepción COM de pywin32."""
    try:
        # pythoncom.com_error: (hresult, texto, (wcode, source, text, ...), argerror)
        if hasattr(e, "excepinfo") and e.excepinfo and len(e.excepinfo) > 2 and e.excepinfo[2]:
            return str(e.excepinfo[2])
        if hasattr(e, "strerror") and e.strerror:
            return str(e.strerror)
    except Exception:
        pass
    return str(e)


# ─────────────────────────────────────────────────────────────────────────────
#  Captura con la cámara
# ─────────────────────────────────────────────────────────────────────────────

class EstadoCaptura:
    """Estado observable de la exposición en curso, para mostrarlo en la web."""

    def __init__(self):
        self._lock = threading.Lock()
        self.en_curso = False
        self.inicio = None
        self.exposicion = 0.0
        self.descripcion = ""
        self.cancelar = threading.Event()

    def empezar(self, exposicion, descripcion=""):
        with self._lock:
            self.en_curso = True
            self.inicio = time.time()
            self.exposicion = float(exposicion)
            self.descripcion = descripcion
            self.cancelar.clear()

    def terminar(self):
        with self._lock:
            self.en_curso = False
            self.inicio = None
            self.descripcion = ""

    def como_dict(self):
        with self._lock:
            restante = 0.0
            if self.en_curso and self.inicio is not None:
                restante = max(0.0, self.exposicion - (time.time() - self.inicio))
            return {
                "en_curso": self.en_curso,
                "exposicion": self.exposicion,
                "restante": round(restante, 1),
                "descripcion": self.descripcion,
            }


estado_captura = EstadoCaptura()


def capturar_frame(camara, exposicion, binning=1, estado=None, descripcion="", light=True):
    """
    Expone y devuelve el fotograma crudo (CFA) como np.uint16 2D.
    Debe ejecutarse DENTRO del hilo de la cámara (vía TrabajadorCOM.ejecutar).

    - Configura el binning y el área completa del sensor (sin acumular divisiones).
    - Sondea ImageReady cada 0,2 s; si `estado.cancelar` se activa, aborta la
      exposición y lanza CapturaCancelada.
    - Si la cámara no devuelve la imagen en exposicion + 60 s, lanza ErrorHardware.
    """
    estado = estado or estado_captura
    exposicion = float(exposicion)

    camara.BinX = binning
    camara.BinY = binning
    camara.StartX = 0
    camara.StartY = 0
    camara.NumX = int(camara.CameraXSize // binning)
    camara.NumY = int(camara.CameraYSize // binning)

    estado.empezar(exposicion, descripcion)
    try:
        camara.StartExposure(exposicion, bool(light))
        inicio = time.time()
        limite = exposicion + 60.0
        while not camara.ImageReady:
            if estado.cancelar.is_set():
                _abortar(camara)
                raise CapturaCancelada("Exposición cancelada por el usuario")
            if time.time() - inicio > limite:
                _abortar(camara)
                raise ErrorHardware(f"La cámara no devolvió la imagen en {limite:.0f} s")
            time.sleep(0.2)
        return np.array(camara.ImageArray, dtype=np.uint16)
    finally:
        estado.terminar()


def _abortar(camara):
    try:
        if getattr(camara, "CanAbortExposure", False):
            camara.AbortExposure()
        elif getattr(camara, "CanStopExposure", False):
            camara.StopExposure()
    except Exception as e:
        log.warning("No se pudo abortar la exposición: %s", e)


def pistas_platesolve(header, focal_mm, pixel_x_um, pixel_y_um, binning=1, ra_h=None, dec_deg=None):
    """
    Añade a una cabecera FITS lo que ASTAP necesita para resolver rápido y sin
    adivinar: escala de placa (FOCALLEN + XPIXSZ/YPIXSZ, ya multiplicados por el
    binning) y posición aproximada (RA/DEC en grados). Sin FOCALLEN/XPIXSZ ASTAP
    usa el campo de sus ajustes, y si no coincide con el real no resuelve nunca.
    """
    header["FOCALLEN"] = (float(focal_mm), "Longitud focal (mm)")
    header["XPIXSZ"] = (float(pixel_x_um) * binning, "Tamano de pixel con binning (um)")
    header["YPIXSZ"] = (float(pixel_y_um) * binning, "Tamano de pixel con binning (um)")
    header["XBINNING"] = binning
    header["YBINNING"] = binning
    if ra_h is not None and dec_deg is not None:
        header["RA"] = (float(ra_h) * 15.0, "Posicion aproximada de la montura (grados)")
        header["DEC"] = (float(dec_deg), "Posicion aproximada de la montura (grados)")
    return header


def capturar_frame_con_pixel(camara, exposicion, binning=1, **kw):
    """capturar_frame() + tamaño de píxel de la cámara: (frame, pixel_x_um, pixel_y_um)."""
    frame = capturar_frame(camara, exposicion, binning, **kw)
    try:
        px, py = float(camara.PixelSizeX), float(camara.PixelSizeY)
    except Exception:
        px = py = 3.76   # valor típico si el driver no lo da
    return frame, px, py
