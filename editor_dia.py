# editor_dia.py
#
# Editor de día de la galería: las fotos del Sol, la Luna y el paisaje que tienen su
# imagen en crudo guardada (static/fotos/crudos/<base>.fits: el apilado de un vídeo, en
# color, o la foto suelta con su mosaico Bayer) se vuelven a revelar con otros ajustes
# (revelado_dia.py) y, si vienen de un vídeo que sigue en el mini PC, se vuelven a apilar
# con más o menos fotogramas (apilado_video.py).
#
# El editor de noche (editor_fotos.py) no sirve para estas fotos: su procesado es el de
# los apilados de noche (fondo de cielo, estirado, estrellas) y busca el FITS junto al JPG.
# Aquí se comparten con él las rutas, el original apartado en originales/ y la firma.
#
# Pocos controles a propósito: exposición, contraste, detalle, nitidez y color, más el
# porcentaje de fotogramas para volver a apilar. La vista previa se calcula aquí a unos
# 900 px (alrededor de un segundo en el mini PC) y al guardar se revela a resolución
# completa, se firma y sustituye el JPG de la galería; el original se aparta la primera vez.

import os
import json
import time
import logging
import threading
import configparser

import numpy as np
from idioma import _, fmt_num

import editor_fotos
import firma
import giro_camara
import revelado_dia
import apilado_video

log = logging.getLogger("scopepower.editor_dia")

CARPETA_CRUDOS = os.path.join(editor_fotos.CARPETA_FOTOS, "crudos")
ANCHO_PREVIEW = 800
CALIDAD_PREVIEW = 85
CALIDAD_GUARDADO = 92
MODOS_DIA = ("video", "dia")

EDICION = {"en_curso": False, "mensaje": "", "ok": None, "archivo": ""}
_lock_preview = threading.Lock()
_lock_cache = threading.Lock()
_cache = {"clave": None, "lineal": None, "limbo": None}


class ErrorEditorDia(Exception):
    """Algo que el usuario tiene que saber (la foto no tiene crudo, el vídeo ya no está...)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Qué fotos son de día y de dónde vienen
# ─────────────────────────────────────────────────────────────────────────────
def ruta_crudo(nombre):
    base, __, __, __ = editor_fotos.rutas(nombre)
    return os.path.join(CARPETA_CRUDOS, os.path.basename(base) + ".fits")


def cabecera(nombre):
    """Cabecera del FITS en crudo, o None si la foto no lo tiene."""
    ruta = ruta_crudo(nombre)
    if not os.path.isfile(ruta):
        return None
    try:
        from astropy.io import fits
        return fits.getheader(ruta)
    except Exception as e:
        log.warning("No se pudo leer la cabecera de %s: %s", ruta, e)
        return None


def es_foto_dia(nombre):
    """True si la foto de la galería tiene su crudo de día (apilado de vídeo o foto suelta)."""
    try:
        hdr = cabecera(nombre)
    except editor_fotos.ErrorEditor:
        return False
    return hdr is not None and str(hdr.get("MODO", "")).strip().lower() in MODOS_DIA


def carpeta_videos(ruta_config="config.ini"):
    cfg = configparser.ConfigParser()
    cfg.read(ruta_config)
    import grabador_video
    return cfg.get("Video", "carpeta", fallback=grabador_video.CARPETA_VIDEOS).strip() or grabador_video.CARPETA_VIDEOS


def ruta_video(hdr):
    """Ruta del SER del que salió el apilado (cabecera VIDEO), exista o no; None si la foto no viene de un vídeo."""
    nombre = str(hdr.get("VIDEO") or "").strip()
    if not nombre:
        return None
    return os.path.join(carpeta_videos(), os.path.basename(nombre) + ".ser")


def info(nombre):
    """Lo que necesita la pantalla del editor de día."""
    base, ruta_jpg, __, __ = editor_fotos.rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditorDia(_("Imagen no encontrada"))
    hdr = cabecera(nombre)
    if hdr is None or str(hdr.get("MODO", "")).strip().lower() not in MODOS_DIA:
        raise ErrorEditorDia(_("Esta foto no tiene su imagen en crudo guardada; no se puede volver a revelar."))
    meta = firma.meta_de_archivo(ruta_jpg)
    modo = str(hdr.get("MODO", "")).strip().lower()
    objetivo = str(hdr.get("OBJETIVO") or "").strip().lower()
    d = {"nombre": nombre, "objeto": meta.get("objeto") or editor_fotos.partes_nombre(nombre)[0].replace("_", " "),
         "fecha": meta.get("fecha") or "", "modo": modo, "objetivo": objetivo,
         "ancho": int(hdr.get("NAXIS1") or 0), "alto": int(hdr.get("NAXIS2") or 0),
         "exposicion": float(hdr.get("EXPTIME") or 0.0),
         "exposicion_texto": __import__("foto_dia").formatear_exposicion(float(hdr.get("EXPTIME") or 0.0)),
         "hay_original": os.path.isfile(editor_fotos.ruta_original(base)),
         "ajustes": revelado_dia.normalizar(meta.get("ajustes_dia")),
         "firmada": bool(meta.get("firmada", True)), "video": None}
    if modo == "video":
        ruta_ser = ruta_video(hdr)
        apilado = {}
        if ruta_ser and apilado_video.hay_apilado(ruta_ser):
            try:
                with open(os.path.splitext(ruta_ser)[0] + ".apilado.json", encoding="utf-8") as f:
                    apilado = json.load(f)
            except (OSError, ValueError):
                apilado = {}
        d["video"] = {"nombre": os.path.splitext(os.path.basename(ruta_ser))[0] if ruta_ser else "",
                      "frames": int(hdr.get("NFRAMES") or apilado.get("frames") or 0),
                      "usados": int(hdr.get("NUSADOS") or apilado.get("usados") or 0),
                      "pct": apilado.get("pct"), "limbo_px": apilado.get("limbo_px"),
                      "giro_max_deg": apilado.get("giro_max_deg"),
                      "reapilable": bool(ruta_ser and os.path.isfile(ruta_ser)),
                      "apilado_guardado": bool(ruta_ser and apilado_video.hay_apilado(ruta_ser))}
    return d


# ─────────────────────────────────────────────────────────────────────────────
#  La imagen lineal de la foto
# ─────────────────────────────────────────────────────────────────────────────
def _orientar(lineal, nombre_transformacion):
    import vivo_dia
    return vivo_dia.a_orientacion_ascom(lineal, nombre_transformacion)


def cargar_lineal(nombre):
    """
    (RGB lineal float32 con negro y balance aplicados, en la orientación de las fotos de la
    galería y sin el giro de Cámara; FWHM del limbo en px o None). Se guarda la última en
    memoria: mover un deslizador no debe volver a leer el disco.
    """
    hdr = cabecera(nombre)
    if hdr is None:
        raise ErrorEditorDia(_("Esta foto no tiene su imagen en crudo guardada; no se puede volver a revelar."))
    modo = str(hdr.get("MODO", "")).strip().lower()
    ruta_f = ruta_crudo(nombre)
    ruta_ser = ruta_video(hdr) if modo == "video" else None
    usar_apilado = bool(ruta_ser and apilado_video.hay_apilado(ruta_ser))
    fuente = apilado_video.ruta_apilado(ruta_ser) if usar_apilado else ruta_f
    try:
        clave = (fuente, os.path.getmtime(fuente))
    except OSError:
        raise ErrorEditorDia(_("No se encuentra la imagen en crudo de esta foto"))
    with _lock_cache:
        if _cache["clave"] == clave and _cache["lineal"] is not None:
            return _cache["lineal"], _cache["limbo"]
    from astropy.io import fits
    if usar_apilado:
        media, inf = apilado_video.cargar_apilado(ruta_ser)
        lineal = revelado_dia.aplicar_balance(media, inf.get("negro"), inf.get("balance"))
        orientacion = apilado_video.leer_meta(ruta_ser).get("orientacion")
        if not orientacion:
            import orientacion_camara
            orientacion = orientacion_camara.leer_sdk()["transformacion"]
        lineal = np.ascontiguousarray(_orientar(lineal, orientacion))
        limbo = inf.get("limbo_px")
    else:
        with fits.open(ruta_f) as hdul:
            datos = np.asarray(hdul[0].data)
        if modo == "video":
            # El cubo (3, h, w) que se guardó con la foto: ya lineal, con el balance y el blanco al 65535
            if datos.ndim != 3 or datos.shape[0] != 3:
                raise ErrorEditorDia(_("El FITS de esta foto no es un apilado en color"))
            lineal = np.ascontiguousarray(np.transpose(datos.astype(np.float32), (1, 2, 0)))
        else:
            import foto_dia
            patron = str(hdr.get("BAYERPAT") or "").strip().upper()
            balance = None
            if hdr.get("WB_R") is not None and hdr.get("WB_B") is not None:
                balance = {"r": float(hdr["WB_R"]), "b": float(hdr["WB_B"])}
            zona = foto_dia.recortar_centro(datos, float(hdr.get("CAMPO") or 1.0))
            if patron in ("RGGB", "BGGR", "GRBG", "GBRG"):
                lineal = revelado_dia.lineal_de_cfa(zona, patron, float(hdr.get("NEGRO") or 0.0), balance)
            else:
                lineal = revelado_dia.aplicar_balance(np.repeat(np.asarray(zona, dtype=np.float32)[..., None], 3, axis=2),
                                                      float(hdr.get("NEGRO") or 0.0), None)
        objetivo = str(hdr.get("OBJETIVO") or "").strip().lower()
        limbo = None
        if objetivo in ("sol", "luna"):
            limbo, __ = apilado_video.medir_limbo(lineal)
    with _lock_cache:
        _cache.update({"clave": clave, "lineal": lineal, "limbo": limbo})
    return lineal, limbo


def olvidar_cache():
    with _lock_cache:
        _cache.update({"clave": None, "lineal": None, "limbo": None})


def _revelar(nombre, ajustes, ancho=None, progreso=None):
    """BGR uint8 revelado con el giro de Cámara, y el limbo medido."""
    import cv2
    lineal, limbo = cargar_lineal(nombre)
    escala = 1.0
    if ancho:
        lineal, escala = revelado_dia.reducir(lineal, ancho)
    rgb8 = revelado_dia.revelar(lineal, ajustes, limbo_px=limbo, escala=escala, progreso=progreso)
    bgr = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
    return giro_camara.aplicar(bgr, giro_camara.leer()), limbo


def vista_previa(nombre, ajustes):
    """JPEG (bytes) de la foto revelada con `ajustes`, con el lado mayor a ANCHO_PREVIEW. Solo una a la vez."""
    import cv2
    if not _lock_preview.acquire(blocking=False):
        raise ErrorEditorDia(_("Hay otra vista previa en marcha; espera a que termine"))
    try:
        bgr, __ = _revelar(nombre, ajustes, ancho=ANCHO_PREVIEW)
        ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_PREVIEW])
        if not ok:
            raise ErrorEditorDia(_("No se pudo codificar la vista previa"))
        return jpg.tobytes()
    finally:
        _lock_preview.release()


# ─────────────────────────────────────────────────────────────────────────────
#  Guardar, restaurar, volver a apilar
# ─────────────────────────────────────────────────────────────────────────────
def _meta_para_guardar(ruta_jpg, ajustes):
    meta = firma.meta_de_archivo(ruta_jpg)
    for k, v in firma.datos_observatorio().items():
        if k == "activa":
            continue
        if v not in ("", 0, 0.0) or k not in meta:
            meta[k] = v
    a = revelado_dia.normalizar(ajustes)
    meta["ajustes_dia"] = {k: v for k, v in a.items() if k != "firma"}
    meta["firmada"] = bool(a["firma"])
    meta["giro"] = giro_camara.leer()
    return meta, a


def guardar(nombre, ajustes):
    """Revela a resolución completa, firma y sustituye el JPG de la galería (el original se aparta). Estado en EDICION."""
    import cv2
    if EDICION["en_curso"]:
        raise ErrorEditorDia(_("Ya hay un guardado en curso"))
    base, ruta_jpg, __, ruta_json = editor_fotos.rutas(nombre)
    if not os.path.isfile(ruta_jpg):
        raise ErrorEditorDia(_("Imagen no encontrada"))
    EDICION.update({"en_curso": True, "ok": None, "archivo": nombre, "mensaje": _("Revelando a resolución completa...")})

    def _trabajo():
        inicio = time.time()
        try:
            bgr, __ = _revelar(nombre, ajustes, progreso=lambda f: EDICION.update(
                mensaje=_("Revelando a resolución completa ({p} %)...", p=int(f * 100))))
            meta, a = _meta_para_guardar(ruta_jpg, ajustes)
            if a["firma"]:
                bgr = firma.firmar(bgr, meta)
            editor_fotos._apartar_original(base, ruta_jpg, ruta_json)
            cv2.imwrite(ruta_jpg, bgr, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_GUARDADO])
            firma.guardar_meta(ruta_jpg, meta)
            EDICION.update({"ok": True, "mensaje": _("Foto guardada en {s} s", s=fmt_num(time.time() - inicio, 0))})
            log.info("Foto %s revelada otra vez desde el editor de día (%s)", nombre, revelado_dia.texto_ajustes(a))
        except Exception as e:
            log.exception("Error guardando la foto del editor de día")
            EDICION.update({"ok": False, "mensaje": _("Error al guardar: {e}", e=e)})
        finally:
            EDICION["en_curso"] = False

    threading.Thread(target=_trabajo, name="editor-dia-guardar", daemon=True).start()
    return dict(EDICION)


def restaurar(nombre):
    """Vuelve a poner el JPG y el JSON originales (los apartados la primera vez que se guardó o se volvió a apilar)."""
    try:
        editor_fotos.restaurar(nombre)
    except editor_fotos.ErrorEditor as e:
        raise ErrorEditorDia(str(e))


def reapilar(nombre, pct, guardar_cb, ajustes=None, contexto=None):
    """
    Vuelve a apilar el vídeo del que salió la foto con el `pct` % de fotogramas más nítidos y la
    revela con `ajustes`; guardar_cb(rgb8, cubo16, info) la deja en la galería (sustituye la foto;
    el original se aparta). El progreso se sigue en apilado_video.trabajo_apilado_video.
    """
    hdr = cabecera(nombre)
    if hdr is None or str(hdr.get("MODO", "")).strip().lower() != "video":
        raise ErrorEditorDia(_("Esta foto no viene de un vídeo; no hay nada que volver a apilar."))
    ruta_ser = ruta_video(hdr)
    if not ruta_ser or not os.path.isfile(ruta_ser):
        raise ErrorEditorDia(_("El vídeo de esta foto ya no está en el mini PC; no se puede volver a apilar."))
    base, ruta_jpg, __, ruta_json = editor_fotos.rutas(nombre)
    try:
        pct = max(1.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        pct = apilado_video.MEJORES_PCT_DEFECTO
    a = revelado_dia.normalizar(ajustes)
    try:
        apilado_video.trabajo_apilado_video.iniciar(ruta_ser, guardar_cb, pct=pct, ajustes=a, contexto=contexto)
    except RuntimeError as e:
        raise ErrorEditorDia(str(e))
    editor_fotos._apartar_original(base, ruta_jpg, ruta_json)
    olvidar_cache()
    log.info("Volviendo a apilar %s desde el editor de día: mejores %.0f %%, %s", os.path.basename(ruta_ser), pct,
             revelado_dia.texto_ajustes(a))
    return apilado_video.trabajo_apilado_video.estado()
