"""
Prueba sintética de la corrección cosmética (cosmetica.py) y del borde del apilado
incremental (apilado_incremental.mascara_valida).

  1. Un mosaico RGGB con fondo, ruido y estrellas de FWHM 2 a 5 px, más píxeles calientes
     (normales y saturados) y fríos (restados de más por un dark más caliente): se
     corrigen todos y ninguna estrella cambia.
  2. Sin defectos no se toca nada.
  3. El borde del sensor en una foto alineada con un desplazamiento: la primera y la
     última columna tienen dato (antes la erosión las quitaba y solo quedaba la foto
     de referencia: una tira con cinco veces el ruido, Nebulosa del Mago del 26-nov).

Uso: python herramientas/probar_cosmetica.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import apilado_incremental  # noqa: E402
import cosmetica  # noqa: E402

LADO = 600
fallos = []


def comprobar(cond, texto):
    print(("  ok    " if cond else "  FALLO ") + texto)
    if not cond:
        fallos.append(texto)


def mosaico(semilla):
    """RGGB con fondo distinto por color, ruido y 120 estrellas (FWHM 2–5 px); devuelve (cfa, picos de estrella)."""
    rng = np.random.default_rng(semilla)
    fondo = np.zeros((LADO, LADO), np.float32)
    fondo[0::2, 0::2], fondo[0::2, 1::2], fondo[1::2, 0::2], fondo[1::2, 1::2] = 1800, 3400, 3400, 2200
    ys, xs = np.mgrid[0:LADO, 0:LADO].astype(np.float32)
    cielo = np.zeros((LADO, LADO), np.float32)
    picos = []
    for __ in range(120):
        x, y = rng.uniform(20, LADO - 20, 2)
        fwhm = rng.uniform(2.0, 5.0)
        s = fwhm / 2.355
        flujo = rng.uniform(800, 40000)
        cielo += flujo * np.exp(-((xs - x) ** 2 + (ys - y) ** 2) / (2 * s * s))
        picos.append((int(round(y)), int(round(x))))
    cfa = fondo + cielo * np.where(fondo == 3400, 1.0, 0.6) + rng.normal(0, 60, (LADO, LADO)).astype(np.float32)
    return np.clip(cfa, 0, 65535).astype(np.float32), picos


def main():
    print("Corrección cosmética")
    cfa, picos = mosaico(1)
    limpio, nc, nf = cosmetica.corregir(cfa)
    comprobar(nc == 0 and nf == 0 and np.array_equal(limpio, cfa), f"sin defectos no se toca nada ({nc} calientes, {nf} fríos)")

    rng = np.random.default_rng(2)
    estrella = np.zeros_like(cfa, bool)
    for y, x in picos:
        estrella[max(0, y - 6):y + 7, max(0, x - 6):x + 7] = True
    libres = np.argwhere(~estrella[4:-4, 4:-4]) + 4
    elegidos = libres[rng.choice(len(libres), 90, replace=False)]
    sucia = cfa.copy()
    calientes, frios = elegidos[:60], elegidos[60:]
    for k, (y, x) in enumerate(calientes):
        sucia[y, x] = 65535 if k % 4 == 0 else cfa[y, x] + rng.uniform(1500, 20000)    # saturados y normales
    for y, x in frios:
        sucia[y, x] = 0.0                                                                # restados de más
    corregida, nc, nf = cosmetica.corregir(sucia)
    resto = max(float(np.abs(corregida[y, x] - cfa[y, x])) for y, x in elegidos)
    comprobar(nc == len(calientes) and nf == len(frios),
              f"{len(calientes)} calientes (un cuarto saturados) y {len(frios)} fríos: corregidos {nc} y {nf}")
    comprobar(resto < 400, f"cada defecto vuelve a su fondo (como mucho {resto:.0f} ADU de diferencia, ruido 60)")
    cambio_estrellas = float(np.abs(corregida - sucia)[estrella].max())
    comprobar(cambio_estrellas == 0.0, f"ninguna estrella (FWHM 2–5 px, hasta 40 000 ADU) cambia: {cambio_estrellas:.0f} ADU")

    print("Borde del apilado incremental")
    M = np.array([[1.0, 0.0, -7.3], [0.0, 1.0, 2.6]])            # la foto se corre 7,3 px a la izquierda y 2,6 abajo
    valida = apilado_incremental.mascara_valida(M, 200, 150)
    comprobar(valida[50:100, 0].all() and not valida[50:100, -8:].any(),
              "la primera columna tiene dato; las últimas 8, que quedan fuera, no")
    comprobar(not valida[:3, 60].any() and valida[3:, 60].all() and valida[-1, 60],
              "arriba faltan las 3 filas que entran de fuera; la última fila del sensor sí tiene dato")
    comprobar(apilado_incremental.mascara_valida(None, 20, 10).all(), "la foto de referencia vale entera")

    if fallos:
        print(f"\n{len(fallos)} fallo(s)")
        sys.exit(1)
    print("\nTodo bien.")


if __name__ == "__main__":
    main()
