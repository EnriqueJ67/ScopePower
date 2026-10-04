# herramientas/comparar_estirado.py
#
# Compara el estirado antiguo (STF global, AutoStrechSTF.apply_stf_color) con el
# estirado adaptativo multiescala (procesado.estirar_multiescala) sobre un FITS
# apilado, y mide lo que tarda cada paso. Sirve para afinar los parámetros con
# imágenes reales y para medir el coste en el mini PC de campo.
#
#   python herramientas\comparar_estirado.py static\resultado_apilado.fits [--escala 0.5] [--completo]
#
# Escribe en comparaciones/ (fuera de static, para no mezclar con la web):
#   <nombre>_comparacion.jpg   izquierda STF, derecha multiescala, con recortes
#                              ampliados de la estrella más brillante y del centro
#   <nombre>_stf.jpg / <nombre>_mas.jpg   cada estirado entero
# --completo pasa además por todo el pipeline (ruido, contraste, color) y mide
# el tiempo total, que es lo que nota el usuario.

import os
import sys
import time
import argparse

import numpy as np
import cv2
from astropy.io import fits

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import procesado                                   # noqa: E402
from AutoStrechSTF import apply_stf_color, debayer_rgb   # noqa: E402


def cronometro(nombre, funcion, *args, **kwargs):
    t0 = time.perf_counter()
    r = funcion(*args, **kwargs)
    print(f"  {nombre:<44s} {time.perf_counter() - t0:6.2f} s")
    return r


def a_uint8(rgbf):
    return np.clip(rgbf * 255.0 + 0.5, 0, 255).astype(np.uint8)


def recorte(img, cx, cy, lado, zoom):
    h, w = img.shape[:2]
    x0, y0 = int(max(0, min(w - lado, cx - lado // 2))), int(max(0, min(h - lado, cy - lado // 2)))
    r = img[y0:y0 + lado, x0:x0 + lado]
    return cv2.resize(r, (lado * zoom, lado * zoom), interpolation=cv2.INTER_NEAREST)


def rotular(img, texto):
    img = img.copy()
    cv2.putText(img, texto, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, texto, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fits", nargs="?", default="static/resultado_apilado.fits")
    ap.add_argument("--escala", type=float, default=0.5, help="0.5 = vista previa, 1.0 = galería")
    ap.add_argument("--completo", action="store_true", help="pasar también por ruido/contraste/color")
    ap.add_argument("--estilo", default=None, help="natural | seestar | agresivo (por defecto el de config.ini)")
    for clave in ("fondo_objetivo", "agresividad", "compresion", "recuperacion", "escala_fondo"):
        ap.add_argument("--" + clave, type=float, default=None)
    ap.add_argument("--curva", default=None, help="gamma | asinh | mtf")
    args = ap.parse_args()

    with fits.open(args.fits) as hdul:
        cubo = hdul[0].data
        cabecera = hdul[0].header
    if cubo.ndim == 3 and cubo.shape[0] == 3:                  # apilado RGB (3, H, W)
        rgb = np.transpose(cubo.astype(np.float32), (1, 2, 0))
    elif cubo.ndim == 2:                                       # toma CFA sin apilar
        rgb = debayer_rgb(cubo, cabecera.get("BAYERPAT", "RGGB")).astype(np.float32)
    else:
        rgb = cubo.astype(np.float32)
    print(f"{args.fits}: {rgb.shape[1]}x{rgb.shape[0]} px, mediana {np.median(rgb):.1f} ADU, max {rgb.max():.0f}")

    ajustes = procesado.leer_config()
    if args.estilo:
        ajustes.update(procesado.ESTILOS[args.estilo]); ajustes["estilo"] = args.estilo
    for clave in ("fondo_objetivo", "agresividad", "compresion", "recuperacion"):
        if getattr(args, clave) is not None:
            ajustes[clave] = getattr(args, clave)
    if args.escala_fondo is not None:
        ajustes["escala"] = args.escala_fondo
    if args.curva:
        ajustes["curva"] = args.curva
    print("ajustes:", {k: ajustes[k] for k in ("estilo", "fondo_objetivo", "agresividad", "compresion", "recuperacion")},
          "curva", ajustes.get("curva", "gamma"), "escala fondo", ajustes.get("escala", procesado.ESCALA_FONDO_PX), "px")

    if args.escala != 1.0:
        h, w = rgb.shape[:2]
        rgb = cv2.resize(rgb, (int(w * args.escala), int(h * args.escala)), interpolation=cv2.INTER_AREA)
    print(f"procesando a {rgb.shape[1]}x{rgb.shape[0]} px (escala {args.escala})")

    print("Pasos comunes:")
    rgb = cronometro("quitar_gradiente", procesado.quitar_gradiente, rgb)

    print("Estirado antiguo (STF global):")
    stf = cronometro("apply_stf_color", apply_stf_color, rgb, linked=True, neutralizar=True, output_8bit=False)

    print("Estirado multiescala:")
    L = procesado.neutralizar_fondo(rgb).mean(axis=2) / 65535.0
    m, sigma = cronometro("  estadistica_fondo", procesado.estadistica_fondo, L)
    blanco = float(np.percentile(L[::4, ::4], 99.82))
    S = cronometro("  CurvaFondo", procesado.CurvaFondo, m, sigma, ajustes["fondo_objetivo"], ajustes["agresividad"],
                   ajustes.get("curva", "gamma"), blanco)
    Sd = procesado.CurvaFondo(m, sigma, ajustes["fondo_objetivo"], ajustes["agresividad"], ajustes.get("curva", "gamma"),
                              blanco ** (1.0 - float(ajustes["compresion"])))
    print(f"    mediana {m:.5f}  sigma {sigma:.5f}  c0 {S.c0:.5f}  {S.tipo} param {S.param:.3g}"
          f"  -> cielo {float(S(np.float32(m))):.3f}, 2x cielo {float(S(np.float32(2 * m))):.3f}, 10x cielo {float(S(np.float32(10 * m))):.3f}")
    cronometro("  fondo_gran_escala", procesado.fondo_gran_escala, L, m, sigma,
               ajustes.get("escala", procesado.ESCALA_FONDO_PX) * args.escala)
    # Curva efectiva para una fuente puntual sobre el cielo (D ADU por encima del fondo)
    from AutoStrechSTF import calcular_parametros_stf, aplicar_parametros_stf
    bk, wh, gm = calcular_parametros_stf(L * 65535.0, clipping=0.0018, target_median=0.10)
    print(f"    blanco {blanco*65535:.0f} ADU, blanco del detalle {Sd.blanco*65535:.0f} ADU (STF viejo: gamma {gm:.3f}, blanco {wh:.0f} ADU)")
    print("    D sobre el cielo:   " + "".join(f"{t:>9s}" for t in ("+3sig", "+100", "+500", "+2000", "+7300", "+20000", "sat.")))
    Ds = [3 * sigma * 65535, 100, 500, 2000, 7300, 20000, 65535 - m * 65535]
    viejo = [float(aplicar_parametros_stf(np.float32(m * 65535 + d), bk, wh, gm)) for d in Ds]
    nuevo = [float(S(np.float32(m))) + ajustes["recuperacion"] * (float(Sd(np.float32(m + d / 65535.0))) - float(Sd(np.float32(m)))) for d in Ds]
    print("    STF viejo:          " + "".join(f"{min(v, 1):9.3f}" for v in viejo))
    print("    multiescala:        " + "".join(f"{min(v, 1):9.3f}" for v in nuevo))
    mas = cronometro("estirar_multiescala (total)", procesado.estirar_multiescala, rgb, ajustes, args.escala)

    if args.completo:
        print("Pipeline completo (lo que nota el usuario):")
        t0 = time.perf_counter()
        f = cronometro("  reducir_ruido", procesado.reducir_ruido, mas, ajustes["ruido"])
        f = cronometro("  contraste_local", procesado.contraste_local, f, ajustes["contraste"])
        f8 = cronometro("  color (neutralizar, scnr, saturar)", procesado.color, f, ajustes)
        print(f"  {'total tras el estirado':<44s} {time.perf_counter() - t0:6.2f} s")
        mas8 = f8
        stf8 = a_uint8(stf)
    else:
        stf8, mas8 = a_uint8(stf), a_uint8(mas)

    # Recortes: estrella más brillante (en la luminancia lineal, lejos del borde) y centro
    lum = rgb.mean(axis=2)
    h, w = lum.shape
    margen = 64
    interior = lum[margen:h - margen, margen:w - margen]
    iy, ix = np.unravel_index(np.argmax(cv2.GaussianBlur(interior, (0, 0), 1.5)), interior.shape)
    estrella = (ix + margen, iy + margen)
    centro = (w // 2, h // 2)
    lado, zoom = 96, 4
    def panel(img8, titulo):
        bgr = cv2.cvtColor(img8, cv2.COLOR_RGB2BGR)
        alto_vista = 1000
        vista = cv2.resize(bgr, (int(w * alto_vista / h), alto_vista), interpolation=cv2.INTER_AREA)
        r1 = rotular(recorte(bgr, *estrella, lado, zoom), "estrella")
        r2 = rotular(recorte(bgr, *centro, lado, zoom), "centro")
        columna = np.vstack([r1, r2])
        columna = cv2.resize(columna, (int(columna.shape[1] * alto_vista / columna.shape[0]), alto_vista))
        return rotular(np.hstack([vista, columna]), titulo)

    comparacion = np.hstack([panel(stf8, "STF global (antiguo)"), panel(mas8, "Multiescala (nuevo)")])
    os.makedirs("comparaciones", exist_ok=True)
    base = os.path.join("comparaciones", os.path.splitext(os.path.basename(args.fits))[0])
    cv2.imwrite(base + "_comparacion.jpg", comparacion, [cv2.IMWRITE_JPEG_QUALITY, 90])
    cv2.imwrite(base + "_stf.jpg", cv2.cvtColor(stf8, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    cv2.imwrite(base + "_mas.jpg", cv2.cvtColor(mas8, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    print("escrito", base + "_comparacion.jpg")

    # Cuántos píxeles acaban blancos (los tres canales a 255): la medida del "quemado"
    for nombre, img in (("STF", stf8), ("multiescala", mas8)):
        blancos = int(np.count_nonzero(img.min(axis=2) >= 254))
        print(f"  {nombre:<12s} píxeles blancos: {blancos} ({100.0 * blancos / img[..., 0].size:.3f} %)")


if __name__ == "__main__":
    main()
