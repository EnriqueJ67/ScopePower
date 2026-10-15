# actualizar.py
#
# Actualizar ScopePower desde GitHub desde la página Configuración («Actualizar desde
# GitHub»). Aquí solo se consulta y se pide; el trabajo lo hace actualizar_desde_github.bat
# con la app parada, para que pip pueda reinstalar dependencias y nada cambie bajo los
# pies de la app en marcha:
#
#   1. estado():    versión instalada, si la carpeta es un clon de git, si hay git, la
#                   versión anterior guardada y el resultado de la última actualización.
#   2. comprobar(): consulta GitHub (git fetch con timeout corto) y dice cuál es la última
#                   release (etiqueta v* más alta) y qué commits entrarían. Solo se llama al
#                   pulsar «Comprobar»: en el campo no hay Internet y no tiene sentido mirar solo.
#   3. pedir():     deja el destino en datos/actualizar_pendiente.txt. Después ScopePower
#                   sale con código 5 y lanzar_scopepower.bat ejecuta
#                   actualizar_desde_github.bat /lanzador y vuelve a arrancar la app.
#
# Los nombres de fichero y el código de salida tienen que coincidir con los del .bat.

import logging
import os
import re
import subprocess
import time

import version
from idioma import _

log = logging.getLogger("scopepower.actualizar")

RAIZ = os.path.dirname(os.path.abspath(__file__))
# Para pruebas, la misma variable que entiende el .bat sustituye la URL de GitHub
REMOTO = os.environ.get("SCOPEPOWER_REMOTO") or "https://github.com/EnriqueJ67/ScopePower.git"
BAT = os.path.join(RAIZ, "actualizar_desde_github.bat")
FICHERO_PENDIENTE = os.path.join(RAIZ, "datos", "actualizar_pendiente.txt")
FICHERO_ANTERIOR = os.path.join(RAIZ, "datos", "version_anterior.txt")
LOG_ACTUALIZACION = os.path.join(RAIZ, "logs", "actualizacion.log")
CODIGO_SALIDA_ACTUALIZAR = 5          # lo entiende lanzar_scopepower.bat
DESTINOS = ("release", "master", "volver")

TIMEOUT_CONSULTA_S = 10               # ls-remote: con Internet responde en 1-2 s
TIMEOUT_FETCH_S = 90                  # bajar los objetos nuevos puede tardar algo más

_SIN_RED = ("could not resolve", "unable to access", "failed to connect", "timed out",
            "connection was reset", "network is unreachable")
_SIN_PERMISO = ("authentication failed", "could not read username", "repository not found",
                "terminal prompts disabled", "logon failed", "permission denied")


def _git(*args, timeout=TIMEOUT_CONSULTA_S):
    """Ejecuta git en la carpeta de ScopePower sin ventanas ni preguntas de contraseña."""
    entorno = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", LC_ALL="C")
    return subprocess.run(["git", "-C", RAIZ] + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=entorno,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def hay_git():
    try:
        return _git("--version").returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def es_clon():
    """True si la carpeta de ScopePower es la raíz de un repositorio git."""
    try:
        r = _git("rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        return False
    return os.path.normcase(os.path.normpath(r.stdout.strip())) == os.path.normcase(os.path.normpath(RAIZ))


def _describir_head():
    """'v1.1.0' o 'v1.0.0-3-gabc1234' (3 commits después de la etiqueta), y el sha corto."""
    try:
        d = _git("describe", "--tags", "--always", "HEAD")
        s = _git("rev-parse", "--short", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return (d.stdout.strip() or None) if d.returncode == 0 else None, \
           (s.stdout.strip() or None) if s.returncode == 0 else None


def _rama_actual():
    try:
        r = _git("symbolic-ref", "-q", "--short", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def version_anterior():
    """Lo que guardó el .bat antes de la última actualización: {'sha':..., 'nombre':...} o None."""
    try:
        with open(FICHERO_ANTERIOR, encoding="utf-8") as f:
            linea = f.readline().strip()
    except OSError:
        return None
    if not linea:
        return None
    partes = linea.split(None, 1)
    return {"sha": partes[0], "nombre": partes[1] if len(partes) > 1 else partes[0]}


def ultimo_resultado():
    """Resumen de logs/actualizacion.log (lo escribe el .bat en modo /lanzador), o None."""
    try:
        with open(LOG_ACTUALIZACION, encoding="utf-8", errors="replace") as f:
            texto = f.read()
        fecha = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(LOG_ACTUALIZACION)))
    except OSError:
        return None
    m = re.search(r"^\[RESULTADO\] (ok|error)(.*)$", texto, re.MULTILINE)
    if not m:
        return {"ok": False, "fecha": fecha, "mensaje": _("La actualización no llegó a terminar"),
                "detalle": texto[-3000:]}
    ok = m.group(1) == "ok"
    mensaje = _("Actualizado a {version}", version=m.group(2).strip()) \
        if ok and m.group(2).strip() and "sin cambios" not in m.group(2) \
        else (_("No había nada que actualizar") if ok else _("La actualización falló"))
    # Para el fallo, las líneas de [ERROR]/[AVISO] son lo que interesa
    errores = [l.strip() for l in texto.splitlines() if "[ERROR]" in l or "[AVISO]" in l]
    return {"ok": ok, "fecha": fecha, "mensaje": mensaje, "detalle": "\n".join(errores) if errores else texto[-3000:]}


def estado():
    """Lo que enseña la tarjeta al abrir Configuración (sin tocar la red)."""
    git_ok = hay_git()
    clon = es_clon() if git_ok else False
    nombre, sha = _describir_head() if clon else (None, None)
    pendiente = os.path.exists(FICHERO_PENDIENTE)
    return {
        "version": version.VERSION,
        "hay_git": git_ok,
        "es_clon": clon,
        "commit": sha,
        "descripcion": nombre,
        "rama": _rama_actual() if clon else None,
        "anterior": version_anterior() if clon else None,
        "ultimo_resultado": ultimo_resultado(),
        "pendiente": pendiente,
        "lanzador": os.environ.get("SCOPEPOWER_LANZADOR") == "1",
        "bat": os.path.exists(BAT),
    }


def _clave_version(etiqueta):
    """v1.10.2 > v1.9.0: ordena por números, no por texto."""
    return tuple(int(x) for x in re.findall(r"\d+", etiqueta))


def ultima_release(etiquetas):
    """La etiqueta v* más alta de una lista, o None."""
    validas = [t for t in etiquetas if re.fullmatch(r"v\d+(\.\d+)*", t)]
    return max(validas, key=_clave_version) if validas else None


def _motivo_fallo(salida):
    s = (salida or "").lower()
    if any(p in s for p in _SIN_RED):
        return ("sin_red", _("No hay Internet o no se llega a GitHub. En el campo es lo normal: "
                             "actualiza en casa, con el mini PC en una red con salida a Internet."))
    if any(p in s for p in _SIN_PERMISO):
        return ("sin_permiso", _("GitHub no deja leer el repositorio desde este PC. Mientras sea privado hay que "
                                 "identificarse una vez ejecutando actualizar_desde_github.bat en el mini PC, "
                                 "con pantalla: Git abre el navegador para entrar en GitHub."))
    return ("error", _("git ha fallado: {salida}", salida=(salida or "").strip()[-400:]))


def comprobar():
    """
    Consulta GitHub. Devuelve un dict con 'ok' y, si todo va bien: 'ultima_release',
    'commits_release' y 'commits_master' (lo que entraría con cada destino, más nuevo arriba),
    'al_dia_release' y 'al_dia_master'. Si no, 'motivo' (sin_red | sin_permiso | sin_git |
    no_clon | error) y un 'mensaje' para enseñar.
    """
    if not hay_git():
        return {"ok": False, "motivo": "sin_git",
                "mensaje": _("En este PC no está instalado Git. Instálalo desde https://git-scm.com/download/win "
                             "(todas las opciones por defecto) y vuelve a comprobar.")}
    if not es_clon():
        return {"ok": False, "motivo": "no_clon",
                "mensaje": _("Esta carpeta no es un clon de GitHub (es una copia). Ejecuta una vez "
                             "actualizar_desde_github.bat en el mini PC para convertirla; después ya se "
                             "podrá actualizar desde aquí.")}
    try:
        _git("remote", "set-url", "origin", REMOTO)
        r = _git("ls-remote", "--tags", "--refs", "origin", "v*", timeout=TIMEOUT_CONSULTA_S)
        if r.returncode != 0:
            motivo, mensaje = _motivo_fallo(r.stderr)
            log.warning("Comprobar actualización: %s", (r.stderr or "").strip()[-300:])
            return {"ok": False, "motivo": motivo, "mensaje": mensaje}
        etiquetas = [l.split("refs/tags/")[-1] for l in r.stdout.splitlines() if "refs/tags/" in l]
        f = _git("fetch", "--quiet", "--tags", "--prune", "origin", timeout=TIMEOUT_FETCH_S)
        if f.returncode != 0:
            motivo, mensaje = _motivo_fallo(f.stderr)
            return {"ok": False, "motivo": motivo, "mensaje": mensaje}
    except subprocess.TimeoutExpired:
        return {"ok": False, "motivo": "sin_red",
                "mensaje": _("GitHub no ha respondido a tiempo. Comprueba la conexión a Internet del mini PC.")}
    except OSError as e:
        return {"ok": False, "motivo": "error", "mensaje": _("No se pudo ejecutar git: {e}", e=e)}

    ultima = ultima_release(etiquetas)
    resultado = {"ok": True, "ultima_release": ultima, "commits_release": [], "commits_master": [],
                 "al_dia_release": None, "al_dia_master": None, "fecha_release": None}

    def _entrarian(destino):
        """Commits de destino que aquí no están (más nuevo arriba) y si HEAD ya es destino."""
        r = _git("log", "--oneline", "--no-decorate", f"HEAD..{destino}")
        commits = r.stdout.splitlines() if r.returncode == 0 else []
        d = _git("diff", "--quiet", "HEAD", destino)
        return commits, d.returncode == 0

    if ultima:
        resultado["commits_release"], resultado["al_dia_release"] = _entrarian(ultima)
        fecha = _git("log", "-1", "--format=%cs", ultima)
        resultado["fecha_release"] = fecha.stdout.strip() or None
    resultado["commits_master"], resultado["al_dia_master"] = _entrarian("origin/master")
    log.info("Comprobada la actualización: instalado %s, última release %s, master trae %d commits",
             version.VERSION, ultima or "ninguna", len(resultado["commits_master"]))
    return resultado


def pedir(destino):
    """
    Deja apuntado el destino para actualizar_desde_github.bat /lanzador. Devuelve (ok, mensaje).
    El que llama (ScopePower.py) para después la app con CODIGO_SALIDA_ACTUALIZAR.
    """
    if destino not in DESTINOS:
        return False, _("Destino no válido")
    if not os.path.exists(BAT):
        return False, _("Falta actualizar_desde_github.bat en la carpeta de ScopePower")
    if not hay_git():
        return False, _("Git no está instalado en este PC")
    if not es_clon():
        return False, _("Esta carpeta no es un clon de GitHub: conviértela primero con actualizar_desde_github.bat")
    if destino == "volver" and not version_anterior():
        return False, _("No hay ninguna versión anterior guardada")
    rama = _rama_actual()
    if rama and rama != "master":
        return False, _("La carpeta está en la rama {rama}: es una carpeta de desarrollo, actualízala con git", rama=rama)
    os.makedirs(os.path.dirname(FICHERO_PENDIENTE), exist_ok=True)
    with open(FICHERO_PENDIENTE, "w", encoding="utf-8") as f:
        f.write(destino + "\n")
    log.warning("Actualización desde GitHub pedida desde la web (destino: %s)", destino)
    return True, _("ScopePower se cierra para actualizarse y vuelve a arrancar solo")


def orden_actualizar_a_mano():
    """
    Cuando la app no la arrancó lanzar_scopepower.bat: la orden para cmd que actualiza
    con la app ya cerrada y vuelve a arrancarla (equivalente a lo que hace el lanzador
    con el código de salida 5).
    """
    return f'ping -n 3 127.0.0.1 >nul & "{BAT}" /lanzador /relanzar'
