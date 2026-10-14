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
#   - giro y espejo del encuadre (procesado.girar_campo), a mano o "norte arriba"
#     con el plate solving del FITS;
#   - centro del encuadre: con zoom, el recorte se hace alrededor de un punto
#     elegido (tocando la foto o buscando el objeto por sus coordenadas con el
#     plate solving) en vez del centro del campo;
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
import math
import shutil
import logging
import tempfile
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
CLAVES_FLOAT = ("fondo_objetivo", "compresion", "ruido", "nitidez", "contraste", "saturacion", "saturacion_estrellas", "scnr", "brillo", "temperatura")
CLAVES_BOOL = ("gradiente", "aplanar", "scnr_estrellas", "color_estrellas_uniforme")
ESTIRADOS = ("multiescala", "stf")
ZOOMS = (0, 25, 50, 75)

EDICION = {"en_curso": False, "mensaje": "", "ok": None, "archivo": "", "nombre_nuevo": ""}
_lock_preview = threading.Lock()
_lock_cubo = threading.Lock()
_cache_cubo = {"ruta": None, "mtime": None, "cubo": None, "n": 0, "exposicion": 0.0, "zona": None, "balance": None}
_lock_norte = threading.Lock()
_cache_norte = {}      # (ruta_fits, mtime) -> (giro, espejo) que dejan el norte arriba y el este a la izquierda


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
    # Sin editar todavía: el procesado del config y el encuadre (zoom, giro, espejo) con que se guardó
    base_cfg = dict(procesado.leer_config(), **(meta.get("encuadre") or {}))
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
    Devuelve también las claves propias del editor: estirado, zoom, giro, espejo, firma.
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
    try:
        giro = float(datos.get("giro", 0) or 0)
        aj["giro"] = round((giro + 180.0) % 360.0 - 180.0, 1) if math.isfinite(giro) else 0.0   # horario, -180..180
    except (TypeError, ValueError):
        aj["giro"] = 0.0
    v = datos.get("espejo", False)
    aj["espejo"] = (str(v).lower() == "true") if not isinstance(v, bool) else v
    aj["centro"] = _centro_valido(datos.get("centro"))
    v = datos.get("firma", True)
    aj["firma"] = (str(v).lower() == "true") if not isinstance(v, bool) else v
    return aj


def _centro_valido(valor):
    """
    Centro del encuadre tal como viene de la web o del JSON: [fx, fy], fracciones
    (0..1) del ancho y del alto de la imagen completa del FITS. None = el centro
    del campo (lo de siempre). Se guardan con 4 decimales (0,3 px en 3008 px).
    """
    if valor in (None, "", False):
        return None
    try:
        fx, fy = float(valor[0]), float(valor[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(fx) and math.isfinite(fy)):
        return None
    return [round(min(max(fx, 0.0), 1.0), 4), round(min(max(fy, 0.0), 1.0), 4)]


def _ajustes_para_json(aj):
    """Solo lo que tiene sentido guardar en el JSON hermano (sin los valores del estilo que no se tocan)."""
    guardar = {"estilo": aj["estilo"], "estirado": aj["estirado"], "zoom": aj["zoom"], "bordes": aj.get("bordes", 0),
               "giro": aj.get("giro", 0.0), "espejo": bool(aj.get("espejo")), "centro": aj.get("centro"),
               "firma": bool(aj["firma"]), "curva": str(aj.get("curva") or "gamma").lower()}
    for k in CLAVES_FLOAT:
        guardar[k] = aj.get(k)
    for k in CLAVES_BOOL:
        guardar[k] = bool(aj.get(k))
    return guardar


# ── Procesado ───────────────────────────────────────────────────────────────
def cargar_cubo(ruta_fits):
    """
    Cubo lineal float32 (3, H, W) del FITS apilado con NSTACK, exposición por foto,
    zona cubierta y balance de color de sus estrellas (procesado.balance_estrellas,
    medido a campo completo). Se guarda el último en memoria (108 MB en 3008×3008):
    mover un deslizador no debe volver a leer el fichero del disco.
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
                zona = procesado.leer_zona(h)
                cubo = np.ascontiguousarray(cubo)
                c.update({"ruta": ruta_fits, "mtime": mtime, "cubo": cubo, "n": n,
                          "exposicion": round(total / n, 3) if n and total else 0.0, "zona": zona,
                          "balance": procesado.balance_estrellas(cubo, zona)})
        return c["cubo"], c["n"], c["exposicion"], c["zona"], c["balance"]


def _meta_firma(ruta_jpg, ruta_fits, objeto, n, exposicion):
    meta = firma.meta_de_archivo(ruta_jpg)
    # Autor y lugar: los del config actual, pero sin borrar los que la foto ya
    # tenía si el config no los trae (p. ej. editando en otro PC)
    for k, v in firma.datos_observatorio().items():
        if k == "activa":
            continue
        if v not in ("", 0, 0.0) or k not in meta:
            meta[k] = v
    if objeto != meta.get("objeto") or not meta.get("tipo"):
        # Objeto cambiado a mano (o foto antigua sin tipo): el tipo de la galería se
        # vuelve a deducir del nombre nuevo; si no se reconoce, se conserva el que había
        import catalogo_offline
        meta["tipo"] = catalogo_offline.categoria_objeto("", objeto) or meta.get("tipo", "")
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
    cubo, n, exposicion, zona, balance = cargar_cubo(ruta_fits)
    fraccion = (100 - aj["zoom"]) / 100.0
    recorte, _ = procesado.girar_campo(cubo, fraccion, aj.get("giro", 0.0), zona, margen=aj.get("bordes", 0) / 100.0,
                                       espejo=bool(aj.get("espejo")), centro=aj.get("centro"))
    escala = 1.0 if completa or fraccion <= 0.5 else 0.5
    rgb8 = procesado.procesar(recorte, n, aj, escala=escala, progreso=progreso, balance=balance)
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


def giro_norte(nombre):
    """
    (giro, espejo) que dejan la foto como en los atlas, con el norte arriba y el
    este a la izquierda (anotaciones.norte_arriba). Si el apilador ya los dejó en
    la cabecera del FITS (NORTEGIR, NORTEESP) salen de ahí; si no, se resuelve el
    FITS con ASTAP (en una carpeta temporal, para no dejar el FITS monocromo en la
    galería). El resultado se recuerda mientras el FITS no cambie.
    """
    import anotaciones
    _, _, ruta_fits, _ = rutas(nombre)
    try:
        clave = (ruta_fits, os.path.getmtime(ruta_fits))
    except OSError:
        raise ErrorEditor("Esta foto no tiene FITS guardado; no se puede resolver")
    if not _lock_norte.acquire(blocking=False):
        raise ErrorEditor("Ya se está resolviendo la foto; espera a que termine")
    try:
        if clave in _cache_norte:
            return _cache_norte[clave]
        h = fits.getheader(ruta_fits)
        norte = procesado.leer_norte(h)
        if norte is None:
            with tempfile.TemporaryDirectory(prefix="scopepower_norte_") as carpeta:
                res = anotaciones.resolver_wcs(ruta_fits, ruta_mono=os.path.join(carpeta, "foto_mono.fits"))
            if res is None:
                raise ErrorEditor("ASTAP no ha podido resolver la foto (¿pocas estrellas?)")
            norte = anotaciones.norte_arriba(res[0], int(h["NAXIS1"]), int(h["NAXIS2"]))
            if norte is None:
                raise ErrorEditor("No se pudo calcular la dirección del norte")
        norte = (round(float(norte[0]), 1), bool(norte[1]))
        _cache_norte[clave] = norte
        log.info("Norte arriba en %s: giro de %.1f°%s", nombre, norte[0], " con espejo" if norte[1] else "")
        return norte
    finally:
        _lock_norte.release()


# ── Centro del encuadre ─────────────────────────────────────────────────────
def _matriz_encuadre(nombre, opciones):
    """
    (M, ancho, alto, W, H): la afín 2×3 del píxel del FITS completo (W × H) al
    recorte de ancho × alto que sale con los ajustes `opciones` (los de la
    vista previa que se está viendo), con la misma zona útil que usa procesar_foto.
    """
    _, _, ruta_fits, _ = rutas(nombre)
    aj = ajustes_desde(opciones)
    cubo, _, _, zona, _ = cargar_cubo(ruta_fits)
    H, W = cubo.shape[1], cubo.shape[2]
    if not zona:
        zona = procesado.zona_automatica(cubo)
    M, ancho, alto = procesado.matriz_campo(W, H, (100 - aj["zoom"]) / 100.0, aj.get("giro", 0.0), zona,
                                            aj.get("bordes", 0) / 100.0, bool(aj.get("espejo")), centro=aj.get("centro"))
    return M, ancho, alto, W, H


def centro_desde_toque(nombre, opciones, u, v):
    """
    Centro del encuadre [fx, fy] que deja en el centro el punto tocado en la vista
    previa hecha con `opciones`: (u, v) son fracciones (0..1) del ancho y del alto
    de la imagen que se ve (el recorte procesado, a la escala que sea). Se deshace
    la geometría del encuadre para saber qué píxel del FITS se tocó.
    """
    try:
        u, v = float(u), float(v)
    except (TypeError, ValueError):
        raise ErrorEditor("Toque no válido")
    if not (math.isfinite(u) and math.isfinite(v)):
        raise ErrorEditor("Toque no válido")
    M, ancho, alto, W, H = _matriz_encuadre(nombre, opciones)
    px, py = min(max(u, 0.0), 1.0) * ancho - 0.5, min(max(v, 0.0), 1.0) * alto - 0.5
    A, b = M[:, :2], M[:, 2]
    try:
        x, y = np.linalg.solve(A, np.array([px, py]) - b)
    except np.linalg.LinAlgError:
        raise ErrorEditor("No se pudo deshacer el encuadre")
    return _centro_valido([(x + 0.5) / W, (y + 0.5) / H])


def _ra_dec_grados(ra, dec):
    """OBJCTRA/OBJCTDEC ('17 17 07.4', '+43 08 09') o números (horas, grados) -> (ra_deg, dec_deg) o None."""
    try:
        if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
            return float(ra) * 15.0, float(dec)
        nr = re.findall(r"\d+(?:\.\d+)?", str(ra or ""))
        nd = re.findall(r"\d+(?:\.\d+)?", str(dec or ""))
        if not nr or not nd:
            return None
        nr, nd = [float(x) for x in nr[:3]] + [0.0] * (3 - len(nr[:3])), [float(x) for x in nd[:3]] + [0.0] * (3 - len(nd[:3]))
        ra_deg = (nr[0] + nr[1] / 60.0 + nr[2] / 3600.0) * 15.0
        dec_deg = nd[0] + nd[1] / 60.0 + nd[2] / 3600.0
        if str(dec).strip().startswith("-"):
            dec_deg = -dec_deg
        if not (0.0 <= ra_deg < 360.0 and -90.0 <= dec_deg <= 90.0):
            return None
        return ra_deg, dec_deg
    except (TypeError, ValueError):
        return None


def centro_objeto(nombre):
    """
    Centro del encuadre [fx, fy] que deja el objeto de la foto en el centro, con el
    plate solving de la foto (el mismo que usan las anotaciones de la galería; se
    guarda, así que solo la primera vez tarda). Dónde está el objeto:
      1. por su nombre en el catálogo local (coordenadas J2000 exactas);
      2. si no está, las coordenadas de la cabecera (OBJCTRA/OBJCTDEC) son las de
         la montura al hacer la foto, en JNow y con el error de apuntado (a unos
         20' del objeto): se toma el objeto de catálogo del campo más cercano a
         ellas y, si no hay ninguno, esas mismas coordenadas.
    Devuelve (centro, mensaje). ErrorEditor si no hay con qué, no resuelve o cae
    fuera de la foto.
    """
    import galeria_anotada
    import anotaciones
    import catalogo_offline
    _, ruta_jpg, ruta_fits, _ = rutas(nombre)
    meta = firma.meta_de_archivo(ruta_jpg)
    objeto = " ".join(str(meta.get("objeto") or partes_nombre(nombre)[0].replace("_", " ")).split())
    solucion, _ = galeria_anotada.solucion_de(ruta_fits)
    wcs = galeria_anotada._wcs(solucion)
    W, H = int(solucion["ancho"]), int(solucion["alto"])

    def pixel(ra_deg, dec_deg):
        try:
            x, y = (float(v) for v in wcs.all_world2pix([[float(ra_deg), float(dec_deg)]], 0)[0])
        except Exception:
            return None
        return (x, y) if math.isfinite(x) and math.isfinite(y) and 0 <= x < W and 0 <= y < H else None

    def resultado(p, texto):
        log.info("Centro del encuadre de %s: %s, píxel (%.0f, %.0f) de %dx%d", nombre, texto, p[0], p[1], W, H)
        return _centro_valido([(p[0] + 0.5) / W, (p[1] + 0.5) / H]), texto

    # 1. El objeto por su nombre
    ficha = catalogo_offline.buscar(objeto)
    if ficha:
        p = pixel(float(ficha["ra"]) * 15.0, float(ficha["dec"]))
        if p:
            return resultado(p, f"{ficha.get('name') or objeto} centrado")
    k = catalogo_offline.clave(objeto)
    candidatos = anotaciones.objetos_catalogo_en_campo(wcs, W, H, margen=0.0)
    for o in candidatos:
        if k and len(k) >= 2 and k in catalogo_offline.clave(o["nombre"]):
            p = pixel(o["ra"], o["dec"])
            if p:
                return resultado(p, f"{o['nombre']} centrado")
    # 2. Lo más cercano a las coordenadas de la montura
    coords = _ra_dec_grados(meta.get("ra"), meta.get("dec"))
    if coords is None:
        raise ErrorEditor(f"«{objeto}» no está en el catálogo y la foto no tiene coordenadas; toca la foto para elegir el centro")
    if not candidatos:
        candidatos = anotaciones.objetos_ngc_en_campo(wcs, W, H)
    cerca = [(float(anotaciones._separacion_deg(coords[0], coords[1], o["ra"], o["dec"])), o) for o in candidatos]
    cerca = [c for c in cerca if c[0] <= 0.75]
    if cerca:
        _, o = min(cerca, key=lambda c: c[0])
        p = pixel(o["ra"], o["dec"])
        if p:
            return resultado(p, f"{o['nombre']} centrado (lo más cercano a donde apuntaba la montura)")
    p = pixel(*coords)
    if p is None:
        raise ErrorEditor("Las coordenadas de la foto caen fuera del campo; toca la foto para elegir el centro")
    return resultado(p, "centrado donde apuntaba la montura (puede desviarse unos minutos del objeto)")


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
