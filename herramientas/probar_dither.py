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

from astropy.coordinates import SkyCoord, AltAz
from astropy.time import Time
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


class MonturaSinSeguirAlMover(MonturaConGoTo):
    """
    Como la AZ GoTo con SynScan: mientras MoveAxis mueve los ejes el seguimiento se para (el
    apuntado se queda fijo en acimut y altura y el cielo se va, unos 8″/s hacia arriba con el
    Doble Cúmulo subiendo por el NE), y el primer paso tras un rato siguiendo el cielo apenas
    arranca (holgura o latencia: no se mueve). Es el modelo que reproduce el log
    del 29-oct-2026 (signos que se invierten y vuelven, 4 pasos, residuos de 0,5–0,9′), no una medida.
    """

    def __init__(self, **kw):
        self._seguimiento, self._siguiendo_desde = True, time.time() - 10.0
        super().__init__(**kw)
        self._factor = 1.0

    @property
    def Tracking(self):
        return self._seguimiento

    @Tracking.setter
    def Tracking(self, valor):
        if valor and not self._seguimiento:
            self._siguiendo_desde = time.time()
        self._seguimiento = bool(valor)

    def _avanzar(self):
        with self._lock:
            ahora = time.time()
            dt, t0, self._t = ahora - self._t, self._t, ahora
            if self._seguimiento:
                return                      # sigue el cielo: AR/Dec fijos
            # Sin seguimiento: acimut y altura fijos salvo lo que giren los ejes
            h = SkyCoord(ra=self._ra_h * u.hourangle, dec=self._dec * u.deg).transform_to(
                AltAz(obstime=Time(t0, format="unix"), location=LUGAR))
            az = (float(h.az.deg) + self.signo_az * self._factor * self._v[0] * dt) % 360.0
            alt = float(h.alt.deg) + self.signo_alt * self._factor * self._v[1] * dt
            c = SkyCoord(az=az * u.deg, alt=alt * u.deg, frame=AltAz(obstime=Time(ahora, format="unix"), location=LUGAR)).icrs
            self._ra_h, self._dec = float(c.ra.hour), float(c.dec.deg)

    def MoveAxis(self, eje, v):
        arranca = v != 0 and not any(self._v)
        super().MoveAxis(eje, v)
        if arranca:
            self._factor = 0.0 if time.time() - self._siguiendo_desde > 2.0 else 1.0


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


class Avisos(logging.Handler):
    """Cuenta los avisos de «signo invertido» de correccion_ejes."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.invertidos = 0

    def emit(self, registro):
        if "signo invertido" in registro.getMessage():
            self.invertidos += 1


def caso_deriva():
    print("Dithers de 25–70″ con el seguimiento parado al mover y un primer paso que no arranca (29-oct-2026)")
    avisos = Avisos()
    logging.getLogger("scopepower.correccion_ejes").addHandler(avisos)
    rng = random.Random(4)
    gotos, pasos, residuos, aprendidos = 0, [], [], []
    try:
        for i in range(10):
            m = MonturaSinSeguirAlMover(alt0=38.0, az0=42.0)     # el Doble Cúmulo subiendo por el NE
            r_arcsec, ang = rng.uniform(25, 70), (i * 36 + rng.uniform(-10, 10))
            du, dv = r_arcsec * math.cos(math.radians(ang)) / 3600, r_arcsec * math.sin(math.radians(ang)) / 3600
            try:
                r = ce.corregir(m, du, dv, LUGAR, signos=(1, 1), tolerancia_arcmin=0.25,
                                guardar=lambda a, h, modo: aprendidos.append((a, h)))
                pasos.append(r["pasos"])
                residuos.append(r["residuo_arcmin"])
            except ce.ErrorCorreccion:
                gotos += 1
    finally:
        logging.getLogger("scopepower.correccion_ejes").removeHandler(avisos)
    print(f"  pasos {pasos}, residuos {residuos}′, {gotos} vuelta(s) al GoTo, {avisos.invertidos} signo(s) invertido(s)")
    comprobar(avisos.invertidos == 0, "ningún signo invertido: un paso malo no basta")
    comprobar(not aprendidos, "no se guarda ningún signo (los de verdad no han cambiado)")
    comprobar(gotos == 0, "ninguna vuelta al GoTo")
    comprobar(residuos and max(residuos) <= 0.3, f"todos llegan cerca del punto (el peor a {max(residuos or [99]):.2f}′)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verboso else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for caso in (caso_puntos, caso_moveaxis, caso_goto, caso_deriva):
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
