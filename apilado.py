# apilado.py

import os
import sys
import threading
import time
from ftplib import FTP
import numpy as np
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from astropy.io import fits
import cv2
import matplotlib.pyplot as plt
import configparser
from datetime import datetime
import shutil
from collections import deque
from AutoStrechSTF import apply_stf_color, debayer_rgb
from ScopePower_ObjetosAnotados import resolver_y_generar_imagen_con_objetos
import cv2

import subprocess
from astropy.io.fits import Header, PrimaryHDU
from astropy.wcs import WCS
from math import atan2, degrees
import threading
from ftplib import FTP
import configparser
import os

cfg = configparser.ConfigParser()
cfg.read("config.ini")
ruta_astap = cfg["General"]["ruta_astap"]

# ———————————
# Helpers ASTAP
# ———————————
def generar_fits_monocromo_para_astap(ruta_rgb, ruta_mono):
    from astropy.io import fits
    import numpy as np
    with fits.open(ruta_rgb) as hdul:
        orig = hdul[0]
        data = orig.data.astype(np.float32)
    gris = data.mean(axis=0) if data.ndim==3 else data
    gris = np.clip(gris, 0, 65535).astype(np.uint16)
    hdr = orig.header.copy()
    PrimaryHDU(gris, header=hdr).writeto(ruta_mono, overwrite=True)

def parse_ini_wcs(ruta_ini):
    crpix1=crpix2=crval1=crval2=cd11=cd12=cd21=cd22=None
    with open(ruta_ini) as f:
        for L in f:
            if L.startswith("CRPIX1="): crpix1 = float(L.split("=",1)[1])
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
    hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN", "DEC--TAN"
    hdr["CUNIT1"], hdr["CUNIT2"] = "deg", "deg"
    wcs = WCS(hdr)
    angle = degrees(atan2(cd12, cd11))
    return {"wcs": wcs, "crpix": (crpix1-1, crpix2-1), "angle": angle}

def compute_astap_transform(ruta_fits, ref_info, ruta_astap):
    mono      = ruta_fits.replace(".fits", "_mono.fits")
    ini       = mono.replace(".fits", ".ini")
    wcs_file  = mono.replace(".fits", ".wcs")

    try:
        # Genera FITS monocromo y llama a ASTAP
        generar_fits_monocromo_para_astap(ruta_fits, mono)
        subprocess.run(
            f'"{ruta_astap}" -f "{mono}" -solve -r 50',
            shell=True, capture_output=True, check=True
        )

        # Si no existe el .ini, devolvemos sin más
        if not os.path.exists(ini):
            return None, "ASTAP"

        # Leemos resultados y calculamos la matriz de transformación
        now = parse_ini_wcs(ini)
        cx, cy = ref_info["crpix"]
        ra, dec = ref_info["wcs"].all_pix2world([[cx, cy]], 0)[0]
        x_new, y_new = now["wcs"].all_world2pix([[ra, dec]], 0)[0]
        dx, dy = cx - x_new, cy - y_new
        rot = now["angle"] - ref_info["angle"]

        M = cv2.getRotationMatrix2D((cx, cy), rot, 1.0)
        M[0,2] += dx
        M[1,2] += dy

        return M, "ASTAP"

    except subprocess.CalledProcessError as e:
        # Si ASTAP falla internamente, podemos capturar el error
        return None, f"ASTAP error: {e}"
    except Exception as e:
        # Cualquier otro fallo
        return None, f"Unexpected error: {e}"

    finally:
        # ——— limpieza de archivos intermedios ASTAP ———
        for fichero in (mono, ini, wcs_file):
            try:
                if os.path.exists(fichero):
                    os.remove(fichero)
            except Exception:
                pass
        # ————————————————————————————————




# ─── Flag para activar/desactivar anotación de objetos ─────────
OBJETOS_ANOTADOS = False

apilador_global = None
apilado_pausado = False

def pausar_apilado():
    global apilado_pausado
    apilado_pausado = True

def reanudar_apilado():
    global apilado_pausado
    apilado_pausado = False

def esta_pausado():
    return apilado_pausado



def ruta_absoluta(relativa):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relativa)
    return os.path.join(os.path.abspath("."), relativa)


def subir_imagen_ftp_async(ruta_local, nombre_remoto="ultima.jpg"):
    def subir():
        try:
            config = configparser.ConfigParser()
            config.read("config.ini")

            # Comprobación si está activada la subida FTP
            if config["FTP"].get("subir_ftp", "false").lower() != "true":
                print("🔕 Subida FTP desactivada en config.ini.")
                return

            # Lectura de credenciales y ruta
            ftp_host = config["FTP"]["host"]
            ftp_user = config["FTP"]["usuario"]
            ftp_pass = config["FTP"]["contrasena"]
            ftp_path = config["FTP"].get("ruta_remota", "").strip()

            # Verificación del archivo local
            if not os.path.exists(ruta_local):
                print(f"⚠️ Archivo local no encontrado: {ruta_local}")
                return

            # Conexión FTP y subida
            with FTP(ftp_host) as ftp:
                ftp.login(ftp_user, ftp_pass)
                if ftp_path:
                    ftp.cwd(ftp_path)

                with open(ruta_local, "rb") as f:
                    ftp.storbinary(f"STOR " + nombre_remoto, f)

            print(f"✅ Imagen '{nombre_remoto}' subida al FTP correctamente.")

        except Exception as e:
            print(f"❌ Error al subir imagen por FTP: {e}")

    # Lanzar en hilo separado, no bloquea apilado
    threading.Thread(target=subir, daemon=True).start()

def linear_fit_rgb(rgb, referencia='verde'):
    """
    Ajusta R y B para igualar la media al canal de referencia.
    Entrada y salida: imagen RGB (3, H, W) en float32.
    
    Parámetros:
    - referencia: 'rojo', 'verde', 'azul' o 'promedio'
    """
    r, g, b = rgb

    medias = {
        'rojo': np.mean(r),
        'verde': np.mean(g),
        'azul': np.mean(b),
        'promedio': np.mean([r, g, b])
    }

    ref = referencia.lower()
    if ref not in medias:
        print(f"⚠️ Canal de referencia '{referencia}' no válido. Usando 'verde'.")
        ref = 'verde'
    else:
        print(f"🎯 Usando LinearFit con canal de referencia: '{ref}'")

    media_ref = medias[ref]

    # Escalar R y B al canal de referencia (G si no se indica lo contrario)
    if medias['rojo'] > 0:
        r = r * (media_ref / medias['rojo'])
    if medias['azul'] > 0:
        b = b * (media_ref / medias['azul'])

    return np.stack([r, g, b], axis=0)


def aplicar_estirado_mtf(imagen, shadows=None, highlights=None, midtone=None):
    """
    Estirado MTF basado en PixInsight (más preciso y configurable).
    `imagen` debe estar normalizada entre 0 y 1.
    """

    # Si no se pasan valores, se leen del config.ini
    if shadows is None or highlights is None or midtone is None:
        try:
            nuevo_config = configparser.ConfigParser()
            nuevo_config.read("config.ini")
            shadows = float(nuevo_config["Dispositivos"].get("MTF_SHADOWS", 0.02))
            highlights = float(nuevo_config["Dispositivos"].get("MTF_HIGHLIGHTS", 0.98))
            midtone = float(nuevo_config["Dispositivos"].get("MTF_MIDTONE", 0.4))

            print(f"Usando estirado MTF shadows en factor: '{shadows}'")
            print(f"Usando estirado MTF highlights en factor: '{highlights}'")
            print(f"Usando estirado MTF midtone en factor: '{midtone}'")

        except Exception as e:
            print(f"⚠️ Error leyendo parámetros MTF del config.ini: {e}")
            shadows, highlights, midtone = 0.02, 0.98, 0.4

    xp = (imagen - shadows) / (highlights - shadows)
    xp = np.clip(xp, 0, 1)

    numerador = (midtone - 1) * xp
    denominador = ((2 * midtone - 1) * xp) - midtone

    with np.errstate(divide='ignore', invalid='ignore'):
        resultado = np.true_divide(numerador, denominador)
        resultado[~np.isfinite(resultado)] = 0

    return np.clip(resultado, 0, 1)

import matplotlib.colors as mcolors

def aumentar_saturacion(rgb, factor=1.0):
    """
    Aumenta la saturación de una imagen RGB.

    Acepta (H, W, 3) o (3, H, W), en uint8 [0, 255] o float [0, 1].
    Devuelve la imagen con el mismo formato y dtype de entrada.
    """
    if factor == 1.0 or rgb.ndim != 3:
        return rgb

    canales_primero = rgb.shape[0] == 3 and rgb.shape[-1] != 3
    if not canales_primero and rgb.shape[-1] != 3:
        return rgb  # no es RGB

    hwc = np.moveaxis(rgb, 0, -1) if canales_primero else rgb

    es_uint8 = hwc.dtype == np.uint8
    hwc_f = hwc.astype(np.float32) / 255.0 if es_uint8 else np.clip(hwc.astype(np.float32), 0, 1)

    hsv = mcolors.rgb_to_hsv(hwc_f)
    hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 1)
    rgb_mod = np.clip(mcolors.hsv_to_rgb(hsv), 0, 1)

    if es_uint8:
        rgb_mod = (rgb_mod * 255).astype(np.uint8)
    else:
        rgb_mod = rgb_mod.astype(rgb.dtype)

    return np.moveaxis(rgb_mod, -1, 0) if canales_primero else rgb_mod


def aplicar_scnr_verde(rgb, factor=1.0):
    """
    Aplica una reducción de dominancia verde estilo PixInsight SCNR.
    rgb: imagen en formato (3, H, W)
    factor: intensidad de la reducción (1.0 = máxima eliminación)
    """
    if rgb.shape[0] != 3:
        print("ℹ️ SCNR no aplicado: imagen no tiene 3 canales.")
        return rgb

    print(f"🎯 Usando SCNR al Verde en factor: '{factor}'")
    r, g, b = rgb
    exceso_verde = g - (r + b) / 2
    g_corregido = g - factor * exceso_verde
    g_corregido = np.clip(g_corregido, 0, 65535)
    return np.stack([r, g_corregido, b], axis=0)


class ApiladorEnTiempoReal:
    def __init__(self, carpeta, salida_png, alinear=True):
        self.carpeta = carpeta
        self.salida_png = salida_png
        self.usar_alineacion = alinear
        self.acumulado_r = None
        self.acumulado_g = None
        self.acumulado_b = None
        self.total = 0
        self.imagen_referencia = None
        self.inicio_apilado = datetime.now()
        self.ultima_captura = ""
        self.lock = threading.Lock()
        self.ruta_astap = ruta_astap
        self.lista_r = deque()
        self.lista_g = deque()
        self.lista_b = deque()

        self.ref_info = None
        
    def obtener_transformacion(self, img, ref, ruta_fits=None):
        """
        Llama a ASTAP para calcular rotación+traslación con respecto a la referencia.
        ruta_fits: ruta original al FITS para generar el .ini
        """
        if self.ref_info is None or ruta_fits is None:
            return np.eye(2,3,dtype=np.float32), 'NONE'
        #M, tag = compute_astap_transform(ruta_fits, self.ref_info, self.ruta_astap)
        M, tag = compute_astap_transform(ruta_fits, self.ref_info, self.ruta_astap)
        return M, tag

    def aplicar_transformacion(self, img, M, metodo):
        """
        Aplica la matriz de transformación M (2x3) al array img (2D o 3D).
        Si el método es 'ASTAP' y M no es None, realiza un warpAffine;
        si no, devuelve la imagen original.
        """
        #if metodo == 'ASTAP' and M is not None:
            # img puede ser 2D (gris) o 3D (canal), warpAffine funca igual
        h, w = img.shape[:2]
        return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR)
        #return img



    def limpiar_fits_antiguos(self):
        try:
            nuevo_config = configparser.ConfigParser()
            nuevo_config.read("config.ini")
            NUM_MAX = int(nuevo_config["Dispositivos"].get("numero_maximo_imagenes", 10))

            archivos_fits = sorted(
                [f for f in os.listdir(self.carpeta) if f.lower().endswith(".fits")],
                key=lambda x: os.path.getmtime(os.path.join(self.carpeta, x))
            )
            if len(archivos_fits) > NUM_MAX:
                a_borrar = archivos_fits[:-NUM_MAX]
                for nombre in a_borrar:
                    ruta = os.path.join(self.carpeta, nombre)
                    os.remove(ruta)
                    print(f"🗑️ FITS eliminado por límite: {ruta}")
        except Exception as e:
            print(f"⚠️ Error al limpiar FITS: {e}")


    def cargar_fits_grayscale(self, ruta):
        with fits.open(ruta) as hdul:
            data = hdul[0].data.astype(np.float32)
            if data.ndim == 3 and data.shape[0] == 3:
                r = data[0]
            elif data.ndim == 2:
                r = data
            else:
                raise ValueError("Formato no compatible")
            r = np.nan_to_num(r)
            r = (r - np.min(r)) / (np.max(r) - np.min(r) + 1e-6)
            r = (r * 255).astype(np.uint8)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            r = clahe.apply(r)
            return r



    def procesar_imagen_fits(self, ruta_fits, ObjetosAnotados=False):
        try:

            # Leer valores actualizados de config.ini
            nuevo_config = configparser.ConfigParser()
            nuevo_config.read("config.ini")
            EXPOSICION = float(nuevo_config["Dispositivos"].get("exposicion_captura", 20))
            NUM_MAX = int(nuevo_config["Dispositivos"].get("numero_maximo_imagenes", 20))
            FOCAL_LEN = float(nuevo_config["Dispositivos"]["FOCAL_LEN"])

            # Limpiar imágenes FITS antiguas en disco
            self.limpiar_fits_antiguos()
            # Verificar si ya hay demasiadas imágenes y detener el procesamiento de esta imagen
            archivos_fits = [f for f in os.listdir(self.carpeta) if f.lower().endswith(".fits")]
            if len(archivos_fits) >= NUM_MAX + 5:
                print(f"🚫 Se detiene procesamiento: demasiadas imágenes pendientes ({len(archivos_fits)} ≥ {NUM_MAX + 5})")
                return


            print(f"🔧 Procesando FITS: {ruta_fits}")
            with fits.open(ruta_fits) as hdul:
                hdr  = hdul[0].header
                data = hdul[0].data.astype(np.float32)
                data = np.nan_to_num(data)

            # -- DEMOSAIC con OpenCV (a 16 bits, antes de alinear) --
            if data.ndim == 2:
                raw_uint = np.clip(data, 0, np.iinfo(np.uint16).max).astype(np.uint16)
                patron = hdr.get("BAYERPAT", "RGGB")

                # debayer_rgb ya usa el código OpenCV correcto para cada patrón
                # y devuelve (H, W, 3) en orden RGB
                rgb = debayer_rgb(raw_uint, patron).astype(np.float32)
                r, g, b = rgb[...,0], rgb[...,1], rgb[...,2]



            elif data.ndim == 3 and data.shape[0] == 3:
                # ya tenías un FITS con 3 canales
                r, g, b = data

            else:
                print("✘ Formato inesperado.")
                raise ValueError("El FITS no es ni CFA 2D ni RGB 3×H×W.")

            # A partir de aquí 'r','g','b' son matrices H×W con cada canal
            # y ya puedes hacer alineación y apilado normalmente


            # Generar imagen de referencia si no la hay
            img_r = self.cargar_fits_grayscale(ruta_fits)
            if self.imagen_referencia is None:
                # guardamos la miniatura de referencia
                self.imagen_referencia = img_r
                # y también la info ASTAP
                ref_mono = ruta_fits.replace(".fits","_mono.fits")
                # generar y resolver para la referencia
                generar_fits_monocromo_para_astap(ruta_fits, ref_mono)
                subprocess.run(f'"{ruta_astap}" -f "{ref_mono}" -solve -r 50',
                               shell=True, capture_output=True)
                ref_ini = ref_mono.replace(".fits",".ini")
                self.ref_info = parse_ini_wcs(ref_ini)

                # — limpieza inicial de archivos de plate‐solve —
                for fichero in (ref_mono, ref_ini, ref_mono.replace(".fits", ".wcs")):
                    try: os.remove(fichero)
                    except: pass
            # Alinear con la imagen de referencia
            #M, metodo = obtener_transformacion(img_r, self.imagen_referencia, ruta_fits)
            #M, tag = compute_astap_transform(ruta_fits, self.ref_info, self.ruta_astap)
            M, metodo = self.obtener_transformacion(img_r, self.imagen_referencia, ruta_fits)
            r = self.aplicar_transformacion(r, M, metodo)
            g = self.aplicar_transformacion(g, M, metodo)
            b = self.aplicar_transformacion(b, M, metodo)

            # Guardar en memoria (usamos deque)
            self.lista_r.append(r)
            self.lista_g.append(g)
            self.lista_b.append(b)

            if len(self.lista_r) > NUM_MAX:
                self.lista_r.popleft()
                self.lista_g.popleft()
                self.lista_b.popleft()

            self.total = len(self.lista_r)
            self.ultima_captura = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def sigma_clipped_mean(stack, sigma=2.0):
                """
                stack: lista de imágenes (N, H, W)
                sigma: umbral para excluir outliers
                """
                stack = np.stack(stack, axis=0)  # (N, H, W)
                media = np.mean(stack, axis=0)
                std = np.std(stack, axis=0)

                # Máscara de valores dentro del rango
                mascara = np.abs(stack - media) <= sigma * std
                stack_filtrado = np.where(mascara, stack, np.nan)

                return np.nanmean(stack_filtrado, axis=0)  # promedio ignorando NaNs

            # Calcular promedio con sigma-clipping
            r = sigma_clipped_mean(self.lista_r)
            g = sigma_clipped_mean(self.lista_g)
            b = sigma_clipped_mean(self.lista_b)


            rgb = np.stack([r, g, b], axis=0)
            num_imagenes_preview = self.total
            if self.total < NUM_MAX:
                num_imagenes_preview += 1


            # Usar la cabecera del último FITS como base
            with fits.open(ruta_fits) as hdul:
                header_original = hdul[0].header.copy()

            # Modificar cabecera con datos del apilado
            header_original['NSTACK'] = (num_imagenes_preview, 'Numero de imagenes apiladas')
            header_original['EXPTOT'] = (num_imagenes_preview * EXPOSICION, 'Tiempo total de exposicion (s)')
            header_original['DATE'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            header_original['CREATOR'] = ('ScopePower', 'Real-time Power for Your Telescope')

            # Usamos r (o bien el canal que prefieras), pero como r=g=b
            # ——— 1) Guardar FITS apilado como RGB (3×H×W) ———
            rgb_cube = np.stack([r, g, b], axis=0).astype(np.float32)  # shape = (3, H, W)

            # Preparamos la cabecera
            hdr_out = header_original.copy()
            # Eliminamos claves antiguas de CFA si existen
            for key in ("BAYERPAT","CFA","NAXIS","NAXIS1","NAXIS2","NAXIS3","BITPIX"):
                hdr_out.pop(key, None)
            # Definimos el nuevo formato
            hdr_out["BITPIX"] = -32                   # float32
            hdr_out["NAXIS"]  = 3
            hdr_out["NAXIS1"] = rgb_cube.shape[2]     # ancho
            hdr_out["NAXIS2"] = rgb_cube.shape[1]     # alto
            hdr_out["NAXIS3"] = rgb_cube.shape[0]     # canales
            hdr_out["COMMENT"] = "Debayered RGB image"

            hdu = PrimaryHDU(data=rgb_cube, header=hdr_out)
            hdu.writeto("static/resultado_apilado.fits", overwrite=True)
            print("✅ FITS apilado guardado en modo RGB.")



            # Si objetos anotados, lee el jpg anotado y conviértelo a rgb_mtf
            if ObjetosAnotados:
                # resolver_y_generar_imagen_con_objetos ya devuelve un JPG coloreado
                ruta_obj = "static/scopepower_objetos.jpg"
                ok = resolver_y_generar_imagen_con_objetos("static/resultado_apilado.fits",
                                                           ruta_salida_jpg=ruta_obj)
                if ok and os.path.exists(ruta_obj):
                    img_bgr = cv2.imread(ruta_obj)
                    rgb_mtf = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                else:
                    print("⚠️ Fallback al flujo normal (objetos anotados).")
                    rgb_mtf = apply_stf_color(rgb_cube)
            else:
                # SIN OBJETOS: aplicamos STF al cube CFA
                rgb_mtf = apply_stf_color(rgb_cube)

            # Leer intensidad de saturación en tiempo real
            nuevo_config = configparser.ConfigParser()
            nuevo_config.read("config.ini")
            intensidad_color = float(nuevo_config["Dispositivos"].get("color_intensity", 1.0))

            # Aumentar saturación
            rgb_mtf = aumentar_saturacion(rgb_mtf, factor=intensidad_color)

            #rgb_vis = np.moveaxis(rgb_mtf, 0, -1)

            # Convertir a BGR para OpenCV
            #rgb_uint8 = (rgb_vis * 255).astype(np.uint8)
            imagen_bgr = cv2.cvtColor(rgb_mtf, cv2.COLOR_RGB2BGR)

            # Leer valor de zoom desde config.ini
            zoom = int(nuevo_config["Dispositivos"].get("zoom_preview", 0))
            if zoom > 0:
                h, w = imagen_bgr.shape[:2]
                margen_h = int(h * zoom / 100 / 2)
                margen_w = int(w * zoom / 100 / 2)
                imagen_bgr = imagen_bgr[margen_h:h - margen_h, margen_w:w - margen_w]

            # ⛔️ Si el zoom es muy alto, omitir texto para evitar pixelado
            if zoom <= 80:
                alto = imagen_bgr.shape[0]
                font_scale_grande = round(alto / 800.0, 2)
                font_scale_grande = max(0.4, min(font_scale_grande, 1.5))

                font_scale_pequeno = font_scale_grande * 0.7
                thickness_grande = max(1, int(font_scale_grande * 2.5))
                thickness_pequeno = max(1, int(thickness_grande * 0.7))

                # Hora y fecha actuales
                fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                self.total = len(self.lista_r)

                num_imagenes_preview = self.total
                if self.total < NUM_MAX:
                    num_imagenes_preview += 1

                texto_fecha = f"{fecha_hora}"
                texto_apilado = f"{num_imagenes_preview} imagenes, {num_imagenes_preview * EXPOSICION:.1f} s"

                font = cv2.FONT_HERSHEY_SIMPLEX
                color = (180, 180, 180)

                # Medir tamaños
                (t_width, t_height), _ = cv2.getTextSize(texto_apilado, font, font_scale_grande, thickness_grande)
                (tw_fecha, th_fecha), _ = cv2.getTextSize(texto_fecha, font, font_scale_pequeno, thickness_pequeno)

                # Posiciones centradas
                pos_x = (imagen_bgr.shape[1] - t_width) // 2
                pos_y = imagen_bgr.shape[0] - 10
                pos_x_fecha = (imagen_bgr.shape[1] - tw_fecha) // 2
                pos_y_fecha = pos_y - t_height - 8

                # Fecha
                cv2.putText(imagen_bgr, texto_fecha, (pos_x_fecha + 1, pos_y_fecha + 1), font, font_scale_pequeno, (0, 0, 0), 2)
                cv2.putText(imagen_bgr, texto_fecha, (pos_x_fecha, pos_y_fecha), font, font_scale_pequeno, color, thickness_pequeno)

                # Texto principal
                cv2.putText(imagen_bgr, texto_apilado, (pos_x + 1, pos_y + 1), font, font_scale_grande, (0, 0, 0), 2)
                cv2.putText(imagen_bgr, texto_apilado, (pos_x, pos_y), font, font_scale_grande, color, thickness_grande)

            # Guardar JPG final
            cv2.imwrite(ruta_absoluta("static/resultado_apilado_preview.jpg"), imagen_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])



            duracion = datetime.now() - self.inicio_apilado
            #info = f"{self.total} imágenes apiladas\nTiempo de integración total: {self.total * EXPOSICION:.1f} segundos\nDuración del proceso: {duracion}"
            info = f"{num_imagenes_preview} imágenes apiladas\nTiempo de integración total: {num_imagenes_preview * EXPOSICION:.1f} segundos\nDuración del proceso: {duracion}"

            with open("static/apilado_info.txt", "w") as f:
                f.write(info)

            print(f"✅ Imagen apilada actualizada. Total: {self.total}")

            # Subir imagen al FTP en segundo plano (no bloquea el apilado)
            subir_imagen_ftp_async("static/resultado_apilado_preview.jpg", "ultima.jpg")
            subir_imagen_ftp_async("static/info_objeto_goto.txt", "info_objeto_goto.txt")

            # Limpiar imágenes FITS antiguas en disco
            self.limpiar_fits_antiguos()

        except Exception as e:
            print(f"❌ Error procesando imagen: {e}")



class VigilanteCapturas(FileSystemEventHandler):
    def __init__(self, apilador):
        super().__init__()
        self.apilador = apilador


    def on_created(self, event):
                # Sólo procesar los .fits “originales”, no los *_mono.fits que genera ASTAP
        if (not event.is_directory
            and event.src_path.lower().endswith(".fits")
            and not event.src_path.lower().endswith("_mono.fits")):

            if esta_pausado():  # ← Usamos directamente la función sin importarla
                print("⏸️ Apilado pausado. Imagen ignorada.")
                return

            print(f"📥 Nueva imagen: {event.src_path}")
            time.sleep(0.5)
            self.apilador.procesar_imagen_fits(event.src_path, ObjetosAnotados=OBJETOS_ANOTADOS)
            #self.apilador.procesar_imagen_fits(event.src_path)


def iniciar_hilo_apilado(carpeta="capturas", alinear=True):
    global apilador_global
    apilador = ApiladorEnTiempoReal(carpeta, os.path.join("static", "resultado_apilado_preview.jpg"), alinear=alinear)
    apilador_global = apilador
    evento = VigilanteCapturas(apilador)
    observer = Observer()
    observer.schedule(evento, carpeta, recursive=False)
    observer.daemon = True
    observer.start()
    print(f"🔍 Apilado en tiempo real iniciado (alinear={'Sí' if alinear else 'No'}).")
    return observer


def reiniciar_apilado_global():
    global apilador_global
    if apilador_global:
        with apilador_global.lock:
            apilador_global.imagen_referencia = None
            apilador_global.total = 0
            apilador_global.ultima_captura = ""

            # Si estás usando deque
            apilador_global.lista_r.clear()
            apilador_global.lista_g.clear()
            apilador_global.lista_b.clear()

            # Eliminar imágenes FITS de la carpeta
            for nombre in os.listdir(apilador_global.carpeta):
                if nombre.lower().endswith(".fits"):
                    ruta = os.path.join(apilador_global.carpeta, nombre)
                    try:
                        os.remove(ruta)
                        print(f"🗑️ Eliminado: {ruta}")
                    except Exception as e:
                        print(f"⚠️ No se pudo eliminar {ruta}: {e}")

            # Borrar los archivos del resultado anterior
            for archivo in [
                "static/resultado_apilado_preview.jpg",
                "static/resultado_apilado.fits",
                "static/apilado_info.txt",
                "objetos_filtrados.csv",
                "objetos_descartados.csv"
            ]:
                try:
                    if os.path.exists(archivo):
                        os.remove(archivo)
                        print(f"🗑️ Archivo de resultado eliminado: {archivo}")
                except Exception as e:
                    print(f"⚠️ No se pudo eliminar {archivo}: {e}")

            print("🔄 Apilador, estado y resultados reiniciados.")


def obtener_estado_apilado():
    global apilador_global
    if apilador_global:
        with apilador_global.lock:
            return apilador_global.total, apilador_global.ultima_captura
    return 0, "Ninguna"

