# enfoque.py
#
# Ayuda de enfoque para la pantalla Cámara (foto de prueba y modo en vivo):
# «solo verde» para enfocar con una máscara de Bahtinov.
#
# Con la cámara de color cada canal enfoca en un punto distinto, y en la imagen
# RGB se ven tres cruces de Bahtinov que no coinciden: el ojo se queda con el
# pico más contrastado (en una estrella azul brillante, el azul) y el foco
# acaba en el azul. Medido en los apilados reales: el 8-oct-2026 el azul era el
# canal más fino (FWHM B 5,2 px, G 5,6) y las estrellas salían con halos verdes
# y un punto azul dentro de un halo naranja; el 9-oct el verde era el más fino
# (G 3,8, R 4,2, B 4,4) y las estrellas salían blancas. El verde es el canal que
# conviene: tiene el doble de píxeles, está en medio del espectro (rojo y azul
# quedan igual de desenfocados a cada lado) y es el que mide la nitidez del apilado.
#
# Con el modo activado:
#   - La imagen que se enseña es solo el canal verde, en gris, con un estirado
#     suave (el blanco en el pico de la estrella más brillante, para que los
#     picos de difracción no saturen).
#   - Arriba a la derecha va un recorte ampliado de la estrella más brillante,
#     hecho en el mini PC a resolución completa (por el wifi del SynScan solo
#     viaja el JPG pequeño; ampliar el JPG en el móvil no serviría).
#   - Debajo del recorte, la anchura (FWHM) de esa estrella en verde y, en
#     pequeño, en rojo y azul: sin máscara sirve para comprobar que el verde es
#     el canal más fino.
# El estado (activado o no) vive en memoria: es cosa de la sesión, no del config.
#
# De día, con el Sol o la Luna (11-oct-2026): la Bahtinov no sirve en una fuente
# extensa y a la tablet solo llega un JPG de ~750 px de un sensor de 3008, con el
# que el borde del disco se ve igual enfocado que sin enfocar. Con el mismo
# interruptor, si el objetivo es el Sol o la Luna:
#   - Se localiza el disco (círculo por RANSAC sobre el borde de la zona
#     brillante, radio libre: en una Luna en cuarto el terminador no encaja y
#     queda fuera) y se mide la anchura del limbo: la anchura a media altura del
#     pico de la derivada radial (la función de dispersión de línea), mediana de
#     180 ángulos, solo donde el borde es limbo (contraste claro contra el cielo).
#     El limbo real es más fino que un píxel, así que esa anchura es el
#     desenfoque más el seeing. No vale el 10–90 % del perfil: el oscurecimiento
#     del limbo lo infla (18 px frente a 4,4 en las fotos del 10-oct-2026).
#   - Arriba a la derecha, el limbo ampliado ×5 a resolución completa; en la Luna,
#     el terminador (cráteres y sombras: mucho más detalle que el limbo para el
#     ojo), que es la parte del borde de la zona iluminada que no cae en el
#     círculo. En vivo se enseña el recorte del fotograma más nítido de los
#     últimos 10 (el seeing de día hace temblar la imagen) y el número es la
#     mediana de los últimos 5, con el mejor valor de la sesión al lado: se gira
#     el enfocador hasta el mínimo.
#   - La exposición sigue siendo la automática de día (nivel del disco), no la
#     de la estrella.
import math
import threading
import collections
import numpy as np
import cv2
from idioma import _, fmt_num

_lock = threading.Lock()
_estado = {"activo": False}

TAM_RECORTE = 48          # lado del recorte de la estrella, en píxeles del sensor
AMPLIACION = 5            # el recorte se enseña a ×5
MARGEN = 24               # la estrella más brillante se busca lejos de los bordes
NIVEL_PICO_OBJETIVO = 0.6 # en vivo: el pico de la estrella al 60 % del rango (sin saturar)

OBJETIVOS_DISCO = ("sol", "luna")
ANGULOS_LIMBO = 180       # perfiles radiales por fotograma
VENTANA_LIMBO = 8         # ± píxeles del plano alrededor del radio ajustado
PASO_PERFIL = 0.25        # muestreo del perfil radial (px del plano)
ULTIMOS_FOTOGRAMAS = 10   # en vivo: el recorte es el del fotograma más nítido de estos
MEDIANA_FOTOGRAMAS = 5    # en vivo: el número es la mediana de los últimos 5


def activo():
    with _lock:
        return bool(_estado["activo"])


def poner(valor):
    with _lock:
        _estado["activo"] = bool(valor)
        return bool(_estado["activo"])


# ─────────────────────────────────────────────────────────────────────────────
#  Canal verde
# ─────────────────────────────────────────────────────────────────────────────
_POS_VERDES = {"RGGB": ((0, 1), (1, 0)), "BGGR": ((0, 1), (1, 0)), "GRBG": ((0, 0), (1, 1)), "GBRG": ((0, 0), (1, 1))}


def verde_superpixel(cfa, patron, paso=1):
    """
    Verde del mosaico Bayer sin interpolar: la media de los dos G de cada bloque
    2×2 (mitad de resolución; con `paso` > 1 uno de cada `paso` bloques). Para
    un sensor mono o un patrón desconocido, el propio mosaico reducido.
    """
    f = np.asarray(cfa)
    h, w = f.shape[0] & ~1, f.shape[1] & ~1
    s = 2 * int(paso)
    if str(patron).upper() not in _POS_VERDES:
        return f[0:h:s, 0:w:s].astype(np.float32)
    (i1, j1), (i2, j2) = _POS_VERDES[str(patron).upper()]
    return (f[i1:h:s, j1:w:s].astype(np.float32) + f[i2:h:s, j2:w:s].astype(np.float32)) * 0.5


def revelar_gris(plano, negro=None):
    """
    Canal verde lineal → gris uint8 para enfocar: negro en el fondo de cielo,
    blanco en el pico más alto (la estrella más brillante no satura y sus picos
    de difracción se ven enteros), gamma 0,5 para que el fondo y los picos
    débiles también se vean.
    """
    x = np.asarray(plano, dtype=np.float32)
    sub = x[::4, ::4]
    fondo = float(np.median(sub)) if negro is None else float(negro)
    blanco = max(float(np.percentile(sub, 99.99)), float(x.max()), fondo + 1.0)
    y = np.clip((x - fondo) / (blanco - fondo), 0.0, 1.0)
    return (np.sqrt(y) * 255.0 + 0.5).astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
#  La estrella más brillante y su anchura
# ─────────────────────────────────────────────────────────────────────────────
def estrella_mas_brillante(plano, margen=MARGEN):
    """(x, y, pico) de la estrella más brillante, lejos de los bordes; los píxeles calientes no cuentan (suavizado)."""
    x = np.asarray(plano, dtype=np.float32)
    suav = cv2.GaussianBlur(x, (0, 0), 1.2)
    m = max(1, min(int(margen), min(suav.shape) // 4))
    interior = suav[m:-m, m:-m]
    iy, ix = np.unravel_index(int(np.argmax(interior)), interior.shape)
    return ix + m, iy + m, float(x[iy + m, ix + m])


def fwhm(plano, x, y, radio=7):
    """Anchura a media altura (px) por el segundo momento de la luz alrededor de (x, y), con el fondo del borde restado."""
    p = np.asarray(plano, dtype=np.float32)
    y0, y1, x0, x1 = max(0, y - radio), min(p.shape[0], y + radio + 1), max(0, x - radio), min(p.shape[1], x + radio + 1)
    c = p[y0:y1, x0:x1]
    if c.size < 9:
        return float("nan")
    borde = np.concatenate([c[0], c[-1], c[:, 0], c[:, -1]])
    w = np.clip(c - float(np.median(borde)), 0, None)
    if w.sum() <= 0:
        return float("nan")
    yy, xx = np.mgrid[y0:y1, x0:x1]
    cx, cy = (xx * w).sum() / w.sum(), (yy * w).sum() / w.sum()
    s2 = (((xx - cx) ** 2 + (yy - cy) ** 2) * w).sum() / w.sum() / 2.0
    return float(2.355 * np.sqrt(max(s2, 1e-3)))


def medir(verde, rojo=None, azul=None, escala_px=1.0):
    """
    Estrella más brillante del verde y su FWHM en cada canal disponible, en
    píxeles del sensor (`escala_px` = píxeles del sensor por píxel del plano:
    2 con el verde superpíxel). Devuelve un dict para la web.
    """
    x, y, pico = estrella_mas_brillante(verde)
    info = {"x": int(x), "y": int(y), "pico": round(pico), "fwhm_g": round(fwhm(verde, x, y) * escala_px, 2),
            "saturada": bool(pico >= 0.97 * 65535)}     # con el núcleo recortado la anchura sale falsa
    for nombre, plano in (("fwhm_r", rojo), ("fwhm_b", azul)):
        if plano is not None:
            info[nombre] = round(fwhm(plano, x, y) * escala_px, 2)
    return info


def texto_info(info, para_imagen=False):
    """
    Línea corta: «FWHM verde 3,8 px · rojo 4,2 · azul 4,4». Para dibujarla en la
    imagen (cv2.putText solo tiene ASCII) va con «|» y un aviso corto de saturación.
    """
    if not info:
        return ""
    sep = "  |  " if para_imagen else " · "
    if "limbo" in info:
        if info.get("limbo") is None:
            return _("limbo: {motivo}", motivo=info.get("motivo") or _("no se ve el disco"))
        partes = [_("Limbo {px} px", px=fmt_num(info['limbo'], 1))]
        if info.get("mejor") is not None:
            partes.append(_("mejor {px}", px=fmt_num(info['mejor'], 1)))
        if not para_imagen and info.get("n"):
            partes.append(_("{n} ángulos", n=info['n']))
        return sep.join(partes)
    if not np.isfinite(info.get("fwhm_g", float("nan"))):
        return ""
    partes = [_("FWHM verde {px} px", px=fmt_num(info['fwhm_g'], 1))]
    if np.isfinite(info.get("fwhm_r", float("nan"))):
        partes.append(_("rojo {px}", px=fmt_num(info["fwhm_r"], 1)))
    if np.isfinite(info.get("fwhm_b", float("nan"))):
        partes.append(_("azul {px}", px=fmt_num(info["fwhm_b"], 1)))
    if info.get("saturada"):
        partes.append(_("SATURADA") if para_imagen else _("saturada: baja la exposición o la ganancia"))
    return sep.join(partes)


# ─────────────────────────────────────────────────────────────────────────────
#  Imagen para la web: gris + recorte ampliado + texto
# ─────────────────────────────────────────────────────────────────────────────
def recorte_estrella(gris8, x, y, tam=TAM_RECORTE, ampliacion=AMPLIACION):
    """Recorte tam×tam alrededor de (x, y) ampliado ×ampliacion (cúbica: los picos de la Bahtinov se ven continuos)."""
    h, w = gris8.shape[:2]
    r = tam // 2
    x0, y0 = min(max(0, x - r), max(0, w - tam)), min(max(0, y - r), max(0, h - tam))
    c = gris8[y0:y0 + tam, x0:x0 + tam]
    return cv2.resize(c, (c.shape[1] * ampliacion, c.shape[0] * ampliacion), interpolation=cv2.INTER_CUBIC)


def componer(gris8, recorte, info, pulsado=None):
    """
    Imagen BGR para la web: el verde en gris, el recorte de la estrella en la
    esquina superior derecha (con marco) y la línea de anchuras debajo. Si
    `pulsado` (x, y) se da, se marca la estrella en la imagen grande.
    """
    base = cv2.cvtColor(gris8, cv2.COLOR_GRAY2BGR)
    h, w = base.shape[:2]
    lado = min(h, w)
    if recorte is not None:
        # El recorte ocupa una fracción fija del lado: la foto de noche (3008 px) se
        # reduce a 1600 px para la web y a ~600 en el móvil, y el recorte debe seguir viéndose
        rl = int(lado * (0.4 if lado < 800 else 0.3))
        rec = cv2.cvtColor(cv2.resize(recorte, (rl, rl), interpolation=cv2.INTER_CUBIC), cv2.COLOR_GRAY2BGR)
        m = max(6, lado // 100)
        base[m:m + rl, w - m - rl:w - m] = rec
        grosor = max(2, lado // 500)
        cv2.rectangle(base, (w - m - rl - 1, m - 1), (w - m, m + rl), (80, 200, 80), grosor)
        texto = texto_info(info, para_imagen=True)
        if texto:
            escala = max(0.45, min(2.0, rl / 420.0))
            g_txt = max(1, int(round(escala * 1.3)))
            (tw, th), __ = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SIMPLEX, escala, g_txt)
            ox, oy = max(2, w - m - tw), min(h - 6, m + rl + th + m)
            cv2.putText(base, texto, (ox, oy), cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), g_txt + 3, cv2.LINE_AA)
            cv2.putText(base, texto, (ox, oy), cv2.FONT_HERSHEY_SIMPLEX, escala, (120, 255, 120), g_txt, cv2.LINE_AA)
    if pulsado is not None:
        cv2.circle(base, (int(pulsado[0]), int(pulsado[1])), max(10, lado // 90), (80, 200, 80), max(1, lado // 1000), cv2.LINE_AA)
    return base


# ─────────────────────────────────────────────────────────────────────────────
#  Sol y Luna: el disco y la anchura del limbo
# ─────────────────────────────────────────────────────────────────────────────
def es_disco(objetivo):
    return str(objetivo or "").lower() in OBJETIVOS_DISCO


def revelar_gris_disco(plano):
    """Verde lineal → gris uint8 lineal, negro en el cielo y blanco en lo más brillante del disco (sin gamma: se ve el limbo tal cual)."""
    x = np.asarray(plano, dtype=np.float32)
    sub = x[::4, ::4]
    negro, blanco = float(np.percentile(sub, 0.5)), float(np.percentile(sub, 99.9))
    blanco = max(blanco, negro + 1.0)
    alfa = 255.0 / (blanco - negro)
    return cv2.convertScaleAbs(x, alpha=alfa, beta=-negro * alfa)      # recorta a 0–255 él solo


def _circulo_3_puntos(a, b, c):
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-6:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def circulo_disco(plano, semilla=0, reduccion=2, con_borde=False):
    """
    (cx, cy, r) del disco brillante, en píxeles del plano, o None. Borde de la zona
    brillante y RANSAC de tres puntos con el radio libre: en una Luna en cuarto el
    terminador no encaja en el círculo y se queda fuera; después se afina con los
    puntos que encajan. Se trabaja sobre el plano reducido `reduccion` veces (basta:
    el limbo se busca luego en una ventana de ±VENTANA_LIMBO px alrededor del radio).
    Con `con_borde` devuelve además los puntos del borde (px del plano) para buscar
    el terminador.
    """
    k = max(1, int(reduccion))
    x = np.asarray(plano, dtype=np.float32)[::k, ::k]
    h, w = x.shape
    suave = cv2.GaussianBlur(x, (0, 0), 1.0)
    sub = suave[::4, ::4]
    bajo, alto = float(np.percentile(sub, 2)), float(np.percentile(sub, 99.5))
    if alto - bajo < 0.05 * max(alto, 1.0):
        return None
    mascara = (suave > bajo + 0.25 * (alto - bajo)).astype(np.uint8)
    n, etiquetas, stats, __ = cv2.connectedComponentsWithStats(mascara, 8)
    if n <= 1:
        return None
    mayor = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[mayor, cv2.CC_STAT_AREA] < 400:
        return None
    comp = (etiquetas == mayor).astype(np.uint8)
    contornos, __ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    p = max(contornos, key=len).reshape(-1, 2).astype(np.float64)
    p = p[(p[:, 0] > 1) & (p[:, 0] < w - 2) & (p[:, 1] > 1) & (p[:, 1] < h - 2)]    # el borde de la imagen no es limbo
    if len(p) < 30:
        return None
    rng = np.random.default_rng(semilla)
    if len(p) > 1500:
        p = p[rng.choice(len(p), 1500, replace=False)]
    mejor, mejor_n = None, 0
    for __ in range(300):
        a, b, c = p[rng.choice(len(p), 3, replace=False)]
        circ = _circulo_3_puntos(a, b, c)
        if circ is None or circ[2] < 10 or circ[2] > 2 * max(h, w):
            continue
        cx, cy, r = circ
        n_in = int(np.sum(np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r) < max(1.5, 0.01 * r)))
        if n_in > mejor_n:
            mejor, mejor_n = circ, n_in
    if mejor is None or mejor_n < 0.3 * len(p):
        return None
    cx, cy, r = mejor
    inl = p[np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r) < max(1.5, 0.01 * r)]
    A = np.c_[2 * inl[:, 0], 2 * inl[:, 1], np.ones(len(inl))]
    sol, *__ = np.linalg.lstsq(A, inl[:, 0] ** 2 + inl[:, 1] ** 2, rcond=None)
    cx, cy = float(sol[0]), float(sol[1])
    r = float(math.sqrt(max(sol[2] + cx * cx + cy * cy, 1.0)))
    # Coordenadas del plano entero: el píxel (i) del reducido es el (k·i) del entero, centrado en k·i + (k−1)/2
    if con_borde:
        return k * cx + (k - 1) / 2.0, k * cy + (k - 1) / 2.0, k * r, p * k + (k - 1) / 2.0
    return k * cx + (k - 1) / 2.0, k * cy + (k - 1) / 2.0, k * r


def punto_terminador(cx, cy, r, borde):
    """
    (x, y) del terminador de la Luna para el recorte, o None si no lo hay (llena, o
    el Sol). Terminador = puntos del borde de la zona iluminada que quedan claramente
    dentro del círculo; se toma el más cercano al centro del disco (en cuarto es el
    centro; en creciente o gibosa, el medio del arco).
    """
    if borde is None or len(borde) < 20:
        return None
    dist = np.hypot(borde[:, 0] - cx, borde[:, 1] - cy)
    tol = max(3.0, 0.03 * r)
    dentro = borde[dist < r - tol]
    if len(dentro) < 20:
        return None
    d2 = np.hypot(dentro[:, 0] - cx, dentro[:, 1] - cy)
    p = dentro[int(np.argmin(d2))]
    return int(round(p[0])), int(round(p[1]))


def anchura_limbo(plano, cx, cy, r, n_ang=ANGULOS_LIMBO, ventana=VENTANA_LIMBO, paso=PASO_PERFIL):
    """
    Anchura a media altura del pico de la derivada radial en el limbo, por ángulo
    (px del plano). Solo cuentan los ángulos donde hay limbo: dentro claramente
    más brillante que fuera y el pico entero dentro de la ventana (en la Luna, el
    terminador y la parte en sombra quedan fuera). Devuelve (anchuras, ángulos,
    radio del pico) de los ángulos válidos.
    """
    x = np.asarray(plano, dtype=np.float32)
    h, w = x.shape
    rs = np.arange(r - ventana, r + ventana + paso / 2, paso, dtype=np.float32)
    angs = np.linspace(0, 2 * np.pi, n_ang, endpoint=False).astype(np.float32)
    mx = (cx + rs[None, :] * np.cos(angs)[:, None]).astype(np.float32)
    my = (cy + rs[None, :] * np.sin(angs)[:, None]).astype(np.float32)
    perfiles = cv2.remap(x, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=-1.0)
    dentro_img = (mx.min(axis=1) >= 1) & (my.min(axis=1) >= 1) & (mx.max(axis=1) <= w - 2) & (my.max(axis=1) <= h - 2)
    k_lado = max(2, int(round(3.0 / paso)))
    deriv = -np.gradient(perfiles, paso, axis=1)
    anchuras, validos, radios = [], [], []
    for i in range(n_ang):
        if not dentro_img[i]:
            continue
        p, dp = perfiles[i], deriv[i]
        dentro, fuera = float(np.median(p[:k_lado])), float(np.median(p[-k_lado:]))
        if dentro <= 0 or dentro - fuera < 0.25 * dentro:
            continue
        k = int(np.argmax(dp))
        pico = dp[k]
        if pico <= 0 or k < 2 or k > len(dp) - 3:
            continue
        a = k
        while a > 0 and dp[a] > pico / 2:
            a -= 1
        b = k
        while b < len(dp) - 1 and dp[b] > pico / 2:
            b += 1
        if a == 0 or b == len(dp) - 1:
            continue
        xa = a + (pico / 2 - dp[a]) / (dp[a + 1] - dp[a])
        xb = b - (pico / 2 - dp[b]) / (dp[b - 1] - dp[b])
        anchura = (xb - xa) * paso
        if not (0 < anchura < 2 * ventana):
            continue
        anchuras.append(anchura)
        validos.append(float(angs[i]))
        radios.append(float(rs[k]))
    return np.array(anchuras), np.array(validos), np.array(radios)


def medir_disco(verde, escala_px=1.0, n_ang=ANGULOS_LIMBO, terminador=False):
    """
    Disco en el plano verde y anchura de su limbo en píxeles del sensor
    (`escala_px` = píxeles del sensor por píxel del plano; 2 con el superpíxel).
    Devuelve un dict para la web: limbo (None si no se mide, con `motivo`), n
    (ángulos medidos), arco (fracción del contorno que es limbo), x, y (punto para
    el recorte, px del plano), recorte («limbo» o «terminador») y el círculo
    (cx, cy, r). Con `terminador` (la Luna) el recorte va al terminador si lo hay.
    """
    c = circulo_disco(verde, con_borde=True)
    if c is None:
        return {"limbo": None, "motivo": _("no se ve el disco")}
    cx, cy, r, borde = c
    anch, angs, radios = anchura_limbo(verde, cx, cy, r, n_ang=n_ang)
    info = {"cx": round(cx, 1), "cy": round(cy, 1), "r": round(r, 1)}
    if len(anch) < 12:
        info.update({"limbo": None, "motivo": _("el limbo no se ve bien (¿nubes, saturado, casi fuera de campo?)")})
        return info
    # El recorte: el punto del limbo más cercano a «arriba» del plano (el borde queda horizontal, cielo arriba)
    d = np.abs((angs + np.pi / 2 + np.pi) % (2 * np.pi) - np.pi)
    k = int(np.argmin(d))
    info.update({"limbo": round(float(np.median(anch)) * escala_px, 2), "n": int(len(anch)),
                 "arco": round(len(anch) / float(n_ang), 2), "recorte": "limbo",
                 "x": int(round(cx + radios[k] * math.cos(angs[k]))), "y": int(round(cy + radios[k] * math.sin(angs[k])))})
    if terminador:
        pt = punto_terminador(cx, cy, r, borde)
        if pt is not None:
            info.update({"x": pt[0], "y": pt[1], "recorte": "terminador"})
    return info


class HistorialLimbo:
    """
    En vivo: el recorte que se enseña es el del fotograma más nítido de los
    últimos ULTIMOS_FOTOGRAMAS (el seeing de día hace temblar la imagen), el
    número es la mediana de los últimos MEDIANA_FOTOGRAMAS y se guarda el mejor
    de la sesión para saber si se ha pasado el foco.
    """
    def __init__(self):
        self.ultimos = collections.deque(maxlen=ULTIMOS_FOTOGRAMAS)
        self.mejor = None

    def anotar(self, anchura, recorte):
        if anchura is not None and np.isfinite(anchura):
            self.ultimos.append((float(anchura), recorte))

    def resumen(self):
        """(anchura actual, mejor de la sesión, recorte más nítido) o (None, None, None)."""
        if not self.ultimos:
            return None, self.mejor, None
        anchuras = [a for a, __ in self.ultimos]
        actual = float(np.median(anchuras[-MEDIANA_FOTOGRAMAS:]))
        if self.mejor is None or actual < self.mejor:
            self.mejor = actual
        return actual, self.mejor, min(self.ultimos, key=lambda t: t[0])[1]


def _punto_en_vista(x, y, forma, paso, orientar, girar):
    """Dónde cae el punto (x, y) del plano en la imagen que se enseña (reducida, orientada y girada): se gira una marca."""
    marca = np.zeros((forma[0] // paso + (1 if forma[0] % paso else 0), forma[1] // paso + (1 if forma[1] % paso else 0)), np.uint8)
    yy, xx = min(marca.shape[0] - 1, int(y) // paso), min(marca.shape[1] - 1, int(x) // paso)
    marca[yy, xx] = 255
    girada, __ = girar(orientar(np.ascontiguousarray(marca)))
    iy, ix = np.unravel_index(int(np.argmax(girada)), girada.shape[:2])
    return int(ix), int(iy)


def imagen_vivo_disco(cfa, patron, paso, orientar, girar, historial, objetivo=None):
    """
    Fotograma del vivo con el Sol o la Luna: el verde en gris, el limbo ampliado
    (el del fotograma más nítido de los últimos; en la Luna, el terminador) y la
    anchura del limbo. `historial`: HistorialLimbo de esta sesión del vivo.
    Devuelve (BGR, encuadre, info).
    """
    verde = verde_superpixel(cfa, patron, 1)
    info = medir_disco(verde, escala_px=2.0, terminador=(str(objetivo or "").lower() == "luna"))
    gris = revelar_gris_disco(verde)
    pulsado = None
    if info.get("limbo") is not None:
        historial.anotar(info["limbo"], recorte_estrella(gris, info["x"], info["y"]))
        pulsado = _punto_en_vista(info["x"], info["y"], gris.shape, paso, orientar, girar)
    actual, mejor, recorte = historial.resumen()
    if actual is not None:
        info["limbo_foto"] = info.get("limbo")
        info["limbo"] = round(actual, 2)
        info["mejor"] = round(mejor, 2)
        info.pop("motivo", None)
    vista = gris[::paso, ::paso] if paso > 1 else gris
    girada, encuadre = girar(orientar(np.ascontiguousarray(vista)))
    return componer(girada, recorte, info, pulsado=pulsado), encuadre, info


def imagen_foto_dia_disco(cfa, patron, girar, objetivo=None):
    """
    Foto suelta de día del Sol o la Luna con el modo activado: el verde en gris
    (superpíxel, mitad de resolución), el limbo ampliado (en la Luna, el terminador)
    y la anchura del limbo. `cfa` en la orientación de ASCOM. Devuelve (BGR, encuadre, info).
    """
    verde = verde_superpixel(cfa, patron, 1)
    info = medir_disco(verde, escala_px=2.0, terminador=(str(objetivo or "").lower() == "luna"))
    gris = revelar_gris_disco(verde)
    recorte, pulsado = None, None
    if info.get("limbo") is not None:
        recorte = recorte_estrella(gris, info["x"], info["y"])
        pulsado = _punto_en_vista(info["x"], info["y"], gris.shape, 1, lambda im: im, girar)
    girada, encuadre = girar(gris)
    return componer(girada, recorte, info, pulsado=pulsado), encuadre, info


def imagen_foto_noche(rgb_lineal, girar):
    """
    Foto de prueba de noche con el modo activado. rgb_lineal: (H, W, 3) float del
    debayer (ya con dark y flat), en la orientación del FITS; girar: función
    (imagen) → (imagen girada, encuadre), la de giro_camara. Devuelve
    (imagen BGR para la web, encuadre, info).
    """
    r, g, b = (np.asarray(rgb_lineal[..., i], dtype=np.float32) for i in range(3))
    info = medir(g, r, b, escala_px=1.0)
    gris = revelar_gris(g)
    recorte = recorte_estrella(gris, info["x"], info["y"])
    girada, encuadre = girar(gris)
    # La estrella en la imagen girada: se vuelve a localizar en el gris girado (barato)
    xg, yg, __ = estrella_mas_brillante(girada)
    return componer(girada, recorte, info, pulsado=(xg, yg)), encuadre, info


def imagen_vivo(cfa, patron, paso, orientar, girar):
    """
    Fotograma del modo en vivo con el modo activado. cfa: mosaico crudo del SDK;
    paso: el de la vista reducida; orientar: función (imagen 2D) → imagen en la
    orientación de ASCOM; girar: la de giro_camara. Devuelve (imagen BGR, encuadre, info).
    """
    verde = verde_superpixel(cfa, patron, 1)                  # resolución completa del superpíxel (2 px del sensor)
    info = medir(verde, escala_px=2.0)
    gris_completo = revelar_gris(verde)
    recorte = recorte_estrella(gris_completo, info["x"], info["y"])
    vista = gris_completo[::paso, ::paso] if paso > 1 else gris_completo
    vista = orientar(np.ascontiguousarray(vista))
    girada, encuadre = girar(vista)
    xg, yg, __ = estrella_mas_brillante(girada)
    return componer(girada, recorte, info, pulsado=(xg, yg)), encuadre, info


def nueva_exposicion_estrella(exposicion_s, cfa, minima_s, maxima_s, blanco=65535.0):
    """
    En vivo con el modo activado la exposición busca el pico de la estrella más
    brillante al NIVEL_PICO_OBJETIVO del rango, no el nivel del cielo: con el
    cielo de noche la automática de día subiría hasta el tope y saturaría la
    estrella. Amortiguada como vivo_dia.nueva_exposicion. Devuelve (exposición, nivel del pico).
    """
    muestra = cv2.medianBlur(np.asarray(cfa, dtype=np.uint16), 3)    # sin píxeles calientes
    pico = float(muestra.max()) / float(blanco)
    if pico >= 0.97:
        factor = 0.5
    else:
        factor = (NIVEL_PICO_OBJETIVO / max(pico, 0.01)) ** 0.8
        factor = max(0.25, min(4.0, factor))
    if abs(np.log(factor)) < np.log(1.12):
        return exposicion_s, pico
    return max(minima_s, min(maxima_s, exposicion_s * factor)), pico
