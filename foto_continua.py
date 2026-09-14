# foto_continua.py
#
# Modo continuo de la Foto de prueba: un hilo del servidor encadena fotos de
# prueba (con la exposición de Configuración, el dark y el flat si los hay) hasta
# que el usuario lo para, para enfocar y encuadrar sin tener que pulsar cada vez.
# La web solo consulta el estado y refresca el visor cuando hay foto nueva.
#
# Cada foto puede guardarse además en static/continuo/<fecha>/ como JPG (la foto
# revelada a resolución completa, unos pocos MB) o como FITS (los datos en bruto,
# 18 MB con la IMX533), o no guardarse (solo verse). El bucle se para solo si se
# acaba el disco o tras varios errores seguidos de la cámara.
#
# No es vídeo: por ASCOM cada fotograma cuesta la exposición más la descarga del
# ImageArray y el revelado (varios segundos con la Saturn-C), así que el ritmo
# real es de una foto cada pocos segundos.

import os
import time
import shutil
import threading
import logging
from datetime import datetime

from hardware import estado_captura

log = logging.getLogger("scopepower.continuo")

CARPETA_CONTINUO = os.path.join("static", "continuo")
FORMATOS = ("no", "jpg", "fits")
ERRORES_SEGUIDOS_MAX = 3
DISCO_MINIMO_BYTES = 1 * 1024 ** 3      # con menos de 1 GB libre se deja de guardar (y se para)


def espacio_libre(ruta):
    """Bytes libres en el disco que contiene `ruta` (o su primer padre que exista)."""
    ruta = os.path.abspath(ruta)
    while not os.path.exists(ruta):
        padre = os.path.dirname(ruta)
        if padre == ruta:
            break
        ruta = padre
    try:
        return shutil.disk_usage(ruta).free
    except OSError:
        return None


class TrabajoContinuo:
    def __init__(self):
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo = None
        self.version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.hechas = 0
        self.errores = 0
        self.errores_seguidos = 0
        self.guardar = "no"
        self.ganancia = None
        self.carpeta = ""
        self.bytes = 0
        self.libre = None
        self.mensaje = ""
        self.error = ""
        self.ultima = None        # {"n", "hora", "exposicion", "dark", "ganancia", "archivo"}
        self.inicio = ""

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado,
                "hechas": self.hechas, "errores": self.errores,
                "guardar": self.guardar, "ganancia": self.ganancia,
                "carpeta": os.path.basename(self.carpeta) if self.carpeta else "",
                "bytes": self.bytes, "libre": self.libre,
                "mensaje": self.mensaje, "error": self.error,
                "ultima": dict(self.ultima) if self.ultima else None,
                "inicio": self.inicio, "version": self.version,
                "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto):
        with self._lock:
            self.mensaje = texto
        log.info(texto)

    # ── Control ───────────────────────────────────────────────────────
    def iniciar(self, capturar, camara, guardar="no", ganancia=None, raiz=None, voz=None):
        """
        capturar(copia) -> dict de capturar_y_guardar ({"ok", "mensaje", "dark", "ganancia",
                           "archivo"...}); `copia` es None o {"carpeta", "formato", "nombre"}
                           para guardar además esta foto en la carpeta de la serie.
        camara           : TrabajadorCOM de la cámara (para .ocupado)
        guardar          : "no" | "jpg" | "fits"
        """
        if guardar not in FORMATOS:
            raise ValueError(f"Formato desconocido: {guardar}")
        with self._lock:
            if self.en_curso:
                raise RuntimeError("El modo continuo ya está en marcha")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self._parar.clear()
            self.en_curso = True
            self.guardar = guardar
            self.ganancia = ganancia
            self.inicio = datetime.now().strftime("%Y%m%d_%H%M%S")
            if guardar != "no":
                self.carpeta = os.path.join(raiz or CARPETA_CONTINUO, self.inicio)
                self.libre = espacio_libre(self.carpeta)
            self.mensaje = "Empezando el modo continuo..."
        self._hilo = threading.Thread(target=self._ejecutar, name="foto-continua", daemon=True,
                                      args=(capturar, voz))
        self._hilo.start()
        log.info("Modo continuo iniciado (guardar=%s, ganancia=%s)", guardar, ganancia)
        return self._hilo

    def parar(self):
        """Para el bucle y aborta la exposición en curso, si la hay."""
        self._parar.set()
        estado_captura.cancelar.set()

    # ── Hilo ──────────────────────────────────────────────────────────
    def _ejecutar(self, capturar, voz):
        error = ""
        if voz:
            voz.decir("Modo continuo", categoria="capturas")
        try:
            while not self._parar.is_set():
                n = self.hechas + 1
                copia = None
                if self.guardar != "no":
                    libre = espacio_libre(self.carpeta)
                    with self._lock:
                        self.libre = libre
                    if libre is not None and libre < DISCO_MINIMO_BYTES:
                        error = f"Parado: queda menos de 1 GB libre en el disco ({self.hechas} fotos guardadas)"
                        break
                    copia = {"carpeta": self.carpeta, "formato": self.guardar, "nombre": f"foto_{n:04d}"}
                try:
                    resultado = capturar(copia)
                except Exception as e:
                    log.exception("Error inesperado en el modo continuo")
                    resultado = {"ok": False, "mensaje": f"Error inesperado: {e}"}

                if resultado.get("ok"):
                    archivo = resultado.get("archivo")
                    tamano = 0
                    if archivo:
                        try:
                            tamano = os.path.getsize(archivo)
                        except OSError:
                            pass
                    hora = datetime.now().strftime("%H:%M:%S")
                    with self._lock:
                        self.hechas = n
                        self.errores_seguidos = 0
                        self.bytes += tamano
                        self.ultima = {"n": n, "hora": hora, "exposicion": resultado.get("exposicion"),
                                       "dark": bool(resultado.get("dark")), "ganancia": resultado.get("ganancia"),
                                       "archivo": os.path.basename(archivo) if archivo else ""}
                        self.version += 1
                    self._msg(f"Foto {n} a las {hora}" + (f" · guardada {os.path.basename(archivo)}" if archivo else ""))
                elif resultado.get("cancelada"):
                    # "Cancelar" (o parar()) durante la exposición: se acaba el bucle
                    break
                else:
                    with self._lock:
                        self.errores += 1
                        self.errores_seguidos += 1
                        seguidos = self.errores_seguidos
                    self._msg(f"Error en la foto {n}: {resultado.get('mensaje', 'Error')}")
                    if seguidos >= ERRORES_SEGUIDOS_MAX:
                        error = f"Parado tras {seguidos} errores seguidos: {resultado.get('mensaje', 'Error')}"
                        break
                    self._parar.wait(1.0)
                    continue
                # Respiro corto entre fotos: deja pasar las peticiones de la web
                self._parar.wait(0.2)
        finally:
            with self._lock:
                self.en_curso = False
                self.terminado = True
                self.error = error
                if error:
                    self.mensaje = error
                elif self.guardar != "no" and self.hechas:
                    self.mensaje = f"Modo continuo parado: {self.hechas} fotos guardadas en {os.path.basename(self.carpeta)}"
                else:
                    self.mensaje = f"Modo continuo parado tras {self.hechas} fotos"
            log.info(self.mensaje)
            if voz:
                try:
                    voz.decir("Modo continuo parado", categoria="capturas")
                except Exception:
                    pass


trabajo_continuo = TrabajoContinuo()
