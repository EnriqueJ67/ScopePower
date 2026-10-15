# idioma.py
#
# Idioma de la interfaz, de la voz del PC y de los textos de ScopePower (español o inglés).
# El español es el idioma original: los textos se escriben en español dentro del código y la
# traducción al inglés vive en traducciones/en/LC_MESSAGES/scopepower.po (se compila a .mo y
# a static/i18n/en.js con herramientas/traducciones.py).
#
# El idioma es global para toda la app (PC, móvil, tablet y voz) y se guarda en config.ini:
#     [Interfaz] idioma = es | en
#     [Interfaz] idioma_depurar = false   true = lo que falta por traducir sale como ¿¿texto??
#
# Uso desde cualquier módulo:
#     from idioma import _, ngettext, N_
#     voz.decir(_("Telescopio conectado"))
#     return jsonify({"mensaje": _("No se pudo conectar: {e}", e=e)})
#     ngettext("{n} foto", "{n} fotos", total)          # {n} se rellena solo
#     TIPOS = {"galaxy": N_("Galaxia")}                   # constante: se traduce al mostrarla, _(TIPOS[k])
#
# Reglas:
#   - Nunca _() en una constante de módulo: se quedaría en el idioma del arranque. N_() la marca
#     para traducir y _() se aplica al usarla.
#   - Marcadores siempre con llaves y nombre: {n}, {e}, {nombre}. Nada de % ni f-strings dentro de _().
#   - En un módulo que importa _ no se puede usar _ como variable descartable (for _ in ...):
#     lo comprueba herramientas/probar_traducciones.py.
#
# La traducción vigente es una referencia global que cambiar() sustituye entera, así que los
# hilos de fondo (apilado, voz, plan de la noche, vigilantes) hablan en el idioma nuevo sin
# necesitar el contexto de una petición web.

import configparser
import gettext as _gettext
import logging
import os
import threading

log = logging.getLogger("scopepower.idioma")

IDIOMAS = {"es": "Español", "en": "English"}
POR_DEFECTO = "es"
DOMINIO = "scopepower"
CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "traducciones")
SECCION = "Interfaz"

_LANG = {"es": "es-ES", "en": "en-US"}           # navegador: SpeechSynthesis, toLocaleString
_LCID = {"es": "0A", "en": "09"}                  # final del LCID de las voces SAPI (40A, 409...)
_FECHA = {"es": "%d/%m/%Y", "en": "%Y-%m-%d"}
_FECHA_CORTA = {"es": "%d/%m", "en": "%m-%d"}

_actual = POR_DEFECTO
_depurar = False
_trad = _gettext.NullTranslations()
_lock = threading.Lock()
_oyentes = []


class _Marcador(_gettext.NullTranslations):
    """Último recurso del catálogo en modo depurar: lo que no está traducido sale entre ¿¿ ??."""
    def gettext(self, mensaje):
        return "¿¿" + mensaje + "??"

    def ngettext(self, singular, plural, n):
        return "¿¿" + (singular if n == 1 else plural) + "??"


class _ConHuecos(dict):
    """Para str.format_map: un {marcador} sin valor se deja tal cual en vez de fallar."""
    def __missing__(self, clave):
        return "{" + clave + "}"


def _formatear(texto, valores):
    if not valores:
        return texto
    try:
        return texto.format_map(_ConHuecos(valores))
    except (ValueError, IndexError, AttributeError, TypeError) as e:
        log.warning("Texto mal formado para format: %r (%s)", texto, e)
        return texto


# ── Idioma vigente ────────────────────────────────────────────────────────
def idioma_actual():
    return _actual


def depurando():
    return _depurar


def _cargar_traduccion(codigo, depurar):
    if codigo == POR_DEFECTO:
        trad = _gettext.NullTranslations()       # el español es el original: no hay catálogo
    else:
        trad = _gettext.translation(DOMINIO, CARPETA, languages=[codigo], fallback=True)
        if type(trad) is _gettext.NullTranslations:
            log.warning("No hay traducción compilada para '%s' en %s", codigo, CARPETA)
        if depurar:
            trad.add_fallback(_Marcador())
    return trad


def cambiar(codigo, depurar=None):
    """
    Pone otro idioma para toda la app. No escribe config.ini (eso lo hace quien llama, con el
    candado de ScopePower). Avisa a los oyentes (voz, humor, catálogo...) para que recarguen.
    Devuelve el código que ha quedado en vigor.
    """
    global _actual, _depurar, _trad
    codigo = (codigo or "").strip().lower()
    if codigo not in IDIOMAS:
        log.warning("Idioma desconocido '%s': se usa '%s'", codigo, POR_DEFECTO)
        codigo = POR_DEFECTO
    with _lock:
        if depurar is None:
            depurar = _depurar
        cambia = codigo != _actual
        _trad = _cargar_traduccion(codigo, depurar)
        _actual, _depurar = codigo, bool(depurar)
    if cambia:
        log.info("Idioma de la app: %s", IDIOMAS[codigo])
        for oyente in list(_oyentes):
            try:
                oyente()
            except Exception as e:
                log.warning("Fallo al avisar del cambio de idioma a %r: %s", oyente, e)
    return codigo


def cargar_desde_config(ruta="config.ini"):
    """Lee [Interfaz] idioma e idioma_depurar. Sin la clave (config.ini antiguo) queda en español."""
    cfg = configparser.ConfigParser()
    cfg.read(ruta)          # misma codificación que el resto de la app al escribir config.ini
    codigo = cfg.get(SECCION, "idioma", fallback=POR_DEFECTO)
    depurar = cfg.get(SECCION, "idioma_depurar", fallback="false").strip().lower() == "true"
    return cambiar(codigo, depurar)


def registrar_oyente(funcion):
    """funcion() se llama (sin argumentos) cada vez que cambia el idioma."""
    if funcion not in _oyentes:
        _oyentes.append(funcion)


# ── Traducción ────────────────────────────────────────────────────────────
def gettext(mensaje, **valores):
    """Traduce mensaje al idioma vigente y rellena sus {marcadores} con los valores dados."""
    return _formatear(_trad.gettext(mensaje), valores)


def ngettext(singular, plural, n, **valores):
    """Singular o plural según n, traducido. {n} se rellena solo con n."""
    valores.setdefault("n", n)
    return _formatear(_trad.ngettext(singular, plural, n), valores)


_ = gettext


def N_(mensaje):
    """Marca un texto para traducir sin traducirlo aún (constantes de módulo)."""
    return mensaje


# ── Formatos locales ──────────────────────────────────────────────────────
def fmt_num(x, dec=None):
    """Número con coma decimal en español y punto en inglés. dec=None: sin ceros sobrantes."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    texto = f"{x:.{dec}f}" if dec is not None else f"{x:g}"
    return texto.replace(".", ",") if _actual == "es" else texto


def formato_fecha(hora=True, corta=False):
    """Patrón de strftime para fechas: 15/10/2026 21:30 en español, 2026-10-15 21:30 en inglés."""
    patron = (_FECHA_CORTA if corta else _FECHA)[_actual]
    return patron + (" %H:%M" if hora else "")


def fmt_fecha(fecha, hora=True, corta=False):
    return fecha.strftime(formato_fecha(hora, corta))


def lang_navegador():
    """Código BCP 47 para SpeechSynthesis, SpeechRecognition y toLocaleString."""
    return _LANG[_actual]


def lcid_sufijo():
    """Final del LCID de las voces SAPI del idioma vigente ("0A" español, "09" inglés)."""
    return _LCID[_actual]


def wiki():
    """Idioma de la Wikipedia para los resúmenes de objetos."""
    return _actual


def sufijo_fichero():
    """"" en español, "_en" en inglés: frases_humor_en.csv, descripciones_objetos_en.csv..."""
    return "" if _actual == POR_DEFECTO else "_" + _actual


def ruta_variante(ruta):
    """La versión traducida de un fichero de datos (nombre_en.ext) si existe; si no, la original."""
    sufijo = sufijo_fichero()
    if not sufijo:
        return ruta
    base, ext = os.path.splitext(ruta)
    traducida = base + sufijo + ext
    return traducida if os.path.exists(traducida) else ruta


def para_js():
    """Lo que necesita static/js/i18n.js (window.IDIOMA)."""
    return {"codigo": _actual, "lang": _LANG[_actual], "locale": _LANG[_actual],
            "depurar": _depurar}
