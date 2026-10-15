# camara_webcam.py
#
# Una webcam (o una cámara planetaria UVC sin driver propio) por OpenCV y
# DirectShow, con la misma interfaz que CamaraNativa (Player One): camara_sdk.py
# la envuelve como si fuera un driver ASCOM, así que sirve para la foto de
# día, el modo en vivo, el enfoque, la Luna y los planetas brillantes. Para
# cielo profundo se queda corta: 8 bits, sin control fino de la exposición.
#
# Cómo se adapta a lo que espera la app (fotos RAW con patrón Bayer):
#   - Lista: las webcams son los dispositivos con el driver UVC de Windows
#     (Win32_PnPEntity con Service = usbvideo), en el orden en que suelen
#     aparecer en DirectShow; no hace falta abrirlas para listarlas.
#   - Color: la webcam da BGR ya revelado; se vuelve a montar un mosaico RGGB
#     (R, G, G, B de cada celda 2×2) para que el revelado, el balance de
#     blancos y todo lo demás funcionen igual que con una cámara astronómica.
#   - Exposición: DirectShow la pide en potencias de 2 (CAP_PROP_EXPOSURE =
#     log2 de los segundos; medido el 15-oct-2026 con una Trust USB Camera:
#     -8 → media 25, -2 → 143). Se usa la potencia de 2 que no pasa de la
#     pedida y el brillo se escala por lo que falta, y en modo foto se suman
#     los fotogramas que caben en la exposición: así el brillo crece con el
#     tiempo pedido como en una cámara de verdad y el ruido baja al promediar.
#   - Niveles: 8 bits × 256 → 16 bits (MaxADU 65535, como el RAW16 de las otras).
#   - Tamaño de píxel: no lo da la webcam; [CamaraSDK] pixel_webcam_um (3,0 µm
#     por defecto) para el plate solving.

import time
import logging
import threading
import configparser

import numpy as np

from idioma import _

log = logging.getLogger("scopepower.camara_webcam")

NOMBRE_MARCA = "Webcam"

EXP_LOG2_MIN, EXP_LOG2_MAX = -13, -1     # rango habitual de DirectShow: 1/8192 s .. 0,5 s
GANANCIA_MAX = 255                       # DirectShow no dice el rango: el habitual de las UVC
FORMATOS = ("RAW8", "RAW16", "RGB24", "MONO8")


class ErrorWebcam(Exception):
    """Fallo al usar la webcam."""


def _config(clave, defecto=""):
    cfg = configparser.ConfigParser()
    try:
        cfg.read("config.ini", encoding="utf-8")
    except configparser.Error:
        pass
    return cfg.get("CamaraSDK", clave, fallback=defecto).strip()


def listar_camaras():
    """Nombre de cada webcam conectada (dispositivos UVC de Windows), sin abrirlas."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    wmi = win32com.client.GetObject("winmgmts:")
    filas = wmi.ExecQuery("SELECT Name, DeviceID FROM Win32_PnPEntity WHERE Service = 'usbvideo'")
    return [{"modelo": str(f.Name), "id": str(f.DeviceID)} for f in filas]


def mosaico_rggb(bgr):
    """Imagen BGR (h×w×3) → mosaico RGGB (h×w) del mismo tipo, con h y w pares."""
    h, w = bgr.shape[0] & ~1, bgr.shape[1] & ~1
    bgr = bgr[:h, :w]
    m = np.empty((h, w), dtype=bgr.dtype)
    m[0::2, 0::2] = bgr[0::2, 0::2, 2]
    m[0::2, 1::2] = bgr[0::2, 1::2, 1]
    m[1::2, 0::2] = bgr[1::2, 0::2, 1]
    m[1::2, 1::2] = bgr[1::2, 1::2, 0]
    return m


class CamaraWebcam:
    """
    Una webcam abierta con OpenCV, con la interfaz de CamaraNativa: abrir/cerrar,
    atributos/poner/leer, configurar/configurar_area, disparar/hay_frame/leer_frame (foto)
    e iniciar_video/leer_frame/parar_video (vídeo).
    """

    def __init__(self, indice=0):
        self._indice = int(indice)
        self._cap = None
        self.propiedades = None
        self._valores = {"Gain": 0}
        self._zona = None
        self._binning = 1
        self._formato = "RAW16"
        self._exposicion_ms = 30.0
        self._exp_log2 = None
        self._descartar = 0
        self._video = False
        self._hilo_foto = None
        self._foto = None
        self._error_foto = None
        self._cortar = threading.Event()
        self._buffer = None
        self.max_adu = 65535

    # ── Apertura y cierre ────────────────────────────────────────────────
    def abrir(self):
        import cv2
        cap = cv2.VideoCapture(self._indice, cv2.CAP_DSHOW)
        if not cap.isOpened():
            raise ErrorWebcam(_("No se puede abrir la webcam {n} (¿desconectada o en uso por otro programa?)",
                                 n=self._indice + 1))
        # La mayor resolución que acepte: se pide 4K y DirectShow se queda con la más cercana
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
        ok, f = cap.read()
        if not ok or f is None:
            cap.release()
            raise ErrorWebcam(_("La webcam se abre pero no da imagen"))
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)       # exposición manual
        self._cap = cap
        try:
            nombres = listar_camaras()
            modelo = nombres[self._indice]["modelo"] if self._indice < len(nombres) else f"Webcam {self._indice + 1}"
        except Exception:
            modelo = f"Webcam {self._indice + 1}"
        try:
            pixel = float(_config("pixel_webcam_um", "3.0").replace(",", "."))
        except ValueError:
            pixel = 3.0
        alto, ancho = f.shape[0] & ~1, f.shape[1] & ~1
        self.propiedades = {"modelo": modelo, "id": self._indice, "serie": "", "sensor": "webcam",
                            "ancho": ancho, "alto": alto, "bits": 8, "color": True, "usb3": False,
                            "bayer": "RGGB", "pixel_um": pixel, "bins": [1, 2, 4], "formatos": list(FORMATOS)}
        self._zona = (0, 0, ancho, alto)
        try:
            self._valores["Gain"] = int(cap.get(cv2.CAP_PROP_GAIN))
        except Exception:
            pass
        self.exposicion(self._exposicion_ms)
        log.info("Webcam abierta: %s, %dx%d, píxel %.2f µm (supuesto)", modelo, ancho, alto, pixel)
        return self.propiedades

    def cerrar(self):
        if self._cap is None:
            return
        self.abortar()
        self._cap.release()
        self._cap = None
        log.info("Webcam cerrada")

    def __enter__(self):
        self.abrir()
        return self

    def __exit__(self, *exc):
        self.cerrar()
        return False

    @property
    def abierta(self):
        return self._cap is not None

    def _necesita(self):
        if self._cap is None:
            raise ErrorWebcam(_("La webcam no está abierta"))
        return self._cap

    # ── Ajustes ──────────────────────────────────────────────────────────
    def atributos(self):
        return [
            {"nombre": "Gain", "id": 0, "tipo": "int", "min": 0, "max": GANANCIA_MAX, "defecto": 0,
             "auto": False, "escribible": True},
            {"nombre": "Exposure", "id": 1, "tipo": "int", "min": int(2 ** EXP_LOG2_MIN * 1e6), "max": 3_600_000_000,
             "defecto": 30000, "auto": False, "escribible": True},
        ]

    def poner(self, nombre, valor, auto=False):
        import cv2
        cap = self._necesita()
        if nombre == "Exposure":
            self.exposicion(float(valor) / 1000.0)
        elif nombre == "Gain":
            v = max(0, min(GANANCIA_MAX, int(valor)))
            if not cap.set(cv2.CAP_PROP_GAIN, v):
                log.info("La webcam no deja cambiar la ganancia")
            self._valores["Gain"] = v
        else:
            raise ErrorWebcam(_("La webcam no tiene el ajuste {nombre}", nombre=nombre))

    def leer(self, nombre):
        if nombre == "Exposure":
            return int(round(self._exposicion_ms * 1000))
        if nombre in self._valores:
            return self._valores[nombre]
        raise ErrorWebcam(_("La webcam no tiene el ajuste {nombre}", nombre=nombre))

    # ── Zona, formato y exposición ───────────────────────────────────────
    def configurar(self, roi_px=1024, binning=1, formato="RAW16", exposicion_ms=30.0, ganancia=None, **kw):
        binning = max(1, int(binning))
        ancho_total = self.propiedades["ancho"] // binning
        alto_total = self.propiedades["alto"] // binning
        lado = max(8, min(int(roi_px) // binning, ancho_total, alto_total)) & ~1
        x0 = ((ancho_total - lado) // 2) & ~1
        y0 = ((alto_total - lado) // 2) & ~1
        return self.configurar_area(x0, y0, lado, lado, binning=binning, formato=formato,
                                    exposicion_ms=exposicion_ms, ganancia=ganancia)

    def configurar_area(self, x0, y0, ancho, alto, binning=1, formato="RAW16", exposicion_ms=30.0, ganancia=None,
                        offset=None, usb_limite=None, fps_max=None):
        """Zona en píxeles YA con binning; el recorte y el binning se hacen aquí, sobre la imagen."""
        if formato not in FORMATOS:
            raise ValueError(_("Formato desconocido: {formato} ({formatos})", formato=formato,
                               formatos=", ".join(FORMATOS)))
        self._necesita()
        self.abortar()
        self._binning = max(1, int(binning))
        self._zona = (int(x0) & ~1, int(y0) & ~1, max(2, int(ancho) & ~1), max(2, int(alto) & ~1))
        self._formato = formato
        self.exposicion(exposicion_ms)
        if ganancia is not None:
            self.poner("Gain", ganancia)
        x0, y0, ancho, alto = self._zona
        self.geometria = {"x0": x0 * self._binning, "y0": y0 * self._binning, "lado": ancho, "ancho": ancho,
                          "alto": alto, "binning": self._binning, "formato": formato,
                          "exposicion_ms": self._exposicion_ms}
        return dict(self.geometria)

    def exposicion(self, exposicion_ms):
        """La potencia de 2 que no pasa de la pedida (el resto se compensa al escalar)."""
        import cv2
        cap = self._necesita()
        self._exposicion_ms = max(float(exposicion_ms), 2 ** EXP_LOG2_MIN * 1000.0)
        e = int(np.floor(np.log2(self._exposicion_ms / 1000.0)))
        e = max(EXP_LOG2_MIN, min(EXP_LOG2_MAX, e))
        if e != self._exp_log2:
            cap.set(cv2.CAP_PROP_EXPOSURE, e)
            self._exp_log2 = e
            self._descartar = 5          # medido: la webcam tarda 3–5 fotogramas en aplicar la exposición nueva
        if hasattr(self, "geometria"):
            self.geometria["exposicion_ms"] = self._exposicion_ms

    def ganancia(self, ganancia):
        self.poner("Gain", ganancia)

    # ── Fotogramas ───────────────────────────────────────────────────────
    def _leer_bgr(self):
        cap = self._necesita()
        while self._descartar > 0:
            cap.read()
            self._descartar -= 1
        ok, f = cap.read()
        if not ok or f is None:
            raise ErrorWebcam(_("La webcam no ha dado imagen (¿desconectada?)"))
        return f

    def _convertir(self, bgr, escala):
        """BGR (float o uint8) del sensor completo → zona, binning y formato pedidos."""
        import cv2
        b = self._binning
        if b > 1:
            bgr = cv2.resize(bgr, (bgr.shape[1] // b, bgr.shape[0] // b), interpolation=cv2.INTER_AREA)
        x0, y0, ancho, alto = self._zona
        bgr = bgr[y0:y0 + alto, x0:x0 + ancho]
        f = np.asarray(bgr, dtype=np.float32) * escala
        if self._formato == "RGB24":
            return np.clip(f, 0, 255).astype(np.uint8)
        if self._formato == "MONO8":
            return np.clip(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 0, 255).astype(np.uint8)
        m = mosaico_rggb(f)
        if self._formato == "RAW16":
            return np.clip(m * 256.0, 0, 65535).astype(np.uint16)
        return np.clip(m, 0, 255).astype(np.uint8)

    def _escala(self):
        """Lo que falta de la exposición pedida respecto a la potencia de 2 de la webcam."""
        return (self._exposicion_ms / 1000.0) / (2.0 ** self._exp_log2)

    def iniciar_video(self):
        self._necesita()
        self._video = True

    def parar_video(self):
        self._video = False

    def disparar(self):
        """Suma en segundo plano los fotogramas que caben en la exposición pedida."""
        self._necesita()
        self.abortar()
        self._cortar.clear()
        self._foto, self._error_foto = None, None
        self._hilo_foto = threading.Thread(target=self._integrar, name="webcam-foto", daemon=True)
        self._hilo_foto.start()

    def _integrar(self):
        try:
            hw = 2.0 ** self._exp_log2
            fin = time.monotonic() + max(self._exposicion_ms / 1000.0, hw)
            suma, n = None, 0
            while n == 0 or time.monotonic() < fin:
                if self._cortar.is_set():
                    return
                f = self._leer_bgr().astype(np.float32)
                suma = f if suma is None else suma + f
                n += 1
            # Media de n fotogramas de `hw` segundos, escalada a la exposición pedida
            self._foto = self._convertir(suma / n, self._escala())
            log.debug("Webcam: foto de %.3f s con %d fotograma(s) de %.4f s", self._exposicion_ms / 1000.0, n, hw)
        except Exception as e:
            self._error_foto = e

    def hay_frame(self):
        if self._hilo_foto is not None:
            if self._error_foto:
                raise ErrorWebcam(str(self._error_foto))
            return self._foto is not None
        return self._video

    def abortar(self):
        self._cortar.set()
        if self._hilo_foto is not None:
            self._hilo_foto.join(timeout=5)
            self._hilo_foto = None
        self._video = False

    def foto(self, timeout_ms=None):
        self.disparar()
        return self.leer_frame(timeout_ms).copy()

    def leer_frame(self, timeout_ms=None):
        """En modo foto espera la suma; en vídeo lee el siguiente fotograma."""
        if self._hilo_foto is not None:
            espera = self._exposicion_ms / 1000.0 + (timeout_ms or 5000) / 1000.0
            self._hilo_foto.join(timeout=espera)
            if self._error_foto:
                raise ErrorWebcam(str(self._error_foto))
            if self._foto is None:
                raise ErrorWebcam(_("La foto de la webcam no terminó a tiempo"))
            self._hilo_foto = None
            self._buffer = self._foto
            return self._buffer
        if not self._video:
            raise ErrorWebcam(_("No hay ninguna exposición ni vídeo en marcha"))
        self._buffer = self._convertir(self._leer_bgr(), self._escala())
        return self._buffer

    def descartados(self):
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    camaras = listar_camaras()
    print(camaras)
    if camaras:
        with CamaraWebcam() as cam:
            cam.configurar_area(0, 0, cam.propiedades["ancho"], cam.propiedades["alto"], formato="RAW16",
                                exposicion_ms=60)
            for exp_ms in (30, 120, 1000):
                cam.exposicion(exp_ms)
                t = time.perf_counter()
                f = cam.foto()
                print(f"foto {exp_ms} ms: {f.shape} {f.dtype}, media {f.mean():.0f}, "
                      f"tardó {time.perf_counter() - t:.2f} s")
            cam.configurar(roi_px=512, binning=2, formato="RAW8", exposicion_ms=30)
            cam.iniciar_video()
            t0, n = time.perf_counter(), 0
            while time.perf_counter() - t0 < 2:
                f = cam.leer_frame()
                n += 1
            print(f"vídeo: {n / (time.perf_counter() - t0):.1f} fps, {f.shape} {f.dtype}, media {f.mean():.1f}")
