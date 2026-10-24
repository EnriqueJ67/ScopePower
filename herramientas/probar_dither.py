# herramientas/probar_dither.py
#
# Prueba sintética del dithering del apilado (dither.py) sin montura ni cámara:
#
#   - Los puntos elegidos quedan siempre dentro del círculo de la amplitud alrededor del
#     punto de partida, cada uno a al menos media amplitud del anterior, y tras cientos de
#     movimientos el encuadre no se ha ido a ninguna parte (antes era un paseo al azar).
#   - Dither.mover() sobre la montura altacimutal falsa de probar_guiado_disco.py: llega a
#     cada punto con MoveAxis a velocidad baja, sin GoTo, con los ejes parados y el
#     seguimiento como estaba al acabar.
#   - Si algo ajeno mueve la montura (GoTo a otro objeto, centrado), ese sitio pasa a ser
#     el nuevo punto de partida.
#   - Montura que ignora MoveAxis, o velocidad 0: se va con GoTo.
#
#   python herramientas\probar_dither.py [--verboso]
#
# Sale con código 0 si todo pasa y 1 si no. Tarda unos veinte segundos (los pasos de
# MoveAxis duran ≥ 1 s).

import os
import sys
import math
import time
import random
import argparse
import logging

from astropy.coordinates import SkyCoord
import astropy.units as u

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probar_guiado_disco import MonturaAltAzFalsa, LUGAR, comprobar, FALLOS   # noqa: E402  (también fija el cwd y J2000)

import correccion_ejes as ce                            # noqa: E402
import dither                                           # noqa: E402

AMPLITUD = 48.0      # ″: 15 px a 3,2″/px (Saturn-C con la Evoguide 50)


class MonturaConGoTo(MonturaAltAzFalsa):
    """La montura falsa con SlewToCoordinates (llega al instante) para probar la vuelta al GoTo."""

    CanSlewAsync = True

    def __init__(self, **kw):
        super().__init__(**kw)
        self.gotos = []

    def SlewToCoordinatesAsync(self, ra_h, dec):
        self._avanzar()
        self._ra_h, self._dec = float(ra_h), float(dec)
        self.gotos.append((ra_h, dec))


def apunta(m):
    m._avanzar()
    return SkyCoord(ra=m._ra_h * u.hourangle, dec=m._dec * u.deg)


def caso_puntos():
    print(f"Puntos elegidos: 2000 movimientos con amplitud {AMPLITUD:.0f}″")
    d = dither.Dither(rng=random.Random(1))
    lejos, cerca, anterior = 0.0, float("inf"), (0.0, 0.0)
    suma_e = suma_n = 0.0
    for __ in range(2000):
        p = d.elegir(AMPLITUD)
        lejos = max(lejos, math.hypot(*p))
        cerca = min(cerca, math.hypot(p[0] - anterior[0], p[1] - anterior[1]))
        suma_e += p[0]
        suma_n += p[1]
        d.desplazamiento = anterior = p
    comprobar(lejos <= AMPLITUD + 1e-9, f"todos dentro del círculo: el más lejano a {lejos:.1f}″ del punto de partida")
    comprobar(cerca >= dither.SEPARACION_MINIMA * AMPLITUD - 1e-9,
              f"cada uno a al menos {dither.SEPARACION_MINIMA * AMPLITUD:.0f}″ del anterior (el más cercano a {cerca:.1f}″)")
    media = math.hypot(suma_e, suma_n) / 2000
    comprobar(media < 0.1 * AMPLITUD, f"centrados en el punto de partida: posición media a {media:.1f}″")
    # El paseo al azar de antes, para comparar
    e = n = 0.0
    rng = random.Random(1)
    for __ in range(2000):
        r, a = rng.uniform(0.5, 1.0) * AMPLITUD, rng.uniform(0, 2 * math.pi)
        e, n = e + r * math.cos(a), n + r * math.sin(a)
    print(f"  (con el paseo al azar de antes habría acabado a {math.hypot(e, n):.0f}″ del punto de partida)")


def caso_moveaxis():
    print("Cuatro dithers con MoveAxis en la montura altacimutal falsa")
    m = MonturaConGoTo()
    inicio = apunta(m)
    d = dither.Dither(rng=random.Random(2))
    tol = max(dither.TOLERANCIA_MINIMA_ARCMIN * 60, dither.TOLERANCIA_FRACCION * AMPLITUD)
    for i in range(4):
        r = d.mover(m, AMPLITUD, LUGAR)
        err = float(apunta(m).separation(d.punto(r["posicion"])).arcsec)
        desde_inicio = float(apunta(m).separation(inicio).arcsec)
        comprobar(r["metodo"] == "ejes" and err <= tol + 2,
                  f"dither {i + 1}: por ejes, a {err:.1f}″ del punto (residuo {r['residuo_arcmin']:.2f}′), "
                  f"a {desde_inicio:.0f}″ del punto de partida")
        comprobar(r["nuevo_origen"] == (i == 0), "  el punto de partida solo se fija en el primero")
        comprobar(desde_inicio <= AMPLITUD + tol + 2, "  dentro de la amplitud alrededor del punto de partida")
    comprobar(not m.gotos, "ningún GoTo")
    vmax = max(abs(v) for __, v in m.movimientos)
    comprobar(vmax <= ce.VELOCIDAD_DEFECTO + 1e-9, f"velocidad máxima usada {vmax:.4f} °/s")
    comprobar(not any(m._v) and not m.Slewing, "los ejes acaban parados")
    comprobar(m.Tracking and m.TrackingRate == 2, "el seguimiento vuelve a estar como estaba")

    print("Algo ajeno mueve la montura 5′ (otro GoTo, un centrado): nuevo punto de partida")
    m._dec += 5.0 / 60.0
    movida = apunta(m)
    r = d.mover(m, AMPLITUD, LUGAR)
    comprobar(r["nuevo_origen"], "se detecta y se toma un punto de partida nuevo")
    comprobar(float(d.origen.separation(movida).arcsec) < 1.0, "el nuevo punto de partida es donde la dejaron")
    desde = float(apunta(m).separation(movida).arcsec)
    comprobar(desde <= AMPLITUD + tol + 2, f"y el dither queda a {desde:.0f}″ de ahí, no del de antes")


def caso_goto():
    print("Montura que ignora MoveAxis: el dither se hace con GoTo")
    m = MonturaConGoTo(inmovil=True)
    d = dither.Dither(rng=random.Random(3))
    r = d.mover(m, AMPLITUD, LUGAR, dormir=lambda s: None)
    err = float(apunta(m).separation(d.punto(r["posicion"])).arcsec)
    comprobar(r["metodo"] == "goto" and len(m.gotos) == 1, f"un GoTo ({r['metodo']})")
    comprobar(err < 0.5, f"llega al punto ({err:.2f}″)")
    comprobar(not any(m._v) and m.Tracking, "ejes parados y seguimiento activo")

    print("Velocidad 0 en [Dithering]: directamente con GoTo, sin MoveAxis")
    m = MonturaConGoTo()
    r = d.mover(m, AMPLITUD, LUGAR, velocidad=0, dormir=lambda s: None)
    comprobar(r["metodo"] == "goto" and len(m.gotos) == 1 and not m.movimientos, "un GoTo y ningún MoveAxis")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verboso else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for caso in (caso_puntos, caso_moveaxis, caso_goto):
        t0 = time.time()
        caso()
        print(f"  ({time.time() - t0:.1f} s)")
    if FALLOS:
        print(f"\n{len(FALLOS)} comprobación(es) fallida(s):")
        for f in FALLOS:
            print("  - " + f)
        return 1
    print("\nTodo bien.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
