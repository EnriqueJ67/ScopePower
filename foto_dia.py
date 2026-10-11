# -*- coding: utf-8 -*-
"""
Modo día de la Foto de prueba: exposición automática para Sol (con filtro), Luna
y paisaje, foto suelta o timelapse, y montaje del vídeo.

Funciona como los trabajos de darks y flats: se lanza en un hilo, la web sondea
`estado()` y se cancela con /cancelar-captura (foto) o /timelapse/cancelar.

La exposición se busca como en los flats, pero midiendo la zona MÁS BRILLANTE de
la imagen (percentil 99,8) en vez de la mediana: de día lo que hay que evitar es
quemar el disco del Sol o de la Luna, no dejar el fondo a un nivel dado.
"""
import os
import csv
import json
import time
import logging
import threading
from datetime import datetime

import numpy as np

from hardware import capturar_frame, CapturaCancelada, ErrorHardware, estado_captura
import giro_camara
import enfoque

log = logging.getLogger("scopepower.foto_dia")

# nivel: fracción del blanco que debe alcanzar la zona más brillante
# inicial/minima/maxima: exposiciones en segundos (la mínima real la dice la cámara)
OBJETIVOS = {
    "sol":     {"nombre": "Sol",     "nivel": 0.60, "inicial": 0.001, "minima": 0.00002, "maxima": 0.5,  "seguimiento": "solar"},
    "luna":    {"nombre": "Luna",    "nivel": 0.65, "inicial": 0.005, "minima": 0.0001,  "maxima": 2.0,  "seguimiento": "lunar"},
    "paisaje": {"nombre": "paisaje", "nivel": 0.75, "inicial": 0.005, "minima": 0.0001,  "maxima": 5.0,  "seguimiento": None},
}
MAX_INTENTOS = 6
# Compensación de exposición del usuario, en EV (pasos de cámara): +1 EV = zona más
# brillante al doble de nivel. Se aplica al nivel objetivo, no a la exposición, así la
# búsqueda sigue convergiendo sola; el tope alto deja margen antes del blanco.
COMPENSACION_MAX_EV = 3.0
NIVEL_MINIMO, NIVEL_MAXIMO = 0.03, 0.92


def con_compensacion(conf, ev):
    """Copia de `conf` con el nivel objetivo corregido `ev` pasos (2^ev); ev puede ser un número o una función que lo devuelve."""
    try:
        ev = float(ev() if callable(ev) else (ev or 0.0))
    except (TypeError, ValueError):
        ev = 0.0
    ev = max(-COMPENSACION_MAX_EV, min(COMPENSACION_MAX_EV, ev))
    nuevo = dict(conf)
    nuevo["nivel"] = max(NIVEL_MINIMO, min(NIVEL_MAXIMO, conf["nivel"] * 2.0 ** ev))
    nuevo["ev"] = ev
    return nuevo
# ASCOM DriveRates: 0 sidéreo, 1 lunar, 2 solar, 3 rey
VELOCIDADES = {"sidereo": 0, "lunar": 1, "solar": 2}
CARPETA_TIMELAPSE = os.path.join("static", "timelapse")


def formatear_exposicion(s):
    if s is None:
        return "?"
    if s < 0.001:
        return f"{s * 1e6:.0f} µs"
    if s < 1.0:
        return f"{s * 1000:.1f} ms".replace(".0 ", " ")
    return f"{s:.2f} s".replace(".00 ", " ")


def poner_seguimiento(montura, velocidad):
    """
    Cambia la velocidad de seguimiento de la montura (solar, lunar, sidéreo) si el
    driver lo permite. Devuelve True si se ha cambiado. Nunca lanza.
    """
    codigo = VELOCIDADES.get(velocidad)
    if codigo is None:
        return False

    def _fijar(tel):
        try:
            disponibles = [int(r) for r in tel.TrackingRates]
        except Exception:
            disponibles = [0]
        if codigo not in disponibles:
            raise RuntimeError(f"el driver no admite la velocidad {velocidad}")
        tel.TrackingRate = codigo
        tel.Tracking = True
        return True

    try:
        ok = montura.ejecutar(_fijar, timeout=15)
        log.info("Seguimiento de la montura: %s", velocidad)
        return bool(ok)
    except Exception as e:
        log.warning("No se pudo poner el seguimiento %s: %s", velocidad, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Piezas comunes (se ejecutan DENTRO del hilo de la cámara)
# ─────────────────────────────────────────────────────────────────────────────
def _patron(sensor_type):
    return {2: "RGGB", 3: "CMYG", 4: "CMYG2", 5: "LRGB"}.get(sensor_type)


def sensor_type_de_patron(patron):
    """BAYERPAT de la cabecera -> SensorType de ASCOM (0 = mono), para revelar un FITS guardado."""
    return {"RGGB": 2, "CMYG": 3, "CMYG2": 4, "LRGB": 5}.get(str(patron or "").strip().upper(), 0)


def zona_recorte(ancho, alto, campo):
    """(x0, y0, w, h) del recorte central que deja la fracción `campo` del ancho y del alto."""
    campo = float(campo)
    if campo >= 0.999:
        return 0, 0, ancho, alto
    campo = max(0.1, campo)
    w = max(16, int(ancho * campo)) & ~1
    h = max(16, int(alto * campo)) & ~1
    return ((ancho - w) // 2) & ~1, ((alto - h) // 2) & ~1, w, h


def recortar_centro(frame, campo):
    """
    Recorta el centro del fotograma dejando la fracción `campo` del ancho y del alto
    (1.0 = campo completo). Los bordes quedan en coordenadas pares para no romper el
    patrón Bayer del mosaico CFA.
    """
    x0, y0, w, h = zona_recorte(frame.shape[1], frame.shape[0], campo)
    return frame[y0:y0 + h, x0:x0 + w]


def preparar_camara(cam, conf):
    """Ganancia al mínimo (de día sobra luz) y límites reales. Devuelve un dict con lo que hace falta luego."""
    ganancia_previa, ganancia = None, None
    try:
        ganancia_previa = cam.Gain
        minima_g = cam.GainMin
        if ganancia_previa != minima_g:
            cam.Gain = minima_g
        ganancia = int(cam.Gain)
    except Exception as e:
        log.info("La cámara no deja fijar la ganancia: %s", e)
    try:
        blanco = float(cam.MaxADU) or 65535.0
    except Exception:
        blanco = 65535.0
    try:
        exp_min = max(float(cam.ExposureMin), 1e-6)
    except Exception:
        exp_min = conf["minima"]
    return {"ganancia_previa": ganancia_previa, "ganancia": ganancia, "blanco": blanco,
            "minima": max(conf["minima"], exp_min), "sensor_type": cam.SensorType,
            "px": cam.PixelSizeX, "py": cam.PixelSizeY}


def restaurar_camara(cam, prep):
    if prep.get("ganancia_previa") is not None and prep["ganancia_previa"] != prep.get("ganancia"):
        try:
            cam.Gain = prep["ganancia_previa"]
        except Exception:
            pass


def capturar_ajustando(cam, conf, binning, prep, exposicion, max_intentos, informar=None, descripcion="Foto de día", campo=1.0):
    """
    Expone y corrige la exposición hasta que la zona más brillante queda cerca del
    nivel objetivo (o se agotan los intentos). Devuelve
    (frame, exposicion, nivel, saturada, intentos) con el mejor fotograma no saturado.
    La medida se hace sobre el recorte central `campo` (lo que se va a ver), el
    fotograma devuelto es el completo.
    """
    objetivo_nivel = conf["nivel"]
    blanco, minima = prep["blanco"], prep["minima"]
    exposicion = max(minima, min(conf["maxima"], float(exposicion)))
    mejor, intentos = None, []
    frame, nivel = None, 0.0
    for intento in range(1, max_intentos + 1):
        if informar:
            informar(f"{descripcion}: prueba {intento}, exponiendo {formatear_exposicion(exposicion)}")
        frame = capturar_frame(cam, exposicion, binning, descripcion=f"{descripcion} {intento}")
        zona = recortar_centro(frame, campo)
        nivel = float(np.percentile(zona, 99.8)) / blanco
        saturados = float(np.mean(zona >= 0.98 * blanco))
        intentos.append({"exposicion": exposicion, "nivel": round(nivel, 3)})
        if informar:
            informar(f"{descripcion}: prueba {intento}, {formatear_exposicion(exposicion)} → nivel {100 * nivel:.0f} %", intentos)
        distancia = abs(nivel - objetivo_nivel)
        if saturados < 0.002 and (mejor is None or distancia < mejor[0]):
            mejor = (distancia, frame, exposicion, nivel)
        if saturados < 0.002 and 0.85 * objetivo_nivel <= nivel <= 1.12 * objetivo_nivel:
            break
        if saturados >= 0.002 or nivel >= 0.98:
            factor = 0.25
        else:
            factor = objetivo_nivel / max(nivel, 0.002)
            factor = max(0.05, min(20.0, factor))
        nueva = max(minima, min(conf["maxima"], exposicion * factor))
        if abs(nueva - exposicion) / exposicion < 0.02:
            break
        exposicion = nueva
    if mejor is not None:
        _, frame, exposicion, nivel = mejor
    return frame, exposicion, nivel, mejor is None, intentos


# ─────────────────────────────────────────────────────────────────────────────
#  Color de las fotos de día
# ─────────────────────────────────────────────────────────────────────────────
# El sensor en bruto ve mucho más verde que rojo o azul (dos píxeles verdes de cada
# cuatro y más sensibilidad en verde): sin balance de blancos la Luna sale verde. Y
# el negro no puede sacarse de la propia foto: de día lo más oscuro es el cielo, y
# tomar su canal más débil como negro le quita el rojo y el azul (cielo verde puro).
# Por eso:
#   - el negro es el del sensor (la mediana del dark más reciente, que es casi todo
#     offset), o lo más oscuro de la foto si es menor;
#   - Luna y Sol se equilibran con su propio disco menos el cielo que lo rodea: el
#     disco sale gris neutro y el cielo conserva su azul;
#   - el paisaje usa el balance de luz de día aprendido de la última Luna (la Luna la
#     ilumina el Sol), o uno típico de los sensores Sony si aún no hay ninguna.
BALANCE_DEFECTO = {"r": 1.8, "b": 1.5}         # ganancias de R y B respecto a G a luz de día (típicas IMX)
# La Luna es algo más roja que la luz del Sol (B−V 0,92 frente a 0,65): al pasar del
# balance que la deja gris al de luz de día, el rojo sube y el azul baja
LUNA_A_LUZ_DE_DIA = {"r": 1.10, "b": 0.80}
RUTA_BALANCE = os.path.join("datos", "balance_dia.json")
# Color del Sol (preferencia foto_dia_color_sol). Probado el 27-sep-2026: dejar el disco
# gris neutro (como la Luna) lo pinta blanco-azulado y no se reconoce como el Sol.
#   amarillo: el disco neutro teñido al amarillo-anaranjado de las fotos típicas
#   natural:  balance de luz de día, sin tocar: el color que da el filtro solar
#   blanco:   el disco gris neutro
COLORES_SOL = ("amarillo", "natural", "blanco")
# Tinte del Sol amarillo sobre el disco neutro, en lineal: con la gamma 1/1,6 del revelado
# el disco sale como (255, 190, 65): amarillo anaranjado
TINTE_SOL_AMARILLO = {"r": 1.58, "b": 0.17}
# Solo se aprende la luz de día de algo que parezca la Luna: un disco con cielo alrededor.
# Con el objetivo "Luna" puesto y la cámara en un paisaje, el "disco" es el cielo claro
# (80 % de la foto el 27-sep) y el balance aprendido salía disparatado.
DISCO_MAXIMO_PARA_APRENDER = 0.4
FONDO_MINIMO_PARA_APRENDER = 0.3
_cache_negro = {"clave": None, "valor": None}


def _submuestras(frame, patron, maximo=600_000):
    """Canales (r, g, b) en float32 por superpíxel 2×2, con salto para no pasar de `maximo` superpíxeles."""
    f = np.asarray(frame)
    h, w = f.shape[0] & ~1, f.shape[1] & ~1
    paso = max(1, int(np.sqrt((h // 2) * (w // 2) / maximo)))
    sub = lambda i, j: f[i:h:2, j:w:2][::paso, ::paso].astype(np.float32)
    pos = {"RGGB": ((0, 0), (0, 1), (1, 0), (1, 1)), "BGGR": ((1, 1), (0, 1), (1, 0), (0, 0)),
           "GRBG": ((0, 1), (0, 0), (1, 1), (1, 0)), "GBRG": ((1, 0), (0, 0), (1, 1), (0, 1))}[patron]
    r, g1, g2, b = (sub(*p) for p in pos)
    return r, (g1 + g2) * 0.5, b


def negro_del_sensor():
    """Mediana del dark más reciente de calibraciones/ (el offset de la cámara), o None si no hay ninguno."""
    import glob
    darks = sorted(glob.glob(os.path.join("calibraciones", "dark_*.fits")), key=os.path.getmtime)
    if not darks:
        return None
    clave = (darks[-1], os.path.getmtime(darks[-1]))
    if _cache_negro["clave"] != clave:
        try:
            from astropy.io import fits
            datos = fits.getdata(darks[-1])
            _cache_negro["valor"] = float(np.median(np.asarray(datos)[::4, ::4]))
        except Exception as e:
            log.warning("No se pudo leer el nivel de negro de %s: %s", darks[-1], e)
            _cache_negro["valor"] = None
        _cache_negro["clave"] = clave
    return _cache_negro["valor"]


def nivel_negro(frame, blanco=65535.0):
    """Nivel de negro para revelar la foto: el del sensor, o lo más oscuro de la foto si es menor."""
    muestra = np.asarray(frame)[::3, ::3]
    minimo = float(np.percentile(muestra, 0.02))
    sensor = negro_del_sensor()
    if sensor is None:
        sensor = 0.0025 * blanco      # sin darks: un offset típico (la Saturn-C con offset 35 da ~144 de 65535)
    return max(0.0, min(minimo, sensor))


def leer_balance_luz_dia():
    try:
        with open(RUTA_BALANCE, encoding="utf-8") as f:
            d = json.load(f)
        return {"r": float(d["r"]), "b": float(d["b"]), "origen": d.get("origen", "memoria"), "fecha": d.get("fecha", "")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _escribir_balance(d):
    try:
        os.makedirs(os.path.dirname(RUTA_BALANCE), exist_ok=True)
        with open(RUTA_BALANCE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        return True
    except OSError as e:
        log.warning("No se pudo guardar el balance de luz de día: %s", e)
        return False


def guardar_balance_luz_dia(balance_luna):
    """
    Guarda como balance de luz de día el que deja gris la Luna, corregido por su color.
    Nunca pisa una calibración con un blanco de referencia, que es más fiable.
    """
    anterior = leer_balance_luz_dia()
    if anterior and anterior.get("origen") == ORIGEN_BLANCO:
        return
    d = {"r": round(balance_luna["r"] * LUNA_A_LUZ_DE_DIA["r"], 4), "b": round(balance_luna["b"] * LUNA_A_LUZ_DE_DIA["b"], 4),
         "origen": "Luna", "fecha": datetime.now().isoformat(timespec="seconds")}
    if anterior and abs(anterior["r"] - d["r"]) < 0.02 * d["r"] and abs(anterior["b"] - d["b"]) < 0.02 * d["b"]:
        return          # sin cambios apreciables: no reescribir en cada foto del continuo
    if _escribir_balance(d):
        log.info("Balance de luz de día aprendido de la Luna: R×%.2f, B×%.2f", d["r"], d["b"])


def olvidar_balance_luz_dia():
    """Borra el balance guardado (calibrado o aprendido): se vuelve al típico hasta la próxima Luna o blanco."""
    try:
        os.remove(RUTA_BALANCE)
        log.info("Balance de luz de día olvidado: se vuelve al típico")
        return True
    except FileNotFoundError:
        return True
    except OSError as e:
        log.warning("No se pudo borrar %s: %s", RUTA_BALANCE, e)
        return False


def balance_luz_dia():
    b = leer_balance_luz_dia()
    return b if b else dict(BALANCE_DEFECTO, origen="típico")


# ─────────────────────────────────────────────────────────────────────────────
#  Calibración del color con algo blanco (pantalla Calibración)
# ─────────────────────────────────────────────────────────────────────────────
# Foto de algo blanco iluminado por el Sol directo (pared soleada, un folio al sol
# delante del objetivo; da igual que salga desenfocado): lo que haga falta para
# dejarlo gris es el balance de luz de día de esta cámara con este tubo. Manda sobre
# el aprendido de la Luna, que es algo rojiza y cambia con la altura.
ORIGEN_BLANCO = "referencia blanca"
NIVEL_BLANCO = 0.6                 # la zona central se lleva al 60 % del blanco del sensor
ZONA_BLANCO = 0.5                  # se mide la mitad central del campo
MAX_QUEMADO_BLANCO = 0.01          # como mucho un 1 % de la zona quemada
MIN_NIVEL_BLANCO = 0.08
MAX_DISPERSION_BLANCO = 0.08       # variación del color entre trozos de la zona (8 %)
RUTA_VISTA_BLANCO = os.path.join("static", "balance_blanco.jpg")


class BlancoNoValido(Exception):
    """La foto no sirve como blanco de referencia; el texto dice por qué y qué hacer."""


def medir_balance_blanco(frame, patron, negro, blanco=65535.0):
    """
    Ganancias de R y B (respecto a G) que dejan gris la zona central de la foto de un
    objeto blanco. Lanza BlancoNoValido si la zona está quemada, muy oscura o no es de
    un solo color. Devuelve {"r", "b", "nivel", "dispersion"}.
    """
    r, g, b = (c - negro for c in _submuestras(recortar_centro(frame, ZONA_BLANCO), patron))
    util = blanco - negro
    quemado = float(np.mean(np.maximum(np.maximum(r, g), b) >= 0.95 * util))
    if quemado > MAX_QUEMADO_BLANCO:
        raise BlancoNoValido(f"El blanco sale quemado en un {100 * quemado:.0f} % de la zona central: "
                             "ni con la exposición mínima baja; apunta a algo menos brillante o sin reflejos.")
    nivel = float(np.median(g)) / util
    if nivel < MIN_NIVEL_BLANCO:
        raise BlancoNoValido(f"La zona central está muy oscura ({100 * nivel:.0f} %): el blanco tiene que "
                             "estar al Sol y llenar el centro de la foto.")
    # Color por trozos (6×6): si cambia mucho, en el centro hay más cosas que el blanco
    h, w = g.shape
    gr, gb = [], []
    for i in range(6):
        for j in range(6):
            sl = (slice(i * h // 6, (i + 1) * h // 6), slice(j * w // 6, (j + 1) * w // 6))
            mr, mg, mb = float(np.mean(r[sl])), float(np.mean(g[sl])), float(np.mean(b[sl]))
            if min(mr, mg, mb) > 0:
                gr.append(mg / mr)
                gb.append(mg / mb)
    if len(gr) < 20:
        raise BlancoNoValido("No se ha podido medir el color de la zona central.")
    dispersion = max(float(np.std(gr) / np.mean(gr)), float(np.std(gb) / np.mean(gb)))
    if dispersion > MAX_DISPERSION_BLANCO:
        raise BlancoNoValido(f"El color del centro no es uniforme (varía un {100 * dispersion:.0f} %): "
                             "acerca o encuadra el blanco para que llene todo el centro de la foto.")
    ganancia_r = float(np.mean(g)) / float(np.mean(r))
    ganancia_b = float(np.mean(g)) / float(np.mean(b))
    if not (0.3 <= ganancia_r <= 6.0 and 0.3 <= ganancia_b <= 6.0):
        raise BlancoNoValido(f"Balance fuera de lo razonable (R×{ganancia_r:.2f}, B×{ganancia_b:.2f}): "
                             "¿es blanco de verdad y está al Sol?")
    return {"r": ganancia_r, "b": ganancia_b, "nivel": nivel, "dispersion": dispersion}


def guardar_balance_blanco(medido):
    d = {"r": round(medido["r"], 4), "b": round(medido["b"], 4), "origen": ORIGEN_BLANCO,
         "fecha": datetime.now().isoformat(timespec="seconds"),
         "nivel": round(medido["nivel"], 3), "dispersion": round(medido["dispersion"], 4)}
    if not _escribir_balance(d):
        raise OSError("No se pudo guardar el balance en " + RUTA_BALANCE)
    log.info("Balance de luz de día calibrado con un blanco: R×%.2f, B×%.2f (nivel %.0f %%, color uniforme al %.1f %%)",
             d["r"], d["b"], 100 * medido["nivel"], 100 * medido["dispersion"])
    return d


class TrabajoBalanceBlanco:
    """Foto del blanco con exposición automática, medida y guardado, en un hilo; la web sondea estado()."""
    def __init__(self):
        self._lock = threading.Lock()
        self.version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.mensaje = ""
        self.error = ""
        self.resultado = None

    def estado(self):
        with self._lock:
            return {"en_curso": self.en_curso, "terminado": self.terminado, "mensaje": self.mensaje,
                    "error": self.error, "resultado": dict(self.resultado) if self.resultado else None,
                    "version": self.version, "captura": estado_captura.como_dict()}

    def _msg(self, texto):
        with self._lock:
            self.mensaje = texto
        log.info(texto)

    def iniciar(self, camara, binning, ruta_vista=RUTA_VISTA_BLANCO):
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya se está calibrando el color")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self.en_curso = True
            self.mensaje = "Preparando la cámara..."
        hilo = threading.Thread(target=self._ejecutar, name="balance-blanco", daemon=True, args=(camara, binning, ruta_vista))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, binning, ruta_vista):
        conf = dict(OBJETIVOS["paisaje"], nivel=NIVEL_BLANCO)
        error = ""
        try:
            def _sesion(cam):
                prep = preparar_camara(cam, conf)
                try:
                    frame, exposicion, nivel, saturada, _ = capturar_ajustando(
                        cam, conf, binning, prep, conf["inicial"], MAX_INTENTOS,
                        informar=lambda t, i=None: self._msg(t), descripcion="Blanco de referencia", campo=ZONA_BLANCO)
                    return frame, exposicion, prep
                finally:
                    restaurar_camara(cam, prep)

            frame, exposicion, prep = camara.ejecutar(_sesion, timeout=180)
            patron = _patron(prep["sensor_type"])
            if patron not in ("RGGB", "BGGR", "GRBG", "GBRG"):
                raise BlancoNoValido("La cámara no es de color: no hay balance que calibrar.")
            negro = nivel_negro(frame, prep["blanco"])
            medido = medir_balance_blanco(frame, patron, negro, prep["blanco"])
            guardado = guardar_balance_blanco(medido)
            # Vista previa pequeña con el color ya corregido, para comprobar que el blanco sale blanco
            from AutoStrechSTF import guardar_jpg_web
            imagen, _ = revelar(frame, prep["sensor_type"], "paisaje", balance=guardado, negro=negro, aprender=False)
            os.makedirs(os.path.dirname(ruta_vista) or ".", exist_ok=True)
            guardar_jpg_web(ruta_vista, imagen, ancho=600, calidad=85)
            with self._lock:
                self.resultado = dict(guardado, exposicion=exposicion)
                self.version += 1
            self._msg(f"Color calibrado: R×{guardado['r']:.2f}, B×{guardado['b']:.2f} "
                      f"(exposición {formatear_exposicion(exposicion)}, nivel {100 * medido['nivel']:.0f} %)")
        except BlancoNoValido as e:
            error = str(e)
        except CapturaCancelada:
            error = "Calibración cancelada"
        except ErrorHardware as e:
            error = f"Error de cámara: {e}"
        except Exception as e:
            log.exception("Error calibrando el color con un blanco")
            error = f"Error: {e}"
        finally:
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.error = error
                if error:
                    self.mensaje = error
            if error:
                log.warning("Calibración del color con un blanco: %s", error)


def color_sol(ruta_config="config.ini"):
    """Color con que se revela el Sol ([Interfaz] foto_dia_color_sol; por defecto amarillo)."""
    import configparser
    cfg = configparser.ConfigParser()
    try:
        cfg.read(ruta_config, encoding="utf-8")
        valor = cfg.get("Interfaz", "foto_dia_color_sol", fallback="amarillo").strip().lower()
    except (configparser.Error, OSError):
        valor = "amarillo"
    return valor if valor in COLORES_SOL else "amarillo"


def medir_balance_disco(frame, patron, negro, blanco=65535.0):
    """
    Balance (ganancias de R y B respecto a G) que deja gris el disco del Sol o de la
    Luna: media del disco menos el cielo de alrededor, en cada canal. None si en la
    foto no hay un disco claro (nublado, fuera de campo, todo quemado).
    """
    r, g, b = (c - negro for c in _submuestras(frame, patron))
    # Umbrales entre lo más oscuro (el cielo, que de día puede ser ya un 20 % del disco)
    # y lo más brillante: fijos respecto al tope dejarían el cielo claro en ninguno de los dos
    tope, bajo = float(np.percentile(g, 99.5)), float(np.percentile(g, 5))
    rango = tope - bajo
    if rango <= 0.02 * (blanco - negro):
        return None
    no_quemado = np.maximum(np.maximum(r, g), b) < 0.95 * (blanco - negro)
    disco = (g > bajo + 0.45 * rango) & no_quemado
    fondo = g < bajo + 0.15 * rango
    if int(disco.sum()) < 200:
        return None
    cielo = [float(np.median(c[fondo])) if int(fondo.sum()) > 200 else 0.0 for c in (r, g, b)]
    senal = [float(np.mean(c[disco])) - s for c, s in zip((r, g, b), cielo)]
    # El disco tiene que destacar bien sobre el cielo; si no, la resta sale a ruido
    if min(senal) <= 0 or senal[1] < 0.25 * float(np.mean(g[disco])):
        return None
    gr, gb = senal[1] / senal[0], senal[1] / senal[2]
    if not (0.3 <= gr <= 6.0 and 0.3 <= gb <= 6.0):
        return None
    return {"r": gr, "b": gb, "disco": float(disco.mean()), "fondo": float(fondo.mean())}


def balance_para(frame, sensor_type, objetivo, negro, blanco=65535.0, aprender=True):
    """Balance de blancos de una foto de día: {"r", "b", "origen"}; None para sensores sin color."""
    return balance_para_patron(frame, _patron(sensor_type), objetivo, negro, blanco, aprender)


def balance_para_patron(frame, patron, objetivo, negro, blanco=65535.0, aprender=True):
    """Como balance_para() pero con el patrón Bayer ya sabido (el SDK nativo lo da directamente)."""
    if np.asarray(frame).ndim != 2 or patron not in ("RGGB", "BGGR", "GRBG", "GBRG"):
        return None
    if objetivo == "sol":
        modo = color_sol()
        if modo == "natural":
            return dict(balance_luz_dia(), origen="Sol con el color del filtro")
        medido = medir_balance_disco(frame, patron, negro, blanco)
        if medido is not None:
            # El Sol no enseña la luz de día (su color lo pone el filtro): nunca se aprende de él
            if modo == "amarillo":
                return {"r": medido["r"] * TINTE_SOL_AMARILLO["r"], "b": medido["b"] * TINTE_SOL_AMARILLO["b"],
                        "origen": "Sol amarillo"}
            return {"r": medido["r"], "b": medido["b"], "origen": "disco de sol"}
    elif objetivo == "luna":
        medido = medir_balance_disco(frame, patron, negro, blanco)
        if medido is not None:
            if aprender and medido["disco"] <= DISCO_MAXIMO_PARA_APRENDER and medido["fondo"] >= FONDO_MINIMO_PARA_APRENDER:
                guardar_balance_luz_dia(medido)
            return {"r": medido["r"], "b": medido["b"], "origen": "disco de luna"}
    return balance_luz_dia()


def texto_balance(balance):
    if not balance:
        return ""
    return f"color: R×{balance['r']:.2f} B×{balance['b']:.2f} ({balance.get('origen', '')})"


def revelar(frame, sensor_type, objetivo=None, balance=None, negro=None, aprender=True, ev=0.0):
    """
    Foto de día a 8 bits: resta del negro del sensor, balance de blancos (ver arriba)
    y una gamma suave, sin auto-stretch (de día no hay nada que estirar).
    `ev`: compensación de la exposición automática (con_compensacion). El blanco se toma
    de lo más brillante de la foto, así que sin esto la foto se vería igual con cualquier
    exposición: con +1 EV se revela el doble de clara (se queman las luces) y con −1, la
    mitad de clara, como en una cámara de fotos.
    Devuelve (imagen BGR/gris uint8, {"negro", "balance", "ev"}).
    """
    import cv2
    datos = np.asarray(frame)
    patron = _patron(sensor_type)
    if negro is None:
        negro = nivel_negro(datos)
    if balance is None:
        balance = balance_para(datos, sensor_type, objetivo, negro, aprender=aprender)
    img = datos.astype(np.float32) - float(negro)
    if datos.ndim == 2 and patron in ("RGGB", "BGGR", "GRBG", "GBRG"):
        if balance:
            fr, cr = {"RGGB": (0, 0), "BGGR": (1, 1), "GRBG": (0, 1), "GBRG": (1, 0)}[patron]
            fb, cb = 1 - fr, 1 - cr
            img[fr::2, cr::2] *= float(balance["r"])
            img[fb::2, cb::2] *= float(balance["b"])
        # Blanco: lo más brillante de la foto ya equilibrada (0,1 % se deja quemar, como antes)
        blanco = max(float(np.percentile(img[::3, ::3], 99.9)), 1.0) / 2.0 ** float(ev or 0.0)
        cfa16 = np.clip(img * (65535.0 / blanco), 0, 65535).astype(np.uint16)
        from AutoStrechSTF import debayer_rgb
        img = debayer_rgb(cfa16, patron).astype(np.float32) / 65535.0
    else:
        blanco = max(float(np.percentile(img[::3, ::3], 99.9)), 1.0) / 2.0 ** float(ev or 0.0)
        img = img / blanco
    img = np.clip(img, 0.0, 1.0)
    img = (np.power(img, 1.0 / 1.6) * 255.0).astype(np.uint8)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img, {"negro": float(negro), "balance": balance, "ev": float(ev or 0.0)}


def _ascii_fits(v):
    """La cabecera FITS solo admite ASCII: quita tildes y eñes de los textos ("típico" → "tipico")."""
    if isinstance(v, tuple):
        return tuple(_ascii_fits(x) for x in v)
    if isinstance(v, str):
        import unicodedata
        return unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii")
    return v


def guardar_fits(frame, exposicion, prep, binning, ruta, extra=None):
    from astropy.io import fits
    hdu = fits.PrimaryHDU(np.asarray(frame, dtype=np.uint16))
    hdu.header["EXPTIME"] = (float(exposicion), "Exposicion (s)")
    hdu.header["XBINNING"] = binning
    hdu.header["YBINNING"] = binning
    hdu.header["XPIXSZ"] = (float(prep["px"]) * binning, "Tamano de pixel con binning (um)")
    hdu.header["YPIXSZ"] = (float(prep["py"]) * binning, "Tamano de pixel con binning (um)")
    patron = _patron(prep["sensor_type"])
    if patron:
        hdu.header["BAYERPAT"] = (patron, "Patron Bayer segun ASCOM SensorType")
    hdu.header["MODO"] = ("dia", "Foto de dia con exposicion automatica")
    if prep.get("ganancia") is not None:
        hdu.header["GAIN"] = (prep["ganancia"], "Ganancia de la camara")
    for k, v in (extra or {}).items():
        hdu.header[k] = _ascii_fits(v)
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    hdu.writeto(ruta, overwrite=True)


def cabecera_revelado(objetivo, campo, info, instante=None):
    """Lo que hace falta para revelar otra vez la foto igual (guardar en la galería tras reiniciar)."""
    extra = {"OBJETIVO": (objetivo or "", "Sol, luna o paisaje"),
             "CAMPO": (round(float(campo), 3), "Fraccion del campo que se ve en la web"),
             "DATE-OBS": datetime.fromtimestamp(instante or time.time()).strftime("%Y%m%d_%H%M%S"),
             "NEGRO": (round(float(info["negro"]), 1), "Nivel de negro restado al revelar"),
             "EV": (round(float(info.get("ev") or 0.0), 2), "Compensacion de exposicion al revelar (EV)")}
    b = info.get("balance")
    if b:
        extra.update({"WB_R": (round(float(b["r"]), 4), "Balance de blancos: ganancia del rojo"),
                      "WB_B": (round(float(b["b"]), 4), "Balance de blancos: ganancia del azul"),
                      "WB_ORIG": (str(b.get("origen", ""))[:60], "De donde sale el balance")})
    return extra


def registrar_vista(frame, objetivo, campo, binning, prep, exposicion, info, en_disco=False, encuadre=None, sin_girar=None):
    """
    Anota en foto_actual la foto de día que se acaba de mostrar (el crudo va en memoria si no
    está en disco), con el giro con que se enseña y la imagen revelada sin girar (giro_camara).
    """
    import foto_actual
    foto_actual.registrar("dia", frame.shape[1], frame.shape[0], binning, campo,
                          frame=None if en_disco else frame, objetivo=objetivo, balance=info.get("balance"),
                          negro=info.get("negro"), ev=info.get("ev"), exposicion=exposicion, ganancia=prep.get("ganancia"),
                          prep=prep, encuadre=encuadre, sin_girar=sin_girar)


# ─────────────────────────────────────────────────────────────────────────────
#  Foto suelta
# ─────────────────────────────────────────────────────────────────────────────
class TrabajoFotoDia:
    def __init__(self):
        self._lock = threading.Lock()
        self.version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.objetivo = ""
        self.fase = ""            # ajustando | final | guardando
        self.mensaje = ""
        self.intentos = []        # [{"exposicion", "nivel"}]
        self.exposicion = None
        self.nivel = None
        self.ganancia = None
        self.saturada = False
        self.error = ""
        self.ruta_fits = ""
        self.seguimiento = None   # velocidad puesta en la montura, si alguna

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado, "objetivo": self.objetivo,
                "fase": self.fase, "mensaje": self.mensaje, "intentos": list(self.intentos),
                "exposicion": self.exposicion, "exposicion_texto": formatear_exposicion(self.exposicion),
                "nivel": self.nivel, "ganancia": self.ganancia, "saturada": self.saturada,
                "error": self.error, "version": self.version, "seguimiento": self.seguimiento,
                "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto, intentos=None, fase=None):
        with self._lock:
            self.mensaje = texto
            if fase:
                self.fase = fase
            if intentos is not None:
                self.intentos = list(intentos)
                self.exposicion, self.nivel = intentos[-1]["exposicion"], intentos[-1]["nivel"]
        log.info(texto)

    def iniciar(self, camara, objetivo, binning, exposicion_inicial=None, montura=None,
                ruta_fits=None, ruta_web=None, al_terminar=None, voz=None, campo=1.0, compensacion=None):
        """`compensacion`: EV (número o función que lo devuelve) que corrige el nivel objetivo, ver con_compensacion()."""
        if objetivo not in OBJETIVOS:
            raise ValueError(f"Objetivo desconocido: {objetivo}")
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya hay una foto de día en curso")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self.en_curso = True
            self.objetivo = objetivo
            self.mensaje = "Preparando la cámara..."
            self.fase = "ajustando"
        hilo = threading.Thread(target=self._ejecutar, name="foto-dia", daemon=True,
                                args=(camara, objetivo, binning, exposicion_inicial, montura, ruta_fits, ruta_web, al_terminar, voz, campo, compensacion))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, objetivo, binning, exposicion_inicial, montura, ruta_fits, ruta_web, al_terminar, voz, campo=1.0, compensacion=None):
        conf = con_compensacion(OBJETIVOS[objetivo], compensacion)
        error = ""
        try:
            if montura is not None and conf["seguimiento"] and montura.esta_conectado():
                if poner_seguimiento(montura, conf["seguimiento"]):
                    with self._lock:
                        self.seguimiento = conf["seguimiento"]

            def _sesion(cam):
                prep = preparar_camara(cam, conf)
                with self._lock:
                    self.ganancia = prep["ganancia"]
                try:
                    frame, exposicion, nivel, saturada, intentos = capturar_ajustando(
                        cam, conf, binning, prep, exposicion_inicial or conf["inicial"], MAX_INTENTOS,
                        informar=lambda t, i=None: self._msg(t, i, fase="ajustando"), campo=campo)
                    return frame, exposicion, nivel, saturada, prep
                finally:
                    restaurar_camara(cam, prep)

            frame, exposicion, nivel, saturada, prep = camara.ejecutar(_sesion, timeout=180)

            self._msg("Guardando la foto...", fase="guardando")
            instante = time.time()
            imagen, info = revelar(recortar_centro(frame, campo), prep["sensor_type"], objetivo, ev=conf["ev"])
            girada, encuadre = giro_camara.girar(imagen)      # el giro de Cámara; el FITS queda sin girar
            info_enfoque = None
            if enfoque.activo() and enfoque.es_disco(objetivo):
                # Modo enfoque (enfoque.py): solo el verde, con el limbo ampliado y su anchura; la foto de color sigue en foto_actual
                try:
                    girada, encuadre, info_enfoque = enfoque.imagen_foto_dia_disco(
                        recortar_centro(frame, campo), _patron(prep["sensor_type"]), giro_camara.girar)
                    log.info("Enfoque de día: %s", enfoque.texto_info(info_enfoque))
                except Exception as e:
                    log.warning("Modo enfoque de día: %s", e)
            if ruta_fits:
                guardar_fits(frame, exposicion, prep, binning, ruta_fits, extra=cabecera_revelado(objetivo, campo, info, instante))
            if ruta_web:
                from AutoStrechSTF import guardar_jpg_web
                os.makedirs(os.path.dirname(ruta_web) or ".", exist_ok=True)
                guardar_jpg_web(ruta_web, girada)
            registrar_vista(frame, objetivo, campo, binning, prep, exposicion, info, en_disco=bool(ruta_fits),
                            encuadre=encuadre, sin_girar=imagen)
            with self._lock:
                self.version += 1
                self.ruta_fits = ruta_fits or ""
                self.exposicion, self.nivel, self.saturada = exposicion, round(nivel, 3), saturada
            texto = f"Foto de día: {formatear_exposicion(exposicion)}, nivel {100 * nivel:.0f} %"
            if conf["ev"]:
                texto += f" ({conf['ev']:+.1f} EV)"
            if info.get("balance"):
                texto += " · " + texto_balance(info["balance"])
            if saturada:
                texto += " (sigue saturada con la exposición mínima: hace falta filtro o menos luz)"
            if info_enfoque is not None:
                texto += " · " + enfoque.texto_info(info_enfoque)
            self._msg(texto, fase="final")
            if voz:
                voz.decir("Foto de día terminada")
        except CapturaCancelada:
            error = "Foto cancelada"
        except ErrorHardware as e:
            error = f"Error de cámara: {e}"
        except Exception as e:
            log.exception("Error en la foto de día")
            error = f"Error: {e}"
        finally:
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.error = error
                if error:
                    self.mensaje = error
            if al_terminar:
                try:
                    al_terminar(self.estado())
                except Exception:
                    log.exception("Error en al_terminar de la foto de día")


# ─────────────────────────────────────────────────────────────────────────────
#  Timelapse
# ─────────────────────────────────────────────────────────────────────────────
class TrabajoTimelapse:
    """
    Serie de fotos de día a intervalo fijo, cada una con la exposición reajustada
    (un eclipse oscurece el disco poco a poco). Cada toma se guarda como JPG en
    static/timelapse/<fecha>_<objetivo>/ con un fotos.csv al lado. Entre tomas la
    cámara queda libre, así la web sigue respondiendo; las capturas de noche se
    bloquean mientras dure (ScopePower.py lo comprueba).
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self.version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.objetivo = ""
        self.total = 0
        self.hechas = 0
        self.intervalo = 0.0
        self.siguiente_en = 0.0
        self.carpeta = ""
        self.mensaje = ""
        self.error = ""
        self.exposicion = None
        self.nivel = None
        self.ganancia = None
        self.ultima = None        # {"archivo", "hora", "exposicion", "nivel", "n"}
        self.seguimiento = None
        self.inicio = ""
        self.video = None         # {"en_curso", "ruta", "progreso", "error"}

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado, "objetivo": self.objetivo,
                "total": self.total, "hechas": self.hechas, "intervalo": self.intervalo,
                "siguiente_en": round(max(0.0, self.siguiente_en), 1), "carpeta": self.carpeta,
                "mensaje": self.mensaje, "error": self.error, "exposicion": self.exposicion,
                "exposicion_texto": formatear_exposicion(self.exposicion), "nivel": self.nivel,
                "ganancia": self.ganancia, "ultima": dict(self.ultima) if self.ultima else None,
                "seguimiento": self.seguimiento, "inicio": self.inicio, "version": self.version,
                "video": dict(self.video) if self.video else None,
                "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto):
        with self._lock:
            self.mensaje = texto
        log.info(texto)

    def cancelar(self):
        self._cancelar.set()
        estado_captura.cancelar.set()

    def iniciar(self, camara, objetivo, binning, total, intervalo, exposicion_inicial=None,
                montura=None, al_terminar=None, voz=None, raiz=None, campo=1.0, compensacion=None):
        """`compensacion`: EV (número o función) que corrige el nivel objetivo; si es función se relee en cada foto."""
        if objetivo not in OBJETIVOS:
            raise ValueError(f"Objetivo desconocido: {objetivo}")
        total, intervalo = int(total), float(intervalo)
        if not (1 <= total <= 5000):
            raise ValueError("El número de fotos debe estar entre 1 y 5000")
        if not (1.0 <= intervalo <= 3600.0):
            raise ValueError("El intervalo debe estar entre 1 y 3600 segundos")
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya hay un timelapse en marcha")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self._cancelar.clear()
            self.en_curso = True
            self.objetivo, self.total, self.intervalo = objetivo, total, intervalo
            self.inicio = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.carpeta = os.path.join(raiz or CARPETA_TIMELAPSE, f"{self.inicio}_{objetivo}")
            self.mensaje = "Preparando el timelapse..."
        hilo = threading.Thread(target=self._ejecutar, name="timelapse", daemon=True,
                                args=(camara, objetivo, binning, exposicion_inicial, montura, al_terminar, voz, campo, compensacion))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, objetivo, binning, exposicion_inicial, montura, al_terminar, voz, campo=1.0, compensacion=None):
        import cv2
        conf = OBJETIVOS[objetivo]
        error = ""
        prep = None
        carpeta = self.carpeta
        try:
            os.makedirs(carpeta, exist_ok=True)
            if montura is not None and conf["seguimiento"] and montura.esta_conectado():
                if poner_seguimiento(montura, conf["seguimiento"]):
                    with self._lock:
                        self.seguimiento = conf["seguimiento"]
            prep = camara.ejecutar(lambda cam: preparar_camara(cam, conf), timeout=30)
            with self._lock:
                self.ganancia = prep["ganancia"]
            if voz:
                voz.decir(f"Timelapse de {self.total} fotos cada {self.intervalo:.0f} segundos")

            exposicion = float(exposicion_inicial or conf["inicial"])
            balance_tl = None
            registro = open(os.path.join(carpeta, "fotos.csv"), "w", encoding="utf-8", newline="")
            escritor = csv.writer(registro, delimiter=";")
            escritor.writerow(["n", "hora", "exposicion_s", "nivel", "archivo"])
            try:
                for n in range(1, self.total + 1):
                    if self._cancelar.is_set():
                        raise CapturaCancelada("Timelapse cancelado")
                    t0 = time.time()
                    # Primera toma: búsqueda completa. Siguientes: la exposición anterior y como
                    # mucho dos correcciones, para no perder la cadencia
                    max_intentos = MAX_INTENTOS if n == 1 else 3
                    conf_n = con_compensacion(conf, compensacion)     # la compensación vale desde la siguiente foto
                    frame, exposicion, nivel, saturada, _ = camara.ejecutar(
                        lambda cam: capturar_ajustando(cam, conf_n, binning, prep, exposicion, max_intentos,
                                                       informar=lambda t, i=None: self._msg(f"Foto {n}/{self.total} · {t}"),
                                                       descripcion=f"Timelapse {n}", campo=campo),
                        timeout=180)
                    archivo = f"foto_{n:04d}.jpg"
                    # El color se mide en la primera toma y se mantiene: sin parpadeos de una foto a otra
                    imagen, info = revelar(recortar_centro(frame, campo), prep["sensor_type"], objetivo, balance=balance_tl,
                                           ev=conf_n["ev"])
                    balance_tl = info["balance"]
                    sin_girar = imagen
                    imagen, encuadre = giro_camara.girar(imagen)      # el giro de Cámara, también en el vídeo
                    registrar_vista(frame, objetivo, campo, binning, prep, exposicion, info, encuadre=encuadre, sin_girar=sin_girar)
                    cv2.imwrite(os.path.join(carpeta, archivo), imagen, [cv2.IMWRITE_JPEG_QUALITY, 92])
                    # Miniatura de la última foto para la lista de timelapses de la web
                    from AutoStrechSTF import guardar_jpg_web
                    guardar_jpg_web(os.path.join(carpeta, MINIATURA_TIMELAPSE), imagen, ancho=ANCHO_MINIATURA_TIMELAPSE, calidad=80)
                    hora = datetime.now().strftime("%H:%M:%S")
                    escritor.writerow([n, hora, f"{exposicion:.6f}", f"{nivel:.3f}", archivo])
                    registro.flush()
                    with self._lock:
                        self.hechas = n
                        self.exposicion, self.nivel = exposicion, round(nivel, 3)
                        self.ultima = {"archivo": archivo, "hora": hora, "exposicion": exposicion, "nivel": round(nivel, 3), "n": n}
                        self.version += 1
                    self._msg(f"Foto {n}/{self.total}: {formatear_exposicion(exposicion)}, nivel {100 * nivel:.0f} %"
                              + (" (saturada)" if saturada else ""))
                    if n == self.total:
                        break
                    # Esperar al siguiente disparo (contando desde el inicio de este)
                    while True:
                        restante = self.intervalo - (time.time() - t0)
                        with self._lock:
                            self.siguiente_en = restante
                        if restante <= 0:
                            break
                        if self._cancelar.wait(timeout=min(0.5, restante)):
                            raise CapturaCancelada("Timelapse cancelado")
            finally:
                registro.close()
            self._msg(f"Timelapse terminado: {self.hechas} fotos en {os.path.basename(carpeta)}")
            if voz:
                voz.decir("Timelapse terminado")
        except CapturaCancelada:
            error = f"Timelapse cancelado tras {self.hechas} fotos"
        except ErrorHardware as e:
            error = f"Error de cámara: {e}"
        except Exception as e:
            log.exception("Error en el timelapse")
            error = f"Error: {e}"
        finally:
            if prep is not None:
                try:
                    camara.ejecutar(lambda cam: restaurar_camara(cam, prep), timeout=30)
                except Exception:
                    pass
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.error = error
                self.siguiente_en = 0.0
                if error:
                    self.mensaje = error
            if al_terminar:
                try:
                    al_terminar(self.estado())
                except Exception:
                    log.exception("Error en al_terminar del timelapse")

    # ── vídeo ──────────────────────────────────────────────────────────
    def montar_video(self, carpeta, fps=25, ancho=1920):
        """Junta los JPG de `carpeta` en un MP4 (en un hilo). El estado va en estado()["video"]."""
        with self._lock:
            if self.video and self.video.get("en_curso"):
                raise RuntimeError("Ya se está montando un vídeo")
            self.video = {"en_curso": True, "ruta": "", "progreso": 0, "error": "", "carpeta": carpeta}
        threading.Thread(target=self._montar, name="timelapse-video", daemon=True, args=(carpeta, int(fps), int(ancho))).start()

    def _montar(self, carpeta, fps, ancho):
        import cv2
        error, ruta = "", ""
        try:
            fotos = fotos_timelapse(carpeta)
            if len(fotos) < 2:
                raise RuntimeError("Hacen falta al menos dos fotos para montar un vídeo")
            primera = cv2.imread(os.path.join(carpeta, fotos[0]))
            alto = int(primera.shape[0] * ancho / primera.shape[1])
            alto -= alto % 2
            ruta = os.path.join(carpeta, "timelapse.mp4")
            salida = cv2.VideoWriter(ruta, cv2.VideoWriter_fourcc(*"mp4v"), fps, (ancho, alto))
            if not salida.isOpened():
                raise RuntimeError("No se pudo crear el fichero de vídeo")
            for i, f in enumerate(fotos, 1):
                img = cv2.imread(os.path.join(carpeta, f))
                if img is None:
                    continue
                salida.write(cv2.resize(img, (ancho, alto), interpolation=cv2.INTER_AREA))
                with self._lock:
                    self.video["progreso"] = int(100 * i / len(fotos))
            salida.release()
            log.info("Vídeo montado: %s (%d fotos, %d fps)", ruta, len(fotos), fps)
        except Exception as e:
            log.exception("Error montando el vídeo del timelapse")
            error = str(e)
        finally:
            with self._lock:
                self.video.update({"en_curso": False, "ruta": ruta if not error else "", "error": error, "progreso": 100 if not error else 0})


MINIATURA_TIMELAPSE = "miniatura.jpg"
ANCHO_MINIATURA_TIMELAPSE = 160


def fotos_timelapse(carpeta):
    """Fotogramas de un timelapse en orden (foto_0001.jpg, ...), sin la miniatura."""
    return sorted(f for f in os.listdir(carpeta) if f.lower().startswith("foto_") and f.lower().endswith(".jpg"))


def listar_timelapses(raiz=None, maximo=10):
    """
    Carpetas de timelapse con su número de fotos y si ya tienen vídeo, las más
    recientes primero. `miniatura` es la imagen pequeña para la lista (la última
    foto entera pesa varios MB); los timelapses antiguos no la tienen.
    """
    raiz = raiz or CARPETA_TIMELAPSE
    salida = []
    if not os.path.isdir(raiz):
        return salida
    for nombre in sorted(os.listdir(raiz), reverse=True)[:maximo]:
        ruta = os.path.join(raiz, nombre)
        if not os.path.isdir(ruta):
            continue
        fotos = fotos_timelapse(ruta)
        salida.append({"carpeta": nombre, "fotos": len(fotos), "primera": fotos[0] if fotos else None,
                       "ultima": fotos[-1] if fotos else None,
                       "miniatura": MINIATURA_TIMELAPSE if os.path.isfile(os.path.join(ruta, MINIATURA_TIMELAPSE)) else None,
                       "video": os.path.isfile(os.path.join(ruta, "timelapse.mp4"))})
    return salida


trabajo_foto_dia = TrabajoFotoDia()
trabajo_balance_blanco = TrabajoBalanceBlanco()
trabajo_timelapse = TrabajoTimelapse()
