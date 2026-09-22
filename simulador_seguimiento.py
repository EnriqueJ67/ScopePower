# simulador_seguimiento.py
#
# Cámara y montura falsas para probar el seguimiento de objetos (seguimiento.py)
# sin telescopio: un "mundo" con un objeto que cruza el cielo a velocidad
# angular constante (o con curva), una montura cuya velocidad de ejes se aplica
# con retardo, rampa y holgura, y una cámara que pinta el objeto (y unas
# estrellas fijas) donde tocaría según la orientación real de la cámara respecto
# a los ejes. Las dos clases tienen la misma interfaz que CamaraNativa y
# MonturaEjes, así que TrabajoSeguimiento no distingue si son de verdad.
#
# También hay un controlador de motores Sky-Watcher falso por UDP
# (ServidorUDPFalso) que traduce el protocolo a la montura simulada, para
# ejercitar el backend MonturaSkyWatcherUDP tal cual contra 127.0.0.1.
#
# La app los usa cuando [Seguimiento] simulador = true; también los usa
# herramientas/probar_seguimiento_simulado.py en bucle cerrado sin Flask.

import math
import time
import socket
import logging
import threading

import numpy as np

import montura_ejes
from montura_ejes import MonturaEjes, codificar_24, decodificar_24

log = logging.getLogger("scopepower.simulador_seguimiento")


class Mundo:
    """
    Estado verdadero: apuntado de la montura (az, alt) y objeto que se mueve a
    `v_obj` °/s con rumbo `rumbo_deg` (0 = +acimut, 90 = +altura) en el plano
    de ejes, partiendo de `inicio_deg` respecto al apuntado inicial. `curva_deg_s2`
    hace girar el rumbo (paso de satélite). Todo en grados de eje.
    """

    def __init__(self, v_obj=0.8, rumbo_deg=20.0, inicio_deg=None, az0=180.0, alt0=45.0,
                 curva_deg_s2=0.0, estrellas=12, semilla=1, entrada_deg=0.7):
        self.t0 = time.perf_counter()
        self.az0, self.alt0 = float(az0), float(alt0)
        self.v_obj, self.rumbo, self.curva = float(v_obj), float(rumbo_deg), float(curva_deg_s2)
        if inicio_deg is None and abs(v_obj) < 1e-6:
            # Objeto quieto (para calibrar): cerca del centro del campo
            inicio_deg = (0.08, 0.05)
        elif inicio_deg is None:
            # El objeto entra por el borde del campo y cruza el centro (así se caza de verdad)
            r = math.radians(rumbo_deg)
            inicio_deg = (-entrada_deg * math.cos(r), -entrada_deg * math.sin(r))
        self.inicio = np.array(inicio_deg, dtype=float)
        self.montura = np.array([az0, alt0], dtype=float)      # apuntado actual
        rng = np.random.default_rng(semilla)
        self.estrellas = rng.uniform(-3.0, 3.0, size=(estrellas, 2)) + np.array([az0, alt0])
        self.brillo_estrellas = rng.uniform(40, 160, size=estrellas)

    def tiempo(self):
        return time.perf_counter() - self.t0

    def objeto(self, t=None):
        """Posición absoluta del objeto (az, alt) en grados de eje en el instante t."""
        t = self.tiempo() if t is None else t
        if abs(self.curva) < 1e-9:
            r = math.radians(self.rumbo)
            d = np.array([math.cos(r), math.sin(r)]) * self.v_obj * t
        else:
            # rumbo que gira: integral de la velocidad
            w = math.radians(self.curva)
            r0 = math.radians(self.rumbo)
            d = self.v_obj / w * np.array([math.sin(r0 + w * t) - math.sin(r0), -math.cos(r0 + w * t) + math.cos(r0)])
        return np.array([self.az0, self.alt0]) + self.inicio + d


class MonturaSimulada(MonturaEjes):
    """
    Velocidad por eje con retardo (`retardo_s`), rampa (`aceleracion` °/s²) y
    holgura (`backlash_deg`: al invertir el sentido, ese tramo no mueve el
    apuntado). Integra el apuntado en `mundo.montura`. `cuantizar` redondea las
    velocidades como haría un driver caprichoso (None = no).
    """
    nombre = "simulada"

    # Valores por defecto según lo medido con la AZ GoTo real el 2026-09-22 (con margen):
    # arranque a 140–230 ms, velocidad exacta sin cuantización, 0,81 °/s de tope en
    # modo lento, y unas décimas de holgura al invertir el sentido.
    def __init__(self, mundo, retardo_s=0.3, aceleracion=4.0, backlash_deg=0.03, v_max=0.8, cuantizar=None,
                 latencia_orden_s=0.0):
        self.mundo = mundo
        self.retardo, self.acel, self.backlash = float(retardo_s), float(aceleracion), float(backlash_deg)
        self.v_max = float(v_max)
        self.cuantizar = cuantizar
        self.latencia_orden = float(latencia_orden_s)
        self._lock = threading.Lock()
        self._pendientes = []                 # (t_aplicar, (v_az, v_alt))
        self._objetivo = np.zeros(2)
        self._v = np.zeros(2)
        self._t = mundo.tiempo()
        self._holgura = np.zeros(2)           # holgura pendiente de recorrer por eje
        self._sentido = np.zeros(2)
        self.enviadas = 0
        self.tracking = True
        self.parada = True

    def preparar(self):
        self.tracking = False
        self.avanzar()

    def limites(self):
        return (self.v_max, self.v_max)

    def velocidad(self, v_az, v_alt):
        v_az, v_alt = self.recortar(v_az, v_alt)
        if self.cuantizar:
            v_az = round(v_az / self.cuantizar) * self.cuantizar
            v_alt = round(v_alt / self.cuantizar) * self.cuantizar
        if self.latencia_orden > 0:
            time.sleep(self.latencia_orden)
        with self._lock:
            self._pendientes.append((self.mundo.tiempo() + self.retardo, (v_az, v_alt)))
            self.enviadas += 1
        self.parada = False
        return True

    def parar(self):
        with self._lock:
            self._pendientes.append((self.mundo.tiempo() + self.retardo, (0.0, 0.0)))
        self.parada = True

    def restaurar(self):
        self.parar()
        self.tracking = True

    def avanzar(self):
        """Integra el movimiento hasta ahora (la cámara lo llama antes de pintar)."""
        with self._lock:
            ahora = self.mundo.tiempo()
            # aplicar órdenes vencidas, integrando por tramos
            eventos = sorted([p for p in self._pendientes if p[0] <= ahora]) + [(ahora, None)]
            self._pendientes = [p for p in self._pendientes if p[0] > ahora]
            for t_ev, v_nueva in eventos:
                self._integrar_hasta(t_ev)
                if v_nueva is not None:
                    self._objetivo = np.array(v_nueva)

    def _integrar_hasta(self, t):
        dt = t - self._t
        if dt <= 0:
            return
        pasos = max(1, int(dt / 0.01))
        h = dt / pasos
        for _ in range(pasos):
            dv = np.clip(self._objetivo - self._v, -self.acel * h, self.acel * h)
            self._v = self._v + dv
            d = self._v * h
            for i in range(2):
                s = np.sign(d[i])
                if s != 0 and s != self._sentido[i]:
                    if self._sentido[i] != 0:
                        self._holgura[i] = self.backlash
                    self._sentido[i] = s
                if self._holgura[i] > 0:
                    usado = min(abs(d[i]), self._holgura[i])
                    self._holgura[i] -= usado
                    d[i] -= s * usado
            self.mundo.montura = self.mundo.montura + d
        self._t = t

    def posicion(self):
        self.avanzar()
        return (float(self.mundo.montura[0]), float(self.mundo.montura[1]))

    def velocidad_real(self):
        self.avanzar()
        return tuple(float(x) for x in self._v)

    def telemetria(self):
        return {"backend": self.nombre, "enviadas": self.enviadas, "latencia_ms": self.retardo * 1000,
                "v_real": [round(float(x), 4) for x in self._v]}


class CamaraSimulada:
    """
    Fotograma RAW uint8 de `lado`×`lado` (ya con binning aplicado): fondo con
    ruido, estrellas fijas en el cielo y el objeto como gaussiana. La imagen
    corresponde al mundo `retardo_s` antes (latencia de la cámara). La posición
    en píxeles sigue la orientación real cámara↔ejes (`angulo_deg`, `espejo`)
    y la escala real (`escala_arcsec_px` del plano de detección, a media
    resolución del fotograma).
    """

    def __init__(self, mundo, montura, lado=512, fps=30.0, retardo_s=0.05, escala_arcsec_px=6.0,
                 angulo_deg=25.0, espejo=1, ruido=3.0, brillo_obj=220.0, sigma_obj=2.5, n_objetos=1):
        self.mundo, self.montura = mundo, montura
        self.lado = int(lado) & ~1
        self.fps, self.retardo = float(fps), float(retardo_s)
        self.escala = float(escala_arcsec_px)        # "/px del plano de detección (lado/2)
        self.angulo, self.espejo = float(angulo_deg), 1 if espejo >= 0 else -1
        self.ruido, self.brillo_obj, self.sigma_obj = float(ruido), float(brillo_obj), float(sigma_obj)
        self.n_objetos = int(n_objetos)
        self._buffer = np.zeros((self.lado, self.lado), dtype=np.uint8)
        self._video = False
        self._t_ultimo = 0.0
        self.propiedades = {"modelo": "Simulada", "ancho": lado * 2, "alto": lado * 2, "bayer": "RGGB", "pixel_um": 3.76}
        self.geometria = {}
        self._descartados = 0
        yy, xx = np.mgrid[0:self.lado, 0:self.lado]
        self._xx, self._yy = xx.astype(np.float32), yy.astype(np.float32)

    # ── interfaz CamaraNativa ────────────────────────────────────────────
    def abrir(self):
        return self.propiedades

    def configurar(self, roi_px=1024, binning=2, formato="RAW8", exposicion_ms=5.0, ganancia=None, fps_max=None, **kw):
        if fps_max:
            self.fps = min(self.fps, float(fps_max))
        self.geometria = {"x0": 0, "y0": 0, "lado": self.lado, "binning": binning, "formato": "RAW8",
                          "exposicion_ms": exposicion_ms}
        return dict(self.geometria)

    def iniciar_video(self):
        self._video = True

    def parar_video(self):
        self._video = False

    def cerrar(self):
        self._video = False

    def descartados(self):
        return self._descartados

    def matriz_px_por_deg(self, alt_deg):
        """M verdadera del plano de detección: v_px = M · v_ejes (px/s por °/s)."""
        th = math.radians(self.angulo)
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        S = np.diag([1.0, float(self.espejo)])
        D = np.diag([max(0.05, math.cos(math.radians(alt_deg))), 1.0])
        return (3600.0 / self.escala) * S @ R @ D

    def leer_frame(self, timeout_ms=None):
        # cadencia
        ahora = time.perf_counter()
        resto = 1.0 / self.fps - (ahora - self._t_ultimo)
        if resto > 0:
            time.sleep(resto)
        self._t_ultimo = time.perf_counter()
        self.montura.avanzar()
        t = self.mundo.tiempo() - self.retardo
        apuntado = self.mundo.montura
        M = self.matriz_px_por_deg(apuntado[1])
        centro = self.lado / 4.0              # centro del plano de detección
        img = np.random.normal(20.0, self.ruido, (self.lado // 2, self.lado // 2)).astype(np.float32)
        # estrellas fijas
        for (saz, salt), b in zip(self.mundo.estrellas, self.mundo.brillo_estrellas):
            p = M @ np.array([saz - apuntado[0], salt - apuntado[1]]) + centro
            self._pintar(img, p, b, 1.2)
        # objeto(s)
        obj = self.mundo.objeto(t)
        p = M @ (obj - apuntado) + centro
        self._pintar(img, p, self.brillo_obj, self.sigma_obj)
        if self.n_objetos > 1:
            p2 = M @ (obj + np.array([0.4, 0.3]) - apuntado) + centro
            self._pintar(img, p2, self.brillo_obj * 0.5, self.sigma_obj)
        plano = np.clip(img, 0, 255)
        # De plano (lado/2) a "RAW" lado×lado: repetir cada píxel 2×2 (luminancia_rapida vuelve a sumar)
        raw = np.repeat(np.repeat(plano, 2, axis=0), 2, axis=1)
        self._buffer[...] = raw.astype(np.uint8)
        return self._buffer

    def _pintar(self, img, p, brillo, sigma):
        h, w = img.shape
        x, y = float(p[0]), float(p[1])
        if not (-4 * sigma <= x < w + 4 * sigma and -4 * sigma <= y < h + 4 * sigma):
            return
        r = int(4 * sigma) + 1
        x0, x1 = max(0, int(x) - r), min(w, int(x) + r + 1)
        y0, y1 = max(0, int(y) - r), min(h, int(y) + r + 1)
        xx = self._xx[y0:y1, x0:x1]
        yy = self._yy[y0:y1, x0:x1]
        img[y0:y1, x0:x1] += brillo * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))


class ServidorUDPFalso:
    """
    Controlador de motores Sky-Watcher falso: atiende el protocolo por UDP y
    mueve una MonturaSimulada. Sirve para probar MonturaSkyWatcherUDP tal cual.
    """
    CPR, FREQ, RATIO = 1_296_000, 64_935, 16

    def __init__(self, montura, host="127.0.0.1", puerto=11880):
        self.montura = montura
        self.host, self.puerto = host, int(puerto)
        self._sock = None
        self._hilo = None
        self._parar = threading.Event()
        self.ejes = [{"mode": 1, "dir": 0, "T": 1000, "run": False, "pos0": 0x800000} for _ in (0, 1)]

    def iniciar(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((self.host, self.puerto))
        self._sock.settimeout(0.2)
        self._hilo = threading.Thread(target=self._servir, name="udp-falso", daemon=True)
        self._hilo.start()
        log.info("Montura UDP falsa en %s:%d", self.host, self.puerto)

    def parar(self):
        self._parar.set()
        if self._sock:
            self._sock.close()

    def _velocidad_eje(self, e):
        pasos = self.FREQ / max(montura_ejes.PERIODO_MINIMO, e["T"]) * (self.RATIO if e["mode"] in (0, 3) else 1)
        v = pasos * 360.0 / self.CPR
        return v if e["dir"] == 0 else -v

    def _aplicar(self):
        v = [self._velocidad_eje(e) if e["run"] else 0.0 for e in self.ejes]
        self.montura.velocidad(v[0], v[1])

    def _servir(self):
        while not self._parar.is_set():
            try:
                d, addr = self._sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                break
            m = d.decode(errors="replace").strip("\r\n")
            if not m.startswith(":") or len(m) < 3 or m[2] not in "12":
                continue
            cmd, i, datos = m[1], int(m[2]) - 1, m[3:]
            e = self.ejes[i]
            r = "="
            if cmd == "e":
                r += "020304"
            elif cmd == "a":
                r += codificar_24(self.CPR)
            elif cmd == "b":
                r += codificar_24(self.FREQ)
            elif cmd == "g":
                r += f"{self.RATIO:02X}"
            elif cmd == "D":
                r += codificar_24(int(round(self.FREQ / (montura_ejes.SIDEREO_DEG_S * self.CPR / 360.0))))
            elif cmd == "f":
                x = (1 if e["mode"] in (1, 3) else 0) | (2 if e["dir"] else 0) | (4 if e["mode"] in (0, 3) else 0)
                vr = self.montura.velocidad_real()[i]
                r += f"{x:X}{1 if (e['run'] or abs(vr) > 1e-4) else 0:X}1"
            elif cmd == "F":
                pass
            elif cmd == "j":
                pos = self.montura.posicion()[i]
                r += codificar_24(int(round(e["pos0"] + pos * self.CPR / 360.0)))
            elif cmd == "G":
                e["mode"], e["dir"] = int(datos[0]), int(datos[1])
            elif cmd == "I":
                e["T"] = decodificar_24(datos)
                if e["run"]:
                    self._aplicar()
            elif cmd == "J":
                e["run"] = True
                self._aplicar()
            elif cmd in ("K", "L"):
                e["run"] = False
                self._aplicar()
            else:
                r = "!0"
            try:
                self._sock.sendto((r + "\r").encode(), addr)
            except OSError:
                pass


def fabricas(conf, mundo=None, **kw):
    """
    (fabrica_camara, fabrica_montura, mundo, montura) para TrabajoSeguimiento
    a partir de la configuración [Seguimiento] y parámetros del simulador:
    v_obj, rumbo_deg, retardo_montura_s, retardo_camara_s, aceleracion,
    backlash_deg, angulo_deg, espejo, escala_arcsec_px, cuantizar, udp (bool).
    """
    mundo = mundo or Mundo(v_obj=kw.get("v_obj", 0.8), rumbo_deg=kw.get("rumbo_deg", 20.0),
                           curva_deg_s2=kw.get("curva_deg_s2", 0.0))
    montura = MonturaSimulada(mundo, retardo_s=kw.get("retardo_montura_s", 0.3), aceleracion=kw.get("aceleracion", 4.0),
                              backlash_deg=kw.get("backlash_deg", 0.03), v_max=min(conf.get("v_max", 4.0), 0.8),
                              cuantizar=kw.get("cuantizar"))
    lado = max(64, int(conf.get("roi_px", 1024)) // int(conf.get("binning", 2)))
    escala = kw.get("escala_arcsec_px") or 206.265 * conf.get("pixel_um", 3.76) * conf.get("binning", 2) / conf.get("focal_mm", 260.0) * 2
    camara = CamaraSimulada(mundo, montura, lado=lado, fps=kw.get("fps", 30.0), retardo_s=kw.get("retardo_camara_s", 0.05),
                            escala_arcsec_px=escala, angulo_deg=kw.get("angulo_deg", 25.0), espejo=kw.get("espejo", 1),
                            n_objetos=kw.get("n_objetos", 1))
    servidor = None
    if kw.get("udp"):
        servidor = ServidorUDPFalso(montura, host=conf.get("udp_host", "127.0.0.1"), puerto=int(conf.get("udp_puerto", 11880)))
        servidor.iniciar()

        def fabrica_montura():
            return montura_ejes.MonturaSkyWatcherUDP(host=servidor.host, puerto=servidor.puerto, trabajador_ascom=None)
    else:
        def fabrica_montura():
            return montura
    return (lambda: camara), fabrica_montura, mundo, montura, camara, servidor
