# herramientas/probar_actualizar.py
#
# Prueba de la actualización sin Git (aplicar_actualizacion.py y actualizar.py), sin Internet ni
# tocar la instalación de verdad: un GitHub falso en 127.0.0.1 sirve la release y su ZIP, y la
# «instalación» es una carpeta temporal con unos pocos ficheros del programa y lo propio de un PC
# (config.ini, secretos.ini, calibraciones, capturas, galería, datos, logs, una carpeta .git...).
#
#   1. Exclusiones: lo propio del PC está protegido y ningún fichero del repositorio lo está.
#   2. Consulta: versión nueva, adjunto .zip con preferencia, sin Internet, sin releases, límite.
#   3. Actualización completa con el ZIP automático (carpeta raíz «dueño-repo-sha»): ficheros
#      nuevos, antiguos fuera, lo del usuario intacto, copia en _backup y dependencias.
#   4. Volver a la anterior, y otra vez adelante.
#   5. Descargas malas (ZIP dañado, cortado, incompleto, con rutas raras...): no se toca nada.
#   6. Corte a mitad de la copia y fallo de pip: queda la versión anterior, exacta.
#   7. Actualización interrumpida de golpe (corte de luz): recuperar() la deshace.
#   8. Carpeta de desarrollo (clon en otra rama): se reconoce sin Git.
#   9. El modo «lanzador» como lo ejecuta el .bat (copia del script en otro proceso).
#  10. La capa de la web: comprobar(), empezar() con su avance, y el resultado leído del log.
#
#   python herramientas\probar_actualizar.py
#
# Sale con código 0 si todo pasa y 1 si no. Lo ejecuta el workflow «Comprobar».

import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RAIZ)

import aplicar_actualizacion as act  # noqa: E402

FALLOS = []
TMP = tempfile.mkdtemp(prefix="scopepower_prueba_actualizar_")
os.environ["SCOPEPOWER_ACTUALIZACION"] = os.path.join(TMP, "trabajo")
act.TIMEOUT_CONSULTA_S = 3
act.TIMEOUT_DESCARGA_S = 3


def comprobar(condicion, texto):
    print(("  ok    " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


def falla_con(motivo, accion, texto):
    """La acción tiene que lanzar ErrorActualizacion con ese motivo."""
    try:
        accion()
    except act.ErrorActualizacion as e:
        comprobar(e.motivo == motivo, f"{texto} (motivo {e.motivo}: {e.detalle})")
        return
    comprobar(False, f"{texto}: no ha fallado")


# ── Instalaciones y releases de mentira ───────────────────────────────────
def programa(version):
    """Ficheros del programa de una versión inventada."""
    f = {
        "ScopePower.py": f"print('ScopePower {version}')\n",
        "version.py": f'# version.py\nVERSION = "{version}"\n',
        "requirements.txt": "flask\n",
        "lanzar_scopepower.bat": "@echo off\necho lanzar\ngoto :fin\n:fin\n",      # finales de Unix, como en el ZIP
        "config.example.ini": "[Red]\npuerto = 5000\n",
        "templates/base.html": f"<html>{version}</html>\n",
        "static/js/app.js": f"// {version}\n",
        "static/logo.png": b"\x89PNG\r\n\x1a\n" + bytes(range(256)),
        "igual.py": "IGUAL = 1\n",
        "info_objetos/m13_cumulo.txt": "M13, el gran cúmulo de Hércules\n",
    }
    if version == "1.0.0":
        f["viejo.py"] = "VIEJO = True\n"
        f["herramientas/antigua.py"] = "print('ya no existe en la 1.1.0')\n"
    else:
        f["nuevo.py"] = "NUEVO = True\n"
        f["static/js/nuevo.js"] = "// nuevo\n"
        f["requirements.txt"] = "flask\nsegno\n"
    return {k: (v if isinstance(v, bytes) else v.encode("utf-8")) for k, v in f.items()}


PROPIOS = {
    "config.ini": b"[Telescopio]\ndriver = EL_MIO\n",
    "secretos.ini": b"[Sesion]\nclave = secreta\n",
    "presets_procesado.json": b'{"mio": 1}',
    "calibraciones/dark_20s_g120.fits": b"DARK" * 100,
    "capturas/m31_0001.fits": b"FITS" * 100,
    "static/fotos/m31.jpg": b"\xff\xd8JPG de la galeria",
    "static/fotos/m31.json": b'{"objeto": "M 31"}',
    "static/captura.jpg": b"\xff\xd8ultima foto",
    "datos/plan.json": b'{"objetos": ["M 31"]}',
    "logs/scopepower.log": b"linea del log\n",
    "videos/sol.ser": b"SER" * 50,
    "info_objetos/m31_galaxia.txt": b"cache de Wikipedia que baja la app\n",     # no es del programa
    "mis_notas.txt": b"un fichero del usuario en la carpeta\n",
    ".git/HEAD": b"ref: refs/heads/master\n",
    ".git/config": b"[core]\n",
}


def escribir(carpeta, ficheros):
    for rel, datos in ficheros.items():
        ruta = os.path.join(carpeta, *rel.split("/"))
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "wb") as f:
            f.write(datos)


def instalacion(nombre, version="1.0.0"):
    carpeta = os.path.join(TMP, nombre)
    shutil.rmtree(carpeta, ignore_errors=True)
    escribir(carpeta, programa(version))
    escribir(carpeta, PROPIOS)
    return carpeta


def foto(carpeta, sin=(act.CARPETA_COPIAS, "datos/ficheros_instalados.json")):
    """{ruta relativa: huella} de todo lo que hay en la carpeta, para comparar antes y después."""
    huellas = {}
    for actual, __, nombres in os.walk(carpeta):
        for n in nombres:
            rel = os.path.relpath(os.path.join(actual, n), carpeta).replace("\\", "/")
            if any(rel == s or rel.startswith(s + "/") for s in sin):
                continue
            with open(os.path.join(actual, n), "rb") as f:
                huellas[rel] = hashlib.sha1(f.read()).hexdigest()
    return huellas


def solo_propios(huellas):
    return {k: v for k, v in huellas.items() if k in PROPIOS}


def hacer_zip(ficheros, raiz="dueno-ScopePower-abc1234/"):
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, datos in ficheros.items():
            z.writestr(raiz + rel, datos)
    return memoria.getvalue()


# ── GitHub falso ──────────────────────────────────────────────────────────
class GitHubFalso(http.server.BaseHTTPRequestHandler):
    estado = {"codigo": 200, "release": None, "zips": {}, "arboles": {}, "cortar": False, "crudos": {}}

    def log_message(self, *args):
        pass

    def _json(self, datos):
        cuerpo = json.dumps(datos).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        e = self.estado
        ruta = self.path.split("?")[0]
        if ruta.endswith("/releases/latest"):
            if e["codigo"] != 200:
                self.send_error(e["codigo"])
            else:
                self._json(e["release"])
        elif "/git/trees/" in ruta:
            etiqueta = ruta.split("/git/trees/")[1]
            if etiqueta in e["arboles"]:
                self._json({"truncated": False,
                            "tree": [{"path": p, "type": "blob"} for p in e["arboles"][etiqueta]]})
            else:
                self.send_error(404)
        elif ruta in e["zips"]:
            datos = e["zips"][ruta]
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            if e["cortar"]:                                # promete más de lo que manda
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos[:len(datos) // 2])
            else:                                          # como el ZIP automático: sin tamaño
                self.end_headers()
                self.wfile.write(datos)
        elif ruta in e["crudos"]:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(e["crudos"][ruta])
        else:
            self.send_error(404)


def arrancar_github():
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHubFalso)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor, f"http://127.0.0.1:{servidor.server_address[1]}"


def puerto_cerrado():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{puerto}"


def publicar(base, version, ficheros=None, zip_bytes=None, adjunto=False, sha256=None):
    """Deja en el GitHub falso la release v<version> con su ZIP."""
    e = GitHubFalso.estado
    datos = zip_bytes if zip_bytes is not None else hacer_zip(ficheros if ficheros is not None else programa(version))
    ruta = f"/descargas/{'adjunto' if adjunto else 'zipball'}/v{version}.zip"
    e["zips"] = {ruta: datos}
    e["codigo"], e["cortar"] = 200, False
    e["release"] = {"tag_name": f"v{version}", "published_at": "2026-11-25T10:00:00Z",
                    "body": f"Lo principal de la {version}.\n- Nuevo: una cosa.",
                    "zipball_url": base + ruta if not adjunto else base + "/no/usar/este.zip", "assets": []}
    if adjunto:
        e["release"]["assets"] = [
            {"name": "notas.txt", "browser_download_url": base + "/notas.txt", "size": 3},
            {"name": f"ScopePower-{version}.zip", "browser_download_url": base + ruta, "size": len(datos),
             "digest": "sha256:" + (sha256 or hashlib.sha256(datos).hexdigest())}]
    return datos


class Pip:
    """pip de mentira: apunta con qué requirements.txt lo llaman y contesta lo que se le diga."""
    def __init__(self, va_bien=True):
        self.llamadas, self.va_bien = [], va_bien

    def __call__(self, requisitos, decir=None):
        with open(requisitos, "rb") as f:
            self.llamadas.append(f.read().replace(b"\r\n", b"\n"))
        return self.va_bien


def callado(*args):
    pass


# ── Pruebas ───────────────────────────────────────────────────────────────
def prueba_exclusiones():
    print("\n1. Lo que no se toca")
    for rel in list(PROPIOS) + ["calibraciones", "static/fotos/2026/m42.jpg", "datos/mpc/cometas.txt",
                                "_backup/1.0.0/copia.json", "static/resultado_apilado_mono.fits",
                                "static/ultima_captura.jpg", "config.ini.roto-20261119", "__pycache__/x.pyc",
                                "hosting/publicar_config.php", "Capturas\\M31.fits", "static/mosaico/panel_1.jpg"]:
        if rel in ("info_objetos/m31_galaxia.txt", "mis_notas.txt"):
            continue                                       # carpetas del programa: los protege la lista de ficheros
        comprobar(act.esta_excluido(rel), f"protegido: {rel}")
    for rel in programa("1.1.0"):
        comprobar(not act.esta_excluido(rel), f"del programa: {rel}")
    for rel in ("../fuera.py", "C:/Windows/x.dll", "/etc/passwd", "a/../../b", ""):
        comprobar(not act._ruta_segura(rel), f"ruta no admitida: {rel!r}")
    # Ningún fichero del repositorio puede caer en las exclusiones: no llegaría nunca a los usuarios
    try:
        r = subprocess.run(["git", "-C", RAIZ, "ls-files"], capture_output=True, text=True, encoding="utf-8", timeout=30)
        seguidos = r.stdout.splitlines() if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        seguidos = []
    if seguidos:
        excluidos = [f for f in seguidos if act.esta_excluido(f)]
        comprobar(not excluidos, f"ninguno de los {len(seguidos)} ficheros del repositorio está excluido {excluidos[:5]}")
        comprobar(all(c in seguidos for c in act.FICHEROS_CLAVE), "los ficheros clave existen en el repositorio")
    else:
        print("  (sin git: no se comprueba el repositorio entero)")


def prueba_consulta(base):
    print("\n2. Consulta de la última release")
    publicar(base, "1.1.0")
    r = act.consultar_release()
    comprobar(r["version"] == "1.1.0" and r["etiqueta"] == "v1.1.0" and r["fecha"] == "2026-11-25",
              "versión, etiqueta y fecha de la release")
    comprobar(r["url_zip"].endswith("/zipball/v1.1.0.zip") and r["adjunto"] is None, "sin adjuntos se usa el ZIP automático")
    comprobar("Nuevo: una cosa" in r["notas"], "trae las notas de la versión")
    publicar(base, "1.1.0", adjunto=True)
    r = act.consultar_release()
    comprobar(r["adjunto"] == "ScopePower-1.1.0.zip" and "/adjunto/" in r["url_zip"] and len(r["sha256"] or "") == 64,
              "un adjunto .zip tiene preferencia y trae su huella")
    comprobar(act.clave_version("v1.10.2") > act.clave_version("1.9.0"), "1.10.2 es más nueva que 1.9.0")
    GitHubFalso.estado["codigo"] = 404
    falla_con("sin_release", act.consultar_release, "sin releases publicadas")
    GitHubFalso.estado["codigo"] = 403
    falla_con("limite", act.consultar_release, "límite de consultas de GitHub")
    GitHubFalso.estado["codigo"] = 200
    api = act.API
    act.API = puerto_cerrado()
    t0 = time.time()
    falla_con("sin_red", act.consultar_release, "sin conexión a internet")
    comprobar(time.time() - t0 < 8, "sin Internet contesta enseguida")
    act.API = api


def prueba_actualizacion_completa(base):
    print("\n3. Actualización completa de la 1.0.0 a la 1.1.0")
    raiz = instalacion("completa")
    antes = foto(raiz)
    nueva = programa("1.1.0")
    # La release trae por error cosas propias de un PC: no se instalan
    intrusos = {"config.ini": b"[Telescopio]\ndriver = OTRO\n", "static/fotos/intruso.jpg": b"x",
                "datos/plan.json": b"{}", "capturas/ajena.fits": b"x"}
    publicar(base, "1.1.0", ficheros=dict(nueva, **intrusos))
    GitHubFalso.estado["arboles"] = {"v1.0.0": list(programa("1.0.0"))}

    fases, avance = [], []
    origen = act.preparar(raiz, act.consultar_release(), progreso=lambda b, t: avance.append(b), fase=fases.append)
    comprobar(fases == ["descargando", "comprobando"] and avance and avance[-1] > 0, "fases y avance de la descarga")
    comprobar(os.path.isfile(os.path.join(origen, "ScopePower.py")), "entra en la carpeta raíz del ZIP automático")
    comprobar(not os.path.normcase(origen).startswith(os.path.normcase(raiz)), "se descomprime fuera de la instalación")
    comprobar(foto(raiz) == antes, "descargar y comprobar no toca la instalación")

    pip = Pip()
    resultado = act.aplicar(raiz, origen, instalar=pip, decir=callado)
    despues = foto(raiz)
    comprobar(resultado == ("actualizado", "1.1.0") and act.leer_version(raiz) == "1.1.0", "queda en la 1.1.0")
    bat_nuevo = nueva["lanzar_scopepower.bat"].replace(b"\n", b"\r\n")
    programa_bien = all(despues.get(rel) == hashlib.sha1(bat_nuevo if rel.endswith(".bat") else datos).hexdigest()
                        for rel, datos in nueva.items())
    comprobar(programa_bien, "todos los ficheros del programa son los de la versión nueva")
    with open(os.path.join(raiz, "lanzar_scopepower.bat"), "rb") as f:
        comprobar(b"\r\n" in f.read(), "el .bat queda con finales de línea de Windows")
    comprobar("viejo.py" not in despues and "herramientas/antigua.py" not in despues, "los ficheros antiguos del programa se quitan")
    comprobar(not os.path.isdir(os.path.join(raiz, "herramientas")), "y la carpeta que se queda vacía, también")
    comprobar(solo_propios(despues) == solo_propios(antes) and len(solo_propios(despues)) == len(PROPIOS),
              "config.ini, secretos.ini, calibraciones, capturas, galería, datos, logs y .git, intactos")
    comprobar("static/fotos/intruso.jpg" not in despues and "capturas/ajena.fits" not in despues,
              "lo propio de un PC que venga en el ZIP no se instala")
    comprobar(pip.llamadas == [b"flask\nsegno\n"], "se instalan las dependencias nuevas, una vez")

    copia = act.copia_anterior(raiz)
    comprobar(copia and copia["version"] == "1.0.0" and os.listdir(os.path.join(raiz, "_backup")) == ["1.0.0"],
              "la copia de seguridad es _backup\\1.0.0 y es la única")
    guardado = foto(os.path.join(raiz, "_backup", "1.0.0", "ficheros"), sin=())
    comprobar("viejo.py" in guardado and "ScopePower.py" in guardado and "igual.py" not in guardado
              and not any(k in guardado for k in PROPIOS), "la copia guarda lo cambiado y lo borrado, nada del usuario")
    lista = act.lista_instalada(raiz, "1.1.0")
    comprobar(lista is not None and "nuevo.py" in lista and "config.ini" not in lista, "queda la lista de ficheros instalados")

    pip2 = Pip()
    comprobar(act.aplicar(raiz, origen, instalar=pip2, decir=callado)[0] == "sin_cambios" and not pip2.llamadas,
              "repetirla no cambia nada")
    return raiz, antes, despues


def prueba_volver(raiz, antes, despues):
    print("\n4. Volver a la anterior")
    pip = Pip()
    comprobar(act.volver(raiz, instalar=pip, decir=callado) == "1.0.0", "vuelve a la 1.0.0")
    vuelta = foto(raiz)
    # El .bat de la instalación de partida tenía finales de Unix: vuelve tal cual estaba
    comprobar(vuelta == antes, "todo queda exactamente como antes de actualizar")
    comprobar(pip.llamadas == [b"flask\n"], "y las dependencias anteriores")
    comprobar(os.listdir(os.path.join(raiz, "_backup")) == ["1.1.0"], "ahora la copia guardada es la de la 1.1.0")
    comprobar(act.volver(raiz, instalar=Pip(), decir=callado) == "1.1.0" and foto(raiz) == despues,
              "y se puede deshacer la vuelta")
    falla_con("error", lambda: act.volver(instalacion("sin_copia"), instalar=Pip(), decir=callado),
              "sin copia guardada no hay a dónde volver")


def prueba_descargas_malas(base):
    print("\n5. Descargas malas: no se toca nada")
    raiz = instalacion("malas")
    antes = foto(raiz)
    bueno = hacer_zip(programa("1.1.0"))

    def intentar():
        return act.preparar(raiz, act.consultar_release())

    publicar(base, "1.1.0", zip_bytes=b"esto no es un ZIP" * 1000)
    falla_con("zip_corrupto", intentar, "ZIP que no es un ZIP")
    publicar(base, "1.1.0", zip_bytes=bueno[:len(bueno) // 2])
    falla_con("zip_corrupto", intentar, "ZIP cortado por la mitad")
    roto = bytearray(bueno)
    centro = bueno.index(b"ScopePower.py") + 60
    roto[centro:centro + 8] = b"\x00" * 8
    publicar(base, "1.1.0", zip_bytes=bytes(roto))
    falla_con("zip_corrupto", intentar, "ZIP con un fichero dañado dentro")
    publicar(base, "1.1.0", zip_bytes=bueno)
    GitHubFalso.estado["cortar"] = True
    falla_con("descarga", intentar, "descarga que se corta a mitad")
    publicar(base, "1.1.0", adjunto=True, sha256="0" * 64)
    falla_con("zip_corrupto", intentar, "adjunto cuya huella no coincide")

    sin_clave = programa("1.1.0")
    del sin_clave["templates/base.html"]
    publicar(base, "1.1.0", ficheros=sin_clave)
    falla_con("contenido", intentar, "falta un fichero clave")
    publicar(base, "1.2.0", ficheros=programa("1.1.0"))
    falla_con("contenido", intentar, "version.py no es el de la release")
    publicar(base, "1.1.0", ficheros=dict(programa("1.1.0"), **{"nuevo.py": b"def roto(:\n"}))
    falla_con("contenido", intentar, "un .py a medias (error de sintaxis)")
    publicar(base, "1.1.0", ficheros=dict(programa("1.1.0"), **{"../../fuera.py": b"x = 1\n"}))
    falla_con("zip_corrupto", intentar, "ZIP con rutas que se salen de la carpeta")
    comprobar(not os.path.exists(os.path.join(TMP, "fuera.py")) and not os.path.exists(os.path.join(os.path.dirname(TMP), "fuera.py")),
              "y no ha escrito nada fuera")
    api = act.API
    act.API = puerto_cerrado()
    falla_con("sin_red", lambda: act.preparar(raiz, {"url_zip": act.API + "/x.zip", "version": "1.1.0"}), "sin Internet al descargar")
    act.API = api
    comprobar(foto(raiz) == antes and not os.path.exists(os.path.join(raiz, "_backup")),
              "tras todos los intentos la instalación sigue idéntica y sin copias")


def prueba_fallo_a_mitad(base):
    print("\n6. Fallos al aplicar: queda la versión anterior")
    raiz = instalacion("a_mitad")
    antes = foto(raiz)
    publicar(base, "1.1.0")
    origen = act.preparar(raiz, act.consultar_release())

    pip = Pip(va_bien=False)
    falla_con("dependencias", lambda: act.aplicar(raiz, origen, instalar=pip, decir=callado), "pip falla")
    comprobar(foto(raiz) == antes and not act.interrumpida(raiz), "con pip fallando no se ha tocado ningún fichero")

    os.environ["SCOPEPOWER_ACT_FALLAR_TRAS"] = "3"
    pip = Pip()
    falla_con("copia", lambda: act.aplicar(raiz, origen, instalar=pip, decir=callado), "corte a mitad de la copia")
    del os.environ["SCOPEPOWER_ACT_FALLAR_TRAS"]
    comprobar(foto(raiz) == antes, "tras el corte todo está como antes (programa y usuario)")
    comprobar(act.leer_version(raiz) == "1.0.0" and not act.interrumpida(raiz), "sigue en la 1.0.0 y sin copia en curso")
    comprobar(pip.llamadas == [b"flask\nsegno\n", b"flask\n"], "las dependencias vuelven a las anteriores")
    comprobar(act.lista_instalada(raiz, "1.1.0") is None, "no queda apuntada como instalada la versión nueva")

    # Con una copia anterior ya guardada, un fallo no la pierde
    comprobar(act.aplicar(raiz, origen, instalar=Pip(), decir=callado)[0] == "actualizado", "sin el corte, actualiza")
    publicar(base, "1.2.0", ficheros=dict(programa("1.2.0"), **{"version.py": b'VERSION = "1.2.0"\n'}))
    origen2 = act.preparar(raiz, act.consultar_release())
    os.environ["SCOPEPOWER_ACT_FALLAR_TRAS"] = "1"
    falla_con("copia", lambda: act.aplicar(raiz, origen2, instalar=Pip(), decir=callado), "corte en la siguiente actualización")
    del os.environ["SCOPEPOWER_ACT_FALLAR_TRAS"]
    comprobar(os.listdir(os.path.join(raiz, "_backup")) == ["1.0.0"] and act.leer_version(raiz) == "1.1.0",
              "la copia de la 1.0.0 sigue ahí y la instalada es la 1.1.0")


def prueba_interrumpida(base):
    print("\n7. Actualización interrumpida de golpe")
    raiz = instalacion("interrumpida")
    antes = foto(raiz)
    publicar(base, "1.1.0")
    origen = act.preparar(raiz, act.consultar_release())
    os.environ["SCOPEPOWER_ACT_FALLAR_TRAS"] = "4"
    restaurar = act._restaurar

    def apagon(*args, **kwargs):                           # el proceso muere antes de poder restaurar
        raise KeyboardInterrupt
    act._restaurar = apagon
    try:
        act.aplicar(raiz, origen, instalar=Pip(), decir=callado)
    except KeyboardInterrupt:
        pass
    act._restaurar = restaurar
    del os.environ["SCOPEPOWER_ACT_FALLAR_TRAS"]
    comprobar(foto(raiz) != antes and act.interrumpida(raiz), "queda a medias y se detecta")
    comprobar(solo_propios(foto(raiz)) == solo_propios(antes), "ni a medias se ha tocado lo del usuario")
    comprobar(act.recuperar(raiz, callado) == "1.0.0" and foto(raiz) == antes and not act.interrumpida(raiz),
              "recuperar() deja la 1.0.0 exacta")
    # Si el corte llega mientras se hacía la copia de seguridad, aún no se había cambiado nada
    os.makedirs(os.path.join(raiz, "_backup", "_en_curso", "ficheros"))
    comprobar(act.recuperar(raiz, callado) is None and foto(raiz) == antes and not os.path.exists(os.path.join(raiz, "_backup", "_en_curso")),
              "una copia de seguridad sin terminar se descarta sin tocar nada")


def prueba_desarrollo():
    print("\n8. Carpeta de desarrollo")
    raiz = instalacion("desarrollo")
    comprobar(act.rama_git(raiz) == "master" and act.carpeta_desarrollo(raiz) is None, "un clon en master se actualiza como cualquiera")
    escribir(raiz, {".git/HEAD": b"ref: refs/heads/arreglo-centrado-goto\n"})
    comprobar(act.carpeta_desarrollo(raiz) == "arreglo-centrado-goto", "un clon en otra rama es de desarrollo")
    escribir(raiz, {".git/HEAD": b"0123456789abcdef0123456789abcdef01234567\n"})
    comprobar(act.carpeta_desarrollo(raiz) is None, "una versión suelta (sin rama) no lo es")
    shutil.rmtree(os.path.join(raiz, ".git"))
    comprobar(act.rama_git(raiz) is None, "sin carpeta .git no hay rama")
    escribir(raiz, {".git": b"gitdir: otra/.git/worktrees/x\n", "otra/.git/worktrees/x/HEAD": b"ref: refs/heads/prueba\n"})
    comprobar(act.carpeta_desarrollo(raiz) == "prueba", "un árbol de trabajo secundario también se reconoce")


def prueba_lanzador(base):
    print("\n9. Modo lanzador, como lo ejecuta el .bat")
    raiz = instalacion("lanzador")
    antes = foto(raiz)
    nueva = dict(programa("1.1.0"), **{"requirements.txt": b"flask\n"})      # sin cambio de dependencias: pip de verdad no
    publicar(base, "1.1.0", ficheros=nueva)
    GitHubFalso.estado["arboles"] = {"v1.0.0": list(programa("1.0.0"))}
    # El .bat copia el script fuera de la instalación y lo ejecuta desde allí
    copia_script = os.path.join(TMP, "copia_script", "aplicar_actualizacion.py")
    os.makedirs(os.path.dirname(copia_script), exist_ok=True)
    shutil.copyfile(os.path.join(RAIZ, "aplicar_actualizacion.py"), copia_script)
    entorno = dict(os.environ, SCOPEPOWER_API_GITHUB=base, PYTHONIOENCODING="utf-8")

    def lanzador(pedido):
        if pedido is not None:
            escribir(raiz, {"datos/actualizar_pendiente.txt": json.dumps(pedido).encode("utf-8")})
        r = subprocess.run([sys.executable, copia_script, "lanzador", "--raiz", raiz], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=entorno, timeout=120)
        lineas = [l for l in r.stdout.splitlines() if l.startswith("[RESULTADO]")]
        return r.returncode, (lineas[-1] if lineas else r.stdout[-300:] + r.stderr[-300:])

    origen = act.preparar(raiz, act.consultar_release())
    codigo, linea = lanzador({"destino": "release", "origen": origen, "version": "1.1.0"})
    comprobar(codigo == 0 and linea == "[RESULTADO] ok actualizado 1.1.0", f"actualiza: {linea}")
    comprobar(act.leer_version(raiz) == "1.1.0" and "viejo.py" not in foto(raiz), "queda la 1.1.0 sin los ficheros antiguos")
    comprobar(not os.path.exists(os.path.join(raiz, "datos", "actualizar_pendiente.txt")), "el pedido se consume")
    comprobar(not os.path.exists(act.carpeta_trabajo(raiz)), "y la carpeta de trabajo se limpia")
    codigo, linea = lanzador({"destino": "volver"})
    comprobar(codigo == 0 and linea == "[RESULTADO] ok vuelta 1.0.0" and foto(raiz) == dict(antes),
              f"vuelve a la anterior: {linea}")
    codigo, linea = lanzador({"destino": "release", "origen": os.path.join(TMP, "no_existe")})
    comprobar(codigo == 1 and linea == "[RESULTADO] error mantenida" and foto(raiz) == antes, f"sin descarga no toca nada: {linea}")
    origen = act.preparar(raiz, act.consultar_release())
    entorno["SCOPEPOWER_ACT_FALLAR_TRAS"] = "2"
    codigo, linea = lanzador({"destino": "release", "origen": origen})
    del entorno["SCOPEPOWER_ACT_FALLAR_TRAS"]
    comprobar(codigo == 1 and linea == "[RESULTADO] error mantenida" and foto(raiz) == antes,
              f"con un corte a mitad se mantiene la versión anterior: {linea}")
    codigo, linea = lanzador(None)
    comprobar(codigo == 0 and linea == "[RESULTADO] ok sin_cambios", f"sin pedido no hace nada: {linea}")


def prueba_capa_web(base):
    print("\n10. Lo que ve la web (actualizar.py)")
    import logging
    import actualizar
    import version
    logging.getLogger("scopepower.actualizar").setLevel(logging.CRITICAL)      # los fallos son a propósito
    raiz = instalacion("web")
    escribir(raiz, {"actualizar_desde_github.bat": b"@echo off\r\n"})
    antes = foto(raiz)
    actualizar.RAIZ = raiz
    actualizar.BAT = os.path.join(raiz, "actualizar_desde_github.bat")
    actualizar.FICHERO_PENDIENTE = os.path.join(raiz, act.FICHERO_PENDIENTE)
    actualizar.LOG_ACTUALIZACION = os.path.join(raiz, "logs", "actualizacion.log")
    version_real, version.VERSION = version.VERSION, "1.0.0"
    cerrada = threading.Event()

    def esperar_fin():
        for __ in range(200):
            if actualizar.estado()["descarga"]["fase"] not in ("consultando", "descargando", "comprobando"):
                break
            time.sleep(0.05)
        return actualizar.estado()["descarga"]

    try:
        publicar(base, "1.1.0")
        c = actualizar.comprobar()
        comprobar(c["ok"] and c["ultima_release"] == "1.1.0" and not c["al_dia_release"] and "Nuevo" in c["notas"],
                  "comprobar(): hay versión nueva, con sus notas")
        version.VERSION = "1.1.0"
        comprobar(actualizar.comprobar()["al_dia_release"] is True, "comprobar(): ya tienes la última versión")
        ok, __ = actualizar.empezar(cerrada.set)
        fin = esperar_fin()
        comprobar(ok and fin["fase"] == "al_dia" and not cerrada.is_set(), "empezar() con la última ya puesta no cierra la app")
        version.VERSION = "1.0.0"

        api, act.API = act.API, puerto_cerrado()
        c = actualizar.comprobar()
        comprobar(not c["ok"] and c["motivo"] == "sin_red" and "Sin conexión a internet" in c["mensaje"],
                  "comprobar() sin red: «Sin conexión a internet»")
        actualizar.empezar(cerrada.set)
        fin = esperar_fin()
        comprobar(fin["fase"] == "error" and "Sin conexión a internet" in fin["mensaje"] and not cerrada.is_set(),
                  "empezar() sin red: error a la vista y la app sigue abierta")
        act.API = api

        publicar(base, "1.1.0", zip_bytes=b"basura" * 500)
        actualizar.empezar(cerrada.set)
        fin = esperar_fin()
        comprobar(fin["fase"] == "error" and not cerrada.is_set() and not os.path.exists(actualizar.FICHERO_PENDIENTE)
                  and foto(raiz) == antes, "empezar() con el ZIP dañado: no cierra la app ni deja pedido ni toca nada")

        publicar(base, "1.1.0")
        ok, __ = actualizar.empezar(cerrada.set)
        fin = esperar_fin()
        pedido = act._leer_json(actualizar.FICHERO_PENDIENTE) or {}
        comprobar(ok and fin["fase"] == "aplicando" and cerrada.wait(2), "empezar(): descarga, comprueba y manda cerrar la app")
        comprobar(pedido.get("destino") == "release" and os.path.isfile(os.path.join(pedido.get("origen", ""), "version.py")),
                  "deja el pedido con la carpeta de la versión descargada")
        comprobar(actualizar.empezar(cerrada.set)[0] is False, "con una actualización en marcha no empieza otra")
        actualizar._poner(fase=None)
        os.remove(actualizar.FICHERO_PENDIENTE)

        comprobar(actualizar.pedir("volver")[0] is False, "pedir('volver') sin copia guardada se niega")
        escribir(raiz, {".git/HEAD": b"ref: refs/heads/mi-rama\n"})
        ok, mensaje = actualizar.empezar(cerrada.set)
        comprobar(ok is False and "mi-rama" in mensaje and actualizar.estado()["desarrollo"] == "mi-rama",
                  "en una carpeta de desarrollo no se actualiza desde la web")
        escribir(raiz, {".git/HEAD": b"ref: refs/heads/master\n"})

        casos = [("[RESULTADO] ok actualizado 1.1.0", True, "1.1.0"),
                 ("[RESULTADO] ok vuelta 1.0.0", True, "1.0.0"),
                 ("[RESULTADO] ok sin_cambios", True, ""),
                 (" [ERROR] La copia de ficheros ha fallado: x\n[RESULTADO] error mantenida", False, "se ha mantenido la versión anterior"),
                 ("[RESULTADO] error sin_restaurar", False, "actualizar_desde_github.bat"),
                 ("[RESULTADO] ok release v1.5.0 (bd178d0)", True, ""),          # log del actualizador con git
                 ("se quedó a medias", False, "no llegó a terminar")]
        for texto, ok_esperado, trozo in casos:
            escribir(raiz, {"logs/actualizacion.log": texto.encode("utf-8")})
            u = actualizar.ultimo_resultado()
            comprobar(u["ok"] is ok_esperado and trozo in u["mensaje"], f"resultado del log: {texto.splitlines()[-1]} -> {u['mensaje']}")
    finally:
        version.VERSION = version_real


if __name__ == "__main__":
    servidor, base = arrancar_github()
    act.API = base
    try:
        prueba_exclusiones()
        prueba_consulta(base)
        raiz, antes, despues = prueba_actualizacion_completa(base)
        prueba_volver(raiz, antes, despues)
        prueba_descargas_malas(base)
        prueba_fallo_a_mitad(base)
        prueba_interrumpida(base)
        prueba_desarrollo()
        prueba_lanzador(base)
        prueba_capa_web(base)
    finally:
        servidor.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
    print()
    if FALLOS:
        print(f"FALLAN {len(FALLOS)} comprobaciones:")
        for f in FALLOS:
            print("  - " + f)
        sys.exit(1)
    print("Todo bien.")
