# camara_nativa.py
#
# Acceso directo a las cámaras Player One por su SDK nativo (PlayerOneCamera.dll)
# mediante ctypes, sin pasar por ASCOM.
#
# ASCOM ICamera no tiene modo vídeo: cada fotograma es StartExposure más la
# descarga del ImageArray por COM, unos 4 s con la Saturn-C a resolución
# completa. Para seguir objetos en movimiento (seguimiento.py) hacen falta
# varios fotogramas por segundo con latencia baja, y eso solo lo da el SDK:
# exposición continua, ROI y binning en la propia cámara y lectura del
# fotograma en un búfer numpy sin copias. Medido el 2026-09-20 con la
# Saturn-C SQR real por USB 2.0: bin 2 + ROI 1024 + RAW8 → 126 fps.
#
# Decisiones:
#   - La DLL se busca en las rutas donde la dejan el driver ASCOM de Player One
#     y N.I.N.A.; no hace falta instalar nada más. Se puede indicar otra ruta.
#   - El driver ASCOM de Player One solo admite una conexión: quien use esta
#     clase debe desconectar antes la cámara ASCOM de la app (hardware.py) y
#     reconectarla al terminar.
#   - Los índices del enum POAConfig se releen de la cámara al abrirla
#     (POAGetConfigAttributes, nombre → índice) por si cambian con el SDK; los
#     valores por defecto son los confirmados con el SDK 3.9.1.
#   - El fotograma se lee en un único búfer reutilizado (leer_frame devuelve
#     una vista; copiar si se quiere conservar) para no asignar memoria en
#     cada ciclo del bucle de seguimiento.
#
# Este módulo no depende de Flask ni del resto de la app; lo usan
# seguimiento.py y herramientas/medir_camara_sdk.py.

import os
import ctypes
import logging
import threading
from ctypes import (c_int, c_long, c_double, c_char, c_char_p, c_ubyte,
                    POINTER, byref, Structure, Union)

import numpy as np

log = logging.getLogger("scopepower.camara_nativa")

RUTAS_DLL = (
    r"C:\Program Files (x86)\Common Files\ASCOM\Camera\PlayerOneCamera_x64.dll",
    r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\External\x64\PlayerOne\PlayerOneCamera.dll",
    "PlayerOneCamera.dll",
)

# Formatos de imagen (POAImgFormat)
RAW8, RAW16, RGB24, MONO8 = 0, 1, 2, 3
FORMATOS = {"RAW8": RAW8, "RAW16": RAW16, "RGB24": RGB24, "MONO8": MONO8}

# Patrón Bayer (POABayerPattern)
PATRONES_BAYER = {0: "RGGB", 1: "BGGR", 2: "GRBG", 3: "GBRG", -1: "MONO"}

# Índices de POAConfig confirmados con el SDK 3.9.1 (se releen al abrir la cámara)
CONFIG_POR_DEFECTO = {
    "Exposure": 0, "Gain": 1, "HardwareBin": 2, "Temperature": 3,
    "WB_R": 4, "WB_G": 5, "WB_B": 6, "Offset": 7, "FrameLimit": 26,
    "USBBandWidthLimit": 28, "Pixels Binning Sum": 29, "Mono Bin": 30,
}

# Estado de la cámara (POACameraState)
ESTADO_CERRADA, ESTADO_ABIERTA, ESTADO_EXPONIENDO = 0, 1, 2


# POAErrors: "exposure is in progress"
ERROR_EXPONIENDO = 11


class ErrorCamaraNativa(Exception):
    """Fallo del SDK de Player One (con el texto de POAGetErrorString)."""


class POACameraProperties(Structure):
    _fields_ = [
        ("cameraModelName", c_char * 256), ("userCustomID", c_char * 16),
        ("cameraID", c_int), ("maxWidth", c_int), ("maxHeight", c_int),
        ("bitDepth", c_int), ("isColorCamera", c_int), ("isHasST4Port", c_int),
        ("isHasCooler", c_int), ("isUSB3Speed", c_int), ("bayerPattern", c_int),
        ("pixelSize", c_double), ("SN", c_char * 64), ("sensorModelName", c_char * 32),
        ("localPath", c_char * 256), ("bins", c_int * 8), ("imgFormats", c_int * 8),
        ("isSupportHardBin", c_int), ("pID", c_int), ("reserved", c_char * 248),
    ]


class POAConfigValue(Union):
    _fields_ = [("intValue", c_long), ("floatValue", c_double), ("boolValue", c_int)]


class POAConfigAttributes(Structure):
    _fields_ = [
        ("isSupportAuto", c_int), ("isWritable", c_int), ("isReadable", c_int),
        ("configID", c_int), ("valueType", c_int),
        ("maxValue", POAConfigValue), ("minValue", POAConfigValue), ("defaultValue", POAConfigValue),
        ("szConfName", c_char * 64), ("szDescription", c_char * 128), ("reserved", c_char * 64),
    ]


_dll = None
_dll_lock = threading.Lock()


def cargar_dll(ruta=None):
    """Carga la DLL una sola vez y declara los prototipos. Devuelve el handle ctypes."""
    global _dll
    with _dll_lock:
        if _dll is not None:
            return _dll
        candidatas = [ruta] if ruta else list(RUTAS_DLL)
        ultimo_error = None
        for r in candidatas:
            if r != "PlayerOneCamera.dll" and not os.path.exists(r):
                continue
            try:
                dll = ctypes.CDLL(r)
                break
            except OSError as e:
                ultimo_error = e
        else:
            raise ErrorCamaraNativa(f"No se encuentra PlayerOneCamera.dll ({ultimo_error or 'ninguna ruta válida'})")
        _declarar_prototipos(dll)
        _dll = dll
        log.info("SDK Player One %s cargado de %s", dll.POAGetSDKVersion().decode(errors="replace"), r)
        return dll


def _declarar_prototipos(dll):
    dll.POAGetSDKVersion.restype = c_char_p
    dll.POAGetSDKVersion.argtypes = []
    dll.POAGetErrorString.restype = c_char_p
    dll.POAGetErrorString.argtypes = [c_int]
    dll.POAGetCameraCount.restype = c_int
    dll.POAGetCameraCount.argtypes = []
    dll.POAGetCameraProperties.argtypes = [c_int, POINTER(POACameraProperties)]
    for nombre in ("POAOpenCamera", "POAInitCamera", "POACloseCamera", "POAStopExposure"):
        getattr(dll, nombre).argtypes = [c_int]
    dll.POAGetCameraState.argtypes = [c_int, POINTER(c_int)]
    dll.POAGetConfigsCount.argtypes = [c_int, POINTER(c_int)]
    dll.POAGetConfigAttributes.argtypes = [c_int, c_int, POINTER(POAConfigAttributes)]
    dll.POASetImageBin.argtypes = [c_int, c_int]
    dll.POASetImageSize.argtypes = [c_int, c_int, c_int]
    dll.POASetImageStartPos.argtypes = [c_int, c_int, c_int]
    dll.POASetImageFormat.argtypes = [c_int, c_int]
    dll.POAGetImageBin.argtypes = [c_int, POINTER(c_int)]
    dll.POAGetImageSize.argtypes = [c_int, POINTER(c_int), POINTER(c_int)]
    dll.POAGetImageStartPos.argtypes = [c_int, POINTER(c_int), POINTER(c_int)]
    dll.POAGetImageFormat.argtypes = [c_int, POINTER(c_int)]
    dll.POASetConfig.argtypes = [c_int, c_int, POAConfigValue, c_int]
    dll.POAGetConfig.argtypes = [c_int, c_int, POINTER(POAConfigValue), POINTER(c_int)]
    dll.POAStartExposure.argtypes = [c_int, c_int]
    dll.POAImageReady.argtypes = [c_int, POINTER(c_int)]
    dll.POAGetImageData.argtypes = [c_int, POINTER(c_ubyte), c_long, c_int]
    dll.POAGetDroppedImagesCount.argtypes = [c_int, POINTER(c_int)]
    for f in ("POAGetCameraProperties", "POAOpenCamera", "POAInitCamera", "POACloseCamera",
              "POAStopExposure", "POAGetCameraState", "POAGetConfigsCount", "POAGetConfigAttributes",
              "POASetImageBin", "POASetImageSize", "POASetImageStartPos", "POASetImageFormat",
              "POAGetImageBin", "POAGetImageSize", "POAGetImageStartPos", "POAGetImageFormat",
              "POASetConfig", "POAGetConfig", "POAStartExposure", "POAImageReady", "POAGetImageData",
              "POAGetDroppedImagesCount"):
        getattr(dll, f).restype = c_int


def listar_camaras(ruta_dll=None):
    """Propiedades (dict) de cada cámara Player One conectada, sin abrirlas."""
    dll = cargar_dll(ruta_dll)
    camaras = []
    for i in range(dll.POAGetCameraCount()):
        p = POACameraProperties()
        if dll.POAGetCameraProperties(i, byref(p)) == 0:
            camaras.append(_propiedades_a_dict(p))
    return camaras


def _propiedades_a_dict(p):
    return {
        "modelo": p.cameraModelName.decode(errors="replace"),
        "id": int(p.cameraID), "serie": p.SN.decode(errors="replace"),
        "sensor": p.sensorModelName.decode(errors="replace"),
        "ancho": int(p.maxWidth), "alto": int(p.maxHeight), "bits": int(p.bitDepth),
        "color": bool(p.isColorCamera), "usb3": bool(p.isUSB3Speed),
        "bayer": PATRONES_BAYER.get(int(p.bayerPattern), str(int(p.bayerPattern))),
        "pixel_um": float(p.pixelSize),
        "bins": [int(b) for b in p.bins if b > 0],
        "formatos": [n for n, v in FORMATOS.items() if v in [int(f) for f in p.imgFormats]],
    }


class CamaraNativa:
    """
    Una cámara Player One abierta por el SDK.

    Uso típico:
        with CamaraNativa() as cam:
            cam.configurar(roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5, ganancia=300)
            cam.iniciar_video()
            frame = cam.leer_frame()      # vista sobre el búfer interno (uint8 h×w)
    """

    def __init__(self, indice=0, ruta_dll=None):
        self._dll = cargar_dll(ruta_dll)
        self._indice = indice
        self._id = None
        self.propiedades = None
        self.config_ids = dict(CONFIG_POR_DEFECTO)
        self._buffer = None
        self._forma = None
        self._bytes = 0
        self._exposicion_ms = 10.0
        self._video = False
        self._lock = threading.Lock()

    # ── Apertura y cierre ────────────────────────────────────────────────
    def abrir(self):
        dll = self._dll
        n = dll.POAGetCameraCount()
        if n <= self._indice:
            raise ErrorCamaraNativa("No hay ninguna cámara Player One conectada" if n == 0
                                    else f"Solo hay {n} cámara(s) Player One; pedida la {self._indice}")
        p = POACameraProperties()
        self._ok(dll.POAGetCameraProperties(self._indice, byref(p)), "leer propiedades")
        self.propiedades = _propiedades_a_dict(p)
        cid = int(p.cameraID)
        self._ok(dll.POAOpenCamera(cid), "abrir la cámara (¿la tiene otra aplicación?)")
        try:
            self._ok(dll.POAInitCamera(cid), "inicializar la cámara")
        except ErrorCamaraNativa:
            dll.POACloseCamera(cid)
            raise
        self._id = cid
        self._leer_config_ids()
        log.info("Cámara nativa abierta: %s (%s, %dx%d, %s, USB3=%s)", self.propiedades["modelo"],
                 self.propiedades["sensor"], self.propiedades["ancho"], self.propiedades["alto"],
                 self.propiedades["bayer"], self.propiedades["usb3"])
        return self.propiedades

    def cerrar(self):
        if self._id is None:
            return
        try:
            self.parar_video()
        except ErrorCamaraNativa:
            pass
        self._dll.POACloseCamera(self._id)
        log.info("Cámara nativa cerrada")
        self._id = None

    def __enter__(self):
        self.abrir()
        return self

    def __exit__(self, *exc):
        self.cerrar()
        return False

    @property
    def abierta(self):
        return self._id is not None

    # ── Configuración ────────────────────────────────────────────────────
    def _leer_config_ids(self):
        """Relee nombre → índice de POAConfig de la propia cámara (por si cambia el SDK)."""
        n = c_int()
        if self._dll.POAGetConfigsCount(self._id, byref(n)) != 0:
            return
        encontrados = {}
        for i in range(n.value):
            a = POAConfigAttributes()
            if self._dll.POAGetConfigAttributes(self._id, i, byref(a)) == 0:
                encontrados[a.szConfName.decode(errors="replace")] = int(a.configID)
        cambios = {k: v for k, v in encontrados.items() if k in self.config_ids and self.config_ids[k] != v}
        if cambios:
            log.warning("Índices de POAConfig distintos de los esperados: %s", cambios)
        self.config_ids.update(encontrados)

    def atributos(self):
        """Lista de ajustes que expone la cámara: nombre, índice, tipo, rango y valor por defecto."""
        n = c_int()
        self._ok(self._dll.POAGetConfigsCount(self._id, byref(n)), "contar ajustes")
        salida = []
        for i in range(n.value):
            a = POAConfigAttributes()
            self._ok(self._dll.POAGetConfigAttributes(self._id, i, byref(a)), "leer ajuste")
            flotante = a.valueType == 1
            salida.append({
                "nombre": a.szConfName.decode(errors="replace"), "id": int(a.configID),
                "tipo": "float" if flotante else ("bool" if a.valueType == 2 else "int"),
                "min": a.minValue.floatValue if flotante else int(a.minValue.intValue),
                "max": a.maxValue.floatValue if flotante else int(a.maxValue.intValue),
                "defecto": a.defaultValue.floatValue if flotante else int(a.defaultValue.intValue),
                "auto": bool(a.isSupportAuto), "escribible": bool(a.isWritable),
            })
        return salida

    def poner(self, nombre, valor, auto=False):
        """Fija un ajuste entero/bool por su nombre de POAConfig (p. ej. "Gain", "Exposure" en µs)."""
        if nombre not in self.config_ids:
            raise ErrorCamaraNativa(f"La cámara no tiene el ajuste {nombre}")
        v = POAConfigValue()
        v.intValue = int(valor)
        self._ok(self._dll.POASetConfig(self._id, self.config_ids[nombre], v, 1 if auto else 0), f"fijar {nombre}")

    def leer(self, nombre):
        if nombre not in self.config_ids:
            raise ErrorCamaraNativa(f"La cámara no tiene el ajuste {nombre}")
        v = POAConfigValue()
        auto = c_int()
        self._ok(self._dll.POAGetConfig(self._id, self.config_ids[nombre], byref(v), byref(auto)), f"leer {nombre}")
        if nombre == "Temperature":
            return float(v.floatValue)
        return int(v.intValue)

    def configurar(self, roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5.0, ganancia=None,
                   offset=None, usb_limite=100, fps_max=None):
        """
        ROI cuadrado de `roi_px` píxeles del sensor (antes del binning), centrado, con
        `binning` y formato de imagen. Para el vídeo en curso si lo había.
        """
        binning = max(1, int(binning))
        ancho_total = self.propiedades["ancho"] // binning
        alto_total = self.propiedades["alto"] // binning
        lado = max(8, min(int(roi_px) // binning, ancho_total, alto_total)) & ~1
        x0 = ((ancho_total - lado) // 2) & ~1
        y0 = ((alto_total - lado) // 2) & ~1
        return self.configurar_area(x0, y0, lado, lado, binning=binning, formato=formato, exposicion_ms=exposicion_ms,
                                    ganancia=ganancia, offset=offset, usb_limite=usb_limite, fps_max=fps_max)

    def configurar_area(self, x0, y0, ancho, alto, binning=1, formato="RAW16", exposicion_ms=5.0, ganancia=None,
                        offset=None, usb_limite=100, fps_max=None):
        """
        Zona rectangular en píxeles YA con binning (origen x0, y0; ancho × alto), binning y
        formato. La usa camara_sdk.py (fotos a campo completo como el driver ASCOM); configurar()
        la llama con un cuadrado centrado.
        """
        dll, cid = self._dll, self._id
        if formato not in FORMATOS:
            raise ValueError(f"Formato desconocido: {formato} (RAW8, RAW16, RGB24 o MONO8)")
        with self._lock:
            self.parar_video()
            binning = max(1, int(binning))
            ancho, alto = int(ancho) & ~1, int(alto) & ~1
            x0, y0 = int(x0) & ~1, int(y0) & ~1
            lado = ancho          # nombre histórico en la geometría (ROI cuadrado)
            self._ok(dll.POASetImageBin(cid, binning), "fijar binning")
            self._ok(dll.POASetImageSize(cid, ancho, alto), "fijar tamaño de imagen")
            self._ok(dll.POASetImageStartPos(cid, x0, y0), "fijar origen del ROI")
            self._ok(dll.POASetImageFormat(cid, FORMATOS[formato]), "fijar formato")
            if usb_limite is not None and "USBBandWidthLimit" in self.config_ids:
                self.poner("USBBandWidthLimit", usb_limite)
            self.poner("Exposure", int(round(float(exposicion_ms) * 1000)))
            self._exposicion_ms = float(exposicion_ms)
            if ganancia is not None:
                self.poner("Gain", ganancia)
            if offset is not None:
                self.poner("Offset", offset)
            if "FrameLimit" in self.config_ids:
                # Cadencia máxima en la propia cámara: leer más despacio de lo que produce
                # acumularía fotogramas viejos en el búfer del SDK (latencia)
                self.poner("FrameLimit", int(round(fps_max)) if fps_max else 0)
            # Búfer único para los fotogramas (filas = y, columnas = x)
            if formato == "RAW16":
                self._buffer = np.empty((alto, ancho), dtype=np.uint16)
            elif formato == "RGB24":
                self._buffer = np.empty((alto, ancho, 3), dtype=np.uint8)
            else:
                self._buffer = np.empty((alto, ancho), dtype=np.uint8)
            self._bytes = int(self._buffer.nbytes)
            self._forma = self._buffer.shape
            self.geometria = {"x0": x0 * binning, "y0": y0 * binning, "lado": lado, "ancho": ancho, "alto": alto,
                              "binning": binning, "formato": formato, "exposicion_ms": self._exposicion_ms}
            log.info("Cámara nativa: zona %dx%d en (%d,%d) bin %d %s, exp %.2f ms, gain %s",
                     ancho, alto, x0, y0, binning, formato, self._exposicion_ms, ganancia)
            return dict(self.geometria)

    def exposicion(self, exposicion_ms):
        """Cambia la exposición en caliente (µs internos)."""
        self.poner("Exposure", int(round(float(exposicion_ms) * 1000)))
        self._exposicion_ms = float(exposicion_ms)
        if hasattr(self, "geometria"):
            self.geometria["exposicion_ms"] = self._exposicion_ms

    def ganancia(self, ganancia):
        self.poner("Gain", ganancia)

    # ── Vídeo ─────────────────────────────────────────────────────────────
    def iniciar_video(self):
        if self._buffer is None:
            raise ErrorCamaraNativa("Configura la cámara antes de iniciar el vídeo")
        if not self._video:
            self._ok(self._dll.POAStartExposure(self._id, 0), "iniciar la exposición continua")
            self._video = True

    def parar_video(self):
        if self._id is not None and self._video:
            self._dll.POAStopExposure(self._id)
            self._video = False

    def _empezar_una(self):
        """
        POAStartExposure en modo foto. Si la DLL dice que aún hay una exposición en curso
        (error 11: visto el 28-sep-2026 al reconfigurar entre fotos), la corta y reintenta.
        """
        err = self._dll.POAStartExposure(self._id, 1)
        if err == ERROR_EXPONIENDO:
            log.info("Cámara nativa: había una exposición en curso; se corta y se repite el disparo")
            self._dll.POAStopExposure(self._id)
            import time
            time.sleep(0.05)
            err = self._dll.POAStartExposure(self._id, 1)
        self._ok(err, "iniciar la exposición")

    def disparar(self):
        """Empieza una sola exposición y vuelve enseguida (hay_frame() dice cuándo está; leer_frame() la lee)."""
        if self._buffer is None:
            raise ErrorCamaraNativa("Configura la cámara antes de disparar")
        self.parar_video()
        self._empezar_una()

    def abortar(self):
        """Corta la exposición en curso (foto o vídeo)."""
        if self._id is not None:
            self._dll.POAStopExposure(self._id)
            self._video = False

    def foto(self, timeout_ms=None):
        """Una sola exposición (modo foto) y devuelve el fotograma (copia)."""
        if self._buffer is None:
            raise ErrorCamaraNativa("Configura la cámara antes de disparar")
        self.parar_video()
        self._empezar_una()
        return self.leer_frame(timeout_ms).copy()

    def leer_frame(self, timeout_ms=None):
        """
        Espera el siguiente fotograma y devuelve el búfer interno como array numpy
        (h×w uint8/uint16, o h×w×3 en RGB24). Es una vista: copiar para conservar.
        """
        if self._buffer is None:
            raise ErrorCamaraNativa("Configura la cámara antes de leer fotogramas")
        if timeout_ms is None:
            timeout_ms = int(self._exposicion_ms + 500)
        ptr = self._buffer.ctypes.data_as(POINTER(c_ubyte))
        err = self._dll.POAGetImageData(self._id, ptr, self._bytes, int(timeout_ms))
        if err != 0:
            raise ErrorCamaraNativa(f"leer fotograma: {self.texto_error(err)}")
        return self._buffer

    def hay_frame(self):
        listo = c_int()
        self._ok(self._dll.POAImageReady(self._id, byref(listo)), "consultar fotograma")
        return bool(listo.value)

    def descartados(self):
        n = c_int()
        if self._dll.POAGetDroppedImagesCount(self._id, byref(n)) != 0:
            return -1
        return int(n.value)

    def estado(self):
        s = c_int()
        self._ok(self._dll.POAGetCameraState(self._id, byref(s)), "leer estado")
        return int(s.value)

    # ── Errores ───────────────────────────────────────────────────────────
    def texto_error(self, err):
        try:
            return self._dll.POAGetErrorString(int(err)).decode(errors="replace") + f" ({err})"
        except Exception:
            return f"error {err}"

    def _ok(self, err, contexto):
        if err != 0:
            raise ErrorCamaraNativa(f"{contexto}: {self.texto_error(err)}")


def luminancia_rapida(frame):
    """
    Plano de luminancia uint8 a media resolución a partir de un fotograma RAW
    (CFA) o RGB24: para detectar manchas basta sumar los cuatro píxeles de cada
    celda Bayer, lo que además elimina el patrón de color sin debayerizar.
    """
    if frame.ndim == 3:
        return cv2_gris(frame)
    h, w = frame.shape[0] & ~1, frame.shape[1] & ~1
    f = frame[:h, :w]
    if f.dtype == np.uint16:
        suma = (f[0::2, 0::2].astype(np.uint32) + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2])
        return (suma >> 10).astype(np.uint8)  # 4 × 16 bits (18 bits) → 8 bits
    suma = (f[0::2, 0::2].astype(np.uint16) + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2])
    return (suma >> 2).astype(np.uint8)


def cv2_gris(rgb):
    import cv2
    return cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)


if __name__ == "__main__":
    # Sondeo rápido: lista las cámaras, abre la primera y saca unos fotogramas
    import time
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for c in listar_camaras():
        print(c)
    with CamaraNativa() as cam:
        for a in cam.atributos():
            print(f"  {a['id']:2d} {a['nombre']:24s} {a['tipo']:5s} {a['min']}..{a['max']} (defecto {a['defecto']})")
        cam.configurar(roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5, ganancia=200)
        cam.iniciar_video()
        t0 = time.perf_counter()
        n = 0
        while time.perf_counter() - t0 < 2:
            f = cam.leer_frame()
            n += 1
        print(f"{n / (time.perf_counter() - t0):.1f} fps, media {f.mean():.1f}, descartados {cam.descartados()}")
