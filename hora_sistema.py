# -*- coding: utf-8 -*-
"""
Poner en hora el reloj de Windows con la hora que manda el móvil.

Cambiar la hora del sistema exige el privilegio "Cambiar la hora del sistema"
(SeSystemtimePrivilege). Un usuario normal no lo tiene; instalar_scopepower.bat lo
concede al grupo Usuarios (hace efecto al iniciar sesión de nuevo). Sin él, aquí se
devuelve un mensaje claro en vez de fallar en silencio.
"""
import time
import logging
from datetime import datetime, timezone

from idioma import _

log = logging.getLogger("scopepower.hora")

ERROR_SIN_PERMISO = 1300   # ERROR_NOT_ALL_ASSIGNED: el privilegio no está en el token de este usuario


class SinPermisoHora(Exception):
    pass


def _activar_privilegio():
    import win32api, win32con, win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                           win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY)
    privilegio = win32security.LookupPrivilegeValue(None, "SeSystemtimePrivilege")
    win32security.AdjustTokenPrivileges(token, False, [(privilegio, win32con.SE_PRIVILEGE_ENABLED)])
    if win32api.GetLastError() == ERROR_SIN_PERMISO:
        raise SinPermisoHora(_(
            "Este usuario de Windows no tiene permiso para cambiar la hora. Ejecuta instalar_scopepower.bat "
            "en el PC (concede el permiso) y vuelve a iniciar sesión en Windows; o pon la hora a mano."))


def desfase_s(hora_utc_ms):
    """Segundos que el PC va adelantado (+) o atrasado (-) respecto a la hora recibida."""
    return (time.time() * 1000 - float(hora_utc_ms)) / 1000.0


def poner_en_hora(hora_utc_ms):
    """
    Ajusta el reloj del sistema a `hora_utc_ms` (milisegundos UTC desde 1970, como Date.now()).
    Devuelve el desfase que había, en segundos. Lanza SinPermisoHora si Windows no lo permite.
    """
    import win32api
    antes = desfase_s(hora_utc_ms)
    _activar_privilegio()
    dt = datetime.fromtimestamp(float(hora_utc_ms) / 1000.0, tz=timezone.utc)
    # SetSystemTime trabaja en UTC: no depende de la zona horaria configurada en Windows
    win32api.SetSystemTime(dt.year, dt.month, (dt.weekday() + 1) % 7, dt.day,
                           dt.hour, dt.minute, dt.second, dt.microsecond // 1000)
    log.info("Reloj del PC puesto en hora desde el móvil: corregidos %+.1f s", -antes)
    return antes
