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
# Además, si la montura está en movimiento (un GoTo lanzado desde otra app,
# como SkySafari, que tarda más de un minuto), la foto espera a que termine y
# deja el asentamiento de [Montura]; si no, el apilado seguiría haciendo fotos
# arrastradas durante el viaje, porque ScopePower solo espera a los GoTo suyos.
#
# No actúa mientras el pad mueve los ejes (MoveAxis apaga el seguimiento a
# propósito): para eso está `permitir`. Solo se usa en capturas de noche
# (apilado, foto de prueba, centrado); de día (Sol, Luna, paisaje) el
# seguimiento lo lleva foto_dia.py con su propia velocidad.

import time
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
        self.esperas = 0                     # fotos pospuestas por un movimiento ajeno
        self.ultima_espera = None            # {"hora", "motivo", "segundos"}

    def asegurar(self, montura, motivo="la foto", voz=None, permitir=None, timeout=8,
                 esperar_movimiento=True, max_espera_s=150.0, asentamiento_s=None, dormir=time.sleep):
        """
        Comprueba el seguimiento por ASCOM y lo reactiva si está apagado. Si la
        montura está en movimiento (GoTo desde otra app), antes espera a que
        termine (hasta `max_espera_s`) y deja `asentamiento_s` de reposo.
        Devuelve True si está activo, "reactivado" si estaba apagado y se ha
        encendido, False si no se pudo encender, y None si no se comprobó
        (montura sin conectar, movimiento que no termina, `permitir()` falso o error).
        """
        if montura is None or not montura.esta_conectado():
            return self._anotar("omitida")
        if permitir is not None and not permitir():
            return self._anotar("omitida")

        if esperar_movimiento:
            try:
                en_movimiento = bool(montura.ejecutar(lambda tel: tel.Slewing, timeout=timeout))
            except Exception as e:
                log.warning("Vigilante del seguimiento: no se pudo leer si la montura se mueve antes de %s: %s", motivo, e)
                return self._anotar("error")
            if en_movimiento:
                t0 = time.perf_counter()
                log.warning("⏸ Foto pospuesta: la montura está en movimiento antes de %s (¿GoTo desde otra aplicación?); "
                            "se espera a que llegue", motivo)
                while time.perf_counter() - t0 < max_espera_s:
                    dormir(0.5)
                    try:
                        if not montura.ejecutar(lambda tel: tel.Slewing, timeout=timeout):
                            break
                    except Exception:
                        break
                else:
                    log.error("❌ La montura sigue en movimiento tras %.0f s: %s se hace igualmente", max_espera_s, motivo)
                    return self._anotar("omitida")
                if asentamiento_s is None:
                    try:
                        from hardware import asentamiento_montura
                        asentamiento_s = asentamiento_montura()
                    except Exception:
                        asentamiento_s = 5.0
                dormir(max(0.0, float(asentamiento_s)))
                segundos = time.perf_counter() - t0
                with self._lock:
                    self.esperas += 1
                    self.ultima_espera = {"hora": datetime.now().strftime("%H:%M:%S"), "motivo": motivo,
                                          "segundos": round(segundos, 1)}
                log.warning("▶ Movimiento terminado y asentado: %s sigue tras %.0f s de espera", motivo, segundos)

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
                    "ultima_comprobacion": self.ultima_comprobacion,
                    "esperas": self.esperas, "ultima_espera": dict(self.ultima_espera) if self.ultima_espera else None}


vigilante = VigilanteSeguimiento()
