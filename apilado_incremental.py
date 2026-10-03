"""
Apilado incremental: en vez de guardar las N mejores fotos y volver a apilarlas
todas con cada foto nueva (coste que crece con N), las primeras fotos fijan una
referencia y cada foto nueva se compara con ella y se suma. El coste por foto es
fijo y el apilado no tiene tope: la imagen sigue mejorando toda la sesión.

- Acumulador(fotogramas): sigma-clip clásico de las primeras fotos (las mismas
  que guarda apilado.py, ya alineadas). De ahí salen, por píxel y canal, la suma
  de los valores aceptados, cuántos son y el ruido de una foto (sigma).
- Acumulador.anadir(r, g, b, M): nivela el fondo de la foto con el de la
  referencia (el cielo se aclara u oscurece a lo largo de la sesión y en una
  altacimutal el gradiente gira), rechaza los píxeles que se apartan más de
  k·sigma de la referencia (satélites, aviones, rayos cósmicos) y suma el resto.
  La referencia es la media acumulada, así que mejora con cada foto.

Lo que se pierde frente a «las N mejores»: una foto que ha entrado ya no se puede
quitar, así que la calidad hay que decidirla antes de sumarla.
"""

import warnings

import cv2
import numpy as np

MIN_REFERENCIA = 3      # fotos que debe tener un píxel en la referencia para poder rechazar con ella
# Fotos mínimas de la referencia: en un sigma-clip de n fotos a 2 sigmas ningún valor
# puede apartarse de la media más de raíz(n − 1) sigmas, así que con 5 o menos no se
# rechaza nada (medido con fotos reales: con 5 el rastro se queda, con 10 se va)
MIN_FOTOS_REFERENCIA = 6
CELDAS_NIVEL = 8        # rejilla CELDAS × CELDAS con la que se nivela el fondo
SUBMUESTREO = 4         # el fondo y el ruido de la foto se miden con 1 de cada 4 píxeles
NIVELES_RUIDO = 40      # tramos de brillo del modelo de ruido (ruido en función del nivel)
COBERTURA_ZONA = 0.5    # la vista previa se recorta a los píxeles con al menos esta fracción de las fotos
RUIDO_MAX = 3.0         # tope del factor por el que una foto ruidosa ensancha el umbral


def mascara_valida(M, ancho, alto):
    """
    Píxeles de una foto alineada con la matriz M que tienen dato de verdad: fuera
    quedan el relleno del warpAffine y el borde de 1 px que la interpolación
    mezcla con ese relleno.
    """
    if M is None or np.allclose(M, np.eye(2, 3)):
        return np.ones((alto, ancho), dtype=bool)
    unos = np.ones((alto, ancho), dtype=np.uint8)
    dentro = cv2.warpAffine(unos, np.asarray(M, dtype=np.float64), (ancho, alto), flags=cv2.INTER_NEAREST)
    return cv2.erode(dentro, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)


def niveles_iniciales(planos, mascaras):
    """
    Mapas de fondo de cada foto de referencia respecto a la media de todas (ver
    nivel_fondo). Sin nivelarlas entre sí, lo que cambia el cielo de una foto a
    otra se contaría como ruido y la referencia no rechazaría nada.
    """
    h, w = planos[0].shape
    suma = np.zeros((h, w), dtype=np.float32)
    cuenta = np.zeros((h, w), dtype=np.uint16)
    for plano, mascara in zip(planos, mascaras):
        suma += np.where(mascara, plano, 0)
        cuenta += mascara
    media = suma / np.maximum(cuenta, 1)
    con_media = cuenta >= MIN_REFERENCIA
    return [nivel_fondo(plano, media, mascara & con_media)[0] for plano, mascara in zip(planos, mascaras)]


def _clip(bloque, valida, sigma):
    """
    Sigma-clip de un bloque (fotos, filas, columnas). valida: máscara de la misma
    forma, o None si todas las fotos tienen dato en todo el bloque (más rápido).
    Devuelve (suma, cuenta, std, n_validas) del bloque.
    """
    if valida is None:
        n = bloque.shape[0]
        media = bloque.mean(axis=0)
        desv = bloque.std(axis=0)
        acepta = np.abs(bloque - media) <= sigma * desv + 1e-6
    else:
        n = valida.sum(axis=0)
        divisor = np.maximum(n, 1)
        media = np.where(valida, bloque, 0.0).sum(axis=0) / divisor
        desv = np.sqrt(np.where(valida, (bloque - media) ** 2, 0.0).sum(axis=0) / divisor)
        acepta = valida & (np.abs(bloque - media) <= sigma * desv + 1e-6)
    # Con pocas fotos la desviación de la muestra se queda corta: n / (n − 1)
    return (np.where(acepta, bloque, 0.0).sum(axis=0), acepta.sum(axis=0),
            desv * np.sqrt(n / np.maximum(n - 1, 1)), n)


def estadistica_inicial(planos, mascaras, sigma=2.0, niveles=None, filas_por_bloque=128):
    """
    Sigma-clip de las fotos de referencia (como apilado.apilar_sigma_clip, pero
    sin contar los píxeles de relleno y restando a cada foto su mapa de fondo de
    `niveles`). Devuelve (suma, cuenta, std, n_validas): suma y cuenta de los
    valores aceptados, desviación típica de una foto y cuántas fotos cubren cada
    píxel. Por bloques de filas, para no gastar memoria.
    """
    h, w = planos[0].shape
    suma = np.zeros((h, w), dtype=np.float32)
    cuenta = np.zeros((h, w), dtype=np.uint16)
    std = np.zeros((h, w), dtype=np.float32)
    n_validas = np.zeros((h, w), dtype=np.uint16)
    for y0 in range(0, h, filas_por_bloque):
        y1 = min(h, y0 + filas_por_bloque)
        bloque = np.stack([p[y0:y1] for p in planos], axis=0).astype(np.float32)
        if niveles is not None:
            bloque -= np.stack([m[y0:y1] for m in niveles], axis=0)
        valida = np.stack([m[y0:y1] for m in mascaras], axis=0)
        # El relleno solo está en los bordes: las columnas del medio, con dato en
        # todas las fotos, van por el camino rápido
        completas = valida.all(axis=(0, 1))
        x0 = int(np.argmax(completas))
        x1 = w - int(np.argmax(completas[::-1]))
        if not completas[x0:x1].all():
            x0 = x1 = 0
        for a, b, v in ((0, x0, valida[:, :, :x0]), (x0, x1, None), (x1, w, valida[:, :, x1:])):
            if b > a:
                suma[y0:y1, a:b], cuenta[y0:y1, a:b], std[y0:y1, a:b], n_validas[y0:y1, a:b] = \
                    _clip(bloque[:, :, a:b], v, sigma)
    return suma, cuenta, std, n_validas


def sigma_referencia(ref, std, n_validas):
    """
    Ruido de una foto en cada píxel. La desviación medida con ~10 fotos baila un
    ±25 % de un píxel a otro, y donde por azar sale corta se rechazarían datos
    buenos: se le pone de suelo el ruido típico de su nivel de brillo (la mediana
    de las desviaciones de los píxeles igual de brillantes). Donde la referencia
    tiene menos de MIN_REFERENCIA fotos el ruido es infinito: no se rechaza nada.
    """
    completos = n_validas >= n_validas.max()
    r = ref[::SUBMUESTREO, ::SUBMUESTREO][completos[::SUBMUESTREO, ::SUBMUESTREO]]
    s = std[::SUBMUESTREO, ::SUBMUESTREO][completos[::SUBMUESTREO, ::SUBMUESTREO]]
    sigma = std.copy()
    if r.size >= 100 * NIVELES_RUIDO:
        cortes = np.unique(np.percentile(r, np.linspace(0, 100, NIVELES_RUIDO + 1)))
        tramo = np.clip(np.digitize(r, cortes) - 1, 0, len(cortes) - 2)
        niveles, ruidos = [], []
        for j in range(len(cortes) - 1):
            en_tramo = tramo == j
            if en_tramo.sum() >= 20:
                niveles.append(float(np.median(r[en_tramo])))
                ruidos.append(float(np.median(s[en_tramo])))
        if niveles:
            # Más luz, más ruido de fotones: el suelo no baja al subir el brillo
            ruidos = np.maximum.accumulate(np.array(ruidos, dtype=np.float32))
            suelo = np.interp(ref.ravel(), niveles, ruidos).reshape(ref.shape).astype(np.float32)
            np.maximum(sigma, suelo, out=sigma)
    sigma[n_validas < MIN_REFERENCIA] = np.inf
    return sigma


def nivel_fondo(x, ref, usable, sigma=None):
    """
    Diferencia de fondo entre la foto x y la referencia, por zonas: mediana de
    (x − ref) en una rejilla de CELDAS_NIVEL × CELDAS_NIVEL celdas, ampliada con
    suavidad al tamaño de la foto. El objeto y las estrellas están en las dos y se
    cancelan; queda lo que ha cambiado el cielo. Devuelve (mapa a restar, ruido de
    la foto relativo al de la referencia, nivel medio). Sin `sigma` (el ruido de
    la referencia) el ruido relativo es 1.
    """
    alto, ancho = x.shape
    s = SUBMUESTREO
    d = x[::s, ::s] - ref[::s, ::s]
    ok = usable[::s, ::s]
    ch, cw = d.shape[0] // CELDAS_NIVEL, d.shape[1] // CELDAS_NIVEL
    if ch < 4 or cw < 4 or not ok.any():
        return np.zeros((alto, ancho), dtype=np.float32), 1.0, 0.0
    d = np.where(ok, d, np.nan)[:ch * CELDAS_NIVEL, :cw * CELDAS_NIVEL]
    celdas = d.reshape(CELDAS_NIVEL, ch, CELDAS_NIVEL, cw).transpose(0, 2, 1, 3).reshape(CELDAS_NIVEL, CELDAS_NIVEL, -1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)    # celdas sin ningún dato
        medianas = np.nanmedian(celdas, axis=2)
    # Una celda con pocos datos (borde sin cubrir) toma el nivel general
    medianas[np.isfinite(celdas).mean(axis=2) < 0.2] = np.nan
    general = float(np.nanmedian(d))
    medianas = np.where(np.isfinite(medianas), medianas, general).astype(np.float32)
    mapa = cv2.resize(medianas, (ancho, alto), interpolation=cv2.INTER_CUBIC)

    # Ruido de esta foto frente al de la referencia: el warpAffine promedia más o
    # menos según la fracción de píxel del desplazamiento, y el cielo cambia
    if sigma is None:
        return mapa, 1.0, general
    resto = d - np.repeat(np.repeat(medianas, ch, axis=0), cw, axis=1)
    ruido = 1.4826 * float(np.nanmedian(np.abs(resto)))
    sig = sigma[::s, ::s][ok]
    sig = sig[np.isfinite(sig)]
    tipico = float(np.median(sig)) if sig.size else 0.0
    return mapa, (ruido / tipico if tipico > 0 else 1.0), general


class Acumulador:
    """Suma, cuenta y sigma por píxel y canal; ver la cabecera del módulo."""

    CANALES = ("r", "g", "b")

    def __init__(self, fotogramas, k=3.0, sigma_inicial=2.0):
        """
        fotogramas: dicts con los planos alineados "r", "g", "b" y su matriz "M"
        (None o identidad para la foto de referencia del alineado).
        k: umbral de rechazo de las fotos siguientes, en sigmas.
        """
        self.alto, self.ancho = fotogramas[0]["g"].shape
        self.k = float(k)
        self.n = len(fotogramas)
        self.suma, self.cuenta, self.sigma = {}, {}, {}
        mascaras = [mascara_valida(f.get("M"), self.ancho, self.alto) for f in fotogramas]
        for c in self.CANALES:
            planos = [f[c] for f in fotogramas]
            suma, cuenta, std, n_validas = estadistica_inicial(planos, mascaras, sigma_inicial,
                                                               niveles_iniciales(planos, mascaras))
            self.suma[c], self.cuenta[c] = suma, cuenta
            self.sigma[c] = sigma_referencia(suma / np.maximum(cuenta, 1), std, n_validas)

    def anadir(self, r, g, b, M):
        """
        Suma una foto alineada (planos H×W). Devuelve un dict con la fracción de
        píxeles rechazados, el cambio de fondo respecto a la referencia (ADU del
        verde) y el factor de ruido aplicado al umbral.
        """
        valida = mascara_valida(M, self.ancho, self.alto)
        n_validos = max(int(valida.sum()), 1)
        rechazados, niveles, ruidos = [], {}, {}
        for c, plano in zip(self.CANALES, (r, g, b)):
            x = np.asarray(plano, dtype=np.float32)
            cuenta = self.cuenta[c]
            ref = self.suma[c] / np.maximum(cuenta, 1)
            mapa, ruido, niveles[c] = nivel_fondo(x, ref, valida & (cuenta >= MIN_REFERENCIA), self.sigma[c])
            x = x - mapa
            ruidos[c] = float(min(max(ruido, 1.0), RUIDO_MAX))
            np.subtract(ref, x, out=ref)
            np.abs(ref, out=ref)
            acepta = valida & (ref <= (self.k * ruidos[c]) * self.sigma[c])
            self.suma[c] += np.where(acepta, x, 0.0)
            cuenta += acepta
            rechazados.append(1.0 - int(acepta.sum()) / n_validos)
        self.n += 1
        return {"rechazado": float(np.mean(rechazados)), "nivel": float(niveles["g"]), "ruido": float(ruidos["g"])}

    def imagen(self):
        """Planos (r, g, b) float32 del apilado: media de los valores aceptados."""
        return tuple(self.suma[c] / np.maximum(self.cuenta[c], 1) for c in self.CANALES)

    def cobertura(self):
        """(fotos aceptadas por píxel en el verde, mínimo para entrar en el recorte de la vista previa)."""
        return self.cuenta["g"], max(1.0, COBERTURA_ZONA * self.n)
