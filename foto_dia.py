# -*- coding: utf-8 -*-
"""
Modo día de la Foto de prueba: exposición automática para Sol (con filtro), Luna
y paisaje, y guardado de la foto como FITS + PNG lineal (sin auto-stretch).

Funciona como los trabajos de darks y flats: se lanza en un hilo, la web sondea
`estado()` y se cancela con el mismo /cancelar-captura de siempre.

La exposición se busca como en los flats, pero midiendo la zona MÁS BRILLANTE de
la imagen (percentil 99,8) en vez de la mediana: de día lo que hay que evitar es
quemar el disco del Sol o de la Luna, no dejar el fondo a un nivel dado.
"""
import os
import time
import logging
import threading

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

    def _msg(self, texto, fase=None):
        with self._lock:
            self.mensaje = texto
            if fase:
                self.fase = fase
        log.info(texto)

    # ──────────────────────────────────────────────────────────────────
    def iniciar(self, camara, objetivo, binning, exposicion_inicial=None, montura=None,
                ruta_fits=None, ruta_png=None, al_terminar=None, voz=None):
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
                                args=(camara, objetivo, binning, exposicion_inicial, montura, ruta_fits, ruta_png, al_terminar, voz))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, objetivo, binning, exposicion_inicial, montura, ruta_fits, ruta_png, al_terminar, voz):
        conf = OBJETIVOS[objetivo]
        error = ""
        try:
            # Seguimiento solar/lunar mientras se fotografía (si la montura está conectada)
            if montura is not None and conf["seguimiento"] and montura.esta_conectado():
                if poner_seguimiento(montura, conf["seguimiento"]):
                    with self._lock:
                        self.seguimiento = conf["seguimiento"]

            def _sesion(cam):
                # ── ganancia mínima: de día sobra luz y así hay menos ruido y más rango ──
                ganancia_previa, ganancia = None, None
                try:
                    ganancia_previa = cam.Gain
                    minima = cam.GainMin
                    if ganancia_previa != minima:
                        cam.Gain = minima
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
                minima = max(conf["minima"], exp_min)
                with self._lock:
                    self.ganancia = ganancia

                objetivo_nivel = conf["nivel"]
                exposicion = float(exposicion_inicial or conf["inicial"])
                exposicion = max(minima, min(conf["maxima"], exposicion))
                mejor = None      # (distancia al objetivo, frame, exposicion, nivel)
                frame, nivel = None, 0.0
                try:
                    for intento in range(1, MAX_INTENTOS + 1):
                        self._msg(f"Prueba {intento}: exponiendo {formatear_exposicion(exposicion)}", fase="ajustando")
                        frame = capturar_frame(cam, exposicion, binning, descripcion=f"Foto de día {intento}")
                        nivel = float(np.percentile(frame, 99.8)) / blanco
                        saturados = float(np.mean(frame >= 0.98 * blanco))
                        with self._lock:
                            self.intentos.append({"exposicion": exposicion, "nivel": round(nivel, 3)})
                            self.exposicion, self.nivel = exposicion, round(nivel, 3)
                        self._msg(f"Prueba {intento}: {formatear_exposicion(exposicion)} → nivel {100 * nivel:.0f} %", fase="ajustando")
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
                    saturada = mejor is None
                    with self._lock:
                        self.exposicion, self.nivel, self.saturada = exposicion, round(nivel, 3), saturada
                    return frame, exposicion, nivel, saturada, blanco, cam.SensorType, cam.PixelSizeX, cam.PixelSizeY
                finally:
                    if ganancia_previa is not None and ganancia_previa != ganancia:
                        try:
                            cam.Gain = ganancia_previa
                        except Exception:
                            pass

            frame, exposicion, nivel, saturada, blanco, sensor_type, px, py = camara.ejecutar(_sesion, timeout=180)

            # ── guardar ──
            self._msg("Guardando la foto...", fase="guardando")
            if ruta_fits:
                self._guardar_fits(frame, exposicion, sensor_type, px, py, binning, ruta_fits)
            if ruta_png:
                self._guardar_png(frame, sensor_type, ruta_png)
            with self._lock:
                self.version += 1
                self.ruta_fits = ruta_fits or ""
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

    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _patron(sensor_type):
        return {2: "RGGB", 3: "CMYG", 4: "CMYG2", 5: "LRGB"}.get(sensor_type)

    def _guardar_fits(self, frame, exposicion, sensor_type, px, py, binning, ruta):
        from astropy.io import fits
        hdu = fits.PrimaryHDU(np.asarray(frame, dtype=np.uint16))
        hdu.header["EXPTIME"] = (float(exposicion), "Exposicion (s)")
        hdu.header["XBINNING"] = binning
        hdu.header["YBINNING"] = binning
        hdu.header["XPIXSZ"] = (float(px) * binning, "Tamano de pixel con binning (um)")
        hdu.header["YPIXSZ"] = (float(py) * binning, "Tamano de pixel con binning (um)")
        patron = self._patron(sensor_type)
        if patron:
            hdu.header["BAYERPAT"] = (patron, "Patron Bayer segun ASCOM SensorType")
        hdu.header["MODO"] = ("dia", "Foto de dia con exposicion automatica")
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        hdu.writeto(ruta, overwrite=True)

    def _guardar_png(self, frame, sensor_type, ruta):
        """Conversión lineal (con una gamma suave), sin auto-stretch: de día no hay nada que estirar."""
        import cv2
        datos = np.asarray(frame)
        patron = self._patron(sensor_type)
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
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        cv2.imwrite(ruta, img)


trabajo_foto_dia = TrabajoFotoDia()
