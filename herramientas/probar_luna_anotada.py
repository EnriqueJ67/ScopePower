# herramientas/probar_luna_anotada.py
#
# Prueba de la Luna anotada (luna_anotada.py) sin fotos reales ni Internet:
#   1. La geometría (libración, punto subsolar, eje, fase) contra las efemérides de JPL
#      Horizons para Greenwich en tres fechas (valores copiados de Horizons el 20-oct-2026).
#   2. Lunas sintéticas (el mapa de albedo con la iluminación de cada fecha) en varias fases,
#      con tamaño, posición, giro y espejo al azar: se tienen que recuperar el disco (al 1,5 %
#      del radio), el giro (a 1°) y el espejo.
#   3. Los accidentes: los de una foto salen dentro del disco, con los grandes primero, y
#      Tycho cae donde debe; el terminador y los contornos de los mares salen bien formados.
#
# Uso:
#   python herramientas\probar_luna_anotada.py

import os
import sys
import math
import time
from datetime import datetime, timezone

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RAIZ)

import luna_anotada as L          # noqa: E402

# Horizons (observador en Greenwich, 0° E 51,4769° N, 50 m): fracción iluminada, libración
# (lon, lat), punto subsolar (lon, lat) en longitudes este 0..360
HORIZONS = [
    ("2026-10-20", 0.6208649, 355.058066, 2.487662, 70.992566, -1.329104),
    ("2026-10-25", 0.9800584, 354.215866, -4.390306, 10.194278, -1.387223),
    ("2026-10-30", 0.8035932, 2.042521, -5.054116, 309.453449, -1.438742),
]

fallos = []


def comprobar(condicion, texto):
    print(("  OK   " if condicion else "  FALLO ") + texto)
    if not condicion:
        fallos.append(texto)


def dif_angulo(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def pintar(geo, ancho, alto, cx, cy, r, giro, espejo, semilla=0):
    """Luna sintética en una imagen uint8 de ancho×alto, con el disco y la orientación dados."""
    import cv2
    yy, xx = np.mgrid[0:alto, 0:ancho].astype(np.float64)
    Xr, Yr = (xx - cx) / r, -(yy - cy) / r
    g = math.radians(giro)
    m = -1.0 if espejo else 1.0
    X = math.cos(g) * Xr + math.sin(g) * Yr
    Y = m * (-math.sin(g) * Xr + math.cos(g) * Yr)
    dentro = X * X + Y * Y < 1
    img = np.zeros((alto, ancho), np.float32)
    f = L._desde_vista(geo, X[dentro], Y[dentro])
    lat = np.degrees(np.arcsin(np.clip(f[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(f[:, 1], f[:, 0]))
    mapa = L._albedo()
    h, w = mapa.shape
    a = mapa[np.clip(((90 - lat) / 180 * h).astype(int), 0, h - 1), ((lon + 180) / 360 * w).astype(int) % w]
    fi = f @ geo["R"]
    ci = np.clip(fi @ geo["s"], 0, None)
    ce = np.clip(-(fi @ geo["u"]), 1e-3, None)
    img[dentro] = 290 * a * ci / (ci + ce)
    img = cv2.GaussianBlur(img, (0, 0), 1.0) + np.random.default_rng(semilla).normal(8, 3, img.shape)
    return np.clip((np.clip(img, 0, None) / 255.0) ** (1 / 2.2) * 255, 0, 255).astype(np.uint8)


def probar_geometria():
    print("Geometría contra JPL Horizons (Greenwich)")
    for fecha, ilum, lib_lon, lib_lat, sub_lon, sub_lat in HORIZONS:
        t = datetime.fromisoformat(fecha + "T00:00:00+00:00").timestamp()
        g = L.geometria(t, 51.4769, 0.0, 50.0)
        errores = [abs(dif_angulo(g["libracion_lon"], lib_lon)), abs(g["libracion_lat"] - lib_lat),
                   abs(dif_angulo(g["subsolar_lon"], sub_lon)), abs(g["subsolar_lat"] - sub_lat)]
        comprobar(max(errores) < 0.05 and abs(g["iluminada"] - ilum) < 0.002,
                  f"{fecha}: libración y punto subsolar a {max(errores):.3f}° de Horizons, iluminada {g['iluminada']:.4f}")


def probar_sinteticas():
    print("Lunas sintéticas: disco, giro y espejo")
    rng = np.random.default_rng(1)
    inicio = datetime(2026, 10, 1, 21, tzinfo=timezone.utc).timestamp()
    tiempos = []
    for prueba in range(12):
        t = inicio + (2 + prueba * 2.3) * 86400
        geo = L.geometria(t, 39.95, -1.11)
        giro, espejo = float(rng.uniform(0, 360)), bool(rng.integers(0, 2))
        r = float(rng.uniform(150, 600))
        cx, cy = 800 + rng.uniform(-150, 150), 600 + rng.uniform(-100, 100)
        if geo["iluminada"] < 0.03:          # Luna nueva: no hay nada que anotar (y debe decirlo)
            img = pintar(geo, 1600, 1200, cx, cy, r, giro, espejo, semilla=prueba)
            try:
                L.anotar(img, t, 39.95, -1.11)
                comprobar(False, f"iluminada {geo['iluminada']:.2f}: debería avisar de que no se ve la Luna")
            except L.ErrorLuna as e:
                comprobar(True, f"iluminada {geo['iluminada']:.2f}: avisa ({e})")
            continue
        img = pintar(geo, 1600, 1200, cx, cy, r, giro, espejo, semilla=prueba)
        t0 = time.time()
        res = L.anotar(img, t, 39.95, -1.11)
        tiempos.append(time.time() - t0)
        d, o = res["disco"], res["orientacion"]
        e_centro = math.hypot(d["cx"] - cx, d["cy"] - cy)
        e_giro = dif_angulo(o["giro"], giro)
        comprobar(e_centro < 0.015 * r and abs(d["r"] - r) < 0.015 * r and abs(e_giro) < 1.0 and o["espejo"] == espejo,
                  f"iluminada {geo['iluminada']:.2f}, radio {r:.0f} px: centro a {e_centro:.1f} px, radio {d['r'] - r:+.1f} px, "
                  f"giro {e_giro:+.2f}°, espejo {'sí' if o['espejo'] else 'no'} (margen {o['margen']:.3f})")
    print(f"  {np.mean(tiempos[1:]):.2f} s por foto de 1600×1200")


def probar_accidentes():
    print("Accidentes sobre la foto")
    t = datetime(2026, 10, 21, 19, 30, tzinfo=timezone.utc).timestamp()
    geo = L.geometria(t, 39.95, -1.11)
    cx, cy, r, giro = 800.0, 600.0, 450.0, 37.0
    img = pintar(geo, 1600, 1200, cx, cy, r, giro, True)
    res = L.anotar(img, t, 39.95, -1.11)
    acc = res["accidentes"]
    dentro = all(math.hypot(a["x"] - cx, a["y"] - cy) < r * 1.02 for a in acc)
    comprobar(len(acc) > 100 and dentro, f"{len(acc)} accidentes, todos dentro del disco")
    comprobar(acc[0]["familia"] == "estacion" or acc[0]["familia"] == "mar", f"el primero es un alunizaje o un mar ({acc[0]['nombre']})")
    tycho = next((a for a in acc if a["nombre"] == "Tycho"), None)
    X, Y, __, __ = L.a_vista(geo, L._vectores_superficie(np.array([-43.296]), np.array([-11.215])))
    x, y = L._a_foto(X, Y, {"cx": cx, "cy": cy, "r": r}, {"giro": giro, "espejo": True})
    comprobar(tycho is not None and math.hypot(tycho["x"] - x[0], tycho["y"] - y[0]) < 3.0,
              "Tycho cae donde debe" + (f" (a {math.hypot(tycho['x'] - x[0], tycho['y'] - y[0]):.1f} px)" if tycho else ""))
    comprobar(len(res["terminador"]) > 20, f"terminador con {len(res['terminador'])} puntos")
    trazado = res["mares"]
    comprobar(trazado.startswith("M") and trazado.count("M") > 50 and "l M" not in trazado and not trazado.endswith("l"),
              f"contornos de los mares: {trazado.count('M')} trazos, {len(trazado) // 1024} KB")


if __name__ == "__main__":
    probar_geometria()
    probar_sinteticas()
    probar_accidentes()
    if fallos:
        print(f"\n{len(fallos)} comprobaciones fallidas")
        sys.exit(1)
    print("\nLuna anotada: todo bien")
