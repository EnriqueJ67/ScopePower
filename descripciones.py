# -*- coding: utf-8 -*-
"""
Descripción hablada de un objeto para la pantalla GoTo.

Orden de búsqueda:
  1. Catálogo propio: catalogo_objetos.csv (datos) + descripciones_objetos.csv
     (texto largo escrito para leerse en voz alta). Sin Internet siempre.
  2. Caché de Wikipedia en info_objetos/ (lo que ya se bajó otra noche).
  3. Wikipedia en línea (recorridos.obten_resumen_wiki), recortada y limpiada.

Delante del texto va una frase con los datos del catálogo ("M 4, cúmulo globular
en Escorpio, magnitud 5.9, unos 26 minutos de arco") para situar al oyente.
"""
import csv
import os
import re
import logging

from unidecode import unidecode

import esta_noche
import recorridos

log = logging.getLogger("scopepower.descripciones")

RUTA_DESCRIPCIONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "descripciones_objetos.csv")

# Nombres de constelación del catálogo que no están escritos para ser leídos
_CONSTELACIONES = {"Se1": "la Serpiente", "Se2": "la Serpiente", "Canes Venatici": "los Perros de Caza",
                   "Cabellera de Berenice": "la Cabellera de Berenice", "Osa Mayor": "la Osa Mayor",
                   "Zorra": "la Zorra", "Escudo": "el Escudo", "Unicornio": "el Unicornio", "Cisne": "el Cisne",
                   "Dragón": "el Dragón", "Triángulo": "el Triángulo", "Escultor": "el Escultor"}


# ─────────────────────────────────────────────────────────────────────────────
def _clave(texto):
    """'M 31' / 'm31' / 'Galaxia de Andrómeda' -> 'm31' / 'galaxiadeandromeda'."""
    return re.sub(r"[^a-z0-9]", "", unidecode(str(texto or "")).lower())


def _cargar_descripciones():
    textos = {}
    if not os.path.isfile(RUTA_DESCRIPCIONES):
        return textos
    with open(RUTA_DESCRIPCIONES, encoding="utf-8") as f:
        lineas = [l for l in f if not l.startswith("#")]
    for fila in csv.DictReader(lineas, delimiter=";"):
        if fila.get("id") and fila.get("descripcion_larga"):
            textos[_clave(fila["id"])] = fila["descripcion_larga"].strip()
    return textos


def buscar_en_catalogo(nombre):
    """Fila del catálogo cuyo id, nombre o nombre alternativo coincide con `nombre`, o None."""
    clave = _clave(nombre)
    if not clave:
        return None
    # "Andromeda" también debe encajar con "Galaxia de Andrómeda": segundo intento por inclusión
    candidatos = esta_noche.cargar_catalogo()
    for o in candidatos:
        if clave in (_clave(o["id"]), _clave(o["nombre"]), _clave(o["nombre_alt"])):
            return o
    # Nombre entre paréntesis: "Cabeza de Caballo (Barnard 33)"
    for o in candidatos:
        if clave in [_clave(x) for x in re.split(r"[()]", o["nombre"]) if x.strip()]:
            return o
    if len(clave) >= 5:
        parciales = [o for o in candidatos if clave in _clave(o["nombre"]) or (o["nombre_alt"] and clave in _clave(o["nombre_alt"]))]
        if len(parciales) == 1:
            return parciales[0]
        if parciales:
            return max(parciales, key=lambda o: o["popularidad"])
    return None


def frase_datos(o):
    """'Galaxia de Andrómeda, galaxia en Andrómeda, magnitud 3.4, unos 178 minutos de arco.'"""
    tipo = o["tipo"].lower()
    const = _CONSTELACIONES.get(o["constelacion"], o["constelacion"])
    partes = [f"{o['nombre']}, {tipo}" + (f" en {const}" if const else "")]
    if o["mag"] is not None:
        partes.append(f"magnitud {o['mag']:g}".replace(".", ","))
    if o["tamano_arcmin"]:
        t = o["tamano_arcmin"]
        if t >= 60:
            partes.append(f"unos {t / 60:.1f} grados de tamaño".replace(".", ",").replace(",0 ", " "))
        else:
            partes.append(f"unos {t:g} minutos de arco")
    return ", ".join(partes) + "."


def limpiar_wikipedia(texto, max_frases=4):
    """Quita alias entre paréntesis, caracteres invisibles y referencias; deja las primeras frases."""
    t = texto or ""
    t = re.sub(r"[​‌‍﻿]", "", t)          # espacios de ancho cero
    t = re.sub(r"\[\d+\]", "", t)                            # [12]
    t = re.sub(r"\s*\([^()]*\)", "", t)                      # (también conocido como ...)
    t = re.sub(r"\s+", " ", t).strip()
    frases = re.split(r"(?<=[.!?])\s+", t)
    return " ".join(frases[:max_frases]).strip()


def describir(nombre, reemplazar_numeros=None):
    """
    Devuelve {"texto", "texto_hablar", "fuente", "objeto"}. `texto_hablar` lleva la
    frase de datos delante cuando el objeto está en el catálogo.
    fuente: catalogo | cache | wikipedia | ninguna
    """
    nombre = (nombre or "").strip()
    o = buscar_en_catalogo(nombre)
    intro = frase_datos(o) if o else ""
    if o:
        largo = _cargar_descripciones().get(_clave(o["id"]))
        if largo:
            return {"texto": largo, "texto_hablar": f"{intro} {largo}", "fuente": "catalogo", "objeto": o["nombre"]}

    # Wikipedia: primero la caché (misma función; si el fichero existe no sale a Internet)
    consulta = o["nombre"] if o else nombre
    # Para la Wikipedia el nombre corto (M 31) desambigua mejor que "Galaxia de Andrómeda"
    consultas = [nombre] if not o else [o["id"], o["nombre"], nombre]
    texto, fuente = "", "ninguna"
    for q in dict.fromkeys(consultas):
        en_cache = os.path.isfile(recorridos.ruta_cache_wiki(q, reemplazar_numeros))
        resumen = recorridos.obten_resumen_wiki(q, reemplazar_numeros)
        if resumen and not resumen.startswith("No se encontró información"):
            texto, fuente = limpiar_wikipedia(resumen), ("cache" if en_cache else "wikipedia")
            break
    if not texto:
        if intro:
            return {"texto": intro, "texto_hablar": intro, "fuente": "catalogo", "objeto": o["nombre"]}
        return {"texto": f"No se encontró información sobre «{nombre}».", "texto_hablar": "", "fuente": "ninguna", "objeto": nombre}
    hablar = f"{intro} {texto}" if intro else f"{consulta}: {texto}"
    return {"texto": texto, "texto_hablar": hablar, "fuente": fuente, "objeto": o["nombre"] if o else nombre}
