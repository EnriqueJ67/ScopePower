# guia.py
#
# Guía «¿Qué quieres hacer hoy?»: el botón principal del inicio pregunta qué se quiere
# hacer y lleva de la mano hasta dejarlo a un toque de «Empezar». No sustituye a ninguna
# pantalla: une las que ya hay (alineación, GoTo con centrado, darks y flats, enfoque,
# apilado) con una lista de pasos y un semáforo por comprobación.
#
# Tres partes, sin Flask ni COM (se prueba en seco con herramientas/probar_guia.py):
#
#   1. ACTIVIDADES y PASOS: lo que ofrece el menú y los pasos de cada ruta guiada. De
#      momento solo cielo profundo va guiado; las demás actividades son accesos directos
#      que dejan su pantalla preparada.
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

from idioma import _, N_, fmt_num

log = logging.getLogger("scopepower.guia")

RUTA = os.path.join("datos", "guia.json")
CADUCA_S = 12 * 3600             # una sesión o un «ya está hecho» de hace más de esto es de otra noche
ALTURA_CENIT = 75.0              # por encima, una altacimutal alarga las estrellas (deriva en acimut)
ALTURA_ENFOQUE = (35.0, 72.0)    # altura cómoda de la estrella para enfocar
MARGEN_CAMPO = 0.8               # el objeto «cabe» si ocupa menos de esta fracción del lado corto del campo
SEPARACION_MISMO_OBJETO = 0.5    # grados: el último GoTo es «el del objeto» si cae a menos de esto
MARCABLES = ("alineacion", "puesta_estacion", "enfoque")
MODOS_ECUATORIALES = ("ecuatorial", "ecuatorial_alemana")

# El fondo de cada tarjeta es static/guia/<clave>.jpg (ver herramientas/imagenes_guia.py)
ACTIVIDADES = [
    {"clave": "cielo_profundo", "titulo": N_("Cielo profundo"), "cuando": "noche", "guiada": True,
     "desc": N_("Galaxias, nebulosas y cúmulos: apilado en tiempo real")},
    {"clave": "luna", "titulo": N_("Luna"), "cuando": "luna", "destino": "/foto-prueba", "al_llegar": "dia",
     "preferencias": {"modo_dia": "on", "foto_dia_objetivo": "luna"},
     "desc": N_("Foto o vídeo de la Luna, con la exposición automática")},
    {"clave": "sol", "titulo": N_("Sol"), "cuando": "dia", "destino": "/foto-prueba", "al_llegar": "dia",
     "filtro_solar": True, "preferencias": {"modo_dia": "on", "foto_dia_objetivo": "sol"},
     "desc": N_("Foto o vídeo del Sol, siempre con el filtro solar puesto")},
    {"clave": "paisaje", "titulo": N_("Paisaje"), "cuando": "dia", "destino": "/foto-prueba", "al_llegar": "dia",
     "preferencias": {"modo_dia": "on", "foto_dia_objetivo": "paisaje"},
     "desc": N_("Fotos de día de algo en tierra")},
    {"clave": "enfocar", "titulo": N_("Enfocar"), "cuando": "siempre", "destino": "/foto-prueba", "al_llegar": "enfoque",
     "desc": N_("Afina el enfoque antes de hacer fotos")},
    {"clave": "calibracion", "titulo": N_("Fotos de calibración"), "cuando": "siempre", "destino": "/calibracion",
     "desc": N_("Darks y flats para limpiar las fotos")},
    {"clave": "galeria", "titulo": N_("Galería"), "cuando": "siempre", "destino": "/galeria",
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
        {"clave": "captura", "titulo": N_("Exposición")},
        {"clave": "calibracion", "titulo": N_("Darks y flats")},
        {"clave": "enfoque", "titulo": N_("Enfoque"), "saltable": True, "pantalla": "/foto-prueba",
         "banda": N_("Enfoca y pulsa «Hecho, siguiente»")},
        {"clave": "apuntar", "titulo": N_("Apuntar y centrar")},
        {"clave": "listo", "titulo": N_("Listo"), "pantalla": "/apilado",
         "banda": N_("Todo listo: pulsa «Empezar»")},
    ],
}
AVISO_SALTAR = {
    "alineacion": N_("Sin alinear, el GoTo puede dejar el objeto fuera del campo y el centrado tardará más."),
    "enfoque": N_("Si el enfoque no está fino, las estrellas saldrán gordas y se perderán las débiles."),
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


def _pasos(clave_actividad):
    return PASOS.get(clave_actividad, [])


def _paso(ses):
    for p in _pasos(ses["actividad"]):
        if p["clave"] == ses["paso"]:
            return p
    return _pasos(ses["actividad"])[0]


def _indice(ses):
    claves = [p["clave"] for p in _pasos(ses["actividad"])]
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
        if clave_paso not in [p["clave"] for p in _pasos(ses["actividad"])]:
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
        pasos = _pasos(ses["actividad"])
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

def altura(ra_h, dec_deg, lat, lon, ts=None):
    """Altura sobre el horizonte, en grados, con la precisión que pide un semáforo (≈ medio grado)."""
    ts = _ahora() if ts is None else ts
    dias = ts / 86400.0 + 2440587.5 - 2451545.0
    tsl = (280.46061837 + 360.98564736629 * dias + lon) % 360.0          # tiempo sidéreo local, en grados
    h = math.radians(tsl - ra_h * 15.0)
    fi, de = math.radians(lat), math.radians(dec_deg)
    seno = math.sin(fi) * math.sin(de) + math.cos(fi) * math.cos(de) * math.cos(h)
    return math.degrees(math.asin(max(-1.0, min(1.0, seno))))


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


def exposicion_recomendada(modo_montura, altura_objeto=None):
    """
    Margen razonable de exposición por foto: {"minimo", "maximo", "texto"}. Una altacimutal no compensa
    el giro del campo, y cerca del cénit además deriva en acimut; una ecuatorial aguanta más.
    """
    if modo_montura in MODOS_ECUATORIALES:
        return {"minimo": 15.0, "maximo": 60.0,
                "texto": _("Con la montura ecuatorial bien puesta en estación, entre 15 y 60 s por foto.")}
    if altura_objeto is not None and altura_objeto >= ALTURA_CENIT:
        return {"minimo": 5.0, "maximo": 10.0,
                "texto": _("El objeto está muy alto ({alt}°): cerca del cénit una montura altacimutal alarga las estrellas. "
                           "Mejor entre 5 y 10 s, o esperar a que baje.", alt=fmt_num(altura_objeto, 0))}
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
                       "guiada": bool(a.get("guiada")), "filtro_solar": bool(a.get("filtro_solar")),
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
    for clave, nombre_ok, nombre_no in (("montura", _("Montura conectada"), _("La montura no está conectada")),
                                        ("camara", _("Cámara conectada"), _("La cámara no está conectada"))):
        if datos.get(clave):
            lista.append(_c(clave, "ok", nombre_ok))
        elif datos.get("conectando"):
            lista.append(_c(clave, "pendiente", _("Conectando...")))
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
    if datos.get("de_dia"):
        desde = datos.get("noche_desde")
        lista.append(_c("noche", "warn", _("Todavía es de día: sin estrellas no hay centrado ni apilado hasta las {hora}.", hora=desde)
                        if desde else _("Todavía es de día: sin estrellas no hay centrado ni apilado.")))
    return lista, bool(datos.get("montura") and datos.get("camara") and not disco.get("sin_espacio"))


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


def _comprobar_objeto(ses, hechos_, datos):
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
    rec = exposicion_recomendada(datos.get("modo_montura"), altura_objeto(ses, datos))
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
    flat = datos.get("flat") or {}
    if flat.get("existe"):
        cuando = _antiguedad(flat.get("antiguedad_min"))
        lista.append(_c("flat", "ok", _("Flat listo ({cuando})", cuando=cuando) if cuando else _("Flat listo"), "flats"))
    else:
        lista.append(_c("flat", "warn", _("No hay flat: el viñeteo y las motas de polvo no se corregirán"), "flats"))
    return lista, not trabajo.get("en_curso")


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


_COMPROBADORES = {"equipo": _comprobar_equipo, "alineacion": _comprobar_alineacion, "objeto": _comprobar_objeto,
                  "captura": _comprobar_captura, "calibracion": _comprobar_calibracion, "enfoque": _comprobar_enfoque,
                  "apuntar": _comprobar_apuntar, "listo": _comprobar_listo}


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
    pasos = _pasos(ses["actividad"])
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
        "comprobaciones": lista, "puede_seguir": puede,
    }
    return salida
