# publicar.py — galería pública: una copia estática de las fotos marcadas, subida al hosting
#
# La galería de ScopePower solo se ve con el mini PC encendido y desde su red. Para enseñar
# las fotos desde el móvil (con el mini PC apagado) se publica una copia estática en el
# hosting del usuario: por cada foto marcada con «Publicar» en la galería, un JPG reducido,
# una miniatura, la versión de 1024 px que usa el planetario (si la foto tiene posición), las
# anotaciones (si ya están calculadas: al publicar no se lanza ASTAP) y un JSON público; más
# un índice galeria.json (con las gráficas del Sol). En la carpeta del propietario solo van .jpg
# y .json. El visor (las páginas de templates/publico, genéricas: el título y el autor los pone el
# JS leyendo galeria.json) y lo común (JS/CSS de static/publico, motor del planetario,
# traducciones) van a _comun/<versión>/, compartido entre propietarios del mismo hosting, y
# solo puede subirlo el dueño del hosting (su clave está en publicar_config.php). El endpoint
# copia las páginas de la versión más alta que tenga a la carpeta de cada propietario al
# registrarlo y con cada galeria.json: un ScopePower más nuevo que el hosting usa las páginas
# que haya, por eso el formato de galeria.json se mantiene compatible.
#
#   <url_base>/galeria/<nombre>/            la galería pública del propietario
#   <url_base>/scopepower/publicar.php      el endpoint que recibe los ficheros (hosting/)
#
# Qué sale y qué no: solo JPG (nunca FITS ni vídeos) y un JSON por lista blanca (objeto,
# tipo, fecha, exposición, coordenadas del objeto, nota, autor): ni latitud ni longitud del
# observatorio, ni ajustes, ni rutas. El observador del planetario público va redondeado
# a grados enteros ([Publicar] coordenadas) o no va.
#
# La subida es una cola persistente (datos/publicar.json) que un hilo a prioridad baja va
# vaciando de un fichero en uno, pensada para el wifi del campo (0,5–2 Mbit/s): si no hay
# red espera y reintenta, y la app no se entera. El índice galeria.json se sube el último y
# los borrados van después, así la galería pública nunca apunta a un fichero que no está.
# Un replanteo cada 15 minutos recoge lo que cambie por fuera (editor, nota, renombrados,
# borrados) sin necesitar ganchos en cada sitio.
#
# Nombres repetidos: el dueño del hosting da de alta cada nombre con su clave en
# publicar_config.php; la clave manda sobre el nombre, así que otra instalación con el mismo
# nombre y otra clave recibe un 401 y no puede tocar ni leer esa carpeta.
#
# Quien no es el dueño recibe un 403 al subir a _comun/ o el .htaccess: no es un error, es que no
# le toca. Se anota una vez en el log y no se vuelve a encolar en lo que dure la app.
#
# Qué foto se puede publicar: las que llevan las coordenadas del objeto (ra y dec: del JSON hermano
# o de la cabecera del FITS), y las del Sol y la Luna, que no las necesitan; las de tipo Paisaje,
# nunca. El endpoint exige lo mismo (un freno a fotos que no salen de ScopePower, no un filtro de
# contenido), así que la app avisa al marcar en vez de encolarla y fallar.

import os
import re
import json
import time
import shutil
import hashlib
import logging
import secrets as _secrets
import threading
import configparser
from datetime import datetime, timezone

import requests
from unidecode import unidecode

import firma
import secretos
import version
import catalogo_offline
import fondo_planetario
import fotometria_solar
import galeria_anotada
import luna_anotada
import manchas_solares
from editor_fotos import rutas, partes_nombre, CARPETA_FOTOS, ErrorEditor
from idioma import _

log = logging.getLogger("scopepower.publicar")

SECCION = "Publicar"
RUTA_ESTADO = os.path.join("datos", "publicar.json")
CARPETA_SALIDA = os.path.join("datos", "publicar")
CARPETA_PAGINAS = os.path.join(CARPETA_SALIDA, "_paginas")      # las páginas genéricas del visor, antes de ir a _comun
CARPETA_PLANTILLAS = os.path.join("templates", "publico")
CARPETA_ESTATICOS = os.path.join("static", "publico")
CARPETA_ATLAS = os.path.join("static", "atlas")
RUTA_HTACCESS = os.path.join("hosting", "galeria.htaccess")
CABECERA_CLAVE = "X-ScopePower-Clave"
# Los cortafuegos de algunos hostings devuelven 403 al agente "python-requests": la app se presenta con su nombre
AGENTE = f"ScopePower/{version.VERSION} (galeria publica)"
CLAVE_SECRETO = "publicar_clave"
TIMEOUT = (15, 180)                           # conexión, lectura
TROZO = 1_000_000                             # bytes por envío: por debajo del límite de 2 MB que traen PHP y muchos hostings
ESPERAS_REINTENTO = (30, 60, 120, 300, 900)   # tras cada fallo del hosting, en segundos
ESPERA_SIN_RED = 60
INTERVALO_REVISION = 15 * 60
IDIOMAS_VALIDOS = ("es", "en")
# Lo que el endpoint admite en _comun/ (hosting/publicar.php): nada de código ejecutable
EXTENSIONES_PUBLICAS = (".jpg", ".jpeg", ".webp", ".png", ".json", ".html", ".js", ".css", ".md", ".txt", ".ico")
NOMBRES_PUBLICOS = ("LICENSE", "properties")
# En la carpeta del propietario, solo esto (las páginas las coloca el endpoint)
EXTENSIONES_GALERIA = (".jpg", ".json")
# Lo que sale de cada objeto anotado, además de su posición y su tamaño (fuente: cielo; familia, luz y rango: Luna)
CAMPOS_OBJETO = ("nombre", "angulo", "mag", "fuente", "familia", "luz", "rango")
# Lo que sale de cada grupo de manchas, además de su posición: tamaños y coordenadas sobre el Sol, no dependen del JPG
CAMPOS_GRUPO = ("id", "n", "lat", "dlon", "km2", "diametro_km", "largo_km")
FORMATO_ANOTACIONES = 4                       # sube si cambia lo que se publica: se regeneran todas
TIPOS_SIN_COORDENADAS = ("Sol", "Luna")       # como TIPOS_SIN_COORDENADAS en hosting/publicar.php

# El hosting de serie: scopepower.es admite altas desde la app (registro abierto): basta un nombre libre
HOSTING_PUBLICO = "https://scopepower.es"
DEFECTOS = {"activado": "false", "url_base": HOSTING_PUBLICO, "endpoint": "", "nombre": "", "autor_publico": "",
            "lado_foto": "1600", "lado_mini": "480", "calidad": "85", "coordenadas": "redondeadas",
            "redondeo_grados": "1", "idiomas": "es,en"}


class ErrorHosting(Exception):
    """El hosting no ha aceptado la operación (o no responde)."""


class ErrorRed(ErrorHosting):
    """No hay conexión con el hosting (sin red en el campo): se espera sin contar el intento."""


class ErrorClave(ErrorHosting):
    """La clave no es la de ese nombre: cola parada hasta que el usuario lo arregle."""


class ErrorTamano(ErrorHosting):
    """El fichero pasa del límite del hosting: se salta."""


class ErrorNombre(ErrorHosting):
    """El nombre ya es de otro (o está reservado): cola parada hasta que el usuario elija otro."""


class ErrorCuota(ErrorHosting):
    """La galería ha agotado su cuota en el hosting: cola parada hasta que retire fotos."""


class ErrorPermiso(ErrorHosting):
    """El endpoint no deja tocar esa ruta a este nombre (lo común y el .htaccess son del dueño): se salta."""


class ErrorContenido(ErrorHosting):
    """El endpoint no acepta el contenido del fichero (422: no es un JPEG, JSON fuera de la lista blanca): se salta."""


# ───────────────────────── Configuración ─────────────────────────
def slug(texto):
    """'Enrique Jiménez' -> 'enrique-jimenez'; '' si no queda un nombre válido para la URL."""
    s = unidecode(str(texto or "")).lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", s) else ""


def url_valida(url):
    """Solo https, salvo el propio PC para las pruebas: la clave viaja en una cabecera."""
    return bool(re.match(r"https://[^/\s]+", url or "")) or bool(re.match(r"http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url or ""))


def clave():
    """La clave de esta instalación; si no la hay, se genera y se guarda en secretos.ini (nadie tiene que verla)."""
    actual = str(secretos.obtener(CLAVE_SECRETO, "") or "").strip()
    if not actual:
        actual = _secrets.token_urlsafe(32)
        secretos.guardar(CLAVE_SECRETO, actual)
        log.info("Galería pública: clave nueva generada y guardada en secretos.ini")
    return actual


def leer_config(ruta="config.ini"):
    """[Publicar] de config.ini con los valores por defecto, el slug ya resuelto y si está todo para publicar."""
    cfg = configparser.ConfigParser()
    cfg.read(ruta)          # misma codificación que el resto de la app
    s = cfg[SECCION] if SECCION in cfg else {}
    obs = cfg["Observatorio"] if "Observatorio" in cfg else {}
    d = {k: str(s.get(k, v)).strip() for k, v in DEFECTOS.items()}
    d["activado"] = d["activado"].lower() == "true"
    for k, minimo, maximo in (("lado_foto", 400, 4000), ("lado_mini", 120, 1200), ("calidad", 50, 95), ("redondeo_grados", 1, 10)):
        try:
            d[k] = max(minimo, min(maximo, int(float(d[k]))))
        except ValueError:
            d[k] = int(DEFECTOS[k])
    d["coordenadas"] = d["coordenadas"].lower() if d["coordenadas"].lower() in ("redondeadas", "ninguna") else "redondeadas"
    d["idiomas"] = [i.strip().lower() for i in d["idiomas"].split(",") if i.strip().lower() in IDIOMAS_VALIDOS] or ["es"]
    d["autor"] = str(obs.get("autor", "")).strip()
    d["autor_publico"] = d["autor_publico"] or d["autor"]
    d["nombre"] = slug(d["nombre"] or d["autor"])
    d["url_base"] = d["url_base"].rstrip("/")
    d["endpoint"] = d["endpoint"] or (d["url_base"] + "/scopepower/publicar.php" if d["url_base"] else "")
    try:
        d["latitud"], d["longitud"] = float(obs.get("latitud_observatorio", 0)), float(obs.get("longitud_observatorio", 0))
    except ValueError:
        d["latitud"], d["longitud"] = 0.0, 0.0
    d["clave"] = clave()
    d["hosting_publico"] = d["url_base"].rstrip("/") == HOSTING_PUBLICO
    d["configurado"] = bool(d["activado"] and url_valida(d["url_base"]) and d["nombre"] and d["clave"])
    return d


def url_publica(cfg=None, foto=None):
    """La dirección de la galería pública (y de una foto: #<id>), o '' si no está configurada."""
    cfg = cfg or leer_config()
    if not cfg["url_base"] or not cfg["nombre"]:
        return ""
    url = f"{cfg['url_base']}/galeria/{cfg['nombre']}/"
    return url + ("#" + os.path.splitext(os.path.basename(foto))[0] if foto else "")


def por_que_no(cfg=None):
    """Qué falta para publicar, en palabras para el usuario; '' si no falta nada."""
    cfg = cfg or leer_config()
    if not cfg["activado"]:
        return _("La galería pública está desactivada en la configuración ([Publicar] activado).")
    if not url_valida(cfg["url_base"]):
        return _("Falta la dirección del hosting ([Publicar] url_base, con https://).")
    if not cfg["nombre"]:
        return _("Falta un nombre válido para la galería pública ([Publicar] nombre o [Observatorio] autor).")
    return ""


# ───────────────────────── Selección ─────────────────────────
def por_que_no_publicable(nombre, meta):
    """
    Por qué una foto no puede ir a la galería pública (en palabras para el usuario), o '' si puede: el
    endpoint rechaza el índice si una foto no lleva coordenadas del objeto y no es del Sol ni la Luna,
    y las de tipo Paisaje no se publican.
    """
    meta = meta if isinstance(meta, dict) else {}
    objeto = str(meta.get("objeto") or partes_nombre(nombre)[0].replace("_", " "))
    tipo = catalogo_offline.categoria_objeto(str(meta.get("tipo") or ""), objeto) or ""
    if tipo == "Paisaje":
        return _("Las fotos de tipo Paisaje no se publican en la galería pública.")
    if tipo not in TIPOS_SIN_COORDENADAS and not (_texto(meta.get("ra")) and _texto(meta.get("dec"))):
        return _("Esta foto no tiene coordenadas del objeto y no se puede publicar en la galería pública (solo las del Sol y la Luna pueden ir sin ellas).")
    return ""


def marcar(nombre, publicada=True):
    """
    Marca (o retira) una foto de la galería para la galería pública y pone la cola en marcha. Una foto
    sin coordenadas (y que no sea del Sol ni la Luna) o de tipo Paisaje no se marca: ErrorEditor con el porqué.
    """
    __, ruta_jpg, __, __ = rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditor(_("La foto no existe"))
    meta = firma.meta_de_archivo(ruta_jpg) or {}
    if publicada:
        motivo = por_que_no_publicable(nombre, meta)
        if motivo:
            raise ErrorEditor(motivo)
    meta["publicada"] = bool(publicada)
    firma.guardar_meta(ruta_jpg, meta)
    log.info("Galería pública: %s %s", nombre, "publicada" if publicada else "retirada")
    sincronizar()
    return meta


def publicadas():
    """{nombre del JPG: metadatos} de las fotos marcadas para publicar."""
    salida = {}
    if not os.path.isdir(CARPETA_FOTOS):
        return salida
    for f in sorted(os.listdir(CARPETA_FOTOS)):
        if not f.lower().endswith(".jpg"):
            continue
        ruta_json = os.path.join(CARPETA_FOTOS, os.path.splitext(f)[0] + ".json")
        if not os.path.isfile(ruta_json):
            continue
        try:
            with open(ruta_json, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("publicada") is True:
            salida[f] = meta
    return salida


# ───────────────────────── Copia pública de cada foto ─────────────────────────
def _sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def _reducir(ruta_jpg, destino, lado, calidad):
    """JPG con el lado mayor a `lado` px (o igual si ya es menor), sin metadatos. Devuelve (ancho, alto)."""
    import cv2
    bgr = cv2.imread(ruta_jpg, cv2.IMREAD_COLOR)
    if bgr is None:
        raise OSError(f"No se puede leer {ruta_jpg}")
    h, w = bgr.shape[:2]
    if max(h, w) > lado:
        f = lado / float(max(h, w))
        bgr = cv2.resize(bgr, (max(8, int(round(w * f))), max(8, int(round(h * f)))), interpolation=cv2.INTER_AREA)
    tmp = destino + ".tmp.jpg"
    if not cv2.imwrite(tmp, bgr, [cv2.IMWRITE_JPEG_QUALITY, int(calidad)]):
        raise OSError(f"No se pudo escribir {destino}")
    os.replace(tmp, destino)
    return bgr.shape[1], bgr.shape[0]


def _dimensiones(ruta):
    from PIL import Image
    with Image.open(ruta) as im:
        return im.size


def _fecha_iso(fecha, fecha_nombre):
    """'29/09/2026 16:56' o '20260929_165652' -> '2026-09-29T16:56'; '' si no hay fecha."""
    for texto, formato in ((fecha, "%d/%m/%Y %H:%M"), (fecha, "%d/%m/%Y"), (fecha_nombre, "%Y%m%d_%H%M%S")):
        try:
            return datetime.strptime(str(texto or ""), formato).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return ""


def _numero(v, tipo=float):
    try:
        return tipo(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _texto(v):
    return str(v) if isinstance(v, (str, int, float)) and str(v) != "" else None


def meta_publica(nombre, meta, cfg):
    """
    Lo que se publica de una foto, por lista blanca: nada del observatorio (latitud, longitud),
    de los ajustes ni de las rutas. `tipo` va en español (clave del catálogo): el visor lo traduce.
    """
    meta = meta if isinstance(meta, dict) else {}
    partes = partes_nombre(nombre)
    objeto = str(meta.get("objeto") or partes[0].replace("_", " "))
    nota = meta.get("nota") if isinstance(meta.get("nota"), dict) else None
    video = meta.get("video") if isinstance(meta.get("video"), dict) else None
    wolf = meta.get("wolf") if isinstance(meta.get("wolf"), dict) else None
    return {
        "id": os.path.splitext(nombre)[0],
        "objeto": objeto,
        "tipo": catalogo_offline.categoria_objeto(str(meta.get("tipo") or ""), objeto) or "",
        "fecha": str(meta.get("fecha") or ""),
        "fecha_iso": _fecha_iso(meta.get("fecha"), partes[1]),
        "n_fotos": _numero(meta.get("n_fotos"), int),
        "exposicion": _numero(meta.get("exposicion")),
        "ra": _texto(meta.get("ra")),
        "dec": _texto(meta.get("dec")),
        "nota": {k: _numero(nota.get(k)) for k in ("puntuacion", "estrellas", "fwhm")} if nota else None,
        "autor": cfg["autor_publico"],
        "mosaico": bool(meta.get("mosaico")),
        "cometa_nucleo": bool(meta.get("cometa_nucleo")),
        "wolf": {k: _numero(wolf.get(k)) for k in ("R", "g", "f", "k") if k in wolf} if wolf else None,
        "video": {k: _numero(video.get(k)) for k in ("frames", "duracion_s") if k in video} if video else None,
    }


def _huella(nombre, ruta_jpg, ruta_fits):
    """La huella en el cielo que ya calculó el planetario (caché) o, si no está, calculada ahora (solo cabecera + WCS)."""
    if not os.path.isfile(ruta_fits):
        return None
    sj, sf = os.stat(ruta_jpg), os.stat(ruta_fits)
    clave_cache = f"{nombre}|{sj.st_size}_{sj.st_mtime_ns}|{sf.st_size}_{sf.st_mtime_ns}"
    h = fondo_planetario._leer_cache().get(clave_cache)
    if h and h.get("esquinas"):
        return h
    try:
        return fondo_planetario._huella(nombre)
    except Exception as e:
        log.debug("Galería pública: %s sin huella en el cielo (%s)", nombre, e)
        return None


def _origen_cielo(nombre, base_local, ruta_fits):
    """(ruta, modo) de la versión para el planetario: la de campo completo si ya está revelada, si no el JPG reducido."""
    completa = fondo_planetario._ruta_completa(base_local, os.stat(ruta_fits), fondo_planetario.LADO_MINIATURA)
    if os.path.isfile(completa):
        return completa, "completa"
    return fondo_planetario.miniatura(nombre), "jpg"


def anotaciones_publicas(d, ancho, alto):
    """
    Lo que se publica de las anotaciones de una foto (lo que da /galeria/anotaciones/<nombre>), por
    lista blanca y en píxeles del JPG publicado (ancho × alto): lo justo para que el visor las pinte
    como la app. Del Sol y la Luna, además, el disco y lo que los pintores necesitan, nunca
    desde dónde se hizo la foto. None si no hay nada que pintar.
    """
    f = ancho / float(d["ancho"])
    px = lambda v: round(float(v) * f, 1)
    salida = {"ancho": int(ancho), "alto": int(alto)}
    if d.get("sol") or d.get("luna"):
        salida["disco"] = {"cx": px(d["disco"]["cx"]), "cy": px(d["disco"]["cy"]), "r": px(d["disco"]["r"])}
    if d.get("sol"):
        if not d.get("manchas"):
            return None
        salida["disco"]["cortado"] = bool(d["disco"].get("cortado"))
        salida.update({"sol": True, "wolf": {k: d["wolf"][k] for k in ("g", "f", "R", "k")},
                       "manchas": [{"x": px(m["x"]), "y": px(m["y"]), "r_px": px(m["r_px"]), "umbra": bool(m.get("umbra")),
                                    "grupo": int(m["grupo"])} for m in d["manchas"]],
                       "grupos": [{**{k: g[k] for k in CAMPOS_GRUPO if k in g}, "x": px(g["x"]), "y": px(g["y"])} for g in d["grupos"]],
                       "resolucion_km": d.get("resolucion_km")})
        return salida
    if not d.get("objetos"):
        return None
    salida["objetos"] = [{**{k: o[k] for k in CAMPOS_OBJETO if k in o}, "x": px(o["x"]), "y": px(o["y"]), "a": px(o["a"]), "b": px(o["b"])}
                         for o in d["objetos"]]
    if d.get("luna"):
        # El trazado de los mares son números sueltos («M x y l dx dy...»): se escalan todos
        mares = re.sub(r"-?\d+(?:\.\d+)?", lambda m: f"{px(m.group()):g}", d.get("mares") or "")
        salida.update({"luna": True, "mares": mares, "terminador": [[px(x), px(y)] for x, y in d.get("terminador") or []],
                       "iluminada": float(d["iluminada"]), "dudosa": bool(d.get("dudosa"))})
    return salida


def _solucion_guardada(ruta_fits):
    """La solución del campo ya calculada (la de la galería anotada, con SIMBAD, o el WCS del FITS); None si no la hay."""
    if not os.path.isfile(ruta_fits):
        return None
    st = os.stat(ruta_fits)
    return galeria_anotada._leer_cache().get(f"{st.st_size}_{st.st_mtime_ns}") or fondo_planetario._solucion_de(ruta_fits)


def _origen_anotaciones(nombre, meta, ruta_fits, cfg):
    """
    (sello, calcular) de las anotaciones de una foto: el sello cambia si cambia aquello de lo que
    salen, y calcular() las da como la ruta /galeria/anotaciones de la app (None si no se puede
    saber sin resolver nada). La Luna y el Sol necesitan el observatorio; no llega a publicarse.
    """
    obs = (cfg["latitud"], cfg["longitud"]) if cfg["latitud"] or cfg["longitud"] else None
    if manchas_solares.es_sol(nombre, meta):
        conteo = hashlib.sha256(json.dumps(meta.get("wolf"), sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return f"sol|{obs}|{conteo}", lambda: manchas_solares.objetos_foto(nombre, obs)    # el conteo corregido no toca el JPG
    if luna_anotada.es_luna(nombre, meta):
        return f"luna|{obs}", lambda: luna_anotada.objetos_foto(nombre, obs)
    solucion = _solucion_guardada(ruta_fits)
    if not solucion:
        return "sin solucion", None
    return "cielo|" + hashlib.sha256(json.dumps(solucion, sort_keys=True).encode("utf-8")).hexdigest()[:16], lambda: galeria_anotada.objetos_foto(nombre, solucion)


def _preparar_anotaciones(nombre, meta, ruta_fits, cfg, sellos, destino):
    """Deja en `destino` las anotaciones públicas de la foto (o nada) si ha cambiado su origen; True si las lleva."""
    try:
        sello, calcular = _origen_anotaciones(nombre, meta, ruta_fits, cfg)
    except Exception as e:
        log.debug("Galería pública: %s sin anotaciones (%s)", nombre, e)
        sello, calcular = "sin solucion", None
    sello = f"{FORMATO_ANOTACIONES}|{sello}"
    if sellos.get("anotaciones") == sello:
        return os.path.isfile(destino)
    publicas = None
    if calcular:
        try:
            publicas = anotaciones_publicas(calcular(), sellos["ancho"], sellos["alto"])
        except ErrorEditor as e:             # la Luna no se ve, el Sol no se encuentra...
            log.info("Galería pública: %s sin anotaciones (%s)", nombre, e)
        except Exception as e:
            log.warning("Galería pública: %s sin anotaciones (%s)", nombre, e)
    if publicas:
        _escribir_json(destino, publicas)
    elif os.path.isfile(destino):
        os.remove(destino)
    sellos["anotaciones"] = sello
    return bool(publicas)


def _preparar_foto(nombre, meta, cfg, salida, derivadas):
    """
    Genera (si hace falta) las derivadas de una foto en `salida` y devuelve su ficha para el índice
    con la lista de ficheros locales: {ruta remota relativa a galeria/: ruta local}.
    """
    base_local, ruta_jpg, ruta_fits, __ = rutas(nombre)
    base = os.path.splitext(nombre)[0]
    sellos = derivadas.setdefault(base, {})
    sj = os.stat(ruta_jpg)
    d_jpg, d_mini = os.path.join(salida, base + ".jpg"), os.path.join(salida, base + "_mini.jpg")
    sello_jpg = f"{sj.st_size}_{sj.st_mtime_ns}_{cfg['lado_foto']}_{cfg['lado_mini']}_{cfg['calidad']}"
    if sellos.get("jpg") != sello_jpg or not (os.path.isfile(d_jpg) and os.path.isfile(d_mini)):
        ancho, alto = _reducir(ruta_jpg, d_jpg, cfg["lado_foto"], cfg["calidad"])
        _reducir(ruta_jpg, d_mini, cfg["lado_mini"], cfg["calidad"])
        sellos.update({"jpg": sello_jpg, "ancho": ancho, "alto": alto})
        sellos.pop("anotaciones", None)      # van en píxeles de este JPG

    cielo, d_cielo = None, os.path.join(salida, base + "_cielo.jpg")
    huella = _huella(nombre, ruta_jpg, ruta_fits)
    if huella:
        try:
            origen, modo = _origen_cielo(nombre, base_local, ruta_fits)
            so = os.stat(origen)
            sello_cielo = f"{os.path.basename(origen)}|{so.st_size}_{so.st_mtime_ns}"
            if sellos.get("cielo") != sello_cielo or not os.path.isfile(d_cielo):
                shutil.copyfile(origen, d_cielo + ".tmp.jpg")
                os.replace(d_cielo + ".tmp.jpg", d_cielo)
                sellos["cielo"] = sello_cielo
            cw, ch = _dimensiones(d_cielo)
            completa = modo == "completa" and huella.get("esquinas_completa")
            cielo = {"esquinas": huella["esquinas_completa"] if completa else huella["esquinas"],
                     "ancho": int(cw), "alto": int(ch), "modo": modo,
                     "area_deg2": huella.get("area_completa_deg2") if completa else huella.get("area_deg2")}
        except Exception as e:
            log.warning("Galería pública: %s sin versión para el planetario (%s)", nombre, e)
            cielo = None
    if cielo is None:
        sellos.pop("cielo", None)
        if os.path.isfile(d_cielo):
            os.remove(d_cielo)
    anotada = _preparar_anotaciones(nombre, meta, ruta_fits, cfg, sellos, os.path.join(salida, base + "_anotaciones.json"))

    ficha = meta_publica(nombre, meta, cfg)
    ficha.update({"ancho": sellos.get("ancho"), "alto": sellos.get("alto"), "cielo": cielo,
                  "ficheros": {"jpg": base + ".jpg", "mini": base + "_mini.jpg", **({"cielo": base + "_cielo.jpg"} if cielo else {}),
                               **({"anotaciones": base + "_anotaciones.json"} if anotada else {})}})
    locales = {ficha["ficheros"][k]: os.path.join(salida, ficha["ficheros"][k]) for k in ficha["ficheros"]}
    ficha["v"] = {k: _sha256(locales[ficha["ficheros"][k]])[:10] for k in ficha["ficheros"]}
    d_json = os.path.join(salida, base + ".json")
    _escribir_json(d_json, ficha)
    locales[base + ".json"] = d_json
    return ficha, locales


def _escribir_json(ruta, datos):
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)


def sol_publico(fotos):
    """
    Las gráficas del Sol del visor (Número de Wolf y diámetro frente a la efeméride, con la órbita si
    hay datos) por lista blanca: como en la app, con todas las medidas de las fotos que siguen en la
    galería, publicadas o no (decisión del usuario: sale el número y la fecha, nunca la foto). Solo si
    hay alguna foto del Sol publicada, que es cuando el visor tiene filtro Sol; si no, None.
    """
    if not any(manchas_solares.es_sol(f, meta) for f, meta in fotos.items()):
        return None
    try:
        wolf = [{k: d[k] for k in ("fecha", "g", "f", "R", "k", "n")} for d in manchas_solares.serie_diaria()]
        serie = fotometria_solar.serie(set(os.listdir(CARPETA_FOTOS)))
        if not wolf and not serie:
            return None
        return {"wolf": wolf, "curva": fotometria_solar.curva_anual(serie), "orbita": fotometria_solar.ajustar_orbita(serie),
                "diametro": [{k: s[k] for k in ("instante", "r_arcsec", "r_efem", "desvio_pct", "distancia", "fiable")} for s in serie]}
    except Exception as e:
        log.warning("Galería pública: sin las gráficas del Sol (%s)", e)
        return None


def observador_publico(cfg):
    """Posición redondeada del observatorio para el planetario público, o None si no se publica."""
    if cfg["coordenadas"] != "redondeadas" or (cfg["latitud"] == 0 and cfg["longitud"] == 0):
        return None
    r = max(1, int(cfg["redondeo_grados"]))
    return {"latitud": float(round(cfg["latitud"] / r) * r), "longitud": float(round(cfg["longitud"] / r) * r), "redondeo_grados": r}


# ───────────────────────── Visor y ficheros comunes ─────────────────────────
def _traduccion(codigo):
    import gettext
    import idioma
    if codigo == idioma.POR_DEFECTO:
        return gettext.NullTranslations()
    return gettext.translation(idioma.DOMINIO, idioma.CARPETA, languages=[codigo], fallback=True)


def visor(salida=CARPETA_PAGINAS):
    """
    Renderiza las plantillas de templates/publico/*.html en español (raíz) y en inglés (en/) con un
    entorno Jinja propio, sin tocar el idioma de la app. Son páginas genéricas, iguales para todos los
    propietarios del hosting: ni autor, ni nombre, ni dirección; eso lo pone el JS leyendo galeria.json.
    Devuelve {ruta relativa a _comun/<versión>/publico/paginas/: ruta local}. Sin plantillas, nada.
    """
    if not os.path.isdir(CARPETA_PLANTILLAS):
        return {}
    import jinja2
    import idioma
    plantillas = sorted(p for p in os.listdir(CARPETA_PLANTILLAS) if p.endswith(".html"))
    if not plantillas:
        return {}
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(CARPETA_PLANTILLAS), extensions=["jinja2.ext.i18n"],
                             autoescape=True, trim_blocks=True, lstrip_blocks=True)
    generados = {}
    for codigo in IDIOMAS_VALIDOS:
        trad = _traduccion(codigo)
        # Como en la app: _("{n} fotos", n=...) rellena los marcadores (idioma._formatear)
        env.install_gettext_callables(
            lambda mensaje, _t=trad, **valores: idioma._formatear(_t.gettext(mensaje), valores),
            lambda singular, plural, n, _t=trad, **valores: idioma._formatear(_t.ngettext(singular, plural, n), dict(valores, n=n)),
            newstyle=False)
        sub = "" if codigo == idioma.POR_DEFECTO else codigo + "/"
        raiz = "." if not sub else ".."
        contexto = {"idioma": codigo, "lang": idioma._LANG.get(codigo, codigo), "raiz": raiz,
                    "comun": f"{raiz}/../_comun/{version.VERSION}", "version": version.VERSION,
                    "idiomas": list(IDIOMAS_VALIDOS), "por_defecto": idioma.POR_DEFECTO}
        for p in plantillas:
            html = env.get_template(p).render(**contexto)
            destino = os.path.join(salida, sub, p)
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino + ".tmp", "w", encoding="utf-8", newline="\n") as f:
                f.write(html)
            os.replace(destino + ".tmp", destino)
            generados[sub + p] = destino
    return generados


def archivos_comun():
    """
    {ruta relativa a _comun/<versión>/: ruta local} de lo que comparten todos los propietarios de un
    hosting: las páginas del visor (publico/paginas/, que el endpoint copia a cada galería),
    traducciones y JS/CSS del visor (static/publico) y, si hay planetario público, el motor del atlas
    entero (6,6 MB, solo rutas relativas: se copia tal cual). Solo lo sube el dueño del hosting.
    """
    salida = {}
    for rel, local in visor().items():
        salida["publico/paginas/" + rel] = local
    for rel, local in (("js/i18n.js", "static/js/i18n.js"), ("i18n/en.js", "static/i18n/en.js"),
                       ("js/atlas_catalogos.js", "static/js/atlas_catalogos.js"), ("js/capa_fotos.js", "static/js/capa_fotos.js"),
                       ("js/luna_anotada.js", "static/js/luna_anotada.js"), ("js/manchas_solares.js", "static/js/manchas_solares.js"),
                       ("js/fotometria_solar.js", "static/js/fotometria_solar.js"),
                       ("publico/icono.png", "static/icono-192.png"), ("publico/scopepower.ico", "static/scopepower.ico")):
        if os.path.isfile(local):
            salida[rel] = local
    if os.path.isdir(CARPETA_ESTATICOS):
        for f in sorted(os.listdir(CARPETA_ESTATICOS)):
            local = os.path.join(CARPETA_ESTATICOS, f)
            if os.path.isfile(local) and f.lower().endswith(EXTENSIONES_PUBLICAS):
                salida["publico/" + f] = local
    if os.path.isfile(os.path.join(CARPETA_PLANTILLAS, "planetario.html")) and os.path.isdir(CARPETA_ATLAS):
        for carpeta, subcarpetas, ficheros in os.walk(CARPETA_ATLAS):
            subcarpetas[:] = sorted(s for s in subcarpetas if s != "licenses")
            for f in sorted(ficheros):
                if not (f.lower().endswith(EXTENSIONES_PUBLICAS) or f in NOMBRES_PUBLICOS):
                    continue
                local = os.path.join(carpeta, f)
                rel = os.path.relpath(local, CARPETA_ATLAS).replace(os.sep, "/")
                salida["atlas/" + rel] = local
    return salida


# ───────────────────────── Estado persistente y plan ─────────────────────────
_lock = threading.Lock()
_despertar = threading.Event()
_hilo = None
_replanificar = False
_verificar_remoto = True
_sin_permiso_comun = False       # el endpoint ha dicho que lo común no es de este nombre: no se vuelve a encolar
ESTADO = {"activo": False, "ocupado": False, "subiendo": "", "pendientes": 0, "bytes_pendientes": 0,
          "fotos": 0, "ultimo_ok": "", "ultimo_error": "", "error_clave": False, "parada": False, "sin_red": False}
# Errores que paran la cola hasta que el usuario arregle algo (clave, nombre, cuota)
ERRORES_QUE_PARAN = (ErrorClave, ErrorNombre, ErrorCuota)
THREAD_PRIORITY_BELOW_NORMAL = -1


def _prioridad_baja():
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
    except Exception as e:
        log.debug("No se pudo bajar la prioridad del hilo de publicación: %s", e)


def _estado_vacio():
    return {"remoto": {}, "cola": [], "derivadas": {}, "comun_version": "", "contacto": "", "ultimo_ok": "", "ultimo_error": ""}


def _leer_estado():
    try:
        with open(RUTA_ESTADO, encoding="utf-8") as f:
            d = json.load(f)
        base = _estado_vacio()
        base.update(d if isinstance(d, dict) else {})
        return base
    except (OSError, ValueError):
        return _estado_vacio()


def _guardar_estado(estado):
    os.makedirs(os.path.dirname(RUTA_ESTADO), exist_ok=True)
    _escribir_json(RUTA_ESTADO, estado)
    with _lock:
        ESTADO["pendientes"] = len(estado["cola"])
        ESTADO["bytes_pendientes"] = sum(op.get("bytes", 0) for op in estado["cola"])
        ESTADO["ultimo_ok"], ESTADO["ultimo_error"] = estado.get("ultimo_ok", ""), estado.get("ultimo_error", "")


def planificar(estado, cfg=None):
    """
    Regenera la copia pública (derivadas, JSON, índice, visor) y deja en estado["cola"] las
    operaciones que faltan para que el hosting sea igual: subidas en orden (fotos, visor, comunes),
    el índice galeria.json el último y los borrados después. Devuelve el índice.
    """
    cfg = cfg or leer_config()
    nombre = cfg["nombre"]
    salida = os.path.join(CARPETA_SALIDA, nombre)
    os.makedirs(salida, exist_ok=True)
    deseado = []                        # [(ruta remota relativa a galeria/, ruta local, grupo)]
    if os.path.isfile(RUTA_HTACCESS) and not _sin_permiso_comun:
        deseado.append((".htaccess", RUTA_HTACCESS, "htaccess"))

    fotos, vigentes = [], set()
    derivadas = estado.setdefault("derivadas", {})
    marcadas = publicadas()
    for jpg, meta in marcadas.items():
        motivo = por_que_no_publicable(jpg, meta)
        if motivo:                        # marcada a mano o antes de este freno: el endpoint la rechazaría con el índice
            log.warning("Galería pública: %s no se publica (%s)", jpg, motivo)
            continue
        try:
            ficha, locales = _preparar_foto(jpg, meta, cfg, salida, derivadas)
        except Exception as e:
            log.warning("Galería pública: %s no se puede preparar (%s)", jpg, e)
            continue
        fotos.append(ficha)
        vigentes.add(ficha["id"])
        for rel, local in locales.items():
            deseado.append((f"{nombre}/{rel}", local, "foto"))
    for base in list(derivadas):          # derivadas de fotos ya retiradas
        if base not in vigentes:
            derivadas.pop(base, None)
            for sufijo in (".jpg", "_mini.jpg", "_cielo.jpg", "_anotaciones.json", ".json"):
                try:
                    os.remove(os.path.join(salida, base + sufijo))
                except OSError:
                    pass
    fotos.sort(key=lambda f: f.get("fecha_iso") or "", reverse=True)

    # Lo común (páginas del visor incluidas) solo lo sube el dueño del hosting; a los demás el endpoint les dice que no
    comun = {} if _sin_permiso_comun else archivos_comun()
    if comun and estado.get("comun_version") != version.VERSION:
        for rel, local in comun.items():
            deseado.append((f"_comun/{version.VERSION}/{rel}", local, "comun"))

    # El índice: solo claves de la lista blanca del endpoint (hosting/publicar.php, ESQUEMA_INDICE)
    indice = {"version": 1, "generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "app": version.VERSION, "comun": f"../_comun/{version.VERSION}", "autor": cfg["autor_publico"],
              "nombre": nombre, "idiomas": cfg["idiomas"], "observador": observador_publico(cfg),
              "planetario": os.path.isfile(os.path.join(CARPETA_PLANTILLAS, "planetario.html")),
              "contacto": str(estado.get("contacto") or ""), "sol": sol_publico(marcadas), "fotos": fotos}
    ruta_indice = os.path.join(salida, "galeria.json")
    _escribir_json(ruta_indice, indice)
    deseado.append((f"{nombre}/galeria.json", ruta_indice, "indice"))

    remoto = estado.setdefault("remoto", {})
    orden = {"htaccess": 0, "foto": 1, "comun": 2, "indice": 3}
    subir = []
    rutas_deseadas = set()
    for ruta, local, grupo in sorted(deseado, key=lambda d: orden[d[2]]):
        if ruta.startswith(nombre + "/") and not ruta.lower().endswith(EXTENSIONES_GALERIA):
            continue                    # a la carpeta del propietario solo van fotos y datos
        rutas_deseadas.add(ruta)
        sha = _sha256(local)
        if remoto.get(ruta) != sha:
            subir.append({"op": "subir", "ruta": ruta, "local": local, "sha256": sha, "bytes": os.path.getsize(local), "intentos": 0})
    borrar = [{"op": "borrar", "ruta": r, "intentos": 0} for r in sorted(remoto) if r.startswith(nombre + "/") and r not in rutas_deseadas]
    estado["cola"] = subir + borrar
    with _lock:
        ESTADO["fotos"] = len(fotos)
    return indice


# ───────────────────────── Cliente del endpoint ─────────────────────────
class ClienteHosting:
    """Habla con hosting/publicar.php: POST multipart con la clave en una cabecera, respuesta JSON."""

    def __init__(self, endpoint, clave, nombre, timeout=TIMEOUT):
        self.endpoint, self.clave, self.nombre, self.timeout = endpoint, clave, nombre, timeout

    def _post(self, accion, datos=None, fichero=None):
        campos = {"accion": accion, "slug": self.nombre}
        campos.update(datos or {})
        ficheros = {"fichero": ("fichero", fichero, "application/octet-stream")} if fichero is not None else None
        try:
            r = requests.post(self.endpoint, data=campos, files=ficheros,
                              headers={CABECERA_CLAVE: self.clave, "User-Agent": AGENTE}, timeout=self.timeout)
        except requests.RequestException as e:
            raise ErrorRed(_("No se pudo conectar con el hosting: {e}", e=e)) from e
        if r.status_code == 401:
            raise ErrorClave(_("El hosting rechaza la clave: ese nombre ya está en uso o la clave no es la suya."))
        if r.status_code == 409 and accion == "registrar":
            raise ErrorNombre(_("Ese nombre ya está en uso en el hosting (o está reservado): elige otro en Configuración."))
        if r.status_code == 507:
            raise ErrorCuota(_("La galería ha agotado su cuota en el hosting: retira alguna foto."))
        if r.status_code == 404:
            raise ErrorHosting(_("No existe el endpoint en el hosting (código 404): falta subir publicar.php (ver hosting/LEEME.md)."))
        if r.status_code == 413:
            raise ErrorTamano(_("El hosting no admite ficheros tan grandes (código 413)."))
        try:
            j = r.json()
        except ValueError:
            if r.status_code == 403:
                # Sin JSON no es publicar.php: es el cortafuegos del hosting (o la carpeta) quien corta la petición
                raise ErrorHosting(_("El hosting bloquea la petición (código 403): revisa el cortafuegos o la carpeta del endpoint."))
            raise ErrorHosting(_("El hosting no responde como el endpoint de ScopePower (código {codigo}).", codigo=r.status_code))
        mensaje = str((j or {}).get("error") if isinstance(j, dict) else "") or f"HTTP {r.status_code}"
        if r.status_code == 403 and accion in ("subir", "borrar"):
            raise ErrorPermiso(mensaje)
        if r.status_code == 422 and accion == "subir":
            raise ErrorContenido(mensaje)
        if not r.ok or not isinstance(j, dict) or not j.get("ok"):
            raise ErrorHosting(mensaje)
        return j

    def probar(self):
        return self._post("probar")

    def registrar(self):
        """Reserva el nombre con esta clave si está libre (registro abierto); con el nombre ya suyo, no hace nada."""
        return self._post("registrar")

    def listar(self, prefijo=""):
        return self._post("listar", {"prefijo": prefijo}).get("ficheros") or {}

    def subir(self, ruta, datos, sha256):
        """Sube el fichero entero o, si pasa de TROZO, en trozos que el endpoint junta y comprueba con el sha256 del total."""
        if len(datos) <= TROZO:
            return self._post("subir", {"ruta": ruta, "sha256": sha256}, fichero=datos)
        trozos = -(-len(datos) // TROZO)
        r = None
        for i in range(trozos):
            r = self._post("subir", {"ruta": ruta, "sha256": sha256, "trozo": i, "trozos": trozos}, fichero=datos[i * TROZO:(i + 1) * TROZO])
        return r

    def borrar(self, ruta):
        return self._post("borrar", {"ruta": ruta})


def cliente(cfg=None):
    cfg = cfg or leer_config()
    return ClienteHosting(cfg["endpoint"], cfg["clave"], cfg["nombre"])


def probar(cfg=None):
    """
    Reserva el nombre si hace falta y comprueba el endpoint. Devuelve lo que diga el hosting (con
    `registrado`: True si el nombre acaba de quedar reservado); lanza ErrorHosting, ErrorClave, ErrorNombre.
    """
    cfg = cfg or leer_config()
    falta = por_que_no(cfg)
    if falta:
        raise ErrorHosting(falta)
    cli = cliente(cfg)
    registro = cli.registrar()
    r = cli.probar()
    r["registrado"] = bool(registro.get("registrado"))
    if "dueno" in r:
        _anotar_hosting({}, r)
    return r


# ───────────────────────── Hilo de la cola ─────────────────────────
def _reconciliar(cli, estado, nombre):
    """Lo que hay de verdad en el hosting, por si alguien borró o subió a mano: sustituye `remoto`."""
    remoto = {}
    prefijos = (f"{nombre}/",) if _sin_permiso_comun else (f"{nombre}/", f"_comun/{version.VERSION}/", ".htaccess")
    for prefijo in prefijos:
        try:
            lista = cli.listar(prefijo)
        except ErrorClave:
            raise
        except ErrorHosting as e:
            log.warning("Galería pública: no se pudo listar %r en el hosting (%s)", prefijo, e)
            return False
        for ruta, datos in (lista or {}).items():
            remoto[ruta] = (datos or {}).get("sha256", "")
    # Por contenido, no solo porque esté: un JS o CSS retocado dentro de la misma versión también tiene que subir.
    # Las páginas que el endpoint coloca en la carpeta del propietario no salen en la lista: no son de la app.
    comun = {} if _sin_permiso_comun else archivos_comun()
    completo = all(remoto.get(f"_comun/{version.VERSION}/{rel}") == _sha256(local) for rel, local in comun.items())
    estado["remoto"] = remoto
    estado["comun_version"] = version.VERSION if (comun and completo) or _sin_permiso_comun else ""
    return True


def _anotar_hosting(estado, info):
    """Lo que dice el endpoint al probar y que va al índice (contacto) o decide qué se encola (si somos el dueño)."""
    global _sin_permiso_comun
    estado["contacto"] = str(info.get("contacto") or "")
    if info.get("dueno") is False and not _sin_permiso_comun:
        _sin_permiso_comun = True
        log.info("Galería pública: este nombre no es el dueño del hosting: lo común y las páginas las pone el hosting")


def _ejecutar(cli, op):
    if op["op"] == "subir":
        with open(op["local"], "rb") as f:
            datos = f.read()
        cli.subir(op["ruta"], datos, op["sha256"])
    else:
        cli.borrar(op["ruta"])


def _bucle():
    global _replanificar, _verificar_remoto
    _prioridad_baja()
    espera = INTERVALO_REVISION
    while True:
        despertado = _despertar.wait(timeout=espera)
        _despertar.clear()
        espera = INTERVALO_REVISION
        cfg = leer_config()
        if not cfg["configurado"]:
            continue
        with _lock:
            ESTADO["ocupado"] = True
            replan, verificar = _replanificar or not despertado, _verificar_remoto
            _replanificar = False
            if ESTADO["parada"] and not replan:
                ESTADO["ocupado"] = False
                continue
            ESTADO["error_clave"] = ESTADO["parada"] = False
        try:
            estado = _leer_estado()
            cli = cliente(cfg)
            if verificar:
                try:
                    cli.registrar()          # reserva el nombre si está libre; con el nombre ya suyo no hace nada
                    _con_red()
                    _anotar_hosting(estado, cli.probar())
                    if _reconciliar(cli, estado, cfg["nombre"]):
                        _verificar_remoto = False
                        replan = True
                except ERRORES_QUE_PARAN as e:
                    _parar(estado, e)
                    continue
                except ErrorRed as e:
                    _sin_red(e)
                except ErrorHosting as e:
                    _con_red()
                    log.warning("Galería pública: el hosting no responde al registrar (%s)", e)
            if replan or not estado["cola"]:
                try:
                    planificar(estado, cfg)
                except Exception as e:
                    log.exception("Galería pública: no se pudo planificar la subida")
                    estado["ultimo_error"] = _("No se pudo preparar la galería pública: {e}", e=e)
                _guardar_estado(estado)
            espera = _vaciar(cli, estado)
        except Exception:
            log.exception("Galería pública: fallo inesperado en la cola")
        finally:
            with _lock:
                ESTADO.update({"ocupado": False, "subiendo": ""})


def _sin_red(e, ruta=""):
    """Sin conexión con el hosting. En el campo es lo normal y se reintenta cada minuto: el log lo dice
    una vez al perderla, y los reintentos van a depuración para no tapar los avisos que importan."""
    with _lock:
        primera = not ESTADO["sin_red"]
        ESTADO["sin_red"] = True
    if primera:
        log.warning("Galería pública: sin conexión con el hosting (%s); %s se reintenta cada %s s "
                    "sin repetir este aviso", e, f"{ruta} y el resto de la cola" if ruta else "la cola", ESPERA_SIN_RED)
    else:
        log.debug("Galería pública: sigue sin conexión con el hosting (%s)", e)


def _con_red():
    """El hosting ha contestado (bien o mal): si antes no había red, una línea para decir que ha vuelto."""
    with _lock:
        volvio = ESTADO["sin_red"]
        ESTADO["sin_red"] = False
    if volvio:
        log.info("Galería pública: vuelve la conexión con el hosting")


def _parar(estado, e):
    """Clave, nombre o cuota: la cola se queda en disco y parada hasta que el usuario lo arregle y sincronice."""
    estado["ultimo_error"] = str(e)
    _guardar_estado(estado)           # la cola queda en disco antes de avisar
    with _lock:
        ESTADO["parada"] = True
        ESTADO["error_clave"] = isinstance(e, ErrorClave)
    log.error("Galería pública: %s", e)


def _vaciar(cli, estado):
    """Ejecuta la cola de una en una. Devuelve cuánto esperar antes de volver (segundos)."""
    global _sin_permiso_comun
    while estado["cola"]:
        op = estado["cola"][0]
        with _lock:
            ESTADO["subiendo"] = op["ruta"]
        try:
            _ejecutar(cli, op)
        except ERRORES_QUE_PARAN as e:
            _parar(estado, e)
            return INTERVALO_REVISION
        except ErrorPermiso as e:
            if op["ruta"].startswith("_comun/") or op["ruta"] == ".htaccess":
                # No somos el dueño del hosting: lo común no nos toca. Una vez en el log y fuera de la cola.
                if not _sin_permiso_comun:
                    log.info("Galería pública: el hosting no admite lo común de este nombre (%s): lo pone el dueño", e)
                _sin_permiso_comun = True
                estado["cola"] = [o for o in estado["cola"] if not (o["ruta"].startswith("_comun/") or o["ruta"] == ".htaccess")]
                estado["comun_version"] = version.VERSION
                _guardar_estado(estado)
                continue
            log.warning("Galería pública: %s se salta (%s)", op["ruta"], e)
            estado["ultimo_error"] = f"{op['ruta']}: {e}"
            estado["cola"].pop(0)
            _guardar_estado(estado)
            continue
        except (ErrorTamano, ErrorContenido) as e:
            log.warning("Galería pública: %s se salta (%s)", op["ruta"], e)
            estado["ultimo_error"] = f"{op['ruta']}: {e}"
            estado["cola"].pop(0)
            _guardar_estado(estado)
            continue
        except ErrorRed as e:
            # Sin red el intento no cuenta: en el campo es lo normal y la cola espera entera
            estado["ultimo_error"] = str(e)
            _guardar_estado(estado)
            _sin_red(e, op["ruta"])
            return ESPERA_SIN_RED
        except ErrorHosting as e:
            _con_red()
            op["intentos"] = op.get("intentos", 0) + 1
            estado["ultimo_error"] = str(e)
            _guardar_estado(estado)
            espera = ESPERAS_REINTENTO[min(op["intentos"], len(ESPERAS_REINTENTO)) - 1]
            log.warning("Galería pública: %s falló (%s); se reintenta en %s s", op["ruta"], e, espera)
            return espera
        _con_red()
        if op["op"] == "subir":
            estado["remoto"][op["ruta"]] = op["sha256"]
            if op["ruta"].startswith("_comun/"):
                faltan = [r for r in estado["cola"][1:] if r["ruta"].startswith("_comun/")]
                if not faltan:
                    estado["comun_version"] = version.VERSION
        else:
            estado["remoto"].pop(op["ruta"], None)
        estado["cola"].pop(0)
        estado["ultimo_ok"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        estado["ultimo_error"] = ""
        _guardar_estado(estado)
        log.info("Galería pública: %s %s", "subido" if op["op"] == "subir" else "borrado", op["ruta"])
    return INTERVALO_REVISION


def _arrancar_hilo():
    global _hilo
    with _lock:
        if _hilo is None or not _hilo.is_alive():
            _hilo = threading.Thread(target=_bucle, name="publicar", daemon=True)
            _hilo.start()
            ESTADO["activo"] = True


def sincronizar(verificar=False):
    """
    Replantea la copia pública y pone la cola en marcha (vuelve al momento). Con verificar=True
    pregunta antes al hosting qué tiene. False si no está configurado.
    """
    global _replanificar, _verificar_remoto
    if not leer_config()["configurado"]:
        return False
    with _lock:
        _replanificar = True
        if verificar:
            _verificar_remoto = True
        ESTADO["error_clave"] = ESTADO["parada"] = False
    _arrancar_hilo()
    _despertar.set()
    return True


def iniciar():
    """Al arrancar la app: si está configurada, comprueba el hosting y sigue con lo que quedara en la cola."""
    cfg = leer_config()
    if not cfg["configurado"]:
        return False
    with _lock:
        ESTADO["pendientes"] = len(_leer_estado()["cola"])
    return sincronizar(verificar=True)


def al_borrar(nombre):
    """Gancho de eliminar la foto en la galería: si estaba publicada, se retira del hosting."""
    sincronizar()


def estado():
    """Copia del estado para la web, con la dirección pública y qué falta si no está configurado."""
    cfg = leer_config()
    with _lock:
        d = dict(ESTADO)
    d.update({"configurado": cfg["configurado"], "url": url_publica(cfg), "nombre": cfg["nombre"], "falta": por_que_no(cfg)})
    return d


def esperar(timeout=None):
    """Espera a que la cola se vacíe y el hilo descanse (para las pruebas). True si se vació."""
    fin = None if timeout is None else time.time() + timeout
    while True:
        with _lock:
            quieto = not ESTADO["ocupado"] and not _replanificar
        if quieto and (not _leer_estado()["cola"] or ESTADO["parada"]):
            return True
        if fin is not None and time.time() > fin:
            return False
        time.sleep(0.05)
