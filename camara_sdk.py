# camara_sdk.py
#
# Cámaras por su SDK nativo, sin ASCOM, con la misma cara que un driver ASCOM,
# como en N.I.N.A.: en Configuración se elige el "driver" SDK:<marca> en vez del
# ASCOM y TODO lo de la app (fotos de noche y de día, darks, flats, apilado,
# centrado, alineación, continuo, timelapse, modo en vivo, seguimiento) pasa
# por el SDK sin cambiar nada más. Si el SDK diera problemas, basta volver a
# elegir el driver ASCOM.
#
# Marcas (MARCAS): Player One (camara_nativa.py), ZWO ASI (camara_zwo.py),
# QHYCCD (camara_qhy.py), la familia ToupTek con Altair, Ogma, Risingcam,
# SVBony, Omegon y MallinCam (camara_toupcam.py) y webcams UVC
# (camara_webcam.py). Cada módulo da listar_camaras() y una clase
# con la interfaz de CamaraNativa; para añadir otra marca basta escribir su
# módulo y darla de alta en MARCAS. El driver es "SDK:<marca>" para la primera
# cámara de esa marca y "SDK:<marca>:<n>" para la n-ésima (desde 0), y el
# desplegable de Configuración enseña las que hay conectadas (detectar_camaras).
#
# Patrón Bayer: la app trabaja en RGGB. Si el sensor es BGGR, GRBG o GBRG, la
# imagen se desplaza un píxel en la fila o la columna para que empiece en rojo
# (se pierde solo el borde).
#
# Solo imita lo que usa la app de ASCOM ICamera (inventario del 28-sep-2026):
# Connected, CameraXSize/YSize, PixelSizeX/Y, SensorType, MaxADU, BinX/BinY,
# StartX/StartY/NumX/NumY, Gain/GainMin/GainMax, Offset, ExposureMin/Max,
# CCDTemperature, StartExposure, ImageReady, ImageArray, AbortExposure,
# StopExposure, CanAbortExposure, CanStopExposure y Name.
#
# Detalles medidos con la Saturn-C real el 28-sep-2026:
#   - La imagen del SDK es la TRASPUESTA de la de ASCOM (correlación 0,999 entre
#     las 8 simetrías; orientacion_camara.leer_sdk). ImageArray la devuelve ya
#     girada: los FITS, el plate solving y la orientación guardada no cambian.
#   - Mismo patrón Bayer (RGGB), mismos niveles y MaxADU 65535 en RAW16.
#   - Foto completa: 3,0 s por ASCOM y 1,2 s por el SDK (con 0,46 s de
#     exposición, USB 2). En el PC de campo va por USB 3.
#   - Offset: el SDK arranca con 35 y el driver ASCOM guarda el suyo (50 en este
#     PC, 35 en el de campo según la cabecera de sus darks). Para que los darks
#     casen se fija el de config.ini (offset_camara) o, si no hay, el OFFSET del
#     dark más reciente.

import os
import glob
import time
import logging
import configparser

import numpy as np

import orientacion_camara

log = logging.getLogger("scopepower.camara_sdk")

PREFIJO = "SDK:"
DRIVER_SDK = "SDK:PlayerOne"
NOMBRE_DRIVER = "por SDK nativo (sin ASCOM)"
SENSOR_TYPE = {"MONO": 0, "RGGB": 2, "BGGR": 2, "GRBG": 2, "GBRG": 2}
PATRONES = ("RGGB", "BGGR", "GRBG", "GBRG")
# Desplazamiento (filas, columnas) que lleva cada patrón a RGGB, en la orientación del SDK
A_RGGB = {"RGGB": (0, 0), "GRBG": (0, -1), "GBRG": (-1, 0), "BGGR": (-1, -1)}

# Clave del driver → (nombre visible, módulo, clase). El orden es el del desplegable.
MARCAS = {
    "PlayerOne": ("Player One", "camara_nativa", "CamaraNativa"),
    "ZWO": ("ZWO ASI", "camara_zwo", "CamaraZWO"),
    "QHY": ("QHYCCD", "camara_qhy", "CamaraQHY"),
    "ToupTek": ("ToupTek (Altair, Ogma, Risingcam, SVBony…)", "camara_toupcam", "CamaraToupcam"),
    "Webcam": ("Webcam", "camara_webcam", "CamaraWebcam"),
}


def es_sdk(driver):
    return str(driver or "").strip().upper().startswith(PREFIJO)


def leer_driver(driver):
    """'SDK:ZWO:1' → ('ZWO', 1). Los drivers ASCOM devuelven la marca que se les supone, con índice 0."""
    texto = str(driver or "").strip()
    if es_sdk(texto):
        partes = texto[len(PREFIJO):].split(":")
        marca = next((m for m in MARCAS if m.upper() == partes[0].strip().upper()), None)
        if marca is None:
            raise ValueError(f"Marca de cámara desconocida en el driver «{texto}» "
                             f"(conocidas: {', '.join(MARCAS)})")
        try:
            indice = max(0, int(partes[1])) if len(partes) > 1 and partes[1].strip() else 0
        except ValueError:
            raise ValueError(f"Número de cámara no válido en el driver «{texto}»")
        return marca, indice
    # Driver ASCOM: el modo en vivo y el seguimiento abren la cámara por el SDK de su marca
    alto = texto.upper()
    if "QHY" in alto:
        return "QHY", 0
    if any(m in alto for m in ("TOUP", "ALTAIR", "OGMA", "NNCAM", "RISING", "OMEGON", "MALLIN")):
        return "ToupTek", 0
    if "ASICAMERA" in alto or "ZWO" in alto:
        return "ZWO", 0
    return "PlayerOne", 0


def driver_de(marca, indice=0):
    return f"{PREFIJO}{marca}" + (f":{indice}" if indice else "")


def _modulo(marca):
    import importlib
    return importlib.import_module(MARCAS[marca][1])


def detectar_camaras(no_tocar=()):
    """
    Entradas {progid, name} del desplegable: una por cámara conectada de cada marca y, si el
    SDK de una marca está instalado pero no hay ninguna enchufada, una genérica. Las marcas de
    `no_tocar` (la cámara está abierta por la app) no se vuelven a sondear: se usa lo último visto.
    """
    entradas = []
    for marca, (nombre, _, _) in MARCAS.items():
        if marca in no_tocar and marca in _ultimas:
            camaras = _ultimas[marca]
        else:
            try:
                camaras = _modulo(marca).listar_camaras()
            except Exception as e:
                log.info("Cámaras %s por SDK: no disponibles (%s)", nombre, e)
                _ultimas.pop(marca, None)
                continue
            _ultimas[marca] = camaras
        if not camaras:
            if marca != "Webcam":
                entradas.append({"progid": driver_de(marca), "name": f"{nombre} {NOMBRE_DRIVER} — ninguna conectada ahora"})
            continue
        for i, c in enumerate(camaras):
            modelo = str(c.get("modelo") or nombre)
            prefijo = str(c.get("marca") or nombre.split()[0])        # la familia ToupTek dice su marca
            if marca != "Webcam" and not modelo.upper().startswith(prefijo.upper()):
                modelo = f"{prefijo} {modelo}"
            entradas.append({"progid": driver_de(marca, i),
                             "name": f"{modelo} (webcam, sin ASCOM)" if marca == "Webcam" else f"{modelo} {NOMBRE_DRIVER}"})
    return entradas


_ultimas = {}


def _config(ruta="config.ini"):
    cfg = configparser.ConfigParser()
    try:
        cfg.read(ruta, encoding="utf-8")
    except configparser.Error:
        pass
    return cfg


def offset_deseado(ruta_config="config.ini"):
    """Offset que hay que fijar: el de config.ini, el del dark más reciente o None (el del SDK)."""
    cfg = _config(ruta_config)
    valor = cfg.get("Dispositivos", "offset_camara", fallback="").strip()
    if valor:
        try:
            return int(float(valor)), "config.ini"
        except ValueError:
            log.warning("offset_camara no es un número: %r", valor)
    darks = sorted(glob.glob(os.path.join("calibraciones", "dark_*.fits")), key=os.path.getmtime)
    if darks:
        try:
            from astropy.io import fits
            off = fits.getheader(darks[-1]).get("OFFSET")
            if off is not None:
                return int(float(off)), os.path.basename(darks[-1])
        except Exception as e:
            log.warning("No se pudo leer el OFFSET de %s: %s", darks[-1], e)
    return None, ""


def nativa_por_defecto(driver=None):
    """
    La cámara del driver (por defecto el de config.ini) abierta por el SDK de su marca, o la
    simulada si config.ini tiene [CamaraSDK] simulador_fits (pruebas).
    """
    cfg = _config()
    ruta = cfg.get("CamaraSDK", "simulador_fits", fallback="").strip()
    if ruta:
        cam = CamaraNativaSimulada(ruta)
    else:
        if driver is None:
            driver = cfg.get("Dispositivos", "driver_camara", fallback=DRIVER_SDK)
        marca, indice = leer_driver(driver)
        cam = getattr(_modulo(marca), MARCAS[marca][2])(indice)
    cam.abrir()
    return cam


class CamaraSDK:
    """Objeto con la interfaz de ASCOM ICamera que usa la app, sobre la cámara nativa de cualquier marca."""

    def __init__(self, fabrica_nativa=None, driver=None):
        self._fabrica = fabrica_nativa or (lambda: nativa_por_defecto(driver))
        self._cam = None
        self._orientacion = "traspuesta"
        self.BinX = self.BinY = 1
        self.StartX = self.StartY = 0
        self.NumX = self.NumY = 0
        self._area = None
        self._limites = {}

    # ── conexión ─────────────────────────────────────────────────────────
    @property
    def Connected(self):
        return self._cam is not None

    @Connected.setter
    def Connected(self, valor):
        if valor and self._cam is None:
            cam = self._fabrica()
            try:
                self._preparar(cam)
            except Exception:
                cam.cerrar()
                raise
            self._cam = cam
        elif not valor and self._cam is not None:
            try:
                self._cam.cerrar()
            finally:
                self._cam = None
                self._area = None

    def _preparar(self, cam):
        p = cam.propiedades
        self._orientacion = orientacion_camara.leer_sdk()["transformacion"]
        self._limites = {a["nombre"]: a for a in cam.atributos()}
        # Como un driver recién conectado: campo completo, binning 1
        self.CameraXSize_ = int(p["ancho"])
        self.CameraYSize_ = int(p["alto"])
        self.BinX = self.BinY = 1
        self.StartX = self.StartY = 0
        self.NumX, self.NumY = self.CameraXSize_, self.CameraYSize_
        self._area = None
        off, origen = offset_deseado()
        if off is not None and "Offset" in self._limites:
            cam.poner("Offset", off)
            log.info("Cámara por SDK: offset %d (%s)", off, origen)
        patron = str(p.get("bayer", "MONO")).upper()
        if patron in PATRONES and self._orientacion not in ("identidad", "traspuesta", "rot180", "traspuesta_rot180"):
            log.warning("La orientación SDK→ASCOM %s cambia el patrón Bayer %s: los colores pueden salir cambiados",
                        self._orientacion, patron)
        self._patron = patron
        if A_RGGB.get(patron, (0, 0)) != (0, 0):
            log.info("Cámara por SDK: patrón %s, se desplaza la imagen un píxel para entregarla en RGGB", patron)
        log.info("Cámara por SDK conectada: %s %s, %dx%d, %s, píxel %.2f µm, imagen SDK→ASCOM %s",
                 p.get("modelo", ""), p.get("sensor", ""), self.CameraXSize_, self.CameraYSize_, patron,
                 float(p.get("pixel_um") or 0), self._orientacion)

    def _necesita(self):
        if self._cam is None:
            raise RuntimeError("La cámara por SDK no está conectada")
        return self._cam

    # ── propiedades fijas ────────────────────────────────────────────────
    @property
    def Name(self):
        return f"{(self._cam.propiedades.get('modelo') if self._cam else 'Cámara')} (SDK)"

    @property
    def CameraXSize(self):
        self._necesita()
        return self.CameraXSize_

    @property
    def CameraYSize(self):
        self._necesita()
        return self.CameraYSize_

    @property
    def PixelSizeX(self):
        return float(self._necesita().propiedades.get("pixel_um") or 3.76)

    PixelSizeY = PixelSizeX

    @property
    def SensorType(self):
        return SENSOR_TYPE.get(str(self._necesita().propiedades.get("bayer", "MONO")).upper(), 0)

    @property
    def BayerOffsetX(self):
        return 0

    BayerOffsetY = BayerOffsetX
    @property
    def MaxADU(self):
        # RAW16 del SDK, alineado a 16 bits como el del driver ASCOM
        return int(getattr(self._cam, "max_adu", 65535) or 65535)

    CanAbortExposure = True
    CanStopExposure = True

    # ── ganancia, offset, exposición, temperatura ────────────────────────
    @property
    def Gain(self):
        return int(self._necesita().leer("Gain"))

    @Gain.setter
    def Gain(self, valor):
        self._necesita().poner("Gain", int(valor))

    @property
    def GainMin(self):
        return int(self._limites.get("Gain", {}).get("min", 0))

    @property
    def GainMax(self):
        return int(self._limites.get("Gain", {}).get("max", 0))

    @property
    def Offset(self):
        return int(self._necesita().leer("Offset"))

    @Offset.setter
    def Offset(self, valor):
        self._necesita().poner("Offset", int(valor))

    @property
    def ExposureMin(self):
        return float(self._limites.get("Exposure", {}).get("min", 10)) / 1e6

    @property
    def ExposureMax(self):
        return float(self._limites.get("Exposure", {}).get("max", 2e9)) / 1e6

    @property
    def CCDTemperature(self):
        return float(self._necesita().leer("Temperature"))

    # ── exposición ───────────────────────────────────────────────────────
    def _zona_sdk(self):
        """(x0, y0, ancho, alto, binning) del SDK para la zona pedida en coordenadas de ASCOM."""
        b = max(1, int(self.BinX))
        x0, y0, nx, ny = int(self.StartX), int(self.StartY), int(self.NumX), int(self.NumY)
        if self._orientacion in ("identidad", "traspuesta"):
            # ASCOM llama x/y a los mismos ejes del sensor que el SDK; solo cambia cómo se entrega el array
            return x0, y0, nx, ny, b
        # Las otras simetrías no se han visto nunca: se expone todo y ImageArray recorta
        return 0, 0, self.CameraXSize_ // b, self.CameraYSize_ // b, b

    def StartExposure(self, duracion, luz=True):
        cam = self._necesita()
        zona = self._zona_sdk()
        exposicion_ms = max(float(duracion), self.ExposureMin) * 1000.0
        if zona != self._area:
            x0, y0, ancho, alto, b = zona
            cam.configurar_area(x0, y0, ancho, alto, binning=b, formato="RAW16", exposicion_ms=exposicion_ms)
            self._area = zona
        else:
            cam.exposicion(exposicion_ms)
        cam.disparar()

    @property
    def ImageReady(self):
        return bool(self._necesita().hay_frame())

    @property
    def ImageArray(self):
        """El fotograma en la orientación de ASCOM (np.array(ImageArray) como con el driver ASCOM)."""
        buf = np.array(self._necesita().leer_frame(timeout_ms=5000), copy=True)
        desplazamiento = A_RGGB.get(getattr(self, "_patron", "MONO"), (0, 0))
        if desplazamiento != (0, 0) and int(self.BinX) == 1 and buf.ndim == 2:
            buf = np.roll(buf, desplazamiento, axis=(0, 1))       # que la imagen empiece por el rojo
        img = np.ascontiguousarray(orientacion_camara.TRANSFORMACIONES[self._orientacion](buf))
        if self._orientacion not in ("identidad", "traspuesta"):
            b = max(1, int(self.BinX))
            img = img[int(self.StartX):int(self.StartX) + int(self.NumX), int(self.StartY):int(self.StartY) + int(self.NumY)]
        return img

    def AbortExposure(self):
        if self._cam is not None:
            self._cam.abortar()

    StopExposure = AbortExposure


def crear(driver=DRIVER_SDK):
    """Fábrica para hardware.TrabajadorCOM (drivers SDK:<marca>[:<n>])."""
    leer_driver(driver)          # marca desconocida: error claro antes de intentar conectar
    return CamaraSDK(driver=driver)


# ─────────────────────────────────────────────────────────────────────────────
#  Cámara nativa simulada (pruebas sin la cámara)
# ─────────────────────────────────────────────────────────────────────────────
class CamaraNativaSimulada:
    """
    Imita CamaraNativa con un FITS crudo de ASCOM (p. ej. una foto de día): lo pasa a la
    orientación del SDK y escala el brillo con la exposición. Sirve a CamaraSDK (fotos) y a
    vivo_dia (vídeo). Se activa con [CamaraSDK] simulador_fits = ruta en config.ini.
    """
    def __init__(self, ruta_fits):
        from astropy.io import fits
        with fits.open(ruta_fits) as h:
            datos = np.asarray(h[0].data, dtype=np.float32)
            self._exp_ref = float(h[0].header.get("EXPTIME") or 0.001)
            patron = str(h[0].header.get("BAYERPAT") or "RGGB").strip().upper()
        nombre = orientacion_camara.leer_sdk()["transformacion"]
        prueba = np.arange(16).reshape(4, 4)
        inversa = next(n for n, op in orientacion_camara.TRANSFORMACIONES.items()
                       if np.array_equal(orientacion_camara.TRANSFORMACIONES[nombre](op(prueba)), prueba))
        self._cfa = orientacion_camara.TRANSFORMACIONES[inversa](datos)
        self.propiedades = {"modelo": "Simulada (FITS)", "sensor": "FITS", "ancho": self._cfa.shape[1],
                            "alto": self._cfa.shape[0], "bayer": patron if patron in PATRONES else "MONO",
                            "pixel_um": 3.76}
        self._valores = {"Gain": 0, "Offset": 35, "Temperature": 25.0}
        self._zona = self._cfa
        self._exp_s = self._exp_ref
        self._periodo = 0.2
        self._rng = np.random.default_rng(0)
        self._listo_en = None
        self.exposiciones = []
        self.abierta = False

    def abrir(self):
        self.abierta = True
        return self.propiedades

    def cerrar(self):
        self.abierta = False

    def atributos(self):
        return [{"nombre": "Gain", "min": 0, "max": 600}, {"nombre": "Offset", "min": 0, "max": 1500},
                {"nombre": "Exposure", "min": 10, "max": 2000000000}]

    def poner(self, nombre, valor, auto=False):
        self._valores[nombre] = valor

    def leer(self, nombre):
        return self._valores.get(nombre, 0)

    def configurar_area(self, x0, y0, ancho, alto, binning=1, formato="RAW16", exposicion_ms=5.0, fps_max=None, **_):
        self._zona = self._cfa[y0:y0 + alto, x0:x0 + ancho]
        self._exp_s = exposicion_ms / 1000.0
        self._periodo = 1.0 / (fps_max or 5)
        return {"x0": x0, "y0": y0, "lado": ancho, "ancho": ancho, "alto": alto, "binning": 1, "formato": formato}

    def configurar(self, roi_px=1024, binning=1, formato="RAW16", exposicion_ms=5.0, ganancia=None, fps_max=None, **_):
        lado = max(8, min(int(roi_px), self._cfa.shape[0], self._cfa.shape[1])) & ~1
        y0 = ((self._cfa.shape[0] - lado) // 2) & ~1
        x0 = ((self._cfa.shape[1] - lado) // 2) & ~1
        return self.configurar_area(x0, y0, lado, lado, exposicion_ms=exposicion_ms, fps_max=fps_max)

    def exposicion(self, exposicion_ms):
        self._exp_s = exposicion_ms / 1000.0
        self.exposiciones.append(self._exp_s)

    def iniciar_video(self):
        pass

    def disparar(self):
        self._listo_en = time.time() + self._exp_s + 0.05

    def hay_frame(self):
        return self._listo_en is not None and time.time() >= self._listo_en

    def abortar(self):
        self._listo_en = None

    def _fotograma(self):
        senal = (self._zona - 144.0) * (self._exp_s / self._exp_ref)
        ruido = self._rng.normal(0, 25, self._zona.shape).astype(np.float32)
        return np.clip(senal + 144.0 + ruido, 0, 65535).astype(np.uint16)

    def leer_frame(self, timeout_ms=None):
        if self._listo_en is not None:            # modo foto
            espera = self._listo_en - time.time()
            if espera > 0:
                time.sleep(espera)
            self._listo_en = None
        else:                                     # modo vídeo
            time.sleep(max(self._periodo, self._exp_s))
        return self._fotograma()
