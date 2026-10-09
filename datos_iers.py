# datos_iers.py
#
# Tabla de rotación de la Tierra (IERS) sin Internet.
#
# astropy necesita la tabla IERS (UT1-UTC y movimiento del polo) para pasar de
# RA/Dec a altura/acimut. Por defecto la descarga él mismo en mitad del
# cálculo y, si la que tiene guardada pasa de 30 días y no hay red, lanza un
# error: así fallaron el GoTo y "Esta noche" en el campo el 8-oct-2026.
#
# Aquí se le quita a astropy la descarga automática y se le da la tabla más
# reciente que haya en el PC (datos/iers, la caché de astropy o la que trae el
# paquete). Cuando hay red, un hilo la renueva cada semana. Una tabla vieja
# solo cuesta precisión: UT1-UTC deriva menos de un segundo al año (unos 15"
# en el cielo), muy por debajo de lo que corrige el centrado automático.
#
#   configurar()                  -> al arrancar, antes de cualquier cálculo
#   actualizar_en_segundo_plano() -> descarga si hay red y la guardada es vieja
#   estado()                      -> qué tabla se usa y hasta cuándo llega

import os
import time
import socket
import logging
import threading
import warnings

from astropy.time import Time
from astropy.utils import iers
from astropy.utils.data import is_url_in_cache, download_file

log = logging.getLogger("scopepower.iers")

CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "iers")
FICHERO = os.path.join(CARPETA, "finals2000A.all")
URLS = (iers.IERS_A_URL, iers.IERS_A_URL_MIRROR)
HOST_IERS = ("datacenter.iers.org", 443)
MAX_EDAD_DIAS = 7

_lock = threading.Lock()
_actual = {"origen": None, "ruta": None, "desde": None, "hasta": None}


def _abrir(ruta):
    """Abre un finals2000A.all; None si no existe o no se puede leer."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return iers.IERS_A.open(ruta)
    except Exception as e:
        log.warning("Tabla IERS ilegible (%s): %s", ruta, e)
        return None


def _candidatas():
    """(origen, ruta) de todas las tablas que hay en el PC, sin tocar la red."""
    out = [("guardada", FICHERO)] if os.path.exists(FICHERO) else []
    for url in URLS:
        try:
            if is_url_in_cache(url):
                # Con la URL en caché, download_file devuelve la ruta local sin conectarse
                out.append(("caché de astropy", download_file(url, cache=True)))
        except Exception:
            pass
    out.append(("la de astropy", iers.IERS_A_FILE))
    return out


def _fecha(mjd):
    return Time(mjd, format="mjd").strftime("%Y-%m-%d")


def instalar_mejor_tabla():
    """Carga la tabla con las predicciones más recientes y se la da a astropy."""
    mejor = None
    for origen, ruta in _candidatas():
        tabla = _abrir(ruta)
        if tabla is None:
            continue
        # predictive_mjd: primer día de predicción = fecha en que se publicó la tabla
        clave = float(tabla.meta.get("predictive_mjd", tabla["MJD"][-1].value))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, origen, ruta, tabla)
    if mejor is None:
        log.error("No hay ninguna tabla IERS legible: astropy usará la suya")
        return
    clave, origen, ruta, tabla = mejor
    iers.earth_orientation_table.set(tabla)
    with _lock:
        _actual.update(origen=origen, ruta=ruta, desde=_fecha(clave), hasta=_fecha(tabla["MJD"][-1].value))
    hoy = Time.now().mjd
    if hoy > tabla["MJD"][-1].value:
        log.warning("Tabla IERS (%s) del %s, solo llega al %s: se extrapola (pierde algo de precisión, el GoTo sigue)",
                    origen, _actual["desde"], _actual["hasta"])
    else:
        log.info("Tabla IERS: %s, publicada el %s (%.0f días), predicciones hasta el %s",
                 origen, _actual["desde"], hoy - clave, _actual["hasta"])


def configurar():
    """Sin descargas en mitad de un cálculo y sin errores por tabla vieja."""
    iers.conf.auto_download = False         # nada de red al calcular (también evita esperas de 10 s)
    iers.conf.auto_max_age = None           # una tabla de más de 30 días vale igual
    iers.conf.iers_degraded_accuracy = "warn"   # fuera de la tabla: extrapolar, no fallar
    # Ese aviso ya lo da instalar_mejor_tabla() una vez; astropy lo repetiría en cada cálculo
    warnings.filterwarnings("ignore", category=iers.IERSDegradedAccuracyWarning)
    warnings.filterwarnings("ignore", message="Tried to get polar motions for times after IERS data is valid")
    instalar_mejor_tabla()


def _edad_dias(ruta):
    if not os.path.exists(ruta):
        return None
    return (time.time() - os.path.getmtime(ruta)) / 86400.0


def hay_internet(timeout=3.0):
    try:
        with socket.create_connection(HOST_IERS, timeout=timeout):
            return True
    except OSError:
        return False


def actualizar(forzar=False):
    """Descarga la tabla si falta o tiene más de MAX_EDAD_DIAS y hay red. Devuelve el mensaje."""
    edad = _edad_dias(FICHERO)
    if not forzar and edad is not None and edad <= MAX_EDAD_DIAS:
        return "Tabla IERS al día"
    if not hay_internet():
        return "Sin Internet: se usa la tabla IERS guardada"
    import requests
    os.makedirs(CARPETA, exist_ok=True)
    temporal = FICHERO + ".tmp"
    for url in URLS:
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            with open(temporal, "wb") as f:
                f.write(r.content)
            if _abrir(temporal) is None:
                raise ValueError("el fichero no tiene el formato esperado")
            os.replace(temporal, FICHERO)
            log.info("Tabla IERS descargada de %s (%d bytes)", url, len(r.content))
            instalar_mejor_tabla()
            return "Tabla IERS actualizada"
        except Exception as e:
            log.warning("No se pudo descargar la tabla IERS de %s: %s", url, e)
    try:
        os.remove(temporal)
    except OSError:
        pass
    return "No se pudo descargar la tabla IERS: se usa la guardada"


def actualizar_en_segundo_plano():
    threading.Thread(target=lambda: log.info("IERS: %s", actualizar()), name="iers-actualizar", daemon=True).start()


def estado():
    with _lock:
        return dict(_actual)
