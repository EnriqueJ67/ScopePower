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
#      https://svs.gsfc.nasa.gov/4720 → lroc_color_poles_2k.tif (2048×1024, cilíndrico,
#      longitud 0 en el centro). Se guarda en grises: solo sirve para reconocer los mares.
#
#   3. Contornos de los mares: el mapa de los límites de los mares del LROC (Nelson et al. 2014,
#      datos de la NASA en el PDS), https://data.lroc.im-ldi.com/lroc/view_rdr/SHAPEFILE_LROC_GLOBAL_MARE
#      → LROC_GLOBAL_MARE_180.ZIP. De sus 450 000 puntos se quedan unos pocos miles: solo la
#      cara visible y su borde, sin las manchas de menos de MIN_ANILLO_GRADOS y simplificados a
#      TOLERANCIA_GRADOS (unos 3 km), que en la foto de la Luna entera es un píxel o menos.
#
#   python herramientas/preparar_luna.py MOON_nomenclature_center_pts.zip lroc_color_poles_2k.tif LROC_GLOBAL_MARE_180.ZIP

import os
import io
import sys
import csv
import json
import math
import struct
import zipfile

import cv2
import numpy as np

CARPETA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "luna")
MIN_CRATER_KM = 15.0
MAX_DISTANCIA_CENTRO = 100.0       # grados desde el centro de la cara visible (libración incluida)
SIN_USAR = {"SF", "LF"}            # cráteres satélite (Tycho A...) y nombres de los alunizajes del Apolo
TOLERANCIA_GRADOS = 0.1            # simplificación de los contornos de los mares (~3 km)
MIN_ANILLO_GRADOS = 1.0            # contornos más pequeños (islas, manchas de ~30 km) no se guardan


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
    if img is None or img.shape[1] != 2 * img.shape[0]:
        sys.exit(f"Se esperaba un mapa cilíndrico (el doble de ancho que de alto): {ruta_jpg}")
    ruta = os.path.join(CARPETA, "albedo_luna.jpg")
    cv2.imwrite(ruta, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"Mapa de albedo en {ruta} ({os.path.getsize(ruta) // 1024} KB)")


def anillos_shp(datos):
    """Anillos [(lon, lat)...] de un .shp de polígonos (tipo 5), leído a mano."""
    if struct.unpack("<i", datos[32:36])[0] != 5:
        sys.exit("Se esperaba un shapefile de polígonos")
    anillos, p = [], 100
    while p < len(datos):
        __, largo = struct.unpack(">2i", datos[p:p + 8])
        reg = datos[p + 8:p + 8 + 2 * largo]
        p += 8 + 2 * largo
        if struct.unpack("<i", reg[:4])[0] != 5:
            continue
        n_partes, n_puntos = struct.unpack("<2i", reg[36:44])
        partes = list(struct.unpack(f"<{n_partes}i", reg[44:44 + 4 * n_partes])) + [n_puntos]
        puntos = np.frombuffer(reg[44 + 4 * n_partes:44 + 4 * n_partes + 16 * n_puntos], dtype="<f8").reshape(-1, 2)
        anillos += [puntos[partes[i]:partes[i + 1]] for i in range(n_partes)]
    return anillos


def preparar_mares(ruta_zip):
    with zipfile.ZipFile(ruta_zip) as z:
        nombre_shp = next(n for n in z.namelist() if n.lower().endswith(".shp"))
        anillos = anillos_shp(z.read(nombre_shp))
    salida, puntos = [], 0
    for a in anillos:
        lon, lat = a[:, 0], a[:, 1]
        if np.ptp(lon) > 180:                     # cruza la longitud ±180: es de la cara oculta
            continue
        if max(np.ptp(lon) * math.cos(math.radians(float(np.mean(lat)))), np.ptp(lat)) < MIN_ANILLO_GRADOS:
            continue
        if min(distancia_al_centro(float(la), float(lo)) for lo, la in a[::max(1, len(a) // 50)]) > MAX_DISTANCIA_CENTRO:
            continue
        # Douglas-Peucker en grados (la longitud encogida por la latitud, como se ve)
        escala = math.cos(math.radians(float(np.mean(lat))))
        curva = np.column_stack([lon * escala, lat]).astype(np.float32).reshape(-1, 1, 2)
        simple = cv2.approxPolyDP(curva, TOLERANCIA_GRADOS, True).reshape(-1, 2)
        if len(simple) < 4:
            continue
        salida.append([[round(float(la), 2), round(float(lo) / escala, 2)] for lo, la in simple])
        puntos += len(simple)
    ruta = os.path.join(CARPETA, "contornos_mares.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(salida, f, separators=(",", ":"))
    print(f"{len(salida)} contornos de mares ({puntos} puntos) en {ruta} ({os.path.getsize(ruta) // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("Uso: preparar_luna.py <nomenclatura.zip> <mapa.jpg> <mares.zip>")
    os.makedirs(CARPETA, exist_ok=True)
    preparar_nombres(sys.argv[1])
    preparar_albedo(sys.argv[2])
    preparar_mares(sys.argv[3])
