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

import time
import socket
import logging
import threading
from concurrent.futures import Future

log = logging.getLogger("scopepower.montura_ejes")

SIDEREO_DEG_S = 360.0 / 86164.0905          # 0,0041781 °/s
UMBRAL_RAPIDO_DEG_S = 128 * SIDEREO_DEG_S   # 0,5348 °/s: por encima el firmware exige el modo rápido
# El modo rápido (micropasos × ratio) vale también a velocidades menores, solo es
# algo menos fino (a 0,05 °/s da ~10 pasos/s y vibra). Como cambiar de modo
# obliga a parar el eje casi un segundo (y un objeto a 0,8 °/s se escapa del
# campo mientras tanto), para seguir objetos se va SIEMPRE en rápido (umbral 0);
# udp_umbral_rapido en config permite subirlo (p. ej. 0,2) para seguir algo lento.
UMBRAL_RAPIDO_SEGUIMIENTO = 0.0
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

    def __init__(self, trabajador, intervalo_min_s=0.4, banda_muerta=0.02, redondeo=None, timeout=10):
        self._t = trabajador
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
                lim.append(max((hi for _, hi in rangos), default=4.0) or 4.0)
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
        self._limites = (float(lim[0]), float(lim[1]))
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
        raise ValueError(f"Respuesta de longitud inesperada: {texto!r}")
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
        raise ValueError(f"Estado inesperado: {texto!r}")
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
                 trabajador_ascom=None, cambio_en_caliente=True, umbral_rapido=UMBRAL_RAPIDO_SEGUIMIENTO):
        self.host, self.puerto = host, int(puerto)
        self.timeout_s, self.reintentos = float(timeout_s), int(reintentos)
        self._t = trabajador_ascom
        self.cambio_en_caliente = cambio_en_caliente
        self.umbral_rapido = float(umbral_rapido)
        self._sock = None
        self._lock = threading.RLock()
        self.version = {}
        self.cpr = [0, 0]
        self.freq = [0, 0]
        self.ratio = [1, 1]
        self._eje = [{"v": 0.0, "dir": None, "rapido": None, "marcha": False, "T": None, "espera": None} for _ in (0, 1)]
        self.cambios_de_modo = 0
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

    def enviar(self, cmd, canal="1", datos=""):
        """Manda ':cmd canal datos\\r' y devuelve la carga de la respuesta ('=' quitado)."""
        self.conectar()
        trama = f":{cmd}{canal}{datos}\r".encode("ascii")
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
                    while True:
                        datos_rx, _ = self._sock.recvfrom(64)
                        r = datos_rx.decode("ascii", errors="replace").strip("\r\n ")
                        if r.startswith("=") or r.startswith("!"):
                            break
                    self._latencia_ms = (time.perf_counter() - t0) * 1000.0
                    self.enviadas += 1
                    if r.startswith("!"):
                        raise ErrorMontura(f"La montura rechazó :{cmd}{canal}{datos} (error {r[1:] or '?'})")
                    return r[1:]
                except (socket.timeout, OSError) as e:
                    ultimo = e
                    continue
        self.errores += 1
        raise ErrorMontura(f"Sin respuesta de la montura en {self.host}:{self.puerto} a :{cmd}{canal} ({ultimo})")

    def leer_24(self, cmd, canal):
        return decodificar_24(self.enviar(cmd, canal))

    # ── Preparación ───────────────────────────────────────────────────────
    def preparar(self):
        self.conectar()
        self.version = self.leer_version()
        for i, c in enumerate(self.CANALES):
            self.cpr[i] = self.leer_24("a", c)
            self.freq[i] = self.leer_24("b", c)
            try:
                self.ratio[i] = self.leer_24("D", c)
            except (ErrorMontura, ValueError):
                self.ratio[i] = 1
            estado = self.estado(i)
            if not estado["inicializado"]:
                self.enviar("F", c)
            self._pos0[i] = self.leer_24("j", c)
            if self.cpr[i] <= 0 or self.freq[i] <= 0:
                raise ErrorMontura(f"Parámetros del motor {c} no válidos (a={self.cpr[i]}, b={self.freq[i]})")
        if self._t is not None:
            from hardware import ErrorHardware
            try:
                self._tracking_inicial, self._pos_ascom = self._t.ejecutar(self._leer_ascom_y_quitar_tracking, timeout=10)
            except ErrorHardware as e:
                raise ErrorMontura(f"No se pudo desactivar el seguimiento en SynScan Pro: {e}") from e
        log.info("Montura UDP lista: versión %s, cpr %s, freq %s, ratio %s, tracking inicial %s",
                 self.version.get("texto"), self.cpr, self.freq, self.ratio, self._tracking_inicial)

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
        return {"texto": f"{mayor}.{menor:02d} (montura 0x{codigo:02X})", "raw": r,
                "valor": (mayor << 16) | (menor << 8) | codigo, "codigo_montura": codigo}

    def estado(self, eje):
        return interpretar_estado(self.enviar("f", self.CANALES[eje]))

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
                                  version=self.version.get("valor", 0), umbral=self.umbral_rapido)
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
            try:
                if not self.estado(eje)["en_marcha"]:
                    return True
            except ErrorMontura:
                pass
            time.sleep(0.05)
        log.warning("Montura UDP: el eje %d no confirmó la parada en %.1f s", eje, maximo_s)
        return False

    def parar(self):
        for eje, c in enumerate(self.CANALES):
            try:
                self.enviar("K", c)
            finally:
                self._eje[eje].update(marcha=False, v=0.0, T=None, espera=None)
        for eje in (0, 1):
            self._esperar_parada(eje)

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
        finally:
            if self._t is not None and self._tracking_inicial:
                try:
                    self._t.ejecutar(lambda tel: setattr(tel, "Tracking", True), timeout=10)
                except Exception as e:
                    log.warning("Montura UDP: no se pudo restaurar el seguimiento: %s", e)
            self.cerrar()

    def telemetria(self):
        return {"backend": self.nombre, "latencia_ms": self._latencia_ms, "enviadas": self.enviadas,
                "errores": self.errores, "version": self.version.get("texto"), "cambios_de_modo": self.cambios_de_modo,
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
                                    umbral_rapido=float(conf.get("udp_umbral_rapido", UMBRAL_RAPIDO_SEGUIMIENTO)))
    if trabajador_ascom is None:
        raise ErrorMontura("El backend ASCOM necesita el TrabajadorCOM de la montura")
    redondeo = conf.get("redondeo_ascom")
    return MonturaASCOM(trabajador_ascom, intervalo_min_s=float(conf.get("intervalo_ascom_s", 0.4)),
                        banda_muerta=float(conf.get("banda_muerta", 0.02)),
                        redondeo=float(redondeo) if redondeo not in (None, "", "0") else None)


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
    T, _ = velocidad_a_periodo(0.05, cpr, freq, ratio)
    assert abs(periodo_a_velocidad(T, True, cpr, freq, ratio) - 0.05) < 0.001
    # Por encima del umbral del firmware siempre rápido, aunque se pida otro umbral
    assert velocidad_a_periodo(0.6, cpr, freq, ratio, rapido_actual=False, umbral=5.0)[1] is True
    assert velocidad_a_periodo(0.25, cpr, freq, ratio, umbral=5.0)[1] is False
    assert velocidad_a_periodo(0, cpr, freq, ratio) is None
    assert velocidad_a_periodo(50.0, cpr, freq, ratio)[0] == PERIODO_MINIMO
    print("✅ montura_ejes: pruebas del protocolo correctas")
