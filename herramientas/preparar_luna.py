# herramientas/preparar_luna.py
#
# Prepara los datos de la Luna anotada (luna_anotada.py) a partir de las fuentes
# públicas. Solo hace falta volver a pasarlo si la UAI añade nombres o se cambia el mapa;
# los resultados van en git (carpeta luna/) y la app no necesita Internet.
#
#   1. Nombres: el nomenclátor de la UAI que publica el USGS (dominio público), en
#      https://planetarynames.wr.usgs.gov/GIS_Downloads → MOON_nomenclature_center_pts.zip
#      De sus ~9 000 nombres se quedan los de la cara visible y su borde (hasta 100° del
#      centro, por la libración): sin las letras de los cráteres satélite («Tycho A») ni
#      los rincones del Apolo, y los cráteres desde MIN_CRATER_KM.
#   2. Mapa de albedo: el de color del LROC del CGI Moon Kit de la NASA (dominio público),
#      https://svs.gsfc.nasa.gov/4720 → lroc_color_poles_1k.jpg (1024×512, cilíndrico,
#      longitud 0 en el centro). Se guarda en grises: solo sirve para reconocer los mares.
#
#   python herramientas/preparar_luna.py MOON_nomenclature_center_pts.zip lroc_color_poles_1k.jpg

import os
import io
import sys
import csv
import math
import struct
import zipfile

import cv2

CARPETA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "luna")
MIN_CRATER_KM = 15.0
MAX_DISTANCIA_CENTRO = 100.0       # grados desde el centro de la cara visible (libración incluida)
SIN_USAR = {"SF", "LF"}            # cráteres satélite (Tycho A...) y nombres de los alunizajes del Apolo


def leer_dbf(datos):
    """Filas (dict) de un .dbf de shapefile, leído a mano: no hace falta pyshp."""
    f = io.BytesIO(datos)
    n, lcab, lreg = struct.unpack("<IHH", f.read(32)[4:12])
    campos = []
    while True:
        d = f.read(32)
        if d[0] == 0x0D:
            break
        campos.append((d[:11].split(b"\0")[0].decode(), d[16]))
    f.seek(lcab)
    filas = []
    for __ in range(n):
        r = f.read(lreg)
        if r[:1] == b"*":                  # registro borrado
            continue
        p, fila = 1, {}
        for nombre, largo in campos:
            fila[nombre] = r[p:p + largo].decode("utf-8", "replace").strip()
            p += largo
        filas.append(fila)
    return filas


def distancia_al_centro(lat, lon):
    return math.degrees(math.acos(max(-1.0, min(1.0, math.cos(math.radians(lat)) * math.cos(math.radians(lon))))))


def preparar_nombres(ruta_zip):
    with zipfile.ZipFile(ruta_zip) as z:
        nombre_dbf = next(n for n in z.namelist() if n.lower().endswith(".dbf"))
        filas = leer_dbf(z.read(nombre_dbf))
    salida = []
    for x in filas:
        codigo = x["code"]
        if codigo in SIN_USAR or x["approval"] != "Adopted by IAU":
            continue
        lat = float(x["center_lat"])
        lon = (float(x["center_lon"]) + 180.0) % 360.0 - 180.0
        diametro = float(x["diameter"] or 0.0)
        if codigo == "AA" and diametro < MIN_CRATER_KM:
            continue
        if distancia_al_centro(lat, lon) - math.degrees(diametro / 2.0 / 1737.4) > MAX_DISTANCIA_CENTRO:
            continue
        salida.append((x["clean_name"], codigo, round(lat, 3), round(lon, 3), round(diametro, 1)))
    salida.sort(key=lambda s: (-s[4], s[0]))
    ruta = os.path.join(CARPETA, "accidentes_luna.csv")
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nombre", "codigo", "lat", "lon", "diametro_km"])
        w.writerows(salida)
    print(f"{len(salida)} nombres en {ruta}")


def preparar_albedo(ruta_jpg):
    img = cv2.imread(ruta_jpg, cv2.IMREAD_GRAYSCALE)
    if img is None or img.shape != (512, 1024):
        sys.exit(f"Se esperaba un mapa de 1024×512: {ruta_jpg}")
    ruta = os.path.join(CARPETA, "albedo_luna.jpg")
    cv2.imwrite(ruta, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"Mapa de albedo en {ruta} ({os.path.getsize(ruta) // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__ or "Uso: preparar_luna.py <nomenclatura.zip> <mapa.jpg>")
    os.makedirs(CARPETA, exist_ok=True)
    preparar_nombres(sys.argv[1])
    preparar_albedo(sys.argv[2])
