# editor_fotos.py
#
# Editor de las fotos de la galería: vuelve a procesar el FITS apilado que se
# guarda junto a cada JPG (static/fotos/Objeto_AAAAMMDD_HHMMSS.fits, el cubo RGB
# lineal a campo completo) con los mismos controles que el panel "Procesado de la
# vista previa" del apilado, más:
#
#   - estirado: "multiescala" (el nuevo, sep-2026) o "stf" (el clásico), para
#     comparar los dos sistemas sobre la misma foto;
#   - firma (logo, autor, lugar, fecha, objeto, integración, coordenadas) sí/no;
#   - nombre del objeto: cambia el rótulo de la firma y, si se guarda, el nombre
#     de los tres ficheros (JPG, FITS y JSON), que es de donde la galería saca el
#     nombre y la fecha.
#
# Vista previa: procesado a media resolución (como en el apilado), síncrono, con
# un cerrojo para que solo haya una en marcha. Guardar: a resolución completa en
# un hilo (20-30 s en el mini PC), estado en EDICION. La primera vez que se
# guarda, el JPG y el JSON originales se apartan en static/fotos/originales/ para
# poder volver atrás ("Restaurar original").

import os
import io
import re
import json
import shutil
import logging
import threading
from datetime import datetime

import numpy as np
import cv2
from astropy.io import fits
from unidecode import unidecode

import firma
import procesado

log = logging.getLogger("scopepower.editor")

CARPETA_FOTOS = os.path.abspath(os.path.join("static", "fotos"))
CARPETA_ORIGINALES = os.path.join(CARPETA_FOTOS, "originales")

# Lado mayor de la vista previa que se manda a la tablet (en el campo el wifi del
# SynScan da 0,5-2 Mbit/s: 1200 px a calidad 85 son ~350 KB)
ANCHO_PREVIEW = 1200
CALIDAD_PREVIEW = 85
CALIDAD_GUARDADO = 92

# Claves de procesado que acepta el editor (las del panel del apilado + estirado + scnr)
CLAVES_FLOAT = ("fondo_objetivo", "compresion", "ruido", "contraste", "saturacion", "scnr", "brillo", "temperatura")
CLAVES_BOOL = ("gradiente", "aplanar")
ESTIRADOS = ("multiescala", "stf")
ZOOMS = (0, 25, 50, 75)

EDICION = {"en_curso": False, "mensaje": "", "ok": None, "archivo": "", "nombre_nuevo": ""}
_lock_preview = threading.Lock()
_lock_cubo = threading.Lock()
_cache_cubo = {"ruta": None, "mtime": None, "cubo": None, "n": 0, "exposicion": 0.0, "zona": None}


class ErrorEditor(Exception):
    pass


# ── Ficheros ────────────────────────────────────────────────────────────────
def rutas(nombre):
    """
    (base, ruta_jpg, ruta_fits, ruta_json) de una foto de la galería, o lanza
    ErrorEditor si el nombre no es un JPG simple dentro de static/fotos.
    """
    nombre = str(nombre or "")
    if nombre != os.path.basename(nombre) or not re.fullmatch(r"[A-Za-z0-9_.\-]+\.jpg", nombre):
        raise ErrorEditor("Nombre de imagen no válido")
    ruta_jpg = os.path.abspath(os.path.join(CARPETA_FOTOS, nombre))
    if os.path.dirname(ruta_jpg) != CARPETA_FOTOS:
        raise ErrorEditor("Nombre de imagen no válido")
    base = os.path.splitext(ruta_jpg)[0]
    return base, ruta_jpg, base + ".fits", base + ".json"


def partes_nombre(nombre):
    """'Lagoon_Nebula_20260911_234515.jpg' -> ('Lagoon_Nebula', '20260911_234515'); sin fecha -> (base, '')."""
    base = os.path.splitext(os.path.basename(nombre))[0]
    m = re.fullmatch(r"(.+)_(\d{8}_\d{6})", base)
    return (m.group(1), m.group(2)) if m else (base, "")


def nombre_archivo_objeto(objeto):
    """
    Nombre de objeto -> parte del nombre de fichero, como al guardar desde el
    apilado: sin tildes ni eñes (las rutas de la galería solo admiten ASCII) y
    con guiones bajos en vez de espacios y signos.
    """
    limpio = re.sub(r"[^A-Za-z0-9\-]", "_", unidecode(" ".join(str(objeto or "").split())))
    return limpio.strip("_") or "Objeto"


def ruta_original(base):
    return os.path.join(CARPETA_ORIGINALES, os.path.basename(base) + ".jpg")


def info_foto(nombre):
    """
    Lo que necesita la pantalla del editor: metadatos de la firma (JSON hermano
    o nombre/FITS), si hay FITS, si hay original apartado y los ajustes con que
    se editó la última vez (o los del config si nunca se editó).
    """
    base, ruta_jpg, ruta_fits, ruta_json = rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditor("Imagen no encontrada")
    meta = firma.meta_de_archivo(ruta_jpg)
    ajustes = meta.get("ajustes") or {}
    base_cfg = procesado.leer_config()
    completos = ajustes_desde(dict(base_cfg, **ajustes)) if ajustes else ajustes_desde(base_cfg)
    if "estirado" not in ajustes:
        # Foto guardada antes del editor: la firma dice si iba firmada; el estirado
        # que se usó no consta (STF hasta el 13-sep-2026), se parte del actual
        completos["firma"] = bool(meta.get("firmada", True))
    hdr = {}
    if os.path.isfile(ruta_fits):
        try:
            h = fits.getheader(ruta_fits)
            hdr = {"n_fotos": int(h.get("NSTACK", 0) or 0), "exp_total": float(h.get("EXPTOT", 0) or 0),
                   "ancho": int(h.get("NAXIS1", 0) or 0), "alto": int(h.get("NAXIS2", 0) or 0)}
        except Exception as e:
            log.warning("No se pudo leer la cabecera de %s: %s", ruta_fits, e)
    return {
        "nombre": os.path.basename(ruta_jpg),
        "objeto": meta.get("objeto") or partes_nombre(nombre)[0].replace("_", " "),
        "fecha": meta.get("fecha", ""),
        "n_fotos": meta.get("n_fotos") or hdr.get("n_fotos", 0),
        "exposicion": meta.get("exposicion") or 0,
        "ra": meta.get("ra"), "dec": meta.get("dec"),
        "hay_fits": os.path.isfile(ruta_fits),
        "hay_original": os.path.isfile(ruta_original(base)),
        "editada": bool(meta.get("editada")),
        "ajustes": completos,
        "estilos": procesado.ESTILOS,
        "fits": hdr,
    }


# ── Ajustes ─────────────────────────────────────────────────────────────────
def ajustes_desde(datos):
    """
    Ajustes de procesado completos a partir de lo que manda la web (o de un JSON
    guardado): el estilo aporta los valores base y cada clave explícita manda.
    Devuelve también las claves propias del editor: estirado, zoom, firma.
    """
    datos = datos or {}
    estilo = str(datos.get("estilo", "seestar")).lower()
    if estilo not in procesado.ESTILOS:
        estilo = "seestar"
    aj = dict(procesado.ESTILOS[estilo])
    aj["estilo"] = estilo
    for k in ("agresividad", "recuperacion", "escala", "escala_aplanado", "curva"):
        if k in datos and str(datos[k]).strip() != "":
            aj[k] = datos[k]
    for k in CLAVES_FLOAT:
        if k in datos and str(datos[k]).strip() != "":
            try:
                aj[k] = float(datos[k])
            except (TypeError, ValueError):
                pass
    for k in CLAVES_BOOL:
        if k in datos:
            v = datos[k]
            aj[k] = (str(v).lower() == "true") if not isinstance(v, bool) else v
    estirado = str(datos.get("estirado", "multiescala")).lower()
    aj["estirado"] = estirado if estirado in ESTIRADOS else "multiescala"
    try:
        zoom = int(float(datos.get("zoom", 0) or 0))
    except (TypeError, ValueError):
        zoom = 0
    aj["zoom"] = min(ZOOMS, key=lambda z: abs(z - zoom))
    try:
        aj["bordes"] = int(min(max(float(datos.get("bordes", 0) or 0), 0), 20))   # % recortado por cada lado
    except (TypeError, ValueError):
        aj["bordes"] = 0
    v = datos.get("firma", True)
    aj["firma"] = (str(v).lower() == "true") if not isinstance(v, bool) else v
    return aj


def _ajustes_para_json(aj):
    """Solo lo que tiene sentido guardar en el JSON hermano (sin los valores del estilo que no se tocan)."""
    guardar = {"estilo": aj["estilo"], "estirado": aj["estirado"], "zoom": aj["zoom"], "bordes": aj.get("bordes", 0),
               "firma": bool(aj["firma"])}
    for k in CLAVES_FLOAT:
        guardar[k] = aj.get(k)
    for k in CLAVES_BOOL:
        guardar[k] = bool(aj.get(k))
    return guardar


# ── Procesado ───────────────────────────────────────────────────────────────
def cargar_cubo(ruta_fits):
    """
    Cubo lineal float32 (3, H, W) del FITS apilado con NSTACK, exposición por foto
    y zona cubierta. Se guarda el último en memoria (108 MB en 3008×3008): mover un
    deslizador no debe volver a leer el fichero del disco.
    """
    with _lock_cubo:
        try:
            mtime = os.path.getmtime(ruta_fits)
        except OSError:
            raise ErrorEditor("Esta foto no tiene FITS guardado; no se puede reprocesar")
        c = _cache_cubo
        if c["ruta"] != ruta_fits or c["mtime"] != mtime or c["cubo"] is None:
            with fits.open(ruta_fits) as hdul:
                datos = hdul[0].data
                if datos is None or datos.ndim != 3 or 3 not in (datos.shape[0], datos.shape[-1]):
                    raise ErrorEditor("El FITS no es un apilado RGB de ScopePower")
                cubo = datos.astype(np.float32)
                if cubo.shape[0] != 3:
                    cubo = np.transpose(cubo, (2, 0, 1))
                h = hdul[0].header
                n = int(h.get("NSTACK", 1) or 1)
                total = float(h.get("EXPTOT", 0) or 0)
                c.update({"ruta": ruta_fits, "mtime": mtime, "cubo": np.ascontiguousarray(cubo), "n": n,
                          "exposicion": round(total / n, 3) if n and total else 0.0, "zona": procesado.leer_zona(h)})
        return c["cubo"], c["n"], c["exposicion"], c["zona"]


def _meta_firma(ruta_jpg, ruta_fits, objeto, n, exposicion):
    meta = firma.meta_de_archivo(ruta_jpg)
    # Autor y lugar: los del config actual, pero sin borrar los que la foto ya
    # tenía si el config no los trae (p. ej. editando en otro PC)
    for k, v in firma.datos_observatorio().items():
        if k == "activa":
            continue
        if v not in ("", 0, 0.0) or k not in meta:
            meta[k] = v
    meta["objeto"] = objeto
    meta["n_fotos"] = n or meta.get("n_fotos", 0)
    meta["exposicion"] = exposicion or meta.get("exposicion", 0)
    if not meta.get("fecha"):
        try:
            meta["fecha"] = datetime.fromtimestamp(os.path.getmtime(ruta_fits))
        except OSError:
            pass
    return meta


def procesar_foto(nombre, opciones, completa=False, progreso=None):
    """
    Reprocesa el FITS de la foto con `opciones` (ver ajustes_desde) y devuelve
    (imagen BGR uint8, meta de la firma). completa=False: media resolución (o
    completa si el zoom es del 50 % o más, como en el apilado); True: todo.
    """
    base, ruta_jpg, ruta_fits, _ = rutas(nombre)
    if not os.path.isfile(ruta_fits):
        raise ErrorEditor("Esta foto no tiene FITS guardado; no se puede reprocesar")
    aj = ajustes_desde(opciones)
    objeto = " ".join(str(opciones.get("objeto", "")).split())[:60] or partes_nombre(nombre)[0].replace("_", " ")
    cubo, n, exposicion, zona = cargar_cubo(ruta_fits)
    fraccion = (100 - aj["zoom"]) / 100.0
    recorte, _, _ = procesado.recortar_campo(cubo, fraccion, zona, margen=aj.get("bordes", 0) / 100.0)
    escala = 1.0 if completa or fraccion <= 0.5 else 0.5
    rgb8 = procesado.procesar(recorte, n, aj, escala=escala, progreso=progreso)
    imagen = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
    meta = _meta_firma(ruta_jpg, ruta_fits, objeto, n, exposicion)
    if aj["firma"]:
        imagen = firma.firmar(imagen, meta)
    meta["firmada"] = bool(aj["firma"])
    meta["ajustes"] = _ajustes_para_json(aj)
    meta["editada"] = True
    return imagen, meta


def vista_previa(nombre, opciones):
    """JPEG (bytes) de la vista previa reducida a ANCHO_PREVIEW. Solo una a la vez."""
    if not _lock_preview.acquire(blocking=False):
        raise ErrorEditor("Hay otra vista previa en marcha; espera a que termine")
    try:
        imagen, _ = procesar_foto(nombre, opciones, completa=False)
        h, w = imagen.shape[:2]
        if max(h, w) > ANCHO_PREVIEW:
            f = ANCHO_PREVIEW / float(max(h, w))
            imagen = cv2.resize(imagen, (max(8, int(w * f)), max(8, int(h * f))), interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_PREVIEW])
        if not ok:
            raise ErrorEditor("No se pudo codificar la vista previa")
        return jpg.tobytes()
    finally:
        _lock_preview.release()


# ── Guardar / restaurar ─────────────────────────────────────────────────────
def _apartar_original(base, ruta_jpg, ruta_json):
    """Copia JPG y JSON a originales/ la primera vez (si ya hay copia, se conserva la primera)."""
    destino = ruta_original(base)
    if os.path.isfile(destino):
        return
    os.makedirs(CARPETA_ORIGINALES, exist_ok=True)
    shutil.copy2(ruta_jpg, destino)
    if os.path.isfile(ruta_json):
        shutil.copy2(ruta_json, os.path.splitext(destino)[0] + ".json")


def _renombrar(base, objeto_nuevo):
    """
    Si el nombre del objeto cambia, renombra JPG, FITS, JSON (y el original
    apartado) a Objeto_AAAAMMDD_HHMMSS conservando la fecha. Devuelve el nombre
    nuevo del JPG (o el mismo si no cambia).
    """
    nombre_actual = os.path.basename(base)
    parte_objeto, fecha = partes_nombre(nombre_actual)
    nuevo_objeto = nombre_archivo_objeto(objeto_nuevo)
    if nuevo_objeto == parte_objeto:
        return nombre_actual + ".jpg"
    if not fecha:
        fecha = datetime.fromtimestamp(os.path.getmtime(base + ".jpg")).strftime("%Y%m%d_%H%M%S")
    nueva_base = os.path.join(CARPETA_FOTOS, f"{nuevo_objeto}_{fecha}")
    if any(os.path.exists(nueva_base + ext) for ext in (".jpg", ".fits", ".json")):
        raise ErrorEditor(f"Ya existe una foto llamada {os.path.basename(nueva_base)}")
    for ext in (".jpg", ".fits", ".json"):
        if os.path.isfile(base + ext):
            os.replace(base + ext, nueva_base + ext)
    orig = ruta_original(base)
    if os.path.isfile(orig):
        os.replace(orig, ruta_original(nueva_base))
        orig_json = os.path.splitext(orig)[0] + ".json"
        if os.path.isfile(orig_json):
            os.replace(orig_json, os.path.splitext(ruta_original(nueva_base))[0] + ".json")
    log.info("Foto renombrada: %s -> %s", nombre_actual, os.path.basename(nueva_base))
    return os.path.basename(nueva_base) + ".jpg"


def guardar(nombre, opciones):
    """
    Lanza el guardado en un hilo: procesado a resolución completa, firma, JPG y
    JSON (con los ajustes usados) y renombrado si cambia el objeto. Estado en EDICION.
    """
    if EDICION["en_curso"]:
        raise ErrorEditor("Ya hay un guardado en curso")
    base, ruta_jpg, ruta_fits, ruta_json = rutas(nombre)
    if not os.path.isfile(ruta_fits):
        raise ErrorEditor("Esta foto no tiene FITS guardado; no se puede reprocesar")
    EDICION.update({"en_curso": True, "ok": None, "archivo": nombre, "nombre_nuevo": "",
                    "mensaje": "Procesando a resolución completa..."})

    def _trabajo():
        try:
            def progreso(subfase, fraccion):
                EDICION["mensaje"] = f"Procesando a resolución completa ({int(fraccion * 100)} %)..."
            imagen, meta = procesar_foto(nombre, opciones, completa=True, progreso=progreso)
            EDICION["mensaje"] = "Guardando..."
            _apartar_original(base, ruta_jpg, ruta_json)
            cv2.imwrite(ruta_jpg, imagen, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_GUARDADO])
            firma.guardar_meta(ruta_jpg, meta)
            nuevo = _renombrar(base, meta["objeto"])
            EDICION.update({"ok": True, "nombre_nuevo": nuevo,
                            "mensaje": "Foto guardada" + (f" como {nuevo}" if nuevo != nombre else "")})
            log.info("Foto %s reprocesada desde el editor (%s)", nuevo, meta["ajustes"])
        except Exception as e:
            log.exception("Error guardando la foto editada")
            EDICION.update({"ok": False, "mensaje": f"Error al guardar: {e}"})
        finally:
            EDICION["en_curso"] = False

    threading.Thread(target=_trabajo, name="editor-guardar", daemon=True).start()
    return dict(EDICION)


def restaurar(nombre):
    """Vuelve a poner el JPG y el JSON originales (los apartados la primera vez que se guardó)."""
    base, ruta_jpg, _, ruta_json = rutas(nombre)
    orig = ruta_original(base)
    if not os.path.isfile(orig):
        raise ErrorEditor("Esta foto no tiene original apartado")
    os.replace(orig, ruta_jpg)
    orig_json = os.path.splitext(orig)[0] + ".json"
    if os.path.isfile(orig_json):
        os.replace(orig_json, ruta_json)
    elif os.path.isfile(ruta_json):
        os.remove(ruta_json)
    log.info("Foto %s restaurada a su original", nombre)


def borrar_original(base):
    """Al eliminar una foto de la galería, quitar también su original apartado."""
    for ext in (".jpg", ".json"):
        ruta = os.path.splitext(ruta_original(base))[0] + ext
        if os.path.isfile(ruta):
            os.remove(ruta)


def jpg_original(nombre):
    """Ruta del JPG que se muestra como 'original' en el editor: el apartado si lo hay, si no el actual."""
    base, ruta_jpg, _, _ = rutas(nombre)
    orig = ruta_original(base)
    return orig if os.path.isfile(orig) else ruta_jpg
