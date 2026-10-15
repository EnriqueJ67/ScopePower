# plan_sesion.py
#
# "Plan de la noche": una lista de objetos que la app fotografía sola, uno detrás
# de otro (GoTo + centrado, apilado hasta N fotos apiladas, guardar en la galería,
# siguiente), como el modo Plan de los Seestar. Pensado para el maratón Messier:
# el plan es un objetivo que se completa a lo largo de varias noches.
#
# Tres partes, sin Flask ni COM (se prueba en seco):
#
#   1. Persistencia en datos/plan.json: objetos con sus ajustes (fotos, zoom,
#      exposición) y su estado (pendiente, hecho...), ajustes del plan, y los
#      valores de config.ini que hay que restaurar si la app se cierra a medias.
#   2. Planificador: con la ventana nocturna y las curvas de altura de Esta
#      noche, `simular` recorre la noche con un reloj y asigna a cada objeto un
#      tramo solo si le caben sus N fotos antes de ponerse o de amanecer; el
#      que antes se pone (más al oeste) va primero. La misma simulación decide
#      el siguiente objeto real y dibuja el cronograma de la web.
#   3. TrabajoPlan: el hilo que ejecuta el plan con las piezas de ScopePower
#      inyectadas (goto síncrono, bucle de captura, guardado en galería...).

import os
import re
import json
import copy
import uuid
import time
import logging
import threading
from collections import deque
from datetime import datetime

import numpy as np
from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, get_body
import astropy.units as u
from unidecode import unidecode

import esta_noche
from idioma import _, ngettext, N_, formato_fecha

log = logging.getLogger("scopepower.plan")

RUTA = os.path.join("datos", "plan.json")
PASO_MIN = 5                     # muestreo de la altura (más fino que Esta noche: el "hasta" manda en el orden)
FIJO_POR_OBJETO_S = 120.0        # GoTo + centrado + guardar en la galería
EXTRA_POR_FOTO_DEFECTO_S = 8.0   # procesado y descartes, mientras no haya medida real
TASA_ACEPTACION_DEFECTO = 0.8    # fotos aceptadas / fotos hechas, mientras no haya medida real
ZOOMS = (0, 25, 50, 75)
CREPUSCULOS = {"nautico": -12.0, "astronomico": -18.0}
ESTADOS = ("pendiente", "en_curso", "hecho", "saltado", "error")
# Planetas y Luna que el plan sabe calcular (clave astropy); el Sol no se admite
CUERPOS = {"luna": "moon", "mercurio": "mercury", "venus": "venus", "marte": "mars", "jupiter": "jupiter",
           "saturno": "saturn", "urano": "uranus", "neptuno": "neptune"}
NOMBRES_CUERPOS = {"moon": N_("Luna"), "mercury": N_("Mercurio"), "venus": N_("Venus"), "mars": N_("Marte"), "jupiter": N_("Júpiter"),
                   "saturn": N_("Saturno"), "uranus": N_("Urano"), "neptune": N_("Neptuno")}

AJUSTES_DEFECTO = {
    "altura_minima": 25.0,       # grados; la efectiva es max(esta, la general de config.ini)
    "fotos_por_defecto": 20,
    "zoom_por_defecto": 0,
    "minutos_sin_fotos": 15,     # sin ninguna foto aceptada en este tiempo (nubes) se salta el objeto
    "usar_dark": True,
    "centrar": True,             # centrado por plate solving tras el GoTo
    "max_intentos": 2,           # por noche
    "crepusculo": "nautico",
    "probar_de_dia": False,      # SOLO PRUEBAS: la "noche" son las 6 h siguientes, aunque sea de día
}

_lock = threading.RLock()
_cache = {"plan": None, "version": 0}


# ─────────────────────────────────────────────────────────────────────────────
#  Persistencia
# ─────────────────────────────────────────────────────────────────────────────

def _plan_vacio():
    return {"version": 1, "ajustes": dict(AJUSTES_DEFECTO), "restaurar": None,
            "medido": {}, "objetos": []}


def _normalizar_plan(plan):
    """Completa lo que falte (ficheros de versiones anteriores o editados a mano)."""
    base = _plan_vacio()
    if not isinstance(plan, dict):
        return base
    ajustes = dict(AJUSTES_DEFECTO)
    ajustes.update({k: v for k, v in (plan.get("ajustes") or {}).items() if k in AJUSTES_DEFECTO})
    base["ajustes"] = _validar_ajustes(ajustes)
    base["restaurar"] = plan.get("restaurar") if isinstance(plan.get("restaurar"), dict) else None
    base["medido"] = plan.get("medido") if isinstance(plan.get("medido"), dict) else {}
    objetos = []
    for o in plan.get("objetos") or []:
        try:
            objetos.append(_normalizar_objeto(o))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("Objeto del plan ignorado (%s): %s", e, o)
    base["objetos"] = objetos
    return base


def _normalizar_objeto(o):
    n = {
        "id": str(o.get("id") or _nuevo_id()),
        "nombre": " ".join(str(o["nombre"]).split())[:60],
        "ra_h": float(o["ra_h"]) % 24.0,
        "dec_deg": max(-90.0, min(90.0, float(o["dec_deg"]))),
        "tipo": str(o.get("tipo") or ""),
        "dinamico": o.get("dinamico") or None,
        "fotos": max(1, min(999, int(o.get("fotos") or AJUSTES_DEFECTO["fotos_por_defecto"]))),
        "zoom": int(o.get("zoom") or 0) if int(o.get("zoom") or 0) in ZOOMS else 0,
        "exposicion": (float(o["exposicion"]) if o.get("exposicion") not in (None, "", 0) else None),
        "prioritario": bool(o.get("prioritario", False)),
        "estado": o.get("estado") if o.get("estado") in ESTADOS else "pendiente",
        "intentos": int(o.get("intentos") or 0),
        "motivo": str(o.get("motivo") or ""),
        "resultado": o.get("resultado") if isinstance(o.get("resultado"), dict) else None,
    }
    if n["estado"] == "en_curso":          # la app se cerró a medias
        n["estado"] = "pendiente"
    return n


def _validar_ajustes(a):
    v = dict(a)
    v["altura_minima"] = max(5.0, min(60.0, float(v.get("altura_minima", 25))))
    v["fotos_por_defecto"] = max(1, min(999, int(v.get("fotos_por_defecto", 20))))
    z = int(v.get("zoom_por_defecto", 0) or 0)
    v["zoom_por_defecto"] = z if z in ZOOMS else 0
    v["minutos_sin_fotos"] = max(3, min(60, int(v.get("minutos_sin_fotos", 15))))
    v["usar_dark"] = bool(v.get("usar_dark", True))
    v["centrar"] = bool(v.get("centrar", True))
    v["max_intentos"] = max(1, min(5, int(v.get("max_intentos", 2))))
    v["crepusculo"] = v.get("crepusculo") if v.get("crepusculo") in CREPUSCULOS else "nautico"
    v["probar_de_dia"] = bool(v.get("probar_de_dia", False))
    return v


def _cargar():
    with _lock:
        if _cache["plan"] is None:
            plan = None
            try:
                if os.path.exists(RUTA):
                    with open(RUTA, encoding="utf-8") as f:
                        plan = json.load(f)
            except (OSError, ValueError) as e:
                log.warning("No se pudo leer %s (%s): se empieza con un plan vacío", RUTA, e)
            _cache["plan"] = _normalizar_plan(plan)
        return _cache["plan"]


def _escribir(plan):
    os.makedirs(os.path.dirname(RUTA) or ".", exist_ok=True)
    tmp = RUTA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    os.replace(tmp, RUTA)


def leer():
    """Copia del plan completo (ajustes, objetos, restaurar, medido)."""
    with _lock:
        return copy.deepcopy(_cargar())


def version():
    with _lock:
        return _cache["version"]


def _modificar(fn):
    """Aplica fn(plan) bajo el cerrojo, guarda y sube la versión. Devuelve lo que devuelva fn."""
    with _lock:
        plan = _cargar()
        resultado = fn(plan)
        _cache["version"] += 1
        try:
            _escribir(plan)
        except OSError as e:
            log.error("No se pudo guardar %s: %s", RUTA, e)
        return copy.deepcopy(resultado)


def _nuevo_id():
    return uuid.uuid4().hex[:6]


def normalizar_clave(nombre):
    return re.sub(r"[^a-z0-9]", "", unidecode(str(nombre or "")).lower())


def _buscar(plan, id_):
    for o in plan["objetos"]:
        if o["id"] == id_:
            return o
    return None


def anadir(nombre, ra_h, dec_deg, tipo="", dinamico=None, fotos=None, zoom=None, exposicion=None,
           estado="pendiente", resultado=None, prioritario=False):
    """Añade un objeto. Devuelve (objeto, duplicado): si ya había uno con ese nombre no se repite."""
    def fn(plan):
        clave = normalizar_clave(nombre)
        for o in plan["objetos"]:
            if normalizar_clave(o["nombre"]) == clave:
                return o, True
        a = plan["ajustes"]
        o = _normalizar_objeto({
            "id": _nuevo_id(), "nombre": nombre, "ra_h": ra_h, "dec_deg": dec_deg, "tipo": tipo,
            "dinamico": dinamico, "fotos": fotos if fotos else a["fotos_por_defecto"], "prioritario": prioritario,
            "zoom": zoom if zoom is not None else a["zoom_por_defecto"], "exposicion": exposicion,
            "estado": estado if estado in ESTADOS else "pendiente", "resultado": resultado,
        })
        plan["objetos"].append(o)
        return o, False
    return _modificar(fn)


def editar(id_, **campos):
    """Cambia fotos, zoom, exposicion o nombre. None en exposicion = la de config.ini."""
    def fn(plan):
        o = _buscar(plan, id_)
        if o is None:
            return None
        if "fotos" in campos:
            o["fotos"] = max(1, min(999, int(campos["fotos"])))
        if "zoom" in campos:
            z = int(campos["zoom"] or 0)
            o["zoom"] = z if z in ZOOMS else 0
        if "exposicion" in campos:
            e = campos["exposicion"]
            o["exposicion"] = float(e) if e not in (None, "", 0, "0") else None
        if "prioritario" in campos:
            o["prioritario"] = bool(campos["prioritario"])
        if "nombre" in campos and str(campos["nombre"]).strip():
            o["nombre"] = " ".join(str(campos["nombre"]).split())[:60]
        return o
    return _modificar(fn)


def borrar(id_):
    def fn(plan):
        antes = len(plan["objetos"])
        plan["objetos"] = [o for o in plan["objetos"] if o["id"] != id_]
        return len(plan["objetos"]) < antes
    return _modificar(fn)


def vaciar():
    return _modificar(lambda plan: plan.update(objetos=[]))


def guardar_ajustes(cambios):
    def fn(plan):
        a = dict(plan["ajustes"])
        a.update({k: v for k, v in (cambios or {}).items() if k in AJUSTES_DEFECTO})
        plan["ajustes"] = _validar_ajustes(a)
        return plan["ajustes"]
    return _modificar(fn)


def ajustes():
    return leer()["ajustes"]


def reiniciar_resultados():
    """Todo a pendiente: para repetir el maratón desde cero."""
    def fn(plan):
        for o in plan["objetos"]:
            o.update(estado="pendiente", intentos=0, motivo="", resultado=None)
    return _modificar(fn)


def cambiar_estado(id_, estado):
    """'pendiente' (repetir un objeto hecho o parcial) o 'hecho' (darlo por bueno a mano)."""
    if estado not in ("pendiente", "hecho"):
        raise ValueError(_("Estado no válido"))

    def fn(plan):
        o = _buscar(plan, id_)
        if o is None:
            return None
        o["estado"] = estado
        o["intentos"] = 0
        o["motivo"] = "" if estado == "pendiente" else _("marcado a mano")
        if estado == "hecho" and not o["resultado"]:
            o["resultado"] = {"archivo": None, "fotos": None, "fecha": datetime.now().strftime(formato_fecha()),
                              "motivo": _("marcado a mano"), "parcial": False}
        return o
    return _modificar(fn)


def preparar_noche():
    """Al empezar una noche: lo saltado o con error vuelve a pendiente (los intentos cuentan por noche)."""
    def fn(plan):
        for o in plan["objetos"]:
            if o["estado"] in ("saltado", "error", "en_curso"):
                o["estado"] = "pendiente"
            if o["estado"] == "pendiente":
                o["intentos"] = 0
                o["motivo"] = ""
        return sum(1 for o in plan["objetos"] if o["estado"] == "pendiente")
    return _modificar(fn)


def marcar(id_, estado, motivo="", resultado=None, sumar_intento=False):
    def fn(plan):
        o = _buscar(plan, id_)
        if o is None:
            return None
        o["estado"] = estado
        o["motivo"] = motivo or ""
        if resultado is not None:
            o["resultado"] = resultado
        if sumar_intento:
            o["intentos"] += 1
        return o
    return _modificar(fn)


def actualizar_coords(id_, ra_h, dec_deg):
    def fn(plan):
        o = _buscar(plan, id_)
        if o is not None:
            o["ra_h"], o["dec_deg"] = float(ra_h) % 24.0, float(dec_deg)
        return o
    return _modificar(fn)


def guardar_restaurar(valores):
    """Valores de config.ini que hay que devolver al terminar el plan (None = nada pendiente)."""
    return _modificar(lambda plan: plan.update(restaurar=valores))


def guardar_medido(valores):
    def fn(plan):
        plan["medido"].update(valores)
        return plan["medido"]
    return _modificar(fn)


def restaurar_pendiente(guardar_config):
    """Al arrancar la app: si el plan se quedó a medias, devuelve a config.ini los valores originales."""
    plan = leer()
    if plan["restaurar"]:
        try:
            guardar_config("Dispositivos", plan["restaurar"])
            log.info("Plan: valores de config.ini restaurados tras un cierre a medias: %s", plan["restaurar"])
        except Exception as e:
            log.warning("Plan: no se pudieron restaurar los valores de config.ini: %s", e)
        guardar_restaurar(None)


def resumen(plan=None):
    plan = plan or leer()
    objetos = plan["objetos"]
    return {"total": len(objetos),
            "hechos": sum(1 for o in objetos if o["estado"] == "hecho"),
            "pendientes": sum(1 for o in objetos if o["estado"] != "hecho")}


def exportar():
    """Plan completo con estados y resultados (copia de seguridad y para seguir en otro PC)."""
    plan = leer()
    return {"scopepower_plan": 1, "exportado": datetime.now().isoformat(timespec="seconds"),
            "ajustes": plan["ajustes"], "objetos": plan["objetos"]}


def importar(datos, resolver_nombre):
    """
    `datos`: dict exportado por ScopePower (con "objetos") o texto con un nombre por
    línea. `resolver_nombre(nombre)` -> {"nombre", "ra_h", "dec_deg", "tipo", "dinamico"} o None.
    Los objetos que ya existen se conservan (y su estado si el importado va más avanzado).
    """
    anadidos, duplicados, no_encontrados = 0, 0, []
    entradas = []
    if isinstance(datos, dict):
        for o in datos.get("objetos") or []:
            if isinstance(o, dict) and o.get("nombre"):
                entradas.append(o)
        if isinstance(datos.get("ajustes"), dict) and not datos.get("objetos"):
            guardar_ajustes(datos["ajustes"])
    else:
        for linea in str(datos or "").splitlines():
            linea = linea.split("#", 1)[0].strip().strip(",;")
            if linea:
                entradas.append({"nombre": linea})
    for e in entradas:
        nombre = str(e["nombre"]).strip()
        if e.get("ra_h") is not None and e.get("dec_deg") is not None:
            ficha = {"nombre": nombre, "ra_h": e["ra_h"], "dec_deg": e["dec_deg"], "tipo": e.get("tipo", ""),
                     "dinamico": e.get("dinamico")}
        else:
            try:
                ficha = resolver_nombre(nombre)
            except Exception as ex:
                log.warning("Plan: no se pudo resolver %s: %s", nombre, ex)
                ficha = None
        if not ficha:
            no_encontrados.append(nombre)
            continue
        o, dup = anadir(ficha["nombre"], ficha["ra_h"], ficha["dec_deg"], ficha.get("tipo", ""),
                        ficha.get("dinamico"), fotos=e.get("fotos"), zoom=e.get("zoom"), prioritario=bool(e.get("prioritario")),
                        exposicion=e.get("exposicion"),
                        estado=e.get("estado", "pendiente"), resultado=e.get("resultado"))
        if dup:
            duplicados += 1
            # Un hecho importado no se pierde
            if e.get("estado") == "hecho" and o["estado"] != "hecho":
                marcar(o["id"], "hecho", "importado", e.get("resultado"))
        else:
            anadidos += 1
    return {"anadidos": anadidos, "duplicados": duplicados, "no_encontrados": no_encontrados}


def messier_completo(catalogo=None):
    """Añade los 110 Messier del catálogo de Esta noche (los que ya estén se respetan)."""
    catalogo = catalogo if catalogo is not None else esta_noche.cargar_catalogo()
    messier = [o for o in catalogo if re.match(r"^M ?\d+$", o["id"] or "")]
    messier.sort(key=lambda o: int(o["id"][1:].strip()))
    anadidos = duplicados = 0
    for o in messier:
        __, dup = anadir(o["id"], o["ra_h"], o["dec_deg"], o["tipo"])
        if dup:
            duplicados += 1
        else:
            anadidos += 1
    return {"anadidos": anadidos, "duplicados": duplicados}


# ─────────────────────────────────────────────────────────────────────────────
#  Planificador
# ─────────────────────────────────────────────────────────────────────────────

def _hhmm(t):
    return esta_noche._hora_local(t).strftime("%H:%M") if t is not None else None


def _ms(t):
    return int(round(t.unix * 1000)) if t is not None else None


def coordenadas_dinamicas(objeto, lat, lon, alt_m, cuando):
    """(ra_h, dec_deg) de un planeta, la Luna o un cuerpo menor en `cuando` (Time); las guardadas si falla."""
    d = objeto.get("dinamico")
    try:
        if d in NOMBRES_CUERPOS:
            lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
            c = get_body(d, cuando, lugar)
            return float(c.ra.hour), float(c.dec.deg)
        if isinstance(d, str) and d.startswith("mpc:"):
            import cuerpos_menores
            r = cuerpos_menores.coordenadas(d[4:], lat, lon, alt_m, cuando.to_datetime())
            if r:
                return float(r[0]), float(r[1])
    except Exception as e:
        log.warning("Plan: sin efemérides de %s (%s); se usan las coordenadas guardadas", objeto.get("nombre"), e)
    return float(objeto["ra_h"]), float(objeto["dec_deg"])


class Visibilidad:
    """Curvas de altura de los objetos durante la noche, con lo que el planificador necesita."""

    def __init__(self, inicio, fin, ahora, de_noche, ts, por_id, altura_minima):
        self.inicio, self.fin, self.ahora, self.de_noche = inicio, fin, ahora, de_noche
        self.ts, self.por_id, self.altura_minima = ts, por_id, altura_minima

    @property
    def t0(self):
        return self.ts[0]

    def altura_en(self, id_, t):
        v = self.por_id.get(id_)
        if v is None or len(self.ts) == 0:
            return None
        i = int(round((t - self.ts[0]).sec / (PASO_MIN * 60.0)))
        i = max(0, min(len(v["curva"]) - 1, i))
        return float(v["curva"][i])

    def json(self, id_):
        v = self.por_id.get(id_)
        if v is None:
            return None
        return {"alt_ahora": v["alt_ahora"], "alt_max": v["alt_max"], "hora_max": v["hora_max"],
                "visible_ahora": v["visible_ahora"], "sube": v["sube"],
                "desde": _hhmm(v["desde"]), "hasta": _hhmm(v["hasta"]),
                "desde_ms": _ms(v["desde"]), "hasta_ms": _ms(v["hasta"]),
                "desde_el_anochecer": bool(v["sube"] and v["desde"] is not None and (v["desde"] - self.ts[0]).sec < 1)}


def visibilidad(objetos, lat, lon, alt_m, altura_minima, ahora=None, crepusculo="nautico", probar_de_dia=False):
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    ahora = ahora or Time.now()
    if probar_de_dia:
        # Pruebas con simuladores: la "noche" empieza ahora y dura 6 h, sea la hora que sea
        inicio, fin = ahora, ahora + 6 * u.hour
    else:
        inicio, fin = esta_noche.ventana_nocturna(lugar, ahora, CREPUSCULOS.get(crepusculo, -12.0))
    de_noche = bool((inicio - ahora).sec < 60)
    t0 = ahora if de_noche else inicio
    n = max(2, int((fin - t0).sec // (PASO_MIN * 60)) + 1)
    ts = t0 + np.arange(n) * PASO_MIN * u.min
    medio = t0 + (fin - t0) / 2
    por_id = {}
    if not objetos:
        return Visibilidad(inicio, fin, ahora, de_noche, ts, por_id, altura_minima)
    ras, decs = [], []
    for o in objetos:
        if o.get("dinamico"):
            ra, dec = coordenadas_dinamicas(o, lat, lon, alt_m, medio)
        else:
            ra, dec = float(o["ra_h"]), float(o["dec_deg"])
        ras.append(ra)
        decs.append(dec)
    coords = SkyCoord(ra=ras * u.hourangle, dec=decs * u.deg)
    alturas = coords[:, None].transform_to(AltAz(obstime=ts[None, :], location=lugar)).alt.deg
    alt_ahora = coords.transform_to(AltAz(obstime=ahora, location=lugar)).alt.deg
    for i, o in enumerate(objetos):
        curva = alturas[i]
        mask = curva >= altura_minima
        i_max = int(np.argmax(curva))
        desde = hasta = None
        if mask.any():
            j0 = int(np.argmax(mask))
            resto = ~mask[j0:]
            j1 = j0 + int(np.argmax(resto)) if resto.any() else n - 1
            desde, hasta = ts[j0], ts[j1]
        por_id[o["id"]] = {
            "curva": curva, "alt_ahora": round(float(alt_ahora[i]), 1), "alt_max": round(float(curva[i_max]), 1),
            "hora_max": _hhmm(ts[i_max]), "t_max": ts[i_max], "visible_ahora": bool(de_noche and alt_ahora[i] >= altura_minima),
            "desde": desde, "hasta": hasta, "sube": bool(mask.any()),
        }
    return Visibilidad(inicio, fin, ahora, de_noche, ts, por_id, altura_minima)


def tiempo_por_foto(exposicion_s, medido=None):
    """
    Segundos por foto APILADA. Con medida real de una noche (segundos por foto aceptada
    y exposición con la que se midió) se escala a la exposición pedida; sin ella, la
    exposición más el procesado, dividido por la tasa de aceptación.
    """
    medido = medido or {}
    if medido.get("segundos_por_foto") and medido.get("exposicion"):
        extra = max(2.0, float(medido["segundos_por_foto"]) - float(medido["exposicion"]))
        return float(exposicion_s) + extra
    return (float(exposicion_s) + EXTRA_POR_FOTO_DEFECTO_S) / TASA_ACEPTACION_DEFECTO


def fotos_posibles(objeto, v, t, tpf):
    """Fotos apiladas que caben empezando en `t` antes de que el objeto baje o amanezca."""
    if v is None or v.get("hasta") is None:
        return 0
    seg = (v["hasta"] - t).sec - FIJO_POR_OBJETO_S
    return max(0, min(int(objeto["fotos"]), int(seg // tpf)))


def simular(objetos, vis, tpf_de, max_intentos=2, ahora=None):
    """
    Recorre la noche con un reloj y asigna un tramo a cada objeto que quepa entero.
    Los objetos "prioritarios" reservan antes su hueco centrado en el momento en que
    están más altos (paso por el meridiano); los demás se reparten alrededor sin pisarlo.
    `tpf_de(objeto)` -> segundos por foto apilada. Devuelve un dict con tramos, esperas,
    no_programados (con el motivo) y fin_previsto (Time), listo para `programa_json`.
    """
    ahora = ahora or vis.ahora
    inicio, fin = vis.inicio, vis.fin
    t = ahora if (ahora - inicio).sec > 0 else inicio
    t_ini_noche = t
    candidatos = [o for o in objetos if o["estado"] in ("pendiente", "saltado") and o["intentos"] < max_intentos]
    restantes = list(candidatos)
    tramos, esperas = [], []

    def dur_de(o):
        return o["fotos"] * tpf_de(o) + FIJO_POR_OBJETO_S

    def disponible(o, t_):
        v = vis.por_id.get(o["id"])
        if not v or not v["sube"] or v["desde"] is None:
            return False
        return (v["desde"] - t_).sec <= 1 and (t_ - v["hasta"]).sec < 0 and fotos_posibles(o, v, t_, tpf_de(o)) >= o["fotos"]

    # ── Prioritarios: reservan su hueco centrado en su momento más alto, sin solaparse entre sí ──
    reservas = []
    prioritarios = [o for o in restantes if o.get("prioritario") and vis.por_id.get(o["id"])
                    and vis.por_id[o["id"]]["sube"] and vis.por_id[o["id"]].get("t_max") is not None]
    prioritarios.sort(key=lambda o: vis.por_id[o["id"]]["t_max"].jd)
    for o in prioritarios:
        v = vis.por_id[o["id"]]
        dur = dur_de(o)
        lo = v["desde"] if (v["desde"] - t_ini_noche).sec > 0 else t_ini_noche
        hi = v["hasta"] - dur * u.s            # último inicio con el que caben las N fotos
        if (hi - lo).sec < 0:
            continue                            # no caben: motivo normal más abajo
        ini = v["t_max"] - (dur / 2) * u.s
        if (ini - lo).sec < 0:
            ini = lo
        if (ini - hi).sec > 0:
            ini = hi
        for r in reservas:                      # no pisar otro prioritario: se desplaza tras él
            if (ini - r["fin"]).sec < 0 and (r["inicio"] - (ini + dur * u.s)).sec < 0:
                ini = r["fin"]
        if (ini - hi).sec > 0:
            continue
        reservas.append({"id": o["id"], "inicio": ini, "fin": ini + dur * u.s, "fotos": o["fotos"], "obj": o})
        restantes.remove(o)
    reservas.sort(key=lambda r: r["inicio"].jd)
    pendientes_reserva = list(reservas)

    while (fin - t).sec > 0 and (restantes or pendientes_reserva):
        # Una reserva cuyo momento ha llegado (o se pasó por un retraso) se ejecuta ya
        if pendientes_reserva and (pendientes_reserva[0]["inicio"] - t).sec <= 1:
            r = pendientes_reserva.pop(0)
            o = r["obj"]
            if fotos_posibles(o, vis.por_id[o["id"]], t, tpf_de(o)) >= o["fotos"]:
                t_fin = t + dur_de(o) * u.s
                tramos.append({"id": o["id"], "inicio": t, "fin": t_fin, "fotos": o["fotos"], "prioritario": True})
                t = t_fin
            else:
                restantes.append(o)             # se quedó sin hueco: motivo normal
            continue
        limite = pendientes_reserva[0]["inicio"] if pendientes_reserva else fin
        listos = [o for o in restantes if disponible(o, t) and ((t + dur_de(o) * u.s) - limite).sec <= 0]
        if listos:
            listos.sort(key=lambda o: (o["intentos"], vis.por_id[o["id"]]["hasta"].jd, -(vis.altura_en(o["id"], t) or 0)))
            o = listos[0]
            t_fin = t + dur_de(o) * u.s
            tramos.append({"id": o["id"], "inicio": t, "fin": t_fin, "fotos": o["fotos"], "prioritario": False})
            restantes.remove(o)
            t = t_fin
            continue
        proximos = []
        for o in restantes:
            v = vis.por_id.get(o["id"])
            if v and v["sube"] and v["desde"] is not None and (v["desde"] - t).sec > 1 \
                    and fotos_posibles(o, v, v["desde"], tpf_de(o)) >= o["fotos"]:
                proximos.append(v["desde"])
        if pendientes_reserva:
            proximos.append(pendientes_reserva[0]["inicio"])
        if not proximos:
            break
        t2 = min(proximos, key=lambda x: x.jd)
        esperas.append({"inicio": t, "fin": t2})
        t = t2

    no_programados = []
    for o in restantes:
        v = vis.por_id.get(o["id"])
        if not v or not v["sube"]:
            motivo = _("no sube de {alt}° esta noche", alt=v['alt_max']) if v else _("sin datos de altura")
            fp = 0
        else:
            t_ini = v["desde"] if (v["desde"] - t_ini_noche).sec > 0 else t_ini_noche
            fp = fotos_posibles(o, v, t_ini, tpf_de(o))
            sube_tarde = (v["desde"] - t_ini_noche).sec > 60
            if fp < o["fotos"]:
                amanece = (fin - v["hasta"]).sec < PASO_MIN * 60
                if amanece and sube_tarde:
                    motivo = _("sube a las {sube} y amanece a las {hora}: solo caben {fp} de {n}", sube=_hhmm(v['desde']),
                               hora=_hhmm(fin), fp=fp, n=o['fotos'])
                elif amanece:
                    motivo = _("amanece a las {hora}: solo caben {fp} de {n}", hora=_hhmm(fin), fp=fp, n=o['fotos'])
                elif sube_tarde:
                    motivo = _("sube a las {sube} y se pone a las {hora}: solo caben {fp} de {n}", sube=_hhmm(v['desde']),
                               hora=_hhmm(v['hasta']), fp=fp, n=o['fotos'])
                else:
                    motivo = _("se pone a las {hora}: solo caben {fp} de {n}", hora=_hhmm(v['hasta']), fp=fp, n=o['fotos'])
            else:
                motivo = _("no queda hueco esta noche")
        no_programados.append({"id": o["id"], "motivo": motivo, "fotos_posibles": fp})
    return {"tramos": tramos, "esperas": esperas, "no_programados": no_programados,
            "fin_previsto": tramos[-1]["fin"] if tramos else None,
            "espera_hasta": (tramos[0]["inicio"] if tramos and (tramos[0]["inicio"] - t_ini_noche).sec > 60 else None)}


def programa_json(programa):
    return {
        "tramos": [{"id": tr["id"], "inicio": _hhmm(tr["inicio"]), "fin": _hhmm(tr["fin"]),
                    "inicio_ms": _ms(tr["inicio"]), "fin_ms": _ms(tr["fin"]), "fotos": tr["fotos"],
                    "prioritario": bool(tr.get("prioritario"))} for tr in programa["tramos"]],
        "esperas": [{"inicio": _hhmm(e["inicio"]), "fin": _hhmm(e["fin"]),
                     "inicio_ms": _ms(e["inicio"]), "fin_ms": _ms(e["fin"])} for e in programa["esperas"]],
        "no_programados": programa["no_programados"],
        "fin_previsto": _hhmm(programa["fin_previsto"]),
        "espera_hasta": _hhmm(programa["espera_hasta"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Trabajo en segundo plano
# ─────────────────────────────────────────────────────────────────────────────

class PlanDetenido(Exception):
    pass


class TrabajoPlan:
    """
    Ejecuta el plan en un hilo. `deps` (ver ScopePower._deps_plan) aporta:
      lugar() -> (lat, lon, alt_m); altura_general() -> float; exposicion_config() -> float;
      goto_sincrono(nombre, ra_h, dec_deg, fuente, centrar, cancelar) -> {ok, motivo, centrado, detalle};
      arrancar_apilado(usar_dark); detener_apilado(); estado_apilado() -> {total, procesando};
      guardar_galeria() -> dict; esperar_guardado() -> dict; reiniciar_apilado();
      leer_dispositivos() -> dict; guardar_config(seccion, dict); calcular_altitud(ra, dec);
      es_de_dia(); otro_trabajo_en_curso() -> str|None; bucle; decir(texto)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self._pausa = threading.Event()
        self._saltar = threading.Event()
        self._hilo = None
        self.deps = None
        self._lock_vis = threading.Lock()
        self._cache_vis = None
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.fase = ""              # esperando | goto | centrando | apilando | guardando | pausado | terminado
        self.objeto_actual = None   # dict del objeto en curso
        self.fotos = 0
        self.mensaje = ""
        self.espera_hasta = None
        self.eventos = deque(maxlen=20)
        self.inicio = None
        self.hechos_noche = 0
        self.version_estado = 0
        self.resultado = ""

    # ── Control ───────────────────────────────────────────────────────
    def iniciar(self, deps):
        with self._lock:
            if self.en_curso:
                raise RuntimeError(_("El plan ya está en marcha"))
            otro = deps.otro_trabajo_en_curso()
            if otro:
                raise RuntimeError(_("Espera a que termine el trabajo de {otro}", otro=otro))
            pendientes = preparar_noche()
            if pendientes == 0:
                r = resumen()
                if r["total"] == 0:
                    raise RuntimeError(_("El plan está vacío: añade algún objeto"))
                raise RuntimeError(ngettext("Plan completado: {n} objeto hecho. Reinicia los resultados para repetirlo",
                                             "Plan completado: {n} objetos hechos. Reinicia los resultados para repetirlo", r['hechos']))
            self._reiniciar()
            self._cancelar.clear()
            self._pausa.clear()
            self._saltar.clear()
            self.deps = deps
            self.en_curso = True
            self.inicio = datetime.now()
            self.fase = "esperando"
            self.mensaje = _("Preparando la noche...")
        self._hilo = threading.Thread(target=self._ejecutar, name="plan-noche", daemon=True)
        self._hilo.start()
        return self._hilo

    def pausar(self):
        self._pausa.set()
        if self.fase == "apilando" and self.deps is not None:
            self.deps.bucle.pausar()
        self._evento(_("Plan pausado"))

    def reanudar(self):
        self._pausa.clear()
        if self.deps is not None and self.deps.bucle.activo:
            self.deps.bucle.reanudar()
        self._evento(_("Plan reanudado"))

    def detener(self):
        if not self.en_curso:
            return
        self._cancelar.set()
        self._pausa.clear()
        if self.deps is not None:
            try:
                self.deps.cancelar_centrado()
            except Exception:
                pass

    def saltar(self):
        if self.en_curso:
            self._saltar.set()

    @property
    def pausado(self):
        return self._pausa.is_set()

    def estado(self):
        with self._lock:
            o = self.objeto_actual
            return {
                "en_curso": self.en_curso,
                "pausado": self._pausa.is_set(),
                "fase": self.fase,
                "objeto": ({"id": o["id"], "nombre": o["nombre"], "fotos_objetivo": o["fotos"], "fotos": self.fotos}
                           if o else None),
                "mensaje": self.mensaje,
                "espera_hasta": self.espera_hasta,
                "hechos_noche": self.hechos_noche,
                "eventos": list(self.eventos),
                "inicio": self.inicio.strftime("%H:%M") if self.inicio else "",
                "resultado": self.resultado,
                "version_lista": version(),
                "version_estado": self.version_estado,
            }

    # ── Auxiliares ────────────────────────────────────────────────────
    def _evento(self, texto, decir=None):
        with self._lock:
            self.eventos.append({"hora": datetime.now().strftime("%H:%M"), "texto": texto})
            self.mensaje = texto
            self.version_estado += 1
        log.info("Plan: %s", texto)
        if decir and self.deps is not None:
            try:
                self.deps.decir(decir)
            except Exception:
                pass

    def _set(self, **campos):
        with self._lock:
            for k, v in campos.items():
                setattr(self, k, v)
            self.version_estado += 1

    def _dormir(self, segundos):
        """Espera respetando la pausa y la parada."""
        fin = time.time() + segundos
        while True:
            if self._cancelar.is_set():
                raise PlanDetenido()
            if self._pausa.is_set():
                self._esperar_reanudar()
            resto = fin - time.time()
            if resto <= 0:
                return
            self._cancelar.wait(min(1.0, resto))

    def _esperar_reanudar(self):
        fase = self.fase
        self._set(fase="pausado")
        while self._pausa.is_set():
            if self._cancelar.is_set():
                raise PlanDetenido()
            self._cancelar.wait(0.5)
        self._set(fase=fase)

    def _altura_efectiva(self, plan):
        return max(float(plan["ajustes"]["altura_minima"]), float(self.deps.altura_general()))

    def calcular(self, plan=None, ahora=None, deps=None):
        """
        Visibilidad y programa de la noche para el plan actual (también lo usa GET /plan).
        Las curvas de altura (astropy, ~1 s con 110 objetos) se guardan 60 s; la simulación
        es barata y se rehace en cada llamada.
        """
        deps = deps or self.deps
        plan = plan or leer()
        lat, lon, alt_m = deps.lugar()
        altura = max(float(plan["ajustes"]["altura_minima"]), float(deps.altura_general()))
        clave = (version(), altura, plan["ajustes"]["crepusculo"], plan["ajustes"]["probar_de_dia"], round(lat, 3), round(lon, 3))
        with self._lock_vis:
            c = self._cache_vis
            if ahora is None and c and c["clave"] == clave and time.time() - c["cuando"] < 60:
                vis = c["vis"]
                vis.ahora = Time.now()
            else:
                vis = visibilidad(plan["objetos"], lat, lon, alt_m, altura, ahora, plan["ajustes"]["crepusculo"],
                                  plan["ajustes"]["probar_de_dia"])
                if ahora is None:
                    self._cache_vis = {"clave": clave, "cuando": time.time(), "vis": vis}
        exp_cfg = float(deps.exposicion_config())
        medido = plan.get("medido") or {}
        tpf = lambda o: tiempo_por_foto(o["exposicion"] or exp_cfg, medido)
        programa = simular(plan["objetos"], vis, tpf, plan["ajustes"]["max_intentos"], ahora or vis.ahora)
        return vis, programa

    # ── Hilo ──────────────────────────────────────────────────────────
    def _ejecutar(self):
        deps = self.deps
        plan = leer()
        originales = plan["restaurar"] or deps.leer_dispositivos()
        guardar_restaurar(originales)
        resultado = "terminado"
        try:
            self._bucle_principal(originales)
        except PlanDetenido:
            resultado = "detenido"
            self._evento(_("Plan detenido por el usuario"), decir=_("Plan detenido"))
        except Exception as e:
            resultado = "error"
            log.exception("Error en el plan de la noche")
            self._evento(_("Plan detenido por un error: {e}", e=e), decir=_("Plan detenido por un error"))
        finally:
            try:
                if deps.bucle.activo:
                    deps.detener_apilado()
            except Exception as e:
                log.warning("Plan: no se pudo detener el apilado al terminar: %s", e)
            try:
                deps.guardar_config("Dispositivos", originales)
            except Exception as e:
                log.warning("Plan: no se pudieron restaurar los valores de config.ini: %s", e)
            guardar_restaurar(None)
            # Un objeto que se quedó en curso vuelve a pendiente
            for o in leer()["objetos"]:
                if o["estado"] == "en_curso":
                    marcar(o["id"], "pendiente", _("interrumpido"))
            with self._lock:
                self.en_curso = False
                self.fase = "terminado"
                self.objeto_actual = None
                self.resultado = resultado
                self.version_estado += 1

    def _bucle_principal(self, originales):
        deps = self.deps
        while True:
            if self._pausa.is_set():
                self._esperar_reanudar()
            plan = leer()
            vis, programa = self.calcular(plan)
            if (vis.fin - vis.ahora).sec <= 0:
                self._terminar_noche(plan, programa, _("Amanece"))
                return
            if not vis.de_noche:
                self._set(fase="esperando", espera_hasta=_hhmm(vis.inicio), objeto_actual=None)
                self._evento(_("Esperando al anochecer ({hora})", hora=_hhmm(vis.inicio)))
                self._dormir(30)
                continue
            if not programa["tramos"]:
                self._terminar_noche(plan, programa, _("Nada más que fotografiar esta noche"))
                return
            primero = programa["tramos"][0]
            if (primero["inicio"] - vis.ahora).sec > 60:
                nombre = next((o["nombre"] for o in plan["objetos"] if o["id"] == primero["id"]), "?")
                self._set(fase="esperando", espera_hasta=_hhmm(primero["inicio"]), objeto_actual=None)
                self._evento(_("Esperando a que suba {objeto} ({hora})", objeto=nombre, hora=_hhmm(primero['inicio'])))
                self._dormir(30)
                continue
            objeto = next(o for o in plan["objetos"] if o["id"] == primero["id"])
            self._fotografiar(plan, vis, objeto, originales)

    def _terminar_noche(self, plan, programa, motivo):
        for np_ in programa["no_programados"]:
            marcar(np_["id"], "saltado", np_["motivo"])
        # Los que agotaron los intentos de esta noche (sin fotos, centrado fallido...) quedan con su motivo
        max_intentos = plan["ajustes"]["max_intentos"]
        for o in leer()["objetos"]:
            if o["estado"] == "pendiente" and o["intentos"] >= max_intentos:
                marcar(o["id"], "saltado", ngettext("{n} intento esta noche: {motivo}", "{n} intentos esta noche: {motivo}", o['intentos'],
                                                    motivo=o['motivo'] or _("sin éxito")))
        r = resumen()
        pendientes = r["total"] - r["hechos"]
        if pendientes:
            texto = ngettext("{motivo}. Esta noche: {n} objeto hecho; quedan {p} para otra noche",
                             "{motivo}. Esta noche: {n} objetos hechos; quedan {p} para otra noche",
                             self.hechos_noche, motivo=motivo, p=pendientes)
            decir = ngettext("Plan de esta noche terminado. {n} objeto hecho, quedan {p} para otra noche",
                             "Plan de esta noche terminado. {n} objetos hechos, quedan {p} para otra noche",
                             self.hechos_noche, p=pendientes)
        else:
            texto = ngettext("{motivo}. Esta noche: {n} objeto hecho", "{motivo}. Esta noche: {n} objetos hechos",
                             self.hechos_noche, motivo=motivo)
            decir = ngettext("Plan de esta noche terminado. {n} objeto hecho. Plan completo",
                             "Plan de esta noche terminado. {n} objetos hechos. Plan completo", self.hechos_noche)
        self._evento(texto, decir=decir)
        self._set(fase="terminado", espera_hasta=None, objeto_actual=None)

    def _fotografiar(self, plan, vis, objeto, originales):
        deps = self.deps
        ajustes = plan["ajustes"]
        altura_min = self._altura_efectiva(plan)
        marcar(objeto["id"], "en_curso", "")
        self._set(objeto_actual=objeto, fotos=0, espera_hasta=None)
        self._evento(ngettext("Siguiente objeto: {objeto} ({n} foto)", "Siguiente objeto: {objeto} ({n} fotos)", objeto['fotos'], objeto=objeto['nombre']),
                     decir=_("Siguiente objeto: {objeto}", objeto=objeto['nombre']))

        lat, lon, alt_m = deps.lugar()
        ra, dec = float(objeto["ra_h"]), float(objeto["dec_deg"])
        if objeto.get("dinamico"):
            ra, dec = coordenadas_dinamicas(objeto, lat, lon, alt_m, Time.now())
            actualizar_coords(objeto["id"], ra, dec)

        # Ajustes de este objeto en config.ini (el bucle y el apilador los releen en cada foto)
        exposicion = objeto["exposicion"] or float(originales.get("exposicion_captura") or deps.exposicion_config())
        deps.guardar_config("Dispositivos", {"numero_maximo_imagenes": objeto["fotos"], "zoom_preview": objeto["zoom"],
                                             "exposicion_captura": exposicion})

        # GoTo y centrado
        self._set(fase="goto")
        # La Luna y los planetas no se centran por plate solving (sin estrellas alrededor no resuelve)
        centrar = ajustes["centrar"] and objeto.get("dinamico") not in NOMBRES_CUERPOS
        r = deps.goto_sincrono(objeto["nombre"], ra, dec, "Plan de la noche", centrar=centrar,
                               cancelar=self._cancelar, al_centrar=lambda: self._set(fase="centrando"))
        if self._cancelar.is_set():
            raise PlanDetenido()
        if not r.get("ok"):
            marcar(objeto["id"], "pendiente", r.get("motivo") or _("GoTo fallido"), sumar_intento=True)
            self._evento(_("{objeto}: {motivo}; se deja para más tarde", objeto=objeto['nombre'], motivo=r.get('motivo') or _("GoTo fallido")))
            self._dormir(2)
            return
        if r.get("centrado") in ("error", "cancelado"):
            marcar(objeto["id"], "pendiente", _("centrado fallido: {detalle}", detalle=r.get('detalle', '')), sumar_intento=True)
            self._evento(_("{objeto}: centrado fallido ({detalle}); se deja para más tarde", objeto=objeto['nombre'], detalle=r.get('detalle', '')))
            self._dormir(2)
            return
        if r.get("centrado"):
            self._evento(f"{objeto['nombre']}: {r.get('detalle') or r['centrado']}")

        # Apilado
        self._set(fase="apilando")
        deps.arrancar_apilado(ajustes["usar_dark"])
        motivo = self._vigilar_apilado(plan, objeto, altura_min, ra, dec)

        # Parar y recoger
        if motivo == "reintentar":
            # El apilado lo paró alguien y el usuario ha reanudado: el objeto vuelve a la cola sin contar intento
            marcar(objeto["id"], "pendiente", "")
            deps.reiniciar_apilado()
            return
        deps.detener_apilado()
        self._esperar_procesado()
        total = int(deps.estado_apilado().get("total") or 0)
        self._set(fotos=total)
        completo = motivo == "completo"
        if total >= 1:
            self._set(fase="guardando")
            # La ficha del objeto manda el rótulo y el nombre del archivo: se reescribe por si
            # algo (un login, un GoTo manual con el plan pausado) la cambió mientras se apilaba
            try:
                deps.anotar_objeto(objeto["nombre"], ra, dec, objeto.get("tipo", ""))
            except Exception as ex:
                log.warning("Plan: no se pudo reescribir la ficha del objeto: %s", ex)
            g = deps.guardar_galeria()
            if g.get("ok", True) and g.get("en_curso"):
                g = deps.esperar_guardado()
            archivo = g.get("archivo")
            res = {"archivo": archivo, "fotos": total, "fecha": datetime.now().strftime(formato_fecha()),
                   "motivo": motivo, "parcial": not completo}
            if completo:
                marcar(objeto["id"], "hecho", "", res)
                with self._lock:
                    self.hechos_noche += 1
                self._evento(ngettext("{objeto} hecho: {n} foto, guardado como {archivo}", "{objeto} hecho: {n} fotos, guardado como {archivo}",
                                      total, objeto=objeto['nombre'], archivo=archivo),
                             decir=_("{objeto} terminado y guardado en la galería", objeto=objeto['nombre']))
            else:
                # Detener no cuenta como intento fallido; saltar a mano sí (así pasa al final de la cola)
                marcar(objeto["id"], "pendiente", motivo, res, sumar_intento=(motivo != "detenido"))
                self._evento(ngettext("{objeto}: {motivo}; guardado parcial ({n} foto) y queda pendiente",
                                      "{objeto}: {motivo}; guardado parcial ({n} fotos) y queda pendiente", total,
                                      objeto=objeto['nombre'], motivo=motivo))
        else:
            marcar(objeto["id"], "pendiente", motivo, sumar_intento=(motivo != "detenido"))
            self._evento(_("{objeto}: {motivo}; sin fotos, se deja para más tarde", objeto=objeto['nombre'], motivo=motivo))
        deps.reiniciar_apilado()
        if motivo == "detenido":
            raise PlanDetenido()

    def _vigilar_apilado(self, plan, objeto, altura_min, ra, dec):
        """Sigue el apilado hasta que toque parar; devuelve el motivo."""
        deps = self.deps
        ajustes = plan["ajustes"]
        t_ultima = time.time()
        t_inicio = time.time()
        total_visto = 0
        fotos_objetivo = objeto["fotos"]
        ultimo_check_altura = 0
        exposicion = objeto["exposicion"] or float(deps.exposicion_config())
        while True:
            if self._cancelar.is_set():
                return "detenido"
            if self._saltar.is_set():
                self._saltar.clear()
                return _("saltado a mano")
            e = deps.estado_apilado()
            total = int(e.get("total") or 0)
            if total > total_visto:
                total_visto = total
                t_ultima = time.time()
                self._set(fotos=total)
                if total >= 3:
                    spf = (time.time() - t_inicio) / total
                    guardar_medido({"segundos_por_foto": round(spf, 1), "exposicion": exposicion})
            # Edición en caliente del número de fotos
            actual = next((o for o in leer()["objetos"] if o["id"] == objeto["id"]), None)
            if actual is None:
                return _("saltado a mano")
            if actual["fotos"] != fotos_objetivo:
                fotos_objetivo = actual["fotos"]
                objeto["fotos"] = fotos_objetivo
                deps.guardar_config("Dispositivos", {"numero_maximo_imagenes": fotos_objetivo})
                self._set(objeto_actual=dict(objeto))
            # Edición en caliente del zoom: la vista previa y el JPG de la galería lo leen de config.ini
            if actual["zoom"] != objeto["zoom"]:
                objeto["zoom"] = actual["zoom"]
                deps.guardar_config("Dispositivos", {"zoom_preview": actual["zoom"]})
                self._evento(_("{objeto}: zoom cambiado a {p} % del campo", objeto=objeto['nombre'], p=100 - actual['zoom']))
                try:
                    deps.reprocesar_preview()
                except Exception as ex:
                    log.warning("Plan: no se pudo regenerar la vista previa: %s", ex)
            if total >= fotos_objetivo:
                return "completo"
            bucle = deps.bucle
            if self._pausa.is_set() or bucle.pausado:
                if self._pausa.is_set() and not bucle.pausado:
                    bucle.pausar()
                self._set(fase="pausado")
                t_ultima += 2.0
                self._cancelar.wait(2.0)
                if not self._pausa.is_set() and bucle.pausado:
                    bucle.reanudar()
                if not self._pausa.is_set() and not bucle.pausado:
                    self._set(fase="apilando")
                continue
            if not bucle.activo:
                # Lo paró alguien (errores de cámara, botón Detener del apilado): el plan espera
                self._pausa.set()
                self._evento(_("Apilado detenido ({motivo}); plan en pausa. Reanuda para reintentar {objeto}",
                               motivo=bucle.ultimo_mensaje or _("sin motivo"), objeto=objeto['nombre']),
                             decir=_("Apilado detenido. Plan en pausa"))
                self._esperar_reanudar()
                if self._cancelar.is_set():
                    return "detenido"
                return "reintentar"
            ahora = time.time()
            if ahora - ultimo_check_altura > 60:
                ultimo_check_altura = ahora
                try:
                    if deps.calcular_altitud(ra, dec) < altura_min:
                        return _("se puso")
                except Exception as ex:
                    log.warning("Plan: no se pudo calcular la altura: %s", ex)
                if deps.es_de_dia() and not ajustes.get("probar_de_dia"):
                    return _("amanece")
            if ahora - t_ultima > ajustes["minutos_sin_fotos"] * 60:
                return _("sin fotos aceptadas en {n} min", n=ajustes['minutos_sin_fotos'])
            self._cancelar.wait(2.0)

    def _esperar_procesado(self, timeout=120):
        fin = time.time() + timeout
        while time.time() < fin:
            if not self.deps.estado_apilado().get("procesando"):
                return True
            time.sleep(1)
        return False


trabajo_plan = TrabajoPlan()
