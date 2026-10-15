# cometa.py
#
# Apilado por el núcleo del cometa, en paralelo al apilado normal.
#
# Un cometa se mueve respecto a las estrellas (segundos de arco por minuto: en una
# sesión de una hora son decenas de píxeles con la Saturn-C y el Evoguide). El
# apilado normal alinea por las estrellas (WCS de ASTAP) y el cometa sale corrido.
# Aquí, con la misma matriz de estrellas (que ya lleva la rotación de campo de la
# altacimutal), se añade la traslación que deja el núcleo siempre en el mismo
# píxel de la referencia: las estrellas salen como rastros y el cometa queda fijo.
#
# Dónde está el núcleo en cada foto:
#   1. Previsión: efemérides del MPC (cuerpos_menores.py, sin Internet) para el
#      instante medio de la exposición, pasadas a píxel con el WCS de la
#      referencia. La efeméride tiene un error de minutos de arco, pero es
#      constante a lo largo de la noche: la primera medida lo corrige y después
#      la ventana de búsqueda se estrecha.
#   2. Medida: en la foto ya alineada por las estrellas se busca, alrededor de la
#      previsión, la condensación más brillante que no sea una estrella (apertura
#      morfológica que quita los puntos y suavizado) y se afina con el centroide.
#   3. Sin efeméride (cometa que no está en las listas del MPC, apuntado por JPL
#      Horizons o por nombre): la primera foto parte del centro del encuadre (el
#      GoTo lo ha centrado) y las siguientes de la extrapolación lineal de las
#      medidas anteriores.
#
# Los rastros de las estrellas no los puede rechazar un sigma-clip incremental
# (de una foto a la siguiente apenas se mueven), así que el apilado del cometa es
# una media simple con el fondo nivelado (apilado_incremental.nivel_fondo). La
# imagen bonita (cometa fijo + estrellas fijas) se compone después con las dos.
#
# Configuración ([Cometa] en config.ini, todo opcional):
#   activo = auto | si | no   auto: solo si el objeto del GoTo es un cometa
#   radio_busqueda = 80       px alrededor de la previsión (primera foto, o sin efeméride)
#   radio_centroide = 8       px del centroide del núcleo
#   apertura = 11             px del disco de la apertura que quita las estrellas (hasta ~5 px de anchura)
#   suavizado = 4             sigma (px) del suavizado posterior
#   minimo_sigma = 5          el pico del núcleo debe superar el fondo en tantos sigmas
#   vista_previa_cada = 1     la vista previa del cometa (varios segundos en el mini PC) cada N fotos

import os
import re
import logging
import configparser
from datetime import datetime, timedelta

import numpy as np
import cv2

import apilado_incremental
from idioma import _

log = logging.getLogger("scopepower.cometa")

# C/2023 A3, P/2024 X1, 12P/Pons-Brooks, 2P, 161P/Hartley-IRAS, 2I/Borisov...
PATRON_COMETA = re.compile(r"^\s*(?:[CPXDAI]/\s?\d{4}\s?[A-Z]|\d+[PDI](?:/|-|\s|$))", re.I)

RUTA_FITS = os.path.join("static", "resultado_cometa.fits")
RUTA_PREVIEW = os.path.join("static", "resultado_cometa_preview.jpg")


# ═════════════════════════════════════════════════════════════════════════════
# Configuración y detección del cometa
# ═════════════════════════════════════════════════════════════════════════════
def leer_config(cfg=None):
    if cfg is None:
        cfg = configparser.ConfigParser()
        cfg.read("config.ini", encoding="utf-8")
    c = cfg["Cometa"] if "Cometa" in cfg else {}

    def num(clave, defecto, minimo, maximo, entero=False):
        try:
            v = float(c.get(clave, defecto))
        except (TypeError, ValueError):
            v = defecto
        v = min(max(v, minimo), maximo)
        return int(round(v)) if entero else v

    activo = str(c.get("activo", "auto")).strip().lower()
    if activo in ("true", "si", "sí", "yes", "1"):
        activo = "si"
    elif activo in ("false", "no", "0"):
        activo = "no"
    else:
        activo = "auto"
    return {
        "activo": activo,
        "radio_busqueda": num("radio_busqueda", 80, 10, 400, entero=True),
        "radio_centroide": num("radio_centroide", 8, 2, 40, entero=True),
        "suavizado": num("suavizado", 4.0, 0.5, 12.0),
        "minimo_sigma": num("minimo_sigma", 5.0, 1.0, 50.0),
        "apertura": num("apertura", 11, 3, 31, entero=True),
        "vista_previa_cada": num("vista_previa_cada", 1, 1, 20, entero=True),
    }


def es_cometa(nombre, tipo="", conf=None):
    """
    Si el objeto del GoTo es un cometa: por el tipo de la ficha ("Cometa") o por
    el nombre (C/2023 A3, 12P/Pons-Brooks...). [Cometa] activo = si/no lo fuerza.
    """
    conf = conf or leer_config()
    if conf["activo"] == "no":
        return False
    if conf["activo"] == "si":
        return True
    if "comet" in str(tipo or "").lower():
        return True
    return bool(PATRON_COMETA.match(str(nombre or "")))


def instante_medio(ruta_fits):
    """
    Instante medio de la exposición (datetime UTC sin zona): el DATE-OBS de la app
    es la hora local en que se GUARDÓ la foto (después de exponer), así que se
    resta media exposición. Si no hay cabecera, la hora de modificación del fichero.
    """
    import orientacion_camara
    from astropy.io import fits
    try:
        exptime = float(fits.getheader(ruta_fits).get("EXPTIME") or 0.0)
    except Exception:
        exptime = 0.0
    t = orientacion_camara.instante_de_la_foto(ruta_fits)
    fin = t.to_datetime() if t is not None else datetime.utcnow()
    return fin - timedelta(seconds=exptime / 2.0)


# ═════════════════════════════════════════════════════════════════════════════
# Efemérides y píxel previsto
# ═════════════════════════════════════════════════════════════════════════════
class Efemeride:
    """Posición J2000 del cometa en un instante, de las listas del MPC (sin Internet)."""

    def __init__(self, nombre, ruta_config="config.ini"):
        import cuerpos_menores
        self.nombre = nombre
        self.disponible = False
        self.lat = self.lon = self.alt = 0.0
        try:
            cfg = configparser.ConfigParser()
            cfg.read(ruta_config, encoding="utf-8")
            o = cfg["Observatorio"]
            self.lat, self.lon = float(o.get("latitud_observatorio")), float(o.get("longitud_observatorio"))
            self.alt = float(o.get("altitud_observatorio", "0") or 0)
        except Exception as e:
            log.warning("Sin coordenadas del observatorio para la efeméride del cometa: %s", e)
            return
        try:
            self.disponible = cuerpos_menores.buscar(nombre) is not None
        except Exception as e:
            log.warning("No se pudo buscar %s en las listas del MPC: %s", nombre, e)

    def radec(self, cuando):
        """(ra_grados, dec_grados) J2000 en `cuando` (datetime UTC), o None."""
        if not self.disponible:
            return None
        import cuerpos_menores
        try:
            r = cuerpos_menores.coordenadas(self.nombre, self.lat, self.lon, self.alt, cuando=cuando)
        except Exception as e:
            log.warning("Efeméride de %s: %s", self.nombre, e)
            return None
        if r is None:
            return None
        return r[0] * 15.0, r[1]


def pixel_de(wcs, ra_deg, dec_deg):
    """Píxel (x, y) de unas coordenadas J2000 por el WCS (origen 0)."""
    x, y = wcs.all_world2pix([[ra_deg, dec_deg]], 0)[0]
    return float(x), float(y)


# ═════════════════════════════════════════════════════════════════════════════
# Búsqueda del núcleo
# ═════════════════════════════════════════════════════════════════════════════
EXTENSION_MINIMA = 0.08     # (brillo tras la apertura) / (brillo fino): por debajo es una estrella
MAX_CANDIDATOS = 8


def buscar_nucleo(plano, x0, y0, radio=80, radio_centroide=8, suavizado=4.0, minimo_sigma=5.0, apertura=11):
    """
    Núcleo (condensación central) del cometa alrededor de (x0, y0) en un plano H×W.

    Las estrellas se apagan con una apertura morfológica (erosión y dilatación con
    un disco de `apertura` px: desaparece todo lo más estrecho que el disco, es
    decir, las estrellas hasta ~5 px de anchura, y queda la coma, que es extensa) y
    un suavizado gaussiano. De los máximos locales que quedan se prefiere el más
    brillante que sea EXTENSO: el brillo que sobrevive a la apertura comparado con
    el brillo fino (una estrella brillante deja un resto, pero su brillo fino es
    cien veces mayor; en una coma los dos son parecidos). La posición se afina con
    el centroide del brillo sobre el fondo en la imagen sin estrellas. Devuelve
    {"x", "y", "snr", "pico", "extenso"} o None si nada supera el fondo en
    minimo_sigma sigmas (cometa débil, nubes, núcleo fuera del recorte).
    """
    alto, ancho = plano.shape[:2]
    xa, xb = int(max(0, round(x0 - radio))), int(min(ancho, round(x0 + radio + 1)))
    ya, yb = int(max(0, round(y0 - radio))), int(min(alto, round(y0 + radio + 1)))
    if xb - xa < 2 * radio_centroide + 3 or yb - ya < 2 * radio_centroide + 3:
        return None
    recorte = np.ascontiguousarray(plano[ya:yb, xa:xb], dtype=np.float32)
    # Píxeles sin dato (relleno del warpAffine) no cuentan como fondo ni como pico
    con_dato = recorte > 0
    if con_dato.mean() < 0.5:
        return None
    k = max(3, int(apertura) | 1)
    disco = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    sin_estrellas = cv2.morphologyEx(recorte, cv2.MORPH_OPEN, disco)
    suave = cv2.GaussianBlur(sin_estrellas, (0, 0), float(suavizado))
    fino = cv2.GaussianBlur(recorte, (0, 0), 1.0)
    fondo = float(np.median(suave[con_dato]))
    ruido = 1.4826 * float(np.median(np.abs(suave[con_dato] - fondo))) + 1e-6
    suave = np.where(con_dato, suave - fondo, 0.0).astype(np.float32)
    sin_estrellas = np.where(con_dato, sin_estrellas - fondo, 0.0).astype(np.float32)
    fino = fino - fondo
    # Sin el borde del recorte, donde los filtros se mezclan con el exterior
    b = int(max(k // 2 + 1, round(2 * suavizado)))
    interior = np.zeros_like(con_dato)
    if suave.shape[0] > 2 * b + 2 and suave.shape[1] > 2 * b + 2:
        interior[b:-b, b:-b] = True
    # Máximos locales (separados al menos radio_centroide) por encima del umbral
    d = 2 * int(radio_centroide) + 1
    dilatada = cv2.dilate(suave, np.ones((d, d), np.uint8))
    es_maximo = (suave >= dilatada - 1e-6) & interior & con_dato & (suave >= minimo_sigma * ruido)
    ys, xs = np.nonzero(es_maximo)
    if len(ys) == 0:
        return None
    orden = np.argsort(suave[ys, xs])[::-1][:MAX_CANDIDATOS]
    elegido, extenso = None, False
    for i in orden:
        y, x = int(ys[i]), int(xs[i])
        if suave[y, x] / max(float(fino[y, x]), 1e-6) >= EXTENSION_MINIMA:
            elegido, extenso = (x, y), True
            break
    if elegido is None:
        # Solo restos de estrellas: mejor no medir (el seguidor usará la previsión)
        return None
    ix, iy = elegido
    pico = float(suave[iy, ix])
    # Centroide ponderado por el brillo sobre el fondo, sin estrellas (dos pasadas, recentrando)
    cx, cy = float(ix), float(iy)
    yy, xx = np.mgrid[0:suave.shape[0], 0:suave.shape[1]]
    for __ in range(2):
        dentro = ((xx - cx) ** 2 + (yy - cy) ** 2) <= radio_centroide ** 2
        pesos = np.where(dentro, np.maximum(sin_estrellas, 0.0), 0.0)
        total = float(pesos.sum())
        if total <= 0:
            break
        cx, cy = float((pesos * xx).sum() / total), float((pesos * yy).sum() / total)
    return {"x": xa + cx, "y": ya + cy, "snr": round(pico / ruido, 1), "pico": round(pico, 1), "extenso": extenso}


# ═════════════════════════════════════════════════════════════════════════════
# Seguidor: dónde está el núcleo en cada foto (coordenadas de la referencia)
# ═════════════════════════════════════════════════════════════════════════════
class SeguidorNucleo:
    """
    Lleva la cuenta de dónde cae el núcleo en cada foto, en píxeles de la
    REFERENCIA (la foto ya alineada por las estrellas). Devuelve la traslación que
    hay que sumar a la matriz de estrellas para que el núcleo quede fijo.
    """

    def __init__(self, nombre, wcs_ref, ancho, alto, conf=None, efemeride=None):
        self.nombre = nombre
        self.wcs = wcs_ref
        self.ancho, self.alto = int(ancho), int(alto)
        self.conf = conf or leer_config()
        self.efemeride = efemeride if efemeride is not None else Efemeride(nombre)
        self.nucleo_ref = None          # (x, y) del núcleo en la foto de referencia
        self.t_ref = None
        self.medidas = []               # [(t, x, y, snr)] en coordenadas de la referencia
        self.corrimiento = None         # medida − previsión de la efeméride (px), constante en la noche
        self.sin_medida = 0             # fotos seguidas en que no se ha visto el núcleo
        self.ultimo = None              # detalle de la última foto (para la web)
        try:
            from astropy.wcs.utils import proj_plane_pixel_scales
            self.escala_arcsec = float(np.mean(np.abs(proj_plane_pixel_scales(wcs_ref))) * 3600.0)
        except Exception:
            self.escala_arcsec = None

    # ── previsión ───────────────────────────────────────────────────────────
    def previsto(self, t):
        """(x, y, modo) donde debería estar el núcleo en la referencia en el instante t."""
        radec = self.efemeride.radec(t)
        if radec is not None:
            try:
                x, y = pixel_de(self.wcs, *radec)
                if np.isfinite(x) and np.isfinite(y):
                    if self.corrimiento is not None:
                        return x + self.corrimiento[0], y + self.corrimiento[1], "efemeride"
                    return x, y, "efemeride_sin_corregir"
            except Exception as e:
                log.warning("Previsión del núcleo por efeméride: %s", e)
        if len(self.medidas) >= 2:
            # Extrapolación lineal de las medidas (las últimas 20, por si el ritmo cambia)
            m = self.medidas[-20:]
            t0 = m[0][0]
            ts = np.array([(mi[0] - t0).total_seconds() for mi in m])
            xs = np.array([mi[1] for mi in m]); ys = np.array([mi[2] for mi in m])
            if ts.ptp() > 0:
                px = np.polyfit(ts, xs, 1); py = np.polyfit(ts, ys, 1)
                dt = (t - t0).total_seconds()
                return float(np.polyval(px, dt)), float(np.polyval(py, dt)), "extrapolacion"
        if self.medidas:
            return self.medidas[-1][1], self.medidas[-1][2], "ultima_medida"
        return self.ancho / 2.0, self.alto / 2.0, "centro"

    # ── medida ──────────────────────────────────────────────────────────────
    def medir(self, plano_alineado, t):
        """
        Busca el núcleo en el plano (verde) ya alineado por las estrellas, cerca de
        la previsión. Devuelve (x, y, detalle): la posición del núcleo en
        coordenadas de la referencia (medida o, si no se ve, prevista).
        """
        x0, y0, modo = self.previsto(t)
        c = self.conf
        # Con la efeméride corregida o las medidas anteriores la previsión es fiable y
        # la ventana se estrecha (así una nebulosa o una galaxia cercanas no la
        # confunden); si lleva tres fotos sin verse, se vuelve a abrir
        radio = c["radio_busqueda"]
        if modo in ("efemeride", "extrapolacion", "ultima_medida") and self.sin_medida < 3:
            radio = max(c["radio_centroide"] * 3, c["radio_busqueda"] // 3)
        m = buscar_nucleo(plano_alineado, x0, y0, radio, c["radio_centroide"], c["suavizado"],
                          c["minimo_sigma"], c["apertura"])
        detalle = {"previsto": [round(x0, 1), round(y0, 1)], "modo": modo, "radio": int(radio)}
        if m is None:
            self.sin_medida += 1
            x, y = x0, y0
            detalle.update({"medido": None, "snr": None, "usada": "prevision"})
            # Sin efeméride ni medidas no hay dónde agarrarse: no se puede apilar la foto
            if modo == "centro":
                detalle["usada"] = "ninguna"
        else:
            self.sin_medida = 0
            x, y = m["x"], m["y"]
            detalle.update({"medido": [round(x, 1), round(y, 1)], "snr": m["snr"], "usada": "medida"})
            if modo == "efemeride_sin_corregir":
                self.corrimiento = (x - x0, y - y0)
                detalle["corrimiento"] = [round(self.corrimiento[0], 1), round(self.corrimiento[1], 1)]
            elif modo == "efemeride":
                # El corrimiento se sigue afinando despacio (media móvil)
                self.corrimiento = (0.8 * self.corrimiento[0] + 0.2 * (x - (x0 - self.corrimiento[0])),
                                    0.8 * self.corrimiento[1] + 0.2 * (y - (y0 - self.corrimiento[1])))
            self.medidas.append((t, x, y, m["snr"]))
            if len(self.medidas) > 2000:
                self.medidas.pop(0)
        if self.nucleo_ref is None and detalle["usada"] != "ninguna":
            self.nucleo_ref, self.t_ref = (x, y), t
        detalle["nucleo"] = [round(x, 1), round(y, 1)]
        self.ultimo = detalle
        return x, y, detalle

    def traslacion(self, x, y):
        """Desplazamiento (dx, dy) que lleva el núcleo de esta foto (x, y) al de la referencia."""
        if self.nucleo_ref is None:
            return 0.0, 0.0
        return self.nucleo_ref[0] - x, self.nucleo_ref[1] - y

    # ── para la web ─────────────────────────────────────────────────────────
    def resumen(self):
        r = {"nombre": self.nombre, "efemeride": bool(self.efemeride.disponible),
             "nucleo_ref": [round(self.nucleo_ref[0], 1), round(self.nucleo_ref[1], 1)] if self.nucleo_ref else None,
             "medidas": len(self.medidas), "ultimo": dict(self.ultimo) if self.ultimo else None,
             "escala_arcsec": round(self.escala_arcsec, 2) if self.escala_arcsec else None,
             "corrimiento": [round(self.corrimiento[0], 1), round(self.corrimiento[1], 1)] if self.corrimiento else None}
        if len(self.medidas) >= 2:
            t0, x0, y0, __ = self.medidas[0]
            t1, x1, y1, __ = self.medidas[-1]
            minutos = (t1 - t0).total_seconds() / 60.0
            desplazamiento = float(np.hypot(x1 - x0, y1 - y0))
            r["desplazamiento_px"] = round(desplazamiento, 1)
            r["minutos"] = round(minutos, 1)
            if minutos > 0.5:
                r["velocidad_px_min"] = round(desplazamiento / minutos, 2)
                if self.escala_arcsec:
                    r["velocidad_arcsec_min"] = round(desplazamiento / minutos * self.escala_arcsec, 1)
        return r


# ═════════════════════════════════════════════════════════════════════════════
# Acumulador: media con el fondo nivelado
# ═════════════════════════════════════════════════════════════════════════════
class AcumuladorCometa:
    """
    Suma y cuenta por píxel y canal de las fotos alineadas por el núcleo. Sin
    sigma-clip: los rastros de las estrellas no se pueden rechazar foto a foto (se
    mueven poco entre fotos seguidas) y los satélites y aviones ya los descarta el
    apilado normal antes de llegar aquí. El fondo de cada foto se nivela con el de
    la media acumulada (apilado_incremental.nivel_fondo), como en el incremental.
    """

    CANALES = ("r", "g", "b")

    def __init__(self, ancho, alto):
        self.ancho, self.alto = int(ancho), int(alto)
        self.n = 0
        self.suma = {c: np.zeros((alto, ancho), dtype=np.float32) for c in self.CANALES}
        self.cuenta = np.zeros((alto, ancho), dtype=np.uint16)

    def anadir(self, r, g, b, M):
        valida = apilado_incremental.mascara_valida(M, self.ancho, self.alto)
        nivel = 0.0
        for c, plano in zip(self.CANALES, (r, g, b)):
            x = np.asarray(plano, dtype=np.float32)
            if self.n > 0:
                ref = self.suma[c] / np.maximum(self.cuenta, 1)
                mapa, __, nivel_c = apilado_incremental.nivel_fondo(x, ref, valida & (self.cuenta > 0))
                x = x - mapa
                if c == "g":
                    nivel = nivel_c
            self.suma[c] += np.where(valida, x, 0.0)
        self.cuenta += valida
        self.n += 1
        return {"nivel": float(nivel), "cobertura": float(valida.mean())}

    def imagen(self):
        return tuple(self.suma[c] / np.maximum(self.cuenta, 1) for c in self.CANALES)

    def cobertura(self):
        """(fotos por píxel, mínimo para entrar en el recorte): como el incremental."""
        return self.cuenta, max(1.0, apilado_incremental.COBERTURA_ZONA * self.n)


# ═════════════════════════════════════════════════════════════════════════════
# Composición cometa + estrellas fijas (editor de la galería)
# ═════════════════════════════════════════════════════════════════════════════
ANCHO_RASTROS = 25          # px del disco que borra los rastros de estrellas en el apilado del cometa (con sus alas)
ANCHO_ESTRELLAS = 15        # px del disco que separa las estrellas (y sus halos) del fondo en el apilado normal
SIGMAS_RASTRO = 3.0         # un píxel es rastro si sobresale de su entorno más de tantos sigmas
PLUMA = 0.5                 # fracción del radio del cometa en la que se funde la máscara


def _disco(diametro):
    k = max(3, int(diametro) | 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def _mascara_cometa(alto, ancho, nucleo, desplazamiento, radio):
    """
    Máscara suave (0..1) de la zona del cometa: un disco de `radio` alrededor del
    núcleo y, si se sabe cuánto se movió, la cápsula entre el núcleo y su posición
    final (en el apilado por estrellas el cometa queda corrido a lo largo de ella).
    """
    yy, xx = np.mgrid[0:alto, 0:ancho].astype(np.float32)
    x0, y0 = float(nucleo[0]), float(nucleo[1])
    if desplazamiento and (abs(desplazamiento[0]) > 0.5 or abs(desplazamiento[1]) > 0.5):
        dx, dy = float(desplazamiento[0]), float(desplazamiento[1])
        t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
        dist = np.hypot(xx - (x0 + t * dx), yy - (y0 + t * dy))
    else:
        dist = np.hypot(xx - x0, yy - y0)
    pluma = max(1.0, PLUMA * radio)
    return np.clip((radio + pluma - dist) / pluma, 0.0, 1.0).astype(np.float32)


def componer(cubo_estrellas, cubo_cometa, nucleo, desplazamiento=None, radio=40, progreso=None):
    """
    Cubo lineal (3, H, W) con el cometa fijo Y las estrellas fijas, a partir de
    los dos apilados de la misma sesión (mismos píxeles: el apilado por el núcleo
    es el normal con una traslación por foto).

    - Capa del cometa: el apilado por el núcleo sin los rastros de las estrellas.
      Una apertura con un disco de ANCHO_RASTROS px borra todo lo más estrecho
      que el disco (los rastros: 3-4 px de ancho en el núcleo de la estrella, pero
      con las alas de las brillantes llegan a 10-15 px; con 9 px quedaban restos
      de 600 ADU); los píxeles que sobresalen de esa versión más de SIGMAS_RASTRO
      se sustituyen por ella, salvo dentro del radio del cometa, donde se respeta
      todo (ahí está el núcleo, que también es estrecho). Una cola real es más
      ancha que el disco y se queda.
    - Capa de las estrellas: el apilado normal menos su apertura con un disco de
      ANCHO_ESTRELLAS px (queda lo pequeño: estrellas y halos; se va el cielo, las
      nebulosas y el cometa corrido), tapada en la cápsula que recorrió el cometa.
    - Resultado: capa del cometa (lleva el cielo) + capa de las estrellas.
    """
    est = np.asarray(cubo_estrellas, dtype=np.float32)
    com = np.asarray(cubo_cometa, dtype=np.float32)
    if est.shape != com.shape:
        raise ValueError(_("Los dos apilados no tienen el mismo tamaño"))
    __, alto, ancho = est.shape
    mascara = _mascara_cometa(alto, ancho, nucleo, desplazamiento, float(radio))
    salida = np.empty_like(est)
    d_rastros, d_estrellas = _disco(ANCHO_RASTROS), _disco(ANCHO_ESTRELLAS)
    for c in range(3):
        if progreso:
            progreso("componiendo", c / 3.0)
        # Capa del cometa sin rastros
        plano = com[c]
        fondo = cv2.morphologyEx(plano, cv2.MORPH_OPEN, d_rastros)
        fondo = cv2.GaussianBlur(fondo, (0, 0), 2.0)
        # La apertura va por debajo del cielo (se queda con los mínimos del ruido):
        # lo que sobra tiene un nivel propio (mediana) que no es rastro
        sobra = plano - fondo
        muestra = sobra[::4, ::4]
        nivel = float(np.median(muestra))
        ruido = 1.4826 * float(np.median(np.abs(muestra - nivel))) + 1e-6
        sobra -= nivel
        rastro = (sobra > SIGMAS_RASTRO * ruido).astype(np.uint8)
        rastro = cv2.dilate(rastro, np.ones((3, 3), np.uint8)).astype(np.float32) * (1.0 - mascara)
        capa_cometa = plano - sobra * rastro
        # Capa de las estrellas: lo pequeño del apilado normal, fuera del recorrido del
        # cometa. Solo donde hay estrella (por encima del ruido, con 2 px de margen y
        # el borde fundido): la apertura va por debajo del ruido y, sumada entera,
        # aclaraba el cielo unos 13 ADU fuera del radio del cometa (anillo oscuro)
        estrellas = est[c] - cv2.morphologyEx(est[c], cv2.MORPH_OPEN, d_estrellas)
        muestra = estrellas[::4, ::4]
        nivel_e = float(np.median(muestra))
        ruido_e = 1.4826 * float(np.median(np.abs(muestra - nivel_e))) + 1e-6
        estrellas -= nivel_e
        hay = cv2.dilate((estrellas > SIGMAS_RASTRO * ruido_e).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
        hay = cv2.GaussianBlur(hay, (0, 0), 1.0)
        np.maximum(estrellas, 0.0, out=estrellas)
        salida[c] = capa_cometa + estrellas * hay * (1.0 - mascara)
    return salida


def matriz_nucleo(M, dx, dy):
    """Matriz de estrellas (2×3, foto → referencia) más la traslación (dx, dy) del núcleo."""
    Mc = np.array(M if M is not None else np.eye(2, 3), dtype=np.float64)
    Mc[0, 2] += dx
    Mc[1, 2] += dy
    return Mc
