"""
herramientas/marcar_textos.py — ayuda para traducir: qué textos quedan sin envolver en _().

    python herramientas/marcar_textos.py listar FICHEROS...
        Textos que parecen frases para el usuario y aún no están dentro de _(), ngettext() o N_().
        Es una heurística: salen también rutas, selectores CSS o textos de log que no hay que tocar.
    python herramientas/marcar_textos.py textos FICHEROS...
        Los textos ya marcados en esos ficheros (lo que irá al catálogo), uno por línea.
    python herramientas/marcar_textos.py faltan TRADUCCIONES.json FICHEROS...
        Compara un JSON {"original": "traducción"} con los textos marcados en los ficheros:
        los que faltan por traducir y las claves del JSON que no aparecen (erratas).

Ficheros admitidos: .py, .js y plantillas .html (incluidos sus <script>). Ver idioma.py.
"""

import ast
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "herramientas"))

_RE_LETRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")
_RE_ESPANOL = re.compile(r"[ÁÉÍÓÚÜÑáéíóúüñ¿¡]")
FUNCIONES = {"_", "N_", "ngettext", "gettext"}
LOGS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def parece_frase(texto):
    """Heurística: texto con letras y con espacio o letras españolas, que no parece ruta ni código."""
    t = re.sub(r"<[^>]+>", " ", texto)          # sin etiquetas HTML
    t = re.sub(r"\$\{[^}]*\}|\{[^{}]*\}", " ", t)  # sin huecos de plantilla
    t = t.strip()
    if not _RE_LETRA.search(t):
        return False
    if not (" " in t or _RE_ESPANOL.search(t)):
        return False
    if re.fullmatch(r"[%\w\s:./\\-]*%[a-zA-Z][%\w\s:./\\-]*", t):       # strftime / %s
        return False
    if t.startswith(("/", "http", "#", ".", "\\")) and " " not in t:
        return False
    if re.fullmatch(r"[\w\s.#>:,\[\]=\"'()*-]+", t) and re.search(r"[#.\[][\w-]+", t) and not _RE_ESPANOL.search(t):
        return False                              # selector CSS
    return True


# ── Python ────────────────────────────────────────────────────────────────
def _nombre_funcion(llamada):
    f = llamada.func
    if isinstance(f, ast.Name):
        return f.id, None
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else (f.value.attr if isinstance(f.value, ast.Attribute) else "")
        return f.attr, base
    return None, None


def listar_python(ruta):
    fuente = open(ruta, encoding="utf-8-sig").read()
    arbol = ast.parse(fuente, ruta)
    padres = {}
    for nodo in ast.walk(arbol):
        for hijo in ast.iter_child_nodes(nodo):
            padres[hijo] = nodo
    docstrings = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.body:
            primero = nodo.body[0]
            if isinstance(primero, ast.Expr) and isinstance(primero.value, ast.Constant) and isinstance(primero.value.value, str):
                docstrings.add(primero.value)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            if isinstance(padres.get(nodo), ast.JoinedStr) or nodo in docstrings:
                continue
            texto = nodo.value
        elif isinstance(nodo, ast.JoinedStr):
            texto = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in nodo.values)
        else:
            continue
        if not parece_frase(texto):
            continue
        # Subir por los padres: ¿está dentro de _() o de un log/print?
        saltar, p, hijo = False, padres.get(nodo), nodo
        while p is not None:
            if isinstance(p, ast.Call):
                nombre, base = _nombre_funcion(p)
                if nombre in FUNCIONES and hijo in p.args:
                    saltar = True
                    break
                if nombre == "print" or (nombre in LOGS and base and ("log" in base.lower())):
                    saltar = True
                    break
                if nombre in ("compile", "match", "search", "sub", "fullmatch", "findall", "split") and base == "re":
                    saltar = True
                    break
            if isinstance(p, (ast.FunctionDef, ast.ClassDef, ast.Module)):
                break
            hijo, p = p, padres.get(p)
        if not saltar:
            yield nodo.lineno, texto


# ── JavaScript y plantillas ───────────────────────────────────────────────
_RE_CADENA_JS = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`", re.S)
_ANTES_SIN_TRADUCIR = re.compile(r"(console\.\w+|getElementById|querySelector(All)?|addEventListener|fetch|"
                                 r"classList\.\w+|setAttribute|getAttribute|removeAttribute|getItem|setItem|"
                                 r"removeItem|closest|matches|matchMedia|createElement)\s*\(\s*$")


def _linea(texto, pos):
    return texto.count("\n", 0, pos) + 1


def _cadenas_js(codigo, desplazamiento=0):
    for m in _RE_CADENA_JS.finditer(codigo):
        crudo = m.group()
        contenido = crudo[1:-1]
        antes = codigo[max(0, m.start() - 60):m.start()]
        if re.search(r"(?<![\w$.])(_|N_|ngettext)\(\s*$", antes):
            continue
        if re.search(r"(?<![\w$.])ngettext\(\s*(\"[^\"]*\"|'[^']*')\s*,\s*$", antes):
            continue
        if _ANTES_SIN_TRADUCIR.search(antes):
            continue
        linea_antes = antes.split("\n")[-1]
        if "console." in linea_antes or re.search(r"\b(href|src|className|type|method|id)\s*[:=]\s*$", linea_antes):
            continue
        if parece_frase(contenido):
            yield _linea(codigo, m.start()) + desplazamiento, contenido


def listar_js(ruta):
    yield from _cadenas_js(open(ruta, encoding="utf-8-sig").read())


_RE_SCRIPT = re.compile(r"(<script\b[^>]*>)(.*?)</script>", re.S | re.I)
_RE_ESTILO = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)
_RE_COMENTARIO = re.compile(r"<!--.*?-->", re.S)
_RE_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
_RE_ATRIBUTO = re.compile(r"\b(title|placeholder|aria-label|alt|label|data-titulo|data-texto)\s*=\s*\"([^\"]*)\"", re.I)
_RE_EVENTO = re.compile(r"\son[a-z]+\s*=\s*\"([^\"]*)\"", re.I)


def _en_blanco(m):
    return re.sub(r"[^\n]", " ", m.group())


def listar_plantilla(ruta):
    texto = open(ruta, encoding="utf-8-sig").read()
    for m in _RE_SCRIPT.finditer(texto):
        if re.search(r"\bsrc\s*=", m.group(1), re.I):
            continue
        desplazamiento = _linea(texto, m.start(2)) - 1
        yield from _cadenas_js(_RE_JINJA.sub(_en_blanco, m.group(2)), desplazamiento)
    for m in _RE_EVENTO.finditer(texto):
        for linea, cadena in _cadenas_js(_RE_JINJA.sub(_en_blanco, m.group(1)).replace("&quot;", '"')):
            yield _linea(texto, m.start(1)) + linea - 1, cadena
    html = _RE_SCRIPT.sub(_en_blanco, texto)
    html = _RE_ESTILO.sub(_en_blanco, html)
    html = _RE_COMENTARIO.sub(_en_blanco, html)
    html = _RE_JINJA.sub(_en_blanco, html)
    for m in re.finditer(r">([^<>]+)<", html):
        contenido = re.sub(r"&\w+;|&#\d+;", " ", m.group(1)).strip()
        if contenido and _RE_LETRA.search(contenido):
            yield _linea(html, m.start(1) + len(m.group(1)) - len(m.group(1).lstrip())), contenido
    for m in _RE_ATRIBUTO.finditer(html):
        contenido = m.group(2).strip()
        if contenido and _RE_LETRA.search(contenido):
            yield _linea(html, m.start()), f"[{m.group(1)}] {contenido}"


def listar(ruta):
    if ruta.endswith(".py"):
        return listar_python(ruta)
    if ruta.endswith(".js"):
        return listar_js(ruta)
    if ruta.endswith(".html"):
        return listar_plantilla(ruta)
    return iter(())


# ── Textos marcados ───────────────────────────────────────────────────────
def textos_marcados(ruta):
    """{original: es_plural} de los textos marcados para traducir en el fichero."""
    from babel.messages.extract import extract_from_file
    import traducciones as T
    if ruta.endswith(".py"):
        metodo = T.extraer_python
    elif ruta.endswith(".js"):
        metodo = T.extraer_js
    elif ruta.endswith(".html"):
        metodo = T.extraer_plantilla
    else:
        return {}
    salida = {}
    for lineno, mensaje, comentarios, contexto in extract_from_file(metodo, ruta, T.PALABRAS, (), {}):
        if isinstance(mensaje, tuple):
            salida[mensaje[0]] = True
        else:
            salida.setdefault(mensaje, False)
    return salida


def main(argv):
    if len(argv) < 3 or argv[1] not in ("listar", "textos", "faltan"):
        print(__doc__)
        return 2
    orden = argv[1]
    if orden == "listar":
        total = 0
        for ruta in argv[2:]:
            for linea, texto in sorted(listar(ruta)):
                total += 1
                print(f"{ruta}:{linea}: {texto[:120]}")
        print(f"— {total} textos candidatos sin marcar")
    elif orden == "textos":
        for ruta in argv[2:]:
            for texto in textos_marcados(ruta):
                print(texto)
    elif orden == "faltan":
        with open(argv[2], encoding="utf-8") as f:
            datos = json.load(f)
        marcados = {}
        for ruta in argv[3:]:
            marcados.update(textos_marcados(ruta))
        faltan = [t for t in marcados if t not in datos or not datos[t]]
        sobran = [t for t in datos if t not in marcados]
        mal_plural = [t for t, plural in marcados.items() if plural and t in datos and not isinstance(datos[t], list)]
        for t in faltan:
            print("FALTA  " + t)
        for t in sobran:
            print("SOBRA  " + t)
        for t in mal_plural:
            print("PLURAL " + t + "   (el valor debe ser [\"singular\", \"plural\"])")
        print(f"— {len(marcados)} textos marcados, {len(faltan)} sin traducir, {len(sobran)} claves de más, "
              f"{len(mal_plural)} plurales mal")
        return 1 if (faltan or sobran or mal_plural) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
