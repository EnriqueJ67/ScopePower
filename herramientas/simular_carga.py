# herramientas/simular_carga.py
#
# Simula la carga de trabajo de una sesión real de apilado en el PC de campo,
# sin telescopio, sin cámara y sin cielo, para medir el consumo con una batería:
#
#   1. Apunta los mAh que marca el cargador (o pon a cero su contador).
#   2. Lanza  simular_carga.bat  (o  python herramientas\simular_carga.py).
#   3. Al cabo de 1 hora el script termina y resume lo que ha hecho.
#   4. Los mAh que ha "cargado" la batería en esa hora son el consumo por hora
#      del mini PC trabajando; divide la capacidad útil de la batería entre esa
#      cifra y tienes las horas de autonomía.
#
# Cada "exposición" (cada N segundos, los mismos que exposicion_captura en
# config.ini) el script recorre exactamente los mismos pasos que apilado.py con
# el mismo código y el mismo tamaño de imagen que la Saturn-C (3008x3008 RGGB):
# escribir y leer el FITS, debayer, puntuar las estrellas, resolver con ASTAP,
# alinear, apilar con sigma-clip las N fotos guardadas, guardar el FITS apilado,
# estirar y hacer el JPG de la web. Entre foto y foto el PC descansa, como en
# la realidad (la cámara está exponiendo).
#
# Foto base: si hay un FITS real (--fits, o el más reciente de capturas\) se
# usa como base, desplazado unos píxeles al azar en cada "captura", y ASTAP lo
# resuelve de verdad. Si no hay ninguno, se genera un campo de estrellas
# sintético y el tiempo de ASTAP se imita con cálculo puro (--astap-segundos).
#
# Uso:
#   python herramientas\simular_carga.py                 (1 hora, config.ini)
#   python herramientas\simular_carga.py --minutos 30
#   python herramientas\simular_carga.py --exposicion 10 --num-max 30
#   python herramientas\simular_carga.py --fits capturas\captura_xxx.fits
#   python herramientas\simular_carga.py --sintetico       (ignora los FITS reales)
#   python herramientas\simular_carga.py --sin-pausa       (carga máxima continua)
#   python herramientas\simular_carga.py --modo incremental   (apilado incremental, ver config.example.ini)
#
# Todo lo que escribe va a logs\simulacion_carga\ (FITS de trabajo, JPG y el
# log con los tiempos de cada foto); no toca static\, capturas\ ni config.ini.

import os
import sys
import time
import glob
import argparse
import subprocess
import configparser
from datetime import datetime

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import numpy as np
import cv2
from astropy.io import fits
from astropy.io.fits import PrimaryHDU

import procesado
from AutoStrechSTF import debayer_rgb, guardar_jpg_web
from apilado import (calcular_puntuacion, apilar_sigma_clip, zona_comun, zona_cobertura, esquinas_transformadas,
                     generar_fits_monocromo_para_astap, ini_resuelto, parse_ini_wcs, leer_config_calidad, ASTAP_TIMEOUT)
from apilado_incremental import Acumulador

CARPETA = os.path.join("logs", "simulacion_carga")
ANCHO, ALTO = 3008, 3008          # IMX533 (Saturn-C SQR)
PATRON = "RGGB"


def leer_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}
    calidad = leer_config_calidad()
    return {
        "exposicion": float(d.get("exposicion_captura", 20)),
        "num_max": max(1, int(d.get("numero_maximo_imagenes", 20))),
        "modo": calidad["modo"],
        "fotos_referencia": calidad["fotos_referencia"],
        "sigma_incremental": calidad["sigma_incremental"],
        "ruta_astap": cfg["General"].get("ruta_astap", "") if "General" in cfg else "",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Fotos "capturadas"
# ─────────────────────────────────────────────────────────────────────────────

class FuenteSintetica:
    """Campo de estrellas fijo + fondo con ruido nuevo en cada foto, en mosaico RGGB."""

    def __init__(self, rng, n_estrellas=400):
        self.rng = rng
        puntos = np.zeros((ALTO, ANCHO), dtype=np.float32)
        xs = rng.integers(20, ANCHO - 20, n_estrellas)
        ys = rng.integers(20, ALTO - 20, n_estrellas)
        brillo = rng.pareto(1.5, n_estrellas) * 1500 + 300
        puntos[ys, xs] = np.minimum(brillo, 40000)
        self.estrellas = cv2.GaussianBlur(puntos, (0, 0), 1.6) * 8.0
        # Ganancia por color del mosaico RGGB (el rojo y el azul ven menos fondo)
        self.mosaico = np.ones((ALTO, ANCHO), dtype=np.float32)
        self.mosaico[0::2, 0::2] = 0.7   # R
        self.mosaico[1::2, 1::2] = 0.8   # B
        self.cabecera = fits.Header()
        self.cabecera["BAYERPAT"] = PATRON
        self.cabecera["EXPTIME"] = 20.0
        self.cabecera["OBJECT"] = "SIMULACION"

    def foto(self):
        dx, dy = (int(v) for v in self.rng.integers(-12, 13, 2))
        campo = np.roll(self.estrellas, (dy, dx), axis=(0, 1))
        fondo = 800.0 + self.rng.normal(0.0, 35.0, (ALTO, ANCHO)).astype(np.float32)
        img = (campo + fondo) * self.mosaico
        return np.clip(img, 0, 65535).astype(np.uint16), self.cabecera


class FuenteReal:
    """Un FITS real desplazado unos píxeles al azar y con algo de ruido nuevo cada vez."""

    def __init__(self, rng, ruta):
        self.rng = rng
        with fits.open(ruta) as hdul:
            self.cabecera = hdul[0].header.copy()
            data = np.nan_to_num(hdul[0].data.astype(np.float32))
        if data.ndim != 2:
            raise SystemExit(f"{ruta}: se esperaba un FITS CFA 2D (mosaico Bayer), no {data.shape}")
        self.base = data
        self.ruta = ruta

    def foto(self):
        # Desplazamiento par: así el mosaico Bayer no cambia de fase
        dx, dy = (int(v) * 2 for v in self.rng.integers(-6, 7, 2))
        img = np.roll(self.base, (dy, dx), axis=(0, 1))
        img = img + self.rng.normal(0.0, 6.0, img.shape).astype(np.float32)
        return np.clip(img, 0, 65535).astype(np.uint16), self.cabecera


# ─────────────────────────────────────────────────────────────────────────────
#  ASTAP
# ─────────────────────────────────────────────────────────────────────────────

def resolver_astap(ruta_fits, ruta_astap):
    """Resuelve el FITS con ASTAP como hace apilado.py. Devuelve el dict de parse_ini_wcs o None."""
    mono = ruta_fits.replace(".fits", "_mono.fits")
    ini = mono.replace(".fits", ".ini")
    try:
        generar_fits_monocromo_para_astap(ruta_fits, mono)
        subprocess.run(f'"{ruta_astap}" -f "{mono}" -solve -r 50',
                       shell=True, capture_output=True, timeout=ASTAP_TIMEOUT)
        return parse_ini_wcs(ini) if ini_resuelto(ini) else None
    except subprocess.TimeoutExpired:
        return None
    finally:
        for f in (mono, ini, mono.replace(".fits", ".wcs")):
            try:
                os.remove(f)
            except OSError:
                pass


def matriz_desde_wcs(ref, ahora):
    cx, cy = ref["crpix"]
    ra, dec = ref["wcs"].all_pix2world([[cx, cy]], 0)[0]
    x_new, y_new = ahora["wcs"].all_world2pix([[ra, dec]], 0)[0]
    M = cv2.getRotationMatrix2D((cx, cy), ahora["angle"] - ref["angle"], 1.0)
    M[0, 2] += cx - x_new
    M[1, 2] += cy - y_new
    return M


def imitar_astap(segundos):
    """Sin ASTAP: cálculo de un solo hilo durante `segundos` (ASTAP también va a un hilo)."""
    fin = time.perf_counter() + segundos
    a = np.random.default_rng(1).random((512, 512)).astype(np.float32)
    while time.perf_counter() < fin:
        a = np.abs(np.fft.fft2(a)).astype(np.float32)
        a /= a.max() + 1e-6


# ─────────────────────────────────────────────────────────────────────────────
#  Simulación
# ─────────────────────────────────────────────────────────────────────────────

def cpu_pct(cpu0, wall0):
    """% de CPU del proceso (100 % = todos los núcleos) desde (cpu0, wall0)."""
    dt = time.perf_counter() - wall0
    return 100.0 * (time.process_time() - cpu0) / max(dt, 1e-6) / max(os.cpu_count() or 1, 1)


def main():
    cfg = leer_config()
    p = argparse.ArgumentParser(description="Simula la carga de una sesión de apilado para medir el consumo.")
    p.add_argument("--minutos", type=float, default=60, help="duración de la prueba (60)")
    p.add_argument("--exposicion", type=float, default=cfg["exposicion"],
                   help=f"segundos entre fotos, como exposicion_captura ({cfg['exposicion']:g})")
    p.add_argument("--num-max", type=int, default=cfg["num_max"],
                   help=f"fotos que se guardan y apilan, como numero_maximo_imagenes ({cfg['num_max']})")
    p.add_argument("--modo", choices=("mejores", "incremental"), default=cfg["modo"],
                   help=f"cómo se apila, como modo_apilado ({cfg['modo']})")
    p.add_argument("--fotos-referencia", type=int, default=cfg["fotos_referencia"],
                   help=f"fotos que fijan la referencia en el modo incremental ({cfg['fotos_referencia']})")
    p.add_argument("--fits", help="FITS real (CFA) que usar como foto base; por defecto el más reciente de capturas\\")
    p.add_argument("--sintetico", action="store_true", help="campo de estrellas sintético aunque haya FITS reales")
    p.add_argument("--astap-segundos", type=float, default=4.0,
                   help="segundos de cálculo que imitan a ASTAP cuando no se puede usar de verdad (4)")
    p.add_argument("--sin-pausa", action="store_true", help="no esperar entre fotos: carga máxima continua")
    args = p.parse_args()

    os.makedirs(CARPETA, exist_ok=True)
    for viejo in glob.glob(os.path.join(CARPETA, "*.fits")):
        os.remove(viejo)
    ruta_log = os.path.join(CARPETA, "simulacion_carga.log")
    log_f = open(ruta_log, "a", encoding="utf-8")

    def log(texto):
        linea = f"{datetime.now():%H:%M:%S} {texto}"
        print(linea, flush=True)
        log_f.write(linea + "\n")
        log_f.flush()

    rng = np.random.default_rng()
    ruta_base = args.fits
    if ruta_base is None and not args.sintetico:
        candidatos = [f for f in glob.glob(os.path.join("capturas", "*.fits")) if "_mono" not in f]
        if candidatos:
            ruta_base = max(candidatos, key=os.path.getmtime)
    usar_astap = False
    if ruta_base and not args.sintetico:
        fuente = FuenteReal(rng, ruta_base)
        usar_astap = bool(cfg["ruta_astap"]) and os.path.exists(cfg["ruta_astap"])
        log(f"Foto base: {ruta_base} ({fuente.base.shape[1]}x{fuente.base.shape[0]}); "
            f"ASTAP {'de verdad' if usar_astap else 'NO encontrado: se imita con cálculo'}")
    else:
        fuente = FuenteSintetica(rng)
        log(f"Foto base: campo sintético {ANCHO}x{ALTO} {PATRON}; ASTAP imitado con {args.astap_segundos:g} s de cálculo")
    limite = (f"apilado incremental (referencia de {args.fotos_referencia} fotos, sin tope)"
              if args.modo == "incremental" else f"apilando hasta {args.num_max}")
    log(f"Prueba de {args.minutos:g} min: una foto cada {args.exposicion:g} s, {limite} "
        f"({os.cpu_count()} núcleos){' — SIN pausa entre fotos' if args.sin_pausa else ''}")
    log("Apunta ahora los mAh del cargador.")

    ajustes = procesado.leer_config()
    fotogramas = []
    acumulador = None       # modo incremental: suma/cuenta/sigma por píxel una vez fijada la referencia
    n_apiladas = 0
    ref_info = None
    n_fotos = 0
    tiempos = []
    fallos_astap = 0
    inicio = time.perf_counter()
    cpu_ini = time.process_time()
    fin = inicio + args.minutos * 60

    try:
        while time.perf_counter() < fin:
            t_foto = time.perf_counter()
            cpu_foto = time.process_time()
            fases = {}

            def fase(nombre, t0):
                fases[nombre] = time.perf_counter() - t0
                return time.perf_counter()

            # Captura: el bucle real convierte el ImageArray del driver y escribe el FITS
            t = time.perf_counter()
            n_fotos += 1
            cfa, cabecera = fuente.foto()
            ruta_fits = os.path.join(CARPETA, f"sim_{n_fotos:04d}.fits")
            PrimaryHDU(cfa, header=cabecera).writeto(ruta_fits, overwrite=True)
            del cfa
            t = fase("captura", t)

            # Leer + debayer (apilado.procesar_imagen_fits)
            with fits.open(ruta_fits) as hdul:
                hdr = hdul[0].header
                data = np.nan_to_num(hdul[0].data.astype(np.float32))
            raw = np.clip(data, 0, 65535).astype(np.uint16)
            rgb = debayer_rgb(raw, hdr.get("BAYERPAT", PATRON)).astype(np.float32)
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            del data, raw, rgb
            t = fase("leer", t)

            puntuacion, n_estrellas, nitidez = calcular_puntuacion(g)
            t = fase("puntuar", t)

            # Resolver y alinear
            if usar_astap:
                info = resolver_astap(ruta_fits, cfg["ruta_astap"])
                if info is None:
                    fallos_astap += 1
                    M = np.eye(2, 3, dtype=np.float32)
                elif ref_info is None:
                    ref_info = info
                    M = np.eye(2, 3, dtype=np.float32)
                else:
                    M = matriz_desde_wcs(ref_info, info)
            else:
                imitar_astap(args.astap_segundos)
                ang = float(rng.normal(0.0, 0.05))
                M = cv2.getRotationMatrix2D((ANCHO / 2, ALTO / 2), ang, 1.0)
                M[:, 2] += rng.normal(0.0, 6.0, 2)
            t = fase("resolver", t)

            h, w = r.shape
            r = cv2.warpAffine(r, M, (w, h), flags=cv2.INTER_LINEAR)
            g = cv2.warpAffine(g, M, (w, h), flags=cv2.INTER_LINEAR)
            b = cv2.warpAffine(b, M, (w, h), flags=cv2.INTER_LINEAR)
            t = fase("alinear", t)

            # Apilar + FITS de salida
            if acumulador is not None:
                acumulador.anadir(r, g, b, M)
                n_apiladas += 1
            else:
                fotogramas.append({
                    "esquinas": esquinas_transformadas(M, w, h),
                    "M": M,
                    "r": np.clip(r, 0, 65535).astype(np.uint16),
                    "g": np.clip(g, 0, 65535).astype(np.uint16),
                    "b": np.clip(b, 0, 65535).astype(np.uint16),
                })
                if args.modo != "incremental" and len(fotogramas) > args.num_max:
                    fotogramas.pop(0)
                n_apiladas = len(fotogramas)
                if args.modo == "incremental" and n_apiladas >= args.fotos_referencia:
                    acumulador = Acumulador(fotogramas, k=cfg["sigma_incremental"])
                    fotogramas = []
            del r, g, b
            if acumulador is not None:
                r, g, b = acumulador.imagen()
                zona = zona_cobertura(*acumulador.cobertura())
            else:
                r = apilar_sigma_clip([f["r"] for f in fotogramas])
                g = apilar_sigma_clip([f["g"] for f in fotogramas])
                b = apilar_sigma_clip([f["b"] for f in fotogramas])
                zona = zona_comun([f["esquinas"] for f in fotogramas], w, h)
            cubo = np.stack([r, g, b], axis=0).astype(np.float32)
            del r, g, b
            hdr_out = hdr.copy()
            for k in ("BAYERPAT", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "BITPIX"):
                hdr_out.pop(k, None)
            hdr_out["NSTACK"] = n_apiladas
            PrimaryHDU(cubo, header=hdr_out).writeto(os.path.join(CARPETA, "apilado.fits"), overwrite=True)
            t = fase("apilar", t)

            # Estirado y JPG de la web (apilado.generar_preview)
            recorte, _, _ = procesado.recortar_campo(cubo, 1.0, zona)
            rgb8 = procesado.procesar(recorte, n_apiladas, ajustes, escala=0.5)
            bgr = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
            cv2.imwrite(os.path.join(CARPETA, "preview.jpg"), bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            guardar_jpg_web(os.path.join(CARPETA, "preview_web.jpg"), bgr)
            del cubo, recorte, rgb8, bgr
            t = fase("estirar", t)

            os.remove(ruta_fits)
            total = time.perf_counter() - t_foto
            tiempos.append(total)
            detalle = " ".join(f"{k} {v:.1f}" for k, v in fases.items())
            log(f"foto {n_fotos:3d}: {total:5.1f} s (cpu {cpu_pct(cpu_foto, t_foto):3.0f} %) "
                f"apiladas {n_apiladas:2d}, {n_estrellas} estrellas — {detalle}")

            # Descanso hasta la siguiente "exposición"
            restante = fin - time.perf_counter()
            if restante <= 0:
                break
            if not args.sin_pausa:
                espera = args.exposicion - total
                if espera > 0:
                    time.sleep(min(espera, restante))
                else:
                    log(f"   ⚠️ el procesado ({total:.0f} s) tarda más que la exposición ({args.exposicion:g} s): sin descanso")
    except KeyboardInterrupt:
        log("Interrumpido con Ctrl+C.")

    minutos = (time.perf_counter() - inicio) / 60
    ocupado = sum(tiempos) / max(time.perf_counter() - inicio, 1e-6)
    log("─" * 70)
    log(f"Fin: {n_fotos} fotos en {minutos:.1f} min; procesado {np.mean(tiempos) if tiempos else 0:.1f} s por foto "
        f"(mín {min(tiempos) if tiempos else 0:.1f}, máx {max(tiempos) if tiempos else 0:.1f})")
    log(f"PC ocupado el {ocupado:.0%} del tiempo; CPU media del proceso {cpu_pct(cpu_ini, inicio):.0f} % "
        f"(100 % = los {os.cpu_count()} núcleos)")
    if usar_astap and fallos_astap:
        log(f"ASTAP no resolvió {fallos_astap} de {n_fotos} fotos")
    log("Apunta ahora los mAh del cargador: la diferencia es el consumo de esta prueba.")
    log(f"Log completo en {ruta_log}")
    log_f.close()
    for f in glob.glob(os.path.join(CARPETA, "sim_*.fits")):
        os.remove(f)


if __name__ == "__main__":
    main()
