# herramientas/medir_latencia_montura.py
#
# Mide cuánto tarda la montura en obedecer una velocidad de eje por cada
# camino de órdenes (montura_ejes.py): ASCOM MoveAxis a través de SynScan Pro,
# y protocolo Sky-Watcher directo por UDP al módulo wifi. Con los resultados se
# decide qué backend usa el seguimiento de objetos:
#   UDP si su latencia es ≤ la mitad de la de ASCOM, responde ≥ 98 % de las
#   veces con SynScan Pro conectado y admite cambiar :I en caliente; si no,
#   ASCOM con intervalo_ascom_s = latencia medida × 1,5.
#
#   python herramientas\medir_latencia_montura.py [--backend ascom|udp|ambos]
#         [--velocidad 0.3] [--repeticiones 3] [--duracion 2] [--eje 0|1]
#         [--host 192.168.4.1] [--puerto 11880] [--driver ASCOM.SynScanMobile.Telescope]
#         [--con-synscan] [--sin-mover]
#
# Pruebas:
#   A  latencia: posición a 20 Hz, orden de velocidad en t0, tiempo hasta que
#      la posición empieza a cambiar y hasta alcanzar el 90 % de la velocidad
#      pedida; luego parada y tiempo de frenada. Vuelve al punto de partida.
#   B  (UDP) 20 × :e1 con SynScan Pro conectado: % de respuestas y tiempo.
#   C  (UDP) velocidad máxima real en modo lento y si :I en caliente cambia la
#      velocidad sin parar el eje.
#
# ¡Mueve la montura! Dejar sitio libre alrededor del tubo. Con --sin-mover solo
# hace la prueba B y lecturas. Resultados en logs/medidas_montura_<fecha>.csv.

import os
import sys
import csv
import time
import argparse
import configparser
from datetime import datetime

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import montura_ejes                                                   # noqa: E402
from montura_ejes import (MonturaASCOM, MonturaSkyWatcherUDP, ErrorMontura,   # noqa: E402
                          codificar_24, velocidad_a_periodo, periodo_a_velocidad)

FILAS = []


def ms(x):
    return "—" if x is None else f"{x:.0f}"


def anotar(**fila):
    fila.setdefault("hora", datetime.now().strftime("%H:%M:%S"))
    FILAS.append(fila)
    return fila


def driver_por_defecto():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini", encoding="utf-8")
    return cfg.get("Dispositivos", "driver_telescopio", fallback="ASCOM.SynScanMobile.Telescope")


# ─────────────────────────────────────────────────────────────────────────────
# Lectura de posición por cada camino
# ─────────────────────────────────────────────────────────────────────────────
class LectorASCOM:
    def __init__(self, trabajador, eje):
        self.t, self.eje = trabajador, eje

    def leer(self):
        if self.eje == 0:
            return self.t.ejecutar(lambda tel: float(tel.Azimuth), timeout=5)
        return self.t.ejecutar(lambda tel: float(tel.Altitude), timeout=5)


class LectorUDP:
    def __init__(self, montura, eje):
        self.m, self.eje = montura, eje

    def leer(self):
        c = self.m.CANALES[self.eje]
        return self.m.leer_24("j", c) * 360.0 / self.m.cpr[self.eje]


def muestrear(lector, segundos, hz=20):
    """Lista de (t, posición) durante `segundos`; también mide lo que tarda cada lectura."""
    muestras, costes = [], []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < segundos:
        ta = time.perf_counter()
        try:
            p = lector.leer()
        except Exception:
            p = None
        tb = time.perf_counter()
        costes.append(tb - ta)
        if p is not None:
            muestras.append((tb - t0, p))
        resto = 1.0 / hz - (time.perf_counter() - tb)
        if resto > 0:
            time.sleep(resto)
    return muestras, (np.mean(costes) * 1000 if costes else None)


def velocidad_en(muestras, t, ventana=0.3):
    sel = [(a, b) for a, b in muestras if t - ventana <= a <= t]
    if len(sel) < 2:
        return 0.0
    (t1, p1), (t2, p2) = sel[0], sel[-1]
    if t2 == t1:
        return 0.0
    d = (p2 - p1 + 180) % 360 - 180
    return d / (t2 - t1)


# ─────────────────────────────────────────────────────────────────────────────
# Prueba A: latencia de una orden de velocidad
# ─────────────────────────────────────────────────────────────────────────────
def prueba_A(nombre, montura, lector, eje, v, duracion, repeticiones):
    print(f"\n🅰 Latencia {nombre}: eje {eje}, {v:g} °/s, {repeticiones} repeticiones")
    for rep in range(repeticiones):
        for signo in (+1, -1):          # ida y vuelta para acabar donde empezó
            vv = signo * v
            base, coste_lectura = muestrear(lector, 0.5)
            p0 = base[-1][1] if base else None
            t_orden = time.perf_counter()
            montura.velocidad(vv if eje == 0 else 0.0, vv if eje == 1 else 0.0)
            if hasattr(montura, "pendiente"):
                montura.pendiente()
            t_orden_fin = time.perf_counter()
            muestras, _ = muestrear(lector, duracion)
            # tiempos relativos a la orden
            t_ini = None
            t_90 = None
            for t, p in muestras:
                d = abs((p - p0 + 180) % 360 - 180) if p0 is not None else 0
                if t_ini is None and d > 0.003:
                    t_ini = t
                if t_90 is None and abs(velocidad_en(muestras, t)) >= 0.9 * abs(vv):
                    t_90 = t
            v_medida = velocidad_en(muestras, muestras[-1][0], ventana=0.5) if muestras else 0.0
            t_stop = time.perf_counter()
            montura.parar()
            t_stop_fin = time.perf_counter()
            tras, _ = muestrear(lector, 1.5)
            t_quieto = None
            for t, p in tras:
                if abs(velocidad_en(tras, t, 0.2)) < 0.01 and t > 0.1:
                    t_quieto = t
                    break
            fila = anotar(prueba="A", backend=nombre, eje=eje, v_pedida=vv, orden_ms=(t_orden_fin - t_orden) * 1000,
                          inicio_ms=None if t_ini is None else t_ini * 1000 + (t_orden_fin - t_orden) * 1000,
                          v90_ms=None if t_90 is None else t_90 * 1000 + (t_orden_fin - t_orden) * 1000,
                          v_medida=v_medida, parada_orden_ms=(t_stop_fin - t_stop) * 1000,
                          quieto_ms=None if t_quieto is None else t_quieto * 1000, lectura_ms=coste_lectura)
            print(f"   {rep + 1}.{'ida' if signo > 0 else 'vuelta':6s} orden {fila['orden_ms']:.0f} ms · "
                  f"empieza a moverse {ms(fila['inicio_ms'])} ms · 90 % a {ms(fila['v90_ms'])} ms · "
                  f"v medida {v_medida:+.3f} °/s · parada {fila['parada_orden_ms']:.0f} ms, quieto a "
                  f"{ms(fila['quieto_ms'])} ms · lectura {coste_lectura:.0f} ms")
            time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Prueba B: UDP responde con SynScan Pro conectado
# ─────────────────────────────────────────────────────────────────────────────
def prueba_B(m, con_synscan, n=20):
    print(f"\n🅱 Respuesta UDP a :e1 ×{n} ({'con' if con_synscan else 'sin'} SynScan Pro conectado)")
    ok, fallos, corruptas, tiempos = 0, 0, 0, []
    ref = None
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            r = m.enviar("e", "1")
            tiempos.append((time.perf_counter() - t0) * 1000)
            if ref is None:
                ref = r
            if r != ref:
                corruptas += 1
            else:
                ok += 1
        except ErrorMontura:
            fallos += 1
        time.sleep(0.05)
    fila = anotar(prueba="B", backend="udp", con_synscan=con_synscan, ok=ok, fallos=fallos, corruptas=corruptas,
                  media_ms=np.mean(tiempos) if tiempos else None, p95_ms=np.percentile(tiempos, 95) if tiempos else None)
    print(f"   {ok}/{n} correctas, {fallos} sin respuesta, {corruptas} distintas · "
          f"{fila['media_ms'] or 0:.1f} ms de media, p95 {fila['p95_ms'] or 0:.1f} ms · versión {m.version.get('texto')}")
    return ok / n


# ─────────────────────────────────────────────────────────────────────────────
# Prueba C: modo lento y cambio de :I en caliente
# ─────────────────────────────────────────────────────────────────────────────
def prueba_C(m, eje, duracion):
    c = m.CANALES[eje]
    cpr, freq, ratio = m.cpr[eje], m.freq[eje], m.ratio[eje]
    lector = LectorUDP(m, eje)
    print(f"\n🅲 Modo lento en el eje {eje} (cpr {cpr}, freq {freq}, ratio {ratio}; umbral teórico "
          f"{montura_ejes.UMBRAL_RAPIDO_DEG_S:.3f} °/s)")
    print("   velocidad pedida → medida en modo lento (:G 1)")
    for v in (0.2, 0.4, 0.5, 0.55, 0.6, 0.8, 1.0, 1.5):
        T = max(montura_ejes.PERIODO_MINIMO, int(round(freq / (v * cpr / 360.0))))
        try:
            m.enviar("G", c, "10")
            m.enviar("I", c, codificar_24(T))
            m.enviar("J", c)
            muestras, _ = muestrear(lector, duracion)
            v_med = velocidad_en(muestras, muestras[-1][0], 0.5) if muestras else 0.0
            m.enviar("K", c)
            m._esperar_parada(eje)
            # volver
            m.enviar("G", c, "11"); m.enviar("I", c, codificar_24(T)); m.enviar("J", c)
            time.sleep(duracion); m.enviar("K", c); m._esperar_parada(eje)
            estado = "ok" if abs(abs(v_med) - v) < 0.15 * v else "NO alcanza"
        except ErrorMontura as e:
            v_med, estado = 0.0, f"rechazada ({e})"
            try:
                m.enviar("K", c)
            except ErrorMontura:
                pass
        anotar(prueba="C-lento", backend="udp", eje=eje, v_pedida=v, T=T, v_medida=v_med, resultado=estado)
        print(f"   {v:4.2f} °/s (T={T}) → {abs(v_med):.3f} °/s  {estado}")
        time.sleep(0.3)

    print("   cambio de :I en caliente: 0,2 → 0,4 °/s sin parar")
    T1 = int(round(freq / (0.2 * cpr / 360.0)))
    T2 = int(round(freq / (0.4 * cpr / 360.0)))
    try:
        m.enviar("G", c, "10"); m.enviar("I", c, codificar_24(T1)); m.enviar("J", c)
        a, _ = muestrear(lector, 1.5)
        v1 = velocidad_en(a, a[-1][0], 0.5)
        t0 = time.perf_counter()
        m.enviar("I", c, codificar_24(T2))
        b, _ = muestrear(lector, 1.5)
        v2 = velocidad_en(b, b[-1][0], 0.5)
        t_cambio = None
        for t, p in b:
            if abs(velocidad_en(b, t, 0.3)) >= 0.9 * 0.4:
                t_cambio = t
                break
        m.enviar("K", c); m._esperar_parada(eje)
        m.enviar("G", c, "11"); m.enviar("I", c, codificar_24(T1)); m.enviar("J", c)
        time.sleep(1.5); m.enviar("I", c, codificar_24(T2)); time.sleep(1.5)
        m.enviar("K", c); m._esperar_parada(eje)
        funciona = abs(abs(v2) - 0.4) < 0.08
        anotar(prueba="C-caliente", backend="udp", eje=eje, v1=v1, v2=v2, cambio_ms=None if t_cambio is None else t_cambio * 1000,
               resultado="ok" if funciona else "NO cambia")
        print(f"   antes {abs(v1):.3f} °/s, después {abs(v2):.3f} °/s"
              f"{'' if t_cambio is None else f' (a los {t_cambio * 1000:.0f} ms)'} → {'✅ funciona' if funciona else '❌ no cambia en caliente'}")
    except ErrorMontura as e:
        print(f"   ❌ {e}")
        try:
            m.enviar("K", c)
        except ErrorMontura:
            pass


# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Latencia de la montura por ASCOM y por UDP directo")
    ap.add_argument("--backend", choices=("ascom", "udp", "ambos"), default="ambos")
    ap.add_argument("--velocidad", type=float, default=0.3)
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--duracion", type=float, default=2.0, help="segundos en movimiento por prueba")
    ap.add_argument("--eje", type=int, choices=(0, 1), default=0)
    ap.add_argument("--host", default="192.168.4.1")
    ap.add_argument("--puerto", type=int, default=11880)
    ap.add_argument("--driver", default=None)
    ap.add_argument("--con-synscan", action="store_true", help="anota que SynScan Pro está conectado (prueba B)")
    ap.add_argument("--sin-mover", action="store_true", help="solo pruebas que no mueven la montura")
    ap.add_argument("--sin-ascom", action="store_true", help="no usar ASCOM ni para quitar el tracking (UDP puro)")
    args = ap.parse_args()

    trabajador = None
    if not args.sin_ascom and args.backend in ("ascom", "ambos") or (args.backend == "udp" and not args.sin_ascom):
        from hardware import TrabajadorCOM, ErrorHardware
        driver = args.driver or driver_por_defecto()
        trabajador = TrabajadorCOM("montura", lambda: driver)
        try:
            print(f"🔭 Conectando por ASCOM con {driver}…")
            trabajador.ejecutar(lambda tel: tel.RightAscension, timeout=30)
        except ErrorHardware as e:
            print(f"❌ ASCOM: {e}")
            if args.backend == "ascom":
                return 1
            trabajador = None

    try:
        if args.backend in ("ascom", "ambos") and trabajador is not None and not args.sin_mover:
            m = MonturaASCOM(trabajador, intervalo_min_s=0.0, banda_muerta=0.0)
            m.preparar()
            print(f"   límites ASCOM {m.limites()} °/s, tracking inicial {m._tracking_inicial}")
            try:
                prueba_A("ascom", m, LectorASCOM(trabajador, args.eje), args.eje, args.velocidad, args.duracion, args.repeticiones)
            finally:
                m.parar()

        if args.backend in ("udp", "ambos"):
            m = MonturaSkyWatcherUDP(host=args.host, puerto=args.puerto, trabajador_ascom=trabajador)
            try:
                m.preparar()
            except ErrorMontura as e:
                print(f"❌ UDP: {e}")
                return 1
            print(f"   versión {m.version.get('texto')} · cpr {m.cpr} · freq {m.freq} · ratio {m.ratio} · "
                  f"tracking inicial {m._tracking_inicial}")
            try:
                prueba_B(m, args.con_synscan)
                if not args.sin_mover:
                    prueba_A("udp", m, LectorUDP(m, args.eje), args.eje, args.velocidad, args.duracion, args.repeticiones)
                    prueba_C(m, args.eje, args.duracion)
            finally:
                m.restaurar()
    except KeyboardInterrupt:
        print("\n⏹ Interrumpido")
    finally:
        if FILAS:
            os.makedirs("logs", exist_ok=True)
            ruta = os.path.join("logs", f"medidas_montura_{datetime.now():%Y%m%d_%H%M%S}.csv")
            campos = []
            for f in FILAS:
                for k in f:
                    if k not in campos:
                        campos.append(k)
            with open(ruta, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=campos)
                w.writeheader()
                w.writerows(FILAS)
            print(f"\n📄 Guardado en {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
