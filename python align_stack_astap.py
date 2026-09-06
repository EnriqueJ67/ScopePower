#!/usr/bin/env python3
# align_with_astap.py

import os
import subprocess
import configparser
import cv2
import numpy as np
from astropy.io import fits
from astropy.io.fits import Header, PrimaryHDU
from astropy.wcs import WCS
from math import atan2, degrees
from AutoStrechSTF import apply_stf_color

# ————————————— Configuración —————————————
INPUT_FOLDER = r"C:\ScopePower\capturas"
OUTPUT_PATH  = r"C:\ScopePower\stacked_astap.png"
CONFIG_INI   = "config.ini"

cfg = configparser.ConfigParser()
cfg.read(CONFIG_INI)
RUTA_ASTAP = cfg["General"]["ruta_astap"]

# ————————————— Helpers —————————————

def generar_fits_monocromo_para_astap(ruta_rgb, ruta_mono):
    """Convierte FITS color a monocromo uint16 manteniendo cabecera."""
    with fits.open(ruta_rgb) as hdul:
        orig = hdul[0]
        data = orig.data.astype(np.float32)

    if data.ndim == 3 and data.shape[0] == 3:
        gris = data.mean(axis=0)
    else:
        gris = data

    gris = np.clip(gris, 0, 65535).astype(np.uint16)
    hdr = orig.header.copy()
    PrimaryHDU(gris, header=hdr).writeto(ruta_mono, overwrite=True)

def parse_ini_wcs(ruta_ini):
    """
    Lee CRPIX1/2, CRVAL1/2 y CD1_1..CD2_2 de .ini de ASTAP
    Devuelve dict con:
      - wcs: objeto WCS
      - crpix: (crpix1-1, crpix2-1)
      - angle: rotación real en grados
    """
    crpix1 = crpix2 = crval1 = crval2 = None
    cd11 = cd12 = cd21 = cd22 = None

    with open(ruta_ini) as f:
        for L in f:
            if   L.startswith("CRPIX1="): crpix1 = float(L.split("=",1)[1])
            elif L.startswith("CRPIX2="): crpix2 = float(L.split("=",1)[1])
            elif L.startswith("CRVAL1="): crval1 = float(L.split("=",1)[1])
            elif L.startswith("CRVAL2="): crval2 = float(L.split("=",1)[1])
            elif L.startswith("CD1_1="):  cd11   = float(L.split("=",1)[1])
            elif L.startswith("CD1_2="):  cd12   = float(L.split("=",1)[1])
            elif L.startswith("CD2_1="):  cd21   = float(L.split("=",1)[1])
            elif L.startswith("CD2_2="):  cd22   = float(L.split("=",1)[1])

    hdr = Header()
    hdr["CRPIX1"], hdr["CRPIX2"] = crpix1, crpix2
    hdr["CRVAL1"], hdr["CRVAL2"] = crval1, crval2
    hdr["CD1_1"],  hdr["CD1_2"]  = cd11, cd12
    hdr["CD2_1"],  hdr["CD2_2"]  = cd21, cd22
    hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN","DEC--TAN"
    hdr["CUNIT1"], hdr["CUNIT2"] = "deg","deg"

    wcs = WCS(hdr)
    angle = degrees(atan2(cd12, cd11))
    return {"wcs": wcs, "crpix": (crpix1-1, crpix2-1), "angle": angle}

def compute_astap_transform(ruta_fits, ref_info):
    """
    Llama a ASTAP, parsea WCS .ini y construye M que deshace
    rotación+traslación para volver al ref_info.
    """
    mono = ruta_fits.replace(".fits","_mono.fits")
    ini  = mono.replace(".fits",".ini")

    generar_fits_monocromo_para_astap(ruta_fits, mono)
    subprocess.run(f'"{RUTA_ASTAP}" -f "{mono}" -solve -r 50',
                   shell=True, capture_output=True, text=True)
    if not os.path.exists(ini):
        print(f"❌ ASTAP no generó INI para {ruta_fits}")
        return None, "ASTAP"

    now = parse_ini_wcs(ini)
    cx, cy = ref_info["crpix"]

    # world de la referencia
    ra, dec = ref_info["wcs"].all_pix2world([[cx,cy]],0)[0]
    # pix en el frame actual
    x_new, y_new = now["wcs"].all_world2pix([[ra,dec]],0)[0]

    dx = cx - x_new
    dy = cy - y_new

    # **invierto signo** para deshacer rotación
    rot = now["angle"] - ref_info["angle"]

    # Construyo M forward (src→dst)
    M = cv2.getRotationMatrix2D((cx, cy), rot, 1.0)
    M[0,2] += dx
    M[1,2] += dy

    print(f"    dx={dx:.1f}, dy={dy:.1f}, rot={rot:.3f}°")
    return M, "ASTAP"

def load_fits_as_float(path):
    """Carga FITS y normaliza float 0–1."""
    data = fits.getdata(path).astype(np.float32)
    img  = data.mean(axis=0) if data.ndim==3 else data
    img  = np.nan_to_num(img)
    mn, mx = img.min(), img.max()
    return (img-mn)/(mx-mn) if mx>mn else img*0

# ————————————— Pipeline principal —————————————

def align_and_stack_astap(folder):
    fits_files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".fits"))
    if not fits_files:
        raise RuntimeError("No hay FITS en la carpeta.")

    # 1ª imagen como referencia
    ref_f = os.path.join(folder, fits_files[0])
    mono_ref = ref_f.replace(".fits","_mono.fits")
    generar_fits_monocromo_para_astap(ref_f, mono_ref)
    subprocess.run(f'"{RUTA_ASTAP}" -f "{mono_ref}" -solve -r 50',
                   shell=True, capture_output=True, text=True)
    ini_ref  = mono_ref.replace(".fits",".ini")
    ref_info = parse_ini_wcs(ini_ref)

    aligned = []
    for fn in fits_files:
        ruta = os.path.join(folder, fn)
        print("→ Allineando", fn)
        img = load_fits_as_float(ruta)
        M, tag = compute_astap_transform(ruta, ref_info)
        if M is None:
            print("   ⚠️ Descarta", fn)
            continue
        warped = cv2.warpAffine(
            img,
            M,
            (img.shape[1], img.shape[0]),
            flags=cv2.INTER_LINEAR
        )
        aligned.append(warped)

    if not aligned:
        raise RuntimeError("Ninguna imagen alineada.")

    stack = np.nanmean(np.stack(aligned,0), 0)
    return apply_stf_color(stack, clipping=0.001, target_median=0.25, output_8bit=True)

# ————————————— Ejecución —————————————

if __name__ == "__main__":
    out = align_and_stack_astap(INPUT_FOLDER)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    cv2.imwrite(OUTPUT_PATH, out)
    print("✅ Guardado en:", OUTPUT_PATH)
