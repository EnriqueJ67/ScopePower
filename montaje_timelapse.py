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


# ─── Ajustes de revelado (los mismos para todas las fotos del vídeo) ──────────
# Se eligen mirando el primer fotograma (página del timelapse en la galería) y se aplican a
# cada foto al montar; las fotos de la serie no se tocan, así que se puede rehacer el
# vídeo con otros ajustes tantas veces como se quiera.
AJUSTES_NEUTROS = {"exposicion": 0.0, "contraste": 1.0, "medios": 1.0, "saturacion": 1.0, "temperatura": 0.0, "nitidez": 0.0}
LIMITES_AJUSTES = {"exposicion": (-2.0, 2.0), "contraste": (0.5, 2.0), "medios": (0.5, 2.0), "saturacion": (0.0, 2.5),
                   "temperatura": (-1.0, 1.0), "nitidez": (0.0, 3.0)}


def ajustes_validos(datos):
    """Los ajustes de `datos` dentro de sus límites (los que falten, neutros)."""
    salida = dict(AJUSTES_NEUTROS)
    for clave, (lo, hi) in LIMITES_AJUSTES.items():
        try:
            v = float((datos or {}).get(clave, salida[clave]))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            salida[clave] = round(min(hi, max(lo, v)), 3)
    return salida


def son_neutros(ajustes):
    return all(abs(float(ajustes.get(k, v)) - v) < 1e-6 for k, v in AJUSTES_NEUTROS.items())


def _tabla_tonos(a):
    """
    Tabla (1, 256, 3) de los ajustes que van canal a canal (exposición, temperatura, medios
    tonos y contraste): con cv2.LUT cuestan ~1 ms por foto en vez de ~200 ms en coma flotante.
    """
    x = np.arange(256, dtype=np.float64) / 255.0
    canales = []
    for factor in (1.0 - 0.25 * a["temperatura"], 1.0, 1.0 + 0.25 * a["temperatura"]):     # B, G, R
        y = np.clip(x * (2.0 ** a["exposicion"]) * factor, 0.0, 1.0)
        if a["medios"] != 1.0:
            y = np.power(y, 1.0 / a["medios"])
        if a["contraste"] != 1.0:
            y = 0.5 + (y - 0.5) * a["contraste"]
        canales.append(np.clip(np.rint(y * 255.0), 0, 255))
    return np.stack(canales, axis=-1).astype(np.uint8).reshape(1, 256, 3)


def aplicar_ajustes(img, ajustes):
    """
    Foto BGR de 8 bits con los ajustes: exposición (EV), temperatura (−1 frío … +1 cálido),
    medios tonos (>1 aclara), contraste (alrededor del gris medio), saturación y nitidez
    (máscara de enfoque).
    """
    import cv2
    a = ajustes_validos(ajustes)
    if son_neutros(a):
        return img
    salida = cv2.LUT(img, _tabla_tonos(a))
    if a["saturacion"] != 1.0:
        gris = cv2.cvtColor(cv2.cvtColor(salida, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        salida = cv2.addWeighted(salida, a["saturacion"], gris, 1.0 - a["saturacion"], 0.0)
    if a["nitidez"] > 0:
        sigma = max(1.0, salida.shape[1] / 800.0)
        salida = cv2.addWeighted(salida, 1.0 + a["nitidez"], cv2.GaussianBlur(salida, (0, 0), sigma), -a["nitidez"], 0.0)
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
            img = aplicar_ajustes(img, ajustes)
            if img.shape[1] != ancho or img.shape[0] != alto:
                img = cv2.resize(img, (ancho, alto), interpolation=cv2.INTER_AREA)
            escritor.write(img)
            if escritos == 0:
                lado = 320.0 / max(img.shape[:2])
                cv2.imwrite(os.path.join(carpeta, MINIATURA_VIDEO), cv2.resize(img, None, fx=lado, fy=lado, interpolation=cv2.INTER_AREA),
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
            escritos += 1
            progreso(base + int((100 - base) * (i + 1) / len(fotos)))
    finally:
        escritor.release()
    if escritos < 2:
        raise RuntimeError("Quedan menos de dos fotos útiles para el vídeo ({} quitadas)".format(len(quitados)))
    resumen = {"archivo": nombre, "codec": codec, "fotogramas": escritos, "fotos": len(fotos),
               "quitados": quitados, "centrado": disco_si, "radio_px": round(radio, 1) if radio else None,
               "ancho": ancho, "alto": alto, "fps": fps, "objetivo": objetivo,
               "alinear": bool(alinear), "quitar_tapadas": bool(quitar_tapadas), "ajustes": ajustes_validos(ajustes)}
    with open(os.path.join(carpeta, RESUMEN), "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=1)
    log.info("Vídeo del timelapse: %s (%s, %d de %d fotos, %d quitadas%s)", os.path.join(carpeta, nombre), codec,
             escritos, len(fotos), len(quitados), ", disco centrado" if disco_si else "")
    return resumen
