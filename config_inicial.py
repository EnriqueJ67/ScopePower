# config_inicial.py
#
# Lo mínimo para que ScopePower arranque en un PC recién instalado y sepa si tiene
# que enseñar el asistente de configuración inicial (templates/_asistente.html).
#
#   asegurar_config()      si no hay config.ini, lo crea copiando config.example.ini
#                          (sin él la app no arrancaba: varios módulos lo leen al importarse)
#   asistente_pendiente()  [General] asistente_pendiente = true en un config.ini recién
#                          creado; sin la clave (instalaciones anteriores) se da por hecho
#   hay_usuarios()         algún usuario de [Usuarios] tiene contraseña
#   ruta_astap()           ruta de ASTAP leída en el momento, para que una instalación
#                          desde el asistente valga sin reiniciar

import configparser
import os
import shutil

CONFIG = "config.ini"
EJEMPLO = "config.example.ini"


def asegurar_config():
    """Crea config.ini desde la plantilla si no existe. Devuelve True si lo ha creado."""
    if os.path.exists(CONFIG) or not os.path.exists(EJEMPLO):
        return False
    shutil.copyfile(EJEMPLO, CONFIG)
    print("⚙️ config.ini no existía: creado desde config.example.ini (se abrirá el asistente de configuración)")
    return True


def _leer():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG)
    return cfg


def asistente_pendiente():
    return _leer().get("General", "asistente_pendiente", fallback="false").strip().lower() == "true"


def hay_usuarios():
    cfg = _leer()
    return "Usuarios" in cfg and any(v.strip() for v in cfg["Usuarios"].values())


def ruta_astap():
    """Ruta de astap.exe (o astap_cli.exe) de [General]; las \\ dobles de la plantilla valen igual."""
    return _leer().get("General", "ruta_astap", fallback="").strip().replace("\\\\", "\\")
