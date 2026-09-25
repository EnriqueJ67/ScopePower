"""
Defectos de una sola foto que estropean el apilado aunque la foto tenga buenas
estrellas: rastros de satélites y aviones, y luces parásitas (una linterna, los
faros de un coche) que solo salen en esa foto.

- detectar_rastro(plano): busca líneas rectas largas y finas. Se trabaja a media
  resolución; las manchas gordas (el núcleo de un cúmulo, una galaxia, las
  estrellas brillantes) se borran antes de buscar líneas para que no las
  confunda con un rastro.
- rejilla_fondo(plano) + detectar_luz(rejilla, historial, ruido): compara el
  fondo de cielo por zonas con el de las últimas fotos. Una luz que ilumina el
  tubo sube el fondo de golpe en parte de la foto; el cielo cambia despacio
  (crepúsculo, objeto que baja), y si el cambio se queda (una farola que se
  enciende) deja de marcarse en cuanto domina el historial.
"""

import cv2
import numpy as np

# Rastros (en píxeles de media resolución)
SIGMA_RASTRO = 2.0          # umbral sobre el fondo, en sigmas del ruido suavizado
ANCHO_MANCHA = 9            # lo que sobrevive a una apertura de este tamaño es "gordo"
FRACCION_LONGITUD = 0.20    # longitud mínima del rastro, fracción del lado corto
HUECO_MAX = 12              # huecos tolerados dentro de una línea (estrellas, parpadeo)

# Luces parásitas
CELDAS = 12                 # rejilla CELDAS × CELDAS de medianas del fondo
SIGMA_LUZ = 1.5             # subida del fondo por zonas, en sigmas del ruido por píxel
SALTO_GENERAL = 0.25        # subida de toda la foto a la vez, fracción del fondo
FOTOS_HISTORIAL = 3         # fotos anteriores con las que se compara


def media_resolucion(plano):
    img = np.asarray(plano, dtype=np.float32)
    return cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)


def mediana_mad(img):
    muestra = img[::4, ::4]
    mediana = float(np.median(muestra))
    mad = float(np.median(np.abs(muestra - mediana))) * 1.4826 + 1e-3
    return mediana, mad


def detectar_rastro(plano, peq=None):
    """
    Busca un rastro (línea recta larga y fina) en el plano (H, W).
    Devuelve None si no hay, o un dict con "longitud" (px de la foto completa),
    "angulo" (grados) y "segmento" (x1, y1, x2, y2 en px de la foto completa).
    peq: el plano ya reducido a media resolución, si se tiene.
    """
    if peq is None:
        peq = media_resolucion(plano)
    # Un suavizado ligero sube la señal de una línea de 2-3 px frente al ruido
    suave = cv2.GaussianBlur(peq, (0, 0), 1.2)
    mediana, mad = mediana_mad(suave)
    mascara = (suave > mediana + SIGMA_RASTRO * mad).astype(np.uint8)

    # Fuera lo gordo: una línea fina desaparece con la apertura, un cúmulo no.
    # Se dilata un poco para llevarse también el halo de las estrellas brillantes.
    nucleo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ANCHO_MANCHA, ANCHO_MANCHA))
    gordo = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, nucleo)
    gordo = cv2.dilate(gordo, nucleo)
    mascara[gordo > 0] = 0

    # Las estrellas sueltas son manchas pequeñas y redondas: fuera también,
    # quedan los trozos alargados (y el ruido, que no forma líneas largas)
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(mascara, connectivity=8)
    if n > 1:
        ancho = stats[1:, cv2.CC_STAT_WIDTH]
        alto = stats[1:, cv2.CC_STAT_HEIGHT]
        cortos = np.concatenate([[False], np.maximum(ancho, alto) < 6])
        mascara[cortos[etiquetas]] = 0

    lado = min(peq.shape)
    longitud_min = FRACCION_LONGITUD * lado
    lineas = cv2.HoughLinesP(mascara, rho=1, theta=np.pi / 360, threshold=int(longitud_min * 0.5),
                             minLineLength=int(longitud_min), maxLineGap=HUECO_MAX)
    if lineas is None:
        return None
    x1, y1, x2, y2 = max(lineas[:, 0, :], key=lambda s: np.hypot(s[2] - s[0], s[3] - s[1]))
    longitud = float(np.hypot(x2 - x1, y2 - y1))
    return {
        "longitud": round(2 * longitud),
        "angulo": round(float(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180, 1),
        "segmento": [int(2 * x1), int(2 * y1), int(2 * x2), int(2 * y2)],
    }


def rejilla_fondo(peq):
    """Mediana del fondo en una rejilla CELDAS × CELDAS (robusta a las estrellas)."""
    h, w = peq.shape
    rejilla = np.empty((CELDAS, CELDAS), dtype=np.float32)
    for i in range(CELDAS):
        for j in range(CELDAS):
            celda = peq[i * h // CELDAS:(i + 1) * h // CELDAS:2, j * w // CELDAS:(j + 1) * w // CELDAS:2]
            rejilla[i, j] = np.median(celda)
    return rejilla


def detectar_luz(rejilla, historial, ruido):
    """
    ¿Se ha iluminado el fondo de golpe en esta foto? rejilla: la de esta foto;
    historial: rejillas de las fotos anteriores (misma exposición); ruido: sigma
    del ruido por píxel. Devuelve None o un dict con "subida" (en sigmas, la
    subida de la zona más iluminada sobre el resto de la foto, o la de toda la
    foto si el salto es general) y "zona" (fracción de la foto afectada).

    El cielo cambia casi igual en toda la foto (crepúsculo, amanecer, objeto
    que baja): esa parte común no cuenta mientras no sea un salto brusco. Una
    linterna o unos faros iluminan más un lado que otro.
    """
    if len(historial) < 1:
        return None
    referencia = np.median(np.stack(historial[-FOTOS_HISTORIAL:]), axis=0)
    diferencia = rejilla - referencia
    comun = float(np.median(diferencia))
    local = (diferencia - comun) / max(ruido, 1e-3)
    maxima = float(local.max())
    if maxima >= SIGMA_LUZ:
        return {"subida": round(maxima, 2), "zona": round(float((local >= SIGMA_LUZ).mean()), 2)}
    if comun > SALTO_GENERAL * float(np.median(referencia)):
        return {"subida": round(comun / max(ruido, 1e-3), 2), "zona": 1.0}
    return None
