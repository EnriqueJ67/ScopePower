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
#     nombre y la fecha;
#   - cometa: si la foto tiene hermana (<objeto>_<fecha> y <objeto>_<fecha>_nucleo,
#     los dos apilados de la misma sesión), se puede procesar el apilado por las
#     estrellas, el del núcleo o la composición de los dos (cometa.componer:
#     cometa fijo y estrellas fijas).
#
#   - curvas por puntos de control (como las del Seestar): una para los tres
#     canales y una por canal, que se dibujan en el navegador.
#   - estrellas aparte (oct-2026): con StarNet2 instalado (estrellas_aparte.py),
#     el apilado se separa en objeto y estrellas (una vez por foto, en un hilo,
#     varios minutos; la pareja queda en static/fotos/capas/) y se procesan por
#     separado (procesado.procesar_capas): el objeto sin que las estrellas
#     estorben y las estrellas con su color; un deslizador de tamaño (0 = sin
#     estrellas) las devuelve con una pantalla. En la vista previa el navegador
#     recibe las dos capas y hace la pantalla él.
#
# Vista previa (oct-2026): el servidor procesa a media resolución (como en el
# apilado) solo hasta la BASE de color (procesado.color_base: estirado, contraste
# local, fondo neutralizado, color de las estrellas) y manda un paquete con la
# base en JPEG, las dos máscaras de estrellas en PNG, la firma como capa PNG con
# transparencia y el nivel del cielo; el navegador hace en vivo los ajustes
# rápidos (temperatura, brillo, SCNR, saturación, curvas: static/js/retoque.js)
# sobre esa base, sin volver a pedir nada mientras el mini PC apila. Síncrona,
# con un cerrojo para que solo haya una en marcha. Guardar: a resolución
# completa en un hilo (20-30 s en el mini PC), con los mismos ajustes rápidos en
# coma flotante (procesado.ajustes_rapidos), estado en EDICION. La primera vez
# que se guarda, el JPG y el JSON originales se apartan en
# static/fotos/originales/ para poder volver atrás ("Restaurar original").

import os
import re
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
import cometa
import estrellas_aparte
from idioma import _

SUFIJO_NUCLEO = "_nucleo"
COMPOSICIONES = ("estrellas", "cometa", "compuesta")
RADIO_COMETA = (10, 300, 40)      # mínimo, máximo y valor por defecto (px) del radio del cometa al componer

log = logging.getLogger("scopepower.editor")

CARPETA_FOTOS = os.path.abspath(os.path.join("static", "fotos"))
CARPETA_ORIGINALES = os.path.join(CARPETA_FOTOS, "originales")

# Lado mayor de la vista previa que se manda a la tablet (en el campo el wifi del
# SynScan da 0,5-2 Mbit/s: 1200 px a calidad 85 son ~350 KB)
ANCHO_PREVIEW = 1200
CALIDAD_PREVIEW = 85
CALIDAD_GUARDADO = 92

# Claves de procesado que acepta el editor (las del panel del apilado + estirado + scnr)
CLAVES_FLOAT = ("fondo_objetivo", "compresion", "ruido", "nitidez", "contraste", "saturacion", "saturacion_estrellas", "scnr", "brillo", "temperatura",
                "tamano_estrellas")
CLAVES_BOOL = ("gradiente", "aplanar", "scnr_estrellas", "color_estrellas_uniforme", "estrellas_aparte")
ESTIRADOS = ("multiescala", "stf")
ZOOMS = (0, 25, 50, 75)

EDICION = {"en_curso": False, "mensaje": "", "ok": None, "archivo": "", "nombre_nuevo": ""}
_lock_preview = threading.Lock()
_lock_cubo = threading.Lock()
_cache_cubo = {"ruta": None, "mtime": None, "cubo": None, "n": 0, "exposicion": 0.0, "zona": None, "balance": None}
_lock_norte = threading.Lock()
_cache_norte = {}      # (ruta_fits, mtime) -> (giro, espejo) que dejan el norte arriba y el este a la izquierda
_lock_compuesto = threading.Lock()
_cache_compuesto = {"clave": None, "cubo": None}
_lock_capas = threading.Lock()
_cache_capas = {"ruta": None, "mtime": None, "capas": None}     # (objeto, estrellas) de estrellas_aparte.cargar_capas


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
        raise ErrorEditor(_("Nombre de imagen no válido"))
    ruta_jpg = os.path.abspath(os.path.join(CARPETA_FOTOS, nombre))
    if os.path.dirname(ruta_jpg) != CARPETA_FOTOS:
        raise ErrorEditor(_("Nombre de imagen no válido"))
    base = os.path.splitext(ruta_jpg)[0]
    return base, ruta_jpg, base + ".fits", base + ".json"


def partes_nombre(nombre):
    """
    'Lagoon_Nebula_20260911_234515.jpg' -> ('Lagoon_Nebula', '20260911_234515'); sin
    fecha -> (base, ''). El sufijo del apilado por el núcleo del cometa
    ('..._nucleo') no forma parte ni del objeto ni de la fecha.
    """
    base = os.path.splitext(os.path.basename(nombre))[0]
    if base.endswith(SUFIJO_NUCLEO):
        base = base[:-len(SUFIJO_NUCLEO)]
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


def pareja_cometa(nombre):
    """
    Si la foto forma pareja con su apilado hermano de la misma sesión
    (<objeto>_<fecha>.fits por las estrellas y <objeto>_<fecha>_nucleo.fits por el
    núcleo del cometa), devuelve {"estrellas": ruta_fits, "cometa": ruta_fits,
    "es_nucleo": bool, "hermano": nombre_jpg, "nucleo": [x, y] o None,
    "desplazamiento": [dx, dy] o None}; si no, None.
    """
    base, __, ruta_fits, __ = rutas(nombre)
    if base.endswith(SUFIJO_NUCLEO):
        base_est, base_com, es_nucleo = base[:-len(SUFIJO_NUCLEO)], base, True
    else:
        base_est, base_com, es_nucleo = base, base + SUFIJO_NUCLEO, False
    f_est, f_com = base_est + ".fits", base_com + ".fits"
    if not (os.path.isfile(f_est) and os.path.isfile(f_com)):
        return None
    hermano = (base_est if es_nucleo else base_com) + ".jpg"
    nucleo = desplazamiento = None
    try:
        h = fits.getheader(f_com)
        if "NUCLEOX" in h and "NUCLEOY" in h:
            nucleo = [float(h["NUCLEOX"]), float(h["NUCLEOY"])]
        if "COMETDX" in h and "COMETDY" in h:
            desplazamiento = [float(h["COMETDX"]), float(h["COMETDY"])]
    except Exception as e:
        log.warning("No se pudo leer la cabecera del apilado del cometa %s: %s", f_com, e)
    return {"estrellas": f_est, "cometa": f_com, "es_nucleo": es_nucleo,
            "hermano": os.path.basename(hermano) if os.path.isfile(hermano) else "",
            "nucleo": nucleo, "desplazamiento": desplazamiento}


def info_foto(nombre):
    """
    Lo que necesita la pantalla del editor: metadatos de la firma (JSON hermano
    o nombre/FITS), si hay FITS, si hay original apartado y los ajustes con que
    se editó la última vez (o los del config si nunca se editó).
    """
    base, ruta_jpg, ruta_fits, ruta_json = rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditor(_("Imagen no encontrada"))
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
    pareja = pareja_cometa(nombre)
    if pareja and "composicion" not in ajustes:
        completos["composicion"] = "cometa" if pareja["es_nucleo"] else "estrellas"
    return {
        "nombre": os.path.basename(ruta_jpg),
        # Estrellas aparte: si StarNet2 está instalado y si esta foto ya tiene las capas separadas
        "starnet": {"disponible": estrellas_aparte.disponible(), "producto": estrellas_aparte.NOMBRE_PRODUCTO,
                    "capas": os.path.isfile(ruta_fits) and estrellas_aparte.hay_capas(ruta_fits)},
        "cometa": ({"hermano": pareja["hermano"], "es_nucleo": pareja["es_nucleo"], "nucleo": pareja["nucleo"],
                    "desplazamiento": pareja["desplazamiento"], "hay_nucleo": pareja["nucleo"] is not None}
                   if pareja else None),
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
    # Cometa: qué apilado se procesa (solo cuenta si la foto tiene hermana)
    composicion = str(datos.get("composicion", "") or "").lower()
    aj["composicion"] = composicion if composicion in COMPOSICIONES else ""
    try:
        aj["radio_cometa"] = int(min(max(float(datos.get("radio_cometa", RADIO_COMETA[2]) or RADIO_COMETA[2]), RADIO_COMETA[0]), RADIO_COMETA[1]))
    except (TypeError, ValueError):
        aj["radio_cometa"] = RADIO_COMETA[2]
    aj["curvas"] = procesado.normalizar_curvas(datos.get("curvas"))
    # Estrellas aparte: la casilla y el tamaño (0..100 %); una foto de antes no las trae
    aj["estrellas_aparte"] = bool(aj.get("estrellas_aparte", False))
    try:
        aj["tamano_estrellas"] = float(min(max(float(aj.get("tamano_estrellas", procesado.TAMANO_ESTRELLAS)), 0.0), 100.0))
    except (TypeError, ValueError):
        aj["tamano_estrellas"] = procesado.TAMANO_ESTRELLAS
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
    if aj.get("composicion"):
        guardar["composicion"] = aj["composicion"]
        guardar["radio_cometa"] = aj.get("radio_cometa", RADIO_COMETA[2])
    for k in CLAVES_FLOAT:
        guardar[k] = aj.get(k)
    for k in CLAVES_BOOL:
        guardar[k] = bool(aj.get(k))
    if aj.get("curvas"):
        guardar["curvas"] = aj["curvas"]
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
            raise ErrorEditor(_("Esta foto no tiene FITS guardado; no se puede reprocesar"))
        c = _cache_cubo
        if c["ruta"] != ruta_fits or c["mtime"] != mtime or c["cubo"] is None:
            with fits.open(ruta_fits) as hdul:
                datos = hdul[0].data
                if datos is None or datos.ndim != 3 or 3 not in (datos.shape[0], datos.shape[-1]):
                    raise ErrorEditor(_("El FITS no es un apilado RGB de ScopePower"))
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


def cargar_cubo_compuesto(pareja, radio, progreso=None):
    """
    Cubo lineal con el cometa fijo y las estrellas fijas (cometa.componer) a partir
    de la pareja de apilados; n, exposición, zona y balance del apilado por las
    estrellas. El último se guarda en memoria (cambiar un deslizador del estirado
    no debe volver a componer: unos segundos en el mini PC).
    """
    if pareja["nucleo"] is None:
        raise ErrorEditor(_("El apilado del cometa no lleva la posición del núcleo; no se puede componer"))
    try:
        clave = (pareja["estrellas"], os.path.getmtime(pareja["estrellas"]),
                 pareja["cometa"], os.path.getmtime(pareja["cometa"]), int(radio))
    except OSError:
        raise ErrorEditor(_("Falta uno de los dos apilados de la pareja"))
    cubo_est, n, exposicion, zona_est, balance = cargar_cubo(pareja["estrellas"])
    with _lock_compuesto:
        if _cache_compuesto["clave"] != clave or _cache_compuesto["cubo"] is None:
            with fits.open(pareja["cometa"]) as hdul:
                datos = hdul[0].data
                if datos is None or datos.ndim != 3:
                    raise ErrorEditor(_("El FITS del cometa no es un apilado RGB de ScopePower"))
                cubo_com = np.ascontiguousarray(datos.astype(np.float32))
                if cubo_com.shape[0] != 3:
                    cubo_com = np.ascontiguousarray(np.transpose(cubo_com, (2, 0, 1)))
                zona_com = procesado.leer_zona(hdul[0].header)
            if cubo_com.shape != cubo_est.shape:
                raise ErrorEditor(_("Los dos apilados no tienen el mismo tamaño"))
            cubo = cometa.componer(cubo_est, cubo_com, pareja["nucleo"], pareja["desplazamiento"], radio, progreso=progreso)
            # Zona cubierta: la común a los dos apilados
            zona = zona_est
            if zona_est and zona_com:
                x0, y0 = max(zona_est[0], zona_com[0]), max(zona_est[1], zona_com[1])
                x1 = min(zona_est[0] + zona_est[2], zona_com[0] + zona_com[2])
                y1 = min(zona_est[1] + zona_est[3], zona_com[1] + zona_com[3])
                zona = (x0, y0, max(x1 - x0, 1), max(y1 - y0, 1))
            elif zona_com:
                zona = zona_com
            _cache_compuesto.update({"clave": clave, "cubo": cubo, "zona": zona})
        return _cache_compuesto["cubo"], n, exposicion, _cache_compuesto["zona"], balance


def cargar_capas(ruta_fits):
    """
    (objeto, estrellas) de estrellas_aparte.cargar_capas para el FITS, o None si
    aún no están separadas. La última pareja se queda en memoria (160 MB en
    3008×3008): cambiar un deslizador no debe releerla del disco.
    """
    with _lock_capas:
        if not estrellas_aparte.hay_capas(ruta_fits):
            return None
        mtime = os.path.getmtime(estrellas_aparte.rutas_capas(ruta_fits)[0])
        c = _cache_capas
        if c["ruta"] != ruta_fits or c["mtime"] != mtime or c["capas"] is None:
            c.update({"ruta": ruta_fits, "mtime": mtime, "capas": estrellas_aparte.cargar_capas(ruta_fits)})
        return c["capas"]


def separar_estrellas(nombre):
    """Lanza la separación de estrellas de la foto en un hilo (estrellas_aparte.separar_en_hilo). Devuelve su estado."""
    __, __, ruta_fits, __ = rutas(nombre)
    if not os.path.isfile(ruta_fits):
        raise ErrorEditor(_("Esta foto no tiene FITS guardado; no se puede reprocesar"))
    if estrellas_aparte.hay_capas(ruta_fits):
        return dict(estrellas_aparte.estado(), listas=True)
    try:
        return dict(estrellas_aparte.separar_en_hilo(ruta_fits, nombre), listas=False)
    except estrellas_aparte.ErrorSeparacion as e:
        raise ErrorEditor(str(e))


def estado_separacion(nombre=None):
    """Estado de la separación en curso y, con `nombre`, si esa foto ya tiene sus capas."""
    d = estrellas_aparte.estado()
    if nombre:
        try:
            __, __, ruta_fits, __ = rutas(nombre)
            d["listas"] = estrellas_aparte.hay_capas(ruta_fits)
        except ErrorEditor:
            d["listas"] = False
    return d


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


def procesar_foto(nombre, opciones, completa=False, progreso=None, solo_base=False):
    """
    Reprocesa el FITS de la foto con `opciones` (ver ajustes_desde) y devuelve
    (imagen BGR uint8, meta de la firma). completa=False: media resolución (o
    completa si el zoom es del 50 % o más, como en el apilado); True: todo.
    solo_base: se para en la base de color (procesado.color_base) y devuelve
    (base RGB float32 [0, 1], estrellas, pequenas, meta, capa), sin firmar: es lo
    que la vista previa manda al navegador para que haga él los ajustes rápidos.
    `capa` es la capa de estrellas estirada (float32 [0, 1]) con las estrellas
    aparte, y None si no. Con estrellas aparte pedidas y la foto sin separar
    todavía, ErrorEditor (la web lanza antes la separación).
    """
    base, ruta_jpg, ruta_fits, __ = rutas(nombre)
    if not os.path.isfile(ruta_fits):
        raise ErrorEditor(_("Esta foto no tiene FITS guardado; no se puede reprocesar"))
    aj = ajustes_desde(opciones)
    objeto = " ".join(str(opciones.get("objeto", "")).split())[:60] or partes_nombre(nombre)[0].replace("_", " ")
    # Cometa: la pareja de apilados permite procesar el otro o la composición de los dos
    pareja = pareja_cometa(nombre)
    if pareja is None:
        aj["composicion"] = ""
    elif not aj["composicion"]:
        aj["composicion"] = "cometa" if pareja["es_nucleo"] else "estrellas"
    if aj["composicion"] == "compuesta":
        cubo, n, exposicion, zona, balance = cargar_cubo_compuesto(pareja, aj["radio_cometa"], progreso=progreso)
    elif aj["composicion"] == "cometa":
        cubo, n, exposicion, zona, balance = cargar_cubo(pareja["cometa"])
    elif aj["composicion"] == "estrellas":
        cubo, n, exposicion, zona, balance = cargar_cubo(pareja["estrellas"])
    else:
        cubo, n, exposicion, zona, balance = cargar_cubo(ruta_fits)
    fraccion = (100 - aj["zoom"]) / 100.0
    encuadre = dict(margen=aj.get("bordes", 0) / 100.0, espejo=bool(aj.get("espejo")), centro=aj.get("centro"))
    escala = 1.0 if completa or fraccion <= 0.5 else 0.5
    meta = _meta_firma(ruta_jpg, ruta_fits, objeto, n, exposicion)
    # Estrellas aparte: solo sobre el apilado de la propia foto (no sobre la
    # composición del cometa ni el apilado hermano, que no tienen capas)
    capas = None
    apilado_propio = "" if pareja is None else ("cometa" if pareja["es_nucleo"] else "estrellas")
    if aj["estrellas_aparte"] and aj["composicion"] == apilado_propio:
        capas = cargar_capas(ruta_fits)
        if capas is None:
            raise ErrorEditor(_("Las estrellas de esta foto aún no están separadas"))
    else:
        aj["estrellas_aparte"] = False
    if capas is not None:
        objeto_cubo, __ = procesado.girar_campo(capas[0], fraccion, aj.get("giro", 0.0), zona, **encuadre)
        capa_U, __ = procesado.girar_campo(capas[1], fraccion, aj.get("giro", 0.0), zona, **encuadre)
        if solo_base:
            base, capa, mascara = procesado.procesar_capas(objeto_cubo, capa_U, n, aj, escala=escala, progreso=progreso,
                                                           balance=balance, solo_base=True)
            meta["ajustes"] = _ajustes_para_json(aj)
            return base, mascara, mascara, meta, capa
        rgb8 = procesado.procesar_capas(objeto_cubo, capa_U, n, aj, escala=escala, progreso=progreso, balance=balance)
    else:
        recorte, __ = procesado.girar_campo(cubo, fraccion, aj.get("giro", 0.0), zona, **encuadre)
        if solo_base:
            base, estrellas, pequenas = procesado.procesar(recorte, n, aj, escala=escala, progreso=progreso,
                                                           balance=balance, solo_base=True)
            meta["ajustes"] = _ajustes_para_json(aj)
            return base, estrellas, pequenas, meta, None
        rgb8 = procesado.procesar(recorte, n, aj, escala=escala, progreso=progreso, balance=balance)
    imagen = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
    if aj["firma"]:
        imagen = firma.firmar(imagen, meta)
    meta["firmada"] = bool(aj["firma"])
    meta["ajustes"] = _ajustes_para_json(aj)
    meta["editada"] = True
    if aj["composicion"]:
        meta["composicion"] = aj["composicion"]
        meta["cometa_nucleo"] = aj["composicion"] == "cometa"
    return imagen, meta


def _reducir(imagen, lado):
    """La imagen (H, W[, C]) con el lado mayor a `lado` px como mucho (INTER_AREA)."""
    h, w = imagen.shape[:2]
    if max(h, w) <= lado:
        return imagen
    f = lado / float(max(h, w))
    return cv2.resize(imagen, (max(8, int(w * f)), max(8, int(h * f))), interpolation=cv2.INTER_AREA)


def capa_firma(ancho, alto, meta):
    """
    La firma como capa BGRA con transparencia del tamaño de la vista previa,
    para que el navegador la pinte ENCIMA de la imagen ya retocada (si fuera
    parte de la base, las curvas y la saturación teñirían el logo y los textos).
    firma.firmar solo sabe pintar sobre una foto: se firma un negro y un blanco
    y de la diferencia salen la transparencia y el color de cada píxel
    (composición "sobre": negro = a·c, blanco = a·c + (1 - a)·255).
    """
    negro = firma.firmar(np.zeros((alto, ancho, 3), dtype=np.uint8), meta).astype(np.int16)
    blanco = firma.firmar(np.full((alto, ancho, 3), 255, dtype=np.uint8), meta).astype(np.int16)
    alfa = np.clip(255 - (blanco - negro).mean(axis=2), 0, 255)
    with np.errstate(divide="ignore", invalid="ignore"):
        color = np.where(alfa[..., None] > 0, negro * 255.0 / np.maximum(alfa[..., None], 1), 0)
    capa = np.dstack([np.clip(color, 0, 255), alfa]).astype(np.uint8)
    return capa


def vista_previa(nombre, opciones):
    """
    Paquete de la vista previa para el navegador: dict con "base" (JPEG, bytes),
    "mascaras" (PNG gris con las dos máscaras lado a lado, estrellas | pequeñas,
    a la mitad del tamaño de la base, bytes), "firma"
    (PNG BGRA con transparencia, bytes) y "fondo" ([r, g, b] del cielo, 0..1),
    todo con el lado mayor a ANCHO_PREVIEW. Con las estrellas aparte, además
    "estrellas" (JPEG de la capa de estrellas sobre negro, del tamaño de la base;
    b"" si no). Solo una a la vez.
    """
    if not _lock_preview.acquire(blocking=False):
        raise ErrorEditor(_("Hay otra vista previa en marcha; espera a que termine"))
    try:
        base, estrellas, pequenas, meta, capa = procesar_foto(nombre, opciones, completa=False, solo_base=True)
        fondo = procesado.fondo_cielo(base)
        base = _reducir(base, ANCHO_PREVIEW)
        alto, ancho = base.shape[:2]
        jpg_capa = b""
        if capa is not None:
            capa8 = np.clip(_reducir(capa, ANCHO_PREVIEW) * 255.0 + 0.5, 0, 255).astype(np.uint8)
            ok, jpg_capa = cv2.imencode(".jpg", cv2.cvtColor(capa8, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_PREVIEW])
            jpg_capa = jpg_capa.tobytes() if ok else b""
        # Máscaras: las dos lado a lado en un solo gris (estrellas | pequeñas), a la
        # mitad del lado de la base y en 16 niveles: son pesos de mezcla suaves y
        # así pesan ~100 KB en vez de 650 KB (el wifi del campo da 0,5-2 Mbit/s)
        mascaras = _reducir(np.hstack([estrellas, pequenas]), ANCHO_PREVIEW)
        mascaras = (np.clip(mascaras * 15.0 + 0.5, 0, 15).astype(np.uint8)) * 17
        base8 = np.clip(base * 255.0 + 0.5, 0, 255).astype(np.uint8)
        ok1, jpg = cv2.imencode(".jpg", cv2.cvtColor(base8, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_PREVIEW])
        ok2, png_m = cv2.imencode(".png", mascaras, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        ok3, png_f = cv2.imencode(".png", capa_firma(ancho, alto, meta), [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if not (ok1 and ok2 and ok3):
            raise ErrorEditor(_("No se pudo codificar la vista previa"))
        return {"base": jpg.tobytes(), "mascaras": png_m.tobytes(), "firma": png_f.tobytes(), "estrellas": jpg_capa,
                "fondo": [round(float(v), 5) for v in fondo], "ancho": int(ancho), "alto": int(alto)}
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
    __, __, ruta_fits, __ = rutas(nombre)
    try:
        clave = (ruta_fits, os.path.getmtime(ruta_fits))
    except OSError:
        raise ErrorEditor(_("Esta foto no tiene FITS guardado; no se puede resolver"))
    if not _lock_norte.acquire(blocking=False):
        raise ErrorEditor(_("Ya se está resolviendo la foto; espera a que termine"))
    try:
        if clave in _cache_norte:
            return _cache_norte[clave]
        h = fits.getheader(ruta_fits)
        norte = procesado.leer_norte(h)
        if norte is None:
            with tempfile.TemporaryDirectory(prefix="scopepower_norte_") as carpeta:
                res = anotaciones.resolver_wcs(ruta_fits, ruta_mono=os.path.join(carpeta, "foto_mono.fits"))
            if res is None:
                raise ErrorEditor(_("ASTAP no ha podido resolver la foto (¿pocas estrellas?)"))
            norte = anotaciones.norte_arriba(res[0], int(h["NAXIS1"]), int(h["NAXIS2"]))
            if norte is None:
                raise ErrorEditor(_("No se pudo calcular la dirección del norte"))
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
    __, __, ruta_fits, __ = rutas(nombre)
    aj = ajustes_desde(opciones)
    cubo, __, __, zona, __ = cargar_cubo(ruta_fits)
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
        raise ErrorEditor(_("Toque no válido"))
    if not (math.isfinite(u) and math.isfinite(v)):
        raise ErrorEditor(_("Toque no válido"))
    M, ancho, alto, W, H = _matriz_encuadre(nombre, opciones)
    px, py = min(max(u, 0.0), 1.0) * ancho - 0.5, min(max(v, 0.0), 1.0) * alto - 0.5
    A, b = M[:, :2], M[:, 2]
    try:
        x, y = np.linalg.solve(A, np.array([px, py]) - b)
    except np.linalg.LinAlgError:
        raise ErrorEditor(_("No se pudo deshacer el encuadre"))
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
    __, ruta_jpg, ruta_fits, __ = rutas(nombre)
    meta = firma.meta_de_archivo(ruta_jpg)
    objeto = " ".join(str(meta.get("objeto") or partes_nombre(nombre)[0].replace("_", " ")).split())
    solucion, __ = galeria_anotada.solucion_de(ruta_fits)
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
            return resultado(p, _("{objeto} centrado", objeto=ficha.get('name') or objeto))
    k = catalogo_offline.clave(objeto)
    candidatos = anotaciones.objetos_catalogo_en_campo(wcs, W, H, margen=0.0)
    for o in candidatos:
        if k and len(k) >= 2 and k in catalogo_offline.clave(o["nombre"]):
            p = pixel(o["ra"], o["dec"])
            if p:
                return resultado(p, _("{objeto} centrado", objeto=o['nombre']))
    # 2. Lo más cercano a las coordenadas de la montura
    coords = _ra_dec_grados(meta.get("ra"), meta.get("dec"))
    if coords is None:
        raise ErrorEditor(_("«{objeto}» no está en el catálogo y la foto no tiene coordenadas; toca la foto para elegir el centro", objeto=objeto))
    if not candidatos:
        candidatos = anotaciones.objetos_ngc_en_campo(wcs, W, H)
    cerca = [(float(anotaciones._separacion_deg(coords[0], coords[1], o["ra"], o["dec"])), o) for o in candidatos]
    cerca = [c for c in cerca if c[0] <= 0.75]
    if cerca:
        __, o = min(cerca, key=lambda c: c[0])
        p = pixel(o["ra"], o["dec"])
        if p:
            return resultado(p, _("{objeto} centrado (lo más cercano a donde apuntaba la montura)", objeto=o['nombre']))
    p = pixel(*coords)
    if p is None:
        raise ErrorEditor(_("Las coordenadas de la foto caen fuera del campo; toca la foto para elegir el centro"))
    return resultado(p, _("centrado donde apuntaba la montura (puede desviarse unos minutos del objeto)"))


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
    nuevo del JPG (o el mismo si no cambia). La pareja del cometa (la foto y su
    '_nucleo') se renombra junta, para que no se pierda la pareja; el rótulo
    "· núcleo" del apilado por el núcleo no cuenta como parte del nombre.
    """
    nombre_actual = os.path.basename(base)
    sufijo = SUFIJO_NUCLEO if nombre_actual.endswith(SUFIJO_NUCLEO) else ""
    base_comun = base[:-len(sufijo)] if sufijo else base
    parte_objeto, fecha = partes_nombre(nombre_actual)
    if sufijo:
        objeto_nuevo = re.sub(r"\s*[·\-–]?\s*n[uú]cleo\s*$", "", str(objeto_nuevo or ""), flags=re.I)
    nuevo_objeto = nombre_archivo_objeto(objeto_nuevo)
    if nuevo_objeto == parte_objeto:
        return nombre_actual + ".jpg"
    if not fecha:
        fecha = datetime.fromtimestamp(os.path.getmtime(base + ".jpg")).strftime("%Y%m%d_%H%M%S")
    nueva_comun = os.path.join(CARPETA_FOTOS, f"{nuevo_objeto}_{fecha}")
    # La foto y, si existe, su pareja del cometa
    def existe(b):
        return any(os.path.isfile(b + ext) for ext in (".jpg", ".fits", ".json"))
    parejas = [(base, nueva_comun + sufijo)]
    otra = base_comun if sufijo else base + SUFIJO_NUCLEO
    if existe(otra):
        parejas.append((otra, nueva_comun if sufijo else nueva_comun + SUFIJO_NUCLEO))
    for __, nueva in parejas:
        if any(os.path.exists(nueva + ext) for ext in (".jpg", ".fits", ".json")):
            raise ErrorEditor(_("Ya existe una foto llamada {nombre}", nombre=os.path.basename(nueva)))
    for vieja, nueva in parejas:
        for ext in (".jpg", ".fits", ".json"):
            if os.path.isfile(vieja + ext):
                os.replace(vieja + ext, nueva + ext)
        estrellas_aparte.renombrar_capas(vieja, nueva)
        orig = ruta_original(vieja)
        if os.path.isfile(orig):
            os.replace(orig, ruta_original(nueva))
            orig_json = os.path.splitext(orig)[0] + ".json"
            if os.path.isfile(orig_json):
                os.replace(orig_json, os.path.splitext(ruta_original(nueva))[0] + ".json")
        log.info("Foto renombrada: %s -> %s", os.path.basename(vieja), os.path.basename(nueva))
    return os.path.basename(nueva_comun + sufijo) + ".jpg"


def guardar(nombre, opciones):
    """
    Lanza el guardado en un hilo: procesado a resolución completa, firma, JPG y
    JSON (con los ajustes usados) y renombrado si cambia el objeto. Estado en EDICION.
    """
    if EDICION["en_curso"]:
        raise ErrorEditor(_("Ya hay un guardado en curso"))
    base, ruta_jpg, ruta_fits, ruta_json = rutas(nombre)
    if not os.path.isfile(ruta_fits):
        raise ErrorEditor(_("Esta foto no tiene FITS guardado; no se puede reprocesar"))
    EDICION.update({"en_curso": True, "ok": None, "archivo": nombre, "nombre_nuevo": "",
                    "mensaje": _("Procesando a resolución completa...")})

    def _trabajo():
        try:
            def progreso(subfase, fraccion):
                EDICION["mensaje"] = _("Procesando a resolución completa ({p} %)...", p=int(fraccion * 100))
            imagen, meta = procesar_foto(nombre, opciones, completa=True, progreso=progreso)
            EDICION["mensaje"] = _("Guardando...")
            _apartar_original(base, ruta_jpg, ruta_json)
            cv2.imwrite(ruta_jpg, imagen, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_GUARDADO])
            firma.guardar_meta(ruta_jpg, meta)
            nuevo = _renombrar(base, meta["objeto"])
            EDICION.update({"ok": True, "nombre_nuevo": nuevo,
                            "mensaje": _("Foto guardada como {nombre}", nombre=nuevo) if nuevo != nombre else _("Foto guardada")})
            log.info("Foto %s reprocesada desde el editor (%s)", nuevo, meta["ajustes"])
        except Exception as e:
            log.exception("Error guardando la foto editada")
            EDICION.update({"ok": False, "mensaje": _("Error al guardar: {e}", e=e)})
        finally:
            EDICION["en_curso"] = False

    threading.Thread(target=_trabajo, name="editor-guardar", daemon=True).start()
    return dict(EDICION)


def restaurar(nombre):
    """Vuelve a poner el JPG y el JSON originales (los apartados la primera vez que se guardó)."""
    base, ruta_jpg, __, ruta_json = rutas(nombre)
    orig = ruta_original(base)
    if not os.path.isfile(orig):
        raise ErrorEditor(_("Esta foto no tiene original apartado"))
    os.replace(orig, ruta_jpg)
    orig_json = os.path.splitext(orig)[0] + ".json"
    if os.path.isfile(orig_json):
        os.replace(orig_json, ruta_json)
    elif os.path.isfile(ruta_json):
        os.remove(ruta_json)
    log.info("Foto %s restaurada a su original", nombre)


def borrar_original(base):
    """Al eliminar una foto de la galería, quitar también su original apartado y sus capas de estrellas."""
    for ext in (".jpg", ".json"):
        ruta = os.path.splitext(ruta_original(base))[0] + ext
        if os.path.isfile(ruta):
            os.remove(ruta)
    estrellas_aparte.borrar_capas(base)


def jpg_original(nombre):
    """Ruta del JPG que se muestra como 'original' en el editor: el apartado si lo hay, si no el actual."""
    base, ruta_jpg, __, __ = rutas(nombre)
    orig = ruta_original(base)
    return orig if os.path.isfile(orig) else ruta_jpg
