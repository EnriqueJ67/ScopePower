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
import json
import time
import logging
import threading
from datetime import datetime

import numpy as np

import foto_dia
import foto_actual
import giro_camara
import orientacion_camara
import enfoque
from idioma import _, fmt_num

log = logging.getLogger("scopepower.vivo")

FPS_CAMARA = 5                 # FrameLimit de la cámara: no más fotogramas de los que se procesan
FPS_GRABACION_DEFECTO = 20     # mientras se graba un vídeo (la vista sigue a FPS_CAMARA; el resto solo se graba)
NOMBRE_VIDEO = {"sol": "Sol", "luna": "Luna", "paisaje": "Paisaje"}
ANCHO_JPG = 900
CALIDAD_JPG = 72
EXPOSICION_MAXIMA_S = 0.5      # en vivo no tiene sentido esperar más por foto
EXPOSICION_MAXIMA_ENFOQUE_S = 2.0   # con el modo enfoque (de noche, Bahtinov sobre una estrella) se permite más
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
        raise ErrorVivo(_("No se pudo codificar el JPG"))
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
        # Grabación de vídeo (grabador_video.Grabacion) pedida desde la web mientras dura el vivo
        self._peticion_grabacion = None
        self._parar_grabacion = threading.Event()
        self._grabacion = None
        self._grabacion_info = None
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.parado_sin_mirar = False
        self.error_grabacion = ""
        self.ultima_grabacion = None   # resumen de la última grabación cerrada (nombre, fotogramas, segundos...)
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
        self.enfoque = None            # medida de la estrella con el modo enfoque (enfoque.py)
        self._ultimo_visto = time.time()

    def estado(self):
        with self._lock:
            return {"en_curso": self.en_curso, "terminado": self.terminado, "fase": self.fase,
                    "mensaje": self.mensaje, "error": self.error, "objetivo": self.objetivo,
                    "campo": self.campo, "fps": round(self.fps, 1), "exposicion": self.exposicion,
                    "exposicion_texto": foto_dia.formatear_exposicion(self.exposicion),
                    "nivel": self.nivel, "color": foto_dia.texto_balance(self.balance),
                    "fotogramas": self.fotogramas, "version": self.version, "enfoque": self.enfoque,
                    "grabacion": self._estado_grabacion(), "ultima_grabacion": self.ultima_grabacion,
                    "error_grabacion": self.error_grabacion}

    def _estado_grabacion(self):
        g, info = self._grabacion, self._grabacion_info
        if g is None or info is None:
            return None
        e = g.estado()
        e.update({"segundos": round(time.time() - info["inicio"], 1), "duracion_s": info["duracion"], "fps": info["fps"]})
        return e

    # ── grabación de vídeo (se atiende desde el hilo del vivo) ─────────────
    def grabar(self, duracion_s=120, fps=FPS_GRABACION_DEFECTO, carpeta=None, extra_meta=None):
        """
        Pide grabar un SER desde el siguiente fotograma: `duracion_s` segundos (0 = hasta parar).
        `extra_meta` se añade al .json del vídeo (la ubicación y la orientación de la cámara, que
        hacen falta para corregir la rotación de campo al apilar).
        """
        with self._lock:
            if not self.en_curso or self.fase != "en vivo":
                raise RuntimeError(_("El modo en vivo no está en marcha"))
            if self._grabacion is not None or self._peticion_grabacion is not None:
                raise RuntimeError(_("Ya se está grabando un vídeo"))
            self._parar_grabacion.clear()
            self._peticion_grabacion = {"duracion": max(0.0, float(duracion_s or 0)), "fps": max(1.0, float(fps or FPS_GRABACION_DEFECTO)),
                                        "carpeta": carpeta, "extra_meta": dict(extra_meta or {})}

    def parar_grabacion(self):
        self._parar_grabacion.set()

    def _atender_grabacion(self, cam, geo, patron, orientacion, props, exposicion, ganancia, negro, balance):
        """Arranca o cierra la grabación según lo pedido; True mientras hay que encolar fotogramas."""
        with self._lock:
            peticion, self._peticion_grabacion = self._peticion_grabacion, None
        if peticion is not None and self._grabacion is None:
            self._empezar_grabacion(cam, geo, patron, orientacion, props, exposicion, ganancia, negro, balance, peticion)
        g, info = self._grabacion, self._grabacion_info
        if g is None:
            return False
        agotada = info["duracion"] > 0 and time.time() >= info["inicio"] + info["duracion"]
        if self._parar_grabacion.is_set() or agotada or not g.activa:
            self._terminar_grabacion(cam)
            return False
        return True

    def _empezar_grabacion(self, cam, geo, patron, orientacion, props, exposicion, ganancia, negro, balance, peticion):
        import grabador_video
        nombre = f"{NOMBRE_VIDEO.get(self.objetivo, 'Video')}_{datetime.now():%Y%m%d_%H%M%S}"
        carpeta = peticion["carpeta"] or grabador_video.CARPETA_VIDEOS
        bits = 16 if str(geo.get("formato", "RAW16")).upper() == "RAW16" else 8
        try:
            g = grabador_video.Grabacion(carpeta, ("ser",), ancho=geo["ancho"], alto=geo["alto"], fps=peticion["fps"],
                                         bayer=patron, bits=bits, nombre=nombre, instrumento=str(props.get("modelo") or ""))
        except Exception as e:
            with self._lock:
                self.error_grabacion = str(e)
            log.warning("En vivo: no se puede grabar: %s", e)
            return
        try:
            cam.poner("FrameLimit", int(round(peticion["fps"])))
        except Exception as e:
            log.warning("En vivo: no se pudo subir la cadencia de la cámara para grabar: %s", e)
        self._grabacion = g
        self._grabacion_info = {"nombre": nombre, "inicio": time.time(), "duracion": peticion["duracion"], "fps": peticion["fps"],
                                "ruta": g.rutas["ser"]}
        meta = {"objetivo": self.objetivo, "campo": self.campo, "exposicion_s": exposicion, "ganancia": ganancia,
                "patron": patron, "formato": geo.get("formato"), "bits": bits, "orientacion": orientacion,
                "lado": geo.get("lado"), "x0": geo.get("x0"), "y0": geo.get("y0"), "binning": geo.get("binning", 1),
                "px_um": props.get("pixel_um") or props.get("px") or 3.76, "modelo": props.get("modelo"),
                "negro": None if negro is None else float(negro), "balance": balance, "instante": time.time(),
                "fps_pedido": peticion["fps"], "duracion_pedida_s": peticion["duracion"]}
        meta.update(peticion.get("extra_meta") or {})
        self._escribir_meta(meta)
        with self._lock:
            self.error_grabacion = ""
        log.info("En vivo: grabando vídeo %s (%s, %dx%d, %d bits, %s, hasta %s fps, %s)", nombre, patron, geo["ancho"], geo["alto"],
                 bits, g.rutas["ser"], fmt_num(peticion["fps"], 0),
                 (_("{s} s", s=fmt_num(peticion["duracion"], 0)) if peticion["duracion"] else _("sin límite")))

    def _terminar_grabacion(self, cam):
        g, info = self._grabacion, self._grabacion_info
        if g is None:
            return
        self._grabacion = None
        g.cerrar()
        if cam is not None:
            try:
                cam.poner("FrameLimit", FPS_CAMARA)
            except Exception as e:
                log.warning("En vivo: no se pudo devolver la cadencia de la cámara: %s", e)
        segundos = round(time.time() - info["inicio"], 1)
        resumen = {"nombre": info["nombre"], "ruta": info["ruta"], "frames": g.frames, "descartados": g.descartados,
                   "segundos": segundos, "mb": round(g.frames * g.bytes_frame / 1e6, 1), "error": g.error}
        meta = self._leer_meta(info["ruta"])
        meta.update({"frames": g.frames, "descartados": g.descartados, "duracion_s": segundos})
        self._escribir_meta(meta, info["ruta"])
        with self._lock:
            self.ultima_grabacion = resumen
            if g.error:
                self.error_grabacion = g.error
        log.info("En vivo: vídeo %s cerrado: %d fotogramas en %s s (%d descartados)%s", info["nombre"], g.frames,
                 fmt_num(segundos, 1), g.descartados, f" · {g.error}" if g.error else "")

    def _escribir_meta(self, meta, ruta_ser=None):
        ruta = os.path.splitext(ruta_ser or self._grabacion_info["ruta"])[0] + ".json"
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
        except OSError as e:
            log.warning("En vivo: no se pudo escribir %s: %s", ruta, e)

    @staticmethod
    def _leer_meta(ruta_ser):
        try:
            with open(os.path.splitext(ruta_ser)[0] + ".json", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

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
            raise ValueError(_("Objetivo desconocido: {objetivo}", objetivo=objetivo))
        with self._lock:
            if self.en_curso:
                raise RuntimeError(_("El modo en vivo ya está en marcha"))
            if camara_ascom is not None and camara_ascom.ocupado:
                raise RuntimeError(_("La cámara está ocupada con otra captura"))
            self._reiniciar()
            self._parar.clear()
            self._jpg = None
            self.en_curso = True
            self.fase = "preparando"
            self.objetivo = objetivo
            self.campo = max(0.2, min(1.0, float(campo)))
            self.mensaje = _("Pasando la cámara al SDK...")
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
                camara_ascom.reservar(_("el modo en vivo"))
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
            try:
                self._terminar_grabacion(None)     # si se para el vivo grabando, el SER se cierra bien
            except Exception as e:
                log.warning("En vivo: al cerrar la grabación: %s", e)
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
                    self.mensaje = _("Error: {e}", e=error)
                elif not self.parado_sin_mirar:
                    self.mensaje = _("Modo en vivo terminado")

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
        if enfoque.activo() and not enfoque.es_disco(self.objetivo):
            # Enfocar de noche con la Bahtinov: hasta 2 s por fotograma y la ganancia de las fotos de noche
            # (con el Sol o la Luna la exposición y la ganancia son las de día: se mide el limbo del disco)
            maxima_s = max(maxima_s, EXPOSICION_MAXIMA_ENFOQUE_S)
            ganancia = _ganancia_noche(ganancia)
        historial_limbo = enfoque.HistorialLimbo()      # el mejor fotograma y el mejor valor de esta sesión del vivo
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
        nombre_objeto = conf['nombre']
        self._msg(_("imagen continua de {objeto}", objeto=_(nombre_objeto)), fase="en vivo")

        negro, balance, ultimo_balance = None, None, 0.0
        saltar = 0                      # fotogramas a descartar tras cambiar la exposición
        errores_seguidos = 0
        t_fps, n_fps = time.perf_counter(), 0
        ultimo_mostrado = 0.0           # grabando, la vista sigue a FPS_CAMARA y el resto de fotogramas solo se graba
        while not self._parar.is_set():
            if time.time() - self._ultimo_visto > SIN_MIRAR_S and self._grabacion is None:
                self.parado_sin_mirar = True
                self._msg(_("Parado: nadie ha mirado la imagen en {n} minutos; la cámara vuelve a ASCOM", n=SIN_MIRAR_S // 60))
                break
            try:
                cfa = np.array(cam.leer_frame(timeout_ms=int(exposicion * 1000 * 2) + 1500))
                errores_seguidos = 0
            except Exception as e:
                errores_seguidos += 1
                if errores_seguidos >= 5:
                    raise ErrorVivo(_("la cámara no entrega fotogramas ({e})", e=e))
                continue
            if saltar:
                saltar -= 1
                continue
            # Grabación: el fotograma crudo tal cual sale del SDK (el SER lleva el patrón Bayer y
            # apilado_video.py lo orienta al apilar). Mientras se graba, la exposición y el balance
            # se quedan fijos: un vídeo con la exposición cambiando a mitad no se apila bien.
            grabando = self._atender_grabacion(cam, geo, patron, orientacion, props, exposicion, ganancia, negro, balance)
            if grabando:
                self._grabacion.encolar(cfa)
                if time.perf_counter() - ultimo_mostrado < 1.0 / FPS_CAMARA:
                    continue

            conf_n = foto_dia.con_compensacion(conf, compensacion)
            modo_enfoque = enfoque.activo()
            enfoque_disco = modo_enfoque and enfoque.es_disco(self.objetivo)
            if modo_enfoque and not enfoque_disco:
                nueva, nivel = enfoque.nueva_exposicion_estrella(exposicion, cfa, minima_s, maxima_s, BLANCO)
            else:
                nueva, nivel = nueva_exposicion(exposicion, cfa, conf_n["nivel"], minima_s, maxima_s)
            if nueva != exposicion and not grabando:
                exposicion = nueva
                cam.exposicion(exposicion * 1000.0)
                saltar = 1

            ahora = time.perf_counter()
            ultimo_mostrado = ahora
            if balance is None or (ahora - ultimo_balance >= CADA_BALANCE_S and not grabando):
                negro = foto_dia.nivel_negro(cfa, BLANCO)
                balance = foto_dia.balance_para_patron(cfa, patron, self.objetivo, negro, BLANCO, aprender=False)
                ultimo_balance = ahora

            imagen = revelar_vivo(cfa, patron, negro, balance, orientacion, paso, ev=conf_n["ev"])
            info_enfoque = None
            if enfoque_disco:
                # Solo el verde, con el limbo del Sol o la Luna ampliado y su anchura (enfoque.py)
                girada, encuadre, info_enfoque = enfoque.imagen_vivo_disco(cfa, patron, paso, lambda im: a_orientacion_ascom(im, orientacion),
                                                                           giro_camara.girar, historial_limbo, objetivo=self.objetivo)
            elif modo_enfoque:
                # Solo el verde, con la estrella más brillante ampliada (enfoque.py); foto_actual sigue con la de color
                girada, encuadre, info_enfoque = enfoque.imagen_vivo(cfa, patron, paso, lambda im: a_orientacion_ascom(im, orientacion),
                                                                     giro_camara.girar)
            else:
                girada, encuadre = giro_camara.girar(imagen)      # el giro de Cámara; se anota sin girar (disco.py la mide así)
            jpg = jpg_web(girada)
            foto_actual.registrar("vivo", ancho_ascom, alto_ascom, 1, campo_visto, imagen=imagen,
                                  objetivo=self.objetivo, exposicion=exposicion, ev=conf_n["ev"], encuadre=encuadre)
            n_fps += 1
            with self._nuevo:
                self._jpg = jpg
                self.version += 1
                self._nuevo.notify_all()
                self.fotogramas += 1
                self.exposicion, self.nivel, self.balance = exposicion, round(nivel, 3), balance
                self.enfoque = info_enfoque
                if ahora - t_fps >= 2.0:
                    self.fps = n_fps / (ahora - t_fps)
                    t_fps, n_fps = ahora, 0


def _ganancia_noche(defecto, ruta_config="config.ini"):
    """Ganancia de las fotos de noche (ganancia_camara del config), para enfocar estrellas en vivo; `defecto` si no hay."""
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(ruta_config)
    for seccion in cfg.sections():
        v = cfg[seccion].get("ganancia_camara", "")
        if str(v).strip():
            try:
                return int(float(v))
            except ValueError:
                break
    return defecto


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
