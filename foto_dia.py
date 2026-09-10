# -*- coding: utf-8 -*-
"""
Modo día de la Foto de prueba: exposición automática para Sol (con filtro), Luna
y paisaje, foto suelta o timelapse, y montaje del vídeo.

Funciona como los trabajos de darks y flats: se lanza en un hilo, la web sondea
`estado()` y se cancela con /cancelar-captura (foto) o /timelapse/cancelar.

La exposición se busca como en los flats, pero midiendo la zona MÁS BRILLANTE de
la imagen (percentil 99,8) en vez de la mediana: de día lo que hay que evitar es
quemar el disco del Sol o de la Luna, no dejar el fondo a un nivel dado.
"""
import os
import csv
import time
import logging
import threading
from datetime import datetime

import numpy as np

from hardware import capturar_frame, CapturaCancelada, ErrorHardware, estado_captura

log = logging.getLogger("scopepower.foto_dia")

# nivel: fracción del blanco que debe alcanzar la zona más brillante
# inicial/minima/maxima: exposiciones en segundos (la mínima real la dice la cámara)
OBJETIVOS = {
    "sol":     {"nombre": "Sol",     "nivel": 0.60, "inicial": 0.001, "minima": 0.00002, "maxima": 0.5,  "seguimiento": "solar"},
    "luna":    {"nombre": "Luna",    "nivel": 0.65, "inicial": 0.005, "minima": 0.0001,  "maxima": 2.0,  "seguimiento": "lunar"},
    "paisaje": {"nombre": "paisaje", "nivel": 0.75, "inicial": 0.005, "minima": 0.0001,  "maxima": 5.0,  "seguimiento": None},
}
MAX_INTENTOS = 6
# ASCOM DriveRates: 0 sidéreo, 1 lunar, 2 solar, 3 rey
VELOCIDADES = {"sidereo": 0, "lunar": 1, "solar": 2}
CARPETA_TIMELAPSE = os.path.join("static", "timelapse")


def formatear_exposicion(s):
    if s is None:
        return "?"
    if s < 0.001:
        return f"{s * 1e6:.0f} µs"
    if s < 1.0:
        return f"{s * 1000:.1f} ms".replace(".0 ", " ")
    return f"{s:.2f} s".replace(".00 ", " ")


def poner_seguimiento(montura, velocidad):
    """
    Cambia la velocidad de seguimiento de la montura (solar, lunar, sidéreo) si el
    driver lo permite. Devuelve True si se ha cambiado. Nunca lanza.
    """
    codigo = VELOCIDADES.get(velocidad)
    if codigo is None:
        return False

    def _fijar(tel):
        try:
            disponibles = [int(r) for r in tel.TrackingRates]
        except Exception:
            disponibles = [0]
        if codigo not in disponibles:
            raise RuntimeError(f"el driver no admite la velocidad {velocidad}")
        tel.TrackingRate = codigo
        tel.Tracking = True
        return True

    try:
        ok = montura.ejecutar(_fijar, timeout=15)
        log.info("Seguimiento de la montura: %s", velocidad)
        return bool(ok)
    except Exception as e:
        log.warning("No se pudo poner el seguimiento %s: %s", velocidad, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Piezas comunes (se ejecutan DENTRO del hilo de la cámara)
# ─────────────────────────────────────────────────────────────────────────────
def _patron(sensor_type):
    return {2: "RGGB", 3: "CMYG", 4: "CMYG2", 5: "LRGB"}.get(sensor_type)


def recortar_centro(frame, campo):
    """
    Recorta el centro del fotograma dejando la fracción `campo` del ancho y del alto
    (1.0 = campo completo). Los bordes quedan en coordenadas pares para no romper el
    patrón Bayer del mosaico CFA.
    """
    campo = float(campo)
    if campo >= 0.999:
        return frame
    campo = max(0.1, campo)
    alto, ancho = frame.shape[0], frame.shape[1]
    w = max(16, int(ancho * campo)) & ~1
    h = max(16, int(alto * campo)) & ~1
    x0 = ((ancho - w) // 2) & ~1
    y0 = ((alto - h) // 2) & ~1
    return frame[y0:y0 + h, x0:x0 + w]


def preparar_camara(cam, conf):
    """Ganancia al mínimo (de día sobra luz) y límites reales. Devuelve un dict con lo que hace falta luego."""
    ganancia_previa, ganancia = None, None
    try:
        ganancia_previa = cam.Gain
        minima_g = cam.GainMin
        if ganancia_previa != minima_g:
            cam.Gain = minima_g
        ganancia = int(cam.Gain)
    except Exception as e:
        log.info("La cámara no deja fijar la ganancia: %s", e)
    try:
        blanco = float(cam.MaxADU) or 65535.0
    except Exception:
        blanco = 65535.0
    try:
        exp_min = max(float(cam.ExposureMin), 1e-6)
    except Exception:
        exp_min = conf["minima"]
    return {"ganancia_previa": ganancia_previa, "ganancia": ganancia, "blanco": blanco,
            "minima": max(conf["minima"], exp_min), "sensor_type": cam.SensorType,
            "px": cam.PixelSizeX, "py": cam.PixelSizeY}


def restaurar_camara(cam, prep):
    if prep.get("ganancia_previa") is not None and prep["ganancia_previa"] != prep.get("ganancia"):
        try:
            cam.Gain = prep["ganancia_previa"]
        except Exception:
            pass


def capturar_ajustando(cam, conf, binning, prep, exposicion, max_intentos, informar=None, descripcion="Foto de día", campo=1.0):
    """
    Expone y corrige la exposición hasta que la zona más brillante queda cerca del
    nivel objetivo (o se agotan los intentos). Devuelve
    (frame, exposicion, nivel, saturada, intentos) con el mejor fotograma no saturado.
    La medida se hace sobre el recorte central `campo` (lo que se va a ver), el
    fotograma devuelto es el completo.
    """
    objetivo_nivel = conf["nivel"]
    blanco, minima = prep["blanco"], prep["minima"]
    exposicion = max(minima, min(conf["maxima"], float(exposicion)))
    mejor, intentos = None, []
    frame, nivel = None, 0.0
    for intento in range(1, max_intentos + 1):
        if informar:
            informar(f"{descripcion}: prueba {intento}, exponiendo {formatear_exposicion(exposicion)}")
        frame = capturar_frame(cam, exposicion, binning, descripcion=f"{descripcion} {intento}")
        zona = recortar_centro(frame, campo)
        nivel = float(np.percentile(zona, 99.8)) / blanco
        saturados = float(np.mean(zona >= 0.98 * blanco))
        intentos.append({"exposicion": exposicion, "nivel": round(nivel, 3)})
        if informar:
            informar(f"{descripcion}: prueba {intento}, {formatear_exposicion(exposicion)} → nivel {100 * nivel:.0f} %", intentos)
        distancia = abs(nivel - objetivo_nivel)
        if saturados < 0.002 and (mejor is None or distancia < mejor[0]):
            mejor = (distancia, frame, exposicion, nivel)
        if saturados < 0.002 and 0.85 * objetivo_nivel <= nivel <= 1.12 * objetivo_nivel:
            break
        if saturados >= 0.002 or nivel >= 0.98:
            factor = 0.25
        else:
            factor = objetivo_nivel / max(nivel, 0.002)
            factor = max(0.05, min(20.0, factor))
        nueva = max(minima, min(conf["maxima"], exposicion * factor))
        if abs(nueva - exposicion) / exposicion < 0.02:
            break
        exposicion = nueva
    if mejor is not None:
        _, frame, exposicion, nivel = mejor
    return frame, exposicion, nivel, mejor is None, intentos


def frame_a_imagen8(frame, sensor_type):
    """Conversión lineal (con una gamma suave), sin auto-stretch: de día no hay nada que estirar. Devuelve BGR/gris uint8."""
    import cv2
    datos = np.asarray(frame)
    patron = _patron(sensor_type)
    if datos.ndim == 2 and patron in ("RGGB", "BGGR", "GRBG", "GBRG"):
        from AutoStrechSTF import debayer_rgb
        img = debayer_rgb(datos, patron).astype(np.float32)
    else:
        img = datos.astype(np.float32)
    blanco = float(np.percentile(img, 99.9))
    negro = float(np.percentile(img, 0.1))
    img = np.clip((img - negro) / max(blanco - negro, 1.0), 0.0, 1.0)
    img = (np.power(img, 1.0 / 1.6) * 255.0).astype(np.uint8)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img


def guardar_fits(frame, exposicion, prep, binning, ruta, extra=None):
    from astropy.io import fits
    hdu = fits.PrimaryHDU(np.asarray(frame, dtype=np.uint16))
    hdu.header["EXPTIME"] = (float(exposicion), "Exposicion (s)")
    hdu.header["XBINNING"] = binning
    hdu.header["YBINNING"] = binning
    hdu.header["XPIXSZ"] = (float(prep["px"]) * binning, "Tamano de pixel con binning (um)")
    hdu.header["YPIXSZ"] = (float(prep["py"]) * binning, "Tamano de pixel con binning (um)")
    patron = _patron(prep["sensor_type"])
    if patron:
        hdu.header["BAYERPAT"] = (patron, "Patron Bayer segun ASCOM SensorType")
    hdu.header["MODO"] = ("dia", "Foto de dia con exposicion automatica")
    for k, v in (extra or {}).items():
        hdu.header[k] = v
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    hdu.writeto(ruta, overwrite=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Foto suelta
# ─────────────────────────────────────────────────────────────────────────────
class TrabajoFotoDia:
    def __init__(self):
        self._lock = threading.Lock()
        self.version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.objetivo = ""
        self.fase = ""            # ajustando | final | guardando
        self.mensaje = ""
        self.intentos = []        # [{"exposicion", "nivel"}]
        self.exposicion = None
        self.nivel = None
        self.ganancia = None
        self.saturada = False
        self.error = ""
        self.ruta_fits = ""
        self.seguimiento = None   # velocidad puesta en la montura, si alguna

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado, "objetivo": self.objetivo,
                "fase": self.fase, "mensaje": self.mensaje, "intentos": list(self.intentos),
                "exposicion": self.exposicion, "exposicion_texto": formatear_exposicion(self.exposicion),
                "nivel": self.nivel, "ganancia": self.ganancia, "saturada": self.saturada,
                "error": self.error, "version": self.version, "seguimiento": self.seguimiento,
                "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto, intentos=None, fase=None):
        with self._lock:
            self.mensaje = texto
            if fase:
                self.fase = fase
            if intentos is not None:
                self.intentos = list(intentos)
                self.exposicion, self.nivel = intentos[-1]["exposicion"], intentos[-1]["nivel"]
        log.info(texto)

    def iniciar(self, camara, objetivo, binning, exposicion_inicial=None, montura=None,
                ruta_fits=None, ruta_png=None, al_terminar=None, voz=None, campo=1.0):
        if objetivo not in OBJETIVOS:
            raise ValueError(f"Objetivo desconocido: {objetivo}")
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya hay una foto de día en curso")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self.en_curso = True
            self.objetivo = objetivo
            self.mensaje = "Preparando la cámara..."
            self.fase = "ajustando"
        hilo = threading.Thread(target=self._ejecutar, name="foto-dia", daemon=True,
                                args=(camara, objetivo, binning, exposicion_inicial, montura, ruta_fits, ruta_png, al_terminar, voz, campo))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, objetivo, binning, exposicion_inicial, montura, ruta_fits, ruta_png, al_terminar, voz, campo=1.0):
        conf = OBJETIVOS[objetivo]
        error = ""
        try:
            if montura is not None and conf["seguimiento"] and montura.esta_conectado():
                if poner_seguimiento(montura, conf["seguimiento"]):
                    with self._lock:
                        self.seguimiento = conf["seguimiento"]

            def _sesion(cam):
                prep = preparar_camara(cam, conf)
                with self._lock:
                    self.ganancia = prep["ganancia"]
                try:
                    frame, exposicion, nivel, saturada, intentos = capturar_ajustando(
                        cam, conf, binning, prep, exposicion_inicial or conf["inicial"], MAX_INTENTOS,
                        informar=lambda t, i=None: self._msg(t, i, fase="ajustando"), campo=campo)
                    return frame, exposicion, nivel, saturada, prep
                finally:
                    restaurar_camara(cam, prep)

            frame, exposicion, nivel, saturada, prep = camara.ejecutar(_sesion, timeout=180)

            self._msg("Guardando la foto...", fase="guardando")
            if ruta_fits:
                guardar_fits(frame, exposicion, prep, binning, ruta_fits)
            if ruta_png:
                import cv2
                os.makedirs(os.path.dirname(ruta_png) or ".", exist_ok=True)
                cv2.imwrite(ruta_png, frame_a_imagen8(recortar_centro(frame, campo), prep["sensor_type"]))
            with self._lock:
                self.version += 1
                self.ruta_fits = ruta_fits or ""
                self.exposicion, self.nivel, self.saturada = exposicion, round(nivel, 3), saturada
            texto = f"Foto de día: {formatear_exposicion(exposicion)}, nivel {100 * nivel:.0f} %"
            if saturada:
                texto += " (sigue saturada con la exposición mínima: hace falta filtro o menos luz)"
            self._msg(texto, fase="final")
            if voz:
                voz.decir("Foto de día terminada")
        except CapturaCancelada:
            error = "Foto cancelada"
        except ErrorHardware as e:
            error = f"Error de cámara: {e}"
        except Exception as e:
            log.exception("Error en la foto de día")
            error = f"Error: {e}"
        finally:
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.error = error
                if error:
                    self.mensaje = error
            if al_terminar:
                try:
                    al_terminar(self.estado())
                except Exception:
                    log.exception("Error en al_terminar de la foto de día")


# ─────────────────────────────────────────────────────────────────────────────
#  Timelapse
# ─────────────────────────────────────────────────────────────────────────────
class TrabajoTimelapse:
    """
    Serie de fotos de día a intervalo fijo, cada una con la exposición reajustada
    (un eclipse oscurece el disco poco a poco). Cada toma se guarda como JPG en
    static/timelapse/<fecha>_<objetivo>/ con un fotos.csv al lado. Entre tomas la
    cámara queda libre, así la web sigue respondiendo; las capturas de noche se
    bloquean mientras dure (ScopePower.py lo comprueba).
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self.version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.objetivo = ""
        self.total = 0
        self.hechas = 0
        self.intervalo = 0.0
        self.siguiente_en = 0.0
        self.carpeta = ""
        self.mensaje = ""
        self.error = ""
        self.exposicion = None
        self.nivel = None
        self.ganancia = None
        self.ultima = None        # {"archivo", "hora", "exposicion", "nivel", "n"}
        self.seguimiento = None
        self.inicio = ""
        self.video = None         # {"en_curso", "ruta", "progreso", "error"}

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado, "objetivo": self.objetivo,
                "total": self.total, "hechas": self.hechas, "intervalo": self.intervalo,
                "siguiente_en": round(max(0.0, self.siguiente_en), 1), "carpeta": self.carpeta,
                "mensaje": self.mensaje, "error": self.error, "exposicion": self.exposicion,
                "exposicion_texto": formatear_exposicion(self.exposicion), "nivel": self.nivel,
                "ganancia": self.ganancia, "ultima": dict(self.ultima) if self.ultima else None,
                "seguimiento": self.seguimiento, "inicio": self.inicio, "version": self.version,
                "video": dict(self.video) if self.video else None,
                "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto):
        with self._lock:
            self.mensaje = texto
        log.info(texto)

    def cancelar(self):
        self._cancelar.set()
        estado_captura.cancelar.set()

    def iniciar(self, camara, objetivo, binning, total, intervalo, exposicion_inicial=None,
                montura=None, al_terminar=None, voz=None, raiz=None, campo=1.0):
        if objetivo not in OBJETIVOS:
            raise ValueError(f"Objetivo desconocido: {objetivo}")
        total, intervalo = int(total), float(intervalo)
        if not (1 <= total <= 5000):
            raise ValueError("El número de fotos debe estar entre 1 y 5000")
        if not (1.0 <= intervalo <= 3600.0):
            raise ValueError("El intervalo debe estar entre 1 y 3600 segundos")
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya hay un timelapse en marcha")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self._cancelar.clear()
            self.en_curso = True
            self.objetivo, self.total, self.intervalo = objetivo, total, intervalo
            self.inicio = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.carpeta = os.path.join(raiz or CARPETA_TIMELAPSE, f"{self.inicio}_{objetivo}")
            self.mensaje = "Preparando el timelapse..."
        hilo = threading.Thread(target=self._ejecutar, name="timelapse", daemon=True,
                                args=(camara, objetivo, binning, exposicion_inicial, montura, al_terminar, voz, campo))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, objetivo, binning, exposicion_inicial, montura, al_terminar, voz, campo=1.0):
        import cv2
        conf = OBJETIVOS[objetivo]
        error = ""
        prep = None
        carpeta = self.carpeta
        try:
            os.makedirs(carpeta, exist_ok=True)
            if montura is not None and conf["seguimiento"] and montura.esta_conectado():
                if poner_seguimiento(montura, conf["seguimiento"]):
                    with self._lock:
                        self.seguimiento = conf["seguimiento"]
            prep = camara.ejecutar(lambda cam: preparar_camara(cam, conf), timeout=30)
            with self._lock:
                self.ganancia = prep["ganancia"]
            if voz:
                voz.decir(f"Timelapse de {self.total} fotos cada {self.intervalo:.0f} segundos")

            exposicion = float(exposicion_inicial or conf["inicial"])
            registro = open(os.path.join(carpeta, "fotos.csv"), "w", encoding="utf-8", newline="")
            escritor = csv.writer(registro, delimiter=";")
            escritor.writerow(["n", "hora", "exposicion_s", "nivel", "archivo"])
            try:
                for n in range(1, self.total + 1):
                    if self._cancelar.is_set():
                        raise CapturaCancelada("Timelapse cancelado")
                    t0 = time.time()
                    # Primera toma: búsqueda completa. Siguientes: la exposición anterior y como
                    # mucho dos correcciones, para no perder la cadencia
                    max_intentos = MAX_INTENTOS if n == 1 else 3
                    frame, exposicion, nivel, saturada, _ = camara.ejecutar(
                        lambda cam: capturar_ajustando(cam, conf, binning, prep, exposicion, max_intentos,
                                                       informar=lambda t, i=None: self._msg(f"Foto {n}/{self.total} · {t}"),
                                                       descripcion=f"Timelapse {n}", campo=campo),
                        timeout=180)
                    archivo = f"foto_{n:04d}.jpg"
                    cv2.imwrite(os.path.join(carpeta, archivo), frame_a_imagen8(recortar_centro(frame, campo), prep["sensor_type"]),
                                [cv2.IMWRITE_JPEG_QUALITY, 92])
                    hora = datetime.now().strftime("%H:%M:%S")
                    escritor.writerow([n, hora, f"{exposicion:.6f}", f"{nivel:.3f}", archivo])
                    registro.flush()
                    with self._lock:
                        self.hechas = n
                        self.exposicion, self.nivel = exposicion, round(nivel, 3)
                        self.ultima = {"archivo": archivo, "hora": hora, "exposicion": exposicion, "nivel": round(nivel, 3), "n": n}
                        self.version += 1
                    self._msg(f"Foto {n}/{self.total}: {formatear_exposicion(exposicion)}, nivel {100 * nivel:.0f} %"
                              + (" (saturada)" if saturada else ""))
                    if n == self.total:
                        break
                    # Esperar al siguiente disparo (contando desde el inicio de este)
                    while True:
                        restante = self.intervalo - (time.time() - t0)
                        with self._lock:
                            self.siguiente_en = restante
                        if restante <= 0:
                            break
                        if self._cancelar.wait(timeout=min(0.5, restante)):
                            raise CapturaCancelada("Timelapse cancelado")
            finally:
                registro.close()
            self._msg(f"Timelapse terminado: {self.hechas} fotos en {os.path.basename(carpeta)}")
            if voz:
                voz.decir("Timelapse terminado")
        except CapturaCancelada:
            error = f"Timelapse cancelado tras {self.hechas} fotos"
        except ErrorHardware as e:
            error = f"Error de cámara: {e}"
        except Exception as e:
            log.exception("Error en el timelapse")
            error = f"Error: {e}"
        finally:
            if prep is not None:
                try:
                    camara.ejecutar(lambda cam: restaurar_camara(cam, prep), timeout=30)
                except Exception:
                    pass
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.error = error
                self.siguiente_en = 0.0
                if error:
                    self.mensaje = error
            if al_terminar:
                try:
                    al_terminar(self.estado())
                except Exception:
                    log.exception("Error en al_terminar del timelapse")

    # ── vídeo ──────────────────────────────────────────────────────────
    def montar_video(self, carpeta, fps=25, ancho=1920):
        """Junta los JPG de `carpeta` en un MP4 (en un hilo). El estado va en estado()["video"]."""
        with self._lock:
            if self.video and self.video.get("en_curso"):
                raise RuntimeError("Ya se está montando un vídeo")
            self.video = {"en_curso": True, "ruta": "", "progreso": 0, "error": "", "carpeta": carpeta}
        threading.Thread(target=self._montar, name="timelapse-video", daemon=True, args=(carpeta, int(fps), int(ancho))).start()

    def _montar(self, carpeta, fps, ancho):
        import cv2
        error, ruta = "", ""
        try:
            fotos = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".jpg"))
            if len(fotos) < 2:
                raise RuntimeError("Hacen falta al menos dos fotos para montar un vídeo")
            primera = cv2.imread(os.path.join(carpeta, fotos[0]))
            alto = int(primera.shape[0] * ancho / primera.shape[1])
            alto -= alto % 2
            ruta = os.path.join(carpeta, "timelapse.mp4")
            salida = cv2.VideoWriter(ruta, cv2.VideoWriter_fourcc(*"mp4v"), fps, (ancho, alto))
            if not salida.isOpened():
                raise RuntimeError("No se pudo crear el fichero de vídeo")
            for i, f in enumerate(fotos, 1):
                img = cv2.imread(os.path.join(carpeta, f))
                if img is None:
                    continue
                salida.write(cv2.resize(img, (ancho, alto), interpolation=cv2.INTER_AREA))
                with self._lock:
                    self.video["progreso"] = int(100 * i / len(fotos))
            salida.release()
            log.info("Vídeo montado: %s (%d fotos, %d fps)", ruta, len(fotos), fps)
        except Exception as e:
            log.exception("Error montando el vídeo del timelapse")
            error = str(e)
        finally:
            with self._lock:
                self.video.update({"en_curso": False, "ruta": ruta if not error else "", "error": error, "progreso": 100 if not error else 0})


def listar_timelapses(raiz=None, maximo=10):
    """Carpetas de timelapse con su número de fotos y si ya tienen vídeo, las más recientes primero."""
    raiz = raiz or CARPETA_TIMELAPSE
    salida = []
    if not os.path.isdir(raiz):
        return salida
    for nombre in sorted(os.listdir(raiz), reverse=True)[:maximo]:
        ruta = os.path.join(raiz, nombre)
        if not os.path.isdir(ruta):
            continue
        fotos = sorted(f for f in os.listdir(ruta) if f.lower().endswith(".jpg"))
        salida.append({"carpeta": nombre, "fotos": len(fotos), "primera": fotos[0] if fotos else None,
                       "ultima": fotos[-1] if fotos else None,
                       "video": os.path.isfile(os.path.join(ruta, "timelapse.mp4"))})
    return salida


trabajo_foto_dia = TrabajoFotoDia()
trabajo_timelapse = TrabajoTimelapse()
