# herramientas/probar_seguimiento_simulado.py
#
# Corre el seguimiento de objetos (seguimiento.TrabajoSeguimiento) en bucle
# cerrado contra la cámara y la montura simuladas (simulador_seguimiento.py),
# sin Flask ni equipo, y dice si converge: tiempo hasta que el error baja de
# `--tolerancia` px y se mantiene, error máximo después, y si oscila.
#
#   python herramientas\probar_seguimiento_simulado.py [--v-obj 0.8] [--rumbo 20]
#         [--retardo 0.5] [--retardo-camara 0.05] [--aceleracion 2] [--backlash 0.05]
#         [--cuantizar 0.05] [--kp 0.6] [--ki 0.3] [--frecuencia 4] [--segundos 30]
#         [--angulo 25] [--error-calibracion 0] [--espejo 1] [--udp] [--curva 0]
#
# OJO: los valores por defecto son pesimistas a propósito (retardo 0,5 s,
# aceleración 2 °/s², holgura 0,05°, velocidades cuantizadas a 0,05 °/s), pero
# el simulador no es la montura real: que converja aquí solo dice que la lógica
# está bien. Los retardos y velocidades reales salen de medir_latencia_montura.py
# y hay que ponerlos aquí antes de fiarse de las ganancias.
#
# Guarda logs/simulacion_seguimiento_<fecha>.csv (una fila por muestra) y sale
# con código 0 si converge según el criterio y 1 si no.

import os
import sys
import csv
import time
import argparse
import logging
from datetime import datetime

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import seguimiento                                        # noqa: E402
import simulador_seguimiento as sim                       # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Bucle cerrado del seguimiento con cámara y montura simuladas")
    ap.add_argument("--v-obj", type=float, default=0.8, help="velocidad angular del objeto (°/s)")
    ap.add_argument("--rumbo", type=float, default=20.0, help="rumbo del objeto en el plano de ejes (°)")
    ap.add_argument("--curva", type=float, default=0.0, help="giro del rumbo (°/s²), paso de satélite")
    ap.add_argument("--retardo", type=float, default=0.5, help="retardo de la montura (s)")
    ap.add_argument("--retardo-camara", type=float, default=0.05)
    ap.add_argument("--aceleracion", type=float, default=2.0, help="°/s²")
    ap.add_argument("--backlash", type=float, default=0.05, help="holgura al invertir (°)")
    ap.add_argument("--cuantizar", type=float, default=0.05, help="paso de velocidad que admite la montura (°/s); 0 = continuo")
    ap.add_argument("--kp", type=float, default=0.6)
    ap.add_argument("--ki", type=float, default=0.3)
    ap.add_argument("--frecuencia", type=float, default=4.0)
    ap.add_argument("--segundos", type=float, default=30.0)
    ap.add_argument("--angulo", type=float, default=25.0, help="ángulo real cámara↔ejes (°)")
    ap.add_argument("--espejo", type=int, default=1)
    ap.add_argument("--error-calibracion", type=float, default=0.0, help="error del ángulo que cree la calibración (°)")
    ap.add_argument("--tolerancia", type=float, default=20.0, help="px")
    ap.add_argument("--udp", action="store_true", help="pasar por el backend UDP contra el controlador falso en 127.0.0.1")
    ap.add_argument("--puerto", type=int, default=11890)
    ap.add_argument("--silencio", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.silencio else logging.INFO, format="%(message)s")
    conf = seguimiento.leer_conf({"frecuencia_hz": args.frecuencia, "kp": args.kp, "ki": args.ki,
                                  "udp_host": "127.0.0.1", "udp_puerto": args.puerto, "vista_previa_hz": 0.5})
    fab_cam, fab_mont, mundo, montura, camara, servidor = sim.fabricas(
        conf, v_obj=args.v_obj, rumbo_deg=args.rumbo, curva_deg_s2=args.curva, retardo_montura_s=args.retardo,
        retardo_camara_s=args.retardo_camara, aceleracion=args.aceleracion, backlash_deg=args.backlash,
        cuantizar=args.cuantizar or None, angulo_deg=args.angulo, espejo=args.espejo, udp=args.udp)
    cal = seguimiento.Calibracion.analitica(escala=camara.escala, angulo=args.angulo + args.error_calibracion,
                                            espejo=args.espejo, origen="simulador")
    print(f"🎯 objeto a {args.v_obj:g} °/s rumbo {args.rumbo:g}° · montura retardo {args.retardo:g} s, acel {args.aceleracion:g} °/s², "
          f"holgura {args.backlash:g}°, cuantización {args.cuantizar:g} °/s · cámara {camara.lado}px retardo {args.retardo_camara:g} s "
          f"· kp {args.kp:g} ki {args.ki:g} a {args.frecuencia:g} Hz · backend {'udp' if args.udp else 'directo'}")

    trabajo = seguimiento.TrabajoSeguimiento()
    trabajo.iniciar(None, None, conf, fabrica_camara=fab_cam, fabrica_montura=fab_mont, calibracion=cal)
    filas = []
    t0 = time.perf_counter()
    t_bloqueo = None
    t_convergencia = None
    try:
        while time.perf_counter() - t0 < args.segundos:
            time.sleep(0.25)
            e = trabajo.estado()
            tel = e["telemetria"]
            if not e["en_curso"]:
                print(f"❌ el trabajo terminó solo: {e['mensaje']} {e['error']}")
                break
            t = time.perf_counter() - t0
            err = tel.get("error_px", [0, 0])
            mod = float(np.hypot(*err))
            v_real = montura.velocidad_real()
            fila = {"t": round(t, 2), "fase": e["fase"], "error_px": round(mod, 1), "ex": err[0], "ey": err[1],
                    "v_az": tel.get("v_az"), "v_alt": tel.get("v_alt"), "v_real_az": round(v_real[0], 4),
                    "v_real_alt": round(v_real[1], 4), "fps": tel.get("fps"), "manchas": tel.get("manchas")}
            filas.append(fila)
            if e["fase"] == "bloqueado" and t_bloqueo is None:
                t_bloqueo = t
            if t_bloqueo is not None and t_convergencia is None and mod < args.tolerancia:
                t_convergencia = t
            if t_convergencia is not None and mod >= args.tolerancia * 2:
                t_convergencia = None       # se volvió a salir: no cuenta
            if not args.silencio:
                print(f"  {t:5.1f}s {e['fase']:10s} error {mod:6.1f} px  v {tel.get('v_az', 0):+.3f}/{tel.get('v_alt', 0):+.3f}"
                      f"  real {v_real[0]:+.3f}/{v_real[1]:+.3f} °/s  {tel.get('fps', 0)} fps")
    finally:
        trabajo.parar()
        trabajo.esperar(5)
        if servidor:
            servidor.parar()

    if not filas:
        return 1
    os.makedirs("logs", exist_ok=True)
    ruta = os.path.join("logs", f"simulacion_seguimiento_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)
    # Veredicto
    tras = [f for f in filas if t_convergencia is not None and f["t"] >= t_convergencia + 2]
    err_max = max((f["error_px"] for f in tras), default=None)
    err_med = float(np.mean([f["error_px"] for f in tras])) if tras else None
    converge = t_convergencia is not None and t_convergencia <= 8.0 and (err_max is None or err_max < args.tolerancia * 2)
    print(f"\n{'✅' if converge else '❌'} bloqueo a los {t_bloqueo and f'{t_bloqueo:.1f}' or '—'} s · "
          f"error < {args.tolerancia:g} px a los {t_convergencia and f'{t_convergencia:.1f}' or '—'} s · "
          f"después: medio {err_med and f'{err_med:.1f}' or '—'} px, máximo {err_max and f'{err_max:.1f}' or '—'} px")
    print(f"📄 {ruta}\n⚠️  Converger en simulación no significa que la montura real llegue: medir con medir_latencia_montura.py")
    return 0 if converge else 1


if __name__ == "__main__":
    sys.exit(main())
