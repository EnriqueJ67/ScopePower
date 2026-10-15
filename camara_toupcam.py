# camara_toupcam.py
#
# Cámaras de la familia ToupTek por su SDK nativo (toupcam.dll) mediante
# ctypes, sin ASCOM, con la misma interfaz que CamaraNativa (Player One):
# camara_sdk.py la envuelve como si fuera un driver ASCOM y vivo_dia.py y
# seguimiento.py la usan para el vídeo.
#
# La familia: ToupTek fabrica para otras marcas y les da la misma DLL
# renombrada, con otro prefijo en las funciones (Altaircam_, Ogmacam_,
# Nncam_ de Risingcam, Svbonycam_ de las SVBony que no usan SVBCameraSDK,
# Omegonprocam_, Mallincam_). Cada DLL solo ve las cámaras de su marca, así que
# listar_camaras() junta las de todas las que encuentre (VARIANTES) en una sola
# lista; el índice del driver SDK:ToupTek:<n> es la posición en esa lista.
# Verificado el 15-oct-2026 que las siete DLL de N.I.N.A. exportan todas las
# funciones que usa este módulo.
#
# Decisiones:
#   - Las DLL no se distribuyen con la app: se buscan donde las deja N.I.N.A.
#     (External\x64\<marca>), SharpCap o el driver ASCOM de cada marca. Se puede
#     indicar otra en config.ini, [CamaraSDK] dll_touptek (cualquier DLL de la
#     familia: el prefijo se averigua solo) o dll_<variante> (dll_altair,
#     dll_ogma, dll_risingcam, dll_svbony, dll_omegon, dll_mallincam).
#   - Modo «pull» con aviso por callback, en RAW (TOUPCAM_OPTION_RAW) y con la
#     profundidad máxima del sensor para RAW16. El SDK entrega los datos
#     alineados abajo (0..2^bits − 1); se desplazan a 16 bits como los de
#     Player One y ZWO, porque flats, enfoque, vivo y darks cuentan con 0..65535.
#   - Foto = disparo por software (TOUPCAM_OPTION_TRIGGER = 1, Trigger(1));
#     vídeo = modo continuo (TRIGGER = 0).
#   - En Windows el SDK entrega la imagen boca abajo por defecto (herencia de
#     los DIB); se pone TOUPCAM_OPTION_UPSIDE_DOWN = 0 para tener el orden del
#     sensor, como en Linux y en los SDK de las otras marcas.
#   - Ajustes con los nombres de Player One: "Gain" es la ganancia analógica
#     del SDK en % (100 = ×1), "Exposure" en µs, "Offset" el nivel de negro
#     (TOUPCAM_OPTION_BLACKLEVEL) y "Temperature" en °C (el SDK da décimas).
#   - Binning digital por promedio (TOUPCAM_OPTION_BINNING = 0x80 | n) y zona
#     con put_Roi en píxeles del sensor sin binning; el tamaño real del
#     fotograma se pide a get_FinalSize.
#   - Hecho sin una cámara de la familia a mano: la API está comprobada contra
#     las DLL reales (carga, versión, recuento de cámaras = 0), pero abrir,
#     exponer, leer fotogramas, la zona con binning, el cambio foto↔vídeo en
#     marcha, la orientación y el patrón Bayer quedan pendientes de probar con
#     una cámara de verdad.

import os
import glob
import time
import ctypes
import logging
import threading
import configparser
from ctypes import (c_int, c_uint, c_ushort, c_short, c_float, c_ulonglong, c_wchar, c_wchar_p, c_void_p,
                    c_char_p, POINTER, byref, Structure, create_string_buffer)

import numpy as np

log = logging.getLogger("scopepower.camara_toupcam")

NOMBRE_MARCA = "ToupTek"

NINA = r"C:\Program Files\N.I.N.A. - Nighttime Imaging 'N' Astronomy\External\x64"

# (nombre visible, DLL, prefijo de las funciones, carpeta de N.I.N.A., clave de config.ini).
# Las de otras marcas van antes que la de ToupTek: si una cámara la vieran dos DLL, gana su marca.
VARIANTES = (
    ("Altair", "altaircam.dll", "Altaircam", "Altair", "altair"),
    ("Ogma", "ogmacam.dll", "Ogmacam", "OGMA", "ogma"),
    ("Risingcam", "nncam.dll", "Nncam", "Risingcam", "risingcam"),
    ("SVBony", "svbonycam.dll", "Svbonycam", "SVBony", "svbony"),
    ("Omegon", "omegonprocam.dll", "Omegonprocam", "Omegon", "omegon"),
    ("MallinCam", "mallincam.dll", "Mallincam", "MallinCam", "mallincam"),
    ("ToupTek", "toupcam.dll", "Toupcam", "ToupTek", "touptek"),
)
PREFIJOS = {v[2]: v[0] for v in VARIANTES}

# Otras rutas donde suelen estar (además de la carpeta de N.I.N.A.)
OTRAS_RUTAS = (
    r"C:\Program Files\SharpCap*\{dll}",
    r"C:\Program Files\SharpCap*\x64\{dll}",
    r"C:\Program Files (x86)\Common Files\ASCOM\Camera\*\x64\{dll}",
    r"C:\Program Files (x86)\{marca}*\ASCOM\x64\{dll}",
    r"C:\Program Files\{marca}*\x64\{dll}",
)

MAX_CAMARAS = 128                     # TOUPCAM_MAX

# TOUPCAM_FLAG_*
FLAG_MONO, FLAG_USB30, FLAG_TEC = 0x10, 0x40, 0x80

# TOUPCAM_OPTION_*
OPT_RAW, OPT_BITDEPTH, OPT_TRIGGER, OPT_BLACKLEVEL, OPT_BINNING, OPT_UPSIDE_DOWN = 0x04, 0x06, 0x0b, 0x15, 0x17, 0x23

# TOUPCAM_EVENT_*
EV_IMAGE, EV_ERROR, EV_DESCONECTADA, EV_SIN_FRAMES = 0x0004, 0x0080, 0x0081, 0x0082

E_PENDING = 0x8000000A
ERRORES = {
    0x8000FFFF: "llamada inesperada", 0x80004001: "no lo admite esta cámara", 0x80070005: "acceso denegado",
    0x8007000E: "sin memoria", 0x80070057: "argumento no válido", 0x80004003: "puntero no válido",
    0x80004005: "error general", 0x8001010E: "hilo equivocado", 0x8007001F: "el dispositivo no responde",
    0x800700AA: "la cámara está ocupada", E_PENDING: "no hay fotograma todavía", 0x8001011F: "tiempo agotado",
}

FORMATOS = {"RAW8": 8, "MONO8": 8, "RAW16": 16}

CALLBACK = ctypes.WINFUNCTYPE(None, c_uint, c_void_p)


class ErrorCamaraToupcam(Exception):
    """Fallo del SDK de ToupTek (o de una marca de su familia)."""


class _Resolucion(Structure):
    _fields_ = [("width", c_uint), ("height", c_uint)]


class _ModeloV2(Structure):
    _fields_ = [("name", c_wchar_p), ("flag", c_ulonglong), ("maxspeed", c_uint), ("preview", c_uint),
                ("still", c_uint), ("maxfanspeed", c_uint), ("ioctrol", c_uint), ("xpixsz", c_float),
                ("ypixsz", c_float), ("res", _Resolucion * 16)]


class _DispositivoV2(Structure):
    _fields_ = [("displayname", c_wchar * 64), ("id", c_wchar * 64), ("model", POINTER(_ModeloV2))]


class _InfoFrameV2(Structure):
    _fields_ = [("width", c_uint), ("height", c_uint), ("flag", c_uint), ("seq", c_uint),
                ("timestamp", c_ulonglong)]


class Sdk:
    """Una DLL de la familia ya cargada: sdk.f("Open") es la función <Prefijo>_Open."""

    def __init__(self, dll, prefijo, marca, ruta):
        self.dll, self.prefijo, self.marca, self.ruta = dll, prefijo, marca, ruta
        self._declarar()

    def f(self, nombre):
        return getattr(self.dll, f"{self.prefijo}_{nombre}")

    def tiene(self, nombre):
        return hasattr(self.dll, f"{self.prefijo}_{nombre}")

    def version(self):
        return self.f("Version")() or "?"

    def _declarar(self):
        h = c_void_p
        pu, pi = POINTER(c_uint), POINTER(c_int)
        pus = POINTER(c_ushort)
        firmas = {
            "EnumV2": (c_uint, [POINTER(_DispositivoV2)]),
            "Open": (c_void_p, [c_wchar_p]),
            "Close": (None, [h]),
            "Version": (c_wchar_p, []),
            "StartPullModeWithCallback": (c_int, [h, CALLBACK, c_void_p]),
            "PullImageV2": (c_int, [h, c_void_p, c_int, POINTER(_InfoFrameV2)]),
            "Stop": (c_int, [h]),
            "put_Option": (c_int, [h, c_uint, c_int]), "get_Option": (c_int, [h, c_uint, pi]),
            "Trigger": (c_int, [h, c_ushort]),
            "put_ExpoTime": (c_int, [h, c_uint]), "get_ExpoTime": (c_int, [h, pu]),
            "get_ExpTimeRange": (c_int, [h, pu, pu, pu]),
            "put_AutoExpoEnable": (c_int, [h, c_int]),
            "put_ExpoAGain": (c_int, [h, c_ushort]), "get_ExpoAGain": (c_int, [h, pus]),
            "get_ExpoAGainRange": (c_int, [h, pus, pus, pus]),
            "get_Temperature": (c_int, [h, POINTER(c_short)]),
            "put_Roi": (c_int, [h, c_uint, c_uint, c_uint, c_uint]),
            "put_eSize": (c_int, [h, c_uint]),
            "get_Size": (c_int, [h, pi, pi]),
            "get_FinalSize": (c_int, [h, pi, pi]),
            "get_RawFormat": (c_int, [h, pu, pu]),
            "get_MaxBitDepth": (c_uint, [h]),
            "get_MaxSpeed": (c_int, [h]),
            "put_Speed": (c_int, [h, c_ushort]),
            "get_SerialNumber": (c_int, [h, c_char_p]),
            "get_FrameRate": (c_int, [h, pu, pu, pu]),
        }
        for nombre, (res, args) in firmas.items():
            if self.tiene(nombre):
                fn = self.f(nombre)
                fn.restype = res
                fn.argtypes = args


_sdks = {}                 # marca → Sdk (o None si no se encontró)
_dll_lock = threading.Lock()


def _config(clave, defecto=""):
    cfg = configparser.ConfigParser()
    try:
        cfg.read("config.ini", encoding="utf-8")
    except configparser.Error:
        pass
    return cfg.get("CamaraSDK", clave, fallback=defecto).strip()


def _cargar(ruta):
    if os.path.dirname(ruta):
        if not os.path.exists(ruta):
            return None
        os.add_dll_directory(os.path.dirname(ruta))
    return ctypes.CDLL(ruta, winmode=0)


def _prefijo_de(dll):
    return next((p for p in PREFIJOS if hasattr(dll, f"{p}_EnumV2")), None)


def cargar_sdks():
    """Carga (una vez) cada DLL de la familia que se encuentre. Devuelve la lista de Sdk."""
    with _dll_lock:
        if _sdks:
            return [s for s in _sdks.values() if s is not None]
        # Ruta propia genérica: cualquier DLL de la familia, el prefijo dice la marca
        propia = _config("dll_touptek")
        if propia:
            try:
                dll = _cargar(propia)
                prefijo = dll and _prefijo_de(dll)
                if prefijo:
                    _sdks[PREFIJOS[prefijo]] = Sdk(dll, prefijo, PREFIJOS[prefijo], propia)
                else:
                    log.warning("dll_touptek = %s no es una DLL de la familia ToupTek", propia)
            except OSError as e:
                log.warning("No se pudo cargar dll_touptek = %s: %s", propia, e)
        for marca, nombre_dll, prefijo, carpeta, clave in VARIANTES:
            if marca in _sdks:
                continue
            candidatas = [_config(f"dll_{clave}"), os.path.join(NINA, carpeta, nombre_dll)]
            for patron in OTRAS_RUTAS:
                candidatas.extend(sorted(glob.glob(patron.format(dll=nombre_dll, marca=marca)), reverse=True))
            candidatas.append(nombre_dll)
            _sdks[marca] = None
            for r in candidatas:
                if not r:
                    continue
                try:
                    dll = _cargar(r)
                except OSError:
                    continue                        # p. ej. la de 32 bits de PHD2
                if dll is None or not hasattr(dll, f"{prefijo}_EnumV2"):
                    continue
                _sdks[marca] = Sdk(dll, prefijo, marca, r)
                log.info("SDK %s %s cargado de %s", marca, _sdks[marca].version(), r)
                break
        cargados = [s for s in _sdks.values() if s is not None]
        if not cargados:
            _sdks.clear()                           # que se vuelva a buscar la próxima vez
            raise ErrorCamaraToupcam("No se encuentra toupcam.dll ni ninguna DLL de su familia: instala el driver "
                                     "de la marca o N.I.N.A., o indica la ruta en config.ini, [CamaraSDK] dll_touptek")
        return cargados


def _enumerar(sdk):
    arr = (_DispositivoV2 * MAX_CAMARAS)()
    n = int(sdk.f("EnumV2")(arr))
    return [arr[i] for i in range(min(n, MAX_CAMARAS))]


def _info_a_dict(sdk, d):
    m = d.model.contents if d.model else None
    flag = int(m.flag) if m else 0
    modelo = (m.name if m and m.name else d.displayname) or sdk.marca
    return {
        "modelo": modelo.strip(), "marca": sdk.marca, "id": d.id, "nombre_usb": d.displayname,
        "ancho": int(m.res[0].width) if m else 0, "alto": int(m.res[0].height) if m else 0,
        "color": not (flag & FLAG_MONO), "usb3": bool(flag & FLAG_USB30), "refrigerada": bool(flag & FLAG_TEC),
        "pixel_um": float(m.xpixsz) if m else 0.0, "_sdk": sdk,
    }


def _camaras():
    vistos, salida = set(), []
    for sdk in cargar_sdks():
        for d in _enumerar(sdk):
            if d.id in vistos:
                continue
            vistos.add(d.id)
            salida.append(_info_a_dict(sdk, d))
    return salida


def listar_camaras():
    """Propiedades (dict) de cada cámara de la familia ToupTek conectada, sin abrirlas."""
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in _camaras()]


def _texto_error(err):
    err = int(err) & 0xFFFFFFFF
    return f"{ERRORES.get(err, 'error desconocido')} ({err:#010x})"


class CamaraToupcam:
    """
    Una cámara de la familia ToupTek abierta por su SDK, con la interfaz de CamaraNativa:
    abrir/cerrar, atributos/poner/leer, configurar/configurar_area, disparar/hay_frame/leer_frame
    (foto) e iniciar_video/leer_frame/parar_video (vídeo).
    """

    def __init__(self, indice=0):
        cargar_sdks()
        self._indice = int(indice)
        self._sdk = None
        self._h = None
        self.propiedades = None
        self._caps = {}
        self._buffer = None
        self._bits = 16
        self._desplazar = 0
        self._exposicion_ms = 10.0
        self._video = False
        self._foto = False
        self._en_marcha = False
        self._evento = threading.Event()
        self._error_evento = None
        self._callback = CALLBACK(self._al_evento)      # hay que guardarlo: el SDK lo llama desde su hilo
        self._lock = threading.Lock()
        self.max_adu = 65535

    def f(self, nombre):
        return self._sdk.f(nombre)

    # ── Apertura y cierre ────────────────────────────────────────────────
    def abrir(self):
        camaras = _camaras()
        if len(camaras) <= self._indice:
            raise ErrorCamaraToupcam("No hay ninguna cámara ToupTek (ni Altair, Ogma, Risingcam…) conectada"
                                     if not camaras else
                                     f"Solo hay {len(camaras)} cámara(s) ToupTek; pedida la {self._indice + 1}")
        info = camaras[self._indice]
        self._sdk = info["_sdk"]
        h = self.f("Open")(info["id"])
        if not h:
            raise ErrorCamaraToupcam("abrir la cámara: el SDK no la abre (¿la tiene otra aplicación?)")
        self._h = h
        try:
            self._preparar(info)
        except Exception:
            self.f("Close")(h)
            self._h = None
            raise
        log.info("Cámara %s abierta: %s (%dx%d, %s, %.2f µm, %d bits, USB3=%s)", self._sdk.marca,
                 self.propiedades["modelo"], self.propiedades["ancho"], self.propiedades["alto"],
                 self.propiedades["bayer"], self.propiedades["pixel_um"], self.propiedades["bits"],
                 self.propiedades["usb3"])
        return self.propiedades

    def _preparar(self, info):
        f, h = self.f, self._h
        self._ok(f("put_eSize")(h, 0), "elegir la resolución completa")
        self._ok(f("put_Option")(h, OPT_RAW, 1), "pedir la imagen RAW")
        f("put_Option")(h, OPT_UPSIDE_DOWN, 0)          # orden del sensor (en Windows viene volteada)
        f("put_AutoExpoEnable")(h, 0)
        ancho, alto = c_int(), c_int()
        self._ok(f("get_Size")(h, byref(ancho), byref(alto)), "leer el tamaño del sensor")
        bits = int(f("get_MaxBitDepth")(h)) or 8
        fourcc, bpp = c_uint(), c_uint()
        patron = "MONO"
        if info["color"] and f("get_RawFormat")(h, byref(fourcc), byref(bpp)) >= 0:
            patron = fourcc.value.to_bytes(4, "little").decode("ascii", errors="replace").upper()
            if patron not in ("RGGB", "BGGR", "GRBG", "GBRG"):
                log.warning("Patrón Bayer desconocido %r; se supone RGGB", patron)
                patron = "RGGB"
        serie = create_string_buffer(64)
        if self._sdk.tiene("get_SerialNumber"):
            f("get_SerialNumber")(h, serie)
        self.propiedades = {
            "modelo": info["modelo"], "marca": self._sdk.marca, "id": info["id"],
            "serie": serie.value.decode(errors="replace").strip(), "sensor": "",
            "ancho": int(ancho.value), "alto": int(alto.value), "bits": bits, "color": info["color"],
            "usb3": info["usb3"], "bayer": patron, "pixel_um": info["pixel_um"],
            "bins": [1, 2, 3, 4], "formatos": ["RAW8", "RAW16"] if bits > 8 else ["RAW8"],
        }
        self._leer_caps()
        usb = _config("usb_touptek")
        if usb and "Speed" in self._caps:
            try:
                self._ok(f("put_Speed")(h, int(self._recortar("Speed", float(usb)))), "fijar la velocidad USB")
            except (ValueError, ErrorCamaraToupcam) as e:
                log.warning("No se pudo fijar la velocidad USB %r: %s", usb, e)

    def cerrar(self):
        if self._h is None:
            return
        try:
            self.abortar()
        except ErrorCamaraToupcam:
            pass
        if self._en_marcha:
            self.f("Stop")(self._h)
            self._en_marcha = False
        self.f("Close")(self._h)
        log.info("Cámara %s cerrada", self._sdk.marca)
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

    # ── Avisos del SDK (desde su hilo) ───────────────────────────────────
    def _al_evento(self, evento, _ctx):
        if evento == EV_IMAGE:
            self._evento.set()
        elif evento in (EV_ERROR, EV_DESCONECTADA, EV_SIN_FRAMES):
            self._error_evento = {EV_ERROR: "error de la cámara", EV_DESCONECTADA: "la cámara se ha desconectado",
                                  EV_SIN_FRAMES: "la cámara no manda fotogramas"}[evento]
            self._evento.set()

    # ── Ajustes ──────────────────────────────────────────────────────────
    def _leer_caps(self):
        f, h = self.f, self._h
        self._caps = {}
        mn, mx, df = c_ushort(), c_ushort(), c_ushort()
        if f("get_ExpoAGainRange")(h, byref(mn), byref(mx), byref(df)) >= 0:
            self._caps["Gain"] = {"id": 0, "min": mn.value, "max": mx.value, "defecto": df.value}
        emn, emx, edf = c_uint(), c_uint(), c_uint()
        if f("get_ExpTimeRange")(h, byref(emn), byref(emx), byref(edf)) >= 0:
            self._caps["Exposure"] = {"id": 1, "min": emn.value, "max": emx.value, "defecto": edf.value}
        # TOUPCAM_BLACKLEVELn_MAX = 31 · 2^(n-8)
        self._caps["Offset"] = {"id": OPT_BLACKLEVEL, "min": 0, "max": 31 * (1 << max(0, self.propiedades["bits"] - 8)),
                                "defecto": 0}
        t = c_short()
        if f("get_Temperature")(h, byref(t)) >= 0:
            self._caps["Temperature"] = {"id": 2, "min": -50.0, "max": 50.0, "defecto": t.value / 10.0}
        if self._sdk.tiene("get_MaxSpeed"):
            vmax = int(f("get_MaxSpeed")(h))
            if vmax > 0:
                self._caps["Speed"] = {"id": 3, "min": 0, "max": vmax, "defecto": vmax}

    def _recortar(self, nombre, valor):
        c = self._caps[nombre]
        return max(c["min"], min(c["max"], valor))

    def atributos(self):
        """Lista de ajustes que expone la cámara: nombre, índice, tipo, rango y valor por defecto."""
        return [{"nombre": n, "id": c["id"], "tipo": "float" if n == "Temperature" else "int",
                 "min": c["min"], "max": c["max"], "defecto": c["defecto"], "auto": False,
                 "escribible": n != "Temperature"} for n, c in self._caps.items()]

    def poner(self, nombre, valor, auto=False):
        """Fija un ajuste por su nombre ("Gain" en %, "Exposure" en µs, "Offset"...)."""
        f, h = self.f, self._h
        if nombre not in self._caps:
            raise ErrorCamaraToupcam(f"La cámara no tiene el ajuste {nombre}")
        valor = int(round(float(valor)))
        if nombre == "Gain":
            err = f("put_ExpoAGain")(h, valor)
        elif nombre == "Exposure":
            err = f("put_ExpoTime")(h, valor)
        elif nombre == "Offset":
            err = f("put_Option")(h, OPT_BLACKLEVEL, valor)
        elif nombre == "Speed":
            err = f("put_Speed")(h, valor)
        else:
            raise ErrorCamaraToupcam(f"El ajuste {nombre} es de solo lectura")
        self._ok(err, f"fijar {nombre}")

    def leer(self, nombre):
        f, h = self.f, self._h
        if nombre not in self._caps:
            raise ErrorCamaraToupcam(f"La cámara no tiene el ajuste {nombre}")
        if nombre == "Gain":
            v = c_ushort()
            self._ok(f("get_ExpoAGain")(h, byref(v)), "leer Gain")
        elif nombre == "Exposure":
            v = c_uint()
            self._ok(f("get_ExpoTime")(h, byref(v)), "leer Exposure")
        elif nombre == "Temperature":
            v = c_short()
            self._ok(f("get_Temperature")(h, byref(v)), "leer Temperature")
            return v.value / 10.0
        elif nombre == "Offset":
            v = c_int()
            self._ok(f("get_Option")(h, OPT_BLACKLEVEL, byref(v)), "leer Offset")
        else:
            return self._caps[nombre]["defecto"]
        return int(v.value)

    # ── Zona, formato y exposición ───────────────────────────────────────
    def configurar(self, roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5.0, ganancia=None,
                   offset=None, usb_limite=None, fps_max=None):
        """Zona cuadrada de `roi_px` píxeles del sensor (antes del binning), centrada."""
        binning = max(1, int(binning))
        ancho_total = self.propiedades["ancho"] // binning
        alto_total = self.propiedades["alto"] // binning
        lado = max(16, min(int(roi_px) // binning, ancho_total, alto_total)) & ~7
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
        f, h = self.f, self._h
        if formato not in FORMATOS:
            raise ValueError(f"Formato desconocido: {formato} (RAW8, RAW16 o MONO8)")
        binning = max(1, int(binning))
        if binning not in self.propiedades["bins"]:
            raise ErrorCamaraToupcam(f"La cámara no admite binning {binning} (admite {self.propiedades['bins']})")
        with self._lock:
            self.abortar()
            if self._en_marcha:                      # RAW, bits y zona se cambian con la cámara parada
                f("Stop")(h)
                self._en_marcha = False
            ancho, alto = max(16, int(ancho) & ~1), max(16, int(alto) & ~1)
            x0, y0 = int(x0) & ~1, int(y0) & ~1
            self._bits = 16 if FORMATOS[formato] == 16 and self.propiedades["bits"] > 8 else 8
            self._ok(f("put_Option")(h, OPT_BITDEPTH, 1 if self._bits == 16 else 0), f"fijar {self._bits} bits")
            # 16 bits: los datos se alinean arriba en _sacar() (desplazamiento de 16 − bits del sensor)
            self._desplazar = max(0, 16 - int(self.propiedades["bits"])) if self._bits == 16 else 0
            self.max_adu = 65535 if self._bits == 16 else 255
            self._ok(f("put_Option")(h, OPT_BINNING, 1 if binning == 1 else 0x80 | binning), f"fijar binning {binning}")
            completo = (x0, y0) == (0, 0) and ancho * binning >= self.propiedades["ancho"] - 1 \
                and alto * binning >= self.propiedades["alto"] - 1
            if completo:
                self._ok(f("put_Roi")(h, 0, 0, 0, 0), "quitar la zona")
            else:
                self._ok(f("put_Roi")(h, x0 * binning, y0 * binning, ancho * binning, alto * binning), "fijar la zona")
            self._ok(f("put_Option")(h, OPT_TRIGGER, 1), "poner el disparo por software")
            self._ok(f("StartPullModeWithCallback")(h, self._callback, None), "arrancar la cámara")
            self._en_marcha = True
            fw, fh = c_int(), c_int()
            if f("get_FinalSize")(h, byref(fw), byref(fh)) >= 0 and fw.value > 0:
                ancho, alto = int(fw.value), int(fh.value)
            self._buffer = np.empty((alto, ancho), dtype=np.uint16 if self._bits == 16 else np.uint8)
            self.poner("Exposure", self._recortar("Exposure", round(float(exposicion_ms) * 1000)))
            self._exposicion_ms = float(exposicion_ms)
            if ganancia is not None:
                self.poner("Gain", self._recortar("Gain", ganancia))
            if offset is not None:
                self.poner("Offset", self._recortar("Offset", offset))
            self.geometria = {"x0": x0 * binning, "y0": y0 * binning, "lado": ancho, "ancho": ancho, "alto": alto,
                              "binning": binning, "formato": formato, "exposicion_ms": self._exposicion_ms}
            log.info("Cámara %s: zona %dx%d en (%d,%d) bin %d %s, exp %.2f ms, gain %s", self._sdk.marca,
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
            raise ErrorCamaraToupcam("Configura la cámara antes de iniciar el vídeo")
        if self._foto:
            self.f("Trigger")(self._h, 0)
            self._foto = False
        if not self._video:
            self._evento.clear()
            self._ok(self.f("put_Option")(self._h, OPT_TRIGGER, 0), "iniciar el vídeo")
            self._video = True

    def parar_video(self):
        if self._h is not None and self._video:
            self.f("put_Option")(self._h, OPT_TRIGGER, 1)
            self._video = False

    # ── Foto ──────────────────────────────────────────────────────────────
    def disparar(self):
        """Empieza una sola exposición y vuelve enseguida (hay_frame() dice cuándo está; leer_frame() la lee)."""
        if self._buffer is None:
            raise ErrorCamaraToupcam("Configura la cámara antes de disparar")
        self.parar_video()
        if self._foto:
            log.info("Cámara %s: había una exposición en curso; se corta y se repite el disparo", self._sdk.marca)
            self.f("Trigger")(self._h, 0)
        self._evento.clear()
        self._error_evento = None
        self._ok(self.f("Trigger")(self._h, 1), "iniciar la exposición")
        self._foto = True

    def hay_frame(self):
        if self._error_evento:
            raise ErrorCamaraToupcam(self._error_evento)
        if self._foto:
            return self._evento.is_set()
        return self._video

    def abortar(self):
        """Corta la exposición en curso (foto o vídeo)."""
        if self._h is None:
            return
        if self._foto:
            self.f("Trigger")(self._h, 0)
            self._foto = False
        self.parar_video()

    def foto(self, timeout_ms=None):
        """Una sola exposición (modo foto) y devuelve el fotograma (copia)."""
        self.disparar()
        return self.leer_frame(timeout_ms).copy()

    def _sacar(self):
        info = _InfoFrameV2()
        err = self.f("PullImageV2")(self._h, self._buffer.ctypes.data_as(c_void_p), self._bits, byref(info))
        if err < 0:
            return err
        if (info.height, info.width) != self._buffer.shape:
            log.warning("Fotograma de %dx%d en un búfer de %dx%d", info.width, info.height,
                        self._buffer.shape[1], self._buffer.shape[0])
        if self._desplazar:
            np.left_shift(self._buffer, self._desplazar, out=self._buffer)
        return 0

    def leer_frame(self, timeout_ms=None):
        """
        En modo foto espera a que acabe la exposición y la lee; en vídeo espera el siguiente
        fotograma. Devuelve el búfer interno (h×w uint8/uint16): copiar para conservar.
        """
        if self._buffer is None:
            raise ErrorCamaraToupcam("Configura la cámara antes de leer fotogramas")
        if not (self._foto or self._video):
            raise ErrorCamaraToupcam("No hay ninguna exposición ni vídeo en marcha")
        if timeout_ms is None:
            timeout_ms = int(self._exposicion_ms * 2 + 500)
        espera = (self._exposicion_ms if self._foto else 0) / 1000.0 + timeout_ms / 1000.0
        limite = time.monotonic() + espera
        while True:
            restante = limite - time.monotonic()
            if restante <= 0 or not self._evento.wait(restante):
                raise ErrorCamaraToupcam("leer fotograma: la exposición no terminó a tiempo" if self._foto
                                         else "leer fotograma: no llega ningún fotograma del vídeo")
            self._evento.clear()
            if self._error_evento:
                raise ErrorCamaraToupcam(self._error_evento)
            err = self._sacar()
            if err == 0:
                self._foto = False
                return self._buffer
            if (err & 0xFFFFFFFF) != E_PENDING:
                self._ok(err, "leer fotograma")

    def descartados(self):
        return -1                     # el SDK no cuenta los perdidos en modo pull

    # ── Errores ───────────────────────────────────────────────────────────
    @staticmethod
    def texto_error(err):
        return _texto_error(err)

    def _ok(self, err, contexto):
        if err is not None and int(err) < 0:
            raise ErrorCamaraToupcam(f"{contexto}: {_texto_error(err)}")


if __name__ == "__main__":
    # Sondeo: versión de cada SDK de la familia y cámaras conectadas; con una, abre y saca unos fotogramas
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in cargar_sdks():
        print(f"SDK {s.marca:10s} {s.version():12s} {s.ruta}")
    camaras = listar_camaras()
    print(f"{len(camaras)} cámara(s) de la familia ToupTek conectada(s)")
    for c in camaras:
        print(c)
    if camaras:
        with CamaraToupcam() as cam:
            print(cam.propiedades)
            for a in cam.atributos():
                print(f"  {a['nombre']:12s} {a['min']}..{a['max']} (defecto {a['defecto']})")
            cam.configurar(roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5, ganancia=200)
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
