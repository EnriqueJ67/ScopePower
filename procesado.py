# procesado.py
#
# Procesado "estilo Seestar" del apilado, en este orden:
#   1. Gradiente: se ajusta una superficie polinómica de 2º orden al fondo
#      (con estrellas y objeto enmascarados) y se resta. Espacio lineal.
#   2. Neutralización de fondo + STF enlazado (AutoStrechSTF).
#   3. Reducción de ruido: fuerte en color, suave en luminancia, con máscara
#      de estrellas para no ablandarlas. La fuerza baja con el nº de fotos.
#   4. Contraste local (CLAHE en luminancia) + curva en S suave.
#   5. SCNR (quitar dominante verde) y saturación, ya con el ruido reducido.
#
# Tres estilos: natural, seestar (por defecto) y agresivo, con ajustes finos
# (ruido, contraste, saturación) en la sección [Procesado] de config.ini.

import logging
import configparser

import numpy as np
import cv2

from AutoStrechSTF import apply_stf_color

log = logging.getLogger("scopepower.procesado")

ESTILOS = {
    #            gradiente ruido  contraste saturacion scnr
    "natural":  {"gradiente": True, "aplanar": False, "ruido": 0.30, "contraste": 0.25, "saturacion": 1.3, "scnr": 0.5},
    "seestar":  {"gradiente": True, "aplanar": False, "ruido": 0.55, "contraste": 0.50, "saturacion": 1.7, "scnr": 0.8},
    "agresivo": {"gradiente": True, "aplanar": False, "ruido": 0.80, "contraste": 0.80, "saturacion": 2.1, "scnr": 1.0},
    # "aplanar" (fondo local a gran escala) queda como opción manual: no elimina
    # las sombras de polvo del sensor, que tienen el tamaño de una galaxia
    # (eso solo lo arreglan los flats), y puede comerse nebulosas extensas.
}


def leer_config():
    """Estilo y ajustes. Un valor explícito en [Procesado] manda sobre el del estilo."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    p = cfg["Procesado"] if "Procesado" in cfg else {}
    estilo = str(p.get("estilo", "seestar")).lower()
    if estilo not in ESTILOS:
        estilo = "seestar"
    ajustes = dict(ESTILOS[estilo])
    ajustes["estilo"] = estilo
    for clave in ("ruido", "contraste", "saturacion", "scnr"):
        v = p.get(clave, "")
        if str(v).strip() != "":
            ajustes[clave] = float(v)
    for clave in ("gradiente", "aplanar"):
        if str(p.get(clave, "")).strip() != "":
            ajustes[clave] = str(p.get(clave)).lower() == "true"
    if str(p.get("escala_aplanado", "")).strip() != "":
        ajustes["escala_aplanado"] = float(p.get("escala_aplanado"))
    return ajustes


def guardar_config(cambios):
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if "Procesado" not in cfg:
        cfg["Procesado"] = {}
    for k, v in cambios.items():
        if k in ("estilo", "ruido", "contraste", "saturacion", "scnr", "gradiente", "aplanar", "escala_aplanado"):
            cfg["Procesado"][k] = str(v).lower() if isinstance(v, bool) else str(v)
    with open("config.ini", "w") as f:
        cfg.write(f)


# ─────────────────────────────────────────────────────────────────────────────
#  1. Gradiente de fondo (espacio lineal)
# ─────────────────────────────────────────────────────────────────────────────

def _base_polinomio(x, y):
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)


def quitar_gradiente(rgb, rejilla=12):
    """
    rgb: (H, W, 3) float lineal. Devuelve la imagen con el gradiente de fondo
    restado (se conserva el nivel medio del fondo para no dejar negativos).
    """
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    # Muestras: mediana de cada celda de una rejilla, calculada sobre una
    # versión reducida (rápido) y con los píxeles brillantes excluidos.
    peq = cv2.resize(rgb, (rejilla * 16, rejilla * 16), interpolation=cv2.INTER_AREA)
    lum = peq.mean(axis=2)
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) * 1.4826 + 1e-6
    # excluir estrellas y objeto, y también los bordes negros que deja el alineado
    fondo_ok = (lum < med + 2.5 * mad) & (lum > 0.3 * med)
    ch = peq.shape[0] // rejilla
    xs, ys, vals = [], [], []
    for j in range(rejilla):
        for i in range(rejilla):
            celda = peq[j * ch:(j + 1) * ch, i * ch:(i + 1) * ch]
            mask = fondo_ok[j * ch:(j + 1) * ch, i * ch:(i + 1) * ch]
            if mask.sum() < 0.3 * mask.size:
                continue                       # celda dominada por el objeto
            xs.append((i + 0.5) / rejilla); ys.append((j + 0.5) / rejilla)
            vals.append(np.median(celda[mask], axis=0))
    if len(vals) < 10:
        return rgb
    xs, ys, vals = np.array(xs), np.array(ys), np.array(vals)   # vals: (n, 3)

    # Ajuste robusto: dos pasadas descartando celdas que se alejan del modelo
    A = _base_polinomio(xs, ys)
    usar = np.ones(len(xs), dtype=bool)
    coef = None
    for _ in range(2):
        coef, *_ = np.linalg.lstsq(A[usar], vals[usar], rcond=None)   # (6, 3)
        resid = np.abs(A @ coef - vals).mean(axis=1)
        umbral = np.median(resid[usar]) * 3 + 1e-6
        usar = resid <= umbral
        if usar.sum() < 10:
            break

    # Superficie a resolución completa
    gx, gy = np.meshgrid((np.arange(w) + 0.5) / w, (np.arange(h) + 0.5) / h)
    superficie = (_base_polinomio(gx.ravel(), gy.ravel()) @ coef).reshape(h, w, 3).astype(np.float32)
    nivel = superficie.reshape(-1, 3).mean(axis=0)   # nivel medio que se conserva
    return rgb - superficie + nivel


def aplanar_fondo(rgb, escala=0.16):
    """
    Aplanado local del fondo (espacio lineal): elimina las sombras de polvo del
    sensor, el viñeteo y restos de gradiente que el polinomio no recoge, sin
    necesitar flats. Modelo de fondo = mediana a gran escala de la imagen con
    estrellas y objeto enmascarados; se resta conservando el nivel medio.
    `escala` es la fracción del lado de la imagen que define el tamaño de la
    mediana: todo lo más pequeño que eso se considera fondo. Con 0,16 sobre
    3008 px son ~480 px: las sombras de polvo (100-250 px) desaparecen y las
    galaxias y nebulosas de tamaño medio se conservan. Nebulosas que llenan el
    campo pierden brillo difuso: para ellas, desactivarlo.
    """
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    # Trabajar a 1/4 de resolución: sobra para un fondo de gran escala
    factor = 4
    peq = cv2.resize(rgb, (max(32, w // factor), max(32, h // factor)), interpolation=cv2.INTER_AREA)
    lum = peq.mean(axis=2)
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) * 1.4826 + 1e-6
    fondo_ok = (lum < med + 2.5 * mad) & (lum > 0.3 * med)   # ni estrellas/objeto ni bordes negros

    # Mediana por bloques de tamaño `escala` (solo con píxeles de fondo), luego
    # suavizado e interpolación: el modelo de fondo no contiene nada más pequeño
    # que un bloque.
    lado = max(8, int(escala * peq.shape[1]))
    nb_y, nb_x = max(2, peq.shape[0] // lado), max(2, peq.shape[1] // lado)
    modelo = np.zeros((nb_y, nb_x, 3), dtype=np.float32)
    for j in range(nb_y):
        for i in range(nb_x):
            y0, y1 = j * peq.shape[0] // nb_y, (j + 1) * peq.shape[0] // nb_y
            x0, x1 = i * peq.shape[1] // nb_x, (i + 1) * peq.shape[1] // nb_x
            celda = peq[y0:y1, x0:x1].reshape(-1, 3)
            ok = fondo_ok[y0:y1, x0:x1].ravel()
            modelo[j, i] = np.median(celda[ok], axis=0) if ok.sum() > 20 else np.nan
    # Rellenar celdas sin fondo (objeto grande) con la media de las válidas
    for c in range(3):
        m = modelo[..., c]
        if np.isnan(m).any():
            m[np.isnan(m)] = np.nanmean(m)
    fondo = cv2.resize(modelo, (peq.shape[1], peq.shape[0]), interpolation=cv2.INTER_CUBIC)
    fondo = cv2.GaussianBlur(fondo, (0, 0), lado / 3.0)
    fondo_grande = cv2.resize(fondo, (w, h), interpolation=cv2.INTER_LINEAR)
    nivel = fondo_grande.reshape(-1, 3).mean(axis=0)
    return rgb - fondo_grande + nivel


# ─────────────────────────────────────────────────────────────────────────────
#  3. Ruido
# ─────────────────────────────────────────────────────────────────────────────

def mascara_estrellas(lum8, dilatar=2):
    med = float(np.median(lum8))
    mad = float(np.median(np.abs(lum8.astype(np.float32) - med))) * 1.4826 + 1.0
    m = (lum8.astype(np.float32) > med + 6 * mad).astype(np.uint8)
    if dilatar > 0:
        m = cv2.dilate(m, np.ones((2 * dilatar + 1, 2 * dilatar + 1), np.uint8))
    return cv2.GaussianBlur(m.astype(np.float32), (0, 0), 1.0)   # borde suave 0..1


def medir_ruido(canal, fondo):
    """Sigma del ruido de un canal float: MAD del paso alto sobre píxeles de fondo."""
    alto = canal - cv2.GaussianBlur(canal, (0, 0), 3)
    v = alto[fondo]
    if v.size < 100:
        return 0.0
    return float(np.median(np.abs(v - np.median(v))) * 1.4826)


def reducir_ruido(rgbf, fuerza, n_fotos=1):
    """
    rgbf: (H, W, 3) float32 en [0, 1] ya estirado. fuerza 0..1. Devuelve float32 [0, 1].

    Se trabaja en coma flotante (no en 8 bits: con el fondo en ~24/255 y ruido de
    ±1,5 niveles, filtrar cuantizado deja el fondo a parches). La intensidad se
    calcula a partir del ruido MEDIDO en el fondo, así se adapta sola al número
    de fotos apiladas. Luminancia: medias no locales a 16 bits con h ≈ k·sigma.
    Color: suavizado gaussiano de a/b (sin parches). Las estrellas se restauran
    del original en ambos.
    """
    fuerza = float(np.clip(fuerza, 0, 1))
    if fuerza <= 0:
        return rgbf
    rgbf = np.clip(np.asarray(rgbf, dtype=np.float32), 0, 1)
    lab = cv2.cvtColor(rgbf, cv2.COLOR_RGB2LAB)          # L 0..100, a/b -127..127
    L, a, b = cv2.split(lab)
    L8 = np.clip(L * 2.55, 0, 255).astype(np.uint8)
    mask = mascara_estrellas(L8)
    fondo = mask < 0.05

    def restaurar(orig, filtrado):
        return filtrado * (1 - mask) + orig * mask

    # Luminancia: NLM a 16 bits, h proporcional al ruido medido
    s_L = medir_ruido(L, fondo)
    if s_L > 1e-4:
        k = 0.5 + 1.0 * fuerza
        L16 = np.clip(L / 100.0 * 65535.0, 0, 65535).astype(np.uint16)
        h16 = float(s_L / 100.0 * 65535.0 * k)
        L16d = cv2.fastNlMeansDenoising(L16, h=[h16], templateWindowSize=7, searchWindowSize=21,
                                        normType=cv2.NORM_L1)
        L_d = restaurar(L, L16d.astype(np.float32) / 65535.0 * 100.0)
    else:
        L_d = L

    # Color: gaussiano en a/b (el ojo no distingue detalle de color fino)
    sigma_ab = 1.0 + 4.0 * fuerza
    a_d = restaurar(a, cv2.GaussianBlur(a, (0, 0), sigma_ab))
    b_d = restaurar(b, cv2.GaussianBlur(b, (0, 0), sigma_ab))

    log.debug("ruido: sigma L %.3f -> k %.2f; sigma color %.1f px", s_L, 0.5 + 1.0 * fuerza, sigma_ab)
    out = cv2.cvtColor(cv2.merge([L_d, a_d, b_d]), cv2.COLOR_LAB2RGB)
    return np.clip(out, 0, 1).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
#  4. Contraste local + curva
# ─────────────────────────────────────────────────────────────────────────────

def contraste_local(rgb8, cantidad):
    """CLAHE en luminancia mezclado con el original según `cantidad` (0..1), más curva en S suave."""
    cantidad = float(np.clip(cantidad, 0, 1))
    if cantidad <= 0:
        return rgb8
    lab = cv2.cvtColor(rgb8, cv2.COLOR_RGB2LAB)
    L, a, b = cv2.split(lab)
    mask = mascara_estrellas(L, dilatar=4)

    # CLAHE se calcula sobre una luminancia SIN estrellas (sustituidas por el
    # fondo local) para que las estrellas brillantes no oscurezcan su entorno.
    # Después se aplica al original solo la DIFERENCIA que introduce CLAHE
    # (un mapa suave de ajuste local); así estrellas y halos quedan intactos y
    # no aparecen anillos oscuros alrededor.
    fondo_local = cv2.medianBlur(L, 21)
    L_sin = (L.astype(np.float32) * (1 - mask) + fondo_local.astype(np.float32) * mask).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=1.0 + 2.0 * cantidad, tileGridSize=(6, 6))
    L_c = clahe.apply(L_sin).astype(np.float32)
    delta = cv2.GaussianBlur(L_c - L_sin.astype(np.float32), (0, 0), 2.0)
    L_m = L.astype(np.float32) + cantidad * delta

    # Curva en S suave alrededor del gris medio (mezcla máxima 35 %)
    x = np.clip(L_m / 255.0, 0, 1)
    k = 1.0 + 1.0 * cantidad
    s = 1.0 / (1.0 + np.exp(-k * (x - 0.5) * 6.0))
    s0, s1 = 1.0 / (1.0 + np.exp(k * 3.0)), 1.0 / (1.0 + np.exp(-k * 3.0))
    s = (s - s0) / (s1 - s0)
    peso = 0.35 * cantidad
    curva = x * (1 - peso) + s * peso

    L_out = np.clip(curva * 255.0, 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.merge([L_out, a, b]), cv2.COLOR_LAB2RGB)


# ─────────────────────────────────────────────────────────────────────────────
#  5. Color
# ─────────────────────────────────────────────────────────────────────────────

def scnr_verde(rgb8, factor):
    """Elimina el exceso de verde (media de R y B como referencia), estilo SCNR average neutral."""
    factor = float(np.clip(factor, 0, 1))
    if factor <= 0:
        return rgb8
    f = rgb8.astype(np.float32)
    ref = (f[..., 0] + f[..., 2]) / 2
    exceso = np.clip(f[..., 1] - ref, 0, None)
    f[..., 1] -= factor * exceso
    return np.clip(f, 0, 255).astype(np.uint8)


def saturar(rgb8, factor):
    factor = float(factor)
    if abs(factor - 1.0) < 1e-3:
        return rgb8
    hsv = cv2.cvtColor(rgb8, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)


# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline completo
# ─────────────────────────────────────────────────────────────────────────────

def procesar(cubo_lineal, n_fotos=1, ajustes=None, escala=1.0):
    """
    cubo_lineal: (3, H, W) o (H, W, 3) float lineal (el apilado).
    escala: 0.5 para la vista previa (4 veces más rápido), 1.0 para guardar.
    Devuelve uint8 (h, w, 3) RGB listo para JPG.
    """
    ajustes = ajustes or leer_config()
    rgb = np.asarray(cubo_lineal, dtype=np.float32)
    if rgb.ndim == 3 and rgb.shape[0] == 3:
        rgb = np.transpose(rgb, (1, 2, 0))
    if escala != 1.0:
        h, w = rgb.shape[:2]
        rgb = cv2.resize(rgb, (max(8, int(w * escala)), max(8, int(h * escala))), interpolation=cv2.INTER_AREA)

    if ajustes.get("gradiente", True):
        rgb = quitar_gradiente(rgb)
    if ajustes.get("aplanar", False):
        rgb = aplanar_fondo(rgb, float(ajustes.get("escala_aplanado", 0.16)))

    # Neutralización + STF enlazado (AutoStrechSTF), en coma flotante
    rgbf = apply_stf_color(rgb, linked=True, neutralizar=True, output_8bit=False).astype(np.float32)

    rgbf = reducir_ruido(rgbf, ajustes.get("ruido", 0.5), n_fotos)
    rgb8 = np.clip(rgbf * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rgb8 = contraste_local(rgb8, ajustes.get("contraste", 0.5))
    rgb8 = scnr_verde(rgb8, ajustes.get("scnr", 0.8))
    rgb8 = saturar(rgb8, ajustes.get("saturacion", 1.5))
    return rgb8
