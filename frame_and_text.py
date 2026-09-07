#!/usr/bin/env python3
"""
Script de prueba para añadir un marco fino blanco interior, un área de texto simulada por un relleno negro y un marco blanco exterior.
Estrategia:
 1) Dibujar el marco fino blanco interior.
 2) Pintar de negro todo lo que esté fuera de ese marco (bandera de texto).
 3) Dibujar el marco blanco exterior, dejando la zona negra como el «marco negro».
Colocar luego el texto centrado en dicha zona negra.
"""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import os
import sys

def add_frame_and_text(image_bgr, text,
                       inner_w_ratio=0.005,  # grosor marco blanco interior
                       band_ratio=0.03,     # grosor zona de texto (marco negro)
                       border_ratio=0.005,   # grosor marco blanco exterior
                       margin_ratio=0.005    # margen negro exterior
                       ):
    # Convertir BGR a PIL
    img = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_orig = Image.fromarray(img)
    w0, h0 = pil_orig.size
    min_side0 = min(w0, h0)
    # pixeles originales
    inner_w = int(inner_w_ratio * min_side0)
    band = int(band_ratio * min_side0)
    border_w = int(border_ratio * min_side0)
    margin = int(margin_ratio * min_side0)

    # Crear lienzo con margen negro exterior
    new_w = w0 + 2 * margin
    new_h = h0 + 2 * margin
    pil = Image.new('RGB', (new_w, new_h), (0, 0, 0))
    # Pegar la imagen original centrada con margen
    pil.paste(pil_orig, (margin, margin))

    w, h = new_w, new_h
    draw = ImageDraw.Draw(pil)

    # Calculamos distancias para marcos internos
    inset_inner = margin + border_w + band
    # 1) Marco fino blanco interior
    rect_inner = [inset_inner, inset_inner, w - inset_inner, h - inset_inner]
    draw.rounded_rectangle(rect_inner, radius=inner_w*2, outline=(255,255,255), width=inner_w)

    # 2) Pintar de negro todo fuera de rect_inner (bandera negra)
    mask = Image.new('L', (w, h), 255)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle(rect_inner, radius=inner_w*2, fill=0)
    pil.paste((0,0,0), mask=mask)

    # 3) Marco blanco exterior
    rect_outer = [margin, margin, w - margin, h - margin]
    draw.rounded_rectangle(rect_outer, radius=border_w*2, outline=(255,255,255), width=border_w)

        # --- Texto en la banda negra ---
    # Cargar fuente TrueType con tamaño legible
    try:
        font_size = max(14, int(band * 0.56))  # aprox mitad del grosor de banda
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()
    # Medir texto con la fuente seleccionada
    bbox = draw.textbbox((0,0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

        # Centrar horizontalmente en la banda
    x = (w - text_w) // 2
        # Centrar verticalmente el texto dentro de la banda negra simulada en la parte inferior de la imagen
    # coordenadas de la franja inferior:
    band_top = h - margin - border_w - band
    # centrar verticalmente dentro de esa franja (mejor centrado)
    # en vez de usar (band - text_h)//2, podemos conservar un pequeño offset superior e inferior equilibrado
    y = band_top + max( (band - text_h) // 2, int(band * 0.1) )
    draw.text((x, y), text, fill=(255,255,255), font=font)

    # Convertir de vuelta a BGR y devolver
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

# Configuración: rutas y texto
INPUT_JPG  = 'input.jpg'
OUTPUT_JPG = 'output.jpg'
TEXT       = 'ScopePower 2025 © TestObject 5 imágenes, 150.0s'

if __name__ == '__main__':
    if not os.path.isfile(INPUT_JPG):
        print(f"Error: no se encuentra el archivo {INPUT_JPG}", file=sys.stderr)
        sys.exit(1)
    img = cv2.imread(INPUT_JPG)
    if img is None:
        print(f"Error: no se pudo leer la imagen {INPUT_JPG}", file=sys.stderr)
        sys.exit(1)
    out = add_frame_and_text(img, TEXT)
    cv2.imwrite(OUTPUT_JPG, out, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"Imagen procesada guardada en {OUTPUT_JPG}")
