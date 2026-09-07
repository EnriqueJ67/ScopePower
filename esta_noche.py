# esta_noche.py
#
# "Esta noche": qué objetos populares se ven bien hoy desde el observatorio.
#
# Sin Internet ni Stellarium: el catálogo va en catalogo_objetos.csv (Messier
# completo más los NGC/IC/Caldwell que se fotografían de verdad, con nombre
# en español, popularidad 1-5 y una frase) y los planetas y la Luna se
# calculan con astropy. Para la noche de hoy (del anochecer náutico al
# amanecer) se muestrea la altura de cada objeto cada 10 minutos y se puntúa:
#
#     puntuación = popularidad × calidad de visibilidad
#     calidad    = altura máxima esta noche (normalizada a 60°) ponderada por
#                  las horas que pasa por encima de la altura mínima
#
# Así arriba salen los objetos famosos que además están bien colocados hoy;
# uno famoso pero que no sube de 20° cae abajo, y uno modesto pero cenital
# sube pero no adelanta a los grandes clásicos.

import os
import csv
import time
import logging
import threading
from datetime import datetime, timedelta

import numpy as np
from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, get_sun, get_body
import astropy.units as u

log = logging.getLogger("scopepower.esta_noche")

RUTA_CATALOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogo_objetos.csv")
PASO_MIN = 10                       # muestreo de la altura, en minutos
CREPUSCULO_SOL = -12.0              # anochecer/amanecer náutico: cielo ya oscuro para DSO
CACHE_S = 300                       # se recalcula como mucho cada 5 min

PLANETAS = [  # (clave astropy, nombre, tipo, popularidad, descripción)
    ("moon", "Luna", "Luna", 4, "Cráteres y mares; con exposiciones muy cortas."),
    ("jupiter", "Júpiter", "Planeta", 4, "Bandas y las cuatro lunas galileanas."),
    ("saturn", "Saturno", "Planeta", 4, "Los anillos; pequeño, pide mucha focal."),
    ("mars", "Marte", "Planeta", 3, "Disco pequeño y rojizo; mejor cerca de la oposición."),
    ("venus", "Venus", "Planeta", 2, "Muy brillante, con fases; solo al anochecer o al amanecer."),
]

_cache = {"hasta": 0.0, "clave": None, "datos": None, "lock": threading.Lock()}


def cargar_catalogo(ruta=RUTA_CATALOGO):
    objetos = []
    with open(ruta, encoding="utf-8") as f:
        lineas = [l for l in f if not l.startswith("#")]
    for r in csv.DictReader(lineas, delimiter=";"):
        try:
            objetos.append({
                "id": r["id"], "nombre": r["nombre"], "nombre_alt": r.get("nombre_alt", ""),
                "tipo": r["tipo"], "ra_h": float(r["ra_h"]), "dec_deg": float(r["dec_deg"]),
                "mag": float(r["mag"]) if r.get("mag") else None,
                "tamano_arcmin": float(r["tamano_arcmin"]) if r.get("tamano_arcmin") else None,
                "constelacion": r.get("constelacion", ""), "popularidad": int(r.get("popularidad", 2)),
                "descripcion": r.get("descripcion", ""),
            })
        except (KeyError, ValueError) as e:
            log.warning("Fila del catálogo ignorada (%s): %s", e, r)
    return objetos


def ventana_nocturna(lugar, ahora=None):
    """
    (inicio, fin) de la noche "de hoy" como Time: del anochecer náutico al
    amanecer náutico. Si ya es de noche, el inicio es ahora. Si es de día, la
    noche que viene. Si el Sol no baja de -12° (verano en latitudes altas) se
    usa el tramo más oscuro de 4 h alrededor de la medianoche solar.
    """
    ahora = ahora or Time.now()
    # Muestreo del Sol cada 10 min durante las próximas 30 h
    ts = ahora + np.arange(0, 30 * 60, PASO_MIN) * u.min
    alt_sol = get_sun(ts).transform_to(AltAz(obstime=ts, location=lugar)).alt.deg
    oscuro = alt_sol < CREPUSCULO_SOL
    if not oscuro.any():
        i_min = int(np.argmin(alt_sol))
        return ts[max(0, i_min - 12)], ts[min(len(ts) - 1, i_min + 12)]
    if oscuro[0]:
        inicio_i = 0
    else:
        inicio_i = int(np.argmax(oscuro))            # primer instante oscuro
    # fin: primer instante claro después del inicio
    despues = ~oscuro[inicio_i:]
    fin_i = inicio_i + (int(np.argmax(despues)) if despues.any() else len(despues) - 1)
    return ts[inicio_i], ts[fin_i]


def _hora_local(t):
    return t.to_datetime().replace(tzinfo=None) + (datetime.now() - datetime.utcnow())


def calcular(lat, lon, alt_m=0.0, altura_minima=20.0, ahora=None, catalogo=None):
    """Lista de objetos con su visibilidad esta noche, ordenada por puntuación."""
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    ahora = ahora or Time.now()
    inicio, fin = ventana_nocturna(lugar, ahora)
    n = max(2, int(((fin - inicio).to(u.min).value) // PASO_MIN) + 1)
    ts = inicio + np.arange(n) * PASO_MIN * u.min
    marco = AltAz(obstime=ts[None, :], location=lugar)
    marco_ahora = AltAz(obstime=ahora, location=lugar)

    objetos = list(catalogo if catalogo is not None else cargar_catalogo())
    fijos = SkyCoord(ra=[o["ra_h"] for o in objetos] * u.hourangle, dec=[o["dec_deg"] for o in objetos] * u.deg)
    alturas = fijos[:, None].transform_to(marco).alt.deg          # (objetos, tiempos)
    alt_ahora = fijos.transform_to(marco_ahora).alt.deg

    # Planetas y Luna: posición a mitad de la noche vale para toda ella
    medio = inicio + (fin - inicio) / 2
    for clave, nombre, tipo, pop, desc in PLANETAS:
        try:
            c = get_body(clave, medio, lugar)
            c_ahora = get_body(clave, ahora, lugar)
        except Exception as e:
            log.warning("Sin efemérides de %s: %s", nombre, e)
            continue
        objetos.append({"id": nombre, "nombre": nombre, "nombre_alt": "", "tipo": tipo, "ra_h": float(c.ra.hour),
                        "dec_deg": float(c.dec.deg), "mag": None, "tamano_arcmin": None, "constelacion": "",
                        "popularidad": pop, "descripcion": desc})
        # Coordenada escalar: se convierte en un vector de un elemento para poder usar el marco (1, tiempos)
        cc = SkyCoord(ra=[c.ra.deg] * u.deg, dec=[c.dec.deg] * u.deg)
        alturas = np.vstack([alturas, cc[:, None].transform_to(marco).alt.deg])
        alt_ahora = np.append(alt_ahora, float(SkyCoord(ra=c_ahora.ra.deg * u.deg, dec=c_ahora.dec.deg * u.deg).transform_to(marco_ahora).alt.deg))

    horas_noche = (fin - inicio).to(u.hour).value
    resultado = []
    for i, o in enumerate(objetos):
        curva = alturas[i]
        i_max = int(np.argmax(curva))
        alt_max = float(curva[i_max])
        visible = curva >= altura_minima
        horas_ok = float(visible.sum() * PASO_MIN / 60.0)
        if alt_max < altura_minima:
            calidad = 0.0
        else:
            calidad = min(alt_max, 60.0) / 60.0 * min(1.0, 0.5 + horas_ok / max(horas_noche, 1.0))
        # primer tramo visible: desde / hasta
        desde = hasta = None
        if visible.any():
            j0 = int(np.argmax(visible))
            j1 = j0 + int(np.argmin(visible[j0:])) if (~visible[j0:]).any() else n - 1
            desde, hasta = _hora_local(ts[j0]), _hora_local(ts[j1])
        resultado.append({
            **{k: v for k, v in o.items()},
            "alt_ahora": round(float(alt_ahora[i]), 1),
            "alt_max": round(alt_max, 1),
            "hora_max": _hora_local(ts[i_max]).strftime("%H:%M"),
            "visible_ahora": bool(alt_ahora[i] >= altura_minima),
            "visible_desde": desde.strftime("%H:%M") if desde else None,
            "visible_hasta": hasta.strftime("%H:%M") if hasta else None,
            "horas_visible": round(horas_ok, 1),
            "puntuacion": round(o["popularidad"] * calidad, 2),
        })
    resultado.sort(key=lambda r: (-r["puntuacion"], -r["alt_max"]))
    return {
        "noche_desde": _hora_local(inicio).strftime("%H:%M"), "noche_hasta": _hora_local(fin).strftime("%H:%M"),
        "altura_minima": altura_minima, "calculado": datetime.now().strftime("%H:%M"),
        "objetos": resultado,
    }


def obtener(lat, lon, alt_m=0.0, altura_minima=20.0):
    """calcular() con caché de 5 minutos (la transformación de todo el catálogo tarda ~1 s)."""
    clave = (round(lat, 3), round(lon, 3), altura_minima)
    with _cache["lock"]:
        if _cache["datos"] is not None and _cache["clave"] == clave and time.time() < _cache["hasta"]:
            return _cache["datos"]
    datos = calcular(lat, lon, alt_m, altura_minima)
    with _cache["lock"]:
        _cache.update(datos=datos, clave=clave, hasta=time.time() + CACHE_S)
    return datos
