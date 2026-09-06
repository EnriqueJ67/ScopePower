# darks.py
#
# Generación del dark maestro en segundo plano y consulta del último dark.
#
# El dark se resta a cada foto individual en el momento de capturarla (antes
# del debayer y del alineado, ver /capturar-imagen en ScopePower.py). Este
# módulo solo se ocupa de FABRICAR el fichero calibraciones/dark_<exp>s.fits.

import os
import threading
import logging
from datetime import datetime

import numpy as np
from astropy.io import fits

from hardware import capturar_frame, CapturaCancelada, ErrorHardware
from apilado import apilar_sigma_clip

log = logging.getLogger("scopepower.darks")

CARPETA = "calibraciones"
_MAP_BAYER = {2: 'RGGB', 3: 'CMYG', 4: 'CMYG2', 5: 'LRGB'}


def ruta_dark(exposicion):
    return os.path.join(CARPETA, f"dark_{float(exposicion):.2f}s.fits")


def info_dark(exposicion):
    """Datos del dark maestro guardado para esa exposición (o existe=False)."""
    ruta = ruta_dark(exposicion)
    if not os.path.exists(ruta):
        return {"existe": False, "exposicion": float(exposicion)}
    try:
        hdr = fits.getheader(ruta)
    except Exception as e:
        return {"existe": False, "exposicion": float(exposicion), "error": str(e)}

    fecha = datetime.fromtimestamp(os.path.getmtime(ruta))
    for clave in ("DATE-OBS", "DATE"):
        try:
            if hdr.get(clave):
                fecha = datetime.fromisoformat(str(hdr[clave]))
                break
        except ValueError:
            pass
    antiguedad_min = int((datetime.now() - fecha).total_seconds() // 60)

    def _num(clave):
        v = hdr.get(clave)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    return {
        "existe": True,
        "ruta": ruta,
        "exposicion": float(exposicion),
        "fecha": fecha.strftime("%Y-%m-%d %H:%M"),
        "antiguedad_min": antiguedad_min,
        "binning": _num("XBINNING"),
        "temperatura": _num("CCD-TEMP"),
        "gain": _num("GAIN"),
        "ndarks": _num("NDARKS"),
    }


class TrabajoDarks:
    """Estado de la generación de darks en curso (una sola a la vez)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.completadas = 0
        self.total = 0
        self.exposicion = 0.0
        self.error = ""
        self.mensaje = ""
        self.ruta = ""

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso,
                "terminado": self.terminado,
                "completadas": self.completadas,
                "total": self.total,
                "exposicion": self.exposicion,
                "error": self.error,
                "mensaje": self.mensaje,
                "ruta": self.ruta,
            }

    def iniciar(self, camara, exposicion, binning, n=10):
        """
        Lanza la captura de `n` darks en el hilo de la cámara. Devuelve al
        instante; el progreso se consulta con estado(). Lanza RuntimeError si
        ya hay un trabajo en curso o la cámara está ocupada.
        """
        n = max(1, int(n))
        exposicion = float(exposicion)
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya se están generando darks")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self.en_curso = True
            self.total = n
            self.exposicion = exposicion
            self.mensaje = "Preparando la cámara..."

        def tarea(cam):
            frames = []
            for i in range(n):
                with self._lock:
                    self.mensaje = f"Capturando dark {i + 1} de {n}"
                frame = capturar_frame(cam, exposicion, binning,
                                       descripcion=f"Dark {i + 1}/{n}", light=False)
                frames.append(frame)
                with self._lock:
                    self.completadas = i + 1
                log.info("Dark %d/%d capturado", i + 1, n)

            with self._lock:
                self.mensaje = "Combinando darks..."

            extra = {}
            for clave, attr in (("CCD-TEMP", "CCDTemperature"), ("GAIN", "Gain"), ("OFFSET", "Offset")):
                try:
                    extra[clave] = float(getattr(cam, attr))
                except Exception:
                    pass
            extra["BAYERPAT"] = _MAP_BAYER.get(getattr(cam, "SensorType", None), "Mono")
            return frames, extra

        futuro = camara.ejecutar_async(tarea)
        futuro.add_done_callback(lambda f: self._terminar(f, binning))

    def _terminar(self, futuro, binning):
        try:
            frames, extra = futuro.result()
            master = apilar_sigma_clip(frames) if len(frames) >= 3 else np.mean(
                [f.astype(np.float32) for f in frames], axis=0)
            master = np.clip(master, 0, 65535).astype(np.uint16)

            os.makedirs(CARPETA, exist_ok=True)
            ruta = ruta_dark(self.exposicion)
            hdu = fits.PrimaryHDU(master)
            hdu.header["IMAGETYP"] = "DARK"
            hdu.header["EXPOSURE"] = (self.exposicion, "Exposicion en segundos")
            hdu.header["EXPTIME"] = self.exposicion
            hdu.header["XBINNING"] = binning
            hdu.header["YBINNING"] = binning
            hdu.header["NDARKS"] = (len(frames), "Darks combinados (sigma-clipping)")
            hdu.header["DATE-OBS"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            hdu.header["BAYERPAT"] = (extra.pop("BAYERPAT"), "Patron CFA segun ASCOM SensorType")
            for clave, valor in extra.items():
                hdu.header[clave] = valor
            hdu.header["CREATOR"] = "ScopePower"
            hdu.writeto(ruta, overwrite=True)

            with self._lock:
                self.ruta = ruta
                self.mensaje = f"Dark maestro guardado ({len(frames)} tomas)"
            log.info("Dark maestro guardado en %s", ruta)
        except CapturaCancelada:
            with self._lock:
                self.error = "Generación de darks cancelada"
                self.mensaje = self.error
        except ErrorHardware as e:
            with self._lock:
                self.error = f"Error de cámara: {e}"
                self.mensaje = self.error
        except Exception as e:
            log.exception("Error generando el dark maestro")
            with self._lock:
                self.error = f"Error generando el dark: {e}"
                self.mensaje = self.error
        finally:
            with self._lock:
                self.en_curso = False
                self.terminado = True
                aviso = self.error or "Dark maestro guardado. Ya puedes destapar el telescopio"
            try:
                import voz
                voz.decir(aviso)
            except Exception:
                pass


trabajo_darks = TrabajoDarks()
