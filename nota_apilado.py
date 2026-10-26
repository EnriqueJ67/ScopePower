# nota_apilado.py
#
# Nota de calidad de un apilado guardado en la galería, para quedarse con las
# mejores fotos de cada objeto. Es la misma puntuación con que el apilado en
# vivo decide qué fotos entran (apilado.calcular_puntuacion: nitidez × √estrellas),
# pero medida sobre el FITS apilado y no sobre una foto suelta:
#
#   - más tiempo de integración baja el ruido y aparecen más estrellas débiles,
#     así que la nota sube con las fotos buenas que entraron;
#   - el foco, el seeing y el arrastre se ven en la nitidez (pico/flujo de cada
#     estrella), como en cada foto.
#
# Se mide en la zona que cubren todas las fotos (ZONA* de la cabecera), así los
# bordes con menos fotos y más ruido no cuentan. Solo se comparan bien fotos del
# mismo equipo y encuadre: otra cámara, otra focal o un campo más grande cambian
# el número de estrellas.
#
# La nota se guarda en el JSON hermano del JPG ("nota"): el apilado la escribe al
# guardar en la galería y las fotos antiguas se puntúan desde la galería, en
# segundo plano (TRABAJO), porque leer cada FITS tarda unos segundos en el mini PC.

import os
import json
import logging
import threading

import numpy as np
from astropy.io import fits

from idioma import _, ngettext

log = logging.getLogger("scopepower.nota_apilado")

TRABAJO = {"en_curso": False, "hechas": 0, "total": 0, "mensaje": "", "ok": None}


def calcular(ruta_fits):
    """
    Nota del FITS apilado: dict con "puntuacion", "estrellas", "nitidez" y "fwhm"
    (píxeles; None si no hay estrellas que medir). Se mide en el canal verde.
    """
    import apilado
    import procesado
    with fits.open(ruta_fits, memmap=True) as hdul:
        datos = hdul[0].data
        zona = procesado.leer_zona(hdul[0].header)
        plano = np.asarray(datos[1] if datos.ndim == 3 else datos, dtype=np.float32)
    plano = np.nan_to_num(plano, nan=0.0, posinf=0.0, neginf=0.0)
    if zona:
        x0, y0, w, h = zona
        recorte = plano[y0:y0 + h, x0:x0 + w]
        if recorte.shape[0] >= 64 and recorte.shape[1] >= 64:
            plano = recorte
    puntuacion, n_estrellas, nitidez = apilado.calcular_puntuacion(plano)
    try:
        fwhm = apilado.fwhm_estrellas(plano)
    except Exception:
        fwhm = float("nan")
    return {"puntuacion": round(float(puntuacion), 3), "estrellas": int(n_estrellas),
            "nitidez": round(float(nitidez), 4), "fwhm": round(fwhm, 2) if np.isfinite(fwhm) else None}


def _ruta_json(ruta_jpg):
    return os.path.splitext(ruta_jpg)[0] + ".json"


def leer(ruta_jpg):
    """La nota guardada en el JSON hermano del JPG, o None."""
    try:
        with open(_ruta_json(ruta_jpg), "r", encoding="utf-8") as f:
            nota = json.load(f).get("nota")
        return nota if isinstance(nota, dict) and "puntuacion" in nota else None
    except Exception:
        return None


def puntuar(ruta_jpg):
    """Calcula la nota del FITS hermano del JPG y la añade a su JSON (lo crea si no lo hay)."""
    import firma
    ruta_fits = os.path.splitext(ruta_jpg)[0] + ".fits"
    nota = calcular(ruta_fits)
    meta = firma.meta_de_archivo(ruta_jpg)
    meta["nota"] = nota
    firma.guardar_meta(ruta_jpg, meta)
    return nota


def puntuar_varias(rutas_jpg):
    """Puntúa en segundo plano las fotos dadas; el progreso, en TRABAJO."""
    if TRABAJO["en_curso"]:
        raise RuntimeError(_("Ya se están puntuando fotos"))
    rutas_jpg = list(rutas_jpg)
    TRABAJO.update({"en_curso": True, "hechas": 0, "total": len(rutas_jpg), "ok": None, "mensaje": ""})

    def _trabajo():
        fallos = 0
        try:
            for ruta in rutas_jpg:
                try:
                    puntuar(ruta)
                except Exception as e:
                    fallos += 1
                    log.info("Nota del apilado: %s sin puntuar: %s", os.path.basename(ruta), e)
                TRABAJO["hechas"] += 1
            TRABAJO.update({"ok": True, "mensaje": ngettext("{n} foto puntuada", "{n} fotos puntuadas", len(rutas_jpg) - fallos)})
        except Exception as e:
            log.exception("Error puntuando las fotos de la galería")
            TRABAJO.update({"ok": False, "mensaje": str(e)})
        finally:
            TRABAJO["en_curso"] = False

    threading.Thread(target=_trabajo, name="nota-apilado", daemon=True).start()
    return dict(TRABAJO)
