# fondo_planetario.py
#
# Fotos de la galería sobre el cielo del planetario: cada apilado o mosaico guardado
# se pinta como fondo, deformado a su huella real en el cielo y con transparencia,
# para ver qué está ya fotografiado y encajar el siguiente mosaico encima.
#
# De cada foto hace falta dónde cae cada esquina de su JPG en RA/Dec:
#   - El FITS hermano lleva el WCS de la referencia del apilado (desde el 14-oct-2026;
#     los mosaicos, el del propio mosaico). Las fotos anteriores solo entran si la
#     galería anotada ya las resolvió con ASTAP (datos/anotaciones_galeria.json).
#   - Del píxel del FITS al del JPG guardado se pasa con el mismo encuadre que usan las
#     anotaciones de la galería (galeria_anotada._matriz_al_jpg: zona, zoom, giro,
#     espejo); aquí se invierte para llevar las esquinas del JPG al FITS y de ahí al cielo.
# El resultado se guarda en datos/fondo_planetario.json (clave: tamaño y fecha del FITS y
# del JPG). La imagen que se pinta es, en cuanto está, una versión a campo completo revelada
# aquí del FITS (zona útil entera, sin zoom, sin giro y sin la firma; completa()), reducida a
# 1024 px y guardada en datos/planetario_fotos/; mientras se revela en segundo plano se enseña
# el JPG de la galería reducido.

import os
import json
import math
import logging
import threading

import numpy as np
from astropy.io import fits

import firma
import procesado
import galeria_anotada
from editor_fotos import rutas, partes_nombre, CARPETA_FOTOS

log = logging.getLogger("scopepower.fondo_planetario")

RUTA_CACHE = os.path.join("datos", "fondo_planetario.json")
CARPETA_MINIATURAS = os.path.join("datos", "planetario_fotos")
LADO_MINIATURA = 1024
SUFIJOS_FUERA = ("_comparacion", "_mas", "_stf", "_nucleo")
_lock = threading.Lock()


def _leer_cache():
    try:
        with open(RUTA_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _guardar_cache(cache):
    try:
        os.makedirs(os.path.dirname(RUTA_CACHE), exist_ok=True)
        with open(RUTA_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as e:
        log.warning("No se pudo guardar la caché de fotos del planetario: %s", e)


def _solucion_de(ruta_fits):
    """Solución del campo (como la de galeria_anotada) del WCS de la cabecera del FITS o de la caché de la galería anotada."""
    hdr = fits.getheader(ruta_fits)
    claves = ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CD1_1", "CD1_2", "CD2_1", "CD2_2")
    if all(k in hdr for k in claves):
        cd = [[float(hdr["CD1_1"]), float(hdr["CD1_2"])], [float(hdr["CD2_1"]), float(hdr["CD2_2"])]]
        det = cd[0][0] * cd[1][1] - cd[0][1] * cd[1][0]
        zona = procesado.leer_zona(hdr)
        return {"ancho": int(hdr["NAXIS1"]), "alto": int(hdr["NAXIS2"]), "zona": list(zona) if zona else None,
                "crpix": [float(hdr["CRPIX1"]), float(hdr["CRPIX2"])], "crval": [float(hdr["CRVAL1"]), float(hdr["CRVAL2"])],
                "cd": cd, "escala_arcsec": math.sqrt(abs(det)) * 3600.0,
                "angulo_cam": math.degrees(math.atan2(cd[0][1], cd[0][0]))}
    st = os.stat(ruta_fits)
    return galeria_anotada._leer_cache().get(f"{st.st_size}_{st.st_mtime_ns}")


def _huella(nombre):
    """{nombre, ancho, alto, esquinas [[ra, dec]×4], area_deg2, objeto, fecha} del JPG, o None si no se sabe dónde cae."""
    __, ruta_jpg, ruta_fits, __ = rutas(nombre)
    if not os.path.isfile(ruta_fits):
        return None
    solucion = _solucion_de(ruta_fits)
    if not solucion:
        return None
    wcs = galeria_anotada._wcs(solucion)
    meta = firma.meta_de_archivo(ruta_jpg) or {}
    M, ancho, alto = galeria_anotada._matriz_al_jpg(meta, solucion, ruta_jpg)
    inv = np.linalg.inv(np.vstack([M, [0.0, 0.0, 1.0]]))
    esquinas_jpg = np.array([[0, 0, 1], [ancho, 0, 1], [ancho, alto, 1], [0, alto, 1]], dtype=np.float64)
    px = (inv @ esquinas_jpg.T).T[:, :2]
    cielo = wcs.all_pix2world(px, 0)
    if not np.all(np.isfinite(cielo)):
        return None
    escala = float(solucion["escala_arcsec"]) / 3600.0
    factor = math.sqrt(abs(np.linalg.det(M[:, :2])))      # px del JPG por px del FITS
    area = (ancho / factor) * (alto / factor) * escala * escala
    # Campo completo: la zona útil del FITS (lo que enseña la versión revelada aquí)
    zx, zy, zw, zh = solucion.get("zona") or (0, 0, solucion["ancho"], solucion["alto"])
    esq_zona = np.array([[zx, zy], [zx + zw, zy], [zx + zw, zy + zh], [zx, zy + zh]], dtype=np.float64) - 0.5
    cielo_zona = wcs.all_pix2world(esq_zona, 0)
    return {"nombre": nombre, "ancho": int(ancho), "alto": int(alto),
            "esquinas": [[round(float(a), 5), round(float(b), 5)] for a, b in cielo],
            "esquinas_completa": [[round(float(a), 5), round(float(b), 5)] for a, b in cielo_zona],
            "area_completa_deg2": round(zw * zh * escala * escala, 3),
            "area_deg2": round(area, 3), "objeto": meta.get("objeto") or partes_nombre(nombre)[0].replace("_", " "),
            "fecha": meta.get("fecha") or ""}


def lista():
    """Fotos de la galería que se pueden poner sobre el cielo, las grandes primero (los mosaicos debajo de las fotos sueltas)."""
    with _lock:
        cache = _leer_cache()
        salida, usadas, cambiado, sin_solucion = [], set(), False, []
        for f in sorted(os.listdir(CARPETA_FOTOS)):
            if not f.lower().endswith(".jpg") or os.path.splitext(f)[0].endswith(SUFIJOS_FUERA):
                continue
            ruta_jpg = os.path.join(CARPETA_FOTOS, f)
            ruta_fits = os.path.splitext(ruta_jpg)[0] + ".fits"
            if not os.path.isfile(ruta_fits):
                continue
            try:
                sj, sf = os.stat(ruta_jpg), os.stat(ruta_fits)
                clave = f"{f}|{sj.st_size}_{sj.st_mtime_ns}|{sf.st_size}_{sf.st_mtime_ns}"
                usadas.add(clave)
                if clave in cache and cache[clave] and "esquinas_completa" in cache[clave]:
                    huella = cache[clave]
                else:
                    huella = _huella(f)
                    if huella:
                        cache[clave] = huella
                        cambiado = True
                if huella:
                    salida.append(huella)
                elif clave not in _sin_solucion:
                    sin_solucion.append(f)
            except Exception as e:
                log.warning("Fondo del planetario: %s no se puede situar (%s)", f, e)
        if cambiado or any(k not in usadas for k in cache):
            _guardar_cache({k: v for k, v in cache.items() if k in usadas})
    # Las que tienen FITS pero nadie ha resuelto nunca: se resuelven con ASTAP en segundo plano
    # (galeria_anotada guarda la solución y la siguiente lista ya las coloca)
    if sin_solucion:
        preparar_soluciones(sin_solucion)
    resolviendo = len(sin_solucion)
    # Qué versión se va a servir de cada una: la de campo completo si ya está revelada; si no, el
    # JPG de la galería (desfirmado) mientras se revela en segundo plano
    pendientes = []
    for h in salida:
        try:
            base, __, ruta_fits, __ = rutas(h["nombre"])
            completa_lista = os.path.isfile(_ruta_completa(base, os.stat(ruta_fits), LADO_MINIATURA))
        except Exception:
            completa_lista = False
        h["modo"] = "completa" if completa_lista else "jpg"
        if not completa_lista:
            pendientes.append(h["nombre"])
    if pendientes:
        preparar_completas(pendientes)
    salida.sort(key=lambda h: -(h["area_completa_deg2"] if h["modo"] == "completa" else h["area_deg2"]))
    return salida, resolviendo


_hilo_soluciones = None
_sin_solucion = set()      # fotos que ASTAP no pudo resolver (clave jpg|fits): no se insiste hasta reiniciar


def preparar_soluciones(nombres):
    """Resuelve con ASTAP, en segundo plano y de una en una, las fotos que no tienen posición."""
    global _hilo_soluciones
    if _hilo_soluciones is not None and _hilo_soluciones.is_alive():
        return
    pendientes = list(nombres)

    def trabajo():
        import time
        for nombre in pendientes:
            try:
                __, ruta_jpg, ruta_fits, __ = rutas(nombre)
                sj, sf = os.stat(ruta_jpg), os.stat(ruta_fits)
                clave = f"{nombre}|{sj.st_size}_{sj.st_mtime_ns}|{sf.st_size}_{sf.st_mtime_ns}"
            except Exception:
                continue
            for intento in range(12):
                if galeria_anotada._lock.locked():        # otra resolución en curso (la galería anotada): esperar
                    time.sleep(5)
                    continue
                try:
                    galeria_anotada.solucion_de(ruta_fits)
                    log.info("Fondo del planetario: %s resuelta con ASTAP", nombre)
                except galeria_anotada.ErrorEditor as e:
                    log.info("Fondo del planetario: %s sin posición (%s)", nombre, e)
                    _sin_solucion.add(clave)
                except Exception as e:
                    log.warning("Fondo del planetario: error resolviendo %s: %s", nombre, e)
                    _sin_solucion.add(clave)
                break
    _hilo_soluciones = threading.Thread(target=trabajo, name="planetario-soluciones", daemon=True)
    _hilo_soluciones.start()


def _ruta_completa(base, st_fits, lado):
    return os.path.join(CARPETA_MINIATURAS, f"{os.path.basename(base)}_{st_fits.st_mtime_ns}_{lado}_completa.jpg")


def completa(nombre, lado=LADO_MINIATURA):
    """
    Versión a campo completo de la foto, revelada aquí del FITS (zona útil entera, sin zoom,
    sin giro y sin firma) y reducida a `lado` px, para el planetario: así una foto guardada con
    zoom al 50 % se ve con todo lo que cubre el sensor. Revelado estándar (procesado.leer_config)
    a escala reducida: unos segundos por foto; queda en datos/planetario_fotos.
    """
    import cv2
    base, ruta_jpg, ruta_fits, __ = rutas(nombre)
    st = os.stat(ruta_fits)
    destino = _ruta_completa(base, st, lado)
    if os.path.isfile(destino):
        return destino
    with fits.open(ruta_fits) as hdul:
        hdr = hdul[0].header
        datos = hdul[0].data
        if datos is None or datos.ndim != 3:
            raise ValueError("el FITS no es un apilado RGB")
        cubo = np.nan_to_num(datos.astype(np.float32))
        if cubo.shape[0] != 3:
            cubo = np.ascontiguousarray(cubo.transpose(2, 0, 1))
        n = int(hdr.get("NSTACK", 1) or 1)
    solucion = _solucion_de(ruta_fits) or {}
    zona = solucion.get("zona") or procesado.leer_zona(hdr) or procesado.zona_automatica(cubo)
    balance = procesado.balance_estrellas(cubo, zona)
    recorte, __ = procesado.girar_campo(cubo, 1.0, 0.0, zona)
    del cubo
    escala = min(1.0, lado / float(max(recorte.shape[1], recorte.shape[2])))
    rgb8 = procesado.procesar(recorte, n, procesado.leer_config(), escala=escala, balance=balance)
    os.makedirs(CARPETA_MINIATURAS, exist_ok=True)
    cv2.imwrite(destino, cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 84])
    return destino


_hilo_completas = None


def preparar_completas(nombres, lado=LADO_MINIATURA):
    """Revela en segundo plano, de una en una, las versiones a campo completo que falten."""
    global _hilo_completas
    if _hilo_completas is not None and _hilo_completas.is_alive():
        return
    pendientes = list(nombres)
    if not pendientes:
        return

    def trabajo():
        for nombre in pendientes:
            try:
                completa(nombre, lado)
                log.info("Fondo del planetario: %s revelada a campo completo", nombre)
            except Exception as e:
                log.warning("Fondo del planetario: no se pudo revelar %s a campo completo: %s", nombre, e)
    _hilo_completas = threading.Thread(target=trabajo, name="planetario-completas", daemon=True)
    _hilo_completas.start()


def miniatura(nombre, lado=LADO_MINIATURA):
    """
    Ruta de la versión reducida del JPG de la galería (con su firma): es lo que se enseña mientras
    no está revelada la versión a campo completo (completa). Se guarda en datos/planetario_fotos.
    """
    import cv2
    base, ruta_jpg, __, __ = rutas(nombre)
    st = os.stat(ruta_jpg)
    os.makedirs(CARPETA_MINIATURAS, exist_ok=True)
    destino = os.path.join(CARPETA_MINIATURAS, f"{os.path.basename(base)}_{st.st_mtime_ns}_{lado}.jpg")
    if not os.path.isfile(destino):
        bgr = cv2.imread(ruta_jpg, cv2.IMREAD_COLOR)
        if bgr is None:
            raise OSError(f"No se puede leer {ruta_jpg}")
        h, w = bgr.shape[:2]
        if max(h, w) > lado:
            f = lado / float(max(h, w))
            bgr = cv2.resize(bgr, (max(8, int(round(w * f))), max(8, int(round(h * f)))), interpolation=cv2.INTER_AREA)
        cv2.imwrite(destino, bgr, [cv2.IMWRITE_JPEG_QUALITY, 84])
        # Versiones viejas de la misma foto
        for viejo in os.listdir(CARPETA_MINIATURAS):
            if viejo.startswith(os.path.basename(base) + "_") and viejo != os.path.basename(destino):
                try:
                    os.remove(os.path.join(CARPETA_MINIATURAS, viejo))
                except OSError:
                    pass
    return os.path.abspath(destino)
