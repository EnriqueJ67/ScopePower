# guia.py
#
# Guía «¿Qué quieres hacer hoy?»: el botón principal del inicio pregunta qué se quiere
# hacer y lleva de la mano hasta dejarlo a un toque de «Empezar». No sustituye a ninguna
# pantalla: une las que ya hay (alineación, GoTo con centrado, darks y flats, enfoque,
# apilado) con una lista de pasos y un semáforo por comprobación.
#
# Tres partes, sin Flask ni COM (se prueba en seco con herramientas/probar_guia.py):
#
#   1. ACTIVIDADES y PASOS: lo que ofrece el menú y los pasos de cada ruta guiada. Van
#      guiados cielo profundo, el Sol, la Luna, el paisaje, enfocar y las fotos de
#      calibración; la galería no es una ruta: enseña las últimas fotos y lleva a ellas.
#   2. La sesión, en datos/guia.json: actividad, paso, objeto elegido y lo que ya se ha
#      hecho esta noche (alineación, enfoque). La app no puede saber si la montura está
#      alineada (eso vive en el programa de la montura) ni si el enfoque es bueno sin
#      hacer una foto: lo recuerda de cuando se hizo, o lo pregunta.
#   3. comprobaciones(): con los datos del equipo que le pasa ScopePower.py (conexión,
#      disco, dark, flat, centrado...) dice qué está bien, qué falta y si se puede seguir.
#
# No confundir con el asistente de configuración inicial (static/js/asistente.js).

import os
import json
import math
import time
import logging
import threading
from datetime import datetime

from idioma import _, N_, fmt_num, ngettext

log = logging.getLogger("scopepower.guia")

RUTA = os.path.join("datos", "guia.json")
CADUCA_S = 12 * 3600             # una sesión o un «ya está hecho» de hace más de esto es de otra noche
ALTURA_CENIT = 75.0              # por encima, una altacimutal alarga las estrellas (deriva en acimut)
ALTURA_ENFOQUE = (35.0, 72.0)    # altura cómoda de la estrella para enfocar
MARGEN_CAMPO = 0.8               # el objeto «cabe» si ocupa menos de esta fracción del lado corto del campo
SEPARACION_MISMO_OBJETO = 0.5    # grados: el último GoTo es «el del objeto» si cae a menos de esto
MARCABLES = ("alineacion", "puesta_estacion", "enfoque")      # lo hecho en la noche: vale para cualquier ruta
PASOS_MARCABLES = ("apuntar_disco", "encuadre", "apuntar_enfoque")      # lo hecho en esta sesión de la guía
MODOS = ("foto", "video", "timelapse")
ALTURA_MINIMA_DISCO = 3.0        # grados: por debajo, el Sol o la Luna se dan por puestos
OMEGA_TIERRA = 7.2921e-5         # rad/s: giro de la Tierra, del que sale el giro del campo en una altacimutal
TOLERANCIA_GIRO_PX = 3.0         # lo que pueden estirarse las estrellas de las esquinas en una foto sin que se note
EXPOSICION_TOPE_ALTAZ = 30.0     # s: aunque el campo casi no gire, el seguimiento de una altacimutal no da para más
MODOS_ECUATORIALES = ("ecuatorial", "ecuatorial_alemana")

# El fondo de cada tarjeta es static/guia/<clave>.jpg (ver herramientas/imagenes_guia.py); «imagen_ancha»
# es el recorte apaisado para cuando la tarjeta va la primera, a todo lo ancho. «preferencias»: cómo se
# deja la pantalla Cámara al empezar. «astro»: el disco con el que se sincroniza y se centra de día.
ACTIVIDADES = [
    {"clave": "cielo_profundo", "titulo": N_("Cielo profundo"), "cuando": "noche", "guiada": True,
     "imagen_ancha": "inicio.jpg", "desc": N_("Galaxias, nebulosas y cúmulos: apilado en tiempo real")},
    {"clave": "luna", "titulo": N_("Luna"), "cuando": "luna", "guiada": True, "astro": "luna",
     "preferencias": {"modo_dia": "on", "foto_dia_objetivo": "luna"},
     "desc": N_("Foto o vídeo de la Luna, con la exposición automática")},
    {"clave": "sol", "titulo": N_("Sol"), "cuando": "dia", "guiada": True, "astro": "sol", "imagen_ancha": "sol_ancho.jpg",
     "preferencias": {"modo_dia": "on", "foto_dia_objetivo": "sol"},
     "desc": N_("Foto o vídeo del Sol, siempre con el filtro solar puesto")},
    {"clave": "paisaje", "titulo": N_("Paisaje"), "cuando": "dia", "guiada": True, "sin_montura": True,
     "preferencias": {"modo_dia": "on", "foto_dia_objetivo": "paisaje"},
     "desc": N_("Fotos de día de algo en tierra")},
    {"clave": "enfocar", "titulo": N_("Enfocar"), "cuando": "siempre", "guiada": True, "sin_montura": True,
     "desc": N_("Afina el enfoque antes de hacer fotos")},
    {"clave": "calibracion", "titulo": N_("Fotos de calibración"), "cuando": "siempre", "guiada": True, "sin_montura": True,
     "desc": N_("Darks y flats para limpiar las fotos")},
    # La galería no abre sesión: la web enseña las últimas fotos (GET /guia/fotos) y lleva a su editor
    {"clave": "galeria", "titulo": N_("Galería"), "cuando": "siempre", "destino": "/galeria", "vista": "galeria",
     "desc": N_("Ver, editar y compartir tus fotos")},
]
_ORDEN_DIA = ("sol", "luna", "paisaje", "enfocar", "cielo_profundo", "calibracion", "galeria")
_ORDEN_NOCHE = ("cielo_profundo", "luna", "enfocar", "calibracion", "galeria", "paisaje", "sol")

# Pasos de cada ruta guiada. «pantalla»: el paso se hace en esa pantalla, con la banda de la guía arriba;
# «banda»: lo que dice la banda mientras tanto.
PASOS = {
    "cielo_profundo": [
        {"clave": "equipo", "titulo": N_("Equipo")},
        {"clave": "alineacion", "titulo": N_("Alineación"), "saltable": True, "pantalla": "/alineacion",
         "banda": N_("Alinea la montura y pulsa «Hecho, siguiente»")},
        {"clave": "objeto", "titulo": N_("Objeto"), "pantalla": "/planetario",
         "banda": N_("Ve a un objeto desde esta pantalla y pulsa «Hecho, siguiente»")},
        {"clave": "mosaico", "titulo": N_("Mosaico"), "solo": "mosaico"},
        {"clave": "captura", "titulo": N_("Exposición")},
        {"clave": "calibracion", "titulo": N_("Darks y flats")},
        {"clave": "enfoque", "titulo": N_("Enfoque"), "saltable": True, "pantalla": "/foto-prueba",
         "banda": N_("Enfoca y pulsa «Hecho, siguiente»")},
        # El mosaico y el plan de la noche hacen ellos el GoTo y el centrado de cada tesela o de cada objeto
        {"clave": "apuntar", "titulo": N_("Apuntar y centrar"), "solo": "uno"},
        {"clave": "listo", "titulo": N_("Listo"), "pantalla": "/apilado", "solo": "uno",
         "banda": N_("Todo listo: pulsa «Empezar»")},
        {"clave": "listo_mosaico", "titulo": N_("Listo"), "solo": "mosaico"},
        {"clave": "listo_plan", "titulo": N_("Listo"), "solo": "varios"},
    ],
    # De día no hay estrellas: la montura se sincroniza con el disco del Sol o de la Luna (disco.py)
    "sol": [
        {"clave": "filtro", "titulo": N_("Filtro solar")},
        {"clave": "equipo", "titulo": N_("Equipo")},
        {"clave": "apuntar_disco", "titulo": N_("Apuntar al Sol"), "pantalla": "/foto-prueba",
         "banda": N_("Lleva el Sol al centro de la foto con el pad y pulsa «Hecho, siguiente»")},
        {"clave": "enfoque", "titulo": N_("Enfoque"), "saltable": True, "pantalla": "/foto-prueba",
         "banda": N_("Enfoca y pulsa «Hecho, siguiente»")},
        {"clave": "modo", "titulo": N_("Foto o vídeo")},
        {"clave": "listo_camara", "titulo": N_("Listo")},
    ],
    "luna": [
        {"clave": "equipo", "titulo": N_("Equipo")},
        {"clave": "apuntar_disco", "titulo": N_("Apuntar a la Luna"), "pantalla": "/foto-prueba",
         "banda": N_("Lleva la Luna al centro de la foto con el pad y pulsa «Hecho, siguiente»")},
        {"clave": "enfoque", "titulo": N_("Enfoque"), "saltable": True, "pantalla": "/foto-prueba",
         "banda": N_("Enfoca y pulsa «Hecho, siguiente»")},
        {"clave": "modo", "titulo": N_("Foto o vídeo")},
        {"clave": "listo_camara", "titulo": N_("Listo")},
    ],
    "paisaje": [
        {"clave": "equipo", "titulo": N_("Equipo")},
        {"clave": "encuadre", "titulo": N_("Encuadre"), "saltable": True, "pantalla": "/foto-prueba",
         "banda": N_("Encuadra con el pad y pulsa «Hecho, siguiente»")},
        {"clave": "enfoque", "titulo": N_("Enfoque"), "saltable": True, "pantalla": "/foto-prueba",
         "banda": N_("Enfoca y pulsa «Hecho, siguiente»")},
        {"clave": "modo", "titulo": N_("Foto o vídeo")},
        {"clave": "listo_camara", "titulo": N_("Listo")},
    ],
    "enfocar": [
        {"clave": "equipo", "titulo": N_("Equipo")},
        {"clave": "apuntar_enfoque", "titulo": N_("Dónde enfocar"), "saltable": True},
        {"clave": "enfoque", "titulo": N_("Enfoque"), "pantalla": "/foto-prueba",
         "banda": N_("Enfoca y pulsa «Hecho, siguiente»")},
        {"clave": "listo_enfoque", "titulo": N_("Listo")},
    ],
    # Los darks valen para la exposición y la ganancia con que se hicieron: primero se fijan
    "calibracion": [
        {"clave": "equipo", "titulo": N_("Equipo")},
        {"clave": "captura", "titulo": N_("Exposición")},
        {"clave": "calibracion", "titulo": N_("Darks")},
        {"clave": "flats", "titulo": N_("Flats"), "saltable": True, "pantalla": "/flats",
         "banda": N_("Haz los flats y pulsa «Hecho, siguiente»")},
        {"clave": "listo_calibracion", "titulo": N_("Listo")},
    ],
}
AVISO_SALTAR = {
    "alineacion": N_("Sin alinear, el GoTo puede dejar el objeto fuera del campo y el centrado tardará más."),
    "enfoque": N_("Con el enfoque sin afinar se pierde el detalle: las estrellas salen gordas y el borde del Sol o de la Luna, blando."),
    "encuadre": N_("Sin encuadrar, la foto saldrá de donde apunte ahora el telescopio."),
    "apuntar_enfoque": N_("Se enfocará con lo que haya ahora en la foto, sea lo que sea."),
    "flats": N_("Sin flat, el viñeteo y las motas de polvo se quedarán en las fotos."),
}

# Estrellas brillantes para enfocar: (nombre, AR en horas, declinación en grados, magnitud), J2000
ESTRELLAS_ENFOQUE = [
    ("Sirius", 6.7525, -16.716, -1.46), ("Arcturus", 14.2610, 19.182, -0.05), ("Vega", 18.6156, 38.784, 0.03),
    ("Capella", 5.2782, 45.998, 0.08), ("Rigel", 5.2423, -8.202, 0.13), ("Procyon", 7.6550, 5.225, 0.34),
    ("Betelgeuse", 5.9195, 7.407, 0.50), ("Altair", 19.8464, 8.868, 0.77), ("Aldebaran", 4.5987, 16.509, 0.85),
    ("Antares", 16.4901, -26.432, 0.96), ("Spica", 13.4199, -11.161, 0.97), ("Pollux", 7.7553, 28.026, 1.14),
    ("Fomalhaut", 22.9608, -29.622, 1.16), ("Deneb", 20.6905, 45.280, 1.25), ("Regulus", 10.1395, 11.967, 1.35),
    ("Castor", 7.5767, 31.888, 1.58), ("Alioth", 12.9005, 55.960, 1.77), ("Mirfak", 3.4054, 49.861, 1.79),
    ("Dubhe", 11.0621, 61.751, 1.79), ("Alkaid", 13.7923, 49.313, 1.86), ("Alpheratz", 0.1398, 29.090, 2.06),
    ("Hamal", 2.1195, 23.462, 2.00), ("Polaris", 2.5303, 89.264, 1.98),
]

_lock = threading.RLock()
_mem = None


def _ahora():
    return time.time()


def _hora(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _tr(tabla, campo):
    """Traduce al pintarlo un texto guardado con N_() en una tabla del módulo; "" si no lo hay."""
    texto = tabla.get(campo)
    return _(texto) if texto else ""


# ───────────────────────── Persistencia ─────────────────────────

def _leer():
    """El estado guardado, ya sin lo caducado: {"sesion": {...} | None, "hechos": {clave: hora}}."""
    global _mem
    with _lock:
        if _mem is None:
            _mem = {"sesion": None, "hechos": {}}
            try:
                with open(RUTA, encoding="utf-8") as f:
                    leido = json.load(f)
                if isinstance(leido, dict):
                    _mem["sesion"] = leido.get("sesion") if isinstance(leido.get("sesion"), dict) else None
                    _mem["hechos"] = {k: float(v) for k, v in (leido.get("hechos") or {}).items() if k in MARCABLES}
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as e:
                log.warning("No se pudo leer %s (%s): la guía empieza de cero", RUTA, e)
        limite = _ahora() - CADUCA_S
        if _mem["sesion"] and _mem["sesion"].get("actualizado", 0) < limite:
            _mem["sesion"] = None
        _mem["hechos"] = {k: v for k, v in _mem["hechos"].items() if v >= limite}
        return _mem


def _guardar():
    with _lock:
        try:
            os.makedirs(os.path.dirname(RUTA) or ".", exist_ok=True)
            tmp = RUTA + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_mem, f, ensure_ascii=False, indent=1)
            os.replace(tmp, RUTA)
        except OSError as e:
            log.warning("No se pudo guardar %s: %s", RUTA, e)


def olvidar_todo():
    """Vuelve a leer del disco la próxima vez (para las pruebas)."""
    global _mem
    with _lock:
        _mem = None


# ───────────────────────── Actividades y pasos ─────────────────────────

def actividad(clave):
    for a in ACTIVIDADES:
        if a["clave"] == clave:
            return a
    raise ValueError(_("Actividad desconocida: {clave}", clave=clave))


def variante(ses):
    """Cómo se hace el cielo profundo: "uno" (un objeto), "mosaico" o "varios" (plan de la noche)."""
    if ses and ses.get("varios"):
        return "varios"
    return "mosaico" if ses and ses.get("mosaico") else "uno"


def _pasos(clave_actividad, ses=None):
    """Los pasos de la ruta; con la sesión, solo los de su variante (un objeto, mosaico o varios)."""
    cual = variante(ses)
    return [p for p in PASOS.get(clave_actividad, []) if p.get("solo") in (None, cual)]


def _paso(ses):
    for p in _pasos(ses["actividad"], ses):
        if p["clave"] == ses["paso"]:
            return p
    return _pasos(ses["actividad"], ses)[0]


def _indice(ses):
    claves = [p["clave"] for p in _pasos(ses["actividad"], ses)]
    return claves.index(ses["paso"]) if ses["paso"] in claves else 0


# ───────────────────────── Sesión ─────────────────────────

def sesion():
    """Copia de la sesión en curso, o None."""
    with _lock:
        s = _leer()["sesion"]
        return json.loads(json.dumps(s)) if s else None


def hechos():
    with _lock:
        return dict(_leer()["hechos"])


def empezar(clave):
    """Abre la sesión de una actividad guiada (y cierra la que hubiera). Las demás no tienen sesión."""
    act = actividad(clave)
    with _lock:
        m = _leer()
        if not act.get("guiada"):
            return None
        ahora = _ahora()
        m["sesion"] = {"actividad": clave, "paso": _pasos(clave)[0]["clave"], "objeto": None, "mosaico": False,
                       "modo": "foto", "hechos_paso": {}, "varios": False, "rejilla": None,
                       "saltados": [], "inicio": ahora, "actualizado": ahora, "paso_desde": ahora}
        _guardar()
        return sesion()


def terminar():
    with _lock:
        m = _leer()
        if m["sesion"]:
            m["sesion"] = None
            _guardar()


def _mover(ses, clave_paso):
    ses["paso"] = clave_paso
    ses["actualizado"] = ses["paso_desde"] = _ahora()


def ir_a(clave_paso):
    """Salta a un paso de la sesión (los puntos de la cabecera)."""
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        if clave_paso not in [p["clave"] for p in _pasos(ses["actividad"], ses)]:
            raise ValueError(_("Paso desconocido: {paso}", paso=clave_paso))
        _mover(ses, clave_paso)
        _guardar()
        return sesion()


def avanzar(delta=1, saltar=False):
    """Paso siguiente (delta=1) o anterior (delta=-1). `saltar` apunta el paso como saltado a sabiendas."""
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        pasos = _pasos(ses["actividad"], ses)
        i = _indice(ses)
        if saltar:
            if not pasos[i].get("saltable"):
                raise ValueError(_("Este paso no se puede saltar"))
            if ses["paso"] not in ses["saltados"]:
                ses["saltados"].append(ses["paso"])
        elif delta > 0 and ses["paso"] in ses["saltados"]:
            ses["saltados"].remove(ses["paso"])
        _mover(ses, pasos[max(0, min(len(pasos) - 1, i + (1 if saltar else delta)))]["clave"])
        _guardar()
        return sesion()


def fijar_objeto(nombre, ra_h, dec_deg, tipo="", tamano_arcmin=None):
    nombre = " ".join(str(nombre or "").split())
    if not nombre:
        raise ValueError(_("Falta el nombre del objeto"))
    ra_h, dec_deg = float(ra_h), float(dec_deg)
    if not (0.0 <= ra_h < 24.0 and -90.0 <= dec_deg <= 90.0):
        raise ValueError(_("Coordenadas fuera de rango"))
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        ses["objeto"] = {"nombre": nombre[:80], "ra_h": ra_h, "dec_deg": dec_deg, "tipo": str(tipo or "")[:40],
                         "tamano_arcmin": float(tamano_arcmin) if tamano_arcmin else None}
        ses["mosaico"] = False
        ses["actualizado"] = _ahora()
        _guardar()
        return sesion()


def fijar_mosaico(activado):
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        ses["mosaico"] = bool(activado)
        if activado:
            ses["varios"] = False
        ses["actualizado"] = _ahora()
        _guardar()
        return sesion()


def fijar_varios(activado):
    """Varios objetos en la noche: la lista vive en el plan de la noche (plan_sesion.py), no en la sesión."""
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        ses["varios"] = bool(activado)
        if activado:
            ses["mosaico"] = False
        ses["actualizado"] = _ahora()
        _guardar()
        return sesion()


def fijar_rejilla(filas, columnas, solape_pct, fotos_por_tesela):
    """La rejilla del mosaico elegida en la guía (ya validada por quien llama)."""
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        ses["rejilla"] = {"filas": int(filas), "columnas": int(columnas), "solape_pct": float(solape_pct),
                          "fotos_por_tesela": max(1, int(fotos_por_tesela))}
        ses["actualizado"] = _ahora()
        _guardar()
        return sesion()


def fijar_modo(modo):
    """Qué se va a hacer al final de una ruta de día: fotos sueltas, vídeo o timelapse."""
    if modo not in MODOS:
        raise ValueError(_("Modo desconocido: {modo}", modo=modo))
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        ses["modo"] = modo
        ses["actualizado"] = _ahora()
        _guardar()
        return sesion()


def paso_hecho(clave, nota=""):
    """Da por hecho un paso de esta sesión que se hace a mano o desde la web (centrar el disco, encuadrar)."""
    if clave not in PASOS_MARCABLES:
        raise ValueError(_("No se puede marcar: {clave}", clave=clave))
    with _lock:
        ses = _leer()["sesion"]
        if not ses:
            raise ValueError(_("No hay ninguna guía en marcha"))
        ses.setdefault("hechos_paso", {})[clave] = {"hora": _ahora(), "nota": " ".join(str(nota or "").split())[:300]}
        ses["actualizado"] = _ahora()
        _guardar()
        return sesion()


def marcar(clave, hecho=True):
    """Apunta (o borra) que algo ya está hecho esta noche: alineación, puesta en estación, enfoque."""
    if clave not in MARCABLES:
        raise ValueError(_("No se puede marcar: {clave}", clave=clave))
    with _lock:
        m = _leer()
        if hecho:
            m["hechos"][clave] = _ahora()
        else:
            m["hechos"].pop(clave, None)
        _guardar()


AVISOS_ENFOCADO = ("centrada", "enfocado", "zona")    # Bahtinov centrada, firma de colores, limbo en la zona buena


def enfoque_detectado(aviso):
    """
    Lo llaman el vivo y la foto de prueba con cada aviso del enfoque (enfoque.py): con una Bahtinov
    centrada, la firma de colores o el limbo del Sol o la Luna en la zona buena, apunta que esta noche
    ya se ha enfocado. Antes solo lo apuntaba «Hecho, siguiente» dentro de la guía, y quien enfocaba
    desde Cámara (26-nov) veía luego «No consta que hayas enfocado esta noche». Devuelve True si lo apuntó.
    """
    if aviso not in AVISOS_ENFOCADO:
        return False
    try:
        marcar("enfoque")
    except Exception as e:          # la guía no debe tumbar el vivo
        log.warning("Guía: no se pudo apuntar el enfoque: %s", e)
        return False
    return True


def observar(datos):
    """
    Apunta sola lo que el equipo cuenta: una alineación o una puesta en estación terminadas bien, y
    cierra la guía cuando el apilado ya está en marcha en el último paso. Devuelve True si la cerró.
    """
    with _lock:
        m = _leer()
        cambio = False
        ali = datos.get("alineacion") or {}
        if ali.get("terminado") and ali.get("resultado") in ("completada", "parcial") and "alineacion" not in m["hechos"]:
            m["hechos"]["alineacion"] = _ahora()
            cambio = True
        pe = datos.get("puesta_estacion") or {}
        if pe.get("terminado") and pe.get("resultado") in ("terminada", "completada", "ok") and "puesta_estacion" not in m["hechos"]:
            m["hechos"]["puesta_estacion"] = _ahora()
            cambio = True
        cerrada = False
        ses = m["sesion"]
        if ses and ses["paso"] == "listo" and datos.get("bucle_activo"):
            m["sesion"] = None
            cambio = cerrada = True
        if cambio:
            _guardar()
        return cerrada


# ───────────────────────── Cálculos ─────────────────────────

def altaz(ra_h, dec_deg, lat, lon, ts=None):
    """(altura, acimut) en grados, el acimut desde el norte hacia el este; con la precisión de un semáforo (≈ medio grado)."""
    ts = _ahora() if ts is None else ts
    dias = ts / 86400.0 + 2440587.5 - 2451545.0
    tsl = (280.46061837 + 360.98564736629 * dias + lon) % 360.0          # tiempo sidéreo local, en grados
    h = math.radians(tsl - ra_h * 15.0)
    fi, de = math.radians(lat), math.radians(dec_deg)
    seno = math.sin(fi) * math.sin(de) + math.cos(fi) * math.cos(de) * math.cos(h)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, seno))))
    az = math.degrees(math.atan2(-math.cos(de) * math.sin(h), math.sin(de) * math.cos(fi) - math.cos(de) * math.sin(fi) * math.cos(h)))
    return alt, az % 360.0


def altura(ra_h, dec_deg, lat, lon, ts=None):
    """Altura sobre el horizonte, en grados."""
    return altaz(ra_h, dec_deg, lat, lon, ts)[0]


def giro_campo_px_s(alt, az, lat, radio_px):
    """
    Píxeles por segundo que se mueven las estrellas de las esquinas porque el campo gira en una montura
    altacimutal: el giro vale Ω·cos(latitud)·cos(acimut)/cos(altura). Es nulo hacia el este y el oeste,
    máximo en el meridiano y se dispara cerca del cénit.
    """
    coseno = math.cos(math.radians(alt))
    if coseno < 1e-3:
        return float("inf")
    return abs(OMEGA_TIERRA * math.cos(math.radians(lat)) * math.cos(math.radians(az)) / coseno) * float(radio_px)


def radio_campo_px(campo):
    """Del centro de la foto a una esquina, en píxeles; None si no se conoce el sensor."""
    try:
        return 0.5 * math.hypot(float(campo["ancho_px"]), float(campo["alto_px"]))
    except (TypeError, KeyError, ValueError):
        return None


def rejilla_recomendada(tamano_arcmin, campo, solape_pct=20.0):
    """(filas, columnas) de la rejilla cuadrada más pequeña, hasta 6×6, que cubre el objeto con un 10 % de margen."""
    try:
        lado = min(float(campo["ancho_deg"]), float(campo["alto_deg"])) * 60.0
    except (TypeError, KeyError, ValueError):
        lado = 0.0
    if not tamano_arcmin or lado <= 0:
        return 2, 2
    paso = 1.0 - float(solape_pct) / 100.0
    for n in range(2, 7):
        if lado * (1 + (n - 1) * paso) >= 1.1 * float(tamano_arcmin):
            return n, n
    return 6, 6


def separacion(ra1_h, dec1, ra2_h, dec2):
    """Separación angular en grados entre dos posiciones (AR en horas)."""
    a1, a2 = math.radians(ra1_h * 15.0), math.radians(ra2_h * 15.0)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def estrella_para_enfocar(lat, lon, ts=None):
    """La estrella más brillante a una altura cómoda ahora: {nombre, ra_h, dec_deg, mag, altura} o None."""
    mejor = None
    for nombre, ra, dec, mag in ESTRELLAS_ENFOQUE:
        alt = altura(ra, dec, lat, lon, ts)
        if ALTURA_ENFOQUE[0] <= alt <= ALTURA_ENFOQUE[1] and (mejor is None or mag < mejor["mag"]):
            mejor = {"nombre": nombre, "ra_h": ra, "dec_deg": dec, "mag": mag, "altura": round(alt, 1)}
    return mejor


def exposicion_recomendada(modo_montura, altura_objeto=None, acimut=None, lat=None, radio_px=None):
    """
    Margen razonable de exposición por foto: {"minimo", "maximo", "texto"}. Una altacimutal no compensa
    el giro del campo, y cerca del cénit además deriva en acimut; una ecuatorial aguanta más. Con el
    acimut del objeto, la latitud y el tamaño del sensor, el máximo sale del giro del campo de verdad:
    lo que tardan las estrellas de las esquinas en estirarse TOLERANCIA_GIRO_PX píxeles.
    """
    if modo_montura in MODOS_ECUATORIALES:
        return {"minimo": 15.0, "maximo": 60.0,
                "texto": _("Con la montura ecuatorial bien puesta en estación, entre 15 y 60 s por foto.")}
    if altura_objeto is not None and altura_objeto >= ALTURA_CENIT:
        return {"minimo": 5.0, "maximo": 10.0,
                "texto": _("El objeto está muy alto ({alt}°): cerca del cénit una montura altacimutal alarga las estrellas. "
                           "Mejor entre 5 y 10 s, o esperar a que baje.", alt=fmt_num(altura_objeto, 0))}
    if None not in (altura_objeto, acimut, lat, radio_px):
        velocidad = giro_campo_px_s(altura_objeto, acimut, lat, radio_px)
        tope = TOLERANCIA_GIRO_PX / velocidad if velocidad > 0 else float("inf")
        if tope >= EXPOSICION_TOPE_ALTAZ:
            return {"minimo": 10.0, "maximo": EXPOSICION_TOPE_ALTAZ,
                    "texto": _("Donde está ahora el objeto el campo casi no gira: hasta {t} s por foto, que es lo que "
                               "aguanta el seguimiento de una montura altacimutal.", t=fmt_num(EXPOSICION_TOPE_ALTAZ, 0))}
        maximo = float(max(5, int(tope)))
        return {"minimo": min(10.0, maximo), "maximo": maximo,
                "texto": _("Donde está ahora el objeto, el giro del campo de la montura altacimutal mueve las estrellas de las "
                           "esquinas {v} px por segundo: hasta unos {t} s por foto salen redondas.",
                           v=fmt_num(velocidad, 2), t=fmt_num(maximo, 0))}
    return {"minimo": 10.0, "maximo": 25.0,
            "texto": _("En una montura altacimutal, entre 10 y 25 s por foto para que las estrellas no se muevan.")}


def cabe_en_el_campo(tamano_arcmin, campo):
    """None si no se sabe; True/False según el objeto quepa en el lado corto del campo de la cámara."""
    if not tamano_arcmin or not campo:
        return None
    lado = min(float(campo.get("ancho_deg") or 0), float(campo.get("alto_deg") or 0)) * 60.0
    if lado <= 0:
        return None
    return float(tamano_arcmin) <= MARGEN_CAMPO * lado


# ───────────────────────── Menú ─────────────────────────

def menu(datos):
    """Las tarjetas del menú, ordenadas para la hora que es, con su chip («Ahora», «desde las 21:40»...)."""
    de_dia = bool(datos.get("de_dia"))
    alt_luna = datos.get("altura_luna")
    por_clave = {a["clave"]: a for a in ACTIVIDADES}
    salida = []
    for clave in (_ORDEN_DIA if de_dia else _ORDEN_NOCHE):
        a = por_clave[clave]
        chip, apagada = None, False
        if a["cuando"] == "noche":
            if de_dia:
                desde = datos.get("noche_desde")
                chip = {"nivel": "warn", "texto": _("desde las {hora}", hora=desde) if desde else _("de noche")}
            else:
                chip = {"nivel": "ok", "texto": _("Ahora")}
        elif a["cuando"] == "dia":
            if de_dia:
                chip = {"nivel": "ok", "texto": _("Ahora")}
            else:
                chip, apagada = {"nivel": "neutro", "texto": _("de día")}, True
        elif a["cuando"] == "luna" and alt_luna is not None:
            if alt_luna > 5:
                chip = {"nivel": "ok", "texto": _("Luna a {alt}°", alt=fmt_num(alt_luna, 0))}
            else:
                chip = {"nivel": "neutro", "texto": _("bajo el horizonte")}
        salida.append({"clave": clave, "titulo": _tr(a, "titulo"), "desc": _tr(a, "desc"), "imagen": clave + ".jpg",
                       "imagen_ancha": a.get("imagen_ancha") or clave + ".jpg", "guiada": bool(a.get("guiada")),
                       "vista": a.get("vista") or "",
                       "chip": chip, "apagada": apagada})
    return salida


# ───────────────────────── Comprobaciones ─────────────────────────

def _c(clave, nivel, texto, accion=None):
    """Un semáforo. nivel: ok | warn | err | pendiente (el equipo está en ello) | falta (le toca al usuario)."""
    return {"clave": clave, "nivel": nivel, "texto": texto, "accion": accion}


def altura_objeto(ses, datos):
    obj = ses.get("objeto")
    if not obj or datos.get("lat") is None or datos.get("lon") is None:
        return None
    return altura(obj["ra_h"], obj["dec_deg"], float(datos["lat"]), float(datos["lon"]), datos.get("ahora"))


def _texto_captura(cap):
    gan = cap.get("ganancia")
    return _("{exp} s por foto, ganancia {gan}, hasta {n} fotos", exp=fmt_num(cap.get("exposicion") or 0, 1),
             gan=(gan if gan not in (None, "") else _("la del driver")), n=cap.get("fotos") or 0)


def _antiguedad(minutos):
    if minutos is None:
        return ""
    if minutos < 90:
        return _("hace {n} min", n=int(round(minutos)))
    if minutos < 48 * 60:
        return _("hace {n} h", n=int(round(minutos / 60.0)))
    return _("hace {n} días", n=int(round(minutos / 1440.0)))


def _comprobar_equipo(ses, hechos_, datos):
    lista = []
    sin_montura = bool(actividad(ses["actividad"]).get("sin_montura"))
    for clave, nombre_ok, nombre_no in (("montura", _("Montura conectada"), _("La montura no está conectada")),
                                        ("camara", _("Cámara conectada"), _("La cámara no está conectada"))):
        if datos.get(clave):
            lista.append(_c(clave, "ok", nombre_ok))
        elif datos.get("conectando"):
            lista.append(_c(clave, "pendiente", _("Conectando...")))
        elif clave == "montura" and sin_montura:
            lista.append(_c(clave, "warn", _("Sin montura: podrás hacer las fotos, pero no mover el telescopio desde aquí"), "conectar"))
        else:
            lista.append(_c(clave, "err", nombre_no, "conectar"))
    disco = datos.get("disco") or {}
    if disco.get("ok"):
        libre = fmt_num(disco.get("libre_gb") or 0, 1)
        if disco.get("sin_espacio"):
            lista.append(_c("disco", "err", _("Disco lleno: quedan {gb} GB. Borra fotos antes de empezar.", gb=libre)))
        elif disco.get("bajo"):
            lista.append(_c("disco", "warn", _("Queda poco disco: {gb} GB libres", gb=libre)))
        else:
            lista.append(_c("disco", "ok", _("Disco: {gb} GB libres", gb=libre)))
    if datos.get("de_dia") and ses["actividad"] == "cielo_profundo":
        desde = datos.get("noche_desde")
        lista.append(_c("noche", "warn", _("Todavía es de día: sin estrellas no hay centrado ni apilado hasta las {hora}.", hora=desde)
                        if desde else _("Todavía es de día: sin estrellas no hay centrado ni apilado.")))
    return lista, bool((datos.get("montura") or sin_montura) and datos.get("camara") and not disco.get("sin_espacio"))


def _comprobar_alineacion(ses, hechos_, datos):
    lista = []
    ali = datos.get("alineacion") or {}
    if ali.get("en_curso"):
        lista.append(_c("alineacion", "pendiente", _("Alineando: paso {n} de {total}", n=ali.get("paso") or 0, total=ali.get("total_pasos") or 0)))
    elif "alineacion" in hechos_:
        lista.append(_c("alineacion", "ok", _("Montura alineada a las {hora}", hora=_hora(hechos_["alineacion"])), "repetir_alineacion"))
    else:
        lista.append(_c("alineacion", "warn", _("No consta que la montura esté alineada esta noche"), "ir_alineacion"))
    if datos.get("modo_montura") in MODOS_ECUATORIALES:
        if "puesta_estacion" in hechos_:
            lista.append(_c("puesta_estacion", "ok", _("Puesta en estación hecha a las {hora}", hora=_hora(hechos_["puesta_estacion"]))))
        else:
            lista.append(_c("puesta_estacion", "warn", _("Montura ecuatorial: no consta la puesta en estación de esta noche"), "ir_alineacion"))
    return lista, "alineacion" in hechos_ and not ali.get("en_curso")


def recomendacion_captura(ses, datos):
    """La exposición recomendada para el objeto de la sesión, con el giro del campo si se sabe dónde está."""
    obj = ses.get("objeto")
    if not obj or variante(ses) == "varios" or datos.get("lat") is None or datos.get("lon") is None:
        return exposicion_recomendada(datos.get("modo_montura"))
    alt, az = altaz(obj["ra_h"], obj["dec_deg"], float(datos["lat"]), float(datos["lon"]), datos.get("ahora"))
    return exposicion_recomendada(datos.get("modo_montura"), alt, az, float(datos["lat"]), radio_campo_px(datos.get("campo")))


def _comprobar_objeto(ses, hechos_, datos):
    if ses.get("varios"):
        # El plan de la noche es una lista que dura varias noches (un maratón Messier, por ejemplo): lo que se
        # elige aquí entra como prioritario, para esta noche, y lo que ya había pendiente sigue detrás
        plan = datos.get("plan") or {}
        n, otros = int(plan.get("prioritarios") or 0), int(plan.get("otros") or 0)
        if not n and not otros:
            return [_c("plan", "falta", _("Toca los objetos que quieres fotografiar esta noche"))], False
        lista = [_c("plan", "ok", ngettext("Un objeto elegido para esta noche", "{n} objetos elegidos para esta noche", n)) if n
                 else _c("plan", "warn", _("No has elegido ninguno: el plan seguirá con lo que ya tenía pendiente"))]
        if otros:
            lista.append(_c("otros", "ok", ngettext("El plan tiene además un objeto pendiente de otras noches",
                                                    "El plan tiene además {n} objetos pendientes de otras noches", otros)))
        return lista, True
    obj = ses.get("objeto")
    if not obj:
        return [_c("objeto", "falta", _("Elige el objeto que quieres fotografiar"))], False
    lista = []
    alt = altura_objeto(ses, datos)
    tipo = obj.get("tipo")      # dato del catálogo, en español: se traduce al pintarlo
    nombre = obj["nombre"] + (" (" + _(tipo) + ")" if tipo else "")
    minima = float(datos.get("altura_minima") or 0)
    if alt is None:
        lista.append(_c("objeto", "ok", nombre))
    elif alt < minima:
        lista.append(_c("objeto", "warn", _("{objeto}: ahora a {alt}° de altura, por debajo del mínimo de {minima}°",
                                            objeto=nombre, alt=fmt_num(alt, 0), minima=fmt_num(minima, 0))))
    else:
        lista.append(_c("objeto", "ok", _("{objeto}: ahora a {alt}° de altura", objeto=nombre, alt=fmt_num(alt, 0))))
    cabe = cabe_en_el_campo(obj.get("tamano_arcmin"), datos.get("campo"))
    if ses.get("mosaico"):
        lista.append(_c("mosaico", "ok", _("Se hará en mosaico: varias teselas que luego se unen")))
    elif cabe is False:
        lista.append(_c("mosaico", "warn", _("Mide {t}′ y no cabe en el campo de la cámara: mejor en mosaico",
                                             t=fmt_num(obj["tamano_arcmin"], 0)), "mosaico"))
    return lista, True


def _comprobar_captura(ses, hechos_, datos):
    cap = datos.get("captura") or {}
    rec = recomendacion_captura(ses, datos)
    exp = float(cap.get("exposicion") or 0)
    if exp > rec["maximo"]:
        nivel, texto = "warn", _("{exp} s es mucha exposición para esta montura", exp=fmt_num(exp, 1))
    elif 0 < exp < rec["minimo"]:
        nivel, texto = "warn", _("{exp} s es poca exposición: harán falta muchas más fotos", exp=fmt_num(exp, 1))
    else:
        nivel, texto = "ok", _texto_captura(cap)
    return [_c("captura", nivel, texto)], exp > 0


def _comprobar_calibracion(ses, hechos_, datos):
    lista = []
    cap = datos.get("captura") or {}
    trabajo = datos.get("trabajo_darks") or {}
    dark = datos.get("dark") or {}
    if trabajo.get("en_curso"):
        lista.append(_c("dark", "pendiente", _("Haciendo darks: {n} de {total}", n=trabajo.get("completadas") or 0, total=trabajo.get("total") or 0)))
    elif dark.get("existe"):
        cuando = _antiguedad(dark.get("antiguedad_min"))
        if dark.get("lejos") and dark.get("diferencia") is not None:
            lista.append(_c("dark", "warn", _("El dark más cercano está a {d} °C del sensor: conviene hacer darks nuevos",
                                              d=fmt_num(abs(dark["diferencia"]), 0)), "darks"))
        else:
            lista.append(_c("dark", "ok", _("Dark de {exp} s listo ({cuando})", exp=fmt_num(cap.get("exposicion") or 0, 1), cuando=cuando)
                            if cuando else _("Dark de {exp} s listo", exp=fmt_num(cap.get("exposicion") or 0, 1)), "darks"))
    else:
        lista.append(_c("dark", "warn", _("No hay dark para {exp} s con esta ganancia", exp=fmt_num(cap.get("exposicion") or 0, 1)), "darks"))
    if ses["actividad"] != "calibracion":
        lista.append(_semaforo_flat(datos, "warn"))
    return lista, not trabajo.get("en_curso")


def _semaforo_flat(datos, nivel_si_falta):
    flat = datos.get("flat") or {}
    if flat.get("existe"):
        cuando = _antiguedad(flat.get("antiguedad_min"))
        return _c("flat", "ok", _("Flat listo ({cuando})", cuando=cuando) if cuando else _("Flat listo"), "flats")
    return _c("flat", nivel_si_falta, _("No hay flat: el viñeteo y las motas de polvo no se corregirán"), "flats")


def _comprobar_enfoque(ses, hechos_, datos):
    if "enfoque" in hechos_:
        return [_c("enfoque", "ok", _("Enfocado a las {hora}", hora=_hora(hechos_["enfoque"])), "repetir_enfoque")], True
    return [_c("enfoque", "warn", _("No consta que hayas enfocado esta noche"), "ir_enfoque")], False


def estado_apuntado(ses, datos):
    """
    Dónde está el GoTo al objeto de la sesión: (fase, nivel, texto), con fase
    sin_ir | moviendo | centrando | centrado | sin_centrar | error.
    """
    obj = ses.get("objeto")
    if not obj:
        return "sin_ir", "falta", _("Falta elegir el objeto")
    ug = datos.get("ultimo_goto") or {}
    if ug.get("ra_h") is None or separacion(obj["ra_h"], obj["dec_deg"], float(ug["ra_h"]), float(ug["dec_deg"])) > SEPARACION_MISMO_OBJETO:
        return "sin_ir", "falta", _("La montura todavía no ha ido a {objeto}", objeto=obj["nombre"])
    if datos.get("moviendose"):
        return "moviendo", "pendiente", _("La montura va hacia {objeto}...", objeto=obj["nombre"])
    cen = datos.get("centrado") or {}
    if cen.get("en_curso"):
        return "centrando", "pendiente", cen.get("mensaje") or _("Centrando por plate solving...")
    if cen.get("previo"):
        return "centrando", "pendiente", cen["previo"].get("mensaje") or _("Esperando a que la montura se asiente...")
    objetivo = datos.get("centrado_objetivo")
    suyo = bool(objetivo) and separacion(obj["ra_h"], obj["dec_deg"], float(objetivo[0]), float(objetivo[1])) <= SEPARACION_MISMO_OBJETO
    if suyo and cen.get("resultado") == "centrado":
        error = cen.get("error_final")
        return "centrado", "ok", (_("{objeto} centrado, a {e}′ del centro", objeto=obj["nombre"], e=fmt_num(error, 1))
                                  if error is not None else _("{objeto} centrado", objeto=obj["nombre"]))
    if suyo and cen.get("resultado") in ("fuera_tolerancia", "sin_mejora"):
        return "sin_centrar", "warn", cen.get("detalle") or _("El centrado no llegó a la tolerancia")
    if suyo and cen.get("resultado") in ("error", "cancelado"):
        return "error", "err", cen.get("detalle") or _("El centrado no pudo terminar")
    if cen.get("omitido"):
        return "sin_centrar", "warn", cen["omitido"].get("mensaje") or _("No se ha centrado")
    return "sin_centrar", "warn", _("La montura está en {objeto}, sin centrado por plate solving", objeto=obj["nombre"])


def _comprobar_apuntar(ses, hechos_, datos):
    fase, nivel, texto = estado_apuntado(ses, datos)
    return [_c("apuntar", nivel, texto, "goto" if fase in ("sin_ir", "sin_centrar", "error") else None)], fase in ("centrado", "sin_centrar")


def _comprobar_listo(ses, hechos_, datos):
    lista = []
    obj = ses.get("objeto")
    if obj:
        __, nivel, texto = estado_apuntado(ses, datos)
        lista.append(_c("apuntar", "ok" if nivel == "ok" else "warn", texto))
    lista.append(_c("captura", "ok", _texto_captura(datos.get("captura") or {})))
    dark = datos.get("dark") or {}
    lista.append(_c("dark", "ok", _("Con dark")) if dark.get("existe") else _c("dark", "warn", _("Sin dark")))
    if "enfoque" in hechos_:
        lista.append(_c("enfoque", "ok", _("Enfocado a las {hora}", hora=_hora(hechos_["enfoque"]))))
    else:
        lista.append(_c("enfoque", "warn", _("Enfoque sin comprobar")))
    if datos.get("trabajo"):
        lista.append(_c("trabajo", "warn", _("Ahora mismo el equipo está con: {trabajo}", trabajo=datos["trabajo"])))
    return lista, True


# ── Cielo profundo en mosaico o con varios objetos ──

def _comprobar_mosaico(ses, hechos_, datos):
    obj = ses.get("objeto")
    if not obj:
        return [_c("mosaico", "falta", _("Falta elegir el objeto"))], False
    previsto = datos.get("mosaico_previsto") or {}
    rejilla = ses.get("rejilla") or {}
    lista = []
    if previsto.get("error"):
        return [_c("mosaico", "err", previsto["error"])], False
    tamano = previsto.get("tamano_deg")
    if tamano:
        lista.append(_c("mosaico", "ok", _("Rejilla de {f} × {c}: cubre {w}° × {h}°", f=rejilla.get("filas"), c=rejilla.get("columnas"),
                                           w=fmt_num(tamano[0], 1), h=fmt_num(tamano[1], 1))))
        if obj.get("tamano_arcmin") and float(obj["tamano_arcmin"]) > 60.0 * min(tamano):
            lista.append(_c("tamano", "warn", _("{objeto} mide {t}′ y la rejilla se queda corta: añade teselas",
                                                objeto=obj["nombre"], t=fmt_num(obj["tamano_arcmin"], 0))))
    if previsto.get("duracion_min") is not None:
        lista.append(_c("duracion", "ok", _("Unos {min} min: acabaría a las {hora}", min=previsto["duracion_min"], hora=previsto.get("fin_hora") or "")))
    if previsto.get("solape_min_pct") is not None and previsto["solape_min_pct"] < 8:
        lista.append(_c("solape", "warn", _("Con el giro del campo, alguna tesela solo solapará un {s} % con su vecina: sube el solape",
                                            s=fmt_num(previsto["solape_min_pct"], 0))))
    if datos.get("mosaico_en_curso"):
        lista.append(_c("en_curso", "warn", _("Ya hay un mosaico en marcha: detenlo antes de empezar otro")))
    return lista, True


def _resumen_comun(ses, hechos_, datos):
    """Los semáforos que comparten los tres finales de cielo profundo: exposición, dark, enfoque y equipo libre."""
    lista = [_c("captura", "ok", _("{exp} s por foto, ganancia {gan}", exp=fmt_num((datos.get("captura") or {}).get("exposicion") or 0, 1),
                                   gan=((datos.get("captura") or {}).get("ganancia") if (datos.get("captura") or {}).get("ganancia")
                                        not in (None, "") else _("la del driver"))))]
    dark = datos.get("dark") or {}
    lista.append(_c("dark", "ok", _("Con dark")) if dark.get("existe") else _c("dark", "warn", _("Sin dark")))
    if "enfoque" in hechos_:
        lista.append(_c("enfoque", "ok", _("Enfocado a las {hora}", hora=_hora(hechos_["enfoque"]))))
    else:
        lista.append(_c("enfoque", "warn", _("Enfoque sin comprobar")))
    if datos.get("trabajo"):
        lista.append(_c("trabajo", "warn", _("Ahora mismo el equipo está con: {trabajo}", trabajo=datos["trabajo"])))
    return lista


def _comprobar_listo_mosaico(ses, hechos_, datos):
    lista = [c for c in _comprobar_mosaico(ses, hechos_, datos)[0] if c["clave"] in ("mosaico", "duracion", "en_curso")]
    lista.extend(_resumen_comun(ses, hechos_, datos))
    return lista, bool(ses.get("objeto")) and not datos.get("mosaico_en_curso")


def _comprobar_listo_plan(ses, hechos_, datos):
    plan = datos.get("plan") or {}
    lista = []
    if plan.get("error"):
        lista.append(_c("plan", "warn", plan["error"]))
    else:
        n, fuera = int(plan.get("programados") or 0), int(plan.get("elegidos_fuera") or 0)
        if n:
            lista.append(_c("plan", "ok", ngettext("Un objeto cabe esta noche: acabaría a las {hora}",
                                                   "{n} objetos caben esta noche: acabaría a las {hora}", n, hora=plan.get("fin_previsto") or "")))
        else:
            lista.append(_c("plan", "warn", _("Ningún objeto del plan cabe esta noche")))
        if fuera:
            lista.append(_c("fuera", "warn", ngettext("Uno de los que has elegido no cabe esta noche y se queda para otra",
                                                      "{n} de los que has elegido no caben esta noche y se quedan para otra", fuera)))
    if plan.get("en_curso"):
        lista.append(_c("en_curso", "warn", _("El plan de la noche ya está en marcha")))
    lista.extend(_resumen_comun(ses, hechos_, datos))
    return lista, int(plan.get("pendientes") or 0) > 0 and not plan.get("en_curso")


# ── Rutas de día: Sol, Luna y paisaje ──

def _comprobar_filtro(ses, hechos_, datos):
    f = datos.get("filtro_solar") or {}
    if f.get("confirmado"):
        return [_c("filtro", "ok", _("Filtro solar confirmado a las {hora}", hora=f.get("hora") or "--:--"))], True
    return [_c("filtro", "err", _("Falta confirmar que el filtro solar está puesto"), "filtro")], False


def _comprobar_apuntar_disco(ses, hechos_, datos):
    es_sol = actividad(ses["actividad"]).get("astro") == "sol"
    alt = datos.get("altura_sol" if es_sol else "altura_luna")
    if alt is not None and alt < ALTURA_MINIMA_DISCO:
        return [_c("apuntar_disco", "err", _("El Sol está bajo el horizonte") if es_sol else _("La Luna está bajo el horizonte"))], False
    if es_sol and not (datos.get("filtro_solar") or {}).get("confirmado"):
        return [_c("apuntar_disco", "err", _("El filtro solar no está confirmado: vuelve al primer paso"))], False
    hecho = (ses.get("hechos_paso") or {}).get("apuntar_disco")
    if hecho:
        return [_c("apuntar_disco", "ok", hecho.get("nota") or (_("El Sol está centrado") if es_sol else _("La Luna está centrada")))], True
    if datos.get("moviendose"):
        lista = [_c("apuntar_disco", "pendiente", _("La montura va hacia el Sol...") if es_sol else _("La montura va hacia la Luna..."))]
    elif (datos.get("foto_dia") or {}).get("en_curso"):
        lista = [_c("apuntar_disco", "pendiente", _("Haciendo una foto para encontrar el disco..."))]
    else:
        lista = [_c("apuntar_disco", "falta", _("Lleva el telescopio al Sol y céntralo") if es_sol
                    else _("Lleva el telescopio a la Luna y céntrala"), "goto_disco")]
    if not datos.get("orientacion_camara"):
        lista.append(_c("orientacion", "warn", _("Aún no se conoce la orientación de la cámara (se aprende con un plate solving "
                                                 "de noche): el disco habrá que centrarlo a mano")))
    return lista, False


def _comprobar_encuadre(ses, hechos_, datos):
    if (ses.get("hechos_paso") or {}).get("encuadre"):
        return [_c("encuadre", "ok", _("Encuadre hecho"))], True
    return [_c("encuadre", "falta", _("Apunta el telescopio a lo que quieres fotografiar"), "encuadre")], False


def _comprobar_modo(ses, hechos_, datos):
    textos = {"foto": _("Vas a hacer fotos sueltas"), "video": _("Vas a grabar vídeo"), "timelapse": _("Vas a hacer un timelapse")}
    modo = ses.get("modo") if ses.get("modo") in MODOS else "foto"
    lista = [_c("modo", "ok", textos[modo])]
    if modo == "video" and not datos.get("video_disponible"):
        lista.append(_c("video", "warn", _("El vídeo necesita la cámara conectada por su SDK; con esta, usa las fotos o el timelapse")))
    return lista, True


def _comprobar_listo_camara(ses, hechos_, datos):
    astro = actividad(ses["actividad"]).get("astro")
    lista = []
    if astro == "sol":
        lista.extend(_comprobar_filtro(ses, hechos_, datos)[0])
    hecho = (ses.get("hechos_paso") or {}).get("apuntar_disco" if astro else "encuadre")
    if hecho:
        lista.append(_c("apuntado", "ok", hecho.get("nota") or (_("El Sol está centrado") if astro == "sol" else
                                                                _("La Luna está centrada") if astro == "luna" else _("Encuadre hecho"))))
    else:
        lista.append(_c("apuntado", "warn", _("Sin comprobar que el disco esté centrado") if astro else _("Encuadre sin comprobar")))
    if "enfoque" in hechos_:
        lista.append(_c("enfoque", "ok", _("Enfocado a las {hora}", hora=_hora(hechos_["enfoque"]))))
    else:
        lista.append(_c("enfoque", "warn", _("Enfoque sin comprobar")))
    lista.extend(_comprobar_modo(ses, hechos_, datos)[0])
    return lista, True


# ── Enfocar y fotos de calibración ──

def _comprobar_apuntar_enfoque(ses, hechos_, datos):
    hecho = (ses.get("hechos_paso") or {}).get("apuntar_enfoque")
    if hecho:
        return [_c("apuntar_enfoque", "ok", hecho.get("nota") or _("El telescopio apunta a algo con que enfocar"))], True
    cen = datos.get("centrado") or {}
    if datos.get("moviendose"):
        return [_c("apuntar_enfoque", "pendiente", _("La montura se está moviendo..."))], False
    if cen.get("en_curso") or cen.get("previo"):
        return [_c("apuntar_enfoque", "pendiente", cen.get("mensaje") or (cen.get("previo") or {}).get("mensaje")
                   or _("Centrando por plate solving..."))], False
    return [_c("apuntar_enfoque", "falta", _("Apunta el telescopio a algo con que enfocar"), "apuntar_enfoque")], False


def _comprobar_listo_enfoque(ses, hechos_, datos):
    if "enfoque" in hechos_:
        return [_c("enfoque", "ok", _("Enfocado a las {hora}", hora=_hora(hechos_["enfoque"])))], True
    return [_c("enfoque", "warn", _("Enfoque sin comprobar"))], True


def _comprobar_flats(ses, hechos_, datos):
    trabajo = datos.get("trabajo_flats") or {}
    if trabajo.get("en_curso"):
        return [_c("flat", "pendiente", trabajo.get("mensaje") or _("Haciendo los flats..."))], False
    semaforo = _semaforo_flat(datos, "falta")
    return [semaforo], semaforo["nivel"] == "ok"


def _comprobar_listo_calibracion(ses, hechos_, datos):
    cap = datos.get("captura") or {}
    dark = datos.get("dark") or {}
    exp = fmt_num(cap.get("exposicion") or 0, 1)
    if not dark.get("existe"):
        lista = [_c("dark", "warn", _("No hay dark para {exp} s con esta ganancia", exp=exp))]
    elif dark.get("lejos") and dark.get("diferencia") is not None:
        lista = [_c("dark", "warn", _("El dark más cercano está a {d} °C del sensor: conviene hacer darks nuevos",
                                      d=fmt_num(abs(dark["diferencia"]), 0)))]
    else:
        lista = [_c("dark", "ok", _("Dark de {exp} s listo", exp=exp))]
    lista.append(_semaforo_flat(datos, "warn"))
    return lista, True


def texto_biblioteca(biblioteca, captura, temperatura):
    """
    Una línea con los darks que hay para la exposición, la ganancia y el binning elegidos, por temperatura,
    y la del sensor ahora: «Para 20 s y ganancia 130 hay darks a 19, 20 y 21 °C. El sensor está a 18,5 °C.»
    """
    exp, gan, binning = captura.get("exposicion"), captura.get("ganancia"), captura.get("binning") or 1
    temps = sorted({round(d["temperatura"]) for d in biblioteca
                    if abs(float(d["exposicion"]) - float(exp or 0)) < 0.005 and (d.get("binning") or 1) == binning
                    and (gan is None or d.get("gain") is None or int(d["gain"]) == int(gan)) and d.get("temperatura") is not None})
    if temps:
        lista = ", ".join(fmt_num(t, 0) for t in temps)
        texto = ngettext("Para esa exposición y esa ganancia hay un dark, a {lista} °C.",
                         "Para esa exposición y esa ganancia hay {n} darks, a {lista} °C.", len(temps), lista=lista)
    else:
        texto = _("Para esa exposición y esa ganancia todavía no hay ningún dark con su temperatura apuntada.")
    if temperatura is not None:
        texto += " " + _("El sensor está ahora a {t} °C.", t=fmt_num(temperatura, 1))
    return texto


_COMPROBADORES = {"equipo": _comprobar_equipo, "alineacion": _comprobar_alineacion, "objeto": _comprobar_objeto,
                  "captura": _comprobar_captura, "calibracion": _comprobar_calibracion, "enfoque": _comprobar_enfoque,
                  "apuntar": _comprobar_apuntar, "listo": _comprobar_listo,
                  "filtro": _comprobar_filtro, "apuntar_disco": _comprobar_apuntar_disco, "encuadre": _comprobar_encuadre,
                  "modo": _comprobar_modo, "listo_camara": _comprobar_listo_camara,
                  "apuntar_enfoque": _comprobar_apuntar_enfoque, "listo_enfoque": _comprobar_listo_enfoque,
                  "flats": _comprobar_flats, "listo_calibracion": _comprobar_listo_calibracion,
                  "mosaico": _comprobar_mosaico, "listo_mosaico": _comprobar_listo_mosaico, "listo_plan": _comprobar_listo_plan}


def comprobaciones(ses, hechos_, datos):
    """(lista de semáforos del paso en curso, ¿se puede pasar al siguiente?)."""
    return _COMPROBADORES[_paso(ses)["clave"]](ses, hechos_, datos)


# ───────────────────────── Estado para la web ─────────────────────────

def estado(datos):
    """Todo lo que pinta la web: el menú, la sesión con sus pasos y los semáforos del paso en curso."""
    cerrada = observar(datos)
    with _lock:
        ses, hechos_ = sesion(), hechos()
    salida = {"activa": bool(ses), "terminada": cerrada, "de_dia": bool(datos.get("de_dia")), "menu": menu(datos), "sesion": None}
    if not ses:
        return salida
    act = actividad(ses["actividad"])
    pasos = _pasos(ses["actividad"], ses)
    i = _indice(ses)
    paso = pasos[i]
    lista, puede = comprobaciones(ses, hechos_, datos)
    salida["sesion"] = {
        "actividad": ses["actividad"], "titulo": _tr(act, "titulo"), "imagen": ses["actividad"] + ".jpg",
        "paso": paso["clave"], "indice": i, "total": len(pasos),
        "pasos": [{"clave": p["clave"], "titulo": _tr(p, "titulo"),
                   "estado": "saltado" if p["clave"] in ses["saltados"] and j != i else ("hecho" if j < i else "actual" if j == i else "pendiente")}
                  for j, p in enumerate(pasos)],
        "saltable": bool(paso.get("saltable")), "aviso_saltar": _tr(AVISO_SALTAR, paso["clave"]),
        "pantalla": paso.get("pantalla"), "banda": _tr(paso, "banda"),
        "objeto": ses.get("objeto"), "mosaico": bool(ses.get("mosaico")),
        "astro": act.get("astro"), "modo": ses.get("modo") if ses.get("modo") in MODOS else "foto",
        "variante": variante(ses), "rejilla": ses.get("rejilla"),
        "comprobaciones": lista, "puede_seguir": puede,
    }
    return salida
