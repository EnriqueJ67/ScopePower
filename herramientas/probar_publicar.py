# herramientas/probar_publicar.py
#
# Prueba sin hosting de la galería pública (publicar.py + hosting/publicar.php) sobre una
# galería de mentira en una carpeta temporal:
#
#   1. Un servidor HTTP falso en Python que habla el mismo contrato que publicar.php
#      (probar / listar / subir / borrar, clave en la cabecera, mismas reglas de rutas y de
#      permisos: lo común y el .htaccess solo para el dueño, en <nombre>/ solo .jpg y .json
#      comprobados al llegar, páginas que coloca el propio endpoint) y que se puede poner a
#      fallar, a rechazar la clave o a no estar.
#   2. Marcar y retirar fotos, las derivadas (≤ 1600 y ≤ 480 px), el JSON por lista blanca
#      (sin latitud, longitud, ajustes ni rutas), el índice con el observador redondeado y la
#      huella en el cielo solo donde la hay, el orden de la cola (galeria.json el último al
#      subir y el primero al retirar), reintentos, clave rechazada, sin red, foto reescrita
#      (v nuevo), renombrada (borrar + subir), persistencia de la cola, rutas rechazadas y las
#      anotaciones (de la caché, a la escala del JPG publicado y por lista blanca) y las
#      gráficas del Sol (todas las medidas, solo si hay alguna foto del Sol publicada).
#      Y el reparto de permisos: un nombre registrado no sube a _comun ni .htaccess (403, y la
#      app lo da por bueno sin encolarlo más) ni HTML a su carpeta; un .jpg que no es JPEG o un
#      galeria.json con claves de más, un tipo inventado, 1001 fotos o una foto sin coordenadas
#      que no sea del Sol ni la Luna, 422; las páginas aparecen en la carpeta del nombre al
#      registrarlo y con cada galeria.json. La app no deja marcar una foto que el endpoint rechazaría.
#   3. Si hay `php` en el PATH, lo mismo contra hosting/publicar.php de verdad con el servidor
#      de PHP (`php -S`); si no, se avisa y se salta (en GitHub no hay PHP).
#
# Uso: python herramientas/probar_publicar.py   (desde la carpeta de ScopePower)

import os
import re
import sys
import json
import time
import email
import shutil
import socket
import hashlib
import tempfile
import threading
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datos_iers
datos_iers.configurar()
import cv2
from astropy.io import fits
import catalogo_offline
import editor_fotos
import fondo_planetario
import fotometria_solar
import galeria_anotada
import manchas_solares
import publicar

RAIZ = tempfile.mkdtemp(prefix="scopepower_publicar_")
FOTOS = os.path.join(RAIZ, "fotos")
DATOS = os.path.join(RAIZ, "datos")
os.makedirs(FOTOS)
os.makedirs(DATOS)
CLAVE = "clave-de-prueba-larga-0123456789"
NOMBRE = "enrique-jimenez"
LAT, LON = 40.4168, -3.7038

# La galería de mentira: los módulos miran estas carpetas
editor_fotos.CARPETA_FOTOS = FOTOS
editor_fotos.CARPETA_ORIGINALES = os.path.join(FOTOS, "originales")
publicar.CARPETA_FOTOS = FOTOS
fondo_planetario.CARPETA_FOTOS = FOTOS
fondo_planetario.RUTA_CACHE = os.path.join(DATOS, "fondo_planetario.json")
fondo_planetario.CARPETA_MINIATURAS = os.path.join(DATOS, "planetario_fotos")
galeria_anotada.RUTA_CACHE = os.path.join(DATOS, "anotaciones_galeria.json")
manchas_solares.RUTA_SERIE = os.path.join(DATOS, "numero_wolf.csv")
fotometria_solar.RUTA_MEDIDAS = os.path.join(DATOS, "medidas_sol.json")
publicar.RUTA_ESTADO = os.path.join(DATOS, "publicar.json")
publicar.CARPETA_SALIDA = os.path.join(DATOS, "publicar")
publicar.CARPETA_PAGINAS = os.path.join(DATOS, "publicar", "_paginas")
# Unas plantillas mínimas del visor (las de verdad van en casos_visor), sin estáticos ni el atlas (6,6 MB)
PLANTILLAS = os.path.join(RAIZ, "plantillas_minimas")
os.makedirs(PLANTILLAS)
for nombre_plantilla in ("index.html", "planetario.html"):
    with open(os.path.join(PLANTILLAS, nombre_plantilla), "w", encoding="utf-8") as f:
        f.write('<!doctype html><html lang="{{ lang }}"><title>{{ _("Galería") }}</title><p>{{ raiz }}/galeria.json {{ comun }}</p></html>')
publicar.CARPETA_PLANTILLAS = PLANTILLAS
publicar.CARPETA_ESTATICOS = os.path.join(RAIZ, "sin_estaticos")
publicar.CARPETA_ATLAS = os.path.join(RAIZ, "sin_atlas")
publicar.ESPERAS_REINTENTO = (0.3,)
publicar.ESPERA_SIN_RED = 0.3
publicar.TIMEOUT = (5, 30)
publicar.clave = lambda: CONFIG["clave"]

CONFIG = {"endpoint": "", "clave": CLAVE, "coordenadas": "redondeadas", "nombre": ""}
SEMILLA = [10]                                   # cada tanda reescribe M 31 con un contenido distinto
_leer_config_original = publicar.leer_config


def config_prueba(ruta="config.ini"):
    ini = os.path.join(RAIZ, "config.ini")
    with open(ini, "w") as f:            # la codificación del sistema, como config.ini de la app
        f.write(f"[Observatorio]\nlatitud_observatorio = {LAT}\nlongitud_observatorio = {LON}\nautor = Enrique Jiménez\n"
                f"[Publicar]\nactivado = true\nurl_base = http://127.0.0.1:1\nendpoint = {CONFIG['endpoint']}\nnombre = {CONFIG['nombre']}\n"
                f"coordenadas = {CONFIG['coordenadas']}\nlado_foto = 1600\nlado_mini = 480\n")
    return _leer_config_original(ini)


publicar.leer_config = config_prueba


# ───────────────────────── Servidor falso con el contrato de publicar.php ─────────────────────────
EXTENSIONES_COMUN = ("jpg", "jpeg", "webp", "png", "json", "html", "js", "css", "md", "txt", "ico")
EXTENSIONES_GALERIA = ("jpg", "json")
PAGINAS = ("index.html", "planetario.html", "en/index.html", "en/planetario.html")
LADO_MAX_JPG, MAX_FOTOS, MAX_TEXTO = 1700, 1000, 200
TIPOS_SIN_COORDENADAS = ("Sol", "Luna")
SIN_COORDENADAS = "cada foto debe llevar coordenadas (ra y dec) o ser del Sol o la Luna"
MAX_JSON, MAX_JSON_ANOTACIONES, MAX_JSON_INDICE = 256 * 1024, 1024 * 1024, 2 * 1024 * 1024
CONTACTO = "dueno@hosting.example"
SOLO_DUENO = "Solo el dueño del hosting puede subir o borrar lo común (_comun) y el .htaccess"

# Los esquemas por lista blanca, como en publicar.php (ESQUEMA_FOTO, ESQUEMA_INDICE, ESQUEMA_ANOTACIONES)
ESQUEMA_FOTO = {"obj": {
    "id": "id", "objeto": "texto", "tipo": "categoria", "fecha": "texto", "fecha_iso": "texto", "n_fotos": "num",
    "exposicion": "num", "ra": "texto", "dec": "texto", "nota": {"obj": {"puntuacion": "num", "estrellas": "num", "fwhm": "num"}},
    "autor": "texto", "mosaico": "bool", "cometa_nucleo": "bool", "wolf": {"obj": {"R": "num", "g": "num", "f": "num", "k": "num"}},
    "video": {"obj": {"frames": "num", "duracion_s": "num"}},
    "ficheros": {"obj": {"jpg": "fichero", "mini": "fichero", "cielo": "fichero", "anotaciones": "fichero"}},
    "v": {"obj": {"jpg": "texto", "mini": "texto", "cielo": "texto", "anotaciones": "texto"}}, "ancho": "num", "alto": "num",
    "cielo": {"obj": {"esquinas": "puntos", "ancho": "num", "alto": "num", "modo": "texto", "area_deg2": "num"}}, "anotaciones": "texto"}}
ESQUEMA_INDICE = {"obj": {
    "version": "num", "generado": "texto", "app": "texto", "comun": "texto", "autor": "texto", "nombre": "texto", "idiomas": "textos",
    "planetario": "bool", "contacto": "texto", "observador": {"obj": {"latitud": "num", "longitud": "num", "redondeo_grados": "num"}},
    "sol": {"obj": {"wolf": {"lista": {"obj": {"fecha": "texto", "g": "num", "f": "num", "R": "num", "k": "num", "n": "num"}}},
                    "curva": {"lista": {"obj": {"instante": "num", "r": "num"}}},
                    "orbita": {"obj": {"e": "num", "r0": "num", "perihelio": "texto", "n": "num", "dias": "num"}},
                    "diametro": {"lista": {"obj": {"instante": "num", "r_arcsec": "num", "r_efem": "num", "desvio_pct": "num",
                                                   "distancia": "num", "fiable": "bool"}}}}},
    "fotos": {"lista": ESQUEMA_FOTO, "max": MAX_FOTOS}}}
ESQUEMA_ANOTACIONES = {"obj": {
    "ancho": "num", "alto": "num", "disco": {"obj": {"cx": "num", "cy": "num", "r": "num", "cortado": "bool"}}, "sol": "bool",
    "wolf": {"obj": {"g": "num", "f": "num", "R": "num", "k": "num"}},
    "manchas": {"lista": {"obj": {"x": "num", "y": "num", "r_px": "num", "umbra": "bool", "grupo": "num"}}},
    "grupos": {"lista": {"obj": {"id": "num", "n": "num", "lat": "num", "dlon": "num", "km2": "num", "diametro_km": "num",
                                 "largo_km": "num", "x": "num", "y": "num"}}}, "resolucion_km": "num",
    "objetos": {"lista": {"obj": {"nombre": "texto", "angulo": "num", "mag": "num", "fuente": "texto", "familia": "texto", "luz": "bool",
                                  "rango": "num", "x": "num", "y": "num", "a": "num", "b": "num"}}},
    "luna": "bool", "mares": "trazado", "terminador": "puntos", "iluminada": "num", "dudosa": "bool"}}


def es_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def comprobar(valor, esquema, donde):
    """Lo mismo que comprobar() en publicar.php: qué falla, o None."""
    if valor is None:
        return None
    if isinstance(esquema, str):
        if esquema == "num":
            return None if es_num(valor) else f"{donde} no es un número"
        if esquema == "bool":
            return None if isinstance(valor, bool) else f"{donde} no es verdadero o falso"
        if esquema == "texto":
            return None if isinstance(valor, str) and len(valor.encode("utf-8")) <= MAX_TEXTO else f"{donde} no es un texto corto"
        if esquema == "trazado":
            return None if isinstance(valor, str) else f"{donde} no es un texto"
        if esquema == "id":
            return None if isinstance(valor, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", valor) else f"{donde} no es un identificador"
        if esquema == "fichero":
            return None if isinstance(valor, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}\.(jpg|json)", valor) else f"{donde} no es un fichero"
        if esquema == "categoria":
            return None if isinstance(valor, str) and (valor == "" or valor in catalogo_offline.CATEGORIAS) else f"{donde} no es un tipo de la app"
        if esquema == "textos":
            return None if isinstance(valor, list) and all(isinstance(v, str) and len(v) <= MAX_TEXTO for v in valor) else f"{donde} no es una lista"
        if esquema == "puntos":
            ok = isinstance(valor, list) and all(isinstance(p, list) and len(p) == 2 and es_num(p[0]) and es_num(p[1]) for p in valor)
            return None if ok else f"{donde} no es una lista de puntos"
        return f"{donde}: esquema desconocido"
    if "lista" in esquema:
        if not isinstance(valor, list):
            return f"{donde} no es una lista"
        if len(valor) > esquema.get("max", 20000):
            return f"{donde} tiene más de {esquema.get('max', 20000)} elementos"
        for i, v in enumerate(valor):
            e = comprobar(v, esquema["lista"], f"{donde}[{i}]")
            if e:
                return e
        return None
    if not isinstance(valor, dict):
        return f"{donde} no es un objeto"
    for k, v in valor.items():
        if k not in esquema["obj"]:
            return f"{donde} tiene una clave que no se publica: {k}"
        e = comprobar(v, esquema["obj"][k], f"{donde}.{k}")
        if e:
            return e
    return None


def comprobar_foto(foto, donde):
    """Como comprobarFoto() en publicar.php: coordenadas del objeto salvo el Sol y la Luna; sin Paisaje."""
    tipo = foto.get("tipo") or ""
    if tipo == "Paisaje":
        return f"{donde}: las fotos de tipo Paisaje no se publican"
    if tipo in TIPOS_SIN_COORDENADAS:
        return None
    ra, dec = foto.get("ra") or "", foto.get("dec") or ""
    if not isinstance(ra, str) or not ra.strip() or not isinstance(dec, str) or not dec.strip():
        return f"{donde}: {SIN_COORDENADAS}"
    return None


def comprobar_contenido(ruta, datos):
    """Como comprobarContenido() en publicar.php: JPEG de verdad y acotado, JSON por lista blanca."""
    nombre = ruta.rsplit("/", 1)[-1]
    if nombre.lower().endswith(".jpg"):
        if not datos.startswith(b"\xff\xd8\xff"):
            return "El fichero no es un JPEG"
        im = cv2.imdecode(np.frombuffer(datos, np.uint8), cv2.IMREAD_UNCHANGED)
        if im is None:
            return "El fichero no es un JPEG"
        if max(im.shape[:2]) > LADO_MAX_JPG:
            return f"La foto pasa de {LADO_MAX_JPG} px de lado"
        return None
    es_indice, es_anot = nombre == "galeria.json", nombre.endswith("_anotaciones.json")
    maximo = MAX_JSON_INDICE if es_indice else MAX_JSON_ANOTACIONES if es_anot else MAX_JSON
    if len(datos) > maximo:
        return f"{nombre} pasa de {maximo // 1024} KB"
    try:
        d = json.loads(datos.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return f"{nombre} no es un JSON válido"
    if not isinstance(d, (dict, list)):
        return f"{nombre} no es un JSON válido"
    if es_indice:
        if not isinstance(d, dict) or "version" not in d or not isinstance(d.get("fotos"), list):
            return "galeria.json necesita version y la lista fotos"
        mal = comprobar(d, ESQUEMA_INDICE, "galeria")
        if mal:
            return mal
        for i, foto in enumerate(d["fotos"]):
            mal = comprobar_foto(foto if isinstance(foto, dict) else {}, f"galeria.fotos[{i}]")
            if mal:
                return mal
        return None
    if isinstance(d, list) and not d:
        d = {}
    if es_anot:
        return comprobar(d, ESQUEMA_ANOTACIONES, nombre)
    return comprobar(d, ESQUEMA_FOTO, nombre) or comprobar_foto(d, nombre)


class ServidorFalso(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, raiz):
        super().__init__(("127.0.0.1", 0), ManejadorFalso)
        self.raiz = raiz
        self.fallos_pendientes = 0
        self.registro = []                   # [(accion, ruta)] en orden de llegada (solo las aceptadas)
        self.claves = {NOMBRE: CLAVE}        # nombres dados de alta a mano (publicar_config.php): el dueño
        self.duenos = ["dueno-registrado"]   # nombres registrados desde la app que también son el dueño
        self.registrados = {}                # nombres reservados desde la app: {nombre: clave}
        self.cuota_nombre = 2 * 1024 * 1024  # como cuota_nombre_mb = 2 en la prueba del PHP real
        self.endpoint_viejo = False          # True: probar sin 'dueno' ni 'contacto', como un publicar.php anterior
        self.lock = threading.Lock()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server_address[1]}/scopepower/publicar.php"

    def carpeta_paginas(self):
        """_comun/<versión más alta>/publico/paginas, o None (como carpetaPaginas() en publicar.php)."""
        comun = os.path.join(self.raiz, "_comun")
        if not os.path.isdir(comun):
            return None
        mejor = None
        for v in os.listdir(comun):
            carpeta = os.path.join(comun, v, "publico", "paginas")
            if re.fullmatch(r"[0-9][A-Za-z0-9.]*", v) and os.path.isfile(os.path.join(carpeta, "index.html")):
                clave_v = tuple(int(x) if x.isdigit() else 0 for x in v.split("."))
                if mejor is None or clave_v > mejor[0]:
                    mejor = (clave_v, carpeta)
        return mejor[1] if mejor else None

    def colocar_paginas(self, slug):
        origen = self.carpeta_paginas()
        if origen is None:
            return
        for p in PAGINAS:
            de = os.path.join(origen, p.replace("/", os.sep))
            if os.path.isfile(de):
                a = os.path.join(self.raiz, slug, p.replace("/", os.sep))
                os.makedirs(os.path.dirname(a), exist_ok=True)
                shutil.copyfile(de, a)


def ruta_segura(slug, ruta, es_dueno):
    """(ruta, None) si se admite; (None, (código, mensaje)) si no: 403 lo que no es suyo, 400 lo que no vale."""
    if ruta == ".htaccess":
        return (ruta, None) if es_dueno else (None, (403, SOLO_DUENO))
    if len(ruta) > 400 or not re.fullmatch(r"[A-Za-z0-9_./-]+", ruta):
        return None, (400, "ruta")
    seg = ruta.split("/")
    if len(seg) < 2 or any(s in ("", ".", "..") or s.startswith(".") for s in seg):
        return None, (400, "ruta")
    ext = seg[-1].rsplit(".", 1)[-1].lower() if "." in seg[-1] else ""
    if seg[0] == "_comun":
        if not es_dueno:
            return None, (403, SOLO_DUENO)
        if len(seg) < 3 or not re.fullmatch(r"[0-9][A-Za-z0-9.]*", seg[1]):
            return None, (400, "ruta")
        if ext not in EXTENSIONES_COMUN and seg[-1] not in ("LICENSE", "properties"):
            return None, (400, "extensión")
    else:
        if seg[0] != slug:
            return None, (403, "Esa carpeta no es de este nombre")
        if ext not in EXTENSIONES_GALERIA:
            return None, (400, "En la galería solo se admiten .jpg y .json (las páginas las pone el hosting)")
    return ruta, None


class ManejadorFalso(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, codigo, datos):
        cuerpo = json.dumps(datos).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def partes(self):
        largo = int(self.headers.get("Content-Length", 0))
        cuerpo = self.rfile.read(largo)
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):   # sin fichero, requests no usa multipart
            return {k: v[0] for k, v in urllib.parse.parse_qs(cuerpo.decode("utf-8")).items()}, None
        msg = email.message_from_bytes(b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + cuerpo)
        campos, fichero = {}, None
        for parte in msg.walk():
            if parte.get_content_maintype() == "multipart":
                continue
            nombre = parte.get_param("name", header="content-disposition")
            datos = parte.get_payload(decode=True) or b""
            if nombre == "fichero":
                fichero = datos
            elif nombre:
                campos[nombre] = datos.decode("utf-8")
        return campos, fichero

    def do_POST(self):
        s = self.server
        campos, fichero = self.partes()
        with s.lock:
            if s.fallos_pendientes > 0:
                s.fallos_pendientes -= 1
                return self.responder(500, {"ok": False, "error": "fallo provocado"})
        slug = campos.get("slug", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", slug):
            return self.responder(400, {"ok": False, "error": "nombre"})
        clave = self.headers.get("X-ScopePower-Clave", "")
        accion = campos.get("accion")
        registrada = clave != "" and s.registrados.get(slug) == clave
        valida = (clave != "" and s.claves.get(slug) == clave) or registrada
        es_dueno = (clave != "" and s.claves.get(slug) == clave) or (registrada and slug in s.duenos)
        if accion == "registrar":
            if valida:
                s.colocar_paginas(slug)
                return self.responder(200, {"ok": True, "registrado": False, "nombre": slug, "contacto": CONTACTO})
            if slug in s.claves or slug in s.registrados or slug in ("admin", "api", "scopepower", "_comun"):
                return self.responder(409, {"ok": False, "error": "nombre en uso o reservado"})
            if len(clave) < 16:
                return self.responder(400, {"ok": False, "error": "clave corta"})
            with s.lock:
                s.registrados[slug] = clave
            os.makedirs(os.path.join(s.raiz, slug), exist_ok=True)
            s.colocar_paginas(slug)
            return self.responder(200, {"ok": True, "registrado": True, "nombre": slug, "cuota_mb": 2, "contacto": CONTACTO})
        if not valida:
            return self.responder(401, {"ok": False, "error": "clave"})
        if accion == "probar":
            r = {"ok": True, "version": 3, "raiz_escribible": True, "max_bytes": 8 << 20, "cuota_mb": 2, "usado_mb": 0, "registro_abierto": True}
            if not s.endpoint_viejo:
                r.update({"contacto": CONTACTO, "dueno": es_dueno})
            return self.responder(200, r)
        if accion == "listar":
            prefijo = campos.get("prefijo", "")
            if prefijo == ".htaccess":
                ruta = os.path.join(s.raiz, ".htaccess")
                lista = {".htaccess": {"bytes": os.path.getsize(ruta), "sha256": sha(ruta)}} if os.path.isfile(ruta) else {}
                return self.responder(200, {"ok": True, "ficheros": lista})
            prefijo = prefijo or slug + "/"
            if ".." in prefijo or not (prefijo.startswith(slug + "/") or prefijo.startswith("_comun/")):
                return self.responder(400, {"ok": False, "error": "prefijo"})
            lista = {}
            carpeta = os.path.join(s.raiz, prefijo.rstrip("/"))
            for c, __, fs in os.walk(carpeta):
                for f in fs:
                    if f.startswith(".") or (not prefijo.startswith("_comun/") and f.lower().endswith(".html")):
                        continue        # las páginas de <slug>/ las pone el endpoint: para la app no existen
                    r = os.path.join(c, f)
                    lista[os.path.relpath(r, s.raiz).replace(os.sep, "/")] = {"bytes": os.path.getsize(r), "sha256": sha(r)}
            return self.responder(200, {"ok": True, "ficheros": lista})
        ruta_pedida = campos.get("ruta", "")
        if accion == "subir" and ruta_pedida.startswith(slug + "/") and ruta_pedida[len(slug) + 1:] in PAGINAS:
            ruta, error = ruta_pedida, None
        else:
            ruta, error = ruta_segura(slug, ruta_pedida, es_dueno)
        if ruta is None:
            return self.responder(error[0], {"ok": False, "error": error[1]})
        abs_ = os.path.join(s.raiz, ruta.replace("/", os.sep))
        de_galeria = not ruta.startswith("_comun/") and ruta != ".htaccess"
        if accion == "subir":
            if fichero is None:
                return self.responder(400, {"ok": False, "error": "fichero"})
            if de_galeria and ruta[len(slug) + 1:] in PAGINAS:     # páginas de una app anterior: se aceptan y se descartan
                s.colocar_paginas(slug)
                return self.responder(200, {"ok": True, "ruta": ruta, "ignorado": True, "bytes": 0, "sha256": campos["sha256"]})
            if de_galeria:     # cuota de la galería del nombre
                carpeta = os.path.join(s.raiz, slug)
                usado = sum(os.path.getsize(os.path.join(c, f)) for c, __, fs in os.walk(carpeta) for f in fs) if os.path.isdir(carpeta) else 0
                viejo = os.path.getsize(abs_) if os.path.isfile(abs_) else 0
                if usado - viejo + len(fichero) > s.cuota_nombre:
                    return self.responder(507, {"ok": False, "error": "cuota agotada"})
            os.makedirs(os.path.dirname(abs_), exist_ok=True)
            trozos, trozo = int(campos.get("trozos", 1)), int(campos.get("trozo", 0))
            if trozos > 1:      # por trozos, como publicar.php: se pegan a .subiendo-<sha> y el último comprueba
                parcial = os.path.join(os.path.dirname(abs_), ".subiendo-" + campos.get("sha256", ""))
                if trozo == 0 and os.path.isfile(parcial):
                    os.remove(parcial)
                if trozo > 0 and not os.path.isfile(parcial):
                    return self.responder(409, {"ok": False, "error": "falta el principio"})
                with open(parcial, "ab") as f:
                    f.write(fichero)
                if trozo < trozos - 1:
                    return self.responder(200, {"ok": True, "ruta": ruta, "trozo": trozo})
                with open(parcial, "rb") as f:
                    fichero = f.read()
                os.remove(parcial)
            if hashlib.sha256(fichero).hexdigest() != campos.get("sha256"):
                return self.responder(422, {"ok": False, "error": "sha256"})
            if de_galeria:
                mal = comprobar_contenido(ruta, fichero)
                if mal:
                    return self.responder(422, {"ok": False, "error": mal})
            with open(abs_, "wb") as f:
                f.write(fichero)
            with s.lock:
                s.registro.append(("subir", ruta))
            if de_galeria and ruta.endswith("/galeria.json"):
                s.colocar_paginas(slug)
            return self.responder(200, {"ok": True, "ruta": ruta, "bytes": len(fichero), "sha256": campos["sha256"]})
        if accion == "borrar":
            existia = os.path.isfile(abs_)
            if existia:
                os.remove(abs_)
            with s.lock:
                s.registro.append(("borrar", ruta))
            return self.responder(200, {"ok": True, "existia": existia})
        return self.responder(400, {"ok": False, "error": "accion"})


def sha(ruta):
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ───────────────────────── Galería de mentira ─────────────────────────
def foto_sintetica(nombre, lado=2400, semilla=0):
    rng = np.random.default_rng(semilla)
    img = rng.integers(10, 40, (lado * 2 // 3, lado, 3), dtype=np.uint8)
    for __ in range(60):
        x, y = int(rng.integers(0, lado)), int(rng.integers(0, lado * 2 // 3))
        cv2.circle(img, (x, y), int(rng.integers(2, 6)), (255, 255, 255), -1)
    cv2.imwrite(os.path.join(FOTOS, nombre), img, [cv2.IMWRITE_JPEG_QUALITY, 92])


def json_de_prueba(**extra):
    meta = {"autor": "Enrique Jiménez", "latitud": LAT, "longitud": LON, "objeto": "M 31", "tipo": "Galaxia",
            "fecha": "29/09/2026 23:15", "n_fotos": 40, "exposicion": 20.0, "ra": "00 42 44", "dec": "+41 16 09",
            "firmada": True, "encuadre": {"zoom": 1.0, "giro": 0, "espejo": False},
            "ajustes": {"estirado": 2.5, "ruido": 1}, "nota": {"puntuacion": 3.42, "estrellas": 812, "nitidez": 0.9, "fwhm": 2.3}}
    meta.update(extra)
    return meta


def json_foto(nombre, **extra):
    meta = json_de_prueba(**extra)
    with open(os.path.join(FOTOS, os.path.splitext(nombre)[0] + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def fits_sintetico(nombre):
    hdu = fits.PrimaryHDU(np.zeros((3, 24, 36), np.float32))
    hdu.header["NSTACK"] = 40
    hdu.writeto(os.path.join(FOTOS, os.path.splitext(nombre)[0] + ".fits"), overwrite=True)


def huella_en_cache(nombre, completa):
    """La huella como la deja fondo_planetario.lista(), y la versión a campo completo si se pide."""
    ruta_jpg = os.path.join(FOTOS, nombre)
    base = os.path.splitext(ruta_jpg)[0]
    sj, sf = os.stat(ruta_jpg), os.stat(base + ".fits")
    clave = f"{nombre}|{sj.st_size}_{sj.st_mtime_ns}|{sf.st_size}_{sf.st_mtime_ns}"
    h = {"nombre": nombre, "ancho": 2400, "alto": 1600,
         "esquinas": [[11.2, 41.9], [10.1, 41.9], [10.1, 40.9], [11.2, 40.9]],
         "esquinas_completa": [[11.4, 42.0], [9.9, 42.0], [9.9, 40.8], [11.4, 40.8]],
         "area_deg2": 1.1, "area_completa_deg2": 1.5, "objeto": "M 31", "fecha": "29/09/2026 23:15"}
    fondo_planetario._guardar_cache({clave: h})
    if completa:
        os.makedirs(fondo_planetario.CARPETA_MINIATURAS, exist_ok=True)
        destino = fondo_planetario._ruta_completa(base, sf, fondo_planetario.LADO_MINIATURA)
        cv2.imwrite(destino, np.full((683, 1024, 3), 60, np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 84])


def solucion_de_prueba(simbad=None):
    """Una solución como la de galeria_anotada: M 31 en el centro de un campo de 2400×1600 px a 2″/px."""
    return {"ancho": 2400, "alto": 1600, "zona": None, "crpix": [1200.5, 800.5], "crval": [10.6847, 41.2689],
            "cd": [[-2.0 / 3600, 0.0], [0.0, 2.0 / 3600]], "escala_arcsec": 2.0, "angulo_cam": 0.0,
            "simbad": simbad if simbad is not None else [{"nombre": "Prueba SIMBAD", "ra": 10.9, "dec": 41.4, "tamano_arcsec": 90.0,
                                                          "mag": 9.5, "fuente": "simbad"}],
            "resuelta": "2026-09-30 01:00:00"}


def solucion_en_cache(simbad=None, quitar=False):
    """datos/anotaciones_galeria.json con la solución de M 31 (la clave es el tamaño y la fecha del FITS) o sin ella."""
    st = os.stat(os.path.join(FOTOS, "M_31_20260929_231500.fits"))
    galeria_anotada._guardar_en_cache(f"{st.st_size}_{st.st_mtime_ns}", None if quitar else solucion_de_prueba(simbad))


def casos_anotaciones_publicas():
    """La lista blanca y la escala de las anotaciones de la Luna y el Sol, con los datos de sus módulos."""
    luna = {"ok": True, "luna": True, "ancho": 3200, "alto": 2400, "iluminada": 0.89, "dudosa": False,
            "disco": {"cx": 1600.0, "cy": 1200.0, "r": 1000.0}, "terminador": [[100.0, 200.0], [300.0, 400.0]],
            "mares": "M100 200l10 -20 4 6M-40 8l2 2", "orientacion": {"giro": 10.0, "espejo": False, "latitud": LAT},
            "objetos": [{"nombre": "Mare Imbrium", "familia": "mar", "x": 800.0, "y": 600.0, "a": 200.0, "b": 100.0, "angulo": 12.5, "luz": True},
                        {"nombre": "Tycho", "familia": "crater", "x": 200.0, "y": 400.0, "a": 20.0, "b": 16.0, "angulo": 8.0, "luz": True, "rango": 1}]}
    p = publicar.anotaciones_publicas(luna, 1600, 1200)
    assert p["mares"] == "M50 100l5 -10 2 3M-20 4l1 1" and p["terminador"] == [[50.0, 100.0], [150.0, 200.0]], p
    assert p["disco"] == {"cx": 800.0, "cy": 600.0, "r": 500.0} and p["objetos"] == [
        {"nombre": "Mare Imbrium", "familia": "mar", "luz": True, "angulo": 12.5, "x": 400.0, "y": 300.0, "a": 100.0, "b": 50.0},
        {"nombre": "Tycho", "familia": "crater", "luz": True, "rango": 1, "angulo": 8.0, "x": 100.0, "y": 200.0, "a": 10.0, "b": 8.0}], p
    assert set(p) == {"ancho", "alto", "luna", "disco", "mares", "terminador", "iluminada", "dudosa", "objetos"}, set(p)
    sol = {"ok": True, "sol": True, "ancho": 3200, "alto": 2400, "guardado": True, "origen": "guardado",
           "disco": {"cx": 1600.0, "cy": 1200.0, "r": 1000.0, "cortado": False},
           "manchas": [{"x": 800.0, "y": 600.0, "r_px": 8.0, "umbra": True, "grupo": 1, "lat": 12.0, "dlon": -3.0, "area_mhs": 40.0, "zona": 3}],
           "grupos": [{"id": 1, "n": 1, "x": 800.0, "y": 600.0, "lat": 12.0, "dlon": -3.0, "area_mhs": 40.0,
                       "km2": 3000000, "diametro_km": 2000, "largo_km": 66000}], "resolucion_km": 8000,
           "wolf": {"g": 1, "f": 1, "R": 18.7, "k": 1.7}, "geometria": {"P": 1.0, "B0": 2.0, "L0": 3.0}, "zonas": [], "medida": {"ruido": 0.1}}
    p = publicar.anotaciones_publicas(sol, 1600, 1200)
    assert p["manchas"] == [{"x": 400.0, "y": 300.0, "r_px": 4.0, "umbra": True, "grupo": 1}], p
    # Los tamaños son del Sol, no de la foto: no se escalan
    assert p["grupos"] == [{"id": 1, "n": 1, "lat": 12.0, "dlon": -3.0, "km2": 3000000, "diametro_km": 2000, "largo_km": 66000, "x": 400.0, "y": 300.0}], p
    assert p["resolucion_km"] == 8000, p
    assert p["disco"] == {"cx": 800.0, "cy": 600.0, "r": 500.0, "cortado": False} and p["wolf"]["R"] == 18.7, p
    assert set(p) == {"ancho", "alto", "sol", "disco", "wolf", "manchas", "grupos", "resolucion_km"}, set(p)
    assert publicar.anotaciones_publicas(dict(sol, manchas=[], grupos=[]), 1600, 1200) is None
    assert publicar.anotaciones_publicas({"ok": True, "ancho": 3200, "alto": 2400, "objetos": []}, 1600, 1200) is None
    wolf = publicar.meta_publica("Sol_20261022_120000.jpg", {"wolf": {"g": 2, "f": 9, "R": 49.3, "k": 1.7, "manchas": [{}], "guardado_en": "x"}}, publicar.leer_config())["wolf"]
    assert wolf == {"R": 49.3, "g": 2, "f": 9, "k": 1.7}, wolf
    print("✅ anotaciones de la Luna y el Sol: a la escala publicada, por lista blanca y sin nada si no hay qué pintar")


def datos_del_sol():
    """La serie del Wolf y las medidas: de la foto publicada, de otra sin publicar y de una ya borrada."""
    foto_sintetica("Sol_20261020_110000.jpg", lado=300, semilla=4)
    with open(manchas_solares.RUTA_SERIE, "w", encoding="utf-8", newline="") as f:
        f.write("fecha,foto,g,f,R,k,area_mhs,a_mano\n2026-10-20T11:00:00,Sol_20261020_110000.jpg,3,8,38,1,50,1\n"
                "2026-10-22T12:00:00,Sol_20261022_120000.jpg,2,9,29,1,50,1\n")

    def medida(instante, fecha):
        return {"instante": instante, "fecha": fecha, "avisos": [], "calima": 0.04, "clave": "1_1", "ajuste": {"R": {"i95": 0.6}},
                "diametro": {"r_arcsec": 963.5, "r_efem": 963.7, "desvio_pct": -0.02, "distancia": 0.9925, "distancia_efem": 0.9923,
                             "altura": 33.0, "escala": 2.9}}
    with open(fotometria_solar.RUTA_MEDIDAS, "w", encoding="utf-8") as f:
        json.dump({"medidas": {"Sol_20261020_110000.jpg": medida(1792494000.0, "2026-10-20T11:00:00"),
                               "Sol_20261022_120000.jpg": medida(1792670400.0, "2026-10-22T12:00:00"),
                               "Sol_20260101_120000.jpg": medida(1767265200.0, "2026-01-01T12:00:00")}}, f)


def reiniciar(raiz_servidor):
    """Cola, derivadas y hosting a cero (el hilo de publicar sigue vivo entre casos)."""
    publicar.esperar(30)
    for r in (publicar.RUTA_ESTADO, publicar.CARPETA_SALIDA, raiz_servidor):
        if os.path.isdir(r):
            shutil.rmtree(r, ignore_errors=True)
        elif os.path.isfile(r):
            os.remove(r)
    os.makedirs(raiz_servidor, exist_ok=True)
    with publicar._lock:
        publicar._verificar_remoto = True
        publicar._sin_permiso_comun = False
        publicar.ESTADO.update({"error_clave": False, "sin_red": False, "ultimo_error": ""})


def esperar(cond, segundos=60):
    for __ in range(int(segundos * 10)):
        if cond():
            return True
        time.sleep(0.1)
    return False


def sincronizado(timeout=90):
    assert publicar.esperar(timeout), f"la cola no se vació: {publicar.estado()}"
    e = publicar.estado()
    assert not e["error_clave"] and not publicar._leer_estado()["cola"], e


def ficheros_de(raiz):
    salida = {}
    for c, __, fs in os.walk(raiz):
        for f in fs:
            r = os.path.join(c, f)
            salida[os.path.relpath(r, raiz).replace(os.sep, "/")] = r
    return salida


def leer_json(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


# ───────────────────────── Casos ─────────────────────────
def casos(endpoint, raiz_servidor, servidor=None, etiqueta=""):
    CONFIG["endpoint"] = endpoint
    reiniciar(raiz_servidor)
    huella_en_cache("M_31_20260929_231500.jpg", completa=True)     # la tanda anterior reescribe el JPG y pierde la huella
    cfg = publicar.leer_config()
    assert cfg["configurado"] and cfg["nombre"] == NOMBRE and cfg["autor_publico"] == "Enrique Jiménez", cfg
    assert publicar.url_publica(cfg) == f"http://127.0.0.1:1/galeria/{NOMBRE}/"
    r = publicar.probar()
    assert r.get("ok") and r.get("version") == 3 and r.get("registrado") is False, r
    assert r.get("dueno") is True and r.get("contacto") == CONTACTO, r
    print(f"✅ {etiqueta}: endpoint responde a probar ({r})")

    # 1. Marcar: la foto antigua sin JSON lo gana; la marcada se desmarca sin perder nada
    publicar.marcar("M_31_20260929_231500.jpg")
    publicar.marcar("Sol_20261022_120000.jpg")
    meta_sol = leer_json(os.path.join(FOTOS, "Sol_20261022_120000.json"))
    assert meta_sol["publicada"] is True and meta_sol["objeto"] == "Sol" and meta_sol["n_fotos"] == 40, meta_sol
    publicar.marcar("Otra_20261022_140000.jpg", True)
    publicar.marcar("Otra_20261022_140000.jpg", False)
    m = leer_json(os.path.join(FOTOS, "Otra_20261022_140000.json"))
    assert m["publicada"] is False and m["ajustes"], m
    assert set(publicar.publicadas()) == {"M_31_20260929_231500.jpg", "Sol_20261022_120000.jpg"}
    # Sin coordenadas (y sin ser del Sol ni la Luna) o de tipo Paisaje: la app avisa y no marca
    for foto, motivo in (("Playa_20261022_150000.jpg", "coordenadas"), ("Montes_20261022_160000.jpg", "Paisaje")):
        try:
            publicar.marcar(foto)
            raise AssertionError(f"{foto} no se puede publicar")
        except editor_fotos.ErrorEditor as e:
            assert motivo in str(e), (foto, e)
    assert set(publicar.publicadas()) == {"M_31_20260929_231500.jpg", "Sol_20261022_120000.jpg"}
    assert publicar.por_que_no_publicable("Luna_20261022_210000.jpg", {}) == "", "la Luna no necesita coordenadas"
    # Marcada a mano sin coordenadas: se queda fuera del índice (el endpoint rechazaría el índice entero)
    json_foto("Playa_20261022_150000.jpg", objeto="Playa", tipo="", ra=None, dec=None, publicada=True)
    sincronizado()

    # 2. Qué ha llegado al hosting
    remoto = ficheros_de(raiz_servidor)
    assert ".htaccess" in remoto and f"{NOMBRE}/galeria.json" in remoto, sorted(remoto)
    # Las páginas del visor: el dueño las sube a _comun y el endpoint las copia a la carpeta del nombre
    V = publicar.version.VERSION
    for p in PAGINAS:
        assert f"_comun/{V}/publico/paginas/{p}" in remoto and f"{NOMBRE}/{p}" in remoto, (p, sorted(remoto))
    assert sha(remoto[f"{NOMBRE}/en/index.html"]) == sha(remoto[f"_comun/{V}/publico/paginas/en/index.html"])
    assert "Gallery" in open(remoto[f"{NOMBRE}/en/index.html"], encoding="utf-8").read()
    if servidor:
        assert not any(a == "subir" and r.startswith(NOMBRE + "/") and not r.endswith((".jpg", ".json")) for a, r in servidor.registro), \
            "a la carpeta del nombre solo suben .jpg y .json"
    for base in ("M_31_20260929_231500", "Sol_20261022_120000"):
        for sufijo in (".jpg", "_mini.jpg", ".json"):
            assert f"{NOMBRE}/{base}{sufijo}" in remoto, sorted(remoto)
    assert f"{NOMBRE}/M_31_20260929_231500_cielo.jpg" in remoto and f"{NOMBRE}/Sol_20261022_120000_cielo.jpg" not in remoto, sorted(remoto)
    assert f"{NOMBRE}/Otra_20261022_140000.jpg" not in remoto
    im = cv2.imread(remoto[f"{NOMBRE}/M_31_20260929_231500.jpg"])
    mini = cv2.imread(remoto[f"{NOMBRE}/M_31_20260929_231500_mini.jpg"])
    assert max(im.shape[:2]) == 1600 and max(mini.shape[:2]) == 480, (im.shape, mini.shape)
    # Privacidad: nada del observatorio, de los ajustes ni de las rutas en lo que sale
    for rel, ruta in remoto.items():
        if not rel.startswith(NOMBRE + "/") or not rel.endswith(".json"):
            continue
        texto = open(ruta, encoding="utf-8").read()
        for prohibido in (str(LAT), str(LON), "ajustes", "encuadre", "static/", "fotos/", "C:\\", "firmada", "publicada"):
            assert prohibido not in texto, f"{rel} contiene {prohibido!r}"
    indice = leer_json(remoto[f"{NOMBRE}/galeria.json"])
    assert indice["version"] == 1 and indice["nombre"] == NOMBRE and indice["autor"] == "Enrique Jiménez"
    assert indice["contacto"] == CONTACTO and set(indice) <= set(ESQUEMA_INDICE["obj"]), sorted(indice)
    assert indice["observador"] == {"latitud": 40.0, "longitud": -4.0, "redondeo_grados": 1}, indice["observador"]
    fotos = {f["id"]: f for f in indice["fotos"]}
    assert set(fotos) == {"M_31_20260929_231500", "Sol_20261022_120000"}, sorted(fotos)
    assert not any("Playa_" in r for r in remoto), "la marcada a mano sin coordenadas no sube"
    json_foto("Playa_20261022_150000.jpg", objeto="Playa", tipo="", ra=None, dec=None)
    m31, sol = fotos["M_31_20260929_231500"], fotos["Sol_20261022_120000"]
    assert m31["tipo"] == "Galaxia" and m31["fecha_iso"] == "2026-09-29T23:15" and m31["n_fotos"] == 40 and m31["exposicion"] == 20.0
    assert m31["nota"] == {"puntuacion": 3.42, "estrellas": 812, "fwhm": 2.3} and m31["ra"] == "00 42 44", m31
    assert m31["ancho"] == 1600 and m31["alto"] == 1067 and set(m31["v"]) == {"jpg", "mini", "cielo", "anotaciones"}, m31
    assert m31["cielo"]["modo"] == "completa" and m31["cielo"]["esquinas"][0] == [11.4, 42.0] and m31["cielo"]["ancho"] == 1024, m31["cielo"]
    assert m31["cielo"]["area_deg2"] == 1.5 and "latitud" not in m31
    assert m31["video"] == {"frames": 2398, "duracion_s": 120}, m31["video"]
    assert sol["tipo"] == "Sol" and sol["cielo"] is None and "cielo" not in sol["ficheros"] and sol["video"] is None, sol
    assert sol["fecha_iso"] == "2026-10-22T12:00" and sol["n_fotos"] == 40 and sol["autor"] == "Enrique Jiménez", sol
    # Las gráficas del Sol: también la foto sin publicar, no la borrada, por lista blanca y con su curva del año
    g = indice["sol"]
    assert [(d["fecha"], d["g"], d["f"], d["n"]) for d in g["wolf"]] == [("2026-10-20T11:00:00", 3, 8, 1), ("2026-10-22T12:00:00", 2, 9, 1)], g["wolf"]
    assert set(g["wolf"][0]) == {"fecha", "g", "f", "R", "k", "n"} and g["orbita"] is None, g["wolf"]
    assert len(g["diametro"]) == 2 and set(g["diametro"][0]) == {"instante", "r_arcsec", "r_efem", "desvio_pct", "distancia", "fiable"}, g["diametro"]
    assert [d["instante"] for d in g["diametro"]] == [1792494000.0, 1792670400.0] and len(g["curva"]) > 60 and 940 < g["curva"][0]["r"] < 990, g["curva"][:1]
    assert indice["fotos"][0]["id"] == "Sol_20261022_120000", "las más recientes primero"
    ficha = leer_json(remoto[f"{NOMBRE}/M_31_20260929_231500.json"])
    assert ficha["id"] == "M_31_20260929_231500" and ficha["v"] == m31["v"]
    print(f"✅ {etiqueta}: 2 fotos publicadas, derivadas 1600/480 px, JSON por lista blanca, observador redondeado, huella solo con FITS; "
          "la foto sin coordenadas y la de tipo Paisaje no se dejan marcar ni suben")

    # Anotaciones: las de la caché, a la escala del JPG publicado; la foto sin solución, sin el fichero y sin resolver nada
    assert m31["ficheros"]["anotaciones"] == "M_31_20260929_231500_anotaciones.json", m31["ficheros"]
    assert m31["v"]["anotaciones"] == sha(remoto[f"{NOMBRE}/M_31_20260929_231500_anotaciones.json"])[:10]
    assert "anotaciones" not in sol["ficheros"] and f"{NOMBRE}/Sol_20261022_120000_anotaciones.json" not in remoto, sol["ficheros"]
    anot = leer_json(remoto[f"{NOMBRE}/M_31_20260929_231500_anotaciones.json"])
    assert anot["ancho"] == 1600 and anot["alto"] == 1067 and set(anot) == {"ancho", "alto", "objetos"}, set(anot)
    originales = {o["nombre"]: o for o in galeria_anotada.objetos_foto("M_31_20260929_231500.jpg", solucion_de_prueba())["objetos"]}
    publicos = {o["nombre"]: o for o in anot["objetos"]}
    assert "M32" in publicos and "Prueba SIMBAD" in publicos and set(publicos) == set(originales), sorted(publicos)
    assert publicos["Prueba SIMBAD"]["fuente"] == "simbad" and publicos["Prueba SIMBAD"]["mag"] == 9.5, publicos["Prueba SIMBAD"]
    for n, o in publicos.items():
        assert set(o) <= {"nombre", "x", "y", "a", "b", "angulo", "mag", "fuente"}, o
        assert "angulo" in o and "mag" in o, o              # el visor distingue None de que no venga
        for campo in "xyab":
            assert abs(o[campo] - originales[n][campo] * 1600 / 2400) <= 0.11, (n, campo, o, originales[n])
    texto = open(remoto[f"{NOMBRE}/M_31_20260929_231500_anotaciones.json"], encoding="utf-8").read()
    for prohibido in (str(LAT), str(LON), "latitud", "longitud", "crval", "resuelta", "zona", "C:\\"):
        assert prohibido not in texto, f"las anotaciones contienen {prohibido!r}"
    print(f"✅ {etiqueta}: anotaciones de la caché escaladas al JPG publicado, por lista blanca; la foto sin solución no las lleva")

    # 3. Orden: galeria.json es la última subida de la primera tanda
    if servidor:
        subidas = [r for a, r in servidor.registro if a == "subir"]
        assert subidas[-1] == f"{NOMBRE}/galeria.json" and subidas[0] == ".htaccess", subidas
        assert subidas.index(f"{NOMBRE}/M_31_20260929_231500_mini.jpg") < subidas.index(f"{NOMBRE}/M_31_20260929_231500.json")
        servidor.registro.clear()
    # Retirar: primero el índice nuevo, después los borrados
    publicar.marcar("Sol_20261022_120000.jpg", False)
    sincronizado()
    remoto = ficheros_de(raiz_servidor)
    assert not any("Sol_20261022_120000" in r for r in remoto), sorted(remoto)
    indice = leer_json(remoto[f"{NOMBRE}/galeria.json"])
    assert [f["id"] for f in indice["fotos"]] == ["M_31_20260929_231500"] and indice["sol"] is None, indice["sol"]
    if servidor:
        reg = list(servidor.registro)
        assert reg[0] == ("subir", f"{NOMBRE}/galeria.json") and all(a == "borrar" for a, __ in reg[1:]) and len(reg) == 4, reg
        servidor.registro.clear()
    assert not os.path.isfile(os.path.join(publicar.CARPETA_SALIDA, NOMBRE, "Sol_20261022_120000.jpg")), "derivadas locales de la retirada"
    print(f"✅ {etiqueta}: retirar sube el índice nuevo y después borra los 3 ficheros")

    # 4. El hosting falla dos veces: se reintenta (la cola queda en disco mientras tanto) y acaba
    if servidor:
        with servidor.lock:
            servidor.fallos_pendientes = 2
        publicar.marcar("Sol_20261022_120000.jpg", True)
        assert esperar(lambda: publicar.estado()["ultimo_error"] != "" or not publicar._leer_estado()["cola"], 20)
        sincronizado()
        assert f"{NOMBRE}/Sol_20261022_120000.jpg" in ficheros_de(raiz_servidor)
        assert publicar.estado()["ultimo_error"] == "" and publicar.estado()["ultimo_ok"], publicar.estado()
        print(f"✅ {etiqueta}: dos fallos del hosting seguidos de reintentos; cola vaciada")

    # 5. Clave rechazada: cola parada y aviso; con la clave buena, sigue
    CONFIG["clave"] = "otra-clave-que-no-es"
    publicar.marcar("Otra_20261022_140000.jpg", True)
    assert esperar(lambda: publicar.estado()["error_clave"] and publicar.estado()["parada"], 20), publicar.estado()
    assert publicar._leer_estado()["cola"], "la cola debe seguir en disco"
    assert "clave" in publicar.estado()["ultimo_error"].lower(), publicar.estado()
    try:
        publicar.probar()
        raise AssertionError("probar debía fallar con la clave mala")
    except (publicar.ErrorClave, publicar.ErrorNombre):     # probar() registra antes: el nombre es de otro con esa clave
        pass
    CONFIG["clave"] = CLAVE
    publicar.sincronizar()
    sincronizado()
    assert f"{NOMBRE}/Otra_20261022_140000.jpg" in ficheros_de(raiz_servidor)
    print(f"✅ {etiqueta}: clave rechazada para la cola y vuelve a andar con la buena")

    # 6. Sin red: espera sin contar intentos y sigue cuando vuelve
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto_cerrado = s.getsockname()[1]
    CONFIG["endpoint"] = f"http://127.0.0.1:{puerto_cerrado}/publicar.php"
    SEMILLA[0] += 1
    foto_sintetica("M_31_20260929_231500.jpg", semilla=SEMILLA[0])     # reescrita: contenido y fecha nuevos
    v_antes = m31["v"]["jpg"]
    publicar.sincronizar()
    assert esperar(lambda: publicar.estado()["sin_red"], 20), publicar.estado()
    assert publicar._leer_estado()["cola"][0]["intentos"] == 0
    CONFIG["endpoint"] = endpoint
    publicar.sincronizar()
    sincronizado()
    assert not publicar.estado()["sin_red"]
    indice = leer_json(ficheros_de(raiz_servidor)[f"{NOMBRE}/galeria.json"])
    v_despues = {f["id"]: f for f in indice["fotos"]}["M_31_20260929_231500"]["v"]["jpg"]
    assert v_despues != v_antes, "la foto reescrita debe subirse con v nuevo"
    assert sha(ficheros_de(raiz_servidor)[f"{NOMBRE}/M_31_20260929_231500.jpg"])[:10] == v_despues
    print(f"✅ {etiqueta}: sin red espera y, al volver, la foto reescrita sube con v nuevo")

    # 7. Renombrar en local: borrar la vieja y subir la nueva
    for sufijo in (".jpg", ".json"):
        os.replace(os.path.join(FOTOS, "Otra_20261022_140000" + sufijo), os.path.join(FOTOS, "NGC_7000_20261022_140000" + sufijo))
    publicar.sincronizar()
    sincronizado()
    remoto = ficheros_de(raiz_servidor)
    assert f"{NOMBRE}/NGC_7000_20261022_140000.jpg" in remoto and not any("Otra_" in r for r in remoto), sorted(remoto)
    for sufijo in (".jpg", ".json"):
        os.replace(os.path.join(FOTOS, "NGC_7000_20261022_140000" + sufijo), os.path.join(FOTOS, "Otra_20261022_140000" + sufijo))
    print(f"✅ {etiqueta}: foto renombrada: borrada la vieja y subida la nueva")

    # 8. Borrada por fuera (como eliminar_imagen): al_borrar la quita del hosting
    for sufijo in (".jpg", ".json"):
        os.remove(os.path.join(FOTOS, "Otra_20261022_140000" + sufijo))
    publicar.al_borrar("Otra_20261022_140000.jpg")
    sincronizado()
    assert not any("Otra_" in r for r in ficheros_de(raiz_servidor))
    foto_sintetica("Otra_20261022_140000.jpg", lado=900, semilla=3)
    json_foto("Otra_20261022_140000.jpg", objeto="Otra", tipo="")
    print(f"✅ {etiqueta}: foto borrada en local, retirada del hosting")

    # 9. Reconciliación: alguien borra a mano en el hosting; sincronizar(verificar=True) lo repone
    os.remove(ficheros_de(raiz_servidor)[f"{NOMBRE}/M_31_20260929_231500_mini.jpg"])
    publicar.sincronizar(verificar=True)
    sincronizado()
    assert f"{NOMBRE}/M_31_20260929_231500_mini.jpg" in ficheros_de(raiz_servidor)
    # Lo común retocado dentro de la misma versión también sube al verificar, por su contenido
    ruta_comun = f"_comun/{publicar.version.VERSION}/js/i18n.js"
    with open(ficheros_de(raiz_servidor)[ruta_comun], "wb") as f:
        f.write(b"// otra edicion de la misma version")
    publicar.sincronizar(verificar=True)
    sincronizado()
    assert sha(ficheros_de(raiz_servidor)[ruta_comun]) == sha(os.path.join("static", "js", "i18n.js")), "lo común retocado debe volver a subir"
    print(f"✅ {etiqueta}: lo borrado a mano en el hosting se repone al verificar, y lo común retocado también")

    # Las anotaciones siguen a la caché: otra solución las regenera, sin ella se retiran del hosting
    ruta_anot = f"{NOMBRE}/M_31_20260929_231500_anotaciones.json"
    v_antes = sha(ficheros_de(raiz_servidor)[ruta_anot])
    publicar.sincronizar()
    sincronizado()
    assert sha(ficheros_de(raiz_servidor)[ruta_anot]) == v_antes, "sin cambios no se regeneran"
    solucion_en_cache(simbad=[])
    publicar.sincronizar()
    sincronizado()
    anot = leer_json(ficheros_de(raiz_servidor)[ruta_anot])
    assert "Prueba SIMBAD" not in {o["nombre"] for o in anot["objetos"]} and "M32" in {o["nombre"] for o in anot["objetos"]}, anot
    solucion_en_cache(quitar=True)
    publicar.sincronizar()
    sincronizado()
    remoto = ficheros_de(raiz_servidor)
    assert ruta_anot not in remoto, sorted(remoto)
    indice = leer_json(remoto[f"{NOMBRE}/galeria.json"])
    m31 = {f["id"]: f for f in indice["fotos"]}["M_31_20260929_231500"]
    assert "anotaciones" not in m31["ficheros"] and "anotaciones" not in m31["v"], m31
    solucion_en_cache()
    print(f"✅ {etiqueta}: anotaciones regeneradas al cambiar la caché y retiradas del hosting si ya no hay solución")

    # 10. El endpoint rechaza rutas peligrosas
    cli = publicar.cliente()
    for ruta in ("../x.json", f"{NOMBRE}/../otro/x.json", f"{NOMBRE}/x.php", f"{NOMBRE}/.htaccess", "otro-nombre/x.json",
                 f"{NOMBRE}/sub/.oculto.json", "_comun/x.js", "_comun/../x.js", f"{NOMBRE}/x.phtml", f"{NOMBRE}/x.svg",
                 f"{NOMBRE}/x.html", f"{NOMBRE}/en/otra.html", f"{NOMBRE}/x.js", f"{NOMBRE}/x.css", f"{NOMBRE}/x.png"):
        try:
            cli.subir(ruta, b"{}", hashlib.sha256(b"{}").hexdigest())
            raise AssertionError(f"el endpoint aceptó {ruta!r}")
        except publicar.ErrorClave:
            raise
        except publicar.ErrorHosting:
            pass
    try:
        cli.subir(f"{NOMBRE}/x.json", b"{}", "0" * 64)
        raise AssertionError("el endpoint aceptó un sha256 que no coincide")
    except publicar.ErrorHosting:
        pass
    assert not os.path.isfile(os.path.join(raiz_servidor, NOMBRE, "x.json"))
    cli.subir(f"_comun/{publicar.version.VERSION}/js/x.js", b"//", hashlib.sha256(b"//").hexdigest())
    assert cli.borrar(f"_comun/{publicar.version.VERSION}/js/x.js")["existia"] is True
    try:
        cli.borrar(f"{NOMBRE}/index.html")
        raise AssertionError("las páginas del endpoint no se borran desde la app")
    except publicar.ErrorHosting:
        pass
    assert f"{NOMBRE}/index.html" in ficheros_de(raiz_servidor)
    assert f"{NOMBRE}/index.html" not in cli.listar(), "las páginas no salen en la lista: la app ni las sube ni las borra"
    # Una app anterior a 1.2.3 sube sus propias páginas: se aceptan (para no atascar su cola) y se descartan
    propia = b"<!doctype html><title>mi pagina</title>"
    for pagina in ("index.html", "en/planetario.html"):
        r = cli.subir(f"{NOMBRE}/{pagina}", propia, hashlib.sha256(propia).hexdigest())
        assert r.get("ok") and r.get("ignorado") is True, r
        assert open(ficheros_de(raiz_servidor)[f"{NOMBRE}/{pagina}"], "rb").read() != propia, "la página propia no se guarda"
    print(f"✅ {etiqueta}: rutas con .., .php, .htaccess fuera de la raíz, otro nombre, HTML/JS/CSS en la galería y sha256 falso, rechazadas; el dueño sí sube _comun")

    # 11. Un fichero de 3 MB (como el catálogo del planetario) va en trozos de 1 MB y llega entero;
    #     con el servidor de PHP, que limita cada envío a 2 MB, es la única forma de que entre
    #     Un JPEG de verdad de ruido (≈1,3 MB): en la galería no entra otra cosa
    ruido = np.random.default_rng(5).integers(0, 256, (1066, 1600, 3), dtype=np.uint8)
    grande = cv2.imencode(".jpg", ruido, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
    assert publicar.TROZO < len(grande) < 1_900_000, len(grande)
    cli.subir(f"{NOMBRE}/grande.jpg", grande, hashlib.sha256(grande).hexdigest())
    remoto = ficheros_de(raiz_servidor)
    assert f"{NOMBRE}/grande.jpg" in remoto and open(remoto[f"{NOMBRE}/grande.jpg"], "rb").read() == grande
    assert not any(".subiendo-" in r for r in remoto), "el parcial debe desaparecer"
    try:
        cli.subir(f"{NOMBRE}/grande2.jpg", grande, "1" * 64)
        raise AssertionError("un sha256 falso del total debía rechazarse")
    except publicar.ErrorHosting:
        pass
    remoto = ficheros_de(raiz_servidor)
    assert f"{NOMBRE}/grande2.jpg" not in remoto and not any(".subiendo-" in r for r in remoto), sorted(remoto)
    try:
        cli.subir(f"{NOMBRE}/grande3.jpg", grande, hashlib.sha256(grande).hexdigest())
        raise AssertionError("la cuota de 2 MB debía agotarse")
    except publicar.ErrorCuota:
        pass
    cli.borrar(f"{NOMBRE}/grande.jpg")
    print(f"✅ {etiqueta}: JPEG de {len(grande) // 1000} KB subido en trozos de 1 MB y comprobado entero; sha256 falso rechazado sin dejar parciales; cuota de 2 MB respetada")

    # 12. Registro abierto: otro ScopePower reserva un nombre libre con su clave; el mismo nombre con otra clave, no
    otro = publicar.ClienteHosting(endpoint, "clave-de-otro-usuario-0123456789", "otro-usuario")
    r = otro.registrar()
    assert r.get("registrado") is True and r.get("contacto") == CONTACTO, r
    assert otro.registrar().get("registrado") is False, "la segunda vez el nombre ya es suyo"
    r = otro.probar()
    assert r.get("ok") and r.get("dueno") is False and r.get("contacto") == CONTACTO, r
    FICHA_MINIMA = json.dumps({"id": "x", "ra": "00 42 44", "dec": "+41 16 09"}).encode()
    otro.subir("otro-usuario/x.json", FICHA_MINIMA, hashlib.sha256(FICHA_MINIMA).hexdigest())
    assert "otro-usuario/x.json" in ficheros_de(raiz_servidor)
    # Al registrarlo ya tiene las páginas del visor, puestas por el endpoint
    for p in PAGINAS:
        assert f"otro-usuario/{p}" in ficheros_de(raiz_servidor), (p, sorted(ficheros_de(raiz_servidor)))
    intruso = publicar.ClienteHosting(endpoint, "clave-del-intruso-abcdefghijklmnop", "otro-usuario")
    for intento in (intruso.registrar, intruso.probar):
        try:
            intento()
            raise AssertionError("el nombre de otro no se puede usar")
        except (publicar.ErrorNombre, publicar.ErrorClave):
            pass
    for reservado in ("admin", "scopepower"):
        try:
            publicar.ClienteHosting(endpoint, "clave-de-otro-usuario-0123456789", reservado).registrar()
            raise AssertionError(f"{reservado} está reservado")
        except publicar.ErrorNombre:
            pass
    try:
        publicar.ClienteHosting(endpoint, "corta", "nombre-libre").registrar()
        raise AssertionError("una clave corta no vale para registrar")
    except publicar.ErrorHosting:
        pass
    otro.borrar("otro-usuario/x.json")
    print(f"✅ {etiqueta}: registro abierto: nombre libre reservado desde la app, el mismo nombre con otra clave y los reservados rechazados")

    # 13. El reparto de permisos: un nombre registrado no toca lo común ni el .htaccess (403) ni sube páginas a su carpeta
    V = publicar.version.VERSION
    sha_js = hashlib.sha256(b"// mal").hexdigest()
    for ruta in (f"_comun/{V}/js/i18n.js", f"_comun/{V}/publico/paginas/index.html", ".htaccess", f"_comun/{V}/x.js"):
        for intento in (lambda r=ruta: otro.subir(r, b"// mal", sha_js), lambda r=ruta: otro.borrar(r)):
            try:
                intento()
                raise AssertionError(f"un nombre registrado tocó {ruta!r}")
            except publicar.ErrorPermiso:
                pass
    assert sha(ficheros_de(raiz_servidor)[f"_comun/{V}/js/i18n.js"]) == sha(os.path.join("static", "js", "i18n.js")), "lo común sigue intacto"
    for ruta in ("otro-usuario/x.html", "otro-usuario/en/x.html", "otro-usuario/x.js", "otro-usuario/x.css", "otro-usuario/x.php",
                 f"{NOMBRE}/x.json"):
        try:
            otro.subir(ruta, FICHA_MINIMA, hashlib.sha256(FICHA_MINIMA).hexdigest())
            raise AssertionError(f"el endpoint aceptó {ruta!r} de un nombre registrado")
        except publicar.ErrorPermiso:
            assert ruta.startswith(NOMBRE + "/"), ruta          # la carpeta de otro: 403; lo demás, 400
        except publicar.ErrorHosting:
            assert not ruta.startswith(NOMBRE + "/"), ruta
    propia = b"<!doctype html><title>phishing</title>"
    r = otro.subir("otro-usuario/index.html", propia, hashlib.sha256(propia).hexdigest())
    assert r.get("ignorado") is True, r
    remoto = ficheros_de(raiz_servidor)
    assert not any(r.startswith("otro-usuario/x") for r in remoto) and f"{NOMBRE}/x.json" not in remoto, sorted(remoto)
    assert "Gallery" in open(remoto["otro-usuario/en/index.html"], encoding="utf-8").read(), "la página del endpoint sigue intacta"
    assert propia != open(remoto["otro-usuario/index.html"], "rb").read(), "la página propia no se guarda"
    # Un nombre registrado desde la app que está en 'duenos' sí es el dueño (sin copiar su clave a la configuración)
    dueno2 = publicar.ClienteHosting(endpoint, "clave-del-dueno-registrado-0123456789", "dueno-registrado")
    assert dueno2.registrar().get("registrado") is True and dueno2.probar().get("dueno") is True
    dueno2.subir(f"_comun/{V}/js/x.js", b"//", hashlib.sha256(b"//").hexdigest())
    assert dueno2.borrar(f"_comun/{V}/js/x.js")["existia"] is True
    print(f"✅ {etiqueta}: un nombre registrado recibe 403 en _comun y .htaccess y no puede subir HTML, JS ni CSS a su carpeta; uno de 'duenos' sí")

    # 14. Lo que llega a una galería se comprueba: JPEG de verdad y acotado, JSON por lista blanca
    def rechazado(ruta, datos, por):
        try:
            otro.subir(ruta, datos, hashlib.sha256(datos).hexdigest())
            raise AssertionError(f"el endpoint aceptó {ruta!r} ({por})")
        except publicar.ErrorContenido as e:
            return str(e)

    no_jpeg = b"GIF89a" + bytes(200)
    rechazado("otro-usuario/foto.jpg", no_jpeg, "no es JPEG")
    rechazado("otro-usuario/foto.jpg", b"<html><script>alert(1)</script></html>", "HTML con nombre de JPG")
    png = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    rechazado("otro-usuario/foto.jpg", png, "PNG con nombre de JPG")
    enorme = cv2.imencode(".jpg", np.zeros((10, 1701, 3), np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 50])[1].tobytes()
    assert "1700" in rechazado("otro-usuario/foto.jpg", enorme, "1701 px de lado")
    justo = cv2.imencode(".jpg", np.zeros((10, 1700, 3), np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 50])[1].tobytes()
    otro.subir("otro-usuario/foto.jpg", justo, hashlib.sha256(justo).hexdigest())
    rechazado("otro-usuario/x.json", b"{no es json", "JSON roto")
    rechazado("otro-usuario/x.json", b"[1, 2]", "lista en vez de ficha")
    rechazado("otro-usuario/x.json", json.dumps({"id": "x", "latitud": 40.4}).encode(), "clave fuera de la lista blanca")
    rechazado("otro-usuario/x.json", json.dumps({"id": "x", "ficheros": {"jpg": "../otro/x.jpg"}}).encode(), "fichero con ruta")
    rechazado("otro-usuario/x.json", json.dumps({"id": "x", "objeto": "a" * 201}).encode(), "texto de más de 200")
    rechazado("otro-usuario/x.json", json.dumps({"id": "x", "nota": {"puntuacion": "3"}}).encode(), "número como texto")
    rechazado("otro-usuario/x.json", b"{" + b'"a":' * 1 + b"1}" * 1 + bytes(MAX_JSON), "ficha de más de 256 KB")
    assert "coordenadas" in rechazado("otro-usuario/x.json", b"{}", "ficha sin nada")
    assert "coordenadas" in rechazado("otro-usuario/x.json", json.dumps({"id": "x", "ra": "00 42 44", "dec": ""}).encode(), "ficha con dec vacío")
    assert "Paisaje" in rechazado("otro-usuario/x.json", json.dumps({"id": "x", "tipo": "Paisaje", "ra": "00 42 44", "dec": "+41 16 09"}).encode(), "Paisaje")
    for tipo in TIPOS_SIN_COORDENADAS:
        sin = json.dumps({"id": "x", "tipo": tipo}).encode()
        otro.subir("otro-usuario/x.json", sin, hashlib.sha256(sin).hexdigest())
    ficha = publicar.meta_publica("M_31_20260929_231500.jpg", json_de_prueba(), publicar.leer_config())
    ficha.update({"ancho": 1600, "alto": 1067, "cielo": None, "ficheros": {"jpg": "M_31_20260929_231500.jpg", "mini": "M_31_20260929_231500_mini.jpg"},
                  "v": {"jpg": "0123456789", "mini": "abcdef0123"}})
    otro.subir("otro-usuario/M_31_20260929_231500.json", json.dumps(ficha).encode(), hashlib.sha256(json.dumps(ficha).encode()).hexdigest())
    indice = leer_json(ficheros_de(raiz_servidor)[f"{NOMBRE}/galeria.json"])
    assert indice["fotos"], "hace falta un índice real para variarlo"

    def indice_variado(**cambios):
        d = json.loads(json.dumps(indice))
        d.update(cambios)
        return json.dumps(d).encode()

    assert "no se publica" in rechazado("otro-usuario/galeria.json", indice_variado(latitud=40.4), "clave desconocida en el índice")
    f0 = json.loads(json.dumps(indice["fotos"][0]))
    f0["latitud"] = 40.4
    assert "no se publica" in rechazado("otro-usuario/galeria.json", indice_variado(fotos=[f0]), "clave desconocida en una foto")
    f0 = json.loads(json.dumps(indice["fotos"][0]))
    f0["tipo"] = "Agujero negro"
    assert "tipo" in rechazado("otro-usuario/galeria.json", indice_variado(fotos=[f0]), "tipo inventado")
    f0 = json.loads(json.dumps(indice["fotos"][0]))
    f0["id"] = "../../x"
    rechazado("otro-usuario/galeria.json", indice_variado(fotos=[f0]), "id con ruta")
    f0 = json.loads(json.dumps(indice["fotos"][0]))
    f0["tipo"], f0["ra"], f0["dec"] = "Galaxia", None, None       # la primera del índice puede ser la del Sol
    assert "coordenadas" in rechazado("otro-usuario/galeria.json", indice_variado(fotos=[indice["fotos"][0], f0]), "foto sin coordenadas")
    f0["tipo"] = "Luna"
    otro.subir("otro-usuario/galeria.json", indice_variado(fotos=[f0]), hashlib.sha256(indice_variado(fotos=[f0])).hexdigest())
    f0["tipo"] = "Paisaje"
    assert "Paisaje" in rechazado("otro-usuario/galeria.json", indice_variado(fotos=[f0]), "Paisaje en el índice")
    muchas = [dict(indice["fotos"][0], id=f"foto_{i}") for i in range(MAX_FOTOS + 1)]
    assert "1000" in rechazado("otro-usuario/galeria.json", indice_variado(fotos=muchas), "1001 fotos")
    rechazado("otro-usuario/galeria.json", json.dumps({"fotos": []}).encode(), "sin version")
    rechazado("otro-usuario/galeria.json", json.dumps({"version": 1}).encode(), "sin fotos")
    rechazado("otro-usuario/galeria.json", indice_variado(sol={"wolf": [{"fecha": "x", "g": 1, "foto": "Sol.jpg"}]}), "clave de más en la serie del Sol")
    rechazado("otro-usuario/galeria.json", indice_variado(contacto=["x"]), "contacto que no es texto")
    anot = {"ancho": 1600, "alto": 1067, "objetos": [{"nombre": "M32", "x": 1.0, "y": 2.0, "a": 3.0, "b": 4.0, "angulo": None, "mag": 8.1,
                                                       "fuente": "catalogo", "ra": 10.6}]}
    rechazado("otro-usuario/x_anotaciones.json", json.dumps(anot).encode(), "coordenada en las anotaciones")
    anot["objetos"][0].pop("ra")
    otro.subir("otro-usuario/x_anotaciones.json", json.dumps(anot).encode(), hashlib.sha256(json.dumps(anot).encode()).hexdigest())
    # Un galeria.json válido (el real, con 1000 fotos justas, de 'otro') entra, y con él llegan las páginas aunque se hayan borrado
    for p in PAGINAS:
        os.remove(os.path.join(raiz_servidor, "otro-usuario", p.replace("/", os.sep)))
    justas = [dict(indice["fotos"][0], id=f"foto_{i}") for i in range(MAX_FOTOS)]
    otro.subir("otro-usuario/galeria.json", indice_variado(fotos=justas, nombre="otro-usuario"), hashlib.sha256(indice_variado(fotos=justas, nombre="otro-usuario")).hexdigest())
    remoto = ficheros_de(raiz_servidor)
    for p in PAGINAS:
        assert f"otro-usuario/{p}" in remoto, (p, sorted(remoto))
    assert leer_json(remoto["otro-usuario/galeria.json"])["nombre"] == "otro-usuario"
    for r in ("otro-usuario/foto.jpg", "otro-usuario/M_31_20260929_231500.json", "otro-usuario/x_anotaciones.json", "otro-usuario/galeria.json"):
        otro.borrar(r)
    print(f"✅ {etiqueta}: 422 para lo que no es JPEG, pasa de 1700 px, no parsea, lleva claves fuera de la lista blanca, un tipo inventado, "
          "1001 fotos o una foto sin coordenadas que no sea del Sol ni la Luna; lo válido entra y trae las páginas")

    # 15. La app como nombre registrado (no dueño): no encola lo común ni el .htaccess, y el índice lleva el contacto.
    #     Con el servidor falso, primero como si el endpoint fuera viejo (probar sin 'dueno'): la app encola lo
    #     común, recibe el 403, lo da por bueno (una vez en el log) y no lo vuelve a pedir; después con el actual.
    CONFIG["clave"] = "clave-de-otro-usuario-0123456789"
    CONFIG["nombre"] = "otro-usuario"
    publicar.esperar(30)

    def desde_cero(endpoint_viejo):
        i18n_remoto = os.path.join(raiz_servidor, "_comun", V, "js", "i18n.js")
        if endpoint_viejo:
            os.remove(i18n_remoto)       # falta algo en lo común: la app lo encola y se topa con el 403
        with publicar._lock:
            publicar._sin_permiso_comun = False
        estado = publicar._leer_estado()
        estado.update({"comun_version": "", "remoto": {}, "contacto": ""})
        publicar._guardar_estado(estado)
        if servidor:
            with servidor.lock:
                servidor.endpoint_viejo = endpoint_viejo
                servidor.registro.clear()
        publicar.sincronizar(verificar=True)
        sincronizado()
        assert publicar._sin_permiso_comun, "la app tiene que saber que no es el dueño"
        estado = publicar._leer_estado()
        assert estado["comun_version"] == V and not estado["cola"] and estado["ultimo_error"] == "", estado["ultimo_error"]
        remoto = ficheros_de(raiz_servidor)
        assert "otro-usuario/galeria.json" in remoto and "otro-usuario/M_31_20260929_231500.jpg" in remoto, sorted(remoto)
        assert "otro-usuario/index.html" in remoto and "otro-usuario/en/planetario.html" in remoto, "las páginas las puso el endpoint"
        if endpoint_viejo:
            assert f"_comun/{V}/js/i18n.js" not in remoto, "un nombre registrado no repone lo común"
            shutil.copyfile(os.path.join("static", "js", "i18n.js"), i18n_remoto)
        else:
            assert sha(remoto[f"_comun/{V}/js/i18n.js"]) == sha(os.path.join("static", "js", "i18n.js")), "lo común, intacto"
        # Al replanificar sin verificar tampoco encola lo común; al verificar de nuevo, tampoco (sigue sin ser el dueño)
        publicar.sincronizar()
        sincronizado()
        publicar.sincronizar(verificar=True)
        sincronizado()
        assert not publicar._leer_estado()["cola"] and publicar.estado()["ultimo_error"] == ""
        if servidor:
            subidas = [r for a, r in servidor.registro if a == "subir"]
            assert not any(r.startswith("_comun/") or r == ".htaccess" for r in subidas), subidas
        return leer_json(remoto["otro-usuario/galeria.json"])

    if servidor:
        indice = desde_cero(endpoint_viejo=True)
        assert indice["nombre"] == "otro-usuario" and indice["contacto"] == "", indice["contacto"]
    indice = desde_cero(endpoint_viejo=False)
    assert indice["nombre"] == "otro-usuario" and indice["contacto"] == CONTACTO, indice["contacto"]
    CONFIG["clave"], CONFIG["nombre"] = CLAVE, ""
    print(f"✅ {etiqueta}: como nombre registrado, la app publica sin encolar lo común ni el .htaccess y el índice lleva el contacto del hosting")


def casos_visor():
    """El visor se genera en cada idioma con un entorno Jinja propio (sin tocar el idioma de la app) y es genérico."""
    import idioma
    plantillas = os.path.join(RAIZ, "plantillas")
    os.makedirs(plantillas)
    with open(os.path.join(plantillas, "index.html"), "w", encoding="utf-8") as f:
        f.write('<!doctype html><html lang="{{ lang }}"><title>{{ _("Galería") }}</title>'
                '<script src="{{ comun }}/js/i18n.js"></script><p>{{ raiz }}/galeria.json {{ idioma }}</p></html>')
    publicar.CARPETA_PLANTILLAS = plantillas
    salida = os.path.join(RAIZ, "visor")
    generados = publicar.visor(salida)
    assert set(generados) == {"index.html", "en/index.html"}, generados
    es = open(generados["index.html"], encoding="utf-8").read()
    en = open(generados["en/index.html"], encoding="utf-8").read()
    assert 'lang="es-ES"' in es and "Galería" in es and "../_comun/" in es and "./galeria.json" in es, es
    assert 'lang="en-US"' in en and "Gallery" in en and "../../_comun/" in en and "../galeria.json" in en, en
    assert idioma.idioma_actual() == "es", "el visor no debe cambiar el idioma de la app"
    # El visor de verdad (templates/publico): genérico (sin autor, nombre ni dirección: eso lo pone el JS desde
    # galeria.json), rutas relativas, sin Jinja ni rutas de la app; y va a _comun, no a la carpeta del propietario
    publicar.CARPETA_PLANTILLAS = os.path.join("templates", "publico")
    publicar.CARPETA_ESTATICOS = os.path.join("static", "publico")
    publicar.CARPETA_ATLAS = os.path.join("static", "atlas")
    salida = os.path.join(RAIZ, "visor_real")
    generados = publicar.visor(salida)
    assert {"index.html", "en/index.html", "planetario.html", "en/planetario.html"} == set(generados), generados
    pl = open(generados["planetario.html"], encoding="utf-8").read()
    assert "atlas/src/index.js" not in pl and "/atlas/dso-catalog.js" in pl and "url_for" not in pl, pl[:400]
    es = open(generados["index.html"], encoding="utf-8").read()
    en = open(generados["en/index.html"], encoding="utf-8").read()
    for html in (es, en, pl):
        for prohibido in ("{{", "{%", "url_for", "{autor}", "Enrique Jim", "enrique-jimenez", "127.0.0.1", '"url"'):
            assert prohibido not in html, prohibido
        assert 'id="titulo"' in html and 'id="pie-autor"' in html and 'id="contacto"' in html, "el JS rellena título, autor y contacto"
    assert "/static/" not in es and "/static/" not in en        # el planetario enlaza static/atlas/ORIGEN.md en GitHub
    assert "Galería · ScopePower" in es and "Gallery · ScopePower" in en and "Informar de un problema" in es and "Report a problem" in en
    assert '"raiz": "."' in es and '"raiz": ".."' in en and "../_comun/" in es and "../../_comun/" in en
    comun = publicar.archivos_comun()
    assert {"js/i18n.js", "i18n/en.js", "publico/icono.png", "publico/publico.css", "publico/galeria_publica.js",
            "js/luna_anotada.js", "js/manchas_solares.js", "js/fotometria_solar.js"} <= set(comun), sorted(comun)
    assert {"publico/paginas/index.html", "publico/paginas/planetario.html", "publico/paginas/en/index.html",
            "publico/paginas/en/planetario.html"} <= set(comun), sorted(r for r in comun if r.startswith("publico/"))
    assert {"js/atlas_catalogos.js", "js/capa_fotos.js", "atlas/src/index.js", "atlas/dso-catalog.js", "atlas/assets/landscapes/guereins/properties",
            "publico/planetario_publico.js"} <= set(comun), sorted(r for r in comun if not r.startswith("atlas/assets"))
    assert not any(r.endswith(".ts") or "/licenses/" in r for r in comun), "solo lo que el endpoint admite"
    assert all(os.path.isfile(local) for local in comun.values())
    # Los tipos de objeto que admite el endpoint son los de la app
    php = open(os.path.join("hosting", "publicar.php"), encoding="utf-8").read()
    bloque = re.search(r"const CATEGORIAS = \[(.*?)\];", php, re.S).group(1)
    assert tuple(re.findall(r"'([^']+)'", bloque)) == tuple(catalogo_offline.CATEGORIAS), bloque
    publicar.CARPETA_PLANTILLAS = PLANTILLAS
    publicar.CARPETA_ESTATICOS = os.path.join(RAIZ, "sin_estaticos")
    publicar.CARPETA_ATLAS = os.path.join(RAIZ, "sin_atlas")
    print("✅ visor en español y en inglés con el entorno Jinja propio, genérico (sin autor ni dirección) y en _comun; idioma de la app intacto; CATEGORIAS del PHP = las de la app")


def servidor_php(carpeta):
    """php -S con hosting/publicar.php en <carpeta>/scopepower y la galería en <carpeta>/galeria."""
    php = shutil.which("php")
    if not php:
        return None, None
    sp = os.path.join(carpeta, "scopepower")
    os.makedirs(sp)
    shutil.copy(os.path.join("hosting", "publicar.php"), sp)
    with open(os.path.join(sp, "publicar_config.php"), "w", encoding="utf-8") as f:
        f.write("<?php\nreturn ['registro_abierto' => true, 'claves' => ['%s' => '%s'], 'duenos' => ['dueno-registrado'], 'raiz' => __DIR__ . '/../galeria', 'max_bytes' => 8 * 1024 * 1024, 'cuota_nombre_mb' => 2, 'cuota_total_mb' => 50, 'max_nombres' => 5, 'registros_por_dia' => 5, 'contacto' => '%s'];\n" % (NOMBRE, CLAVE, CONTACTO))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    proc = subprocess.Popen([php, "-S", f"127.0.0.1:{puerto}", "-t", carpeta], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    endpoint = f"http://127.0.0.1:{puerto}/scopepower/publicar.php"
    for __ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", puerto), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    return proc, endpoint


# ───────────────────────── Ejecución ─────────────────────────
proc_php = None
try:
    foto_sintetica("M_31_20260929_231500.jpg", semilla=1)
    json_foto("M_31_20260929_231500.jpg", video={"nombre": "M_31_20260929_231500", "frames": 2398, "duracion_s": 120, "usados": 1200})
    fits_sintetico("M_31_20260929_231500.jpg")
    huella_en_cache("M_31_20260929_231500.jpg", completa=True)
    solucion_en_cache()
    datos_del_sol()
    foto_sintetica("Sol_20261022_120000.jpg", lado=1200, semilla=2)        # antigua: sin JSON, con FITS sin WCS (sin huella)
    fits_sintetico("Sol_20261022_120000.jpg")
    foto_sintetica("Otra_20261022_140000.jpg", lado=900, semilla=3)
    json_foto("Otra_20261022_140000.jpg", objeto="Otra", tipo="")
    foto_sintetica("Playa_20261022_150000.jpg", lado=600, semilla=6)        # sin coordenadas: no sale de ScopePower
    json_foto("Playa_20261022_150000.jpg", objeto="Playa", tipo="", ra=None, dec=None)
    foto_sintetica("Montes_20261022_160000.jpg", lado=600, semilla=7)       # Paisaje: no se publica
    json_foto("Montes_20261022_160000.jpg", objeto="Montes", tipo="Paisaje")

    assert publicar.slug("Enrique Jiménez") == "enrique-jimenez" and publicar.slug("  ÑU-2 ") == "nu-2" and publicar.slug("_") == ""
    assert publicar.url_valida("https://scopepower.example") and publicar.url_valida("http://127.0.0.1:8080") and not publicar.url_valida("http://scopepower.example")
    CONFIG["coordenadas"] = "ninguna"
    assert publicar.observador_publico(publicar.leer_config()) is None
    CONFIG["coordenadas"] = "redondeadas"
    print("✅ slug, url_valida y observador 'ninguna'")
    casos_anotaciones_publicas()

    falso = ServidorFalso(os.path.join(RAIZ, "hosting_falso"))
    threading.Thread(target=falso.serve_forever, daemon=True).start()
    casos(falso.endpoint, falso.raiz, falso, "servidor falso")
    casos_visor()

    proc_php, endpoint_php = servidor_php(os.path.join(RAIZ, "php"))
    if proc_php is None:
        print("⚠️ php no está en el PATH: no se prueba hosting/publicar.php de verdad")
    else:
        casos(endpoint_php, os.path.join(RAIZ, "php", "galeria"), None, "PHP real")
    print("✅ Todo bien")
except AssertionError as e:
    print(f"❌ {e}")
    sys.exit(1)
finally:
    if proc_php is not None:
        proc_php.kill()
    shutil.rmtree(RAIZ, ignore_errors=True)
