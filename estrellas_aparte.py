# estrellas_aparte.py
#
# Separar las estrellas del apilado con StarNet2 (https://starnetastro.com/), el
# programa de Mikita Misiura, para procesar por un lado el objeto y el fondo y por
# otro las estrellas (procesado.procesar_capas) y volver a juntarlos con una
# pantalla (screen, como en Photoshop): final = 1 - (1 - objeto)·(1 - estrellas).
#
# StarNet2 es un programa cerrado con su propia licencia (gratis, también para uso
# comercial; se acepta al instalarlo). Su licencia permite que otra aplicación lo
# ejecute como proceso aparte, pero NO redistribuirlo ni empaquetarlo: por eso no
# va con ScopePower, como ASTAP. Hay que instalarlo desde su web y, si no está en
# la ruta de siempre, decir dónde en [General] ruta_starnet de config.ini. Ni este
# módulo ni las pruebas usan sus salidas como datos de prueba (la licencia también
# lo prohíbe).
#
# Cómo se le llama: con el FITS lineal del apilado, dividido por 65535 (los FITS
# en coma flotante tienen que ir en [0, 1]) y con --linear, que estira cada canal
# por dentro y deshace el estirado, así lo que devuelve sigue siendo lineal:
#   starnet2 --linear --input apilado.fits --output objeto.fits --unscreen estrellas.fits
# objeto.fits es el apilado sin estrellas (mismas unidades) y estrellas.fits la
# capa «unscreen» U, una fracción 0..1 con la que objeto + U·(1 - objeto) devuelve
# el original exacto (medido con la Pacman: error medio de 0,009 ADU).
#
# Tarda: 222 s la foto completa de la Saturn-C (3008×3008) y 55 s a la mitad en un
# PC de sobremesa de 8 núcleos solo con CPU; en el mini PC de campo cabe esperar
# 2-4 veces más. Por eso nunca se hace en la vista previa del apilado en vivo:
# solo al guardar en la galería (si [Procesado] estrellas_aparte = true) y en el
# editor, en un hilo, y la pareja de capas se guarda en static/fotos/capas/ para
# no repetirlo (se borra y se renombra con la foto).

import os
import json
import glob
import shutil
import logging
import subprocess
import tempfile
import threading
import configparser
from datetime import datetime

import numpy as np
from astropy.io import fits

from idioma import _

log = logging.getLogger("scopepower.estrellas")

NOMBRE_PRODUCTO = "StarNet2"           # la licencia pide nombrarlo tal cual donde se use
RUTAS_TIPICAS = (r"C:\Program Files\StarNet2\bin\starnet2.exe",
                 "/usr/local/bin/starnet2", "/usr/bin/starnet2")
CARPETA_CAPAS = os.path.abspath(os.path.join("static", "fotos", "capas"))
SUFIJO_OBJETO, SUFIJO_ESTRELLAS = "_objeto.fits", "_estrellas.fits"
TIEMPO_MAXIMO_S = 3600                 # por si el proceso se queda colgado

# Estado de la separación en curso (una sola a la vez), para la web
ESTADO = {"en_curso": False, "archivo": "", "fraccion": 0.0, "mensaje": "", "ok": None, "segundos": 0.0}
_lock = threading.Lock()
# Un solo StarNet2 a la vez (el editor y la cola de cola_estrellas.py): usa todos los
# núcleos y dos a la vez solo irían el doble de lentos
_lock_starnet = threading.Lock()
BAJA_PRIORIDAD = 0x00004000     # BELOW_NORMAL_PRIORITY_CLASS de Windows


class ErrorSeparacion(Exception):
    pass


# ── Dónde está StarNet2 ──────────────────────────────────────────────────────
def ruta_starnet():
    """
    Ruta del ejecutable: [General] ruta_starnet de config.ini si está rellena y
    existe; si no, el PATH y las rutas típicas de su instalador. "" si no se encuentra.
    """
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    ruta = cfg.get("General", "ruta_starnet", fallback="").strip().replace("\\\\", "\\")
    if ruta and os.path.isfile(ruta):
        return ruta
    encontrada = shutil.which("starnet2")
    if encontrada:
        return encontrada
    for r in RUTAS_TIPICAS:
        if os.path.isfile(r):
            return r
    return ""


def disponible():
    return bool(ruta_starnet())


def version():
    """'2.6.2' o '' si no responde."""
    ruta = ruta_starnet()
    if not ruta:
        return ""
    try:
        salida = subprocess.run([ruta, "--version"], capture_output=True, text=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return salida.replace("starnet2", "").replace("version:", "").strip()
    except Exception as e:
        log.warning("StarNet2 no responde a --version: %s", e)
        return ""


# ── Las capas guardadas junto a la galería ───────────────────────────────────
def rutas_capas(ruta_fits):
    """(ruta_objeto, ruta_estrellas) en static/fotos/capas/ para el FITS apilado de una foto."""
    base = os.path.splitext(os.path.basename(ruta_fits))[0]
    return os.path.join(CARPETA_CAPAS, base + SUFIJO_OBJETO), os.path.join(CARPETA_CAPAS, base + SUFIJO_ESTRELLAS)


def hay_capas(ruta_fits):
    """True si la pareja de capas existe y es del FITS actual (misma fecha de modificación)."""
    r_obj, r_est = rutas_capas(ruta_fits)
    if not (os.path.isfile(r_obj) and os.path.isfile(r_est)):
        return False
    try:
        return abs(float(fits.getheader(r_obj).get("SRCMTIME", -1)) - os.path.getmtime(ruta_fits)) < 2.0
    except Exception:
        return False


def cargar_capas(ruta_fits):
    """
    (objeto, estrellas): cubos float32 (3, H, W); el objeto en ADU como el apilado y
    las estrellas como fracción 0..1 (la capa unscreen). None si no están separadas.
    """
    if not hay_capas(ruta_fits):
        return None
    r_obj, r_est = rutas_capas(ruta_fits)
    objeto = np.ascontiguousarray(fits.getdata(r_obj).astype(np.float32))
    with fits.open(r_est) as hdul:
        est = hdul[0].data
        # La capa de estrellas se guarda en uint16 (la mitad de sitio): fracción × 65535
        estrellas = np.ascontiguousarray(est.astype(np.float32) / 65535.0 if est.dtype != np.float32 else est)
    return objeto, estrellas


def renombrar_capas(base_vieja, base_nueva):
    """Al renombrar una foto de la galería, sus capas la siguen (bases sin extensión)."""
    for sufijo in (SUFIJO_OBJETO, SUFIJO_ESTRELLAS):
        vieja = os.path.join(CARPETA_CAPAS, os.path.basename(base_vieja) + sufijo)
        if os.path.isfile(vieja):
            os.replace(vieja, os.path.join(CARPETA_CAPAS, os.path.basename(base_nueva) + sufijo))


def borrar_capas(base):
    """Al borrar una foto de la galería, fuera sus capas."""
    for sufijo in (SUFIJO_OBJETO, SUFIJO_ESTRELLAS):
        ruta = os.path.join(CARPETA_CAPAS, os.path.basename(base) + sufijo)
        if os.path.isfile(ruta):
            os.remove(ruta)


# ── La separación ────────────────────────────────────────────────────────────
def _leer_progreso(linea):
    """
    Una línea JSON de --machine-progress (starnetastro.cli.progress.v1:
    {"event": "start|progress|finish|error", "percent": 2.8, "message": ...}):
    devuelve (fraccion 0..1 o None, mensaje de error o ""). (None, "") si no es JSON.
    """
    try:
        d = json.loads(linea)
    except ValueError:
        return None, ""
    if not isinstance(d, dict):
        return None, ""
    if d.get("event") == "error":
        return None, str(d.get("message", "")).splitlines()[0] if d.get("message") else _("error")
    if isinstance(d.get("percent"), (int, float)):
        return float(d["percent"]) / 100.0, ""
    if d.get("event") == "finish":
        return 1.0, ""
    return None, ""


def separar(cubo, progreso=None, cabecera=None, prioridad_baja=False):
    """
    Pasa StarNet2 al cubo lineal (3, H, W) en ADU y devuelve (objeto, estrellas):
    el apilado sin estrellas en ADU y la capa unscreen (fracción 0..1), los dos
    float32 (3, H, W). progreso(fraccion, mensaje): opcional. Lanza ErrorSeparacion
    si StarNet2 no está o falla. Bloquea: llamar desde un hilo. prioridad_baja: el
    proceso de StarNet2 con prioridad por debajo de lo normal (la cola de después de
    guardar: la captura y el apilado van primero).
    """
    ruta = ruta_starnet()
    if not ruta:
        raise ErrorSeparacion(_("{producto} no está instalado (o no está en ruta_starnet de config.ini)", producto=NOMBRE_PRODUCTO))
    cubo = np.asarray(cubo, dtype=np.float32)
    if cubo.ndim != 3 or cubo.shape[0] != 3:
        raise ErrorSeparacion(_("La separación de estrellas necesita un apilado RGB"))
    if min(cubo.shape[1:]) < 512:
        raise ErrorSeparacion(_("{producto} necesita al menos 512×512 píxeles", producto=NOMBRE_PRODUCTO))

    def avisar(f, texto=""):
        if progreso:
            try:
                progreso(float(f), texto)
            except Exception:
                pass

    with tempfile.TemporaryDirectory(prefix="scopepower_starnet_") as carpeta:
        entrada = os.path.join(carpeta, "apilado.fits")
        salida_obj = os.path.join(carpeta, "objeto.fits")
        salida_est = os.path.join(carpeta, "estrellas.fits")
        # Los FITS float tienen que ir en [0, 1]: se divide por el blanco de 16 bits
        # (lo que pase de 1 se recorta con aviso; un apilado por la media no pasa)
        hdr = fits.Header()
        if cabecera is not None:
            for k in ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "CDELT1", "CDELT2", "CROTA2", "CTYPE1", "CTYPE2",
                      "CD1_1", "CD1_2", "CD2_1", "CD2_2", "NSTACK", "EXPTOT", "OBJCTRA", "OBJCTDEC"):
                if k in cabecera:
                    hdr[k] = cabecera[k]
        fits.writeto(entrada, cubo / np.float32(65535.0), hdr, overwrite=True)
        avisar(0.0, _("Separando las estrellas con {producto}...", producto=NOMBRE_PRODUCTO))
        orden = [ruta, "--quiet", "--machine-progress", "--linear",
                 "--input", entrada, "--output", salida_obj, "--unscreen", salida_est]
        log.info("StarNet2: %s", " ".join(orden[1:]))
        inicio = datetime.now()
        try:
            banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0) | (BAJA_PRIORIDAD if prioridad_baja and os.name == "nt" else 0)
            proc = subprocess.Popen(orden, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                    cwd=carpeta, creationflags=banderas)
        except OSError as e:
            raise ErrorSeparacion(_("No se pudo lanzar {producto}: {e}", producto=NOMBRE_PRODUCTO, e=e))
        avisos = []

        def _vigilar():
            try:
                proc.wait(TIEMPO_MAXIMO_S)
            except subprocess.TimeoutExpired:
                proc.kill()
        vigilante = threading.Thread(target=_vigilar, daemon=True)
        vigilante.start()
        for linea in proc.stderr:
            linea = linea.strip()
            if not linea:
                continue
            f, error = _leer_progreso(linea)
            if f is not None:
                avisar(f)
            elif error:
                avisos.append(error)
                log.warning("StarNet2: %s", error)
            elif not linea.startswith("{"):
                log.debug("StarNet2: %s", linea)      # avisos en texto (data_loss, etc.)
                if "arning" in linea or "rror" in linea:
                    avisos.append(linea)
        proc.wait()
        segundos = (datetime.now() - inicio).total_seconds()
        if proc.returncode != 0 or not (os.path.isfile(salida_obj) and os.path.isfile(salida_est)):
            raise ErrorSeparacion(_("{producto} ha fallado (código {codigo}) {detalle}", producto=NOMBRE_PRODUCTO,
                                    codigo=proc.returncode, detalle="; ".join(avisos)[:300]))
        objeto = fits.getdata(salida_obj).astype(np.float32) * np.float32(65535.0)
        estrellas = np.clip(fits.getdata(salida_est).astype(np.float32), 0.0, 1.0)
    if objeto.shape != cubo.shape or estrellas.shape != cubo.shape:
        raise ErrorSeparacion(_("{producto} ha devuelto una imagen de otro tamaño", producto=NOMBRE_PRODUCTO))
    log.info("StarNet2: estrellas separadas en %.0f s (%dx%d, %.2f %% de píxeles con estrella)",
             segundos, cubo.shape[2], cubo.shape[1], float(np.mean(estrellas.max(axis=0) > 0.002)) * 100)
    avisar(1.0, _("Estrellas separadas en {s} s", s=int(segundos)))
    return np.ascontiguousarray(objeto), np.ascontiguousarray(estrellas)


def separar_fits(ruta_fits, progreso=None, prioridad_baja=False):
    """
    Separa el apilado de una foto de la galería y guarda la pareja en
    static/fotos/capas/. Devuelve (objeto, estrellas) como cargar_capas. Si ya
    estaban separadas del mismo FITS, las devuelve sin volver a pasar StarNet2.
    Una sola a la vez: si hay otra en marcha, espera (y si era de este mismo FITS,
    usa sus capas).
    """
    capas = cargar_capas(ruta_fits)
    if capas is not None:
        return capas
    if not _lock_starnet.acquire(blocking=False):
        if progreso:
            try:
                progreso(0.0, _("Esperando a que termine la otra separación de estrellas..."))
            except Exception:
                pass
        _lock_starnet.acquire()
    try:
        return _separar_fits(ruta_fits, progreso, prioridad_baja)
    finally:
        _lock_starnet.release()


def _separar_fits(ruta_fits, progreso, prioridad_baja):
    capas = cargar_capas(ruta_fits)
    if capas is not None:
        return capas
    with fits.open(ruta_fits) as hdul:
        cubo = hdul[0].data.astype(np.float32)
        if cubo.ndim == 3 and cubo.shape[0] != 3:
            cubo = np.transpose(cubo, (2, 0, 1))
        cabecera = hdul[0].header
    objeto, estrellas = separar(cubo, progreso=progreso, cabecera=cabecera, prioridad_baja=prioridad_baja)
    os.makedirs(CARPETA_CAPAS, exist_ok=True)
    r_obj, r_est = rutas_capas(ruta_fits)
    hdr = fits.Header()
    hdr["SRCMTIME"] = (float(os.path.getmtime(ruta_fits)), "mtime del apilado del que salen")
    hdr["STARNET"] = (version() or "?", "version de StarNet2")
    hdr["HISTORY"] = "ScopePower: apilado sin estrellas (StarNet2 --linear --unscreen)"
    fits.writeto(r_obj, objeto, hdr, overwrite=True)
    hdr2 = fits.Header()
    hdr2["SRCMTIME"] = hdr["SRCMTIME"]
    hdr2["BUNIT"] = ("1/65535", "fraccion unscreen x 65535")
    hdr2["HISTORY"] = "ScopePower: capa de estrellas unscreen (StarNet2): objeto + U*(1-objeto) = original"
    fits.writeto(r_est, np.clip(estrellas * 65535.0 + 0.5, 0, 65535).astype(np.uint16), hdr2, overwrite=True)
    return objeto, estrellas


def separar_en_hilo(ruta_fits, nombre=""):
    """
    Lanza separar_fits en un hilo y lleva el progreso en ESTADO (la web lo sondea).
    Devuelve una copia de ESTADO; ErrorSeparacion si ya hay otra en marcha o no hay StarNet2.
    """
    if not disponible():
        raise ErrorSeparacion(_("{producto} no está instalado (o no está en ruta_starnet de config.ini)", producto=NOMBRE_PRODUCTO))
    with _lock:
        if ESTADO["en_curso"]:
            raise ErrorSeparacion(_("Ya se están separando las estrellas de {archivo}; espera a que termine", archivo=ESTADO["archivo"]))
        ESTADO.update({"en_curso": True, "archivo": nombre or os.path.basename(ruta_fits), "fraccion": 0.0, "ok": None,
                       "segundos": 0.0, "mensaje": _("Separando las estrellas con {producto}...", producto=NOMBRE_PRODUCTO)})
    inicio = datetime.now()

    def _progreso(f, texto):
        ESTADO["fraccion"] = f
        ESTADO["segundos"] = (datetime.now() - inicio).total_seconds()
        if texto:
            ESTADO["mensaje"] = texto
        elif f > 0:
            ESTADO["mensaje"] = _("Separando las estrellas con {producto} ({p} %)...", producto=NOMBRE_PRODUCTO, p=int(f * 100))

    def _trabajo():
        try:
            separar_fits(ruta_fits, progreso=_progreso)
            ESTADO.update({"ok": True, "fraccion": 1.0, "segundos": (datetime.now() - inicio).total_seconds(),
                           "mensaje": _("Estrellas separadas en {s} s", s=int((datetime.now() - inicio).total_seconds()))})
        except Exception as e:
            log.exception("Error separando las estrellas de %s", ruta_fits)
            ESTADO.update({"ok": False, "mensaje": str(e)})
        finally:
            ESTADO["en_curso"] = False

    threading.Thread(target=_trabajo, name="starnet", daemon=True).start()
    return dict(ESTADO)


def estado():
    d = dict(ESTADO)
    d["disponible"] = disponible()
    return d


def limpiar_huerfanas():
    """Capas sin foto en la galería (fotos borradas a mano): fuera. Devuelve cuántas."""
    n = 0
    for ruta in glob.glob(os.path.join(CARPETA_CAPAS, "*.fits")):
        base = os.path.basename(ruta)
        for sufijo in (SUFIJO_OBJETO, SUFIJO_ESTRELLAS):
            if base.endswith(sufijo):
                base = base[:-len(sufijo)]
        if not os.path.isfile(os.path.join(os.path.dirname(CARPETA_CAPAS), base + ".fits")):
            os.remove(ruta)
            n += 1
    return n
