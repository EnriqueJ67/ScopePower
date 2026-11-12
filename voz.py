# voz.py
#
# Avisos hablados por el altavoz del PC (estilo Seestar) usando las voces SAPI
# de Windows a través de pywin32. No requiere instalar nada.
#
# Uso desde cualquier módulo:
#     import voz
#     voz.decir("Telescopio conectado")
#     voz.decir("Empieza la exposición", categoria="capturas")
#     voz.decir("Foto añadida", categoria="bucle_apilado")
#
# Un hilo propio (con su propio contexto COM) va leyendo una cola; si la cola
# se llena, se descartan los avisos más antiguos para no acumular retraso.
# La configuración vive en la sección [Voz] de config.ini:
#     activada = true            interruptor general (también desde la web)
#     anunciar_capturas = true   avisar al empezar/terminar cada foto
#     silenciar_bucle_apilado = false   en el apilado en vivo, callar lo que se repite en cada
#                                foto (empieza/termina la exposición, foto añadida) y decir solo
#                                lo que no es rutina (mejor/peor foto, descartada, errores...)
#     describir_objeto = true    el móvil lee la descripción del objeto al llegar al GoTo
#     volumen = 100              0-100
#     velocidad = 0              -10 (lenta) .. 10 (rápida)
#     voz = Helena               parte del nombre de la voz SAPI a usar
#
# La voz va en el idioma de la app (idioma.py): si la elegida no es de ese idioma (o no hay
# ninguna elegida) se usa la primera voz instalada del idioma, y al cambiar el idioma se reajusta.
# Las voces inglesas (David, Zira) vienen con Windows; otras se añaden en Configuración de
# Windows > Hora e idioma > Voz, y herramientas/activar_voces_windows.bat las hace visibles.
#
# Si Windows deja de dar salida de audio (altavoz apagado, desconectado o sin emparejar), SAPI
# falla en cada aviso con SPERR_NOT_FOUND aunque se reinicie la app. Se sigue intentando con cada
# aviso, para volver a hablar en cuanto haya sonido; el log lo cuenta una vez al empezar y otra al
# volver, y la web (estado de la cabecera y Configuración) enseña que la voz no suena y por qué.

import queue
import threading
import time
import logging
import configparser

import idioma
from idioma import _

log = logging.getLogger("scopepower.voz")

SECCION = "Voz"
_DEFECTOS = {"activada": "true", "anunciar_capturas": "true", "silenciar_bucle_apilado": "false",
             "describir_objeto": "true", "volumen": "100", "velocidad": "0", "voz": ""}
_INTERRUPTORES = ("anunciar_capturas", "silenciar_bucle_apilado", "describir_objeto")
_MAX_COLA = 6
SPERR_NOT_FOUND = -2147200966        # 0x8004503A: SAPI no encuentra lo que necesita, en la práctica la salida de audio


def _codigo_sapi(e):
    """El código de error de SAPI dentro de una excepción COM (excepinfo[5]), o el HRESULT si no lo trae."""
    try:
        codigo = e.excepinfo[5]
        if codigo:
            return codigo
    except Exception:
        pass
    return getattr(e, "hresult", None)


def _salidas_audio():
    """Nombres de las salidas de audio que ve SAPI, o None si ni siquiera se pueden listar."""
    try:
        import win32com.client
        salidas = win32com.client.Dispatch("SAPI.SpVoice").GetAudioOutputs()
        return [salidas.Item(i).GetDescription() for i in range(salidas.Count)]
    except Exception:
        return None


def diagnosticar(e, salidas):
    """Por qué no suena la voz, en palabras: `e` es el error de Speak y `salidas` lo de _salidas_audio()."""
    if salidas == []:
        return _("Windows no tiene ninguna salida de audio: el altavoz está apagado, desconectado o sin emparejar")
    if _codigo_sapi(e) == SPERR_NOT_FOUND:
        if salidas:
            return _("Windows no encuentra la salida de audio (salidas que ve: {salidas}); revisa el altavoz",
                     salidas=", ".join(salidas))
        return _("Windows no encuentra la salida de audio; revisa el altavoz")
    return _("error de la voz de Windows: {e}", e=e)


def _leer_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    v = cfg[SECCION] if SECCION in cfg else {}
    return {
        "activada": str(v.get("activada", _DEFECTOS["activada"])).lower() == "true",
        "anunciar_capturas": str(v.get("anunciar_capturas", _DEFECTOS["anunciar_capturas"])).lower() == "true",
        "silenciar_bucle_apilado": str(v.get("silenciar_bucle_apilado",
                                             _DEFECTOS["silenciar_bucle_apilado"])).lower() == "true",
        # Lo lee el móvil (pantalla GoTo), no el altavoz del PC; vive aquí por ser un ajuste de voz más
        "describir_objeto": str(v.get("describir_objeto", _DEFECTOS["describir_objeto"])).lower() == "true",
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
        self.voz_del_idioma = True
        self.ultimo_error = ""
        self.ultimo_texto = ""
        self.voces = []
        self.fallando_desde = None   # "HH:MM" del primer aviso que no sonó, mientras no vuelva a sonar ninguno
        self.motivo_fallo = ""
        self.perdidos = 0            # avisos que no han sonado desde fallando_desde
        self._hilo = threading.Thread(target=self._bucle, name="voz", daemon=True)
        self._hilo.start()

    # ── API ───────────────────────────────────────────────────────────
    def decir(self, texto, categoria="general"):
        """Encola un aviso. Se ignora si la voz está desactivada (o la categoría).
        `categoria` puede ser un nombre o varios: ("capturas", "bucle_apilado") calla si
        cualquiera de los dos está silenciado."""
        with self._lock:
            cfg = self.config
        if not cfg["activada"]:
            return False
        categorias = {categoria} if isinstance(categoria, str) else set(categoria)
        if "capturas" in categorias and not cfg["anunciar_capturas"]:
            return False
        if "bucle_apilado" in categorias and cfg["silenciar_bucle_apilado"]:
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
        permitidas = {*_INTERRUPTORES, "volumen", "velocidad", "voz"}
        cambios = {k: v for k, v in cambios.items() if k in permitidas}
        with self._lock:
            for k, v in cambios.items():
                if k in _INTERRUPTORES:
                    self.config[k] = bool(v)
                elif k in ("volumen", "velocidad"):
                    self.config[k] = int(v)
                else:
                    self.config[k] = str(v)
            self._aplicar_ajustes = True
        _guardar_config(cambios)

    def reajustar(self):
        """Vuelve a elegir la voz en el siguiente aviso (al cambiar el idioma de la app)."""
        with self._lock:
            self._aplicar_ajustes = True

    def esperar(self, timeout=10.0):
        """Bloquea hasta que se haya dicho todo lo encolado (o pase el timeout).
        Para avisos justo antes de salir del proceso: el hilo de voz es daemon."""
        limite = time.monotonic() + timeout
        while self._cola.unfinished_tasks and time.monotonic() < limite:
            time.sleep(0.1)

    def estado(self):
        with self._lock:
            return {
                **self.config,
                "disponible": self.disponible,
                "voz_usada": self.voz_usada,
                # False si no hay ninguna voz instalada del idioma de la app (se avisa en Configuración)
                "voz_del_idioma": self.voz_del_idioma,
                "voces": list(self.voces),
                "pendientes": self._cola.qsize(),
                "ultimo_texto": self.ultimo_texto,
                "ultimo_error": self.ultimo_error,
                "fallando_desde": self.fallando_desde,
                "motivo_fallo": self.motivo_fallo,
                "perdidos": self.perdidos,
            }

    def resumen(self):
        """Lo justo para la cabecera de la web, que lo pide cada 15 s."""
        with self._lock:
            return {"activada": self.config["activada"], "fallando_desde": self.fallando_desde,
                    "motivo_fallo": self.motivo_fallo, "perdidos": self.perdidos}

    # ── Hilo SAPI ─────────────────────────────────────────────────────
    def _bucle(self):
        import pythoncom
        pythoncom.CoInitialize()
        self._sapi = None
        self._aplicar_ajustes = True
        while True:
            texto = self._cola.get()
            try:
                self._decir_con_reintento(texto)
            finally:
                self._cola.task_done()      # para esperar(): ya se ha dicho (o se ha rendido)

    def _decir_con_reintento(self, texto):
        sapi = self._sapi
        if sapi is None:
            sapi = self._sapi = self._inicializar()
            if sapi is None:
                return
        # Si SAPI falla (p. ej. SPERR_NOT_FOUND al apagar, o la salida de audio
        # que cambia), se vuelve a crear la voz y se reintenta UNA vez el mismo
        # texto, para no perder el aviso
        error = None
        for intento in (1, 2):
            try:
                if self._aplicar_ajustes:
                    self._ajustar(sapi)
                    self._aplicar_ajustes = False
                with self._lock:
                    self.ultimo_texto = texto
                sapi.Speak(texto)   # síncrono dentro de este hilo; no bloquea a nadie más
                self._ha_sonado()
                return
            except Exception as e:
                error = e
                with self._lock:
                    self.ultimo_error = str(e)
                log.debug("Error hablando «%s» (intento %d): %s", texto, intento, e)
                sapi = self._sapi = self._inicializar() if intento == 1 else None
                self._aplicar_ajustes = True
                if sapi is None:
                    break
        if error is not None:
            self._no_ha_sonado(texto, error)

    def _no_ha_sonado(self, texto, error):
        """Un aviso perdido: el primero de una racha va al log con el motivo; los siguientes solo se cuentan."""
        with self._lock:
            primero = self.fallando_desde is None
            if primero:
                self.fallando_desde = time.strftime("%H:%M")
            self.perdidos += 1
        if primero:
            motivo = diagnosticar(error, _salidas_audio())
            with self._lock:
                self.motivo_fallo = motivo
            log.warning("La voz no suena: %s (error: %s). Se perdió «%s»; se seguirá intentando con cada aviso",
                        motivo, error, texto)

    def _ha_sonado(self):
        with self._lock:
            desde, perdidos = self.fallando_desde, self.perdidos
            self.fallando_desde, self.motivo_fallo, self.perdidos = None, "", 0
        if desde is not None:
            log.info("La voz vuelve a sonar (desde las %s se perdieron %d avisos)", desde, perdidos)

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
        sufijo = idioma.lcid_sufijo()     # LCID que termina en 0A = español (40A, C0A, 80A...), 09 = inglés

        def _idioma(v):
            try:
                return str(v.GetAttribute("Language")).upper()
            except Exception:      # algunas voces no declaran idioma: SPERR_NOT_FOUND
                return ""

        def _del_idioma(v):
            # Una voz puede declarar varios LCID separados por ';'
            return any(c.strip().endswith(sufijo) for c in _idioma(v).split(";"))

        if preferida:
            elegida = next((v for v in candidatas if preferida in v.GetDescription().lower()), None)
            if elegida is not None and _idioma(elegida) and not _del_idioma(elegida):
                elegida = None      # la elegida es de otro idioma: buscar una del idioma de la app
        if elegida is None:
            elegida = next((v for v in candidatas if _del_idioma(v)), None)
        if elegida is None and preferida:
            # No hay ninguna voz del idioma instalada: mejor la elegida que la de serie de Windows
            elegida = next((v for v in candidatas if preferida in v.GetDescription().lower()), None)
        with self._lock:
            self.voz_del_idioma = elegida is not None and (not _idioma(elegida) or _del_idioma(elegida))
        if elegida is not None:
            try:
                sapi.Voice = elegida
            except Exception as e:
                log.warning("No se pudo seleccionar la voz %s: %s", elegida.GetDescription(), e)
        sapi.Volume = max(0, min(100, cfg["volumen"]))
        sapi.Rate = max(-10, min(10, cfg["velocidad"]))
        with self._lock:
            self.voz_usada = sapi.Voice.GetDescription()


locutor = Locutor()
idioma.registrar_oyente(locutor.reajustar)


def decir(texto, categoria="general"):
    return locutor.decir(texto, categoria)


def esperar(timeout=10.0):
    return locutor.esperar(timeout)
