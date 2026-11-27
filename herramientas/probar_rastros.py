"""
Prueba sintética de rastros.detectar_rastro en un campo denso de estrellas (la Vía Láctea):

  1. Sin rastro: nada.
  2. Un satélite que cruza muchas estrellas brillantes: al borrarlas con su halo el rastro queda
     a trozos separados por huecos de ~20 px (el del 26-nov, de 12 sigmas, no se detectaba).
  3. Una fila de estrellas brillantes alineadas sin luz entre ellas: no es un rastro aunque Hough
     las pueda unir con huecos grandes.

Uso: python herramientas/probar_rastros.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rastros  # noqa: E402

LADO = 1600
FONDO, RUIDO = 3000.0, 50.0
fallos = []


def comprobar(cond, texto):
    print(("  ok    " if cond else "  FALLO ") + texto)
    if not cond:
        fallos.append(texto)


def estrella(img, x, y, flujo, sigma=1.6):
    r = int(4 * sigma) + 2
    x0, y0 = int(x), int(y)
    ys, xs = np.mgrid[max(0, y0 - r):min(LADO, y0 + r + 1), max(0, x0 - r):min(LADO, x0 + r + 1)]
    img[ys, xs] += flujo * np.exp(-((xs - x) ** 2 + (ys - y) ** 2) / (2 * sigma ** 2))


def campo(semilla):
    rng = np.random.default_rng(semilla)
    img = rng.normal(FONDO, RUIDO, (LADO, LADO)).astype(np.float32)
    for __ in range(1500):        # ~3300 estrellas en 3008² px el 26-nov (Cygnus a 2,9″/px): aquí algo más denso
        estrella(img, rng.uniform(0, LADO), rng.uniform(0, LADO), 40 * RUIDO * rng.pareto(1.6) + 3 * RUIDO)
    return img, rng


def rastro(img, x1, y1, x2, y2, pico, ancho=1.3):
    """Una línea gaussiana de `pico` ADU entre los dos puntos."""
    ys, xs = np.mgrid[0:LADO, 0:LADO].astype(np.float32)
    dx, dy = x2 - x1, y2 - y1
    largo = np.hypot(dx, dy)
    t = ((xs - x1) * dx + (ys - y1) * dy) / largo
    d = np.abs((xs - x1) * dy - (ys - y1) * dx) / largo
    img += np.where((t >= 0) & (t <= largo), pico * np.exp(-d ** 2 / (2 * ancho ** 2)), 0).astype(np.float32)


def main():
    print("Rastros en un campo denso")
    img, __ = campo(1)
    comprobar(rastros.detectar_rastro(img) is None, "campo denso sin rastro: nada")

    # 2. Satélite con estrellas brillantes encima cada ~100 px (al borrarlas, huecos de ~20 px a media resolución)
    img, rng = campo(2)
    x1, y1, x2, y2 = 300.0, 1550.0, 520.0, 250.0
    rastro(img, x1, y1, x2, y2, pico=4 * RUIDO)
    for f in np.arange(0.03, 1.0, 0.075):
        estrella(img, x1 + f * (x2 - x1) + rng.normal(0, 1.5), y1 + f * (y2 - y1), 400 * RUIDO, sigma=2.2)
    r = rastros.detectar_rastro(img)
    esperado = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180
    comprobar(r is not None and abs(r["angulo"] - esperado) < 2 and r["longitud"] >= rastros.FRACCION_LONGITUD * LADO,
              f"satélite que cruza {len(np.arange(0.03, 1.0, 0.075))} estrellas brillantes: {r}")

    # 3. Estrellas brillantes en fila, sin rastro entre ellas
    img, rng = campo(3)
    for f in np.arange(0.0, 1.0, 0.035):
        estrella(img, 200 + f * 1200, 400 + f * 900 + rng.normal(0, 1.0), 400 * RUIDO, sigma=2.2)
    comprobar(rastros.detectar_rastro(img) is None, "fila de estrellas brillantes sin luz entre ellas: no es un rastro")

    if fallos:
        print(f"\n{len(fallos)} fallo(s)")
        sys.exit(1)
    print("\nTodo bien.")


if __name__ == "__main__":
    main()
