# seguimiento.py
#
# Seguimiento experimental de objetos en movimiento (aviones, satélites, la
# ISS) con la cámara como "visión": fotogramas rápidos por el SDK nativo
# (camara_nativa.py), detección de la mancha brillante, y corrección continua
# de la velocidad de los ejes de la montura (montura_ejes.py).
#
# Cómo funciona el bucle (varias veces por segundo):
#   1. leer un fotograma del ROI (bin 2, RAW8, exposición de milisegundos),
#   2. detectar las manchas (umbral mediana + k·MAD y componentes conexas con
#      centroides; lo mismo que apilado.calcular_puntuacion pero devolviendo
#      dónde está cada mancha),
#   3. elegir el objetivo (la más cercana a la posición anterior, o la más
#      brillante / la que el usuario ha pulsado en la vista previa),
#   4. error en píxeles respecto al centro → error en grados de eje con la
#      matriz de calibración → controlador PI → velocidades de eje.
#
# La calibración píxel → ejes tiene dos vías:
#   - por astrometría (la normal): cualquier plate solving de cualquier noche
#     (centrado, alineación, apilado, foto de prueba) deja guardada la
#     orientación de la cámara respecto a los ejes de la montura
#     (orientacion_camara.py). En una altacimutal esa orientación es fija
#     mientras no se toque la cámara, así que no hace falta mover nada: se usa
#     la última. Verificado con 9 resoluciones reales de una noche (±2°).
#   - empírica: mover cada eje y medir el desplazamiento de una luz fija. Sirve
#     de comprobación; si es más reciente que la astrometría, manda ella.
#
# El controlador es PI sobre velocidad de eje: el término integral converge a
# la velocidad angular del objeto. Como a 1 °/s el objeto cruza un ROI de 1,7°
# en menos de 2 s, antes de mover se mide su velocidad aparente en 2–3
# fotogramas y se arranca la integral con ella (bootstrap); el PI solo corrige
# el residuo.
#
# Todo el trabajo corre en un hilo (TrabajoSeguimiento, patrón de
# foto_continua.TrabajoContinuo) y la web sondea estado() y recarga una vista
# previa JPG pequeña. Mientras dura, la cámara ASCOM de la app se desconecta
# (el driver Player One solo admite una conexión) y se reconecta al acabar.

import os
import json
import math
import time
import logging
import threading
from datetime import datetime

import numpy as np
import cv2

log = logging.getLogger("scopepower.seguimiento")

RUTA_CALIBRACION = os.path.join("datos", "calibracion_seguimiento.json")
CARPETA_VISTA = os.path.join("static", "seguimiento")
RUTA_VISTA = os.path.join(CARPETA_VISTA, "vista.jpg")

CONF_POR_DEFECTO = {
    "backend": "ascom", "simulador": False, "udp_host": "192.168.4.1", "udp_puerto": 11880,
    # Campo: roi_px en píxeles del sensor (3008 = sensor entero, 2,5° con el Evoguide 50; 1024 = 0,85°)
    "roi_px": 3008, "binning": 2, "formato": "RAW8", "exposicion_ms": 5.0, "ganancia": 300,
    "fps_camara": 15.0, "ventana_v_s": 0.4, "frecuencia_hz": 4.0, "kp": 0.6, "ki": 0.3, "v_max": 4.0, "perdida_s": 3.0,
    "intervalo_ascom_s": 0.4, "banda_muerta": 0.02, "redondeo_ascom": None,
    "vista_previa_px": 400, "vista_previa_hz": 2.0,
    # Grabación de los fotogramas en el disco del mini PC (grabador_video.py)
    "grabar": False, "formato_video": "ser", "grabar_desde": "bloqueo", "carpeta_video": "videos",
    # Lado del recorte que se graba, en píxeles del fotograma, centrado (el objeto seguido está en el
    # centro); 0 = el fotograma entero. Con el campo amplio el fotograma entero son ~2 GB por minuto.
    "video_lado": 640,
    # Vídeo en color: binning 1 (el mismo campo, pero fotogramas cuatro veces mayores)
    "color": False,
    # Exposición automática guiada por el objeto: el pico de la mancha seguida se mantiene en torno a
    # pico_objetivo (fracción de la escala) tocando solo la exposición, entre los límites en ms
    "exposicion_auto": True, "exposicion_min_ms": 0.1, "exposicion_max_ms": 50.0, "pico_objetivo": 0.7,
    "k_mad": 6.0, "area_min": 3, "area_max": 20000, "radio_px": 80, "frames_bootstrap": 3, "v_min_px_s": 3.0,
    # Contraste mínimo del pico sobre el fondo (de 255) para que una mancha cuente: el ruido no llega;
    # un avión, un satélite o una estrella brillante sí
    "contraste_min": 20.0,
    # Altura mínima para seguir (grados); vacía = sin límite (objetivos terrestres, luces lejanas)
    "altura_minima_seguimiento": None, "focal_mm": 260.0, "pixel_um": 3.76,
    # Solo con simulador = true: objeto simulado (°/s y rumbo), giro real de la cámara y retardo de la montura
    "sim_v_obj": 0.6, "sim_rumbo": 20.0, "sim_angulo": 25.0, "sim_retardo_s": 0.3,
}


def leer_conf(config, focal_mm=None, pixel_um=None):
    """Sección [Seguimiento] de un ConfigParser (o dict) sobre los valores por defecto."""
    conf = dict(CONF_POR_DEFECTO)
    try:
        if hasattr(config, "has_section") and config.has_section("Seguimiento"):
            for k, v in config.items("Seguimiento"):
                conf[k] = v
        elif isinstance(config, dict):
            conf.update(config)
    except Exception as e:
        log.warning("No se pudo leer [Seguimiento]: %s", e)
    for k in ("roi_px", "binning", "ganancia", "area_min", "area_max", "radio_px", "frames_bootstrap", "udp_puerto",
              "vista_previa_px", "video_lado"):
        conf[k] = int(float(conf[k]))
    for k in ("exposicion_ms", "fps_camara", "ventana_v_s", "frecuencia_hz", "kp", "ki", "v_max", "perdida_s", "intervalo_ascom_s", "banda_muerta",
              "vista_previa_hz", "k_mad", "focal_mm", "pixel_um", "v_min_px_s", "contraste_min",
              "exposicion_min_ms", "exposicion_max_ms", "pico_objetivo",
              "sim_v_obj", "sim_rumbo", "sim_angulo", "sim_retardo_s"):
        conf[k] = float(conf[k])
    conf["simulador"] = str(conf["simulador"]).lower() in ("1", "true", "si", "sí", "yes")
    conf["grabar"] = str(conf["grabar"]).lower() in ("1", "true", "si", "sí", "yes")
    conf["color"] = str(conf["color"]).lower() in ("1", "true", "si", "sí", "yes")
    conf["exposicion_auto"] = str(conf["exposicion_auto"]).lower() in ("1", "true", "si", "sí", "yes")
    if conf["color"]:
        conf["binning"] = 1
    conf["formato_video"] = str(conf["formato_video"]).lower()
    conf["grabar_desde"] = "inicio" if str(conf["grabar_desde"]).lower() == "inicio" else "bloqueo"
    if conf["redondeo_ascom"] in ("", "0", "None", "none"):
        conf["redondeo_ascom"] = None
    try:
        am = conf.get("altura_minima_seguimiento")
        conf["altura_minima_seguimiento"] = None if am in (None, "", "none", "None") else float(am)
    except (TypeError, ValueError):
        conf["altura_minima_seguimiento"] = None
    if focal_mm is not None:
        conf["focal_mm"] = float(focal_mm)
    if pixel_um is not None:
        conf["pixel_um"] = float(pixel_um)
    return conf


# ═════════════════════════════════════════════════════════════════════════════
# Detección
# ═════════════════════════════════════════════════════════════════════════════
def detectar_manchas(plano, k_mad=6.0, area_min=3, area_max=20000, maximo=20, contraste_min=0.0):
    """
    Manchas brillantes de un plano uint8/uint16: umbral mediana + k·MAD y
    componentes conexas. Devuelve una lista (ordenada por flujo, mayor primero)
    de dicts {x, y, area, pico, flujo, saturada, w, h}, con x/y centroides en
    píxeles del plano. `contraste_min` (unidades del plano) descarta las manchas
    cuyo pico no supere el fondo al menos en eso: en un fotograma de puro ruido
    el umbral estadístico deja pasar granos y esto los quita.
    """
    img = np.asarray(plano)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    f = img.astype(np.float32)
    muestra = f[::2, ::2] if f.size > 200_000 else f
    mediana = float(np.median(muestra))
    mad = float(np.median(np.abs(muestra - mediana))) * 1.4826
    umbral = mediana + k_mad * max(mad, 1.0)
    mascara = (f > umbral).astype(np.uint8)
    n, etiquetas, stats, centroides = cv2.connectedComponentsWithStats(mascara, connectivity=8)
    if n <= 1:
        return []
    tope = 255 if img.dtype == np.uint8 else int(img.max())
    areas = stats[1:, cv2.CC_STAT_AREA]
    validas = np.where((areas >= area_min) & (areas <= area_max))[0] + 1
    if len(validas) == 0:
        return []
    resta = (f - mediana).ravel()
    ids = etiquetas.ravel()
    flujo = np.bincount(ids, weights=resta, minlength=n)
    pico = np.zeros(n, dtype=np.float32)
    np.maximum.at(pico, ids, resta.astype(np.float32))
    manchas = []
    for i in validas:
        if pico[i] < contraste_min:
            continue
        manchas.append({
            "x": float(centroides[i][0]), "y": float(centroides[i][1]),
            "area": int(stats[i, cv2.CC_STAT_AREA]), "w": int(stats[i, cv2.CC_STAT_WIDTH]),
            "h": int(stats[i, cv2.CC_STAT_HEIGHT]),
            "pico": float(pico[i] + mediana), "flujo": float(flujo[i]),
            "saturada": bool(pico[i] + mediana >= tope * 0.98),
        })
    manchas.sort(key=lambda m: -m["flujo"])
    return manchas[:maximo]


def movimiento_coherente(historia, tolerancia_px=6.0):
    """
    True si las posiciones (t, x, y) siguen aproximadamente una recta uniforme:
    el residuo de cada punto respecto al movimiento rectilíneo entre el primero
    y el último queda por debajo de `tolerancia_px`. El ruido salta al azar y
    falla; un objeto real (o una mancha quieta) pasa.
    """
    if len(historia) < 3:
        return True
    (t0, x0, y0), (t1, x1, y1) = historia[0], historia[-1]
    if t1 - t0 <= 0:
        return False
    vx, vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
    for t, x, y in historia[1:-1]:
        ex, ey = x0 + vx * (t - t0), y0 + vy * (t - t0)
        if math.hypot(x - ex, y - ey) > tolerancia_px:
            return False
    return True


def elegir_objetivo(manchas, anterior_xy=None, radio_px=80.0):
    """La mancha más cercana a la posición anterior dentro del radio; sin anterior, la más brillante."""
    if not manchas:
        return None
    if anterior_xy is None:
        return manchas[0]
    ax, ay = anterior_xy
    mejor, d_mejor = None, radio_px
    for m in manchas:
        d = math.hypot(m["x"] - ax, m["y"] - ay)
        if d <= d_mejor:
            mejor, d_mejor = m, d
    return mejor


# ═════════════════════════════════════════════════════════════════════════════
# Calibración píxel → ejes
# ═════════════════════════════════════════════════════════════════════════════
def escala_arcsec_px(pixel_um, binning, focal_mm):
    return 206.265 * float(pixel_um) * int(binning) / max(1.0, float(focal_mm))


class Calibracion:
    """
    Relación entre el desplazamiento de la imagen (px/s del plano de detección)
    y la velocidad de los ejes (°/s). `matriz(alt)` devuelve M (2×2) tal que
    v_px = M · v_ejes; el controlador usa M⁻¹.

    Por astrometría (origen "astrometria"): Hs = grados del plano tangente
    horizontal (Δaz·cos alt, Δalt) por píxel del sensor en los ejes de la imagen
    del SDK (orientacion_camara.matriz_sensor_sdk); con el binning del momento,
    M = (Hs·factor)⁻¹·diag(cos alt, 1)·diag(signos de los motores).
    Empírica: matriz medida moviendo los ejes (ya lleva los signos de la montura).
    Analítica (solo el simulador): M = (3600/escala)·S·R(θ)·diag(cos alt, 1).
    signo_az / signo_alt: inversión manual desde la página, encima de todo.
    """

    def __init__(self, escala=3.0, angulo=0.0, espejo=1, matriz_empirica=None, signo_az=1, signo_alt=1,
                 alt_empirica=None, origen="", fecha="", backend="", Hs=None, factor_medida=None):
        self.escala = float(escala)             # "/px del plano en que se detecta
        self.angulo = float(angulo) % 360.0     # θ (grados)
        self.espejo = 1 if espejo >= 0 else -1
        self.matriz_empirica = None if matriz_empirica is None else np.asarray(matriz_empirica, dtype=float)
        self.signo_az, self.signo_alt = int(signo_az), int(signo_alt)
        self.alt_empirica = alt_empirica
        self.origen, self.fecha, self.backend = origen, fecha, backend
        self.Hs = None if Hs is None else np.asarray(Hs, dtype=float)
        self.factor_medida = factor_medida       # píxeles del sensor por píxel del plano al medir la empírica
        self.factor_plano = 4.0                  # binning 2 × media resolución, hasta preparar()
        self.signos_cielo = (1, 1)

    @classmethod
    def desde_orientacion(cls, orientacion, sdk=None):
        """Calibración por astrometría a partir de la orientación guardada (orientacion_camara)."""
        import orientacion_camara
        sdk = sdk or orientacion_camara.leer_sdk()
        cal = cls(escala=orientacion.get("escala_arcsec", 3.0), angulo=orientacion.get("angulo_x_deg", 0.0),
                  espejo=orientacion.get("paridad", 1), origen="astrometria", fecha=orientacion.get("fecha", ""),
                  Hs=orientacion_camara.matriz_sensor_sdk(orientacion, sdk))
        cal.detalle = {"resolucion": orientacion.get("origen"), "instante_utc": orientacion.get("instante_utc"),
                       "alt": orientacion.get("alt"), "az": orientacion.get("az"),
                       "sdk": sdk.get("transformacion"), "sdk_verificada": bool(sdk.get("verificada"))}
        return cal

    def preparar(self, binning, signos_cielo=(1, 1)):
        """Ajusta al binning del seguimiento y al sentido de giro de los motores del backend."""
        self.factor_plano = 2.0 * max(1, int(binning))
        self.signos_cielo = tuple(int(x) for x in signos_cielo)

    # ── construcción ─────────────────────────────────────────────────────
    @classmethod
    def analitica(cls, escala, angulo, espejo=1, origen="analitica", **kw):
        return cls(escala=escala, angulo=angulo, espejo=espejo, origen=origen,
                   fecha=datetime.now().isoformat(timespec="seconds"), **kw)

    @classmethod
    def empirica(cls, medidas, escala=None, alt=None, backend="", factor=None):
        """
        medidas = [(d_az, d_alt, dx_px, dy_px), ...] (grados de eje mandados y
        píxeles desplazados, o velocidades) → matriz 2×2 por mínimos cuadrados con
        d_px = M · d_ejes. Con dos medidas (una por eje) es exacta.
        """
        A = np.array([[m[0], m[1]] for m in medidas], dtype=float)   # n×2 velocidades de eje
        B = np.array([[m[2], m[3]] for m in medidas], dtype=float)   # n×2 velocidades en px
        M_t, *_ = np.linalg.lstsq(A, B, rcond=None)                  # B ≈ A · M_t
        M = M_t.T
        if abs(np.linalg.det(M)) < 1e-6:
            raise ValueError("Las medidas no permiten calibrar (ejes sin desplazamiento apreciable)")
        esc = escala
        if esc is None:
            esc = 3600.0 / math.sqrt(abs(np.linalg.det(M)))     # "/px medio
        cal = cls(escala=esc, matriz_empirica=M, alt_empirica=alt, origen="empirica",
                  fecha=datetime.now().isoformat(timespec="seconds"), backend=backend, factor_medida=factor)
        # Ángulo y espejo aproximados a partir de M (informativo)
        cal.angulo = math.degrees(math.atan2(M[1][0], M[0][0])) % 360.0
        cal.espejo = 1 if np.linalg.det(M) > 0 else -1
        return cal

    # ── uso ──────────────────────────────────────────────────────────────
    def matriz(self, alt_deg=None, usar_empirica=None):
        """M (2×2): v_px = M · (v_az, v_alt)."""
        if usar_empirica is None:
            usar_empirica = self.matriz_empirica is not None and self.origen == "empirica"
        cos_alt = max(0.05, math.cos(math.radians(alt_deg))) if alt_deg is not None else 1.0
        if self.Hs is not None and not usar_empirica:
            return np.linalg.inv(self.Hs * self.factor_plano) @ np.diag([cos_alt, 1.0])
        if usar_empirica and self.matriz_empirica is not None:
            M = self.matriz_empirica.copy()
            if self.factor_medida:
                M *= float(self.factor_medida) / self.factor_plano     # otro binning: otro tamaño de píxel del plano
            if alt_deg is not None and self.alt_empirica is not None:
                ca, ce = math.cos(math.radians(alt_deg)), math.cos(math.radians(self.alt_empirica))
                if ce > 0.05:
                    M[:, 0] *= ca / ce
            return M
        th = math.radians(self.angulo)
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        S = np.array([[1.0, 0.0], [0.0, float(self.espejo)]])
        return (3600.0 / self.escala) * S @ R @ np.diag([cos_alt, 1.0])

    def matriz_con_signos(self, alt_deg=None):
        sc = self.signos_cielo if self.Hs is not None and self.origen != "empirica" else (1, 1)
        return self.matriz(alt_deg) @ np.diag([self.signo_az * sc[0], self.signo_alt * sc[1]])

    def px_a_ejes(self, dx_px, dy_px, alt_deg=None):
        """Desplazamiento en píxeles → grados de cada eje (aplicando los signos)."""
        Minv = np.linalg.inv(self.matriz_con_signos(alt_deg))
        return tuple(float(v) for v in Minv @ np.array([dx_px, dy_px], dtype=float))

    # ── persistencia ─────────────────────────────────────────────────────
    def como_dict(self):
        return {"escala": self.escala, "angulo": self.angulo, "espejo": self.espejo,
                "matriz_empirica": None if self.matriz_empirica is None else self.matriz_empirica.tolist(),
                "signo_az": self.signo_az, "signo_alt": self.signo_alt, "alt_empirica": self.alt_empirica,
                "origen": self.origen, "fecha": self.fecha, "backend": self.backend,
                "factor_medida": self.factor_medida,
                "Hs": None if self.Hs is None else self.Hs.tolist(),
                "signos_cielo": list(self.signos_cielo),
                "detalle": getattr(self, "detalle", None)}

    def guardar(self, ruta=RUTA_CALIBRACION):
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(self.como_dict(), f, ensure_ascii=False, indent=1)
        return ruta

    @classmethod
    def leer(cls, ruta=RUTA_CALIBRACION):
        """La calibración empírica guardada (las antiguas "astap" de la fórmula anterior se ignoran)."""
        try:
            with open(ruta, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        if d.get("origen") != "empirica" or not d.get("matriz_empirica"):
            return None
        detalle = d.pop("detalle", None)
        cal = cls(**{k: v for k, v in d.items() if k in ("escala", "angulo", "espejo", "matriz_empirica",
                                                       "signo_az", "signo_alt", "alt_empirica", "origen", "fecha",
                                                       "backend", "factor_medida")})
        if detalle:
            cal.detalle = detalle
        return cal


RUTA_SIGNOS_MANUALES = os.path.join("datos", "signos_manuales_seguimiento.json")


def signos_manuales():
    """Inversión manual de cada eje (botones de la página): {"az": ±1, "alt": ±1}."""
    try:
        with open(RUTA_SIGNOS_MANUALES, encoding="utf-8") as f:
            d = json.load(f)
        return {"az": -1 if d.get("az") == -1 else 1, "alt": -1 if d.get("alt") == -1 else 1}
    except (OSError, ValueError):
        return {"az": 1, "alt": 1}


def guardar_signos_manuales(az, alt):
    os.makedirs(os.path.dirname(RUTA_SIGNOS_MANUALES), exist_ok=True)
    with open(RUTA_SIGNOS_MANUALES, "w", encoding="utf-8") as f:
        json.dump({"az": -1 if az == -1 else 1, "alt": -1 if alt == -1 else 1}, f)


def elegir_calibracion(conf=None):
    """
    La calibración que se usa al iniciar: la más reciente entre la última
    astrometría (cualquier plate solving) y la empírica; si no hay ninguna, una
    teórica con ángulo 0 (origen "por_defecto", que la página señala).
    """
    import orientacion_camara
    emp = Calibracion.leer()
    ori = orientacion_camara.ultima()
    ast = Calibracion.desde_orientacion(ori) if ori else None
    cal = None
    if emp and ast:
        cal = emp if str(emp.fecha) > str(ast.fecha) else ast
    else:
        cal = emp or ast
    if cal is None:
        conf = conf or CONF_POR_DEFECTO
        cal = Calibracion(escala=escala_arcsec_px(conf["pixel_um"], conf["binning"], conf["focal_mm"]) * 2,
                          origen="por_defecto")
    sm = signos_manuales()
    cal.signo_az, cal.signo_alt = sm["az"], sm["alt"]
    return cal


# ═════════════════════════════════════════════════════════════════════════════
# Controlador
# ═════════════════════════════════════════════════════════════════════════════
class ControladorPI:
    """v = kp·e + I ; I += ki·e·dt, con anti-windup al recortar a ±v_max."""

    def __init__(self, kp=0.4, ki=0.15, v_max=4.0, dt_max=0.6):
        self.kp, self.ki, self.v_max, self.dt_max = float(kp), float(ki), float(v_max), float(dt_max)
        self.I = np.zeros(2)
        self.v = np.zeros(2)

    def arrancar(self, v_inicial=(0.0, 0.0)):
        self.I = np.array(v_inicial, dtype=float)
        self.v = self.I.copy()

    def paso(self, error_ejes_deg, dt):
        e = np.array(error_ejes_deg, dtype=float)
        dt = min(max(dt, 0.0), self.dt_max)
        I_nuevo = self.I + self.ki * e * dt
        v = self.kp * e + I_nuevo
        recortada = np.clip(v, -self.v_max, self.v_max)
        # anti-windup: si saturamos y el error empuja hacia fuera, no integrar
        for i in range(2):
            if recortada[i] != v[i] and np.sign(e[i]) == np.sign(v[i]):
                I_nuevo[i] = self.I[i]
        self.I = I_nuevo
        self.v = np.clip(self.kp * e + self.I, -self.v_max, self.v_max)
        return tuple(float(x) for x in self.v)


class Predictor:
    """Hueco para la predicción por elementos orbitales (TLE) de satélites: velocidad esperada en ejes."""

    def velocidad_esperada(self, t):
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Trabajo en hilo
# ═════════════════════════════════════════════════════════════════════════════
class TrabajoSeguimiento:
    FASES = ("parado", "preparando", "buscando", "manual", "bloqueado", "perdido", "calibrando", "parando")

    def __init__(self):
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo = None
        self.version = 0
        self._objetivo_pedido = None
        self._orden_calibrar = None
        self._manual = None           # (v_az, v_alt, instante) del pad de la página
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.fase = "parado"
        self.mensaje = ""
        self.error = ""
        self.inicio = ""
        self.telemetria = {}
        self.calibracion = None
        self.grabacion = None

    # ── estado para la web ───────────────────────────────────────────────
    def estado(self):
        with self._lock:
            return {"en_curso": self.en_curso, "terminado": self.terminado, "fase": self.fase,
                    "mensaje": self.mensaje, "error": self.error, "inicio": self.inicio, "version": self.version,
                    "telemetria": dict(self.telemetria),
                    "calibracion": self.calibracion.como_dict() if self.calibracion else None,
                    "grabacion": self.grabacion.estado() if self.grabacion else None}

    def _msg(self, texto, nivel=logging.INFO):
        with self._lock:
            self.mensaje = texto
        log.log(nivel, "Seguimiento: %s", texto)

    def _fase(self, fase):
        with self._lock:
            self.fase = fase

    # ── control ──────────────────────────────────────────────────────────
    def iniciar(self, camara_ascom, montura_ascom, conf, fabrica_camara=None, fabrica_montura=None,
                calibracion=None, voz=None, lat_lon=None):
        """
        camara_ascom / montura_ascom: TrabajadorCOM de la app (la cámara se
        desconecta mientras dura el seguimiento). fabrica_camara() y
        fabrica_montura() devuelven objetos con la interfaz de CamaraNativa y
        MonturaEjes (inyección para el simulador).
        """
        with self._lock:
            if self.en_curso:
                raise RuntimeError("El seguimiento ya está en marcha")
            if camara_ascom is not None and camara_ascom.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self._parar.clear()
            self.en_curso = True
            self.fase = "preparando"
            self.inicio = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.mensaje = "Preparando cámara y montura..."
            self.calibracion = calibracion or elegir_calibracion(conf)
        self._conf = dict(conf)
        self._hilo = threading.Thread(target=self._ejecutar, name="seguimiento", daemon=True,
                                      args=(camara_ascom, montura_ascom, fabrica_camara, fabrica_montura, voz, lat_lon))
        self._hilo.start()
        return self._hilo

    def parar(self):
        self._parar.set()

    def objetivo(self, fx, fy):
        """El usuario ha pulsado en la vista previa (fracciones 0–1 del ancho/alto)."""
        self._objetivo_pedido = (float(fx), float(fy))

    def mover_manual(self, v_az, v_alt):
        """Pad de la página: mueve la montura por el mismo camino que el seguimiento (latido cada 0,5 s)."""
        self._manual = (float(v_az), float(v_alt), time.perf_counter())

    def calibrar_empirica(self, v=0.3, duracion=1.0):
        """Pide al hilo que mida la matriz empírica moviendo cada eje (solo en marcha y sin objetivo bloqueado)."""
        self._orden_calibrar = (float(v), float(duracion))

    def esperar(self, timeout=None):
        if self._hilo:
            self._hilo.join(timeout)

    # ── fábricas por defecto ─────────────────────────────────────────────
    def _camara_por_defecto(self):
        from camara_nativa import CamaraNativa
        cam = CamaraNativa()
        cam.abrir()
        return cam

    def _montura_por_defecto(self, montura_ascom):
        import montura_ejes
        return montura_ejes.crear(self._conf, trabajador_ascom=montura_ascom)

    # ── hilo ─────────────────────────────────────────────────────────────
    def _ejecutar(self, camara_ascom, montura_ascom, fabrica_camara, fabrica_montura, voz, lat_lon):
        conf = self._conf
        cam = None
        montura = None
        error = ""
        camara_desconectada = False
        try:
            if camara_ascom is not None and fabrica_camara is None:
                camara_ascom.desconectar()
                camara_desconectada = True
            cam = fabrica_camara() if fabrica_camara else self._camara_por_defecto()
            props = getattr(cam, "propiedades", None) or {}
            self._modelo_camara = str(props.get("modelo", ""))
            self._bayer_camara = str(props.get("bayer", "MONO"))
            geo = cam.configurar(roi_px=conf["roi_px"], binning=conf["binning"], formato=conf["formato"],
                                 exposicion_ms=conf["exposicion_ms"], ganancia=conf["ganancia"],
                                 fps_max=conf["fps_camara"])
            montura = fabrica_montura() if fabrica_montura else self._montura_por_defecto(montura_ascom)
            montura.preparar()
            self.calibracion.preparar(geo.get("binning", conf["binning"]), montura.signos_cielo())
            log.info("Seguimiento: calibración %s (%s), signos de los motores %s", self.calibracion.origen,
                     self.calibracion.fecha, self.calibracion.signos_cielo)
            cam.iniciar_video()
            if voz:
                voz.decir("Seguimiento de objetos", categoria="capturas")
            self._bucle(cam, montura, geo, conf)
        except Exception as e:
            error = str(e)
            log.exception("Seguimiento: error")
        finally:
            self._fase("parando")
            if montura is not None:
                for fn in (montura.parar, montura.restaurar):
                    try:
                        fn()
                    except Exception as e:
                        log.warning("Seguimiento: al parar la montura: %s", e)
            if self.grabacion is not None:
                try:
                    self.grabacion.cerrar()
                except Exception as e:
                    log.warning("Seguimiento: al cerrar la grabación: %s", e)
            if cam is not None:
                try:
                    cam.cerrar()
                except Exception as e:
                    log.warning("Seguimiento: al cerrar la cámara: %s", e)
            if camara_desconectada:
                self._reconectar_camara(camara_ascom)
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.fase = "parado"
                self.error = error
                self.mensaje = f"Error: {error}" if error else "Seguimiento terminado"
                self.version += 1

    def _reconectar_camara(self, camara_ascom):
        for intento in range(3):
            try:
                camara_ascom.ejecutar(lambda c: c.Connected, timeout=30)
                log.info("Seguimiento: cámara ASCOM reconectada")
                return True
            except Exception as e:
                log.warning("Seguimiento: no se pudo reconectar la cámara ASCOM (%d/3): %s", intento + 1, e)
                time.sleep(2)
        self._msg("La cámara ASCOM no se ha podido reconectar; reconéctala desde Configuración", logging.ERROR)
        return False

    def _bucle(self, cam, montura, geo, conf):
        """
        Lee fotogramas a la cadencia de la cámara (fps_camara, limitada en la
        propia cámara para no acumular fotogramas viejos) y manda velocidades a
        la montura a frecuencia_hz. La velocidad aparente del objeto se estima
        sobre los últimos `ventana_v_s` segundos de detecciones.
        """
        from camara_nativa import luminancia_rapida
        periodo_orden = 1.0 / max(0.5, conf["frecuencia_hz"])
        vista_cada = 1.0 / max(0.1, conf["vista_previa_hz"])
        ventana_v = conf.get("ventana_v_s", 0.4)
        lado = geo["lado"]
        centro = np.array([lado / 4.0, lado / 4.0])      # el plano de detección es media resolución del ROI
        pi = ControladorPI(conf["kp"], conf["ki"], min(conf["v_max"], min(montura.limites())))
        objetivo_xy = None
        historia = []            # (t, x, y) del objetivo
        t_ultima_vista = 0.0
        t_ultima_orden = 0.0
        t_perdido = None
        fps_t0, fps_n, fps = time.perf_counter(), 0, 0.0
        v_ejes = (0.0, 0.0)
        alt = None
        t_pos = 0.0
        n_frames = 0
        m = None
        error_px = (0.0, 0.0)
        v_obj = None
        elegido_por_usuario = False
        ignorar = []             # (t, x, y) de manchas quietas descartadas en la adquisición automática
        exposicion_ms = float(conf["exposicion_ms"])
        t_ultimo_ajuste = 0.0
        fondo_plano = ruido_plano = maximo_plano = 0
        # Tras soltar el pad o parar los ejes la montura aún frena: las luces fijas parecen moverse
        # en la imagen y se confundirían con un objeto. No se adquiere nada hasta que se asienta.
        asentamiento_s = float(conf.get("asentamiento_s", 1.5) or 1.5)
        t_calma = 0.0
        tope_plano = 255.0       # luminancia_rapida devuelve uint8

        def reiniciar_objetivo(fase, msg):
            nonlocal objetivo_xy, historia, t_perdido, elegido_por_usuario
            objetivo_xy, historia, t_perdido, elegido_por_usuario = None, [], None, False
            pi.arrancar()
            self._fase(fase)
            if msg:
                self._msg(msg)

        def candidata_automatica(manchas, t):
            """La mancha más brillante que no esté en la lista de descartadas (últimos 5 s)."""
            vivas = [(x, y) for (tt, x, y) in ignorar if t - tt < 5.0]
            for cand in manchas:
                if all(math.hypot(cand["x"] - x, cand["y"] - y) > 15 for x, y in vivas):
                    return cand
            return None

        self._fase("buscando")
        self._msg("Buscando un objeto brillante (o pulsa uno en la vista previa)")
        while not self._parar.is_set():
            # Orden de calibración empírica
            if self._orden_calibrar is not None:
                v, dur = self._orden_calibrar
                self._orden_calibrar = None
                self._calibrar_empirica(cam, montura, conf, v, dur, alt)
                reiniciar_objetivo("buscando", "")
                continue
            try:
                frame = cam.leer_frame()
            except Exception as e:
                self._msg(f"Fotograma perdido: {e}", logging.WARNING)
                self._parar.wait(0.05)
                continue
            t_frame = time.perf_counter()
            n_frames += 1
            fps_n += 1
            if t_frame - fps_t0 >= 1.0:
                fps, fps_n, fps_t0 = fps_n / (t_frame - fps_t0), 0, t_frame
            plano = luminancia_rapida(frame)

            # Pad manual: mientras se mueve con el pad no se adquiere nada; al soltarlo, a buscar
            man = self._manual
            en_manual = False
            if man is not None:
                if t_frame - man[2] <= 1.2 and (man[0] or man[1]):
                    en_manual = True
                    if self.fase != "manual":
                        reiniciar_objetivo("manual", "Moviendo con el pad")
                    if (man[0], man[1]) != v_ejes:
                        montura.velocidad(man[0], man[1])
                        v_ejes = (man[0], man[1])
                else:
                    self._manual = None
                    montura.parar()
                    v_ejes = (0.0, 0.0)
                    t_calma = t_frame + asentamiento_s
                    reiniciar_objetivo("buscando", "Pad suelto: esperando a que la montura se asiente")
            if n_frames % 15 == 1:
                # Niveles del fotograma para la telemetría (diagnóstico: ¿ve algo la cámara?)
                fondo_plano = np.median(plano)
                ruido_plano = np.median(np.abs(plano.astype(np.int16) - fondo_plano)) * 1.4826
                maximo_plano = plano.max()
            manchas = detectar_manchas(plano, conf["k_mad"], conf["area_min"], conf["area_max"],
                                       contraste_min=conf["contraste_min"])
            if conf["grabar"] and (conf["grabar_desde"] == "inicio" or self.fase in ("bloqueado", "perdido")):
                recorte = self._recorte_video(frame, conf)
                if self.grabacion is None:
                    self._abrir_grabacion(recorte, geo, conf)
                if self.grabacion is not None and self.grabacion.activa:
                    self.grabacion.encolar(recorte)

            # Objetivo pedido por el usuario (fracción del ROI → píxel del plano)
            pedido = self._objetivo_pedido
            if pedido is not None and not en_manual:
                self._objetivo_pedido = None
                px, py = pedido[0] * plano.shape[1], pedido[1] * plano.shape[0]
                m = elegir_objetivo(manchas, (px, py), radio_px=max(conf["radio_px"], plano.shape[0] * 0.1))
                reiniciar_objetivo("buscando", "Objetivo elegido por el usuario" if m else "")
                if m:
                    objetivo_xy = (m["x"], m["y"])
                    elegido_por_usuario = True
                else:
                    self._msg("No hay ninguna mancha cerca del punto pulsado", logging.WARNING)

            m = elegir_objetivo(manchas, objetivo_xy, conf["radio_px"]) if objetivo_xy is not None else None
            if t_frame < t_calma and not en_manual:
                en_manual = True          # asentándose: se ve y se mide, pero no se adquiere nada
                if objetivo_xy is not None and not elegido_por_usuario:
                    objetivo_xy, historia = None, []
            if m is None and objetivo_xy is None and manchas and not en_manual:
                # Sin objetivo: adquirir automáticamente la más brillante que no se haya descartado por quieta
                m = candidata_automatica(manchas, t_frame)
                if m is not None:
                    self._msg(f"Objeto adquirido en ({m['x']:.0f}, {m['y']:.0f})")

            # Altura para la seguridad y para la matriz (1 Hz)
            if t_frame - t_pos >= 1.0:
                t_pos = t_frame
                try:
                    pos = montura.posicion()
                except Exception:
                    pos = None
                if pos is not None:
                    alt = pos[1]
                    lim = conf.get("altura_minima_seguimiento")
                    if lim is not None and alt < lim:
                        self._msg(f"Altura {alt:.1f}° por debajo del mínimo ({lim:g}°): se para el seguimiento", logging.WARNING)
                        break

            if (m is None and not manchas and conf["exposicion_auto"] and t_frame - t_ultimo_ajuste >= 1.0
                    and hasattr(cam, "exposicion") and exposicion_ms < conf["exposicion_max_ms"]):
                # Nada a la vista: subir la exposición poco a poco hasta el máximo (huevo y gallina: la
                # automática por el objeto solo actúa cuando ya hay objeto)
                nueva = min(conf["exposicion_max_ms"], exposicion_ms * 2.0)
                try:
                    cam.exposicion(nueva)
                    log.info("Seguimiento: sin manchas, exposición %.2f → %.2f ms", exposicion_ms, nueva)
                    exposicion_ms = nueva
                except Exception as e:
                    log.warning("Seguimiento: no se pudo cambiar la exposición: %s", e)
                t_ultimo_ajuste = t_frame
            if m is not None and conf["exposicion_auto"] and t_frame - t_ultimo_ajuste >= 1.0 and hasattr(cam, "exposicion"):
                # Exposición automática por el pico del objeto (solo exposición; la ganancia no se toca)
                pico = m["pico"] / tope_plano
                nueva = exposicion_ms
                if m["saturada"] or pico > 0.92:
                    nueva = exposicion_ms * 0.5
                elif pico < 0.4:
                    nueva = exposicion_ms * min(2.5, conf["pico_objetivo"] / max(pico, 0.05))
                elif pico > 0.85:
                    nueva = exposicion_ms * 0.8
                nueva = max(conf["exposicion_min_ms"], min(conf["exposicion_max_ms"], nueva))
                if abs(nueva - exposicion_ms) / exposicion_ms > 0.05:
                    try:
                        cam.exposicion(nueva)
                        log.info("Seguimiento: exposición %.2f → %.2f ms (pico del objeto %.0f %%)", exposicion_ms, nueva, pico * 100)
                        exposicion_ms = nueva
                    except Exception as e:
                        log.warning("Seguimiento: no se pudo cambiar la exposición: %s", e)
                t_ultimo_ajuste = t_frame
            if m is not None:
                objetivo_xy = (m["x"], m["y"])
                historia.append((t_frame, m["x"], m["y"]))
                historia = [h for h in historia if t_frame - h[0] <= ventana_v]
                if t_perdido is not None:
                    t_perdido = None
                    self._fase("bloqueado")
                    self._msg("Objetivo recuperado")
                error_px = (m["x"] - centro[0], m["y"] - centro[1])
                # Velocidad aparente del objeto en px/s sobre la ventana
                v_obj = None
                if len(historia) >= 2:
                    (t1, x1, y1), (t2, x2, y2) = historia[0], historia[-1]
                    if t2 - t1 >= 0.08:
                        v_obj = ((x2 - x1) / (t2 - t1), (y2 - y1) / (t2 - t1))
                listo = (self.fase == "buscando" and v_obj is not None and len(historia) >= conf["frames_bootstrap"]
                         and historia[-1][0] - historia[0][0] >= ventana_v * 0.8)
                if (listo and not elegido_por_usuario and math.hypot(*v_obj) < conf["v_min_px_s"]
                        and v_ejes == (0.0, 0.0)):
                    # Mancha quieta con la montura parada (una estrella): descartarla y buscar otra
                    ignorar.append((t_frame, m["x"], m["y"]))
                    reiniciar_objetivo("buscando", f"Mancha quieta en ({m['x']:.0f}, {m['y']:.0f}) descartada")
                    m = None
                elif listo and not movimiento_coherente(historia):
                    # Las posiciones no siguen una línea: es ruido o son manchas distintas; no mover la montura
                    pass
                elif listo:
                    # Bootstrap: la integral arranca con la velocidad angular aparente del objeto
                    v0 = self.calibracion.px_a_ejes(v_obj[0], v_obj[1], alt)
                    v0 = (v0[0] + v_ejes[0], v0[1] + v_ejes[1])
                    pi.arrancar(v0)
                    self._fase("bloqueado")
                    self._msg(f"Bloqueado: velocidad inicial az {v0[0]:+.3f} alt {v0[1]:+.3f} °/s")
                    t_ultima_orden = 0.0
                if m is not None and self.fase == "bloqueado" and t_frame - t_ultima_orden >= periodo_orden:
                    dt = periodo_orden if t_ultima_orden == 0.0 else t_frame - t_ultima_orden
                    t_ultima_orden = t_frame
                    e_ejes = self.calibracion.px_a_ejes(error_px[0], error_px[1], alt)
                    v_ejes = pi.paso(e_ejes, dt)
                    montura.velocidad(*v_ejes)
            else:
                error_px = (0.0, 0.0)
                v_obj = None
                if objetivo_xy is not None:
                    if t_perdido is None:
                        t_perdido = t_frame
                        self._fase("perdido")
                        self._msg("Objetivo perdido; se mantiene la última velocidad")
                    elif t_frame - t_perdido > conf["perdida_s"]:
                        montura.parar()
                        v_ejes = (0.0, 0.0)
                        t_calma = t_frame + asentamiento_s
                        reiniciar_objetivo("buscando", "Objetivo perdido: ejes parados, buscando de nuevo")
            if hasattr(montura, "pendiente"):
                montura.pendiente()

            # Telemetría y vista previa
            tel_m = montura.telemetria()
            with self._lock:
                self.telemetria = {
                    "fps": round(fps, 1), "frames": n_frames, "manchas": len(manchas),
                    "objetivo": None if objetivo_xy is None else [round(objetivo_xy[0], 1), round(objetivo_xy[1], 1)],
                    "error_px": [round(error_px[0], 1), round(error_px[1], 1)],
                    "v_obj_px_s": None if v_obj is None else [round(v_obj[0], 1), round(v_obj[1], 1)],
                    "v_az": round(v_ejes[0], 4), "v_alt": round(v_ejes[1], 4),
                    "integral": [round(float(pi.I[0]), 4), round(float(pi.I[1]), 4)],
                    "alt_deg": None if alt is None else round(alt, 2), "lado": plano.shape[1],
                    "saturada": bool(m and m["saturada"]), "descartados": cam.descartados() if hasattr(cam, "descartados") else 0,
                    "exposicion_ms": round(exposicion_ms, 2), "pico": None if m is None else round(m["pico"] / tope_plano * 100),
                    "fondo": int(fondo_plano), "ruido": round(float(ruido_plano), 1), "maximo": int(maximo_plano),
                    "montura": tel_m, "latencia_ms": tel_m.get("latencia_ms"),
                }
            if t_frame - t_ultima_vista >= vista_cada:
                t_ultima_vista = t_frame
                self._guardar_vista(plano, manchas, objetivo_xy, conf["vista_previa_px"])

    @staticmethod
    def _recorte_video(frame, conf):
        """Cuadrado centrado de video_lado píxeles (origen par, para no romper el mosaico Bayer)."""
        lado = int(conf.get("video_lado") or 0)
        h, w = frame.shape[:2]
        if lado <= 0 or lado >= min(h, w):
            return frame
        lado &= ~1
        y0, x0 = ((h - lado) // 2) & ~1, ((w - lado) // 2) & ~1
        return frame[y0:y0 + lado, x0:x0 + lado]

    def _abrir_grabacion(self, frame, geo, conf):
        """Abre la grabación de vídeo con la geometría real del fotograma (una vez por seguimiento)."""
        try:
            import grabador_video
            formatos = tuple(f.strip() for f in conf["formato_video"].replace("ambos", "ser,mp4").split(",") if f.strip())
            alto, ancho = frame.shape[:2]
            bayer = "MONO" if geo.get("binning", 1) > 1 or frame.ndim == 3 else getattr(self, "_bayer_camara", "MONO")
            self.grabacion = grabador_video.Grabacion(
                carpeta=conf["carpeta_video"], formatos=formatos, ancho=ancho, alto=alto, fps=conf["fps_camara"],
                bayer=bayer, bits=16 if frame.dtype == np.uint16 else 8,
                instrumento=getattr(self, "_modelo_camara", ""), telescopio=f"focal {conf['focal_mm']:.0f} mm")
            self._msg(f"Grabando vídeo {'en color' if bayer != 'MONO' else 'monocromo'} en {self.grabacion.nombre} ({', '.join(formatos)})")
        except Exception as e:
            log.error("Seguimiento: no se pudo abrir la grabación de vídeo: %s", e)
            self._msg(f"Sin grabación de vídeo: {e}", logging.ERROR)
            self.grabacion = None
            self._conf["grabar"] = False

    def _guardar_vista(self, plano, manchas, objetivo_xy, ancho):
        try:
            os.makedirs(CARPETA_VISTA, exist_ok=True)
            # Estirado solo para mirar (la detección usa el plano crudo): del percentil 1 al 99,9 %
            bajo, alto_p = np.percentile(plano, (1.0, 99.9))
            if alto_p - bajo < 8:
                alto_p = bajo + 8
            estirado = np.clip((plano.astype(np.float32) - bajo) * (255.0 / (alto_p - bajo)), 0, 255).astype(np.uint8)
            img = cv2.cvtColor(estirado, cv2.COLOR_GRAY2BGR)
            h, w = plano.shape
            cv2.drawMarker(img, (w // 2, h // 2), (0, 200, 255), cv2.MARKER_CROSS, 24, 1)
            for m in manchas[:10]:
                cv2.circle(img, (int(m["x"]), int(m["y"])), 8, (120, 120, 120), 1)
            if objetivo_xy is not None:
                cv2.rectangle(img, (int(objetivo_xy[0]) - 12, int(objetivo_xy[1]) - 12),
                              (int(objetivo_xy[0]) + 12, int(objetivo_xy[1]) + 12), (0, 255, 0), 1)
            if w != ancho:
                img = cv2.resize(img, (ancho, int(h * ancho / w)), interpolation=cv2.INTER_AREA)
            tmp = RUTA_VISTA + ".tmp.jpg"
            cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 65])
            # En Windows el reemplazo falla si Flask está sirviendo el fichero en ese
            # instante: reintentar unas veces y, si no, dejar la vista anterior
            for intento in range(4):
                try:
                    os.replace(tmp, RUTA_VISTA)
                    break
                except PermissionError:
                    if intento == 3:
                        return
                    time.sleep(0.02)
            with self._lock:
                self.version += 1
        except Exception as e:
            log.warning("Seguimiento: no se pudo guardar la vista previa: %s", e)

    def _calibrar_empirica(self, cam, montura, conf, v, duracion, alt):
        """
        Mueve cada eje a ±v durante `duracion` s y relaciona el desplazamiento de
        la mancha más brillante (px) con el ángulo que la montura dice haber
        recorrido (posicion_ahora antes y después, con la imagen ya quieta). Así
        no importan el retardo ni la rampa de la montura. La matriz queda en
        píxeles por grado de eje MANDADO (signo de la orden), de modo que el
        controlador no necesita signos aparte.
        """
        from camara_nativa import luminancia_rapida
        self._fase("calibrando")
        self._msg(f"Calibración empírica: moviendo cada eje a {v:g} °/s durante {duracion:g} s")

        def mancha():
            plano = luminancia_rapida(cam.leer_frame())
            ms = detectar_manchas(plano, conf["k_mad"], conf["area_min"], conf["area_max"], contraste_min=conf["contraste_min"])
            if not ms:
                raise RuntimeError("No hay ninguna mancha con la que calibrar")
            return ms[0]["x"], ms[0]["y"]

        def esperar_quieta(maximo_s=4.0):
            """Espera a que la mancha deje de moverse (dos lecturas separadas 0,3 s casi iguales)."""
            t0 = time.perf_counter()
            x0, y0 = mancha()
            while time.perf_counter() - t0 < maximo_s:
                self._parar.wait(0.3)
                x1, y1 = mancha()
                if math.hypot(x1 - x0, y1 - y0) < 1.0:
                    return (x0 + x1) / 2.0, (y0 + y1) / 2.0
                x0, y0 = x1, y1
            return x0, y0

        def mover(v_az, v_alt, segundos):
            montura.velocidad(v_az, v_alt)
            if hasattr(montura, "pendiente"):
                montura.pendiente()
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < segundos and not self._parar.is_set():
                self._parar.wait(0.1)
                if hasattr(montura, "pendiente"):
                    montura.pendiente()
            montura.parar()

        medidas = []
        try:
            for eje in (0, 1):
                x0, y0 = esperar_quieta()
                m0 = montura.posicion_ahora()
                mover(v if eje == 0 else 0.0, v if eje == 1 else 0.0, duracion)
                x1, y1 = esperar_quieta()
                m1 = montura.posicion_ahora()
                if m0 is None or m1 is None:
                    raise RuntimeError("La montura no informa de su posición; no se puede medir el ángulo recorrido")
                d_deg = (m1[eje] - m0[eje] + 180.0) % 360.0 - 180.0
                if abs(d_deg) < 0.02:
                    raise RuntimeError(f"El eje {eje} no se ha movido ({d_deg:.3f}°): ¿montura parada o sin posición?")
                d_cmd = math.copysign(abs(d_deg), v)      # grados en el sentido de la orden
                # La matriz del controlador relaciona el movimiento RELATIVO del objeto (objeto menos
                # montura) con la imagen: al mover la montura el objeto se desplaza al revés, de ahí el signo
                medidas.append((d_cmd if eje == 0 else 0.0, d_cmd if eje == 1 else 0.0, -(x1 - x0), -(y1 - y0)))
                log.info("Calibración empírica eje %d: %.3f° recorridos (informados %.3f°), mancha (%.1f, %.1f) → (%.1f, %.1f)",
                         eje, d_cmd, d_deg, x0, y0, x1, y1)
                # Volver (aproximadamente) al punto de partida
                mover(-v if eje == 0 else 0.0, -v if eje == 1 else 0.0, duracion)
                esperar_quieta()
            cal = Calibracion.empirica(medidas, alt=alt, backend=montura.nombre, factor=self.calibracion.factor_plano)
            previa = self.calibracion
            if previa is not None and previa.origen in ("astrometria", "analitica"):
                Ma = previa.matriz_con_signos(alt)
                Me = cal.matriz_empirica
                diff = float(np.degrees(np.arccos(np.clip((Ma[:, 0] @ Me[:, 0]) /
                                                          (np.linalg.norm(Ma[:, 0]) * np.linalg.norm(Me[:, 0]) + 1e-9), -1, 1))))
                cal.detalle = {"angulo_vs_analitica_deg": round(diff, 1)}
                log.info("Calibración empírica vs analítica: %.1f° de diferencia en el eje de acimut", diff)
            cal.guardar()
            with self._lock:
                self.calibracion = cal
            self._msg(f"Calibración empírica guardada (escala {cal.escala:.2f}\"/px, ángulo {cal.angulo:.1f}°, espejo {cal.espejo:+d})")
        except Exception as e:
            try:
                montura.parar()
            except Exception:
                pass
            self._msg(f"Calibración empírica fallida: {e}", logging.ERROR)


def _luminancia_2x2(img):
    """Suma de cada celda 2×2 en float (quita el mosaico Bayer), para comparar imágenes."""
    f = np.asarray(img, dtype=np.float32)
    h, w = f.shape[0] & ~1, f.shape[1] & ~1
    f = f[:h, :w]
    return f[0::2, 0::2] + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2]


def comprobar_orientacion_sdk(camara_ascom, ganancia=None, ruta_dll=None):
    """
    Hace una foto de la misma escena por ASCOM y por el SDK nativo y averigua
    cuál de las 8 simetrías del cuadrado lleva una a la otra (la calibración por
    astrometría se mide en la imagen de ASCOM y el seguimiento mira la del SDK).
    Sirve cualquier escena con algo de estructura (una habitación, el paisaje de
    día); un cielo negro sin estrellas no. De paso mira si el binning 2 del SDK
    conserva el mosaico Bayer (vídeo en color sin perder resolución).
    Guarda el resultado si es fiable. Deja la cámara ASCOM conectada.
    """
    import orientacion_camara
    from hardware import capturar_frame
    from camara_nativa import CamaraNativa

    # 1) Foto por ASCOM, subiendo o bajando la exposición hasta que se vea algo
    exp, img_ascom, maximo = 0.02, None, 65535.0
    for _ in range(6):
        img_ascom, maximo = camara_ascom.ejecutar(
            lambda c: (capturar_frame(c, exp, 1, descripcion="Orientación SDK", ganancia=ganancia),
                       float(getattr(c, "MaxADU", 65535) or 65535)), timeout=exp + 90)
        nivel = float(np.percentile(img_ascom, 99.5)) / maximo
        if nivel > 0.9:
            exp *= 0.2
        elif nivel < 0.08 and exp < 2.0:
            exp = min(2.0, exp * 8)
        else:
            break
    log.info("Orientación SDK: foto ASCOM de %.3f s, forma %s", exp, img_ascom.shape)

    # 2) La misma escena por el SDK (sensor entero, binning 2)
    camara_ascom.desconectar()
    frame = None
    try:
        with CamaraNativa(ruta_dll=ruta_dll) as cam:
            cam.configurar(roi_px=cam.propiedades["ancho"], binning=2, formato="RAW16",
                           exposicion_ms=exp * 1000.0, ganancia=ganancia)
            cam.iniciar_video()
            for _ in range(3):
                frame = cam.leer_frame(int(exp * 1000 + 3000)).copy()
    finally:
        for intento in range(3):
            try:
                camara_ascom.ejecutar(lambda c: c.Connected, timeout=30)
                break
            except Exception:
                time.sleep(2)

    # 3) Comparación
    res = orientacion_camara.comparar_ascom_sdk(_luminancia_2x2(img_ascom), _luminancia_2x2(frame))
    mejor, segunda = res[0], res[1]
    fiable = mejor[1] > 0.5 and (mejor[1] - segunda[1]) > 0.1
    conservado, medias_sdk, disp_sdk = orientacion_camara.bayer_conservado(frame)
    _, medias_ascom, disp_ascom = orientacion_camara.bayer_conservado(img_ascom)
    previa = orientacion_camara.leer_sdk()
    d = {"transformacion": mejor[0] if fiable else previa["transformacion"], "verificada": bool(fiable or previa.get("verificada")),
         "correlaciones": [(n, round(c, 3)) for n, c in res], "fecha": datetime.now().isoformat(timespec="seconds"),
         "exposicion_s": round(exp, 4),
         # Solo se puede juzgar el mosaico si la escena tiene color (las subredes de ASCOM difieren)
         "bayer_en_binning": (conservado if disp_ascom > 0.08 else None),
         "subredes_ascom": medias_ascom, "subredes_sdk_bin2": medias_sdk}
    if fiable:
        orientacion_camara.guardar_sdk(d)
        log.info("Orientación SDK frente a ASCOM: %s (correlación %.2f, la siguiente %.2f)", mejor[0], mejor[1], segunda[1])
    else:
        log.warning("Orientación SDK frente a ASCOM sin decidir (mejor %s %.2f, siguiente %s %.2f): escena con poca estructura",
                    mejor[0], mejor[1], segunda[0], segunda[1])
    d["fiable"] = fiable
    return d


trabajo_seguimiento = TrabajoSeguimiento()
