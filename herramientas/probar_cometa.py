# herramientas/probar_cometa.py
#
# Prueba del apilado por el núcleo del cometa (cometa.py) sin cielo ni cámara.
#
# A partir de un FITS real de la galería (RGB apilado, que ASTAP resuelve) se
# fabrica una serie de fotos con un cometa sintético que se mueve respecto a las
# estrellas (coma + condensación central), con giro de campo y deriva como en la
# altacimutal, y se pasan una a una por el apilador real (apilado.py con ASTAP) en
# una copia aislada del programa (%TEMP%\scopepower_prueba_cometa: no toca
# static\, capturas\ ni config.ini de verdad). Las efemérides se sustituyen por
# la trayectoria de verdad con un error constante (como el del MPC), o se quitan
# con --sin-efemeride para probar el seguimiento foto a foto.
#
# Comprueba que:
#   - el núcleo medido en cada foto coincide con el de verdad (< 1,5 px),
#   - el error de la efeméride se corrige con la primera medida,
#   - el cometa sale nítido en el apilado por el núcleo y corrido en el normal,
#   - las estrellas salen nítidas en el normal y arrastradas en el del cometa,
# y mide lo que tarda la parte del cometa por foto.
#
# Uso:
#   python herramientas\probar_cometa.py
#   python herramientas\probar_cometa.py --fits static\fotos\M_36_20260929_121741.fits --fotos 12
#   python herramientas\probar_cometa.py --sin-efemeride
#   python herramientas\probar_cometa.py --modo incremental

import os
import re
import sys
import glob
import time
import shutil
import argparse
import subprocess
import configparser
from datetime import datetime, timedelta, timezone

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COPIA = os.path.join(os.environ.get("TEMP", RAIZ), "scopepower_prueba_cometa")


def preparar_copia(modo, fotos_referencia):
    """Copia aislada del programa: .py, config.ini, datos\\ y lo mínimo de static\\."""
    if os.path.isdir(COPIA):
        shutil.rmtree(COPIA, ignore_errors=True)
    os.makedirs(os.path.join(COPIA, "static"), exist_ok=True)
    os.makedirs(os.path.join(COPIA, "capturas"), exist_ok=True)
    os.makedirs(os.path.join(COPIA, "logs"), exist_ok=True)
    for ruta in glob.glob(os.path.join(RAIZ, "*.py")):
        shutil.copy(ruta, COPIA)
    shutil.copytree(os.path.join(RAIZ, "datos"), os.path.join(COPIA, "datos"), dirs_exist_ok=True)
    for nombre in ("esperando.png", "logo_transparente.png", "logo.png"):
        origen = os.path.join(RAIZ, "static", nombre)
        if os.path.exists(origen):
            shutil.copy(origen, os.path.join(COPIA, "static", nombre))
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(RAIZ, "config.ini"), encoding="utf-8")
    cfg["Dispositivos"]["exposicion_captura"] = "30"
    cfg["Dispositivos"]["numero_maximo_imagenes"] = "60"
    if "Apilado" not in cfg:
        cfg["Apilado"] = {}
    cfg["Apilado"]["modo_apilado"] = modo
    cfg["Apilado"]["fotos_referencia"] = str(fotos_referencia)
    cfg["Apilado"]["detectar_rastros"] = "false"
    cfg["Apilado"]["detectar_luces"] = "false"
    cfg["Apilado"]["objetos_anotados"] = "false"
    if "Cometa" not in cfg:
        cfg["Cometa"] = {}
    cfg["Cometa"]["activo"] = "auto"
    with open(os.path.join(COPIA, "config.ini"), "w", encoding="utf-8") as f:
        cfg.write(f)
    with open(os.path.join(COPIA, "static", "info_objeto_goto.txt"), "w", encoding="utf-8") as f:
        f.write("Nombre: C/2026 T1 (Prueba)\nTipo: cometa\nMagnitud: 8.00\n")


def main():
    p = argparse.ArgumentParser(description="Prueba el apilado por el núcleo del cometa con fotos sintéticas y ASTAP.")
    p.add_argument("--fits", help="FITS RGB de la galería que sirve de campo (por defecto el más reciente de static\\fotos)")
    p.add_argument("--fotos", type=int, default=12, help="fotos de la serie (12)")
    p.add_argument("--cadencia", type=float, default=30.0, help="segundos entre fotos (30)")
    p.add_argument("--velocidad", type=float, nargs=2, default=(4.0, -2.0), metavar=("VX", "VY"),
                   help="movimiento del cometa en px/min (4 -2)")
    p.add_argument("--modo", choices=("mejores", "incremental"), default="incremental")
    p.add_argument("--fotos-referencia", type=int, default=6)
    p.add_argument("--sin-efemeride", action="store_true", help="el cometa no está en las listas del MPC")
    p.add_argument("--error-efemeride", type=float, nargs=2, default=(25.0, -15.0), metavar=("EX", "EY"),
                   help="error constante de la efeméride en px (25 -15)")
    args = p.parse_args()

    import numpy as np
    import cv2
    from astropy.io import fits
    from astropy.io.fits import PrimaryHDU

    base_fits = args.fits or max(glob.glob(os.path.join(RAIZ, "static", "fotos", "*.fits")), key=os.path.getmtime)
    print(f"Campo base: {base_fits}")
    preparar_copia(args.modo, args.fotos_referencia)
    os.chdir(COPIA)
    sys.path.insert(0, COPIA)

    import apilado
    import cometa
    import voz
    import humor
    import cuerpos_menores
    voz.decir = lambda *a, **k: None
    humor.decir = lambda *a, **k: None

    # ── Campo base y su WCS (para la efeméride falsa) ──
    with fits.open(base_fits) as hdul:
        base = np.nan_to_num(hdul[0].data.astype(np.float32))
        hdr_base = hdul[0].header.copy()
    if base.ndim != 3 or base.shape[0] != 3:
        sys.exit("El FITS base debe ser RGB (3×H×W) de la galería")
    _, H, W = base.shape
    centro = (W / 2.0, H / 2.0)
    mono = os.path.join(COPIA, "base_mono.fits")
    apilado.generar_fits_monocromo_para_astap(base_fits, mono)
    subprocess.run(f'"{apilado.config_inicial.ruta_astap()}" -f "{mono}" -solve -r 50', shell=True,
                   capture_output=True, timeout=120)
    ini = mono.replace(".fits", ".ini")
    if not apilado.ini_resuelto(ini):
        sys.exit("ASTAP no resuelve el campo base: elige otro FITS con --fits")
    wcs_base = apilado.parse_ini_wcs(ini)["wcs"]

    # ── Trayectoria de verdad del cometa (px del campo base por instante) ──
    t0 = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=args.cadencia * args.fotos)
    p0 = (centro[0] - 30.0, centro[1] + 20.0)
    vx, vy = args.velocidad

    def posicion(t):
        m = (t - t0).total_seconds() / 60.0
        return p0[0] + vx * m, p0[1] + vy * m

    # ── Efeméride falsa: la trayectoria de verdad con un error constante ──
    if args.sin_efemeride:
        cuerpos_menores.buscar = lambda nombre: None
    else:
        ex, ey = args.error_efemeride

        def coordenadas_falsas(nombre, lat, lon, alt_m=0.0, cuando=None):
            x, y = posicion(cuando or datetime.utcnow())
            ra, dec = wcs_base.all_pix2world([[x + ex, y + ey]], 0)[0]
            return float(ra) / 15.0, float(dec), {"nombre": nombre, "tipo": "cometa", "mag": 8.0}
        cuerpos_menores.buscar = lambda nombre: {"nombre": nombre, "tipo": "cometa", "linea": ""}
        cuerpos_menores.coordenadas = coordenadas_falsas

    # ── Modelo del cometa: coma verdosa + condensación central ──
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    COLOR_COMA = (0.8, 1.0, 0.9)
    AMP_COMA, SIG_COMA, AMP_NUCLEO, SIG_NUCLEO = 600.0, 10.0, 2500.0, 2.5

    def con_cometa(x, y):
        r2 = (xx - x) ** 2 + (yy - y) ** 2
        coma = np.exp(-r2 / (2 * SIG_COMA ** 2))
        nucleo = np.exp(-r2 / (2 * SIG_NUCLEO ** 2))
        return np.stack([base[c] + AMP_COMA * COLOR_COMA[c] * coma + AMP_NUCLEO * nucleo for c in range(3)])

    rng = np.random.default_rng(7)
    zona_local = datetime.now().astimezone().tzinfo
    verdad = []
    apilador = apilado.ApiladorEnTiempoReal("capturas", os.path.join("static", "resultado_apilado_preview.jpg"))
    apilado.apilador_global = apilador          # obtener_estado_apilado() lee el global
    tiempos = []
    print(f"{args.fotos} fotos cada {args.cadencia:.0f} s, cometa a ({vx}, {vy}) px/min, "
          f"{'sin efeméride' if args.sin_efemeride else 'efeméride con error (%.0f, %.0f) px' % tuple(args.error_efemeride)}, "
          f"modo {args.modo}")
    for i in range(args.fotos):
        t_medio = t0 + timedelta(seconds=args.cadencia * i)
        x, y = posicion(t_medio)
        verdad.append((t_medio, x, y))
        cubo = con_cometa(x, y)
        # Giro de campo y deriva: A lleva el cielo (base) a la foto
        A = cv2.getRotationMatrix2D(centro, 0.25 * i, 1.0)
        A[:, 2] += (1.5 * i, -1.0 * i)
        foto = np.stack([cv2.warpAffine(cubo[c], A, (W, H), flags=cv2.INTER_LINEAR) for c in range(3)])
        foto += rng.normal(0, 8.0, foto.shape).astype(np.float32)
        hdr = hdr_base.copy()
        for clave in ("NSTACK", "EXPTOT", "ZONAX0", "ZONAY0", "ZONAW", "ZONAH", "NORTEGIR", "NORTEESP", "DATE", "COMMENT"):
            hdr.pop(clave, None)
        hdr["EXPTIME"] = (args.cadencia, "Exposicion (s)")
        hdr["EXPOSURE"] = (args.cadencia, "Exposicion (s)")
        fin_local = (t_medio + timedelta(seconds=args.cadencia / 2)).replace(tzinfo=timezone.utc).astimezone(zona_local)
        hdr["DATE-OBS"] = fin_local.strftime("%Y%m%d_%H%M%S")
        ruta = os.path.join("capturas", f"cometa_{i:03d}.fits")
        PrimaryHDU(data=np.clip(foto, 0, 65535).astype(np.float32), header=hdr).writeto(ruta, overwrite=True)
        ini_t = time.time()
        apilador.procesando = True
        try:
            apilador.procesar_imagen_fits(ruta, ObjetosAnotados=False)
        finally:
            apilador.procesando = False
            apilador.terminar_fases()
        tiempos.append(time.time() - ini_t)
        e = apilado.obtener_estado_apilado()
        c = e.get("cometa") or {}
        print(f"  foto {i:2d}: {tiempos[-1]:5.1f} s · apiladas {e['total']} · cometa {c.get('fotos', 0)} · "
              f"núcleo {c.get('ultimo', {}).get('nucleo') if c.get('ultimo') else '-'} "
              f"[{c.get('ultimo', {}).get('usada') if c.get('ultimo') else '-'}] · verdad ({x:.1f}, {y:.1f})")

    # ── Comprobaciones ──
    fallos = []

    def check(cond, msg):
        print(("  OK  " if cond else "  FALLO  ") + msg)
        if not cond:
            fallos.append(msg)

    print("\nResultados")
    estado = apilado.obtener_estado_apilado()
    c = estado.get("cometa")
    check(c is not None and c.get("activo"), f"apilado del cometa activo: {c and c.get('motivo')}")
    check(c is not None and c.get("fotos") == estado["total"], f"fotos en el apilado del cometa {c and c.get('fotos')} = apiladas {estado['total']}")
    check((c or {}).get("efemeride") == (not args.sin_efemeride), f"efeméride disponible: {(c or {}).get('efemeride')}")
    seg = apilador.seguidor_cometa
    if seg is not None:
        errores = []
        for (t, x, y, snr) in seg.medidas:
            xv, yv = posicion(t)
            errores.append(float(np.hypot(x - xv, y - yv)))
        check(len(errores) >= args.fotos - 1, f"núcleo medido en {len(errores)} de {args.fotos} fotos")
        if errores:
            check(max(errores) < 1.5, f"error máximo del núcleo medido {max(errores):.2f} px (media {np.mean(errores):.2f})")
        if not args.sin_efemeride and seg.corrimiento:
            ex, ey = args.error_efemeride
            err_c = float(np.hypot(seg.corrimiento[0] + ex, seg.corrimiento[1] + ey))
            check(err_c < 2.0, f"corrimiento de la efeméride corregido: {seg.corrimiento[0]:.1f}, {seg.corrimiento[1]:.1f} px (esperado {-ex:.0f}, {-ey:.0f})")
        print(f"  velocidad medida: {c.get('velocidad_px_min')} px/min = {c.get('velocidad_arcsec_min')}\"/min "
              f"(verdad {np.hypot(vx, vy):.2f} px/min)")

    # Nitidez del cometa y de las estrellas en los dos apilados
    def pico(img, x, y, r):
        x, y = int(round(x)), int(round(y))
        rec = img[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
        return float(rec.max() - np.median(img[::8, ::8]))

    if os.path.exists(cometa.RUTA_FITS) and os.path.exists("static/resultado_apilado.fits"):
        with fits.open(cometa.RUTA_FITS) as hdul:
            g_cometa = hdul[0].data[1].astype(np.float32)
            nx, ny = float(hdul[0].header.get("NUCLEOX", p0[0])), float(hdul[0].header.get("NUCLEOY", p0[1]))
        with fits.open("static/resultado_apilado.fits") as hdul:
            g_estrellas = hdul[0].data[1].astype(np.float32)
        pico_foto = AMP_COMA * COLOR_COMA[1] + AMP_NUCLEO
        pc = pico(g_cometa, nx, ny, 6)
        # En el apilado por estrellas el cometa queda corrido a lo largo de su recorrido: se
        # mide sobre la diferencia con el campo base (sin cometa), para que no cuente una estrella
        diferencia = g_estrellas - base[1]
        # ... y sin los restos de las estrellas (interpolación, nivelado del fondo): se tapan
        fondo_b, ruido_b = float(np.median(base[1][::8, ::8])), 1.4826 * float(np.median(np.abs(base[1][::8, ::8] - np.median(base[1][::8, ::8]))))
        estrellas = cv2.dilate((base[1] > fondo_b + 5 * ruido_b).astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
        diferencia[estrellas] = 0.0
        pe = pico(diferencia, p0[0] + vx * args.cadencia * args.fotos / 120, p0[1] + vy * args.cadencia * args.fotos / 120, 60)
        check(pc > 0.8 * pico_foto, f"cometa nítido por el núcleo: pico {pc:.0f} ADU frente a {pico_foto:.0f} de una foto")
        check(pe < 0.65 * pico_foto, f"cometa corrido por las estrellas: pico {pe:.0f} ADU")
        # La estrella más brillante del campo lejos del cometa
        g_base = base[1]
        lejos = np.hypot(xx - p0[0], yy - p0[1]) > 150
        borde = (xx > 200) & (xx < W - 200) & (yy > 200) & (yy < H - 200)
        iy, ix = np.unravel_index(int(np.argmax(np.where(lejos & borde, g_base, -np.inf))), g_base.shape)
        pe_e, pe_c = pico(g_estrellas, ix, iy, 4), pico(g_cometa, ix, iy, 4)
        check(pe_c < 0.75 * pe_e, f"estrella ({ix}, {iy}) arrastrada por el núcleo: {pe_c:.0f} frente a {pe_e:.0f} ADU por las estrellas")
        check(os.path.exists(cometa.RUTA_PREVIEW), f"vista previa del cometa escrita: {cometa.RUTA_PREVIEW}")
    else:
        check(False, "no hay FITS del apilado del cometa o del normal")

    d = apilador.duraciones.get("cometa")
    print(f"  tiempo por foto: {np.mean(tiempos):.1f} s en total, de los que la parte del cometa (FITS + vista previa) "
          f"{np.mean(d) if d else 0:.1f} s")
    print(f"\nResultados en {COPIA}\\static (resultado_cometa_preview.jpg y resultado_apilado_preview.jpg)")
    print(f"\n{'TODO BIEN' if not fallos else str(len(fallos)) + ' FALLO(S)'}")
    for f in fallos:
        print(" -", f)
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
