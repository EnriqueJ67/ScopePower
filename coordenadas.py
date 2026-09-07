# coordenadas.py
#
# Sistema de coordenadas de la montura: J2000 o JNow.
#
# Los catálogos, Stellarium (raJ2000/decJ2000), SIMBAD y el plate solving de
# ASTAP dan coordenadas J2000 (ICRS). Muchas monturas ASCOM, la SynScan entre
# ellas, trabajan en JNow (equinoccio de la fecha). La diferencia hoy es de
# unos 20 minutos de arco: si se le envía J2000 a una montura JNow, el GOTO
# cae a 20' del objeto y un Sync con coordenadas J2000 corrompe su modelo de
# apuntado en esa misma cantidad.
#
# Al conectar la montura se lee su propiedad EquatorialSystem y aquí se
# guarda. Todo lo que ordena posiciones a la montura (GOTO, Sync, centrado,
# alineación, recorridos) pasa por a_montura(); lo que lee posiciones de la
# montura para compararlas con el cielo pasa por desde_montura().

import logging
import threading

from astropy.coordinates import SkyCoord, TETE
from astropy.time import Time
import astropy.units as u

log = logging.getLogger("scopepower.coordenadas")

# Valores de ASCOM EquatorialCoordinateType
_ASCOM = {0: "Otro", 1: "JNow", 2: "J2000", 3: "J2050", 4: "B1950"}

_lock = threading.Lock()
_sistema = "J2000"      # hasta que se lea de la montura, no se convierte nada
_leido = False


def establecer_desde_ascom(codigo):
    """Guarda el sistema a partir del valor de tel.EquatorialSystem."""
    global _sistema, _leido
    nombre = _ASCOM.get(int(codigo) if codigo is not None else -1, "Otro")
    with _lock:
        _sistema = "JNow" if nombre == "JNow" else "J2000"
        _leido = True
    if nombre not in ("JNow", "J2000"):
        log.warning("La montura declara el sistema '%s'; se tratará como J2000", nombre)
    log.info("Sistema de coordenadas de la montura: %s", nombre)
    return _sistema


def sistema():
    with _lock:
        return _sistema


def leido():
    with _lock:
        return _leido


def a_montura(ra_h, dec_deg):
    """Coordenadas J2000 (ICRS) -> las que espera la montura. Devuelve (ra_h, dec_deg)."""
    if sistema() != "JNow":
        return float(ra_h), float(dec_deg)
    c = SkyCoord(ra=float(ra_h) * u.hourangle, dec=float(dec_deg) * u.deg, frame="icrs")
    t = c.transform_to(TETE(obstime=Time.now()))
    return float(t.ra.hour), float(t.dec.deg)


def desde_montura(ra_h, dec_deg):
    """Coordenadas leídas de la montura -> J2000 (ICRS). Devuelve (ra_h, dec_deg)."""
    if sistema() != "JNow":
        return float(ra_h), float(dec_deg)
    c = SkyCoord(ra=float(ra_h) * u.hourangle, dec=float(dec_deg) * u.deg,
                 frame=TETE(obstime=Time.now()))
    i = c.transform_to("icrs")
    return float(i.ra.hour), float(i.dec.deg)
