"""
herramientas/traducciones.py — catálogo de traducciones de ScopePower (ver idioma.py).

El español es el idioma original: los textos se escriben en el código dentro de _() y aquí
se recogen en traducciones/scopepower.pot y en el .po de cada idioma, que se traduce con
cualquier editor de texto o con Poedit.

    python herramientas/traducciones.py extraer
        Busca los textos _(), ngettext() y N_() en los .py, en static/**.js y en las plantillas
        (también dentro de sus <script> y de los onclick="..."). Actualiza el .pot y los .po:
        lo ya traducido se conserva y los textos nuevos quedan vacíos.
    python herramientas/traducciones.py compilar
        .po -> traducciones/<idioma>/LC_MESSAGES/scopepower.mo (Python y plantillas)
        y static/i18n/<idioma>.js (lo que usa el JavaScript de las páginas).
    python herramientas/traducciones.py pendientes [idioma]
        Textos sin traducir o dudosos (fuzzy), con el fichero donde aparecen.
    python herramientas/traducciones.py importar fichero.json [idioma]
        Añade traducciones desde un JSON {"texto original": "traducción"}; para un plural,
        la clave es el singular original y el valor ["singular", "plural"].

Hace falta Babel (pip install babel) solo para estas tareas; la app no lo necesita.
Después de cambiar textos: extraer, traducir, compilar, y subir .po, .mo y static/i18n/*.js.
"""

import io
import json
import os
import re
import sys
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA = os.path.join(RAIZ, "traducciones")
DOMINIO = "scopepower"
IDIOMAS = ("en",)
POT = os.path.join(CARPETA, DOMINIO + ".pot")
CARPETA_JS = os.path.join(RAIZ, "static", "i18n")
FECHA_FIJA = datetime(2026, 10, 15)    # cabecera estable: que extraer no cambie el .po sin motivo

# Palabras clave: _("texto"), gettext("texto"), ngettext("singular", "plural", n), N_("texto")
PALABRAS = {"_": None, "gettext": None, "ngettext": (1, 2), "N_": None}
MARCA_JS = "js"    # comentario automático: el texto también lo usa el navegador


def ruta_po(idioma):
    return os.path.join(CARPETA, idioma, "LC_MESSAGES", DOMINIO + ".po")


def ruta_mo(idioma):
    return os.path.join(CARPETA, idioma, "LC_MESSAGES", DOMINIO + ".mo")


def ruta_js(idioma):
    return os.path.join(CARPETA_JS, idioma + ".js")


# ── Extractores ───────────────────────────────────────────────────────────
def extraer_python(fileobj, keywords, comment_tags, options):
    """Como el de Babel, pero los N_() (constantes que se pintan en las páginas) van también al JS."""
    from babel.messages.extract import extract_python
    for lineno, funcion, mensajes, comentarios in extract_python(fileobj, keywords, comment_tags, options):
        if funcion == "N_":
            comentarios = list(comentarios) + [MARCA_JS]
        yield lineno, funcion, mensajes, comentarios


_RE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_RE_EVENTO = re.compile(r"\son[a-z]+\s*=\s*\"([^\"]*)\"", re.I)
_RE_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def _sin_jinja(texto):
    """Cambia las etiquetas Jinja por espacios (conservando los saltos de línea)."""
    return _RE_JINJA.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), texto)


# Sin JSX: con él, Babel toma el /> de una expresión regular (.replace(/>/g, ...)) por una
# etiqueta y se salta el resto del fichero
OPCIONES_JS = {"jsx": False}


def extraer_js(fileobj, keywords, comment_tags, options):
    from babel.messages.extract import extract_javascript
    yield from extract_javascript(fileobj, keywords, comment_tags, OPCIONES_JS)


def _extraer_js_de(codigo, desplazamiento, keywords, comment_tags):
    from babel.messages.extract import extract_javascript
    for lineno, funcion, mensajes, comentarios in extract_javascript(
            io.BytesIO(codigo.encode("utf-8")), keywords, comment_tags, OPCIONES_JS):
        yield lineno + desplazamiento, funcion, mensajes, list(comentarios) + [MARCA_JS]


def extraer_plantilla(fileobj, keywords, comment_tags, options):
    """Plantilla Jinja: sus {{ _() }} y el _() del JavaScript de sus <script> y onclick."""
    from jinja2.ext import babel_extract
    bruto = fileobj.read()
    yield from babel_extract(io.BytesIO(bruto), keywords, comment_tags,
                             {"extensions": "jinja2.ext.i18n", "silent": "false"})
    texto = bruto.decode("utf-8")
    for m in _RE_SCRIPT.finditer(texto):
        if re.search(r"\bsrc\s*=", m.group(1), re.I):
            continue
        desplazamiento = texto.count("\n", 0, m.start(2))
        yield from _extraer_js_de(_sin_jinja(m.group(2)), desplazamiento, keywords, comment_tags)
    for m in _RE_EVENTO.finditer(texto):
        desplazamiento = texto.count("\n", 0, m.start(1))
        codigo = _sin_jinja(m.group(1)).replace("&quot;", '"').replace("&#39;", "'")
        yield from _extraer_js_de(codigo, desplazamiento, keywords, comment_tags)


METODOS = [
    ("herramientas/**", "ignore"),
    ("static/atlas/**", "ignore"),
    ("static/i18n/**", "ignore"),
    ("static/js/NoSleep.js", "ignore"),
    ("docs/**", "ignore"),
    ("datos/**", "ignore"),
    ("capturas/**", "ignore"),
    ("logs/**", "ignore"),
    ("traducciones/**", "ignore"),
    ("**.py", extraer_python),
    ("static/**.js", extraer_js),
    ("templates/**.html", extraer_plantilla),
]
OPCIONES = {"**.py": {"encoding": "utf-8"}}


def catalogo_extraido():
    """Catálogo plantilla (.pot) con todos los textos que hay ahora en el código."""
    from babel.messages.catalog import Catalog
    from babel.messages.extract import extract_from_dir
    sys.path.insert(0, RAIZ)
    try:
        import version
        ver = version.VERSION
    except Exception:
        ver = ""
    plantilla = Catalog(project="ScopePower", version=ver, charset="utf-8", fuzzy=False,
                        creation_date=FECHA_FIJA, revision_date=FECHA_FIJA,
                        msgid_bugs_address="https://github.com/EnriqueJ67/ScopePower/issues")
    for fichero, lineno, mensaje, comentarios, contexto in extract_from_dir(
            RAIZ, METODOS, OPCIONES, PALABRAS, comment_tags=(), strip_comment_tags=False):
        fichero = fichero.replace(os.sep, "/")
        existente = plantilla.get(mensaje if isinstance(mensaje, str) else mensaje[0], contexto)
        if existente is not None and isinstance(mensaje, tuple) != existente.pluralizable:
            print(f"⚠️ {fichero}:{lineno}: «{existente.id if isinstance(existente.id, str) else existente.id[0]}» "
                  "aparece a la vez como texto simple y como plural; se queda como plural")
            if isinstance(mensaje, str):
                mensaje = existente.id
        comentarios = sorted(set(comentarios) | (set(existente.auto_comments) if existente else set()))
        plantilla.add(mensaje, None, [(fichero, lineno)], auto_comments=comentarios, context=contexto)
    return plantilla


def _escribir_po(ruta, catalogo):
    from babel.messages.pofile import write_po
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "wb") as f:
        write_po(f, catalogo, width=0, sort_by_file=True, ignore_obsolete=True,
                 include_lineno=False, include_previous=False)


def _leer_po(idioma):
    from babel.messages.pofile import read_po
    with open(ruta_po(idioma), "rb") as f:
        return read_po(f, locale=idioma)


def extraer():
    from babel.messages.catalog import Catalog
    plantilla = catalogo_extraido()
    _escribir_po(POT, plantilla)
    print(f"📄 {os.path.relpath(POT, RAIZ)}: {len(plantilla)} textos")
    for idioma in IDIOMAS:
        if os.path.exists(ruta_po(idioma)):
            cat = _leer_po(idioma)
        else:
            cat = Catalog(locale=idioma, project="ScopePower", charset="utf-8", fuzzy=False,
                          creation_date=FECHA_FIJA, revision_date=FECHA_FIJA)
        cat.update(plantilla, no_fuzzy_matching=False)
        _escribir_po(ruta_po(idioma), cat)
        faltan = sum(1 for m in cat if m.id and (m.fuzzy or not _traducido(m)))
        print(f"📄 {os.path.relpath(ruta_po(idioma), RAIZ)}: {len(cat)} textos, {faltan} pendientes")


def _traducido(m):
    if m.pluralizable:
        return isinstance(m.string, (list, tuple)) and all(m.string)
    return bool(m.string)


def _va_al_js(m):
    return MARCA_JS in m.auto_comments or any(loc[0].endswith(".js") for loc in m.locations)


def catalogo_js(cat):
    """Diccionario {original: traducción | [singular, plural]} con lo que necesita el navegador."""
    salida = {}
    for m in cat:
        if not m.id or m.fuzzy or not _traducido(m) or not _va_al_js(m):
            continue
        if m.pluralizable:
            salida[m.id[0]] = list(m.string)
        else:
            salida[m.id] = m.string
    return salida


def texto_js(diccionario):
    lineas = [json.dumps(k, ensure_ascii=False) + ":" + json.dumps(v, ensure_ascii=False)
              for k, v in sorted(diccionario.items())]
    return ("// Generado por herramientas/traducciones.py compilar a partir del .po: no editar a mano.\n"
            "window.I18N_CATALOGO = {\n" + ",\n".join(lineas) + "\n};\n")


def compilar():
    from babel.messages.mofile import write_mo
    for idioma in IDIOMAS:
        cat = _leer_po(idioma)
        with open(ruta_mo(idioma), "wb") as f:
            write_mo(f, cat, use_fuzzy=False)
        dic = catalogo_js(cat)
        os.makedirs(CARPETA_JS, exist_ok=True)
        with open(ruta_js(idioma), "w", encoding="utf-8", newline="\n") as f:
            f.write(texto_js(dic))
        hechos = sum(1 for m in cat if m.id and not m.fuzzy and _traducido(m))
        print(f"✅ {idioma}: {hechos}/{len(cat)} textos traducidos; {len(dic)} van al navegador "
              f"({os.path.getsize(ruta_js(idioma)) // 1024} KB)")


def pendientes(idioma="en"):
    cat = _leer_po(idioma)
    n = 0
    for m in cat:
        if not m.id or (_traducido(m) and not m.fuzzy):
            continue
        n += 1
        donde = ", ".join(sorted({loc[0] for loc in m.locations}))
        original = m.id if isinstance(m.id, str) else " | ".join(m.id)
        print(f"{'[fuzzy] ' if m.fuzzy else ''}{donde}: {original}")
    print(f"{n} pendientes en {idioma}")
    return n


def importar(ruta_json, idioma="en"):
    with open(ruta_json, encoding="utf-8") as f:
        datos = json.load(f)
    cat = _leer_po(idioma)
    puestos, desconocidos = 0, []
    for original, traduccion in datos.items():
        m = cat.get(original)
        if m is None:
            desconocidos.append(original)
            continue
        if m.pluralizable:
            if isinstance(traduccion, str):
                traduccion = [traduccion, traduccion]
            m.string = tuple(traduccion[:2])
        else:
            if isinstance(traduccion, (list, tuple)):
                traduccion = traduccion[0]
            m.string = traduccion
        m.flags.discard("fuzzy")
        puestos += 1
    _escribir_po(ruta_po(idioma), cat)
    print(f"✅ {puestos} traducciones importadas de {os.path.basename(ruta_json)}")
    for original in desconocidos[:40]:
        print(f"   ⚠️ no está en el catálogo (¿falta extraer o ha cambiado?): {original[:90]}")
    if len(desconocidos) > 40:
        print(f"   ... y {len(desconocidos) - 40} más")
    return desconocidos


def main(argv):
    if len(argv) < 2 or argv[1] not in ("extraer", "compilar", "pendientes", "importar"):
        print(__doc__)
        return 2
    orden = argv[1]
    if orden == "extraer":
        extraer()
    elif orden == "compilar":
        compilar()
    elif orden == "pendientes":
        pendientes(argv[2] if len(argv) > 2 else "en")
    elif orden == "importar":
        if len(argv) < 3:
            print("Falta el fichero JSON")
            return 2
        importar(argv[2], argv[3] if len(argv) > 3 else "en")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
