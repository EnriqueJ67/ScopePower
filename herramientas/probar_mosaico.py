# herramientas/probar_mosaico.py
#
# Prueba de la combinación de teselas del modo Mosaico (mosaico.py) sin cielo,
# cámara ni ASTAP: se fabrica un cielo sintético (estrellas en RA/Dec y una
# nebulosa grande) y se "fotografía" por teselas, cada una con su propio WCS
# (giro de campo distinto, espejo, desplazamiento de apuntado), su fondo, su
# gradiente y su ruido, como las deja el apilador. Luego se combinan con
# mosaico.combinar y se comprueba que:
#   - las estrellas caen donde dice el WCS del mosaico (residuo < 0,3 px),
#   - no quedan escalones de fondo en las costuras (diferencia entre teselas
#     vecinas en el solape por debajo del ruido),
#   - la nebulosa que cruza varias teselas sale continua,
# y mide lo que tarda. Se ejecuta en una copia aislada (%TEMP%\scopepower_prueba_mosaico)
# para no tocar static\ ni config.ini.
#
# Uso:
#   python herramientas\probar_mosaico.py
#   python herramientas\probar_mosaico.py --filas 3 --columnas 3 --lado 1200 --solape 25

import os
import sys
import glob
import math
import time
import shutil
import argparse
import configparser

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COPIA = os.path.join(os.environ.get("TEMP", RAIZ), "scopepower_prueba_mosaico")


def preparar_copia():
    if os.path.isdir(COPIA):
        shutil.rmtree(COPIA, ignore_errors=True)
    os.makedirs(os.path.join(COPIA, "static"), exist_ok=True)
    os.makedirs(os.path.join(COPIA, "logs"), exist_ok=True)
    for ruta in glob.glob(os.path.join(RAIZ, "*.py")):
        shutil.copy(ruta, COPIA)
    # datos/ no va a git (tablas IERS, MPC, calibraciones): en un clon limpio no existe
    if os.path.isdir(os.path.join(RAIZ, "datos")):
        shutil.copytree(os.path.join(RAIZ, "datos"), os.path.join(COPIA, "datos"), dirs_exist_ok=True)
    os.makedirs(os.path.join(COPIA, "datos"), exist_ok=True)
    for nombre in ("esperando.png", "logo_transparente.png", "logo.png"):
        origen = os.path.join(RAIZ, "static", nombre)
        if os.path.exists(origen):
            shutil.copy(origen, os.path.join(COPIA, "static", nombre))
    cfg = configparser.ConfigParser()
    # Sin config.ini (clon limpio, GitHub Actions) vale la plantilla
    ruta_cfg = os.path.join(RAIZ, "config.ini")
    if not os.path.exists(ruta_cfg):
        ruta_cfg = os.path.join(RAIZ, "config.example.ini")
    cfg.read(ruta_cfg, encoding="utf-8")
    cfg["FTP"]["subir_ftp"] = "false"
    if "Voz" in cfg:
        cfg["Voz"]["activada"] = "false"
    if "Mosaico" not in cfg:
        cfg["Mosaico"] = {}
    cfg["Mosaico"]["lado_maximo"] = "2600"
    with open(os.path.join(COPIA, "config.ini"), "w", encoding="utf-8") as f:
        cfg.write(f)
    with open(os.path.join(COPIA, "static", "info_objeto_goto.txt"), "w", encoding="utf-8") as f:
        f.write("Nombre: Prueba mosaico\nTipo: Nebulosa\n")


def cielo_sintetico(ra_c, dec_c, campo_deg, n_estrellas, semilla=7):
    rng = np.random.default_rng(semilla)
    r = campo_deg * 1.2
    cd = math.cos(math.radians(dec_c))
    ra = ra_c + rng.uniform(-r, r, n_estrellas) / cd
    dec = dec_c + rng.uniform(-r, r, n_estrellas)
    flujo = 10 ** rng.uniform(2.3, 4.3, n_estrellas)
    return {"ra": ra, "dec": dec, "flujo": flujo,
            "nebulosa": {"ra": ra_c + 0.35 / cd, "dec": dec_c - 0.2, "sigma_deg": 0.25, "pico": 900.0}}


def render(wcs, ancho, alto, cielo, fwhm_px=3.0):
    """Imagen (H, W, 3) lineal del cielo sintético vista con ese WCS (sin fondo ni ruido)."""
    import cv2
    img = np.zeros((alto, ancho, 3), dtype=np.float32)
    # Nebulosa: gaussiana en el cielo evaluada en una rejilla gruesa e interpolada
    paso = 16
    ys, xs = np.mgrid[0:alto:paso, 0:ancho:paso]
    pts = np.column_stack([xs.ravel(), ys.ravel()]).astype(np.float64)
    sky = wcs.all_pix2world(pts, 0)
    nb = cielo["nebulosa"]
    d_ra = ((sky[:, 0] - nb["ra"] + 180) % 360 - 180) * math.cos(math.radians(nb["dec"]))
    d_dec = sky[:, 1] - nb["dec"]
    g = nb["pico"] * np.exp(-(d_ra ** 2 + d_dec ** 2) / (2 * nb["sigma_deg"] ** 2))
    g = g.reshape(xs.shape).astype(np.float32)
    g = cv2.resize(g, (ancho, alto), interpolation=cv2.INTER_CUBIC)
    img[..., 0] += g * 1.0
    img[..., 1] += g * 0.6
    img[..., 2] += g * 0.8
    # Estrellas: gaussianas en su píxel
    pix = wcs.all_world2pix(np.column_stack([cielo["ra"], cielo["dec"]]), 0)
    sigma = fwhm_px / 2.3548
    radio = int(4 * sigma) + 1
    yy, xx = np.mgrid[-radio:radio + 1, -radio:radio + 1]
    for (x, y), f in zip(pix, cielo["flujo"]):
        if not (radio < x < ancho - radio - 1 and radio < y < alto - radio - 1):
            continue
        xi, yi = int(round(x)), int(round(y))
        k = f * np.exp(-((xx + xi - x) ** 2 + (yy + yi - y) ** 2) / (2 * sigma ** 2)) / (2 * math.pi * sigma ** 2)
        for c, gan in enumerate((0.9, 1.0, 1.1)):
            img[yi - radio:yi + radio + 1, xi - radio:xi + radio + 1, c] += k * gan
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filas", type=int, default=2)
    ap.add_argument("--columnas", type=int, default=2)
    ap.add_argument("--lado", type=int, default=1000, help="lado de cada tesela en px")
    ap.add_argument("--solape", type=float, default=20.0)
    ap.add_argument("--escala", type=float, default=3.1, help="segundos de arco por píxel")
    args = ap.parse_args()

    preparar_copia()
    os.chdir(COPIA)
    sys.path.insert(0, COPIA)
    import cv2
    from astropy.io import fits
    from astropy.io.fits import PrimaryHDU
    import mosaico

    ra_c, dec_c = 83.8, 22.0
    ancho = alto = args.lado
    escala_deg = args.escala / 3600.0
    campo = {"ancho_px": ancho, "alto_px": alto, "escala_arcsec": args.escala,
             "ancho_deg": ancho * escala_deg, "alto_deg": alto * escala_deg}
    cielo = cielo_sintetico(ra_c, dec_c, campo["ancho_deg"] * max(args.filas, args.columnas), 350)

    # Rejilla de teselas con la cámara a 25°, como la calcularía el trabajo
    wcs_centro = mosaico.wcs_tangente(ra_c, dec_c, mosaico.cd_desde_angulo(escala_deg, 25.0), ancho, alto)
    teselas = mosaico.calcular_teselas(wcs_centro, ancho, alto, args.filas, args.columnas, args.solape)
    tam = mosaico.tamano_mosaico(campo, args.filas, args.columnas, args.solape)
    print(f"Mosaico {args.filas}×{args.columnas}, teselas de {ancho} px ({campo['ancho_deg']:.2f}°), "
          f"solape {args.solape:.0f} %: {tam[0]:.2f}° × {tam[1]:.2f}°")

    rng = np.random.default_rng(3)
    os.makedirs(mosaico.CARPETA, exist_ok=True)
    rutas = []
    for t in teselas:
        # Cada tesela con su giro de campo (altacimutal), un error de apuntado y, la
        # segunda, en espejo (paridad distinta: debe dar igual)
        giro = 25.0 + rng.uniform(-12, 12)
        cd = np.array(mosaico.cd_desde_angulo(escala_deg, giro))
        if t["i"] == 1:
            cd = cd @ np.array([[-1, 0], [0, 1]])
        ra_t = t["ra_h"] * 15.0 + rng.uniform(-0.03, 0.03)
        dec_t = t["dec_deg"] + rng.uniform(-0.03, 0.03)
        wcs = mosaico.wcs_tangente(ra_t, dec_t, cd.tolist(), ancho, alto)
        img = render(wcs, ancho, alto, cielo)
        # Fondo distinto, gradiente y ruido (como un apilado de N fotos)
        fondo = rng.uniform(600, 1100)
        yy, xx = np.mgrid[0:alto, 0:ancho]
        grad = (xx / ancho - 0.5) * rng.uniform(-80, 80) + (yy / alto - 0.5) * rng.uniform(-80, 80)
        img += fondo + grad[..., None]
        img += rng.normal(0, 6.0, img.shape).astype(np.float32)
        # Zona cubierta: un marco de 12 px sin datos, como deja el alineado
        img[:12] = img[-12:] = img[:, :12] = img[:, -12:] = 0
        hdr = fits.Header()
        hdr["NSTACK"] = 20
        hdr["EXPTOT"] = 400.0
        hdr["ZONAX0"], hdr["ZONAY0"], hdr["ZONAW"], hdr["ZONAH"] = 12, 12, ancho - 24, alto - 24
        w = wcs.wcs
        hdr["CRPIX1"], hdr["CRPIX2"] = float(w.crpix[0]), float(w.crpix[1])
        hdr["CRVAL1"], hdr["CRVAL2"] = float(w.crval[0]), float(w.crval[1])
        hdr["CD1_1"], hdr["CD1_2"] = float(cd[0, 0]), float(cd[0, 1])
        hdr["CD2_1"], hdr["CD2_2"] = float(cd[1, 0]), float(cd[1, 1])
        ruta = os.path.join(mosaico.CARPETA, f"tesela_{t['i'] + 1:02d}.fits")
        PrimaryHDU(data=np.ascontiguousarray(img.transpose(2, 0, 1)), header=hdr).writeto(ruta, overwrite=True)
        rutas.append(ruta)
        print(f"  tesela {t['i'] + 1}: fila {t['fila']} col {t['col']}, giro {giro:.1f}°, fondo {fondo:.0f}"
              + (" (en espejo)" if t["i"] == 1 else ""))

    t0 = time.time()
    cubo, hdr, info = mosaico.combinar(rutas, ra_c / 15.0, dec_c, progreso=lambda s: print("   ", s))
    t_comb = time.time() - t0
    print(f"Combinado en {t_comb:.1f} s: {info['ancho']}×{info['alto']} px, reducción ×{info['factor']}, "
          f"residuos de la homografía {info['residuos_px']} px, fondos igualados {info['offsets']}, "
          f"cobertura {info['cobertura_pct']} %, zona {info['zona']}")
    t0 = time.time()
    mosaico.publicar(cubo, hdr, "Prueba", args.filas, args.columnas, progreso=lambda s: print("   ", s))
    print(f"Vista previa en {time.time() - t0:.1f} s: {os.path.abspath(mosaico.RUTA_PREVIEW)}")

    # ── Comprobaciones ──
    fallos = []
    wcs_out = mosaico.wcs_de_cabecera(hdr)
    verde = cubo[1]
    H, W = verde.shape
    # 1) Estrellas: el centroide medido alrededor de donde las pone el WCS del mosaico
    pix = wcs_out.all_world2pix(np.column_stack([cielo["ra"], cielo["dec"]]), 0)
    residuos = []
    r = 4
    for (x, y), f in zip(pix, cielo["flujo"]):
        if f < 1200 or not (r + 2 < x < W - r - 3 and r + 2 < y < H - r - 3):
            continue
        xi, yi = int(round(x)), int(round(y))
        rec = verde[yi - r:yi + r + 1, xi - r:xi + r + 1].astype(np.float64)
        if rec.max() <= 0:
            continue
        rec = rec - np.median(verde[max(0, yi - 20):yi + 21, max(0, xi - 20):xi + 21])
        rec = np.clip(rec, 0, None)
        if rec.sum() < 1:
            continue
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        cx = (rec * xx).sum() / rec.sum() + xi
        cy = (rec * yy).sum() / rec.sum() + yi
        residuos.append(math.hypot(cx - x, cy - y))
    residuos = np.array(residuos)
    print(f"Estrellas medidas: {residuos.size}; residuo mediano {np.median(residuos):.2f} px, máximo {residuos.max():.2f} px")
    if residuos.size < 10 or np.median(residuos) > 0.3 or np.percentile(residuos, 90) > 1.0:
        fallos.append("las estrellas no caen donde dice el WCS del mosaico")
    # 2) Costuras: el mosaico frente al cielo de verdad proyectado en el mismo marco (sin
    #    fondo ni ruido): la diferencia, quitado su nivel, no debe tener escalones
    verdad = render(wcs_out, W, H, cielo)
    cuenta = np.zeros((H, W), dtype=bool)
    cuenta[verde > 0] = True
    dif = (verde - verdad[..., 1])
    dif[~cuenta] = np.nan
    # El mosaico conserva un gradiente medio (los planos de fondo se reparten con media
    # cero y el gradiente global lo quita después el procesado): se resta el mejor plano
    yy, xx = np.mgrid[0:H, 0:W]
    sel = cuenta & np.isfinite(dif)
    idx = np.flatnonzero(sel.ravel())[::97]
    A = np.column_stack([xx.ravel()[idx] / W, yy.ravel()[idx] / H, np.ones(idx.size)])
    coef = np.linalg.lstsq(A, dif.ravel()[idx], rcond=None)[0]
    dif = dif - (coef[0] * xx / W + coef[1] * yy / H + coef[2]).astype(np.float32)
    verde_plano = verde - (coef[0] * xx / W + coef[1] * yy / H + coef[2]).astype(np.float32)
    # Por bloques de 64 px: nivel de fondo medio; los escalones entre bloques vecinos delatan costuras
    b = 64
    bloques = np.nanmedian(dif[:H // b * b, :W // b * b].reshape(H // b, b, W // b, b), axis=(1, 3))
    salto_x = np.nanmax(np.abs(np.diff(bloques, axis=1)))
    salto_y = np.nanmax(np.abs(np.diff(bloques, axis=0)))
    ruido = float(np.nanstd(dif[cuenta]))
    print(f"Fondo del mosaico frente al cielo real: ruido {ruido:.1f} ADU, mayor escalón entre bloques "
          f"{max(salto_x, salto_y):.1f} ADU (el ruido de una tesela era 6 ADU por píxel)")
    if max(salto_x, salto_y) > 12.0:
        fallos.append("quedan escalones de fondo en las costuras")
    # 3) La nebulosa sale continua: correlación alta entre el mosaico suavizado y la verdad
    s_m = cv2.GaussianBlur(np.nan_to_num(verde_plano), (0, 0), 6)
    s_v = cv2.GaussianBlur(verdad[..., 1], (0, 0), 6)
    m = cuenta & (s_v > 50)
    corr = np.corrcoef(s_m[m], s_v[m])[0, 1]
    print(f"Nebulosa: correlación mosaico/verdad {corr:.4f}")
    if corr < 0.95:
        fallos.append("la nebulosa no sale continua")
    cv2.imwrite(os.path.join(COPIA, "static", "verdad.png"), np.clip(s_v / s_v.max() * 255, 0, 255).astype(np.uint8))
    if fallos:
        print("❌ FALLOS: " + "; ".join(fallos))
        sys.exit(1)
    print("✅ Mosaico correcto")


if __name__ == "__main__":
    main()
