# manchas_solares.py
#
# El Número de Wolf de una foto del Sol de la galería: R = k · (10·g + f), con g grupos de
# manchas y f manchas sueltas (umbras), el índice clásico del observador solar desde 1849.
# Es la capa «Anotaciones» del visor cuando la foto es del Sol (como luna_anotada.py lo es
# de la Luna): las manchas y los grupos se pintan encima de la foto y el conteo se corrige
# a mano tocando (separar grupos tiene parte de criterio) antes de guardarlo.
#
#   1. La imagen: el crudo lineal de la foto (editor_dia.cargar_lineal, sin curva de tonos)
#      cuando se sabe con qué giro de Cámara se hizo su JPG (meta["giro"]: así las manchas se
#      pintan donde toca); si no, el JPG aproximado a lineal, que medido en las fotos reales
#      vale casi igual. El disco se localiza con enfoque.circulo_disco (radio libre: el JPG
#      puede estar recortado o con zoom).
#   2. El oscurecimiento del limbo se aplana: el perfil radial (la mediana del brillo por
#      anillos de μ = cos ρ) y se divide por él; y lo que queda (gradientes de calima o viñeteo,
#      de varios % en las fotos reales) se quita dividiendo por la fotosfera local, un cierre
#      morfológico más grande que cualquier mancha. Fuera del 95 % del radio no se cuenta.
#   3. Mancha = por debajo de un umbral relativo al ruido medido de la fotosfera (según la
#      sensibilidad); umbra = por debajo de otro más bajo. Componentes conexas: f = núcleos de
#      umbra; una penumbra sin umbra distinguible (un poro) cuenta como una mancha. Las motas
#      de 3 px (polvo, píxeles calientes) y lo pegado al limbo se descartan.
#   4. Los grupos: manchas encadenadas a menos de una separación angular sobre la esfera. Esa
#      separación sale de la posición en el disco sin saber dónde está el norte (la geometría
#      de la esfera no depende de la orientación), así que g, f y R salen siempre. Con la
#      orientación de la cámara conocida (la del último plate solving) se dan además las
#      coordenadas heliográficas de cada mancha y de cada grupo.
#   5. P, B0 y L0 (el ángulo del eje, la latitud del centro del disco y la longitud del
#      meridiano central) con las fórmulas de Meeus (Astronomical Algorithms, cap. 29) sobre
#      la longitud aparente del Sol de astropy. Sin Internet (datos_iers.py).
#
# Límites: con 50 mm de abertura y 3″/px se ven umbras y penumbras medianas, no los poros
# pequeños; f sale por debajo del de SILSO y para eso está el factor k ([Sol] factor_k), que
# cada observador calibra comparando con el índice diario de SILSO. El conteo guardado va al
# JSON de la foto (meta["wolf"]) y a la serie datos/numero_wolf.csv.

import os
import csv
import math
import time
import logging
import threading
import configparser
from datetime import datetime, timezone

import numpy as np

from idioma import _

log = logging.getLogger("scopepower.manchas_solares")

RUTA_SERIE = os.path.join("datos", "numero_wolf.csv")
COLUMNAS_SERIE = ("fecha", "foto", "g", "f", "R", "k", "area_mhs", "a_mano")
NOMBRES_SOL = {"sol", "sun"}
ANCHO_ANALISIS = 2000          # la imagen se analiza como mucho a este lado mayor
RADIO_UTIL = 0.95              # fracción del radio dentro de la cual se mide (más allá, el aplanado del limbo engaña)
CENTRO_MAX = 0.93              # una mancha con el centro más allá de esto es casi seguro el limbo mal aplanado
RADIO_MIN_PX = 40.0            # un disco más pequeño no tiene manchas que contar
ANILLOS_PERFIL = 48            # anillos de μ para el perfil del oscurecimiento del limbo
SIGMA_SUAVIZADO = 1.0          # desenfoque (px del análisis) antes de umbralizar
AREA_MIN_PENUMBRA = 4          # px del análisis, como poco; crece con el radio (ver detectar)
RADIO_MIN_MANCHA = 0.0045      # fracción del radio: una mancha real, desenfocada por la óptica, no es más pequeña
AREA_MIN_UMBRA = 2
OSCURO_MAX = 0.08              # más de esta fracción del disco «oscura»: nubes o el disco mal medido
SEP_GRUPO_DEG = 8.0            # dos manchas a menos de esto (sobre la esfera) van al mismo grupo
RADIO_SOL_KM = 695700.0        # radio nominal del Sol (IAU 2015): el radio del disco en píxeles son estos km
FONDO_LOCAL = 0.12             # fracción del radio: tamaño del cierre que estima la fotosfera local (quita gradientes)
# Por sensibilidad: (contraste mínimo de una mancha, veces el ruido de la fotosfera, umbral de umbra).
# Una zona es mancha si queda por debajo de 1 − max(contraste mínimo, n·σ) de la fotosfera local;
# medido en las fotos reales del 10 y el 17-oct-2026: σ ≈ 0,005–0,006 en las limpias, las manchas
# pequeñas contrastan un 7–10 % y las grandes un 20–45 % (solo el núcleo de la mayor baja del 50 %).
SENSIBILIDADES = {"baja": (0.10, 6.0, 0.45), "normal": (0.07, 4.0, 0.50), "alta": (0.05, 3.0, 0.55)}

_lock = threading.Lock()
_cache = {}


class ErrorSol(Exception):
    """No se puede contar en esta foto (el texto dice por qué)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Qué fotos son del Sol y cuándo se hicieron
# ─────────────────────────────────────────────────────────────────────────────
def es_sol(nombre, meta=None):
    """Si la foto de la galería es del Sol: por el objeto de su JSON («Sol», «Sun») o, si no tiene, por su nombre."""
    objeto = str((meta or {}).get("objeto") or "").strip().lower()
    if objeto:
        return objeto.split()[0] in NOMBRES_SOL
    base = os.path.splitext(os.path.basename(nombre))[0]
    return base.split("_")[0].lower() in NOMBRES_SOL


def sensibilidad_valida(valor):
    v = str(valor or "").strip().lower()
    return v if v in SENSIBILIDADES else "normal"


def factor_k(ruta_config="config.ini"):
    """[Sol] factor_k de config.ini (1,0 si no está): el factor personal del observador."""
    cfg = configparser.ConfigParser()
    cfg.read(ruta_config)
    try:
        k = float(cfg.get("Sol", "factor_k", fallback="1.0").replace(",", "."))
    except ValueError:
        k = 1.0
    return k if math.isfinite(k) and k > 0 else 1.0


def numero_wolf(g, f, k=1.0):
    return round(float(k) * (10 * int(g) + int(f)), 1)


# ─────────────────────────────────────────────────────────────────────────────
#  Efemérides físicas del Sol (Meeus, cap. 29)
# ─────────────────────────────────────────────────────────────────────────────
def geometria_sol(instante):
    """
    P (ángulo de posición del eje de rotación, desde el norte celeste hacia el este), B0
    (latitud heliográfica del centro del disco) y L0 (longitud de Carrington del meridiano
    central), en grados, en el instante unix dado. Fórmulas de Meeus (Astronomical
    Algorithms, cap. 29) con la longitud aparente del Sol y la oblicuidad verdadera.
    """
    from astropy.coordinates import get_body, GeocentricTrueEcliptic
    from astropy.time import Time
    import erfa
    t = Time(float(instante), format="unix")
    ecl = get_body("sun", t).transform_to(GeocentricTrueEcliptic(equinox=t))
    lam = float(ecl.lon.deg) - 20.4898 / 3600.0 / float(ecl.distance.to_value("AU"))   # aberración
    jd1, jd2 = t.tt.jd1, t.tt.jd2
    eps = math.degrees(erfa.obl06(jd1, jd2) + erfa.nut06a(jd1, jd2)[1])
    jde = float(t.tt.jd)
    theta = ((jde - 2398220.0) * 360.0 / 25.38) % 360.0
    inc = math.radians(7.25)
    K = 73.6667 + 1.3958333 * (jde - 2396758.0) / 36525.0
    lk = math.radians(lam - K)
    x = math.degrees(math.atan(-math.cos(math.radians(lam)) * math.tan(math.radians(eps))))
    y = math.degrees(math.atan(-math.cos(lk) * math.tan(inc)))
    P = x + y
    B0 = math.degrees(math.asin(math.sin(lk) * math.sin(inc)))
    # η en el cuadrante opuesto a λ−K: así L0 coincide con el modelo IAU del Sol (y con el ejemplo 29.a)
    eta = math.degrees(math.atan2(-math.sin(lk) * math.cos(inc), -math.cos(lk)))
    L0 = (eta - theta) % 360.0
    return {"P": P, "B0": B0, "L0": L0, "instante": float(instante)}


def _fecha_local_de(nombre, meta):
    """Instante unix de la foto: luna_anotada.instante_de (el JSON o la fecha del nombre)."""
    import luna_anotada
    return luna_anotada.instante_de(nombre, meta)


# ─────────────────────────────────────────────────────────────────────────────
#  Geometría sobre la esfera
# ─────────────────────────────────────────────────────────────────────────────
def _xyz(x, y, disco):
    """(X, Y, Z) unitarios del punto (x, y) de la imagen sobre la esfera solar (X, Y en el plano del disco)."""
    X = (float(x) - disco["cx"]) / disco["r"]
    Y = (float(y) - disco["cy"]) / disco["r"]
    rho2 = X * X + Y * Y
    if rho2 >= 1.0:
        n = math.sqrt(rho2)
        X, Y, rho2 = X / n * 0.9999, Y / n * 0.9999, 0.9998
    return X, Y, math.sqrt(1.0 - rho2)


def separacion_deg(a, b, disco):
    """Ángulo (grados, sobre la esfera) entre dos manchas por su posición en el disco; no depende del norte."""
    pa, pb = _xyz(a["x"], a["y"], disco), _xyz(b["x"], b["y"], disco)
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(u * v for u, v in zip(pa, pb))))))


def _coordenadas(x, y, disco, cielo):
    """
    (latitud, Δlongitud desde el meridiano central con el oeste positivo, longitud de
    Carrington) del punto (x, y) de la imagen. `cielo` trae las direcciones del norte y el
    este solares en los ejes de la imagen y la geometría (P, B0, L0).
    """
    X, Y, Z = _xyz(x, y, disco)
    # Componentes a lo largo del norte y del este solares, en la esfera (Z hacia el observador)
    n, e = cielo["norte_sol"], cielo["este_sol"]
    yn = X * n[0] + Y * n[1]
    xe = X * e[0] + Y * e[1]
    b0 = math.radians(cielo["geometria"]["B0"])
    lat = math.degrees(math.asin(max(-1.0, min(1.0, yn * math.cos(b0) + Z * math.sin(b0)))))
    dlon = math.degrees(math.atan2(-xe, Z * math.cos(b0) - yn * math.sin(b0)))
    return lat, dlon, (cielo["geometria"]["L0"] + dlon) % 360.0


def _completar_coordenadas(manchas, disco, cielo):
    for m in manchas:
        if cielo is None:
            m["lat"] = m["dlon"] = m["lon"] = None
        else:
            m["lat"], m["dlon"], m["lon"] = (round(v, 1) for v in _coordenadas(m["x"], m["y"], disco, cielo))


def agrupar(manchas, disco):
    """
    Asigna a cada mancha su `grupo` (1, 2, ...): enlace simple, dos manchas van al mismo grupo
    si están a menos de SEP_GRUPO_DEG sobre la esfera (el criterio no depende de la orientación:
    así el conteo es el mismo con y sin plate solving). Los grupos se numeran de izquierda a derecha.
    """
    n = len(manchas)
    padre = list(range(n))

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if separacion_deg(manchas[i], manchas[j], disco) <= SEP_GRUPO_DEG:
                padre[raiz(i)] = raiz(j)
    return renumerar_grupos(manchas, [raiz(i) for i in range(n)])


def renumerar_grupos(manchas, etiquetas=None):
    """Grupos 1..g de izquierda a derecha (por el centro de cada grupo); devuelve las manchas."""
    if etiquetas is None:
        etiquetas = [int(m.get("grupo") or 0) for m in manchas]
    centros = {}
    for m, e in zip(manchas, etiquetas):
        centros.setdefault(e, []).append(float(m["x"]))
    orden = sorted(centros, key=lambda e: (sum(centros[e]) / len(centros[e]), e))
    nuevo = {e: i + 1 for i, e in enumerate(orden)}
    for m, e in zip(manchas, etiquetas):
        m["grupo"] = nuevo[e]
    return manchas


def largo_km(puntos, disco):
    """
    La mayor distancia sobre la esfera (km) entre los puntos dados (px de la imagen), más un píxel: entre
    centros de píxel se queda medio píxel corta por cada lado.
    """
    P = np.asarray(puntos, dtype=np.float64).reshape(-1, 2)
    if len(P) == 0:
        return 0.0
    X, Y = (P[:, 0] - disco["cx"]) / disco["r"], (P[:, 1] - disco["cy"]) / disco["r"]
    rho = np.hypot(X, Y)
    fuera = rho > 0.9999
    X[fuera], Y[fuera] = X[fuera] / rho[fuera] * 0.9999, Y[fuera] / rho[fuera] * 0.9999
    V = np.c_[X, Y, np.sqrt(np.clip(1.0 - X * X - Y * Y, 0.0, 1.0))]
    return float(np.arccos(np.clip(np.min(V @ V.T), -1.0, 1.0))) * RADIO_SOL_KM + RADIO_SOL_KM / disco["r"]


def tamano_grupo(ms, zonas_por_id, disco):
    """
    (área en km², diámetro equivalente en km, largo de punta a punta en km) de un grupo: sus zonas
    oscuras (umbra y penumbra) cuentan una vez cada una aunque tengan varias umbras; una mancha
    añadida a mano, sin zona, cuenta como un círculo de su radio. El largo es la mayor distancia sobre
    la esfera entre dos puntos de los contornos de sus zonas.
    """
    km_px = RADIO_SOL_KM / disco["r"]
    area, puntos, vistas = 0.0, [], set()
    for m in ms:
        z = zonas_por_id.get(m.get("zona"))
        if z is not None:
            if z["id"] not in vistas:
                vistas.add(z["id"])
                area += z["area_km2"]
                puntos += z["contorno"]
        else:
            __, __, Z = _xyz(m["x"], m["y"], disco)
            rp = float(m.get("r_px") or 2.0)
            area += math.pi * (rp * km_px) ** 2 / max(Z, 0.15)
            puntos += [[m["x"] + rp * math.cos(a), m["y"] + rp * math.sin(a)] for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
    return area, 2.0 * math.sqrt(area / math.pi), largo_km(puntos, disco)


def grupos_de(manchas, zonas=None, disco=None):
    """
    Lista de grupos (id, n, centro, coordenadas medias si todas sus manchas las tienen, área) y, si se
    dan las zonas oscuras de la foto (detectar) y el disco, su tamaño en km.
    """
    por_id = {}
    for m in manchas:
        por_id.setdefault(int(m["grupo"]), []).append(m)
    zonas_por_id = {z["id"]: z for z in (zonas or [])}
    salida = []
    for gid in sorted(por_id):
        ms = por_id[gid]
        g = {"id": gid, "n": len(ms), "x": round(sum(m["x"] for m in ms) / len(ms), 1),
             "y": round(sum(m["y"] for m in ms) / len(ms), 1),
             "area_mhs": round(sum(float(m.get("area_mhs") or 0.0) for m in ms), 1), "lat": None, "dlon": None,
             "km2": None, "diametro_km": None, "largo_km": None}
        if all(m.get("lat") is not None for m in ms):
            g["lat"] = round(sum(m["lat"] for m in ms) / len(ms), 1)
            g["dlon"] = round(sum(m["dlon"] for m in ms) / len(ms), 1)
        if zonas is not None and disco:
            a, d_eq, largo = tamano_grupo(ms, zonas_por_id, disco)
            g.update({"km2": round(a), "diametro_km": round(d_eq), "largo_km": round(largo)})
        salida.append(g)
    return salida


# ─────────────────────────────────────────────────────────────────────────────
#  Detección en la imagen
# ─────────────────────────────────────────────────────────────────────────────
def localizar_disco(lum):
    """{cx, cy, r} del disco en la imagen o ErrorSol si no se ve."""
    import enfoque
    c = enfoque.circulo_disco(lum, reduccion=2 if max(lum.shape) > 900 else 1)
    if c is None or c[2] < RADIO_MIN_PX:
        raise ErrorSol(_("No se ve el disco del Sol en la foto"))
    cx, cy, r = (float(v) for v in c)
    h, w = lum.shape
    cortado = cx - r < -0.05 * r or cy - r < -0.05 * r or cx + r > w - 1 + 0.05 * r or cy + r > h - 1 + 0.05 * r
    return {"cx": cx, "cy": cy, "r": r, "cortado": bool(cortado)}


def aplanar_limbo(lum, disco):
    """
    (fotosfera aplanada, máscara del disco útil): la imagen dividida por su perfil radial
    (mediana por anillos de μ = cos ρ, interpolada), que quita el oscurecimiento del limbo.
    Fuera de RADIO_UTIL el valor no sirve (queda a 1).
    """
    h, w = lum.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.hypot(xx - disco["cx"], yy - disco["cy"]) / disco["r"]
    util = d < RADIO_UTIL
    mu = np.sqrt(np.clip(1.0 - d * d, 0.0, 1.0))
    idx = np.clip((mu * ANILLOS_PERFIL).astype(np.int32), 0, ANILLOS_PERFIL - 1)
    # El perfil con una muestra de píxeles (basta: 48 medianas robustas)
    paso = max(1, int(round(max(h, w) / 700.0)))
    sub_l, sub_i, sub_u = lum[::paso, ::paso], idx[::paso, ::paso], util[::paso, ::paso]
    centros = (np.arange(ANILLOS_PERFIL) + 0.5) / ANILLOS_PERFIL
    medianas = np.full(ANILLOS_PERFIL, np.nan)
    for i in range(ANILLOS_PERFIL):
        sel = sub_u & (sub_i == i)
        if np.count_nonzero(sel) >= 8:
            medianas[i] = np.median(sub_l[sel])
    ok = np.isfinite(medianas) & (medianas > 0)
    if np.count_nonzero(ok) < 4:
        raise ErrorSol(_("El disco del Sol sale demasiado oscuro o plano para medirlo"))
    perfil = np.interp(mu, centros[ok], medianas[ok]).astype(np.float32)
    plano = np.ones_like(lum)
    np.divide(lum, perfil, out=plano, where=util & (perfil > 0))
    return plano, util


def fotosfera_local(plano, disco):
    """
    (brillo relativo a la fotosfera local, ruido σ): el aplanado radial deja gradientes (calima,
    viñeteo con el Sol descentrado) de varios % que parecerían manchas; un cierre morfológico más
    grande que cualquier mancha da la fotosfera de alrededor y se divide por ella. El ruido es la
    desviación robusta (MAD) dentro del 90 % del radio.
    """
    import cv2
    suave = cv2.GaussianBlur(plano, (0, 0), SIGMA_SUAVIZADO)
    k = max(9, int(round(FONDO_LOCAL * disco["r"])) | 1)
    if k > 25:                                   # el cierre grande, a mitad de resolución: igual de bueno y 4× más rápido
        h, w = suave.shape
        peq = cv2.resize(suave, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        fondo = cv2.morphologyEx(peq, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k // 2 | 1, k // 2 | 1)))
        fondo = cv2.resize(fondo, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        fondo = cv2.morphologyEx(suave, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    local = suave / np.maximum(fondo, 1e-3)
    h, w = local.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    centro = np.hypot(xx - disco["cx"], yy - disco["cy"]) < 0.9 * disco["r"]
    v = local[centro]
    if v.size < 100:
        raise ErrorSol(_("El disco del Sol sale demasiado oscuro o plano para medirlo"))
    mediana = float(np.median(v))
    sigma = float(1.4826 * np.median(np.abs(v - mediana)))
    return local, max(sigma, 1e-4)


def detectar(lum, disco, sensibilidad="normal"):
    """
    Manchas sobre la fotosfera local: lista de dicts (x, y, r_px, umbra, area_px, area_mhs,
    contraste, zona) en píxeles de `lum`, el total de la zona de penumbra, {ruido, umbral,
    resolucion_km} y las zonas oscuras (umbra y penumbra juntas: lo que se ve como una mancha) con su
    tamaño en km: [{id, x, y, area_km2, largo_km}]. f = una mancha por núcleo de umbra; una penumbra
    sin umbra cuenta una. Lanza ErrorSol si el disco no es limpio.
    """
    import cv2
    c_min, n_sigma, u_umb = SENSIBILIDADES[sensibilidad_valida(sensibilidad)]
    plano, util = aplanar_limbo(lum, disco)
    suave, sigma = fotosfera_local(plano, disco)
    u_pen = 1.0 - max(c_min, n_sigma * sigma)
    pen = (suave < u_pen) & util
    n_util = int(np.count_nonzero(util))
    if n_util and np.count_nonzero(pen) > OSCURO_MAX * n_util:
        raise ErrorSol(_("Demasiado oscuro dentro del disco: ¿nubes, o el disco mal medido?"))
    umb = (suave < u_umb) & util
    n_p, et_p, st_p, __ = cv2.connectedComponentsWithStats(pen.astype(np.uint8), 8)
    n_u, et_u, st_u, cen_u = cv2.connectedComponentsWithStats(umb.astype(np.uint8), 8)
    r = disco["r"]

    def _mancha(etiquetas, label, area, umbra):
        ys, xs = np.nonzero(etiquetas == label)
        x, y = float(xs.mean()), float(ys.mean())
        X, Y, Z = _xyz(x, y, disco)
        return {"x": round(x, 1), "y": round(y, 1), "r_px": round(math.sqrt(area / math.pi), 2), "umbra": bool(umbra),
                "area_px": int(area), "area_mhs": round(area * 1e6 / (2.0 * math.pi * r * r * max(Z, 0.15)), 1),
                "contraste": round(float(1.0 - suave[ys, xs].mean()), 3), "zona": None}

    km_px = RADIO_SOL_KM / r

    def _zona(lab):
        """
        La zona oscura `lab` sobre la esfera: área (cada píxel vale 1/μ por el escorzo), contorno (la
        envolvente convexa: con ella se mide el largo de la zona y el de su grupo) y largo de punta a punta.
        """
        ys, xs = np.nonzero(et_p == lab)
        X, Y = (xs - disco["cx"]) / r, (ys - disco["cy"]) / r
        Z = np.sqrt(np.clip(1.0 - X * X - Y * Y, 0.0, 1.0))
        contorno = cv2.convexHull(np.c_[xs, ys].astype(np.float32)).reshape(-1, 2)
        return {"id": int(lab), "x": round(float(xs.mean()), 1), "y": round(float(ys.mean()), 1),
                "area_km2": round(float(np.sum(1.0 / np.maximum(Z, 0.15))) * km_px * km_px),
                "largo_km": round(largo_km(contorno, disco)), "contorno": [[float(a), float(b)] for a, b in contorno]}

    def _en_el_disco(m):
        return math.hypot(m["x"] - disco["cx"], m["y"] - disco["cy"]) < CENTRO_MAX * r

    # Medido en las fotos reales: los puntos de 3 px con el 7 % de contraste son ruido (polvo,
    # píxeles calientes); las manchas reales más pequeñas miden 8 px a r = 330 px
    area_min = max(AREA_MIN_PENUMBRA, int(round(math.pi * (RADIO_MIN_MANCHA * r) ** 2)))
    manchas, con_umbra = [], set()
    for lab in range(1, n_u):
        area = int(st_u[lab, cv2.CC_STAT_AREA])
        if area < AREA_MIN_UMBRA:
            continue
        m = _mancha(et_u, lab, area, True)
        if not _en_el_disco(m):
            continue
        m["zona"] = int(et_p[int(round(cen_u[lab][1])), int(round(cen_u[lab][0]))]) or None
        con_umbra.add(m["zona"])
        manchas.append(m)
    area_pen, zonas = 0, []
    for lab in range(1, n_p):
        area = int(st_p[lab, cv2.CC_STAT_AREA])
        if area < area_min:
            continue
        m = _mancha(et_p, lab, area, False)
        if not _en_el_disco(m):
            continue
        area_pen += area
        zonas.append(_zona(lab))
        if lab in con_umbra:
            continue
        m["zona"] = int(lab)
        manchas.append(m)
    ids = {z["id"] for z in zonas}
    for m in manchas:
        if m["zona"] not in ids:
            m["zona"] = None
    medida = {"ruido": round(sigma, 4), "umbral": round(u_pen, 3), "resolucion_km": resolucion_km(lum, disco)}
    return manchas, area_pen, medida, zonas


def resolucion_km(lum, disco):
    """
    Lo que emborronan el desenfoque y el seeing, en km sobre el Sol: la anchura del limbo (la caída del
    brillo en el borde del disco). Una mancha más pequeña sale con este tamaño, no con el suyo.
    """
    import enfoque
    try:
        anch, __, __ = enfoque.anchura_limbo(np.asarray(lum, dtype=np.float32), disco["cx"], disco["cy"], disco["r"])
    except Exception:
        return None
    if len(anch) < 30:
        return None
    return round(float(np.median(anch)) * RADIO_SOL_KM / disco["r"])


# ─────────────────────────────────────────────────────────────────────────────
#  La imagen de la foto y cómo pasar de sus píxeles a los del JPG
# ─────────────────────────────────────────────────────────────────────────────
def _tamano_jpg(ruta_jpg):
    from PIL import Image
    with Image.open(ruta_jpg) as im:
        return im.size


def _imagen(nombre, meta, ruta_jpg):
    """
    (luminancia float32 2D para detectar, T 3×3 que lleva un píxel de esa imagen a píxeles del
    JPG o None si no se sabe, origen): el crudo lineal si la foto lo tiene (editor_dia) y se
    sabe con qué giro de Cámara se hizo su JPG (meta["giro"]: el giro de hoy puede no ser el de
    entonces, y con el giro equivocado las manchas caen fuera de sitio); si no, el JPG aproximado a
    lineal, que vale casi igual (medido en las fotos reales: ruido 0,009 frente a 0,006).
    """
    import cv2
    import editor_dia
    import procesado
    import revelado_dia
    wj, hj = _tamano_jpg(ruta_jpg)
    enc = meta.get("giro") if isinstance(meta.get("giro"), dict) else None
    lineal = None
    if enc is not None:
        try:
            lineal, __ = editor_dia.cargar_lineal(nombre)
        except editor_dia.ErrorEditorDia:
            lineal = None
    if lineal is not None:
        h, w = lineal.shape[:2]
        g = procesado.geometria_giro(w, h, 1.0, enc.get("giro", 0.0), bool(enc.get("espejo")))
        if (g["ancho"], g["alto"]) == (wj, hj):
            return revelado_dia.luminancia(lineal).astype(np.float32), np.asarray(g["T"], dtype=np.float64), "crudo"
        log.info("Wolf en %s: el crudo girado (%d×%d) no casa con el JPG (%d×%d); se cuenta sobre el JPG", nombre, g["ancho"], g["alto"], wj, hj)
    gris = cv2.imread(ruta_jpg, cv2.IMREAD_GRAYSCALE)
    if gris is None:
        raise ErrorSol(_("No se pudo leer la imagen"))
    return (gris.astype(np.float32) / 255.0) ** 2.2, None, "jpg"


def _reducir(lum):
    """(imagen con el lado mayor a ANCHO_ANALISIS, escala) para que el análisis no se eternice en el mini PC."""
    import revelado_dia
    return revelado_dia.reducir(lum, ANCHO_ANALISIS)


def _a_jpg(T, x, y):
    X, Y, __ = T @ np.array([float(x), float(y), 1.0])
    return float(X), float(Y)


# ─────────────────────────────────────────────────────────────────────────────
#  Orientación: dónde están el norte y el este en el JPG
# ─────────────────────────────────────────────────────────────────────────────
def _direcciones_cielo(nombre, meta, instante, T_jpg, observatorio):
    """
    Direcciones unitarias (en los ejes del JPG: x a la derecha, y hacia abajo) del norte y del
    este celestes en el Sol, o None si no se conoce la orientación de la cámara. La orientación
    (matriz H del plate solving, en píxeles de la foto de ASCOM) viene con el vídeo del que salió
    la foto o es la última guardada (orientacion_camara); el crudo está en los ejes de ASCOM y
    T_jpg lleva del crudo al JPG.
    """
    import editor_dia
    import apilado_video
    import orientacion_camara
    H, binning = None, 1
    hdr = editor_dia.cabecera(nombre)
    if hdr is not None and str(hdr.get("MODO", "")).strip().lower() == "video":
        mv = apilado_video.leer_meta(editor_dia.ruta_video(hdr))
        if mv.get("H_camara"):
            H, binning = mv["H_camara"], mv.get("binning_foto", 1)
            if mv.get("observatorio"):
                observatorio = tuple(mv["observatorio"])
    if H is None:
        o = orientacion_camara.ultima()
        if o and o.get("H"):
            H, binning = o["H"], o.get("binning_foto", 1)
    if H is None or not observatorio or len(observatorio) < 2:
        return None
    from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
    from astropy.time import Time
    import astropy.units as u
    lat, lon = float(observatorio[0]), float(observatorio[1])
    alt_m = float(observatorio[2]) if len(observatorio) > 2 else 0.0
    t = Time(float(instante), format="unix")
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    sol = get_body("sun", t, lugar)
    ra, dec = float(sol.ra.deg), float(sol.dec.deg)
    paso = 0.1
    puntos = SkyCoord(ra=[ra, ra, ra + paso / math.cos(math.radians(dec))] * u.deg, dec=[dec, dec + paso, dec] * u.deg, frame="icrs")
    h = puntos.transform_to(AltAz(obstime=t, location=lugar))
    az, alt = h.az.deg, h.alt.deg
    Hc = np.asarray(H, dtype=float) / float(binning or 1)          # (Δaz·cos alt, Δalt) = Hc · (Δcolumna, Δfila) del crudo
    A = np.asarray(T_jpg, dtype=float)[:2, :2]                     # del crudo al JPG (giro y espejo)
    M = A @ np.linalg.inv(Hc)
    salida = {}
    for clave, i in (("norte", 1), ("este", 2)):
        du = ((az[i] - az[0] + 180.0) % 360.0 - 180.0) * math.cos(math.radians(alt[0]))
        dv = alt[i] - alt[0]
        v = M @ np.array([du, dv])
        n = float(np.linalg.norm(v))
        if not np.isfinite(n) or n == 0:
            return None
        salida[clave] = (float(v[0] / n), float(v[1] / n))
    return salida


def contexto_cielo(nombre, meta, instante, T_jpg, observatorio):
    """
    Geometría y direcciones solares en el JPG, o None sin orientación: `norte_sol` y `este_sol`
    (unitarios, ejes del JPG), `norte_deg` (ángulo del norte celeste desde +x hacia +y) y la
    geometría P, B0, L0. La geometría se devuelve aparte aunque no haya orientación.
    """
    geo = geometria_sol(instante)
    if T_jpg is None:                            # no se sabe cómo está girado el JPG respecto al crudo
        return geo, None
    try:
        dirs = _direcciones_cielo(nombre, meta, instante, T_jpg, observatorio)
    except Exception as e:
        log.warning("Wolf en %s: no se pudo saber la orientación: %s", nombre, e)
        dirs = None
    if dirs is None:
        return geo, None
    n, e = np.array(dirs["norte"]), np.array(dirs["este"])
    p = math.radians(geo["P"])
    ns = math.cos(p) * n + math.sin(p) * e
    es = math.cos(p) * e - math.sin(p) * n
    return geo, {"geometria": geo, "norte_sol": (float(ns[0]), float(ns[1])), "este_sol": (float(es[0]), float(es[1])),
                 "norte_deg": math.degrees(math.atan2(n[1], n[0])) % 360.0}


# ─────────────────────────────────────────────────────────────────────────────
#  La foto de la galería: detectar, guardar, serie
# ─────────────────────────────────────────────────────────────────────────────
def _meta_y_rutas(nombre):
    from editor_fotos import ErrorEditor, rutas
    import luna_anotada
    __, ruta_jpg, __, ruta_json = rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditor(_("Imagen no encontrada"))
    meta = luna_anotada.meta_de(ruta_jpg)
    if not es_sol(nombre, meta):
        raise ErrorEditor(_("La foto no es del Sol"))
    return meta, ruta_jpg, ruta_json


def _observatorio_de(meta, observatorio):
    lat, lon = meta.get("latitud"), meta.get("longitud")
    if lat is None or lon is None:
        return tuple(observatorio) if observatorio else None
    return (float(lat), float(lon))


def _paquete(nombre, meta, disco, manchas, area_mhs, geo, cielo, k, sensibilidad, ancho, alto, guardado, origen="",
             zonas=None, resolucion=None):
    grupos = grupos_de(manchas, zonas, disco)
    g, f = len(grupos), len(manchas)
    return {"ok": True, "sol": True, "ancho": ancho, "alto": alto,
            "disco": {"cx": round(disco["cx"], 1), "cy": round(disco["cy"], 1), "r": round(disco["r"], 1), "cortado": bool(disco.get("cortado"))},
            "manchas": [{k_: v for k_, v in m.items() if k_ != "area_px"} for m in manchas],
            "grupos": grupos, "wolf": {"g": g, "f": f, "R": numero_wolf(g, f, k), "k": k},
            "area_mhs": round(float(area_mhs), 1),
            "geometria": {"P": round(geo["P"], 2), "B0": round(geo["B0"], 2), "L0": round(geo["L0"], 2),
                          "norte_deg": round(cielo["norte_deg"], 1) if cielo else None},
            "sensibilidad": sensibilidad, "guardado": bool(guardado), "sep_grupo_deg": SEP_GRUPO_DEG, "origen": origen,
            "zonas": zonas, "resolucion_km": resolucion, "radio_sol_km": RADIO_SOL_KM}


def _desde_guardado(nombre, meta, ruta_jpg, observatorio):
    """El conteo guardado en el JSON de la foto (meta["wolf"]), con las coordenadas al día."""
    w = meta["wolf"]
    disco = dict(w["disco"])
    manchas = [dict(m) for m in w["manchas"]]
    wj, hj = _tamano_jpg(ruta_jpg)
    instante = _fecha_local_de(nombre, meta) or time.time()
    try:
        __, T, __ = _imagen(nombre, meta, ruta_jpg)          # solo para saber cómo está girado el JPG
    except ErrorSol:
        T = None
    geo, cielo = contexto_cielo(nombre, meta, instante, T, _observatorio_de(meta, observatorio))
    _completar_coordenadas(manchas, disco, cielo)
    renumerar_grupos(manchas)
    return _paquete(nombre, meta, disco, manchas, w.get("area_mhs", 0.0), geo, cielo, float(w.get("k") or factor_k()),
                    sensibilidad_valida(w.get("sensibilidad")), wj, hj, True, "guardado",
                    w.get("zonas"), w.get("resolucion_km"))


def objetos_foto(nombre, observatorio=None, sensibilidad="normal", forzar=False):
    """
    Manchas y grupos de una foto del Sol de la galería en píxeles de su JPG, con el Número de
    Wolf, para la capa del visor (static/js/manchas_solares.js). Si la foto ya tiene un conteo
    guardado se devuelve ese (salvo `forzar`). observatorio = (lat, lon) por si la foto no lo
    lleva en su JSON. Lanza editor_fotos.ErrorEditor con el motivo si no se puede.
    """
    from editor_fotos import ErrorEditor
    meta, ruta_jpg, __ = _meta_y_rutas(nombre)
    if not forzar and isinstance(meta.get("wolf"), dict) and meta["wolf"].get("manchas") is not None and meta["wolf"].get("disco"):
        return _desde_guardado(nombre, meta, ruta_jpg, observatorio)
    sensibilidad = sensibilidad_valida(sensibilidad)
    est = os.stat(ruta_jpg)
    clave = (nombre, est.st_size, est.st_mtime_ns, sensibilidad)
    with _lock:
        if clave in _cache:
            return _cache[clave]
    t0 = time.time()
    try:
        lum, T, origen = _imagen(nombre, meta, ruta_jpg)
        lum, escala = _reducir(lum)
        disco_a = localizar_disco(lum)
        manchas, area_pen, medida, zonas = detectar(lum, disco_a, sensibilidad)
    except ErrorSol as e:
        log.info("Sol sin contar en %s: %s (%.1f s)", nombre, e, time.time() - t0)
        raise ErrorEditor(str(e))
    # A píxeles del JPG: deshacer la reducción y aplicar el giro de Cámara
    Tj = (np.eye(3) if T is None else T) @ np.diag([1.0 / escala, 1.0 / escala, 1.0])
    cx, cy = _a_jpg(Tj, disco_a["cx"], disco_a["cy"])
    disco = {"cx": cx, "cy": cy, "r": disco_a["r"] / escala, "cortado": disco_a["cortado"]}
    for m in manchas:
        m["x"], m["y"] = (round(v, 1) for v in _a_jpg(Tj, m["x"], m["y"]))
        m["r_px"] = round(m["r_px"] / escala, 2)
    for z in zonas:                                        # los km no cambian: solo la posición
        z["x"], z["y"] = (round(v, 1) for v in _a_jpg(Tj, z["x"], z["y"]))
        z["contorno"] = [[round(v, 1) for v in _a_jpg(Tj, a, b)] for a, b in z["contorno"]]
    wj, hj = _tamano_jpg(ruta_jpg)
    instante = _fecha_local_de(nombre, meta) or time.time()
    geo, cielo = contexto_cielo(nombre, meta, instante, T, _observatorio_de(meta, observatorio))
    _completar_coordenadas(manchas, disco, cielo)
    agrupar(manchas, disco)
    area_mhs = area_pen * 1e6 / (2.0 * math.pi * disco_a["r"] ** 2)
    salida = _paquete(nombre, meta, disco, manchas, area_mhs, geo, cielo, factor_k(), sensibilidad, wj, hj, False, origen,
                      zonas, medida.get("resolucion_km"))
    salida["medida"] = medida
    log.info("Wolf en %s (%s, sensibilidad %s, ruido %.4f, umbral %.3f): g=%d f=%d R=%s%s, P=%.1f° B0=%.1f°%s, %.1f s", nombre, origen,
             sensibilidad, medida["ruido"], medida["umbral"], salida["wolf"]["g"], salida["wolf"]["f"], salida["wolf"]["R"],
             " (disco cortado)" if disco["cortado"] else "", geo["P"], geo["B0"], "" if cielo else " (sin orientación)", time.time() - t0)
    with _lock:
        if len(_cache) > 20:
            for k_ in list(_cache)[:10]:
                del _cache[k_]
        _cache[clave] = salida
    return salida


def _olvidar_cache(nombre):
    with _lock:
        for k_ in [k_ for k_ in _cache if k_[0] == nombre]:
            del _cache[k_]


def _fecha_iso(nombre, meta):
    t = _fecha_local_de(nombre, meta)
    return datetime.fromtimestamp(t).replace(microsecond=0).isoformat() if t else ""


def guardar(nombre, datos, observatorio=None):
    """
    Guarda el conteo corregido en el visor: datos = {manchas: [{x, y, r_px, umbra, grupo, area_mhs, zona}],
    disco: {cx, cy, r}, sensibilidad, k (opcional), zonas y resolucion_km (los tamaños en km)}. Va al JSON de la foto (meta["wolf"]) y a la
    serie datos/numero_wolf.csv (una fila por foto). Devuelve el paquete para el visor.
    """
    from editor_fotos import ErrorEditor
    import firma
    meta, ruta_jpg, __ = _meta_y_rutas(nombre)
    try:
        disco = {k_: float(datos["disco"][k_]) for k_ in ("cx", "cy", "r")}
        disco["cortado"] = bool(datos["disco"].get("cortado"))
        manchas = []
        for m in datos.get("manchas") or []:
            x, y, r_px = float(m["x"]), float(m["y"]), float(m.get("r_px") or 2.0)
            if not all(math.isfinite(v) for v in (x, y, r_px)):
                raise ValueError("coordenada no finita")
            X, Y, Z = _xyz(x, y, disco)
            area_mhs = m.get("area_mhs")
            if area_mhs is None:
                area_mhs = math.pi * r_px * r_px * 1e6 / (2.0 * math.pi * disco["r"] ** 2 * max(Z, 0.15))
            manchas.append({"x": round(x, 1), "y": round(y, 1), "r_px": round(r_px, 2), "umbra": bool(m.get("umbra")),
                            "grupo": int(m.get("grupo") or 1), "area_mhs": round(float(area_mhs), 1),
                            "contraste": float(m.get("contraste") or 0.0),
                            "zona": int(m["zona"]) if m.get("zona") is not None else None})
        zonas = None
        if isinstance(datos.get("zonas"), list):
            zonas = [{"id": int(z["id"]), "x": round(float(z["x"]), 1), "y": round(float(z["y"]), 1),
                      "area_km2": round(float(z["area_km2"])), "largo_km": round(float(z["largo_km"])),
                      "contorno": [[round(float(a), 1), round(float(b), 1)] for a, b in z["contorno"]]} for z in datos["zonas"]]
        resolucion = datos.get("resolucion_km")
        resolucion = round(float(resolucion)) if resolucion is not None else None
        if disco["r"] <= 0:
            raise ValueError("radio")
        k = float(datos.get("k") or factor_k())
        if not math.isfinite(k) or k <= 0:
            k = factor_k()
    except (KeyError, TypeError, ValueError) as e:
        raise ErrorEditor(_("Conteo no válido: {e}", e=e))
    renumerar_grupos(manchas)
    g, f = len(grupos_de(manchas)), len(manchas)
    area_mhs = round(float(datos.get("area_mhs") or sum(m["area_mhs"] for m in manchas)), 1)
    fecha = _fecha_iso(nombre, meta)
    meta["wolf"] = {"g": g, "f": f, "R": numero_wolf(g, f, k), "k": k, "fecha": fecha,
                    "guardado_en": datetime.now().replace(microsecond=0).isoformat(), "a_mano": True,
                    "sensibilidad": sensibilidad_valida(datos.get("sensibilidad")), "area_mhs": area_mhs,
                    "disco": {k_: round(disco[k_], 1) for k_ in ("cx", "cy", "r")} | {"cortado": disco["cortado"]},
                    "manchas": manchas, "zonas": zonas, "resolucion_km": resolucion}
    firma.guardar_meta(ruta_jpg, meta)
    _anotar_serie(nombre, fecha, g, f, k, area_mhs)
    _olvidar_cache(nombre)
    log.info("Número de Wolf guardado para %s: g=%d f=%d R=%s (k=%g)", nombre, g, f, meta["wolf"]["R"], k)
    return objetos_foto(nombre, observatorio)


def olvidar(nombre, observatorio=None, sensibilidad="normal"):
    """Quita el conteo guardado de la foto (y de la serie) y vuelve a detectar."""
    import firma
    meta, ruta_jpg, __ = _meta_y_rutas(nombre)
    if "wolf" in meta:
        del meta["wolf"]
        firma.guardar_meta(ruta_jpg, meta)
    _quitar_de_serie(nombre)
    _olvidar_cache(nombre)
    return objetos_foto(nombre, observatorio, sensibilidad, forzar=True)


# ─── La serie ────────────────────────────────────────────────────────────────
def _leer_serie(ruta=None):
    filas = []
    try:
        with open(ruta or RUTA_SERIE, encoding="utf-8", newline="") as f:
            for fila in csv.DictReader(f):
                if fila.get("foto"):
                    filas.append({c: fila.get(c, "") for c in COLUMNAS_SERIE})
    except OSError:
        pass
    return filas


def _escribir_serie(filas, ruta=None):
    ruta = ruta or RUTA_SERIE
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    filas = sorted(filas, key=lambda r: (r["fecha"], r["foto"]))
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_SERIE)
        w.writeheader()
        w.writerows(filas)


def _anotar_serie(nombre, fecha, g, f, k, area_mhs):
    filas = [r for r in _leer_serie() if r["foto"] != nombre]
    filas.append({"fecha": fecha, "foto": nombre, "g": g, "f": f, "R": numero_wolf(g, f, k), "k": f"{k:g}",
                  "area_mhs": area_mhs, "a_mano": "1"})
    _escribir_serie(filas)


def _quitar_de_serie(nombre):
    filas = _leer_serie()
    quedan = [r for r in filas if r["foto"] != nombre]
    if len(quedan) != len(filas):
        _escribir_serie(quedan)


def serie():
    """La serie de Números de Wolf guardados, por fecha: [{fecha, foto, g, f, R, k}] (números ya convertidos)."""
    salida = []
    for r in _leer_serie():
        try:
            salida.append({"fecha": r["fecha"], "foto": r["foto"], "g": int(float(r["g"])), "f": int(float(r["f"])),
                           "R": float(r["R"]), "k": float(r["k"] or 1.0)})
        except (TypeError, ValueError):
            continue
    return sorted(salida, key=lambda d: d["fecha"])


if __name__ == "__main__":
    # Ejemplo 29.a de Meeus: 1992-10-13 0h TD → P = +26,27°, B0 = +5,99°, L0 = 238,63°
    from astropy.time import Time
    g = geometria_sol(Time("1992-10-13T00:00:00", scale="tt").unix)
    print(f"P={g['P']:.2f}° B0={g['B0']:.2f}° L0={g['L0']:.2f}°")
    g = geometria_sol(datetime.now(timezone.utc).timestamp())
    print(f"Hoy: P={g['P']:.2f}° B0={g['B0']:.2f}° L0={g['L0']:.2f}°")
