# secretos.py
#
# Secretos de la aplicación fuera de config.ini: clave de la API de
# Astrometry.net y clave de sesión de Flask.
#
# Se guardan en secretos.ini (ignorado por git). La ruta se puede cambiar con
# la variable de entorno SCOPEPOWER_SECRETOS para sacar el fichero de la
# carpeta sincronizada por OneDrive, por ejemplo:
#   set SCOPEPOWER_SECRETOS=C:\Users\<usuario>\scopepower_secretos.ini

import os
import sys
import secrets as _secrets
import configparser
import logging

log = logging.getLogger("scopepower.secretos")

SECCION = "Secretos"

# Valores por defecto.
_DEFECTOS = {
    "flask_secret_key": None,        # None = generar uno aleatorio
    "astrometry_api_key": "",
}

# Claves que antes vivían en config.ini: (sección, clave) para migrarlas
_EN_CONFIG_INI = {
    "astrometry_api_key": ("General", "astrometry_api_key"),
}


def _ruta_base(relativa):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relativa)
    return os.path.join(os.path.abspath("."), relativa)


def ruta_secretos():
    return os.environ.get("SCOPEPOWER_SECRETOS") or _ruta_base("secretos.ini")


def _leer():
    cfg = configparser.ConfigParser()
    cfg.read(ruta_secretos(), encoding="utf-8")
    if SECCION not in cfg:
        cfg[SECCION] = {}
    return cfg


def _escribir(cfg):
    ruta = ruta_secretos()
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("; Secretos de ScopePower. NO compartir ni subir a git.\n")
        cfg.write(f)


def obtener(clave, defecto=None):
    """
    Devuelve el secreto `clave`. Si no está en secretos.ini:
      - lo toma de config.ini si aún estuviera allí (y avisa),
      - o genera/aplica el valor por defecto y lo guarda.
    """
    cfg = _leer()
    valor = cfg[SECCION].get(clave)
    if valor not in (None, ""):
        return valor

    # Migración desde config.ini
    if clave in _EN_CONFIG_INI:
        seccion, clave_ini = _EN_CONFIG_INI[clave]
        # config.ini está en la codificación del sistema (cp1252), como lo lee el resto de la app
        ini = configparser.ConfigParser()
        ini.read(_ruta_base("config.ini"))
        heredado = ini.get(seccion, clave_ini, fallback="")
        if heredado:
            log.warning("El secreto '%s' sigue en config.ini; se copia a %s. "
                        "Borra la línea de config.ini.", clave, ruta_secretos())
            guardar(clave, heredado)
            return heredado

    if defecto is None:
        defecto = _DEFECTOS.get(clave)
    if defecto is None and clave == "flask_secret_key":
        defecto = _secrets.token_hex(32)
        log.info("Generada una clave de sesión nueva en %s", ruta_secretos())
    if defecto is None:
        return ""
    guardar(clave, defecto)
    return defecto


def guardar(clave, valor):
    cfg = _leer()
    cfg[SECCION][clave] = str(valor)
    _escribir(cfg)
