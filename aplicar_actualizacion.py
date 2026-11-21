# aplicar_actualizacion.py
#
# Actualizar ScopePower con el ZIP de la última release de GitHub, sin Git: sirve igual para
# una carpeta copiada a mano, descomprimida de un ZIP, traída en un pendrive o clonada.
#
# Este fichero solo usa la biblioteca estándar y no importa nada de ScopePower, porque la
# sustitución de ficheros se hace con la app parada y desde una COPIA de este script fuera de la
# carpeta (actualizar_desde_github.bat la deja en %TEMP%). Lo usan:
#
#   - actualizar.py (botón «Actualizar» de Configuración): consultar_release() y preparar()
#     con la app en marcha; si la descarga falla, la app ni se cierra.
#   - actualizar_desde_github.bat: «lanzador» (lo pedido desde la web, sin preguntas) e
#     «interactivo» (doble clic en el mini PC).
#
# Qué hace una actualización:
#   1. consultar_release(): /repos/<repo>/releases/latest de la API de GitHub.
#   2. preparar(): descarga el ZIP (un adjunto .zip de la release si lo hay; si no, el ZIP
#      automático, que trae una carpeta raíz «dueño-repo-sha»), lo descomprime en una carpeta de
#      trabajo fuera de la instalación y comprueba que es un ScopePower entero.
#   3. aplicar(): instala las dependencias si cambia requirements.txt, guarda en
#      _backup\<versión anterior>\ lo que va a sobrescribir o borrar (solo se conserva la última
#      copia), copia los ficheros que cambian y borra los del programa que ya no existen.
#      Si algo falla, restaura la copia: la versión anterior queda como estaba.
#   4. volver(): deshace la última actualización con esa copia.
#
# Lo que es propio de cada PC no se toca nunca (EXCLUIDOS_*): ni se copia encima, ni se borra,
# ni se guarda en la copia. Y solo se borra un fichero si estaba en la lista de ficheros de la
# versión anterior (datos\ficheros_instalados.json, o la lista de esa versión pedida a GitHub):
# lo que el usuario o la app hayan dejado en las carpetas del programa se queda.
#
# El resultado se escribe con una línea «[RESULTADO] ok|error <clave> <versión>» que
# actualizar.py lee de logs\actualizacion.log para enseñarlo en la web.
#
# Para pruebas (herramientas/probar_actualizar.py): SCOPEPOWER_API_GITHUB y
# SCOPEPOWER_RAW_GITHUB (este último lo usa el .bat) sustituyen las direcciones de GitHub,
# SCOPEPOWER_ACTUALIZACION la carpeta de trabajo y SCOPEPOWER_ACT_FALLAR_TRAS=N corta la copia
# después de N ficheros.

import argparse
import filecmp
import fnmatch
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib

REPOSITORIO = "EnriqueJ67/ScopePower"
API = (os.environ.get("SCOPEPOWER_API_GITHUB") or "https://api.github.com").rstrip("/")
AGENTE = "ScopePower-actualizador"

TIMEOUT_CONSULTA_S = 10          # la consulta responde en 1-2 s con Internet
TIMEOUT_DESCARGA_S = 30          # por cada lectura de la red, no para la descarga entera
TIMEOUT_PIP_S = 1800
MAX_ZIP_MB = 300
MAX_DESCOMPRIMIDO_MB = 1000
ESPACIO_MINIMO_MB = 150

CARPETA_COPIAS = "_backup"
EN_CURSO = "_en_curso"
FICHERO_LISTA = os.path.join("datos", "ficheros_instalados.json")
FICHERO_PENDIENTE = os.path.join("datos", "actualizar_pendiente.txt")
LISTA_ANTERIOR = "ficheros_anteriores.json"

# Lo que tiene que traer una release para darla por buena antes de tocar la instalación
FICHEROS_CLAVE = ("ScopePower.py", "version.py", "requirements.txt", "lanzar_scopepower.bat",
                  "config.example.ini", "templates/base.html")

# ── Lo que la actualización no toca nunca ─────────────────────────────────
# Rutas relativas a la carpeta de ScopePower, con «/» y en minúsculas. Es lo mismo que
# .gitignore deja fuera del repositorio y que copiar_a_campo.bat respeta.
EXCLUIDOS_CARPETAS = (
    ".git", ".claude", ".vs", CARPETA_COPIAS,
    "capturas", "calibraciones", "logs", "datos", "videos", "comparaciones", "sitio_web",
    "static/fotos", "static/mosaico", "static/seguimiento", "static/timelapse",
    "static/continuo", "static/evolucion", "traducciones/parciales",
)
EXCLUIDOS_FICHEROS = (
    "config.ini", "secretos.ini", "presets_procesado.json", "copiar_a_campo.destino.txt",
    "objetos_filtrados.csv", "objetos_descartados.csv", "hosting/publicar_config.php",
    "static/scopepower_objetos.jpg", "static/apilado_info.txt", "static/info_objeto_goto.txt",
    "static/centrado_preview.jpg",
)
EXCLUIDOS_PATRONES = (
    "config.ini.roto-*", ".config.ini.*.tmp", ".secretos.ini.*.tmp", "*.fits", "*.wcs",
    "static/captura*", "static/resultado_*", "static/ultima_captura*", "*- copia*",
)


class ErrorActualizacion(Exception):
    """Fallo con un motivo corto (sin_red, limite, sin_release, descarga, zip_corrupto,
    contenido, dependencias, copia, sin_restaurar, desarrollo, error) y un detalle para el log."""
    def __init__(self, motivo, detalle=""):
        super().__init__(detalle or motivo)
        self.motivo = motivo
        self.detalle = detalle or motivo


def _decir(texto=""):
    print(texto, flush=True)


# ── Rutas y exclusiones ───────────────────────────────────────────────────
def _normalizar(rel):
    return rel.replace("\\", "/").strip("/")


def esta_excluido(rel):
    """True si esa ruta (relativa a la carpeta de ScopePower) es propia del PC y no se toca."""
    r = _normalizar(rel).lower()
    partes = r.split("/")
    if "__pycache__" in partes or r.endswith((".pyc", ".pyo")):
        return True
    if any(r == c or r.startswith(c + "/") for c in EXCLUIDOS_CARPETAS):
        return True
    if r in EXCLUIDOS_FICHEROS:
        return True
    return any(fnmatch.fnmatchcase(r, p) for p in EXCLUIDOS_PATRONES)


def _ruta_segura(rel):
    """Una ruta relativa normal: sin unidad, sin «..» y sin empezar en la raíz del disco."""
    r = rel.replace("\\", "/")
    if not r or r.startswith("/") or ":" in r:
        return False
    return all(p not in ("", ".", "..") for p in r.split("/"))


def _destino(raiz, rel):
    """Ruta absoluta dentro de la instalación; se niega con lo excluido o lo que se sale."""
    if not _ruta_segura(rel) or esta_excluido(rel):
        raise ErrorActualizacion("error", f"ruta protegida o no admitida: {rel}")
    return os.path.join(raiz, *_normalizar(rel).split("/"))


def listar_programa(carpeta):
    """Ficheros de una versión descomprimida (rutas relativas con «/»), sin lo excluido."""
    ficheros = []
    for actual, carpetas, nombres in os.walk(carpeta):
        rel_carpeta = os.path.relpath(actual, carpeta).replace("\\", "/")
        rel_carpeta = "" if rel_carpeta == "." else rel_carpeta + "/"
        carpetas[:] = [c for c in carpetas if not esta_excluido(rel_carpeta + c)]
        for n in nombres:
            if not esta_excluido(rel_carpeta + n):
                ficheros.append(rel_carpeta + n)
    return sorted(ficheros)


# ── Versión instalada y carpeta de desarrollo ─────────────────────────────
def leer_version(carpeta):
    """VERSION de version.py sin importarlo (None si no está)."""
    try:
        with open(os.path.join(carpeta, "version.py"), encoding="utf-8", errors="replace") as f:
            m = re.search(r"^VERSION\s*=\s*[\"']([^\"']+)[\"']", f.read(), re.MULTILINE)
    except OSError:
        return None
    return m.group(1).strip() if m else None


def clave_version(texto):
    """v1.10.2 > v1.9.0: ordena por números, no por texto."""
    return tuple(int(x) for x in re.findall(r"\d+", texto or ""))


def rama_git(raiz):
    """Rama activa si la carpeta es un clon de git, leída de .git\\HEAD (no hace falta Git)."""
    punto_git = os.path.join(raiz, ".git")
    try:
        if os.path.isfile(punto_git):                      # árbol de trabajo secundario
            with open(punto_git, encoding="utf-8", errors="replace") as f:
                m = re.match(r"gitdir:\s*(.+)", f.read().strip())
            if not m:
                return None
            punto_git = os.path.normpath(os.path.join(raiz, m.group(1).strip()))
        with open(os.path.join(punto_git, "HEAD"), encoding="utf-8", errors="replace") as f:
            cabeza = f.read().strip()
    except OSError:
        return None
    m = re.match(r"ref:\s*refs/heads/(.+)", cabeza)
    return m.group(1) if m else None


def carpeta_desarrollo(raiz):
    """Nombre de la rama si la carpeta es un clon con una rama que no es master (None si no).
    Ahí hay trabajo a medias: no se le echa encima una release."""
    rama = rama_git(raiz)
    return rama if rama and rama != "master" else None


def carpeta_trabajo(raiz):
    """Carpeta fuera de la instalación para descargar y descomprimir (una por instalación)."""
    base = os.environ.get("SCOPEPOWER_ACTUALIZACION")
    if not base:
        local = os.environ.get("LOCALAPPDATA")
        base = os.path.join(local, "ScopePower", "actualizacion") if local \
            else os.path.join(tempfile.gettempdir(), "scopepower_actualizacion")
    huella = hashlib.sha1(os.path.normcase(os.path.abspath(raiz)).encode("utf-8")).hexdigest()[:8]
    return os.path.join(base, huella)


def limpiar_trabajo(raiz):
    """Borra la carpeta de trabajo de esta instalación (y la que la contiene, si queda vacía)."""
    trabajo = carpeta_trabajo(raiz)
    shutil.rmtree(trabajo, ignore_errors=True)
    try:
        os.rmdir(os.path.dirname(trabajo))
    except OSError:
        pass


# ── GitHub ────────────────────────────────────────────────────────────────
def _error_de_red(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (403, 429):
            return ErrorActualizacion("limite", f"GitHub respondió {e.code}")
        if e.code == 404:
            return ErrorActualizacion("sin_release", "GitHub respondió 404")
        return ErrorActualizacion("error", f"GitHub respondió {e.code}")
    return ErrorActualizacion("sin_red", str(getattr(e, "reason", e)))


def _pedir_json(url):
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE,
                                                    "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(peticion, timeout=TIMEOUT_CONSULTA_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, http.client.HTTPException) as e:      # URLError, tiempo agotado, corte...
        raise _error_de_red(e) from None
    except ValueError as e:
        raise ErrorActualizacion("error", f"respuesta de GitHub ilegible: {e}") from None


def consultar_release():
    """
    Última release publicada: {'etiqueta', 'version', 'fecha', 'notas', 'url_zip', 'adjunto',
    'tamano', 'sha256'}. Lanza ErrorActualizacion (sin_red, limite, sin_release, error).
    """
    d = _pedir_json(f"{API}/repos/{REPOSITORIO}/releases/latest")
    etiqueta = (d.get("tag_name") or "").strip()
    if not etiqueta or not clave_version(etiqueta):
        raise ErrorActualizacion("sin_release", f"release sin número de versión: {etiqueta!r}")
    release = {"etiqueta": etiqueta, "version": etiqueta.lstrip("vV"),
               "fecha": (d.get("published_at") or "")[:10] or None, "notas": d.get("body") or "",
               "url_zip": d.get("zipball_url"), "adjunto": None, "tamano": None, "sha256": None}
    # Un adjunto .zip de la release tiene preferencia sobre el ZIP automático
    for a in d.get("assets") or []:
        if str(a.get("name", "")).lower().endswith(".zip") and a.get("browser_download_url"):
            release.update(url_zip=a["browser_download_url"], adjunto=a["name"], tamano=a.get("size"))
            huella = str(a.get("digest") or "")
            if huella.startswith("sha256:"):
                release["sha256"] = huella.split(":", 1)[1].lower()
            break
    if not release["url_zip"]:
        raise ErrorActualizacion("sin_release", "la release no tiene ZIP para descargar")
    return release


def lista_de_version(version):
    """Ficheros de la release v<version> según GitHub (None si la lista viene incompleta)."""
    d = _pedir_json(f"{API}/repos/{REPOSITORIO}/git/trees/v{version}?recursive=1")
    if d.get("truncated"):
        return None
    return sorted(e["path"] for e in d.get("tree") or [] if e.get("type") == "blob" and e.get("path"))


def descargar(url, destino, progreso=None, sha256=None):
    """Baja el ZIP a destino. progreso(bytes, total o None) se llama según llega."""
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    resumen = hashlib.sha256()
    recibido = 0
    try:
        with urllib.request.urlopen(peticion, timeout=TIMEOUT_DESCARGA_S) as r, open(destino, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0) or None
            while True:
                trozo = r.read(256 * 1024)
                if not trozo:
                    break
                f.write(trozo)
                resumen.update(trozo)
                recibido += len(trozo)
                if recibido > MAX_ZIP_MB * 1024 * 1024:
                    raise ErrorActualizacion("descarga", f"el ZIP pasa de {MAX_ZIP_MB} MB")
                if progreso:
                    progreso(recibido, total)
    except urllib.error.HTTPError as e:
        raise _error_de_red(e) from None
    except (OSError, http.client.HTTPException) as e:      # URLError, tiempo agotado, corte...
        # Si ya había empezado a llegar, es un corte; si no, es que no hay red
        raise ErrorActualizacion("descarga" if recibido else "sin_red",
                                 f"la descarga se cortó a los {recibido} bytes: {e}") from None
    if total and recibido != total:
        raise ErrorActualizacion("descarga", f"llegaron {recibido} bytes de {total}")
    if sha256 and resumen.hexdigest() != sha256:
        raise ErrorActualizacion("zip_corrupto", "la huella SHA-256 no coincide con la de GitHub")
    return recibido


# ── ZIP ───────────────────────────────────────────────────────────────────
def extraer(ruta_zip, destino):
    """
    Descomprime el ZIP en destino. Si todo cuelga de una única carpeta (el ZIP automático de
    GitHub: «dueño-repo-sha/»), entra en ella. Devuelve el número de ficheros.
    """
    try:
        with zipfile.ZipFile(ruta_zip) as z:
            malo = z.testzip()
            if malo:
                raise ErrorActualizacion("zip_corrupto", f"fichero dañado dentro del ZIP: {malo}")
            entradas = [i for i in z.infolist() if not i.is_dir()]
            if not entradas:
                raise ErrorActualizacion("zip_corrupto", "el ZIP está vacío")
            if sum(i.file_size for i in entradas) > MAX_DESCOMPRIMIDO_MB * 1024 * 1024:
                raise ErrorActualizacion("zip_corrupto", f"el ZIP descomprimido pasa de {MAX_DESCOMPRIMIDO_MB} MB")
            nombres = [i.filename.replace("\\", "/") for i in entradas]
            primeras = {n.split("/", 1)[0] for n in nombres}
            prefijo = primeras.pop() + "/" if len(primeras) == 1 and all("/" in n for n in nombres) else ""
            for entrada, nombre in zip(entradas, nombres):
                rel = nombre[len(prefijo):]
                if not _ruta_segura(rel):
                    raise ErrorActualizacion("zip_corrupto", f"ruta no admitida en el ZIP: {nombre}")
                ruta = os.path.join(destino, *rel.split("/"))
                os.makedirs(os.path.dirname(ruta), exist_ok=True)
                with z.open(entrada) as origen, open(ruta, "wb") as salida:
                    shutil.copyfileobj(origen, salida)
            return len(entradas)
    except (zipfile.BadZipFile, zlib.error, EOFError) as e:
        raise ErrorActualizacion("zip_corrupto", f"el ZIP no se puede leer: {e}") from None
    except OSError as e:
        raise ErrorActualizacion("error", f"no se pudo descomprimir: {e}") from None


def validar(carpeta, version_esperada=None):
    """
    Comprueba que la carpeta descomprimida es un ScopePower entero: ficheros clave, número de
    versión igual al de la release y todos los .py sin errores de sintaxis. Devuelve la versión.
    """
    faltan = [f for f in FICHEROS_CLAVE if not os.path.isfile(os.path.join(carpeta, *f.split("/")))]
    if not os.path.isdir(os.path.join(carpeta, "static")):
        faltan.append("static/")
    if faltan:
        raise ErrorActualizacion("contenido", "faltan en la descarga: " + ", ".join(faltan))
    version = leer_version(carpeta)
    if not version:
        raise ErrorActualizacion("contenido", "version.py no dice la versión")
    if version_esperada and clave_version(version) != clave_version(version_esperada):
        raise ErrorActualizacion("contenido", f"version.py dice {version} y la release es la {version_esperada}")
    for rel in listar_programa(carpeta):
        if not rel.endswith(".py"):
            continue
        ruta = os.path.join(carpeta, *rel.split("/"))
        try:
            with open(ruta, "rb") as f:
                compile(f.read(), rel, "exec")
        except (SyntaxError, ValueError) as e:
            raise ErrorActualizacion("contenido", f"{rel} no es Python válido: {e}") from None
    return version


def _espacio_libre(carpeta):
    try:
        libre_mb = shutil.disk_usage(carpeta).free / (1024 * 1024)
    except OSError:
        return
    if libre_mb < ESPACIO_MINIMO_MB:
        raise ErrorActualizacion("error", f"quedan {libre_mb:.0f} MB libres en el disco; "
                                          f"hacen falta al menos {ESPACIO_MINIMO_MB}")


def preparar(raiz, release=None, zip_local=None, progreso=None, fase=None):
    """
    Deja una versión nueva descomprimida y comprobada en la carpeta de trabajo, sin tocar la
    instalación. release es lo que devuelve consultar_release(); zip_local, un ZIP ya bajado.
    Devuelve la carpeta. fase(nombre) avisa de «descargando» y «comprobando».
    """
    trabajo = carpeta_trabajo(raiz)
    shutil.rmtree(trabajo, ignore_errors=True)
    os.makedirs(trabajo, exist_ok=True)
    _espacio_libre(trabajo)
    _espacio_libre(raiz)
    if zip_local:
        ruta_zip = zip_local
    else:
        if fase:
            fase("descargando")
        ruta_zip = os.path.join(trabajo, "descarga.zip")
        descargar(release["url_zip"], ruta_zip, progreso, release.get("sha256"))
    if fase:
        fase("comprobando")
    nueva = os.path.join(trabajo, "nueva")
    extraer(ruta_zip, nueva)
    validar(nueva, release["version"] if release else None)
    if not zip_local:
        os.remove(ruta_zip)
    # Para saber qué ficheros del programa sobran después: si esta instalación aún no tiene su
    # lista (viene de antes de este actualizador), se pide a GitHub la de su versión.
    instalada = leer_version(raiz)
    if instalada and lista_instalada(raiz, instalada) is None:
        try:
            lista = lista_de_version(instalada)
        except ErrorActualizacion:
            lista = None                                  # sin lista no se borra nada: no es grave
        if lista:
            _escribir_json(os.path.join(trabajo, LISTA_ANTERIOR), {"version": instalada, "ficheros": lista})
    return nueva


# ── Lista de ficheros instalados ──────────────────────────────────────────
def _leer_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _escribir_json(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta + ".tmp", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(ruta + ".tmp", ruta)


def _lista_de(ruta, version):
    d = _leer_json(ruta)
    if not isinstance(d, dict) or d.get("version") != version or not isinstance(d.get("ficheros"), list):
        return None
    return [f for f in d["ficheros"] if isinstance(f, str)]


def lista_instalada(raiz, version):
    """Ficheros del programa que dejó la última actualización, si es de esa versión."""
    return _lista_de(os.path.join(raiz, FICHERO_LISTA), version)


# ── Copiar, guardar y restaurar ───────────────────────────────────────────
def _es_bat(rel):
    return rel.lower().endswith((".bat", ".cmd"))


def _bytes_bat(ruta):
    """Un .bat con finales de línea de Windows: el ZIP de GitHub los trae de Unix y cmd se
    pierde con las etiquetas (goto, call) de un .bat así."""
    with open(ruta, "rb") as f:
        return f.read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def _iguales(origen, destino, rel):
    if not os.path.isfile(destino):
        return False
    try:
        if _es_bat(rel):
            with open(destino, "rb") as f:
                return f.read() == _bytes_bat(origen)
        return filecmp.cmp(origen, destino, shallow=False)
    except OSError:
        return False


def _con_reintentos(accion, intentos=5, espera_s=1.0):
    """Windows niega un momento los ficheros que otro proceso acaba de soltar (antivirus,
    OneDrive, la app cerrándose): se insiste antes de darlo por fallo."""
    for intento in range(intentos):
        try:
            return accion()
        except PermissionError:
            if intento == intentos - 1:
                raise
            time.sleep(espera_s)


def _copiar(origen, destino, rel=""):
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    if rel and _es_bat(rel):
        datos = _bytes_bat(origen)

        def escribir():
            with open(destino, "wb") as f:
                f.write(datos)
        _con_reintentos(escribir)
    else:
        _con_reintentos(lambda: shutil.copyfile(origen, destino))


def _quitar(raiz, rel):
    """Borra un fichero del programa y las carpetas que se queden vacías por encima."""
    ruta = _destino(raiz, rel)
    if os.path.isfile(ruta):
        _con_reintentos(lambda: os.remove(ruta))
    carpeta = os.path.dirname(ruta)
    while os.path.normcase(carpeta) != os.path.normcase(raiz) and len(carpeta) > len(raiz):
        try:
            os.rmdir(carpeta)                              # solo si está vacía
        except OSError:
            break
        carpeta = os.path.dirname(carpeta)


def _restaurar(raiz, carpeta_copia, decir=_decir):
    """Deja la instalación como dice una copia: devuelve lo guardado y quita lo añadido."""
    info = _leer_json(os.path.join(carpeta_copia, "copia.json")) or {}
    guardados = os.path.join(carpeta_copia, "ficheros")
    fallos = []
    for rel in info.get("anadidos", []):
        try:
            _quitar(raiz, rel)
        except (OSError, ErrorActualizacion) as e:
            fallos.append(f"{rel}: {e}")
    for rel in info.get("modificados", []) + info.get("borrados", []):
        try:
            _copiar(os.path.join(guardados, *rel.split("/")), _destino(raiz, rel))
        except (OSError, ErrorActualizacion) as e:
            fallos.append(f"{rel}: {e}")
    if fallos:
        for f in fallos[:20]:
            decir(f" [ERROR] no se pudo restaurar {f}")
        raise ErrorActualizacion("sin_restaurar", f"{len(fallos)} ficheros sin restaurar; la copia sigue en {carpeta_copia}")


def recuperar(raiz, decir=_decir):
    """
    Si una actualización se quedó a medias (corte de luz, proceso cerrado), deja la versión
    anterior como estaba con la copia en curso. Devuelve la versión recuperada o None.
    """
    en_curso = os.path.join(raiz, CARPETA_COPIAS, EN_CURSO)
    if not os.path.isdir(en_curso):
        return None
    info = _leer_json(os.path.join(en_curso, "copia.json"))
    if info is None:
        # La copia no llegó a completarse: todavía no se había cambiado ningún fichero
        shutil.rmtree(en_curso, ignore_errors=True)
        return None
    decir(" Una actualización anterior se quedó a medias: se recupera la versión que había.")
    _restaurar(raiz, en_curso, decir)
    shutil.rmtree(en_curso, ignore_errors=True)
    return info.get("version") or "anterior"


def interrumpida(raiz):
    return os.path.isfile(os.path.join(raiz, CARPETA_COPIAS, EN_CURSO, "copia.json"))


def copia_anterior(raiz):
    """La copia de la última actualización: {'version', 'version_nueva', 'fecha', 'carpeta'} o None."""
    base = os.path.join(raiz, CARPETA_COPIAS)
    try:
        nombres = sorted(n for n in os.listdir(base) if n != EN_CURSO)
    except OSError:
        return None
    mejor = None
    for n in nombres:
        info = _leer_json(os.path.join(base, n, "copia.json"))
        if isinstance(info, dict) and info.get("version"):
            info["carpeta"] = os.path.join(base, n)
            if mejor is None or str(info.get("fecha", "")) > str(mejor.get("fecha", "")):
                mejor = info
    return mejor


def _mismo_texto(a, b):
    """Dos ficheros de texto iguales sin contar los finales de línea."""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read().replace(b"\r\n", b"\n").strip() == fb.read().replace(b"\r\n", b"\n").strip()
    except OSError:
        return False


def instalar_dependencias(requisitos, decir=_decir):
    """pip install -r con el mismo Python que ejecuta este script (el entorno de ScopePower)."""
    orden = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", requisitos]
    try:
        r = subprocess.run(orden, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=TIMEOUT_PIP_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        decir(f" [ERROR] pip no ha terminado: {e}")
        return False
    if r.returncode != 0:
        for linea in (r.stderr or r.stdout or "").strip().splitlines()[-8:]:
            decir("   " + linea)
        return False
    # pywin32 necesita un paso extra para registrar sus DLL (COM/ASCOM), como en el instalador
    post = os.path.join(os.path.dirname(sys.executable), "pywin32_postinstall.py")
    if os.path.isfile(post):
        try:
            subprocess.run([sys.executable, post, "-install"], capture_output=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return True


def _nombre_copia(version):
    return re.sub(r"[^0-9A-Za-z._-]", "_", version or "anterior") or "anterior"


def _cambiar(raiz, copias, borrados, info, instalar, decir, tal_cual=False):
    """
    El cambio de ficheros con red de seguridad. copias: {ruta relativa: fichero de origen};
    borrados: rutas del programa que sobran. Si falla, la instalación queda como estaba y se
    lanza ErrorActualizacion. Si va bien, la copia de lo que había queda en _backup\\<versión>\\
    y es la única que se conserva. tal_cual: sin arreglar los finales de línea de los .bat
    (al volver a la anterior se devuelve cada fichero idéntico a como estaba).
    """
    base = os.path.join(raiz, CARPETA_COPIAS)
    en_curso = os.path.join(base, EN_CURSO)
    guardados = os.path.join(en_curso, "ficheros")
    shutil.rmtree(en_curso, ignore_errors=True)

    # 1) Dependencias antes de tocar nada: si pip falla, no hay nada que deshacer
    requisitos_antes = os.path.join(raiz, "requirements.txt")
    cambia_pip = "requirements.txt" in copias and not _mismo_texto(copias["requirements.txt"], requisitos_antes)
    if cambia_pip:
        decir(" requirements.txt cambia: instalando dependencias (pip), puede tardar unos minutos...")
        if not instalar(copias["requirements.txt"]):
            raise ErrorActualizacion("dependencias", "pip no pudo instalar las dependencias (¿sin Internet?)")
        decir(" [OK] Dependencias instaladas.")

    try:
        # 2) Copia de seguridad de lo que se va a sobrescribir o borrar
        modificados = sorted(r for r in copias if os.path.isfile(_destino(raiz, r)))
        anadidos = sorted(r for r in copias if r not in modificados)
        for rel in modificados + list(borrados):
            _copiar(_destino(raiz, rel), os.path.join(guardados, *rel.split("/")))
        # copia.json se escribe al final: su presencia dice que la copia está completa
        _escribir_json(os.path.join(en_curso, "copia.json"),
                       dict(info, fecha=time.strftime("%Y-%m-%d %H:%M:%S"), modificados=modificados,
                            borrados=sorted(borrados), anadidos=anadidos, dependencias=cambia_pip))
    except (OSError, ErrorActualizacion) as e:
        shutil.rmtree(en_curso, ignore_errors=True)
        raise ErrorActualizacion("copia", f"no se pudo hacer la copia de seguridad: {e}") from None

    # 3) Ficheros nuevos encima y fuera los que sobran
    fallar_tras = int(os.environ.get("SCOPEPOWER_ACT_FALLAR_TRAS") or 0)      # solo para pruebas
    try:
        for n, (rel, origen) in enumerate(sorted(copias.items()), 1):
            if fallar_tras and n > fallar_tras:
                raise OSError("corte simulado a mitad de la copia")
            _copiar(origen, _destino(raiz, rel), "" if tal_cual else rel)
        for rel in borrados:
            _quitar(raiz, rel)
    except (OSError, ErrorActualizacion) as e:
        decir(f" [ERROR] La copia de ficheros ha fallado: {e}")
        decir(" Restaurando la versión anterior...")
        _restaurar(raiz, en_curso, decir)                 # si falla, la copia en curso se queda
        shutil.rmtree(en_curso, ignore_errors=True)
        if cambia_pip:
            decir(" Volviendo a las dependencias anteriores (pip)...")
            if not instalar(requisitos_antes):
                decir(" [AVISO] pip no ha podido dejar las dependencias anteriores: si la app no arranca,"
                      " ejecuta instalar_scopepower.bat.")
        raise ErrorActualizacion("copia", str(e)) from None

    # 4) La copia en curso pasa a ser la única guardada
    destino_copia = os.path.join(base, _nombre_copia(info.get("version")))
    for nombre in os.listdir(base):
        if nombre != EN_CURSO:
            shutil.rmtree(os.path.join(base, nombre), ignore_errors=True)
    try:
        _con_reintentos(lambda: os.rename(en_curso, destino_copia))
    except OSError as e:
        decir(f" [AVISO] No se pudo dar nombre a la copia de seguridad ({e}); queda en {en_curso}")
        return en_curso
    return destino_copia


def aplicar(raiz, origen, anteriores=None, instalar=instalar_dependencias, decir=_decir):
    """
    Pone en raiz la versión descomprimida en origen. anteriores: lista de ficheros de la versión
    instalada (si no se pasa, se busca la guardada o la que dejó preparar()).
    Devuelve ('actualizado' | 'sin_cambios', versión nueva).
    """
    raiz = os.path.abspath(raiz)
    nueva = validar(origen)
    recuperar(raiz, decir)
    antes = leer_version(raiz) or "desconocida"
    nuevos = listar_programa(origen)
    if anteriores is None:
        anteriores = lista_instalada(raiz, antes) \
            or _lista_de(os.path.join(os.path.dirname(os.path.abspath(origen)), LISTA_ANTERIOR), antes)

    copias = {}
    for rel in nuevos:
        fichero = os.path.join(origen, *rel.split("/"))
        if not _iguales(fichero, _destino(raiz, rel), rel):
            copias[rel] = fichero
    en_la_nueva = {r.lower() for r in nuevos}
    borrados = sorted(
        r for r in {_normalizar(a) for a in (anteriores or [])}
        if r.lower() not in en_la_nueva and _ruta_segura(r) and not esta_excluido(r)
        and os.path.isfile(os.path.join(raiz, *r.split("/"))))

    decir(f" Instalada: {antes}   Nueva: {nueva}")
    if anteriores is None:
        decir(" [AVISO] No hay lista de ficheros de la versión instalada: no se borra ninguno antiguo.")
    if not copias and not borrados:
        _escribir_json(os.path.join(raiz, FICHERO_LISTA), {"version": nueva, "ficheros": nuevos})
        decir(" [OK] Los ficheros ya son los de esa versión: no hay nada que cambiar.")
        return "sin_cambios", nueva
    decir(f" Ficheros que cambian: {len(copias)}   Ficheros antiguos que se quitan: {len(borrados)}")
    copia = _cambiar(raiz, copias, borrados, {"version": antes, "version_nueva": nueva}, instalar, decir)
    _escribir_json(os.path.join(raiz, FICHERO_LISTA), {"version": nueva, "ficheros": nuevos})
    decir(f" [OK] ScopePower actualizado a la {nueva}. Lo que había queda guardado en {copia}")
    return "actualizado", nueva


def volver(raiz, instalar=instalar_dependencias, decir=_decir):
    """Deshace la última actualización con la copia guardada. Devuelve la versión que queda."""
    raiz = os.path.abspath(raiz)
    recuperar(raiz, decir)
    copia = copia_anterior(raiz)
    if not copia:
        raise ErrorActualizacion("error", "no hay ninguna versión anterior guardada")
    guardados = os.path.join(copia["carpeta"], "ficheros")
    ahora = leer_version(raiz) or "desconocida"
    copias = {rel: os.path.join(guardados, *rel.split("/"))
              for rel in copia.get("modificados", []) + copia.get("borrados", [])}
    falta = [rel for rel, ruta in copias.items() if not os.path.isfile(ruta)]
    if falta:
        raise ErrorActualizacion("error", f"la copia guardada está incompleta (falta {falta[0]})")
    sobran = [rel for rel in copia.get("anadidos", []) if os.path.isfile(os.path.join(raiz, *rel.split("/")))]
    decir(f" Instalada: {ahora}   Se vuelve a la: {copia['version']}")
    _cambiar(raiz, copias, sobran, {"version": ahora, "version_nueva": copia["version"]}, instalar, decir,
             tal_cual=True)
    # La lista de ficheros instalados era de la versión que se deja: ya no vale
    try:
        os.remove(os.path.join(raiz, FICHERO_LISTA))
    except OSError:
        pass
    decir(f" [OK] ScopePower vuelve a ser la {copia['version']}.")
    return copia["version"]


# ── ScopePower en marcha (modo interactivo) ───────────────────────────────
def buscar_app(raiz):
    """
    Procesos de Python que ejecutan ScopePower.py: [(pid, pid del lanzador o None, seguro)].
    «seguro» = se sabe que es el de esta carpeta, porque la ruta sale en su línea de órdenes o
    en la de la ventana de lanzar_scopepower.bat que lo arrancó.
    """
    orden = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'py*' -or $_.Name -eq 'cmd.exe' } | "
             "Select-Object ProcessId, ParentProcessId, Name, CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", orden], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        procesos = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    if isinstance(procesos, dict):
        procesos = [procesos]
    por_pid = {p.get("ProcessId"): p for p in procesos}
    carpeta = os.path.normcase(os.path.abspath(raiz))
    encontrados = []
    for p in procesos:
        linea = p.get("CommandLine") or ""
        if not str(p.get("Name", "")).lower().startswith("python") or "scopepower.py" not in linea.lower():
            continue
        seguro = carpeta in os.path.normcase(linea)
        lanzador, padre = None, por_pid.get(p.get("ParentProcessId"))
        for __ in range(3):                                # py.exe puede estar en medio
            if not padre:
                break
            linea_padre = padre.get("CommandLine") or ""
            if str(padre.get("Name", "")).lower() == "cmd.exe" and "lanzar_scopepower" in linea_padre.lower():
                if carpeta in os.path.normcase(linea_padre):
                    lanzador, seguro = padre.get("ProcessId"), True
                break
            padre = por_pid.get(padre.get("ParentProcessId"))
        encontrados.append((p.get("ProcessId"), lanzador, seguro))
    return encontrados


def cerrar_app(procesos):
    for pid, lanzador, __ in procesos:
        if lanzador:                                       # que no se quede en su «pause»
            subprocess.run(["taskkill", "/PID", str(lanzador), "/F"], capture_output=True)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    time.sleep(2)


def arrancar_app(raiz):
    lanzador = os.path.join(raiz, "lanzar_scopepower.bat")
    if os.path.isfile(lanzador):
        # En una sola cadena: start necesita el título entre comillas, y subprocess no se las pone
        subprocess.Popen(f'start "ScopePower" /D "{raiz}" "{lanzador}"', shell=True, close_fds=True)


# ── Órdenes para actualizar_desde_github.bat ──────────────────────────────
def _explicar(e):
    """Un ErrorActualizacion contado para quien no sabe de ordenadores."""
    textos = {
        "sin_red": "Sin conexión a internet (o no se llega a GitHub). Conecta el PC a una red con "
                   "salida a Internet y vuelve a intentarlo.",
        "limite": "GitHub no admite más consultas desde esta conexión por ahora. Prueba dentro de una hora.",
        "sin_release": "Todavía no hay ninguna versión publicada en GitHub.",
        "descarga": "La descarga se ha cortado. Vuelve a intentarlo.",
        "zip_corrupto": "La descarga ha llegado dañada. Vuelve a intentarlo.",
        "contenido": "Lo descargado no es un ScopePower completo: no se ha tocado nada.",
        "dependencias": "No se pudieron instalar las dependencias nuevas: no se ha tocado nada.",
        "copia": "No se pudo actualizar, se ha mantenido la versión anterior.",
        "sin_restaurar": "La actualización ha fallado y no se ha podido dejar todo como estaba. "
                         "Vuelve a ejecutar actualizar_desde_github.bat.",
        "desarrollo": "Esta carpeta es de desarrollo (un clon de git en otra rama): se actualiza con git.",
    }
    return textos.get(e.motivo, "No se pudo actualizar.") + f"\n          ({e.detalle})"


def _progreso_consola():
    ultimo = [0.0]

    def progreso(recibido, total):
        ahora = time.time()
        if ahora - ultimo[0] < 1.0 and recibido != total:
            return
        ultimo[0] = ahora
        de = f" de {total / 1048576:.1f}" if total else ""
        _decir(f"   {recibido / 1048576:.1f}{de} MB")
    return progreso


def _preguntar(texto, sin_preguntar):
    if sin_preguntar:
        return True
    try:
        return input(f" {texto} [S/N] ").strip().lower() in ("s", "si", "sí", "y", "yes")
    except EOFError:
        return False


def modo_interactivo(raiz, sin_preguntar=False, deshacer=False, zip_local=None):
    """Doble clic en actualizar_desde_github.bat. Códigos de salida: 0 bien, 1 error, 2 cancelado."""
    _decir()
    _decir(" ScopePower - actualizar")
    _decir(f" Carpeta: {raiz}")
    _decir()
    try:
        os.remove(os.path.join(raiz, FICHERO_PENDIENTE))  # un pedido de la web que se quedó sin hacer
    except OSError:
        pass
    try:
        rama = carpeta_desarrollo(raiz)
        if rama:
            raise ErrorActualizacion("desarrollo", f"rama {rama}")
        if recuperar(raiz):
            _decir(" [OK] Recuperada la versión anterior.")
        instalada = leer_version(raiz)
        _decir(f" Versión instalada: {instalada or 'desconocida'}")
        release = None
        if deshacer:
            copia = copia_anterior(raiz)
            if not copia:
                raise ErrorActualizacion("error", "no hay ninguna versión anterior guardada")
            if not _preguntar(f"¿Volver a la versión {copia['version']}?", sin_preguntar):
                _decir(" No se ha cambiado nada.")
                return 2
        elif not zip_local:
            _decir(" Consultando GitHub...")
            release = consultar_release()
            _decir(f" Última versión publicada: {release['version']}"
                   + (f" ({release['fecha']})" if release["fecha"] else ""))
            if instalada and clave_version(instalada) >= clave_version(release["version"]):
                _decir()
                _decir(" [OK] Ya tienes la última versión.")
                return 0
            if not _preguntar(f"¿Actualizar ScopePower a la {release['version']}?", sin_preguntar):
                _decir(" No se ha cambiado nada.")
                return 2

        origen = None
        if not deshacer:
            _decir(" Descargando..." if release else " Comprobando el ZIP...")
            origen = preparar(raiz, release, zip_local, progreso=_progreso_consola())
            _decir(" [OK] Descarga comprobada.")

        procesos = buscar_app(raiz)
        seguros = [p for p in procesos if p[2]]
        reiniciar = False
        if seguros or (procesos and not sin_preguntar):
            _decir()
            _decir(" ScopePower está en marcha en este PC: hay que cerrarlo para actualizar.")
            if not _preguntar("¿Cerrarlo ahora y volver a arrancarlo al terminar?", sin_preguntar):
                _decir(" No se ha cambiado nada.")
                return 2
            _decir(" Cerrando ScopePower...")
            cerrar_app(seguros or procesos)
            reiniciar = True
        elif procesos:
            _decir(" [AVISO] Hay un ScopePower en marcha que puede ser el de esta carpeta: no verá la"
                   " versión nueva hasta que se reinicie.")

        _decir()
        _decir(" Actualizando...")
        try:
            if deshacer:
                volver(raiz)
            else:
                aplicar(raiz, origen)
        finally:
            limpiar_trabajo(raiz)
            if reiniciar:
                _decir(" Arrancando ScopePower de nuevo...")
                arrancar_app(raiz)
        return 0
    except ErrorActualizacion as e:
        _decir()
        _decir(" [ERROR] " + _explicar(e))
        return 1


def modo_lanzador(raiz):
    """
    Lo pedido desde la web (datos\\actualizar_pendiente.txt), con la app ya cerrada y sin
    preguntas. Todo lo que escribe va a logs\\actualizacion.log; la última línea es el resultado.
    """
    _decir(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Actualización pedida desde la web")
    ruta_pendiente = os.path.join(raiz, FICHERO_PENDIENTE)
    pedido = _leer_json(ruta_pendiente) or {}
    try:
        os.remove(ruta_pendiente)
    except OSError:
        pass
    destino = pedido.get("destino")
    _decir(f" Carpeta: {raiz}")
    _decir(f" Pedido: {destino or 'nada'}")
    try:
        if destino == "release":
            origen = pedido.get("origen") or ""
            if not os.path.isdir(origen):
                raise ErrorActualizacion("error", "no está la versión descargada")
            resultado, version = aplicar(raiz, origen)
            _decir(f"[RESULTADO] ok {resultado} {version}")
        elif destino == "volver":
            _decir(f"[RESULTADO] ok vuelta {volver(raiz)}")
        else:                                              # «reparar» o un pedido que se perdió
            version = recuperar(raiz)
            _decir(f"[RESULTADO] ok reparada {version}" if version else "[RESULTADO] ok sin_cambios")
        codigo = 0
    except ErrorActualizacion as e:
        _decir(" [ERROR] " + _explicar(e))
        _decir("[RESULTADO] error " + ("sin_restaurar" if e.motivo == "sin_restaurar" else "mantenida"))
        codigo = 1
    except Exception as e:                                 # que la web tenga siempre un resultado
        _decir(f" [ERROR] Fallo inesperado: {e!r}")
        _decir("[RESULTADO] error " + ("sin_restaurar" if interrumpida(raiz) else "mantenida"))
        codigo = 1
    limpiar_trabajo(raiz)
    return codigo


def main(argumentos=None):
    p = argparse.ArgumentParser(description="Actualizar ScopePower con el ZIP de la última release de GitHub")
    p.add_argument("modo", choices=("interactivo", "lanzador"))
    p.add_argument("--raiz", required=True, help="carpeta de ScopePower")
    p.add_argument("--si", action="store_true", help="no pedir confirmación")
    p.add_argument("--volver", action="store_true", help="deshacer la última actualización")
    p.add_argument("--zip", help="actualizar con un ZIP ya descargado (sin Internet)")
    a = p.parse_args(argumentos)
    raiz = os.path.abspath(a.raiz)
    if not os.path.isfile(os.path.join(raiz, "ScopePower.py")):
        _decir(f" [ERROR] En {raiz} no hay un ScopePower.")
        return 1
    if a.modo == "lanzador":
        return modo_lanzador(raiz)
    return modo_interactivo(raiz, a.si, a.volver, a.zip)


if __name__ == "__main__":
    sys.exit(main())
