# alargamiento.py
#
# Forma de las estrellas de una foto del apilado: además de la anchura (el foco), cuánto
# más largas que anchas salen y hacia dónde. Sirve para avisar de que la montura deriva
# DENTRO de cada exposición, que es algo que el apilado no puede arreglar: alinea una foto
# con otra, pero la estrella ya viene hecha un trazo.
#
# El 7-nov-2026 la Nebulosa Creciente (61 × 20 s, a 73° de altura) salió con las estrellas
# «en peladilla». Las 51 fotos sueltas que quedaban tenían todas las estrellas alargadas
# (eje mayor entre eje menor 1,9 de mediana, 6,3 × 3,3 px) a lo largo del acimut, con 6 px
# de deriva entre fotos seguidas; el alineado estaba bien (0,2 px). La puntuación de
# calidad (número de estrellas × nitidez) no lo distingue y entraron todas, y nada avisaba.
#
# Se mide sobre las mismas estrellas brillantes, sin saturar y aisladas que ya se usaban
# para la anchura. La anchura (FWHM) sigue siendo la de enfoque.py, para que los números de
# la sesión se puedan comparar con los de otras noches. La forma se saca aparte, con
# segundos momentos de peso gaussiano ajustado a la propia estrella (momentos adaptativos):
# los momentos sin peso de enfoque.py suman el ruido del recuadro por igual en los dos ejes
# y redondean la estrella (con las fotos del 7-nov daban 1,4 donde hay 1,9).
#
# Lo que cuenta es lo COMÚN a todas las estrellas de la foto: se toma la mediana de cada
# momento antes de sacar los ejes, así una aberración del borde del campo (que alarga cada
# estrella hacia un lado distinto) o una doble no cuentan como movimiento del tubo.

import math

import numpy as np
import cv2

# Umbral y ventana, elegidos con las dos noches de las que hay fotos sueltas (AZ GoTo, 20 s,
# 2,9″/px). Una altacimutal alarga algo las estrellas también en una noche buena: las 50 fotos
# de la Burbuja del 10-oct-2026 dan 1,28 de mediana, con ratos de 1,6 (trazos de 3 px, 5 en el
# peor rato), y su apilado salió bien. Las 51 de la Creciente dan 1,91 (trazos de 8 px, 5,7
# en el mejor rato). Con la mediana de 9 fotos y 1,65 la noche buena no avisa nunca y la mala
# avisa desde la tercera foto y no se apaga; con 1,6 la buena avisa a ratos y con 1,7 la mala
# se apaga un rato a media sesión.
UMBRAL_DEFECTO = 1.65      # eje mayor / eje menor a partir del cual se avisa ([Apilado] aviso_alargamiento)
VENTANA = 9                # el aviso mira la mediana de las últimas fotos, no una sola (una racha de viento)
MINIMO_FOTOS = 3
HISTERESIS = 0.15          # el aviso se quita cuando la mediana baja de umbral − esto
ALARGAMIENTO_OBJETIVO = 1.15                    # con la exposición sugerida las estrellas quedarían así
EXPOSICIONES = (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 45, 60, 90, 120)
RADIO = 12                 # px del recuadro de cada estrella: caben un trazo de 15 px y su fondo
MOMENTO_MAXIMO = 60.0      # px²: por encima no es una estrella (18 px de anchura)
MINIMO_ESTRELLAS = 5       # con menos no se puede hablar de una forma común a la foto
_MALLA = np.mgrid[-RADIO:RADIO + 1, -RADIO:RADIO + 1].astype(np.float64)


def estrellas_brillantes(plano, maximo=60):
    """
    (imagen float32, [(x, y), ...]) de hasta `maximo` estrellas brillantes, sin saturar y
    aisladas (máximos locales de la foto suavizada, 20 sigmas sobre el fondo), de más a
    menos brillo. Para medir su anchura y su forma.
    """
    img = np.asarray(plano, dtype=np.float32)
    suav = cv2.GaussianBlur(img, (0, 0), 1.2)
    fondo = float(np.median(suav[::8, ::8]))
    mad = float(np.median(np.abs(suav[::8, ::8] - fondo))) * 1.4826 + 1e-3
    dil = cv2.dilate(suav, np.ones((15, 15), np.uint8))
    cand = np.argwhere((suav == dil) & (suav - fondo > 20.0 * mad) & (img < 55000))
    h, w = img.shape
    cand = [(suav[y, x], y, x) for y, x in cand if 10 < y < h - 10 and 10 < x < w - 10]
    cand.sort(reverse=True)
    return img, [(int(x), int(y)) for __, y, x in cand[:maximo]]


def momentos_adaptativos(img, x, y, radio=RADIO, iteraciones=12):
    """
    (sxx, syy, sxy) de la estrella que hay en (x, y): segundos momentos de la luz con un peso
    gaussiano que se va ajustando a la forma de la propia estrella, con el fondo del borde del
    recuadro restado. None si no converge (borde de la foto, ruido, una mancha que no es estrella).
    """
    rec = img[y - radio:y + radio + 1, x - radio:x + radio + 1]
    if x < radio or y < radio or rec.shape != (2 * radio + 1, 2 * radio + 1):
        return None
    rec = rec.astype(np.float64)
    rec = rec - np.median(np.concatenate([rec[0], rec[-1], rec[1:-1, 0], rec[1:-1, -1]]))
    yy, xx = _MALLA if radio == RADIO else np.mgrid[-radio:radio + 1, -radio:radio + 1].astype(np.float64)
    cx = cy = sxy = 0.0
    sxx = syy = 4.0
    for __ in range(iteraciones):
        det = sxx * syy - sxy * sxy
        if det <= 1e-6 or sxx > MOMENTO_MAXIMO or syy > MOMENTO_MAXIMO:
            return None
        dx, dy = xx - cx, yy - cy
        luz = np.exp(-0.5 * (syy * dx * dx - 2.0 * sxy * dx * dy + sxx * dy * dy) / det) * rec
        total = luz.sum()
        if total <= 0:
            return None
        ncx, ncy = (luz * xx).sum() / total, (luz * yy).sum() / total
        dx, dy = xx - ncx, yy - ncy
        # Con un peso igual a la estrella el momento medido es la mitad del real: por eso el 2
        nxx, nyy, nxy = (2.0 * (luz * dx * dx).sum() / total, 2.0 * (luz * dy * dy).sum() / total,
                         2.0 * (luz * dx * dy).sum() / total)
        if abs(ncx) > radio / 2 or abs(ncy) > radio / 2 or nxx <= 0.05 or nyy <= 0.05:
            return None
        quieto = max(abs(ncx - cx), abs(ncy - cy), abs(nxx - sxx), abs(nyy - syy), abs(nxy - sxy)) < 1e-3
        cx, cy, sxx, syy, sxy = ncx, ncy, nxx, nyy, nxy
        if quieto:
            break
    return sxx, syy, sxy


def ejes(sxx, syy, sxy):
    """(sigma mayor, sigma menor, ángulo del eje mayor en grados: 0 = eje x de la foto, 90 = eje y)."""
    medio, dif = (sxx + syy) / 2.0, math.hypot((sxx - syy) / 2.0, sxy)
    return (math.sqrt(max(medio + dif, 1e-6)), math.sqrt(max(medio - dif, 1e-6)),
            math.degrees(0.5 * math.atan2(2.0 * sxy, sxx - syy)))


def medir(plano, maximo=60):
    """
    Forma de las estrellas de una foto (canal verde a resolución completa). None si no hay
    estrellas que medir; si no, un dict:

      fwhm           anchura a media altura (px), mediana estrella a estrella (el foco)
      alargamiento   eje mayor / eje menor de la forma común a todas (1 = redondas); NaN,
                     como los demás datos de la forma, con menos de MINIMO_ESTRELLAS estrellas
      angulo_deg     dirección del eje mayor en la foto (0 = eje x, 90 = eje y), de −90 a 90
      fwhm_mayor, fwhm_menor   anchura a media altura a lo largo de cada eje (px)
      trazo_px       largo del trazo recto que explicaría lo que sobra en el eje mayor (orientativo:
                     un trazo recto no es gaussiano y el peso lo ve hasta un 15 % más largo)
      n              estrellas con la forma medida
    """
    import enfoque
    img, estrellas = estrellas_brillantes(plano, maximo)
    anchos = [a for a in (enfoque.fwhm(img, x, y) for x, y in estrellas) if np.isfinite(a)]
    if not anchos:
        return None
    momentos = [m for m in (momentos_adaptativos(img, x, y) for x, y in estrellas) if m is not None]
    if len(momentos) < MINIMO_ESTRELLAS:
        # La anchura se puede dar con una sola estrella; la forma común, no
        nan = float("nan")
        return {"fwhm": float(np.median(anchos)), "alargamiento": nan, "angulo_deg": nan,
                "fwhm_mayor": nan, "fwhm_menor": nan, "trazo_px": nan, "n": len(momentos)}
    m = np.array(momentos, dtype=np.float64)
    mayor, menor, angulo = ejes(float(np.median(m[:, 0])), float(np.median(m[:, 1])), float(np.median(m[:, 2])))
    return {
        "fwhm": float(np.median(anchos)),
        "alargamiento": mayor / menor,
        "angulo_deg": angulo,
        "fwhm_mayor": 2.355 * mayor, "fwhm_menor": 2.355 * menor,
        # Un trazo recto de largo L suma L²/12 a la varianza de la estrella a lo largo del trazo
        "trazo_px": math.sqrt(12.0 * max(mayor * mayor - menor * menor, 0.0)),
        "n": len(momentos),
    }


def exposicion_sugerida(trazo_px, fwhm_menor, exposicion):
    """
    Exposición (s) con la que el trazo se quedaría en lo que deja las estrellas en
    ALARGAMIENTO_OBJETIVO, suponiendo una deriva constante (el trazo crece con el tiempo).
    Se redondea hacia abajo a un valor redondo. None si no hay nada que ganar o no se puede.
    """
    if not (trazo_px and fwhm_menor and exposicion) or trazo_px <= 0 or exposicion <= 0:
        return None
    sigma_menor = fwhm_menor / 2.355
    trazo_admisible = sigma_menor * math.sqrt(12.0 * (ALARGAMIENTO_OBJETIVO ** 2 - 1.0))
    tope = exposicion * trazo_admisible / trazo_px
    if tope >= 0.8 * exposicion:
        return None
    validas = [e for e in EXPOSICIONES if e <= tope]
    return validas[-1] if validas else None


class Aviso:
    """
    Aviso de estrellas alargadas a lo largo de la sesión: mira la mediana de las últimas
    VENTANA fotos y tiene histéresis, para no encenderse por una racha de viento ni
    parpadear cuando la mediana ronda el umbral.
    """

    def __init__(self):
        self.medidas = []          # (alargamiento, trazo_px, fwhm_menor, exposición) de cada foto medida
        self.activo = False

    def reiniciar(self):
        self.medidas, self.activo = [], False

    def anadir(self, medida, exposicion, umbral=UMBRAL_DEFECTO):
        """
        Apunta la medida de una foto (dict de medir()) y devuelve "empieza" si con ella el
        aviso se enciende, "termina" si se apaga y None si sigue como estaba.
        """
        if not medida or not math.isfinite(medida.get("alargamiento", float("nan"))):
            return None
        self.medidas.append((medida["alargamiento"], medida["trazo_px"], medida["fwhm_menor"], float(exposicion or 0)))
        del self.medidas[:-200]
        antes = self.activo
        ultimas = self.medidas[-VENTANA:]
        if umbral <= 0 or len(ultimas) < MINIMO_FOTOS:
            self.activo = False
        else:
            mediana = float(np.median([u[0] for u in ultimas]))
            self.activo = mediana >= (umbral - HISTERESIS if antes else umbral)
        if self.activo and not antes:
            return "empieza"
        if antes and not self.activo:
            return "termina"
        return None

    def estado(self):
        """Para la web: la forma de las últimas fotos y, si el aviso está encendido, qué exposición probar."""
        if not self.medidas:
            return None
        ultimas = self.medidas[-VENTANA:]
        trazo = float(np.median([u[1] for u in ultimas]))
        exposicion = ultimas[-1][3]
        return {
            "ultimo": round(self.medidas[-1][0], 2),
            "reciente": round(float(np.median([u[0] for u in ultimas])), 2),
            "fotos": len(ultimas),
            "trazo_px": round(trazo, 1),
            "exposicion": exposicion,
            "aviso": self.activo,
            "exposicion_sugerida": (exposicion_sugerida(trazo, float(np.median([u[2] for u in ultimas])), exposicion)
                                    if self.activo else None),
        }
