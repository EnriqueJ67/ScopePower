"""
Corrección cosmética: píxeles calientes y fríos sueltos de una foto en crudo (mosaico
Bayer), antes de pasarla a color y alinearla.

El dark quita la mayoría de los píxeles calientes, pero no todos (Nebulosa del Mago,
26-nov, con la Saturn-C):
  - los que se saturan: en el dark valen 48 000 o el tope de 65 535 y, restado el
    dark, en cada foto les sobran 15 000;
  - los que se encienden y se apagan solos (señal telegráfica): uno valía 151 en el
    dark de 20 °C y 4 119 en el de 14 °C;
  - y, al revés, los que el dark resta de más cuando el sensor se enfría durante la
    sesión y se quedan a cero.
Con el dither cada pocas fotos, uno de estos píxeles cae varias fotos seguidas en el
mismo sitio del cielo, el rechazo del apilado no lo quita y el revelado lo convierte
en una bola de color.

Cada píxel se compara con los de su mismo color a 2 px (el subplano del mosaico) y
con sus cuatro vecinos a 1 px, que son de otros colores. Un píxel caliente es un pico
de un solo píxel: muy por encima de los de su color y con los de al lado en el fondo.
Una estrella, por fina que sea, sube también los píxeles de al lado: con una anchura
(FWHM) de 2 px el vecino a 1 px conserva la mitad del pico. Lo que se corrige se
sustituye por la mediana de los de su color.
"""

import cv2
import numpy as np

SIGMAS = 10.0           # el pico tiene que salirse al menos esto del ruido de su color...
VECES_VECINOS = 8.0     # ...y ser este número de veces lo que suben los de su color y los de al lado
SIGMAS_FRIO = 10.0      # un píxel frío: tantos sigmas por debajo de los de su color, con los de al lado normales
MAX_FRACCION = 0.002    # si sale más del 0,2 % de la foto, algo no cuadra (foto rara): no se toca nada


def _subplanos(cfa):
    return [cfa[i::2, j::2] for i in (0, 1) for j in (0, 1)]


def corregir(cfa):
    """
    cfa: mosaico Bayer (H, W), cualquier tipo numérico. Devuelve (corregido float32,
    número de píxeles calientes, número de fríos). No cambia `cfa`.
    """
    a = np.asarray(cfa, dtype=np.float32)
    h, w = a.shape
    if h < 16 or w < 16 or h % 2 or w % 2:
        return a.copy(), 0, 0
    mediana = np.empty_like(a)      # mediana de los 9 de su color (3×3 en el subplano)
    fondo = np.empty_like(a)        # fondo local de su color (5×5 en el subplano: 10×10 px)
    ruido = np.empty_like(a)
    cielo = np.empty_like(a)        # fondo del cielo de toda la foto para su color
    for (i, j), sp in zip(((0, 0), (0, 1), (1, 0), (1, 1)), _subplanos(a)):
        sp = np.ascontiguousarray(sp)
        m3 = cv2.medianBlur(sp, 3)
        m5 = cv2.medianBlur(sp, 5)
        resto = (sp - m3)[::2, ::2]
        sigma = 1.4826 * float(np.median(np.abs(resto - np.median(resto)))) + 1e-3
        mediana[i::2, j::2] = m3
        fondo[i::2, j::2] = m5
        ruido[i::2, j::2] = sigma
        cielo[i::2, j::2] = float(np.median(sp[::4, ::4]))
    exceso = a - fondo
    # Lo que suben los cuatro vecinos a 1 px (otros colores), cada uno sobre su propio fondo
    vecinos = cv2.blur(exceso, (3, 3), borderType=cv2.BORDER_REFLECT) * 9.0
    cruz = (vecinos - exceso - (np.roll(exceso, (1, 1), (0, 1)) + np.roll(exceso, (1, -1), (0, 1))
                                + np.roll(exceso, (-1, 1), (0, 1)) + np.roll(exceso, (-1, -1), (0, 1)))) / 4.0
    propio = mediana - fondo
    sube = np.maximum(np.maximum(propio, cruz), ruido)
    caliente = (a - mediana > SIGMAS * ruido) & (exceso > VECES_VECINOS * sube)
    # Un píxel frío queda muy por debajo del cielo de toda la foto, cosa que nada real hace (entre estrellas
    # muy juntas el centro puede quedar por debajo de sus vecinos de color, y hasta del fondo de 10×10 px)
    frio = ((mediana - a > SIGMAS_FRIO * ruido) & (cielo - a > SIGMAS_FRIO * ruido)
            & (np.abs(cruz) < 3.0 * ruido) & (np.abs(propio) < 3.0 * ruido))
    for m in (caliente, frio):          # bordes: el reflejo de los filtros no es de fiar
        m[:2, :] = m[-2:, :] = False
        m[:, :2] = m[:, -2:] = False
    malos = caliente | frio
    n = int(malos.sum())
    salida = a.copy()
    if n == 0 or n > MAX_FRACCION * a.size:
        return salida, 0, 0
    salida[malos] = mediana[malos]
    return salida, int(caliente.sum()), int(frio.sum())
