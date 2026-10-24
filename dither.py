# dither.py
#
# Dithering del apilado: entre fotos se mueve la montura unos píxeles para que los
# defectos fijos del sensor (píxeles calientes, el patrón del dark) no caigan siempre
# en el mismo sitio del objeto y el apilado los diluya.
#
# Cada movimiento va a un punto al azar ALREDEDOR DEL PUNTO DE PARTIDA, dentro de un
# círculo de radio `amplitud`, y no a un paso al azar desde donde quedó el anterior:
# así el encuadre no se va desplazando durante la noche (un paseo al azar de 15 px
# se aleja del orden de 100 px en 100 movimientos, el recorte de los bordes crece y
# el objeto se descentra). Cada punto nuevo queda a al menos media amplitud del
# anterior, para que el movimiento sirva de algo.
#
# El punto de partida es donde apunta la montura en el primer dither. Si al ir a
# hacer otro la montura no está donde la dejó el anterior (GoTo a otro objeto,
# centrado, pad), algo la ha movido más que el propio dither: ese sitio pasa a ser
# el nuevo punto de partida.
#
# El movimiento se hace con MoveAxis a velocidad baja (correccion_ejes.py) y no con un
# GoTo: un GoTo corto en la AZ GoTo se pasa unos 25′ y vuelve (medido el 21-oct-2026),
# y las fotos de después saldrían movidas. Si la montura no responde a MoveAxis, o
# con velocidad 0 ([Dithering] velocidad_deg_s), se va con un GoTo como antes.

import math
import time
import random
import logging

from astropy.coordinates import SkyCoord
import astropy.units as u

from idioma import _
import coordenadas
import correccion_ejes

log = logging.getLogger("scopepower.dither")

SEPARACION_MINIMA = 0.5       # cada punto nuevo, a al menos esta fracción de la amplitud del anterior
INTENTOS = 30                 # sorteos para encontrarlo (casi siempre basta el primero o el segundo)
DESVIO_MINIMO_ARCSEC = 30.0   # algo ajeno ha movido la montura si se ha ido más que esto (o que la amplitud)
TOLERANCIA_FRACCION = 0.25    # MoveAxis da el movimiento por bueno a un cuarto de la amplitud del destino
TOLERANCIA_MINIMA_ARCMIN = 0.25   # por debajo no se gana nada: el cielo corre ~15″ en cada paso de ≥ 1 s sin seguimiento
ESPERA_GOTO_S = 60.0


class ErrorDither(Exception):
    """La montura no terminó el movimiento del dither."""


def apuntado_icrs(tel):
    """SkyCoord ICRS adonde dicen apuntar los codificadores de la montura."""
    ra, dec = coordenadas.desde_montura(tel.RightAscension, tel.Declination)
    return SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)


class Dither:
    """Recuerda el punto de partida y dónde quedó el último dither respecto a él."""

    def __init__(self, rng=None):
        self.origen = None                  # SkyCoord ICRS del punto de partida
        self.desplazamiento = (0.0, 0.0)    # ″ (este, norte) desde el origen del último dither
        self._rng = rng or random.Random()

    def reiniciar(self):
        self.origen = None
        self.desplazamiento = (0.0, 0.0)

    def punto(self, desplazamiento):
        """SkyCoord ICRS a `desplazamiento` (″ este, ″ norte) del origen."""
        e, n = desplazamiento
        return self.origen.spherical_offsets_by(e * u.arcsec, n * u.arcsec)

    def elegir(self, amplitud):
        """
        (este, norte) en ″ del siguiente punto: al azar y uniforme dentro del círculo de radio
        `amplitud` alrededor del origen, a al menos SEPARACION_MINIMA·amplitud del anterior.
        """
        e0, n0 = self.desplazamiento
        mejor = None
        for _intento in range(INTENTOS):
            r = amplitud * math.sqrt(self._rng.random())
            a = self._rng.uniform(0.0, 2.0 * math.pi)
            p = (r * math.cos(a), r * math.sin(a))
            d = math.hypot(p[0] - e0, p[1] - n0)
            if d >= SEPARACION_MINIMA * amplitud:
                return p
            if mejor is None or d > mejor[0]:
                mejor = (d, p)
        return mejor[1]

    def mover(self, tel, amplitud, lugar, velocidad=correccion_ejes.VELOCIDAD_DEFECTO, dormir=time.sleep):
        """
        Lleva la montura al siguiente punto del dither (`amplitud` en ″). Se ejecuta DENTRO del
        hilo de la montura (montura.ejecutar). Devuelve un dict con `movido` y `posicion` (″ este,
        ″ norte: el movimiento hecho y dónde queda respecto al origen), `nuevo_origen`, `metodo`
        ('ejes' o 'goto') y `residuo_arcmin` (None con GoTo). Lanza ErrorDither si el GoTo no acaba.
        """
        actual = apuntado_icrs(tel)
        nuevo_origen = self.origen is None
        if not nuevo_origen:
            desvio = float(actual.separation(self.punto(self.desplazamiento)).arcsec)
            if desvio > max(amplitud, DESVIO_MINIMO_ARCSEC):
                log.info("Dither: la montura se ha movido %.0f″ desde el último; nuevo punto de partida", desvio)
                nuevo_origen = True
        if nuevo_origen:
            self.origen = actual
            self.desplazamiento = (0.0, 0.0)

        anterior = self.desplazamiento
        nuevo = self.elegir(amplitud)
        destino = self.punto(nuevo)

        metodo, residuo = "goto", None
        if velocidad > 0:
            tolerancia = max(TOLERANCIA_MINIMA_ARCMIN, TOLERANCIA_FRACCION * amplitud / 60.0)
            try:
                r = correccion_ejes.ir_a(tel, destino, lugar, velocidad_max=velocidad, dormir=dormir,
                                         tolerancia_arcmin=tolerancia)
                metodo, residuo = "ejes", r["residuo_arcmin"]
            except correccion_ejes.ErrorCorreccion as e:
                log.warning("Dither: movimiento por ejes fallido (%s); se hace con GoTo", e)
        if metodo == "goto":
            _goto(tel, destino, dormir)

        self.desplazamiento = nuevo
        return {"movido": (nuevo[0] - anterior[0], nuevo[1] - anterior[1]), "posicion": nuevo,
                "nuevo_origen": nuevo_origen, "metodo": metodo, "residuo_arcmin": residuo}


def _goto(tel, destino, dormir):
    ra, dec = coordenadas.a_montura(float(destino.ra.hour), float(destino.dec.deg))
    tel.Tracking = True
    if getattr(tel, "CanSlewAsync", False):
        tel.SlewToCoordinatesAsync(ra, dec)
    else:
        tel.SlewToCoordinates(ra, dec)
    inicio = time.time()
    dormir(0.3)
    while tel.Slewing:
        if time.time() - inicio > ESPERA_GOTO_S:
            raise ErrorDither(_("La montura no terminó el movimiento de dithering en 60 s"))
        dormir(0.3)
