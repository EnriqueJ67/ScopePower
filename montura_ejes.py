# montura_ejes.py
#
# Mover la montura por velocidad de eje (grados/segundo con signo) para el
# seguimiento de objetos en movimiento (seguimiento.py) y para el pad.
#
# Hay dos caminos posibles hasta los motores y no sabemos aún cuál responde
# mejor con la AZ GoTo real, así que los dos se ofrecen detrás de la misma
# interfaz (MonturaEjes) y se eligen por configuración:
#
#   MonturaASCOM          MoveAxis del driver ASCOM (ASCOM.SynScanMobile →
#                         SynScan Pro → wifi → montura). No toca nada de lo que
#                         ya usa la app, pero el pad ya enseñó que al driver no
#                         le sientan bien las ráfagas de MoveAxis distintos:
#                         aquí se limita la cadencia y se ignoran cambios
#                         menores que una banda muerta.
#   MonturaSkyWatcherUDP  Protocolo del controlador de motores Sky-Watcher
#                         directo al módulo wifi SynScan (UDP 192.168.4.1:11880,
#                         el mismo que usa INDI), sin pasar por SynScan Pro.
#                         Latencia de decenas de ms y velocidad ajustable en
#                         caliente. Ojo: el firmware distingue modo lento y
#                         rápido (umbral 128× sidéreo ≈ 0,53 °/s) y cambiar de
#                         modo o de sentido obliga a parar el eje antes.
#
# Convenio: eje 0 = acimut (canal 1 del protocolo), eje 1 = altura (canal 2).
# Velocidad positiva = la que el driver ASCOM llama positiva en MoveAxis, o la
# que hace crecer la cuenta del codificador (:j) en UDP. El signo real respecto
# al cielo lo fija la calibración de seguimiento.py, no este módulo.
#
# Las funciones del protocolo (codificar_24, decodificar_24, velocidad_a_periodo,
# interpretar_estado) son puras y se prueban con `python montura_ejes.py`.

import os
import json
import time
import socket
import logging
import threading
from concurrent.futures import Future

from idioma import _

log = logging.getLogger("scopepower.montura_ejes")

SIDEREO_DEG_S = 360.0 / 86164.0905          # 0,0041781 °/s
UMBRAL_RAPIDO_DEG_S = 128 * SIDEREO_DEG_S   # 0,5348 °/s: por encima el firmware exige el modo rápido
# Medido el 2026-09-22 con la AZ GoTo real: en modo lento el eje arranca 140 ms
# después de :J, va exactamente a la velocidad pedida, admite cambiar :I en
# caliente (0,2 → 0,4 °/s en 164 ms) y llega a freq/(6·pasos_por_grado) = 0,81 °/s.
# El modo rápido tarda 1,1–1,4 s en arrancar con rampa tipo GoTo y frena en más
# de un segundo: inútil para seguir objetos. Por eso el seguimiento va SIEMPRE en
# modo lento y recorta la velocidad a la máxima del modo lento; con
# udp_permitir_rapido = true se deja pasar a rápido por encima de ese máximo.
# (Por ASCOM el driver cambia solo de modo a 128× sidéreo, 0,53 °/s: ahí está el
# techo útil de ese camino.)
UMBRAL_RAPIDO_SEGUIMIENTO = 0.0
V_MAX_ASCOM = 0.5
BANDA_MUERTA_UDP = 0.01     # °/s: por debajo se para el eje en vez de invertir el sentido por ruido
PERIODO_MINIMO = 6                           # ticks de reloj mínimos por micropaso (EQMOD/INDI)


class ErrorMontura(Exception):
    """Fallo de comunicación con la montura (ASCOM o UDP)."""


# ═════════════════════════════════════════════════════════════════════════════
# Interfaz común
# ═════════════════════════════════════════════════════════════════════════════
class MonturaEjes:
    nombre = "base"

    def preparar(self):
        """Conecta, lee parámetros y deja la montura lista para recibir velocidades."""

    def velocidad(self, v_az, v_alt):
        """Velocidad de cada eje en °/s con signo. Devuelve True si se envió a la montura."""
        raise NotImplementedError

    def parar(self):
        """Para los dos ejes (bloqueante hasta confirmar)."""
        raise NotImplementedError

    def posicion(self):
        """(azimut, altura) en grados o None si no se sabe. No debe bloquear el bucle."""
        return None

    def posicion_ahora(self):
        """Como posicion() pero leída en el momento (puede bloquear); para la calibración empírica."""
        return self.posicion()

    def limites(self):
        """(v_max_az, v_max_alt) en °/s."""
        return (4.0, 4.0)

    def signos_cielo(self):
        """
        (signo_az, signo_alt): +1 si una velocidad positiva de ese eje hace crecer
        el acimut / la altura del cielo. La calibración por astrometría trabaja en
        acimut y altura, y necesita saber hacia dónde gira cada motor.
        """
        return (1, 1)

    def restaurar(self):
        """Deja la montura como estaba antes de preparar() (seguimiento sidéreo, etc.)."""

    def telemetria(self):
        return {"backend": self.nombre}

    def recortar(self, v_az, v_alt):
        ma, mal = self.limites()
        return max(-ma, min(ma, float(v_az))), max(-mal, min(mal, float(v_alt)))


# ═════════════════════════════════════════════════════════════════════════════
# ASCOM (MoveAxis)
# ═════════════════════════════════════════════════════════════════════════════
def rangos_axisrates(tel, eje):
    """
    Rangos (min, max) en °/s que admite MoveAxis en ese eje, o [] si el driver
    no lo dice. La colección AxisRates se recorre por enumeración COM: el
    índice de Item() es 1-based en unos drivers y 0-based en otros.
    """
    try:
        rates = tel.AxisRates(eje)
    except Exception:
        return []
    rangos = []
    try:
        for r in rates:
            rangos.append((float(r.Minimum), float(r.Maximum)))
    except Exception:
        rangos = []
    if not rangos:
        try:
            n = int(rates.Count)
        except Exception:
            return []
        for i in list(range(1, n + 1)) + list(range(0, n)):
            try:
                r = rates.Item(i)
                par = (float(r.Minimum), float(r.Maximum))
                if par not in rangos:
                    rangos.append(par)
            except Exception:
                continue
    return rangos


_axisrates_anotados = False


def anotar_axisrates(tel):
    """Deja en el log, una vez, las velocidades que anuncia el driver (para saber qué acepta la montura)."""
    global _axisrates_anotados
    if _axisrates_anotados:
        return
    _axisrates_anotados = True
    for eje in (0, 1):
        rangos = rangos_axisrates(tel, eje)
        log.info("Pad: AxisRates eje %d: %s °/s", eje,
                 ", ".join(f"{lo:g}–{hi:g}" if lo != hi else f"{lo:g}" for lo, hi in rangos) or "no lo dice")


def mover_ejes_ascom(tel, vx, vy, tracking_tras_parar=None):
    """
    MoveAxis en los dos ejes (la lógica del pad y de las flechas). Al parar
    (vx = vy = 0) reactiva el seguimiento sidéreo si estaba activo antes de la
    orden, o el que indique `tracking_tras_parar` si se pasa.
    """
    anotar_axisrates(tel)
    estaba_tracking = tel.Tracking
    if tel.CanMoveAxis(0):
        tel.MoveAxis(0, vx)
    if tel.CanMoveAxis(1):
        tel.MoveAxis(1, vy)
    if vx == 0 and vy == 0:
        quiere = estaba_tracking if tracking_tras_parar is None else tracking_tras_parar
        if quiere and not tel.Tracking:
            tel.Tracking = True
    return "ok"


class MonturaASCOM(MonturaEjes):
    """
    Velocidad por eje con MoveAxis a través del TrabajadorCOM de la app.

    Las órdenes se mandan sin bloquear el bucle: si la anterior aún no ha
    terminado en el hilo COM (SynScan Pro puede tardar), la nueva se guarda como
    pendiente y se manda en el siguiente ciclo. Entre dos MoveAxis distintos se
    respeta `intervalo_min_s`, y los cambios menores que `banda_muerta` no se
    mandan. `redondeo` (p. ej. 0,05) cuantiza la velocidad por si la montura
    real solo digiere valores "redondos".
    """
    nombre = "ascom"

    def __init__(self, trabajador, intervalo_min_s=0.4, banda_muerta=0.02, redondeo=None, timeout=10,
                 v_max=V_MAX_ASCOM):
        self._t = trabajador
        self.v_max = float(v_max)
        self.intervalo_min_s = float(intervalo_min_s)
        self.banda_muerta = float(banda_muerta)
        self.redondeo = float(redondeo) if redondeo else None
        self.timeout = timeout
        self._limites = (4.0, 4.0)
        self._tracking_inicial = None
        self._enviada = (0.0, 0.0)
        self._pendiente = None
        self._futuro = None
        self._t_envio = 0.0
        self._latencia_ms = None
        self._pos = None
        self._pos_futuro = None
        self._pos_t = 0.0
        self.errores = 0
        self.enviadas = 0

    def preparar(self):
        from hardware import ErrorHardware

        def _leer(tel):
            anotar_axisrates(tel)
            lim = []
            for eje in (0, 1):
                rangos = rangos_axisrates(tel, eje)
                lim.append(max((hi for __, hi in rangos), default=4.0) or 4.0)
            tracking = bool(tel.Tracking)
            try:
                pos = (float(tel.Azimuth), float(tel.Altitude))
            except Exception:
                pos = None
            return lim, tracking, pos
        try:
            lim, self._tracking_inicial, self._pos = self._t.ejecutar(_leer, timeout=self.timeout)
        except ErrorHardware as e:
            raise ErrorMontura(str(e)) from e
        self._limites = (min(float(lim[0]), self.v_max), min(float(lim[1]), self.v_max))
        self._pos_t = time.perf_counter()
        log.info("Montura ASCOM lista: límites %s °/s, tracking=%s, posición=%s", self._limites,
                 self._tracking_inicial, self._pos)

    def limites(self):
        return self._limites

    def _cuantizar(self, v):
        if self.redondeo:
            v = round(v / self.redondeo) * self.redondeo
        return round(v, 4)

    def velocidad(self, v_az, v_alt):
        v_az, v_alt = self.recortar(v_az, v_alt)
        v = (self._cuantizar(v_az), self._cuantizar(v_alt))
        ahora = time.perf_counter()
        # ¿Terminó la orden anterior?
        if self._futuro is not None:
            if not self._futuro.done():
                self._pendiente = v
                return False
            self._recoger(self._futuro)
            self._futuro = None
        if v == self._enviada:
            self._pendiente = None
            return False
        cambio = max(abs(v[0] - self._enviada[0]), abs(v[1] - self._enviada[1]))
        parada = v == (0.0, 0.0)
        if not parada and cambio < self.banda_muerta:
            self._pendiente = None
            return False
        if not parada and ahora - self._t_envio < self.intervalo_min_s:
            self._pendiente = v
            return False
        self._enviar(v)
        return True

    def _enviar(self, v):
        self._enviada = v
        self._pendiente = None
        self._t_envio = time.perf_counter()
        t0 = self._t_envio
        tracking = self._tracking_inicial

        def _mover(tel):
            mover_ejes_ascom(tel, v[0], v[1], tracking_tras_parar=tracking)
            return time.perf_counter() - t0
        self._futuro = self._t.ejecutar_async(_mover)
        self.enviadas += 1

    def _recoger(self, futuro):
        try:
            self._latencia_ms = futuro.result(timeout=0) * 1000.0
        except Exception as e:
            self.errores += 1
            log.warning("Montura ASCOM: MoveAxis falló: %s", e)

    def pendiente(self):
        """Manda la orden pendiente si la hay (llamar en cada ciclo aunque no cambie la consigna)."""
        if self._pendiente is not None:
            return self.velocidad(*self._pendiente)
        return False

    def parar(self):
        from hardware import ErrorHardware
        self._pendiente = None
        if self._futuro is not None:
            try:
                self._futuro.result(timeout=self.timeout)
            except Exception:
                pass
            self._futuro = None
        tracking = self._tracking_inicial
        try:
            self._t.ejecutar(lambda tel: mover_ejes_ascom(tel, 0.0, 0.0, tracking_tras_parar=tracking),
                             timeout=self.timeout)
        except ErrorHardware as e:
            self.errores += 1
            raise ErrorMontura(str(e)) from e
        finally:
            self._enviada = (0.0, 0.0)

    def posicion(self):
        ahora = time.perf_counter()
        if self._pos_futuro is not None and self._pos_futuro.done():
            try:
                self._pos = self._pos_futuro.result(timeout=0)
            except Exception:
                pass
            self._pos_futuro = None
        if self._pos_futuro is None and ahora - self._pos_t >= 1.0 and (self._futuro is None or self._futuro.done()):
            self._pos_t = ahora
            self._pos_futuro = self._t.ejecutar_async(lambda tel: (float(tel.Azimuth), float(tel.Altitude)))
        return self._pos

    def posicion_ahora(self):
        from hardware import ErrorHardware
        try:
            self._pos = self._t.ejecutar(lambda tel: (float(tel.Azimuth), float(tel.Altitude)), timeout=self.timeout)
        except ErrorHardware as e:
            raise ErrorMontura(str(e)) from e
        return self._pos

    def restaurar(self):
        # parar() ya deja el tracking como estaba
        pass

    def telemetria(self):
        return {"backend": self.nombre, "latencia_ms": self._latencia_ms, "enviadas": self.enviadas,
                "errores": self.errores, "v_enviada": list(self._enviada), "limites": list(self._limites)}


# ═════════════════════════════════════════════════════════════════════════════
# Protocolo Sky-Watcher por UDP (módulo wifi SynScan)
# ═════════════════════════════════════════════════════════════════════════════
def codificar_24(valor):
    """Entero de 24 bits → 6 hex con los bytes en orden inverso (0x123456 → '563412')."""
    v = int(valor) & 0xFFFFFF
    return f"{v & 0xFF:02X}{(v >> 8) & 0xFF:02X}{(v >> 16) & 0xFF:02X}"


def decodificar_24(texto):
    """'563412' → 0x123456. Admite respuestas de 2, 4 o 6 hex (bytes en orden inverso)."""
    t = texto.strip()
    if len(t) not in (2, 4, 6):
        raise ValueError(_("Respuesta de longitud inesperada: {respuesta}", respuesta=repr(texto)))
    pares = [t[i:i + 2] for i in range(0, len(t), 2)]
    return int("".join(reversed(pares)), 16)


def velocidad_a_periodo(v_deg_s, cpr, freq, ratio, rapido_actual=None, version=0, umbral=UMBRAL_RAPIDO_SEGUIMIENTO):
    """
    Velocidad en °/s → (periodo T para :I, modo rápido) según los parámetros del
    controlador: pasos por vuelta (:a), frecuencia del temporizador (:b) y ratio
    de alta velocidad (:D). Con histéresis del 10 % respecto al modo actual para
    no oscilar en torno al umbral. Devuelve None si la velocidad es nula.
    """
    pasos_s = abs(float(v_deg_s)) * cpr / 360.0
    if pasos_s <= 0:
        return None
    # Histéresis asimétrica: cambiar de modo obliga a parar el eje, así que una
    # vez en rápido se sigue en rápido hasta bastante por debajo del umbral.
    # Por encima del umbral del firmware (128× sidéreo) el modo rápido es obligatorio.
    if umbral == float("inf"):
        rapido = False                       # seguimiento: siempre modo lento (T se satura en el mínimo)
    else:
        umbral = min(umbral, UMBRAL_RAPIDO_DEG_S)
        if rapido_actual is True:
            umbral *= 0.6
        elif rapido_actual is False:
            umbral *= 1.05
        rapido = umbral <= 0 or abs(v_deg_s) > umbral or abs(v_deg_s) > UMBRAL_RAPIDO_DEG_S * 1.05
    T = freq / pasos_s
    if rapido:
        T *= max(1, ratio)
    T = int(round(T))
    if version in (0x010600, 0x010601):       # arreglo de EQMOD/INDI para el firmware 1.6
        T -= 3
    return max(PERIODO_MINIMO, T), rapido


def periodo_a_velocidad(T, rapido, cpr, freq, ratio):
    pasos_s = freq / max(1, T) * (max(1, ratio) if rapido else 1)
    return pasos_s * 360.0 / cpr


def interpretar_estado(texto):
    """Respuesta de :f ('xyz' en hex) → dict con en_marcha, bloqueado, inicializado, modo_slew, sentido_cw, rapido."""
    t = texto.strip()
    if len(t) < 3:
        raise ValueError(_("Estado inesperado: {respuesta}", respuesta=repr(texto)))
    x, y, z = int(t[0], 16), int(t[1], 16), int(t[2], 16)
    return {"modo_slew": bool(x & 1), "sentido_cw": bool(x & 2), "rapido": bool(x & 4),
            "en_marcha": bool(y & 1), "bloqueado": bool(y & 2), "inicializado": bool(z & 1)}


class MonturaSkyWatcherUDP(MonturaEjes):
    """
    Velocidad por eje hablando directamente con el controlador de motores por
    UDP. Si se pasa `trabajador_ascom` (el TrabajadorCOM de la montura), antes
    de mandar velocidades se desactiva el seguimiento sidéreo en SynScan Pro
    (si no, la app reenvía sus propias velocidades y pelea con nosotros) y se
    restaura al final; también se usa para leer la altura absoluta.
    """
    nombre = "udp"
    CANALES = ("1", "2")

    def __init__(self, host="192.168.4.1", puerto=11880, timeout_s=0.5, reintentos=2,
                 trabajador_ascom=None, cambio_en_caliente=True, umbral_rapido=UMBRAL_RAPIDO_SEGUIMIENTO,
                 permitir_rapido=False):
        self.host, self.puerto = host, int(puerto)
        self.timeout_s, self.reintentos = float(timeout_s), int(reintentos)
        self._t = trabajador_ascom
        self.cambio_en_caliente = cambio_en_caliente
        self.umbral_rapido = float(umbral_rapido)
        self.permitir_rapido = bool(permitir_rapido)
        self._sock = None
        self._lock = threading.RLock()
        self.version = {}
        self.cpr = [0, 0]
        self.freq = [0, 0]
        self.ratio = [1, 1]
        self.periodo_sidereo = [None, None]
        self._eje = [{"v": 0.0, "dir": None, "rapido": None, "marcha": False, "T": None, "espera": None} for __ in (0, 1)]
        self.cambios_de_modo = 0
        self.cruzadas = 0
        self._signos = self._leer_signos()
        self._pos0 = [None, None]
        self._tracking_inicial = None
        self._pos_ascom = None
        self._pos_futuro = None
        self._pos_t = 0.0
        self._latencia_ms = None
        self.errores = 0
        self.enviadas = 0
        self._limites = (4.0, 4.0)

    # ── Transporte ────────────────────────────────────────────────────────
    def conectar(self):
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.settimeout(self.timeout_s)

    def cerrar(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # Longitud de la carga que devuelve cada orden ('=' aparte). El módulo wifi nos
    # reenvía también las respuestas a las consultas de SynScan Pro (medido el
    # 2026-09-22 con la AZ GoTo real: 15–20 % de respuestas cruzadas), así que
    # una respuesta de longitud distinta a la esperada se descarta y se sigue
    # esperando la buena. Sin esto una parada puede darse por confirmada sin serlo.
    LONGITUDES = {"e": (6,), "a": (6,), "b": (6,), "D": (2, 6), "g": (2, 6), "f": (3,), "j": (6,), "h": (6,), "s": (6,),
                  "G": (0,), "I": (0,), "J": (0,), "K": (0,), "L": (0,), "F": (0,), "S": (0,), "M": (0,), "H": (0,)}

    def enviar(self, cmd, canal="1", datos=""):
        """Manda ':cmd canal datos\\r' y devuelve la carga de la respuesta ('=' quitado)."""
        self.conectar()
        trama = f":{cmd}{canal}{datos}\r".encode("ascii")
        esperadas = self.LONGITUDES.get(cmd)
        ultimo = None
        with self._lock:
            for intento in range(self.reintentos + 1):
                t0 = time.perf_counter()
                try:
                    # Vaciar respuestas atrasadas
                    self._sock.setblocking(False)
                    try:
                        while True:
                            self._sock.recvfrom(64)
                    except (BlockingIOError, OSError):
                        pass
                    self._sock.settimeout(self.timeout_s)
                    self._sock.sendto(trama, (self.host, self.puerto))
                    limite = time.perf_counter() + self.timeout_s
                    while True:
                        resto = limite - time.perf_counter()
                        if resto <= 0:
                            raise socket.timeout()
                        self._sock.settimeout(resto)
                        datos_rx, __ = self._sock.recvfrom(64)
                        r = datos_rx.decode("ascii", errors="replace").strip("\r\n ")
                        if r.startswith("!"):
                            break
                        if r.startswith("=") and (esperadas is None or len(r) - 1 in esperadas):
                            break
                        self.cruzadas += 1          # respuesta a otra consulta: seguir esperando la nuestra
                    self._latencia_ms = (time.perf_counter() - t0) * 1000.0
                    self.enviadas += 1
                    if r.startswith("!"):
                        raise ErrorMontura(_("La montura rechazó {orden} (error {codigo})", orden=f":{cmd}{canal}{datos}",
                                              codigo=r[1:] or "?"))
                    return r[1:]
                except (socket.timeout, OSError) as e:
                    ultimo = e
                    continue
        self.errores += 1
        raise ErrorMontura(_("Sin respuesta de la montura en {direccion} a {orden} ({error})",
                             direccion=f"{self.host}:{self.puerto}", orden=f":{cmd}{canal}", error=ultimo))

    def leer_24(self, cmd, canal):
        return decodificar_24(self.enviar(cmd, canal))

    # ── Preparación ───────────────────────────────────────────────────────
    def preparar(self):
        self.conectar()
        self.version = self.leer_version()
        for i, c in enumerate(self.CANALES):
            self.cpr[i] = self.leer_24("a", c)
            self.freq[i] = self.leer_24("b", c)
            # :g = ratio de alta velocidad (32 en la AZ GoTo); :D es el periodo de
            # seguimiento sidéreo T1 (1158 ticks en la AZ GoTo), no el ratio
            try:
                self.ratio[i] = self.leer_24("g", c)
            except (ErrorMontura, ValueError):
                self.ratio[i] = 1
            try:
                self.periodo_sidereo[i] = self.leer_24("D", c)
            except (ErrorMontura, ValueError):
                self.periodo_sidereo[i] = None
            estado = self.estado(i)
            if not estado["inicializado"]:
                self.enviar("F", c)
            self._pos0[i] = self.leer_24("j", c)
            if self.cpr[i] <= 0 or self.freq[i] <= 0:
                raise ErrorMontura(_("Parámetros del motor {eje} no válidos (a={a}, b={b})", eje=c, a=self.cpr[i],
                                      b=self.freq[i]))
        if not self.permitir_rapido:
            # Techo del modo lento: T mínimo de PERIODO_MINIMO ticks por micropaso
            self._limites = tuple(self.freq[i] / PERIODO_MINIMO * 360.0 / self.cpr[i] for i in (0, 1))
        if self._t is not None and None in self._signos:
            self._detectar_signos()
        if self._t is not None:
            from hardware import ErrorHardware
            try:
                self._tracking_inicial, self._pos_ascom = self._t.ejecutar(self._leer_ascom_y_quitar_tracking, timeout=10)
            except ErrorHardware as e:
                raise ErrorMontura(_("No se pudo desactivar el seguimiento en SynScan Pro: {e}", e=e)) from e
        log.info("Montura UDP lista: versión %s, cpr %s, freq %s, ratio %s, límites %s °/s, tracking inicial %s",
                 self.version.get("texto"), self.cpr, self.freq, self.ratio,
                 tuple(round(v, 3) for v in self._limites), self._tracking_inicial)

    # ── Hacia dónde gira cada motor ──────────────────────────────────────
    RUTA_SIGNOS = os.path.join("datos", "signos_udp.json")

    def _leer_signos(self):
        try:
            with open(self.RUTA_SIGNOS, encoding="utf-8") as f:
                d = json.load(f)
            return [d.get("az"), d.get("alt")]
        except (OSError, ValueError):
            return [None, None]

    def _guardar_signos(self):
        try:
            os.makedirs(os.path.dirname(self.RUTA_SIGNOS), exist_ok=True)
            with open(self.RUTA_SIGNOS, "w", encoding="utf-8") as f:
                json.dump({"az": self._signos[0], "alt": self._signos[1],
                           "fecha": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
        except OSError as e:
            log.warning("Montura UDP: no se pudieron guardar los signos de los ejes: %s", e)

    def _detectar_signos(self, espera_s=2.5):
        """
        Sin mover nada: mientras SynScan Pro sigue el cielo, compara hacia dónde
        cambian los codificadores (:j) y el acimut/altura que da el driver ASCOM.
        Un eje que no se mueve lo bastante (la altura en el meridiano) queda sin
        saber y se intentará la próxima vez.
        """
        def leer():
            enc = [self.leer_24("j", c) for c in self.CANALES]
            cielo = self._t.ejecutar(lambda tel: (float(tel.Azimuth), float(tel.Altitude)), timeout=10)
            return enc, cielo
        try:
            e0, c0 = leer()
            time.sleep(espera_s)
            e1, c1 = leer()
        except Exception as e:
            log.warning("Montura UDP: no se pudo comprobar el sentido de los motores: %s", e)
            return
        for i in (0, 1):
            if self._signos[i] is not None:
                continue
            d_enc = (e1[i] - e0[i]) * 360.0 / self.cpr[i]
            d_cielo = ((c1[i] - c0[i] + 180.0) % 360.0) - 180.0
            if abs(d_cielo) > 0.003 and abs(d_enc) > 0.003 and 0.3 < abs(d_enc / d_cielo) < 3.0:
                self._signos[i] = 1 if d_enc * d_cielo > 0 else -1
                log.info("Montura UDP: el eje %d gira %s respecto al cielo (codificador %+.4f°, cielo %+.4f°)",
                         i, "igual" if self._signos[i] > 0 else "al revés", d_enc, d_cielo)
        if None not in self._signos:
            self._guardar_signos()
        else:
            log.warning("Montura UDP: sentido de algún eje sin comprobar (¿seguimiento parado o en el meridiano?): "
                        "se supone el mismo que el cielo")

    def signos_cielo(self):
        return tuple(1 if s is None else int(s) for s in self._signos)

    @staticmethod
    def _leer_ascom_y_quitar_tracking(tel):
        tracking = bool(tel.Tracking)
        try:
            pos = (float(tel.Azimuth), float(tel.Altitude))
        except Exception:
            pos = None
        if tracking:
            tel.Tracking = False
        return tracking, pos

    def leer_version(self):
        r = self.enviar("e", "1")
        try:
            v = decodificar_24(r)
        except ValueError:
            return {"texto": r, "raw": r}
        # Como INDI: la versión son los dos primeros bytes y el tercero es el código de montura
        mayor, menor, codigo = (v & 0xFF), (v >> 8) & 0xFF, (v >> 16) & 0xFF
        return {"texto": _("{version} (montura 0x{codigo})", version=f"{mayor}.{menor:02d}", codigo=f"{codigo:02X}"),
                "raw": r,
                "valor": (mayor << 16) | (menor << 8) | codigo, "codigo_montura": codigo}

    def estado(self, eje):
        return interpretar_estado(self.enviar("f", self.CANALES[eje]))

    def parado_confirmado(self, eje, lecturas=2):
        """True solo si `lecturas` consultas seguidas de :f dicen que el eje no está en marcha."""
        for __ in range(lecturas):
            try:
                if self.estado(eje)["en_marcha"]:
                    return False
            except ErrorMontura:
                return False
        return True

    # ── Movimiento ────────────────────────────────────────────────────────
    def limites(self):
        return self._limites

    def velocidad(self, v_az, v_alt):
        v_az, v_alt = self.recortar(v_az, v_alt)
        enviado = False
        for eje, v in ((0, v_az), (1, v_alt)):
            enviado |= self._velocidad_eje(eje, v)
        return enviado

    def _velocidad_eje(self, eje, v):
        """
        Consigna de un eje. Si hay que cambiar de sentido o de modo, manda parar
        y deja la nueva consigna en espera: `pendiente()` (que el bucle llama en
        cada fotograma) la arranca cuando el eje confirma la parada, sin
        bloquear la lectura de la cámara mientras tanto.
        """
        e = self._eje[eje]
        c = self.CANALES[eje]
        if abs(v) < BANDA_MUERTA_UDP:
            e["espera"] = None
            if e["marcha"]:
                self.enviar("K", c)
                e.update(marcha=False, v=0.0, T=None)
                return True
            return False
        res = velocidad_a_periodo(v, self.cpr[eje], self.freq[eje], self.ratio[eje],
                                  rapido_actual=e["rapido"] if e["marcha"] else None,
                                  version=self.version.get("valor", 0),
                                  umbral=self.umbral_rapido if self.permitir_rapido else float("inf"))
        T, rapido = res
        direccion = 0 if v > 0 else 1
        if e.get("espera"):
            # Ya estamos parando para cambiar: actualizar la consigna que arrancará
            e["espera"]["consigna"] = (v, T, direccion, rapido)
            return self._avanzar_espera(eje)
        if e["marcha"] and e["T"] == T and e["dir"] == direccion and e["rapido"] == rapido:
            return False
        if e["marcha"] and e["dir"] == direccion and e["rapido"] == rapido and self.cambio_en_caliente:
            self.enviar("I", c, codificar_24(T))          # cambio de velocidad sin parar
            e.update(v=v, T=T)
            return True
        if e["marcha"]:
            self.enviar("K", c)
            e.update(marcha=False, v=0.0, T=None)
            e["espera"] = {"t0": time.perf_counter(), "consigna": (v, T, direccion, rapido)}
            self.cambios_de_modo += 1
            return True
        self._arrancar(eje, v, T, direccion, rapido)
        return True

    def _arrancar(self, eje, v, T, direccion, rapido):
        c = self.CANALES[eje]
        self.enviar("G", c, f"{3 if rapido else 1}{direccion}")
        self.enviar("I", c, codificar_24(T))
        self.enviar("J", c)
        self._eje[eje].update(marcha=True, v=v, T=T, dir=direccion, rapido=rapido, espera=None)

    def _avanzar_espera(self, eje, maximo_s=1.5):
        """Si el eje que estaba parando ya está quieto (o pasó el tope), arranca la consigna en espera."""
        e = self._eje[eje]
        esp = e.get("espera")
        if not esp:
            return False
        try:
            quieto = not self.estado(eje)["en_marcha"]
        except ErrorMontura:
            quieto = False
        if quieto or time.perf_counter() - esp["t0"] > maximo_s:
            if not quieto:
                log.warning("Montura UDP: el eje %d no confirmó la parada en %.1f s", eje, maximo_s)
            self._arrancar(eje, *esp["consigna"])
            return True
        return False

    def pendiente(self):
        """Avanza los cambios de modo/sentido en curso (llamar en cada ciclo)."""
        enviado = False
        for eje in (0, 1):
            if self._eje[eje].get("espera"):
                enviado |= self._avanzar_espera(eje)
        return enviado

    def _esperar_parada(self, eje, maximo_s=1.5):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < maximo_s:
            if self.parado_confirmado(eje, lecturas=1):
                return True
            time.sleep(0.05)
        log.warning("Montura UDP: el eje %d no confirmó la parada en %.1f s", eje, maximo_s)
        return False

    def parar(self, maximo_s=6.0):
        """
        Para los dos ejes y NO vuelve hasta que cada uno confirma que está quieto.
        Una orden de parar puede perderse por el wifi (pasó el 2026-09-22: la
        montura siguió girando un minuto), así que se repite :K y, si sigue en
        marcha, :L (frenado en seco), hasta `maximo_s`; si aun así no confirma,
        lanza ErrorMontura para que quien llame avise.
        """
        for eje in (0, 1):
            self._eje[eje].update(marcha=False, v=0.0, T=None, espera=None)
        sin_confirmar = []
        for eje, c in enumerate(self.CANALES):
            t0 = time.perf_counter()
            intentos = 0
            while time.perf_counter() - t0 < maximo_s:
                try:
                    self.enviar("L" if intentos >= 2 else "K", c)
                except ErrorMontura as e:
                    log.warning("Montura UDP: parada del eje %d sin respuesta (%s); se reintenta", eje, e)
                intentos += 1
                time.sleep(0.15)
                if self.parado_confirmado(eje):
                    break
            else:
                sin_confirmar.append(eje)
        if sin_confirmar:
            raise ErrorMontura(_("La montura no confirma la parada de los ejes {ejes}: comprobar a mano",
                                ejes=sin_confirmar))

    def posicion_codificador(self):
        """Desplazamiento de cada eje en grados desde preparar() (según :j)."""
        out = []
        for i, c in enumerate(self.CANALES):
            p = self.leer_24("j", c)
            out.append((p - self._pos0[i]) * 360.0 / self.cpr[i] if self._pos0[i] is not None else 0.0)
        return tuple(out)

    def posicion_ahora(self):
        """Desplazamiento de los codificadores (grados de eje desde preparar): sirve para medir cuánto se movió."""
        return self.posicion_codificador()

    def posicion(self):
        if self._t is None:
            return None
        ahora = time.perf_counter()
        if self._pos_futuro is not None and self._pos_futuro.done():
            try:
                self._pos_ascom = self._pos_futuro.result(timeout=0)
            except Exception:
                pass
            self._pos_futuro = None
        if self._pos_futuro is None and ahora - self._pos_t >= 1.0:
            self._pos_t = ahora
            self._pos_futuro = self._t.ejecutar_async(lambda tel: (float(tel.Azimuth), float(tel.Altitude)))
        return self._pos_ascom

    def restaurar(self):
        try:
            self.parar()
        except ErrorMontura as e:
            log.error("Montura UDP: %s", e)
            raise
        finally:
            if self._t is not None and self._tracking_inicial:
                try:
                    self._t.ejecutar(lambda tel: setattr(tel, "Tracking", True), timeout=10)
                except Exception as e:
                    log.warning("Montura UDP: no se pudo restaurar el seguimiento: %s", e)
            self.cerrar()

    def telemetria(self):
        return {"backend": self.nombre, "latencia_ms": self._latencia_ms, "enviadas": self.enviadas,
                "errores": self.errores, "cruzadas": self.cruzadas, "version": self.version.get("texto"),
                "signos": list(self._signos),
                "cambios_de_modo": self.cambios_de_modo,
                "ejes": [{"v": e["v"], "T": e["T"], "rapido": e["rapido"], "marcha": e["marcha"],
                          "parando": bool(e.get("espera"))} for e in self._eje],
                "limites": list(self._limites)}


# ═════════════════════════════════════════════════════════════════════════════
def crear(conf, trabajador_ascom=None):
    """
    Fabrica el backend según un dict de configuración ([Seguimiento] de config.ini):
    backend = ascom | udp, udp_host, udp_puerto, intervalo_ascom_s, banda_muerta, redondeo_ascom.
    """
    backend = str(conf.get("backend", "ascom")).lower()
    if backend == "udp":
        return MonturaSkyWatcherUDP(host=conf.get("udp_host", "192.168.4.1"), puerto=int(conf.get("udp_puerto", 11880)),
                                    timeout_s=float(conf.get("udp_timeout_s", 0.5)), trabajador_ascom=trabajador_ascom,
                                    cambio_en_caliente=str(conf.get("udp_cambio_en_caliente", "true")).lower() != "false",
                                    umbral_rapido=float(conf.get("udp_umbral_rapido", UMBRAL_RAPIDO_SEGUIMIENTO)),
                                    permitir_rapido=str(conf.get("udp_permitir_rapido", "false")).lower() == "true")
    if trabajador_ascom is None:
        raise ErrorMontura(_("El backend ASCOM necesita el TrabajadorCOM de la montura"))
    redondeo = conf.get("redondeo_ascom")
    return MonturaASCOM(trabajador_ascom, intervalo_min_s=float(conf.get("intervalo_ascom_s", 0.4)),
                        banda_muerta=float(conf.get("banda_muerta", 0.02)),
                        redondeo=float(redondeo) if redondeo not in (None, "", "0") else None,
                        v_max=float(conf.get("v_max_ascom", V_MAX_ASCOM)))


if __name__ == "__main__":
    # Pruebas de las funciones puras del protocolo
    assert codificar_24(0x123456) == "563412"
    assert decodificar_24("563412") == 0x123456
    assert decodificar_24("10") == 0x10
    assert decodificar_24(codificar_24(0x800000)) == 0x800000
    e = interpretar_estado("511")
    assert e["modo_slew"] and e["rapido"] and e["en_marcha"] and e["inicializado"] and not e["sentido_cw"]
    # AZ-GTi típica: cpr 9216000? no; usamos valores redondos de ejemplo
    cpr, freq, ratio = 1_296_000, 64_935, 16
    T, rapido = velocidad_a_periodo(SIDEREO_DEG_S, cpr, freq, ratio, umbral=0.2)
    assert not rapido and abs(periodo_a_velocidad(T, rapido, cpr, freq, ratio) - SIDEREO_DEG_S) / SIDEREO_DEG_S < 0.01
    T, rapido = velocidad_a_periodo(1.0, cpr, freq, ratio)
    assert rapido and abs(periodo_a_velocidad(T, rapido, cpr, freq, ratio) - 1.0) < 0.05
    # Con umbral 0,2 °/s: histéresis asimétrica (×1,05 para subir, ×0,6 para bajar)
    assert velocidad_a_periodo(0.20, cpr, freq, ratio, rapido_actual=False, umbral=0.2)[1] is False
    assert velocidad_a_periodo(0.22, cpr, freq, ratio, rapido_actual=False, umbral=0.2)[1] is True
    assert velocidad_a_periodo(0.21, cpr, freq, ratio, rapido_actual=None, umbral=0.2)[1] is True
    assert velocidad_a_periodo(0.13, cpr, freq, ratio, rapido_actual=True, umbral=0.2)[1] is True
    assert velocidad_a_periodo(0.11, cpr, freq, ratio, rapido_actual=True, umbral=0.2)[1] is False
    # Por defecto (umbral 0) siempre rápido, también a velocidad sidérea
    assert velocidad_a_periodo(SIDEREO_DEG_S, cpr, freq, ratio)[1] is True
    T, __ = velocidad_a_periodo(0.05, cpr, freq, ratio)
    assert abs(periodo_a_velocidad(T, True, cpr, freq, ratio) - 0.05) < 0.001
    # Modo lento forzado (umbral infinito): nunca rápido, y por encima del máximo se satura en T mínimo
    cpr_az, freq_az = 1_452_951, 19_531          # AZ GoTo real
    v_max_lento = freq_az / PERIODO_MINIMO * 360.0 / cpr_az
    assert abs(v_max_lento - 0.807) < 0.01
    T, rapido = velocidad_a_periodo(0.6, cpr_az, freq_az, 16, umbral=float("inf"))
    assert rapido is False and abs(periodo_a_velocidad(T, False, cpr_az, freq_az, 16) - 0.6) < 0.03
    T, rapido = velocidad_a_periodo(2.0, cpr_az, freq_az, 16, umbral=float("inf"))
    assert rapido is False and T == PERIODO_MINIMO
    # Por encima del umbral del firmware siempre rápido, aunque se pida otro umbral
    assert velocidad_a_periodo(0.6, cpr, freq, ratio, rapido_actual=False, umbral=5.0)[1] is True
    assert velocidad_a_periodo(0.25, cpr, freq, ratio, umbral=5.0)[1] is False
    assert velocidad_a_periodo(0, cpr, freq, ratio) is None
    assert velocidad_a_periodo(50.0, cpr, freq, ratio)[0] == PERIODO_MINIMO
    print("✅ montura_ejes: pruebas del protocolo correctas")
