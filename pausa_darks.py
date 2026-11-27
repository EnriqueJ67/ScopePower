# pausa_darks.py
#
# «Pausar y hacer darks»: parar el apilado en tiempo real, hacer darks a la temperatura que tiene
# ahora el sensor y seguir apilando.
#
# Sin refrigeración el sensor sigue al aire, y en una noche puede bajar varios grados: el dark del
# principio deja de casar con las fotos (la cabecera lo avisa, ver darks.AvisoTemperatura). Antes
# había que detener el apilado para hacer darks nuevos; ahora se pausa, se hacen los darks con la
# exposición, la ganancia y el binning de las fotos del apilado y se reanuda. El maestro nuevo va a
# la biblioteca por temperatura (darks.ruta_dark) y cada foto siguiente coge sola el dark más
# cercano a su temperatura (darks.elegir_dark, en capturar_y_guardar). Las fotos ya apiladas se
# quedan como están.
#
# Pasos (PausaDarks.fase):
#   ""                  nada en marcha
#   "esperando_foto"    el bucle está pausado, pero la foto en curso aún expone: no tapar todavía
#   "poner_tapa"        la cámara está libre; el usuario pone la tapa y lo confirma
#                       (no se puede comprobar solo, igual que en Calibración: darks y flats)
#   "darks"             haciendo los darks (darks.trabajo_darks: dark i de N)
#   "quitar_tapa"       hechos, fallidos o cancelados: el usuario quita la tapa y se reanuda
#
# Cancelar antes de tapar reanuda sin darks nuevos; cancelar durante los darks corta la toma en
# curso y pide igualmente quitar la tapa antes de seguir. Si el apilado se detiene a medias, el
# flujo termina sin reanudar nada.
#
# El módulo no importa la app: recibe el bucle, la cámara, el trabajo de darks y lo demás, para
# poder probarlo sin equipo (herramientas/probar_pausa_darks.py).

import logging
import threading
import time

from idioma import _

log = logging.getLogger("scopepower.pausa_darks")

FASES = ("", "esperando_foto", "poner_tapa", "darks", "quitar_tapa")


class PausaDarks:
    def __init__(self, bucle, camara, trabajo_darks, pausar_apilado, reanudar_apilado,
                 parametros, cancelar_captura, captura_en_curso=lambda: False, decir=None,
                 intervalo=0.5):
        """
        bucle            : BucleCaptura (activo, pausado, pausar, reanudar, parado_en_pausa)
        camara           : TrabajadorCOM de la cámara (para .ocupado; lo usa trabajo_darks.iniciar)
        trabajo_darks    : darks.TrabajoDarks
        pausar_apilado / reanudar_apilado : las de apilado.py
        parametros()     : {"exposicion", "ganancia", "binning", "numero_darks"} de las fotos del apilado
        cancelar_captura(): corta la exposición en curso (estado_captura.cancelar.set)
        captura_en_curso(): True mientras hay una exposición en marcha
        decir(texto)     : voz del PC; opcional
        """
        self._bucle = bucle
        self._camara = camara
        self._trabajo = trabajo_darks
        self._pausar_apilado = pausar_apilado
        self._reanudar_apilado = reanudar_apilado
        self._parametros = parametros
        self._cancelar_captura = cancelar_captura
        self._captura_en_curso = captura_en_curso
        self._decir = decir
        self._intervalo = intervalo
        self._lock = threading.Lock()
        self._hilo = None
        self.fase = ""
        self.n = 0
        self.parametros = {}
        self.mensaje = ""           # lo que se enseña en la web
        self.error = ""
        self.ruta = ""              # el dark maestro nuevo
        self.cancelado = False
        self.reanudado = False      # el último flujo terminó reanudando el apilado

    # ── Consultas ─────────────────────────────────────────────────────
    @property
    def activo(self):
        return self.fase != ""

    def estado(self):
        with self._lock:
            d = {"fase": self.fase, "activo": self.fase != "", "n": self.n, "mensaje": self.mensaje,
                 "error": self.error, "ruta": self.ruta, "cancelado": self.cancelado,
                 "parametros": dict(self.parametros), "completadas": 0, "total": self.n}
        if d["fase"] == "darks":
            t = self._trabajo.estado()
            d["completadas"], d["total"] = t.get("completadas", 0), t.get("total", self.n)
            d["mensaje"] = t.get("mensaje") or d["mensaje"]
        return d

    def _voz(self, texto):
        if self._decir:
            try:
                self._decir(texto)
            except Exception:
                pass

    # ── Acciones ──────────────────────────────────────────────────────
    def iniciar(self, n=None):
        """Pausa el apilado y empieza el flujo. Lanza RuntimeError si no se puede."""
        with self._lock:
            if self.fase:
                raise RuntimeError(_("Ya hay una pausa para hacer darks en marcha"))
            if not self._bucle.activo:
                raise RuntimeError(_("El apilado no está en marcha"))
            if self._trabajo.en_curso:
                raise RuntimeError(_("Ya se están generando darks"))
            p = dict(self._parametros())
            self.n = max(1, min(50, int(n if n is not None else p.get("numero_darks", 10))))
            self.parametros = p
            self.error = self.ruta = ""
            self.cancelado = self.reanudado = False
            self.fase = "esperando_foto"
            self.mensaje = _("Apilado en pausa: esperando a que termine la foto en curso")
        self._pausar_apilado()
        self._bucle.pausar(_("En pausa para hacer darks"))
        log.info("Pausa para hacer darks: %d tomas de %.2f s, ganancia %s, binning %s", self.n,
                 float(p["exposicion"]), p.get("ganancia"), p.get("binning"))
        self._hilo = threading.Thread(target=self._vigilar, name="pausa-darks", daemon=True)
        self._hilo.start()

    def tapa_puesta(self):
        """El usuario confirma que ha tapado el telescopio: empiezan los darks."""
        with self._lock:
            if self.fase != "poner_tapa":
                raise RuntimeError(_("Todavía no toca tapar el telescopio"))
            p = self.parametros
            # Lanza RuntimeError si la cámara aún está ocupada: se queda en poner_tapa
            self._trabajo.iniciar(self._camara, p["exposicion"], p["binning"], self.n, ganancia=p.get("ganancia"))
            self.fase = "darks"
            self.mensaje = _("Preparando la cámara...")
        log.info("Tapa puesta: empiezan los darks")

    def cancelar(self):
        """Antes de tapar: reanuda sin darks nuevos. Durante los darks: los corta y pide quitar la tapa."""
        with self._lock:
            fase = self.fase
            if fase == "darks":
                self.cancelado = True
        if fase in ("esperando_foto", "poner_tapa"):
            with self._lock:
                self.cancelado = True
            log.info("Pausa para hacer darks cancelada antes de tapar: se reanuda sin darks nuevos")
            self._reanudar(_("Cancelado: el apilado sigue con los darks que había"))
        elif fase == "darks":
            log.info("Pausa para hacer darks: se cancelan los darks en curso")
            self._cancelar_captura()
        elif fase == "quitar_tapa":
            self.continuar()

    def continuar(self):
        """El usuario ha quitado la tapa: se reanuda el apilado."""
        with self._lock:
            if self.fase != "quitar_tapa":
                raise RuntimeError(_("Todavía no toca quitar la tapa"))
        nuevo = bool(self.ruta) and not self.error
        self._reanudar(_("Apilado reanudado con los darks nuevos") if nuevo
                       else _("Apilado reanudado sin darks nuevos"))

    def _reanudar(self, texto):
        bucle_activo = self._bucle.activo
        with self._lock:
            self.fase = ""
            self.mensaje = texto if bucle_activo else _("El apilado se ha detenido; no hay nada que reanudar")
            self.reanudado = bucle_activo
        if bucle_activo:
            self._reanudar_apilado()
            self._bucle.reanudar()
            log.info("Pausa para hacer darks terminada: %s", texto)
            self._voz(texto)

    def _terminar_sin_reanudar(self, texto):
        with self._lock:
            self.fase = ""
            self.mensaje = texto
            self.reanudado = False
        log.info("Pausa para hacer darks terminada sin reanudar: %s", texto)

    # ── Hilo vigilante ────────────────────────────────────────────────
    def _foto_terminada(self):
        """La foto que estaba exponiendo ha acabado y el bucle está quieto en la pausa."""
        return (getattr(self._bucle, "parado_en_pausa", True) and not self._camara.ocupado
                and not self._captura_en_curso())

    def _vigilar(self):
        while True:
            time.sleep(self._intervalo)
            fase = self.fase
            if not fase:
                return
            if fase in ("esperando_foto", "poner_tapa") and not self._bucle.activo:
                self._terminar_sin_reanudar(_("El apilado se ha detenido; no se hacen darks"))
                return
            if fase == "esperando_foto" and self._foto_terminada():
                with self._lock:
                    if self.fase != "esperando_foto":
                        continue
                    self.fase = "poner_tapa"
                    self.mensaje = _("Pon la tapa del telescopio y pulsa «Tapa puesta»")
                self._voz(_("Apilado en pausa. Pon la tapa del telescopio para hacer los darks"))
            elif fase == "darks" and not self._trabajo.en_curso:
                t = self._trabajo.estado()
                with self._lock:
                    self.ruta = t.get("ruta") or ""
                    self.error = t.get("error") or ""
                    self.fase = "quitar_tapa"
                    # La voz ya la pone trabajo_darks al terminar («quita la tapa...»)
                    self.mensaje = ((self.error + ". ") if self.error else (t.get("mensaje", "") + ". ")) + \
                        _("Quita la tapa del telescopio y pulsa «Seguir apilando»")
                log.info("Pausa para hacer darks: %s", self.ruta or self.error)
            elif fase == "quitar_tapa":
                return      # ya solo espera al usuario
