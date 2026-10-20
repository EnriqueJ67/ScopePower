# luna_anotada.py
#
# Los accidentes de la Luna con su nombre encima de una foto: mares, cráteres, montes,
# rimas... (el nomenclátor de la UAI, en luna/accidentes_luna.csv). Es la capa
# «Anotaciones» de la galería cuando la foto es de la Luna: ASTAP no sirve aquí, porque
# en una foto de la Luna no hay estrellas con que resolver el campo.
#
#   1. Cómo se ve la Luna en ese instante y desde ese lugar: hacia dónde apunta su eje,
#      qué cara asoma por la libración y de dónde le da el Sol. La orientación sale del
#      modelo de rotación de la UAI (Archinal et al. 2011, el que usan las efemérides para
#      el sistema de coordenadas de los nombres) y las posiciones de la Luna y el Sol, de
#      astropy. Precisión de décimas de grado: sobra.
#   2. Dónde está el disco en la foto: un círculo ajustado con RANSAC al borde de la zona
#      brillante. El radio no se conoce (el JPG puede estar recortado o con zoom), pero en
#      cualquier fase el limbo iluminado es media circunferencia y el terminador no es un
#      círculo, así que gana el limbo. Con el disco cortado por los bordes de la foto, un
#      arco corto del limbo da mal el radio y un trozo de terminador puede parecer limbo: se
#      prueban varios círculos (también un barrido de radios) y gana el que, pintando la Luna
#      que se debería ver con él, más se parece a la foto entera. Sin limbo a la vista (un
#      trozo del interior), el trozo se busca en la Luna esperada (escala, giro, espejo y
#      posición). Si dos soluciones distintas encajan casi igual, no se anota.
#   3. El giro y el espejo de la foto: se pinta la Luna que se debería ver (el mapa de
#      albedo del LROC con la iluminación de ese instante) y se compara con la foto en
#      coordenadas polares, donde girar es desplazar y el espejo es invertir el ángulo.
#      Gana el giro con mejor correlación, con espejo o sin él. La iluminación da el giro
#      y los mares deciden el espejo; así no depende de cómo estaba montada la cámara
#      ni de si la foto se giró después en el editor.
#   4. Cada accidente se proyecta sobre el disco (proyección ortográfica: los cráteres
#      del borde salen como elipses aplastadas) y se pasa a píxeles del JPG; también los
#      límites de los mares (luna/contornos_mares.json, del LROC).

import os
import re
import csv
import math
import json
import time
import logging
import threading
from datetime import datetime

import numpy as np

from idioma import _

log = logging.getLogger("scopepower.luna_anotada")

CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "luna")
RUTA_ACCIDENTES = os.path.join(CARPETA, "accidentes_luna.csv")
RUTA_ALBEDO = os.path.join(CARPETA, "albedo_luna.jpg")
RUTA_MARES = os.path.join(CARPETA, "contornos_mares.json")
RADIO_KM = 1737.4
ANCHO_ANALISIS = 800           # la foto se analiza a ~800 px: el giro sale a décimas de grado
ANILLOS, ANGULOS = 64, 720     # rejilla polar de la comparación (medio grado por paso)
MIN_DIAMETRO_PX = 6.0          # un cráter más pequeño en el JPG no se distingue
AMBIGUO = 0.01                 # otro disco distinto, ya afinado, a menos de esto de parecido: trozo ambiguo, no se anota
LIMBO_MIN_GRADOS = 40.0        # con el disco cortado, menos limbo iluminado a la vista no sitúa el trozo con seguridad
LIMBO_BRILLO_MAX = 0.45        # fuera del limbo, el cielo: menos de esto del brillo de la Luna junto al borde...
LIMBO_TEXTURA_MAX = 0.35       # ...o, de día con el cielo claro, menos de esto de su textura
LADO_VISTA = 512               # lado de la imagen de la Luna esperada para comparar trozos (se compara a 240 px)
LADO_VISTA_TROZO = 1024        # ...y para buscar un trozo sin limbo (la foto a 128 px puede ser 2/5 del radio)
SEGUIR_TOLERANCIA = 0.03       # en vivo, el trozo se sigue mientras encaje casi tan bien como en la imagen anterior
MARGEN_SIN_LIMBO = 0.04        # sin limbo, la mejor solución debe sacar esto a cualquier otra distinta
PARECIDO_MIN_SIN_LIMBO = 0.75
MIN_CRATER_NOCHE_KM = 90.0    # en la parte de noche solo se nombran los cráteres desde este tamaño
MAX_ACCIDENTES = 300
NOMBRES_LUNA = {"luna", "moon"}

# Familias del nomenclátor (código del USGS) y su peso al repartir los rótulos: con el
# mismo tamaño, antes un mar que un cráter y antes un cráter que una rima
FAMILIAS = {
    "OC": ("mar", 4.0), "ME": ("mar", 4.0), "LC": ("mar", 2.0), "SI": ("mar", 2.0),
    "PA": ("mar", 2.0), "PL": ("mar", 2.0),
    "AA": ("crater", 1.5),
    "MO": ("monte", 0.8), "RU": ("monte", 0.8), "PR": ("monte", 0.8), "AL": ("monte", 0.8),
    "VA": ("rima", 0.6), "RI": ("rima", 0.6), "CA": ("rima", 0.6),
    "DO": ("dorso", 0.6),
    "ST": ("estacion", 0.0),   # alunizajes: siempre, aunque midan cero
}

_lock = threading.Lock()
_cache = {}


class ErrorLuna(Exception):
    """No se puede anotar la Luna en esta foto (el texto dice por qué)."""


class SinLuna(ErrorLuna):
    """En la foto no hay nada brillante que pueda ser la Luna (negra, uniforme, tapa puesta)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Datos
# ─────────────────────────────────────────────────────────────────────────────
def _accidentes():
    if "accidentes" not in _cache:
        filas = []
        with open(RUTA_ACCIDENTES, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                codigo = r["codigo"]
                if codigo not in FAMILIAS:
                    continue
                filas.append({"nombre": r["nombre"], "codigo": codigo, "familia": FAMILIAS[codigo][0],
                              "lat": float(r["lat"]), "lon": float(r["lon"]), "diametro": float(r["diametro_km"])})
        _cache["accidentes"] = filas
    return _cache["accidentes"]


def _albedo():
    """Mapa de albedo (float32, media 1) en proyección cilíndrica: lon −180..180, lat 90..−90."""
    if "albedo" not in _cache:
        import cv2
        m = cv2.imread(RUTA_ALBEDO, cv2.IMREAD_GRAYSCALE)
        if m is None:
            raise ErrorLuna(_("Falta el mapa de la Luna ({ruta}).", ruta=RUTA_ALBEDO))
        m = m.astype(np.float32)
        _cache["albedo"] = m / float(m.mean())
    return _cache["albedo"]


# ─────────────────────────────────────────────────────────────────────────────
#  Geometría: la Luna vista desde el observatorio en un instante
# ─────────────────────────────────────────────────────────────────────────────
def orientacion_iau(jd_tdb):
    """Polo (α0, δ0) y meridiano cero W de la Luna en grados (modelo IAU, Archinal et al. 2011)."""
    d = jd_tdb - 2451545.0
    T = d / 36525.0
    E = [None,
         125.045 - 0.0529921 * d, 250.089 - 0.1059842 * d, 260.008 + 13.0120009 * d,
         176.625 + 13.3407154 * d, 357.529 + 0.9856003 * d, 311.589 + 26.4057084 * d,
         134.963 + 13.0649930 * d, 276.617 + 0.3287146 * d, 34.226 + 1.7484877 * d,
         15.134 - 0.1589763 * d, 119.743 + 0.0036096 * d, 239.961 + 0.1643573 * d,
         25.053 + 12.9590088 * d]
    s = [None] + [math.sin(math.radians(e)) for e in E[1:]]
    c = [None] + [math.cos(math.radians(e)) for e in E[1:]]
    alfa = (269.9949 + 0.0031 * T - 3.8787 * s[1] - 0.1204 * s[2] + 0.0700 * s[3] - 0.0172 * s[4]
            + 0.0072 * s[6] - 0.0052 * s[10] + 0.0043 * s[13])
    delta = (66.5392 + 0.0130 * T + 1.5419 * c[1] + 0.0239 * c[2] - 0.0278 * c[3] + 0.0068 * c[4]
             - 0.0029 * c[6] + 0.0009 * c[7] + 0.0008 * c[10] - 0.0009 * c[13])
    W = (38.3213 + 13.17635815 * d - 1.4e-12 * d * d + 3.5610 * s[1] + 0.1208 * s[2] - 0.0642 * s[3]
         + 0.0158 * s[4] + 0.0252 * s[5] - 0.0066 * s[6] - 0.0047 * s[7] - 0.0046 * s[8] + 0.0028 * s[9]
         + 0.0052 * s[10] + 0.0040 * s[11] + 0.0019 * s[12] - 0.0044 * s[13])
    return alfa, delta, W


def _matriz_cuerpo(alfa, delta, W):
    """Matriz que pasa un vector de ICRS a los ejes de la Luna (x al meridiano cero, z al polo norte)."""
    a, d, w = (math.radians(v) for v in (alfa, delta, W))
    polo = np.array([math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)])
    nodo = np.array([-math.sin(a), math.cos(a), 0.0])
    m = np.cross(polo, nodo)
    x = math.cos(w) * nodo + math.sin(w) * m
    y = -math.sin(w) * nodo + math.cos(w) * m
    return np.array([x, y, polo])


def _lat_lon(v):
    v = np.asarray(v, dtype=np.float64)
    v = v / np.linalg.norm(v)
    return math.degrees(math.asin(v[2])), (math.degrees(math.atan2(v[1], v[0])) + 180.0) % 360.0 - 180.0


def geometria(instante, lat, lon, alt_m=0.0):
    """
    La Luna vista desde (lat, lon, alt_m) en el instante unix dado: la matriz a sus ejes, la
    dirección hacia ella (u), el este y el norte del cielo en su posición (e, n), la
    dirección al Sol desde la Luna (s), la libración, el punto subsolar, el ángulo del eje
    (desde el norte hacia el este), la fracción iluminada y el radio aparente.
    """
    from astropy.coordinates import EarthLocation, get_body
    from astropy.time import Time
    import astropy.units as u
    t = Time(float(instante), format="unix")
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    luna = get_body("moon", t, lugar)
    sol = get_body("sun", t, lugar)
    v_luna = np.asarray(luna.cartesian.xyz.to(u.km).value, dtype=np.float64)
    v_sol = np.asarray(sol.cartesian.xyz.to(u.km).value, dtype=np.float64)
    R = _matriz_cuerpo(*orientacion_iau(float(t.tdb.jd)))
    dist = float(np.linalg.norm(v_luna))
    uu = v_luna / dist
    e = np.cross([0.0, 0.0, 1.0], uu)
    e /= np.linalg.norm(e)
    n = np.cross(uu, e)
    s = v_sol - v_luna
    s /= np.linalg.norm(s)
    lib_lat, lib_lon = _lat_lon(R @ -uu)
    sub_lat, sub_lon = _lat_lon(R @ s)
    polo = R[2]
    pa_eje = (math.degrees(math.atan2(float(polo @ e), float(polo @ n)))) % 360.0
    fase = (1.0 + float(s @ -uu)) / 2.0
    return {"R": R, "u": uu, "e": e, "n": n, "s": s, "instante": float(instante),
            "libracion_lat": lib_lat, "libracion_lon": lib_lon, "subsolar_lat": sub_lat, "subsolar_lon": sub_lon,
            "pa_eje": pa_eje, "iluminada": fase, "radio_arcsec": math.degrees(math.asin(RADIO_KM / dist)) * 3600.0}


def _vectores_superficie(lat, lon):
    """Vectores unitarios (N×3, ejes de la Luna) de los puntos (lat, lon) en grados."""
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def a_vista(geo, f_cuerpo):
    """
    Puntos de la superficie (ejes de la Luna) en la vista con el norte arriba y el este a la
    izquierda, como se ve en el cielo: X a la derecha (oeste), Y arriba, en radios lunares.
    Devuelve X, Y, cos_e (hacia el observador: > 0 visible) y cos_i (> 0 iluminado).
    """
    f = f_cuerpo @ geo["R"]                       # a ICRS (R es ortogonal: Rᵀ·v = v·R)
    return -(f @ geo["e"]), f @ geo["n"], -(f @ geo["u"]), f @ geo["s"]


def _desde_vista(geo, X, Y):
    """Lo contrario: el punto de la cara visible que se ve en (X, Y) (|X,Y| < 1), en ejes de la Luna."""
    z = np.sqrt(np.clip(1.0 - X * X - Y * Y, 0.0, 1.0))
    f = (-X)[..., None] * geo["e"] + Y[..., None] * geo["n"] - z[..., None] * geo["u"]
    return f @ geo["R"].T


# ─────────────────────────────────────────────────────────────────────────────
#  El disco y su orientación en la foto
# ─────────────────────────────────────────────────────────────────────────────
def _circulo_3_puntos(p1, p2, p3):
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _ajustar_radio_fijo(puntos, centro, radio, iteraciones=15):
    """Gauss-Newton: centro que minimiza Σ(|p−c| − R)² con R fijo."""
    c = np.array(centro, dtype=np.float64)
    for __ in range(iteraciones):
        d = puntos - c
        dist = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
        J = -d / dist[:, None]
        paso, *__ = np.linalg.lstsq(J, -(dist - radio), rcond=None)
        c += paso
        if np.hypot(*paso) < 1e-3:
            break
    return c


def _ajustar_circulo(puntos, cx, cy, r, iteraciones=20):
    """Gauss-Newton: centro y radio que minimizan Σ(|p−c| − r)²."""
    p = np.array([cx, cy, r], dtype=np.float64)
    for __ in range(iteraciones):
        d = puntos - p[:2]
        dist = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
        J = np.column_stack([-d[:, 0] / dist, -d[:, 1] / dist, -np.ones(len(d))])
        paso, *__ = np.linalg.lstsq(J, -(dist - p[2]), rcond=None)
        p += paso
        if np.abs(paso).max() < 1e-3:
            break
    return p


def _borde_brillante(lum):
    """
    El borde de la zona brillante mayor (la Luna) con lo que hace falta para buscar el limbo:
    dict con suave (la imagen suavizada), comp (la zona, 0/1), puntos (N×2), grad (dirección
    hacia lo brillante, unitaria) y nitidez (cuánto cambia el brillo, 1 = de los más nítidos).
    """
    import cv2
    lum = np.asarray(lum, dtype=np.float32)
    h, w = lum.shape
    suave = cv2.GaussianBlur(lum, (0, 0), 1.2)
    bajo, alto = float(np.percentile(suave, 5)), float(np.percentile(suave, 99.5))
    if alto - bajo < 0.02 * max(alto, 1.0):
        raise SinLuna(_("No se ve la Luna en la foto: la imagen es casi uniforme."))
    mascara = (suave > bajo + 0.3 * (alto - bajo)).astype(np.uint8)
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, etiquetas, stats, __ = cv2.connectedComponentsWithStats(mascara, 8)
    if n <= 1:
        raise SinLuna(_("No se ve la Luna en la foto."))
    mayor = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    comp = (etiquetas == mayor).astype(np.uint8)
    contornos, __ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    puntos = max(contornos, key=len).reshape(-1, 2).astype(np.float64)
    # El corte de la foto no es limbo: fuera los puntos pegados a los bordes (una recta ahí
    # encajaría con un círculo enorme)
    dentro = (puntos[:, 0] > 3) & (puntos[:, 0] < w - 4) & (puntos[:, 1] > 3) & (puntos[:, 1] < h - 4)
    puntos = puntos[dentro]
    if len(puntos) < 40:
        raise ErrorLuna(_("Casi todo el borde de la Luna queda fuera de la foto."))
    # Hacia dónde está lo brillante en cada punto del borde: en el limbo apunta al centro
    # del disco; en el terminador, hacia fuera del círculo que pasaría por él (si no, un
    # terminador casi recto encaja con un círculo enorme)
    gx = cv2.Sobel(suave, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(suave, cv2.CV_32F, 0, 1, ksize=3)
    xi, yi = puntos[:, 0].astype(int), puntos[:, 1].astype(int)
    grad = np.column_stack([gx[yi, xi], gy[yi, xi]]).astype(np.float64)
    nitidez = np.hypot(grad[:, 0], grad[:, 1])
    grad /= np.maximum(nitidez, 1e-6)[:, None]
    nitidez /= max(float(np.percentile(nitidez, 95)), 1e-6)

    return {"suave": suave, "comp": comp, "puntos": puntos, "grad": grad, "nitidez": nitidez}


def _en_circulo(borde, c, tol):
    """Puntos del borde a menos de tol del círculo c y con lo brillante hacia su centro."""
    puntos, grad = borde["puntos"], borde["grad"]
    d = np.column_stack([c[0] - puntos[:, 0], c[1] - puntos[:, 1]])
    dist = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
    hacia_dentro = (grad * d).sum(axis=1) / dist > 0.6
    return (np.abs(dist - c[2]) < tol) & hacia_dentro


def _abarca(pts, c):
    """Grados de circunferencia que cubren los puntos (sin contar el hueco mayor)."""
    if len(pts) < 2:
        return 0.0
    angs = np.sort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
    huecos = np.diff(np.concatenate([angs, angs[:1] + 2.0 * math.pi]))
    return math.degrees(2.0 * math.pi - float(huecos.max()))


def limbo_visible(lum, disco):
    """Grados de limbo iluminado que se ven en la foto con ese disco."""
    borde = _borde_brillante(lum)
    c = (disco["cx"], disco["cy"], disco["r"])
    return _abarca(borde["puntos"][_en_circulo(borde, c, max(1.5, 0.012 * disco["r"]))], c)


def localizar_disco(lum, semilla=0):
    """
    Centro y radio del disco de la Luna en `lum` (luminancia float) sin saber su tamaño: el
    mejor de candidatos_disco(). Devuelve dict cx, cy, r, arco (fracción del limbo dentro de
    la foto que encaja en el círculo) y parcial (si el disco se sale de la foto).
    """
    return candidatos_disco(lum, semilla)[0]


def candidatos_disco(lum, semilla=0, cuantos=4):
    """
    Círculos que pueden ser el disco de la Luna, el más probable primero: círculos de tres
    puntos del borde de la zona brillante, puntuados por lo nítido que es el borde encima
    (el limbo es un corte neto contra el cielo; el terminador, un degradado con sombras).
    Vale con el disco cortado por los bordes de la foto, mientras se vea un arco del limbo
    iluminado. Con el disco entero gana casi siempre el primero; con un trozo, un arco del
    terminador puede parecerse a uno del limbo, y por eso hay varios: anotar() se queda con
    el que mejor encaja con la Luna que se debería ver.
    """
    h, w = np.asarray(lum).shape[:2]
    borde = _borde_brillante(lum)
    suave, comp, puntos, nitidez = borde["suave"], borde["comp"], borde["puntos"], borde["nitidez"]

    def _en_circulo_(c, tol):
        return _en_circulo(borde, c, tol)

    rng = np.random.default_rng(semilla)
    # Hasta 6 veces la foto: un trozo ampliado de la Luna en el que solo se ve un arco del limbo
    r_min, r_max = 0.03 * max(w, h), 6.0 * max(w, h)
    yy, xx = np.nonzero(comp)
    t = np.linspace(0.0, 2.0 * math.pi, 2880, endpoint=False)
    salida, quedan = [], np.ones(len(puntos), bool)

    def _validar(c, tol):
        """El círculo c como candidato (dict), o None si no puede ser el disco."""
        cx, cy, r = (float(v) for v in c)
        if not (r_min < r < r_max):
            return None
        inliers = puntos[_en_circulo_(c, tol)]
        # El limbo iluminado es media circunferencia en cualquier fase (de lo que cae dentro
        # de la foto, si el disco se sale): bastante menos, no es la Luna
        xs, ys = cx + r * np.cos(t), cy + r * np.sin(t)
        dentro_img = float(np.mean((xs > 1) & (xs < w - 2) & (ys > 1) & (ys < h - 2)))
        arco = len(inliers) / (2.0 * math.pi * r * max(dentro_img, 1e-3))
        # Lo brillante tiene que caer dentro del círculo: un círculo pequeño metido en una
        # zona brillante mayor (un trozo del interior) no es el disco
        fuera = float(np.mean(np.hypot(xx - cx, yy - cy) > 1.03 * r)) if len(xx) else 1.0
        parcial = cx - r < 0 or cy - r < 0 or cx + r > w or cy + r > h
        # Con el disco cortado, lo que cae dentro de la foto puede ser sobre todo limbo a
        # oscuras: se pide menos arco iluminado (decide la comparación con el modelo)
        if arco < (0.12 if parcial else 0.3) or len(inliers) < max(25.0, 0.08 * (w + h)) or fuera > 0.04 \
                or _abarca(inliers, (cx, cy)) < 20.0:
            return None
        cielo = _cielo_fuera(suave, inliers, (cx, cy, r))
        if cielo is None:
            return None
        return {"cx": cx, "cy": cy, "r": r, "arco": round(arco, 3), "parcial": bool(parcial), **cielo}

    # RANSAC en secuencia: tras cada candidato se quitan sus puntos y se busca el siguiente,
    # así un arco corto del limbo tiene su candidato aunque el terminador tenga más puntos
    for __ in range(cuantos):
        idx = np.flatnonzero(quedan)
        if len(idx) < 30:
            break
        mejor, mejor_p = None, 0.0
        for __ in range(400):
            i, j, k = rng.choice(idx, 3, replace=False)
            c = _circulo_3_puntos(puntos[i], puntos[j], puntos[k])
            if c is None or not (r_min < c[2] < r_max):
                continue
            sobre = _en_circulo_(c, max(1.5, 0.012 * c[2])) & quedan
            p = float(nitidez[sobre].sum())
            # Un arco de menos de 20° casi no tiene curvatura: en una Luna gibosa cortada, el
            # terminador (una curva suave) encajaría con un círculo enorme
            if p > mejor_p and sobre.sum() >= 20 and _abarca(puntos[sobre], c) >= 20.0:
                mejor, mejor_p = c, p
        if mejor is None:
            break
        # Afinado con la tolerancia cada vez más estrecha: casi en Luna llena el terminador
        # pasa muy cerca del limbo y con la tolerancia ancha tiraría del círculo
        for fraccion in (0.012, 0.008, 0.005):
            sobre = _en_circulo_(mejor, max(1.0, fraccion * mejor[2])) & quedan
            if sobre.sum() < 20:
                break
            afinado = _ajustar_circulo(puntos[sobre], *mejor)
            if not (r_min < afinado[2] < r_max):      # un arco casi recto puede escaparse: se queda el de RANSAC
                break
            mejor = afinado
        quedan &= ~_en_circulo_(mejor, max(3.0, 0.02 * mejor[2]))
        cand = _validar(mejor, max(1.0, 0.005 * mejor[2]))
        if cand:
            salida.append(cand)
            if not cand["parcial"]:            # el disco entero a la vista: no hace falta buscar más
                break

    # Con el disco cortado, un arco corto (y el limbo tiene montañas) da mal el radio, y un
    # trozo de terminador puede pasar por limbo. Barrido de radios: para cada uno, el centro
    # con RANSAC de dos puntos (con el radio fijo, dos puntos del borde dan dos centros) que
    # más borde nítido tiene encima. La comparación con la Luna que se debería ver elige entre
    # todos (anotar)
    if not salida or salida[0]["parcial"]:
        rk = max(r_min, 0.2 * max(w, h))
        while rk < r_max:
            mejor, mejor_p = None, 0.0
            for __ in range(150):
                i, j = rng.choice(len(puntos), 2, replace=False)
                p, q = puntos[i], puntos[j]
                cuerda = float(np.hypot(*(q - p)))
                if cuerda < max(20.0, 0.2 * rk) or cuerda >= 2.0 * rk:
                    continue
                medio = (p + q) / 2.0
                altura = math.sqrt(rk * rk - (cuerda / 2.0) ** 2)
                normal = np.array([-(q - p)[1], (q - p)[0]]) / cuerda
                for c in (medio + altura * normal, medio - altura * normal):
                    sobre = _en_circulo_((c[0], c[1], rk), max(2.0, 0.015 * rk))
                    pt = float(nitidez[sobre].sum())
                    if pt > mejor_p and sobre.sum() >= 20 and _abarca(puntos[sobre], (c[0], c[1])) >= 20.0:
                        mejor, mejor_p = c, pt
            if mejor is not None:
                sobre = _en_circulo_((mejor[0], mejor[1], rk), max(2.0, 0.015 * rk))
                c = _ajustar_radio_fijo(puntos[sobre], mejor, rk)
                cand = _validar((c[0], c[1], rk), max(2.0, 0.015 * rk))
                if cand:
                    cand["barrido"] = True
                    salida.append(cand)
            rk *= 1.12
    if not salida:
        raise ErrorLuna(_("El borde no parece el de la Luna: ¿está desenfocada, tapada por nubes o fuera de campo?"))
    return salida


def _cielo_fuera(suave, inliers, c):
    """
    Al otro lado del limbo hay cielo: mucho más oscuro que la Luna o, de día, liso. Al otro
    lado del borde de un mar con las tierras altas (lo que confunde en un trozo del interior)
    hay más Luna: algo más oscura, pero con textura. Compara una franja por dentro y otra
    por fuera del arco; devuelve sus cocientes de brillo y de textura, o None si no es cielo.
    """
    import cv2
    cx, cy, r = c
    h, w = suave.shape
    textura = np.abs(cv2.Laplacian(suave, cv2.CV_32F, ksize=3))
    d = np.column_stack([inliers[:, 0] - cx, inliers[:, 1] - cy])
    d /= np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)[:, None]
    franja = max(4.0, 0.04 * min(r, max(h, w)))
    medidas = {}
    for lado, signo in (("dentro", -1.0), ("fuera", 1.0)):
        vals, tex = [], []
        for paso in (0.4, 0.7, 1.0):
            p = inliers + signo * paso * franja * d
            ok = (p[:, 0] >= 0) & (p[:, 0] <= w - 1) & (p[:, 1] >= 0) & (p[:, 1] <= h - 1)
            pi = p[ok].astype(int)
            vals.append(suave[pi[:, 1], pi[:, 0]])
            tex.append(textura[pi[:, 1], pi[:, 0]])
        vals, tex = np.concatenate(vals), np.concatenate(tex)
        if len(vals) < 15:
            return None
        medidas[lado] = (float(np.median(vals)), float(np.median(tex)))
    fondo = float(np.percentile(suave, 1))
    brillo = (medidas["fuera"][0] - fondo) / max(medidas["dentro"][0] - fondo, 1e-6)
    tex = medidas["fuera"][1] / max(medidas["dentro"][1], 1e-6)
    if brillo > LIMBO_BRILLO_MAX and tex > LIMBO_TEXTURA_MAX:
        return None
    return {"cielo_brillo": round(brillo, 3), "cielo_textura": round(tex, 3)}


def _polares(radios, angulos):
    rr, aa = np.meshgrid(radios, angulos, indexing="ij")
    return rr * np.cos(aa), rr * np.sin(aa)


def _vista(geo, lado=None):
    """
    La Luna que se debería ver (albedo × iluminación), con el norte arriba y el este a la
    izquierda, en una imagen de LADO_VISTA² que cubre [-1, 1]² en radios lunares (cielo a 0).
    No depende de dónde esté el disco en la foto ni de cómo esté girada: se pinta una vez por
    instante y cada candidato la toma con un giro y un escalado (_modelo, _pintar_modelo).
    """
    lado = lado or LADO_VISTA
    vistas = geo.setdefault("_vistas", {})
    if lado not in vistas:
        n = lado
        c = (np.arange(n) + 0.5) / n * 2.0 - 1.0
        X, Y = np.meshgrid(c, -c)
        dentro = X * X + Y * Y < 1.0
        img = np.zeros((n, n), np.float32)
        f = _desde_vista(geo, X[dentro], Y[dentro])
        lat = np.degrees(np.arcsin(np.clip(f[:, 2], -1, 1)))
        lon = np.degrees(np.arctan2(f[:, 1], f[:, 0]))
        fi = f @ geo["R"]
        cos_i = np.clip(fi @ geo["s"], 0.0, None)
        cos_e = np.clip(-(fi @ geo["u"]), 1e-3, None)
        img[dentro] = _albedo_en(lat, lon) * cos_i / (cos_i + cos_e)   # Lommel-Seeliger: el limbo no se oscurece
        vistas[lado] = img
    return vistas[lado]


def _modelo(geo, radios, angulos):
    """
    La Luna que se debería ver (albedo × iluminación) en la rejilla polar de la vista. Si ya
    está pintada la vista (disco cortado: muchos candidatos), se toma de ella; con el disco
    entero se orienta una sola vez y sale más barato calcular solo los puntos de la rejilla.
    """
    import cv2
    X, Y = _polares(radios, angulos)
    if geo.get("_vistas"):
        vista_img = geo["_vistas"][max(geo["_vistas"])]
        n = vista_img.shape[0]
        return cv2.remap(vista_img, ((X + 1.0) / 2.0 * n - 0.5).astype(np.float32),
                         ((1.0 - Y) / 2.0 * n - 0.5).astype(np.float32), cv2.INTER_LINEAR).astype(np.float64)
    f = _desde_vista(geo, X, Y)
    lat = np.degrees(np.arcsin(np.clip(f[..., 2], -1, 1)))
    lon = np.degrees(np.arctan2(f[..., 1], f[..., 0]))
    fi = f @ geo["R"]
    cos_i = np.clip(fi @ geo["s"], 0.0, None)
    cos_e = np.clip(-(fi @ geo["u"]), 1e-3, None)
    return _albedo_en(lat, lon) * cos_i / (cos_i + cos_e)          # Lommel-Seeliger: el limbo no se oscurece


def _foto_polar(lum, disco, radios, angulos):
    """La foto en la rejilla polar del disco y qué muestras caen dentro de la foto (máscara 0/1)."""
    import cv2
    X, Y = _polares(radios, angulos)
    xs = (disco["cx"] + disco["r"] * X).astype(np.float32)
    ys = (disco["cy"] - disco["r"] * Y).astype(np.float32)
    h, w = np.asarray(lum).shape[:2]
    valida = ((xs >= 1) & (xs <= w - 2) & (ys >= 1) & (ys <= h - 2)).astype(np.float64)
    return cv2.remap(np.asarray(lum, dtype=np.float32), xs, ys, cv2.INTER_LINEAR).astype(np.float64), valida


def _rejilla(lum, disco, rapida=False):
    """
    Radios y ángulos de la comparación: con el disco entero, los de siempre; con el disco
    cortado, solo los radios que tocan la foto y más ángulos, para que el trozo visible
    tenga muestras de sobra aunque el disco sea varias veces mayor que la foto.
    """
    h, w = np.asarray(lum).shape[:2]
    cx, cy, r = disco["cx"], disco["cy"], disco["r"]
    esquinas = [math.hypot(x - cx, y - cy) for x in (0, w) for y in (0, h)]
    cerca = math.hypot(min(max(cx, 0), w) - cx, min(max(cy, 0), h) - cy)
    r0 = max(0.04, cerca / r)
    r1 = min(0.97, max(esquinas) / r)
    if r1 <= r0 + 0.05:
        r0, r1 = 0.06, 0.96
    anillos = int(min(160, max(ANILLOS, ANILLOS * (r1 - r0) * r / 400.0)))
    angulos = ANGULOS if r < 600 else 2 * ANGULOS
    if rapida:                       # para puntuar candidatos y afinar: un cuarto de las muestras
        anillos, angulos = max(24, anillos // 2), angulos // 2
    return np.linspace(r0, r1, anillos), np.arange(angulos) * (2.0 * math.pi / angulos)


def _distintos(a, b):
    """Si dos discos son soluciones de verdad distintas (no el mismo con un poco de error)."""
    r = max(a["r"], b["r"])
    return math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"]) > 0.1 * r or abs(a["r"] - b["r"]) > 0.1 * r


def _albedo_en(lat, lon):
    """Albedo del mapa en esos puntos (grados, arrays de cualquier forma), interpolado."""
    import cv2
    mapa = _albedo()
    h, w = mapa.shape
    forma = np.shape(lat)
    mx = ((np.ravel(lon) + 180.0) / 360.0 * w - 0.5).astype(np.float32)
    my = ((90.0 - np.ravel(lat)) / 180.0 * h - 0.5).astype(np.float32)
    # remap no admite más de 32 767 columnas: se pasa en filas de 4 096
    n = len(mx)
    filas = max(1, -(-n // 4096))
    relleno = filas * 4096 - n
    mx = np.pad(mx, (0, relleno)).reshape(filas, 4096)
    my = np.pad(my, (0, relleno)).reshape(filas, 4096)
    return cv2.remap(mapa, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP).ravel()[:n].reshape(forma)


def _pintar_modelo(geo, disco, orient, ancho, alto, lado=None):
    """
    La Luna que se debería ver con ese disco y esa orientación, en una imagen de ancho×alto
    (cielo a 0): la vista de _vista() con una transformación afín de los píxeles de la foto.
    """
    import cv2
    g = math.radians(orient["giro"])
    m = -1.0 if orient["espejo"] else 1.0
    vista_img = _vista(geo, lado)
    cg, sg, r, n = math.cos(g), math.sin(g), disco["r"], vista_img.shape[0]
    # Píxel (x, y) de la foto → (Xr, Yr) = ((x − cx)/r, −(y − cy)/r) → giro y espejo →
    # (X, Y) de la vista → píxel (u, v) de la imagen de _vista(). Todo es afín
    a = np.array([[1.0 / r, 0.0, -disco["cx"] / r], [0.0, -1.0 / r, disco["cy"] / r]])        # → (Xr, Yr)
    rot = np.array([[cg, sg], [-m * sg, m * cg]])                                                # → (X, Y)
    vista = np.array([[n / 2.0, 0.0], [0.0, -n / 2.0]])                                          # → (u, v)
    lineal = vista @ rot @ a[:, :2]
    traslacion = vista @ rot @ a[:, 2] + np.array([n / 2.0 - 0.5, n / 2.0 - 0.5])
    M = np.column_stack([lineal, traslacion])
    return cv2.warpAffine(vista_img, M, (ancho, alto), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0).astype(np.float64)


def _parecido(lum_red, k, disco, orient, geo):
    """
    Correlación entre la foto entera (reducida por k) y la Luna que se debería ver con ese
    disco y esa orientación. A diferencia de orientar(), mide todos los candidatos sobre los
    mismos píxeles: un disco enorme del que la foto solo ve un trocito no sale favorecido.
    """
    alto, ancho = lum_red.shape
    d = {"cx": (disco["cx"] + 0.5) * k - 0.5, "cy": (disco["cy"] + 0.5) * k - 0.5, "r": disco["r"] * k}
    modelo = _pintar_modelo(geo, d, orient, ancho, alto)
    a = lum_red - lum_red.mean()
    b = modelo - modelo.mean()
    return float((a * b).sum() / math.sqrt(float((a * a).sum() * (b * b).sum()) or 1.0))


def _elegir_disco_cortado(lum, candidatos, geo):
    """
    Con el disco cortado: cada candidato se orienta y se puntúa comparando la foto entera con
    la Luna que se debería ver; se afinan los tres mejores (distintos entre sí) y gana el que
    mejor queda. Si otro disco claramente distinto encaja casi igual, la foto es ambigua (medio
    disco cortado por el centro con el terminador a la vista, por ejemplo) y no se anota:
    mejor sin nombres que con los nombres en otro sitio.
    """
    import cv2
    h, w = lum.shape
    k = min(1.0, 240.0 / max(h, w))
    lum_red = cv2.resize(lum, (max(8, round(w * k)), max(8, round(h * k))), interpolation=cv2.INTER_AREA).astype(np.float64)

    def _puntuar(c, rapida=True):
        # La orientación de orientar() y la del espejo contrario: decide el parecido con la foto
        # entera (con un trozo, la comparación en polares puede equivocarse de espejo)
        o = orientar(lum, c, geo, rapida=rapida)
        alt = dict(o, **o["otra"])
        return max(((_parecido(lum_red, k, c, x, geo), x) for x in (o, alt)), key=lambda px: px[0])

    puntuados = sorted(((_puntuar(c)[0], c) for c in candidatos), key=lambda x: -x[0])
    semillas = []
    for __, c in puntuados:
        if all(_distintos(c, o) for o in semillas):
            semillas.append(c)
        if len(semillas) == 3:
            break
    afinados = []
    for c in semillas:
        # Dos pasadas: con la orientación fija se afina el círculo (rápido), se reorienta y otra vez
        d = c
        for __ in range(2):
            o = _puntuar(d)[1]
            d = afinar_disco(lum, d, geo, lambda dd, o=o: _parecido(lum_red, k, dd, o, geo))
        afinados.append(_puntuar(d, rapida=False) + (d,))
    afinados.sort(key=lambda x: -x[0])
    nota, orient, disco = afinados[0]
    # Solo cuentan como rivales las soluciones que siguen siendo distintas después de afinar:
    # un círculo un 10 % mayor sobre el mismo arco es la misma solución con algo de error
    if limbo_visible(lum, disco) < LIMBO_MIN_GRADOS:
        raise ErrorLuna(_("Se ve poco borde de la Luna y la foto encaja con más de una posición: "
                          "no se puede saber con seguridad qué parte es. Con más disco a la vista sí."))
    rivales = [p for p, __, d in afinados[1:] if _distintos(d, disco)]
    log.debug("Disco cortado: %s", [(round(p, 3), round(d["cx"]), round(d["cy"]), round(d["r"])) for p, __, d in afinados])
    if rivales and max(rivales) > nota - AMBIGUO:
        raise ErrorLuna(_("Se ve poco borde de la Luna y la foto encaja con más de una posición: "
                          "no se puede saber con seguridad qué parte es. Con más disco a la vista sí."))
    return disco, orient


def _plantilla(lum, lado):
    import cv2
    h, w = lum.shape
    k = lado / max(h, w)
    return cv2.resize(lum, (max(8, round(w * k)), max(8, round(h * k))), interpolation=cv2.INTER_AREA).astype(np.float32), k


def _probar_plantilla(geo, t, R, giros, espejo):
    """
    Busca la foto reducida `t` dentro de la Luna esperada con disco de radio R (px de t), con
    cada giro de `giros` y ese espejo. Solo valen las posiciones con la foto entera dentro del
    disco (si no, se vería el limbo). Devuelve [(parecido, R, cx, cy, giro, espejo)], con el
    centro del disco en píxeles de t.
    """
    import cv2
    th, tw = t.shape
    lado = int(2 * R + 4)
    if lado < max(th, tw):
        return []
    d = {"cx": R + 2, "cy": R + 2, "r": R}
    yy, xx = np.mgrid[0:lado - th + 1, 0:lado - tw + 1]
    valida = np.ones(xx.shape, bool)
    for ex, ey in ((0, 0), (tw, 0), (0, th), (tw, th)):
        valida &= np.hypot(xx + ex - d["cx"], yy + ey - d["cy"]) < 0.98 * R
    if not valida.any():
        return []
    salida = []
    for g in giros:
        v = _pintar_modelo(geo, d, {"giro": g, "espejo": espejo}, lado, lado, LADO_VISTA_TROZO).astype(np.float32)
        cc = cv2.matchTemplate(v, t, cv2.TM_CCOEFF_NORMED)
        cc = np.where(valida & np.isfinite(cc), cc, -1.0).astype(np.float32)
        __, mx, __, pos = cv2.minMaxLoc(cc)
        salida.append((float(mx), R, d["cx"] - pos[0], d["cy"] - pos[1], float(g) % 360.0, espejo))
    return salida


def _distintas(a, b):
    """Si dos soluciones (parecido, R, cx, cy, giro, espejo) de buscar_sin_limbo son de verdad distintas."""
    R = max(a[1], b[1])
    dg = abs((a[4] - b[4] + 180.0) % 360.0 - 180.0)
    return a[5] != b[5] or dg > 15.0 or math.hypot(a[2] - b[2], a[3] - b[3]) > 0.15 * R or abs(a[1] - b[1]) > 0.15 * R


def buscar_sin_limbo(lum, geo):
    """
    Un trozo del interior de la Luna, sin limbo a la vista (con una Barlow o un recorte): se
    busca la foto dentro de la Luna que se debería ver, probando escala, giro y espejo, y la
    posición con búsqueda por plantilla. Primero en grueso (la foto a 64 px, giros de 12° y
    trece escalas, de que la foto cubra el 40 % del radio lunar a 2 radios), luego en fino
    alrededor de las tres mejores (128 px, giros de 3°) y por último un afinado continuo.
    Las zonas lisas (un mar o las tierras altas sin nada destacado) encajan casi igual en
    varios sitios: si otra solución distinta queda a menos de MARGEN_SIN_LIMBO, no se anota.
    Devuelve (disco, orientación) en píxeles de lum.
    """
    t, k = _plantilla(lum, 64)
    lado_t = max(t.shape)
    gruesos = []
    for R in np.geomspace(0.5, 2.5, 13) * lado_t:
        for espejo in (False, True):
            gruesos += _probar_plantilla(geo, t, R, np.arange(0.0, 360.0, 12.0), espejo)
    gruesos = [(p, R / k, cx / k, cy / k, g, e) for p, R, cx, cy, g, e in gruesos]
    gruesos.sort(key=lambda x: -x[0])
    semillas = []
    for x in gruesos:
        if all(_distintas(x, o) for o in semillas):
            semillas.append(x)
        if len(semillas) == 3:
            break
    t2, k2 = _plantilla(lum, 128)
    finos = []
    for __, R, __, __, g, e in semillas:
        mejor = None
        for f in (0.9, 0.95, 1.0, 1.05, 1.1):
            for x in _probar_plantilla(geo, t2, R * f * k2, np.arange(g - 9.0, g + 9.1, 3.0), e):
                if mejor is None or x[0] > mejor[0]:
                    mejor = x
        if mejor:
            p, R2, cx2, cy2, g2, e2 = mejor
            finos.append((p, R2 / k2, cx2 / k2, cy2 / k2, g2, e2))
    if not finos:
        raise ErrorLuna(_("No se reconoce qué parte de la Luna es."))
    finos.sort(key=lambda x: -x[0])
    mejor = finos[0]
    rival = max([x[0] for x in finos[1:] if _distintas(x, mejor)] + [x[0] for x in gruesos if _distintas(x, mejor)][:1] + [-1.0])
    log.debug("Sin limbo: mejor %.3f, rival %.3f", mejor[0], rival)
    if mejor[0] < PARECIDO_MIN_SIN_LIMBO or mejor[0] - rival < MARGEN_SIN_LIMBO:
        raise ErrorLuna(_("No se ve el borde de la Luna y este trozo encaja en más de un sitio (una zona lisa, "
                          "sin nada destacado): no se puede saber qué parte es."))
    # Afinado continuo de centro, radio y giro con el parecido de la foto entera
    import cv2
    from scipy.optimize import minimize
    h, w = lum.shape
    kr = min(1.0, 240.0 / max(h, w))
    lum_red = cv2.resize(lum, (max(8, round(w * kr)), max(8, round(h * kr))), interpolation=cv2.INTER_AREA).astype(np.float64)
    __, R, cx, cy, g, espejo = mejor
    x0 = np.array([cx, cy, R, g])

    def _coste(p):
        return -_parecido(lum_red, kr, {"cx": p[0], "cy": p[1], "r": p[2]}, {"giro": p[3], "espejo": espejo}, geo)

    paso = 0.02 * R
    simplex = np.array([x0, x0 + [paso, 0, 0, 0], x0 + [0, paso, 0, 0], x0 + [0, 0, paso, 0], x0 + [0, 0, 0, 2.0]])
    res = minimize(_coste, x0, method="Nelder-Mead", options={"initial_simplex": simplex, "maxfev": 80})
    cx, cy, R, g = (float(v) for v in res.x)
    disco = {"cx": cx, "cy": cy, "r": R, "parcial": True, "sin_limbo": True}
    orient = {"giro": round(g % 360.0, 2), "espejo": espejo, "correlacion": round(-float(res.fun), 4),
              "margen": round(mejor[0] - rival, 4)}
    return disco, orient


def afinar_disco(lum, disco, geo, puntuar, margen=0.12):
    """
    Con el disco cortado, el arco visible del limbo deja el radio y el centro con un error de
    unos %: aquí se afinan buscando (Nelder-Mead, hasta ±margen del radio) el círculo que
    maximiza puntuar(disco). Devuelve el disco afinado.
    """
    from scipy.optimize import minimize
    r0 = disco["r"]
    x0 = np.array([disco["cx"], disco["cy"], r0])

    def _coste(p):
        if np.any(np.abs(p - x0) > margen * r0):
            return 1.0
        return -puntuar({"cx": p[0], "cy": p[1], "r": p[2]})

    simplex = np.array([x0, x0 + [0.02 * r0, 0, 0], x0 + [0, 0.02 * r0, 0], x0 + [0, 0, 0.02 * r0]])
    res = minimize(_coste, x0, method="Nelder-Mead",
                   options={"initial_simplex": simplex, "xatol": 0.002 * r0, "fatol": 1e-4, "maxfev": 45})
    return dict(disco, cx=float(res.x[0]), cy=float(res.x[1]), r=float(res.x[2]))


def orientar(lum, disco, geo, rapida=False):
    """
    Giro (grados, hacia la izquierda) y espejo de la foto respecto a la vista del cielo. Con
    giro g y sin espejo, el punto de la vista a un ángulo ψ cae en la foto a g + ψ; con
    espejo, a g − ψ. Devuelve dict giro, espejo, correlacion y margen (sobre la mejor
    solución con el espejo contrario: poco margen, espejo dudoso).
    """
    radios, angulos = _rejilla(lum, disco, rapida)
    n = len(angulos)
    foto, valida = _foto_polar(lum, disco, radios, angulos)
    modelo = _modelo(geo, radios, angulos)
    # Cada anillo pesa lo que su área; las muestras fuera de la foto no cuentan. Correlación
    # normalizada con máscara: el modelo se normaliza con la parte que cae sobre la foto en
    # cada giro (con el disco entero, la máscara es todo unos y queda la correlación de siempre)
    W = valida * radios[:, None]
    media = float((foto * W).sum() / max(W.sum(), 1e-9))
    P = (foto - media) * W
    P /= math.sqrt(float((P * P / np.maximum(W, 1e-9) * (W > 0)).sum()) or 1.0)
    modelo = modelo - float((modelo * radios[:, None]).sum() / (radios.sum() * n))
    FP, FW = np.fft.rfft(P, axis=1), np.fft.rfft(W, axis=1)
    resultados = []
    for espejo in (False, True):
        m = modelo[:, (-np.arange(n)) % n] if espejo else modelo
        num = np.fft.irfft(FP * np.conj(np.fft.rfft(m, axis=1)), n=n, axis=1).sum(axis=0)
        den = np.fft.irfft(FW * np.conj(np.fft.rfft(m * m, axis=1)), n=n, axis=1).sum(axis=0)
        corr = num / np.sqrt(np.maximum(den, 1e-12))
        k = int(np.argmax(corr))
        a, b, c = corr[(k - 1) % n], corr[k], corr[(k + 1) % n]
        fino = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        resultados.append({"giro": ((k + fino) * 360.0 / n) % 360.0, "espejo": espejo, "correlacion": float(b)})
    resultados.sort(key=lambda r: -r["correlacion"])
    mejor = dict(resultados[0])
    mejor["margen"] = round(mejor["correlacion"] - resultados[1]["correlacion"], 4)
    mejor["correlacion"] = round(mejor["correlacion"], 4)
    mejor["giro"] = round(mejor["giro"], 2)
    mejor["otra"] = {"giro": round(resultados[1]["giro"], 2), "espejo": resultados[1]["espejo"]}   # la del espejo contrario
    return mejor


# ─────────────────────────────────────────────────────────────────────────────
#  Los accidentes en píxeles de la foto
# ─────────────────────────────────────────────────────────────────────────────
def _a_foto(X, Y, disco, orient):
    g = math.radians(orient["giro"])
    m = -1.0 if orient["espejo"] else 1.0
    Xr = math.cos(g) * X - math.sin(g) * m * Y
    Yr = math.sin(g) * X + math.cos(g) * m * Y
    return disco["cx"] + disco["r"] * Xr, disco["cy"] - disco["r"] * Yr


def accidentes(geo, disco, orient, ancho, alto):
    """
    Los accidentes con nombre que se ven en la foto, en sus píxeles: nombre, familia, x, y,
    semiejes a y b de la elipse (b en la dirección del centro del disco, aplastado por la
    perspectiva), angulo en grados (el de a, como en SVG) y si les da el Sol. Primero los
    que más importan, que es el orden en que se rotulan.
    """
    lista = _accidentes()
    lat = np.array([a["lat"] for a in lista])
    lon = np.array([a["lon"] for a in lista])
    X, Y, cos_e, cos_i = a_vista(geo, _vectores_superficie(lat, lon))
    x, y = _a_foto(X, Y, disco, orient)
    # Un paso pequeño hacia el centro del disco da la dirección del eje corto de la elipse
    xc, yc = _a_foto(X * 0.99, Y * 0.99, disco, orient)
    salida = []
    for i, a in enumerate(lista):
        if cos_e[i] <= 0.03 or not (0 <= x[i] < ancho and 0 <= y[i] < alto):
            continue
        radio_px = a["diametro"] / 2.0 / RADIO_KM * disco["r"]
        familia = a["familia"]
        if familia != "estacion" and 2.0 * radio_px < MIN_DIAMETRO_PX * (1.0 if familia == "crater" else 2.0):
            continue
        # En la parte de noche no se ve nada pequeño: solo los mares y los cráteres grandes
        if cos_i[i] <= 0 and not (familia in ("mar", "estacion") or (familia == "crater" and a["diametro"] >= MIN_CRATER_NOCHE_KM)):
            continue
        dx, dy = xc[i] - x[i], yc[i] - y[i]
        angulo = math.degrees(math.atan2(dy, dx)) + 90.0 if math.hypot(dx, dy) > 1e-9 else 0.0
        importancia = 1e9 if familia == "estacion" else a["diametro"] * FAMILIAS[a["codigo"]][1]
        salida.append({"nombre": a["nombre"], "familia": familia, "x": round(float(x[i]), 1), "y": round(float(y[i]), 1),
                       "a": round(radio_px, 1), "b": round(radio_px * float(cos_e[i]), 1), "angulo": round(angulo, 1),
                       "luz": bool(cos_i[i] > 0), "importancia": importancia})
    salida.sort(key=lambda s: -s["importancia"])
    for s in salida:
        del s["importancia"]
    return salida[:MAX_ACCIDENTES]


def _contornos_mares():
    """Anillos de los límites de los mares (arrays N×2 de lat, lon en grados), de luna/contornos_mares.json."""
    if "mares" not in _cache:
        try:
            with open(RUTA_MARES, encoding="utf-8") as f:
                _cache["mares"] = [np.asarray(a, dtype=np.float64) for a in json.load(f)]
        except (OSError, ValueError) as e:
            log.warning("Sin contornos de los mares: %s", e)
            _cache["mares"] = []
    return _cache["mares"]


def contornos(geo, disco, orient, ancho, alto):
    """
    Los límites de los mares en píxeles de la foto, como trazado SVG («M x y l dx dy...»,
    con movimientos relativos en píxeles enteros), cortados donde pasan a la cara oculta y
    simplificados a un píxel de la pantalla: unas decenas de KB aunque el JPG sea de
    4 000 px, menos que el propio JPG que se está viendo. La tolerancia
    va con el disco: el visor enseña la Luna entera a unos 400 px de radio como mucho, así
    que r/400 px del JPG es un píxel de pantalla. Los anillos más pequeños que eso, fuera.
    """
    import cv2
    tolerancia = max(0.8, disco["r"] / 400.0)
    trazos = []
    for anillo in _contornos_mares():
        X, Y, cos_e, __ = a_vista(geo, _vectores_superficie(anillo[:, 0], anillo[:, 1]))
        x, y = _a_foto(X, Y, disco, orient)
        visible = (cos_e > 0) & (x > -50) & (x < ancho + 50) & (y > -50) & (y < alto + 50)
        if not visible.any():
            continue
        if visible.all():                         # anillo entero: se cierra
            tramos = [np.append(np.arange(len(x)), 0)]
        else:                                     # trozos visibles, empezando tras uno oculto
            orden = np.roll(np.arange(len(x)), -int(np.argmin(visible)))
            tramos, actual = [], []
            for k in orden:
                if visible[k]:
                    actual.append(k)
                elif actual:
                    tramos.append(np.array(actual))
                    actual = []
            if actual:
                tramos.append(np.array(actual))
        for t in tramos:
            if len(t) < 2:
                continue
            linea = np.column_stack([x[t], y[t]]).astype(np.float32).reshape(-1, 1, 2)
            simple = cv2.approxPolyDP(linea, tolerancia, False).reshape(-1, 2)
            if len(simple) < 2 or np.ptp(simple, axis=0).max() < 6 * tolerancia:
                continue
            p = np.rint(simple).astype(int)
            d = np.diff(p, axis=0)
            d = d[(d != 0).any(axis=1)]
            if not len(d):                        # un «l» sin números estropearía el trazado entero
                continue
            trazos.append(f"M{p[0, 0]} {p[0, 1]}l" + " ".join(f"{a} {b}" for a, b in d))
    return "".join(trazos)


def terminador(geo, disco, orient, puntos=181):
    """Línea entre el día y la noche en la cara visible, en píxeles de la foto (lista de [x, y])."""
    s = geo["s"]
    a = np.cross(s, geo["u"])
    if np.linalg.norm(a) < 1e-6:                   # Luna llena o nueva: no se ve terminador
        return []
    a /= np.linalg.norm(a)
    b = np.cross(s, a)
    t = np.linspace(0.0, 2.0 * math.pi, 2 * puntos)
    f = np.cos(t)[:, None] * a + np.sin(t)[:, None] * b          # en ICRS
    visible = -(f @ geo["u"]) > 0
    X, Y = -(f @ geo["e"]), f @ geo["n"]
    x, y = _a_foto(X, Y, disco, orient)
    # El arco visible es uno solo: se empieza justo después de un punto oculto
    if visible.all() or not visible.any():
        return []
    inicio = int(np.argmin(visible)) if visible[0] else 0
    orden = np.roll(np.arange(len(t)), -inicio)
    return [[round(float(x[k]), 1), round(float(y[k]), 1)] for k in orden if visible[k]]


def _seguir(pequena, geo, previo):
    """
    Con un trozo (disco cortado o sin limbo), la solución de la imagen anterior afinada sobre
    esta (en vivo la Luna apenas se mueve entre una y otra): menos de medio segundo frente a los
    varios de la búsqueda completa. (disco, orientación) o None si ya no encaja tan bien como
    antes (se movió mucho, o es otra foto) y hay que buscar de nuevo.
    """
    import cv2
    interno = (previo or {}).get("_interno")
    if not interno or interno["metodo"] == "disco" or interno["forma"] != pequena.shape:
        return None
    h, w = pequena.shape
    kr = min(1.0, 240.0 / max(h, w))
    lum_red = cv2.resize(pequena, (max(8, round(w * kr)), max(8, round(h * kr))), interpolation=cv2.INTER_AREA).astype(np.float64)
    orient = interno["orientacion"]
    disco = afinar_disco(pequena, interno["disco"], geo, lambda d: _parecido(lum_red, kr, d, orient, geo), margen=0.08)
    nota = _parecido(lum_red, kr, disco, orient, geo)
    if nota < interno["parecido"] - SEGUIR_TOLERANCIA:
        return None
    return disco, dict(orient, correlacion=round(nota, 4))


def anotar(lum_o_bgr, instante, lat, lon, alt_m=0.0, previo=None):
    """
    Todo de una vez para una imagen (luminancia o BGR uint8/float) de la Luna: disco,
    orientación, accidentes y terminador en píxeles de esa imagen. `previo`: el resultado de
    anotar() con la imagen anterior del mismo tamaño (modo en vivo), para seguir un trozo en
    vez de buscarlo otra vez.
    """
    import cv2
    img = np.asarray(lum_o_bgr)
    lum = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    lum = lum.astype(np.float32)
    alto, ancho = lum.shape
    paso = max(1.0, max(alto, ancho) / ANCHO_ANALISIS)
    pequena = cv2.resize(lum, (int(round(ancho / paso)), int(round(alto / paso))), interpolation=cv2.INTER_AREA) if paso > 1 else lum
    geo = geometria(instante, lat, lon, alt_m)
    # Con el disco entero basta el primer círculo; con el disco cortado se prueban todos y gana
    # el que mejor encaja con la Luna que se debería ver; sin limbo reconocible puede ser un
    # trozo del interior (con una Barlow o un recorte), que se busca en la Luna esperada
    seguido = _seguir(pequena, geo, previo)
    if seguido:
        (disco_p, orient), metodo = seguido, previo["_interno"]["metodo"]
    else:
        try:
            candidatos = candidatos_disco(pequena)
        except SinLuna:
            raise                   # sin nada brillante no hay trozo que buscar
        except ErrorLuna as sin_borde:
            log.debug("Sin limbo reconocible (%s): se busca como trozo del interior", sin_borde)
            disco_p, orient = buscar_sin_limbo(pequena, geo)
            metodo = "sin limbo"
        else:
            if not candidatos[0]["parcial"]:
                disco_p, orient = candidatos[0], orientar(pequena, candidatos[0], geo)
                metodo = "disco"
            else:
                try:
                    disco_p, orient = _elegir_disco_cortado(pequena, candidatos, geo)
                    metodo = "disco cortado"
                except ErrorLuna as ambiguo:
                    # Lo que parecía un arco del limbo puede ser el borde de un mar en un trozo del
                    # interior (sin cielo, el mar es lo más oscuro): se busca también así, y si
                    # tampoco sale, el aviso es el del disco cortado
                    try:
                        disco_p, orient = buscar_sin_limbo(pequena, geo)
                        metodo = "sin limbo"
                    except ErrorLuna:
                        raise ambiguo
    interno = {"metodo": metodo, "forma": pequena.shape, "disco": disco_p, "orientacion": orient}
    if metodo != "disco":           # para seguir el trozo en la imagen siguiente (_seguir)
        h, w = pequena.shape
        kr = min(1.0, 240.0 / max(h, w))
        lum_red = cv2.resize(pequena, (max(8, round(w * kr)), max(8, round(h * kr))), interpolation=cv2.INTER_AREA).astype(np.float64)
        interno["parecido"] = _parecido(lum_red, kr, disco_p, orient, geo)
    k = ancho / float(pequena.shape[1])
    disco = {"cx": (disco_p["cx"] + 0.5) * k - 0.5, "cy": (disco_p["cy"] + 0.5) * k - 0.5, "r": disco_p["r"] * k}
    return {"_interno": interno, "seguido": bool(seguido), "ancho": ancho, "alto": alto, "disco": {c: round(v, 1) for c, v in disco.items()},
            "orientacion": orient, "accidentes": accidentes(geo, disco, orient, ancho, alto),
            "terminador": terminador(geo, disco, orient), "mares": contornos(geo, disco, orient, ancho, alto),
            "iluminada": round(geo["iluminada"], 3), "libracion": [round(geo["libracion_lat"], 2), round(geo["libracion_lon"], 2)],
            "pa_eje": round(geo["pa_eje"], 2), "metodo": metodo}


# ─────────────────────────────────────────────────────────────────────────────
#  Fotos de la galería
# ─────────────────────────────────────────────────────────────────────────────
def meta_de(ruta_jpg):
    """El JSON hermano del JPG de la galería (vacío si no tiene)."""
    try:
        with open(os.path.splitext(ruta_jpg)[0] + ".json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def es_luna(nombre, meta=None):
    """
    Si la foto de la galería es de la Luna: por el objeto de su JSON («Luna», «Luna llena»...)
    o, si no tiene, por su nombre.
    """
    objeto = str((meta or {}).get("objeto") or "").strip().lower()
    if objeto:
        return objeto.split()[0] in NOMBRES_LUNA
    base = os.path.splitext(os.path.basename(nombre))[0]
    return base.split("_")[0].lower() in NOMBRES_LUNA


def instante_de(nombre, meta=None):
    """Instante unix de la foto: el del JSON o, si no, la fecha y hora (locales) del nombre."""
    meta = meta or {}
    if meta.get("instante"):
        return float(meta["instante"])
    m = re.search(r"_(\d{8})_(\d{6})", os.path.basename(nombre))
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
    return None


def _para_el_visor(r, etiqueta, t0):
    """Resultado de anotar() en el formato que pintan la galería y Cámara (static/js/luna_anotada.js)."""
    orient = r["orientacion"]
    log.info("Luna anotada en %s (%s): %d accidentes, giro %.1f°%s, correlación %.3f (margen %.3f), %.1f s",
             etiqueta, r["metodo"], len(r["accidentes"]), orient["giro"], " con espejo" if orient["espejo"] else "",
             orient["correlacion"], orient["margen"], time.time() - t0)
    return {"ok": True, "luna": True, "ancho": r["ancho"], "alto": r["alto"], "objetos": r["accidentes"],
            "disco": r["disco"], "terminador": r["terminador"], "mares": r["mares"], "iluminada": r["iluminada"],
            "orientacion": orient, "dudosa": orient["margen"] < 0.02}


def _log_fallo(etiqueta, error, t0, repetible=False):
    """
    Deja en el log por qué no se pudo anotar y cuánto se tardó en saberlo. Con `repetible` (la
    foto de Cámara, que en vivo se pide cada pocos segundos) el mismo motivo solo se apunta
    una vez por minuto: con la Luna fuera del encuadre no se llena el log.
    """
    texto = str(error)
    if repetible:
        ultimo = _cache.get("ultimo_fallo_camara")
        if ultimo and ultimo[0] == texto and time.time() - ultimo[1] < 60.0:
            return
        _cache["ultimo_fallo_camara"] = (texto, time.time())
    log.info("Luna sin anotar en %s: %s (%.1f s)", etiqueta, texto, time.time() - t0)


def _guardar_en_cache(clave, salida):
    with _lock:
        if len(_cache) > 40:
            for k in [k for k in _cache if isinstance(k, tuple)][:20]:
                del _cache[k]
        _cache[clave] = salida


def objetos_foto(nombre, observatorio=None):
    """
    Accidentes de la Luna sobre una foto de la galería, en el formato de las anotaciones del
    visor (galeria_anotada.objetos_foto) más el disco y el terminador. observatorio = (lat, lon)
    por si la foto no lo lleva en su JSON.
    """
    import cv2
    from editor_fotos import ErrorEditor, rutas
    __, ruta_jpg, __, __ = rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditor(_("Imagen no encontrada"))
    meta = meta_de(ruta_jpg)
    if not es_luna(nombre, meta):
        raise ErrorEditor(_("La foto no es de la Luna"))
    instante = instante_de(nombre, meta)
    if instante is None:
        raise ErrorEditor(_("No se sabe cuándo se hizo la foto: hace falta para saber cómo se veía la Luna."))
    lat, lon = meta.get("latitud"), meta.get("longitud")
    if lat is None or lon is None:
        lat, lon = observatorio or (None, None)
    if lat is None or lon is None:
        raise ErrorEditor(_("No se sabe desde dónde se hizo la foto: falta el observatorio en Configuración."))
    est = os.stat(ruta_jpg)
    clave = (nombre, est.st_size, est.st_mtime_ns, float(lat), float(lon))
    with _lock:
        if clave in _cache:
            return _cache[clave]
    img = cv2.imread(ruta_jpg, cv2.IMREAD_COLOR)
    if img is None:
        raise ErrorEditor(_("No se pudo leer la imagen"))
    t0 = time.time()
    try:
        salida = _para_el_visor(anotar(img, instante, float(lat), float(lon)), nombre, t0)
    except ErrorLuna as e:
        _log_fallo(nombre, e, t0)
        raise ErrorEditor(str(e))
    _guardar_en_cache(clave, salida)
    return salida


def objetos_camara(jpg, clave, instante, observatorio):
    """
    Accidentes de la Luna sobre la foto que enseña Cámara (`jpg`: los bytes del JPG que ve
    la web, ya con su recorte y su giro), en el mismo formato que la galería. `clave`
    identifica esa foto (la versión del fotograma del vivo o la fecha del JPG): pedirla otra
    vez no repite el cálculo. Lanza ErrorLuna si no se ve la Luna.
    """
    import cv2
    lat, lon = observatorio
    clave = ("camara", clave, float(lat), float(lon))
    with _lock:
        if clave in _cache:
            return _cache[clave]
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ErrorLuna(_("No se pudo leer la imagen"))
    t0 = time.time()
    # En vivo, el resultado del fotograma anterior: con un trozo de la Luna se sigue desde él
    previo = _cache.get("vivo_previo") if clave[1][0] == "vivo" else None
    try:
        r = anotar(img, instante, float(lat), float(lon), previo=previo)
    except ErrorLuna as e:
        _cache.pop("vivo_previo", None)        # lo anterior ya no sirve para seguir
        _log_fallo("Cámara" + (" (en vivo)" if clave[1][0] == "vivo" else ""), e, t0, repetible=True)
        raise
    if clave[1][0] == "vivo":
        _cache["vivo_previo"] = r
    salida = _para_el_visor(r, "Cámara" + (" (seguido)" if r["seguido"] else ""), t0)
    _guardar_en_cache(clave, salida)
    return salida
