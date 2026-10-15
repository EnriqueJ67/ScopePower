"""
herramientas/probar_traducciones.py — comprobaciones del idioma inglés sin equipo (ver idioma.py).

    python herramientas/probar_traducciones.py            todas las comprobaciones
    python herramientas/probar_traducciones.py --parcial  sin exigir que todo esté traducido

Comprueba:
  1. El .po no tiene textos sin traducir ni dudosos (fuzzy)              (no con --parcial)
  2. Los marcadores {nombre} de cada traducción coinciden con los del original
  3. El .mo y static/i18n/en.js están compilados con el .po actual
  4. frases_humor_en.csv tiene los mismos eventos que frases_humor.csv y marcadores válidos
  5. Los ficheros de datos _en (descripciones, catálogo, constelaciones) corresponden a uno original
  6. idioma.py traduce, vuelve al español y da formatos de número y fecha correctos
  7. Ningún texto del código falta en el .po (hay que pasar «traducciones.py extraer»)
  8. Los módulos que importan _ no lo usan además como variable, ni meten f-strings en _()
  9. El JavaScript no usa _ como parámetro ni plantillas `...` o concatenaciones dentro de _()

Termina con código 1 si algo falla. Necesita Babel para 1, 3 y 7 (pip install babel).
"""

import ast
import csv
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "herramientas"))
os.chdir(RAIZ)

PARCIAL = "--parcial" in sys.argv
fallos = []
avisos = []


def fallo(texto):
    fallos.append(texto)
    print("   ❌ " + texto)


def aviso(texto):
    avisos.append(texto)
    print("   ⚠️ " + texto)


_RE_MARCADOR = re.compile(r"\{(\w+)(?:[:!][^{}]*)?\}")


def marcadores(texto):
    return set(_RE_MARCADOR.findall(texto or ""))


def _corto(texto, n=70):
    texto = texto if isinstance(texto, str) else texto[0]
    return texto if len(texto) <= n else texto[:n] + "…"


try:
    import babel  # noqa: F401
    import traducciones as T
    HAY_BABEL = True
except ImportError:
    HAY_BABEL = False
    print("⚠️ Babel no está instalado: se saltan las comprobaciones 1, 3 y 7 (pip install babel)")

cat = None
if HAY_BABEL and os.path.exists(T.ruta_po("en")):
    cat = T._leer_po("en")

# ── 1 y 2. El .po ──────────────────────────────────────────────────────────
print("1-2. Catálogo traducciones/en/LC_MESSAGES/scopepower.po")
if cat is None:
    if HAY_BABEL:
        fallo("No existe el .po de inglés")
else:
    pendientes = [m for m in cat if m.id and (m.fuzzy or not T._traducido(m))]
    if pendientes and not PARCIAL:
        fallo(f"{len(pendientes)} textos sin traducir o dudosos (python herramientas/traducciones.py pendientes)")
        for m in pendientes[:10]:
            print("        · " + _corto(m.id))
    elif pendientes:
        aviso(f"{len(pendientes)} textos sin traducir todavía (--parcial)")
    for m in cat:
        if not m.id or not T._traducido(m):
            continue
        if m.pluralizable:
            orig = [marcadores(m.id[0]), marcadores(m.id[1])]
            for i, trad in enumerate(m.string[:2]):
                esperados = orig[min(i, 1)] | (orig[1] if i else set())
                encontrados = marcadores(trad)
                # el singular inglés puede decir "one photo" sin {n}
                if encontrados - (orig[0] | orig[1]) or (esperados - encontrados - {"n"}):
                    fallo(f"Marcadores distintos en el plural «{_corto(m.id)}»: {sorted(encontrados)}")
        else:
            if marcadores(m.id) != marcadores(m.string):
                fallo(f"Marcadores distintos en «{_corto(m.id)}»: original {sorted(marcadores(m.id))}, "
                      f"traducción {sorted(marcadores(m.string))}")
        ids = m.id if isinstance(m.id, tuple) else (m.id,)
        for texto in ids:
            if re.search(r"%\(|%s\b|%d\b", texto):
                fallo(f"Marcador con % en «{_corto(texto)}»: usar {{nombre}}")

# ── 3. Compilados al día ──────────────────────────────────────────────────
print("3. Compilados (.mo y static/i18n/en.js)")
if cat is not None:
    import gettext
    from babel.messages.mofile import write_mo
    if not os.path.exists(T.ruta_mo("en")):
        fallo("Falta el .mo: python herramientas/traducciones.py compilar")
    else:
        buf = io.BytesIO()
        write_mo(buf, cat, use_fuzzy=False)
        buf.seek(0)
        nuevo = dict(gettext.GNUTranslations(buf)._catalog)
        with open(T.ruta_mo("en"), "rb") as f:
            actual = dict(gettext.GNUTranslations(f)._catalog)
        nuevo.pop("", None)
        actual.pop("", None)
        if nuevo != actual:
            fallo("El .mo no corresponde al .po: python herramientas/traducciones.py compilar")
    if not os.path.exists(T.ruta_js("en")):
        fallo("Falta static/i18n/en.js: python herramientas/traducciones.py compilar")
    else:
        texto = open(T.ruta_js("en"), encoding="utf-8").read()
        m = re.search(r"window\.I18N_CATALOGO\s*=\s*(\{.*\});", texto, re.S)
        if not m or json.loads(m.group(1)) != T.catalogo_js(cat):
            fallo("static/i18n/en.js no corresponde al .po: python herramientas/traducciones.py compilar")

# ── 4. Humor ──────────────────────────────────────────────────────────────
print("4. Frases de humor en inglés")
import humor  # noqa: E402

RUTA_HUMOR_EN = "frases_humor_en.csv"
if not os.path.exists(RUTA_HUMOR_EN):
    fallo(f"No existe {RUTA_HUMOR_EN}")
else:
    es, en = humor.Humorista(ruta=humor.RUTA_FRASES), humor.Humorista(ruta=RUTA_HUMOR_EN)
    es._recargar_si_cambio()
    en._recargar_si_cambio()
    if en._errores_csv:
        fallo(f"{RUTA_HUMOR_EN}: {en._errores_csv} filas con errores (ver el log)")
    eventos_es = {ev for ev, h, f in es._frases}
    eventos_en = {ev for ev, h, f in en._frases}
    if eventos_es - eventos_en:
        fallo(f"Eventos sin frases en inglés: {sorted(eventos_es - eventos_en)}")
    if eventos_en - eventos_es:
        fallo(f"Eventos en inglés que no existen en español: {sorted(eventos_en - eventos_es)}")
    permitidos = {}
    for ev, h, f in es._frases:
        permitidos.setdefault(ev, {"nivel"}).update(marcadores(f))
    for ev, h, f in en._frases:
        sobran = marcadores(f) - permitidos.get(ev, {"nivel"})
        if sobran:
            fallo(f"{RUTA_HUMOR_EN} ({ev}): marcadores {sorted(sobran)} que el evento no rellena: {_corto(f)}")
    for ev in sorted(eventos_es & eventos_en):
        n_es = sum(1 for e, h, f in es._frases if e == ev)
        n_en = sum(1 for e, h, f in en._frases if e == ev)
        if n_en < max(1, n_es // 2):
            aviso(f"Humor «{ev}»: {n_en} frases en inglés frente a {n_es} en español")

# ── 5. Datos _en ──────────────────────────────────────────────────────────
print("5. Ficheros de datos en inglés")


def ids_csv(ruta, columna="id"):
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader((l for l in f if not l.startswith("#")), delimiter=";")
        return {(fila.get(columna) or "").strip() for fila in lector}


ids_catalogo = ids_csv("catalogo_objetos.csv")
for ruta in ("catalogo_objetos_en.csv", "descripciones_objetos_en.csv"):
    if not os.path.exists(ruta):
        fallo(f"No existe {ruta}")
        continue
    extra = ids_csv(ruta) - ids_catalogo - {""}
    if extra:
        fallo(f"{ruta}: ids que no están en catalogo_objetos.csv: {sorted(extra)[:10]}")
if os.path.exists("descripciones_objetos.csv") and os.path.exists("descripciones_objetos_en.csv"):
    faltan = ids_csv("descripciones_objetos.csv") - ids_csv("descripciones_objetos_en.csv") - {""}
    if faltan:
        aviso(f"descripciones_objetos_en.csv: {len(faltan)} objetos sin descripción en inglés (sale la española)")
for ruta in glob.glob(os.path.join("constelaciones", "*_en.csv")) + glob.glob(os.path.join("info_objetos", "*_en.txt")):
    base = ruta[:-len("_en.csv")] + ".csv" if ruta.endswith(".csv") else ruta[:-len("_en.txt")] + ".txt"
    if not os.path.exists(base):
        fallo(f"{ruta} no tiene su original {base}")

# ── 6. idioma.py ──────────────────────────────────────────────────────────
print("6. idioma.py")
import idioma  # noqa: E402

try:
    idioma.cambiar("en", depurar=False)
    if cat is not None and any(m.id == "Inicio" and m.string for m in cat):
        if idioma._("Inicio") == "Inicio":
            fallo("Con el idioma en inglés _('Inicio') no se traduce (¿falta compilar?)")
    if idioma.fmt_num(3.25, 2) != "3.25" or idioma.fmt_fecha(datetime(2026, 10, 15, 21, 30)) != "2026-10-15 21:30":
        fallo("Formatos en inglés incorrectos")
    plural = idioma.ngettext("{n} foto", "{n} fotos", 3)
    if "3" not in plural or "{n}" in plural:
        fallo("ngettext no rellena {n}")
    if idioma._("Hola {x}", x=1, y=2) != "Hola 1" or idioma._("Sin {hueco}") != "Sin {hueco}":
        fallo("_() no rellena bien los marcadores")
    idioma.cambiar("en", depurar=True)
    if idioma._("Texto que no existe en el catálogo") != "¿¿Texto que no existe en el catálogo??":
        fallo("El modo depurar no marca lo no traducido")
    idioma.cambiar("es", depurar=False)
    if idioma._("Inicio") != "Inicio" or idioma.fmt_num(3.25, 2) != "3,25" \
            or idioma.fmt_fecha(datetime(2026, 10, 15, 21, 30)) != "15/10/2026 21:30":
        fallo("Al volver al español no se recuperan textos y formatos")
    if idioma.cambiar("xx") != "es":
        fallo("Un idioma desconocido debería quedar en español")
except Exception as e:
    fallo(f"idioma.py lanzó {type(e).__name__}: {e}")

# ── 7. Extracción al día ──────────────────────────────────────────────────
print("7. Textos del código frente al .po")
if cat is not None:
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        extraido = T.catalogo_extraido()
    en_codigo = {m.id if isinstance(m.id, str) else m.id[0] for m in extraido if m.id}
    en_po = {m.id if isinstance(m.id, str) else m.id[0] for m in cat if m.id}
    faltan = en_codigo - en_po
    sobran = en_po - en_codigo
    if faltan:
        fallo(f"{len(faltan)} textos nuevos que no están en el .po (python herramientas/traducciones.py extraer):")
        for t in sorted(faltan)[:10]:
            print("        · " + _corto(t))
    if sobran:
        fallo(f"{len(sobran)} textos del .po que ya no están en el código (python herramientas/traducciones.py extraer)")

# ── 8. Python: _ como variable y f-strings en _() ─────────────────────────
print("8. Uso de _ en los módulos de Python")
FUNCIONES = {"_", "gettext", "ngettext", "N_"}
for ruta in sorted(glob.glob("*.py")):
    try:
        arbol = ast.parse(open(ruta, encoding="utf-8-sig").read(), ruta)
    except SyntaxError as e:
        fallo(f"{ruta}: no compila ({e})")
        continue
    # idioma.py define _ = gettext: ahí sí es una asignación legítima
    importa = any(isinstance(n, ast.ImportFrom) and n.module == "idioma" and any(a.name == "_" for a in n.names)
                  for n in ast.walk(arbol))
    for nodo in ast.walk(arbol):
        if importa:
            nombre = None
            if isinstance(nodo, ast.Name) and isinstance(nodo.ctx, (ast.Store, ast.Del)) and nodo.id == "_":
                nombre = nodo
            elif isinstance(nodo, ast.arg) and nodo.arg == "_":
                nombre = nodo
            elif isinstance(nodo, ast.ExceptHandler) and nodo.name == "_":
                nombre = nodo
            if nombre is not None:
                fallo(f"{ruta}:{nombre.lineno}: «_» usado como variable en un módulo que traduce con _() "
                      "(cámbialo por __ u otro nombre)")
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id in FUNCIONES and nodo.args:
            primero = nodo.args[0]
            if isinstance(primero, ast.JoinedStr):
                fallo(f"{ruta}:{nodo.lineno}: f-string dentro de {nodo.func.id}(): usar marcadores {{nombre}} y argumentos")
            elif isinstance(primero, ast.Subscript) and any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                                                            for n in ast.walk(primero.slice)):
                fallo(f"{ruta}:{nodo.lineno}: {nodo.func.id}(x[\"clave\"]): Babel toma la clave por un texto; "
                      "pásalo antes a una variable")
            elif isinstance(primero, ast.BinOp):
                fallo(f"{ruta}:{nodo.lineno}: texto compuesto con + o % dentro de {nodo.func.id}(): no se puede extraer")
            if nodo.func.id == "ngettext" and len(nodo.args) >= 2 and isinstance(nodo.args[1], ast.JoinedStr):
                fallo(f"{ruta}:{nodo.lineno}: f-string en el plural de ngettext()")

# ── 9. JavaScript ─────────────────────────────────────────────────────────
print("9. Uso de _ en el JavaScript")
REGLAS_JS = [
    (re.compile(r"(?<![\w$.])_\(\s*`"), "plantilla `...` dentro de _(): usar comillas y {marcadores}"),
    (re.compile(r"(?<![\w$.])(?:_|ngettext)\(\s*(\"[^\"\n]*\"|'[^'\n]*')\s*\+"), "texto concatenado con + dentro de _()"),
    (re.compile(r"\(\s*_\s*[,)]|(?<![\w$.])_\s*=>|,\s*_\s*\)\s*=>"), "«_» usado como parámetro: tapa la traducción"),
    (re.compile(r"(?<![\w$.])_\(\s*[\w$.]+\[\s*[\"']"), "_(x[\"clave\"]): Babel toma la clave por un texto; pásalo antes a una variable"),
    (re.compile(r"\$\{\s*(?:_|ngettext|N_)\("), "_() dentro de ${...}: Babel no siempre lo extrae; sácalo con + o a una variable"),
    (re.compile(r"\{%-?\s*trans\b"), "{% trans %}: usar {{ _(\"...\") }} (marcadores {nombre})"),
]
ficheros_js = (glob.glob("static/js/*.js") + glob.glob("static/*.js") + glob.glob("templates/*.html"))
for ruta in sorted(ficheros_js):
    if ruta.replace("\\", "/").endswith(("NoSleep.js", "i18n.js")):
        continue
    for num, linea in enumerate(open(ruta, encoding="utf-8-sig"), start=1):
        for regla, explicacion in REGLAS_JS:
            if regla.search(linea):
                fallo(f"{ruta}:{num}: {explicacion}")

print()
if fallos:
    print(f"❌ {len(fallos)} fallos" + (f", {len(avisos)} avisos" if avisos else ""))
    sys.exit(1)
print("✅ Traducciones correctas" + (f" ({len(avisos)} avisos)" if avisos else ""))
