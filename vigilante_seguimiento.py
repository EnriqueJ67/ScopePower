# vigilante_seguimiento.py
#
# Comprueba, antes de cada foto de noche, que la montura sigue el cielo, y si
# el seguimiento sidéreo está apagado lo reactiva y lo deja escrito en el log.
#
# Motivo: una noche en el campo las fotos salieron con las estrellas arrastradas
# y no se supo quién había apagado el seguimiento (¿otra app conectada a SynScan
# Pro, un movimiento manual, la propia app?). ScopePower solo lo activaba al
# conectar y al soltar sus flechas o su pad; cualquier otra cosa que lo apagara
# pasaba desapercibida hasta ver las fotos. Probado el 2026-09-22 que SkySafari
# conectado a SynScan Pro por TCP no lo apaga (pad, GoTo ni cierre), así que el
# culpable sigue sin nombre: este vigilante cubre todos los casos y, la próxima
# vez, el log dirá en qué momento estaba apagado.
#
# No toca nada si la montura está haciendo un GoTo (durante el viaje el driver
# dice Tracking = False) ni mientras el pad mueve los ejes (MoveAxis también lo
# apaga temporalmente): para eso está `permitir`. Solo se usa en capturas de
# noche (apilado, foto de prueba, centrado); de día (Sol, Luna, paisaje) el
# seguimiento lo lleva foto_dia.py con su propia velocidad.

import logging
import threading
from datetime import datetime

log = logging.getLogger("scopepower.vigilante_seguimiento")


class VigilanteSeguimiento:
    def __init__(self):
        self._lock = threading.Lock()
        self.reactivaciones = 0
        self.fallos = 0
        self.ultima_reactivacion = None      # {"hora", "motivo"}
        self.ultima_comprobacion = None      # "activo" | "reactivado" | "no_reactivable" | "omitida" | "error"

    def asegurar(self, montura, motivo="la foto", voz=None, permitir=None, timeout=8):
        """
        Comprueba el seguimiento por ASCOM y lo reactiva si está apagado.
        Devuelve True si está activo, "reactivado" si estaba apagado y se ha
        encendido, False si no se pudo encender, y None si no se comprobó
        (montura sin conectar, GoTo en curso, `permitir()` falso o error).
        """
        if montura is None or not montura.esta_conectado():
            return self._anotar("omitida")
        if permitir is not None and not permitir():
            return self._anotar("omitida")

        def _comprobar(tel):
            try:
                if tel.Slewing:
                    return None
            except Exception:
                pass
            if bool(tel.Tracking):
                return True
            tel.Tracking = True
            return "reactivado" if bool(tel.Tracking) else False

        try:
            resultado = montura.ejecutar(_comprobar, timeout=timeout)
        except Exception as e:
            log.warning("Vigilante del seguimiento: no se pudo comprobar antes de %s: %s", motivo, e)
            return self._anotar("error")
        if resultado is None:
            return self._anotar("omitida")
        if resultado is True:
            return self._anotar("activo")
        if resultado == "reactivado":
            with self._lock:
                self.reactivaciones += 1
                self.ultima_reactivacion = {"hora": datetime.now().strftime("%H:%M:%S"), "motivo": motivo}
            log.warning("⚠️ El seguimiento sidéreo estaba DESACTIVADO antes de %s (¿otra app, un movimiento manual?): "
                        "reactivado (%d veces esta sesión)", motivo, self.reactivaciones)
            if voz is not None:
                try:
                    voz.decir("Seguimiento reactivado")
                except Exception:
                    pass
            return self._anotar("reactivado")
        with self._lock:
            self.fallos += 1
        log.error("❌ El seguimiento sidéreo está desactivado antes de %s y no se ha podido reactivar", motivo)
        return self._anotar("no_reactivable")

    def _anotar(self, que):
        with self._lock:
            self.ultima_comprobacion = que
        return {"omitida": None, "error": None, "activo": True, "reactivado": "reactivado", "no_reactivable": False}[que]

    def estado(self):
        with self._lock:
            return {"reactivaciones": self.reactivaciones, "fallos": self.fallos,
                    "ultima_reactivacion": dict(self.ultima_reactivacion) if self.ultima_reactivacion else None,
                    "ultima_comprobacion": self.ultima_comprobacion}


vigilante = VigilanteSeguimiento()
