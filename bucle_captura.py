# bucle_captura.py
#
# Bucle de captura para el apilado, en el servidor.
#
# Antes el navegador lanzaba una petición /capturar-imagen cada pocos segundos:
# si se cerraba la pestaña o el móvil apagaba la pantalla, el apilado se paraba,
# y varias pestañas abiertas se pisaban. Ahora un hilo del servidor encadena
# las capturas; la web solo consulta el estado y muestra la imagen.
#
# El bucle cede la cámara automáticamente mientras hay otro trabajo en curso
# (darks, centrado) y se detiene solo tras varios errores seguidos.

import time
import threading
import logging
from datetime import datetime

from idioma import _

log = logging.getLogger("scopepower.bucle")


def _humor(evento, **datos):
    """Frase de humor de la voz del PC (humor.py); nunca estorba al bucle."""
    try:
        import humor
        humor.decir(evento, **datos)
    except Exception:
        pass

ERRORES_SEGUIDOS_MAX = 3


class BucleCaptura:
    def __init__(self, capturar, camara, otros_trabajos=(), dither=None):
        """
        capturar(usar_dark) -> dict con al menos {"ok": bool, "mensaje": str}
        camara            : TrabajadorCOM de la cámara (para .ocupado)
        otros_trabajos    : objetos con atributo .en_curso que tienen prioridad
                            sobre el bucle (darks, centrado)
        dither()          : mueve la montura unos píxeles al azar y espera a que
                            se asiente; devuelve una descripción. Opcional.
        """
        self._capturar = capturar
        self._camara = camara
        self._otros = list(otros_trabajos)
        self._dither = dither
        self._lock = threading.Lock()
        self._hilo = None
        self._parar = threading.Event()
        # Dithering: mover la montura unos píxeles al azar cada N fotos para que
        # los defectos fijos del sensor no caigan siempre en el mismo sitio.
        self.dithering = False
        self.dither_cada = 3
        self.dithers = 0
        self.ultimo_dither = ""
        self.desde_ultimo_dither = 0
        self._reiniciar_estado()

    def _reiniciar_estado(self):
        self.activo = False
        self.pausado = False
        self.motivo_pausa = ""   # vacío = pausa del usuario; si no, por qué se pausó solo (disco lleno)
        self.usar_dark = True
        self.capturas = 0
        self.errores = 0
        self.errores_seguidos = 0
        self.ultimo_error = ""
        self.ultimo_mensaje = ""
        self.ultima_captura = ""
        self.inicio = None
        self.esperando = ""     # motivo por el que el bucle está esperando

    # ── Control ───────────────────────────────────────────────────────
    def iniciar(self, usar_dark=True):
        with self._lock:
            if self.activo:
                self.pausado = False
                self.motivo_pausa = ""
                self.usar_dark = usar_dark
                return
            self._reiniciar_estado()
            self.activo = True
            self.usar_dark = usar_dark
            self.inicio = datetime.now()
            self._parar.clear()
            self._hilo = threading.Thread(target=self._ejecutar, name="bucle-captura", daemon=True)
            self._hilo.start()
        log.info("Bucle de captura iniciado (dark=%s)", usar_dark)
        _humor("apilado_inicio")

    def configurar_dithering(self, activado=None, cada=None):
        with self._lock:
            if activado is not None:
                self.dithering = bool(activado)
            if cada is not None:
                self.dither_cada = max(1, int(cada))
        log.info("Dithering %s (cada %d fotos)", "activado" if self.dithering else "desactivado", self.dither_cada)

    def pausar(self, motivo=""):
        with self._lock:
            self.pausado = True
            self.motivo_pausa = motivo
        log.info("Bucle de captura pausado%s", f": {motivo}" if motivo else "")

    def reanudar(self):
        with self._lock:
            self.pausado = False
            self.motivo_pausa = ""
        log.info("Bucle de captura reanudado")

    def detener(self, motivo=None):
        if motivo is None:
            motivo = _("Detenido por el usuario")
        with self._lock:
            if not self.activo:
                return
            self.activo = False
            self.ultimo_mensaje = motivo
        self._parar.set()
        log.info("Bucle de captura detenido: %s", motivo)
        _humor("apilado_fin", n=self.capturas)

    def esperar_fin(self, timeout=None):
        """Espera a que el hilo termine (tras detener): la captura en curso puede tardar
        toda la exposición salvo que se cancele antes con estado_captura.cancelar."""
        hilo = self._hilo
        if hilo is not None and hilo.is_alive():
            hilo.join(timeout)
        return hilo is None or not hilo.is_alive()

    def estado(self):
        with self._lock:
            return {
                "activo": self.activo,
                "pausado": self.pausado,
                "motivo_pausa": self.motivo_pausa,
                "usar_dark": self.usar_dark,
                "capturas": self.capturas,
                "errores": self.errores,
                "ultimo_error": self.ultimo_error,
                "mensaje": self.ultimo_mensaje,
                "ultima_captura": self.ultima_captura,
                "esperando": self.esperando,
                "inicio": self.inicio.strftime("%H:%M:%S") if self.inicio else "",
                "dithering": self.dithering,
                "dither_cada": self.dither_cada,
                "dithers": self.dithers,
                "ultimo_dither": self.ultimo_dither,
                "fotos_hasta_dither": (max(0, self.dither_cada - self.desde_ultimo_dither)
                                       if self.dithering and self._dither else None),
            }

    # ── Hilo ──────────────────────────────────────────────────────────
    def _motivo_espera(self):
        if self.pausado:
            return _("pausado")
        for t in self._otros:
            if getattr(t, "en_curso", False):
                return _("otro trabajo usa la cámara")
        if self._camara.ocupado:
            return _("cámara ocupada")
        return ""

    def _ejecutar(self):
        while not self._parar.is_set():
            motivo = self._motivo_espera()
            if motivo:
                with self._lock:
                    self.esperando = motivo
                self._parar.wait(0.5)
                continue
            with self._lock:
                self.esperando = ""

            # Dithering: antes de la captura, si toca
            if (self.dithering and self._dither is not None and self.capturas > 0
                    and self.desde_ultimo_dither >= self.dither_cada):
                with self._lock:
                    self.esperando = _("dithering (moviendo la montura)")
                try:
                    descripcion = self._dither()
                    with self._lock:
                        self.dithers += 1
                        self.ultimo_dither = descripcion or ""
                    log.info("Dither: %s", descripcion)
                except Exception as e:
                    log.warning("Dither fallido: %s", e)
                    with self._lock:
                        self.ultimo_dither = _("fallido: {e}", e=e)
                with self._lock:
                    self.desde_ultimo_dither = 0
                    self.esperando = ""
                if self._parar.is_set():
                    break

            try:
                resultado = self._capturar(self.usar_dark)
            except Exception as e:
                log.exception("Error inesperado en el bucle de captura")
                resultado = {"ok": False, "mensaje": _("Error inesperado: {e}", e=e)}

            with self._lock:
                if resultado.get("ok"):
                    self.capturas += 1
                    self.desde_ultimo_dither += 1
                    self.errores_seguidos = 0
                    self.ultimo_error = ""
                    self.ultimo_mensaje = resultado.get("mensaje", "")
                    self.ultima_captura = datetime.now().strftime("%H:%M:%S")
                elif resultado.get("cancelada"):
                    self.ultimo_mensaje = resultado.get("mensaje", _("Captura cancelada"))
                else:
                    self.errores += 1
                    self.errores_seguidos += 1
                    self.ultimo_error = resultado.get("mensaje", _("Error"))
                    if self.errores_seguidos >= ERRORES_SEGUIDOS_MAX:
                        self.activo = False
                        self.ultimo_mensaje = _("Bucle detenido tras {n} errores seguidos: {error}",
                                                n=self.errores_seguidos, error=self.ultimo_error)
                        self._parar.set()
                        log.error("Bucle detenido tras %d errores seguidos: %s", self.errores_seguidos,
                                  self.ultimo_error)
                        try:
                            import voz
                            voz.decir(_("Apilado detenido por errores seguidos de la cámara"))
                            _humor("error_camara")
                        except Exception:
                            pass

            # Pequeña pausa entre capturas: deja respirar al hilo del apilado
            self._parar.wait(0.3)

        with self._lock:
            self.activo = False
            self.esperando = ""
