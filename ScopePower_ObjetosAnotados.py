from AutoStrechSTF import apply_stf_color
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astroquery.simbad import Simbad
import astropy.units as u
import configparser
import cv2
import math
import numpy as np
import os
import re
import subprocess
from astropy.utils.masked import Masked


config = configparser.ConfigParser()
config.read("config.ini")
RUTA_ASTAP = config["General"]["RUTA_ASTAP"]


def extraer_coordenadas_ini(ruta_ini):
    if not os.path.exists(ruta_ini):
        return None

    ra_resuelto = None
    dec_resuelto = None
    angulo_camara = None
    cdelt1 = None
    cdelt2 = None
    solucionado = False

    with open(ruta_ini, 'r') as file:
        for linea in file:
            if "PLTSOLVD=T" in linea:
                solucionado = True
            elif linea.strip().startswith("CRVAL1="):
                match = re.search(r"[-+]?\d*\.\d+E[-+]?\d+|\d+\.\d+", linea)
                if match:
                    ra_resuelto = float(match.group())
            elif linea.strip().startswith("CRVAL2="):
                match = re.search(r"[-+]?\d*\.\d+E[-+]?\d+|\d+\.\d+", linea)
                if match:
                    dec_resuelto = float(match.group())
            elif linea.strip().startswith("CROTA2="):
                match = re.search(r"[-+]?\d*\.\d+E[-+]?\d+|\d+\.\d+", linea)
                if match:
                    angulo_camara = float(match.group())
            elif linea.strip().startswith("CDELT1="):
                match = re.search(r"[-+]?\d*\.\d+E[-+]?\d+|\d+\.\d+", linea)
                if match:
                    cdelt1 = abs(float(match.group()))
            elif linea.strip().startswith("CDELT2="):
                match = re.search(r"[-+]?\d*\.\d+E[-+]?\d+|\d+\.\d+", linea)
                if match:
                    cdelt2 = abs(float(match.group()))

    if not solucionado or ra_resuelto is None or dec_resuelto is None:
        return None  # No se resolvió o falta información

    tamano_pixel = None
    if cdelt1 is not None and cdelt2 is not None:
        tamano_pixel = (cdelt1 + cdelt2) / 2

    return ra_resuelto, dec_resuelto, angulo_camara, tamano_pixel


def generar_fits_monocromo_para_platesolve(ruta_rgb, ruta_monocromo):
    with fits.open(ruta_rgb) as hdul:
        original = hdul[0]
        data = original.data.astype(np.float32)

        if data.ndim == 3 and data.shape[0] == 3:
            gris = np.mean(data, axis=0)
        else:
            gris = data

        gris = np.clip(gris, 0, 65535).astype(np.uint16)

        # Copiar cabecera
        header = original.header.copy()

        fits.PrimaryHDU(gris, header=header).writeto(ruta_monocromo, overwrite=True)

from astropy.utils.masked import Masked
import numpy as np
import math

def limpiar_numero(valor):
    if valor is None:
        return None
    if isinstance(valor, (Masked, np.ma.MaskedArray)):
        return None
    try:
        resultado = float(valor)
        if math.isnan(resultado):
            return None
        return resultado
    except:
        return None


def obtener_objetos_simbad(ruta_ini, ruta_fits, mag_max=12.0):
    import os
    import csv
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    from astroquery.simbad import Simbad
    from astropy.io import fits
    from datetime import datetime

    # 0️⃣ Si ya existe el CSV cacheado, lo cargamos y devolvemos
    cache_path = 'objetos_filtrados.csv'
    if os.path.exists(cache_path):
        objetos = []
        with open(cache_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                objetos.append({
                    'nombre': row['nombre'],
                    'ra': float(row['ra']),
                    'dec': float(row['dec']),
                    'tamano_arcsec': float(row['tamano_arcsec']),
                    'tamano_menor_arcsec': float(row['tamano_menor_arcsec']) if row['tamano_menor_arcsec'] else None,
                    'angulo_objeto': float(row['angulo_objeto']) if row['angulo_objeto'] else None,
                    'mag': float(row['mag']) if row['mag'] else None
                })
        print(f"♻️ Cargando {len(objetos)} objetos desde '{cache_path}'.")
        return objetos

    # 1️⃣ Extraer coordenadas del INI
    coordenadas = extraer_coordenadas_ini(ruta_ini)
    if coordenadas is None:
        print("❌ No se pudieron extraer coordenadas del archivo INI.")
        return None

    ra_centro, dec_centro, _, tamano_pixel_grados = coordenadas
    if tamano_pixel_grados is None:
        print("❌ No se pudo obtener el tamaño de píxel.")
        return None

    # 2️⃣ Tamaño de la imagen y cálculo del radio de búsqueda
    with fits.open(ruta_fits) as hdul:
        data = hdul[0].data
        altura, anchura = data.shape[-2:]
    campo_ancho_grados = anchura * tamano_pixel_grados
    campo_alto_grados = altura * tamano_pixel_grados
    radio_arcmin = (max(campo_ancho_grados, campo_alto_grados) / 2) * 60

    centro = SkyCoord(ra=ra_centro, dec=dec_centro, unit=(u.deg, u.deg), frame='icrs')
    Simbad.reset_votable_fields()
    Simbad.add_votable_fields('dimensions', 'V')
    Simbad.TIMEOUT = 180

    inicio = datetime.now()
    print(f"⏳ Consultando objetos en la región... [{inicio.strftime('%H:%M:%S')}]")
    resultado = Simbad.query_region(centro, radius=radio_arcmin * u.arcmin)
    fin = datetime.now()
    #print("Columnas disponibles:", resultado.colnames)

    duracion = (fin - inicio).total_seconds()
    print(f"✅ Consulta completada. [{fin.strftime('%H:%M:%S')}] (duración: {duracion:.1f} s)")

    if resultado is None:
        print("🔍 SIMBAD no devolvió resultados para esta región.")
        return None
    print(f"🔍 SIMBAD devolvió {len(resultado)} objetos.")

    # 3️⃣ Filtrado de resultados
    inicio = datetime.now()
    print(f"⏳ Filtrando objetos... [{inicio.strftime('%H:%M:%S')}]")

    objetos = []
    descartados = []
    for fila in resultado:
        try:
            nombre = fila['main_id']
            tam_mayor = limpiar_numero(fila['galdim_majaxis'])
            tam_menor = limpiar_numero(fila['galdim_minaxis'])
            mag = limpiar_numero(fila['V'])

            razones = []
            if not isinstance(nombre, str):
                razones.append("nombre no es string")
            elif not (nombre.startswith("M ") or "NGC " in nombre or "UGC " in nombre or "IC " in nombre):
                razones.append("catálogo inválido")
            if tam_mayor is None or tam_mayor <= 0:
                razones.append("tam_mayor inválido")
            if tam_menor is None or tam_menor <= 0:
                razones.append("tam_menor inválido")
            # if mag is None or mag > mag_max:
            #     razones.append("mag inválida o > mag_max")

            if razones:
                descartados.append({
                    "nombre": nombre,
                    "tam_mayor": tam_mayor,
                    "tam_menor": tam_menor,
                    "mag": mag,
                    "razones": "; ".join(razones)
                })
                continue

            # Decodificar nombre si viene en bytes
            nombre = fila['main_id'].decode('utf-8') if isinstance(fila['main_id'], bytes) else fila['main_id']
            #coord = SkyCoord(fila['ra'], fila['dec'], unit=(u.hourangle, u.deg))
            coord = SkyCoord(ra=fila['ra'], dec=fila['dec'], unit=(u.deg, u.deg), frame='icrs')
            ra = coord.ra.deg
            dec = coord.dec.deg

            ang = fila['galdim_angle']
            #angulo_dibujo = -(float(ang) - 90) if ang and not hasattr(ang, "mask") else None
            if ang and not hasattr(ang, "mask"):
                angulo_dibujo = -(float(ang) - 90)
            else:
                angulo_dibujo = None

            objetos.append({
                "nombre": nombre,
                "ra": ra,
                "dec": dec,
                "tamano_arcsec": float(tam_mayor) * 60,
                "tamano_menor_arcsec": float(tam_menor) * 60,
                "angulo_objeto": angulo_dibujo,
                "mag": float(mag) if mag is not None else None
            })

        except Exception as e:
            n = fila.get('main_id', '?')
            if isinstance(n, bytes):
                n = n.decode('utf-8', errors='ignore')
            print(f"⚠️ Error con objeto {n}: {e}")

    fin = datetime.now()
    duracion = (fin - inicio).total_seconds()
    print(f"✅ Filtrado completado. [{fin.strftime('%H:%M:%S')}] (duración: {duracion:.1f} s)")

    # 4️⃣ Guardar resultados filtrados en CSV para cache
    if objetos:
        with open(cache_path, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=objetos[0].keys(), delimiter=';')
            writer.writeheader()
            writer.writerows(objetos)
    else:
        print("⚠️ No se encontraron objetos válidos para guardar en CSV.")

    if descartados:
        with open('objetos_descartados.csv', mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=descartados[0].keys(), delimiter=';')
            writer.writeheader()
            writer.writerows(descartados)
        print(f"⚠️ Se descartaron {len(descartados)} objetos (ver objetos_descartados.csv)")

    print("✅ Objetos SIMBAD encontrados:")
    for obj in objetos:
        mag_str = f"{obj['mag']:.2f}" if obj.get('mag') is not None else "N/A"
        mayor = obj['tamano_arcsec'] / 60
        menor = (obj['tamano_menor_arcsec'] / 60) if obj.get('tamano_menor_arcsec') else None
        menor_str = f", menor: {menor:.2f}′" if menor is not None else ""
        ang_str = f"{(obj.get('angulo_objeto') or 0):.1f}°" #ang_str = f"{obj.get('angulo_objeto',0):.1f}°"
        print(f" - {obj['nombre']}: mag {mag_str}, mayor: {mayor:.2f}′{menor_str}, ángulo: {ang_str}")

    return objetos


def generar_jpg_con_circulos_desde_ini(ruta_fits, ruta_ini, objetos_simbad, ruta_salida="salida.jpg", usar_circulo=False):
    import math, cv2, numpy as np
    from astropy.io import fits
    from astropy.io.fits import Header
    from astropy.wcs import WCS
    from AutoStrechSTF import apply_stf_color

    # 1) Abre FITS y aplica STF+color
    with fits.open(ruta_fits) as hdul:
        data = hdul[0].data
    img = np.transpose(data, (1,2,0)) if data.ndim==3 else np.expand_dims(data, -1)
    img_color = cv2.cvtColor(apply_stf_color(img), cv2.COLOR_RGB2BGR)
    h, w = img_color.shape[:2]

    # 2) Lee WCS del INI
    ra0 = dec0 = crpix1 = crpix2 = None
    cd11 = cd12 = cd21 = cd22 = None
    for L in open(ruta_ini):
        if   L.startswith("CRVAL1="): ra0    = float(L.split("=",1)[1])
        elif L.startswith("CRVAL2="): dec0   = float(L.split("=",1)[1])
        elif L.startswith("CRPIX1="): crpix1 = float(L.split("=",1)[1])
        elif L.startswith("CRPIX2="): crpix2 = float(L.split("=",1)[1])
        elif L.startswith("CD1_1="):  cd11   = float(L.split("=",1)[1])
        elif L.startswith("CD1_2="):  cd12   = float(L.split("=",1)[1])
        elif L.startswith("CD2_1="):  cd21   = float(L.split("=",1)[1])
        elif L.startswith("CD2_2="):  cd22   = float(L.split("=",1)[1])
    if None in (ra0, dec0, crpix1, crpix2, cd11, cd12, cd21, cd22):
        print("❌ Parámetros WCS incompletos en el INI.")
        return

    # 3) Header mínimo + WCS
    hdr = Header()
    hdr['CRPIX1'], hdr['CRPIX2'] = crpix1, crpix2
    hdr['CRVAL1'], hdr['CRVAL2'] = ra0,    dec0
    hdr['CD1_1'],  hdr['CD1_2']  = cd11,   cd12
    hdr['CD2_1'],  hdr['CD2_2']  = cd21,   cd22
    hdr['CTYPE1'], hdr['CTYPE2'] = 'RA---TAN','DEC--TAN'
    hdr['CUNIT1'], hdr['CUNIT2'] = 'deg','deg'
    wcs = WCS(hdr)

    # 4) Ángulo de cámara (grados)
    phi_cam = math.degrees(math.atan2(cd12, cd11))

    # 5) Escala de píxel en arcsec/pix
    det = cd11*cd22 - cd12*cd21
    ps_arcsec = math.sqrt(abs(det)) * 3600.0

    # 6) Dibujar objetos
    for obj in objetos_simbad:
        try:
            # centro
            xw, yw = wcs.all_world2pix([[obj['ra'], obj['dec']]], 1)[0]
            x, y = int(round(xw-1)), int(round(yw-1))
            #print(f"DEBUG: objeto {obj['nombre']} → xw={xw:.1f}, yw={yw:.1f} → x={x}, y={y}  tamaño imagen: w={w}, h={h}")
            if not (0<=x<w and 0<=y<h): continue

            # semiejes en píxels
            semi_maj_px = (obj['tamano_arcsec']/2)/ps_arcsec
            semi_min_px = (obj.get('tamano_menor_arcsec', obj['tamano_arcsec'])/2)/ps_arcsec
            if not (math.isfinite(semi_maj_px) and math.isfinite(semi_min_px)): continue

            # ángulo de la elipse
            # ángulo de la elipse que viene de SIMBAD (o None si no había)
            pa_sim = obj.get('angulo_objeto', None)

            if pa_sim is None:                # No había ángulo real: dibujamos un círculo
                r = int(round(semi_maj_px))
                if r > 0:
                    cv2.circle(img_color, (x, y), r, (255,255,255), 1)

            elif usar_circulo:
                # Forzar todos círculos si se pide así
                r = int(round(semi_maj_px))
                if r > 0:
                    cv2.circle(img_color, (x,y), r, (255,255,255), 1)

            else:
                # Dibujo de la elipse normal con su ángulo corregido
                ang = pa_sim - phi_cam
                axes = (int(round(semi_maj_px)), int(round(semi_min_px)))
                if axes[0] >= 1 and axes[1] >= 1:
                    cv2.ellipse(img_color, (x,y), axes, ang, 0, 360, (255,255,255), 1)

            # ── Texto (siempre, una sola vez por objeto) ──
            mag = obj.get('mag')
            txt = f"{obj['nombre']} ({mag:.1f})" if mag is not None else obj['nombre']
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)

            # centra horizontalmente y sitúa a y+radio+12 px
            cv2.putText(img_color, txt,
                        (x - tw//2, y + int(semi_maj_px) + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255,255,255), 1)

        except Exception as e:
            print(f"⚠️ Error dibujando {obj.get('nombre','?')}: {e}")

    # 7) Guarda la imagen
    cv2.imwrite(ruta_salida, img_color)






def resolver_y_generar_imagen_con_objetos(ruta_fits, mag_max=14.0, ruta_salida_jpg="static/scopepower_objetos.jpg"):
    """
    Flujo completo ScopePower:
    - Plate solve del FITS con ASTAP
    - Consulta a SIMBAD con objetos con tamaño
    - Dibujo de círculos sobre la imagen estirada
    - Devuelve la ruta del JPG generado (o None si falla)
    """
    if not os.path.exists(ruta_fits):
        print(f"❌ No se encontró el archivo FITS: {ruta_fits}")
        return None

    # 1. Generar FITS monocromo y hacer plate solve
    ruta_mono = os.path.splitext(ruta_fits)[0] + "_mono.fits"
    generar_fits_monocromo_para_platesolve(ruta_fits, ruta_mono)

    comando_astap = f'"{RUTA_ASTAP}" -f "{ruta_mono}" -solve -r 50'
    subprocess.run(comando_astap, shell=True, capture_output=True, text=True)

    ruta_ini = os.path.splitext(ruta_mono)[0] + ".ini"
    if not os.path.exists(ruta_ini):
        print(f"❌ No se encontró el archivo INI generado por ASTAP: {ruta_ini}")
        return None

    # 2. Obtener objetos SIMBAD formateados
    objetos_simbad = obtener_objetos_simbad(ruta_ini, ruta_fits, mag_max=mag_max)
    if not objetos_simbad:
        print("⚠️ No se encontraron objetos adecuados en SIMBAD.")
        return None

    # 3. Generar la imagen final con círculos
    generar_jpg_con_circulos_desde_ini(ruta_fits, ruta_ini, objetos_simbad, ruta_salida_jpg)

    return ruta_salida_jpg


if __name__ == "__main__":
    ruta_fits = "static/resultado_apilado.fits"  # Cambia esto por el FITS que quieras procesar
    ruta_jpg = resolver_y_generar_imagen_con_objetos(ruta_fits)
    if ruta_jpg:
        print("✅ Imagen generada:", ruta_jpg)
    else:
        print("❌ Error al generar la imagen.")
