import os
import numpy as np
import cv2
from astropy.io import fits


# ─────────────────────────────────────────────────────────────────────────────
#  Debayer
# ─────────────────────────────────────────────────────────────────────────────
#
# OJO con la convención de OpenCV: el sufijo de COLOR_BayerXY NO es el color del
# píxel (0,0) sino el de las columnas 2 y 3 de la SEGUNDA fila del mosaico.
# Por eso un sensor RGGB (cabecera FITS BAYERPAT='RGGB') necesita COLOR_BayerBG2*.
# Usar COLOR_BayerRG2* con datos RGGB intercambia los canales rojo y azul.
#
BAYER_A_OPENCV_RGB = {
    "RGGB": cv2.COLOR_BayerBG2RGB,
    "BGGR": cv2.COLOR_BayerRG2RGB,
    "GRBG": cv2.COLOR_BayerGB2RGB,
    "GBRG": cv2.COLOR_BayerGR2RGB,
}


def debayer_rgb(cfa, patron="RGGB"):
    """
    Demosaica un mosaico CFA 2D (uint16 o uint8) y devuelve una imagen (H, W, 3)
    en orden RGB con el mismo dtype de entrada. Se trabaja a 16 bits; nunca hay
    que reducir a 8 bits antes de este paso.

    patron: valor de la cabecera FITS BAYERPAT ('RGGB', 'BGGR', 'GRBG', 'GBRG').
    """
    if cfa.ndim != 2:
        raise ValueError("debayer_rgb espera un mosaico CFA 2D")

    if cfa.dtype not in (np.uint8, np.uint16):
        cfa = np.clip(cfa, 0, np.iinfo(np.uint16).max).astype(np.uint16)

    patron = str(patron).strip().upper()
    if patron not in BAYER_A_OPENCV_RGB:
        print(f"⚠️ Patrón Bayer '{patron}' desconocido, se asume RGGB")
        patron = "RGGB"

    return cv2.cvtColor(cfa, BAYER_A_OPENCV_RGB[patron])


# ─────────────────────────────────────────────────────────────────────────────
#  STF (auto-stretch estilo PixInsight)
# ─────────────────────────────────────────────────────────────────────────────

def calcular_parametros_stf(data, clipping=0.0005, target_median=0.25):
    """
    Calcula black point, white point y gamma para un auto-stretch.

    Devuelve (black_val, white_val, gamma) tal que
        ((data - black_val) / (white_val - black_val)) ** gamma
    lleva la mediana de `data` a `target_median`.
    """
    data = np.asarray(data, dtype=np.float32)
    black_val, median_val, white_val = np.percentile(
        data, [clipping * 100.0, 50.0, (1.0 - clipping) * 100.0]
    )

    denom = white_val - black_val
    if denom < 1e-12:
        denom = 1e-12

    ratio = (median_val - black_val) / denom
    ratio = min(max(ratio, 1e-12), 1.0 - 1e-12)
    gamma = np.log(target_median) / np.log(ratio)

    return float(black_val), float(black_val + denom), float(gamma)


def aplicar_parametros_stf(data, black_val, white_val, gamma):
    """Aplica una transformación STF ya calculada. Devuelve float32 en [0, 1]."""
    denom = white_val - black_val
    if denom < 1e-12:
        denom = 1e-12
    stretched = (np.asarray(data, dtype=np.float32) - black_val) / denom
    stretched = np.clip(stretched, 0, 1)
    stretched = stretched ** gamma
    return np.clip(stretched, 0, 1)


def stf_auto_stretch(data, clipping=0.0005, target_median=0.25):
    """
    Emula un auto-stretch similar a la STF de PixInsight en una imagen monocromática.

    Parámetros:
      data          : Array 2D (un canal de la imagen).
      clipping      : Fracción de píxeles para determinar el black/white point.
      target_median : Valor objetivo para la mediana (en el rango [0, 1]).

    Devuelve:
      Una imagen estirada en el rango [0,1].
    """
    black_val, white_val, gamma = calcular_parametros_stf(data, clipping, target_median)
    return aplicar_parametros_stf(data, black_val, white_val, gamma)


def neutralizar_fondo(rgb):
    """
    Neutralización de fondo (equivalente simplificado a BackgroundNeutralization):
    escala cada canal para que la mediana del fondo de cielo sea igual en R, G y B.

    rgb: imagen lineal (H, W, 3) en float. Devuelve float32 (H, W, 3).
    """
    rgb = np.asarray(rgb, dtype=np.float32)
    medianas = np.array([np.median(rgb[..., i]) for i in range(3)], dtype=np.float32)
    if np.any(medianas <= 0):
        print("⚠️ Neutralización de fondo omitida: alguna mediana de canal es <= 0")
        return rgb
    referencia = medianas.mean()
    factores = referencia / medianas
    return rgb * factores.reshape(1, 1, 3)


def apply_stf_color(imagen, clipping=0.001800, target_median=0.10, output_8bit=True,
                    linked=True, neutralizar=True):
    """
     clipping=0.0018, target_median=0.10 OK
    Aplica un auto-stretch similar a STF de PixInsight a una imagen.

    Parámetros:
      imagen        : Puede ser una ruta a un archivo FITS o un array de imagen.
                      Se acepta:
                        - Imagen monocromática en formato 2D (H, W)
                        - Imagen en color en formato (H, W, 3) o (3, H, W)
      clipping      : Fracción de píxeles para determinar el black/white point.
      target_median : Valor objetivo para la mediana (en el rango [0,1]).
      output_8bit   : Si True (valor por defecto), la imagen se devuelve en uint8 ([0, 255]),
                      de lo contrario se devuelve normalizada en [0, 1].
      linked        : Si True (por defecto) los tres canales se estiran con los MISMOS
                      parámetros (calculados sobre la luminancia), conservando el color
                      real de los objetos. Si False, cada canal se estira por separado
                      (fuerza el fondo a gris y falsea el color).
      neutralizar   : Si True y linked=True, iguala primero las medianas del fondo de
                      cielo en R, G y B para eliminar dominantes (contaminación lumínica).

    Devuelve:
      La imagen procesada, en formato (H, W) para monocromo o (H, W, 3) para color.
    """
    # Si "imagen" es una ruta existente, cargar datos del FITS.
    if isinstance(imagen, str) and os.path.exists(imagen):
        with fits.open(imagen) as hdul:
            data = hdul[0].data.astype(np.float64)
        imagen = data

    def _salida(x):
        return (x * 255).astype(np.uint8) if output_8bit else x

    # Procesar imágenes monocromáticas (2D)
    if imagen.ndim == 2:
        return _salida(stf_auto_stretch(imagen, clipping=clipping, target_median=target_median))

    # Procesar imágenes en color (3D)
    elif imagen.ndim == 3:
        # Determinar el formato: (H, W, 3) o (3, H, W)
        if imagen.shape[-1] == 3:
            im_color = imagen  # ya en (H, W, 3)
        elif imagen.shape[0] == 3:
            im_color = np.transpose(imagen, (1, 2, 0))  # cambiar de (3, H, W) a (H, W, 3)
        # En algunos casos puede venir como (H, W, 1) -> tratar como monocromo:
        elif imagen.shape[-1] == 1:
            im_gray = imagen[..., 0]
            return _salida(stf_auto_stretch(im_gray, clipping=clipping, target_median=target_median))
        else:
            raise ValueError("La imagen en formato 3D no tiene 3 canales reconocidos.")

        im_color = np.asarray(im_color, dtype=np.float32)

        if linked:
            if neutralizar:
                im_color = neutralizar_fondo(im_color)
            # Parámetros únicos calculados sobre la luminancia
            luminancia = im_color.mean(axis=2)
            black_val, white_val, gamma = calcular_parametros_stf(
                luminancia, clipping=clipping, target_median=target_median
            )
            imagen_rgb = aplicar_parametros_stf(im_color, black_val, white_val, gamma)
            return _salida(imagen_rgb)

        # Estirado por canal (comportamiento antiguo)
        canales_procesados = []
        for i in range(3):
            canal = im_color[..., i]
            stretched = stf_auto_stretch(canal, clipping=clipping, target_median=target_median)
            canales_procesados.append(_salida(stretched))

        # Combinar canales para formar la imagen final en color
        imagen_rgb = np.stack(canales_procesados, axis=2)
        return imagen_rgb

    else:
        raise ValueError("La imagen debe ser un array 2D o 3D para procesarse.")

# Ejemplos de uso:
# Para archivo FITS en color:
#   img = apply_stf_color("static/resultado_apilado.fits")
# Para un mosaico CFA recién capturado:
#   rgb = debayer_rgb(cfa_uint16, "RGGB"); img = apply_stf_color(rgb)
