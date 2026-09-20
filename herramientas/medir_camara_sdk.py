# herramientas/medir_camara_sdk.py
#
# Mide lo que da la cámara Player One por el SDK nativo (camara_nativa.py) en
# este PC: fotogramas por segundo, intervalo entre fotogramas (medio y p95),
# fotogramas perdidos y lo que tarda la detección de manchas sobre cada
# fotograma. Sirve para elegir ROI, binning y formato del seguimiento de
# objetos y para saber si el mini PC de campo llega.
#
#   python herramientas\medir_camara_sdk.py [--roi 512,1024,1504] [--bin 1,2]
#         [--formato RAW8,RAW16] [--exp-ms 1,5,20] [--segundos 5] [--ganancia 200]
#
# Exige que ninguna otra aplicación (ScopePower, N.I.N.A., SharpCap...) tenga
# la cámara conectada: el driver solo admite una conexión.
# Escribe la tabla en consola y en logs/medidas_camara_sdk_<fecha>.csv.

import os
import sys
import csv
import time
import argparse
from datetime import datetime

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import camara_nativa                                  # noqa: E402
from camara_nativa import CamaraNativa, luminancia_rapida, ErrorCamaraNativa   # noqa: E402


def detectar(plano):
    """Detección de manchas como la de seguimiento.py (umbral mediana + 5·MAD y componentes conexas)."""
    import cv2
    mediana = float(np.median(plano))
    mad = float(np.median(np.abs(plano.astype(np.int16) - mediana))) or 1.0
    mascara = (plano > mediana + 5.0 * mad).astype(np.uint8)
    n, _, stats, centroides = cv2.connectedComponentsWithStats(mascara, connectivity=8)
    return n - 1


def lista(texto, tipo):
    return [tipo(x) for x in texto.split(",") if x.strip()]


def medir(cam, roi, binning, formato, exp_ms, ganancia, segundos):
    geo = cam.configurar(roi_px=roi, binning=binning, formato=formato, exposicion_ms=exp_ms, ganancia=ganancia)
    cam.iniciar_video()
    cam.leer_frame(5000)           # el primero puede tardar (arranque del flujo)
    t0 = time.perf_counter()
    t_prev = t0
    intervalos = []
    coste_det = []
    n = 0
    manchas = 0
    while time.perf_counter() - t0 < segundos:
        f = cam.leer_frame()
        t = time.perf_counter()
        intervalos.append(t - t_prev)
        t_prev = t
        n += 1
        if n % 5 == 0:
            td = time.perf_counter()
            manchas = detectar(luminancia_rapida(f))
            coste_det.append(time.perf_counter() - td)
    dt = time.perf_counter() - t0
    perdidos = cam.descartados()
    cam.parar_video()
    iv = np.array(intervalos) * 1000
    cd = np.array(coste_det) * 1000 if coste_det else np.array([0.0])
    return {
        "roi": roi, "bin": binning, "formato": formato, "exp_ms": exp_ms, "lado": geo["lado"],
        "frames": n, "fps": n / dt, "intervalo_ms": iv.mean(), "p95_ms": np.percentile(iv, 95),
        "max_ms": iv.max(), "perdidos": perdidos, "deteccion_ms": cd.mean(), "manchas": manchas,
        "media": float(f.mean()), "maximo": int(f.max()),
    }


def main():
    ap = argparse.ArgumentParser(description="Mide fps y latencia de la cámara Player One por el SDK nativo")
    ap.add_argument("--roi", default="512,1024,1504")
    ap.add_argument("--bin", default="1,2")
    ap.add_argument("--formato", default="RAW8,RAW16")
    ap.add_argument("--exp-ms", default="1,5,20")
    ap.add_argument("--ganancia", type=int, default=200)
    ap.add_argument("--segundos", type=float, default=5.0)
    ap.add_argument("--dll", default=None, help="Ruta a PlayerOneCamera.dll si no está en las habituales")
    args = ap.parse_args()

    rois, bins, formatos, exps = lista(args.roi, int), lista(args.bin, int), lista(args.formato, str), lista(args.exp_ms, float)
    try:
        camaras = camara_nativa.listar_camaras(args.dll)
    except ErrorCamaraNativa as e:
        print(f"❌ {e}")
        return 1
    if not camaras:
        print("❌ No hay ninguna cámara Player One conectada")
        return 1
    c = camaras[0]
    print(f"📷 {c['modelo']} ({c['sensor']}, {c['ancho']}x{c['alto']}, {c['bayer']}, "
          f"{'USB 3' if c['usb3'] else 'USB 2'}) · SDK {camara_nativa.cargar_dll(args.dll).POAGetSDKVersion().decode()}")
    filas = []
    with CamaraNativa(ruta_dll=args.dll) as cam:
        print(f"{'ROI':>5} {'bin':>3} {'fmt':>5} {'exp ms':>6} {'lado':>5} {'fps':>6} {'int ms':>7} {'p95 ms':>7} "
              f"{'max ms':>7} {'perd':>4} {'det ms':>6} {'manch':>5}")
        for formato in formatos:
            for binning in bins:
                for roi in rois:
                    for exp in exps:
                        try:
                            r = medir(cam, roi, binning, formato, exp, args.ganancia, args.segundos)
                        except ErrorCamaraNativa as e:
                            print(f"{roi:>5} {binning:>3} {formato:>5} {exp:>6g}  ❌ {e}")
                            continue
                        filas.append(r)
                        print(f"{r['roi']:>5} {r['bin']:>3} {r['formato']:>5} {r['exp_ms']:>6g} {r['lado']:>5} "
                              f"{r['fps']:>6.1f} {r['intervalo_ms']:>7.1f} {r['p95_ms']:>7.1f} {r['max_ms']:>7.1f} "
                              f"{r['perdidos']:>4} {r['deteccion_ms']:>6.1f} {r['manchas']:>5}")
    if filas:
        os.makedirs("logs", exist_ok=True)
        ruta = os.path.join("logs", f"medidas_camara_sdk_{datetime.now():%Y%m%d_%H%M%S}.csv")
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
            w.writeheader()
            w.writerows(filas)
        print(f"📄 Guardado en {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
