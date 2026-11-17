# herramientas/imagenes_guia.py
#
# Prepara el fondo de una tarjeta de la guía «¿Qué quieres hacer hoy?» (static/guia/<clave>.jpg)
# a partir de una foto de la galería: la recorta a la proporción de la tarjeta, la reduce y la
# comprime hasta que pesa poco (la tablet la pide por la wifi de la montura). El resultado no
# lleva metadatos (ni EXIF ni ubicación). La firma de la galería va abajo a la derecha: el
# recorte se elige para que no entre.
#
# Uso (desde la carpeta de ScopePower):
#   python herramientas\imagenes_guia.py sol static\fotos\Sol_20261010_175258.jpg --centro auto --zoom 2.5
#   python herramientas\imagenes_guia.py luna static\fotos\Luna_x.jpg --centro 0.5,0.45
#   python herramientas\imagenes_guia.py cielo_profundo static\fotos\Andromeda_x.jpg --centro 0.52,0.49 --giro 17 --zoom 1.4
#   python herramientas\imagenes_guia.py galeria foto1.jpg foto2.jpg ... foto6.jpg      (varias fotos: mosaico de miniaturas)
#   python herramientas\imagenes_guia.py calibracion calibraciones\flat.fits            (un FITS: se estira para que se vea)
#
#   --centro x,y   centro del recorte, en fracciones de la foto (0,0 arriba a la izquierda); «auto» busca lo más brillante
#   --zoom n       cuánto se acerca: 1 = todo el ancho de la foto
#   --giro grados  gira la foto alrededor del centro, en sentido antihorario (para tumbar una galaxia a lo largo
#                  de la tarjeta); las esquinas que quedan sin foto se rellenan reflejando el cielo
#   --mueve dx,dy  desplaza el recorte respecto al centro, en fracciones de la foto: con --mueve=-0.05,0 lo que
#                  está en el centro (el núcleo de la galaxia) queda un poco a la derecha, fuera del texto
#   --tamano AxB   tamaño de salida (640x400 las tarjetas; 960x360 la franja del inicio)
#   --peso kB      peso máximo; baja la calidad del JPG hasta cumplirlo

import os
import sys
import argparse

import cv2
import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CARPETA = os.path.join(RAIZ, "static", "guia")
FRANJA_FIRMA = 0.15      # parte de abajo de la foto que «auto» no mira (ahí va la firma)


def leer(ruta):
    """La foto en BGR de 8 bits. Un FITS (dark o flat en bruto) se estira entre percentiles."""
    if ruta.lower().endswith((".fits", ".fit")):
        from astropy.io import fits
        datos = np.asarray(fits.getdata(ruta), dtype=np.float32)
        if datos.ndim == 3:
            datos = datos.mean(axis=0)
        bajo, alto = np.percentile(datos, (0.5, 99.8))
        gris = np.clip((datos - bajo) / max(alto - bajo, 1e-6), 0, 1)
        gris = cv2.resize(gris, (gris.shape[1] // 2, gris.shape[0] // 2), interpolation=cv2.INTER_AREA)   # funde el mosaico Bayer
        return cv2.cvtColor((gris * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    img = cv2.imdecode(np.fromfile(ruta, dtype=np.uint8), cv2.IMREAD_COLOR)     # admite rutas con tildes
    if img is None:
        sys.exit("No se pudo leer " + ruta)
    return img


def centro_auto(img):
    """Centro de lo más brillante de la foto (el disco del Sol, la estrella), sin mirar la franja de la firma."""
    gris = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (0, 0), max(2, img.shape[1] / 400))
    gris = gris[:int(gris.shape[0] * (1 - FRANJA_FIRMA))]
    ys, xs = np.nonzero(gris >= 0.6 * gris.max())
    return float(xs.mean()) / img.shape[1], float(ys.mean()) / img.shape[0]


def girar(img, centro, grados):
    """La foto girada `grados` en sentido antihorario alrededor de `centro` (fracciones de la foto)."""
    h, w = img.shape[:2]
    matriz = cv2.getRotationMatrix2D((centro[0] * w, centro[1] * h), grados, 1.0)
    return cv2.warpAffine(img, matriz, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def recortar(img, centro, zoom, ancho, alto):
    h, w = img.shape[:2]
    rw = min(w, w / max(zoom, 0.01))
    rh = rw * alto / ancho
    if rh > h:
        rh, rw = h, h * ancho / alto
    x0 = min(max(centro[0] * w - rw / 2, 0), w - rw)
    y0 = min(max(centro[1] * h - rh / 2, 0), h - rh)
    trozo = img[int(y0):int(y0 + rh), int(x0):int(x0 + rw)]
    return cv2.resize(trozo, (ancho, alto), interpolation=cv2.INTER_AREA if rw >= ancho else cv2.INTER_CUBIC)


def mosaico(fotos, ancho, alto):
    """Varias fotos en una rejilla de miniaturas (la tarjeta de la galería)."""
    columnas = 3 if len(fotos) >= 3 else len(fotos)
    filas = int(np.ceil(len(fotos) / columnas))
    cw, ch = ancho // columnas, alto // filas
    lienzo = np.zeros((alto, ancho, 3), np.uint8)
    for i, ruta in enumerate(fotos):
        celda = recortar(leer(ruta), (0.5, 0.45), 1.3, cw, ch)
        f, c = divmod(i, columnas)
        lienzo[f * ch:(f + 1) * ch, c * cw:(c + 1) * cw] = celda
    return lienzo


def guardar(img, ruta, peso_kb):
    for calidad in range(84, 38, -4):
        ok, datos = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, calidad, cv2.IMWRITE_JPEG_PROGRESSIVE, 1])
        if ok and len(datos) <= peso_kb * 1024:
            break
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    datos.tofile(ruta)
    return len(datos) / 1024.0, calidad


def main():
    p = argparse.ArgumentParser(description="Prepara un fondo de la guía a partir de una foto de la galería.")
    p.add_argument("clave", help="actividad: cielo_profundo, luna, sol, paisaje, enfocar, calibracion, galeria, inicio")
    p.add_argument("fotos", nargs="+", help="foto de origen (varias: mosaico de miniaturas)")
    p.add_argument("--centro", default="0.5,0.5")
    p.add_argument("--zoom", type=float, default=1.0)
    p.add_argument("--giro", type=float, default=0.0)
    p.add_argument("--mueve", default="0,0")
    p.add_argument("--tamano", default="640x400")
    p.add_argument("--peso", type=float, default=40.0)
    a = p.parse_args()
    ancho, alto = (int(v) for v in a.tamano.lower().split("x"))
    if len(a.fotos) > 1:
        img = mosaico(a.fotos, ancho, alto)
    else:
        origen = leer(a.fotos[0])
        centro = centro_auto(origen) if a.centro == "auto" else tuple(float(v) for v in a.centro.split(","))
        if a.giro:
            origen = girar(origen, centro, a.giro)
        dx, dy = (float(v) for v in a.mueve.split(","))
        centro = (centro[0] + dx, centro[1] + dy)
        img = recortar(origen, centro, a.zoom, ancho, alto)
    destino = os.path.join(CARPETA, a.clave + ".jpg")
    kb, calidad = guardar(img, destino, a.peso)
    print("%s: %dx%d, %.0f kB (calidad %d)" % (os.path.relpath(destino, RAIZ), ancho, alto, kb, calidad))


if __name__ == "__main__":
    main()
