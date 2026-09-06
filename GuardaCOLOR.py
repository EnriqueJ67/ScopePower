import os
from astropy.io import fits
import numpy as np
import cv2
from AutoStrechSTF import apply_stf_color, debayer_rgb

# Rutas
fits_path = 'static/captura.fits'
out_jpg   = 'static/captura_stretched_fixed.jpg'

# 1) Leer FITS CFA, demosaicar y montar cubo (3,H,W)…
with fits.open(fits_path) as hdul:
    cfa = hdul[0].data.astype(np.uint16)
    hdr = hdul[0].header

pattern = hdr.get('BAYERPAT','RGGB')
# debayer_rgb aplica el codigo OpenCV correcto (RGGB -> COLOR_BayerBG2RGB)
rgb = debayer_rgb(cfa, pattern)
cube = np.moveaxis(rgb, -1, 0).astype(np.float32)

# 2) Normalización simple (opcional, o deja que lo haga apply_stf_color)
cube_norm = cube / np.max(cube)

# 3) Aplicar el STF de AutoStrechSTF
stf = apply_stf_color(cube_norm)

print("apply_stf_color devolvió:", type(stf), "dtype:", stf.dtype,
      "min,max:", stf.min(), stf.max())

# 4) Prepara el JPEG sin volver a multiplicar si ya es uint8
if stf.dtype == np.uint8 and stf.max() <= 255:
    jpeg_rgb = stf
else:
    # si fuese float 0–1, entonces sí escalamos:
    jpeg_rgb = (np.clip(stf, 0, 1) * 255).astype(np.uint8)

# 5) OpenCV quiere BGR
jpeg_bgr = cv2.cvtColor(jpeg_rgb, cv2.COLOR_RGB2BGR)

os.makedirs(os.path.dirname(out_jpg), exist_ok=True)
if not cv2.imwrite(out_jpg, jpeg_bgr):
    raise IOError(f"No pude guardar {out_jpg}")

print("✅ JPEG corregido guardado en", out_jpg)
