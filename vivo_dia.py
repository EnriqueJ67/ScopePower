# vivo_dia.py
#
# Modo en vivo de día (pantalla Cámara): imagen casi continua del Sol, la Luna o
# un paisaje para centrar y enfocar, leyendo la cámara por su SDK nativo
# (camara_nativa.py) en vez de por ASCOM.
#
# Por ASCOM cada foto es "exponer + descargar el ImageArray por COM": el
# 27-sep-2026 el modo continuo de día sacaba una foto cada ~3,3 s aunque la
# exposición era de 0,2 ms. El SDK deja la cámara en exposición continua con el
# recorte (ROI) hecho en la propia cámara y entrega cada fotograma en memoria.
#
# Decisiones:
#   - Solo con la cámara en modo SDK (Configuración → driver "Player One por SDK
#     nativo", camara_sdk.py): la app entera usa entonces la DLL. La cámara de la
#     app se reserva (hardware.TrabajadorCOM.reservar) mientras dura, y así el
#     vídeo abre la DLL sin choques; al terminar se reconecta.
#   - La imagen del SDK es la traspuesta de la de ASCOM (verificado con la cámara
#     real el 28-sep-2026, correlación 0,999).
#   - Siempre binning 1: el binning 2 por hardware del sensor de color no está
#     verificado (puede mezclar el mosaico Bayer). La imagen se reduce a la mitad
#     con superpíxeles 2×2 (un píxel RGB por cada R, G, G, B), sin interpolar:
#     rápido y justo lo que hace falta para una vista de ~900 px.
#   - El color es el de las fotos de día (foto_dia: negro del sensor, balance con
#     el disco o el de luz de día, Sol amarillo) y la exposición automática busca
#     el mismo nivel, con la compensación ±EV de la página releída en cada foto.
#   - La imagen del SDK se gira a la orientación de las fotos de ASCOM
#     (orientacion_camara.leer_sdk; "traspuesta", verificada):
#     así se ve igual que la foto de día y "Apunta aquí" usa la misma orientación.
#   - A la tablet solo va un JPG pequeño, el último; la web pide el siguiente
#     cuando termina de cargar el anterior, así se adapta a lo que dé la wifi del
#     SynScan. Si nadie lo pide en 2 minutos (tablet cerrada), se para solo y
#     devuelve la cámara a ASCOM.

import os
import time
import logging
import threading

import numpy as np

import foto_dia
import foto_actual
import orientacion_camara

log = logging.getLogger("scopepower.vivo")

FPS_CAMARA = 5                 # FrameLimit de la cámara: no más fotogramas de los que se procesan
ANCHO_JPG = 900
CALIDAD_JPG = 72
EXPOSICION_MAXIMA_S = 0.5      # en vivo no tiene sentido esperar más por foto
CADA_BALANCE_S = 2.0           # el color se recalcula cada 2 s (sin parpadeos ni gastar CPU)
SIN_MIRAR_S = 120
BLANCO = 65535.0               # RAW16 del SDK, alineado a 16 bits como el de ASCOM
PATRONES = ("RGGB", "BGGR", "GRBG", "GBRG")


class ErrorVivo(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  Imagen
# ─────────────────────────────────────────────────────────────────────────────
def superpixel_rgb(cfa, patron, paso=1):
    """
    Mosaico Bayer (h×w) → RGB float32: un píxel por cada bloque 2×2 (G promediado),
    y con `paso` > 1 solo uno de cada `paso` bloques en cada eje (vista reducida sin coste).
    """
    f = np.asarray(cfa)
    h, w = f.shape[0] & ~1, f.shape[1] & ~1
    pos = {"RGGB": ((0, 0), (0, 1), (1, 0), (1, 1)), "BGGR": ((1, 1), (0, 1), (1, 0), (0, 0)),
           "GRBG": ((0, 1), (0, 0), (1, 1), (1, 0)), "GBRG": ((1, 0), (0, 0), (1, 1), (0, 1))}[patron]
    s = 2 * int(paso)
    r, g1, g2, b = (f[i:h:s, j:w:s].astype(np.float32) for i, j in pos)
    return np.dstack([r, (g1 + g2) * 0.5, b])


def paso_vista(lado, ancho=ANCHO_JPG):
    """
    Cuántos superpíxeles saltar para que la vista no pase de ~1,15 veces `ancho`: lo que
    sobre se lo come el JPG de todos modos y en el mini PC cada píxel procesado cuenta.
    Saturn-C: 100 % → 752 px, 75 % → 564, 50 % → 752, 35 % → 526.
    """
    import math
    return max(1, math.ceil((lado / 2.0) / (ancho * 1.15)))


def a_orientacion_ascom(img, nombre):
    """Aplica la simetría SDK → ASCOM (orientacion_camara.TRANSFORMACIONES) a una imagen 2D o RGB."""
    op = orientacion_camara.TRANSFORMACIONES[nombre]
    if img.ndim == 2:
        return np.ascontiguousarray(op(img))
    return np.ascontiguousarray(np.dstack([op(img[..., i]) for i in range(img.shape[2])]))


def revelar_vivo(cfa, patron, negro, balance, orientacion, paso=1, ev=0.0):
    """Fotograma crudo del SDK → BGR uint8 a media resolución (o menos con `paso`), en la orientación de ASCOM."""
    import cv2
    if patron in PATRONES:
        img = superpixel_rgb(cfa, patron, paso) - float(negro)
        if balance:
            img[..., 0] *= float(balance["r"])
            img[..., 2] *= float(balance["b"])
    else:
        img = np.asarray(cfa, dtype=np.float32)[::2 * paso, ::2 * paso] - float(negro)
    blanco = max(float(np.percentile(img[::2, ::2], 99.9)), 1.0) / 2.0 ** float(ev or 0.0)   # ±EV, como foto_dia.revelar
    img = np.clip(img / blanco, 0.0, 1.0)
    img = (np.power(img, 1.0 / 1.6) * 255.0).astype(np.uint8)
    img = a_orientacion_ascom(img, orientacion)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img


def jpg_web(imagen_bgr, ancho=ANCHO_JPG, calidad=CALIDAD_JPG):
    import cv2
    h, w = imagen_bgr.shape[:2]
    if w > ancho:
        imagen_bgr = cv2.resize(imagen_bgr, (ancho, int(h * ancho / w)), interpolation=cv2.INTER_AREA)
    ok, datos = cv2.imencode(".jpg", imagen_bgr, [cv2.IMWRITE_JPEG_QUALITY, int(calidad)])
    if not ok:
        raise ErrorVivo("No se pudo codificar el JPG")
    return datos.tobytes()


def nueva_exposicion(exposicion_s, cfa, nivel_objetivo, minima_s, maxima_s):
    """
    Siguiente exposición para llevar la zona más brillante (percentil 99,8) al nivel
    buscado, como foto_dia.capturar_ajustando pero amortiguada: en vivo se corrige
    un poco en cada fotograma en vez de repetir la foto. Devuelve (exposición, nivel).
    """
    muestra = np.asarray(cfa)[::3, ::3]
    nivel = float(np.percentile(muestra, 99.8)) / BLANCO
    saturados = float(np.mean(muestra >= 0.98 * BLANCO))
    if saturados >= 0.002 or nivel >= 0.97:
        factor = 0.3
    else:
        factor = (nivel_objetivo / max(nivel, 0.002)) ** 0.8
        factor = max(0.1, min(10.0, factor))
    if abs(np.log(factor)) < np.log(1.08):          # ya está cerca: no tocar (evita oscilar)
        return exposicion_s, nivel
    return max(minima_s, min(maxima_s, exposicion_s * factor)), nivel


# ─────────────────────────────────────────────────────────────────────────────
#  Trabajo
# ─────────────────────────────────────────────────────────────────────────────
class TrabajoVivo:
    def __init__(self):
        self._lock = threading.Lock()
        self._nuevo = threading.Condition(self._lock)     # avisa a quien espera la siguiente imagen
        self._parar = threading.Event()
        self._hilo = None
        self._jpg = None
        self.version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.fase = ""
        self.mensaje = ""
        self.error = ""
        self.objetivo = ""
        self.campo = 1.0
        self.fps = 0.0
        self.exposicion = None
        self.nivel = None
        self.balance = None
        self.fotogramas = 0
        self._ultimo_visto = time.time()

    def estado(self):
        with self._lock:
            return {"en_curso": self.en_curso, "terminado": self.terminado, "fase": self.fase,
                    "mensaje": self.mensaje, "error": self.error, "objetivo": self.objetivo,
                    "campo": self.campo, "fps": round(self.fps, 1), "exposicion": self.exposicion,
                    "exposicion_texto": foto_dia.formatear_exposicion(self.exposicion),
                    "nivel": self.nivel, "color": foto_dia.texto_balance(self.balance),
                    "fotogramas": self.fotogramas, "version": self.version}

    def imagen(self, despues=None, espera=3.0):
        """
        (versión, bytes del último JPG) o (versión, None); cuenta como que alguien mira.
        Con `despues`, espera hasta `espera` s a que haya una imagen más nueva que esa versión:
        la tablet no vuelve a bajar por la wifi la misma imagen.
        """
        with self._nuevo:
            self._ultimo_visto = time.time()
            if despues is not None:
                self._nuevo.wait_for(lambda: self.version > despues or not self.en_curso, timeout=espera)
            return self.version, self._jpg

    def _msg(self, texto, fase=None):
        with self._lock:
            self.mensaje = texto
            if fase:
                self.fase = fase
        log.info("En vivo: %s", texto)

    def iniciar(self, camara_ascom, objetivo, campo, compensacion=None, fabrica_camara=None):
        """
        camara_ascom: TrabajadorCOM de la app (se reserva mientras dura).
        compensacion: EV o función que lo devuelve (se relee en cada fotograma).
        fabrica_camara(): objeto con la interfaz de CamaraNativa (el simulador en pruebas).
        """
        if objetivo not in foto_dia.OBJETIVOS:
            raise ValueError(f"Objetivo desconocido: {objetivo}")
        with self._lock:
            if self.en_curso:
                raise RuntimeError("El modo en vivo ya está en marcha")
            if camara_ascom is not None and camara_ascom.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self._parar.clear()
            self._jpg = None
            self.en_curso = True
            self.fase = "preparando"
            self.objetivo = objetivo
            self.campo = max(0.2, min(1.0, float(campo)))
            self.mensaje = "Pasando la cámara al SDK..."
        self._hilo = threading.Thread(target=self._ejecutar, name="vivo-dia", daemon=True,
                                      args=(camara_ascom, compensacion, fabrica_camara))
        self._hilo.start()
        return self._hilo

    def parar(self):
        self._parar.set()

    def esperar(self, timeout=None):
        if self._hilo:
            self._hilo.join(timeout)

    # ── hilo ─────────────────────────────────────────────────────────────
    def _ejecutar(self, camara_ascom, compensacion, fabrica_camara):
        cam = None
        error = ""
        reservada = False
        try:
            if camara_ascom is not None:
                camara_ascom.reservar("el modo en vivo")
                reservada = True
            if fabrica_camara:
                cam = fabrica_camara()
            else:
                import camara_sdk
                cam = camara_sdk.nativa_por_defecto()     # la real, o la simulada en pruebas
            self._bucle(cam, compensacion)
        except Exception as e:
            error = str(e)
            log.exception("En vivo: error")
        finally:
            if cam is not None:
                try:
                    cam.cerrar()
                except Exception as e:
                    log.warning("En vivo: al cerrar la cámara: %s", e)
            if reservada:
                camara_ascom.liberar()
                self._reconectar(camara_ascom)
            foto_actual.olvidar("vivo")      # la web vuelve a la última foto de verdad
            with self._nuevo:
                self._nuevo.notify_all()
                self.en_curso = False
                self.terminado = True
                self.fase = "parado"
                self.error = error
                if error:
                    self.mensaje = f"Error: {error}"
                elif not self.mensaje.startswith("Parado"):
                    self.mensaje = "Modo en vivo terminado"

    def _reconectar(self, camara_ascom):
        for intento in range(3):
            try:
                camara_ascom.ejecutar(lambda c: c.Connected, timeout=30)
                log.info("En vivo: la cámara vuelve a la app")
                return True
            except Exception as e:
                log.warning("En vivo: no se pudo devolver la cámara a la app (%d/3): %s", intento + 1, e)
                time.sleep(2)
        return False

    def _bucle(self, cam, compensacion):
        conf = foto_dia.OBJETIVOS[self.objetivo]
        props = getattr(cam, "propiedades", None) or {}
        ancho_sensor, alto_sensor = int(props.get("ancho", 3008)), int(props.get("alto", 3008))
        patron = str(props.get("bayer", "MONO")).upper()
        orientacion = orientacion_camara.leer_sdk()["transformacion"]

        # Límites de la cámara: ganancia mínima (de día sobra luz) y exposición mínima
        ganancia, minima_s = None, conf["minima"]
        try:
            for a in cam.atributos():
                if a["nombre"] == "Gain":
                    ganancia = int(a["min"])
                elif a["nombre"] == "Exposure":
                    minima_s = max(minima_s, float(a["min"]) / 1e6)
        except Exception as e:
            log.warning("En vivo: no se pudieron leer los límites de la cámara: %s", e)
        maxima_s = min(conf["maxima"], EXPOSICION_MAXIMA_S)
        exposicion = max(minima_s, min(maxima_s, float(_ultima_exposicion(self.objetivo) or conf["inicial"])))

        roi = int(round(self.campo * min(ancho_sensor, alto_sensor)))
        geo = cam.configurar(roi_px=roi, binning=1, formato="RAW16", exposicion_ms=exposicion * 1000.0,
                             ganancia=ganancia, fps_max=FPS_CAMARA)
        lado = int(geo["lado"])
        paso = paso_vista(lado)          # campo completo: 3008 px → vista de 752 px
        # Lo que se ve, en la orientación de ASCOM (para "Apunta aquí" y guardar en la galería)
        alto_ascom, ancho_ascom = a_orientacion_ascom(np.zeros((alto_sensor, ancho_sensor), np.uint8), orientacion).shape
        campo_visto = lado / float(min(ancho_sensor, alto_sensor))
        log.info("En vivo: %s, ROI %d px (%.0f %% del campo), %s, orientación SDK→ASCOM %s, ganancia %s",
                 conf["nombre"], lado, 100 * campo_visto, patron, orientacion, ganancia)
        cam.iniciar_video()
        self._msg(f"imagen continua de {conf['nombre']}", fase="en vivo")

        negro, balance, ultimo_balance = None, None, 0.0
        saltar = 0                      # fotogramas a descartar tras cambiar la exposición
        errores_seguidos = 0
        t_fps, n_fps = time.perf_counter(), 0
        while not self._parar.is_set():
            if time.time() - self._ultimo_visto > SIN_MIRAR_S:
                self._msg(f"Parado: nadie ha mirado la imagen en {SIN_MIRAR_S // 60} minutos; la cámara vuelve a ASCOM")
                break
            try:
                cfa = np.array(cam.leer_frame(timeout_ms=int(exposicion * 1000 * 2) + 1500))
                errores_seguidos = 0
            except Exception as e:
                errores_seguidos += 1
                if errores_seguidos >= 5:
                    raise ErrorVivo(f"la cámara no entrega fotogramas ({e})")
                continue
            if saltar:
                saltar -= 1
                continue

            conf_n = foto_dia.con_compensacion(conf, compensacion)
            nueva, nivel = nueva_exposicion(exposicion, cfa, conf_n["nivel"], minima_s, maxima_s)
            if nueva != exposicion:
                exposicion = nueva
                cam.exposicion(exposicion * 1000.0)
                saltar = 1

            ahora = time.perf_counter()
            if balance is None or ahora - ultimo_balance >= CADA_BALANCE_S:
                negro = foto_dia.nivel_negro(cfa, BLANCO)
                balance = foto_dia.balance_para_patron(cfa, patron, self.objetivo, negro, BLANCO, aprender=False)
                ultimo_balance = ahora

            imagen = revelar_vivo(cfa, patron, negro, balance, orientacion, paso, ev=conf_n["ev"])
            jpg = jpg_web(imagen)
            foto_actual.registrar("vivo", ancho_ascom, alto_ascom, 1, campo_visto, imagen=imagen,
                                  objetivo=self.objetivo, exposicion=exposicion, ev=conf_n["ev"])
            n_fps += 1
            with self._nuevo:
                self._jpg = jpg
                self.version += 1
                self._nuevo.notify_all()
                self.fotogramas += 1
                self.exposicion, self.nivel, self.balance = exposicion, round(nivel, 3), balance
                if ahora - t_fps >= 2.0:
                    self.fps = n_fps / (ahora - t_fps)
                    t_fps, n_fps = ahora, 0


def _ultima_exposicion(objetivo, ruta_config="config.ini"):
    """Última exposición buena de la foto de día para este objetivo ([FotoDia] en config.ini)."""
    import configparser
    cfg = configparser.ConfigParser()
    try:
        cfg.read(ruta_config, encoding="utf-8")
        return float(cfg.get("FotoDia", f"exposicion_{objetivo}", fallback="0")) or None
    except (configparser.Error, ValueError):
        return None


trabajo_vivo = TrabajoVivo()
