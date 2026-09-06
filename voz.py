# voz.py
#
# Avisos hablados por el altavoz del PC (estilo Seestar) usando las voces SAPI
# de Windows a través de pywin32. No requiere instalar nada.
#
# Uso desde cualquier módulo:
#     import voz
#     voz.decir("Telescopio conectado")
#     voz.decir("Empieza la exposición", categoria="capturas")
#
# Un hilo propio (con su propio contexto COM) va leyendo una cola; si la cola
# se llena, se descartan los avisos más antiguos para no acumular retraso.
# La configuración vive en la sección [Voz] de config.ini:
#     activada = true            interruptor general (también desde la web)
#     anunciar_capturas = true   avisar al empezar/terminar cada foto
#     volumen = 100              0-100
#     velocidad = 0              -10 (lenta) .. 10 (rápida)
#     voz = Helena               parte del nombre de la voz SAPI a usar

import queue
import threading
import logging
import configparser

log = logging.getLogger("scopepower.voz")

SECCION = "Voz"
_DEFECTOS = {"activada": "true", "anunciar_capturas": "true", "volumen": "100", "velocidad": "0", "voz": ""}
_MAX_COLA = 6


def _leer_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    v = cfg[SECCION] if SECCION in cfg else {}
    return {
        "activada": str(v.get("activada", _DEFECTOS["activada"])).lower() == "true",
        "anunciar_capturas": str(v.get("anunciar_capturas", _DEFECTOS["anunciar_capturas"])).lower() == "true",
        "volumen": int(v.get("volumen", _DEFECTOS["volumen"])),
        "velocidad": int(v.get("velocidad", _DEFECTOS["velocidad"])),
        "voz": str(v.get("voz", _DEFECTOS["voz"])),
    }


def _guardar_config(cambios):
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if SECCION not in cfg:
        cfg[SECCION] = {}
    for k, val in cambios.items():
        cfg[SECCION][k] = str(val).lower() if isinstance(val, bool) else str(val)
    with open("config.ini", "w") as f:
        cfg.write(f)


class Locutor:
    def __init__(self):
        self._cola = queue.Queue(maxsize=_MAX_COLA)
        self._lock = threading.Lock()
        self.config = _leer_config()
        self.disponible = None       # None = aún no se ha intentado inicializar SAPI
        self.voz_usada = ""
        self.ultimo_error = ""
        self.ultimo_texto = ""
        self.voces = []
        self._hilo = threading.Thread(target=self._bucle, name="voz", daemon=True)
        self._hilo.start()

    # ── API ───────────────────────────────────────────────────────────
    def decir(self, texto, categoria="general"):
        """Encola un aviso. Se ignora si la voz está desactivada (o la categoría)."""
        with self._lock:
            cfg = self.config
        if not cfg["activada"]:
            return False
        if categoria == "capturas" and not cfg["anunciar_capturas"]:
            return False
        texto = str(texto).strip()
        if not texto:
            return False
        try:
            self._cola.put_nowait(texto)
        except queue.Full:
            try:
                self._cola.get_nowait()   # descartar el más antiguo
            except queue.Empty:
                pass
            try:
                self._cola.put_nowait(texto)
            except queue.Full:
                return False
        return True

    def activar(self, activada):
        with self._lock:
            self.config["activada"] = bool(activada)
        _guardar_config({"activada": bool(activada)})
        log.info("Voz %s", "activada" if activada else "desactivada")
        return self.config["activada"]

    def configurar(self, **cambios):
        permitidas = {"anunciar_capturas", "volumen", "velocidad", "voz"}
        cambios = {k: v for k, v in cambios.items() if k in permitidas}
        with self._lock:
            for k, v in cambios.items():
                if k == "anunciar_capturas":
                    self.config[k] = bool(v)
                elif k in ("volumen", "velocidad"):
                    self.config[k] = int(v)
                else:
                    self.config[k] = str(v)
            self._aplicar_ajustes = True
        _guardar_config(cambios)

    def estado(self):
        with self._lock:
            return {
                **self.config,
                "disponible": self.disponible,
                "voz_usada": self.voz_usada,
                "voces": list(self.voces),
                "pendientes": self._cola.qsize(),
                "ultimo_texto": self.ultimo_texto,
                "ultimo_error": self.ultimo_error,
            }

    # ── Hilo SAPI ─────────────────────────────────────────────────────
    def _bucle(self):
        import pythoncom
        pythoncom.CoInitialize()
        sapi = None
        self._aplicar_ajustes = True
        while True:
            texto = self._cola.get()
            if sapi is None:
                sapi = self._inicializar()
                if sapi is None:
                    continue
            try:
                if self._aplicar_ajustes:
                    self._ajustar(sapi)
                    self._aplicar_ajustes = False
                with self._lock:
                    self.ultimo_texto = texto
                sapi.Speak(texto)   # síncrono dentro de este hilo; no bloquea a nadie más
            except Exception as e:
                with self._lock:
                    self.ultimo_error = str(e)
                log.warning("Error hablando: %s", e)
                sapi = None   # reintentar inicialización en el siguiente aviso

    def _inicializar(self):
        try:
            import win32com.client
            sapi = win32com.client.Dispatch("SAPI.SpVoice")
            voces = sapi.GetVoices()
            with self._lock:
                self.voces = [voces.Item(i).GetDescription() for i in range(voces.Count)]
                self.disponible = True
                self.ultimo_error = ""
            return sapi
        except Exception as e:
            with self._lock:
                self.disponible = False
                self.ultimo_error = f"SAPI no disponible: {e}"
            log.error("SAPI no disponible: %s", e)
            return None

    def _ajustar(self, sapi):
        with self._lock:
            cfg = dict(self.config)
        voces = sapi.GetVoices()
        elegida = None
        preferida = cfg["voz"].strip().lower()
        candidatas = [voces.Item(i) for i in range(voces.Count)]
        if preferida:
            elegida = next((v for v in candidatas if preferida in v.GetDescription().lower()), None)
        if elegida is None:
            # Preferir una voz en español (LCID que termina en 0A: 40A es-ES, C0A es-ES, 80A es-MX...)
            elegida = next((v for v in candidatas
                            if str(v.GetAttribute("Language")).upper().endswith("0A")), None)
        if elegida is not None:
            sapi.Voice = elegida
        sapi.Volume = max(0, min(100, cfg["volumen"]))
        sapi.Rate = max(-10, min(10, cfg["velocidad"]))
        with self._lock:
            self.voz_usada = sapi.Voice.GetDescription()


locutor = Locutor()


def decir(texto, categoria="general"):
    return locutor.decir(texto, categoria)
