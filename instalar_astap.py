# instalar_astap.py
#
# Comprueba si ASTAP y un catálogo de estrellas están instalados y, si faltan, los
# descarga e instala sin pedir permisos de administrador (asistente de configuración
# inicial, templates/_asistente.html).
#
# Qué se instala (probado el 6-oct-2026 con una captura real de mayo de 2025):
#   - astap_cli.exe, el resolvedor de línea de órdenes de ASTAP (zip de 0,3 MB). Acepta
#     la misma orden que usa la app (-f foto -solve -r 50) y escribe el mismo .ini/.wcs
#     que astap.exe, en 0,4 s frente a 1,9 s.
#   - El catálogo elegido en zip (D05, D20 o D50; el D80 solo existe como instalador .exe
#     y se deja como enlace).
# Todo va a %LOCALAPPDATA%\ScopePower\astap: fuera de OneDrive (el D50 descomprimido
# ocupa ~1 GB y no debe sincronizarse) y sin necesidad de administrador. Si ya hay un
# ASTAP con catálogo en Program Files, se usa ese y no se descarga nada.

import os
import shutil
import threading
import time
import zipfile

import requests

URL_SF = "https://downloads.sourceforge.net/project/astap-program/"
ZIP_CLI = "windows_installer/astap_command-line_version_win64.zip"
CARPETA_LOCAL = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ScopePower", "astap")
CLI_LOCAL = os.path.join(CARPETA_LOCAL, "astap_cli.exe")
URL_WEB = "https://www.hnsky.org/astap.htm"

# Catálogos que se pueden instalar solos (zip) y su tamaño aproximado de descarga
CATALOGOS = {
    "d05": {"mb": 102, "nombre": "D05", "nota": "el más pequeño; campos de 1° o más"},
    "d20": {"mb": 400, "nombre": "D20", "nota": "campos de 0,6° o más"},
    "d50": {"mb": 901, "nombre": "D50", "nota": "el más usado; campos de 0,6° o más"},
}
# Prefijos de los ficheros de catálogo que sirven para resolver (como herramientas/comprobar_equipo.py)
PREFIJOS = ("d05", "d20", "d50", "d80", "h17", "h18", "w08", "v50", "g05")

_lock = threading.Lock()
_progreso = {"en_curso": False, "fase": "", "texto": "", "bytes": 0, "total": 0,
             "mb_s": 0.0, "error": "", "ok": False, "base": "", "cancelar": False}


def _candidatos(ruta_config):
    """Rutas posibles del ejecutable, en orden de preferencia."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    rutas = [ruta_config,
             os.path.join(pf, "astap", "astap.exe"), os.path.join(pf, "astap", "astap_cli.exe"),
             os.path.join(pf86, "astap", "astap.exe"),
             CLI_LOCAL]
    vistas, salida = set(), []
    for r in rutas:
        if r and os.path.normcase(r) not in vistas:
            vistas.add(os.path.normcase(r))
            salida.append(r)
    return salida


def catalogos_en(carpeta):
    """Catálogos presentes en la carpeta de ASTAP: ['d50', ...]."""
    try:
        nombres = os.listdir(carpeta)
    except OSError:
        return []
    return sorted({n[:3].lower() for n in nombres
                   if n.lower().startswith(PREFIJOS) and not n.lower().endswith((".txt", ".exe", ".zip", ".part"))})


def recomendado(campo_deg):
    """Catálogo aconsejado para el campo de la cámara (diagonal corta, en grados)."""
    if campo_deg is None:
        return "d50"
    if campo_deg < 0.6:
        return "d80"      # solo como instalador: se ofrece el enlace
    if campo_deg < 1.0:
        return "d50"
    if campo_deg < 2.0:
        return "d20"
    return "d05"


def estado(ruta_config="", campo_deg=None):
    """
    {"ok", "ruta", "programa", "catalogos", "recomendado", "campo_deg", "opciones", "progreso"}
    ok = hay un ejecutable con catálogo; ruta = el que hay que guardar en config.ini.
    """
    ruta, catalogos, programa = "", [], False
    for r in _candidatos(ruta_config):
        if os.path.isfile(r):
            bases = catalogos_en(os.path.dirname(r))
            if not programa or bases:
                ruta, catalogos, programa = r, bases, True
            if bases:
                break
    return {
        "ok": bool(programa and catalogos),
        "ruta": ruta,
        "programa": programa,
        "catalogos": catalogos,
        "recomendado": recomendado(campo_deg),
        "campo_deg": round(campo_deg, 2) if campo_deg else None,
        "opciones": [dict(id=k, **v) for k, v in CATALOGOS.items()],
        "carpeta_local": CARPETA_LOCAL,
        "web": URL_WEB,
        "progreso": progreso(),
    }


def progreso():
    with _lock:
        return {k: v for k, v in _progreso.items() if k != "cancelar"}


def _poner(**cambios):
    with _lock:
        _progreso.update(cambios)


def cancelar():
    _poner(cancelar=True)


class _Cancelado(Exception):
    pass


def _descargar(ruta_relativa, destino, texto):
    """Descarga de SourceForge a destino (vía .part) con progreso; lanza _Cancelado o RuntimeError."""
    parcial = destino + ".part"
    _poner(fase="descargando", texto=texto, bytes=0, total=0, mb_s=0.0)
    with requests.get(URL_SF + ruta_relativa, stream=True, timeout=(20, 60), allow_redirects=True) as r:
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            raise RuntimeError("SourceForge ha devuelto una página web en vez del fichero; prueba más tarde")
        total = int(r.headers.get("Content-Length") or 0)
        _poner(total=total)
        hechos, t0 = 0, time.time()
        with open(parcial, "wb") as f:
            for trozo in r.iter_content(chunk_size=1 << 20):
                if _progreso["cancelar"]:
                    raise _Cancelado()
                f.write(trozo)
                hechos += len(trozo)
                _poner(bytes=hechos, mb_s=round(hechos / 1e6 / max(time.time() - t0, 0.1), 2))
    if total and hechos < total:
        raise RuntimeError(f"descarga incompleta ({hechos // 1_000_000} de {total // 1_000_000} MB)")
    os.replace(parcial, destino)


def _descomprimir(ruta_zip, carpeta, texto):
    _poner(fase="descomprimiendo", texto=texto)
    with zipfile.ZipFile(ruta_zip) as z:
        miembros = [m for m in z.infolist() if not m.is_dir()]
        _poner(bytes=0, total=len(miembros), mb_s=0.0)
        for i, m in enumerate(miembros, 1):
            if _progreso["cancelar"]:
                raise _Cancelado()
            nombre = os.path.basename(m.filename)    # los zip de ASTAP son planos; por si acaso, sin carpetas
            if nombre:
                with z.open(m) as origen, open(os.path.join(carpeta, nombre), "wb") as dest:
                    shutil.copyfileobj(origen, dest)
            if i % 25 == 0 or i == len(miembros):
                _poner(bytes=i)


def instalar(base, al_terminar=None):
    """
    Lanza en segundo plano la instalación de astap_cli.exe + el catálogo `base` (d05/d20/d50).
    al_terminar(ruta_exe) se llama si acaba bien (para guardar ruta_astap en config.ini).
    Devuelve (True, "") o (False, motivo) si no se puede empezar.
    """
    base = str(base or "").lower()
    if base not in CATALOGOS:
        return False, "Ese catálogo no se puede instalar automáticamente"
    with _lock:
        if _progreso["en_curso"]:
            return False, "Ya hay una instalación en marcha"
    os.makedirs(CARPETA_LOCAL, exist_ok=True)
    necesario_mb = CATALOGOS[base]["mb"] * 2.3 + 50   # zip + descomprimido (algo mayor) + margen
    libre_mb = shutil.disk_usage(CARPETA_LOCAL).free / 1e6
    if libre_mb < necesario_mb:
        return False, f"No hay sitio en el disco: hacen falta unos {necesario_mb / 1000:.1f} GB y quedan {libre_mb / 1000:.1f} GB"
    with _lock:
        _progreso.update(en_curso=True, fase="empezando", texto="", bytes=0, total=0, mb_s=0.0,
                         error="", ok=False, base=base, cancelar=False)
    threading.Thread(target=_tarea, args=(base, al_terminar), name="instalar-astap", daemon=True).start()
    return True, ""


def _tarea(base, al_terminar):
    zip_cat = os.path.join(CARPETA_LOCAL, f"{base}_star_database.zip")
    zip_cli = os.path.join(CARPETA_LOCAL, "astap_cli.zip")
    try:
        if not os.path.isfile(CLI_LOCAL):
            _descargar(ZIP_CLI, zip_cli, "Programa ASTAP (línea de órdenes)")
            _descomprimir(zip_cli, CARPETA_LOCAL, "Programa ASTAP")
            os.remove(zip_cli)
            if not os.path.isfile(CLI_LOCAL):
                raise RuntimeError("el zip de ASTAP no traía astap_cli.exe")
        nombre = CATALOGOS[base]["nombre"]
        _descargar(f"star_databases/{base}_star_database.zip", zip_cat, f"Catálogo de estrellas {nombre}")
        _descomprimir(zip_cat, CARPETA_LOCAL, f"Catálogo de estrellas {nombre}")
        os.remove(zip_cat)
        if base not in catalogos_en(CARPETA_LOCAL):
            raise RuntimeError("el catálogo no ha quedado en la carpeta de ASTAP")
        if al_terminar:
            al_terminar(CLI_LOCAL)
        _poner(en_curso=False, fase="terminado", texto=f"ASTAP y el catálogo {nombre} instalados", ok=True)
    except _Cancelado:
        _poner(en_curso=False, fase="cancelado", texto="Instalación cancelada", error="")
    except Exception as e:
        _poner(en_curso=False, fase="error", error=str(e) or e.__class__.__name__)
    finally:
        for resto in (zip_cat, zip_cli, zip_cat + ".part", zip_cli + ".part"):
            try:
                if os.path.exists(resto):
                    os.remove(resto)
            except OSError:
                pass
