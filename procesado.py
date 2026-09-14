# procesado.py
#
# Procesado "estilo Seestar" del apilado, en este orden:
#   1. Gradiente: se ajusta una superficie polinómica de 2º orden al fondo
#      (con estrellas y objeto enmascarados) y se resta. Espacio lineal.
#   2. Neutralización de fondo + estirado adaptativo multiescala (tipo
#      MultiscaleAdaptiveStretch de PixInsight): se estira la luminancia con una
#      ganancia LOCAL (alta en el fondo, baja en estrellas y núcleos) y el color
#      se escala en proporción, así lo brillante no se quema ni pierde el color.
#   3. Reducción de ruido: fuerte en color, suave en luminancia, con máscara
#      de estrellas para no ablandarlas. La fuerza baja con el nº de fotos.
#   4. Contraste local (CLAHE en luminancia) + curva en S suave.
#   5. Color: reneutralizar el fondo, temperatura de color y brillo general
#      (deslizadores del editor), SCNR (quitar dominante verde, sin tocar las
#      estrellas) y saturación, ya con el ruido reducido.
#
# Tres estilos: natural, seestar (por defecto) y agresivo, con ajustes finos
# (ruido, contraste, saturación) en la sección [Procesado] de config.ini.

import logging
import configparser
import json
import threading

import numpy as np
import cv2

from AutoStrechSTF import neutralizar_fondo

log = logging.getLogger("scopepower.procesado")

ESTILOS = {
    #            gradiente ruido  contraste saturacion scnr  + estirado (fondo_objetivo, agresividad, compresion, recuperacion)
    "natural":  {"gradiente": True, "aplanar": False, "ruido": 0.30, "contraste": 0.25, "saturacion": 1.3, "scnr": 0.5,
                 "fondo_objetivo": 0.10, "agresividad": 0.6, "compresion": 0.3, "recuperacion": 1.0},
    "seestar":  {"gradiente": True, "aplanar": False, "ruido": 0.55, "contraste": 0.50, "saturacion": 1.7, "scnr": 0.8,
                 "fondo_objetivo": 0.13, "agresividad": 0.7, "compresion": 0.3, "recuperacion": 1.0},
    "agresivo": {"gradiente": True, "aplanar": False, "ruido": 0.80, "contraste": 0.80, "saturacion": 2.1, "scnr": 1.0,
                 "fondo_objetivo": 0.16, "agresividad": 0.8, "compresion": 0.2, "recuperacion": 1.1},
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
    for clave in ("ruido", "contraste", "saturacion", "scnr", "brillo", "temperatura",
                  "fondo_objetivo", "agresividad", "compresion", "recuperacion", "escala"):
        v = p.get(clave, "")
        if str(v).strip() != "":
            ajustes[clave] = float(v)
    for clave in ("gradiente", "aplanar"):
        if str(p.get(clave, "")).strip() != "":
            ajustes[clave] = str(p.get(clave)).lower() == "true"
    if str(p.get("escala_aplanado", "")).strip() != "":
        ajustes["escala_aplanado"] = float(p.get("escala_aplanado"))
    if str(p.get("curva", "")).strip() != "":
        ajustes["curva"] = str(p.get("curva")).strip().lower()
    return ajustes


# Presets: conjuntos de ajustes con nombre, guardados en presets_procesado.json
# (propio de cada PC; copiar_a_campo.bat no lo copia). Se crean desde el editor
# de la galería o desde el apilado y se cargan en cualquiera de los dos.
PRESETS_RUTA = "presets_procesado.json"
CLAVES_PRESET = ("estilo", "estirado", "curva", "fondo_objetivo", "compresion", "brillo", "temperatura",
                 "ruido", "contraste", "saturacion", "scnr", "gradiente", "aplanar")
_lock_presets = threading.Lock()


def leer_presets():
    """{nombre: ajustes} ordenado por nombre; {} si no hay fichero o está roto."""
    try:
        with open(PRESETS_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
        if not isinstance(datos, dict):
            return {}
        return {str(k): v for k, v in sorted(datos.items(), key=lambda kv: kv[0].lower()) if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


def _filtrar_preset(ajustes):
    limpio = {}
    for k in CLAVES_PRESET:
        if k not in ajustes or ajustes[k] is None or str(ajustes[k]).strip() == "":
            continue
        v = ajustes[k]
        if k in ("gradiente", "aplanar"):
            limpio[k] = (str(v).lower() == "true") if not isinstance(v, bool) else v
        elif k in ("estilo", "estirado", "curva"):
            limpio[k] = str(v).lower()
        else:
            try:
                limpio[k] = float(v)
            except (TypeError, ValueError):
                pass
    return limpio


def guardar_preset(nombre, ajustes):
    """Crea o sustituye un preset. Devuelve el diccionario completo de presets."""
    nombre = " ".join(str(nombre or "").split())[:40]
    if not nombre:
        raise ValueError("El preset necesita un nombre")
    with _lock_presets:
        presets = leer_presets()
        presets[nombre] = _filtrar_preset(ajustes or {})
        with open(PRESETS_RUTA, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=1)
    return leer_presets()


def borrar_preset(nombre):
    with _lock_presets:
        presets = leer_presets()
        presets.pop(str(nombre), None)
        with open(PRESETS_RUTA, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=1)
    return leer_presets()


def guardar_config(cambios):
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if "Procesado" not in cfg:
        cfg["Procesado"] = {}
    for k, v in cambios.items():
        if k in ("estilo", "ruido", "contraste", "saturacion", "scnr", "gradiente", "aplanar", "escala_aplanado",
                 "fondo_objetivo", "agresividad", "compresion", "recuperacion", "escala", "curva",
                 "brillo", "temperatura"):
            cfg["Procesado"][k] = str(v).lower() if isinstance(v, bool) else str(v)
    with open("config.ini", "w") as f:
        cfg.write(f)


# ─────────────────────────────────────────────────────────────────────────────
#  0. Recorte de zoom (campo visible)
# ─────────────────────────────────────────────────────────────────────────────
# Con un sensor grande y focal corta los objetos pequeños (globulares,
# planetarias, galaxias) salen diminutos. zoom_preview ([Dispositivos] de
# config.ini) es el porcentaje que se quita por los bordes: 0 = campo completo,
# 50 = se ve el 50 % del lado, 75 = el 25 %. El recorte se hace sobre el cubo
# lineal, ANTES de procesar: el plate solve, el alineado y el FITS apilado
# siguen a campo completo (cuantas más estrellas, mejor resuelve).

def fraccion_campo(config=None):
    """Fracción del lado del campo que se muestra (1.0 = todo), según zoom_preview."""
    try:
        if config is None:
            config = configparser.ConfigParser()
            config.read("config.ini")
        zoom = int(float(config["Dispositivos"].get("zoom_preview", 0)))
    except Exception:
        zoom = 0
    zoom = min(90, max(0, zoom))
    return (100 - zoom) / 100.0


def recortar_centro(cubo, fraccion):
    """
    Recorte central de un cubo (3, H, W) o imagen (H, W, 3) conservando
    `fraccion` del ancho y del alto. Devuelve (recorte, x0, y0): el
    desplazamiento en píxeles de la esquina superior izquierda respecto a la
    imagen completa, que necesitan las anotaciones.
    """
    if fraccion >= 0.999:
        return cubo, 0, 0
    canales_primero = cubo.ndim == 3 and cubo.shape[0] == 3 and cubo.shape[-1] != 3
    h, w = (cubo.shape[1], cubo.shape[2]) if canales_primero else cubo.shape[:2]
    nh, nw = max(8, int(round(h * fraccion))), max(8, int(round(w * fraccion)))
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    if canales_primero:
        rec = cubo[:, y0:y0 + nh, x0:x0 + nw]
    else:
        rec = cubo[y0:y0 + nh, x0:x0 + nw]
    return np.ascontiguousarray(rec), x0, y0


def recortar_campo(cubo, fraccion, zona=None, margen=0.0):
    """
    Recorte completo del campo: primero la `zona` (x0, y0, ancho, alto) cubierta
    por todas las fotos del apilado (con montura altacimutal la rotación de campo
    deja los bordes con menos fotos; si no viene, se detecta en la imagen),
    después un `margen` extra por cada lado (fracción del lado, 0..0.3; el
    "Recorte de bordes" del editor) y, dentro, el recorte central de zoom.
    Devuelve (recorte, x0, y0) con el desplazamiento respecto a la imagen
    completa, como recortar_centro.
    """
    zx, zy = 0, 0
    canales_primero = cubo.ndim == 3 and cubo.shape[0] == 3 and cubo.shape[-1] != 3
    if not zona:
        zona = zona_automatica(cubo)
    if zona:
        x0, y0, ancho, alto = [int(v) for v in zona]
        cubo = cubo[:, y0:y0 + alto, x0:x0 + ancho] if canales_primero else cubo[y0:y0 + alto, x0:x0 + ancho]
        zx, zy = x0, y0
    margen = float(min(max(margen, 0.0), 0.3))
    if margen > 0:
        h, w = (cubo.shape[1], cubo.shape[2]) if canales_primero else cubo.shape[:2]
        mx, my = int(w * margen), int(h * margen)
        cubo = cubo[:, my:h - my, mx:w - mx] if canales_primero else cubo[my:h - my, mx:w - mx]
        zx, zy = zx + mx, zy + my
    rec, x1, y1 = recortar_centro(cubo, fraccion)
    return np.ascontiguousarray(rec), zx + x1, zy + y1


def zona_automatica(cubo, margen=12):
    """
    Zona útil de un apilado que no trae ZONA* en la cabecera (guardado antes de
    que existiera): el mayor rectángulo sin bordes negros de la alineación. La
    rotación de campo deja cuñas diagonales en las esquinas, así que no basta
    con quitar filas y columnas enteras: se parte de una máscara de "hay
    imagen" (a 1/8 de resolución, > 0.8 del cielo) y se encoge el rectángulo por
    el lado menos cubierto hasta que queda entero dentro, como zona_comun del
    apilado. Devuelve (x0, y0, ancho, alto) o None si no hay nada que recortar.
    """
    canales_primero = cubo.ndim == 3 and cubo.shape[0] == 3 and cubo.shape[-1] != 3
    lum = cubo.mean(axis=0) if canales_primero else (cubo.mean(axis=2) if cubo.ndim == 3 else cubo)
    h, w = lum.shape
    f = max(1, min(h, w) // 376)
    peq = cv2.resize(np.asarray(lum, dtype=np.float32), (max(8, w // f), max(8, h // f)), interpolation=cv2.INTER_AREA)
    peq = cv2.medianBlur(peq, 5)                      # sin estrellas ni ruido
    valido = peq > 0.8 * float(np.median(peq))
    if valido.all():
        return None
    fil, col = np.nonzero(valido)
    if len(fil) == 0:
        return None
    y0, y1, x0, x1 = int(fil.min()), int(fil.max()) + 1, int(col.min()), int(col.max()) + 1
    while y1 - y0 > 2 and x1 - x0 > 2:
        sub = valido[y0:y1, x0:x1]
        if sub.all():
            break
        bordes = {"arriba": sub[0].mean(), "abajo": sub[-1].mean(), "izq": sub[:, 0].mean(), "der": sub[:, -1].mean()}
        peor = min(bordes, key=bordes.get)
        if peor == "arriba": y0 += 1
        elif peor == "abajo": y1 -= 1
        elif peor == "izq": x0 += 1
        else: x1 -= 1
    X0, Y0 = x0 * f, y0 * f
    X1, Y1 = min(w, x1 * f), min(h, y1 * f)
    if X0 > 0: X0 += margen
    if Y0 > 0: Y0 += margen
    if X1 < w: X1 -= margen
    if Y1 < h: Y1 -= margen
    if X1 - X0 < w // 2 or Y1 - Y0 < h // 2:
        return None
    if (X0, Y0, X1, Y1) == (0, 0, w, h):
        return None
    return (int(X0), int(Y0), int(X1 - X0), int(Y1 - Y0))


def leer_zona(header):
    """Zona cubierta por todas las fotos, guardada en la cabecera del FITS apilado
    (ZONAX0, ZONAY0, ZONAW, ZONAH); None si el FITS no la lleva."""
    try:
        if header is None or "ZONAW" not in header:
            return None
        return (int(header["ZONAX0"]), int(header["ZONAY0"]), int(header["ZONAW"]), int(header["ZONAH"]))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  1. Gradiente de fondo (espacio lineal)
# ─────────────────────────────────────────────────────────────────────────────

def _rellenar_huecos(malla):
    """Rellena los NaN de una malla (n, m, 3) con la media de sus vecinos válidos, por oleadas."""
    malla = malla.copy()
    for _ in range(malla.shape[0] + malla.shape[1]):
        nan = np.isnan(malla[..., 0])
        if not nan.any():
            break
        ok = (~nan).astype(np.float32)
        suma = cv2.blur(np.where(nan[..., None], 0, malla).astype(np.float32), (3, 3), borderType=cv2.BORDER_CONSTANT)
        cuenta = cv2.blur(ok, (3, 3), borderType=cv2.BORDER_CONSTANT)
        nuevos = nan & (cuenta > 0)
        malla[nuevos] = (suma / np.maximum(cuenta, 1e-6)[..., None])[nuevos]
    if np.isnan(malla).any():
        malla[np.isnan(malla)] = np.nanmean(malla)
    return malla


def quitar_gradiente(rgb, rejilla=12):
    """
    rgb: (H, W, 3) float lineal. Devuelve la imagen con el gradiente de fondo
    restado (se conserva el nivel medio del fondo para no dejar negativos).

    Modelo de fondo: la mediana de cada celda de una rejilla (sin estrellas ni
    bordes negros) suavizada e interpolada, no un polinomio: un polinomio de 2º
    grado no puede seguir un cielo plano por arriba que baja por abajo (viñeteo
    o gradiente asimétrico) y "rebota" dejando una franja clara en un borde.
    Las celdas con objeto se detectan como envolvente inferior (residuo positivo
    frente al modelo, con la dispersión medida solo en los residuos negativos)
    y se rellenan con sus vecinas, así el halo de una galaxia grande no entra
    en el fondo.
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
    malla = np.full((rejilla, rejilla, 3), np.nan, dtype=np.float32)
    for j in range(rejilla):
        for i in range(rejilla):
            celda = peq[j * ch:(j + 1) * ch, i * ch:(i + 1) * ch]
            mask = fondo_ok[j * ch:(j + 1) * ch, i * ch:(i + 1) * ch]
            if mask.sum() < 0.3 * mask.size:
                continue                       # celda dominada por el objeto
            malla[j, i] = np.median(celda[mask], axis=0)
    if np.count_nonzero(~np.isnan(malla[..., 0])) < 10:
        return rgb

    def modelo_de(m):
        return cv2.GaussianBlur(_rellenar_huecos(m), (0, 0), 1.0, borderType=cv2.BORDER_REPLICATE)

    usar = ~np.isnan(malla[..., 0])
    modelo = None
    for _ in range(6):
        modelo = modelo_de(np.where(usar[..., None], malla, np.nan))
        resid = (malla - modelo).mean(axis=2)                          # + = más brillante que el modelo
        negativos = np.abs(resid[usar & (resid < 0)])
        sigma = (np.median(negativos) * 1.4826 if negativos.size >= 5
                 else np.nanmedian(np.abs(resid[usar])) * 1.4826) + 1e-6
        nuevo = usar & ~np.isnan(resid) & (resid <= 2.0 * sigma)
        if nuevo.sum() < 10 or np.array_equal(nuevo, usar):
            break
        usar = nuevo

    # Superficie a resolución completa (los centros de celda quedan en su sitio)
    superficie = cv2.resize(modelo, (w, h), interpolation=cv2.INTER_CUBIC)
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
#  2. Estirado adaptativo multiescala (de lineal a no lineal)
# ─────────────────────────────────────────────────────────────────────────────
#
# Una curva global (STF, MTF, gamma) no puede a la vez sacar la señal débil y
# conservar lo brillante: para subir el fondo hay que llevar estrellas y
# núcleos a saturación (engordan, pierden detalle y color). Aquí el estirado no
# es una curva sino un campo de ganancia que varía por la imagen:
#
#   1. L = luminancia. B = componente de gran escala de L (fondo de cielo y
#      brillo difuso de los objetos; sin estrellas ni estructuras pequeñas).
#   2. S(B) = B estirado con una curva (gamma por defecto; asinh o la MTF de
#      PixInsight como opción) con sombras y medios calculados de la
#      estadística robusta del fondo (mediana y MAD) y el blanco en el
#      percentil 99,8 %, como el STF clásico: así lo extenso (nebulosas) tiene
#      la misma pegada que con él.
#   3. El detalle fino (L - B) se estira con la misma familia de curva pero con
#      el blanco desplazado hacia 1.0 según `compresion`:
#          L' = S(B) + recuperacion * (S_d(L) - S_d(B))
#      compresion = 0: S_d = S, es decir, el STF clásico sobre la luminancia
#      (las estrellas saturan igual); compresion = 1: el blanco del detalle es
#      el máximo real y nada se recorta (perfil y color de las estrellas
#      intactos). Como la curva es cóncava, donde B ya es alto (núcleos,
#      cúmulos) el detalle recibe menos ganancia: esa es la parte "local".
#   4. Color: cada canal recibe la misma ganancia que la luminancia sobre lo
#      que tiene por encima del cielo, RGB' = S(m) + (RGB - m) * (L' - S(m)) /
#      (L - m), como hace el STF canal a canal; lo que rebasa 1.0 se desatura
#      hacia L' en vez de recortarse canal a canal.
#
# Parámetros ([Procesado] de config.ini, con defectos por estilo):
#   fondo_objetivo  brillo del fondo de cielo en la imagen final (0.10-0.20)
#   agresividad     0..1: cuánto se acerca el punto negro al pico del fondo
#   compresion      0..1: protección de lo brillante (0 = como el STF clásico,
#                   1 = nada se recorta)
#   recuperacion    peso del detalle fino reinyectado (1 = contraste del STF)
#   escala          tamaño en px (a resolución completa) por encima del cual una
#                   estructura se considera fondo (1024 por defecto, como MAS)
#   curva           gamma (por defecto), asinh o mtf: familia de la curva del fondo

ESCALA_FONDO_PX = 1024.0


def mtf(x, mt):
    """Midtones transfer function de PixInsight: x en [0, 1], mt = balance de medios."""
    x = np.clip(np.asarray(x, dtype=np.float32), 0, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = (mt - 1.0) * x / ((2.0 * mt - 1.0) * x - mt)
    y = np.where(np.isfinite(y), y, 0)
    return np.clip(y, 0, 1).astype(np.float32)


def asinh_norm(x, a):
    """Estirado asinh normalizado: 0 -> 0, 1 -> 1; `a` pequeño = más agresivo en las sombras."""
    x = np.clip(np.asarray(x, dtype=np.float32), 0, 1)
    return (np.arcsinh(x / a) / np.arcsinh(1.0 / a)).astype(np.float32)


class CurvaFondo:
    """
    Curva S que lleva el fondo de cielo (mediana m) a `fondo_objetivo`, con las
    sombras en c0 = m - k*sigma (k según agresividad, como la AutoSTF de
    PixInsight) y el blanco en 1.0. Las tres familias pasan por los mismos
    puntos pero difieren en la pendiente en el cielo (= contraste de lo débil)
    y en lo que comprimen por arriba:
      gamma (por defecto): x**gamma, la del STF antiguo pero sin recorte por
            arriba; la más suave de las tres en el cielo (pendiente ~0.23 de
            la secante) y la que más comprime lo brillante.
      asinh: logarítmica en lo brillante; ~3 veces más pendiente en el cielo.
      mtf:  la MTF de PixInsight; la más brusca (con un fondo limpio, mt ~ 0.001).
    """

    def __init__(self, m, sigma, fondo_objetivo=0.13, agresividad=0.7, tipo="gamma", blanco=1.0):
        k = 4.0 - 2.5 * float(np.clip(agresividad, 0, 1))     # 0 -> 4 sigma, 1 -> 1.5 sigma
        self.c0 = max(0.0, m - k * sigma)
        # Punto blanco: con 1.0 la gamma sale muy baja (~0.2) y aplasta todo lo
        # extenso (una nebulosa a 10x el cielo se queda en 0.4). El STF clásico
        # lo ponía en el percentil 99.8 % de la imagen (gamma ~0.35) y por eso
        # las nebulosas tenían más pegada; aquí se recibe ese blanco y lo que
        # queda por encima lo comprime la rodilla del detalle, no la curva.
        self.blanco = float(min(1.0, max(blanco, m + 20.0 * sigma, self.c0 + 1e-4)))
        self.tipo = tipo if tipo in ("gamma", "asinh", "mtf") else "gamma"
        x_med = min(max((m - self.c0) / (self.blanco - self.c0), 1e-6), 1 - 1e-6)
        objetivo = float(np.clip(fondo_objetivo, 0.02, 0.6))
        if self.tipo == "gamma":
            self.param = float(np.log(objetivo) / np.log(x_med))
        elif self.tipo == "mtf":
            # mt tal que mtf(x_med, mt) = objetivo: la MTF es involutiva en ese sentido
            self.param = float(mtf(np.float32(x_med), objetivo))
        else:
            # a por bisección en log(a): asinh_norm(x_med, a) crece al bajar a
            lo, hi = -18.0, 2.0
            for _ in range(50):
                mid = 0.5 * (lo + hi)
                if float(asinh_norm(np.float32(x_med), np.exp(mid))) > objetivo:
                    lo = mid
                else:
                    hi = mid
            self.param = float(np.exp(0.5 * (lo + hi)))

    def __call__(self, v):
        x = (np.asarray(v, dtype=np.float32) - self.c0) / (self.blanco - self.c0)
        if self.tipo == "gamma":
            return np.power(np.clip(x, 0, 1), self.param).astype(np.float32)
        return mtf(x, self.param) if self.tipo == "mtf" else asinh_norm(x, self.param)

    def derivada(self, v):
        """Pendiente dS/dv (ganancia local de la curva) en el valor lineal v."""
        x = np.clip((np.asarray(v, dtype=np.float32) - self.c0) / (self.blanco - self.c0), 1e-9, 1)
        if self.tipo == "gamma":
            d = self.param * np.power(x, self.param - 1.0)
        elif self.tipo == "mtf":
            mt = self.param
            d = mt * (1.0 - mt) / np.square((2.0 * mt - 1.0) * x - mt)
        else:
            a = self.param
            d = 1.0 / (np.arcsinh(1.0 / a) * np.sqrt(x * x + a * a))
        return (d / (self.blanco - self.c0)).astype(np.float32)


def estadistica_fondo(L):
    """Mediana y sigma robusto (1.4826*MAD) de la luminancia, sobre un submuestreo."""
    sub = L[::4, ::4]
    m = float(np.median(sub))
    sigma = float(np.median(np.abs(sub - m))) * 1.4826 + 1e-7
    return m, sigma


def fondo_gran_escala(L, m, sigma, escala_px, factor=8):
    """
    Componente de gran escala B de la luminancia: todo lo más pequeño que
    `escala_px` (en píxeles de L) queda fuera. Se trabaja a 1/factor de
    resolución con una mediana de tamaño escala_px/factor: estrellas, cúmulos,
    galaxias medianas y sus halos desaparecen del fondo (no se esparcen como
    haría un desenfoque). Para poder usar una mediana grande (OpenCV solo la
    tiene en 8 bits) el fondo se cuantiza entre m - 5 sigma y ~3 veces el cielo:
    lo que está por encima cuenta como "brillante" y la mediana lo ignora
    mientras no llene media ventana. Devuelve B (H, W) > 0.
    """
    h, w = L.shape
    pw, ph = max(16, w // factor), max(16, h // factor)
    peq = cv2.resize(L, (pw, ph), interpolation=cv2.INTER_AREA)
    lo = max(0.0, m - 5.0 * sigma)
    hi = m + max(10.0 * sigma, 2.0 * m)
    u8 = np.clip((peq - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    k = int(max(3, round(escala_px / factor)))
    k = min(k, 2 * min(pw, ph) - 1) | 1          # impar (lo exige medianBlur) y acotado a la imagen reducida
    # Bordes por reflexión: la mediana de OpenCV rellena con ceros lo que queda
    # fuera de la imagen y, con una ventana tan grande, el fondo se hundía en las
    # esquinas (un arco oscuro de un cuarto de círculo tras el desenfoque).
    s = max(1.0, k / 4.0)
    borde = min(k // 2 + int(3 * s), min(pw, ph) - 1)
    u8 = cv2.copyMakeBorder(u8, borde, borde, borde, borde, cv2.BORDER_REFLECT_101)
    med = cv2.medianBlur(u8, k).astype(np.float32) / 255.0 * (hi - lo) + lo
    med = cv2.GaussianBlur(med, (0, 0), s, borderType=cv2.BORDER_REPLICATE)
    med = med[borde:borde + ph, borde:borde + pw]
    B = cv2.resize(med, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.maximum(B, 1e-6).astype(np.float32)


def estirar_multiescala(rgb, ajustes=None, escala_imagen=1.0, progreso=None):
    """
    rgb: (H, W, 3) float lineal en ADU (0..65535), ya sin gradiente.
    escala_imagen: 0.5 si la imagen viene a media resolución (la escala de fondo
    en px se reduce en proporción). progreso(fraccion): opcional, 0..1.
    Devuelve float32 (H, W, 3) en [0, 1], no lineal.
    """
    aj = ajustes or {}
    fondo_objetivo = float(aj.get("fondo_objetivo", 0.13))
    agresividad = float(aj.get("agresividad", 0.7))
    compresion = float(aj.get("compresion", 0.3))
    recuperacion = float(aj.get("recuperacion", 1.0))
    curva = str(aj.get("curva", "gamma")).lower()
    escala_px = float(aj.get("escala", ESCALA_FONDO_PX)) * float(escala_imagen)

    def avisar(f):
        if progreso:
            try:
                progreso(f)
            except Exception:
                pass

    rgb = neutralizar_fondo(np.asarray(rgb, dtype=np.float32))
    rgb = np.clip(rgb / 65535.0, 0, None)
    L = rgb.mean(axis=2)
    m, sigma = estadistica_fondo(L)
    blanco = float(np.percentile(L[::4, ::4], 99.82))        # el mismo blanco que el STF clásico
    S_curva = CurvaFondo(m, sigma, fondo_objetivo, agresividad, curva, blanco=blanco)
    avisar(0.2)

    B = fondo_gran_escala(L, m, sigma, escala_px)
    avisar(0.5)

    # Fondo estirado con la curva (blanco del STF) y detalle fino con la MISMA
    # familia de curva pero con el blanco desplazado hacia 1.0 según
    # `compresion`: con 0 es exactamente el STF clásico sobre la luminancia
    # (las estrellas saturan igual); con 1 el blanco del detalle es el máximo
    # real y nada se recorta (perfil y color de las estrellas intactos). Al ser
    # una curva cóncava, el detalle se frena desde el principio como en el STF
    # (una ganancia lineal con rodilla subía demasiado lo de brillo medio).
    # Donde el fondo B es alto (núcleos, cúmulos) la curva ya da menos a lo que
    # va por encima: esa es la ganancia local.
    S = S_curva(B)
    c = float(np.clip(compresion, 0, 1))
    blanco_detalle = float(S_curva.blanco ** (1.0 - c))          # entre el blanco del STF y 1.0
    S_detalle = CurvaFondo(m, sigma, fondo_objetivo, agresividad, curva, blanco=blanco_detalle)
    L2 = S + recuperacion * (S_detalle(L) - S_detalle(B))
    np.clip(L2, 0, 1, out=L2)
    avisar(0.8)

    # Color: a cada canal se le aplica la ganancia que ha recibido la luminancia,
    # pero sobre lo que tiene POR ENCIMA del fondo de cielo (no sobre el total).
    # Es lo que hace el STF clásico al estirar canal a canal con el negro justo
    # bajo el cielo: en una nebulosa débil la relación (R-cielo)/(G-cielo) es
    # mucho mayor que R/G, y con la de los totales la nebulosa salía gris. El
    # fondo (medianas iguales tras neutralizar) queda en S(m) en los tres.
    S_cielo = float(S_curva(np.float32(m)))
    dL = L - m
    pendiente_cielo = float(S_detalle.derivada(np.float32(m)))
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(np.abs(dL) > 0.1 * sigma, (L2 - S_cielo) / dL, pendiente_cielo).astype(np.float32)
    k = np.clip(k, 0, 4.0 * pendiente_cielo)
    out = S_cielo + (rgb - m) * k[..., None]
    mx = out.max(axis=2)
    exceso = mx > 1.0
    if exceso.any():
        L2e = L2[exceso]
        f = ((1.0 - L2e) / np.maximum(mx[exceso] - L2e, 1e-6))[..., None]
        out[exceso] = L2e[..., None] + (out[exceso] - L2e[..., None]) * f
    out = np.clip(out, 0, 1).astype(np.float32)
    avisar(1.0)
    log.debug("estirado %s: mediana %.5f sigma %.5f c0 %.5f blanco %.4f (detalle %.4f) param %.3g",
              S_curva.tipo, m, sigma, S_curva.c0, S_curva.blanco, blanco_detalle, S_curva.param)
    return out


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
    # Solo el contraste local: CLAHE también desplaza el nivel medio de cada
    # mosaico según su histograma y en cielo plano eso se ve como bandas del
    # tamaño del mosaico (sobre todo junto a los bordes). Se le resta al ajuste
    # su componente de gran escala (medio mosaico) y el fondo conserva su nivel.
    lado = max(L.shape) / 6.0
    delta -= cv2.GaussianBlur(delta, (0, 0), lado / 2.0, borderType=cv2.BORDER_REPLICATE)
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

# Toda la etapa de color va en coma flotante [0, 1] y se cuantiza a 8 bits una
# sola vez al final: neutralizar, quitar verde y saturar en uint8 encadenaba
# redondeos de ±1 nivel que la saturación convertía en un tinte del fondo.

def neutralizar_fondo_final(rgbf):
    """
    Iguala las medianas R, G y B del fondo de cielo desplazando cada canal. La
    reducción de ruido (ida y vuelta por LAB) deja un sesgo de ~1 nivel que la
    saturación multiplicaría después hasta un tinte visible; aquí se elimina.
    """
    L = rgbf.mean(axis=2)
    m = float(np.median(L))
    mad = float(np.median(np.abs(L - m))) * 1.4826 + 1e-4
    fondo = L < m + 2.0 * mad
    if fondo.sum() < 1000:
        return rgbf
    medianas = np.median(rgbf[fondo], axis=0)
    return np.clip(rgbf - (medianas - medianas.mean()).reshape(1, 1, 3), 0, 1).astype(np.float32)


def scnr_verde(rgbf, factor, estrellas=None):
    """
    Elimina el exceso de verde (media de R y B como referencia), estilo SCNR
    average neutral. Las estrellas (máscara 0..1) se dejan fuera: con el
    estirado multiescala ya no saturan a blanco y quitarles el verde las
    teñiría de magenta.
    """
    factor = float(np.clip(factor, 0, 1))
    if factor <= 0:
        return rgbf
    f = rgbf.copy()
    ref = (f[..., 0] + f[..., 2]) / 2
    exceso = np.clip(f[..., 1] - ref, 0, None)
    if estrellas is not None:
        exceso *= (1.0 - estrellas)
    f[..., 1] -= factor * exceso
    return np.clip(f, 0, 1).astype(np.float32)


def saturar(rgbf, factor):
    factor = float(factor)
    if abs(factor - 1.0) < 1e-3:
        return rgbf
    hsv = cv2.cvtColor(np.clip(rgbf, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)   # H 0..360, S y V 0..1
    hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 1)
    return np.clip(cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB), 0, 1).astype(np.float32)


def temperatura_color(rgbf, temperatura):
    """
    Desplaza la tonalidad de lo que está por encima del fondo: temperatura > 0
    hacia el cálido (más rojo, menos azul), < 0 hacia el frío. Se aplica sobre la
    señal respecto al nivel del fondo, así el cielo (ya neutralizado) no se tiñe
    y solo cambian objetos y estrellas. Rango útil -1..1 (±30 % en R y B).
    """
    t = float(np.clip(temperatura, -1, 1))
    if abs(t) < 1e-3:
        return rgbf
    L = rgbf.mean(axis=2)
    m = float(np.median(L))
    mad = float(np.median(np.abs(L - m))) * 1.4826 + 1e-4
    cielo = L < m + 2.0 * mad
    fondo = np.median(rgbf[cielo], axis=0) if cielo.sum() > 1000 else np.array([m, m, m], dtype=np.float32)
    fondo = fondo.reshape(1, 1, 3).astype(np.float32)
    ganancia = np.array([1.0 + 0.3 * t, 1.0, 1.0 - 0.3 * t], dtype=np.float32).reshape(1, 1, 3)
    return np.clip(fondo + (rgbf - fondo) * ganancia, 0, 1).astype(np.float32)


def brillo_general(rgbf, brillo):
    """Curva de medios sobre los tres canales: brillo 1 = raíz cuadrada (mucho más claro), -1 = cuadrado."""
    b = float(np.clip(brillo, -1, 1))
    if abs(b) < 1e-3:
        return rgbf
    return np.power(np.clip(rgbf, 0, 1), 2.0 ** (-b)).astype(np.float32)


def color(rgb8, ajustes):
    """Etapa de color completa sobre la imagen de 8 bits: devuelve uint8."""
    f = rgb8.astype(np.float32) / 255.0
    estrellas = mascara_estrellas(cv2.cvtColor(rgb8, cv2.COLOR_RGB2GRAY), dilatar=2)
    f = neutralizar_fondo_final(f)
    f = temperatura_color(f, ajustes.get("temperatura", 0.0))
    f = brillo_general(f, ajustes.get("brillo", 0.0))
    f = scnr_verde(f, ajustes.get("scnr", 0.8), estrellas)
    f = saturar(f, ajustes.get("saturacion", 1.5))
    return np.clip(f * 255.0 + 0.5, 0, 255).astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline completo
# ─────────────────────────────────────────────────────────────────────────────

# Sub-fases del procesado y la fracción del tiempo total que se lleva cada una
# (medidas en la vista previa; la reducción de ruido es la que manda). Sirven
# para que la web pinte el progreso dentro de la fase "estirando" del apilador.
SUBFASES = (("gradiente", 0.10), ("estirando", 0.20), ("ruido", 0.55), ("contraste", 0.10), ("color", 0.05))


def procesar(cubo_lineal, n_fotos=1, ajustes=None, escala=1.0, progreso=None):
    """
    cubo_lineal: (3, H, W) o (H, W, 3) float lineal (el apilado).
    escala: 0.5 para la vista previa (4 veces más rápido), 1.0 para guardar.
    progreso(subfase, fraccion): opcional; se llama al empezar cada paso (y
    dentro del estirado) con la fracción 0..1 del procesado completo.
    Devuelve uint8 (h, w, 3) RGB listo para JPG.
    """
    ajustes = ajustes or leer_config()
    inicio = {}
    acumulado = 0.0
    for nombre, peso in SUBFASES:
        inicio[nombre] = acumulado
        acumulado += peso
    pesos = dict(SUBFASES)

    def avisar(subfase, dentro=0.0):
        if progreso:
            try:
                progreso(subfase, inicio[subfase] + pesos[subfase] * float(dentro))
            except Exception:
                pass
    rgb = np.asarray(cubo_lineal, dtype=np.float32)
    if rgb.ndim == 3 and rgb.shape[0] == 3:
        rgb = np.transpose(rgb, (1, 2, 0))
    if escala != 1.0:
        h, w = rgb.shape[:2]
        rgb = cv2.resize(rgb, (max(8, int(w * escala)), max(8, int(h * escala))), interpolation=cv2.INTER_AREA)

    avisar("gradiente")
    if ajustes.get("gradiente", True):
        rgb = quitar_gradiente(rgb)
    if ajustes.get("aplanar", False):
        rgb = aplanar_fondo(rgb, float(ajustes.get("escala_aplanado", 0.16)))

    # Neutralización + estirado adaptativo multiescala, en coma flotante
    avisar("estirando")
    if str(ajustes.get("estirado", "multiescala")).lower() == "stf":
        # Estirado clásico (STF enlazado de AutoStrechSTF, el sistema anterior a
        # sep-2026): lo ofrece el editor de la galería para comparar con el nuevo
        from AutoStrechSTF import apply_stf_color
        rgbf = apply_stf_color(rgb, linked=True, neutralizar=True, output_8bit=False).astype(np.float32)
    else:
        rgbf = estirar_multiescala(rgb, ajustes, escala_imagen=escala,
                                   progreso=lambda f: avisar("estirando", f))

    avisar("ruido")
    rgbf = reducir_ruido(rgbf, ajustes.get("ruido", 0.5), n_fotos)
    rgb8 = np.clip(rgbf * 255.0 + 0.5, 0, 255).astype(np.uint8)
    avisar("contraste")
    rgb8 = contraste_local(rgb8, ajustes.get("contraste", 0.5))
    avisar("color")
    return color(rgb8, ajustes)
