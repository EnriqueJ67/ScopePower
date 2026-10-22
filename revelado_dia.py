# revelado_dia.py
#
# Revelado de las fotos de día (Sol, Luna, paisaje): el apilado de un vídeo o una foto
# suelta, a partir de la imagen lineal en color. Lo usan apilado_video.py al apilar y el
# editor de día de la galería (editor_dia.py) para volver a revelar con otros ajustes.
#
# Lo que hace, en orden, sobre la imagen lineal (negro restado y balance de blancos ya
# aplicados):
#   1. Nitidez: deconvolución Richardson-Lucy del disco con el desenfoque medido en su
#      limbo (apilado_video.deconvolucionar_disco), 4 iteraciones por punto. Sin disco,
#      una máscara de desenfoque.
#   2. Blanco: lo más brillante de la imagen (percentil 99,995) va al blanco, con la
#      compensación de exposición (±EV). Antes el blanco era el percentil 99,9 ANTES del
#      balance, y el balance (azul ×2 en la Luna) empujaba las luces por encima: el 1,5 %
#      más brillante del disco salía blanco puro, sin detalle (vídeos del 21-oct-2026).
#   3. Detalle: realce local por escalas (2, 4 y 8 px, como las capas de Registax), solo
#      dentro del disco para que el cielo no gane un halo.
#   4. Curva de tonos: gamma suave, rodilla en las luces (se comprimen, no se recortan) y
#      el contraste como una S alrededor de los medios tonos que no toca ni el negro ni
#      el blanco.
#   5. Color: saturación. 1 = la natural; con 3–5 en la Luna aparecen los tonos de los
#      mares (azules, con titanio) y de las tierras altas (ocres): el «color de la Luna».
#      El balance medido en el disco deja la Luna neutra, que es el punto de partida que
#      hace falta. La crominancia se suaviza antes de amplificarla para no amplificar el
#      ruido de color.

import logging

import numpy as np

log = logging.getLogger("scopepower.revelado_dia")

AJUSTES_DEFECTO = {"exposicion": 0.0, "contraste": 1.0, "detalle": 1.0, "nitidez": 1.0, "color": 1.0, "firma": True}
LIMITES = {"exposicion": (-2.0, 2.0), "contraste": (0.5, 2.0), "detalle": (0.0, 3.0), "nitidez": (0.0, 3.0), "color": (0.0, 5.0)}
GAMMA = 1.3                    # curva de tonos (antes 1,6: aplanaba las luces)
RODILLA = 0.75                 # por encima, las luces se comprimen hacia el blanco sin recortar
PERCENTIL_BLANCO = 99.995      # lo más brillante de la imagen va al blanco
UMBRAL_DISCO = 0.04            # fracción del blanco por encima de la cual un píxel es «disco» (no cielo)
ESCALAS_DETALLE = ((2.0, 0.8), (4.0, 0.8), (8.0, 0.5))     # (sigma px, ganancia) con detalle = 1
LIMITE_DETALLE = 0.5           # la capa de detalle no pasa de esta fracción del nivel local (sin anillos)
SIGMA_CROMA = 1.5              # suavizado de la crominancia antes de saturar (px)


def normalizar(ajustes):
    """Ajustes completos y dentro de sus límites a partir de lo que mande la web (o nada)."""
    a = dict(AJUSTES_DEFECTO)
    for clave, (lo, hi) in LIMITES.items():
        try:
            v = float((ajustes or {}).get(clave, a[clave]))
        except (TypeError, ValueError):
            v = a[clave]
        a[clave] = min(hi, max(lo, v))
    a["firma"] = bool((ajustes or {}).get("firma", a["firma"]))
    return a


# ─────────────────────────────────────────────────────────────────────────────
#  Imagen lineal
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_balance(rgb, negro, balance):
    """RGB lineal (negro restado, balance de blancos aplicado), float32, a partir de la media o del desmosaicado."""
    img = np.clip(np.asarray(rgb, dtype=np.float32) - float(negro or 0.0), 0.0, None)
    if balance:
        img[..., 0] *= float(balance.get("r", 1.0))
        img[..., 2] *= float(balance.get("b", 1.0))
    return img


def lineal_de_cfa(cfa, patron, negro, balance):
    """Fotograma crudo (mosaico Bayer, 16 bits) → RGB lineal float32 a resolución completa."""
    import apilado_video
    return aplicar_balance(apilado_video.desmosaicar(cfa, patron), negro, balance)


def reducir(lineal, ancho):
    """(imagen reducida, escala) con el lado mayor a `ancho` px, o la misma si ya es menor."""
    import cv2
    h, w = lineal.shape[:2]
    if max(h, w) <= ancho:
        return lineal, 1.0
    escala = ancho / float(max(h, w))
    return cv2.resize(lineal, (max(8, int(round(w * escala))), max(8, int(round(h * escala)))), interpolation=cv2.INTER_AREA), escala


# ─────────────────────────────────────────────────────────────────────────────
#  Pasos del revelado
# ─────────────────────────────────────────────────────────────────────────────
def luminancia(rgb):
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def mascara_disco(lum01, escala=1.0):
    """Peso 0..1 suave: 1 en el disco (o el paisaje), 0 en el cielo; evita el halo del realce."""
    import cv2
    m = (lum01 > UMBRAL_DISCO).astype(np.float32)
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, 3.0 * escala))
    return np.clip(m, 0.0, 1.0)[..., None]


def detalle_local(img01, cantidad, escala=1.0, mascara=None):
    """
    Realce multiescala: a cada escala se suma lo que hay entre dos desenfoques, con la
    ganancia de la escala por `cantidad`, acotado a una fracción del nivel local para no
    anillar, y solo donde `mascara` lo permite.
    """
    import cv2
    if cantidad <= 0:
        return img01
    out = img01.copy()
    previa = img01
    for sigma, ganancia in ESCALAS_DETALLE:
        suave = cv2.GaussianBlur(img01, (0, 0), max(0.6, sigma * escala))
        capa = previa - suave
        tope = LIMITE_DETALLE * np.abs(suave) + 0.01
        capa = np.clip(capa, -tope, tope)
        if mascara is not None:
            capa = capa * mascara
        out += float(cantidad) * ganancia * capa
        previa = suave
    return np.clip(out, 0.0, None)


def curva_tonos(img01, contraste=1.0):
    """
    Gamma suave con rodilla en las luces y contraste en S alrededor de los medios tonos
    (y^c / (y^c + (1 − y)^c)), que deja fijos el negro, el medio tono y el blanco.
    """
    y = np.power(np.clip(img01, 0.0, None), 1.0 / GAMMA)
    # Rodilla: por encima de RODILLA la curva se aplana y deja sitio a lo que pase del blanco
    # (el realce de detalle y la exposición positiva), con el blanco (1) fijo en 1
    alto = y > RODILLA
    k = 1.0 / (1.0 - RODILLA)
    norma = 1.0 - np.exp(-k * (1.0 - RODILLA))
    y = np.where(alto, RODILLA + (1.0 - RODILLA) * (1.0 - np.exp(-k * (y - RODILLA))) / norma, y)
    y = np.clip(y, 0.0, 1.0)
    c = float(contraste)
    if abs(c - 1.0) > 1e-3:
        # S alrededor del medio tono con pendiente c en ½ y los extremos fijos: y^c / (y^c + (1−y)^c)
        yc = np.power(y, c)
        y = yc / (yc + np.power(1.0 - y, c) + 1e-12)
    return np.clip(y, 0.0, 1.0)


def saturar(rgb01, factor, escala=1.0):
    """Saturación `factor` (1 = natural, 0 = blanco y negro) a luminancia constante, con la crominancia suavizada."""
    import cv2
    f = float(factor)
    if abs(f - 1.0) < 1e-3:
        return rgb01
    ycc = cv2.cvtColor(np.ascontiguousarray(rgb01, dtype=np.float32), cv2.COLOR_RGB2YCrCb)
    croma = ycc[..., 1:] - 0.5
    if f > 1.0:
        croma = cv2.GaussianBlur(croma, (0, 0), max(0.6, SIGMA_CROMA * escala))
    ycc[..., 1:] = 0.5 + croma * f
    return np.clip(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB), 0.0, 1.0)


def revelar(lineal, ajustes=None, limbo_px=None, escala=1.0, progreso=None, devolver_lineal=False):
    """
    Imagen lineal RGB float32 (negro y balance aplicados) → RGB uint8 revelado.
    limbo_px: FWHM del desenfoque medida en el limbo del disco, en píxeles de la imagen
    completa (None sin disco). escala: tamaño de `lineal` respecto a la completa (vista
    previa reducida). Con devolver_lineal, también la lineal afinada y normalizada al blanco
    (0..1, sin curva ni color), para el FITS.
    """
    import apilado_video
    a = normalizar(ajustes)
    img = np.asarray(lineal, dtype=np.float32)
    iteraciones = int(round(apilado_video.ITERACIONES_POR_PUNTO * a["nitidez"]))
    iteraciones = min(iteraciones, apilado_video.ITERACIONES_POR_PUNTO * 3)
    if iteraciones and limbo_px:
        img = apilado_video.deconvolucionar_disco(img, iteraciones, float(limbo_px) * float(escala), progreso)
    muestra = img[::2, ::2] if img.shape[0] * img.shape[1] > 1_500_000 else img
    blanco = max(float(np.percentile(muestra, PERCENTIL_BLANCO)), 1.0) / 2.0 ** a["exposicion"]
    img = img / blanco
    lineal01 = np.clip(img, 0.0, 1.0) if devolver_lineal else None
    if a["detalle"] > 0:
        img = detalle_local(img, a["detalle"], escala, mascara_disco(luminancia(img), escala))
    if iteraciones and not limbo_px:
        img = np.clip(apilado_video.afinar(np.clip(img, 0.0, 1.0), a["nitidez"]), 0.0, None)
    img = curva_tonos(img, a["contraste"])
    img = saturar(img, a["color"], escala)
    rgb8 = (img * 255.0 + 0.5).astype(np.uint8)
    return (rgb8, lineal01) if devolver_lineal else rgb8


def texto_ajustes(ajustes):
    a = normalizar(ajustes)
    return "EV {:+.1f}, contraste {:.1f}, detalle {:.1f}, nitidez {:.1f}, color {:.1f}".format(
        a["exposicion"], a["contraste"], a["detalle"], a["nitidez"], a["color"])


if __name__ == "__main__":
    # Comprobaciones puras: la curva deja fijos negro y blanco, el detalle no toca el cielo,
    # la saturación conserva la luminancia y con 0 deja gris
    x = np.linspace(0, 1, 11, dtype=np.float32)[:, None, None].repeat(3, axis=2)
    for c in (0.5, 1.0, 2.0):
        y = curva_tonos(x, c)
        assert abs(float(y[0, 0, 0])) < 1e-6 and abs(float(y[-1, 0, 0]) - 1.0) < 1e-6, (c, y[0, 0, 0], y[-1, 0, 0])
        assert np.all(np.diff(y[:, 0, 0]) > 0), "la curva debe ser creciente"
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:200, 0:200]
    disco = ((xx - 100) ** 2 + (yy - 100) ** 2 < 60 ** 2).astype(np.float32)
    img = np.dstack([disco * 0.8] * 3) + rng.normal(0, 0.002, (200, 200, 3)).astype(np.float32)
    img[70:80, 90:110] *= 0.5                                   # un «cráter»
    con = detalle_local(np.clip(img, 0, None), 2.0, 1.0, mascara_disco(luminancia(np.clip(img, 0, None))))
    cielo = ((xx - 100) ** 2 + (yy - 100) ** 2 > 75 ** 2)
    assert float(np.abs(con - img)[cielo].max()) < 0.02, "el realce no debe tocar el cielo"
    assert float(np.abs(con - img)[70:80, 90:110].mean()) > 0.02, "el realce debe marcar el cráter"
    rgb = np.dstack([np.full((50, 50), 0.6), np.full((50, 50), 0.55), np.full((50, 50), 0.5)]).astype(np.float32)
    sat = saturar(rgb, 3.0)
    assert abs(float(luminancia(sat).mean() - luminancia(rgb).mean())) < 0.01
    assert float(sat[..., 0].mean() - sat[..., 2].mean()) > 2.5 * float(rgb[..., 0].mean() - rgb[..., 2].mean())
    gris = saturar(rgb, 0.0)
    assert float(np.abs(gris[..., 0] - gris[..., 2]).max()) < 0.01
    print("✅ revelado_dia: curva, detalle (sin halo en el cielo) y saturación correctos")
