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
#   - Con la máscara puesta (30-oct-2026, tras la prueba del 29-oct con la Saturn-C):
#     en el verde solo, los picos de la Bahtinov salían como «borroncitos» sueltos.
#     La máscara es una rejilla que abre la luz por colores a lo largo de cada pico,
#     y con la banda del verde (480–600 nm) cada orden es un trazo corto con huecos
#     entre uno y otro; con los tres colores los trazos se solapan. Así que, si
#     medir_bahtinov encuentra la cruz, el recorte es de los tres colores juntos,
#     con el núcleo quemado y la cruceta virtual encima (las rayas de la X y, a
#     trazos, por dónde tiene que pasar el pico central), el desvío en píxeles y
#     una barra. AyudaBahtinov avisa «centrada» y, al quitar la máscara, aprende
#     la relación entre las anchuras de los tres colores en el foco bueno para
#     avisar «enfocado» las veces siguientes sin máscara.
#     Al tocar el enfocador el tubo vibra y un fotograma puede salir movido, sin
#     cruz: la máscara no se da por quitada hasta pasar unos segundos sin verla (en
#     vivo), y una estrella alargada no cuenta ni para eso ni para aprender.
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
#     círculo. El recorte lleva su etiqueta («limbo» o «terminador»): el número
#     es siempre la anchura del limbo.
#   - En vivo (HistorialLimbo, medido con los vídeos de la Luna del 21-oct-2026):
#     el limbo de cada fotograma varía un 6–7 % con el mismo foco por el seeing,
#     así que el número es el percentil 25 de los últimos 2,5 s (los fotogramas
#     con mejor seeing son los que enseñan el desenfoque real), con histéresis
#     para que no parpadee; el recorte es el del fotograma más nítido de esa
#     ventana; al lado va el mejor valor de la sesión; debajo, la gráfica del
#     último minuto para ver si al girar el enfocador se baja o se sube; y por
#     voz se dice «mejora», «empeora» o «igual» cada pocos segundos comparando
#     la ventana con la del último veredicto. Cerca del foco la curva es plana
#     (medido con el Sol del 25-oct-2026: el limbo es √(suelo² + desenfoque²) con
#     un suelo de seeing y óptica de ~4,1 px, así que entre 0,9 y 1,8 px de
#     desenfoque el número solo se mueve 0,3 px): el foco bueno es el centro de
#     esa zona plana, como en la curva en V de los autoenfocadores. Cuando se
#     cruza la zona y el valor sube claramente se dice «te has pasado del foco,
#     vuelve atrás» y la gráfica marca la zona y su centro; al volver a entrar,
#     «estás en la zona buena». Cerca del mínimo el número va con dos decimales
#     y sin histéresis. Las medidas con el disco cortado
#     por el borde (pocos ángulos de limbo, otro radio) o moviéndose deprisa (pad,
#     mano en el tubo) se descartan: daban un limbo falso y estropeaban el «mejor».
#   - La exposición sigue siendo la automática de día (nivel del disco), no la
#     de la estrella.
import os
import json
import math
import time
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

# Bahtinov (medir_bahtinov). El primer orden de los picos cae a unos 25 px del sensor de la estrella
# con cualquier telescopio: la rejilla de la máscara se hace proporcional a la focal
RADIO_BAHTINOV = 112      # px del sensor alrededor de la estrella donde se buscan los picos
RADIO_BAHTINOV_MIN = 48   # más cerca del borde de la foto no se mide
NUCLEO_BAHTINOV = 6       # px del sensor: el núcleo de la estrella no cuenta para situar las rayas
SENAL_BAHTINOV = 6.0      # una raya tiene que destacar 6 veces la dispersión de la fuerza por ángulo...
FRACCION_RAYA = 0.03      # ...y al menos un 3 % del trazo más fuerte (cada raya de la X lleva ~10 % del central)
ANGULO_BAHTINOV_MIN = 10.0  # las rayas de la X, entre 10° y 45° del pico central...
ANGULO_BAHTINOV_MAX = 45.0
ASIMETRIA_BAHTINOV = 4.0  # ...y casi simétricas (una araña de reflector da rayas a 90°); al buscarlas por los
                          # trazos se deja el doble: desenfocado, el pico central no pasa por la estrella
OPUESTO_GRADOS = 8        # el trazo de enfrente de una raya, hasta 8° fuera de la línea por la estrella
FRANJA_BAHTINOV = 3.0     # px del sensor a cada lado de una raya con que se ajusta su recta
# Centrada: con la Bahtinov simulada de una focal de 260 mm a f/4,3, el desvío crece unos 0,023 px del
# sensor por µm de desenfoque y la zona crítica de foco es de ±25 µm (±0,55 px): 0,3 px queda dentro
UMBRAL_BAHTINOV_PX = 0.3  # desvío del pico central por debajo del cual la Bahtinov está centrada...
SALIDA_BAHTINOV_PX = 0.6  # ...y por encima del cual deja de estarlo (para que el aviso no vaya y venga)
VENTANA_BAHTINOV_S = 2.0  # en vivo, el desvío enseñado es la mediana de los últimos 2 s (el seeing lo mueve)
FOTOGRAMAS_CENTRADA = 3   # en vivo, «centrada» necesita al menos estos fotogramas en la ventana
GRACIA_SIN_CRUZ_S = 3.0   # en vivo, la máscara no se da por quitada hasta 3 s sin ver la cruz (al tocar el
                          # enfocador el tubo vibra y algún fotograma sale movido y sin cruz)
ALARGAMIENTO_MOVIDA = 1.4 # una estrella con un eje 1,4 veces el otro está movida: no es «sin máscara» ni sirve
                          # para la firma (el movimiento ensancha igual los tres colores y la relación tira a 1)
ESCALA_BARRA_PX = 3.0     # la barra de debajo del recorte va de −3 a +3 px
LADO_RECORTE_BAHTINOV = 480  # px del recorte de la Bahtinov antes de encajarlo en la imagen...
RADIO_VISTA_BAHTINOV = 64    # ...que enseña 64 px del sensor alrededor de la estrella (×3,7)
# Firma del foco (AyudaBahtinov): la relación entre las anchuras de los tres colores con la Bahtinov
# centrada, aprendida al quitar la máscara y guardada en datos/ para avisar «enfocado» sin máscara
RUTA_FIRMA = "datos/firma_enfoque.json"
APRENDER_S = 180.0        # se aprende con lo que llegue hasta 3 minutos después de centrar la Bahtinov
FOTOGRAMAS_APRENDER = {"vivo": 10, "foto": 1}
TOLERANCIA_FIRMA = 0.05   # enfocado si R/G y B/G están a menos de un 5 % de los aprendidos (o 2 veces su dispersión)...
SALIDA_FIRMA = 2.0        # ...y deja de estarlo al pasar del doble
DISTINCION_FIRMA = 2.0    # la firma solo sirve si se aparta de 1 (lo que da un desenfoque grande) 2 veces la tolerancia
CERCA_MEJOR_G = 1.15      # y el verde tiene que estar a menos de un 15 % del más fino de la sesión

OBJETIVOS_DISCO = ("sol", "luna")
ANGULOS_LIMBO = 180       # perfiles radiales por fotograma
VENTANA_LIMBO = 8         # ± píxeles del plano alrededor del radio ajustado
PASO_PERFIL = 0.25        # muestreo del perfil radial (px del plano)
ANGULOS_MINIMOS = 30      # con menos ángulos de limbo la medida no vale (disco cortado por el borde: el
                          # círculo se ajusta al terminador y el limbo sale falso; el limbo iluminado de la
                          # Luna abarca siempre medio círculo, unos 100 ángulos, y el Sol entero, 180)
# En vivo (HistorialLimbo)
VENTANA_S = 2.5           # el número es el percentil de las medidas de los últimos 2,5 s
PERCENTIL_VENTANA = 25    # bajo: los fotogramas con mejor seeing enseñan el desenfoque real
HISTERESIS_PX = 0.08      # el número enseñado no cambia si la ventana se mueve menos que esto (px del sensor)
FOTOGRAMAS_MEJOR = 8      # el «mejor» de la sesión solo cuenta ventanas con estos fotogramas o más
TOLERANCIA_RADIO = 0.03   # un círculo con un radio distinto al de la sesión en más de un 3 % no es el disco
VELOCIDAD_MAX = 25.0      # px del plano por segundo: si el disco se mueve más deprisa (pad, mano en el tubo) la
                          # exposición lo emborrona y la medida no vale (el seeing lo mueve ~6 px/s; el pad, 80)
TENDENCIA_S = 60.0        # la serie guarda el último minuto (para la curva en V)...
GRAFICA_S = 30.0          # ...y la gráfica dibuja los últimos 30 s: una pasada por el foco dura 10–20 s
TECHO_GRAFICA = 1.6       # la escala vertical llega hasta 1,6 veces el mínimo: lo de más arriba se recorta
TENDENCIA_CADA_S = 0.25   # un punto de la gráfica cada cuarto de segundo
VOZ_CADA_S = 3.0          # un veredicto por voz como mucho cada 3 s
UMBRAL_VOZ = 0.05         # «mejora»/«empeora» si la ventana cambia más de un 5 %, más de UMBRAL_VOZ_PX y más de
UMBRAL_VOZ_PX = 0.15      # 3 veces el ruido del percentil (medido con la dispersión de los fotogramas de la ventana)
CERCA_MINIMO = 0.10       # a menos de un 10 % del mejor no hay histéresis: es donde se afina el foco
# Curva en V (HistorialLimbo.curva_v): la zona plana alrededor del mínimo del último minuto
# Elegidos con barridos simulados (√(4,1² + desenfoque²) y el ruido de seeing de los fotogramas reales):
# sin falsas alarmas en 20 minutos quieto en el foco, avisa ~5 s después de cruzar el foco (casi todo
# es el retraso de la ventana y salir de la zona plana) y el centro sale a ±0,2 s del foco real.
# Con una zona de 0,15 px y subida de 1,5 avisaba antes pero con un 15 % de falsas alarmas.
ZONA_V = 0.04             # zona plana: hasta un 4 % por encima del mínimo...
ZONA_V_PX = 0.15          # ...y al menos 0,15 px (el número de 2,5 s se mueve ~0,08 px con el seeing)
SUBIDA_V = 2.0            # «te has pasado» cuando el valor sube 2 veces la zona por encima del mínimo...
SUBIDA_V_S = 0.5          # ...durante medio segundo seguido (una racha de mal seeing no basta)
SUAVIZADO_V_S = 1.0       # mediana de 1 s sobre la serie antes de buscar la zona


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


def _momentos(plano, x, y, radio=7):
    """(sxx, syy, sxy): segundos momentos de la luz alrededor de (x, y), con el fondo del borde restado; None sin luz."""
    p = np.asarray(plano, dtype=np.float32)
    y0, y1, x0, x1 = max(0, y - radio), min(p.shape[0], y + radio + 1), max(0, x - radio), min(p.shape[1], x + radio + 1)
    c = p[y0:y1, x0:x1]
    if c.size < 9:
        return None
    borde = np.concatenate([c[0], c[-1], c[:, 0], c[:, -1]])
    w = np.clip(c - float(np.median(borde)), 0, None)
    if w.sum() <= 0:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    cx, cy = (xx * w).sum() / w.sum(), (yy * w).sum() / w.sum()
    dx, dy = xx - cx, yy - cy
    return (float((dx * dx * w).sum() / w.sum()), float((dy * dy * w).sum() / w.sum()), float((dx * dy * w).sum() / w.sum()))


def fwhm(plano, x, y, radio=7):
    """Anchura a media altura (px) por el segundo momento de la luz alrededor de (x, y), con el fondo del borde restado."""
    m = _momentos(plano, x, y, radio)
    if m is None:
        return float("nan")
    s2 = (m[0] + m[1]) / 2.0
    return float(2.355 * np.sqrt(max(s2, 1e-3)))


def alargamiento(plano, x, y, radio=7):
    """Eje mayor entre eje menor de la estrella (1 = redonda); la estrella sale alargada si el tubo se movió al exponer."""
    m = _momentos(plano, x, y, radio)
    if m is None:
        return float("nan")
    sxx, syy, sxy = m
    medio, dif = (sxx + syy) / 2.0, np.hypot((sxx - syy) / 2.0, sxy)
    return float(np.sqrt((medio + dif) / max(medio - dif, 1e-3)))


def medir(verde, rojo=None, azul=None, escala_px=1.0):
    """
    Estrella más brillante del verde y su FWHM en cada canal disponible, en
    píxeles del sensor (`escala_px` = píxeles del sensor por píxel del plano:
    2 con el verde superpíxel). Devuelve un dict para la web.
    """
    x, y, pico = estrella_mas_brillante(verde)
    alarg = alargamiento(verde, x, y)
    info = {"x": int(x), "y": int(y), "pico": round(pico), "fwhm_g": round(fwhm(verde, x, y) * escala_px, 2),
            "saturada": bool(pico >= 0.97 * 65535),     # con el núcleo recortado la anchura sale falsa
            "movida": bool(np.isfinite(alarg) and alarg >= ALARGAMIENTO_MOVIDA)}
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
        partes = [_("Limbo {px} px", px=fmt_num(info['limbo'], 2))]
        if info.get("mejor") is not None:
            partes.append(_("mejor {px}", px=fmt_num(info['mejor'], 2)))
        if not para_imagen and info.get("n"):
            partes.append(_("{n} ángulos", n=info['n']))
        return sep.join(partes)
    bahtinov = info.get("bahtinov")
    if bahtinov:
        # Con la máscara puesta la anchura de la estrella no dice nada: va el desvío del pico central
        partes = [_("Bahtinov {px} px", px=fmt_num(abs(bahtinov["desvio"]), 2))]
        if bahtinov.get("centrada"):
            partes.append(_("CENTRADA") if para_imagen else _("centrada"))
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
    if info.get("movida"):
        partes.append(_("MOVIDA") if para_imagen else _("estrella movida: espera a que el tubo se calme"))
    firma = info.get("firma")
    if firma and firma.get("aprendiendo") is not None:
        partes.append(_("APRENDIENDO {n}/{total}", n=firma["aprendiendo"], total=firma["total"]) if para_imagen
                      else _("aprendiendo el foco: {n} de {total}", n=firma["aprendiendo"], total=firma["total"]))
    elif firma and firma.get("enfocado"):
        partes.append(_("ENFOCADO") if para_imagen else _("enfocado"))
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


ETIQUETAS_RECORTE = {"limbo": lambda: _("limbo"), "terminador": lambda: _("terminador")}
VERDE_TEXTO = (120, 255, 120)
VERDE_MARCO = (80, 200, 80)


def _grafica_tendencia(base, serie, mejor, x0, y0, ancho, alto, zona_v=None):
    """
    La gráfica del limbo de los últimos GRAFICA_S segundos en (x0, y0)-(x0+ancho, y0+alto): fondo
    oscuro, la línea del número enseñado (recortada por arriba a TECHO_GRAFICA veces
    el mínimo), el mejor de la sesión como raya de puntos
    y el valor actual como punto. Eje x = tiempo (GRAFICA_S), eje y = anchura con
    un margen; a la izquierda, el máximo y el mínimo del tramo. Con `zona_v`
    (t_ini, t_fin, t_centro, mínimo), la zona plana del foco sombreada y su centro
    como raya vertical: es adonde hay que volver.
    """
    if len(serie) < 2 or ancho < 40 or alto < 20:
        return
    t1 = float(serie[-1][0])
    serie = [p for p in serie if t1 - p[0] <= GRAFICA_S]
    ts = np.array([p[0] for p in serie], float)
    vs = np.array([p[1] for p in serie], float)
    vmin, vmax = float(vs.min()), float(vs.max())
    if mejor is not None:
        vmin = min(vmin, float(mejor))
    # La escala se centra en el fondo de la curva: es donde se decide el foco. Lejos del foco los
    # valores (10–16 px) aplastaban la V contra el borde de abajo
    vmax = min(vmax, TECHO_GRAFICA * vmin)
    margen = max(0.1, 0.15 * (vmax - vmin), 0.05 * vmax)
    lo, hi = vmin - margen, vmax + margen
    vs = np.minimum(vs, hi)
    fondo = base[y0:y0 + alto, x0:x0 + ancho]
    cv2.addWeighted(fondo, 0.35, np.zeros_like(fondo), 0.65, 0, dst=fondo)
    cv2.rectangle(base, (x0, y0), (x0 + ancho - 1, y0 + alto - 1), VERDE_MARCO, 1)
    escala_txt = max(0.35, min(0.8, alto / 90.0))
    (tw, th), __ = cv2.getTextSize(fmt_num(hi, 1), cv2.FONT_HERSHEY_SIMPLEX, escala_txt, 1)
    margen_izq = tw + 6
    gx0, gx1 = x0 + margen_izq, x0 + ancho - 4
    gy0, gy1 = y0 + 3, y0 + alto - 4
    def px(t, v):
        return (int(round(gx1 - (t1 - t) / GRAFICA_S * (gx1 - gx0))),
                int(round(gy1 - (v - lo) / (hi - lo) * (gy1 - gy0))))
    cv2.putText(base, fmt_num(hi, 1), (x0 + 3, gy0 + th), cv2.FONT_HERSHEY_SIMPLEX, escala_txt, VERDE_TEXTO, 1, cv2.LINE_AA)
    cv2.putText(base, fmt_num(lo, 1), (x0 + 3, gy1), cv2.FONT_HERSHEY_SIMPLEX, escala_txt, VERDE_TEXTO, 1, cv2.LINE_AA)
    if zona_v is not None and t1 - zona_v[1] <= GRAFICA_S:
        xa, xb = max(gx0, px(zona_v[0], lo)[0]), px(zona_v[1], lo)[0]
        if xb > xa:
            banda = base[gy0:gy1, xa:xb + 1]
            cv2.addWeighted(banda, 0.45, np.full_like(banda, (50, 140, 50)), 0.55, 0, dst=banda)
        xc = px(zona_v[2], lo)[0]
        if xc >= gx0:
            cv2.line(base, (xc, gy0), (xc, gy1), VERDE_TEXTO, max(1, alto // 50), cv2.LINE_AA)
    if mejor is not None:
        ym = px(t1, float(mejor))[1]
        for x in range(gx0, gx1, 6):
            cv2.line(base, (x, ym), (min(x + 2, gx1), ym), VERDE_MARCO, 1)
    puntos = np.array([px(t, v) for t, v in zip(ts, vs)], np.int32)
    if len(puntos) >= 2:
        cv2.polylines(base, [puntos.reshape(-1, 1, 2)], False, VERDE_TEXTO, max(1, alto // 60), cv2.LINE_AA)
    cv2.circle(base, tuple(int(v) for v in puntos[-1]), max(2, alto // 25), VERDE_TEXTO, -1, cv2.LINE_AA)


def componer(gris8, recorte, info, pulsado=None, tendencia=None, etiqueta=None, zona_v=None):
    """
    Imagen BGR para la web: el verde en gris, el recorte de la estrella en la
    esquina superior derecha (con marco y, con `etiqueta`, su nombre: «limbo» o
    «terminador»), la línea de anchuras debajo y, con `tendencia` [(t, px)], la
    gráfica del último minuto debajo del texto (con `zona_v`, la zona plana del
    foco y su centro). Si `pulsado` (x, y) se da, se marca la estrella en la imagen grande.
    """
    base = cv2.cvtColor(gris8, cv2.COLOR_GRAY2BGR)
    h, w = base.shape[:2]
    lado = min(h, w)
    if recorte is not None:
        # El recorte ocupa una fracción fija del lado: la foto de noche (3008 px) se
        # reduce a 1600 px para la web y a ~600 en el móvil, y el recorte debe seguir viéndose
        rl = int(lado * (0.4 if lado < 800 else 0.3))
        rec = cv2.resize(recorte, (rl, rl), interpolation=cv2.INTER_CUBIC if recorte.shape[0] < rl else cv2.INTER_AREA)
        if rec.ndim == 2:
            rec = cv2.cvtColor(rec, cv2.COLOR_GRAY2BGR)      # el de la Bahtinov ya viene en color, con la cruceta
        m = max(6, lado // 100)
        base[m:m + rl, w - m - rl:w - m] = rec
        grosor = max(2, lado // 500)
        cv2.rectangle(base, (w - m - rl - 1, m - 1), (w - m, m + rl), VERDE_MARCO, grosor)
        escala = max(0.45, min(2.0, rl / 420.0))
        g_txt = max(1, int(round(escala * 1.3)))
        nombre = ETIQUETAS_RECORTE.get(etiqueta)
        if nombre:
            # El nombre del recorte dentro del marco, arriba a la izquierda (el número de debajo es siempre el limbo)
            e_et = escala * 0.75
            (tw, th), __ = cv2.getTextSize(nombre(), cv2.FONT_HERSHEY_SIMPLEX, e_et, g_txt)
            cv2.putText(base, nombre(), (w - m - rl + 4, m + th + 4), cv2.FONT_HERSHEY_SIMPLEX, e_et, (0, 0, 0), g_txt + 2, cv2.LINE_AA)
            cv2.putText(base, nombre(), (w - m - rl + 4, m + th + 4), cv2.FONT_HERSHEY_SIMPLEX, e_et, VERDE_TEXTO, g_txt, cv2.LINE_AA)
        texto = texto_info(info, para_imagen=True)
        oy = m + rl
        if texto:
            (tw, th), __ = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SIMPLEX, escala, g_txt)
            ox, oy = max(2, w - m - tw), min(h - 6, m + rl + th + m)
            cv2.putText(base, texto, (ox, oy), cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), g_txt + 3, cv2.LINE_AA)
            cv2.putText(base, texto, (ox, oy), cv2.FONT_HERSHEY_SIMPLEX, escala, VERDE_TEXTO, g_txt, cv2.LINE_AA)
        if tendencia:
            alto_g = int(rl * 0.4)
            if oy + m + alto_g < h:
                _grafica_tendencia(base, tendencia, (info or {}).get("mejor"), w - m - rl, oy + m, rl, alto_g, zona_v=zona_v)
    if pulsado is not None:
        cv2.circle(base, (int(pulsado[0]), int(pulsado[1])), max(10, lado // 90), VERDE_MARCO, max(1, lado // 1000), cv2.LINE_AA)
    return base


# ─────────────────────────────────────────────────────────────────────────────
#  Máscara de Bahtinov: la cruz, la cruceta virtual y el desvío
# ─────────────────────────────────────────────────────────────────────────────
def planos_superpixel(cfa, patron):
    """(rojo, verde, azul) del mosaico Bayer a mitad de resolución, sin interpolar; (None, mosaico, None) si no es de color."""
    f = np.asarray(cfa)
    p = str(patron).upper()
    if p not in _POS_VERDES:
        return None, verde_superpixel(f, patron), None
    h, w = f.shape[0] & ~1, f.shape[1] & ~1
    pos_r = {"RGGB": (0, 0), "BGGR": (1, 1), "GRBG": (0, 1), "GBRG": (1, 0)}[p]
    pos_b = (1 - pos_r[0], 1 - pos_r[1])
    rojo = f[pos_r[0]:h:2, pos_r[1]:w:2].astype(np.float32)
    azul = f[pos_b[0]:h:2, pos_b[1]:w:2].astype(np.float32)
    return rojo, verde_superpixel(f, patron), azul


def _dif_angulo(a, b):
    """a − b en grados, llevado a (−90, 90]: las rayas no tienen sentido."""
    d = (float(a) - float(b)) % 180.0
    return d - 180.0 if d > 90.0 else d


def _recta_pesada(xs, ys, w):
    """Recta de mínimos cuadrados totales de los puntos (xs, ys) con pesos w: (normal unitaria n, d) con n·q = d."""
    sw = float(w.sum())
    mx, my = float((xs * w).sum()) / sw, float((ys * w).sum()) / sw
    dx, dy = xs - mx, ys - my
    cxx, cyy, cxy = float((dx * dx * w).sum()), float((dy * dy * w).sum()), float((dx * dy * w).sum())
    ang = 0.5 * math.atan2(2.0 * cxy, cxx - cyy)                  # la dirección de más dispersión: la de la raya
    n = np.array([-math.sin(ang), math.cos(ang)])
    return n, float(n @ (mx, my))


def medir_bahtinov(lum, x, y, escala_px=1.0):
    """
    Busca la cruz de una máscara de Bahtinov alrededor de la estrella (x, y) del plano `lum` (la
    suma de los tres colores: con un solo color los picos salen a trozos) y mide cuánto se aparta
    el pico central del cruce de las dos rayas de la X, que es por donde pasa cuando el foco es bueno.

    Con un telescopio corto los picos son poco más que un trazo a cada lado de la estrella (el
    primer orden de la rejilla, a ~25 px del sensor), así que no se buscan rectas largas: se miran
    los trazos alrededor de la estrella (brillo por ángulo, en polares), cada raya es una pareja de
    trazos opuestos, y su recta se ajusta con los píxeles de una franja estrecha a lo largo de la
    pareja (mínimos cuadrados totales, tres vueltas), sin el núcleo, donde las rayas se cruzan.

    Devuelve None si no hay tres rayas con la forma de una Bahtinov (sin máscara, la araña de un
    reflector...) o un dict con el desvío en px del sensor (con signo), los ángulos de las rayas y
    sus rectas en px del recorte (central, X, X): {"desvio", "angulos", "rectas", "cruce", "radio", "escala"}.
    """
    p = np.asarray(lum, dtype=np.float32)
    r = int(round(RADIO_BAHTINOV / escala_px))
    r = min(r, x, y, p.shape[1] - 1 - x, p.shape[0] - 1 - y)
    if r < RADIO_BAHTINOV_MIN / escala_px:
        return None
    c = p[y - r:y + r + 1, x - r:x + r + 1].astype(np.float32)
    c = c - float(np.median(c))
    # Suavizado de un píxel (los trazos tienen varios: el ruido baja y ellos no) y solo lo que sale del ruido
    suave = cv2.GaussianBlur(c, (0, 0), max(0.5, 1.0 / escala_px))
    sigma = 1.4826 * float(np.median(np.abs(suave - float(np.median(suave))))) + 1e-6
    senal = np.clip(suave - float(np.median(suave)) - 3.0 * sigma, 0, None)
    interior = NUCLEO_BAHTINOV / escala_px
    # Brillo por ángulo (1°) entre el núcleo y el borde del recorte
    n_ang = 360
    polar = cv2.warpPolar(senal, (r, n_ang), (float(r), float(r)), float(r), cv2.WARP_POLAR_LINEAR + cv2.INTER_LINEAR)
    radios = np.arange(r)
    brillo = polar[:, radios >= interior].sum(axis=1)
    brillo = np.convolve(np.r_[brillo[-2:], brillo, brillo[:2]], np.ones(3) / 3.0, mode="same")[2:-2]
    # Una raya: un trazo a cada lado (el de enfrente puede caer unos grados más allá si la raya no pasa por la estrella)
    desfase = np.array([max(range(-OPUESTO_GRADOS, OPUESTO_GRADOS + 1), key=lambda d: brillo[(a + 180 + d) % n_ang])
                        for a in range(180)])
    enfrente = brillo[(np.arange(180) + 180 + desfase) % n_ang]
    eje = np.arange(180) + desfase / 2.0          # la dirección de la raya: entre un trazo y el de enfrente
    fuerza = np.sqrt(np.clip(brillo[:180], 0, None) * np.clip(enfrente, 0, None))
    fondo = float(np.median(fuerza))
    dispersion = 1.4826 * float(np.median(np.abs(fuerza - fondo))) + 1e-6
    # Sin ruido de fondo (todo lo que no son trazos queda a cero) la dispersión es nula: entonces cuenta lo que
    # destaque un FRACCION_RAYA del trazo más fuerte
    umbral = fondo + max(SENAL_BAHTINOV * dispersion, FRACCION_RAYA * float(fuerza.max()))
    picos = [i for i in range(180) if fuerza[i] >= fuerza[i - 1] and fuerza[i] >= fuerza[(i + 1) % 180] and fuerza[i] > umbral]
    picos = sorted(picos, key=lambda i: -fuerza[i])[:6]
    mejor = None
    for ic in picos:
        for i1 in picos:
            for i2 in picos:
                if len({ic, i1, i2}) < 3 or i1 > i2 or fuerza[ic] < max(fuerza[i1], fuerza[i2]):
                    continue        # el pico central es el más fuerte: sale de media máscara, y cada raya de la X de un cuarto
                d1, d2 = _dif_angulo(eje[i1], eje[ic]), _dif_angulo(eje[i2], eje[ic])
                if d1 * d2 >= 0 or min(abs(d1), abs(d2)) < ANGULO_BAHTINOV_MIN \
                        or max(abs(d1), abs(d2)) > ANGULO_BAHTINOV_MAX or abs(abs(d1) - abs(d2)) > 2 * ASIMETRIA_BAHTINOV:
                    continue
                total = fuerza[ic] + fuerza[i1] + fuerza[i2]
                if mejor is None or total > mejor[0]:
                    mejor = (total, ic, i1, i2)
    if mejor is None:
        return None
    yy, xx = np.mgrid[0:2 * r + 1, 0:2 * r + 1].astype(np.float32)
    dist = np.hypot(xx - r, yy - r)
    fuera_nucleo = (dist >= interior) & (dist <= r)
    franja = FRANJA_BAHTINOV / escala_px
    rectas = []
    for i in mejor[1:]:
        # La recta de partida, por el centro de los dos trazos
        puntos = []
        for a0 in (i, i + 180 + desfase[i]):
            a = math.radians(a0)
            cuna = np.abs(((np.arctan2(yy - r, xx - r) - a + np.pi) % (2 * np.pi)) - np.pi) <= math.radians(3.0)
            w = senal * (cuna & fuera_nucleo)
            if w.sum() <= 0:
                break
            puntos.append(((xx * w).sum() / w.sum(), (yy * w).sum() / w.sum()))
        if len(puntos) < 2:
            return None
        (x1, y1), (x2, y2) = puntos
        ang = math.atan2(y2 - y1, x2 - x1)
        n = np.array([-math.sin(ang), math.cos(ang)])
        d = float(n @ (x1, y1))
        # Tres vueltas con los píxeles de la franja a lo largo de la recta
        for __ in range(3):
            en_franja = (np.abs(xx * n[0] + yy * n[1] - d) <= franja) & fuera_nucleo
            w = senal[en_franja]
            if w.sum() <= 0:
                return None
            n, d = _recta_pesada(xx[en_franja], yy[en_franja], w)
        rectas.append((n, d))
    (nc, dc), (n1, d1), (n2, d2) = rectas
    angulos = [math.degrees(math.atan2(-n[0], n[1])) % 180.0 for n, __ in rectas]
    if abs(abs(_dif_angulo(angulos[1], angulos[0])) - abs(_dif_angulo(angulos[2], angulos[0]))) > ASIMETRIA_BAHTINOV:
        return None
    try:
        cruce = np.linalg.solve(np.array([n1, n2]), np.array([d1, d2]))
    except np.linalg.LinAlgError:
        return None
    if np.hypot(cruce[0] - r, cruce[1] - r) > r / 3:
        return None
    # El signo depende de hacia dónde apunte la normal del pico central, que _recta_pesada da con el
    # ángulo de la raya en (−90°, 90°]: es estable mientras la máscara y la cámara no giren
    desvio = float(nc @ cruce - dc) * escala_px
    return {"desvio": desvio, "angulos": [round(a, 2) for a in angulos],
            "rectas": [(float(n[0]), float(n[1]), float(d)) for n, d in rectas],
            "cruce": (float(cruce[0]), float(cruce[1])), "radio": r, "escala": float(escala_px)}


AMBAR = (0, 190, 255)


def _linea_discontinua(img, p0, p1, color, grosor, trazo=14, hueco=9):
    """Línea a trazos de p0 a p1 (cv2 no tiene)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    largo = float(np.hypot(*(p1 - p0)))
    if largo < 1:
        return
    u = (p1 - p0) / largo
    s = 0.0
    while s < largo:
        a, b = p0 + u * s, p0 + u * min(largo, s + trazo)
        cv2.line(img, (int(round(a[0])), int(round(a[1]))), (int(round(b[0])), int(round(b[1]))), color, grosor, cv2.LINE_AA)
        s += trazo + hueco


def _extremos_recta(n, d, centro, radio):
    """Los dos puntos donde la recta n·q = d corta el círculo (centro, radio), o None si no lo corta."""
    t = np.array([n[1], -n[0]])
    pie = np.asarray(centro, float) + n * (d - float(n @ np.asarray(centro, float)))
    h2 = radio * radio - float(np.sum((pie - centro) ** 2))
    if h2 <= 0:
        return None
    h = math.sqrt(h2)
    return pie - t * h, pie + t * h


def recorte_bahtinov(lum, x, y, medida, estado=None):
    """
    El recorte de la estrella con la máscara de Bahtinov: los tres colores juntos (los picos se ven
    seguidos), el núcleo quemado para que los picos se vean, y encima la cruceta virtual: las dos
    rayas de la X tal como se han medido (finas) y, a trazos, por dónde tendría que pasar el pico
    central (por su cruce). Si el pico central de verdad cae sobre los trazos, el foco es bueno.
    Abajo, una barra con el desvío (−3 a +3 px) y la zona de «centrada». `estado`: el
    info["bahtinov"] de AyudaBahtinov (desvío de la ventana y si está centrada). Devuelve BGR.
    """
    # La vista, más cerrada que la zona medida: los trazos del primer orden caen a ~25 px de la estrella
    r = int(medida["radio"])
    rv = min(r, int(round(RADIO_VISTA_BAHTINOV / float(medida.get("escala", 1.0)))))
    desplaz = float(r - rv)                       # de px del recorte medido a px de la vista
    c = np.asarray(lum, dtype=np.float32)[y - rv:y + rv + 1, x - rv:x + rv + 1]
    # Negro justo encima del ruido del cielo y blanco en lo brillante de los trazos (fuera del núcleo): el
    # núcleo se quema y los trazos se ven. En el foco el núcleo es tan puntual que los trazos no llegan al 2 % del pico
    fondo = float(np.median(c))
    sigma = 1.4826 * float(np.median(np.abs(c - fondo))) + 1e-6
    negro = fondo + 2.5 * sigma
    yy, xx = np.mgrid[-rv:rv + 1, -rv:rv + 1]
    trazos = c[(xx * xx + yy * yy > (NUCLEO_BAHTINOV / float(medida.get("escala", 1.0))) ** 2) & (c > negro + 3.0 * sigma)]
    if trazos.size >= 20:
        blanco = negro + float(np.percentile(trazos - negro, 90))
    else:
        blanco = negro + max(0.2 * (float(c.max()) - negro), 8.0 * sigma)
    gris = (np.sqrt(np.clip((c - negro) / (blanco - negro), 0.0, 1.0)) * 255.0 + 0.5).astype(np.uint8)
    lado = LADO_RECORTE_BAHTINOV
    k = lado / float(2 * rv + 1)
    img = cv2.cvtColor(cv2.resize(gris, (lado, lado), interpolation=cv2.INTER_CUBIC), cv2.COLOR_GRAY2BGR)
    estado = estado or {}
    centrada = bool(estado.get("centrada"))
    grosor = max(1, lado // 240)
    centro = np.array([lado / 2.0 - 0.5, lado / 2.0 - 0.5])

    def en_vista(n, d):
        """La recta n·q = d (px del recorte medido) en px de la vista: q = (q_vista + 1/2)/k − 1/2 + desplaz."""
        return d * k - (desplaz - 0.5) * k * (n[0] + n[1]) - 0.5 * (n[0] + n[1])

    capa = img.copy()
    rectas = [(np.array(v[:2]), v[2]) for v in medida["rectas"]]
    for n, d in rectas[1:]:
        ext = _extremos_recta(n, en_vista(n, d), centro, lado / 2.0 - 2)
        if ext:
            cv2.line(capa, tuple(int(round(v)) for v in ext[0]), tuple(int(round(v)) for v in ext[1]), VERDE_MARCO, grosor, cv2.LINE_AA)
    cv2.addWeighted(capa, 0.6, img, 0.4, 0, dst=img)
    # El pico central ideal: la dirección del medido, por el cruce de la X; fino y algo transparente para
    # que se vea el pico de verdad debajo
    nc = rectas[0][0]
    ext = _extremos_recta(nc, en_vista(nc, float(nc @ np.asarray(medida["cruce"]))), centro, lado / 2.0 - 2)
    if ext:
        capa = img.copy()
        _linea_discontinua(capa, ext[0], ext[1], VERDE_TEXTO if centrada else AMBAR, grosor)
        cv2.addWeighted(capa, 0.75, img, 0.25, 0, dst=img)
    # La barra del desvío
    desvio = estado.get("desvio", medida["desvio"])
    alto_b = max(10, lado // 28)
    y0 = lado - alto_b - lado // 30
    x0, x1 = lado // 6, lado - lado // 6
    escala = (x1 - x0) / (2.0 * ESCALA_BARRA_PX)
    xm = (x0 + x1) // 2
    banda = img[y0:y0 + alto_b, x0:x1]
    cv2.addWeighted(banda, 0.3, np.zeros_like(banda), 0.7, 0, dst=banda)
    zona = int(round(UMBRAL_BAHTINOV_PX * escala))
    cv2.rectangle(img, (xm - zona, y0), (xm + zona, y0 + alto_b), (50, 120, 50), -1)
    cv2.rectangle(img, (x0, y0), (x1, y0 + alto_b), VERDE_MARCO, 1)
    cv2.line(img, (xm, y0 - alto_b // 3), (xm, y0 + alto_b + alto_b // 3), VERDE_TEXTO, grosor)
    xd = int(round(xm + max(-ESCALA_BARRA_PX, min(ESCALA_BARRA_PX, desvio)) * escala))
    cv2.circle(img, (xd, y0 + alto_b // 2), alto_b // 2 + 2, VERDE_TEXTO if centrada else AMBAR, -1, cv2.LINE_AA)
    return img


TEXTO_AVISO = {"centrada": lambda: _("Bahtinov centrada"),
               "aprendida": lambda: _("Foco aprendido: relación de los colores guardada"),
               "enfocado": lambda: _("Enfocado")}


def texto_aviso(clave):
    """La frase para decir por voz (en el idioma de la app) o ""."""
    f = TEXTO_AVISO.get(clave)
    return f() if f else ""


class AyudaBahtinov:
    """
    Lo que se recuerda entre fotogramas del vivo (o entre fotos de prueba) al enfocar estrellas:

      - El desvío de la Bahtinov enseñado: en vivo, la mediana de los últimos VENTANA_BAHTINOV_S
        (el seeing lo mueve de un fotograma a otro); en fotos sueltas, el de la última. El signo
        se mantiene mientras la máscara no gire. «Centrada» con histéresis, y aviso al entrar.
      - La firma del foco: después de centrar la Bahtinov, al quitar la máscara (la estrella deja de
        tener picos), la relación entre las anchuras de los tres colores (R/G y B/G) de las
        siguientes FOTOGRAMAS_APRENDER tomas es la del foco bueno de este telescopio y esta cámara:
        se guarda en RUTA_FIRMA, una por modo (en vivo el verde es el superpíxel, en la foto el
        revelado completo, y las anchuras no salen iguales). Con un telescopio con color residual,
        en el foco el verde es más fino y los otros dos se abren; muy desenfocado, los tres son
        el mismo círculo y la relación vuelve a 1.
      - Sin máscara y con firma: «enfocado» cuando R/G y B/G llegan a los aprendidos y el verde está
        cerca del más fino de la sesión. Si la firma casi no se aparta de 1 (un reflector, o un
        refractor muy corregido con mal seeing), no sirve y se dice.
      - Un fotograma sin cruz no basta para dar la máscara por quitada: al tocar el enfocador el
        tubo vibra y la cruz se emborrona. En vivo hacen falta GRACIA_SIN_CRUZ_S seguidos sin ella;
        y una estrella movida (alargada) no cuenta nunca, ni para eso ni para la firma.
    """

    def __init__(self, ruta=RUTA_FIRMA, reloj=time.monotonic):
        self.ruta, self.reloj = ruta, reloj
        self._lock = threading.Lock()
        self.firmas = self._cargar()
        self.t_centrada = None          # la última vez que la Bahtinov estuvo centrada
        self._reiniciar(None)

    def _reiniciar(self, modo):
        self.modo = modo
        self.desvios = collections.deque()      # (t, desvío)
        self.normal = None
        self.centrada = False
        self.t_cruz = None                      # la última toma con la cruz de la Bahtinov
        self.muestras = []                    # (R/G, B/G) para aprender la firma
        self.relaciones = collections.deque()   # (t, R/G, B/G, verde) para comparar con la firma
        self.enfocado = False
        self.mejor_g = None
        self.t_ultimo = None

    def _cargar(self):
        try:
            with open(self.ruta, encoding="utf-8") as f:
                datos = json.load(f)
            return {m: v for m, v in datos.items() if isinstance(v, dict) and "rg" in v and "bg" in v}
        except (OSError, ValueError):
            return {}

    def _guardar(self):
        try:
            os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
            tmp = self.ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.firmas, f, indent=1)
            os.replace(tmp, self.ruta)
        except OSError:
            pass

    def anotar(self, info, medida, modo):
        """
        Una toma más. `info`: el de medir() (con fwhm_r y fwhm_b); `medida`: la de medir_bahtinov o
        None; `modo`: "vivo" o "foto". Añade a `info` las claves "bahtinov" y "firma" que
        correspondan y, si hay algo que decir por voz, "aviso" (centrada, aprendida, enfocado).
        """
        with self._lock:
            t = self.reloj()
            if modo != self.modo or (self.t_ultimo is not None and t - self.t_ultimo > 600):
                self._reiniciar(modo)
            self.t_ultimo = t
            ventana = VENTANA_BAHTINOV_S if modo == "vivo" else 0.0
            aviso = None
            hueco = medida is None and (info.get("movida") or (modo == "vivo" and self.t_cruz is not None
                                                                and t - self.t_cruz <= GRACIA_SIN_CRUZ_S))
            if hueco:
                pass            # sin cruz, pero quizá solo emborronada por la vibración: no cambia nada
            elif medida is not None:
                self.t_cruz = t
                self.muestras, self.enfocado = [], False
                self.relaciones.clear()
                n = np.array(medida["rectas"][0][:2])
                desvio = float(medida["desvio"])
                if self.normal is None:
                    self.normal = n
                elif float(n @ self.normal) < 0:
                    desvio = -desvio            # la misma raya con la normal al revés (ángulo cerca de ±90°)
                self.desvios.append((t, desvio))
                while len(self.desvios) > 1 and t - self.desvios[0][0] > ventana:
                    self.desvios.popleft()
                valor = float(np.median([d for __, d in self.desvios]))
                suficientes = len(self.desvios) >= (FOTOGRAMAS_CENTRADA if modo == "vivo" else 1)
                if not self.centrada and suficientes and abs(valor) <= UMBRAL_BAHTINOV_PX:
                    self.centrada, aviso = True, "centrada"
                elif self.centrada and abs(valor) > SALIDA_BAHTINOV_PX:
                    self.centrada = False
                if self.centrada:
                    self.t_centrada = t
                info["bahtinov"] = {"desvio": round(valor, 2), "centrada": self.centrada, "umbral": UMBRAL_BAHTINOV_PX,
                                    "angulos": medida["angulos"], "n": len(self.desvios)}
            else:
                self.desvios.clear()
                self.normal, self.centrada, self.t_cruz = None, False, None
                g, r, b = (info.get(k, float("nan")) for k in ("fwhm_g", "fwhm_r", "fwhm_b"))
                if all(v is not None and np.isfinite(v) and v > 0 for v in (g, r, b)) and not info.get("saturada"):
                    rg, bg = r / g, b / g
                    if self.t_centrada is not None and t - self.t_centrada <= APRENDER_S:
                        self.muestras.append((rg, bg))
                        total = FOTOGRAMAS_APRENDER.get(modo, 1)
                        info["firma"] = {"aprendiendo": len(self.muestras), "total": total}
                        if len(self.muestras) >= total:
                            m = np.array(self.muestras)
                            disp = 1.4826 * np.median(np.abs(m - np.median(m, axis=0)), axis=0) if len(m) > 2 else np.zeros(2)
                            self.firmas[modo] = {"rg": round(float(np.median(m[:, 0])), 3), "bg": round(float(np.median(m[:, 1])), 3),
                                                 "dispersion": round(float(disp.max()), 3), "n": len(m),
                                                 "fecha": time.strftime("%Y-%m-%d %H:%M")}
                            self._guardar()
                            self.t_centrada, self.muestras, aviso = None, [], "aprendida"
                    firma = self.firmas.get(modo)
                    if firma and "aprendiendo" not in info.get("firma", {}):
                        self.relaciones.append((t, rg, bg, g))
                        while len(self.relaciones) > 1 and t - self.relaciones[0][0] > ventana:
                            self.relaciones.popleft()
                        rg_v, bg_v, g_v = (float(np.median([v[i] for v in self.relaciones])) for i in (1, 2, 3))
                        self.mejor_g = g_v if self.mejor_g is None else min(self.mejor_g, g_v)
                        tol = max(TOLERANCIA_FIRMA, 2.0 * float(firma.get("dispersion", 0.0)))
                        util = max(abs(firma["rg"] - 1.0), abs(firma["bg"] - 1.0)) >= DISTINCION_FIRMA * tol
                        dist = max(abs(rg_v - firma["rg"]) / firma["rg"], abs(bg_v - firma["bg"]) / firma["bg"])
                        cerca = dist <= tol and g_v <= CERCA_MEJOR_G * self.mejor_g
                        if util and not self.enfocado and cerca:
                            self.enfocado, aviso = True, "enfocado"
                        elif self.enfocado and (not util or dist > SALIDA_FIRMA * tol):
                            self.enfocado = False
                        info["firma"] = {"rg": round(rg_v, 3), "bg": round(bg_v, 3), "rg_foco": firma["rg"], "bg_foco": firma["bg"],
                                         "util": util, "enfocado": self.enfocado}
            if aviso:
                info["aviso"] = aviso
            return info


ayuda_bahtinov = AyudaBahtinov()


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
    if len(anch) < ANGULOS_MINIMOS:
        info.update({"limbo": None, "n": int(len(anch)),
                     "motivo": _("el limbo no se ve bien (¿disco cortado por el borde, nubes, saturado?)")})
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
    En vivo: las medidas de los fotogramas y lo que se enseña con ellas. Medido con
    los vídeos de la Luna del 21-oct-2026 (20 fotogramas/s, 15,8 ms, Luna baja): el
    limbo de cada fotograma varía un 6–7 % con el mismo foco, y la nitidez de todo
    el disco o del terminador no es más estable: es el seeing, no la medida. La
    mediana de 5 fotogramas saltaba hasta un 8 % entre lecturas; el percentil 25
    de los últimos 2,5 s se queda en el 1–2 % y además mide el desenfoque real
    (los fotogramas con mejor seeing son los que menos lo tapan, como el apilado
    de los mejores).
      - anotar(): guarda la medida si vale. Con la Luna cortada por el borde de la
        imagen el círculo se ajusta al terminador y el limbo daba 2,0–2,9 px con
        12–18 ángulos cuando el real era 3,5: medir_disco exige ANGULOS_MINIMOS y
        aquí se exige además el radio de la sesión (mediana de los últimos).
      - resumen(): el número (percentil de la ventana, con histéresis para que no
        parpadee), el mejor de la sesión (solo de ventanas con fotogramas de sobra),
        el recorte del fotograma más nítido de la ventana y cuántos fotogramas tiene.
      - tendencia(): los números enseñados en el último minuto, para la gráfica.
      - veredicto(): cada VOZ_CADA_S, «mejora», «empeora» o «igual» comparando la
        ventana actual con la del último veredicto, para decirlo por voz; None si no toca.
    `reloj` se cambia en las pruebas (los tiempos del vídeo).
    """
    def __init__(self, reloj=time.monotonic):
        self._reloj = reloj
        self.medidas = collections.deque()      # (t, anchura, recorte) aceptadas de los últimos 2·VENTANA_S
        self.radios = collections.deque(maxlen=30)
        self.serie = collections.deque()        # (t, número enseñado) del último TENDENCIA_S
        self._centro = None                     # (t, cx, cy) del último disco visto, para la velocidad
        self.mostrado = None
        self.mejor = None
        self.rechazadas = 0                     # medidas descartadas por el radio
        self.movidas = 0                        # medidas descartadas porque el disco se movía
        self._t_voz = None
        self._dicho = None
        self._referencia = None                 # percentil de la ventana en el último veredicto
        self.zona_v = None                      # (t_ini, t_fin, t_centro, mínimo) de la última zona plana cruzada
        self._v_avisada = None                  # t_ini de la zona por la que ya se ha dicho «te has pasado»
        self._v_esperando = False               # tras «te has pasado», se espera a que vuelva a la zona
        self._v_ultimo_t = None                 # último punto de la serie ya mirado

    def anotar(self, info, recorte, t=None):
        """Guarda la medida del fotograma (`info` de medir_disco) si vale; True si se ha aceptado."""
        t = self._reloj() if t is None else float(t)
        anchura = (info or {}).get("limbo")
        if anchura is None or not np.isfinite(anchura):
            return False
        r, cx, cy = info.get("r"), info.get("cx"), info.get("cy")
        if r:
            # El radio de la sesión se sigue con todos los círculos, aceptados o no: si los primeros
            # fotogramas fueran malos (la Luna medio fuera al empezar), la mediana se corrige sola
            ref = float(np.median(self.radios)) if len(self.radios) >= 5 else None
            self.radios.append(float(r))
            if ref is not None and abs(float(r) - ref) > TOLERANCIA_RADIO * ref:
                self.rechazadas += 1
                return False
        if cx is not None and cy is not None:
            anterior, self._centro = self._centro, (t, float(cx), float(cy))
            if anterior is not None and 0 < t - anterior[0] < 0.5:
                if math.hypot(float(cx) - anterior[1], float(cy) - anterior[2]) / (t - anterior[0]) > VELOCIDAD_MAX:
                    self.movidas += 1
                    return False
        self.medidas.append((t, float(anchura), recorte))
        while self.medidas and t - self.medidas[0][0] > 2 * VENTANA_S:
            self.medidas.popleft()
        return True

    def _ventana(self, t, desde, hasta):
        """[(t, anchura)] de las medidas con desde < t − t_medida <= hasta."""
        return [(ta, a) for (ta, a, __) in self.medidas if desde < t - ta <= hasta]

    def resumen(self, t=None):
        """(número enseñado, mejor de la sesión, recorte más nítido de la ventana, fotogramas en la ventana); sin ventana, (None, mejor, None, 0)."""
        t = self._reloj() if t is None else float(t)
        ventana = [(ta, a, rec) for (ta, a, rec) in self.medidas if t - ta <= VENTANA_S]
        if not ventana:
            return None, self.mejor, None, 0
        actual = float(np.percentile([a for __, a, __ in ventana], PERCENTIL_VENTANA))
        cerca = self.mejor is not None and actual <= self.mejor * (1 + CERCA_MINIMO)
        if self.mostrado is None or cerca or abs(actual - self.mostrado) > HISTERESIS_PX:
            # Cerca del mínimo, sin histéresis: ahí cada centésima cuenta (con histéresis la
            # línea se quedaba plana aunque el enfocador siguiera girando)
            self.mostrado = actual
        if len(ventana) >= FOTOGRAMAS_MEJOR and (self.mejor is None or self.mostrado < self.mejor):
            self.mejor = self.mostrado
        if not self.serie or t - self.serie[-1][0] >= TENDENCIA_CADA_S:
            self.serie.append((t, self.mostrado))
        while self.serie and t - self.serie[0][0] > TENDENCIA_S:
            self.serie.popleft()
        return self.mostrado, self.mejor, min(ventana, key=lambda v: v[1])[2], len(ventana)

    def tendencia(self):
        """[(t, número enseñado)] del último minuto, de más antiguo a más nuevo."""
        return list(self.serie)

    def curva_v(self, t=None):
        """
        Curva en V sobre la serie del último minuto: busca la zona plana alrededor del
        mínimo (valores a menos de ZONA_V / ZONA_V_PX del mínimo, tras una mediana de
        SUAVIZADO_V_S) y devuelve el aviso para la voz o None:
          - «pasado»: se ha cruzado la última zona plana y el valor lleva SUBIDA_V_S
            por encima de mínimo + SUBIDA_V·zona. Se guarda la zona en `zona_v`
            (inicio, fin, centro en tiempo y mínimo) para marcarla en la gráfica: el
            foco bueno es su centro, no el punto más bajo, porque cerca del foco el
            número cambia menos que el seeing. Una vez por zona.
          - «zona»: tras «pasado», el valor ha vuelto a la zona plana (una vez).
        Si se cruza la zona hacia el otro lado, la nueva zona vuelve a avisar. Con
        «pasado» se calla un rato el «empeora» (es lo mismo dicho peor).
        """
        t = self._reloj() if t is None else float(t)
        if len(self.serie) < 8 or self.serie[-1][0] == self._v_ultimo_t:
            return None                                          # solo cuando entra un punto nuevo (cada 0,25 s)
        self._v_ultimo_t = self.serie[-1][0]
        ts = np.array([p[0] for p in self.serie], float)
        vs = np.array([p[1] for p in self.serie], float)
        n_suave = max(1, int(round(SUAVIZADO_V_S / TENDENCIA_CADA_S)))
        relleno = np.concatenate([np.full(n_suave - 1, vs[0]), vs])
        suave = np.median(np.lib.stride_tricks.sliding_window_view(relleno, n_suave), axis=1)
        vmin = float(suave.min())
        zona = max(ZONA_V_PX, ZONA_V * vmin)
        dentro = suave <= vmin + zona
        if self._v_esperando and dentro[-1]:
            self._v_esperando = False
            self._t_voz = t
            return "zona"
        # La última zona plana: el último tramo seguido de puntos dentro
        idx = np.nonzero(dentro)[0]
        fin = int(idx[-1])
        ini = fin
        while ini > 0 and dentro[ini - 1]:
            ini -= 1
        if fin == len(suave) - 1:
            return None                                          # aún dentro de la zona
        fuera = (suave > vmin + SUBIDA_V * zona) & (ts > ts[fin])
        alto = np.nonzero(fuera)[0]
        if len(alto) == 0 or not fuera[-1]:
            return None
        # Subida sostenida: los últimos SUBIDA_V_S segundos, todos por encima
        if np.any(~fuera[ts >= t - SUBIDA_V_S]) or t - ts[alto[0]] < SUBIDA_V_S:
            return None
        if self._v_avisada is not None and abs(ts[ini] - self._v_avisada) < 1e-6:
            return None
        self._v_avisada = float(ts[ini])
        self.zona_v = (float(ts[ini]), float(ts[fin]), 0.5 * float(ts[ini] + ts[fin]), vmin)
        self._v_esperando = True
        self._t_voz = t
        self._referencia = float(vs[-1])                         # lo siguiente que diga, contra el valor de ahora
        self._dicho = "empeora"
        return "pasado"

    def veredicto(self, t=None):
        """
        «mejora» / «empeora» si el percentil de la ventana actual difiere del que había
        en el último veredicto más que el ruido (UMBRAL_VOZ, UMBRAL_VOZ_PX y 3 veces la
        dispersión); «igual» la primera vez que deja de cambiar; None si no toca hablar
        (cada VOZ_CADA_S) o la ventana no está llena. La referencia es la del último
        veredicto y no la ventana anterior: el percentil bajo de una ventana a medio
        cambiar ya es el valor nuevo, y comparando ventanas seguidas una mejora se perdía.
        """
        t = self._reloj() if t is None else float(t)
        if self._t_voz is not None and t - self._t_voz < VOZ_CADA_S:
            return None
        ahora = self._ventana(t, -1.0, VENTANA_S)
        # La ventana tiene que estar llena (fotogramas de sobra y casi todo su tiempo): al empezar,
        # o tras un corte (la imagen se movía, el disco se salió), está a medias
        if len(ahora) < FOTOGRAMAS_MEJOR or ahora[-1][0] - ahora[0][0] < 0.7 * VENTANA_S:
            return None
        va = np.array([a for __, a in ahora])
        a = float(np.percentile(va, PERCENTIL_VENTANA))
        self._t_voz = t
        if self._referencia is None:
            self._referencia = a
            return None
        # Ruido del percentil: la dispersión de los fotogramas de la ventana entre la raíz de su
        # número, ×1,4 por ser un percentil y no la media y ×√2 porque la referencia lleva el suyo
        ruido = 2.0 * float(va.std()) / math.sqrt(len(va))
        umbral = max(UMBRAL_VOZ_PX, UMBRAL_VOZ * self._referencia, 3.0 * ruido)
        if a < self._referencia - umbral:
            v = "mejora"
        elif a > self._referencia + umbral:
            v = "empeora"
        else:
            v = "igual"
        if v == "empeora" and self._v_esperando:
            # Tras «te has pasado» el valor sigue subiendo un par de segundos por el retraso de la
            # ventana aunque ya se esté volviendo: «empeora» ahí confunde
            self._referencia = a
            return None
        if v == "igual":
            self._referencia = 0.7 * self._referencia + 0.3 * a      # sigue despacio la deriva del seeing
            if self._dicho in (None, "igual"):
                self._dicho = v
                return None
        else:
            self._referencia = a
        self._dicho = v
        return v


TEXTO_VEREDICTO = {"mejora": lambda: _("mejora"), "empeora": lambda: _("empeora"), "igual": lambda: _("igual"),
                   "pasado": lambda: _("te has pasado del foco, vuelve atrás"),
                   "zona": lambda: _("estás en la zona buena")}


def texto_veredicto(clave):
    """La palabra para decir por voz (en el idioma de la app) o ""."""
    f = TEXTO_VEREDICTO.get(clave)
    return f() if f else ""


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
        movidas = historial.movidas
        if historial.anotar(info, recorte_estrella(gris, info["x"], info["y"])):
            pulsado = _punto_en_vista(info["x"], info["y"], gris.shape, paso, orientar, girar)
        elif historial.movidas > movidas:
            info.update({"limbo": None, "motivo": _("la imagen se mueve")})
        else:
            info.update({"limbo": None, "motivo": _("el disco no es el de antes (¿cortado por el borde?)")})
    actual, mejor, recorte, n_ventana = historial.resumen()
    if mejor is not None:
        info["mejor"] = round(mejor, 2)
    if actual is not None:
        info["limbo_foto"] = info.get("limbo")
        info["limbo"] = round(actual, 2)
        info["ventana"] = n_ventana
        info.pop("motivo", None)
    veredicto = historial.curva_v() or historial.veredicto()
    if veredicto:
        info["veredicto"] = veredicto
    if historial.zona_v is not None:
        info["zona_v"] = [round(v, 2) for v in historial.zona_v]
    vista = gris[::paso, ::paso] if paso > 1 else gris
    girada, encuadre = girar(orientar(np.ascontiguousarray(vista)))
    return componer(girada, recorte, info, pulsado=pulsado, tendencia=historial.tendencia(), etiqueta=info.get("recorte"),
                    zona_v=historial.zona_v), encuadre, info


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
    return componer(girada, recorte, info, pulsado=pulsado, etiqueta=info.get("recorte")), encuadre, info


def _recorte_estrella_o_bahtinov(gris, lum, info, escala_px, modo, ayuda=None):
    """
    El recorte de arriba a la derecha: si la estrella tiene la cruz de una Bahtinov, el de la
    Bahtinov con la cruceta virtual (de `lum`, los tres colores); si no, la estrella en verde ×5.
    Anota la toma en AyudaBahtinov (desvío, firma del foco, aviso), que deja lo suyo en `info`.
    """
    medida = None
    try:
        medida = medir_bahtinov(lum, info["x"], info["y"], escala_px)
    except Exception:
        medida = None
    (ayuda or ayuda_bahtinov).anotar(info, medida, modo)
    if medida is not None:
        return recorte_bahtinov(lum, info["x"], info["y"], medida, info.get("bahtinov"))
    return recorte_estrella(gris, info["x"], info["y"])


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
    recorte = _recorte_estrella_o_bahtinov(gris, r + g + b, info, 1.0, "foto")
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
    rojo, verde, azul = planos_superpixel(cfa, patron)       # resolución completa del superpíxel (2 px del sensor)
    info = medir(verde, rojo, azul, escala_px=2.0)
    gris_completo = revelar_gris(verde)
    lum = verde if rojo is None else rojo + verde + azul
    recorte = _recorte_estrella_o_bahtinov(gris_completo, lum, info, 2.0, "vivo")
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
