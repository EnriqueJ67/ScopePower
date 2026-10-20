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
#      círculo, así que gana el limbo.
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


def localizar_disco(lum, semilla=0):
    """
    Centro y radio del disco de la Luna en `lum` (luminancia float) sin saber su tamaño:
    círculo de tres puntos del borde de la zona brillante con más puntos del borde encima.
    Devuelve dict cx, cy, r, ajuste (fracción del borde en el círculo).
    """
    import cv2
    lum = np.asarray(lum, dtype=np.float32)
    h, w = lum.shape
    suave = cv2.GaussianBlur(lum, (0, 0), 1.2)
    bajo, alto = float(np.percentile(suave, 5)), float(np.percentile(suave, 99.5))
    if alto - bajo < 0.02 * max(alto, 1.0):
        raise ErrorLuna(_("No se ve la Luna en la foto: la imagen es casi uniforme."))
    mascara = (suave > bajo + 0.3 * (alto - bajo)).astype(np.uint8)
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, etiquetas, stats, __ = cv2.connectedComponentsWithStats(mascara, 8)
    if n <= 1:
        raise ErrorLuna(_("No se ve la Luna en la foto."))
    mayor = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    comp = (etiquetas == mayor).astype(np.uint8)
    contornos, __ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    puntos = max(contornos, key=len).reshape(-1, 2).astype(np.float64)
    dentro = (puntos[:, 0] > 1) & (puntos[:, 0] < w - 2) & (puntos[:, 1] > 1) & (puntos[:, 1] < h - 2)
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
    grad /= np.maximum(np.hypot(grad[:, 0], grad[:, 1]), 1e-6)[:, None]

    def _en_circulo(c, tol):
        d = np.column_stack([c[0] - puntos[:, 0], c[1] - puntos[:, 1]])
        dist = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
        hacia_dentro = (grad * d).sum(axis=1) / dist > 0.6
        return (np.abs(dist - c[2]) < tol) & hacia_dentro

    rng = np.random.default_rng(semilla)
    r_min, r_max = 0.03 * max(w, h), 0.75 * max(w, h)
    mejor, mejor_n = None, 0
    for __ in range(600):
        i, j, k = rng.choice(len(puntos), 3, replace=False)
        c = _circulo_3_puntos(puntos[i], puntos[j], puntos[k])
        if c is None or not (r_min < c[2] < r_max):
            continue
        n_in = int(np.sum(_en_circulo(c, max(1.5, 0.012 * c[2]))))
        if n_in > mejor_n:
            mejor, mejor_n = c, n_in
    if mejor is None:
        raise ErrorLuna(_("No se reconoce el borde de la Luna."))
    # Afinado con la tolerancia cada vez más estrecha: casi en Luna llena el terminador
    # pasa muy cerca del limbo y con la tolerancia ancha tiraría del círculo
    for fraccion in (0.012, 0.008, 0.005):
        tol = max(1.0, fraccion * mejor[2])
        inliers = puntos[_en_circulo(mejor, tol)]
        if len(inliers) < 20:
            break
        mejor = _ajustar_circulo(inliers, *mejor)
    cx, cy, r = (float(v) for v in mejor)
    # El limbo iluminado es media circunferencia en cualquier fase (de lo que cae dentro de
    # la foto, si el disco se sale): bastante menos, no es la Luna
    t = np.linspace(0.0, 2.0 * math.pi, 720, endpoint=False)
    xs, ys = cx + r * np.cos(t), cy + r * np.sin(t)
    dentro_img = float(np.mean((xs > 1) & (xs < w - 2) & (ys > 1) & (ys < h - 2)))
    arco = len(inliers) / (2.0 * math.pi * r * max(dentro_img, 1e-3))
    if arco < 0.3 or dentro_img < 0.25:
        raise ErrorLuna(_("El borde no parece el de la Luna: ¿está desenfocada, tapada por nubes o fuera de campo?"))
    return {"cx": cx, "cy": cy, "r": r, "arco": round(arco, 3)}


def _polares(radios, angulos):
    rr, aa = np.meshgrid(radios, angulos, indexing="ij")
    return rr * np.cos(aa), rr * np.sin(aa)


def _modelo(geo, radios, angulos):
    """La Luna que se debería ver (albedo × iluminación) en la rejilla polar de la vista."""
    import cv2
    X, Y = _polares(radios, angulos)
    f = _desde_vista(geo, X, Y)
    lat = np.degrees(np.arcsin(np.clip(f[..., 2], -1, 1)))
    lon = np.degrees(np.arctan2(f[..., 1], f[..., 0]))
    mapa = _albedo()
    h, w = mapa.shape
    albedo = cv2.remap(mapa, ((lon + 180.0) / 360.0 * w - 0.5).astype(np.float32),
                       ((90.0 - lat) / 180.0 * h - 0.5).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    fi = f @ geo["R"]
    cos_i = np.clip(fi @ geo["s"], 0.0, None)
    cos_e = np.clip(-(fi @ geo["u"]), 1e-3, None)
    return albedo * cos_i / (cos_i + cos_e)          # Lommel-Seeliger: el limbo no se oscurece


def _foto_polar(lum, disco, radios, angulos):
    import cv2
    X, Y = _polares(radios, angulos)
    return cv2.remap(np.asarray(lum, dtype=np.float32), (disco["cx"] + disco["r"] * X).astype(np.float32),
                     (disco["cy"] - disco["r"] * Y).astype(np.float32), cv2.INTER_LINEAR)


def orientar(lum, disco, geo):
    """
    Giro (grados, hacia la izquierda) y espejo de la foto respecto a la vista del cielo. Con
    giro g y sin espejo, el punto de la vista a un ángulo ψ cae en la foto a g + ψ; con
    espejo, a g − ψ. Devuelve dict giro, espejo, correlacion y margen (sobre la mejor
    solución con el espejo contrario: poco margen, espejo dudoso).
    """
    radios = np.linspace(0.06, 0.96, ANILLOS)
    angulos = np.arange(ANGULOS) * (2.0 * math.pi / ANGULOS)
    foto = _foto_polar(lum, disco, radios, angulos)
    modelo = _modelo(geo, radios, angulos)
    peso = radios[:, None]                                 # cada anillo pesa lo que su área

    def _normalizar(a):
        a = a - (a * peso).sum() / (peso.sum() * a.shape[1])
        return a / math.sqrt(float((a * a * peso).sum()) or 1.0)

    foto, modelo = _normalizar(foto), _normalizar(modelo)
    F = np.fft.rfft(foto * peso, axis=1)
    resultados = []
    for espejo in (False, True):
        m = modelo[:, (-np.arange(ANGULOS)) % ANGULOS] if espejo else modelo
        corr = np.fft.irfft(F * np.conj(np.fft.rfft(m, axis=1)), n=ANGULOS, axis=1).sum(axis=0)
        k = int(np.argmax(corr))
        a, b, c = corr[(k - 1) % ANGULOS], corr[k], corr[(k + 1) % ANGULOS]
        fino = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        resultados.append({"giro": ((k + fino) * 360.0 / ANGULOS) % 360.0, "espejo": espejo, "correlacion": float(b)})
    resultados.sort(key=lambda r: -r["correlacion"])
    mejor = dict(resultados[0])
    mejor["margen"] = round(mejor["correlacion"] - resultados[1]["correlacion"], 4)
    mejor["correlacion"] = round(mejor["correlacion"], 4)
    mejor["giro"] = round(mejor["giro"], 2)
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


def anotar(lum_o_bgr, instante, lat, lon, alt_m=0.0):
    """
    Todo de una vez para una imagen (luminancia o BGR uint8/float) de la Luna: disco,
    orientación, accidentes y terminador en píxeles de esa imagen.
    """
    import cv2
    img = np.asarray(lum_o_bgr)
    lum = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    lum = lum.astype(np.float32)
    alto, ancho = lum.shape
    paso = max(1.0, max(alto, ancho) / ANCHO_ANALISIS)
    pequena = cv2.resize(lum, (int(round(ancho / paso)), int(round(alto / paso))), interpolation=cv2.INTER_AREA) if paso > 1 else lum
    geo = geometria(instante, lat, lon, alt_m)
    disco_p = localizar_disco(pequena)
    k = ancho / float(pequena.shape[1])
    disco = {"cx": (disco_p["cx"] + 0.5) * k - 0.5, "cy": (disco_p["cy"] + 0.5) * k - 0.5, "r": disco_p["r"] * k}
    orient = orientar(pequena, disco_p, geo)
    return {"ancho": ancho, "alto": alto, "disco": {c: round(v, 1) for c, v in disco.items()},
            "orientacion": orient, "accidentes": accidentes(geo, disco, orient, ancho, alto),
            "terminador": terminador(geo, disco, orient), "mares": contornos(geo, disco, orient, ancho, alto),
            "iluminada": round(geo["iluminada"], 3), "libracion": [round(geo["libracion_lat"], 2), round(geo["libracion_lon"], 2)],
            "pa_eje": round(geo["pa_eje"], 2)}


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
    log.info("Luna anotada en %s: %d accidentes, giro %.1f°%s, correlación %.3f (margen %.3f), %.1f s",
             etiqueta, len(r["accidentes"]), orient["giro"], " con espejo" if orient["espejo"] else "",
             orient["correlacion"], orient["margen"], time.time() - t0)
    return {"ok": True, "luna": True, "ancho": r["ancho"], "alto": r["alto"], "objetos": r["accidentes"],
            "disco": r["disco"], "terminador": r["terminador"], "mares": r["mares"], "iluminada": r["iluminada"],
            "orientacion": orient, "dudosa": orient["margen"] < 0.02}


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
    salida = _para_el_visor(anotar(img, instante, float(lat), float(lon)), "Cámara", t0)
    _guardar_en_cache(clave, salida)
    return salida
