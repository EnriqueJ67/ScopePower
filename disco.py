# disco.py
#
# El Sol o la Luna en la foto de día: dónde está su disco, sincronizar la montura
# con él y mantenerlo centrado (guiado por el disco).
#
# El problema (28-sep-2026): de día no hay estrellas con que alinear. La montura se
# inicializa en alt 0 / az 0 y se lleva al Sol a mano; como no sabe que está en el
# Sol, calcula las velocidades de seguimiento para otro punto del cielo y el disco
# deriva.
#
#   - Sincronizar: se localiza el disco en la foto que se ve y, con la orientación y
#     la escala de la cámara del último plate solving (apuntado_teorico), se calcula
#     hacia dónde apunta el centro de la foto: la posición del Sol (o de la Luna,
#     topocéntrica) en el instante de la foto menos lo que el disco esté
#     descentrado. Esa es la posición que se le da a la montura (SyncToCoordinates),
#     así que no hace falta centrar antes.
#   - Guiado: con el continuo, el modo en vivo o el timelapse en marcha, cada foto
#     nueva se mide dónde está el disco; si se ha ido más de un umbral de donde
#     estaba al activar el guiado, la montura se corrige con un movimiento corto
#     (el mismo del centrado teórico). Si se usa el pad, la referencia pasa a ser
#     la nueva posición.
#
# El disco se localiza ajustando un círculo del radio que debe tener (efemérides +
# escala de la cámara) al borde de la zona brillante, con RANSAC: en una Luna
# creciente el borde iluminado es un arco del círculo y el terminador no, así que el
# centro sale bien aunque no esté entera (el centro de la zona brillante no valdría).

import math
import time
import logging
import threading

import numpy as np

log = logging.getLogger("scopepower.disco")

RADIO_KM = {"sol": 696000.0, "luna": 1737.4}
NOMBRE = {"sol": "Sol", "luna": "Luna"}
ANCHO_ANALISIS = 800           # la imagen se analiza a unos 800 px: sobra para centrar al píxel del sensor×4
UMBRAL_DEFECTO_ARCMIN = 2.0    # se corrige cuando el disco se aparta más de esto ([FotoDia] guiado_umbral_arcmin)
UMBRAL_MINIMO_ARCMIN = 0.5     # por debajo se estaría persiguiendo el propio error del GoTo de la montura
PERDIDAS_MAXIMAS = 6           # fotos seguidas sin disco antes de dejar de guiar
PAUSA_PAD_S = 3.0


class SinDisco(Exception):
    """No se puede localizar el disco en la foto (el texto dice por qué)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Imagen de la foto que se ve
# ─────────────────────────────────────────────────────────────────────────────
def luminancia_de_vista(f, leer_fits=None):
    """
    Luminancia (float32, orientación de ASCOM, lo que se ve en la web, a ~800 px) de la
    foto registrada en foto_actual. `leer_fits()` devuelve (datos, cabecera) de
    static/captura.fits para la foto de día que solo está en disco.
    """
    import cv2
    import foto_dia
    import vivo_dia
    if f is None:
        raise SinDisco("No hay ninguna foto; haz una foto (o pon el continuo o el modo en vivo).")
    modo = f.get("modo")
    if modo == "vivo":
        img = f["imagen"]
        lum = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        return lum.astype(np.float32)
    if modo != "dia":
        raise SinDisco("Solo de día: pon el modo día y haz una foto del Sol o de la Luna.")
    frame, patron = f.get("frame"), None
    if f.get("prep"):
        patron = foto_dia._patron(f["prep"].get("sensor_type"))
    if frame is None:
        if leer_fits is None:
            raise SinDisco("La foto no está disponible.")
        frame, cab = leer_fits()
        patron = str(cab.get("BAYERPAT") or "").strip().upper()
    zona = foto_dia.recortar_centro(np.asarray(frame), f.get("campo", 1.0))
    paso = vivo_dia.paso_vista(min(zona.shape), ANCHO_ANALISIS)
    if patron in vivo_dia.PATRONES:
        return vivo_dia.superpixel_rgb(zona, patron, paso).mean(axis=2)
    return np.asarray(zona, dtype=np.float32)[::2 * paso, ::2 * paso]


def radio_esperado_arcsec(objetivo, instante, lat, lon, alt_m):
    """Radio aparente (″) del Sol o de la Luna vistos desde el observatorio en ese instante (unix)."""
    from astropy.coordinates import EarthLocation, get_body
    from astropy.time import Time
    import astropy.units as u
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    cuerpo = get_body("sun" if objetivo == "sol" else "moon", Time(instante, format="unix"), lugar)
    distancia_km = float(cuerpo.distance.to(u.km).value)
    return math.degrees(math.asin(RADIO_KM[objetivo] / distancia_km)) * 3600.0


def pixeles_por_arcsec(vista, orientacion, ancho_img):
    """Píxeles de la imagen analizada por segundo de arco."""
    import apuntado_teorico
    w_visible, _ = apuntado_teorico.geometria_visible(vista)
    escala_frame = float(orientacion["escala_arcsec"]) * float(vista.get("binning", 1) or 1) / float(orientacion.get("binning_foto", 1) or 1)
    return (ancho_img / float(w_visible)) / escala_frame


# ─────────────────────────────────────────────────────────────────────────────
#  Localizar el disco
# ─────────────────────────────────────────────────────────────────────────────
def _ajustar_radio_fijo(puntos, centro, radio, iteraciones=15):
    """Gauss-Newton: centro que minimiza Σ(|p−c| − R)² con R fijo."""
    c = np.array(centro, dtype=np.float64)
    for _ in range(iteraciones):
        d = puntos - c
        dist = np.hypot(d[:, 0], d[:, 1])
        dist = np.maximum(dist, 1e-6)
        residuo = dist - radio
        J = -d / dist[:, None]
        paso, *_ = np.linalg.lstsq(J, -residuo, rcond=None)
        c += paso
        if np.hypot(*paso) < 1e-3:
            break
    return c


def detectar_disco(lum, radio_px, semilla=0):
    """
    Centro del disco de radio `radio_px` (píxeles de `lum`). Devuelve un dict con cx, cy
    (píxeles), fx, fy (fracciones de la imagen), r, ajuste (fracción del borde que
    encaja en el círculo) y toca_borde. Lanza SinDisco si no hay un disco claro.
    """
    import cv2
    lum = np.asarray(lum, dtype=np.float32)
    h, w = lum.shape
    suave = cv2.GaussianBlur(lum, (0, 0), 1.2)
    bajo, alto = float(np.percentile(suave, 5)), float(np.percentile(suave, 99.5))
    if alto - bajo < 0.02 * max(alto, 1.0):
        raise SinDisco("No se ve ningún disco: la imagen es casi uniforme (¿nubes, tapa puesta, fuera de campo?).")
    mascara = (suave > bajo + 0.35 * (alto - bajo)).astype(np.uint8)
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(mascara, 8)
    if n <= 1:
        raise SinDisco("No se ve ningún disco brillante.")
    mayor = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = float(stats[mayor, cv2.CC_STAT_AREA])
    if area < math.pi * (0.2 * radio_px) ** 2:
        raise SinDisco("La zona brillante es demasiado pequeña para ser el disco (¿casi fuera de campo o tapado?).")
    if area > 1.15 * math.pi * radio_px ** 2 + 0.02 * h * w:
        raise SinDisco("La zona brillante es mayor que el disco esperado: ¿es un paisaje, un cielo muy claro o está quemado?")
    comp = (etiquetas == mayor).astype(np.uint8)
    contornos, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    puntos = max(contornos, key=len).reshape(-1, 2).astype(np.float64)
    # El borde de la imagen no es borde del disco
    dentro = (puntos[:, 0] > 1) & (puntos[:, 0] < w - 2) & (puntos[:, 1] > 1) & (puntos[:, 1] < h - 2)
    toca_borde = bool((~dentro).any())
    puntos = puntos[dentro]
    if len(puntos) < 30:
        raise SinDisco("Casi todo el borde del disco queda fuera de la imagen.")

    # RANSAC con el radio conocido: dos puntos del borde → los dos centros posibles
    rng = np.random.default_rng(semilla)
    tolerancia = max(1.5, 0.02 * radio_px)
    mejor, mejor_n = None, 0
    for _ in range(400):
        i, j = rng.choice(len(puntos), 2, replace=False)
        p, q = puntos[i], puntos[j]
        cuerda = np.hypot(*(q - p))
        if cuerda < 0.3 * radio_px or cuerda >= 2 * radio_px:
            continue
        medio = (p + q) / 2
        altura = math.sqrt(radio_px ** 2 - (cuerda / 2) ** 2)
        normal = np.array([-(q - p)[1], (q - p)[0]]) / cuerda
        for c in (medio + altura * normal, medio - altura * normal):
            n_in = int(np.sum(np.abs(np.hypot(*(puntos - c).T) - radio_px) < tolerancia))
            if n_in > mejor_n:
                mejor, mejor_n = c, n_in
    if mejor is None:
        raise SinDisco("No se reconoce el borde del disco.")
    inliers = puntos[np.abs(np.hypot(*(puntos - mejor).T) - radio_px) < tolerancia]
    centro = _ajustar_radio_fijo(inliers, mejor, radio_px)
    ajuste = len(inliers) / float(len(puntos))
    # Un disco entero da ~100 %; una Luna en cuarto, algo más de la mitad (el terminador no encaja)
    if ajuste < 0.3 or len(inliers) < 25:
        raise SinDisco(f"El borde no encaja con un disco de ese tamaño (solo el {100 * ajuste:.0f} %): "
                       "¿nubes delante, desenfocado o es otra cosa?")
    cx, cy = float(centro[0]), float(centro[1])
    return {"cx": cx, "cy": cy, "fx": (cx + 0.5) / w, "fy": (cy + 0.5) / h, "r": float(radio_px),
            "ajuste": round(ajuste, 3), "toca_borde": toca_borde, "ancho": w, "alto": h}


def localizar(f, orientacion, objetivo, observatorio, leer_fits=None):
    """
    Disco en la foto que se ve y su desplazamiento (du, dv) en grados desde el centro de
    la foto en el plano tangente horizontal (Δaz·cos alt, Δalt). observatorio = (lat, lon, alt_m).
    """
    import apuntado_teorico
    if objetivo not in RADIO_KM:
        raise SinDisco("Elige Sol o Luna en el modo día.")
    lum = luminancia_de_vista(f, leer_fits)
    instante = float(f.get("instante") or time.time())
    try:
        radio_as = radio_esperado_arcsec(objetivo, instante, *observatorio)
        radio_px = radio_as * pixeles_por_arcsec(f, orientacion, lum.shape[1])
    except (KeyError, TypeError, ZeroDivisionError) as e:
        raise SinDisco(f"No se puede calcular el tamaño del disco: {e}")
    disco = detectar_disco(lum, radio_px)
    du, dv = apuntado_teorico.desplazamiento(disco["fx"], disco["fy"], f, orientacion)
    disco.update({"du": du, "dv": dv, "instante": instante, "radio_arcsec": radio_as,
                  "desvio_arcmin": math.hypot(du, dv) * 60.0})
    return disco


def centro_de_la_foto(objetivo, disco, observatorio):
    """
    Coordenadas ICRS (ra_h, dec_deg) hacia las que apuntaba el centro de la foto: la
    posición del Sol/Luna (topocéntrica, en el instante de la foto) menos el
    desplazamiento del disco respecto al centro.
    """
    import apuntado_teorico
    from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
    from astropy.time import Time
    import astropy.units as u
    lat, lon, alt_m = observatorio
    t = Time(disco["instante"], format="unix")
    lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
    marco = AltAz(obstime=t, location=lugar)
    cuerpo = get_body("sun" if objetivo == "sol" else "moon", t, lugar).transform_to(marco)
    az_c, alt_c = apuntado_teorico.mover_en_horizonte(float(cuerpo.az.deg), float(cuerpo.alt.deg), -disco["du"], -disco["dv"])
    centro = SkyCoord(az=az_c * u.deg, alt=alt_c * u.deg, frame=marco).icrs
    return float(centro.ra.hour), float(centro.dec.deg), float(cuerpo.alt.deg)


# ─────────────────────────────────────────────────────────────────────────────
#  Guiado por el disco
# ─────────────────────────────────────────────────────────────────────────────
class GuiadoDisco:
    """
    Hilo que mide el disco en cada foto nueva y corrige la montura si se aparta.
    Todo se inyecta al activar (la foto, la medida, el movimiento, el pad), así se
    prueba sin montura ni cámara.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo = None
        self._reiniciar()

    def _reiniciar(self):
        self.activo = False
        self.objetivo = ""
        self.mensaje = ""
        self.referencia = None       # (du0, dv0): dónde debe estar el disco
        self.ultima = None           # última medida
        self.correcciones = 0
        self.deriva_arcmin_min = None
        self.perdidas = 0
        self.umbral_arcmin = UMBRAL_DEFECTO_ARCMIN
        self._nueva_ref_desde = 0.0   # movido a propósito: la referencia será la siguiente foto posterior

    def estado(self):
        with self._lock:
            u = dict(self.ultima) if self.ultima else None
            return {"activo": self.activo, "objetivo": self.objetivo, "mensaje": self.mensaje,
                    "correcciones": self.correcciones, "umbral_arcmin": round(self.umbral_arcmin, 2),
                    "deriva_arcmin_min": None if self.deriva_arcmin_min is None else round(self.deriva_arcmin_min, 2),
                    "desvio_arcmin": None if not u else round(u["desvio_ref"], 2), "perdidas": self.perdidas}

    def _msg(self, texto, nivel=logging.INFO):
        with self._lock:
            self.mensaje = texto
        log.log(nivel, "Guiado por el disco: %s", texto)

    def activar(self, objetivo, obtener_foto, medir, mover, pad_en_uso, umbral_arcmin=None, cada_s=2.0):
        """
        obtener_foto() → dict de foto_actual (con "instante") o None.
        medir(foto) → dict con du, dv (grados; lanza SinDisco).
        mover(du, dv) → mueve el apuntado esa cantidad (grados, plano tangente horizontal); lanza si falla.
        pad_en_uso() → True mientras se usa el pad (o hace menos de unos segundos).
        """
        with self._lock:
            if self.activo:
                raise RuntimeError("El guiado por el disco ya está activo")
            self._reiniciar()
            self._parar.clear()
            self.activo = True
            self.objetivo = objetivo
            if umbral_arcmin:
                self.umbral_arcmin = float(umbral_arcmin)
            self.mensaje = "Esperando una foto nueva..."
        self._hilo = threading.Thread(target=self._bucle, name="guiado-disco", daemon=True,
                                      args=(obtener_foto, medir, mover, pad_en_uso, cada_s))
        self._hilo.start()

    def desactivar(self):
        self._parar.set()

    def nueva_referencia(self):
        """El disco se ha movido a propósito ("Apunta aquí", centrar): tomarlo como bueno donde quede."""
        with self._lock:
            self.referencia = None
            self._nueva_ref_desde = time.time()

    def esperar(self, timeout=None):
        if self._hilo:
            self._hilo.join(timeout)

    def _bucle(self, obtener_foto, medir, mover, pad_en_uso, cada_s):
        ultimo_instante = 0.0
        no_antes_de = 0.0           # tras mover (o usar el pad) solo valen fotos posteriores
        muestras = []               # (t, du, dv) desde la última corrección, para la deriva
        try:
            while not self._parar.wait(cada_s):
                if pad_en_uso():
                    no_antes_de = time.time() + PAUSA_PAD_S
                    with self._lock:
                        self.referencia = None      # la nueva posición del disco será la referencia
                    muestras = []
                    self._msg("Pausado mientras se usa el pad")
                    continue
                with self._lock:
                    no_antes_de = max(no_antes_de, self._nueva_ref_desde)
                    if self.referencia is None:
                        muestras = []
                foto = obtener_foto()
                instante = float((foto or {}).get("instante") or 0.0)
                if not foto or instante <= ultimo_instante or instante < no_antes_de:
                    continue
                ultimo_instante = instante
                try:
                    m = medir(foto)
                except Exception as e:
                    with self._lock:
                        self.perdidas += 1
                        perdidas = self.perdidas
                    self._msg(f"No se localiza el disco ({perdidas}/{PERDIDAS_MAXIMAS}): {e}", logging.WARNING)
                    if perdidas >= PERDIDAS_MAXIMAS:
                        self._msg(f"Guiado detenido: {PERDIDAS_MAXIMAS} fotos seguidas sin disco", logging.WARNING)
                        break
                    continue
                with self._lock:
                    self.perdidas = 0
                    if self.referencia is None:
                        self.referencia = (m["du"], m["dv"])
                        muestras = []
                    du0, dv0 = self.referencia
                ddu, ddv = m["du"] - du0, m["dv"] - dv0
                desvio = math.hypot(ddu, ddv) * 60.0
                muestras.append((instante, ddu, ddv))
                if len(muestras) >= 2 and muestras[-1][0] - muestras[0][0] >= 20:
                    dt_min = (muestras[-1][0] - muestras[0][0]) / 60.0
                    deriva = math.hypot(muestras[-1][1] - muestras[0][1], muestras[-1][2] - muestras[0][2]) * 60.0 / dt_min
                    with self._lock:
                        self.deriva_arcmin_min = deriva
                umbral = self.umbral_arcmin
                with self._lock:
                    self.ultima = dict(m, desvio_ref=desvio)
                if desvio <= umbral:
                    self._msg(f"Disco en su sitio (a {desvio:.1f}′; se corrige a partir de {umbral:.1f}′)")
                    continue
                self._msg(f"Corrigiendo {desvio:.1f}′...")
                try:
                    mover(ddu, ddv)
                except Exception as e:
                    self._msg(f"No se pudo corregir: {e}", logging.WARNING)
                    no_antes_de = time.time() + 5
                    continue
                no_antes_de = time.time()
                muestras = []
                with self._lock:
                    self.correcciones += 1
                    n = self.correcciones
                self._msg(f"Corregido {desvio:.1f}′ (corrección {n})")
        finally:
            with self._lock:
                self.activo = False
                if not self.mensaje.startswith("Guiado detenido"):
                    self.mensaje = "Guiado desactivado"


guiado = GuiadoDisco()


if __name__ == "__main__":
    # Disco sintético: Sol entero y Luna en cuarto creciente, parcialmente fuera del campo
    import cv2
    h = w = 700
    yy, xx = np.mgrid[0:h, 0:w]
    r = 180.0
    for nombre, cx, cy, fase in (("Sol", 330.4, 360.7, None), ("Luna cuarto", 400.2, 300.9, 0.5), ("Luna creciente", 150.3, 350.5, 0.25), ("Luna gibosa", 350.7, 340.2, 0.85),
                                 ("Sol cortado", 640.0, 350.0, None)):
        d2 = (xx - cx) ** 2 + (yy - cy) ** 2
        img = np.full((h, w), 40.0, np.float32)
        disco_m = d2 < r ** 2
        if fase is None:
            img[disco_m] = 200.0 * (1 - 0.4 * (d2[disco_m] / r ** 2))       # oscurecimiento del limbo
        else:
            # iluminado: lado derecho, terminador elíptico
            x_rel = (xx - cx) / r
            y_rel = (yy - cy) / r
            term = np.sqrt(np.clip(1 - y_rel ** 2, 0, 1)) * (2 * fase - 1)
            ilum = disco_m & (x_rel > -term)
            img[ilum] = 180.0
        img += np.random.default_rng(1).normal(0, 3, img.shape).astype(np.float32)
        try:
            dd = detectar_disco(img, r)
            print(f"{nombre:15s}: centro ({dd['cx']:.1f}, {dd['cy']:.1f}) real ({cx}, {cy}) → error "
                  f"{math.hypot(dd['cx'] - cx, dd['cy'] - cy):.2f} px, ajuste {dd['ajuste']:.2f}, toca borde {dd['toca_borde']}")
        except SinDisco as e:
            print(f"{nombre:15s}: SinDisco: {e}")
    try:
        detectar_disco(np.full((300, 300), 50.0, np.float32), 80)
    except SinDisco as e:
        print("uniforme       : SinDisco:", e)
