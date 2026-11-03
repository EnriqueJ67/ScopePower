# herramientas/probar_publicar.py
#
# Prueba sin hosting de la galería pública (publicar.py + hosting/publicar.php) sobre una
# galería de mentira en una carpeta temporal:
#
#   1. Un servidor HTTP falso en Python que habla el mismo contrato que publicar.php
#      (probar / listar / subir / borrar, clave en la cabecera, mismas reglas de rutas) y que
#      se puede poner a fallar, a rechazar la clave o a no estar.
#   2. Marcar y retirar fotos, las derivadas (≤ 1600 y ≤ 480 px), el JSON por lista blanca
#      (sin latitud, longitud, ajustes ni rutas), el índice con el observador redondeado y la
#      huella en el cielo solo donde la hay, el orden de la cola (galeria.json el último al
#      subir y el primero al retirar), reintentos, clave rechazada, sin red, foto reescrita
#      (v nuevo), renombrada (borrar + subir), persistencia de la cola, rutas rechazadas y las
#      anotaciones (de la caché, a la escala del JPG publicado y por lista blanca).
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
import editor_fotos
import fondo_planetario
import galeria_anotada
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
publicar.RUTA_ESTADO = os.path.join(DATOS, "publicar.json")
publicar.CARPETA_SALIDA = os.path.join(DATOS, "publicar")
publicar.CARPETA_PLANTILLAS = os.path.join(RAIZ, "sin_plantillas")
publicar.CARPETA_ESTATICOS = os.path.join(RAIZ, "sin_estaticos")
publicar.ESPERAS_REINTENTO = (0.3,)
publicar.ESPERA_SIN_RED = 0.3
publicar.TIMEOUT = (5, 30)
publicar.clave = lambda: CONFIG["clave"]

CONFIG = {"endpoint": "", "clave": CLAVE, "coordenadas": "redondeadas"}
SEMILLA = [10]                                   # cada tanda reescribe M 31 con un contenido distinto
_leer_config_original = publicar.leer_config


def config_prueba(ruta="config.ini"):
    ini = os.path.join(RAIZ, "config.ini")
    with open(ini, "w") as f:            # la codificación del sistema, como config.ini de la app
        f.write(f"[Observatorio]\nlatitud_observatorio = {LAT}\nlongitud_observatorio = {LON}\nautor = Enrique Jiménez\n"
                f"[Publicar]\nactivado = true\nurl_base = http://127.0.0.1:1\nendpoint = {CONFIG['endpoint']}\n"
                f"coordenadas = {CONFIG['coordenadas']}\nlado_foto = 1600\nlado_mini = 480\n")
    return _leer_config_original(ini)


publicar.leer_config = config_prueba


# ───────────────────────── Servidor falso con el contrato de publicar.php ─────────────────────────
EXTENSIONES = ("jpg", "jpeg", "webp", "png", "json", "html", "js", "css", "md", "txt", "ico")


class ServidorFalso(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, raiz):
        super().__init__(("127.0.0.1", 0), ManejadorFalso)
        self.raiz = raiz
        self.fallos_pendientes = 0
        self.registro = []                   # [(accion, ruta)] en orden de llegada (solo las aceptadas)
        self.claves = {NOMBRE: CLAVE}        # nombres dados de alta a mano (publicar_config.php)
        self.registrados = {}                # nombres reservados desde la app: {nombre: clave}
        self.cuota_nombre = 2 * 1024 * 1024  # como cuota_nombre_mb = 2 en la prueba del PHP real
        self.lock = threading.Lock()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server_address[1]}/scopepower/publicar.php"


def ruta_segura(slug, ruta):
    if ruta == ".htaccess":
        return ".htaccess"
    if len(ruta) > 400 or not re.fullmatch(r"[A-Za-z0-9_./-]+", ruta):
        return None
    seg = ruta.split("/")
    if len(seg) < 2 or any(s in ("", ".", "..") or s.startswith(".") for s in seg):
        return None
    if seg[0] == "_comun":
        if len(seg) < 3 or not re.fullmatch(r"[0-9][A-Za-z0-9.]*", seg[1]):
            return None
    elif seg[0] != slug:
        return None
    ext = seg[-1].rsplit(".", 1)[-1].lower() if "." in seg[-1] else ""
    if ext not in EXTENSIONES and seg[-1] not in ("LICENSE", "properties"):
        return None
    return ruta


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
        valida = clave != "" and (s.claves.get(slug) == clave or s.registrados.get(slug) == clave)
        if accion == "registrar":
            if valida:
                return self.responder(200, {"ok": True, "registrado": False, "nombre": slug})
            if slug in s.claves or slug in s.registrados or slug in ("admin", "api", "scopepower", "_comun"):
                return self.responder(409, {"ok": False, "error": "nombre en uso o reservado"})
            if len(clave) < 16:
                return self.responder(400, {"ok": False, "error": "clave corta"})
            with s.lock:
                s.registrados[slug] = clave
            os.makedirs(os.path.join(s.raiz, slug), exist_ok=True)
            return self.responder(200, {"ok": True, "registrado": True, "nombre": slug, "cuota_mb": 2})
        if not valida:
            return self.responder(401, {"ok": False, "error": "clave"})
        if accion == "probar":
            return self.responder(200, {"ok": True, "version": 2, "raiz_escribible": True, "max_bytes": 8 << 20, "cuota_mb": 2, "usado_mb": 0, "registro_abierto": True})
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
                    r = os.path.join(c, f)
                    lista[os.path.relpath(r, s.raiz).replace(os.sep, "/")] = {"bytes": os.path.getsize(r), "sha256": sha(r)}
            return self.responder(200, {"ok": True, "ficheros": lista})
        ruta = ruta_segura(slug, campos.get("ruta", ""))
        if ruta is None:
            return self.responder(400, {"ok": False, "error": "ruta"})
        abs_ = os.path.join(s.raiz, ruta.replace("/", os.sep))
        if accion == "subir":
            if fichero is None:
                return self.responder(400, {"ok": False, "error": "fichero"})
            if not ruta.startswith("_comun/") and ruta != ".htaccess":     # cuota de la galería del nombre
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
            with open(abs_, "wb") as f:
                f.write(fichero)
            with s.lock:
                s.registro.append(("subir", ruta))
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


def json_foto(nombre, **extra):
    meta = {"autor": "Enrique Jiménez", "latitud": LAT, "longitud": LON, "objeto": "M 31", "tipo": "Galaxia",
            "fecha": "29/09/2026 23:15", "n_fotos": 40, "exposicion": 20.0, "ra": "00 42 44", "dec": "+41 16 09",
            "firmada": True, "encuadre": {"zoom": 1.0, "giro": 0, "espejo": False},
            "ajustes": {"estirado": 2.5, "ruido": 1}, "nota": {"puntuacion": 3.42, "estrellas": 812, "nitidez": 0.9, "fwhm": 2.3}}
    meta.update(extra)
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
            "objetos": [{"nombre": "Mare Imbrium", "familia": "mar", "x": 800.0, "y": 600.0, "a": 200.0, "b": 100.0, "angulo": 12.5, "luz": True}]}
    p = publicar.anotaciones_publicas(luna, 1600, 1200)
    assert p["mares"] == "M50 100l5 -10 2 3M-20 4l1 1" and p["terminador"] == [[50.0, 100.0], [150.0, 200.0]], p
    assert p["disco"] == {"cx": 800.0, "cy": 600.0, "r": 500.0} and p["objetos"] == [
        {"nombre": "Mare Imbrium", "familia": "mar", "luz": True, "angulo": 12.5, "x": 400.0, "y": 300.0, "a": 100.0, "b": 50.0}], p
    assert set(p) == {"ancho", "alto", "luna", "disco", "mares", "terminador", "iluminada", "dudosa", "objetos"}, set(p)
    sol = {"ok": True, "sol": True, "ancho": 3200, "alto": 2400, "guardado": True, "origen": "guardado",
           "disco": {"cx": 1600.0, "cy": 1200.0, "r": 1000.0, "cortado": False},
           "manchas": [{"x": 800.0, "y": 600.0, "r_px": 8.0, "umbra": True, "grupo": 1, "lat": 12.0, "dlon": -3.0, "area_mhs": 40.0, "zona": 3}],
           "grupos": [{"id": 1, "n": 1, "x": 800.0, "y": 600.0, "lat": 12.0, "dlon": -3.0, "area_mhs": 40.0}],
           "wolf": {"g": 1, "f": 1, "R": 18.7, "k": 1.7}, "geometria": {"P": 1.0, "B0": 2.0, "L0": 3.0}, "zonas": [], "medida": {"ruido": 0.1}}
    p = publicar.anotaciones_publicas(sol, 1600, 1200)
    assert p["manchas"] == [{"x": 400.0, "y": 300.0, "r_px": 4.0, "umbra": True, "grupo": 1}] and p["grupos"] == [{"id": 1, "x": 400.0, "y": 300.0}], p
    assert p["disco"] == {"cx": 800.0, "cy": 600.0, "r": 500.0, "cortado": False} and p["wolf"]["R"] == 18.7, p
    assert set(p) == {"ancho", "alto", "sol", "disco", "wolf", "manchas", "grupos"}, set(p)
    assert publicar.anotaciones_publicas(dict(sol, manchas=[], grupos=[]), 1600, 1200) is None
    assert publicar.anotaciones_publicas({"ok": True, "ancho": 3200, "alto": 2400, "objetos": []}, 1600, 1200) is None
    print("✅ anotaciones de la Luna y el Sol: a la escala publicada, por lista blanca y sin nada si no hay qué pintar")


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
    assert r.get("ok") and r.get("version") == 2 and r.get("registrado") is False, r
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
    sincronizado()

    # 2. Qué ha llegado al hosting
    remoto = ficheros_de(raiz_servidor)
    assert ".htaccess" in remoto and f"{NOMBRE}/galeria.json" in remoto, sorted(remoto)
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
    assert indice["observador"] == {"latitud": 40.0, "longitud": -4.0, "redondeo_grados": 1}, indice["observador"]
    fotos = {f["id"]: f for f in indice["fotos"]}
    m31, sol = fotos["M_31_20260929_231500"], fotos["Sol_20261022_120000"]
    assert m31["tipo"] == "Galaxia" and m31["fecha_iso"] == "2026-09-29T23:15" and m31["n_fotos"] == 40 and m31["exposicion"] == 20.0
    assert m31["nota"] == {"puntuacion": 3.42, "estrellas": 812, "fwhm": 2.3} and m31["ra"] == "00 42 44", m31
    assert m31["ancho"] == 1600 and m31["alto"] == 1067 and set(m31["v"]) == {"jpg", "mini", "cielo", "anotaciones"}, m31
    assert m31["cielo"]["modo"] == "completa" and m31["cielo"]["esquinas"][0] == [11.4, 42.0] and m31["cielo"]["ancho"] == 1024, m31["cielo"]
    assert m31["cielo"]["area_deg2"] == 1.5 and "latitud" not in m31
    assert m31["video"] == {"frames": 2398, "duracion_s": 120}, m31["video"]
    assert sol["tipo"] == "Sol" and sol["cielo"] is None and "cielo" not in sol["ficheros"] and sol["video"] is None, sol
    assert sol["fecha_iso"] == "2026-10-22T12:00" and sol["n_fotos"] == 40 and sol["autor"] == "Enrique Jiménez", sol
    assert indice["fotos"][0]["id"] == "Sol_20261022_120000", "las más recientes primero"
    ficha = leer_json(remoto[f"{NOMBRE}/M_31_20260929_231500.json"])
    assert ficha["id"] == "M_31_20260929_231500" and ficha["v"] == m31["v"]
    print(f"✅ {etiqueta}: 2 fotos publicadas, derivadas 1600/480 px, JSON por lista blanca, observador redondeado, huella solo con FITS")

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
    assert [f["id"] for f in indice["fotos"]] == ["M_31_20260929_231500"]
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
    print(f"✅ {etiqueta}: lo borrado a mano en el hosting se repone al verificar")

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
                 f"{NOMBRE}/sub/.oculto.json", "_comun/x.js", "_comun/../x.js", f"{NOMBRE}/x.phtml", f"{NOMBRE}/x.svg"):
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
    print(f"✅ {etiqueta}: rutas con .., .php, .htaccess fuera de la raíz, otro nombre y sha256 falso, rechazadas")

    # 11. Un fichero de 3 MB (como el catálogo del planetario) va en trozos de 1 MB y llega entero;
    #     con el servidor de PHP, que limita cada envío a 2 MB, es la única forma de que entre
    grande = np.random.default_rng(5).integers(0, 256, 1_500_000, dtype=np.uint8).tobytes()
    cli.subir(f"{NOMBRE}/grande.json", grande, hashlib.sha256(grande).hexdigest())
    remoto = ficheros_de(raiz_servidor)
    assert f"{NOMBRE}/grande.json" in remoto and open(remoto[f"{NOMBRE}/grande.json"], "rb").read() == grande
    assert not any(".subiendo-" in r for r in remoto), "el parcial debe desaparecer"
    try:
        cli.subir(f"{NOMBRE}/grande2.json", grande, "1" * 64)
        raise AssertionError("un sha256 falso del total debía rechazarse")
    except publicar.ErrorHosting:
        pass
    remoto = ficheros_de(raiz_servidor)
    assert f"{NOMBRE}/grande2.json" not in remoto and not any(".subiendo-" in r for r in remoto), sorted(remoto)
    try:
        cli.subir(f"{NOMBRE}/grande3.json", grande, hashlib.sha256(grande).hexdigest())
        raise AssertionError("la cuota de 2 MB debía agotarse")
    except publicar.ErrorCuota:
        pass
    cli.borrar(f"{NOMBRE}/grande.json")
    print(f"✅ {etiqueta}: fichero de 1,5 MB subido en trozos de 1 MB y comprobado entero; sha256 falso rechazado sin dejar parciales; cuota de 2 MB respetada")

    # 12. Registro abierto: otro ScopePower reserva un nombre libre con su clave; el mismo nombre con otra clave, no
    otro = publicar.ClienteHosting(endpoint, "clave-de-otro-usuario-0123456789", "otro-usuario")
    r = otro.registrar()
    assert r.get("registrado") is True, r
    assert otro.registrar().get("registrado") is False, "la segunda vez el nombre ya es suyo"
    assert otro.probar().get("ok")
    otro.subir("otro-usuario/x.json", b"{}", hashlib.sha256(b"{}").hexdigest())
    assert "otro-usuario/x.json" in ficheros_de(raiz_servidor)
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


def casos_visor():
    """El visor se genera en cada idioma con un entorno Jinja propio (sin tocar el idioma de la app)."""
    import idioma
    plantillas = os.path.join(RAIZ, "plantillas")
    os.makedirs(plantillas)
    with open(os.path.join(plantillas, "index.html"), "w", encoding="utf-8") as f:
        f.write('<!doctype html><html lang="{{ lang }}"><title>{{ _("Galería") }} · {{ autor }}</title>'
                '<script src="{{ comun }}/js/i18n.js"></script><p>{{ raiz }}/galeria.json {{ nombre }} {{ url }}</p></html>')
    publicar.CARPETA_PLANTILLAS = plantillas
    salida = os.path.join(RAIZ, "visor")
    generados = publicar.visor(publicar.leer_config(), salida)
    assert set(generados) == {"index.html", "en/index.html"}, generados
    es = open(generados["index.html"], encoding="utf-8").read()
    en = open(generados["en/index.html"], encoding="utf-8").read()
    assert 'lang="es-ES"' in es and "Galería" in es and "../_comun/" in es and "./galeria.json" in es, es
    assert 'lang="en-US"' in en and "Gallery" in en and "../../_comun/" in en and "../galeria.json" in en, en
    assert idioma.idioma_actual() == "es", "el visor no debe cambiar el idioma de la app"
    # El visor de verdad (templates/publico): marcadores rellenos, rutas relativas, sin Jinja ni rutas de la app
    publicar.CARPETA_PLANTILLAS = os.path.join("templates", "publico")
    publicar.CARPETA_ESTATICOS = os.path.join("static", "publico")
    salida = os.path.join(RAIZ, "visor_real")
    generados = publicar.visor(publicar.leer_config(), salida)
    assert {"index.html", "en/index.html", "planetario.html", "en/planetario.html"} <= set(generados), generados
    pl = open(generados["planetario.html"], encoding="utf-8").read()
    assert "Cielo de Enrique Jiménez" in pl and "atlas/src/index.js" not in pl and "/atlas/dso-catalog.js" in pl and "url_for" not in pl, pl[:400]
    es = open(generados["index.html"], encoding="utf-8").read()
    en = open(generados["en/index.html"], encoding="utf-8").read()
    assert "Galería de Enrique Jiménez" in es and "Gallery of Enrique Jiménez" in en, (es[:300], en[:300])
    for html in (es, en):
        for prohibido in ("{{", "{%", "url_for", "/static/", "{autor}"):
            assert prohibido not in html, prohibido
    assert '"raiz": "."' in es and '"raiz": ".."' in en and "../_comun/" in es and "../../_comun/" in en
    comun = publicar.archivos_comun()
    assert {"js/i18n.js", "i18n/en.js", "publico/icono.png", "publico/publico.css", "publico/galeria_publica.js",
            "js/luna_anotada.js", "js/manchas_solares.js"} <= set(comun), sorted(comun)
    assert {"js/atlas_catalogos.js", "js/capa_fotos.js", "atlas/src/index.js", "atlas/dso-catalog.js", "atlas/assets/landscapes/guereins/properties",
            "publico/planetario_publico.js"} <= set(comun), sorted(r for r in comun if not r.startswith("atlas/assets"))
    assert not any(r.endswith(".ts") or "/licenses/" in r for r in comun), "solo lo que el endpoint admite"
    assert all(os.path.isfile(local) for local in comun.values())
    publicar.CARPETA_PLANTILLAS = os.path.join(RAIZ, "sin_plantillas")
    publicar.CARPETA_ESTATICOS = os.path.join(RAIZ, "sin_estaticos")
    print("✅ visor en español y en inglés con el entorno Jinja propio; idioma de la app intacto; el visor real renderiza limpio")


def servidor_php(carpeta):
    """php -S con hosting/publicar.php en <carpeta>/scopepower y la galería en <carpeta>/galeria."""
    php = shutil.which("php")
    if not php:
        return None, None
    sp = os.path.join(carpeta, "scopepower")
    os.makedirs(sp)
    shutil.copy(os.path.join("hosting", "publicar.php"), sp)
    with open(os.path.join(sp, "publicar_config.php"), "w", encoding="utf-8") as f:
        f.write("<?php\nreturn ['registro_abierto' => true, 'claves' => ['%s' => '%s'], 'raiz' => __DIR__ . '/../galeria', 'max_bytes' => 8 * 1024 * 1024, 'cuota_nombre_mb' => 2, 'cuota_total_mb' => 50, 'max_nombres' => 5, 'registros_por_dia' => 5];\n" % (NOMBRE, CLAVE))
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
    foto_sintetica("Sol_20261022_120000.jpg", lado=1200, semilla=2)        # antigua: sin JSON, con FITS sin WCS (sin huella)
    fits_sintetico("Sol_20261022_120000.jpg")
    foto_sintetica("Otra_20261022_140000.jpg", lado=900, semilla=3)
    json_foto("Otra_20261022_140000.jpg", objeto="Otra", tipo="")

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
