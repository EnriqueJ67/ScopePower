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
import anotaciones
import voz
import procesado
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

# Segundos máximos que se espera a ASTAP por fotograma. Si se cuelga, el
# fotograma se descarta en vez de bloquear el apilado para siempre.
ASTAP_TIMEOUT = 90

# Tira de evolución del apilado (miniaturas para ver cómo baja el ruido)
INTERVALO_EVOLUCION = int(cfg.get("Dispositivos", "intervalo_evolucion", fallback=5))
CARPETA_EVOLUCION = os.path.join("static", "evolucion")
ANCHO_MINIATURA = 240


def ini_resuelto(ruta_ini):
    """True si el .ini de ASTAP existe y contiene PLTSOLVD=T."""
    if not os.path.exists(ruta_ini):
        return False
    with open(ruta_ini) as f:
        return any(L.startswith("PLTSOLVD=T") for L in f)

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
            shell=True, capture_output=True, check=True, timeout=ASTAP_TIMEOUT
        )

        # Si ASTAP no ha resuelto, devolvemos sin más
        if not ini_resuelto(ini):
            return None, "ASTAP no resolvió el fotograma"

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

    except subprocess.TimeoutExpired:
        return None, f"ASTAP no terminó en {ASTAP_TIMEOUT} s"
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




# ———————————
# Calidad de fotograma y apilado con memoria acotada
# ———————————
def calcular_puntuacion(plano):
    """
    Puntúa la calidad de un fotograma a partir de sus estrellas.
    Devuelve (puntuacion, n_estrellas, nitidez). Mayor puntuación = mejor.

    - n_estrellas: fuentes detectadas por encima de fondo + 5 sigma (nubes,
      viento o desenfoque fuerte reducen el número).
    - nitidez: mediana de pico/flujo de cada estrella. Una estrella bien
      enfocada y sin arrastre concentra su luz en pocos píxeles (pico alto
      respecto al flujo total); alargada o desenfocada, lo reparte.
    - puntuacion = nitidez * sqrt(n_estrellas)
    Se calcula a media resolución para que tarde décimas de segundo.
    """
    img = np.asarray(plano, dtype=np.float32)
    peq = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)

    mediana = float(np.median(peq))
    mad = float(np.median(np.abs(peq - mediana))) * 1.4826 + 1e-3
    mascara = (peq > mediana + 5.0 * mad).astype(np.uint8)

    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(mascara, connectivity=8)
    if n <= 1:
        return 0.0, 0, 0.0

    areas = stats[1:, cv2.CC_STAT_AREA]
    # Descartar píxeles calientes (1-2 px) y manchas grandes (galaxias, nebulosas)
    validas = (areas >= 3) & (areas <= 200)
    n_estrellas = int(validas.sum())
    if n_estrellas == 0:
        return 0.0, 0, 0.0

    resta = (peq - mediana).ravel()
    ids = etiquetas.ravel()
    flujo = np.bincount(ids, weights=resta, minlength=n)
    pico = np.zeros(n, dtype=np.float32)
    np.maximum.at(pico, ids, resta.astype(np.float32))

    ratio = pico[1:][validas] / np.maximum(flujo[1:][validas], 1e-6)
    nitidez = float(np.median(ratio))
    puntuacion = nitidez * float(np.sqrt(n_estrellas))
    return puntuacion, n_estrellas, nitidez


def calcular_puntuacion_photutils(plano, fwhm_px=2.5, umbral_sigma=4.0):
    """
    Métrica de calidad de la versión de junio (AnalizaFits.py), portada:
    detección con DAOStarFinder y puntuación = suma de (SNR × redondez) de las
    estrellas, penalizada por fondo brillante. Devuelve (puntuacion,
    n_estrellas, redondez_media). Se calcula a media resolución.
    Requiere photutils; si no está instalado devuelve (0, 0, 0).
    """
    try:
        from photutils.detection import DAOStarFinder
        from astropy.stats import sigma_clipped_stats
    except ImportError:
        print("⚠️ photutils no está instalado; usa la métrica 'estrellas'")
        return 0.0, 0, 0.0
    img = np.asarray(plano, dtype=np.float32)
    img = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)
    _, med_bkg, std_bkg = sigma_clipped_stats(img, sigma=3.0)
    finder = DAOStarFinder(fwhm=max(1.5, fwhm_px / 2.0), threshold=umbral_sigma * max(std_bkg, 1e-3))
    tbl = finder(img - med_bkg)
    if tbl is None or len(tbl) == 0:
        return 0.0, 0, 0.0
    brillo_min = max(2 * std_bkg, 70)
    tbl = tbl[tbl['flux'] > brillo_min]
    if len(tbl) == 0:
        return 0.0, 0, 0.0
    forma = (1 - np.abs(tbl['roundness1'])) * (1 - np.abs(tbl['roundness2']))
    snr = tbl['flux'] / max(std_bkg, 1e-3)
    raw = float(np.sum(snr * forma))
    penalizador = 1.0 / (1.0 + med_bkg / max(float(img.max()), 1.0))
    return raw * penalizador, int(len(tbl)), float(np.mean(forma))


def leer_config_calidad():
    """[Apilado]: métrica de calidad y umbral relativo (portado de FiltroApilado.py)."""
    c = cfg["Apilado"] if "Apilado" in cfg else {}
    cfg2 = configparser.ConfigParser()
    cfg2.read("config.ini")
    c = cfg2["Apilado"] if "Apilado" in cfg2 else c
    return {
        "metrica": str(c.get("metrica_calidad", "estrellas")).lower(),
        "umbral_relativo": float(c.get("umbral_calidad", 0.5)),
        "top_para_umbral": int(c.get("top_para_umbral", 10)),
        "fwhm_px": float(c.get("fwhm_px", 2.5)),
    }


def apilar_sigma_clip(planos, sigma=2.0, filas_por_bloque=128):
    """
    Media con sigma-clipping de una lista de planos (H, W) uint16/float.
    Procesa por bloques de filas para no materializar N copias completas en
    memoria (con 35 fotogramas de 9 Mpx eso eran varios GB).
    Devuelve float32 (H, W).
    """
    n = len(planos)
    h, w = planos[0].shape
    salida = np.empty((h, w), dtype=np.float32)

    if n < 3:
        acum = np.zeros((h, w), dtype=np.float32)
        for p in planos:
            acum += p
        salida[:] = acum / n
        return salida

    for y0 in range(0, h, filas_por_bloque):
        y1 = min(h, y0 + filas_por_bloque)
        bloque = np.stack([p[y0:y1] for p in planos], axis=0).astype(np.float32)
        media = bloque.mean(axis=0)
        std = bloque.std(axis=0)
        mascara = np.abs(bloque - media) <= sigma * std + 1e-6
        cuenta = mascara.sum(axis=0)
        suma = np.where(mascara, bloque, 0.0).sum(axis=0)
        salida[y0:y1] = np.where(cuenta > 0, suma / np.maximum(cuenta, 1), media)
    return salida


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
            import secretos
            ftp_pass = secretos.obtener("ftp_contrasena")
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

        # Fotogramas que forman el apilado. Cada elemento es un dict con los
        # planos alineados en uint16 ("r","g","b"), su "puntuacion" y "nombre".
        # Se conservan como máximo NUM_MAX; cuando llega uno mejor que el peor
        # almacenado lo sustituye. uint16 en vez de float32 = la mitad de RAM.
        self.fotogramas = []
        self.num_max = 0

        # Estado visible desde la web
        self.descartadas = 0           # sin resolver / error
        self.descartadas_calidad = 0   # peores que las N guardadas
        self.ultimo_error = ""
        self.ultimo_mensaje = ""
        self.ultima_puntuacion = None
        self.procesando = False
        self.fase = ""   # paso actual del procesado (para la barra de progreso de la web)

        # Versión del JPG de vista previa: sube SOLO cuando el fichero ya está
        # escrito, para que la web no pida la imagen antes de tiempo.
        self.version_preview = 0
        self.apilados_realizados = 0
        # Tira de evolución: miniaturas del apilado cada INTERVALO_EVOLUCION apilados
        self.evolucion = []
        # Desplazamiento (dx, dy) en px de cada foto alineada respecto a la
        # referencia: sirve para ver cuánto "dithering natural" da la montura
        self.desplazamientos = []
        # Mejores puntuaciones vistas (para el umbral relativo) y umbral actual
        self.mejores_puntuaciones = []
        self.umbral = None

        self.ref_info = None

    def _fase(self, nombre):
        with self.lock:
            self.fase = nombre

    def _descartar(self, motivo, aviso="Foto descartada"):
        with self.lock:
            self.descartadas += 1
            self.ultimo_error = motivo
        print(f"⚠️ Fotograma descartado: {motivo}")
        voz.decir(aviso)
        
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



    def generar_preview(self, rgb_cube, num_imagenes_preview, EXPOSICION, ObjetosAnotados=False,
                        anotar_evolucion=True):
        """
        Del cubo lineal (3, H, W) al JPG de vista previa: procesado estilo
        Seestar (procesado.py) a media resolución, recorte de zoom, texto,
        miniatura de la tira de evolución y aviso a la web (versión).
        """
        self._fase("estirando")
        # Procesado completo a media resolución (la web muestra ≤600 px)
        rgb_mtf = procesado.procesar(rgb_cube, num_imagenes_preview, procesado.leer_config(), escala=0.5)
        if ObjetosAnotados:
            # Objetos del campo dibujados SOBRE la imagen procesada (anotaciones.py):
            # catálogo local sin Internet, SIMBAD si hay red, letra proporcional
            self._fase("anotando")
            try:
                anotada = anotaciones.anotar(rgb_mtf, "static/resultado_apilado.fits")
                if anotada is not None:
                    rgb_mtf = anotada
                    cv2.imwrite("static/scopepower_objetos.jpg", cv2.cvtColor(anotada, cv2.COLOR_RGB2BGR))
                else:
                    print("⚠️ No se pudo resolver el campo: vista previa sin anotaciones.")
            except Exception as e:
                print(f"⚠️ Error anotando objetos ({e}): vista previa sin anotaciones.")

        imagen_bgr = cv2.cvtColor(rgb_mtf, cv2.COLOR_RGB2BGR)

        # Recorte de zoom (config.ini)
        nuevo_config = configparser.ConfigParser()
        nuevo_config.read("config.ini")
        zoom = int(nuevo_config["Dispositivos"].get("zoom_preview", 0))
        if zoom > 0:
            h, w = imagen_bgr.shape[:2]
            margen_h = int(h * zoom / 100 / 2)
            margen_w = int(w * zoom / 100 / 2)
            imagen_bgr = imagen_bgr[margen_h:h - margen_h, margen_w:w - margen_w]

        # Marco decorativo con texto (versión de junio, frame_and_text.py): nombre del
        # objeto, fotos y tiempo. Se omite con zoom muy alto. Si falla, texto simple.
        if zoom <= 80:
            obj_name = "Objeto"
            info_path = ruta_absoluta("static/info_objeto_goto.txt")
            if os.path.isfile(info_path):
                with open(info_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("Nombre:"):
                            obj_name = line.split(":", 1)[1].strip() or obj_name
                            break
            texto_apilado = f"{obj_name}  ·  {num_imagenes_preview} fotos, {num_imagenes_preview * EXPOSICION:.0f} s  ·  {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            usar_marco = nuevo_config["Apilado"].get("marco_decorativo", "true").lower() == "true" if "Apilado" in nuevo_config else True
            dibujado = False
            if usar_marco:
                try:
                    from frame_and_text import add_frame_and_text
                    imagen_bgr = add_frame_and_text(imagen_bgr, texto_apilado)
                    dibujado = True
                except Exception as e:
                    print(f"⚠️ Marco decorativo no disponible ({e}); texto simple")
            if not dibujado:
                alto = imagen_bgr.shape[0]
                font_scale = max(0.4, min(round(alto / 800.0, 2), 1.5))
                thickness = max(1, int(font_scale * 2.5))
                font = cv2.FONT_HERSHEY_SIMPLEX
                (t_width, t_height), _ = cv2.getTextSize(texto_apilado, font, font_scale, thickness)
                pos_x = (imagen_bgr.shape[1] - t_width) // 2
                pos_y = imagen_bgr.shape[0] - 10
                cv2.putText(imagen_bgr, texto_apilado, (pos_x + 1, pos_y + 1), font, font_scale, (0, 0, 0), 2)
                cv2.putText(imagen_bgr, texto_apilado, (pos_x, pos_y), font, font_scale, (180, 180, 180), thickness)

        # Guardar JPG final
        self._fase("guardando")
        cv2.imwrite(ruta_absoluta("static/resultado_apilado_preview.jpg"), imagen_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # Tira de evolución: miniatura del primer apilado y luego cada N
        if anotar_evolucion:
            with self.lock:
                self.apilados_realizados += 1
                n_apilado = self.apilados_realizados
            if n_apilado == 1 or n_apilado % INTERVALO_EVOLUCION == 0:
                try:
                    os.makedirs(ruta_absoluta(CARPETA_EVOLUCION), exist_ok=True)
                    h, w = imagen_bgr.shape[:2]
                    mini = cv2.resize(imagen_bgr, (ANCHO_MINIATURA, int(h * ANCHO_MINIATURA / w)),
                                      interpolation=cv2.INTER_AREA)
                    nombre_mini = f"apilado_{n_apilado:04d}.jpg"
                    cv2.imwrite(ruta_absoluta(os.path.join(CARPETA_EVOLUCION, nombre_mini)), mini,
                                [cv2.IMWRITE_JPEG_QUALITY, 75])
                    with self.lock:
                        self.evolucion.append({
                            "n": n_apilado,
                            "fotos": num_imagenes_preview,
                            "segundos": round(num_imagenes_preview * EXPOSICION),
                            "archivo": nombre_mini,
                            "hora": datetime.now().strftime("%H:%M:%S"),
                        })
                except Exception as e:
                    print(f"⚠️ No se pudo guardar la miniatura de evolución: {e}")

        # El JPG ya está en disco: ahora sí, avisar a la web
        with self.lock:
            self.version_preview += 1

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
            self._fase("leyendo")
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


            self.num_max = NUM_MAX
            nombre_fits = os.path.basename(ruta_fits)

            # ── Calidad del fotograma (sobre el canal verde, antes de alinear) ──
            self._fase("puntuando")
            calidad = leer_config_calidad()
            if calidad["metrica"] == "photutils":
                puntuacion, n_estrellas, nitidez = calcular_puntuacion_photutils(g, calidad["fwhm_px"])
            else:
                puntuacion, n_estrellas, nitidez = calcular_puntuacion(g)
            with self.lock:
                self.ultima_puntuacion = round(puntuacion, 3)
                self.ultima_captura = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"⭐ {nombre_fits}: {n_estrellas} estrellas, nitidez {nitidez:.3f}, puntuación {puntuacion:.3f}")

            if n_estrellas == 0:
                self._descartar(f"{nombre_fits}: sin estrellas detectables (¿nubes, tapa, desenfoque?)",
                                aviso="Foto descartada: no se detectan estrellas")
                return

            # ── Umbral relativo (regla de la versión de junio): se guardan las
            #    mejores `top_para_umbral` puntuaciones vistas; una foto por debajo
            #    de umbral_relativo × su media se descarta sin alinear. Las primeras
            #    fotos entran siempre (el umbral se forma con ellas).
            with self.lock:
                if len(self.mejores_puntuaciones) < calidad["top_para_umbral"]:
                    self.mejores_puntuaciones.append(puntuacion)
                elif puntuacion > min(self.mejores_puntuaciones):
                    self.mejores_puntuaciones[self.mejores_puntuaciones.index(min(self.mejores_puntuaciones))] = puntuacion
                self.umbral = calidad["umbral_relativo"] * (sum(self.mejores_puntuaciones) / len(self.mejores_puntuaciones))
                umbral = self.umbral
            if puntuacion < umbral:
                with self.lock:
                    self.descartadas_calidad += 1
                    self.ultimo_mensaje = (f"{nombre_fits} ({puntuacion:.2f}) por debajo del umbral "
                                           f"({umbral:.2f}, {calidad['umbral_relativo']:.0%} de la media de las mejores); descartada")
                print(f"ℹ️ {self.ultimo_mensaje}")
                voz.decir("Foto descartada por calidad")
                return

            self._fase("resolviendo")
            # ── Referencia: el primer fotograma que ASTAP consiga resolver ──
            if self.ref_info is None:
                ref_mono = ruta_fits.replace(".fits", "_mono.fits")
                ref_ini = ref_mono.replace(".fits", ".ini")
                try:
                    generar_fits_monocromo_para_astap(ruta_fits, ref_mono)
                    subprocess.run(f'"{ruta_astap}" -f "{ref_mono}" -solve -r 50',
                                   shell=True, capture_output=True, timeout=ASTAP_TIMEOUT)
                    if ini_resuelto(ref_ini):
                        self.ref_info = parse_ini_wcs(ref_ini)
                        self.imagen_referencia = nombre_fits
                        print(f"📌 Referencia de alineado: {nombre_fits}")
                except subprocess.TimeoutExpired:
                    print(f"⏱️ ASTAP no terminó en {ASTAP_TIMEOUT} s con la referencia")
                except Exception as e:
                    print(f"⚠️ Error resolviendo la referencia: {e}")
                finally:
                    for fichero in (ref_mono, ref_ini, ref_mono.replace(".fits", ".wcs")):
                        try:
                            os.remove(fichero)
                        except Exception:
                            pass

                if self.ref_info is None:
                    self._descartar(f"{nombre_fits}: ASTAP no pudo resolver la referencia; se probará con el siguiente",
                                    aviso="No se ha podido resolver la imagen de referencia. Se intentará con la siguiente")
                    return
                voz.decir("Imagen de referencia establecida")
                # El fotograma de referencia no necesita transformación
                M, metodo = np.eye(2, 3, dtype=np.float32), "REF"
            else:
                M, metodo = compute_astap_transform(ruta_fits, self.ref_info, self.ruta_astap)
                if M is None:
                    self._descartar(f"{nombre_fits}: {metodo}", aviso="Foto descartada: no se ha podido alinear")
                    return
                # Desplazamiento real de esta foto respecto a la referencia (px):
                # la rotación es alrededor del centro, así que el centro solo se traslada
                cx, cy = self.ref_info["crpix"]
                dx = float(M[0, 0] * cx + M[0, 1] * cy + M[0, 2] - cx)
                dy = float(M[1, 0] * cx + M[1, 1] * cy + M[1, 2] - cy)
                with self.lock:
                    self.desplazamientos.append((round(dx, 1), round(dy, 1)))
                    if len(self.desplazamientos) > 200:
                        self.desplazamientos.pop(0)

            if metodo != "REF":
                self._fase("alineando")
                r = self.aplicar_transformacion(r, M, metodo)
                g = self.aplicar_transformacion(g, M, metodo)
                b = self.aplicar_transformacion(b, M, metodo)

            # ── Selección de los mejores NUM_MAX fotogramas ──
            fotograma = {
                "nombre": nombre_fits,
                "puntuacion": puntuacion,
                "r": np.clip(r, 0, 65535).astype(np.uint16),
                "g": np.clip(g, 0, 65535).astype(np.uint16),
                "b": np.clip(b, 0, 65535).astype(np.uint16),
            }
            del r, g, b

            with self.lock:
                if len(self.fotogramas) < NUM_MAX:
                    self.fotogramas.append(fotograma)
                    self.ultimo_mensaje = f"{nombre_fits} añadido ({len(self.fotogramas)}/{NUM_MAX})"
                else:
                    idx_peor = min(range(len(self.fotogramas)), key=lambda i: self.fotogramas[i]["puntuacion"])
                    peor = self.fotogramas[idx_peor]
                    if puntuacion > peor["puntuacion"]:
                        self.fotogramas[idx_peor] = fotograma
                        self.ultimo_mensaje = (f"{nombre_fits} ({puntuacion:.2f}) sustituye a "
                                               f"{peor['nombre']} ({peor['puntuacion']:.2f})")
                    else:
                        self.descartadas_calidad += 1
                        self.ultimo_mensaje = (f"{nombre_fits} ({puntuacion:.2f}) no mejora al peor "
                                               f"almacenado ({peor['puntuacion']:.2f}); descartado")
                        print(f"ℹ️ {self.ultimo_mensaje}")
                        voz.decir("Foto descartada por calidad")
                        return
                self.total = len(self.fotogramas)
                fotogramas_actuales = list(self.fotogramas)
                sustituida = "sustituye" in self.ultimo_mensaje
            print(f"📚 {self.ultimo_mensaje}")
            voz.decir("Foto sustituye a una peor" if sustituida else f"Foto añadida, {self.total} apiladas")

            # ── Apilado (sigma-clipping por bloques, memoria acotada) ──
            self._fase("apilando")
            r = apilar_sigma_clip([f["r"] for f in fotogramas_actuales])
            g = apilar_sigma_clip([f["g"] for f in fotogramas_actuales])
            b = apilar_sigma_clip([f["b"] for f in fotogramas_actuales])


            rgb = np.stack([r, g, b], axis=0)
            num_imagenes_preview = self.total


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



            self.generar_preview(rgb_cube, num_imagenes_preview, EXPOSICION, ObjetosAnotados)



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
            import traceback
            traceback.print_exc()
            self._descartar(f"Error procesando {os.path.basename(ruta_fits)}: {e}")



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
            self.apilador.procesando = True
            try:
                self.apilador.procesar_imagen_fits(event.src_path, ObjetosAnotados=OBJETOS_ANOTADOS)
            finally:
                self.apilador.procesando = False
                self.apilador.fase = ""


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
            apilador_global.ref_info = None
            apilador_global.total = 0
            apilador_global.ultima_captura = ""
            apilador_global.inicio_apilado = datetime.now()
            apilador_global.fotogramas.clear()
            apilador_global.descartadas = 0
            apilador_global.descartadas_calidad = 0
            apilador_global.ultimo_error = ""
            apilador_global.ultimo_mensaje = ""
            apilador_global.ultima_puntuacion = None
            apilador_global.apilados_realizados = 0
            apilador_global.evolucion.clear()
            apilador_global.desplazamientos.clear()
            apilador_global.mejores_puntuaciones.clear()
            apilador_global.umbral = None
            apilador_global.version_preview += 1   # la web debe olvidar la imagen anterior

            # Miniaturas de la tira de evolución del apilado anterior
            carpeta_evo = ruta_absoluta(CARPETA_EVOLUCION)
            if os.path.isdir(carpeta_evo):
                for nombre in os.listdir(carpeta_evo):
                    if nombre.lower().endswith(".jpg"):
                        try:
                            os.remove(os.path.join(carpeta_evo, nombre))
                        except Exception as e:
                            print(f"⚠️ No se pudo eliminar {nombre}: {e}")

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


def reprocesar_preview():
    """
    Regenera la vista previa a partir del FITS apilado que hay en disco con los
    ajustes de procesado actuales (sin capturar nada). Devuelve (ok, mensaje).
    """
    a = apilador_global
    ruta = "static/resultado_apilado.fits"
    if a is None or not os.path.exists(ruta):
        return False, "No hay ningún apilado que reprocesar"
    if a.procesando:
        return False, "El apilador está ocupado; espera a que termine la foto actual"
    try:
        with fits.open(ruta) as hdul:
            cubo = hdul[0].data.astype(np.float32)
            hdr = hdul[0].header
        n = int(hdr.get("NSTACK", max(1, a.total)))
        exptot = float(hdr.get("EXPTOT", 0.0))
        exposicion = exptot / n if n and exptot else 0.0
        a.procesando = True
        try:
            a.generar_preview(cubo, n, exposicion, ObjetosAnotados=False, anotar_evolucion=False)
        finally:
            a.procesando = False
            a.fase = ""
        return True, "Vista previa regenerada"
    except Exception as e:
        print(f"❌ Error reprocesando la vista previa: {e}")
        return False, f"Error: {e}"


def obtener_estado_apilado():
    """Estado del apilado para la web (dict serializable a JSON)."""
    a = apilador_global
    if not a:
        return {"total": 0, "ultima": "Ninguna", "maximo": 0, "descartadas": 0,
                "descartadas_calidad": 0, "ultimo_error": "", "mensaje": "",
                "referencia": False, "procesando": False,
                "puntuacion_ultima": None, "puntuacion_minima": None,
                "version": 0, "evolucion": []}
    with a.lock:
        puntuaciones = [f["puntuacion"] for f in a.fotogramas]
        return {
            "total": a.total,
            "ultima": a.ultima_captura or "Ninguna",
            "maximo": a.num_max,
            "descartadas": a.descartadas,
            "descartadas_calidad": a.descartadas_calidad,
            "ultimo_error": a.ultimo_error,
            "mensaje": a.ultimo_mensaje,
            "referencia": a.ref_info is not None,
            "procesando": a.procesando,
            "fase": a.fase,
            "puntuacion_ultima": a.ultima_puntuacion,
            "puntuacion_minima": round(min(puntuaciones), 3) if puntuaciones else None,
            "version": a.version_preview,
            "evolucion": list(a.evolucion),
            "desplazamiento_ultimo": a.desplazamientos[-1] if a.desplazamientos else None,
            "desplazamiento_std": (
                [round(float(np.std([d[0] for d in a.desplazamientos])), 1),
                 round(float(np.std([d[1] for d in a.desplazamientos])), 1)]
                if len(a.desplazamientos) >= 2 else None),
            "desplazamientos_n": len(a.desplazamientos),
            "umbral": round(a.umbral, 3) if a.umbral is not None else None,
            "metrica": leer_config_calidad()["metrica"],
        }

