# camara_qhy.py
#
# Cámaras QHYCCD por su SDK nativo (qhyccd.dll) mediante ctypes, sin ASCOM,
# con la misma interfaz que CamaraNativa (Player One): camara_sdk.py la envuelve
# como si fuera un driver ASCOM y vivo_dia.py y seguimiento.py la usan para el
# vídeo. Así una QHY hace en la app todo lo que hace la Saturn-C.
#
# Decisiones:
#   - La DLL no se distribuye con la app: se busca donde la dejan N.I.N.A.
#     (External\x64\QHYCCD, junto a ftd2xx.dll y tbb.dll, de las que depende),
#     SharpCap o el driver ASCOM / EZCAP de QHY. Se puede indicar otra en
#     config.ini, [CamaraSDK] dll_qhy. Además hace falta el driver USB de QHY
#     (paquete «All-in-One» de qhyccd.com): sin él ScanQHYCCD no ve ninguna.
#   - Los ajustes se nombran como en Player One ("Gain", "Exposure" en µs,
#     "Offset", "Temperature" en °C) para que el resto de la app no sepa de
#     qué marca es la cámara. El SDK de QHY ya da la temperatura en °C.
#   - El SDK distingue modo foto (stream 0) y modo vídeo (stream 1); cambiar de
#     uno a otro exige volver a llamar a InitQHYCCD, así que se repiten detrás
#     la zona, el binning, los bits y los ajustes puestos.
#   - GetQHYCCDSingleFrame bloquea hasta que acaba la exposición: disparar() la
#     lanza en un hilo y hay_frame() mira si el hilo ha terminado.
#   - Binning antes que la zona (SetQHYCCDBinMode y luego SetQHYCCDResolution
#     en píxeles ya binneados), como hace el driver INDI.
#   - Modo de lectura opcional en [CamaraSDK] modo_qhy (número; 0 por defecto)
#     y tráfico USB en [CamaraSDK] usb_qhy.
#   - Hecho sin una QHY a mano: la API está comprobada contra la DLL real del
#     15-oct-2026 (carga, versión del SDK, recuento de cámaras = 0), pero abrir,
#     exponer, leer fotogramas, el cambio foto↔vídeo, la alineación de los 16
#     bits (max_adu 65535 supuesto) y el patrón Bayer quedan pendientes de
#     probar con una cámara de verdad.

import os
import glob
import time
import ctypes
import logging
import threading
import configparser
from ctypes import c_uint32, c_uint8, c_double, c_char_p, c_void_p, c_bool, POINTER, byref, create_string_buffer

import numpy as np

log = logging.getLogger("scopepower.camara_qhy")

NOMBRE_MARCA = "QHYCCD"

RUTAS_DLL = (
    r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\External\x64\QHYCCD\qhyccd.dll",
    r"C:\Program Files\SharpCap*\qhyccd.dll",
    r"C:\Program Files\SharpCap*\x64\qhyccd.dll",
    r"C:\Program Files\QHYCCD\*\x64\qhyccd.dll",
    r"C:\Program Files\QHYCCD\*\qhyccd.dll",
    r"C:\Program Files (x86)\Common Files\ASCOM\Camera\QHYCCD*\x64\qhyccd.dll",
    r"C:\Program Files\EZCAP_Qt\qhyccd.dll",
    "qhyccd.dll",
)

QHYCCD_SUCCESS = 0
QHYCCD_ERROR = 0xFFFFFFFF
QHYCCD_READ_DIRECTLY = 0x2001

# CONTROL_ID → nombre de la app (el de Player One cuando existe)
CONTROLES = {
    0: "Brightness", 1: "Contrast", 2: "WB_R", 3: "WB_B", 4: "WB_G", 5: "Gamma", 6: "Gain", 7: "Offset",
    8: "Exposure", 9: "Speed", 10: "TransferBit", 12: "USBTraffic", 13: "RowNoiseRe", 14: "Temperature",
    15: "CoolerPowerPerc", 16: "ManualPWM", 18: "TargetTemp",
}
TIPO_CONTROL = {nombre: tipo for tipo, nombre in CONTROLES.items()}
CAM_COLOR = 20
CAM_BIN = {1: 21, 2: 22, 3: 23, 4: 24}           # CAM_BIN1X1MODE … CAM_BIN4X4MODE
CAM_8BITS, CAM_16BITS = 34, 35

# BAYER_ID que devuelve IsQHYCCDControlAvailable(CAM_COLOR)
PATRONES_BAYER = {1: "GBRG", 2: "GRBG", 3: "BGGR", 4: "RGGB"}

FORMATOS = {"RAW8": 8, "MONO8": 8, "RAW16": 16}


class ErrorCamaraQHY(Exception):
    """Fallo del SDK de QHYCCD."""


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
    propia = _config("dll_qhy")
    salida = [propia] if propia else []
    for r in RUTAS_DLL:
        if "*" in r:
            salida.extend(sorted(glob.glob(r), reverse=True))     # la versión más nueva primero
        else:
            salida.append(r)
    return salida


def version_sdk(dll):
    a, m, d, s = c_uint32(), c_uint32(), c_uint32(), c_uint32()
    if dll.GetQHYCCDSDKVersion(byref(a), byref(m), byref(d), byref(s)) != QHYCCD_SUCCESS:
        return "?"
    return f"{a.value}.{m.value:02d}.{d.value:02d}" + (f".{s.value}" if s.value else "")


def cargar_dll(ruta=None):
    """Carga la DLL una sola vez, declara los prototipos e inicia el SDK. Devuelve el handle ctypes."""
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
                    os.add_dll_directory(os.path.dirname(r))     # ftd2xx.dll y tbb.dll están al lado
                dll = ctypes.CDLL(r, winmode=0)
                break
            except (OSError, AttributeError) as e:
                ultimo_error = e
        else:
            raise ErrorCamaraQHY("No se encuentra qhyccd.dll: instala el driver de QHY o N.I.N.A., o indica "
                                 f"la ruta en config.ini, [CamaraSDK] dll_qhy ({ultimo_error or 'ninguna ruta válida'})")
        _declarar_prototipos(dll)
        err = dll.InitQHYCCDResource()
        if err != QHYCCD_SUCCESS:
            raise ErrorCamaraQHY(f"No se pudo iniciar el SDK de QHY ({_texto_error(err)})")
        _dll = dll
        log.info("SDK QHYCCD %s cargado de %s", version_sdk(dll), r)
        return dll


def _declarar_prototipos(dll):
    h = c_void_p
    p32 = POINTER(c_uint32)
    firmas = {
        "InitQHYCCDResource": [], "ScanQHYCCD": [],
        "GetQHYCCDSDKVersion": [p32, p32, p32, p32],
        "GetQHYCCDId": [c_uint32, c_char_p], "GetQHYCCDModel": [c_char_p, c_char_p],
        "CloseQHYCCD": [h], "InitQHYCCD": [h],
        "SetQHYCCDStreamMode": [h, c_uint8], "SetQHYCCDReadMode": [h, c_uint32],
        "IsQHYCCDControlAvailable": [h, c_uint32],
        "GetQHYCCDParamMinMaxStep": [h, c_uint32, POINTER(c_double), POINTER(c_double), POINTER(c_double)],
        "SetQHYCCDParam": [h, c_uint32, c_double],
        "GetQHYCCDChipInfo": [h, POINTER(c_double), POINTER(c_double), p32, p32,
                              POINTER(c_double), POINTER(c_double), p32],
        "GetQHYCCDSensorName": [h, c_char_p],
        "SetQHYCCDDebayerOnOff": [h, c_bool],
        "SetQHYCCDBinMode": [h, c_uint32, c_uint32], "SetQHYCCDBitsMode": [h, c_uint32],
        "SetQHYCCDResolution": [h, c_uint32, c_uint32, c_uint32, c_uint32],
        "GetQHYCCDMemLength": [h],
        "ExpQHYCCDSingleFrame": [h],
        "GetQHYCCDSingleFrame": [h, p32, p32, p32, p32, POINTER(c_uint8)],
        "CancelQHYCCDExposingAndReadout": [h],
        "BeginQHYCCDLive": [h], "StopQHYCCDLive": [h],
        "GetQHYCCDLiveFrame": [h, p32, p32, p32, p32, POINTER(c_uint8)],
    }
    for nombre, args in firmas.items():
        f = getattr(dll, nombre)
        f.argtypes = args
        f.restype = c_uint32
    dll.OpenQHYCCD.argtypes = [c_char_p]
    dll.OpenQHYCCD.restype = c_void_p
    dll.GetQHYCCDParam.argtypes = [h, c_uint32]
    dll.GetQHYCCDParam.restype = c_double


def _texto_error(err):
    err = int(err)
    if err == QHYCCD_ERROR:
        return "error del SDK (QHYCCD_ERROR)"
    return f"código {err:#x}"


def _ids(dll):
    """Identificadores de las cámaras conectadas (ScanQHYCCD vuelve a mirar el USB)."""
    salida = []
    for i in range(int(dll.ScanQHYCCD())):
        buf = create_string_buffer(256)
        if dll.GetQHYCCDId(i, buf) == QHYCCD_SUCCESS:
            salida.append(buf.value.decode(errors="replace"))
    return salida


def _modelo(dll, cid):
    buf = create_string_buffer(256)
    if dll.GetQHYCCDModel(cid.encode(), buf) == QHYCCD_SUCCESS and buf.value:
        return buf.value.decode(errors="replace").strip()
    return cid.split("-")[0]


def listar_camaras(ruta_dll=None):
    """Propiedades (dict) de cada cámara QHY conectada, sin abrirlas (solo modelo e identificador)."""
    dll = cargar_dll(ruta_dll)
    return [{"modelo": _modelo(dll, cid), "marca": "QHY", "id": cid,
             "serie": cid.split("-", 1)[-1] if "-" in cid else ""} for cid in _ids(dll)]


class CamaraQHY:
    """
    Una cámara QHY abierta por el SDK, con la interfaz de CamaraNativa:
    abrir/cerrar, atributos/poner/leer, configurar/configurar_area, disparar/hay_frame/leer_frame
    (foto) e iniciar_video/leer_frame/parar_video (vídeo).
    """

    def __init__(self, indice=0, ruta_dll=None):
        self._dll = cargar_dll(ruta_dll)
        self._indice = int(indice)
        self._h = None
        self.propiedades = None
        self._caps = {}
        self._valores = {}             # ajustes puestos, para repetirlos tras InitQHYCCD
        self._zona = None              # (x0, y0, ancho, alto, binning, bits) ya binneada
        self._stream = 0
        self._buffer = None
        self._frame = None
        self._exposicion_ms = 10.0
        self._video = False
        self._hilo = None
        self._error_hilo = None
        self._lock = threading.Lock()
        self.max_adu = 65535            # 16 bits alineados arriba (pendiente de comprobar con una cámara)

    # ── Apertura y cierre ────────────────────────────────────────────────
    def abrir(self):
        dll = self._dll
        ids = _ids(dll)
        if len(ids) <= self._indice:
            raise ErrorCamaraQHY("No hay ninguna cámara QHY conectada" if not ids
                                 else f"Solo hay {len(ids)} cámara(s) QHY; pedida la {self._indice + 1}")
        cid = ids[self._indice]
        h = dll.OpenQHYCCD(cid.encode())
        if not h:
            raise ErrorCamaraQHY("abrir la cámara: el SDK no la abre (¿la tiene otra aplicación?)")
        try:
            modo = _config("modo_qhy")
            if modo:
                self._ok(dll.SetQHYCCDReadMode(h, int(float(modo))), f"fijar el modo de lectura {modo}")
            self._ok(dll.SetQHYCCDStreamMode(h, 0), "poner el modo foto")
            self._ok(dll.InitQHYCCD(h), "inicializar la cámara")
        except (ErrorCamaraQHY, ValueError):
            dll.CloseQHYCCD(h)
            raise
        self._h = h
        self._stream = 0
        self.propiedades = self._leer_propiedades(cid)
        self._leer_caps()
        if self.propiedades["color"]:
            dll.SetQHYCCDDebayerOnOff(h, False)          # queremos el mosaico Bayer crudo
        usb = _config("usb_qhy")
        if usb and "USBTraffic" in self._caps:
            try:
                self.poner("USBTraffic", self._recortar("USBTraffic", float(usb)))
            except (ValueError, ErrorCamaraQHY) as e:
                log.warning("No se pudo fijar el tráfico USB %r: %s", usb, e)
        log.info("Cámara QHY abierta: %s %s (%dx%d, %s, %.2f µm, %d bits)", self.propiedades["modelo"],
                 self.propiedades["sensor"], self.propiedades["ancho"], self.propiedades["alto"],
                 self.propiedades["bayer"], self.propiedades["pixel_um"], self.propiedades["bits"])
        return self.propiedades

    def _leer_propiedades(self, cid):
        dll, h = self._dll, self._h
        cw, ch, pw, ph = c_double(), c_double(), c_double(), c_double()
        iw, ih, bpp = c_uint32(), c_uint32(), c_uint32()
        self._ok(dll.GetQHYCCDChipInfo(h, byref(cw), byref(ch), byref(iw), byref(ih), byref(pw), byref(ph),
                                       byref(bpp)), "leer el tamaño del sensor")
        sensor = create_string_buffer(256)
        dll.GetQHYCCDSensorName(h, sensor)
        bayer = int(dll.IsQHYCCDControlAvailable(h, CAM_COLOR))
        color = bayer in PATRONES_BAYER
        bins = [b for b, ctl in CAM_BIN.items() if dll.IsQHYCCDControlAvailable(h, ctl) == QHYCCD_SUCCESS] or [1]
        formatos = [f for f, ctl in (("RAW8", CAM_8BITS), ("RAW16", CAM_16BITS))
                    if dll.IsQHYCCDControlAvailable(h, ctl) == QHYCCD_SUCCESS] or ["RAW16"]
        return {
            "modelo": _modelo(dll, cid), "id": cid, "serie": cid.split("-", 1)[-1] if "-" in cid else "",
            "sensor": sensor.value.decode(errors="replace").strip(), "ancho": int(iw.value), "alto": int(ih.value),
            "bits": int(bpp.value), "color": color, "usb3": False,
            "bayer": PATRONES_BAYER[bayer] if color else "MONO", "pixel_um": float(pw.value),
            "bins": bins, "formatos": formatos,
        }

    def cerrar(self):
        if self._h is None:
            return
        try:
            self.abortar()
        except ErrorCamaraQHY:
            pass
        self._dll.CloseQHYCCD(self._h)
        log.info("Cámara QHY cerrada")
        self._h = None

    def __enter__(self):
        self.abrir()
        return self

    def __exit__(self, *exc):
        self.cerrar()
        return False

    @property
    def abierta(self):
        return self._h is not None

    # ── Ajustes ──────────────────────────────────────────────────────────
    def _leer_caps(self):
        self._caps = {}
        for tipo, nombre in CONTROLES.items():
            if self._dll.IsQHYCCDControlAvailable(self._h, tipo) != QHYCCD_SUCCESS:
                continue
            mn, mx, paso = c_double(), c_double(), c_double()
            if self._dll.GetQHYCCDParamMinMaxStep(self._h, tipo, byref(mn), byref(mx), byref(paso)) == QHYCCD_SUCCESS:
                self._caps[nombre] = {"id": tipo, "min": mn.value, "max": mx.value, "paso": paso.value}
            elif nombre == "Temperature":
                self._caps[nombre] = {"id": tipo, "min": -50.0, "max": 50.0, "paso": 0.1}

    def _recortar(self, nombre, valor):
        c = self._caps[nombre]
        return max(c["min"], min(c["max"], float(valor)))

    def atributos(self):
        """Lista de ajustes que expone la cámara: nombre, índice, tipo, rango y valor por defecto."""
        salida = []
        for nombre, c in self._caps.items():
            entero = nombre != "Temperature" and float(c["paso"]).is_integer()
            conv = int if entero else float
            try:
                defecto = self.leer(nombre)
            except ErrorCamaraQHY:
                defecto = c["min"]
            salida.append({"nombre": nombre, "id": c["id"], "tipo": "int" if entero else "float",
                           "min": conv(c["min"]), "max": conv(c["max"]), "defecto": conv(defecto),
                           "auto": False, "escribible": nombre != "Temperature"})
        return salida

    def poner(self, nombre, valor, auto=False):
        """Fija un ajuste por su nombre ("Gain", "Exposure" en µs, "Offset"...). `auto` no existe en QHY."""
        if nombre not in self._caps:
            raise ErrorCamaraQHY(f"La cámara no tiene el ajuste {nombre}")
        self._ok(self._dll.SetQHYCCDParam(self._h, self._caps[nombre]["id"], float(valor)), f"fijar {nombre}")
        self._valores[nombre] = float(valor)

    def leer(self, nombre):
        if nombre not in self._caps:
            raise ErrorCamaraQHY(f"La cámara no tiene el ajuste {nombre}")
        v = float(self._dll.GetQHYCCDParam(self._h, self._caps[nombre]["id"]))
        if v == float(QHYCCD_ERROR):
            raise ErrorCamaraQHY(f"leer {nombre}: {_texto_error(QHYCCD_ERROR)}")
        return v if nombre == "Temperature" else int(round(v))

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
        if formato not in FORMATOS:
            raise ValueError(f"Formato desconocido: {formato} (RAW8, RAW16 o MONO8)")
        binning = max(1, int(binning))
        if binning not in self.propiedades["bins"]:
            raise ErrorCamaraQHY(f"La cámara no admite binning {binning} (admite {self.propiedades['bins']})")
        with self._lock:
            self.abortar()
            ancho, alto = max(8, int(ancho) & ~1), max(2, int(alto) & ~1)
            x0, y0 = int(x0) & ~1, int(y0) & ~1
            self._zona = (x0, y0, ancho, alto, binning, FORMATOS[formato])
            self._aplicar_zona()
            self.poner("Exposure", self._recortar("Exposure", round(float(exposicion_ms) * 1000)))
            self._exposicion_ms = float(exposicion_ms)
            if ganancia is not None:
                self.poner("Gain", self._recortar("Gain", ganancia))
            if offset is not None and "Offset" in self._caps:
                self.poner("Offset", self._recortar("Offset", offset))
            self.geometria = {"x0": x0 * binning, "y0": y0 * binning, "lado": ancho, "ancho": ancho, "alto": alto,
                              "binning": binning, "formato": formato, "exposicion_ms": self._exposicion_ms}
            log.info("Cámara QHY: zona %dx%d en (%d,%d) bin %d %s, exp %.2f ms, gain %s",
                     ancho, alto, x0, y0, binning, formato, self._exposicion_ms, ganancia)
            return dict(self.geometria)

    def _aplicar_zona(self):
        dll, h = self._dll, self._h
        x0, y0, ancho, alto, b, bits = self._zona
        self._ok(dll.SetQHYCCDBinMode(h, b, b), f"fijar binning {b}")
        self._ok(dll.SetQHYCCDResolution(h, x0, y0, ancho, alto), "fijar la zona")
        self._ok(dll.SetQHYCCDBitsMode(h, bits), f"fijar {bits} bits")
        largo = int(dll.GetQHYCCDMemLength(h))
        if largo in (0, QHYCCD_ERROR):
            largo = self.propiedades["ancho"] * self.propiedades["alto"] * 2
        if self._buffer is None or self._buffer.size < largo:
            self._buffer = np.empty(largo, dtype=np.uint8)

    def _cambiar_stream(self, stream):
        """Foto (0) ↔ vídeo (1): InitQHYCCD de nuevo y se repite toda la configuración."""
        if stream == self._stream:
            return
        dll, h = self._dll, self._h
        self._ok(dll.SetQHYCCDStreamMode(h, stream), "cambiar entre foto y vídeo")
        self._ok(dll.InitQHYCCD(h), "reinicializar la cámara")
        self._stream = stream
        if self.propiedades["color"]:
            dll.SetQHYCCDDebayerOnOff(h, False)
        for nombre, valor in self._valores.items():
            self._dll.SetQHYCCDParam(h, self._caps[nombre]["id"], valor)
        if self._zona is not None:
            self._aplicar_zona()

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
        if self._zona is None:
            raise ErrorCamaraQHY("Configura la cámara antes de iniciar el vídeo")
        if self._hilo is not None:
            self.abortar()
        if not self._video:
            self._cambiar_stream(1)
            self._ok(self._dll.BeginQHYCCDLive(self._h), "iniciar el vídeo")
            self._video = True

    def parar_video(self):
        if self._h is not None and self._video:
            self._dll.StopQHYCCDLive(self._h)
            self._video = False

    # ── Foto ──────────────────────────────────────────────────────────────
    def _exponer(self):
        """Hilo de la foto: exposición + lectura (GetQHYCCDSingleFrame bloquea hasta el final)."""
        try:
            err = self._dll.ExpQHYCCDSingleFrame(self._h)
            if err not in (QHYCCD_SUCCESS, QHYCCD_READ_DIRECTLY):
                raise ErrorCamaraQHY(f"iniciar la exposición: {_texto_error(err)}")
            self._frame = self._leer_buffer(self._dll.GetQHYCCDSingleFrame, "leer la foto")
        except Exception as e:
            self._error_hilo = e

    def _leer_buffer(self, funcion, contexto):
        w, h, bpp, canales = c_uint32(), c_uint32(), c_uint32(), c_uint32()
        ptr = self._buffer.ctypes.data_as(POINTER(c_uint8))
        err = funcion(self._h, byref(w), byref(h), byref(bpp), byref(canales), ptr)
        if err != QHYCCD_SUCCESS:
            return err
        tipo = np.uint16 if bpp.value > 8 else np.uint8
        n = int(w.value) * int(h.value) * max(1, int(canales.value))
        datos = self._buffer[:n * np.dtype(tipo).itemsize].view(tipo)
        if canales.value > 1:
            return datos.reshape(int(h.value), int(w.value), int(canales.value))
        return datos.reshape(int(h.value), int(w.value))

    def disparar(self):
        """Empieza una sola exposición y vuelve enseguida (hay_frame() dice cuándo está; leer_frame() la lee)."""
        if self._zona is None:
            raise ErrorCamaraQHY("Configura la cámara antes de disparar")
        self.parar_video()
        if self._hilo is not None:
            log.info("Cámara QHY: había una exposición en curso; se corta y se repite el disparo")
            self.abortar()
        self._cambiar_stream(0)
        self._frame = None
        self._error_hilo = None
        self._hilo = threading.Thread(target=self._exponer, name="qhy-foto", daemon=True)
        self._hilo.start()

    def hay_frame(self):
        if self._hilo is not None:
            return not self._hilo.is_alive()
        return self._video

    def abortar(self):
        """Corta la exposición en curso (foto o vídeo)."""
        if self._h is None:
            return
        if self._hilo is not None:
            self._dll.CancelQHYCCDExposingAndReadout(self._h)
            self._hilo.join(timeout=5)
            self._hilo = None
        self.parar_video()

    def foto(self, timeout_ms=None):
        """Una sola exposición (modo foto) y devuelve el fotograma (copia)."""
        self.disparar()
        return self.leer_frame(timeout_ms).copy()

    def leer_frame(self, timeout_ms=None):
        """
        En modo foto espera a que acabe la exposición y la lee; en vídeo espera el siguiente
        fotograma. Devuelve una vista del búfer interno (h×w uint8/uint16): copiar para conservar.
        """
        if self._zona is None:
            raise ErrorCamaraQHY("Configura la cámara antes de leer fotogramas")
        if timeout_ms is None:
            timeout_ms = int(self._exposicion_ms * 2 + 500)
        if self._hilo is not None:
            self._hilo.join(timeout=self._exposicion_ms / 1000.0 + timeout_ms / 1000.0)
            if self._hilo.is_alive():
                raise ErrorCamaraQHY("leer fotograma: la exposición no terminó a tiempo")
            self._hilo = None
            if self._error_hilo is not None:
                raise ErrorCamaraQHY(str(self._error_hilo))
            if not isinstance(self._frame, np.ndarray):
                raise ErrorCamaraQHY(f"leer la foto: {_texto_error(self._frame)}")
            return self._frame
        if not self._video:
            raise ErrorCamaraQHY("No hay ninguna exposición ni vídeo en marcha")
        limite = time.monotonic() + timeout_ms / 1000.0
        while True:
            frame = self._leer_buffer(self._dll.GetQHYCCDLiveFrame, "leer fotograma")
            if isinstance(frame, np.ndarray):
                return frame
            if time.monotonic() > limite:
                raise ErrorCamaraQHY("leer fotograma: no llega ningún fotograma del vídeo")
            time.sleep(0.002)

    def descartados(self):
        return -1                     # el SDK de QHY no lo cuenta

    # ── Errores ───────────────────────────────────────────────────────────
    @staticmethod
    def texto_error(err):
        return _texto_error(err)

    def _ok(self, err, contexto):
        if err != QHYCCD_SUCCESS:
            raise ErrorCamaraQHY(f"{contexto}: {_texto_error(err)}")


if __name__ == "__main__":
    # Sondeo: versión del SDK y cámaras conectadas; con una, abre y saca unos fotogramas
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("SDK", version_sdk(cargar_dll()))
    camaras = listar_camaras()
    print(f"{len(camaras)} cámara(s) QHY conectada(s)")
    for c in camaras:
        print(c)
    if camaras:
        with CamaraQHY() as cam:
            print(cam.propiedades)
            for a in cam.atributos():
                print(f"  {a['id']:2d} {a['nombre']:24s} {a['min']}..{a['max']} (ahora {a['defecto']})")
            cam.configurar(roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5, ganancia=10)
            cam.iniciar_video()
            t0, n = time.perf_counter(), 0
            while time.perf_counter() - t0 < 2:
                f = cam.leer_frame()
                n += 1
            print(f"{n / (time.perf_counter() - t0):.1f} fps, media {f.mean():.1f}")
            cam.configurar_area(0, 0, cam.propiedades["ancho"], cam.propiedades["alto"], formato="RAW16",
                                exposicion_ms=100)
            f = cam.foto()
            print(f"foto {f.shape} {f.dtype}, media {f.mean():.0f}, máx {f.max()}")
