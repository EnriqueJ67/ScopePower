# apilado_video.py
#
# Apilado de los mejores fotogramas de un vídeo SER («lucky imaging»): el Sol, la Luna
# o un planeta grabados desde el modo en vivo (vivo_dia.py escribe el SER con
# grabador_video.py). Lo que hacen AutoStakkert o PIPP, aquí dentro de la app para que
# todo se haga desde la tablet.
#
#   1. Puntuar: a cada fotograma se le mide la nitidez en el canal verde a media
#      resolución (varianza del laplaciano tras un suavizado ligero, relativa al brillo
#      para que no dependa de la exposición). Los que se salen mucho del brillo mediano
#      (una nube, un bache de la exposición) se descartan.
#   2. Elegir: se quedan los mejores tantos por ciento (de serie el 25 %, nunca menos de
#      10 fotogramas).
#   3. Alinear: cada elegido se desmosaica a color a resolución completa y se desplaza
#      por correlación de fase (subpíxel) sobre el verde hasta el mejor fotograma, que
#      hace de referencia. Vale para el disco entero, para un trozo de Luna y para un
#      planeta: solo hace falta que haya algo con bordes.
#      Rotación de campo: la montura altacimutal sigue al astro pero la cámara no gira con
#      el cielo, así que el disco gira en la imagen (hasta 1,5° en 2 minutos con el Sol a
#      75° de altura al sur, 9 px en el borde del disco). El giro no se mide (un disco
#      redondo casi no tiene forma con que medirlo): se calcula con la hora de cada
#      fotograma, la ubicación y la orientación de la cámara del último plate solving, que
#      dice hacia dónde queda el norte celeste en la imagen del SDK en cada instante. Cada
#      fotograma se gira alrededor del centro del disco hasta el norte de la referencia.
#   4. Apilar: media de los alineados en lineal (el ruido baja con la raíz del número
#      de fotogramas). El apilado lineal se guarda junto al SER (<nombre>.apilado.npy)
#      para poder revelarlo otra vez con otro afinado sin repetir el apilado.
#   5. Afinar: deconvolución Richardson-Lucy del disco con el desenfoque medido en su
#      propio limbo (la anchura del limbo que calcula enfoque.py es la FWHM de la PSF
#      para un borde nítido). Es la «Nitidez» del editor sin la máscara de estrellas: la
#      del editor mide estrellas y aquí no las hay. Sin disco (paisaje) queda una máscara
#      de desenfoque en dos escalas.
#   6. Revelar como la foto de día de siempre (negro, balance de blancos, blanco al
#      99,9 %, gamma suave).
#
# El resultado se guarda en la galería como una foto de día más (JPG firmado + FITS en
# color de 16 bits en crudos/). El editor de la galería no reprocesa estas fotos (su
# procesado es el de los apilados de noche): el afinado se cambia aquí con «Reafinar».
#
# Con la Saturn-C a 260 mm (2,9″/px y 50 mm de apertura) no va a sacar granulación:
# el límite lo ponen la óptica y el muestreo. Lo que gana es un disco limpio, con el
# limbo y las manchas definidos y sin el ruido de una foto suelta.

import os
import json
import struct
import time
import logging
import threading
from datetime import datetime

import numpy as np
from idioma import _, fmt_num

log = logging.getLogger("scopepower.apilado_video")

CABECERA_SER = 178
PATRON_DE_COLOR = {0: "MONO", 8: "RGGB", 9: "GRBG", 10: "GBRG", 11: "BGGR"}
MEJORES_PCT_DEFECTO = 25
MINIMO_FOTOGRAMAS = 10
RESPUESTA_MINIMA = 0.03          # correlación de fase por debajo: el fotograma no se ha podido alinear
TOLERANCIA_BRILLO = 0.25         # ±25 % del brillo mediano; fuera de ahí (nube, salto) se descarta
NITIDEZ_DEFECTO = 1.0            # afinado 0 (nada) .. 3
ITERACIONES_POR_PUNTO = 4        # afinado 1 = 4 iteraciones de Richardson-Lucy; 3 = 12 (como el máximo del editor)
FACTOR_PSF = 0.85                # PSF algo más estrecha que la FWHM del limbo (menos anillos), como en el editor
PASOS_REFINADO = 3              # medidas del desplazamiento que quedan tras girar el fotograma (probado con Soles
AMORTIGUACION = 0.5             # sintéticos: mediana de 0,25 a 0,07 px con limbo nítido, de 0,5 a 0,25 con limbo blando)
GIRO_MINIMO_REFINAR = 0.05      # grados: por debajo el giro no sesga la medida y no se refina
CLAVES_INFO = ("frames", "puntuados", "usados", "no_alineados", "descartados_brillo", "desplazamiento_max_px", "patron",
               "giro_max_deg")

# Posiciones de los dos verdes en el bloque 2×2 y conversión de OpenCV para desmosaicar.
# OpenCV nombra el patrón por el píxel (1,1), no por el (0,0): RGGB es COLOR_BayerBG.
_VERDES = {"RGGB": ((0, 1), (1, 0)), "BGGR": ((0, 1), (1, 0)), "GRBG": ((0, 0), (1, 1)), "GBRG": ((0, 0), (1, 1))}
_DEMOSAICO = {"RGGB": "COLOR_BayerBG2RGB", "BGGR": "COLOR_BayerRG2RGB", "GRBG": "COLOR_BayerGB2RGB", "GBRG": "COLOR_BayerGR2RGB"}


class ErrorVideo(Exception):
    """El vídeo no se puede leer o apilar (el texto dice por qué)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Lectura del SER
# ─────────────────────────────────────────────────────────────────────────────
class LectorSER:
    """Fotogramas de un SER sin cargarlo entero (memmap); `fotograma(i)` → array h×w."""

    def __init__(self, ruta):
        import grabador_video
        self.ruta = ruta
        try:
            cab = grabador_video.leer_cabecera_ser(ruta)
        except (OSError, ValueError, struct.error) as e:
            raise ErrorVideo(_("No se puede leer el vídeo: {e}", e=e))
        self.ancho, self.alto, self.bits, self.frames = cab["ancho"], cab["alto"], cab["bits"], cab["frames"]
        self.patron = PATRON_DE_COLOR.get(cab["color"], "MONO")
        self.con_tiempos = cab["con_tiempos"]
        if self.frames <= 0 or self.ancho <= 0 or self.alto <= 0:
            raise ErrorVideo(_("El vídeo está vacío"))
        dtype = np.dtype("<u2") if self.bits > 8 else np.uint8
        self._datos = np.memmap(ruta, dtype=dtype, mode="r", offset=CABECERA_SER, shape=(self.frames, self.alto, self.ancho))
        self.blanco = 65535.0 if self.bits > 8 else 255.0

    def fotograma(self, i):
        return np.asarray(self._datos[i])

    def tiempos(self):
        """Marcas de tiempo UTC (segundos desde la época) de cada fotograma, o None si el SER no las trae."""
        if not self.con_tiempos:
            return None
        tam = self.frames * self.alto * self.ancho * (2 if self.bits > 8 else 1)
        with open(self.ruta, "rb") as f:
            f.seek(CABECERA_SER + tam)
            ticks = np.frombuffer(f.read(8 * self.frames), dtype="<i8")
        return (ticks - 621355968000000000) / 1e7

    def duracion_s(self):
        t = self.tiempos()
        if t is None or len(t) < 2:
            return None
        return float(t[-1] - t[0])

    def cerrar(self):
        self._datos = None


def info_video(ruta):
    """Resumen de un SER para listarlo (y su .json de al lado si lo escribió el modo en vivo)."""
    lector = LectorSER(ruta)
    meta = leer_meta(ruta)
    duracion = lector.duracion_s()
    lector.cerrar()
    return {"nombre": os.path.splitext(os.path.basename(ruta))[0], "frames": lector.frames, "ancho": lector.ancho,
            "alto": lector.alto, "bits": lector.bits, "patron": lector.patron, "mb": round(os.path.getsize(ruta) / 1e6, 1),
            "duracion_s": None if duracion is None else round(duracion, 1),
            "fps": None if not duracion else round(lector.frames / duracion, 1),
            "fecha": meta.get("instante") or os.path.getmtime(ruta), "objetivo": meta.get("objetivo", "")}


def leer_meta(ruta_ser):
    """El .json que vivo_dia.py deja junto al SER (objetivo, exposición, orientación...); {} si no está."""
    ruta = os.path.splitext(ruta_ser)[0] + ".json"
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ─────────────────────────────────────────────────────────────────────────────
#  Medidas sobre un fotograma
# ─────────────────────────────────────────────────────────────────────────────
def verde(cfa, patron):
    """Canal verde a media resolución (media de los dos verdes del bloque 2×2), float32."""
    f = np.asarray(cfa)
    if patron not in _VERDES:
        return f.astype(np.float32)
    (a, b), (c, d) = _VERDES[patron]
    h, w = f.shape[0] & ~1, f.shape[1] & ~1
    return (f[a:h:2, b:w:2].astype(np.float32) + f[c:h:2, d:w:2].astype(np.float32)) * 0.5


def nitidez(g):
    """(nitidez, brillo medio): varianza del laplaciano tras un suavizado ligero, relativa al brillo²."""
    import cv2
    brillo = float(g.mean())
    if brillo <= 1e-6:
        return 0.0, brillo
    suave = cv2.GaussianBlur(g, (0, 0), 1.0)
    lap = cv2.Laplacian(suave, cv2.CV_32F, ksize=3)
    return float(lap.var()) / (brillo * brillo), brillo


def desmosaicar(cfa, patron):
    """Fotograma crudo → RGB float32 a resolución completa (interpolación bilineal de OpenCV, vale con 16 bits)."""
    import cv2
    f = np.asarray(cfa)
    if patron not in _DEMOSAICO:
        return np.repeat(f.astype(np.float32)[..., None], 3, axis=2)
    return cv2.cvtColor(np.ascontiguousarray(f), getattr(cv2, _DEMOSAICO[patron])).astype(np.float32)


def desplazamiento(referencia, g, ventana):
    """(dx, dy, respuesta) en píxeles de `g` respecto a `referencia` (correlación de fase subpíxel)."""
    import cv2
    # Copias: con ventana, cv2.phaseCorrelate (OpenCV 4.11) multiplica por ella las imágenes que recibe,
    # sobre ellas mismas. La referencia se usa con todos los fotogramas y se iría borrando hacia los bordes
    (dx, dy), respuesta = cv2.phaseCorrelate(np.array(referencia, dtype=np.float32, copy=True),
                                             np.array(g, dtype=np.float32, copy=True), ventana)
    return float(dx), float(dy), float(respuesta)


def desplazar(rgb, dx, dy, giro_deg=0.0, centro=None):
    """
    La imagen llevada sobre la referencia: movida (-dx, -dy) y, con `giro_deg`, girada ese ángulo
    alrededor de `centro` (x, y de la referencia: el centro del disco). El ángulo va en los ejes de
    la imagen (columna, fila): positivo aumenta atan2(Δfila, Δcolumna). Bilineal, bordes replicados.
    """
    import cv2
    if giro_deg and centro is not None:
        a = np.radians(float(giro_deg))
        R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        c = np.asarray(centro, dtype=float)
        # p_ref = c + R·(p − d − c)
        t = c - R @ (np.array([dx, dy]) + c)
        m = np.hstack([R, t[:, None]]).astype(np.float32)
    else:
        m = np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], dtype=np.float32)
    return cv2.warpAffine(rgb, m, (rgb.shape[1], rgb.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


# ─────────────────────────────────────────────────────────────────────────────
#  Rotación de campo de la montura altacimutal
# ─────────────────────────────────────────────────────────────────────────────
def angulos_norte(tiempos_unix, observatorio, Hs, objetivo=None, radec=None, muestras=40):
    """
    Ángulo (grados, desenrollado) de la dirección del norte celeste en los ejes de la imagen del
    SDK (columna, fila hacia abajo) en cada instante, en el astro `objetivo` («sol», «luna») o en
    `radec` = (ra_deg, dec_deg). observatorio = (lat, lon, alt_m). Hs (2×2): (Δaz·cos alt, Δalt)
    = Hs · (Δcolumna, Δfila) del SDK (orientacion_camara.matriz_sensor_sdk); su signo dice si la
    imagen está en espejo y con él el sentido del giro. Se calcula en `muestras` instantes y se
    interpola: el giro es suave.
    """
    from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
    from astropy.time import Time
    import astropy.units as u
    t = np.asarray(tiempos_unix, dtype=float)
    lat, lon, alt_m = observatorio
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    if radec is None:
        cuerpo = get_body("sun" if objetivo == "sol" else "moon", Time(float(np.median(t)), format="unix"), lugar)
        radec = (float(cuerpo.ra.deg), float(cuerpo.dec.deg))
    ra, dec = radec
    # El astro y un punto 0,1° al norte (dirección, sin distancia: con la del Sol saldría la del baricentro)
    paso = 0.1 if dec < 89.8 else -0.1
    puntos = SkyCoord(ra=[ra, ra] * u.deg, dec=[dec, dec + paso] * u.deg, frame="icrs")
    tm = np.linspace(t.min(), t.max(), max(2, int(muestras))) if len(t) > muestras else t
    marco = AltAz(obstime=Time(tm, format="unix"), location=lugar)
    h = puntos[:, None].transform_to(marco)
    az, alt = h.az.deg, h.alt.deg
    du = ((az[1] - az[0] + 180.0) % 360.0 - 180.0) * np.cos(np.radians(alt[0]))
    dv = alt[1] - alt[0]
    if paso < 0:
        du, dv = -du, -dv
    sdk = np.linalg.inv(np.asarray(Hs, dtype=float)) @ np.vstack([du, dv])
    theta = np.unwrap(np.arctan2(sdk[1], sdk[0]))
    if len(tm) != len(t):
        theta = np.interp(t, tm, theta)
    return np.degrees(theta)


MODOS_ROTACION = ("automatico", "siempre", "nunca")
MONTURAS_ECUATORIALES = ("ecuatorial", "ecuatorial_alemana")


def modo_rotacion(valor):
    """[Video] rotacion_campo normalizado: automatico (de serie), siempre o nunca."""
    v = str(valor or "").strip().lower().replace("á", "a")
    return v if v in MODOS_ROTACION else "automatico"


def angulos_de_rotacion(lector, meta, contexto=None):
    """
    (ángulos del norte por fotograma en grados, None) o (None, motivo) para el vídeo de `lector`:
    con las marcas de tiempo del SER (o el inicio y la duración del .json), la ubicación y la
    orientación de la cámara guardadas al grabar (o, en vídeos de antes, las actuales de `contexto`).
    contexto["rotacion_campo"]: «automatico» corrige salvo que la montura sea ecuatorial (lo dijo por
    ASCOM al grabar, o lo dice ahora); «siempre» corrige aunque lo sea (una altacimutal sobre cuña que
    ASCOM no distingue, o al revés); «nunca» no corrige.
    """
    contexto = contexto or {}
    objetivo = meta.get("objetivo")
    if objetivo not in ("sol", "luna"):
        return None, ""                        # paisaje: el campo no gira con el cielo
    modo = modo_rotacion(contexto.get("rotacion_campo"))
    if modo == "nunca":
        return None, _("desactivada en la configuración")
    montura = meta.get("modo_montura") or contexto.get("modo_montura")
    if modo == "automatico" and montura in MONTURAS_ECUATORIALES:
        return None, _("montura ecuatorial, el campo no gira")
    observatorio = meta.get("observatorio") or contexto.get("observatorio")
    H = meta.get("H_camara")
    binning = meta.get("binning_foto", 1)
    if H is None and contexto.get("orientacion"):
        H, binning = contexto["orientacion"].get("H"), contexto["orientacion"].get("binning_foto", 1)
    if not observatorio:
        return None, _("falta la ubicación del observatorio")
    if H is None:
        return None, _("falta la orientación de la cámara: hace falta un plate solving de noche")
    tiempos = lector.tiempos()
    if tiempos is None or len(tiempos) != lector.frames or float(np.ptp(tiempos)) <= 0:
        inicio, dur = meta.get("instante"), meta.get("duracion_s")
        if not inicio or not dur:
            return None, _("el vídeo no trae la hora de cada fotograma")
        tiempos = float(inicio) + np.linspace(0.0, float(dur), lector.frames)
    try:
        import orientacion_camara
        transformacion = meta.get("orientacion") or orientacion_camara.leer_sdk()["transformacion"]
        Hs = orientacion_camara.matriz_sensor_sdk({"H": H, "binning_foto": binning}, {"transformacion": transformacion})
        return angulos_norte(tiempos, observatorio, Hs, objetivo=objetivo), None
    except Exception as e:
        log.warning("Rotación de campo: no se pudo calcular: %s", e)
        return None, _("no se pudo calcular: {e}", e=e)


def afinar(rgb, cantidad):
    """Afinado en dos escalas (máscara de desenfoque fina + media) sobre la imagen lineal 0..1."""
    import cv2
    if cantidad <= 0:
        return rgb
    fino = rgb - cv2.GaussianBlur(rgb, (0, 0), 1.0)
    medio = rgb - cv2.GaussianBlur(rgb, (0, 0), 2.5)
    return np.clip(rgb + float(cantidad) * (0.7 * fino + 0.4 * medio), 0.0, None)


def medir_limbo(media_rgb):
    """
    FWHM del desenfoque (px de la imagen) medida en el limbo del disco del apilado: la anchura a
    media altura de la derivada radial del verde (enfoque.medir_disco), que para un borde nítido
    es la de la PSF. (None, info) si no se ve un disco (paisaje, nubes).
    """
    import enfoque
    verde = np.ascontiguousarray(media_rgb[..., 1], dtype=np.float32)
    try:
        info = enfoque.medir_disco(verde, escala_px=1.0)
    except Exception as e:
        return None, {"limbo": None, "motivo": str(e)}
    limbo = info.get("limbo")
    return (float(limbo) if limbo else None), info


def deconvolucionar_disco(rgb, iteraciones, fwhm_px, progreso=None):
    """
    Richardson-Lucy por canal sobre la imagen lineal con una PSF gaussiana de la FWHM medida en
    el limbo (× FACTOR_PSF). El cociente se acota para que el ruido no dispare la corrección ni
    el limbo anille. Devuelve float32 (H, W, 3) lineal.
    """
    import cv2
    iteraciones = int(iteraciones)
    if iteraciones <= 0 or not fwhm_px or fwhm_px <= 0:
        return np.asarray(rgb, dtype=np.float32)
    sigma = max(float(fwhm_px) * FACTOR_PSF, 1.0) / 2.355
    rgb = np.asarray(rgb, dtype=np.float32)
    salida = np.empty_like(rgb)
    total = 3 * iteraciones
    for c in range(3):
        y = rgb[..., c]
        piso = float(np.percentile(y[::8, ::8], 1.0))
        y = np.clip(y - piso, 1e-3, None) + 1e-3          # RL exige datos positivos
        x = y.copy()
        for i in range(iteraciones):
            est = cv2.GaussianBlur(x, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
            ratio = np.clip(y / np.maximum(est, 1e-6), 0.5, 2.0)
            x *= cv2.GaussianBlur(ratio, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
            if progreso:
                progreso((c * iteraciones + i + 1) / total)
        salida[..., c] = x + piso
    return salida


def revelar(media_rgb, negro, balance, ajustes=None, fwhm_px=None, progreso=None, ev=None, cantidad_nitidez=None):
    """
    Media lineal RGB float32 → (RGB uint8 revelado con revelado_dia, cubo RGB uint16 lineal
    afinado y normalizado al blanco para el FITS, {"iteraciones", "psf_px", "ajustes"}).
    `ajustes` son los del editor de día (exposición, contraste, detalle, nitidez, color);
    `ev` y `cantidad_nitidez` sueltos valen por compatibilidad. Con `fwhm_px` (el limbo medido)
    la nitidez es la deconvolución del disco; sin disco, la máscara de desenfoque.
    """
    import revelado_dia
    a = dict(ajustes or {})
    if ev is not None:
        a.setdefault("exposicion", ev)
    if cantidad_nitidez is not None:
        a.setdefault("nitidez", cantidad_nitidez)
    a = revelado_dia.normalizar(a)
    lineal = revelado_dia.aplicar_balance(media_rgb, negro, balance)
    rgb8, lineal01 = revelado_dia.revelar(lineal, a, limbo_px=fwhm_px, progreso=progreso, devolver_lineal=True)
    cubo16 = (lineal01 * 65535.0 + 0.5).astype(np.uint16)
    iteraciones = min(int(round(ITERACIONES_POR_PUNTO * a["nitidez"])), ITERACIONES_POR_PUNTO * 3) if fwhm_px else 0
    afinado = {"iteraciones": iteraciones, "psf_px": round(float(fwhm_px) * FACTOR_PSF, 2) if (fwhm_px and iteraciones) else None,
               "ajustes": a}
    return rgb8, cubo16, afinado


# ─────────────────────────────────────────────────────────────────────────────
#  Apilado lineal guardado junto al SER (para reafinar sin volver a apilar)
# ─────────────────────────────────────────────────────────────────────────────
def ruta_apilado(ruta_ser):
    return os.path.splitext(ruta_ser)[0] + ".apilado.npy"


def hay_apilado(ruta_ser):
    return os.path.isfile(ruta_apilado(ruta_ser)) and os.path.isfile(os.path.splitext(ruta_ser)[0] + ".apilado.json")


def guardar_apilado(ruta_ser, media, info):
    np.save(ruta_apilado(ruta_ser), np.asarray(media, dtype=np.float32))
    with open(os.path.splitext(ruta_ser)[0] + ".apilado.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)


def cargar_apilado(ruta_ser):
    """(media RGB float32, info) del apilado guardado; ErrorVideo si no lo hay."""
    if not hay_apilado(ruta_ser):
        raise ErrorVideo(_("Este vídeo no tiene un apilado guardado: apílalo primero."))
    try:
        media = np.load(ruta_apilado(ruta_ser))
        with open(os.path.splitext(ruta_ser)[0] + ".apilado.json", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError) as e:
        raise ErrorVideo(_("No se puede leer el apilado guardado: {e}", e=e))
    return media, info


# ─────────────────────────────────────────────────────────────────────────────
#  Apilado
# ─────────────────────────────────────────────────────────────────────────────
def apilar(ruta_ser, pct=MEJORES_PCT_DEFECTO, progreso=None, cancelar=None, angulos=None):
    """
    Apila los mejores `pct` % fotogramas del SER. Devuelve {"media": RGB float32 lineal,
    "patron", "frames", "puntuados", "usados", "no_alineados", "descartados_brillo", "indices",
    "referencia", "desplazamiento_max_px", "giro_max_deg", "lector"}.
    `angulos`: ángulo del norte celeste en la imagen de cada fotograma (angulos_de_rotacion); con
    él cada fotograma se gira alrededor del centro del disco hasta el norte de la referencia.
    progreso(fase, fraccion) y cancelar() → True para abortar (lanza ErrorVideo).
    """
    import cv2
    lector = LectorSER(ruta_ser)
    n = lector.frames
    avisar = progreso or (lambda fase, fr: None)
    parar = cancelar or (lambda: False)

    # 1. Puntuar
    puntos = np.zeros(n, np.float64)
    brillos = np.zeros(n, np.float64)
    for i in range(n):
        if parar():
            raise ErrorVideo(_("Apilado cancelado"))
        puntos[i], brillos[i] = nitidez(verde(lector.fotograma(i), lector.patron))
        if i % 10 == 0:
            avisar("puntuando", i / n)
    mediana = float(np.median(brillos)) if n else 0.0
    validos = np.abs(brillos - mediana) <= TOLERANCIA_BRILLO * max(mediana, 1e-6)
    if validos.sum() == 0:
        validos[:] = True
    descartados_brillo = int(n - validos.sum())

    # 2. Elegir
    orden = [int(i) for i in np.argsort(-puntos) if validos[i]]
    k = max(min(MINIMO_FOTOGRAMAS, len(orden)), int(round(len(orden) * float(pct) / 100.0)))
    elegidos = orden[:k]
    if not elegidos:
        raise ErrorVideo(_("El vídeo no tiene ningún fotograma aprovechable"))
    referencia = elegidos[0]

    # 3 y 4. Alinear sobre el mejor y promediar. El desplazamiento se mide en el verde a resolución
    # completa del fotograma ya desmosaicado (hace falta igual para apilar): en el plano verde a media
    # resolución la correlación de fase se equivoca hasta 0,8 px con desplazamientos de un píxel impar
    ref_g = verde(lector.fotograma(referencia), lector.patron)
    ref_rgb = desmosaicar(lector.fotograma(referencia), lector.patron)
    ref_v = np.ascontiguousarray(ref_rgb[..., 1])
    ventana = cv2.createHanningWindow((ref_v.shape[1], ref_v.shape[0]), cv2.CV_32F)
    centro = None
    if angulos is not None:
        # El giro se hace alrededor del centro del disco de la referencia (lo que sigue la montura)
        import enfoque
        circ = enfoque.circulo_disco(ref_g)
        if circ is not None:
            centro = (2.0 * circ[0] + 0.5, 2.0 * circ[1] + 0.5)       # el verde va a media resolución
        else:
            centro = ((lector.ancho - 1) / 2.0, (lector.alto - 1) / 2.0)
            log.info("Apilado de vídeo: sin disco en la referencia; el giro se hace alrededor del centro de la imagen")
    acumulado = None
    usados, no_alineados, desplazamiento_max, giro_max = 0, 0, 0.0, 0.0
    for j, i in enumerate(elegidos):
        if parar():
            raise ErrorVideo(_("Apilado cancelado"))
        rgb = ref_rgb if i == referencia else desmosaicar(lector.fotograma(i), lector.patron)
        giro = float(angulos[referencia] - angulos[i]) if angulos is not None else 0.0
        if i == referencia:
            dx = dy = 0.0
        else:
            v = np.ascontiguousarray(rgb[..., 1])
            dx, dy, respuesta = desplazamiento(ref_v, v, ventana)
            if respuesta < RESPUESTA_MINIMA:
                no_alineados += 1
                continue
            if abs(giro) >= GIRO_MINIMO_REFINAR:
                # Medido sobre el fotograma aún girado, el desplazamiento sale sesgado (las manchas o los
                # cráteres giran con él): se gira el verde con lo medido y se mide lo que queda
                a = np.radians(giro)
                for __ in range(PASOS_REFINADO):
                    ex, ey, __r = desplazamiento(ref_v, desplazar(v, dx, dy, giro, centro), ventana)
                    # p_ref = c + R(p − d − c) − e  ⇒  d' = d + Rᵀ·e
                    dx += AMORTIGUACION * (np.cos(a) * ex + np.sin(a) * ey)
                    dy += AMORTIGUACION * (-np.sin(a) * ex + np.cos(a) * ey)
        if dx or dy or giro:
            rgb = desplazar(rgb, dx, dy, giro, centro)
        giro_max = max(giro_max, abs(giro))
        desplazamiento_max = max(desplazamiento_max, float(np.hypot(dx, dy)))
        acumulado = rgb if acumulado is None else acumulado + rgb
        usados += 1
        avisar("apilando", (j + 1) / len(elegidos))
    if not usados:
        raise ErrorVideo(_("No se ha podido alinear ningún fotograma"))
    media = acumulado / float(usados)
    log.info("Apilado de vídeo %s: %d fotogramas, %d elegidos (%s %%), %d usados, %d sin alinear, %d descartados por brillo, "
             "desplazamiento máximo %.1f px, giro %s", os.path.basename(ruta_ser), n, k, fmt_num(pct, 0), usados, no_alineados,
             descartados_brillo, desplazamiento_max, "sin corregir" if angulos is None else f"hasta {giro_max:.2f}°")
    return {"media": media, "patron": lector.patron, "frames": n, "puntuados": int(validos.sum()), "usados": usados,
            "no_alineados": no_alineados, "descartados_brillo": descartados_brillo, "indices": elegidos,
            "referencia": referencia, "desplazamiento_max_px": round(desplazamiento_max, 2),
            "giro_max_deg": None if angulos is None else round(giro_max, 3), "lector": lector,
            "blanco": lector.blanco}


# ─────────────────────────────────────────────────────────────────────────────
#  Trabajo en segundo plano (la web sigue el progreso)
# ─────────────────────────────────────────────────────────────────────────────
FASES = {"puntuando": "Midiendo la nitidez de cada fotograma", "apilando": "Alineando y apilando los mejores",
         "revelando": "Revelando y guardando en la galería"}


class TrabajoApiladoVideo:
    def __init__(self):
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo = None
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.fase = ""
        self.progreso = 0.0
        self.mensaje = ""
        self.error = ""
        self.nombre = ""
        self.resultado = None

    def estado(self):
        with self._lock:
            return {"en_curso": self.en_curso, "terminado": self.terminado, "fase": self.fase,
                    "fase_texto": _(FASES[self.fase]) if self.fase in FASES else self.fase,
                    "progreso": round(self.progreso, 3), "mensaje": self.mensaje, "error": self.error,
                    "nombre": self.nombre, "resultado": self.resultado}

    def iniciar(self, ruta_ser, guardar, pct=MEJORES_PCT_DEFECTO, cantidad_nitidez=NITIDEZ_DEFECTO, ev=0.0, reafinar=False,
                contexto=None, ajustes=None):
        """
        guardar(rgb8, cubo16, info) → dict con "archivo" (y lo que quiera): recibe la imagen revelada
        (RGB uint8, orientación del SDK) y el cubo lineal, y la deja en la galería.
        `reafinar`: no se apila; se vuelve a revelar el apilado guardado con el afinado nuevo.
        `contexto`: {"observatorio", "orientacion"} actuales, para corregir la rotación de campo de los
        vídeos grabados sin ellos en su .json. `ajustes`: los del revelado de día (revelado_dia.py);
        si no vienen, nitidez y exposición sueltas.
        """
        if reafinar and not hay_apilado(ruta_ser):
            raise RuntimeError(_("Este vídeo no tiene un apilado guardado: apílalo primero."))
        with self._lock:
            if self.en_curso:
                raise RuntimeError(_("Ya se está apilando un vídeo; espera a que termine."))
            self._reiniciar()
            self._parar.clear()
            self.en_curso = True
            self.nombre = os.path.splitext(os.path.basename(ruta_ser))[0]
            self.fase = "revelando" if reafinar else "puntuando"
            self.mensaje = (_("Reafinando {nombre}...", nombre=self.nombre) if reafinar
                            else _("Apilando {nombre}...", nombre=self.nombre))
        self._hilo = threading.Thread(target=self._ejecutar, name="apilado-video", daemon=True,
                                      args=(ruta_ser, guardar, float(pct), float(cantidad_nitidez), float(ev), bool(reafinar),
                                            contexto, ajustes))
        self._hilo.start()
        return self._hilo

    def cancelar(self):
        self._parar.set()

    def esperar(self, timeout=None):
        if self._hilo:
            self._hilo.join(timeout)

    def _avanzar(self, fase, fraccion):
        with self._lock:
            self.fase, self.progreso = fase, float(fraccion)

    def _ejecutar(self, ruta_ser, guardar, pct, cantidad_nitidez, ev, reafinar=False, contexto=None, ajustes=None):
        inicio = time.time()
        error, resultado = "", None
        try:
            meta = leer_meta(ruta_ser)
            if reafinar:
                media, info = cargar_apilado(ruta_ser)
                self._avanzar("revelando", 0.0)
            else:
                lector_giro = LectorSER(ruta_ser)
                angulos, motivo_giro = angulos_de_rotacion(lector_giro, meta, contexto)
                lector_giro.cerrar()
                if motivo_giro:
                    log.info("Apilado de vídeo %s: rotación de campo sin corregir: %s", self.nombre, motivo_giro)
                r = apilar(ruta_ser, pct, progreso=self._avanzar, cancelar=self._parar.is_set, angulos=angulos)
                self._avanzar("revelando", 0.0)
                lector = r["lector"]
                import foto_dia
                negro = meta.get("negro")
                if negro is None:
                    negro = foto_dia.nivel_negro(lector.fotograma(r["referencia"]), lector.blanco)
                balance = meta.get("balance")
                if not balance and lector.patron in _VERDES:
                    balance = foto_dia.balance_para_patron(lector.fotograma(r["referencia"]), lector.patron, meta.get("objetivo"),
                                                           negro, lector.blanco, aprender=False)
                lector.cerrar()
                media = r["media"]
                limbo, info_limbo = medir_limbo(media)
                if limbo:
                    log.info("Apilado de vídeo %s: limbo %.2f px (%d ángulos)", self.nombre, limbo, info_limbo.get("n", 0))
                else:
                    log.info("Apilado de vídeo %s: sin disco que medir (%s): afinado sencillo", self.nombre, info_limbo.get("motivo"))
                info = {k: r[k] for k in CLAVES_INFO}
                info.update({"pct": pct, "negro": None if negro is None else float(negro), "balance": balance,
                             "limbo_px": None if limbo is None else round(limbo, 2), "instante": time.time(),
                             "giro_motivo": motivo_giro or ""})
                guardar_apilado(ruta_ser, media, info)
            rgb8, cubo16, afinado = revelar(media, info.get("negro"), info.get("balance"), ajustes=ajustes, ev=ev,
                                            cantidad_nitidez=cantidad_nitidez, fwhm_px=info.get("limbo_px"),
                                            progreso=lambda f: self._avanzar("revelando", f))
            info = dict(info, nitidez=cantidad_nitidez, meta=meta, reafinado=reafinar,
                        segundos=round(time.time() - inicio, 1), **afinado)
            resultado = dict(guardar(rgb8, cubo16, info) or {})
            resultado.update(info)
            resultado.pop("meta", None)
        except ErrorVideo as e:
            error = str(e)
            log.warning("Apilado de vídeo %s: %s", os.path.basename(ruta_ser), e)
        except Exception as e:
            error = str(e)
            log.exception("Apilado de vídeo: error")
        finally:
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.progreso = 1.0
                self.error = error
                self.resultado = resultado
                if error:
                    self.mensaje = _("Error: {e}", e=error)
                else:
                    s = fmt_num(resultado["segundos"], 0)
                    self.mensaje = (_("Reafinado en {s} s", s=s) if resultado["reafinado"] else
                                    _("Apilados {usados} de {frames} fotogramas en {s} s", usados=resultado["usados"],
                                      frames=resultado["frames"], s=s))
                    if resultado.get("iteraciones"):
                        self.mensaje += " · " + _("limbo {l} px, {n} iteraciones", l=fmt_num(resultado["limbo_px"], 1),
                                                   n=resultado["iteraciones"])
                    elif resultado.get("nitidez") and not resultado.get("limbo_px"):
                        self.mensaje += " · " + _("sin disco que medir: afinado sencillo")
                    if resultado.get("giro_max_deg") is not None:
                        self.mensaje += " · " + _("rotación de campo corregida (hasta {g}°)", g=fmt_num(resultado["giro_max_deg"], 2))
                    elif resultado.get("giro_motivo"):
                        self.mensaje += " · " + _("rotación de campo sin corregir: {motivo}", motivo=resultado["giro_motivo"])


trabajo_apilado_video = TrabajoApiladoVideo()


def fecha_de_nombre(nombre, meta=None):
    """datetime del vídeo: el instante del .json o, si no, la fecha del nombre Objeto_AAAAMMDD_HHMMSS."""
    if meta and meta.get("instante"):
        return datetime.fromtimestamp(float(meta["instante"]))
    try:
        return datetime.strptime("_".join(nombre.split("_")[-2:]), "%Y%m%d_%H%M%S")
    except ValueError:
        return datetime.now()
