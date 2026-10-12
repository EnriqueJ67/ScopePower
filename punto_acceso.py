# punto_acceso.py
#
# Punto de acceso de Windows en el mini PC ([Red] punto_acceso = true): la tablet
# se conecta a la red que emite el propio mini PC, que es siempre 192.168.137.1
# (el punto de acceso de Android cambia de red cada vez que se enciende). El wifi
# integrado sigue conectado a la vez al módulo del SynScan.
#
# El trabajo lo hace herramientas/punto_acceso.ps1 en modo «vigilar»: lo enciende
# al arrancar, lo vuelve a encender si se apaga (cambio de red, montura apagada
# para cambiar la batería...) y escribe una línea por cada cambio, que aquí se
# pasa al log. Termina solo cuando se cierra ScopePower.

import os
import atexit
import logging
import subprocess
import threading

log = logging.getLogger("scopepower.punto_acceso")

RUTA_PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "herramientas", "punto_acceso.ps1")
IP = "192.168.137.1"


def activado(config):
    try:
        return config.getboolean("Red", "punto_acceso", fallback=False)
    except ValueError:
        return False


def iniciar(config):
    """Lanza el vigilante si está activado en config.ini. Devuelve el proceso o None."""
    if not activado(config):
        return None
    if os.name != "nt" or not os.path.exists(RUTA_PS1):
        log.warning("Punto de acceso activado en config.ini pero no se puede usar aquí (%s)", RUTA_PS1)
        return None
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", RUTA_PS1,
             "vigilar", "-Padre", str(os.getpid())],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.warning("No se pudo lanzar el vigilante del punto de acceso: %s", e)
        return None

    def _leer():
        for linea in proc.stdout:
            linea = linea.strip()
            if linea:
                log.info("%s", linea)
        log.info("Vigilante del punto de acceso terminado (código %s)", proc.wait())

    threading.Thread(target=_leer, name="punto-acceso", daemon=True).start()
    atexit.register(lambda: proc.poll() is None and proc.terminate())
    log.info("Vigilante del punto de acceso iniciado: la tablet entra por http://%s", IP)
    return proc
