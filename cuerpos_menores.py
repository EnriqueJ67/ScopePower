# cuerpos_menores.py
#
# Cometas y asteroides sin Internet.
#
# El Minor Planet Center publica los elementos orbitales en formato xephem
# (cometas observables y asteroides brillantes del año). Cuando el PC tiene
# red, se descargan a datos/mpc/ y se renuevan cada semana; sin red se usan
# los guardados, que valen durante meses. La posición para el momento del
# GOTO la calcula PyEphem en el propio PC (precisión de minutos de arco: de
# sobra para apuntar y que el centrado automático remate).
#
#   coordenadas("C/2023 A3")  -> (ra_h, dec_deg, info) o None
#   coordenadas("4 Vesta")    -> ...
#   brillantes(...)           -> lista para la página "Esta noche"

import os
import re
import time
import socket
import logging
import threading
import unicodedata
from datetime import datetime, date

from idioma import _, ngettext, fmt_num

log = logging.getLogger("scopepower.cuerpos_menores")

CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "mpc")
FUENTES = {
    "cometas.txt": "https://minorplanetcenter.net/iau/Ephemerides/Comets/Soft03Cmt.txt",
    "asteroides.txt": "https://minorplanetcenter.net/iau/Ephemerides/Bright/{anio}/Soft03Bright.txt",
}
MAX_EDAD_DIAS = 7
HOST_MPC = ("minorplanetcenter.net", 443)

_lock = threading.Lock()
_cache = {"mtime": None, "cuerpos": [], "indice": {}}


# ── Descarga ────────────────────────────────────────────────────────────────
def hay_internet(timeout=3.0):
    try:
        with socket.create_connection(HOST_MPC, timeout=timeout):
            return True
    except OSError:
        return False


def _edad_dias(ruta):
    if not os.path.exists(ruta):
        return None
    return (time.time() - os.path.getmtime(ruta)) / 86400.0


def actualizar(forzar=False):
    """
    Descarga las listas si faltan o tienen más de MAX_EDAD_DIAS y hay red.
    Devuelve (descargadas, mensaje).
    """
    os.makedirs(CARPETA, exist_ok=True)
    pendientes = [n for n in FUENTES if forzar or (_edad_dias(os.path.join(CARPETA, n)) or 1e9) > MAX_EDAD_DIAS]
    if not pendientes:
        return 0, _("Elementos orbitales al día")
    if not hay_internet():
        return 0, (_("Sin Internet: se usan los elementos guardados") if estado()["cometas"]
                   else _("Sin Internet y sin elementos guardados"))
    import requests
    n = 0
    for nombre in pendientes:
        url = FUENTES[nombre].format(anio=date.today().year)
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            if b",e," not in r.content[:5000]:
                raise ValueError("el fichero no tiene el formato esperado")
            with open(os.path.join(CARPETA, nombre), "wb") as f:
                f.write(r.content)
            n += 1
            log.info("Elementos orbitales actualizados: %s (%d bytes)", nombre, len(r.content))
        except Exception as e:
            log.warning("No se pudo descargar %s: %s", url, e)
    with _lock:
        _cache["mtime"] = None          # forzar recarga
    return n, ngettext("{n} lista de elementos actualizada", "{n} listas de elementos actualizadas", n)


def actualizar_en_segundo_plano():
    threading.Thread(target=lambda: log.info("MPC: %s", actualizar()[1]), name="mpc-actualizar", daemon=True).start()


def estado():
    out = {}
    for nombre, clave in (("cometas.txt", "cometas"), ("asteroides.txt", "asteroides")):
        edad = _edad_dias(os.path.join(CARPETA, nombre))
        out[clave] = None if edad is None else round(edad, 1)
    return out


# ── Carga e índice ──────────────────────────────────────────────────────────
def _normalizar(texto):
    texto = unicodedata.normalize("NFD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto).strip().lower()


def _claves(nombre_xephem, tipo):
    """Formas por las que se puede buscar un cuerpo."""
    claves = {_normalizar(nombre_xephem)}
    if tipo == "cometa":
        # "C/2023 A3 (Tsuchinshan-ATLAS)" -> "c/2023 a3", "tsuchinshan-atlas", "tsuchinshan atlas", "tsuchinshan"
        m = re.match(r"^\s*([0-9]*[PCDXIA](?:-[A-Z]+)?/?[^(]*?)\s*(?:\((.*)\))?\s*$", nombre_xephem)
        if m:
            desig = m.group(1).strip()
            claves.add(_normalizar(desig))
            claves.add(_normalizar(desig.replace(" ", "")))
            if m.group(2):
                propio = m.group(2)
                claves.add(_normalizar(propio))
                claves.add(_normalizar(propio.replace("-", " ")))
                for parte in re.split(r"[-/ ]", propio):
                    if len(parte) >= 4:
                        claves.add(_normalizar(parte))
        # "12P/Pons-Brooks" -> "12p", "pons-brooks", "pons brooks"
        m2 = re.match(r"^\s*(\d+[PCDI])/(.+)$", nombre_xephem)
        if m2:
            claves.add(_normalizar(m2.group(1)))
            claves.add(_normalizar(m2.group(2)))
            claves.add(_normalizar(m2.group(2).replace("-", " ")))
    else:
        # "1 Ceres" -> "1 ceres", "ceres", "1", "(1) ceres"
        m = re.match(r"^\s*(\d+)\s+(.+)$", nombre_xephem)
        if m:
            claves.add(_normalizar(m.group(2)))
            claves.add(m.group(1))
            claves.add(_normalizar(f"({m.group(1)}) {m.group(2)}"))
        m3 = re.match(r"^\s*(\d{4} [A-Z]{2}\d*)", nombre_xephem)   # designación provisional
        if m3:
            claves.add(_normalizar(m3.group(1)))
    return {c for c in claves if c}


def cargar(forzar=False):
    """Lee las listas guardadas (caché en memoria hasta que cambien los ficheros)."""
    import ephem
    rutas = [(os.path.join(CARPETA, "cometas.txt"), "cometa"), (os.path.join(CARPETA, "asteroides.txt"), "asteroide")]
    mtime = tuple(os.path.getmtime(r) if os.path.exists(r) else 0 for r, __ in rutas)
    with _lock:
        if not forzar and _cache["mtime"] == mtime:
            return _cache["cuerpos"]
    cuerpos, indice = [], {}
    for ruta, tipo in rutas:
        if not os.path.exists(ruta):
            continue
        with open(ruta, encoding="latin-1") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or ",e," not in linea:
                    continue
                try:
                    cuerpo = ephem.readdb(linea)
                except Exception:
                    continue
                entrada = {"nombre": cuerpo.name, "tipo": tipo, "linea": linea}
                cuerpos.append(entrada)
                for c in _claves(cuerpo.name, tipo):
                    indice.setdefault(c, []).append(entrada)
    with _lock:
        _cache.update(mtime=mtime, cuerpos=cuerpos, indice=indice)
    log.info("Cuerpos menores cargados: %d (cometas %d, asteroides %d)", len(cuerpos),
             sum(1 for c in cuerpos if c["tipo"] == "cometa"), sum(1 for c in cuerpos if c["tipo"] == "asteroide"))
    return cuerpos


def buscar(nombre):
    """Entrada del índice que mejor encaja con `nombre`, o None."""
    cargar()
    clave = _normalizar(nombre)
    if not clave:
        return None
    with _lock:
        indice = _cache["indice"]
    if clave in indice:
        return indice[clave][0]
    # Sin espacios ("c/2023a3", "4vesta")
    compacta = clave.replace(" ", "")
    for k, v in indice.items():
        if k.replace(" ", "") == compacta:
            return v[0]
    # Contenido único (p. ej. "tsuchinshan" dentro de "tsuchinshan-atlas") con al menos 4 letras
    if len(clave) >= 4:
        candidatos = {id(v[0]): v[0] for k, v in indice.items() if clave in k}
        if len(candidatos) == 1:
            return next(iter(candidatos.values()))
    return None


def _patron_a_regex(patron):
    """'c/2023*' -> regex; '*' = cualquier texto. Sin comodín: contiene."""
    p = _normalizar(patron)
    if not p:
        return None
    if "*" not in p and "?" not in p:
        return re.compile(re.escape(p))
    partes = [re.escape(x) for x in re.split(r"([*?])", p)]
    rx = "".join(".*" if x == r"\*" else "." if x == r"\?" else x for x in partes)
    return re.compile("^" + rx + "$")


def buscar_patron(patron, maximo=30):
    """
    Cuerpos cuyo nombre encaja con el patrón ('*' cualquier texto, '?' un
    carácter; sin comodines, que lo contenga). Devuelve [{nombre, tipo}].
    """
    rx = _patron_a_regex(patron)
    if rx is None:
        return []
    cuerpos = cargar()
    salida = []
    for c in cuerpos:
        if rx.search(_normalizar(c["nombre"])):
            salida.append({"nombre": c["nombre"], "tipo": c["tipo"]})
            if len(salida) >= maximo:
                break
    return salida


# ── Posiciones ──────────────────────────────────────────────────────────────
def _observador(lat, lon, alt_m, cuando):
    import ephem
    obs = ephem.Observer()
    obs.lat, obs.lon, obs.elevation = str(lat), str(lon), float(alt_m)
    obs.date = ephem.Date(cuando or datetime.utcnow())
    obs.pressure = 0            # sin refracción: queremos coordenadas, no altura aparente
    return obs


def coordenadas(nombre, lat, lon, alt_m=0.0, cuando=None):
    """
    (ra_horas, dec_grados, info) en J2000 astrométrico para `nombre`, o None si
    no está en las listas. `cuando` es datetime UTC (por defecto ahora).
    """
    import ephem
    from math import degrees
    entrada = buscar(nombre)
    if entrada is None:
        return None
    cuerpo = ephem.readdb(entrada["linea"])
    obs = _observador(lat, lon, alt_m, cuando)
    cuerpo.compute(obs)
    ra_h = float(cuerpo.a_ra) * 12.0 / ephem.pi
    dec = degrees(float(cuerpo.a_dec))
    info = {"nombre": entrada["nombre"], "tipo": entrada["tipo"], "mag": round(float(cuerpo.mag), 1),
            "distancia_ua": round(float(cuerpo.earth_distance), 3), "alt": round(degrees(float(cuerpo.alt)), 1)}
    return ra_h, dec, info


def brillantes(lat, lon, alt_m=0.0, cuando=None, mag_max=12.0, maximo=25):
    """
    Cometas y asteroides con magnitud prevista <= mag_max en `cuando`, para
    la página "Esta noche". Cada uno con ra_h, dec_deg, mag, tipo y nombre.
    """
    import ephem
    from math import degrees
    cuerpos = cargar()
    if not cuerpos:
        return []
    obs = _observador(lat, lon, alt_m, cuando)
    salida = []
    for entrada in cuerpos:
        try:
            cuerpo = ephem.readdb(entrada["linea"])
            cuerpo.compute(obs)
            mag = float(cuerpo.mag)
        except Exception:
            continue
        if mag > mag_max or mag != mag:
            continue
        salida.append({
            "id": entrada["nombre"], "nombre": entrada["nombre"], "nombre_alt": "",
            "tipo": "Cometa" if entrada["tipo"] == "cometa" else "Asteroide",
            "ra_h": float(cuerpo.a_ra) * 12.0 / ephem.pi, "dec_deg": degrees(float(cuerpo.a_dec)),
            "mag": round(mag, 1), "tamano_arcmin": None, "constelacion": "",
            "popularidad": 5 if mag < 7 else 4 if mag < 9 else 3 if mag < 11 else 2,
            "descripcion": (_("Cometa a {ua} UA; magnitud prevista {mag} (elementos del MPC).",
                              ua=fmt_num(float(cuerpo.earth_distance), 2), mag=fmt_num(mag, 1))
                            if entrada["tipo"] == "cometa" else
                            _("Asteroide a {ua} UA; magnitud prevista {mag} (elementos del MPC).",
                              ua=fmt_num(float(cuerpo.earth_distance), 2), mag=fmt_num(mag, 1))),
        })
    salida.sort(key=lambda s: s["mag"])
    return salida[:maximo]
