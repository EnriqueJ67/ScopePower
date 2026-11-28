# orientacion_camara.py
#
# Orientación de la cámara respecto a los ejes de la montura altacimutal,
# sacada de cualquier plate solving (centrado, alineación, apilado o foto de
# prueba), para calibrar el seguimiento de objetos sin mover la montura.
#
# En una montura altacimutal la cámara va fija al tubo: respecto a los ejes
# (acimut y altura) su orientación no cambia nunca mientras no se toque la
# cámara; lo único que gira es el cielo (el ángulo paraláctico). Por eso el giro
# de la cámara respecto al norte (CROTA2) cambia de un apuntado a otro, pero
# convertido al marco horizontal da siempre lo mismo. Comprobado el 2026-09-24
# con las 9 resoluciones de una noche real (alineación de 6 puntos, dos
# centrados y el apilado): CROTA2 de 27° a 310°, y el eje x de la imagen a
# 353,6–357,1° del acimut en todas (±2°, lo que dan los errores de apuntado).
#
# El cálculo es numérico, sin fórmulas de signos que equivocar: se toman dos
# píxeles vecinos del centro de la foto resuelta, se pasan a cielo con el WCS
# de ASTAP y de cielo a acimut/altura (astropy) en el instante de la foto. Eso
# da la matriz H (grados en el plano tangente horizontal, Δaz·cos(alt) y Δalt,
# por píxel de la foto), que se guarda en datos/orientacion_camara_altaz.json.
#
# Las fotos resueltas salen de ASCOM y el seguimiento lee la cámara por el SDK
# nativo: la PlayerOne entrega por ASCOM el ImageArray [x][y] (traspuesto
# respecto al búfer del SDK). La relación entre las dos (una de las 8 simetrías
# del cuadrado) se da por "traspuesta" y se puede comprobar con una foto de
# cada camino de la misma escena (comparar_ascom_sdk); el resultado queda en
# datos/orientacion_sdk.json.

import os
import json
import math
import glob
import logging
import configparser
from datetime import datetime, timezone

import numpy as np

log = logging.getLogger("scopepower.orientacion_camara")

RUTA = os.path.join("datos", "orientacion_camara_altaz.json")
RUTA_SDK = os.path.join("datos", "orientacion_sdk.json")
# Resoluciones que quedan en disco (para recuperar la orientación si no hay ninguna guardada)
INIS_CONOCIDOS = ("static/captura_mono.ini", "static/alineacion_mono.ini", "static/centrado_mono.ini")

# Montura ecuatorial (28-nov-2026, Sol con la montura en ecuatorial): la cámara va fija
# respecto a AR/Dec, no respecto al horizonte, así que la H horizontal de una foto
# resuelta solo vale en ese instante y en esa dirección. Con el Sol de ese día la H de
# la noche del 26 estaba girada 75° (el ángulo paraláctico): «Apunta aquí» llevaba el
# Sol a otro sitio y cada corrección del guiado giraba el error ~50° y lo agrandaba
# (2,1′ → 2,8′ → 3,6′ → 4,4′ → 4,9′) hasta perderlo. Con una ecuatorial se guarda
# también H_eq (fija) y ultima() rehace la H horizontal para el instante y la dirección
# de uso. ScopePower dice el modo al conectar y cómo leer dónde apunta la montura.
MODOS_ECUATORIALES = ("ecuatorial", "ecuatorial_alemana")
_estado = {"modo": None, "apuntado": None}


def establecer_modo_montura(modo):
    """«altacimutal», «ecuatorial», «ecuatorial_alemana» o None (ASCOM AlignmentMode al conectar)."""
    _estado["modo"] = modo


def establecer_apuntado(funcion):
    """
    `funcion()` → {"ra": grados ICRS, "dec": grados, "lado": SideOfPier o None} de la
    montura, o None si no se puede leer. Sirve para rehacer la H en una ecuatorial.
    """
    _estado["apuntado"] = funcion


def _apuntado_montura():
    f = _estado["apuntado"]
    if f is None:
        return {}
    try:
        return f() or {}
    except Exception as e:
        log.debug("Sin la posición de la montura para la orientación: %s", e)
        return {}


def es_ecuatorial(modo):
    return modo in MODOS_ECUATORIALES


# ═════════════════════════════════════════════════════════════════════════════
# Orientación respecto a los ejes, desde una resolución de ASTAP
# ═════════════════════════════════════════════════════════════════════════════
def _observatorio(ruta_config="config.ini"):
    cfg = configparser.ConfigParser(strict=False)
    cfg.read(ruta_config, encoding="utf-8")
    o = cfg["Observatorio"]
    return (float(o.get("latitud_observatorio")), float(o.get("longitud_observatorio")),
            float(o.get("altitud_observatorio", "0") or 0))


def instante_de_la_foto(ruta_fits):
    """
    Instante (astropy Time, UTC) de la foto: DATE-OBS si está (la app lo escribe
    en hora local "AAAAMMDD_HHMMSS"; el estándar FITS es ISO en UTC), si no la
    hora de modificación del FITS (se escribe justo tras la exposición).
    """
    from astropy.time import Time
    try:
        from astropy.io import fits
        valor = str(fits.getheader(ruta_fits).get("DATE-OBS") or "").strip()
    except Exception:
        valor = ""
    if valor:
        try:
            if "_" in valor and len(valor) == 15:          # formato propio de la app, hora local
                local = datetime.strptime(valor, "%Y%m%d_%H%M%S").astimezone()
                return Time(local.astimezone(timezone.utc).replace(tzinfo=None), scale="utc")
            return Time(valor.replace(" ", "T"), format="isot", scale="utc")
        except Exception:
            pass
    try:
        return Time(os.path.getmtime(ruta_fits), format="unix")
    except OSError:
        return Time.now()


def matriz_horizontal(wcs, t, lat, lon, alt_m=0.0, centro=(1503.5, 1503.5), paso=40.0):
    """
    H (2×2) tal que (Δaz·cos alt, Δalt) en grados = H · (Δcolumna, Δfila) en
    píxeles de la foto resuelta, alrededor de `centro` (origen 0, como el WCS de
    anotaciones.wcs_desde_ini). Devuelve (H, alt, az) del centro.
    """
    from astropy.coordinates import SkyCoord, EarthLocation, AltAz
    import astropy.units as u
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    cx, cy = centro
    pts = wcs.all_pix2world([[cx, cy], [cx + paso, cy], [cx, cy + paso]], 0)
    h = SkyCoord(ra=pts[:, 0] * u.deg, dec=pts[:, 1] * u.deg).transform_to(AltAz(obstime=t, location=lugar))
    az, alt = h.az.deg, h.alt.deg
    ca = math.cos(math.radians(alt[0]))
    columna = lambda i: [((az[i] - az[0] + 180.0) % 360.0 - 180.0) * ca / paso, (alt[i] - alt[0]) / paso]
    H = np.array([columna(1), columna(2)]).T
    return H, float(alt[0]), float(az[0])


def matriz_ecuatorial(wcs, centro=(1503.5, 1503.5), paso=40.0):
    """
    H_eq (2×2) tal que (ΔAR·cos Dec, ΔDec) en grados = H_eq · (Δcolumna, Δfila) en
    píxeles de la foto resuelta. Con una montura ecuatorial la cámara va fija respecto
    a estos ejes (no respecto al horizonte). Devuelve (H_eq, ra, dec) del centro.
    """
    cx, cy = centro
    pts = wcs.all_pix2world([[cx, cy], [cx + paso, cy], [cx, cy + paso]], 0)
    ra, dec = pts[:, 0], pts[:, 1]
    cd = math.cos(math.radians(dec[0]))
    columna = lambda i: [((ra[i] - ra[0] + 180.0) % 360.0 - 180.0) * cd / paso, (dec[i] - dec[0]) / paso]
    return np.array([columna(1), columna(2)]).T, float(ra[0]), float(dec[0])


def jacobiano_horizontal(ra, dec, t, lugar, paso=0.05):
    """
    J (2×2): (Δaz·cos alt, Δalt) = J · (ΔAR·cos Dec, ΔDec), en grados, alrededor de la
    dirección ICRS (ra, dec) en el instante t (astropy Time) desde `lugar` (EarthLocation).
    Es el giro del ángulo paraláctico (con su espejo: el AR crece hacia el este), numérico.
    """
    from astropy.coordinates import SkyCoord, AltAz
    import astropy.units as u
    cd = max(1e-6, math.cos(math.radians(dec)))
    pts = SkyCoord(ra=[ra, ra + paso / cd, ra] * u.deg, dec=[dec, dec, dec + paso] * u.deg, frame="icrs")
    h = pts.transform_to(AltAz(obstime=t, location=lugar))
    az, alt = h.az.deg, h.alt.deg
    ca = math.cos(math.radians(alt[0]))
    columna = lambda i: [((az[i] - az[0] + 180.0) % 360.0 - 180.0) * ca / paso, (alt[i] - alt[0]) / paso]
    return np.array([columna(1), columna(2)]).T


def desde_solucion(ruta_ini, ruta_fits=None, observatorio=None, origen=""):
    """Orientación (dict serializable) de una foto resuelta por ASTAP; None si no se resolvió."""
    import anotaciones
    res = anotaciones.wcs_desde_ini(ruta_ini)
    if res is None:
        return None
    wcs, escala, _ = res
    ruta_fits = ruta_fits or os.path.splitext(ruta_ini)[0] + ".fits"
    ancho = alto = None
    binning = 1
    try:
        from astropy.io import fits
        cab = fits.getheader(ruta_fits)
        ancho, alto = int(cab.get("NAXIS1")), int(cab.get("NAXIS2"))
        binning = int(cab.get("XBINNING") or 1)
    except Exception:
        pass
    if ancho is None:
        crpix = wcs.wcs.crpix
        ancho, alto = int(round(2 * crpix[0])), int(round(2 * crpix[1]))
    lat, lon, alt_m = observatorio or _observatorio()
    t = instante_de_la_foto(ruta_fits)
    centro = ((ancho - 1) / 2.0, (alto - 1) / 2.0)
    H, alt, az = matriz_horizontal(wcs, t, lat, lon, alt_m, centro=centro)
    H_eq, ra, dec = matriz_ecuatorial(wcs, centro=centro)
    o = {
        "H": H.tolist(), "binning_foto": binning, "ancho": ancho, "alto": alto,
        "escala_arcsec": round(float(escala), 4),
        "angulo_x_deg": round(math.degrees(math.atan2(H[1, 0], H[0, 0])) % 360.0, 2),
        "paridad": int(np.sign(np.linalg.det(H))),
        "alt": round(alt, 2), "az": round(az, 2),
        "H_eq": H_eq.tolist(), "ra": round(ra, 5), "dec": round(dec, 5),
        "instante_utc": t.isot, "fecha": datetime.now().isoformat(timespec="seconds"), "origen": origen,
    }
    if _estado["modo"]:
        o["modo_montura"] = _estado["modo"]
    lado = _apuntado_montura().get("lado")
    if lado is not None:
        o["lado_pilar"] = lado
    return o


def guardar(orientacion, ruta=RUTA):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(orientacion, f, ensure_ascii=False, indent=1)


def leer(ruta=RUTA):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def registrar(ruta_ini, origen=""):
    """
    Lo llama rotacion_camara.anotar_ini tras cada resolución que sale bien.
    Guarda la orientación respecto a los ejes; nunca lanza (no debe estropear
    el centrado ni el apilado).
    """
    try:
        o = desde_solucion(ruta_ini, origen=origen)
        if o is None:
            return None
        anterior = leer()
        guardar(o)
        if anterior is None or abs(((o["angulo_x_deg"] - anterior.get("angulo_x_deg", 0) + 180) % 360) - 180) > 1.0:
            log.info("Orientación de la cámara respecto a los ejes: eje x de la imagen a %.1f° del acimut, "
                     "paridad %+d, %.2f\"/px (%s, altura %.0f°)", o["angulo_x_deg"], o["paridad"],
                     o["escala_arcsec"], origen, o["alt"])
        return o
    except Exception as e:
        log.warning("No se pudo calcular la orientación de la cámara desde %s: %s", ruta_ini, e)
        return None


def ultima(buscar=True, instante=None, apuntado=None):
    """
    La orientación guardada; si no hay, la de la resolución más reciente que quede en disco.

    Con una montura ecuatorial, su "H" es la del instante `instante` (unix, astropy Time o
    None = ahora) y la dirección `apuntado` ((ra, dec) ICRS en grados; None = donde apunta
    la montura), sacada de la H_eq fija de la cámara. Si no se sabe dónde apunta, la H de
    la foto resuelta (y "aviso" lo dice).
    """
    o = leer()
    if o is None and buscar:
        candidatos = [r for r in INIS_CONOCIDOS if os.path.exists(r)]
        for ruta in sorted(candidatos, key=os.path.getmtime, reverse=True):
            o = registrar(ruta, origen=f"resolución guardada ({os.path.basename(ruta)})")
            if o is not None:
                break
    if o is None:
        return None
    modo = _estado["modo"] or o.get("modo_montura")
    if not es_ecuatorial(modo):
        return o
    try:
        return para_ecuatorial(o, instante=instante, apuntado=apuntado)
    except Exception as e:
        if str(e) != _estado.get("ultimo_aviso"):       # una vez por motivo: las páginas la piden a menudo
            log.warning("No se pudo rehacer la orientación de la cámara para la montura ecuatorial "
                        "(se usa la de la foto resuelta): %s", e)
            _estado["ultimo_aviso"] = str(e)
        return dict(o, aviso=str(e))


def _como_time(instante):
    from astropy.time import Time
    if instante is None:
        return Time.now()
    if isinstance(instante, Time):
        return instante
    return Time(float(instante), format="unix")


_cache_h_eq = {}


def matriz_ecuatorial_de(o, observatorio=None):
    """
    H_eq de una orientación: la guardada o, en las guardadas antes del 28-nov-2026, la
    que sale de su H horizontal en el instante y la dirección de la foto resuelta (es
    la misma foto, así que es exacta).
    """
    if o.get("H_eq"):
        return np.array(o["H_eq"], dtype=float)
    clave = (o.get("instante_utc"), o.get("alt"), o.get("az"), str(o.get("H")))
    if clave in _cache_h_eq:
        return _cache_h_eq[clave]
    from astropy.coordinates import SkyCoord, EarthLocation, AltAz
    from astropy.time import Time
    import astropy.units as u
    lat, lon, alt_m = observatorio or _observatorio()
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    t = Time(str(o["instante_utc"]).replace(" ", "T"), format="isot", scale="utc")
    c = SkyCoord(az=float(o["az"]) * u.deg, alt=float(o["alt"]) * u.deg, frame=AltAz(obstime=t, location=lugar)).icrs
    H_eq = np.linalg.inv(jacobiano_horizontal(float(c.ra.deg), float(c.dec.deg), t, lugar)) @ np.array(o["H"], dtype=float)
    _cache_h_eq[clave] = H_eq
    return H_eq


def para_ecuatorial(o, instante=None, apuntado=None, observatorio=None):
    """
    Copia de la orientación `o` con la H horizontal del instante y la dirección dados,
    para una cámara fija respecto a AR/Dec (montura ecuatorial). Si la montura ha pasado
    al otro lado del pilar desde la foto resuelta, la cámara está girada 180°.
    """
    from astropy.coordinates import EarthLocation
    import astropy.units as u
    lado = None
    if apuntado is None:
        m = _apuntado_montura()
        if m.get("ra") is None:
            raise ValueError("no se sabe dónde apunta la montura")
        apuntado, lado = (m["ra"], m["dec"]), m.get("lado")
    else:
        lado = _apuntado_montura().get("lado") if o.get("lado_pilar") is not None else None
    H_eq = matriz_ecuatorial_de(o, observatorio)
    girada = lado is not None and o.get("lado_pilar") is not None and int(lado) != int(o["lado_pilar"])
    if girada:
        H_eq = -H_eq
    lat, lon, alt_m = observatorio or _observatorio()
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    t = _como_time(instante)
    ra, dec = float(apuntado[0]), float(apuntado[1])
    H = jacobiano_horizontal(ra, dec, t, lugar) @ H_eq
    nueva = dict(o)
    nueva.update({"H": H.tolist(), "H_resuelta": o["H"], "marco": "ecuatorial", "pilar_girado": girada,
                  "angulo_x_deg": round(math.degrees(math.atan2(H[1, 0], H[0, 0])) % 360.0, 2),
                  "paridad": int(np.sign(np.linalg.det(H)))})
    return nueva


# ═════════════════════════════════════════════════════════════════════════════
# Relación entre la imagen de ASCOM (la que se resuelve) y la del SDK nativo
# ═════════════════════════════════════════════════════════════════════════════
# Las 8 simetrías del cuadrado, como operaciones sobre la imagen del SDK que la
# llevan a la orientación de la de ASCOM
TRANSFORMACIONES = {
    "identidad": lambda f: f,
    "rot90": lambda f: np.rot90(f, 1),
    "rot180": lambda f: np.rot90(f, 2),
    "rot270": lambda f: np.rot90(f, 3),
    "traspuesta": lambda f: f.T,
    "traspuesta_rot90": lambda f: np.rot90(f.T, 1),
    "traspuesta_rot180": lambda f: np.rot90(f.T, 2),
    "traspuesta_rot270": lambda f: np.rot90(f.T, 3),
}
POR_DEFECTO = "traspuesta"    # ASCOM ImageArray[x][y] frente al búfer fila a fila del SDK


def matriz_transformacion(nombre):
    """
    A (2×2): desplazamiento en la imagen de ASCOM (Δcolumna, Δfila) = A ·
    desplazamiento en la del SDK (Δcolumna, Δfila). Se deduce aplicando la
    operación a imágenes de índices, sin razonar signos a mano.
    """
    op = TRANSFORMACIONES[nombre]
    n = 4
    filas, cols = np.mgrid[0:n, 0:n]
    oc, of = op(cols), op(filas)                  # en cada píxel de salida, de qué columna/fila del SDK viene
    # salida → origen (lineal): origen = B · salida
    B = np.array([[oc[0, 1] - oc[0, 0], oc[1, 0] - oc[0, 0]],
                  [of[0, 1] - of[0, 0], of[1, 0] - of[0, 0]]], dtype=float)
    return np.linalg.inv(B)


def leer_sdk(ruta=RUTA_SDK):
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("transformacion") in TRANSFORMACIONES:
            return d
    except (OSError, ValueError):
        pass
    return {"transformacion": POR_DEFECTO, "verificada": False}


def guardar_sdk(d, ruta=RUTA_SDK):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def _normalizada(img, lado=188):
    import cv2
    f = np.asarray(img, dtype=np.float32)
    f = cv2.resize(f, (lado, lado), interpolation=cv2.INTER_AREA)
    f = cv2.GaussianBlur(f, (0, 0), 1.0)
    f -= f.mean()
    d = f.std()
    return f / d if d > 0 else f


def comparar_ascom_sdk(img_ascom, img_sdk):
    """
    Correlación normalizada de la imagen del SDK, en cada una de las 8
    orientaciones, con la de ASCOM (las dos del mismo campo; tamaños
    cualesquiera, se reducen a 188 px). Devuelve [(nombre, correlación)] de
    mayor a menor.
    """
    a = _normalizada(img_ascom)
    res = []
    for nombre, op in TRANSFORMACIONES.items():
        b = _normalizada(op(np.asarray(img_sdk)))
        res.append((nombre, float((a * b).mean())))
    res.sort(key=lambda x: -x[1])
    return res


def bayer_conservado(frame):
    """
    ¿El fotograma crudo conserva el mosaico Bayer? Compara la media de las
    cuatro subredes 2×2: con color (escena no gris) difieren; si el binning ha
    mezclado los colores son casi iguales. Devuelve (conservado, medias).
    """
    f = np.asarray(frame, dtype=np.float64)
    h, w = f.shape[0] & ~1, f.shape[1] & ~1
    medias = [float(f[i:h:2, j:w:2].mean()) for i in (0, 1) for j in (0, 1)]
    m = max(1e-6, float(np.mean(medias)))
    dispersion = (max(medias) - min(medias)) / m
    return dispersion > 0.08, [round(x, 1) for x in medias], round(dispersion, 3)


# ═════════════════════════════════════════════════════════════════════════════
# Matriz para el seguimiento
# ═════════════════════════════════════════════════════════════════════════════
def matriz_sensor_sdk(orientacion, sdk=None):
    """
    Hs (2×2): (Δaz·cos alt, Δalt) en grados = Hs · desplazamiento en píxeles del
    sensor en los ejes de la imagen del SDK (columna, fila), a binning 1.
    """
    sdk = sdk or leer_sdk()
    H = np.array(orientacion["H"], dtype=float)
    A = matriz_transformacion(sdk["transformacion"])
    return H @ A / float(orientacion.get("binning_foto", 1) or 1)


if __name__ == "__main__":
    # Comprobaciones puras de las simetrías
    for nombre, op in TRANSFORMACIONES.items():
        img = np.zeros((6, 6))
        img[1, 4] = 1.0                         # fila 1, columna 4 en la del SDK
        out = op(img)
        r, c = np.argwhere(out == 1.0)[0]
        img2 = np.zeros((6, 6))
        img2[2, 4] = 1.0                        # una fila más abajo
        r2, c2 = np.argwhere(op(img2) == 1.0)[0]
        A = matriz_transformacion(nombre)
        assert np.allclose(A @ np.array([0, 1]), [c2 - c, r2 - r]), nombre
    assert np.allclose(matriz_transformacion("traspuesta"), [[0, 1], [1, 0]])
    assert np.allclose(matriz_transformacion("identidad"), np.eye(2))
    # La comparación recupera la simetría aplicada a una escena cualquiera
    rng = np.random.default_rng(0)
    escena = np.cumsum(np.cumsum(rng.normal(size=(300, 300)), 0), 1)
    for nombre, op in TRANSFORMACIONES.items():
        # imagen del SDK tal que op(sdk) = escena  ⇒  sdk = inversa(op)(escena): buscar la inversa por fuerza bruta
        for n2, op2 in TRANSFORMACIONES.items():
            if np.array_equal(op(op2(np.arange(16).reshape(4, 4))), np.arange(16).reshape(4, 4)):
                sdk = op2(escena)
                break
        assert comparar_ascom_sdk(escena, sdk)[0][0] == nombre, nombre
    print("✅ orientacion_camara: simetrías y comparación correctas")
