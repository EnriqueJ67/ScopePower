# darks.py
#
# Generación del dark maestro en segundo plano y consulta del último dark.
#
# El dark se resta a cada foto individual en el momento de capturarla (antes
# del debayer y del alineado, ver /capturar-imagen en ScopePower.py). Este
# módulo fabrica los ficheros de la biblioteca de darks y elige el que casa
# con cada foto.
#
# Biblioteca: un dark maestro por combinación de exposición, ganancia y
# binning, en calibraciones/dark_<exp>s_g<ganancia>_b<binning>.fits (sin el
# tramo _g si la ganancia no se fija desde la app). Los ficheros antiguos
# dark_<exp>s.fits siguen valiendo mientras su cabecera no contradiga la
# ganancia ni el binning pedidos.

import os
import glob
import re
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


_RE_NOMBRE = re.compile(r"^dark_(\d+(?:\.\d+)?)s(?:_g(\d+))?(?:_b(\d+))?\.fits$")


def nombre_dark(exposicion, ganancia=None, binning=1):
    """Nombre del fichero: dark_25.00s_g300_b1.fits (sin _g si la ganancia es None)."""
    partes = [f"dark_{float(exposicion):.2f}s"]
    if ganancia is not None:
        partes.append(f"g{int(ganancia)}")
    partes.append(f"b{int(binning)}")
    return "_".join(partes) + ".fits"


def ruta_dark(exposicion, ganancia=None, binning=1):
    return os.path.join(CARPETA, nombre_dark(exposicion, ganancia, binning))


def _num_cabecera(hdr, clave):
    v = hdr.get(clave)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _cabecera_compatible(ruta, ganancia, binning):
    """Un fichero antiguo (sin ganancia ni binning en el nombre) sirve si su cabecera no lo desmiente."""
    try:
        hdr = fits.getheader(ruta)
    except Exception:
        return False
    g, b = _num_cabecera(hdr, "GAIN"), _num_cabecera(hdr, "XBINNING")
    if ganancia is not None and g is not None and int(round(g)) != int(ganancia):
        return False
    if b is not None and int(round(b)) != int(binning):
        return False
    return True


def buscar_dark(exposicion, ganancia=None, binning=1):
    """
    Ruta del dark maestro que casa con la exposición, la ganancia y el binning,
    o None si no hay ninguno. Mira primero el nombre de la biblioteca y, si no
    está, el fichero antiguo dark_<exp>s.fits con cabecera compatible.
    """
    ruta = ruta_dark(exposicion, ganancia, binning)
    if os.path.exists(ruta):
        return ruta
    antigua = os.path.join(CARPETA, f"dark_{float(exposicion):.2f}s.fits")
    if os.path.exists(antigua) and _cabecera_compatible(antigua, ganancia, binning):
        return antigua
    return None


def listar_darks():
    """
    Biblioteca de darks: una entrada por fichero de calibraciones/, ordenada por
    exposición, ganancia y binning. Los valores salen del nombre y, si faltan,
    de la cabecera FITS (ficheros antiguos).
    """
    biblioteca = []
    for ruta in glob.glob(os.path.join(CARPETA, "dark_*.fits")):
        m = _RE_NOMBRE.match(os.path.basename(ruta))
        if not m:
            continue
        exposicion = float(m.group(1))
        ganancia = int(m.group(2)) if m.group(2) else None
        binning = int(m.group(3)) if m.group(3) else None
        fecha = datetime.fromtimestamp(os.path.getmtime(ruta))
        ndarks = temperatura = None
        try:
            hdr = fits.getheader(ruta)
            if ganancia is None and _num_cabecera(hdr, "GAIN") is not None:
                ganancia = int(round(_num_cabecera(hdr, "GAIN")))
            if binning is None:
                binning = int(_num_cabecera(hdr, "XBINNING") or 1)
            ndarks = _num_cabecera(hdr, "NDARKS")
            temperatura = _num_cabecera(hdr, "CCD-TEMP")
            if hdr.get("DATE-OBS"):
                try:
                    fecha = datetime.fromisoformat(str(hdr["DATE-OBS"]))
                except ValueError:
                    pass
        except Exception as e:
            log.warning("No se pudo leer %s: %s", ruta, e)
        biblioteca.append({
            "nombre": os.path.basename(ruta), "ruta": ruta,
            "exposicion": exposicion, "gain": ganancia, "binning": binning or 1,
            "antiguo": not (m.group(2) or m.group(3)),
            "fecha": fecha.strftime("%Y-%m-%d %H:%M"), "ndarks": ndarks, "temperatura": temperatura,
        })
    biblioteca.sort(key=lambda d: (d["exposicion"], -1 if d["gain"] is None else d["gain"], d["binning"]))
    return biblioteca


def info_dark(exposicion, ganancia=None, binning=1):
    """Datos del dark maestro que casa con exposición, ganancia y binning (o existe=False)."""
    pedido = {"exposicion": float(exposicion), "ganancia": ganancia, "binning": int(binning)}
    ruta = buscar_dark(exposicion, ganancia, binning)
    if ruta is None:
        return {"existe": False, **pedido}
    try:
        hdr = fits.getheader(ruta)
    except Exception as e:
        return {"existe": False, "error": str(e), **pedido}

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
        "nombre": os.path.basename(ruta),
        "exposicion": float(exposicion),
        "ganancia": ganancia,
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
        self.ganancia = None
        self.binning = 1
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

    def iniciar(self, camara, exposicion, binning, n=10, ganancia=None):
        """
        Lanza la captura de `n` darks en el hilo de la cámara. Devuelve al
        instante; el progreso se consulta con estado(). Lanza RuntimeError si
        ya hay un trabajo en curso o la cámara está ocupada.
        `ganancia`: la de config.ini, para que el dark case con los lights
        (y forme parte del nombre del fichero, junto con la exposición y el binning).
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
            self.ganancia = None if ganancia is None else int(ganancia)
            self.binning = int(binning)
            self.mensaje = "Preparando la cámara..."

        def tarea(cam):
            frames = []
            for i in range(n):
                with self._lock:
                    self.mensaje = f"Capturando dark {i + 1} de {n}"
                frame = capturar_frame(cam, exposicion, binning,
                                       descripcion=f"Dark {i + 1}/{n}", light=False, ganancia=ganancia)
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
            ruta = ruta_dark(self.exposicion, self.ganancia, binning)
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
