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


# ═════════════════════════════════════════════════════════════════════════════
# Orientación respecto a los ejes, desde una resolución de ASTAP
# ═════════════════════════════════════════════════════════════════════════════
def _observatorio(ruta_config="config.ini"):
    cfg = configparser.ConfigParser()
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
    H, alt, az = matriz_horizontal(wcs, t, lat, lon, alt_m, centro=((ancho - 1) / 2.0, (alto - 1) / 2.0))
    return {
        "H": H.tolist(), "binning_foto": binning, "ancho": ancho, "alto": alto,
        "escala_arcsec": round(float(escala), 4),
        "angulo_x_deg": round(math.degrees(math.atan2(H[1, 0], H[0, 0])) % 360.0, 2),
        "paridad": int(np.sign(np.linalg.det(H))),
        "alt": round(alt, 2), "az": round(az, 2),
        "instante_utc": t.isot, "fecha": datetime.now().isoformat(timespec="seconds"), "origen": origen,
    }


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


def ultima(buscar=True):
    """La orientación guardada; si no hay, la de la resolución más reciente que quede en disco."""
    o = leer()
    if o is not None or not buscar:
        return o
    candidatos = [r for r in INIS_CONOCIDOS if os.path.exists(r)]
    for ruta in sorted(candidatos, key=os.path.getmtime, reverse=True):
        o = registrar(ruta, origen=f"resolución guardada ({os.path.basename(ruta)})")
        if o is not None:
            return o
    return None


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
