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
import threading
import numpy as np
import cv2

_lock = threading.Lock()
_estado = {"activo": False}

TAM_RECORTE = 48          # lado del recorte de la estrella, en píxeles del sensor
AMPLIACION = 5            # el recorte se enseña a ×5
MARGEN = 24               # la estrella más brillante se busca lejos de los bordes
NIVEL_PICO_OBJETIVO = 0.6 # en vivo: el pico de la estrella al 60 % del rango (sin saturar)


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
    if not info or not np.isfinite(info.get("fwhm_g", float("nan"))):
        return ""
    sep = "  |  " if para_imagen else " · "
    partes = [f"FWHM verde {info['fwhm_g']:.1f} px".replace(".", ",")]
    partes += [f"{n} {info[k]:.1f}".replace(".", ",") for k, n in (("fwhm_r", "rojo"), ("fwhm_b", "azul")) if np.isfinite(info.get(k, float("nan")))]
    if info.get("saturada"):
        partes.append("SATURADA" if para_imagen else "saturada: baja la exposición o la ganancia")
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
            (tw, th), _ = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SIMPLEX, escala, g_txt)
            ox, oy = max(2, w - m - tw), min(h - 6, m + rl + th + m)
            cv2.putText(base, texto, (ox, oy), cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), g_txt + 3, cv2.LINE_AA)
            cv2.putText(base, texto, (ox, oy), cv2.FONT_HERSHEY_SIMPLEX, escala, (120, 255, 120), g_txt, cv2.LINE_AA)
    if pulsado is not None:
        cv2.circle(base, (int(pulsado[0]), int(pulsado[1])), max(10, lado // 90), (80, 200, 80), max(1, lado // 1000), cv2.LINE_AA)
    return base


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
    xg, yg, _ = estrella_mas_brillante(girada)
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
    xg, yg, _ = estrella_mas_brillante(girada)
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
