# recorridos.py
#
# Recorrido por constelación (portado de la versión de junio de ScopePower):
# dado el nombre de una constelación en español, construye una lista de sus
# estrellas con nombre propio y objetos Messier, ordenada por cercanía, con la
# descripción de Wikipedia. Se guarda en constelaciones/<IAU>.csv la primera
# vez y después se lee de ahí (sin internet en el campo). Los ficheros
# constelaciones/*.txt definen recorridos de varias constelaciones para
# sesiones guiadas ("Cielo de verano", ...).

import os
import re
import csv
import math
import logging

import numpy as np
from unidecode import unidecode

log = logging.getLogger("scopepower.recorridos")

CARPETA_CONSTELACIONES = "constelaciones"
CARPETA_INFO = "info_objetos"
SOLO_CON_NOMBRE_COMUN = True

CONST_SPA_IAU = {
    'andromeda': 'Andromeda', 'maquina neumatica': 'Antlia', 'ave del paraiso': 'Apus',
    'acuario': 'Aquarius', 'aguila': 'Aquila', 'altar': 'Ara', 'aries': 'Aries',
    'cochero': 'Auriga', 'boyero': 'Boötes', 'el boyero': 'Boötes', 'bootes': 'Boötes',
    'cincel': 'Caelum', 'jirafa': 'Camelopardalis', 'cancer': 'Cancer', 'lebreles': 'Canes Venatici',
    'can mayor': 'Canis Major', 'can menor': 'Canis Minor', 'capricornio': 'Capricornus',
    'quilla': 'Carina', 'casiopea': 'Cassiopeia', 'centauro': 'Centaurus', 'cefeo': 'Cepheus',
    'ballena': 'Cetus', 'camaleon': 'Chamaeleon', 'compas': 'Circinus', 'paloma': 'Columba',
    'cabellera de berenice': 'Coma Berenices', 'corona austral': 'Corona Australis',
    'corona boreal': 'Corona Borealis', 'cuervo': 'Corvus', 'copa': 'Crater', 'cruz del sur': 'Crux',
    'cisne': 'Cygnus', 'delfin': 'Delphinus', 'dorada': 'Dorado', 'dragon': 'Draco',
    'caballito': 'Equuleus', 'eridano': 'Eridanus', 'horno': 'Fornax', 'geminis': 'Gemini',
    'grulla': 'Grus', 'hercules': 'Hercules', 'reloj': 'Horologium', 'hidra': 'Hydra',
    'hidra macho': 'Hydrus', 'indio': 'Indus', 'lagarto': 'Lacerta', 'leo': 'Leo',
    'leon menor': 'Leo Minor', 'liebre': 'Lepus', 'libra': 'Libra', 'lobo': 'Lupus', 'lince': 'Lynx',
    'lira': 'Lyra', 'mesa': 'Mensa', 'microscopio': 'Microscopium', 'unicornio': 'Monoceros',
    'mosca': 'Musca', 'escuadra': 'Norma', 'octante': 'Octans', 'ofiuco': 'Ophiuchus',
    'orion': 'Orion', 'pavo': 'Pavo', 'pegaso': 'Pegasus', 'perseo': 'Perseus', 'ave fenix': 'Phoenix',
    'caballete del pintor': 'Pictor', 'piscis': 'Pisces', 'pez austral': 'Piscis Austrinus',
    'popa': 'Puppis', 'brujula': 'Pyxis', 'reticulo': 'Reticulum', 'flecha': 'Sagitta',
    'sagitario': 'Sagittarius', 'escorpio': 'Scorpius', 'escultor': 'Sculptor', 'escudo': 'Scutum',
    'serpiente': 'Serpens', 'sextante': 'Sextans', 'tauro': 'Taurus', 'telescopio': 'Telescopium',
    'triangulo': 'Triangulum', 'triangulo austral': 'Triangulum Australe', 'tucan': 'Tucana',
    'osa mayor': 'Ursa Major', 'osa menor': 'Ursa Minor', 'velas': 'Vela', 'virgo': 'Virgo',
    'pez volador': 'Volans', 'raposa': 'Vulpecula',
}


# ─────────────────────────────────────────────────────────────────────────────
#  Utilidades de formato
# ─────────────────────────────────────────────────────────────────────────────

def formatear_ra_dec(ra_h, dec_deg):
    """RA en horas -> 'HH MM SS'; DEC en grados -> '+DD MM SS'."""
    ra_h = float(ra_h) % 24
    h = int(ra_h); m = int((ra_h - h) * 60); s = int(round(((ra_h - h) * 60 - m) * 60))
    if s == 60:
        s = 0; m += 1
    if m == 60:
        m = 0; h = (h + 1) % 24
    signo = "-" if dec_deg < 0 else "+"
    d = abs(float(dec_deg)); dd = int(d); dm = int((d - dd) * 60); ds = int(round(((d - dd) * 60 - dm) * 60))
    if ds == 60:
        ds = 0; dm += 1
    if dm == 60:
        dm = 0; dd += 1
    return f"{h:02d} {m:02d} {s:02d}", f"{signo}{dd:02d} {dm:02d} {ds:02d}"


def parse_ra_dec(ra_str, dec_str):
    """Inverso de formatear_ra_dec. Devuelve (ra_horas, dec_grados)."""
    p = ra_str.strip().split()
    if len(p) != 3:
        raise ValueError(f"RA debe tener 3 componentes: {ra_str}")
    h, m, s = (float(x) for x in p)
    ra = h + m / 60 + s / 3600
    q = dec_str.strip().split()
    if len(q) != 3:
        raise ValueError(f"DEC debe tener 3 componentes: {dec_str}")
    signo = -1 if q[0].startswith('-') else 1
    d = float(q[0].lstrip('+-')); dm, ds = float(q[1]), float(q[2])
    return ra, signo * (d + dm / 60 + ds / 3600)


def formatear_mag(mag):
    """Magnitud con un decimal truncado, con punto (los CSV antiguos pueden traer coma)."""
    if mag is None:
        return ""
    return f"{math.trunc(float(mag) * 10) / 10:.1f}"


def leer_mag(texto):
    texto = (texto or "").strip().replace(",", ".")
    try:
        return float(texto) if texto else None
    except ValueError:
        return None


def limpiar_refs(texto):
    return re.sub(r"\[\d+\]", "", texto or "").strip()


def nombre_iau(raw):
    """'orión' / 'Orion' / 'Ursa Major' -> nombre IAU usado como nombre de fichero."""
    clave = unidecode(raw.strip().lower())
    return CONST_SPA_IAU.get(clave) or unidecode(raw.strip()).title()


# ─────────────────────────────────────────────────────────────────────────────
#  Wikipedia con caché en info_objetos/
# ─────────────────────────────────────────────────────────────────────────────

def clave_y_slug(raw_objeto, reemplazar_numeros=None):
    """'M 13' -> ('m13', 'm13'): clave de búsqueda en Wikipedia y nombre del fichero de caché."""
    key = raw_objeto.strip().lower()
    key = re.sub(r'([a-z])\s+(?=\d)', r"\1", key)
    if reemplazar_numeros:
        key = reemplazar_numeros(key)
    slug = re.sub(r"[^\w\- ]", "", key).replace(" ", "_")
    return key, slug


def ruta_cache_wiki(raw_objeto, reemplazar_numeros=None):
    return os.path.join(CARPETA_INFO, f"{clave_y_slug(raw_objeto, reemplazar_numeros)[1]}.txt")


def obten_resumen_wiki(raw_objeto, reemplazar_numeros=None):
    """
    Resumen en español de Wikipedia para `raw_objeto`, probando variantes y
    resolviendo ambigüedades. Se cachea en info_objetos/<slug>.txt para que
    funcione sin internet la siguiente vez. `reemplazar_numeros` (opcional)
    convierte "eme trece" en "m13".
    """
    os.makedirs(CARPETA_INFO, exist_ok=True)
    key, slug = clave_y_slug(raw_objeto, reemplazar_numeros)
    ruta = os.path.join(CARPETA_INFO, f"{slug}.txt")
    if os.path.isfile(ruta):
        with open(ruta, encoding="utf-8") as f:
            return limpiar_refs(f.read())

    import wikipedia
    wikipedia.set_lang("es")
    resumen = None
    for nombre in (key, re.sub(r'([a-z])(?=\d)', r"\1 ", key)):
        try:
            wikipedia.page(nombre)
            resumen = wikipedia.summary(nombre)
        except wikipedia.exceptions.DisambiguationError as e:
            try:
                resumen = wikipedia.summary(e.options[0])
            except Exception:
                continue
        except wikipedia.exceptions.PageError:
            continue
        except Exception as e:
            log.warning("Wikipedia no disponible: %s", e)
            break
        if resumen:
            resumen = limpiar_refs(resumen)
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(resumen)
            return resumen
    return f"No se encontró información sobre «{raw_objeto}»."


# ─────────────────────────────────────────────────────────────────────────────
#  Recorrido de una constelación
# ─────────────────────────────────────────────────────────────────────────────

def cargar_csv(nombre_const):
    """Lee constelaciones/<IAU>.csv -> (descripcion, estrellas) o None si no existe."""
    ruta = os.path.join(CARPETA_CONSTELACIONES, f"{nombre_const}.csv")
    if not os.path.exists(ruta):
        return None
    descripcion, estrellas = [], []
    with open(ruta, newline='', encoding='utf-8') as f:
        while True:
            pos = f.tell()
            linea = f.readline()
            if not linea:
                break
            if linea.strip().lower().startswith("nombre;"):
                f.seek(pos)
                break
            descripcion.append(linea.split(";", 1)[1].rstrip() if linea.startswith("#desc;") else linea.rstrip())
        for row in csv.DictReader(f, delimiter=';'):
            try:
                ra_h, dec = parse_ra_dec(row['ra'], row['dec'])
            except (ValueError, KeyError) as e:
                log.warning("Fila inválida en %s: %s", ruta, e)
                continue
            estrellas.append({
                'nombre': row['nombre'], 'nombre_tecnico': row.get('nombre_tecnico', ''),
                'ra': ra_h, 'dec': dec, 'mag': leer_mag(row.get('mag')),
                'tipo': row.get('tipo', ''), 'infoExtra': row.get('infoExtra', ''),
            })
    return limpiar_refs(" ".join(descripcion)), estrellas


def guardar_csv(nombre_const, descripcion, estrellas):
    os.makedirs(CARPETA_CONSTELACIONES, exist_ok=True)
    ruta = os.path.join(CARPETA_CONSTELACIONES, f"{nombre_const}.csv")
    with open(ruta, "w", encoding='utf-8', newline='') as f:
        f.write(f"#desc;{descripcion}\n")
        w = csv.DictWriter(f, fieldnames=['nombre', 'nombre_tecnico', 'ra', 'dec', 'mag', 'tipo', 'infoExtra'], delimiter=';')
        w.writeheader()
        for e in estrellas:
            ra_s, dec_s = formatear_ra_dec(e['ra'], e['dec'])
            w.writerow({'nombre': e['nombre'], 'nombre_tecnico': e.get('nombre_tecnico', ''),
                        'ra': ra_s, 'dec': dec_s, 'mag': formatear_mag(e.get('mag')),
                        'tipo': e.get('tipo') or '', 'infoExtra': e.get('infoExtra', '')})
    return ruta


def ordenar_por_cercania(estrellas):
    """Recorrido que en cada paso salta al objeto más cercano al anterior."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    if not estrellas:
        return []
    recorrido, pendientes = [estrellas[0]], estrellas[1:]
    while pendientes:
        act = SkyCoord(ra=recorrido[-1]['ra'] * u.hourangle, dec=recorrido[-1]['dec'] * u.deg)
        sig = min(pendientes, key=lambda x: act.separation(SkyCoord(ra=x['ra'] * u.hourangle, dec=x['dec'] * u.deg)).deg)
        recorrido.append(sig)
        pendientes.remove(sig)
    return recorrido


def generar_desde_simbad(nombre_const, maximo_estrellas=10):
    """
    Estrellas brillantes (V<5) con nombre propio y objetos Messier de la
    constelación, vía SIMBAD (necesita internet). Devuelve lista de dicts.
    """
    from astroquery.simbad import Simbad
    from astropy.coordinates import SkyCoord, get_constellation
    import astropy.units as u

    simbad = Simbad()
    simbad.TIMEOUT = 30
    simbad.reset_votable_fields()
    simbad.add_votable_fields('otype', 'V')
    tbl = simbad.query_criteria("Vmag<5")
    if tbl is None:
        raise RuntimeError("SIMBAD no devolvió estrellas brillantes")

    def _s(v):
        return v.decode('utf-8') if isinstance(v, bytes) else str(v)

    tecnicos = []
    for row in tbl:
        if '*' not in _s(row['otype']):
            continue
        coord = SkyCoord(row['ra'], row['dec'], unit=(u.deg, u.deg))
        if get_constellation(coord) != nombre_const:
            continue
        try:
            mag = float(row['V'])
        except (TypeError, ValueError):
            continue
        tecnicos.append({'nombre_tecnico': _s(row['main_id']), 'ra': coord.ra.hour, 'dec': coord.dec.degree, 'mag': mag})
    tecnicos.sort(key=lambda x: x['mag'])
    tecnicos = tecnicos[:maximo_estrellas]

    estrellas = []
    ids = Simbad()
    ids.TIMEOUT = 15
    for e in tecnicos:
        limpio = re.sub(r'^[\*\s]+', '', e['nombre_tecnico'])
        comun = limpio
        try:
            ids_tbl = ids.query_objectids(limpio)
        except Exception:
            ids_tbl = None
        if ids_tbl is not None and len(ids_tbl) > 0:
            for alias in (_s(i) for i in ids_tbl['id']):
                if alias.upper().startswith('NAME '):
                    comun = alias.split(' ', 1)[1]
                    break
        if SOLO_CON_NOMBRE_COMUN and comun == limpio:
            continue
        estrellas.append({'nombre': comun, 'nombre_tecnico': e['nombre_tecnico'], 'ra': e['ra'],
                          'dec': e['dec'], 'mag': e['mag'], 'tipo': 'Estrella'})

    # Objetos Messier de la constelación
    mess = Simbad()
    mess.TIMEOUT = 60
    mess.ROW_LIMIT = 120
    mess.add_votable_fields('ra', 'dec', 'otype', 'V')
    try:
        mess_tab = mess.query_objects([f"M{i}" for i in range(1, 111)])
    except Exception as e:
        log.warning("SIMBAD Messier no disponible: %s", e)
        mess_tab = None
    if mess_tab is not None:
        import numpy.ma as ma
        for row in mess_tab:
            try:
                coord = SkyCoord(float(row['ra']) * u.deg, float(row['dec']) * u.deg)
            except (TypeError, ValueError):
                continue
            if nombre_const not in (get_constellation(coord, short_name=False), get_constellation(coord, short_name=True)):
                continue
            mag = None
            if 'V' in row.colnames and not ma.is_masked(row['V']):
                try:
                    mag = float(row['V'])
                except (TypeError, ValueError):
                    mag = None
            mid = _s(row['main_id'])
            estrellas.append({'nombre': mid, 'nombre_tecnico': mid, 'ra': coord.ra.hour,
                              'dec': coord.dec.degree, 'mag': mag, 'tipo': _s(row['otype'])})
    return estrellas


def recorrido_constelacion(raw_nombre, reemplazar_numeros=None):
    """
    Devuelve (descripcion, estrellas) para el nombre en español. Usa el CSV si
    existe; si no, lo genera con SIMBAD + Wikipedia y lo guarda.
    """
    nombre_const = nombre_iau(raw_nombre)
    cache = cargar_csv(nombre_const)
    if cache is not None:
        return cache
    estrellas = generar_desde_simbad(nombre_const)
    if not estrellas:
        raise RuntimeError(f"No se encontraron estrellas con nombre ni objetos Messier en '{nombre_const}'")
    recorrido = ordenar_por_cercania(estrellas)
    descripcion = limpiar_refs(obten_resumen_wiki(f"{raw_nombre} (constelación)", reemplazar_numeros))
    for e in recorrido:
        e.setdefault('infoExtra', '')
    guardar_csv(nombre_const, descripcion, recorrido)
    return descripcion, recorrido


# ─────────────────────────────────────────────────────────────────────────────
#  Recorridos guiados de varias constelaciones (constelaciones/*.txt)
# ─────────────────────────────────────────────────────────────────────────────

def listar_recorridos_txt():
    os.makedirs(CARPETA_CONSTELACIONES, exist_ok=True)
    return sorted(f for f in os.listdir(CARPETA_CONSTELACIONES) if f.lower().endswith('.txt'))


def leer_recorrido_txt(nombre_fichero):
    """
    Formato:  #descripcion: texto...  (varias líneas)
              #lista: Orion; Tauro; Geminis
    Devuelve (descripcion, lista) o None si no existe.
    """
    ruta = os.path.join(CARPETA_CONSTELACIONES, os.path.basename(nombre_fichero))
    if not os.path.exists(ruta):
        return None
    descripcion, lista = [], []
    modo = None
    with open(ruta, encoding='utf-8') as f:
        for raw in f:
            linea = raw.rstrip('\n')
            if linea.startswith("#descripcion:"):
                modo = "d"; descripcion.append(linea[len("#descripcion:"):].strip())
            elif linea.startswith("#lista:"):
                modo = "l"; lista += [x.strip() for x in linea[len("#lista:"):].split(";") if x.strip()]
            elif modo == "d":
                descripcion.append(linea.strip())
            elif modo == "l" and linea.strip():
                lista += [x.strip() for x in linea.split(";") if x.strip()]
    return limpiar_refs(" ".join(descripcion)), lista
