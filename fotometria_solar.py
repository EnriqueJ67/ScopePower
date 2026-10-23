# fotometria_solar.py
#
# Lo que se mide del disco del Sol en una foto de la galería, además de las manchas
# (manchas_solares.py): el oscurecimiento del limbo en cada color, la temperatura de la
# fotosfera según la profundidad que de ahí se deduce, y el diámetro aparente, que a lo
# largo del año dibuja la órbita de la Tierra.
#
#   1. Sobre el crudo lineal de la foto (no sobre el JPG: la curva de tonos falsearía los
#      brillos). La foto suelta es un mosaico Bayer: se usa en superpíxel, así cada canal es
#      puro y sin interpolar; el apilado de un vídeo ya viene en color.
#   2. El disco: enfoque.circulo_disco lo sitúa y el limbo se define donde el brillo cae más
#      deprisa (el punto de inflexión del perfil radial, enfoque.anchura_limbo, en 180
#      direcciones); un círculo ajustado a esos puntos da el radio al centésimo de píxel. La
#      anchura de esa caída es el desenfoque más el seeing.
#   3. El cielo junto al Sol (de 1,12 a 1,4 radios) se resta en cada canal y su brillo
#      relativo al centro del disco mide la calima: con más del 8 % la luz difusa altera el
#      perfil y la medida se marca como poco fiable (pasó el 27-sep-2026: 12 %).
#   4. El perfil: la mediana del brillo en anillos de 0,02 radios (las manchas no la mueven),
#      relativa al centro, en cada canal; se ajusta la ley cuadrática del oscurecimiento,
#      I(μ)/I(1) = 1 − u(1−μ) − v(1−μ)², con μ = cos θ = √(1 − (r/R)²).
#   5. La temperatura (aproximación de Eddington-Barbier): lo que se ve en la dirección μ sale
#      de la profundidad óptica τ ≈ μ, así que I(μ)/I(1) = B(T(τ=μ)) / B(T(τ=1)) con B la ley de
#      Planck en la longitud de onda de cada canal. Tomando T(τ=1) del modelo gris de Eddington
#      (6100 K con Teff = 5772 K) se despeja T(τ) en cada canal: si la física cuadra, los tres
#      dan la misma temperatura. Medido el 10-oct-2026: 5380 K a τ = 0,3 en rojo, verde y azul
#      (el modelo de Eddington da 5330 K). Las longitudes de onda efectivas de los canales son
#      aproximadas (filtros Bayer del IMX533 con el filtro solar).
#   6. El diámetro: radio en píxeles × escala de placa (la del último plate solving; si no hay,
#      la focal y el píxel de la cámara). Frente a la efeméride, la escala que implica el Sol
#      coincidió con la del plate solving a menos del 0,3 % en las 12 fotos reales con crudo.
#      Del radio sale la distancia Tierra-Sol, y con fotos de varios meses, la excentricidad de
#      la órbita y la fecha del perihelio.
#
# Las medidas se guardan en datos/medidas_sol.json (por foto, con la fecha del crudo): la
# serie del año se pinta sin volver a medir.

import os
import json
import math
import time
import logging
import threading
import configparser
from datetime import datetime

import numpy as np

from idioma import _, ngettext

log = logging.getLogger("scopepower.fotometria_solar")

RUTA_MEDIDAS = os.path.join("datos", "medidas_sol.json")
VERSION = 1                     # sube si cambia la forma de medir: las medidas guardadas se repiten
TEFF = 5772.0                   # temperatura efectiva del Sol (K, IAU 2015)
C2_UM_K = 14387.77              # segunda constante de radiación hc/k (µm·K)
LAMBDA_EF_UM = {"R": 0.610, "G": 0.535, "B": 0.465, "L": 0.550}    # longitudes de onda efectivas aproximadas
PASO_RHO = 0.02                 # anchura de los anillos del perfil (fracción del radio)
RHO_MAX = 0.98                  # más allá el limbo está emborronado por el seeing
CIELO = (1.12, 1.40)            # anillo de cielo junto al Sol (radios)
CALIMA_MAX = 0.08               # cielo/centro por encima de esto: la luz difusa altera el perfil
ANGULOS_MIN = 45                # direcciones con limbo necesarias para dar el radio (un cuarto del contorno)
ANGULOS_ELIPSE = 120            # ...y para ajustar la elipse (dos tercios del contorno)
SATURADO = 0.97                 # fracción del máximo de 16 bits a partir de la cual el centro está saturado
DESVIO_ESCALA_MAX = 0.02        # la escala que implica el Sol se aparta más de esto: ¿otro telescopio?
AU_KM = 149597870.7
ANIO_DIAS = 365.25636           # año anomalístico (de perihelio a perihelio)
ESPAN_ORBITA_DIAS = 120         # con menos días de fotos no se ajusta la órbita
TAUS = [round(0.2 + 0.05 * i, 2) for i in range(17)]          # 0,2 … 1,0

_lock = threading.Lock()
TRABAJO = {"en_curso": False, "hechas": 0, "total": 0, "mensaje": "", "ok": None}


class ErrorFotometria(Exception):
    """No se puede medir esta foto (el texto dice por qué)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Física
# ─────────────────────────────────────────────────────────────────────────────
def t_eddington(tau, teff=TEFF):
    """Temperatura del modelo gris de Eddington a la profundidad óptica τ: T⁴ = ¾·Teff⁴·(τ + ⅔)."""
    return teff * (0.75 * (np.asarray(tau, dtype=float) + 2.0 / 3.0)) ** 0.25


def temperatura_eddington_barbier(cociente, lam_um, t1=None):
    """
    T(τ = μ) a partir de I(μ)/I(1) en la longitud de onda `lam_um`, con T(τ = 1) = t1 (Eddington si
    no se da): B(T)/B(T1) = cociente → T = C2 / (λ · ln(1 + (e^{C2/λT1} − 1) / cociente)).
    """
    t1 = float(t_eddington(1.0)) if t1 is None else float(t1)
    c = np.clip(np.asarray(cociente, dtype=float), 1e-4, None)
    x1 = C2_UM_K / (lam_um * t1)
    x = np.log1p(np.expm1(x1) / c)
    return C2_UM_K / (lam_um * x)


def ley_limbo(mu, u, v):
    one = 1.0 - np.asarray(mu, dtype=float)
    return 1.0 - u * one - v * one * one


def mu_de_rho(rho):
    return np.sqrt(np.clip(1.0 - np.asarray(rho, dtype=float) ** 2, 0.0, 1.0))


def semidiametro_geocentrico(instantes):
    """Semidiámetro del Sol (″) visto desde el centro de la Tierra en los instantes unix dados."""
    from astropy.coordinates import get_body
    from astropy.time import Time
    import astropy.units as u
    import disco
    t = Time(np.atleast_1d(np.asarray(instantes, dtype=float)), format="unix")
    dist = get_body("sun", t).distance.to(u.km).value
    return np.degrees(np.arcsin(disco.RADIO_KM["sol"] / dist)) * 3600.0


def distancia_ua(semidiametro_arcsec):
    """Distancia Tierra-Sol (ua) que corresponde a un semidiámetro aparente."""
    import disco
    return disco.RADIO_KM["sol"] / math.tan(math.radians(float(semidiametro_arcsec) / 3600.0)) / AU_KM


# ─────────────────────────────────────────────────────────────────────────────
#  Medir sobre los planos de color (función pura: la usan la galería y las pruebas)
# ─────────────────────────────────────────────────────────────────────────────
def _medianas_por_anillo(rho, planos, paso=PASO_RHO, hasta=CIELO[1]):
    """Mediana de cada plano en anillos de `paso` radios hasta `hasta` (un solo ordenado para todos)."""
    idx = (rho / paso).astype(np.int32)
    n = int(math.ceil(hasta / paso))
    sel = idx < n
    idx = idx[sel]
    orden = np.argsort(idx, kind="stable")
    idx = idx[orden]
    cortes = np.searchsorted(idx, np.arange(n + 1))
    salida = {}
    for nombre, p in planos.items():
        v = p[sel][orden]
        salida[nombre] = np.array([float(np.median(v[a:b])) if b - a >= 12 else np.nan for a, b in zip(cortes[:-1], cortes[1:])])
    centros = (np.arange(n) + 0.5) * paso
    cuenta = np.diff(cortes)
    return centros, salida, cuenta


def _ajustar_circulo(xs, ys):
    A = np.c_[2 * xs, 2 * ys, np.ones(len(xs))]
    sol, *__ = np.linalg.lstsq(A, xs * xs + ys * ys, rcond=None)
    cx, cy = float(sol[0]), float(sol[1])
    return cx, cy, float(math.sqrt(max(sol[2] + cx * cx + cy * cy, 1.0)))


def localizar_limbo(plano):
    """
    {cx, cy, r, a, b, angulo_menor, fwhm, n} en px del plano: el círculo ajustado a los puntos de
    inflexión del limbo; con limbo en al menos dos tercios del contorno, también la elipse (semiejes a
    y b, dirección del eje menor en grados desde +x hacia +y), que mide el achatamiento por la
    refracción; la anchura mediana de la caída y cuántas direcciones tienen limbo. ErrorFotometria si
    no se ve el disco.
    """
    import enfoque
    c = enfoque.circulo_disco(plano, reduccion=2 if max(plano.shape) > 1500 else 1)
    if c is None:
        raise ErrorFotometria(_("No se ve el disco del Sol en la foto"))
    cx, cy, r = c
    anch, angs, radios = np.array([]), np.array([]), np.array([])
    for paso in range(3):                                    # el centro afinado mueve los perfiles: tres pasadas
        anch, angs, radios = enfoque.anchura_limbo(plano, cx, cy, r)
        if len(radios) < ANGULOS_MIN or paso == 2:
            break
        cx, cy, r = _ajustar_circulo(cx + radios * np.cos(angs), cy + radios * np.sin(angs))
    salida = {"cx": cx, "cy": cy, "r": r, "a": None, "b": None, "angulo_menor": None,
              "fwhm": float(np.median(anch)) if len(anch) else None, "n": int(len(anch))}
    if len(radios) >= ANGULOS_ELIPSE:
        # r(θ) = r0 + (desplazamiento del centro) + A·cos(2θ − φ): una elipse casi circular
        A = np.c_[np.ones_like(angs), np.cos(angs), np.sin(angs), np.cos(2 * angs), np.sin(2 * angs)]
        coef, *__ = np.linalg.lstsq(A, radios, rcond=None)
        amp = math.hypot(coef[3], coef[4])
        salida.update({"a": float(coef[0] + amp), "b": float(coef[0] - amp),
                       "angulo_menor": (math.degrees(math.atan2(coef[4], coef[3])) / 2.0 + 90.0) % 180.0})
    return salida


def refraccion_arcmin(h_aparente):
    """Refracción atmosférica (′) a la altura aparente h (°), fórmula de Bennett con 10 °C y 1010 hPa."""
    return 1.0 / math.tan(math.radians(h_aparente + 7.31 / (h_aparente + 4.4)))


def achatamiento_refraccion(altura):
    """
    Achatamiento vertical del disco (fracción) que produce la refracción con el Sol a `altura` (°,
    verdadera): el borde de abajo se levanta más que el de arriba, −d(refracción)/dh.
    """
    if altura < 2.0:
        return None
    h = altura + refraccion_arcmin(altura) / 60.0
    return (refraccion_arcmin(h - 0.1) - refraccion_arcmin(h + 0.1)) / 60.0 / 0.2


def altura_del_sol(instante, lat, lon):
    from astropy.coordinates import EarthLocation, AltAz, get_body
    from astropy.time import Time
    import astropy.units as u
    t = Time(float(instante), format="unix")
    lugar = EarthLocation(lat=float(lat) * u.deg, lon=float(lon) * u.deg)
    return float(get_body("sun", t, lugar).transform_to(AltAz(obstime=t, location=lugar)).alt.deg)


def medir_planos(planos, px_por_plano=1.0):
    """
    Oscurecimiento del limbo y temperaturas en los planos de color dados ({"R": 2D, "G": 2D, "B": 2D}
    o {"L": 2D}), ya sin el nivel de negro. px_por_plano = píxeles del crudo por píxel del plano (2 en
    superpíxel). Devuelve el disco (en píxeles del crudo), el perfil medido, el ajuste y las
    temperaturas de cada canal, la calima y los avisos.
    """
    nombres = [c for c in ("R", "G", "B", "L") if c in planos]
    guia = planos["G"] if "G" in planos else planos[nombres[0]]
    d = localizar_limbo(guia)
    h, w = guia.shape
    paso_muestra = 2 if h * w > 4_000_000 else 1                 # el apilado de 3008 px: basta uno de cada cuatro
    yy, xx = np.mgrid[0:h:paso_muestra, 0:w:paso_muestra].astype(np.float32)
    rho = np.hypot(xx - d["cx"], yy - d["cy"]) / d["r"]
    sub = {c: np.asarray(planos[c], dtype=np.float32)[::paso_muestra, ::paso_muestra] for c in nombres}
    centros, medianas, cuenta = _medianas_por_anillo(rho, sub)
    avisos = []
    en_cielo = (centros >= CIELO[0]) & (centros < CIELO[1]) & (cuenta >= 12)
    en_disco = centros <= RHO_MAX
    perfil, ajuste, temps, calima = {"rho": [round(float(x), 3) for x in centros[en_disco]]}, {}, {}, {}
    for c in nombres:
        m = medianas[c]
        cielo = float(np.nanmedian(m[en_cielo])) if np.any(en_cielo) and np.any(np.isfinite(m[en_cielo])) else None
        if cielo is None:
            cielo = 0.0
            if "sin_cielo" not in avisos:
                avisos.append("sin_cielo")
        centro = float(np.nanmedian(m[centros < 0.1]))
        if not math.isfinite(centro) or centro - cielo <= 0:
            raise ErrorFotometria(_("El disco del Sol sale demasiado oscuro para medirlo"))
        rel = (m[en_disco] - cielo) / (centro - cielo)
        perfil[c] = [None if not math.isfinite(x) else round(float(x), 4) for x in rel]
        ok = np.isfinite(rel)
        mu = mu_de_rho(centros[en_disco][ok])
        A = np.c_[1.0 - mu, (1.0 - mu) ** 2]
        (u, v), *__ = np.linalg.lstsq(A, 1.0 - rel[ok], rcond=None)
        rms = float(np.sqrt(np.mean((rel[ok] - ley_limbo(mu, u, v)) ** 2)))
        ajuste[c] = {"u": round(float(u), 4), "v": round(float(v), 4), "rms": round(rms, 4),
                     "i95": round(float(ley_limbo(mu_de_rho(0.95), u, v)), 4), "i50": round(float(ley_limbo(0.5, u, v)), 4)}
        lam = LAMBDA_EF_UM[c]
        temps[c] = [round(float(t)) for t in temperatura_eddington_barbier(ley_limbo(TAUS, u, v), lam)]
        calima[c] = round(cielo / centro, 4) if "sin_cielo" not in avisos else None
    cal_g = calima.get("G", calima.get(nombres[0]))
    if cal_g is not None and cal_g > CALIMA_MAX:
        avisos.append("calima")
    t03 = {c: temps[c][TAUS.index(0.3)] for c in nombres}
    k = px_por_plano
    return {"canales": nombres, "perfil": perfil, "ajuste": ajuste, "calima": cal_g,
            "temperatura": {"tau": TAUS, **temps, "eddington": [round(float(t)) for t in t_eddington(TAUS)],
                            "t1": round(float(t_eddington(1.0))), "t03": t03,
                            "dispersion03": round(max(t03.values()) - min(t03.values())) if len(t03) > 1 else 0},
            "disco": {"cx": round(d["cx"] * k, 2), "cy": round(d["cy"] * k, 2), "r": round(d["r"] * k, 3),
                      "a": round(d["a"] * k, 3) if d["a"] else None, "b": round(d["b"] * k, 3) if d["b"] else None,
                      "angulo_menor": round(d["angulo_menor"], 1) if d["angulo_menor"] is not None else None,
                      "fwhm": round(d["fwhm"] * k, 2) if d["fwhm"] else None, "n": d["n"]},
            "avisos": avisos}


# ─────────────────────────────────────────────────────────────────────────────
#  El crudo de una foto de la galería
# ─────────────────────────────────────────────────────────────────────────────
def planos_del_crudo(nombre):
    """
    (planos {"R","G","B"} o {"L"} sin el negro, píxeles del crudo por píxel de plano, cabecera,
    saturado) de la foto de la galería. ErrorFotometria si no tiene crudo.
    """
    import editor_dia
    from astropy.io import fits
    hdr = editor_dia.cabecera(nombre)
    if hdr is None:
        raise ErrorFotometria(_("Esta foto no tiene su imagen en crudo guardada: el brillo solo se puede medir sobre el crudo."))
    modo = str(hdr.get("MODO", "")).strip().lower()
    with fits.open(editor_dia.ruta_crudo(nombre)) as hdul:
        datos = np.asarray(hdul[0].data)
    if modo == "video":
        if datos.ndim != 3 or datos.shape[0] != 3:
            raise ErrorFotometria(_("El FITS de esta foto no es un apilado en color"))
        planos = {c: datos[i].astype(np.float32) for i, c in enumerate("RGB")}
        return planos, 1.0, hdr, False                       # normalizado al blanco al guardarlo: no se sabe si saturó
    import foto_dia
    import vivo_dia
    zona = foto_dia.recortar_centro(datos, float(hdr.get("CAMPO") or 1.0))
    negro = float(hdr.get("NEGRO") or 0.0)
    patron = str(hdr.get("BAYERPAT") or "").strip().upper()
    if patron in ("RGGB", "BGGR", "GRBG", "GBRG"):
        rgb = vivo_dia.superpixel_rgb(zona, patron) - negro   # un píxel por bloque 2×2: canales puros
        planos, k = {c: np.ascontiguousarray(rgb[..., i]) for i, c in enumerate("RGB")}, 2.0
    else:
        planos, k = {"L": zona.astype(np.float32) - negro}, 1.0
    p = float(np.percentile(zona[::4, ::4], 99.9))
    return planos, k, hdr, p >= SATURADO * 65535.0


def escala_de_placa(hdr, ruta_config="config.ini"):
    """
    (segundos de arco por píxel del crudo, origen): la del último plate solving (orientacion_camara)
    o, si no hay, la de la focal de Configuración y el píxel de la cabecera. (None, "") si ninguna.
    """
    binning = int(hdr.get("XBINNING") or 1)
    try:
        import orientacion_camara
        o = orientacion_camara.ultima()
    except Exception:
        o = None
    if o and o.get("escala_arcsec"):
        return float(o["escala_arcsec"]) / float(o.get("binning_foto") or 1) * binning, "plate_solving"
    cfg = configparser.ConfigParser()
    cfg.read(ruta_config)
    try:
        focal = float(cfg.get("Dispositivos", "focal_len", fallback="0"))
        px = float(hdr.get("XPIXSZ") or 0.0)
    except ValueError:
        focal, px = 0.0, 0.0
    if focal > 0 and px > 0:
        return 206.264806 * px / focal, "focal"
    return None, ""


def medir_foto(nombre, observatorio=None):
    """Todas las medidas de una foto del Sol de la galería (sin caché)."""
    import luna_anotada
    from editor_fotos import rutas
    __, ruta_jpg, __, __ = rutas(nombre)
    meta = luna_anotada.meta_de(ruta_jpg)
    planos, k, hdr, saturado = planos_del_crudo(nombre)
    t0 = time.time()
    r = medir_planos(planos, k)
    if saturado:
        r["avisos"].append("saturado")
    instante = luna_anotada.instante_de(nombre, meta)
    r["instante"] = instante
    r["fecha"] = datetime.fromtimestamp(instante).replace(microsecond=0).isoformat() if instante else ""
    escala, origen = escala_de_placa(hdr)
    diam = None
    if r["disco"]["n"] >= ANGULOS_MIN and instante:
        lat, lon = meta.get("latitud"), meta.get("longitud")
        if lat is None or lon is None:
            lat, lon = (observatorio or (0.0, 0.0))[:2]
        import disco
        r_efem = disco.radio_esperado_arcsec("sol", instante, float(lat), float(lon), 0.0)
        # El radio es el semieje mayor: la refracción aplasta el disco en vertical (medido el 10-oct-2026
        # con el Sol a 15°: 0,4 %, lo previsto) y casi no toca el diámetro horizontal
        dd = r["disco"]
        elipse = dd["a"] is not None
        r_px = dd["a"] if elipse else dd["r"]
        diam = {"r_px": r_px, "medida": "elipse" if elipse else "circulo", "r_efem": round(r_efem, 2),
                "escala_implicita": round(r_efem / r_px, 5), "distancia_efem": round(distancia_ua(r_efem), 5),
                "escala": None, "escala_origen": origen, "achatamiento": None, "achatamiento_previsto": None, "altura": None}
        if elipse:
            diam["achatamiento"] = round((dd["a"] - dd["b"]) / dd["a"] * 100.0, 3)
        if meta.get("latitud") is not None or observatorio:
            try:
                alt = altura_del_sol(instante, lat, lon)
                prev = achatamiento_refraccion(alt)
                diam.update({"altura": round(alt, 1), "achatamiento_previsto": round(prev * 100.0, 3) if prev is not None else None})
            except Exception as e:
                log.info("Fotometría del Sol: sin altura para %s: %s", nombre, e)
        if escala:
            r_arc = r_px * escala
            diam.update({"escala": round(escala, 5), "r_arcsec": round(r_arc, 2), "desvio_pct": round((r_arc / r_efem - 1.0) * 100.0, 3),
                         "distancia": round(distancia_ua(r_arc), 5),
                         "seeing_arcsec": round(r["disco"]["fwhm"] * escala, 1) if r["disco"]["fwhm"] else None})
            if abs(r_arc / r_efem - 1.0) > DESVIO_ESCALA_MAX:
                r["avisos"].append("escala_dudosa")
    elif r["disco"]["n"] < ANGULOS_MIN:
        r["avisos"].append("disco_cortado")
    r["diametro"] = diam
    r["nombre"] = nombre
    r["version"] = VERSION
    log.info("Fotometría del Sol en %s: radio %.2f px, I(0,95R) %s, T(τ=0,3) %s, calima %s%s, %.1f s", nombre, r["disco"]["r"],
             {c: a["i95"] for c, a in r["ajuste"].items()}, r["temperatura"]["t03"], r["calima"],
             f", diámetro {diam.get('desvio_pct')} % de la efeméride" if diam and diam.get("r_arcsec") else "", time.time() - t0)
    return r


# ─── Caché en datos/medidas_sol.json ─────────────────────────────────────────
def _clave_crudo(nombre):
    import editor_dia
    try:
        est = os.stat(editor_dia.ruta_crudo(nombre))
    except OSError:
        return None
    return f"{est.st_size}_{est.st_mtime_ns}"


def _leer():
    try:
        with open(RUTA_MEDIDAS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d.get("medidas"), dict) else {"medidas": {}}
    except (OSError, ValueError):
        return {"medidas": {}}


def _escribir(d):
    os.makedirs(os.path.dirname(RUTA_MEDIDAS) or ".", exist_ok=True)
    tmp = RUTA_MEDIDAS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, RUTA_MEDIDAS)


def guardada(nombre):
    """La medida guardada de la foto si sigue valiendo (mismo crudo, misma versión), o None."""
    clave = _clave_crudo(nombre)
    with _lock:
        m = _leer()["medidas"].get(nombre)
    if m and m.get("clave") == clave and m.get("version") == VERSION:
        return m
    return None


def medir(nombre, observatorio=None, forzar=False):
    """Las medidas de la foto: las guardadas o, si no hay o cambió el crudo, medidas ahora y guardadas."""
    if not forzar:
        m = guardada(nombre)
        if m is not None:
            return m
    r = medir_foto(nombre, observatorio)
    r["clave"] = _clave_crudo(nombre)
    with _lock:
        d = _leer()
        d["medidas"][nombre] = r
        _escribir(d)
    return r


def pendientes(nombres):
    """De las fotos del Sol con crudo dadas, las que aún no tienen medida válida."""
    with _lock:
        medidas = _leer()["medidas"]
    salida = []
    for n in nombres:
        m = medidas.get(n)
        if not m or m.get("version") != VERSION or m.get("clave") != _clave_crudo(n):
            salida.append(n)
    return salida


def medir_varias(nombres, observatorio=None):
    """Mide en segundo plano las fotos dadas; el progreso, en TRABAJO."""
    if TRABAJO["en_curso"]:
        raise RuntimeError(_("Ya se están midiendo fotos del Sol"))
    nombres = list(nombres)
    TRABAJO.update({"en_curso": True, "hechas": 0, "total": len(nombres), "ok": None, "mensaje": ""})

    def _trabajo():
        fallos = 0
        try:
            for n in nombres:
                try:
                    medir(n, observatorio)
                except Exception as e:
                    fallos += 1
                    log.info("Fotometría del Sol: %s sin medir: %s", n, e)
                TRABAJO["hechas"] += 1
            TRABAJO.update({"ok": True, "mensaje": ngettext("{n} foto medida", "{n} fotos medidas", len(nombres) - fallos)})
        except Exception as e:
            log.exception("Error midiendo las fotos del Sol")
            TRABAJO.update({"ok": False, "mensaje": str(e)})
        finally:
            TRABAJO["en_curso"] = False

    threading.Thread(target=_trabajo, name="fotometria-solar", daemon=True).start()
    return dict(TRABAJO)


# ─────────────────────────────────────────────────────────────────────────────
#  La serie del año: diámetro frente a la efeméride y la órbita de la Tierra
# ─────────────────────────────────────────────────────────────────────────────
def serie(nombres=None):
    """Las fotos medidas con diámetro en segundos de arco, por fecha (solo las de `nombres` si se da)."""
    with _lock:
        medidas = _leer()["medidas"]
    salida = []
    for n, m in medidas.items():
        if nombres is not None and n not in nombres:
            continue
        d = m.get("diametro") or {}
        if not d.get("r_arcsec") or not m.get("instante"):
            continue
        salida.append({"foto": n, "fecha": m["fecha"], "instante": m["instante"], "r_arcsec": d["r_arcsec"], "r_efem": d["r_efem"],
                       "desvio_pct": d["desvio_pct"], "distancia": d["distancia"], "distancia_efem": d["distancia_efem"],
                       "fiable": not ({"calima", "saturado", "escala_dudosa"} & set(m.get("avisos") or [])),
                       "i95": {c: a["i95"] for c, a in (m.get("ajuste") or {}).items()}, "calima": m.get("calima")})
    return sorted(salida, key=lambda s: s["instante"])


def curva_anual(serie_):
    """Semidiámetro de la efeméride (″) cada 5 días durante los años naturales que abarca la serie."""
    if not serie_:
        return []
    a0 = datetime.fromtimestamp(serie_[0]["instante"]).year
    a1 = datetime.fromtimestamp(serie_[-1]["instante"]).year
    t0, t1 = datetime(a0, 1, 1).timestamp(), datetime(a1 + 1, 1, 1).timestamp()
    ts = np.arange(t0, t1 + 1, 5 * 86400.0)
    return [{"instante": float(t), "r": round(float(r), 2)} for t, r in zip(ts, semidiametro_geocentrico(ts))]


def ajustar_orbita(serie_):
    """
    Con fotos fiables de al menos ESPAN_ORBITA_DIAS días: el semidiámetro es r(t) ≈ r0·(1 + e·cos(ω(t − t_p)))
    (órbita casi circular, ω del año anomalístico); un ajuste lineal de r0 + B·cos ωt + C·sin ωt da la
    excentricidad e = √(B² + C²)/r0 y la fecha del perihelio. None si no hay datos suficientes.
    """
    s = [x for x in serie_ if x["fiable"]]
    if len(s) < 6:
        return None
    t = np.array([x["instante"] for x in s]) / 86400.0
    if t.max() - t.min() < ESPAN_ORBITA_DIAS:
        return None
    r = np.array([x["r_arcsec"] for x in s])
    w = 2 * math.pi / ANIO_DIAS
    A = np.c_[np.ones_like(t), np.cos(w * t), np.sin(w * t)]
    (r0, b, c), *__ = np.linalg.lstsq(A, r, rcond=None)
    e = math.hypot(b, c) / r0
    fase = math.atan2(c, b) / w                   # día (desde 1970) del máximo de la curva
    perihelio = datetime.fromtimestamp(fase * 86400.0)
    hoy = datetime.now()
    # El perihelio del año en curso (los de cualquier año caen en la misma fecha ±1 día)
    perihelio = datetime(hoy.year, 1, 1) + (perihelio - datetime(perihelio.year, 1, 1))
    return {"e": round(e, 5), "r0": round(float(r0), 2), "perihelio": perihelio.date().isoformat(),
            "n": len(s), "dias": round(float(t.max() - t.min()))}
