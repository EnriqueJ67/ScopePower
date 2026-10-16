# humor.py
#
# Nivel de humor de la voz del PC, al estilo del robot TARS de Interstellar. Según un
# porcentaje (0 = serio, 100 = todas las frases, siempre) el PC añade, detrás del aviso
# serio de un evento, una frase sarcástica o de película sacada de frases_humor.csv.
#
# Uso desde cualquier módulo (siempre detrás del voz.decir serio, para que la frase
# salga después en la misma cola):
#     import humor
#     voz.decir("Foto descartada por calidad")
#     humor.decir("foto_descartada")
#     humor.decir("apilado_fin", n=total)       # {n} se sustituye en la frase
#
# El porcentaje manda dos veces: solo salen las frases con nivel <= porcentaje, y el
# porcentaje es también la probabilidad de que un evento suelte frase. Entre frase y
# frase hay una pausa mínima (PAUSA_S) y no se repiten las últimas SIN_REPETIR.
#
# frases_humor.csv (UTF-8, separador ;, líneas # de comentario): evento;humor;frase
#   evento  = momento de la app (ver GRUPO_DE_EVENTO) o grupo genérico de reserva
#             (proceso, error, exito, tars) que se usa si el evento no tiene frases propias
#   humor   = 0-100, nivel mínimo para que la frase pueda salir
#   frase   = texto; admite {nivel} (porcentaje actual) y {n} (dato del evento)
# Se recarga solo cuando cambia la fecha del fichero: se puede editar sin reiniciar.
# Con la app en inglés (idioma.py) se usa frases_humor_en.csv, con las mismas columnas y
# eventos; si no existe, el de español.
#
# Configuración en config.ini (la guarda ScopePower.py al cambiarla desde la web; aquí solo se lee):
#     [Humor] activado = false   el humor y su botón de la barra están escondidos hasta que el
#                                usuario marca «Comentarios de a bordo» en Avisos por voz
#     [Humor] nivel = 50         porcentaje con el que arranca al habilitarlo

import configparser
import csv
import logging
import os
import random
import threading
import time
from collections import deque

import idioma
from idioma import _, N_

log = logging.getLogger("scopepower.humor")

RUTA_FRASES = "frases_humor.csv"
SECCION = "Humor"
NIVEL_POR_DEFECTO = 50
ACTIVADO_POR_DEFECTO = False
PAUSA_S = 20          # segundos mínimos entre dos frases de humor
SIN_REPETIR = 8       # últimas frases que no se vuelven a decir mientras haya otras

GRUPOS = ("proceso", "error", "exito", "tars")

# Evento → grupo genérico de reserva cuando el evento no tiene frases propias al nivel actual
GRUPO_DE_EVENTO = {
    "arranque": "tars",
    "conexion_ok": "exito",
    "conexion_fallo": "error",
    "goto_inicio": "proceso",
    "goto_rechazado": "error",
    "goto_bajo_horizonte": "error",
    "objeto_no_encontrado": "error",
    "goto_llegada": "exito",
    "centrado_ok": "exito",
    "centrado_fallo": "error",
    "sincronizado": "exito",
    "foto_guardada": "exito",
    "foto_descartada": "tars",
    "foto_sustituye": "exito",
    "foto_mejor": "exito",
    "foto_peor": "tars",
    "referencia_fijada": "proceso",
    "apilado_inicio": "proceso",
    "apilado_fin": "exito",
    "error_camara": "error",
    "cancelado": "tars",
    "disco_bajo": "error",
    "configuracion_guardada": "tars",
    "apagado": "tars",
    "nivel_humor": None,      # solo frases propias: la confirmación al cambiar el nivel
}

CONFIRMACION_POR_DEFECTO = N_("Humor ajustado al {nivel} por ciento.")
DESPEDIDA_HUMOR_CERO = N_("Humor desactivado. Volvemos al modo estrictamente profesional.")


class _ConHuecos(dict):
    """Para str.format_map: un {marcador} desconocido se deja tal cual en vez de fallar."""
    def __missing__(self, clave):
        return "{" + clave + "}"


class Humorista:
    def __init__(self, ruta=None):
        self._ruta_fija = ruta       # None = la del idioma de la app (frases_humor[_en].csv)
        self._ruta_cargada = None
        self._lock = threading.Lock()
        self._nivel = NIVEL_POR_DEFECTO
        self._activado = ACTIVADO_POR_DEFECTO
        self._frases = []            # [(evento, humor, frase)]
        self._mtime = None
        self._errores_csv = 0
        self._recientes = deque(maxlen=SIN_REPETIR)
        self._ultima_vez = 0.0       # time.monotonic() de la última frase dicha
        self._voz = None             # módulo voz (o un doble en las pruebas)
        self.ultima_frase = ""

    @property
    def ruta(self):
        return self._ruta_fija or idioma.ruta_variante(RUTA_FRASES)

    def cambiar_idioma(self):
        """Al cambiar el idioma de la app: la próxima frase ya sale del fichero del idioma nuevo."""
        with self._lock:
            self._mtime = None
            self._recientes.clear()

    # ── Configuración ─────────────────────────────────────────────────
    def cargar_nivel(self, config=None):
        """Lee [Humor] activado y nivel de config.ini (o del ConfigParser dado)."""
        cfg = config
        if cfg is None:
            cfg = configparser.ConfigParser()
            cfg.read("config.ini")
        try:
            nivel = int(cfg.get(SECCION, "nivel", fallback=NIVEL_POR_DEFECTO))
        except (ValueError, TypeError):
            log.warning("[%s] nivel no es un número; se usa %d", SECCION, NIVEL_POR_DEFECTO)
            nivel = NIVEL_POR_DEFECTO
        activado = str(cfg.get(SECCION, "activado", fallback=ACTIVADO_POR_DEFECTO)).strip().lower() == "true"
        with self._lock:
            self._nivel = max(0, min(100, nivel))
            self._activado = activado
        return self._nivel

    @property
    def nivel(self):
        with self._lock:
            return self._nivel

    @property
    def activado(self):
        with self._lock:
            return self._activado

    def fijar_activado(self, activado):
        """Habilita o esconde el humor (y su botón de la barra). Guardar en config.ini es
        cosa de quien llama."""
        with self._lock:
            self._activado = bool(activado)
        log.info("Humor %s", "habilitado" if activado else "escondido")
        return self._activado

    def fijar_nivel(self, nivel):
        """Cambia el nivel y lo confirma por voz (siempre, sin frecuencia ni pausa).
        Devuelve la frase dicha. Guardar en config.ini es cosa de quien llama."""
        nivel = max(0, min(100, int(nivel)))
        with self._lock:
            self._nivel = nivel
        if nivel == 0:
            frase = _(DESPEDIDA_HUMOR_CERO)
        else:
            frase = self._elegir_entre(self._candidatas("nivel_humor", nivel)) or _(CONFIRMACION_POR_DEFECTO)
            frase = self._formatear(frase, {})
        self._hablar(frase, "nivel_humor")
        log.info("Nivel de humor: %d %%", nivel)
        return frase

    # ── Frases ────────────────────────────────────────────────────────
    def _recargar_si_cambio(self):
        ruta = self.ruta
        try:
            mtime = os.path.getmtime(ruta)
        except OSError:
            if self._frases or self._mtime is not None:
                log.warning("No se encuentra %s: sin frases de humor", ruta)
            self._frases, self._mtime = [], None
            return
        if mtime == self._mtime and ruta == self._ruta_cargada:
            return
        frases, errores = [], 0
        try:
            with open(ruta, encoding="utf-8-sig", newline="") as f:
                for num, fila in enumerate(csv.reader(_sin_comentarios(f), delimiter=";", quotechar='"'), start=1):
                    if not fila or not any(c.strip() for c in fila):
                        continue
                    if len(fila) < 3:
                        errores += 1
                        log.warning("%s: fila con %d columnas en vez de 3 (se omite): %s", ruta, len(fila), fila)
                        continue
                    evento, humor, frase = fila[0].strip().lower(), fila[1].strip(), ";".join(fila[2:]).strip()
                    if evento == "evento" and not humor.isdigit():
                        continue      # cabecera
                    try:
                        humor = int(humor)
                    except ValueError:
                        errores += 1
                        log.warning("%s: nivel «%s» no es un número (se omite la fila de «%s»)", ruta, humor, evento)
                        continue
                    if not frase:
                        continue
                    if evento not in GRUPO_DE_EVENTO and evento not in GRUPOS:
                        log.warning("%s: evento desconocido «%s» (la frase solo saldrá si algún módulo lo pide)", ruta, evento)
                    frases.append((evento, max(0, min(100, humor)), frase))
        except Exception as e:
            log.warning("No se pudo leer %s: %s", ruta, e)
            return
        self._frases, self._mtime, self._errores_csv = frases, mtime, errores
        self._ruta_cargada = ruta
        log.info("Frases de humor cargadas de %s: %d (%d filas con errores)", os.path.basename(ruta), len(frases), errores)

    def _candidatas(self, evento, nivel, con_reserva=True):
        """Frases del evento con humor <= nivel; si no hay, las del grupo de reserva."""
        self._recargar_si_cambio()
        propias = [f for ev, h, f in self._frases if ev == evento and h <= nivel]
        if propias or not con_reserva:
            return propias
        grupo = GRUPO_DE_EVENTO.get(evento)
        if grupo is None:
            return []
        return [f for ev, h, f in self._frases if ev == grupo and h <= nivel]

    def _elegir_entre(self, candidatas):
        if not candidatas:
            return None
        with self._lock:
            frescas = [f for f in candidatas if f not in self._recientes]
        return random.choice(frescas or candidatas)

    def _formatear(self, frase, datos):
        valores = _ConHuecos(datos)
        valores.setdefault("nivel", self.nivel)
        try:
            return frase.format_map(valores)
        except (ValueError, IndexError):
            return frase          # llaves mal puestas en el CSV: se dice tal cual

    def elegir(self, evento, **datos):
        """Frase para el evento o None (humor escondido, nivel 0, frecuencia, pausa o sin frases)."""
        nivel = self.nivel
        if not self.activado or nivel <= 0:
            return None
        if random.random() * 100 >= nivel:
            return None
        with self._lock:
            if time.monotonic() - self._ultima_vez < PAUSA_S:
                return None
        frase = self._elegir_entre(self._candidatas(evento, nivel))
        return self._formatear(frase, datos) if frase else None

    def decir(self, evento, **datos):
        """Encola la frase del evento en la voz del PC. Devuelve la frase o None.
        Nunca lanza excepciones: un fallo aquí no puede parar un apilado."""
        try:
            frase = self.elegir(evento, **datos)
            if frase is None:
                return None
            self._hablar(frase, evento)
            return frase
        except Exception as e:
            log.warning("Humor (%s): %s", evento, e)
            return None

    def probar(self):
        """Una frase al azar de las disponibles al nivel actual (sin frecuencia ni pausa)."""
        nivel = self.nivel
        if nivel <= 0:
            frase = _(DESPEDIDA_HUMOR_CERO)
        else:
            self._recargar_si_cambio()
            frase = self._elegir_entre([f for ev, h, f in self._frases if h <= nivel and ev != "nivel_humor"])
            if frase is None:
                # Sin mencionar el fichero: el humor tiene que parecer cosa de la propia app
                frase = _("A este nivel no tengo nada que añadir. Sube un poco.")
            frase = self._formatear(frase, {})
        self._hablar(frase, "prueba")
        return frase

    def _hablar(self, frase, evento):
        voz = self._voz
        if voz is None:
            import voz as _voz
            voz = self._voz = _voz
        encolada = voz.decir(frase)
        with self._lock:
            self._ultima_vez = time.monotonic()
            self._recientes.append(frase)
            self.ultima_frase = frase
        log.info("Humor (%s, %d %%%s): %s", evento, self.nivel, "" if encolada else ", voz apagada", frase)
        return encolada

    # ── Estado para la web ────────────────────────────────────────────
    def estado(self):
        self._recargar_si_cambio()
        nivel = self.nivel
        niveles = [h for ev, h, f in self._frases if ev != "nivel_humor"]
        return {
            "activado": self.activado,
            "nivel": nivel,
            "total_frases": len(niveles),
            "disponibles": sum(1 for h in niveles if h <= nivel),
            "por_nivel": sorted(niveles),
            "eventos": sorted({ev for ev, h, f in self._frases}),
            "errores_csv": self._errores_csv,
            "ruta": os.path.abspath(self.ruta),
            "pausa_s": PAUSA_S,
            "ultima_frase": self.ultima_frase,
        }


def _sin_comentarios(lineas):
    for linea in lineas:
        if linea.lstrip().startswith("#"):
            yield ""          # conserva la numeración de filas para los avisos
        else:
            yield linea


humorista = Humorista()
idioma.registrar_oyente(humorista.cambiar_idioma)


def decir(evento, **datos):
    return humorista.decir(evento, **datos)


def nivel():
    return humorista.nivel
