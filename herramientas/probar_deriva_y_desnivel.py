# herramientas/probar_deriva_y_desnivel.py
#
# Prueba sintética de los dos avisos que salieron de la Nebulosa Creciente del 7-nov-2026
# (estrellas «en peladilla»: la montura derivaba 6 px en acimut dentro de cada foto de 20 s,
# con el trípode desnivelado y el objeto a 73° de altura), sin montura, cámara ni ASTAP:
#
#   Forma de las estrellas (alargamiento.py)
#   - Estrellas redondas, con un trazo a lo largo del eje x, inclinado, y con una aberración
#     que alarga cada estrella hacia un lado distinto: alargamiento, dirección y largo del trazo.
#   - Sin estrellas, o con muy pocas, no se inventa una forma.
#   - Exposición sugerida: la que dejaría el trazo en casi nada, redondeada hacia abajo.
#   - El aviso con las medidas reales de dos noches: la de la Burbuja del 10-oct (buena) no
#     avisa nunca; la de la Creciente avisa desde la tercera foto y no se apaga.
#
#   Desnivel del trípode (desnivel.py)
#   - Montura inclinada de mentira: de cada apuntado se construye el WCS que daría ASTAP y se
#     pasa por orientacion_camara.matriz_horizontal, como hace la app; el ajuste recupera
#     cuánto se inclina el eje y hacia dónde, también con el ruido de una noche real.
#   - Trípode nivelado: no avisa. Pocos puntos, o todos hacia el mismo lado: no se pronuncia.
#   - Los puntos reales de la alineación del 7-nov: 1,2° hacia el suroeste, y avisa.
#   - TrabajoAlineacion: el texto que llega a la web y a la voz, y que en una montura
#     ecuatorial no se mide (la cámara gira respecto al horizonte por diseño).
#
#   python herramientas\probar_deriva_y_desnivel.py
#
# Sale con código 0 si todo pasa y 1 si no.

import math
import os
import sys

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

from astropy.utils import iers                          # noqa: E402
iers.conf.auto_download = False                         # sin Internet en GitHub ni en el campo
iers.conf.auto_max_age = None
from astropy.coordinates import SkyCoord, EarthLocation, AltAz    # noqa: E402
from astropy.time import Time                           # noqa: E402
from astropy.wcs import WCS                             # noqa: E402
import astropy.units as u                               # noqa: E402

import alargamiento                                     # noqa: E402
import desnivel                                         # noqa: E402
import orientacion_camara                               # noqa: E402

FALLOS = []


def comprobar(condicion, texto):
    print(("  ok   " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


# ═════════════════════════════════════════════════════════════════════════════
# Forma de las estrellas
# ═════════════════════════════════════════════════════════════════════════════
SIGMA = 1.4          # px: estrellas de 3,3 px de anchura, como las de la Saturn-C con el Evoguide


def campo(trazo=0.0, angulo=0.0, n=80, lado=700, radial=False, semilla=1):
    """
    Foto sintética (float32, ADU) con `n` estrellas gaussianas. `trazo` (px) las arrastra a lo
    largo de `angulo` (grados, 0 = eje x); con `radial`, cada una se arrastra hacia el centro
    de la foto (una aberración de campo: ninguna dirección común).
    """
    rng = np.random.default_rng(semilla)
    img = np.full((lado, lado), 800.0, dtype=np.float64)
    yy, xx = np.mgrid[-12:13, -12:13].astype(np.float64)
    puestas = []
    while len(puestas) < n:
        x, y = rng.uniform(30, lado - 30, 2)
        if all(math.hypot(x - a, y - b) > 30 for a, b in puestas):
            puestas.append((x, y))
    for x, y in puestas:
        flujo = rng.uniform(40000, 120000)
        a = math.atan2(y - lado / 2, x - lado / 2) if radial else math.radians(angulo)
        ix, iy = int(round(x)), int(round(y))
        sello = np.zeros_like(xx)
        pasos = max(1, int(round(trazo * 4)))
        for k in range(pasos):
            d = (k + 0.5) / pasos * trazo - trazo / 2.0
            cx, cy = x - ix + d * math.cos(a), y - iy + d * math.sin(a)
            sello += np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * SIGMA ** 2))
        img[iy - 12:iy + 13, ix - 12:ix + 13] += flujo * sello / pasos / (2 * math.pi * SIGMA ** 2)
    img += rng.normal(0, 12, img.shape)
    return np.clip(img, 0, 65535).astype(np.float32)


# Alargamiento (eje mayor / eje menor) de cada foto medida en las dos noches de las que hay
# fotos sueltas, con alargamiento.medir sobre el verde de cada FITS. Noche buena: la Burbuja
# del 10-oct-2026 (50 fotos; su apilado salió bien).
BUENA = [1.31, 1.61, 1.61, 1.65, 1.30, 1.39, 1.38, 1.45, 1.05, 1.12, 1.26, 1.17, 1.19, 1.51, 1.62, 1.45,
         1.70, 1.84, 1.38, 1.02, 1.20, 1.11, 1.18, 1.15, 1.10, 1.10, 1.47, 1.55, 1.62, 1.70, 1.44, 1.12,
         1.22, 1.22, 1.14, 1.09, 1.11, 1.05, 1.09, 1.20, 1.85, 1.66, 1.57, 1.47, 1.26, 1.51, 1.31, 1.08,
         1.23, 1.23]
# Noche mala: la Creciente del 7-nov-2026 (51 fotos): (alargamiento, trazo en px, anchura del eje menor en px)
MALA = [(1.57, 5.8, 3.3), (1.72, 6.7, 3.3), (1.73, 7.5, 3.6), (1.75, 6.6, 3.1), (2.03, 8.1, 3.1), (1.85, 8.3, 3.6),
        (1.71, 8.4, 4.1), (2.22, 9.2, 3.2), (1.91, 8.0, 3.4), (1.93, 9.1, 3.7), (1.45, 5.7, 3.7), (1.81, 6.7, 3.0),
        (1.44, 5.7, 3.7), (2.77, 12.8, 3.4), (1.14, 2.7, 3.4), (1.54, 5.8, 3.4), (1.47, 5.8, 3.6), (1.65, 7.0, 3.6),
        (2.30, 9.3, 3.1), (1.93, 7.6, 3.1), (1.81, 6.5, 2.9), (1.53, 5.9, 3.5), (1.71, 7.5, 3.6), (2.20, 8.1, 2.8),
        (1.56, 6.3, 3.6), (1.34, 4.9, 3.8), (1.33, 4.2, 3.2), (2.55, 9.7, 2.8), (2.08, 7.6, 2.8), (2.04, 6.3, 2.4),
        (1.52, 5.7, 3.4), (1.88, 6.7, 2.8), (2.29, 8.5, 2.8), (1.77, 7.7, 3.6), (2.18, 9.8, 3.4), (2.84, 10.5, 2.7),
        (2.56, 10.4, 3.0), (1.45, 4.1, 2.7), (2.14, 8.7, 3.1), (2.39, 8.5, 2.7), (2.49, 9.2, 2.8), (1.83, 8.2, 3.6),
        (2.49, 9.7, 2.9), (2.40, 10.4, 3.2), (2.99, 12.5, 3.0), (1.96, 8.6, 3.5), (2.11, 12.2, 4.5), (2.49, 10.2, 3.0),
        (1.59, 6.1, 3.4), (2.44, 10.2, 3.1), (2.55, 10.6, 3.1)]


def probar_forma():
    print("\nForma de las estrellas en fotos sintéticas")
    m = alargamiento.medir(campo())
    comprobar(m is not None and m["n"] >= 50, f"estrellas redondas: {m['n'] if m else 0} medidas")
    comprobar(m["alargamiento"] < 1.08, f"redondas: alargamiento {m['alargamiento']:.2f} (< 1,08)")
    comprobar(abs(m["fwhm_menor"] - 2.355 * SIGMA) < 0.25, f"anchura {m['fwhm_menor']:.2f} px (puestas con {2.355 * SIGMA:.2f})")
    comprobar(m["trazo_px"] < 1.5, f"redondas: trazo {m['trazo_px']:.1f} px (< 1,5)")
    comprobar(3.0 < m["fwhm"] < 4.5, f"la anchura de siempre (enfoque.fwhm) sigue saliendo: {m['fwhm']:.2f} px")

    previsto = math.sqrt(1.0 + 8.0 ** 2 / 12.0 / SIGMA ** 2)
    m = alargamiento.medir(campo(trazo=8.0, angulo=0.0))
    # Un trazo recto no es una gaussiana: el peso adaptativo lo ve algo más largo (un 15 % con 8 px)
    comprobar(previsto - 0.05 < m["alargamiento"] < 1.2 * previsto, f"trazo de 8 px en el eje x: alargamiento {m['alargamiento']:.2f} "
              f"(momentos de un trazo recto: {previsto:.2f}; hasta un 20 % más)")
    comprobar(8.0 <= m["trazo_px"] < 9.6, f"largo del trazo {m['trazo_px']:.1f} px (puesto 8; hasta un 20 % más)")
    comprobar(abs(m["angulo_deg"]) < 4.0, f"dirección {m['angulo_deg']:+.1f}° (puesto 0°)")
    comprobar(abs(m["fwhm_menor"] - 2.355 * SIGMA) < 0.3, f"el eje menor no cambia: {m['fwhm_menor']:.2f} px")

    m = alargamiento.medir(campo(trazo=6.0, angulo=40.0))
    comprobar(abs(m["angulo_deg"] - 40.0) < 4.0, f"trazo de 6 px a 40°: dirección {m['angulo_deg']:+.1f}°")
    comprobar(6.0 <= m["trazo_px"] < 7.2, f"largo del trazo {m['trazo_px']:.1f} px (puesto 6; hasta un 20 % más)")
    m = alargamiento.medir(campo(trazo=6.0, angulo=-70.0))
    comprobar(abs(m["angulo_deg"] + 70.0) < 4.0, f"trazo a −70°: dirección {m['angulo_deg']:+.1f}°")

    m = alargamiento.medir(campo(trazo=6.0, radial=True))
    comprobar(m["alargamiento"] < 1.15, f"cada estrella alargada hacia un lado distinto (aberración): no cuenta como deriva "
              f"({m['alargamiento']:.2f} < 1,15)")

    rng = np.random.default_rng(3)
    vacia = (800 + rng.normal(0, 12, (400, 400))).astype(np.float32)
    comprobar(alargamiento.medir(vacia) is None, "foto sin estrellas: None")
    m = alargamiento.medir(campo(n=3, lado=300))
    comprobar(m is not None and math.isfinite(m["fwhm"]) and math.isnan(m["alargamiento"]),
              "con 3 estrellas se da la anchura pero no una forma común")

    print("\nExposición sugerida")
    comprobar(alargamiento.exposicion_sugerida(8.0, 3.25, 20.0) == 6, f"8 px en 20 s con estrellas de 3,25 px: "
              f"{alargamiento.exposicion_sugerida(8.0, 3.25, 20.0)} s (6)")
    comprobar(alargamiento.exposicion_sugerida(10.2, 3.0, 20.0) == 4, f"10 px en 20 s: {alargamiento.exposicion_sugerida(10.2, 3.0, 20.0)} s (4)")
    comprobar(alargamiento.exposicion_sugerida(2.5, 3.25, 20.0) is None, "2,5 px en 20 s: nada que ganar")
    comprobar(alargamiento.exposicion_sugerida(60.0, 3.0, 2.0) is None, "trazo enorme con 2 s: no hay exposición que valga")
    comprobar(alargamiento.exposicion_sugerida(0.0, 3.0, 20.0) is None and alargamiento.exposicion_sugerida(8.0, 3.0, 0) is None,
              "sin trazo o sin exposición: None")

    print("\nAviso con las medidas de dos noches reales")
    aviso = alargamiento.Aviso()
    eventos = [e for e in (aviso.anadir({"alargamiento": a, "trazo_px": 3.0, "fwhm_menor": 2.5}, 20.0) for a in BUENA) if e]
    comprobar(eventos == [], f"Burbuja del 10-oct (mediana {np.median(BUENA):.2f}, ratos de 1,6): no avisa nunca {eventos}")
    comprobar(aviso.estado()["aviso"] is False and aviso.estado()["exposicion_sugerida"] is None, "y el estado lo dice")

    aviso = alargamiento.Aviso()
    eventos = [(i + 1, e) for i, (a, t, w) in enumerate(MALA)
               for e in [aviso.anadir({"alargamiento": a, "trazo_px": t, "fwhm_menor": w}, 20.0)] if e]
    comprobar(eventos == [(3, "empieza")], f"Creciente del 7-nov (mediana {np.median([m[0] for m in MALA]):.2f}): avisa en la "
              f"tercera foto y no se apaga {eventos}")
    e = aviso.estado()
    comprobar(e["aviso"] and e["fotos"] == alargamiento.VENTANA and e["reciente"] > 2.0 and 4 <= e["exposicion_sugerida"] <= 6,
              f"estado al final: ×{e['reciente']}, trazo {e['trazo_px']} px, probar con {e['exposicion_sugerida']} s")

    # Si después se arregla (exposición más corta), el aviso se quita, y una sola foto movida no lo enciende
    for __ in range(alargamiento.VENTANA):
        ultimo = aviso.anadir({"alargamiento": 1.15, "trazo_px": 1.5, "fwhm_menor": 3.0}, 5.0)
    comprobar(not aviso.activo, "con fotos redondas el aviso se apaga")
    aviso = alargamiento.Aviso()
    eventos = [e for e in (aviso.anadir({"alargamiento": a, "trazo_px": 3.0, "fwhm_menor": 3.0}, 20.0)
                           for a in [1.1, 1.2, 1.1, 3.0, 1.2, 1.1, 2.8, 1.2, 1.1, 1.2]) if e]
    comprobar(eventos == [], "dos fotos movidas sueltas (viento) no encienden el aviso")
    aviso = alargamiento.Aviso()
    eventos = [e for e in (aviso.anadir({"alargamiento": a, "trazo_px": t, "fwhm_menor": w}, 20.0, umbral=0) for a, t, w in MALA) if e]
    comprobar(eventos == [], "con aviso_alargamiento = 0 no se avisa")
    aviso = alargamiento.Aviso()
    comprobar(aviso.anadir(None, 20.0) is None and aviso.anadir({"alargamiento": float("nan")}, 20.0) is None and aviso.estado() is None,
              "una foto sin medida no cuenta")
    aviso.anadir({"alargamiento": 2.0, "trazo_px": 8.0, "fwhm_menor": 3.0}, 20.0)
    aviso.reiniciar()
    comprobar(aviso.estado() is None and not aviso.activo, "reiniciar (otro objeto) lo deja en blanco")


# ═════════════════════════════════════════════════════════════════════════════
# Desnivel del trípode
# ═════════════════════════════════════════════════════════════════════════════
LAT, LON, ALTITUD = 39.5, -0.4, 70.0
LUGAR = EarthLocation(lat=LAT * u.deg, lon=LON * u.deg, height=ALTITUD * u.m)
AHORA = Time("2026-11-07T18:35:00", scale="utc")
ESCALA = 2.91 / 3600.0            # grados por píxel
CENTRO = (1503.5, 1503.5)


def _vector(az, alt):
    """Unitario (este, norte, arriba) de un acimut y una altura en grados."""
    a, h = math.radians(az), math.radians(alt)
    return np.array([math.cos(h) * math.sin(a), math.cos(h) * math.cos(a), math.sin(h)])


def _altaz(v):
    return math.degrees(math.atan2(v[0], v[1])) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))


def angulo_medido(az, alt, inclinacion, hacia, giro_camara=357.0, espejo=1):
    """
    Lo que mediría la app en una foto tomada apuntando a (az, alt) con una montura cuyo eje de
    acimut se inclina `inclinacion` grados hacia el acimut `hacia`: se construye el WCS de la
    foto (la cámara, fija al tubo, con su eje x a `giro_camara` grados del «acimut de la
    montura») y se pasa por orientacion_camara.matriz_horizontal. Devuelve (az, alt, ángulo).
    """
    eje = _vector(hacia, 90.0 - inclinacion)                  # el polo de la montura
    s = _vector(az, alt)
    acimut_montura = np.cross(s, eje)                         # hacia donde crece el acimut de la montura
    acimut_montura /= np.linalg.norm(acimut_montura)
    altura_montura = eje - eje.dot(s) * s
    altura_montura /= np.linalg.norm(altura_montura)
    g = math.radians(giro_camara)
    eje_x = math.cos(g) * acimut_montura + math.sin(g) * altura_montura
    eje_y = espejo * (-math.sin(g) * acimut_montura + math.cos(g) * altura_montura)
    paso = 40.0
    puntos = [s, s + math.radians(paso * ESCALA) * eje_x, s + math.radians(paso * ESCALA) * eje_y]
    horizontales = [_altaz(p / np.linalg.norm(p)) for p in puntos]
    icrs = SkyCoord(az=[h[0] for h in horizontales] * u.deg, alt=[h[1] for h in horizontales] * u.deg,
                    frame=AltAz(obstime=AHORA, location=LUGAR)).icrs
    ra, dec = np.radians(icrs.ra.deg), np.radians(icrs.dec.deg)
    # Coordenadas estándar (plano tangente en el centro) de los dos puntos vecinos, en grados
    d = np.sin(dec[0]) * np.sin(dec[1:]) + np.cos(dec[0]) * np.cos(dec[1:]) * np.cos(ra[1:] - ra[0])
    xi = np.degrees(np.cos(dec[1:]) * np.sin(ra[1:] - ra[0]) / d)
    eta = np.degrees((np.cos(dec[0]) * np.sin(dec[1:]) - np.sin(dec[0]) * np.cos(dec[1:]) * np.cos(ra[1:] - ra[0])) / d)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [icrs.ra.deg[0], icrs.dec.deg[0]]
    w.wcs.crpix = [CENTRO[0] + 1, CENTRO[1] + 1]
    w.wcs.cd = np.array([[xi[0] / paso, xi[1] / paso], [eta[0] / paso, eta[1] / paso]])
    H, alt_m, az_m = orientacion_camara.matriz_horizontal(w, AHORA, LAT, LON, ALTITUD, centro=CENTRO)
    return az_m, alt_m, math.degrees(math.atan2(H[1, 0], H[0, 0])) % 360.0


# El recorrido de la alineación de 6 puntos: tres acimuts a 65° y tres a 35° de altura
RECORRIDO = [(65, 62), (65, 182), (65, 302), (35, 302), (35, 182), (35, 62)]
# Lo que la app anotó en el log la noche del 7-nov-2026 (acimut, altura, ángulo del eje x de
# la foto respecto al acimut): los 5 puntos resueltos de la alineación y, después, M29 y la
# Creciente, ya cerca del cénit
ALINEACION_7_NOV = [(62, 64, 359.6), (182, 63, 358.5), (302, 64, 2.8), (302, 34, 0.7), (182, 35, 358.1)]
ALTOS_7_NOV = [(265.8, 82.8, 10.0), (266.0, 82.7, 11.6), (267.8, 81.2, 9.8), (271.2, 76.4, 5.6)]


def _dif(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def probar_desnivel():
    print("\nDesnivel del trípode con una montura inclinada de mentira")
    az, alt, ang = angulo_medido(120.0, 50.0, 0.0, 0.0)
    comprobar(_dif(ang, 357.0) < 0.05 and _dif(az, 120.0) < 0.01 and abs(alt - 50.0) < 0.01,
              f"montura nivelada: la app mide el giro de la cámara tal cual ({ang:.2f}° a az {az:.1f}° alt {alt:.1f}°)")
    for inclinacion, hacia, espejo in ((1.5, 220.0, 1), (0.8, 40.0, 1), (2.5, 300.0, -1), (1.2, 135.0, -1)):
        puntos = [angulo_medido(a, h, inclinacion, hacia, espejo=espejo) for h, a in RECORRIDO]
        r = desnivel.ajustar(puntos)
        # El modelo es de primer orden en la inclinación: con 2,5° se queda un 4 % corto
        comprobar(r is not None and abs(r["inclinacion_deg"] - inclinacion) < max(0.08, 0.05 * inclinacion) and _dif(r["hacia_az"], hacia) < 4.0
                  and _dif(r["giro_camara_deg"], 357.0) < 0.1,
                  f"eje inclinado {inclinacion}° hacia {hacia:.0f}° ({'espejo' if espejo < 0 else 'sin espejo'}): "
                  f"sale {r['inclinacion_deg']}° hacia {r['hacia_az']:.0f}° ({desnivel.rumbo(r['hacia_az'])}), cámara a {r['giro_camara_deg']}°")
    # Con 1,5° hacia el suroeste, un objeto al oeste a 82° de altura gira 10°: lo que se vio en M29
    r = desnivel.ajustar([angulo_medido(a, h, 1.5, 220.0) for h, a in RECORRIDO])
    __, __, alto = angulo_medido(266.0, 82.0, 1.5, 220.0)
    previsto = desnivel.angulo_previsto(266.0, 82.0, r["giro_camara_deg"], r["inclinacion_deg"], r["hacia_az"])
    comprobar(_dif(alto, previsto) < 1.5 and _dif(alto, 357.0) > 6.0,
              f"cerca del cénit el giro se dispara y el modelo lo predice: {(_dif(alto, 357.0)):.1f}° a 82° de altura "
              f"(previsto {(_dif(previsto, 357.0)):.1f}°)")

    rng = np.random.default_rng(7)
    errores, direcciones = [], []
    for __ in range(200):
        puntos = [(a, h, g + rng.normal(0, 0.6)) for a, h, g in (angulo_medido(a, h, 1.5, 220.0) for h, a in RECORRIDO)]
        r = desnivel.ajustar(puntos)
        errores.append(r["inclinacion_deg"] - 1.5)
        direcciones.append(_dif(r["hacia_az"], 220.0))
    comprobar(np.std(errores) < 0.45 and np.percentile(direcciones, 90) < 30.0,
              f"con 0,6° de ruido por punto (una noche real): ±{np.std(errores):.2f}° en la inclinación, dirección a "
              f"{np.percentile(direcciones, 90):.0f}° como mucho en 9 de cada 10")

    avisos = 0
    for __ in range(200):
        puntos = [(a, h, g + rng.normal(0, 0.6)) for a, h, g in (angulo_medido(a, h, 0.0, 0.0) for h, a in RECORRIDO)]
        avisos += desnivel.hay_que_avisar(desnivel.ajustar(puntos), 1.0)
    comprobar(avisos <= 4, f"trípode nivelado con el mismo ruido: {avisos} avisos falsos en 200 alineaciones (≤ 4)")

    print("\nCasos sin respuesta")
    comprobar(desnivel.ajustar([angulo_medido(a, h, 1.5, 220.0) for h, a in RECORRIDO[:3]]) is None, "con 3 puntos no se ajusta")
    comprobar(desnivel.ajustar([angulo_medido(100.0, h, 1.5, 220.0) for h in (30, 40, 50, 60, 70)]) is None,
              "todos los puntos al mismo acimut: no se distingue del giro de la cámara")
    comprobar(desnivel.ajustar([]) is None and not desnivel.hay_que_avisar(None, 1.0) and not desnivel.es_de_fiar(None), "sin puntos")
    r = desnivel.ajustar([angulo_medido(a, h, 1.5, 220.0) for h, a in RECORRIDO])
    comprobar(not desnivel.hay_que_avisar(r, 0.0) and desnivel.hay_que_avisar(r, 1.0) and not desnivel.hay_que_avisar(r, 2.0),
              "el umbral manda: 0 = no avisar, 1° avisa con 1,5°, 2° no")
    comprobar([desnivel.rumbo(a) for a in (0, 44, 90, 135, 180, 232, 270, 315, 350)] == ["N", "NE", "E", "SE", "S", "SO", "O", "NO", "N"],
              "rumbos")

    print("\nLa noche del 7-nov-2026")
    r = desnivel.ajustar(ALINEACION_7_NOV)
    comprobar(r is not None and 0.9 < r["inclinacion_deg"] < 1.5 and desnivel.rumbo(r["hacia_az"]) == "SO",
              f"con los 5 puntos de la alineación: {r['inclinacion_deg']}° ± {r['incertidumbre_deg']}° hacia el {desnivel.rumbo(r['hacia_az'])}")
    comprobar(desnivel.hay_que_avisar(r, 1.0), "y se habría avisado al acabar la alineación, antes de apuntar a M29")
    previstos = [desnivel.angulo_previsto(a, h, r["giro_camara_deg"], r["inclinacion_deg"], r["hacia_az"]) % 360.0 for a, h, __ in ALTOS_7_NOV]
    comprobar(all(2.0 < _dif(p, r["giro_camara_deg"]) for p in previstos),
              "predice que M29 y la Creciente saldrían giradas: " + ", ".join(f"{_dif(p, 0):.0f}°" for p in previstos)
              + " (se midieron " + ", ".join(f"{g:.0f}°" for __, __, g in ALTOS_7_NOV) + ")")
    r9 = desnivel.ajustar(ALINEACION_7_NOV + ALTOS_7_NOV)
    comprobar(1.3 < r9["inclinacion_deg"] < 2.0 and desnivel.rumbo(r9["hacia_az"]) in ("S", "SO"),
              f"con los 9 puntos de la noche: {r9['inclinacion_deg']}° hacia el {desnivel.rumbo(r9['hacia_az'])}")


class MonturaFalsa:
    """Lo justo para TrabajoAlineacion._medir_desnivel: montura.ejecutar(f) con un telescopio que dice su modo."""

    def __init__(self, modo=0):
        self.AlignmentMode = modo

    def ejecutar(self, f, timeout=None):
        return f(self)


def probar_alineacion():
    print("\nEl aviso al terminar la alineación")
    import alineacion
    trabajo = alineacion.TrabajoAlineacion()
    trabajo.orientaciones = list(ALINEACION_7_NOV)
    texto = trabajo._medir_desnivel(MonturaFalsa(), {"aviso_desnivel": 1.0})
    estado = trabajo.estado()["desnivel"]
    comprobar(bool(texto) and "1,2" in texto and "suroeste" in texto, f"7-nov: «{texto}»")
    comprobar(estado is not None and estado["aviso"] and estado["texto"] == texto and estado["n"] == 5, "llega a la web con el mismo texto")

    trabajo = alineacion.TrabajoAlineacion()
    trabajo.orientaciones = [angulo_medido(a, h, 0.2, 90.0) for h, a in RECORRIDO]
    texto = trabajo._medir_desnivel(MonturaFalsa(), {"aviso_desnivel": 1.0})
    estado = trabajo.estado()["desnivel"]
    comprobar(texto is None and estado is not None and not estado["aviso"] and "nivelado" in estado["texto"],
              f"trípode casi nivelado: no se dice por voz y la web pone «{estado['texto']}»")

    trabajo = alineacion.TrabajoAlineacion()
    trabajo.orientaciones = list(ALINEACION_7_NOV)
    comprobar(trabajo._medir_desnivel(MonturaFalsa(), {"aviso_desnivel": 0}) is None and not trabajo.estado()["desnivel"]["aviso"],
              "con aviso_desnivel = 0 se mide pero no se avisa")
    trabajo = alineacion.TrabajoAlineacion()
    trabajo.orientaciones = list(ALINEACION_7_NOV)
    comprobar(trabajo._medir_desnivel(MonturaFalsa(modo=2), {"aviso_desnivel": 1.0}) is None and trabajo.estado()["desnivel"] is None,
              "montura ecuatorial: no se mide")
    trabajo = alineacion.TrabajoAlineacion()
    trabajo.orientaciones = list(ALINEACION_7_NOV[:2])
    comprobar(trabajo._medir_desnivel(MonturaFalsa(), {"aviso_desnivel": 1.0}) is None and trabajo.estado()["desnivel"] is None,
              "con dos puntos resueltos no se pronuncia")


def main():
    probar_forma()
    probar_desnivel()
    probar_alineacion()
    if FALLOS:
        print(f"\n❌ {len(FALLOS)} comprobaciones fallidas")
        return 1
    print("\n✅ Avisos de deriva en el apilado y de trípode desnivelado correctos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
