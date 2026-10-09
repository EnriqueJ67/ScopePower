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
import matplotlib.colors as mcolors
import configparser
from datetime import datetime
import shutil
from collections import deque
from AutoStrechSTF import debayer_rgb
import anotaciones
import voz
import humor
import procesado
import firma
import rotacion_camara
import rastros
import apilado_incremental
import cv2

import subprocess
from astropy.io.fits import Header, PrimaryHDU
from astropy.wcs import WCS
from math import atan2, degrees
import threading
from ftplib import FTP
import configparser
import os
import config_inicial

cfg = configparser.ConfigParser()
cfg.read("config.ini")
# Ruta de ASTAP: se lee en cada uso (config_inicial.ruta_astap) para que una instalación
# hecha desde el asistente de configuración valga sin reiniciar

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
        rotacion_camara.anotar_ini(ini, "apilado")   # el giro de la cámara, para el planetario

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


def leer_num_max():
    """[Dispositivos] numero_maximo_imagenes: se relee en cada foto, así se puede
    cambiar desde la pantalla de apilado sin parar el bucle."""
    c = configparser.ConfigParser()
    c.read("config.ini")
    try:
        return max(1, int(c["Dispositivos"].get("numero_maximo_imagenes", 20)))
    except (KeyError, ValueError):
        return 20


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
        # Rastros de satélites/aviones y luces parásitas (rastros.py): la foto
        # se puntúa × penalizacion_defectos y no entra en el apilado
        "detectar_rastros": str(c.get("detectar_rastros", "true")).lower() in ("1", "true", "si", "sí", "yes"),
        "detectar_luces": str(c.get("detectar_luces", "true")).lower() in ("1", "true", "si", "sí", "yes"),
        "penalizacion_defectos": float(c.get("penalizacion_defectos", 0.1)),
        # Las fotos descartadas por rastro se guardan aparte (static/fotos/rastros)
        # para revisar si alguno era un meteoro; como mucho max_rastros
        "guardar_rastros": str(c.get("guardar_rastros", "true")).lower() in ("1", "true", "si", "sí", "yes"),
        "max_rastros": int(c.get("max_rastros", 50)),
        # Cómo se combinan las fotos: "mejores" (sigma-clip de las N mejores, se
        # reapilan todas con cada foto) o "incremental" (las primeras
        # fotos_referencia fijan la referencia y las demás se comparan con ella y
        # se suman: coste fijo por foto y sin tope; ver apilado_incremental.py)
        "modo": "incremental" if str(c.get("modo_apilado", "mejores")).strip().lower() == "incremental" else "mejores",
        "fotos_referencia": max(apilado_incremental.MIN_FOTOS_REFERENCIA, int(c.get("fotos_referencia", 10))),
        "sigma_incremental": float(c.get("sigma_incremental", 3.0)),
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


def esquinas_transformadas(M, ancho, alto):
    """Esquinas (4×2, px) que ocupa una foto de ancho×alto una vez alineada con la
    matriz afín M (la misma que usa warpAffine, que mapea foto → referencia)."""
    esquinas = np.array([[0, 0], [ancho, 0], [ancho, alto], [0, alto]], dtype=np.float64)
    return esquinas @ M[:, :2].T + M[:, 2]


def zona_comun(poligonos, ancho, alto, escala=8, margen=2):
    """
    Rectángulo (x0, y0, ancho, alto) cubierto por TODAS las fotos alineadas, para
    recortar los bordes donde solo hay unas pocas (rotación de campo de la montura
    altacimutal, deriva del dithering). Se divide el campo en celdas de `escala` px;
    una celda vale si sus cuatro esquinas caen dentro de todos los polígonos (que
    son convexos, así que la celda entera está dentro). Luego se encoge el
    rectángulo por el lado menos cubierto hasta que queda entero dentro. `margen`
    píxeles más en los lados que no tocan el borde de la imagen, por la
    interpolación del warpAffine. Devuelve None si no hay nada que recortar.
    """
    try:
        if not poligonos:
            return None
        h, w = int(np.ceil(alto / escala)), int(np.ceil(ancho / escala))
        # Esquinas de las celdas: malla (h+1) × (w+1) de puntos en px
        ys, xs = np.mgrid[0:h + 1, 0:w + 1]
        px, py = np.minimum(xs * escala, ancho).astype(np.float64), np.minimum(ys * escala, alto).astype(np.float64)
        dentro = np.ones((h + 1, w + 1), dtype=bool)
        for pol in poligonos:
            pol = np.asarray(pol, dtype=np.float64)
            signos = []
            for k in range(len(pol)):
                (ax, ay), (bx, by) = pol[k], pol[(k + 1) % len(pol)]
                signos.append((bx - ax) * (py - ay) - (by - ay) * (px - ax))
            signos = np.stack(signos)
            dentro &= (signos >= -1e-6).all(axis=0) | (signos <= 1e-6).all(axis=0)
        mascara = dentro[:-1, :-1] & dentro[:-1, 1:] & dentro[1:, :-1] & dentro[1:, 1:]
        return rectangulo_cubierto(mascara, ancho, alto, escala, margen)
    except Exception as e:
        print(f"⚠️ No se pudo calcular la zona común del apilado: {e}")
        return None


def zona_cobertura(cuenta, minimo, escala=8, margen=2):
    """
    Como zona_comun, para el apilado incremental: rectángulo (x0, y0, ancho, alto)
    donde cada píxel tiene al menos `minimo` fotos sumadas (`cuenta` es el mapa de
    fotos por píxel). Con cientos de fotos y rotación de campo la zona cubierta por
    todas se queda en nada; con la mitad de las fotos el ruido solo sube un 40 %.
    """
    try:
        alto, ancho = cuenta.shape
        h, w = int(np.ceil(alto / escala)), int(np.ceil(ancho / escala))
        relleno = np.pad(cuenta, ((0, h * escala - alto), (0, w * escala - ancho)), mode="edge")
        mascara = relleno.reshape(h, escala, w, escala).min(axis=(1, 3)) >= minimo
        return rectangulo_cubierto(mascara, ancho, alto, escala, margen)
    except Exception as e:
        print(f"⚠️ No se pudo calcular la zona cubierta del apilado: {e}")
        return None


def rectangulo_cubierto(mascara, ancho, alto, escala, margen):
    """
    Rectángulo (x0, y0, ancho, alto) en píxeles dentro de una máscara de celdas de
    `escala` px (True = celda cubierta): se parte del que encierra todas las celdas
    cubiertas y se encoge por el lado menos cubierto hasta que queda entero dentro.
    None si no hay nada que recortar o el resultado es absurdo.
    """
    fil, col = np.nonzero(mascara)
    if len(fil) == 0:
        return None
    y0, y1, x0, x1 = int(fil.min()), int(fil.max()) + 1, int(col.min()), int(col.max()) + 1
    while y1 - y0 > 2 and x1 - x0 > 2:
        sub = mascara[y0:y1, x0:x1]
        if sub.all():
            break
        bordes = {"arriba": sub[0].mean(), "abajo": sub[-1].mean(), "izq": sub[:, 0].mean(), "der": sub[:, -1].mean()}
        peor = min(bordes, key=bordes.get)
        if bordes[peor] >= 1.0:
            break   # los cuatro bordes enteros: en una región convexa el interior también
        if peor == "arriba": y0 += 1
        elif peor == "abajo": y1 -= 1
        elif peor == "izq": x0 += 1
        else: x1 -= 1
    X0, Y0 = x0 * escala, y0 * escala
    X1, Y1 = min(ancho, x1 * escala), min(alto, y1 * escala)
    if X0 > 0: X0 += margen
    if Y0 > 0: Y0 += margen
    if X1 < ancho: X1 -= margen
    if Y1 < alto: Y1 -= margen
    if X1 - X0 < ancho // 2 or Y1 - Y0 < alto // 2:
        return None   # algo raro (una foto muy desplazada): mejor no recortar
    if X0 == 0 and Y0 == 0 and X1 == ancho and Y1 == alto:
        return None
    return (int(X0), int(Y0), int(X1 - X0), int(Y1 - Y0))


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


def nombre_objeto_goto():
    """Nombre del objeto del último GOTO (static/info_objeto_goto.txt) o 'Objeto'."""
    nombre = "Objeto"
    info_path = ruta_absoluta("static/info_objeto_goto.txt")
    if os.path.isfile(info_path):
        with open(info_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.lower().startswith("nombre:"):
                    nombre = line.split(":", 1)[1].strip() or nombre
                    break
    return nombre


def tipo_objeto_goto(nombre=None):
    """
    Tipo del objeto del último GOTO reducido a las familias de la galería (galaxia,
    nebulosa, cúmulo abierto...): de la línea «Tipo:» de info_objeto_goto.txt y, si
    no la hay (Sol, Luna, planetas), del nombre. "" si no se sabe.
    """
    import catalogo_offline
    tipo = ""
    info_path = ruta_absoluta("static/info_objeto_goto.txt")
    if os.path.isfile(info_path):
        with open(info_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.lower().startswith("tipo:"):
                    tipo = line.split(":", 1)[1].strip()
                    break
    return catalogo_offline.categoria_objeto(tipo, nombre or nombre_objeto_goto())


def metadatos_firma(cfg=None, ruta_fits=None, n_fotos=0, exposicion=0):
    """
    Datos para firmar el apilado (firma.py): autor, lugar e interruptor del config,
    objeto y tipo del último GOTO, coordenadas de la cabecera del FITS, fotos y exposición.
    """
    meta = firma.datos_observatorio(cfg)
    objeto = nombre_objeto_goto()
    meta.update({"objeto": objeto, "tipo": tipo_objeto_goto(objeto), "fecha": datetime.now(),
                 "n_fotos": n_fotos, "exposicion": exposicion})
    if ruta_fits and os.path.isfile(ruta_fits):
        try:
            hdr = fits.getheader(ruta_fits)
            meta["ra"], meta["dec"] = hdr.get("OBJCTRA"), hdr.get("OBJCTDEC")
        except Exception as e:
            print(f"⚠️ Sin coordenadas para la firma ({e})")
    return meta


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


# Fases del procesado de cada foto, en el orden en que las recorre el apilador,
# y una duración orientativa de cada una (segundos en el mini PC de campo) para
# la cuenta atrás de la web mientras no hay historial de la sesión.
FASES = ("leyendo", "puntuando", "resolviendo", "alineando", "apilando", "estirando", "anotando", "guardando")
DURACION_SEMILLA = {"leyendo": 1.0, "puntuando": 1.0, "resolviendo": 10.0, "alineando": 2.0,
                    "apilando": 3.0, "estirando": 8.0, "anotando": 3.0, "guardando": 1.0}


class ApiladorEnTiempoReal:
    @property
    def ruta_astap(self):
        return config_inicial.ruta_astap()

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

        # Fotogramas que forman el apilado. Cada elemento es un dict con los
        # planos alineados en uint16 ("r","g","b"), su "puntuacion" y "nombre".
        # Se conservan como máximo NUM_MAX; cuando llega uno mejor que el peor
        # almacenado lo sustituye. uint16 en vez de float32 = la mitad de RAM.
        self.fotogramas = []
        self.num_max = 0
        # Apilado incremental (apilado_incremental.py): None mientras se reúnen las
        # fotos de referencia; después, suma/cuenta/sigma por píxel, y en
        # self.fotogramas solo quedan los datos de cada foto, sin sus planos.
        self.acumulador = None
        self.modo = "mejores"
        self.fotos_referencia = 0

        # Estado visible desde la web
        self.descartadas = 0           # sin resolver / error
        self.descartadas_calidad = 0   # peores que las N guardadas
        self.descartadas_rastros = 0   # con rastro de satélite/avión o luz parásita
        # Fondo por zonas de las últimas fotos (para ver si se enciende una luz)
        self.historial_fondo = []
        self.exposicion_fondo = None
        self.ultimo_error = ""
        self.ultimo_mensaje = ""
        self.ultima_puntuacion = None
        # Detalle de la última foto medida (para el panel de estadísticas de la web):
        # nombre, hora, puntuación, estrellas, nitidez, fondo, desplazamiento y qué pasó con ella
        self.ultima_detalle = None
        self.dimensiones = None   # (ancho, alto) del apilado en píxeles
        # Zona (x0, y0, ancho, alto) cubierta por todas las fotos alineadas; la vista
        # previa y el JPG de la galería se recortan a ella (rotación de campo)
        self.zona_comun = None
        # Encuadre con que se hizo la última vista previa (procesado.encuadre_vista): giro, espejo, norte
        self.encuadre = None
        self.procesando = False
        self.fase = ""   # paso actual del procesado (para la barra de progreso de la web)
        # Progreso fino y tiempo restante: sub-fase dentro de la fase (p. ej.
        # "ruido" dentro de "estirando"), fracción 0..1 de la fase, instante en
        # que empezó la fase y la foto, y las últimas duraciones de cada fase
        # para estimar cuánto queda (la primera foto usa DURACION_SEMILLA).
        self.subfase = ""
        self.progreso_fase = 0.0
        self.fase_inicio = None
        self.foto_inicio = None
        self.duraciones = {f: deque(maxlen=5) for f in FASES}
        self.duracion_ultima_foto = None

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
        ahora = time.time()
        with self.lock:
            self._cerrar_fase(ahora)
            if not self.fase:
                self.foto_inicio = ahora
            self.fase = nombre
            self.fase_inicio = ahora
            self.subfase = ""
            self.progreso_fase = 0.0

    def _cerrar_fase(self, ahora):
        """Apunta la duración de la fase en curso (llamar con self.lock cogido)."""
        if self.fase and self.fase_inicio is not None and self.fase in self.duraciones:
            self.duraciones[self.fase].append(ahora - self.fase_inicio)

    def terminar_fases(self):
        """Fin del procesado de una foto (la web deja de ver fase y cuenta atrás)."""
        ahora = time.time()
        with self.lock:
            self._cerrar_fase(ahora)
            if self.foto_inicio is not None:
                self.duracion_ultima_foto = ahora - self.foto_inicio
            self.fase = ""
            self.subfase = ""
            self.progreso_fase = 0.0
            self.fase_inicio = None
            self.foto_inicio = None

    def _subfase(self, nombre, fraccion):
        with self.lock:
            self.subfase = nombre
            self.progreso_fase = float(min(max(fraccion, 0.0), 1.0))

    def _duracion_tipica(self, fase):
        """Mediana de las últimas duraciones de la fase, o la semilla si aún no hay."""
        d = self.duraciones.get(fase)
        if d:
            return float(np.median(d))
        return DURACION_SEMILLA.get(fase, 0.0)

    def _eta(self, ahora):
        """
        Segundos que faltan para ver la foto en la web (llamar con self.lock
        cogido): lo que queda de la fase actual más las fases que vienen detrás.
        None si no se está procesando.
        """
        if not self.procesando or not self.fase or self.fase_inicio is None:
            return None
        transcurrido = ahora - self.fase_inicio
        tipica = self._duracion_tipica(self.fase)
        if self.progreso_fase > 0.05:
            # Dentro de la fase se sabe la fracción hecha: la proyección con el
            # ritmo real es mejor que la mediana histórica
            proyeccion = transcurrido / self.progreso_fase
            tipica = min(tipica, proyeccion) if tipica else proyeccion
        restante = max(0.0, tipica - transcurrido)
        if self.fase in FASES:
            for f in FASES[FASES.index(self.fase) + 1:]:
                if f == "anotando" and not self.duraciones["anotando"]:
                    continue    # fase opcional: solo cuenta si ya se ha hecho alguna vez
                restante += self._duracion_tipica(f)
        return restante

    def _anotar_resultado(self, resultado, motivo=""):
        """Qué ha pasado con la última foto medida (llamar con self.lock cogido)."""
        if self.ultima_detalle is not None:
            self.ultima_detalle["resultado"] = resultado
            self.ultima_detalle["motivo"] = motivo

    def _descartar(self, motivo, aviso="Foto descartada"):
        with self.lock:
            self.descartadas += 1
            self.ultimo_error = motivo
            self._anotar_resultado("descartada", motivo)
        print(f"⚠️ Fotograma descartado: {motivo}")
        voz.decir(aviso)

    def _buscar_defectos(self, g, exposicion, calidad):
        """
        Rastro de satélite/avión/meteoro o luz parásita en el plano verde (rastros.py).
        Devuelve (texto para la web, aviso de voz, rastro, clase) o None si la foto
        está limpia; rastro y clase son None si es una luz.
        """
        try:
            peq = rastros.media_resolucion(g)
            if calidad["detectar_luces"]:
                # El historial se compara a igual exposición: si cambia, se empieza de nuevo
                if exposicion != self.exposicion_fondo:
                    self.historial_fondo = []
                    self.exposicion_fondo = exposicion
                rejilla = rastros.rejilla_fondo(peq)
                _, ruido = rastros.mediana_mad(peq)
                luz = rastros.detectar_luz(rejilla, self.historial_fondo, ruido)
                self.historial_fondo = (self.historial_fondo + [rejilla])[-rastros.FOTOS_HISTORIAL:]
                if luz:
                    return (f"luz parásita (fondo +{luz['subida']:.1f} σ en el {luz['zona']:.0%} de la foto)",
                            "Foto descartada: luz parásita", None, None)
            if calidad["detectar_rastros"]:
                rastro = rastros.detectar_rastro(g, peq)
                if rastro:
                    try:
                        clase = rastros.clasificar_rastro(peq, rastro)
                    except Exception as e:
                        print(f"⚠️ No se pudo clasificar el rastro: {e}")
                        clase = dict(rastros.TIPOS_RASTRO["satelite"], tipo="satelite", rasgos={})
                    longitud = (clase.get("rasgos") or {}).get("longitud", rastro["longitud"])
                    if clase["tipo"] == "meteoro":
                        texto, aviso = "posible meteoro", "¡Posible meteoro!"
                    else:
                        texto = f"rastro de {clase['nombre'].lower()}"
                        aviso = f"Foto descartada: rastro de {clase['nombre'].lower()}"
                    return (f"{clase['icono']} {texto} ({longitud} px a {rastro['angulo']:.0f}°)",
                            aviso, rastro, clase)
        except Exception as e:
            print(f"⚠️ No se pudieron buscar rastros ni luces: {e}")
        return None

    def _guardar_rastro(self, ruta_fits, r, g, b, rastro, clase, exposicion, maximo):
        """
        Guarda aparte la foto descartada por rastro (rastros.guardar_rastro). El FITS
        (~18 MB) solo si queda espacio en el disco; la miniatura y el JSON siempre.
        Devuelve el nombre base o None si falla.
        """
        try:
            import espacio_disco
            disco = espacio_disco.vigilante.estado()
            copiar_fits = not (disco.get("ok") and disco.get("bajo"))
            datos = {"hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "objeto": nombre_objeto_goto(), "exposicion": exposicion}
            base = rastros.guardar_rastro(ruta_fits, r, g, b, rastro, clase, datos,
                                          copiar_fits=copiar_fits, maximo=maximo,
                                          carpeta=ruta_absoluta(rastros.CARPETA_RASTROS))
            print(f"💾 Rastro guardado: {base} ({clase['nombre']}{'' if copiar_fits else ', sin FITS: poco disco'})")
            return base
        except Exception as e:
            print(f"⚠️ No se pudo guardar la foto con rastro: {e}")
            return None

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
            NUM_MAX = leer_num_max()

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
                        anotar_evolucion=True, ruta_fits=None):
        """
        Del cubo lineal (3, H, W) al JPG de vista previa: recorte de zoom y giro,
        procesado estilo Seestar (procesado.py), texto, miniatura de la tira de
        evolución y aviso a la web (versión). ruta_fits: FITS del que sacar las
        coordenadas para la firma.
        """
        self._fase("estirando")
        nuevo_config = configparser.ConfigParser()
        nuevo_config.read("config.ini")
        # Recorte de zoom (zoom_preview en config.ini) sobre el cubo lineal, ANTES
        # de procesar. El apilado y el FITS siguen a campo completo. Con el campo
        # completo (o el 75 %) se procesa a media resolución (la web muestra ≤600 px);
        # con el 50 % o menos, a resolución completa: mismo coste que el campo
        # entero a media resolución y el doble de detalle en los objetos pequeños.
        # El giro y el espejo del encuadre (a mano o "norte arriba", que sale de la
        # cabecera del FITS apilado) van en el mismo paso.
        try:
            cabecera = fits.getheader("static/resultado_apilado.fits")
        except Exception:
            cabecera = None
        encuadre = procesado.encuadre_vista(nuevo_config, cabecera)
        fraccion = encuadre["fraccion"]
        with self.lock:
            zona = self.zona_comun
            self.encuadre = encuadre
        # El color se calibra con las estrellas del campo completo, antes del recorte de zoom
        balance = procesado.balance_estrellas(rgb_cube, zona)
        rgb_cube, matriz = procesado.girar_campo(rgb_cube, fraccion, encuadre["giro"], zona, espejo=encuadre["espejo"])
        escala = 0.5 if fraccion > 0.5 else 1.0
        rgb_mtf = procesado.procesar(rgb_cube, num_imagenes_preview, procesado.leer_config(), escala=escala,
                                     progreso=self._subfase, balance=balance)
        if ObjetosAnotados:
            # Objetos del campo dibujados SOBRE la imagen procesada (anotaciones.py):
            # catálogo local sin Internet, SIMBAD si hay red, letra proporcional
            self._fase("anotando")
            try:
                # Del FITS a la vista previa: el encuadre y la reducción del procesado
                matriz_vista = matriz * (rgb_mtf.shape[1] / float(rgb_cube.shape[2]))
                anotada = anotaciones.anotar(rgb_mtf, "static/resultado_apilado.fits", matriz=matriz_vista)
                if anotada is not None:
                    rgb_mtf = anotada
                    cv2.imwrite("static/scopepower_objetos.jpg", cv2.cvtColor(anotada, cv2.COLOR_RGB2BGR))
                else:
                    print("⚠️ No se pudo resolver el campo: vista previa sin anotaciones.")
            except Exception as e:
                print(f"⚠️ Error anotando objetos ({e}): vista previa sin anotaciones.")

        imagen_bgr = cv2.cvtColor(rgb_mtf, cv2.COLOR_RGB2BGR)

        # Firma (firma.py): logo, autor, lugar y fecha a la izquierda; objeto, tiempo
        # de integración y coordenadas a la derecha. Se desactiva con firma = false.
        try:
            meta_firma = metadatos_firma(nuevo_config, ruta_fits, num_imagenes_preview, EXPOSICION)
            if meta_firma["activa"]:
                imagen_bgr = firma.firmar(imagen_bgr, meta_firma)
        except Exception as e:
            print(f"⚠️ No se pudo firmar la vista previa: {e}")

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

    def _publicar_apilado(self, planos, zona, ruta_fits, EXPOSICION, ObjetosAnotados):
        """
        Del apilado ya calculado (planos r, g, b float32 y su zona cubierta) al FITS
        de static/, la vista previa de la web, el texto de información y el FTP.
        """
        num_imagenes_preview = self.total
        with self.lock:
            self.zona_comun = zona
        if zona:
            print(f"✂️ Zona común de las {num_imagenes_preview} fotos: {zona[2]}×{zona[3]} px desde ({zona[0]}, {zona[1]})")

        # Usar la cabecera del último FITS como base
        with fits.open(ruta_fits) as hdul:
            header_original = hdul[0].header.copy()

        # Modificar cabecera con datos del apilado
        header_original['NSTACK'] = (num_imagenes_preview, 'Numero de imagenes apiladas')
        header_original['EXPTOT'] = (num_imagenes_preview * EXPOSICION, 'Tiempo total de exposicion (s)')
        header_original['DATE'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        header_original['CREATOR'] = ('ScopePower', 'Real-time Power for Your Telescope')
        for clave in ("ZONAX0", "ZONAY0", "ZONAW", "ZONAH"):
            header_original.pop(clave, None)
        if zona:
            header_original['ZONAX0'] = (zona[0], 'Zona cubierta por todas las fotos: x0')
            header_original['ZONAY0'] = (zona[1], 'Zona cubierta por todas las fotos: y0')
            header_original['ZONAW'] = (zona[2], 'Zona cubierta por todas las fotos: ancho')
            header_original['ZONAH'] = (zona[3], 'Zona cubierta por todas las fotos: alto')

        # ——— Guardar FITS apilado como RGB (3×H×W) ———
        rgb_cube = np.stack(planos, axis=0).astype(np.float32)  # shape = (3, H, W)
        del planos

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
        # Giro y espejo que dejan el norte arriba y el este a la izquierda, del plate solving
        # de la referencia: los usan la vista previa (norte_arriba = true) y el editor de fotos
        for clave in ("NORTEGIR", "NORTEESP"):
            hdr_out.pop(clave, None)
        wcs_ref = self.ref_info.get("wcs") if self.ref_info else None
        norte = anotaciones.norte_arriba(wcs_ref, rgb_cube.shape[2], rgb_cube.shape[1]) if wcs_ref is not None else None
        if norte:
            hdr_out["NORTEGIR"] = (norte[0], "Giro horario (grados) que deja el norte arriba")
            hdr_out["NORTEESP"] = (bool(norte[1]), "Espejo previo para dejar el este a la izquierda")

        hdu = PrimaryHDU(data=rgb_cube, header=hdr_out)
        hdu.writeto("static/resultado_apilado.fits", overwrite=True)
        with self.lock:
            self.dimensiones = (int(rgb_cube.shape[2]), int(rgb_cube.shape[1]))
        print("✅ FITS apilado guardado en modo RGB.")

        self.generar_preview(rgb_cube, num_imagenes_preview, EXPOSICION, ObjetosAnotados, ruta_fits=ruta_fits)

        duracion = datetime.now() - self.inicio_apilado
        info = f"{num_imagenes_preview} imágenes apiladas\nTiempo de integración total: {num_imagenes_preview * EXPOSICION:.1f} segundos\nDuración del proceso: {duracion}"

        with open("static/apilado_info.txt", "w") as f:
            f.write(info)

        print(f"✅ Imagen apilada actualizada. Total: {self.total}")

        # Subir imagen al FTP en segundo plano (no bloquea el apilado)
        subir_imagen_ftp_async("static/resultado_apilado_preview.jpg", "ultima.jpg")
        subir_imagen_ftp_async("static/info_objeto_goto.txt", "info_objeto_goto.txt")

        # Limpiar imágenes FITS antiguas en disco
        self.limpiar_fits_antiguos()

    def procesar_imagen_fits(self, ruta_fits, ObjetosAnotados=False):
        try:

            # Leer valores actualizados de config.ini
            nuevo_config = configparser.ConfigParser()
            nuevo_config.read("config.ini")
            EXPOSICION = float(nuevo_config["Dispositivos"].get("exposicion_captura", 20))
            NUM_MAX = leer_num_max()
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
            # Fondo de cielo: mediana del verde (ADU de 16 bits), submuestreado para que sea instantáneo
            fondo = float(np.median(g[::8, ::8]))
            # Un rastro de avión o una linterna no bajan las estrellas ni la
            # nitidez (el rastro hasta suma "estrellas"), pero se quedan en el
            # apilado: la puntuación se hunde y la foto no entra
            defecto = self._buscar_defectos(g, EXPOSICION, calidad)
            puntuacion_limpia = puntuacion
            if defecto:
                puntuacion *= calidad["penalizacion_defectos"]
            with self.lock:
                self.ultima_puntuacion = round(puntuacion, 3)
                self.ultima_captura = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.ultima_detalle = {
                    "nombre": nombre_fits, "hora": datetime.now().strftime("%H:%M:%S"),
                    "puntuacion": round(puntuacion, 3), "estrellas": n_estrellas,
                    "nitidez": round(nitidez, 3), "fondo": round(fondo),
                    "exposicion": EXPOSICION, "desplazamiento": None,
                    "resultado": "midiendo", "motivo": "",
                    "defecto": defecto[0] if defecto else "",
                    "puntuacion_sin_defecto": round(puntuacion_limpia, 3) if defecto else None,
                }
            print(f"⭐ {nombre_fits}: {n_estrellas} estrellas, nitidez {nitidez:.3f}, puntuación {puntuacion:.3f}"
                  + (f" ({defecto[0]}; sin él {puntuacion_limpia:.3f})" if defecto else ""))

            if n_estrellas == 0:
                self._descartar(f"{nombre_fits}: sin estrellas detectables (¿nubes, tapa, desenfoque?)",
                                aviso="Foto descartada: no se detectan estrellas")
                return

            # Antes del umbral relativo: la puntuación hundida no debe entrar en
            # la media de las mejores, y las primeras fotos (que entran siempre) también se filtran
            if defecto:
                texto, aviso, rastro, clase = defecto
                if rastro and calidad["guardar_rastros"]:
                    guardado = self._guardar_rastro(ruta_fits, r, g, b, rastro, clase, EXPOSICION, calidad["max_rastros"])
                    if guardado:
                        texto += " · guardada en Rastros"
                        if clase["tipo"] == "meteoro":
                            aviso += " Foto guardada en rastros"
                with self.lock:
                    self.descartadas_rastros += 1
                    self._anotar_resultado("descartada", texto)
                    if self.ultima_detalle is not None:
                        self.ultima_detalle["defecto"] = texto
                    self.ultimo_mensaje = f"{nombre_fits} ({puntuacion:.2f}): {texto}; descartada"
                print(f"ℹ️ {self.ultimo_mensaje}")
                voz.decir(aviso)
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
                    self._anotar_resultado("descartada", f"por debajo del umbral ({umbral:.2f})")
                with self.lock:
                    self.descartadas_calidad += 1
                    self.ultimo_mensaje = (f"{nombre_fits} ({puntuacion:.2f}) por debajo del umbral "
                                           f"({umbral:.2f}, {calidad['umbral_relativo']:.0%} de la media de las mejores); descartada")
                print(f"ℹ️ {self.ultimo_mensaje}")
                voz.decir("Foto descartada por calidad")
                humor.decir("foto_descartada")
                return

            self._fase("resolviendo")
            # ── Referencia: el primer fotograma que ASTAP consiga resolver ──
            if self.ref_info is None:
                ref_mono = ruta_fits.replace(".fits", "_mono.fits")
                ref_ini = ref_mono.replace(".fits", ".ini")
                try:
                    generar_fits_monocromo_para_astap(ruta_fits, ref_mono)
                    subprocess.run(f'"{self.ruta_astap}" -f "{ref_mono}" -solve -r 50',
                                   shell=True, capture_output=True, timeout=ASTAP_TIMEOUT)
                    if ini_resuelto(ref_ini):
                        rotacion_camara.anotar_ini(ref_ini, "apilado")
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
                humor.decir("referencia_fijada")
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
                    if self.ultima_detalle is not None:
                        self.ultima_detalle["desplazamiento"] = [round(dx, 1), round(dy, 1)]
                    if len(self.desplazamientos) > 200:
                        self.desplazamientos.pop(0)

            esquinas = esquinas_transformadas(M, r.shape[1], r.shape[0])

            if metodo != "REF":
                self._fase("alineando")
                r = self.aplicar_transformacion(r, M, metodo)
                g = self.aplicar_transformacion(g, M, metodo)
                b = self.aplicar_transformacion(b, M, metodo)

            fotograma = {
                "nombre": nombre_fits,
                "puntuacion": puntuacion,
                "estrellas": n_estrellas,
                "nitidez": nitidez,
                "fondo": fondo,
                "exposicion": EXPOSICION,
                "esquinas": esquinas,
            }

            # Incremental: con la referencia ya fijada la foto se suma al acumulador
            # (y ya no se puede volver a "las N mejores" hasta reiniciar el apilado);
            # antes, las fotos se guardan como siempre hasta reunir las de referencia
            acumulador = self.acumulador
            incremental = acumulador is not None or calidad["modo"] == "incremental"
            n_referencia = calidad["fotos_referencia"]
            with self.lock:
                self.modo = "incremental" if incremental else "mejores"
                self.fotos_referencia = n_referencia if incremental else 0
            if acumulador is not None:
                self._fase("apilando")
                suma = acumulador.anadir(r, g, b, None if metodo == "REF" else M)
                del r, g, b
                with self.lock:
                    if self.acumulador is not acumulador:
                        return      # el apilado se ha reiniciado mientras tanto
                    self.fotogramas.append(fotograma)
                    self.total = len(self.fotogramas)
                    self.ultimo_mensaje = (f"{nombre_fits} añadido ({self.total} fotos; "
                                           f"{suma['rechazado']:.1%} de píxeles rechazados)")
                    self._anotar_resultado("apilada")
                    if self.ultima_detalle is not None:
                        self.ultima_detalle["rechazado"] = round(100 * suma["rechazado"], 1)
                        self.ultima_detalle["cambio_fondo"] = round(suma["nivel"])
                print(f"📚 {self.ultimo_mensaje}; fondo {suma['nivel']:+.0f} ADU, ruido ×{suma['ruido']:.2f}")
                if suma["rechazado"] > 0.2:
                    print("⚠️ Más del 20 % de la foto no se parece a la referencia (¿nubes, alineado, enfoque?)")
                voz.decir(f"Foto añadida, {self.total} apiladas")
                self._publicar_apilado(acumulador.imagen(), zona_cobertura(*acumulador.cobertura()),
                                       ruta_fits, EXPOSICION, ObjetosAnotados)
                return

            # ── Selección de los mejores NUM_MAX fotogramas ──
            fotograma.update({
                "M": None if metodo == "REF" else M,
                "r": np.clip(r, 0, 65535).astype(np.uint16),
                "g": np.clip(g, 0, 65535).astype(np.uint16),
                "b": np.clip(b, 0, 65535).astype(np.uint16),
            })
            del r, g, b

            with self.lock:
                # El máximo se puede bajar en caliente desde la web: se quitan las
                # peores hasta dejar hueco (o hasta el nuevo límite)
                quitadas = 0
                while not incremental and len(self.fotogramas) > NUM_MAX:
                    idx_peor = min(range(len(self.fotogramas)), key=lambda i: self.fotogramas[i]["puntuacion"])
                    del self.fotogramas[idx_peor]
                    quitadas += 1
                if quitadas:
                    print(f"✂️ Máximo bajado a {NUM_MAX}: {quitadas} foto(s) peores quitadas del apilado")
                entra = True
                if incremental:
                    # La referencia se hace con fotos BUENAS: el umbral de calidad se
                    # forma con las primeras fotos, así que una mala del principio puede
                    # haber entrado; se repasa con el umbral de ahora y se espera a tener
                    # n_referencia que lo superen
                    malas = [f for f in self.fotogramas if f["puntuacion"] < umbral]
                    if malas:
                        self.fotogramas = [f for f in self.fotogramas if f["puntuacion"] >= umbral]
                        self.descartadas_calidad += len(malas)
                        print("✂️ Fuera de la referencia por calidad (umbral {:.2f}): {}".format(
                            umbral, ", ".join(f"{f['nombre']} ({f['puntuacion']:.2f})" for f in malas)))
                    self.fotogramas.append(fotograma)
                    self.ultimo_mensaje = (f"{nombre_fits} añadido (referencia: {len(self.fotogramas)} de "
                                           f"{n_referencia} fotos buenas"
                                           + (f"; {len(malas)} quitada(s) por calidad)" if malas else ")"))
                    self._anotar_resultado("apilada")
                elif len(self.fotogramas) < NUM_MAX:
                    self.fotogramas.append(fotograma)
                    self.ultimo_mensaje = f"{nombre_fits} añadido ({len(self.fotogramas)}/{NUM_MAX})"
                    self._anotar_resultado("apilada")
                else:
                    idx_peor = min(range(len(self.fotogramas)), key=lambda i: self.fotogramas[i]["puntuacion"])
                    peor = self.fotogramas[idx_peor]
                    if puntuacion > peor["puntuacion"]:
                        self.fotogramas[idx_peor] = fotograma
                        self.ultimo_mensaje = (f"{nombre_fits} ({puntuacion:.2f}) sustituye a "
                                               f"{peor['nombre']} ({peor['puntuacion']:.2f})")
                        self._anotar_resultado("sustituye", f"a {peor['nombre']} ({peor['puntuacion']:.2f})")
                    else:
                        entra = False
                        self.descartadas_calidad += 1
                        self.ultimo_mensaje = (f"{nombre_fits} ({puntuacion:.2f}) no mejora al peor "
                                               f"almacenado ({peor['puntuacion']:.2f}); descartado")
                        self._anotar_resultado("descartada", f"no mejora a la peor apilada ({peor['puntuacion']:.2f})")
                self.total = len(self.fotogramas)
                fotogramas_actuales = list(self.fotogramas)
                sustituida = "sustituye" in self.ultimo_mensaje
            if not entra:
                print(f"ℹ️ {self.ultimo_mensaje}")
                voz.decir("Foto descartada por calidad")
                humor.decir("foto_descartada")
                # Sin recorte no cambia nada y no hace falta reapilar; con recorte
                # sí, para que la vista previa refleje las fotos que quedan
                if not quitadas:
                    return
            else:
                print(f"📚 {self.ultimo_mensaje}")
                voz.decir("Foto sustituye a una peor" if sustituida else f"Foto añadida, {self.total} apiladas")
                if sustituida:
                    humor.decir("foto_sustituye")

            self._fase("apilando")
            if incremental and len(fotogramas_actuales) >= n_referencia:
                # Ya hay fotos buenas suficientes: su sigma-clip es la referencia y
                # desde la siguiente foto se suma sin reapilar. Los planos se sueltan.
                acumulador = apilado_incremental.Acumulador(fotogramas_actuales, k=calidad["sigma_incremental"])
                with self.lock:
                    if not any(f is fotograma for f in self.fotogramas):
                        return      # el apilado se ha reiniciado mientras tanto
                    self.acumulador = acumulador
                    for f in self.fotogramas:
                        for clave in ("r", "g", "b", "M"):
                            f.pop(clave, None)
                print(f"📌 Referencia del apilado incremental fijada con {len(fotogramas_actuales)} fotos")
                voz.decir("Referencia del apilado fijada")
                humor.decir("referencia_fijada")
                planos = acumulador.imagen()
                zona = zona_cobertura(*acumulador.cobertura())
            else:
                # ── Apilado (sigma-clipping por bloques, memoria acotada) ──
                planos = tuple(apilar_sigma_clip([f[c] for f in fotogramas_actuales]) for c in ("r", "g", "b"))
                # Zona cubierta por todas las fotos: fuera de ella solo hay unas pocas
                # (rotación de campo, dithering) y se ven bandas en los bordes
                zona = zona_comun([f["esquinas"] for f in fotogramas_actuales if f.get("esquinas") is not None],
                                  planos[0].shape[1], planos[0].shape[0])
            del fotogramas_actuales
            self._publicar_apilado(planos, zona, ruta_fits, EXPOSICION, ObjetosAnotados)

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
                self.apilador.terminar_fases()


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
            apilador_global.acumulador = None
            apilador_global.descartadas = 0
            apilador_global.descartadas_calidad = 0
            apilador_global.descartadas_rastros = 0
            apilador_global.historial_fondo = []
            apilador_global.exposicion_fondo = None
            apilador_global.ultimo_error = ""
            apilador_global.ultimo_mensaje = ""
            apilador_global.ultima_puntuacion = None
            apilador_global.ultima_detalle = None
            apilador_global.dimensiones = None
            apilador_global.zona_comun = None
            apilador_global.encuadre = None
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
        with a.lock:
            a.zona_comun = procesado.leer_zona(hdr)
        a.procesando = True
        try:
            a.generar_preview(cubo, n, exposicion, ObjetosAnotados=False, anotar_evolucion=False, ruta_fits=ruta)
        finally:
            a.procesando = False
            a.terminar_fases()
        return True, "Vista previa regenerada"
    except Exception as e:
        print(f"❌ Error reprocesando la vista previa: {e}")
        return False, f"Error: {e}"


def obtener_estado_apilado():
    """Estado del apilado para la web (dict serializable a JSON)."""
    a = apilador_global
    if not a:
        return {"total": 0, "ultima": "Ninguna", "maximo": 0, "descartadas": 0,
                "descartadas_calidad": 0, "descartadas_rastros": 0, "ultimo_error": "", "mensaje": "",
                "referencia": False, "procesando": False,
                "puntuacion_ultima": None, "puntuacion_minima": None,
                "puntuacion_maxima": None, "puntuacion_mediana": None,
                "version": 0, "evolucion": []}
    with a.lock:
        puntuaciones = [f["puntuacion"] for f in a.fotogramas]
        media = lambda clave: (round(float(np.mean([f[clave] for f in a.fotogramas])), 3)
                               if a.fotogramas else None)
        duracion = datetime.now() - a.inicio_apilado
        ahora = time.time()
        eta = a._eta(ahora)
        return {
            "total": a.total,
            "ultima": a.ultima_captura or "Ninguna",
            "maximo": leer_num_max(),
            # incremental: sin tope de fotos; fotos_referencia las que fijan la
            # referencia y referencia_fijada si ya se está sumando sobre ella
            "modo": a.modo,
            "fotos_referencia": a.fotos_referencia,
            "referencia_fijada": a.acumulador is not None,
            "descartadas": a.descartadas,
            "descartadas_calidad": a.descartadas_calidad,
            "descartadas_rastros": a.descartadas_rastros,
            "ultimo_error": a.ultimo_error,
            "mensaje": a.ultimo_mensaje,
            "referencia": a.ref_info is not None,
            "procesando": a.procesando,
            "fase": a.fase,
            "subfase": a.subfase,
            "progreso_fase": round(a.progreso_fase, 3),
            "fase_transcurrido": round(ahora - a.fase_inicio, 1) if (a.procesando and a.fase_inicio) else None,
            "eta_segundos": (round(eta, 1) if eta is not None else None),
            "duracion_ultima_foto": round(a.duracion_ultima_foto, 1) if a.duracion_ultima_foto else None,
            "puntuacion_ultima": a.ultima_puntuacion,
            "puntuacion_minima": round(min(puntuaciones), 3) if puntuaciones else None,
            "puntuacion_maxima": round(max(puntuaciones), 3) if puntuaciones else None,
            "puntuacion_mediana": round(float(np.median(puntuaciones)), 3) if puntuaciones else None,
            "puntuacion_media": round(float(np.mean(puntuaciones)), 3) if puntuaciones else None,
            "puntuacion_std": round(float(np.std(puntuaciones)), 3) if len(puntuaciones) >= 2 else None,
            # Medias de las fotos que forman el apilado y datos de la sesión
            "estrellas_media": media("estrellas"),
            "nitidez_media": media("nitidez"),
            "fondo_medio": media("fondo"),
            "segundos_totales": round(sum(f.get("exposicion", 0) for f in a.fotogramas), 1),
            "inicio": a.inicio_apilado.strftime("%H:%M:%S") if a.fotogramas else "",
            "duracion_min": int(duracion.total_seconds() // 60) if a.fotogramas else 0,
            "referencia_nombre": a.imagen_referencia or "",
            "dimensiones": list(a.dimensiones) if a.dimensiones else None,
            "zona_comun": list(a.zona_comun) if a.zona_comun else None,
            "encuadre": dict(a.encuadre) if a.encuadre else None,
            "ultima_detalle": dict(a.ultima_detalle) if a.ultima_detalle else None,
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

