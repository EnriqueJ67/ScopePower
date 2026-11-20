# config_ini.py
# ─────────────────────────────────────────────────────────────────────────────
# Leer y guardar config.ini sin que se rompa.
#
# El 19-nov-2026 dos guardados llegaron en el mismo segundo (la exposición desde
# Configuración y el asentamiento de la montura): cada uno abrió config.ini con "w" y lo
# reescribió a la vez que el otro, y el fichero quedó mezclado, con una clave `voz`
# repetida dentro de [PuestaEnEstacion]. Desde entonces configparser se negaba a leerlo y
# fallaban casi todas las pantallas hasta arreglarlo a mano.
#
# Aquí se arregla de tres formas:
#   - escribir(): escribe en un fichero temporal y lo cambia por el bueno de una vez
#     (os.replace), así que config.ini siempre está entero, el de antes o el nuevo;
#   - editar(): leer, cambiar y guardar con un cerrojo común a toda la app, para que dos
#     guardados no se pisen los cambios;
#   - nuevo()/leer(): ConfigParser(strict=False), que con una clave o sección repetida se
#     queda con la última en vez de fallar; reparar() limpia el fichero al arrancar.
#
# Mismo encoding que el resto de la app (el del sistema) salvo que se diga otro.
# ─────────────────────────────────────────────────────────────────────────────
import configparser
import logging
import os
import shutil
import threading
import time
from contextlib import contextmanager

log = logging.getLogger("scopepower.config")

RUTA = "config.ini"
CERROJO = threading.RLock()     # todo lo que reescribe config.ini (o secretos.ini) pasa por aquí

# os.replace falla en Windows si alguien tiene el fichero abierto justo entonces (cada lectura lo
# abre ~1 ms): se reintenta a menudo durante ~1 s antes de escribir en el sitio
_REINTENTOS_CAMBIO = 100
_ESPERA_CAMBIO_S = 0.01


def nuevo():
    """ConfigParser que no se rompe con claves o secciones repetidas (se queda con la última)."""
    return configparser.ConfigParser(strict=False)


def leer(ruta=RUTA, cfg=None, encoding=None):
    """Lee `ruta` en `cfg` (o en uno nuevo, tolerante) y lo devuelve."""
    cfg = nuevo() if cfg is None else cfg
    cfg.read(ruta, encoding=encoding)
    return cfg


def escribir(cfg, ruta=RUTA, encoding=None, cabecera=""):
    """
    Guarda `cfg` en `ruta` de forma atómica: un temporal en la misma carpeta y os.replace.
    Si alguien tiene el fichero abierto y no deja cambiarlo en ~1 s, se escribe en
    el sitio como antes (con el cerrojo de la app, que es lo que evita las mezclas).
    """
    ruta = os.path.abspath(ruta)
    carpeta = os.path.dirname(ruta)
    with CERROJO:
        os.makedirs(carpeta, exist_ok=True)
        temporal = os.path.join(carpeta, f".{os.path.basename(ruta)}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(temporal, "w", encoding=encoding) as f:
                if cabecera:
                    f.write(cabecera)
                cfg.write(f)
                f.flush()
                os.fsync(f.fileno())
            for intento in range(_REINTENTOS_CAMBIO):
                try:
                    os.replace(temporal, ruta)
                    return
                except PermissionError:
                    time.sleep(_ESPERA_CAMBIO_S)
            log.warning("No se pudo cambiar %s por el temporal (abierto por otro programa): se escribe en el sitio", ruta)
            with open(ruta, "w", encoding=encoding) as f:
                if cabecera:
                    f.write(cabecera)
                cfg.write(f)
        finally:
            try:
                os.remove(temporal)
            except OSError:
                pass


@contextmanager
def editar(ruta=RUTA, encoding=None, cabecera=""):
    """
    with config_ini.editar() as cfg: cfg["Seccion"]["clave"] = "valor"
    Lee el fichero recién, deja cambiarlo y lo guarda al salir (no guarda si hay una
    excepción dentro). Mientras dura, nadie más de la app reescribe el fichero.
    """
    with CERROJO:
        cfg = leer(ruta, encoding=encoding)
        yield cfg
        escribir(cfg, ruta, encoding=encoding, cabecera=cabecera)


def guardar_valores(seccion, valores, ruta=RUTA, encoding=None):
    """Escribe (o sobreescribe) varias claves de una sección; crea la sección si no está."""
    with editar(ruta, encoding=encoding) as cfg:
        if seccion not in cfg:
            cfg[seccion] = {}
        for clave, valor in valores.items():
            cfg[seccion][clave] = str(valor)


def reparar(ruta=RUTA, encoding=None):
    """
    Al arrancar: si config.ini tiene claves o secciones repetidas (un guardado a medias de
    una versión anterior), guarda una copia del dañado al lado y lo reescribe limpio con la
    última aparición de cada clave. Devuelve True si lo ha reparado.
    """
    if not os.path.exists(ruta):
        return False
    with CERROJO:
        try:
            configparser.ConfigParser().read(ruta, encoding=encoding)
            return False
        except (configparser.DuplicateOptionError, configparser.DuplicateSectionError) as e:
            problema = e
        except configparser.Error as e:
            log.error("config.ini no se puede leer y no se sabe repararlo solo: %s", e)
            return False
        copia = f"{ruta}.roto-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copyfile(ruta, copia)
        except OSError as e:
            log.warning("No se pudo guardar la copia del config.ini dañado: %s", e)
            copia = None
        escribir(leer(ruta, encoding=encoding), ruta, encoding=encoding)
        log.warning("config.ini tenía una clave o sección repetida (%s): reparado%s", problema,
                    f"; el roto queda en {copia}" if copia else "")
        return True
