# herramientas/probar_guiado_disco.py
#
# Prueba sintética del guiado por el disco sin montura ni cámara:
#
#   - correccion_ejes.corregir() sobre una montura altacimutal falsa (API ASCOM:
#     RightAscension/Declination que se mueven con MoveAxis en acimut y altura, Tracking
#     que se apaga al mover como hace SynScan, Slewing). Casos: corrección normal; driver
#     con el eje de acimut al revés (se aprende el signo y se guarda); montura que no se
#     mueve (ErrorCorreccion, para volver al GoTo); montura que se atasca tras un primer paso
#     bueno (se para ahí, sin volver al GoTo); parada que falla dos veces (reintentos).
#     En todos los casos los ejes acaban parados y el seguimiento como estaba.
#   - disco.GuiadoDisco con fotos y medidas falsas: con un vídeo grabándose la corrección
#     espera, se hace al terminar la grabación, y más allá de 3 veces el umbral se hace igual.
#
#   python herramientas\probar_guiado_disco.py [--verboso]
#
# Sale con código 0 si todo pasa y 1 si no. Tarda medio minuto (los pasos duran ≥ 1 s).

import os
import sys
import time
import argparse
import logging
import tempfile
import threading

from astropy.coordinates import SkyCoord, AltAz, EarthLocation
from astropy.time import Time
import astropy.units as u

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import coordenadas                                      # noqa: E402
import apuntado_teorico                                 # noqa: E402
import correccion_ejes as ce                            # noqa: E402
import disco                                            # noqa: E402

coordenadas._sistema = "J2000"      # la montura falsa habla en J2000: nada de JNow

LUGAR = EarthLocation(lat=39.6 * u.deg, lon=-0.5 * u.deg, height=200 * u.m)
FALLOS = []


def comprobar(cond, texto):
    print(("  ✅ " if cond else "  ❌ ") + texto)
    if not cond:
        FALLOS.append(texto)


class MonturaAltAzFalsa:
    """
    Apunta a un AR/Dec fijo (sigue el cielo) y MoveAxis(0/1, v) gira el eje de acimut /
    altura a v °/s (signo_az / signo_alt = −1 simulan un driver que gira al revés;
    inmovil = la montura ignora MoveAxis; fallos_parada = veces que falla MoveAxis(…, 0)).
    """

    def __init__(self, alt0=25.0, az0=200.0, signo_az=1, signo_alt=1, inmovil=False, fallos_parada=0):
        marco = AltAz(obstime=Time.now(), location=LUGAR)
        c = SkyCoord(az=az0 * u.deg, alt=alt0 * u.deg, frame=marco).icrs
        self._ra_h, self._dec = float(c.ra.hour), float(c.dec.deg)
        self.signo_az, self.signo_alt = signo_az, signo_alt
        self.inmovil = inmovil
        self.fallos_parada = fallos_parada
        self._v = [0.0, 0.0]
        self._t = time.time()
        self._lock = threading.Lock()
        self.Tracking = True
        self.TrackingRate = 2
        self.Slewing = False
        self.movimientos = []

    def CanMoveAxis(self, eje):
        return True

    def AxisRates(self, eje):
        return []

    def _avanzar(self):
        with self._lock:
            ahora = time.time()
            dt, self._t = ahora - self._t, ahora
            if not any(self._v) or self.inmovil:
                return
            marco = AltAz(obstime=Time(ahora, format="unix"), location=LUGAR)
            h = SkyCoord(ra=self._ra_h * u.hourangle, dec=self._dec * u.deg).transform_to(marco)
            az = (float(h.az.deg) + self.signo_az * self._v[0] * dt) % 360.0
            alt = max(-89.0, min(89.0, float(h.alt.deg) + self.signo_alt * self._v[1] * dt))
            c = SkyCoord(az=az * u.deg, alt=alt * u.deg, frame=marco).icrs
            self._ra_h, self._dec = float(c.ra.hour), float(c.dec.deg)

    @property
    def RightAscension(self):
        self._avanzar()
        return self._ra_h

    @property
    def Declination(self):
        self._avanzar()
        return self._dec

    def MoveAxis(self, eje, v):
        self._avanzar()
        if v == 0 and self.fallos_parada > 0:
            self.fallos_parada -= 1
            raise RuntimeError("COM: la montura no contesta")
        self._v[eje] = float(v)
        self.Slewing = any(self._v)
        if v != 0:
            self.Tracking = False
        self.movimientos.append((eje, float(v)))

    def AbortSlew(self):
        self._v = [0.0, 0.0]
        self.Slewing = False

    def altaz(self):
        self._avanzar()
        h = SkyCoord(ra=self._ra_h * u.hourangle, dec=self._dec * u.deg).transform_to(AltAz(obstime=Time.now(), location=LUGAR))
        return float(h.az.deg), float(h.alt.deg)


def destino_icrs(m, du_arcmin, dv_arcmin):
    """Adónde debería acabar apuntando la montura (SkyCoord ICRS) si se mueve (du, dv)′ desde donde está ahora."""
    az0, alt0 = m.altaz()
    az_d, alt_d = apuntado_teorico.mover_en_horizonte(az0, alt0, du_arcmin / 60.0, dv_arcmin / 60.0)
    return SkyCoord(az=az_d * u.deg, alt=alt_d * u.deg, frame=AltAz(obstime=Time.now(), location=LUGAR)).icrs


def error_arcmin(m, destino):
    """Minutos de arco entre donde apunta la montura y `destino` (fijo en el cielo: el seguimiento no cuenta)."""
    m._avanzar()
    return float(SkyCoord(ra=m._ra_h * u.hourangle, dec=m._dec * u.deg).separation(destino).arcmin)


def caso_normal():
    print("Corrección normal de (+2,0′, −1,5′) a 25° de altura")
    m = MonturaAltAzFalsa()
    destino = destino_icrs(m, 2.0, -1.5)
    r = ce.corregir(m, 2.0 / 60, -1.5 / 60, LUGAR, signos=(1, 1), guardar=None)
    err = error_arcmin(m, destino)
    comprobar(err < 0.3, f"la montura apunta a {err:.2f}′ del destino tras {r['pasos']} paso(s)")
    comprobar(r["residuo_arcmin"] <= ce.TOLERANCIA_ARCMIN, f"residuo {r['residuo_arcmin']:.2f}′ dentro de la tolerancia")
    comprobar(r["pasos"] <= 2, "no ha hecho falta más de dos pasos")
    comprobar(not any(m._v) and not m.Slewing, "los ejes acaban parados")
    comprobar(m.Tracking and m.TrackingRate == 2, "el seguimiento (lunar) vuelve a estar activo")
    comprobar(r["signos"] == (1, 1), "los signos no cambian")
    vmax = max(abs(v) for __, v in m.movimientos)
    comprobar(vmax <= ce.VELOCIDAD_DEFECTO + 1e-9, f"velocidad máxima usada {vmax:.3f} °/s")


def caso_signo_invertido():
    print("Driver con el eje de acimut al revés: se aprende el signo")
    m = MonturaAltAzFalsa(signo_az=-1)
    destino = destino_icrs(m, 2.0, 0.0)
    guardados = []
    r = ce.corregir(m, 2.0 / 60, 0.0, LUGAR, signos=(1, 1), guardar=lambda a, h, modo: guardados.append((a, h, modo)))
    err = error_arcmin(m, destino)
    comprobar(r["signos"] == (-1, 1), f"signo del acimut invertido: {r['signos']}")
    comprobar(guardados == [(-1, 1, "altaz")], "los signos aprendidos se guardan")
    comprobar(err < 0.3, f"y aun así llega a {err:.2f}′ del destino en {r['pasos']} pasos")
    comprobar(not any(m._v) and m.Tracking, "ejes parados y seguimiento activo")
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, "signos.json")
        ce.guardar_signos(-1, 1, "altaz", ruta)
        ce.guardar_signos(1, -1, "eq", ruta)
        comprobar(ce.leer_signos("altaz", ruta) == (-1, 1) and ce.leer_signos("eq", ruta) == (1, -1), "los signos se leen del fichero, por modo de montura")
    comprobar(ce.leer_signos("altaz", os.path.join("no", "existe.json")) == (1, 1), "sin fichero, signos (+1, +1)")


def caso_inmovil():
    print("Montura que ignora MoveAxis: ErrorCorreccion para volver al GoTo")
    m = MonturaAltAzFalsa(inmovil=True)
    try:
        ce.corregir(m, 2.0 / 60, 0.0, LUGAR, signos=(1, 1), guardar=None)
        comprobar(False, "debería haber lanzado ErrorCorreccion")
    except ce.ErrorCorreccion as e:
        comprobar(True, f"lanza ErrorCorreccion: {e}")
    comprobar(not any(m._v) and m.Tracking, "ejes parados y seguimiento activo")
    paradas = [v for __, v in m.movimientos if v == 0]
    comprobar(len(paradas) >= 2, f"la parada se envió ({len(paradas)} MoveAxis a 0)")


class MonturaQueSeAtasca(MonturaAltAzFalsa):
    """Hace un cuarto del primer paso y luego ya no avanza (el límite de precisión de la montura)."""

    def MoveAxis(self, eje, v):
        if v == 0 and any(self._v):
            super().MoveAxis(eje, v)
            if not any(self._v):
                self.inmovil = True
            return
        super().MoveAxis(eje, 0.25 * v)


def caso_se_atasca():
    print("Un primer paso bueno y un segundo que no gana nada: se para ahí, sin ErrorCorreccion")
    m = MonturaQueSeAtasca()
    try:
        r = ce.corregir(m, 2.0 / 60, 0.0, LUGAR, signos=(1, 1), guardar=None)
        comprobar(r["pasos"] == 2 and r["residuo_arcmin"] > ce.TOLERANCIA_ARCMIN,
                  f"se da por acabada tras el paso que no avanza ({r['pasos']} pasos, residuo {r['residuo_arcmin']:.2f}′)")
    except ce.ErrorCorreccion as e:
        comprobar(False, f"no debería volver al GoTo: {e}")
    comprobar(not any(m._v) and m.Tracking, "ejes parados y seguimiento activo")


def caso_parada_con_fallos():
    print("La parada falla dos veces: se reintenta y la montura queda parada")
    m = MonturaAltAzFalsa(fallos_parada=2)
    destino = destino_icrs(m, 1.0, 1.0)
    r = ce.corregir(m, 1.0 / 60, 1.0 / 60, LUGAR, signos=(1, 1), guardar=None)
    err = error_arcmin(m, destino)
    comprobar(not any(m._v) and not m.Slewing, "los ejes acaban parados")
    comprobar(m.Tracking, "el seguimiento vuelve")
    comprobar(err <= 0.6, f"queda a {err:.2f}′ del destino en {r['pasos']} pasos (se movió de más mientras fallaba la parada)")


class FotosFalsas:
    """Fotos con instante creciente y una medida del disco que deriva como se le diga."""

    def __init__(self):
        self.du = 0.0
        self.dv = 0.0
        self.n = 0
        self.lock = threading.Lock()

    def foto(self):
        with self.lock:
            self.n += 1
            return {"instante": time.time(), "n": self.n}

    def medir(self, foto):
        with self.lock:
            return {"du": self.du, "dv": self.dv}


def caso_guiado_aplaza():
    print("Guiado: con un vídeo grabándose la corrección espera; se hace al terminar; más allá de 3× el umbral se hace igual")
    fotos = FotosFalsas()
    movimientos = []
    grabando = {"si": True}

    def mover(du, dv):
        movimientos.append((round(du * 60, 2), round(dv * 60, 2)))
        with fotos.lock:               # la montura se mueve: el disco vuelve a su sitio
            fotos.du -= du
            fotos.dv -= dv
    g = disco.GuiadoDisco()
    g.activar("luna", fotos.foto, fotos.medir, mover, lambda: False,
              umbral_arcmin=1.0, cada_s=0.1, aplazar=lambda: "se está grabando un vídeo" if grabando["si"] else None)
    time.sleep(0.5)
    comprobar(g.referencia is not None, "referencia tomada de la primera foto")
    fotos.du = 2.0 / 60          # 2′: por encima del umbral de 1′
    time.sleep(0.6)
    e = g.estado()
    comprobar(not movimientos, "no corrige mientras se graba")
    comprobar(e["aplazadas"] > 0 and "espera" in e["mensaje"], f"lo dice: «{e['mensaje']}» ({e['aplazadas']} fotos aplazadas)")
    grabando["si"] = False
    time.sleep(0.6)
    comprobar(movimientos == [(2.0, 0.0)], f"al terminar la grabación corrige: {movimientos}")
    grabando["si"] = True
    with fotos.lock:
        fotos.du = 3.5 / 60      # más de 3× el umbral: corrige aunque se grabe
    time.sleep(0.6)
    comprobar(movimientos == [(2.0, 0.0), (3.5, 0.0)], f"con el disco a 3,5′ corrige aunque se grabe: {movimientos}")
    g.desactivar()
    g.esperar(2)
    comprobar(not g.activo, "guiado desactivado")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verboso else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for caso in (caso_normal, caso_signo_invertido, caso_inmovil, caso_se_atasca, caso_parada_con_fallos, caso_guiado_aplaza):
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
