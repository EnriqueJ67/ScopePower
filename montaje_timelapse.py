# montaje_timelapse.py
#
# Vídeo de un timelapse de «Sol, Luna y paisaje» (foto_dia.TrabajoTimelapse.montar_video).
#
# 28-nov-2026, primer timelapse real del Sol (523 fotos en 3 h): el MP4 salía con el
# códec MPEG-4 Part 2 («mp4v» de OpenCV), que Chrome no reproduce, y el disco paseaba
# por el encuadre (el guiado iba mal con la montura en ecuatorial y se corrigió a mano);
# además un árbol y un cable taparon el Sol en ~90 fotos. Ahora:
#
#   - Códec: WebM VP8 (lo reproduce cualquier Chrome y ocupa poco: ~6 MB las 523 fotos a
#     1080 px, frente a ~70 MB en H.264 con Media Foundation). Si OpenCV no puede, H.264
#     en MP4 y, en último caso, el MP4 de antes (avisando de que el navegador no lo abre).
#   - Sol y Luna: cada fotograma se desplaza para dejar el disco en el centro (el disco se
#     localiza con disco.detectar_disco, el radio conocido de la serie).
#   - Sol: se quitan los fotogramas en que algo tapa el disco (árboles, cables, nubes
#     espesas): la fracción del disco que brilla como el disco limpio de la serie. Con las
#     fotos del 28-nov las limpias dan ≥ 0,987 y las tapadas bajan de 0,98. La Luna no se
#     mira (su fase ya deja parte del disco a oscuras).
#
# El resultado deja timelapse.json al lado con lo que se hizo (fotogramas usados y quitados).

import os
import json
import math
import logging

import numpy as np

log = logging.getLogger("scopepower.timelapse")

NOMBRES_VIDEO = ("timelapse.webm", "timelapse.mp4")
RESUMEN = "timelapse.json"
MINIATURA_VIDEO = "miniatura_video.jpg"      # primer fotograma del vídeo, pequeño (tarjeta de la galería)
LOTE_REVELADO = 8               # fotos que se revelan a la vez (en paralelo) antes de escribirlas
COBERTURA_MINIMA = 0.98         # fracción del disco del Sol que ha de verse limpia
LADO_DETECCION = 640            # el disco se busca en una copia reducida (rápido en el mini PC)
RADIO_COBERTURA = 0.85          # el oscurecimiento del limbo no cuenta como «tapado»


def objetivo_de_carpeta(carpeta):
    """«sol», «luna» o lo que sea el final del nombre de la carpeta (AAAAMMDD_HHMMSS_<objetivo>)."""
    return os.path.basename(os.path.normpath(carpeta)).rsplit("_", 1)[-1].lower()


def video_de(carpeta):
    """Nombre del vídeo montado de la carpeta (el WebM antes que un MP4 viejo), o None."""
    for nombre in NOMBRES_VIDEO:
        if os.path.isfile(os.path.join(carpeta, nombre)):
            return nombre
    return None


def reproducible(carpeta, nombre=None):
    """False si el vídeo es un MP4 con el códec que el navegador no abre (montado antes del 28-nov-2026)."""
    nombre = nombre or video_de(carpeta)
    if not nombre or not nombre.endswith(".mp4"):
        return bool(nombre)
    try:
        with open(os.path.join(carpeta, RESUMEN), encoding="utf-8") as f:
            return json.load(f).get("codec") in ("h264", "vp8")
    except (OSError, ValueError):
        return False


def _luminancia(img):
    import cv2
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def radio_de_la_serie(imagenes):
    """
    Radio del disco (píxeles) en las fotos de la serie: el de la zona brillante, con el
    percentil 75 para que no lo encojan las fotos tapadas.
    """
    import cv2
    radios = []
    for img in imagenes:
        s = cv2.GaussianBlur(_luminancia(img), (0, 0), 1.2)
        bajo, alto = np.percentile(s, (5, 99.5))
        if alto - bajo > 0.02 * max(alto, 1.0):
            radios.append(math.sqrt(float((s > bajo + 0.35 * (alto - bajo)).sum()) / math.pi))
    return float(np.percentile(radios, 75)) if radios else None


def localizar(img, radio):
    """(cx, cy, ajuste) del disco en píxeles de `img`, o None si no se ve."""
    import cv2
    import disco
    lum = _luminancia(img)
    esc = min(1.0, LADO_DETECCION / float(max(lum.shape)))
    pequena = cv2.resize(lum, None, fx=esc, fy=esc, interpolation=cv2.INTER_AREA) if esc < 1 else lum
    try:
        d = disco.detectar_disco(pequena, radio * esc)
    except disco.SinDisco:
        return None
    return d["cx"] / esc, d["cy"] / esc, float(d["ajuste"])


def brillo_del_disco(img, cx, cy, radio):
    """
    (percentil 90 dentro del disco, fondo, histograma de 256 niveles del disco): con el
    histograma se cuenta luego qué parte brilla como el disco limpio, sin guardar los píxeles.
    """
    lum = _luminancia(img)
    yy, xx = np.ogrid[:lum.shape[0], :lum.shape[1]]
    dentro = lum[(xx - cx) ** 2 + (yy - cy) ** 2 < (RADIO_COBERTURA * radio) ** 2]
    hist = np.bincount(np.clip(np.rint(dentro), 0, 255).astype(np.int64), minlength=256)
    return float(np.percentile(dentro, 90)), float(np.percentile(lum, 5)), hist


def cobertura(hist, umbral):
    """Fracción del disco por encima de `umbral` (nivel 0–255) según su histograma."""
    total = float(hist.sum())
    return float(hist[int(math.floor(umbral)) + 1:].sum()) / total if total else 0.0


def centrar(img, cx, cy):
    """La foto desplazada para dejar (cx, cy) en el centro; lo que entra por los bordes, del color del fondo."""
    import cv2
    h, w = img.shape[:2]
    M = np.float32([[1, 0, (w - 1) / 2.0 - cx], [0, 1, (h - 1) / 2.0 - cy]])
    fondo = [float(np.percentile(img[::8, ::8, c], 5)) for c in range(img.shape[2])]
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=fondo)


# ─── Revelado como el del editor de día (los mismos ajustes para todas las fotos) ──────
# Las fotos de la serie son JPG revelados con la gamma 1/1,6 de foto_dia: se vuelven a
# lineal y pasan por revelado_dia.revelar con los ajustes del editor de día de la galería
# (exposición, contraste, detalle, nitidez, color y firma). Se eligen mirando el primer
# fotograma (página del timelapse en la galería) y se aplican a cada foto al montar; las
# fotos no se tocan, así que se puede rehacer el vídeo con otros ajustes tantas veces como
# se quiera. Sin ajustes (None) el vídeo lleva las fotos tal cual, que es mucho más rápido:
# revelar cuesta ~1,2 s por foto de 1080 px en el PC de casa (se reparte entre núcleos).
GAMMA_FOTOS = 1.6


def ajustes_validos(datos):
    """Los ajustes del editor de día (revelado_dia.normalizar), o None si no se quiere revelar."""
    import revelado_dia
    if not isinstance(datos, dict):
        return None
    return revelado_dia.normalizar(datos)


def lineal_de_foto(img):
    """RGB float32 lineal (0..65535) de una foto BGR de 8 bits de la serie."""
    import cv2
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.power(rgb, GAMMA_FOTOS) * 65535.0


def medir_limbo(img):
    """FWHM del limbo (px) en una foto de la serie, para la nitidez; None si no hay disco."""
    import apilado_video
    limbo, __ = apilado_video.medir_limbo(lineal_de_foto(img))
    return limbo


def firma_de_carpeta(carpeta, objetivo=None):
    """Datos de la firma: los del observatorio, el objeto y la fecha y hora del comienzo del timelapse."""
    import firma
    from datetime import datetime
    meta = {k: v for k, v in firma.datos_observatorio().items() if k != "activa"}
    nombre = os.path.basename(os.path.normpath(carpeta))
    objetivo = objetivo or objetivo_de_carpeta(carpeta)
    meta["objeto"] = {"sol": "Sol", "luna": "Luna"}.get(objetivo, objetivo.capitalize())
    try:
        meta["fecha"] = datetime.strptime(nombre[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        pass
    return meta


def revelar_foto(img, ajustes, limbo=None, meta_firma=None):
    """Foto BGR de 8 bits revelada con los ajustes del editor de día (y firmada si lo piden)."""
    import cv2
    import revelado_dia
    a = ajustes_validos(ajustes)
    if a is None:
        return img
    rgb8 = revelado_dia.revelar(lineal_de_foto(img), a, limbo_px=limbo)
    salida = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
    if a.get("firma") and meta_firma is not None:
        import firma
        salida = firma.firmar(salida, meta_firma)
    return salida


def leer_resumen(carpeta):
    try:
        with open(os.path.join(carpeta, RESUMEN), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def foto_de_muestra(carpeta, fotos):
    """La primera foto de la serie que no se quitó en el último montaje (la de los ajustes)."""
    quitadas = {q[0] for q in leer_resumen(carpeta).get("quitados", [])}
    return next((f for f in fotos if f not in quitadas), fotos[0] if fotos else None)


def _abrir_escritor(carpeta, fps, ancho, alto):
    """(VideoWriter, nombre, códec) con el primer códec que el navegador reproduce y OpenCV puede escribir."""
    import cv2
    intentos = (("timelapse.webm", cv2.CAP_FFMPEG, "VP80", "vp8"),
                ("timelapse.mp4", cv2.CAP_MSMF, "avc1", "h264"),
                ("timelapse.mp4", cv2.CAP_FFMPEG, "avc1", "h264"),
                ("timelapse.mp4", cv2.CAP_FFMPEG, "mp4v", "mp4v"))
    for nombre, api, fourcc, codec in intentos:
        ruta = os.path.join(carpeta, nombre)
        try:
            w = cv2.VideoWriter(ruta, api, cv2.VideoWriter_fourcc(*fourcc), float(fps), (int(ancho), int(alto)))
        except cv2.error:
            continue
        if w.isOpened():
            return w, nombre, codec
        w.release()
        try:
            os.remove(ruta)
        except OSError:
            pass
    return None, None, None


def montar(carpeta, fotos, fps=25, ancho=1080, objetivo=None, alinear=True, quitar_tapadas=True,
           ajustes=None, progreso=lambda pct: None):
    """
    Junta `fotos` (nombres en `carpeta`, en orden) en un vídeo. Devuelve el resumen (dict):
    archivo, codec, fotogramas, quitados [(foto, motivo)], centrado, ancho, alto, fps.
    """
    import cv2
    objetivo = (objetivo or objetivo_de_carpeta(carpeta)).lower()
    primera = cv2.imread(os.path.join(carpeta, fotos[0]))
    if primera is None:
        raise RuntimeError("No se puede leer {}".format(fotos[0]))
    # Sin ampliar: las fotos de la serie ya vienen a su tamaño (1282 px el 28-nov)
    ancho = int(min(int(ancho), primera.shape[1]))
    ancho -= ancho % 2
    alto = int(primera.shape[0] * ancho / primera.shape[1])
    alto -= alto % 2
    disco_si = alinear and objetivo in ("sol", "luna")
    radio = None
    if disco_si:
        muestra = [cv2.imread(os.path.join(carpeta, f)) for f in fotos[::max(1, len(fotos) // 40)]]
        radio = radio_de_la_serie([m for m in muestra if m is not None])
        disco_si = bool(radio and radio > 10)

    # 1.ª pasada (Sol/Luna): dónde está el disco en cada foto y cuánto se ve
    medidas = {}
    if disco_si:
        for i, f in enumerate(fotos):
            img = cv2.imread(os.path.join(carpeta, f))
            if img is not None:
                pos = localizar(img, radio)
                if pos is not None and objetivo == "sol" and quitar_tapadas:
                    p90, fondo, dentro = brillo_del_disco(img, pos[0], pos[1], radio)
                    medidas[f] = (pos, p90, fondo, dentro)
                else:
                    medidas[f] = (pos, None, None, None)
            progreso(int(60 * (i + 1) / len(fotos)))
    referencia = fondo_ref = None
    if disco_si and objetivo == "sol" and quitar_tapadas:
        brillos = [m[1] for m in medidas.values() if m[1] is not None]
        if brillos:
            referencia = float(np.median(brillos))
            fondo_ref = float(np.median([m[2] for m in medidas.values() if m[2] is not None]))

    escritor, nombre, codec = _abrir_escritor(carpeta, fps, ancho, alto)
    if escritor is None:
        raise RuntimeError("No se pudo crear el fichero de vídeo")
    # Un vídeo anterior con el otro nombre se quita, para que la lista enseñe el nuevo
    for otro in NOMBRES_VIDEO:
        if otro != nombre and os.path.isfile(os.path.join(carpeta, otro)):
            try:
                os.remove(os.path.join(carpeta, otro))
            except OSError:
                pass
    quitados, escritos = [], 0
    base = 60 if disco_si else 0
    a_revelar = ajustes_validos(ajustes)
    pendientes, limbo, meta_firma = [], None, None
    piscina = None
    if a_revelar is not None:
        from concurrent.futures import ThreadPoolExecutor
        piscina = ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) - 1)))
        meta_firma = firma_de_carpeta(carpeta, objetivo) if a_revelar.get("firma") else None

    def _escribir_lote(lote, n):
        nonlocal limbo
        if limbo is None and disco_si:
            limbo = medir_limbo(lote[0]) or 0.0        # una vez, en la primera foto que entra (0: sin disco)
        for revelada in piscina.map(lambda im: revelar_foto(im, a_revelar, limbo or None, meta_firma), lote):
            escritor.write(revelada)
            if n == 0:
                _miniatura(revelada)
            n += 1
        return n

    def _miniatura(img):
        lado = 320.0 / max(img.shape[:2])
        cv2.imwrite(os.path.join(carpeta, MINIATURA_VIDEO), cv2.resize(img, None, fx=lado, fy=lado, interpolation=cv2.INTER_AREA),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    try:
        for i, f in enumerate(fotos):
            img = cv2.imread(os.path.join(carpeta, f))
            if img is None:
                quitados.append((f, "ilegible"))
                continue
            if disco_si:
                pos, p90, fondo, dentro = medidas.get(f, (None, None, None, None))
                if pos is None:
                    quitados.append((f, "sin disco"))
                    continue
                if referencia is not None and dentro is not None:
                    umbral = fondo_ref + 0.6 * (referencia - fondo_ref)
                    visible = cobertura(dentro, umbral)
                    if visible < COBERTURA_MINIMA:
                        quitados.append((f, "tapado {:.0f} %".format(100 * (1 - visible))))
                        continue
                img = centrar(img, pos[0], pos[1])
            if img.shape[1] != ancho or img.shape[0] != alto:
                img = cv2.resize(img, (ancho, alto), interpolation=cv2.INTER_AREA)
            if a_revelar is not None:
                pendientes.append(img)
                if len(pendientes) >= LOTE_REVELADO:
                    escritos = _escribir_lote(pendientes, escritos)
                    pendientes = []
                progreso(base + int((100 - base) * (i + 1) / len(fotos)))
                continue
            escritor.write(img)
            if escritos == 0:
                _miniatura(img)
            escritos += 1
            progreso(base + int((100 - base) * (i + 1) / len(fotos)))
        if pendientes:
            escritos = _escribir_lote(pendientes, escritos)
    finally:
        escritor.release()
        if piscina is not None:
            piscina.shutdown(wait=True)
    if escritos < 2:
        raise RuntimeError("Quedan menos de dos fotos útiles para el vídeo ({} quitadas)".format(len(quitados)))
    resumen = {"archivo": nombre, "codec": codec, "fotogramas": escritos, "fotos": len(fotos),
               "quitados": quitados, "centrado": disco_si, "radio_px": round(radio, 1) if radio else None,
               "ancho": ancho, "alto": alto, "fps": fps, "objetivo": objetivo,
               "alinear": bool(alinear), "quitar_tapadas": bool(quitar_tapadas), "ajustes": a_revelar,
               "limbo_px": round(limbo, 2) if limbo else None}
    with open(os.path.join(carpeta, RESUMEN), "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=1)
    log.info("Vídeo del timelapse: %s (%s, %d de %d fotos, %d quitadas%s)", os.path.join(carpeta, nombre), codec,
             escritos, len(fotos), len(quitados), ", disco centrado" if disco_si else "")
    return resumen
