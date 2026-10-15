# catalogo_offline.py
#
# Resolución de nombres de objetos sin Internet ni Stellarium, con los catálogos
# que ya lleva el planetario en static/atlas (los mismos ficheros que carga el
# navegador, así el PC y la tablet ven exactamente el mismo cielo):
#
#   dso-catalog.js           OpenNGC + Messier (12 579 objetos, alias y nombres comunes)
#   hyg-star-catalog.js      estrellas HYG hasta magnitud ~6,5 (HIP nnn, nombres propios)
#   estrellas-brillantes.js  las 130 estrellas con nombre propio que HYG excluye
#
# A eso se suman los nombres en español de catalogo_objetos.csv (Esta noche) y la
# tabla alias_objetos.csv (nombres en español que no están en ningún catálogo:
# "nebulosa del velo", "estrella polar", "Sirio"...).
#
# Uso:
#     import catalogo_offline
#     ficha = catalogo_offline.buscar("M31")        # o "galaxia de andromeda", "NGC7000", "vega"
#     ficha["ra"] (horas J2000), ficha["dec"] (grados), ficha["name"], ficha["type"], ...
#
# El nombre se busca sin tildes, sin espacios ni signos y sin distinguir mayúsculas,
# que es como llega desde la pantalla GoTo (escrito o dictado): "M 31", "m31",
# "Messier 31" y "eme treinta y uno" acaban en la misma clave.

import os
import re
import csv
import json
import logging
import threading

from unidecode import unidecode

import idioma
from idioma import N_

log = logging.getLogger("scopepower.catalogo")

_RAIZ = os.path.dirname(os.path.abspath(__file__))
RUTA_ATLAS = os.path.join(_RAIZ, "static", "atlas")
RUTA_CATALOGO_ES = os.path.join(_RAIZ, "catalogo_objetos.csv")
RUTA_ALIAS = os.path.join(_RAIZ, "alias_objetos.csv")
RUTA_CATALOGO_EN = os.path.join(_RAIZ, "catalogo_objetos_en.csv")

FUENTE_DSO = N_("Catálogo local (OpenNGC, sin Internet)")
FUENTE_ESTRELLAS = N_("Catálogo local (estrellas HYG, sin Internet)")

# Tipos de OpenNGC / HYG en español (van a la ficha, al rótulo y a la voz). El valor en
# español es la clave que se guarda y se compara; se traduce solo al pintarlo: _(tipo)
TIPOS_ES = {
    "Galaxy": N_("Galaxia"), "Galaxy pair": N_("Par de galaxias"), "Galaxy triplet": N_("Triplete de galaxias"),
    "Galaxy group": N_("Grupo de galaxias"), "Open cluster": N_("Cúmulo abierto"),
    "Globular cluster": N_("Cúmulo globular"), "Cluster with nebulosity": N_("Cúmulo con nebulosa"),
    "Stellar association": N_("Asociación estelar"), "Planetary nebula": N_("Nebulosa planetaria"),
    "Nebula": N_("Nebulosa"), "Emission nebula": N_("Nebulosa de emisión"), "Reflection nebula": N_("Nebulosa de reflexión"),
    "Dark nebula": N_("Nebulosa oscura"), "H II region": N_("Región H II"), "Supernova remnant": N_("Resto de supernova"),
    "Deep-sky object": N_("Objeto de cielo profundo"), "Double star": N_("Estrella doble"), "Star": N_("Estrella"),
}

# Prefijos escritos con palabra entera -> abreviatura del catálogo
_PREFIJOS = (("messier", "m"), ("caldwell", "c"), ("barnard", "b"), ("melotte", "mel"),
             ("collinder", "cr"), ("sharpless", "sh"), ("hipparcos", "hip"))

# Letras griegas como se dicen en español -> como están en los catálogos
_GRIEGO_ES = {"alfa": "alpha", "fi": "phi", "ji": "chi", "ipsilon": "upsilon", "omicron": "omicron"}

_lock = threading.Lock()
_indice = None          # clave normalizada -> ficha
_fichas = None          # lista de fichas únicas (para buscar por patrón)
_resumen = {"objetos": 0, "estrellas": 0}


# ── Normalización ───────────────────────────────────────────────────────────
def clave(texto):
    """'Galaxia de Andrómeda' -> 'galaxiadeandromeda'; 'M 31' -> 'm31'; 'NGC-7000' -> 'ngc7000'."""
    k = re.sub(r"[^a-z0-9]", "", unidecode(str(texto or "")).lower())
    # Letras dictadas por voz: "eme 31" -> m31, "ene ge ce 7000" -> ngc7000, "i ce 434" -> ic434
    k = re.sub(r"^eme(?=\d)", "m", k)
    k = re.sub(r"^enegece(?=\d)", "ngc", k)
    k = re.sub(r"^ice(?=\d)", "ic", k)
    for largo, corto in _PREFIJOS:
        if k.startswith(largo):
            k = corto + k[len(largo):]
            break
    return k


def _variantes(texto):
    """Claves alternativas de un identificador: 'B033' -> b033 y b33; 'NGC 7000' -> ngc7000 y ngc07000."""
    k = clave(texto)
    formas = {k}
    m = re.match(r"^([a-z]+)0*(\d+)(.*)$", k)
    if m:
        letras, numero, resto = m.groups()
        formas.add(f"{letras}{int(numero)}{resto}")
        formas.add(f"{letras}{int(numero):04d}{resto}")
    for es, cat in _GRIEGO_ES.items():
        if k.startswith(es) and not k.startswith(cat):
            formas.add(cat + k[len(es):])
        if k.startswith(cat):
            formas.add(es + k[len(cat):])
    return formas


# ── Carga ───────────────────────────────────────────────────────────────────
def _leer_js(nombre, variable):
    """Lee el JSON asignado a window.<variable> en un fichero JS del atlas."""
    ruta = os.path.join(RUTA_ATLAS, nombre)
    with open(ruta, encoding="utf-8") as f:
        texto = f.read()
    marca = f"window.{variable}="
    inicio = texto.index(marca) + len(marca)
    datos, __ = json.JSONDecoder().raw_decode(texto, inicio)
    return datos


def _ficha_dso(d):
    tipo = d.get("type") or ""
    return {
        "id": d["id"], "name": d.get("name") or d["id"], "name_es": "",
        "ra": float(d["raDeg"]) / 15.0, "dec": float(d["decDeg"]),
        "mag": d.get("mag"), "type": TIPOS_ES.get(tipo, tipo), "type_en": tipo,
        "aliases": list(d.get("aliases") or []), "constellation": "",
        "fuente": FUENTE_DSO,
    }


def _ficha_estrella(e, alias=""):
    return {
        "id": e.get("id") or e["name"], "name": e["name"], "name_es": "",
        "ra": float(e["ra"]), "dec": float(e["dec"]),
        "mag": e.get("mag"), "type": "Estrella", "type_en": "Star",
        "aliases": [alias] if alias else [], "constellation": "",
        "fuente": FUENTE_ESTRELLAS,
    }


def _leer_csv(ruta):
    if not os.path.isfile(ruta):
        return []
    with open(ruta, encoding="utf-8") as f:
        lineas = [l for l in f if l.strip() and not l.startswith("#")]
    return list(csv.DictReader(lineas, delimiter=";"))


def _construir():
    indice = {}
    fichas = []

    def registrar(ficha, *nombres):
        fichas.append(ficha)
        for n in nombres:
            if not n:
                continue
            for k in _variantes(n):
                if k:
                    indice.setdefault(k, ficha)

    # 1) Cielo profundo: primero por identificador (M31, NGC 7000, C9, B033, Mel022...)
    dso = _leer_js("dso-catalog.js", "DSO_CATALOG_DATA")
    fichas_dso = [_ficha_dso(d) for d in dso]
    for f in fichas_dso:
        registrar(f, f["id"])
    # 2) Estrellas con nombre propio (Sirius, Vega...) y su designación Bayer ("Alpha Lyrae")
    n_estrellas = 0
    for e in _leer_js("estrellas-brillantes.js", "STAR_DATA"):
        registrar(_ficha_estrella(e, e.get("alias", "")), e["name"], e.get("alias", ""))
        n_estrellas += 1
    hyg = _leer_js("hyg-star-catalog.js", "HYG_STAR_DATA")
    for e in hyg:
        if e.get("named"):
            registrar(_ficha_estrella(e), e["name"], e.get("id"))
            n_estrellas += 1
    # 3) Nombres comunes y alias de cielo profundo (Andromeda Galaxy, NGC 224 de M31, Pleiades...)
    for f in fichas_dso:
        for n in [f["name"]] + f["aliases"]:
            for k in _variantes(n):
                if k:
                    indice.setdefault(k, f)
    # 4) El resto de estrellas HYG por su identificador HIP
    for e in hyg:
        if not e.get("named"):
            ficha = _ficha_estrella(e)
            for k in _variantes(e.get("id") or ""):
                indice.setdefault(k, ficha)
            n_estrellas += 1

    # 5) Nombres en español: catalogo_objetos.csv (Esta noche) y alias_objetos.csv.
    #    Mandan sobre todo lo anterior (setdefault no pisa: se asignan directamente).
    def enlazar(nombre_es, id_objeto, ra_h=None, dec_deg=None, tipo="", mag=None):
        ficha = None
        for k in _variantes(id_objeto):
            if k in indice:
                ficha = indice[k]
                break
        if ficha is None:
            if ra_h is None or dec_deg is None:
                log.warning("Alias «%s» apunta a «%s», que no está en los catálogos", nombre_es, id_objeto)
                return
            ficha = {"id": id_objeto, "name": nombre_es, "name_es": nombre_es, "ra": float(ra_h),
                     "dec": float(dec_deg), "mag": mag, "type": tipo, "type_en": tipo, "aliases": [],
                     "constellation": "", "fuente": N_("Catálogo de ScopePower")}
            fichas.append(ficha)
        if not ficha["name_es"]:
            ficha["name_es"] = nombre_es
        for k in _variantes(nombre_es):
            if k:
                indice[k] = ficha

    for r in _leer_csv(RUTA_CATALOGO_ES):
        try:
            enlazar(r["nombre"], r["id"], float(r["ra_h"]), float(r["dec_deg"]), r.get("tipo", ""),
                    float(r["mag"]) if r.get("mag") else None)
            # El nombre entre paréntesis ("Cabeza de Caballo (Barnard 33)") también sin él
            sin_parentesis = re.sub(r"\s*\(.*?\)\s*", " ", r["nombre"]).strip()
            if sin_parentesis and sin_parentesis != r["nombre"]:
                enlazar(sin_parentesis, r["id"], float(r["ra_h"]), float(r["dec_deg"]))
        except (KeyError, ValueError) as e:
            log.warning("Fila de catalogo_objetos.csv ignorada (%s): %s", e, r)
    for r in _leer_csv(RUTA_ALIAS):
        if r.get("nombre") and r.get("id"):
            enlazar(r["nombre"].strip(), r["id"].strip())

    # 6) Nombres en inglés del catálogo propio (catalogo_objetos_en.csv): se buscan en los dos
    #    idiomas y son los que se enseñan con la app en inglés (name_en)
    for r in _leer_csv(RUTA_CATALOGO_EN):
        nombre_en, id_objeto = (r.get("nombre") or "").strip(), (r.get("id") or "").strip()
        ficha = next((indice[k] for k in _variantes(id_objeto) if k in indice), None)
        if not nombre_en or ficha is None:
            continue
        ficha["name_en"] = nombre_en
        sin_parentesis = re.sub(r"\s*\(.*?\)\s*", " ", nombre_en).strip()
        for n in {nombre_en, sin_parentesis}:
            for k in _variantes(n):
                if k:
                    indice.setdefault(k, ficha)

    # Textos normalizados por los que buscan las sugerencias (una sola vez, no por tecla)
    for f in fichas:
        f["_busqueda"] = [_normalizar_patron(n) for n in [f["id"], f["name"], f["name_es"], f.get("name_en", "")] + f["aliases"]
                          if n and not _alias_tecnico(n)]

    return indice, fichas, {"objetos": len(fichas_dso), "estrellas": n_estrellas}


def _cargar():
    global _indice, _fichas, _resumen
    if _indice is not None:
        return
    with _lock:
        if _indice is not None:
            return
        try:
            indice, fichas, resumen = _construir()
        except Exception as e:
            log.error("No se pudo cargar el catálogo local de static/atlas: %s", e)
            indice, fichas, resumen = {}, [], {"objetos": 0, "estrellas": 0}
        _indice, _fichas, _resumen = indice, fichas, resumen
        log.info("Catálogo local cargado: %d objetos de cielo profundo, %d estrellas, %d nombres",
                 resumen["objetos"], resumen["estrellas"], len(indice))


def precargar():
    """Carga el catálogo en segundo plano al arrancar (tarda menos de un segundo)."""
    threading.Thread(target=_cargar, name="catalogo-local", daemon=True).start()


def resumen():
    """{"objetos": n, "estrellas": m}; ceros si el catálogo no se ha podido cargar."""
    _cargar()
    return dict(_resumen)


# ── Consultas ───────────────────────────────────────────────────────────────
def _constelacion(ficha):
    """Nombre de la constelación (calculado con astropy, sin Internet)."""
    if ficha.get("constellation"):
        return ficha["constellation"]
    try:
        from astropy.coordinates import SkyCoord, get_constellation
        import astropy.units as u
        ficha["constellation"] = get_constellation(SkyCoord(ra=ficha["ra"] * u.hourangle, dec=ficha["dec"] * u.deg))
    except Exception:
        ficha["constellation"] = ""
    return ficha["constellation"]


def buscar(nombre):
    """
    Ficha del objeto o None. Misma forma que la que daba Stellarium para que la
    pantalla GoTo y guardar_info_goto() la usen sin cambios:
      ra (horas J2000), dec (grados), name, type, mag, constellation, id, fuente.
    """
    _cargar()
    k = clave(nombre)
    if not k:
        return None
    ficha = _indice.get(k)
    if ficha is None:
        for alt in _variantes(nombre):
            if alt in _indice:
                ficha = _indice[alt]
                break
    if ficha is None:
        return None
    return {
        "id": ficha["id"],
        "name": _nombre_visible(ficha),
        "name_catalogo": ficha["name"],
        "ra": ficha["ra"], "dec": ficha["dec"],
        "mag": ficha["mag"], "type": ficha["type"],
        "constellation": _constelacion(ficha),
        "fuente": ficha["fuente"],
    }


def _nombre_visible(ficha):
    """Nombre para enseñar: el del catálogo propio en el idioma de la app, o el del catálogo general."""
    if idioma.idioma_actual() != "es":
        return ficha.get("name_en") or ficha["name"]
    return ficha["name_es"] or ficha["name"]


def _normalizar_patron(texto):
    return re.sub(r"\s+", " ", unidecode(str(texto or "")).lower()).strip()


def buscar_patron(patron, maximo=15, excluir_ids=()):
    """
    Sugerencias: fichas cuyo identificador, nombre o alias contiene el patrón
    ('*' vale por cualquier texto, '?' por un carácter). Las que empiezan por el
    patrón van primero; dentro, las más brillantes. Devuelve fichas como buscar().
    """
    _cargar()
    p = _normalizar_patron(patron)
    if not p:
        return []
    if "*" in p or "?" in p:
        partes = [re.escape(x) for x in re.split(r"([*?])", p)]
        rx = re.compile("^" + "".join(".*" if x == r"\*" else "." if x == r"\?" else x for x in partes) + "$")
    else:
        rx = re.compile(re.escape(p))
    excluidos = {clave(i) for i in excluir_ids}
    candidatos = []
    for f in _fichas:
        if clave(f["id"]) in excluidos:
            continue
        mejor = None
        for nn in f["_busqueda"]:
            m = rx.search(nn)
            if m:
                orden = 0 if m.start() == 0 else 1
                if mejor is None or orden < mejor:
                    mejor = orden
        if mejor is not None:
            candidatos.append((mejor, f["mag"] is None, f["mag"] if f["mag"] is not None else 99, f))
    candidatos.sort(key=lambda t: t[:3])
    resultado = []
    for __, __, __, f in candidatos[:maximo]:
        resultado.append({
            "id": f["id"], "name": _nombre_visible(f), "name_catalogo": f["name"],
            "ra": f["ra"], "dec": f["dec"], "mag": f["mag"], "type": f["type"], "fuente": f["fuente"],
        })
    return resultado


_PREFIJOS_TECNICOS = ("2MASX", "IRAS", "MCG", "PGC", "UGC", "ESO", "MWSC", "LEDA", "CGCG", "KUG", "PN G", "LBN", "Cl ")


def _alias_tecnico(alias):
    return any(alias.startswith(p) for p in _PREFIJOS_TECNICOS)


# ── Tipo de objeto para la galería ──────────────────────────────────────────
# Las fichas llegan con el tipo en español (catálogo local, plan de la noche), en
# inglés (Stellarium: "galaxy", "open star cluster", "Planet"...) o sin tipo (Sol,
# Luna y planetas por efemérides). La galería filtra por unas pocas familias, así
# que aquí se reduce cualquier tipo, y si no hay tipo el propio nombre, a una de
# ellas. Devuelve "" si no se reconoce nada.
# (en español: son claves; se traducen al pintarlas, _(categoria))
CATEGORIAS = (N_("Galaxia"), N_("Nebulosa"), N_("Nebulosa planetaria"), N_("Resto de supernova"), N_("Cúmulo abierto"),
              N_("Cúmulo globular"), N_("Estrella"), N_("Planeta"), N_("Luna"), N_("Sol"), N_("Cometa"),
              N_("Asteroide"), N_("Meteoro"), N_("Satélite artificial"), N_("Avión"), N_("Paisaje"))

_PLANETAS = ("mercurio", "mercury", "venus", "marte", "mars", "jupiter", "saturno", "saturn",
             "urano", "uranus", "neptuno", "neptune", "pluton", "pluto")


def _categoria_de_texto(texto, es_nombre=False):
    t = unidecode(str(texto or "")).lower()
    if not t.strip():
        return ""
    if "supernova" in t:
        return "Resto de supernova"
    if "planetar" in t and "nebul" in t:
        return "Nebulosa planetaria"
    if "galax" in t:
        return "Galaxia"
    if "globular" in t:
        return "Cúmulo globular"
    if "abierto" in t or "open" in t or "asociacion" in t or "association" in t or "nebulosity" in t:
        return "Cúmulo abierto"
    if "cumulo" in t or "cluster" in t:
        # Un tipo genérico ("star cluster" de Stellarium) se da por abierto; en un
        # nombre ("Gran Cúmulo de Hércules" es globular) no se adivina
        return "" if es_nombre else "Cúmulo abierto"
    if "nebul" in t or "h ii" in t or "hii" in t:
        return "Nebulosa"
    if "cometa" in t or "comet" in t:
        return "Cometa"
    if "asteroid" in t or "minor planet" in t:
        return "Asteroide"
    if "paisaje" in t or "landscape" in t:
        return "Paisaje"
    # Fotos sacadas de los rastros guardados (rastros.py)
    if "meteor" in t:
        return "Meteoro"
    if "satelite artificial" in t or "artificial satellite" in t:
        return "Satélite artificial"
    palabras = set(re.findall(r"[a-z]+", t))
    if palabras & {"avion", "aircraft", "airplane"}:
        return "Avión"
    if palabras & {"sol", "sun"}:
        return "Sol"
    if palabras & {"luna", "moon"}:
        return "Luna"
    if "planet" in t or palabras & set(_PLANETAS):
        return "Planeta"
    if "estrella" in t or "star" in t or "stellar" in t:
        return "Estrella"
    return ""


def categoria_objeto(tipo="", nombre=""):
    """
    Familia de la galería a partir del tipo de la ficha y, si no basta, del nombre
    ('Sol', 'Luna', 'Júpiter', 'Whirlpool Galaxy'...) o del catálogo local.
    """
    cat = _categoria_de_texto(tipo)
    if cat or not nombre:
        return cat
    # Primero el catálogo (sabe que el Gran Cúmulo de Hércules es globular) y solo
    # después lo que diga el propio nombre
    try:
        ficha = buscar(nombre)
    except Exception:
        ficha = None
    cat = _categoria_de_texto(ficha["type"]) if ficha else ""
    return cat or _categoria_de_texto(nombre, es_nombre=True)
