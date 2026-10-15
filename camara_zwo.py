# camara_zwo.py
#
# Cámaras ZWO ASI por su SDK nativo (ASICamera2.dll) mediante ctypes, sin
# ASCOM, con la misma interfaz que CamaraNativa (Player One): camara_sdk.py la
# envuelve como si fuera un driver ASCOM y vivo_dia.py y seguimiento.py la usan
# para el vídeo. Así una ZWO hace en la app todo lo que hace la Saturn-C.
#
# Decisiones:
#   - La DLL no se distribuye con la app: se busca donde la dejan N.I.N.A.,
#     SharpCap, ASIStudio o el driver ASCOM de ZWO (ASICamera2_ASCOM_x64.dll,
#     la misma API con otro nombre; verificada el 15-oct-2026 con el SDK
#     1.37). Se puede indicar otra en config.ini, [CamaraSDK] dll_zwo.
#   - Los ajustes se nombran como en Player One ("Gain", "Exposure" en µs,
#     "Offset", "Temperature" en °C) para que el resto de la app no sepa de
#     qué marca es la cámara. La temperatura del SDK viene en décimas.
#   - El ancho de la zona se redondea a múltiplo de 8 y el alto a múltiplo de
#     2 (lo exige el SDK; con el binning se aplica sobre la zona ya binneada).
#   - El ancho de banda USB se deja en el valor de la cámara salvo que se pida
#     otro en [CamaraSDK] usb_zwo (40–100): con USB 2 valores altos pierden
#     fotogramas.
#   - Hecho sin una ZWO a mano: la API está comprobada contra la DLL real
#     (carga, versión, recuento de cámaras), pero abrir, exponer y leer
#     fotogramas quedan pendientes de probar con una cámara de verdad.

import os
import glob
import time
import ctypes
import logging
import threading
import configparser
from ctypes import c_int, c_long, c_double, c_float, c_char, c_char_p, c_ubyte, POINTER, byref, Structure

import numpy as np

from idioma import _, ngettext, N_

log = logging.getLogger("scopepower.camara_zwo")

NOMBRE_MARCA = "ZWO ASI"

RUTAS_DLL = (
    r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\External\x64\ASI\ASICamera2.dll",
    r"C:\Program Files\SharpCap*\ASICamera2.dll",
    r"C:\Program Files\ASIStudio\ASICamera2.dll",
    r"C:\Program Files (x86)\Common Files\ASCOM\ZWO\ASICamera2_ASCOM_x64.dll",
    "ASICamera2.dll",
)

# ASI_IMG_TYPE
ASI_RAW8, ASI_RGB24, ASI_RAW16, ASI_Y8 = 0, 1, 2, 3
FORMATOS = {"RAW8": ASI_RAW8, "RAW16": ASI_RAW16, "RGB24": ASI_RGB24, "MONO8": ASI_Y8}

# ASI_BAYER_PATTERN
PATRONES_BAYER = {0: "RGGB", 1: "BGGR", 2: "GRBG", 3: "GBRG"}

# ASI_CONTROL_TYPE → nombre de la app (el de Player One cuando existe)
CONTROLES = {
    0: "Gain", 1: "Exposure", 2: "Gamma", 3: "WB_R", 4: "WB_B", 5: "Offset", 6: "USBBandWidthLimit",
    7: "Overclock", 8: "Temperature", 9: "Flip", 10: "AutoMaxGain", 11: "AutoMaxExp", 12: "AutoTargetBrightness",
    13: "HardwareBin", 14: "HighSpeedMode", 15: "CoolerPowerPerc", 16: "TargetTemp", 17: "CoolerOn",
    18: "Mono Bin", 19: "FanOn", 20: "PatternAdjust", 21: "AntiDewHeater",
}
TIPO_CONTROL = {nombre: tipo for tipo, nombre in CONTROLES.items()}

# ASI_EXPOSURE_STATUS
EXP_IDLE, EXP_TRABAJANDO, EXP_EXITO, EXP_FALLO = 0, 1, 2, 3

ERRORES = {
    1: N_("índice de cámara no válido"), 2: N_("identificador de cámara no válido"), 3: N_("ajuste no válido"),
    4: N_("la cámara está cerrada"), 5: N_("la cámara se ha desconectado"), 6: N_("ruta no válida"),
    7: N_("formato de archivo no válido"), 8: N_("tamaño de imagen no válido"), 9: N_("tipo de imagen no válido"),
    10: N_("la zona se sale del sensor"), 11: N_("tiempo de espera agotado"), 12: N_("orden de llamadas no válido"),
    13: N_("búfer demasiado pequeño"), 14: N_("el vídeo está en marcha"), 15: N_("hay una exposición en curso"),
    16: N_("error general"), 17: N_("modo no válido"),
}
ERROR_EXPONIENDO = 15


class ErrorCamaraZWO(Exception):
    """Fallo del SDK de ZWO."""


class ASI_CAMERA_INFO(Structure):
    _fields_ = [
        ("Name", c_char * 64), ("CameraID", c_int), ("MaxHeight", c_long), ("MaxWidth", c_long),
        ("IsColorCam", c_int), ("BayerPattern", c_int), ("SupportedBins", c_int * 16),
        ("SupportedVideoFormat", c_int * 8), ("PixelSize", c_double), ("MechanicalShutter", c_int),
        ("ST4Port", c_int), ("IsCoolerCam", c_int), ("IsUSB3Host", c_int), ("IsUSB3Camera", c_int),
        ("ElecPerADU", c_float), ("BitDepth", c_int), ("IsTriggerCam", c_int), ("Unused", c_char * 16),
    ]


class ASI_CONTROL_CAPS(Structure):
    _fields_ = [
        ("Name", c_char * 64), ("Description", c_char * 128), ("MaxValue", c_long), ("MinValue", c_long),
        ("DefaultValue", c_long), ("IsAutoSupported", c_int), ("IsWritable", c_int), ("ControlType", c_int),
        ("Unused", c_char * 32),
    ]


_dll = None
_dll_lock = threading.Lock()


def _config(clave, defecto=""):
    cfg = configparser.ConfigParser()
    try:
        cfg.read("config.ini", encoding="utf-8")
    except configparser.Error:
        pass
    return cfg.get("CamaraSDK", clave, fallback=defecto).strip()


def _candidatas(ruta=None):
    if ruta:
        return [ruta]
    propia = _config("dll_zwo")
    salida = [propia] if propia else []
    for r in RUTAS_DLL:
        if "*" in r:
            salida.extend(sorted(glob.glob(r), reverse=True))     # la versión más nueva de SharpCap primero
        else:
            salida.append(r)
    return salida


def cargar_dll(ruta=None):
    """Carga la DLL una sola vez y declara los prototipos. Devuelve el handle ctypes."""
    global _dll
    with _dll_lock:
        if _dll is not None:
            return _dll
        ultimo_error = None
        for r in _candidatas(ruta):
            if os.path.dirname(r) and not os.path.exists(r):
                continue
            try:
                if os.path.dirname(r):
                    os.add_dll_directory(os.path.dirname(r))     # sus dependencias están al lado
                dll = ctypes.CDLL(r, winmode=0)
                break
            except (OSError, AttributeError) as e:
                ultimo_error = e
        else:
            raise ErrorCamaraZWO(_("No se encuentra ASICamera2.dll: instala el driver de ZWO o N.I.N.A., o indica "
                                   "la ruta en config.ini, [CamaraSDK] dll_zwo ({error})",
                                   error=ultimo_error or _("ninguna ruta válida")))
        _declarar_prototipos(dll)
        _dll = dll
        log.info("SDK ZWO %s cargado de %s", dll.ASIGetSDKVersion().decode(errors="replace"), r)
        return dll


def _declarar_prototipos(dll):
    dll.ASIGetSDKVersion.restype = c_char_p
    dll.ASIGetSDKVersion.argtypes = []
    dll.ASIGetNumOfConnectedCameras.restype = c_int
    dll.ASIGetNumOfConnectedCameras.argtypes = []
    firmas = {
        "ASIGetCameraProperty": [POINTER(ASI_CAMERA_INFO), c_int],
        "ASIOpenCamera": [c_int], "ASIInitCamera": [c_int], "ASICloseCamera": [c_int],
        "ASIGetNumOfControls": [c_int, POINTER(c_int)],
        "ASIGetControlCaps": [c_int, c_int, POINTER(ASI_CONTROL_CAPS)],
        "ASIGetControlValue": [c_int, c_int, POINTER(c_long), POINTER(c_int)],
        "ASISetControlValue": [c_int, c_int, c_long, c_int],
        "ASISetROIFormat": [c_int, c_int, c_int, c_int, c_int],
        "ASISetStartPos": [c_int, c_int, c_int],
        "ASIStartVideoCapture": [c_int], "ASIStopVideoCapture": [c_int],
        "ASIGetVideoData": [c_int, POINTER(c_ubyte), c_long, c_int],
        "ASIGetDroppedFrames": [c_int, POINTER(c_int)],
        "ASIStartExposure": [c_int, c_int], "ASIStopExposure": [c_int],
        "ASIGetExpStatus": [c_int, POINTER(c_int)],
        "ASIGetDataAfterExp": [c_int, POINTER(c_ubyte), c_long],
    }
    for nombre, args in firmas.items():
        f = getattr(dll, nombre)
        f.argtypes = args
        f.restype = c_int


def _texto_error(err):
    texto = _(ERRORES[int(err)]) if int(err) in ERRORES else _("error desconocido")
    return f"{texto} ({err})"


def _info_a_dict(p):
    color = bool(p.IsColorCam)
    return {
        "modelo": p.Name.decode(errors="replace").strip(), "id": int(p.CameraID), "serie": "",
        "sensor": "", "ancho": int(p.MaxWidth), "alto": int(p.MaxHeight), "bits": int(p.BitDepth),
        "color": color, "usb3": bool(p.IsUSB3Camera and p.IsUSB3Host),
        "bayer": PATRONES_BAYER.get(int(p.BayerPattern), "RGGB") if color else "MONO",
        "pixel_um": float(p.PixelSize),
        "bins": [int(b) for b in p.SupportedBins if b > 0],
        "formatos": [n for n, v in FORMATOS.items() if v in [int(f) for f in p.SupportedVideoFormat if f >= 0]],
    }


def listar_camaras(ruta_dll=None):
    """Propiedades (dict) de cada cámara ZWO conectada, sin abrirlas."""
    dll = cargar_dll(ruta_dll)
    camaras = []
    for i in range(dll.ASIGetNumOfConnectedCameras()):
        p = ASI_CAMERA_INFO()
        if dll.ASIGetCameraProperty(byref(p), i) == 0:
            camaras.append(_info_a_dict(p))
    return camaras


class CamaraZWO:
    """
    Una cámara ZWO abierta por el SDK, con la interfaz de CamaraNativa:
    abrir/cerrar, atributos/poner/leer, configurar/configurar_area, disparar/hay_frame/leer_frame
    (foto) e iniciar_video/leer_frame/parar_video (vídeo).
    """

    def __init__(self, indice=0, ruta_dll=None):
        self._dll = cargar_dll(ruta_dll)
        self._indice = int(indice)
        self._id = None
        self.propiedades = None
        self._caps = {}
        self._buffer = None
        self._bytes = 0
        self._exposicion_ms = 10.0
        self._video = False
        self._foto = False
        self._lock = threading.Lock()
        self.max_adu = 65535            # RAW16 del SDK, alineado a 16 bits como el del driver ASCOM

    # ── Apertura y cierre ────────────────────────────────────────────────
    def abrir(self):
        dll = self._dll
        n = dll.ASIGetNumOfConnectedCameras()
        if n <= self._indice:
            raise ErrorCamaraZWO(_("No hay ninguna cámara ZWO conectada") if n == 0
                                 else ngettext("Solo hay {n} cámara ZWO; pedida la {pedida}",
                                               "Solo hay {n} cámaras ZWO; pedida la {pedida}",
                                               n, pedida=self._indice + 1))
        p = ASI_CAMERA_INFO()
        self._ok(dll.ASIGetCameraProperty(byref(p), self._indice), _("leer propiedades"))
        self.propiedades = _info_a_dict(p)
        cid = int(p.CameraID)
        self._ok(dll.ASIOpenCamera(cid), _("abrir la cámara (¿la tiene otra aplicación?)"))
        try:
            self._ok(dll.ASIInitCamera(cid), _("inicializar la cámara"))
        except ErrorCamaraZWO:
            dll.ASICloseCamera(cid)
            raise
        self._id = cid
        self._leer_caps()
        usb = _config("usb_zwo")
        if usb and "USBBandWidthLimit" in self._caps:
            try:
                self.poner("USBBandWidthLimit", self._recortar("USBBandWidthLimit", int(float(usb))))
            except (ValueError, ErrorCamaraZWO) as e:
                log.warning("No se pudo fijar el ancho de banda USB %r: %s", usb, e)
        log.info("Cámara ZWO abierta: %s (%dx%d, %s, %.2f µm, USB3=%s)", self.propiedades["modelo"],
                 self.propiedades["ancho"], self.propiedades["alto"], self.propiedades["bayer"],
                 self.propiedades["pixel_um"], self.propiedades["usb3"])
        return self.propiedades

    def cerrar(self):
        if self._id is None:
            return
        try:
            self.abortar()
        except ErrorCamaraZWO:
            pass
        self._dll.ASICloseCamera(self._id)
        log.info("Cámara ZWO cerrada")
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

    # ── Ajustes ──────────────────────────────────────────────────────────
    def _leer_caps(self):
        n = c_int()
        self._ok(self._dll.ASIGetNumOfControls(self._id, byref(n)), _("contar ajustes"))
        self._caps = {}
        for i in range(n.value):
            c = ASI_CONTROL_CAPS()
            if self._dll.ASIGetControlCaps(self._id, i, byref(c)) == 0:
                nombre = CONTROLES.get(int(c.ControlType)) or c.Name.decode(errors="replace")
                self._caps[nombre] = c

    def _recortar(self, nombre, valor):
        c = self._caps[nombre]
        return max(int(c.MinValue), min(int(c.MaxValue), int(valor)))

    def atributos(self):
        """Lista de ajustes que expone la cámara: nombre, índice, tipo, rango y valor por defecto."""
        salida = []
        for nombre, c in self._caps.items():
            escala = 10.0 if nombre == "Temperature" else 1
            salida.append({
                "nombre": nombre, "id": int(c.ControlType), "tipo": "float" if escala != 1 else "int",
                "min": c.MinValue / escala if escala != 1 else int(c.MinValue),
                "max": c.MaxValue / escala if escala != 1 else int(c.MaxValue),
                "defecto": c.DefaultValue / escala if escala != 1 else int(c.DefaultValue),
                "auto": bool(c.IsAutoSupported), "escribible": bool(c.IsWritable),
            })
        return salida

    def poner(self, nombre, valor, auto=False):
        """Fija un ajuste por su nombre ("Gain", "Exposure" en µs, "Offset"...)."""
        if nombre not in self._caps:
            raise ErrorCamaraZWO(_("La cámara no tiene el ajuste {nombre}", nombre=nombre))
        tipo = int(self._caps[nombre].ControlType)
        self._ok(self._dll.ASISetControlValue(self._id, tipo, int(valor), 1 if auto else 0), _("fijar {nombre}", nombre=nombre))

    def leer(self, nombre):
        if nombre not in self._caps:
            raise ErrorCamaraZWO(_("La cámara no tiene el ajuste {nombre}", nombre=nombre))
        v, auto = c_long(), c_int()
        tipo = int(self._caps[nombre].ControlType)
        self._ok(self._dll.ASIGetControlValue(self._id, tipo, byref(v), byref(auto)), _("leer {nombre}", nombre=nombre))
        if nombre == "Temperature":
            return v.value / 10.0
        return int(v.value)

    # ── Zona, formato y exposición ───────────────────────────────────────
    def configurar(self, roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5.0, ganancia=None,
                   offset=None, usb_limite=None, fps_max=None):
        """Zona cuadrada de `roi_px` píxeles del sensor (antes del binning), centrada."""
        binning = max(1, int(binning))
        ancho_total = self.propiedades["ancho"] // binning
        alto_total = self.propiedades["alto"] // binning
        lado = max(8, min(int(roi_px) // binning, ancho_total, alto_total)) & ~7
        x0 = ((ancho_total - lado) // 2) & ~1
        y0 = ((alto_total - lado) // 2) & ~1
        return self.configurar_area(x0, y0, lado, lado, binning=binning, formato=formato, exposicion_ms=exposicion_ms,
                                    ganancia=ganancia, offset=offset, usb_limite=usb_limite, fps_max=fps_max)

    def configurar_area(self, x0, y0, ancho, alto, binning=1, formato="RAW16", exposicion_ms=5.0, ganancia=None,
                        offset=None, usb_limite=None, fps_max=None):
        """
        Zona rectangular en píxeles YA con binning (origen x0, y0; ancho × alto), binning y formato.
        `usb_limite` y `fps_max` se aceptan por compatibilidad con Player One y no se usan.
        """
        dll, cid = self._dll, self._id
        if formato not in FORMATOS:
            raise ValueError(_("Formato desconocido: {formato} (RAW8, RAW16, RGB24 o MONO8)", formato=formato))
        binning = max(1, int(binning))
        if self.propiedades["bins"] and binning not in self.propiedades["bins"]:
            raise ErrorCamaraZWO(_("La cámara no admite binning {binning} (admite {bins})", binning=binning,
                                   bins=self.propiedades["bins"]))
        with self._lock:
            self.abortar()
            ancho, alto = max(8, int(ancho) & ~7), max(2, int(alto) & ~1)
            x0, y0 = int(x0) & ~1, int(y0) & ~1
            self._ok(dll.ASISetROIFormat(cid, ancho, alto, binning, FORMATOS[formato]), _("fijar zona y formato"))
            self._ok(dll.ASISetStartPos(cid, x0, y0), _("fijar origen de la zona"))     # después: SetROIFormat la centra
            self.poner("Exposure", self._recortar("Exposure", round(float(exposicion_ms) * 1000)))
            self._exposicion_ms = float(exposicion_ms)
            if ganancia is not None:
                self.poner("Gain", self._recortar("Gain", ganancia))
            if offset is not None and "Offset" in self._caps:
                self.poner("Offset", self._recortar("Offset", offset))
            if formato == "RAW16":
                self._buffer = np.empty((alto, ancho), dtype=np.uint16)
            elif formato == "RGB24":
                self._buffer = np.empty((alto, ancho, 3), dtype=np.uint8)
            else:
                self._buffer = np.empty((alto, ancho), dtype=np.uint8)
            self._bytes = int(self._buffer.nbytes)
            self.geometria = {"x0": x0 * binning, "y0": y0 * binning, "lado": ancho, "ancho": ancho, "alto": alto,
                              "binning": binning, "formato": formato, "exposicion_ms": self._exposicion_ms}
            log.info("Cámara ZWO: zona %dx%d en (%d,%d) bin %d %s, exp %.2f ms, gain %s",
                     ancho, alto, x0, y0, binning, formato, self._exposicion_ms, ganancia)
            return dict(self.geometria)

    def exposicion(self, exposicion_ms):
        """Cambia la exposición en caliente (µs internos)."""
        self.poner("Exposure", self._recortar("Exposure", round(float(exposicion_ms) * 1000)))
        self._exposicion_ms = float(exposicion_ms)
        if hasattr(self, "geometria"):
            self.geometria["exposicion_ms"] = self._exposicion_ms

    def ganancia(self, ganancia):
        self.poner("Gain", self._recortar("Gain", ganancia))

    # ── Vídeo ─────────────────────────────────────────────────────────────
    def iniciar_video(self):
        if self._buffer is None:
            raise ErrorCamaraZWO(_("Configura la cámara antes de iniciar el vídeo"))
        if self._foto:
            self._dll.ASIStopExposure(self._id)
            self._foto = False
        if not self._video:
            self._ok(self._dll.ASIStartVideoCapture(self._id), _("iniciar el vídeo"))
            self._video = True

    def parar_video(self):
        if self._id is not None and self._video:
            self._dll.ASIStopVideoCapture(self._id)
            self._video = False

    # ── Foto ──────────────────────────────────────────────────────────────
    def disparar(self):
        """Empieza una sola exposición y vuelve enseguida (hay_frame() dice cuándo está; leer_frame() la lee)."""
        if self._buffer is None:
            raise ErrorCamaraZWO(_("Configura la cámara antes de disparar"))
        self.parar_video()
        err = self._dll.ASIStartExposure(self._id, 0)
        if err == ERROR_EXPONIENDO:
            log.info("Cámara ZWO: había una exposición en curso; se corta y se repite el disparo")
            self._dll.ASIStopExposure(self._id)
            time.sleep(0.05)
            err = self._dll.ASIStartExposure(self._id, 0)
        self._ok(err, _("iniciar la exposición"))
        self._foto = True

    def _estado_exposicion(self):
        s = c_int()
        self._ok(self._dll.ASIGetExpStatus(self._id, byref(s)), _("consultar la exposición"))
        if s.value == EXP_FALLO:
            self._foto = False
            raise ErrorCamaraZWO(_("La exposición ha fallado (la cámara la dio por perdida)"))
        return s.value

    def hay_frame(self):
        if self._foto:
            return self._estado_exposicion() == EXP_EXITO
        return self._video

    def abortar(self):
        """Corta la exposición en curso (foto o vídeo)."""
        if self._id is None:
            return
        if self._foto:
            self._dll.ASIStopExposure(self._id)
            self._foto = False
        self.parar_video()

    def foto(self, timeout_ms=None):
        """Una sola exposición (modo foto) y devuelve el fotograma (copia)."""
        self.disparar()
        return self.leer_frame(timeout_ms).copy()

    def leer_frame(self, timeout_ms=None):
        """
        En modo foto espera a que acabe la exposición y la lee; en vídeo espera el siguiente
        fotograma. Devuelve el búfer interno (h×w uint8/uint16 o h×w×3): copiar para conservar.
        """
        if self._buffer is None:
            raise ErrorCamaraZWO(_("Configura la cámara antes de leer fotogramas"))
        if timeout_ms is None:
            timeout_ms = int(self._exposicion_ms * 2 + 500)
        ptr = self._buffer.ctypes.data_as(POINTER(c_ubyte))
        if self._foto:
            limite = time.monotonic() + self._exposicion_ms / 1000.0 + timeout_ms / 1000.0
            while self._estado_exposicion() != EXP_EXITO:
                if time.monotonic() > limite:
                    raise ErrorCamaraZWO(_("leer fotograma: la exposición no terminó a tiempo"))
                time.sleep(0.01)
            self._foto = False
            self._ok(self._dll.ASIGetDataAfterExp(self._id, ptr, self._bytes), _("leer la foto"))
            return self._buffer
        if not self._video:
            raise ErrorCamaraZWO(_("No hay ninguna exposición ni vídeo en marcha"))
        self._ok(self._dll.ASIGetVideoData(self._id, ptr, self._bytes, int(timeout_ms)), _("leer fotograma"))
        return self._buffer

    def descartados(self):
        n = c_int()
        if self._dll.ASIGetDroppedFrames(self._id, byref(n)) != 0:
            return -1
        return int(n.value)

    # ── Errores ───────────────────────────────────────────────────────────
    @staticmethod
    def texto_error(err):
        return _texto_error(err)

    def _ok(self, err, contexto):
        if err != 0:
            raise ErrorCamaraZWO(f"{contexto}: {_texto_error(err)}")


if __name__ == "__main__":
    # Sondeo: versión del SDK y cámaras conectadas; con una, abre y saca unos fotogramas
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("SDK", cargar_dll().ASIGetSDKVersion().decode())
    camaras = listar_camaras()
    print(f"{len(camaras)} cámara(s) ZWO conectada(s)")
    for c in camaras:
        print(c)
    if camaras:
        with CamaraZWO() as cam:
            for a in cam.atributos():
                print(f"  {a['id']:2d} {a['nombre']:24s} {a['min']}..{a['max']} (defecto {a['defecto']})")
            cam.configurar(roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5, ganancia=100)
            cam.iniciar_video()
            t0, n = time.perf_counter(), 0
            while time.perf_counter() - t0 < 2:
                f = cam.leer_frame()
                n += 1
            print(f"{n / (time.perf_counter() - t0):.1f} fps, media {f.mean():.1f}, descartados {cam.descartados()}")
            cam.configurar_area(0, 0, cam.propiedades["ancho"], cam.propiedades["alto"], formato="RAW16",
                                exposicion_ms=100)
            f = cam.foto()
            print(f"foto {f.shape} {f.dtype}, media {f.mean():.0f}, máx {f.max()}")
