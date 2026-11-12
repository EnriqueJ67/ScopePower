# darks.py
#
# Generación del dark maestro en segundo plano y consulta del último dark.
#
# El dark se resta a cada foto individual en el momento de capturarla (antes
# del debayer y del alineado, ver /capturar-imagen en ScopePower.py). Este
# módulo fabrica los ficheros de la biblioteca de darks y elige el que casa
# con cada foto.
#
# Biblioteca: un dark maestro por combinación de exposición, ganancia, binning
# y temperatura del sensor (redondeada al grado), en
# calibraciones/dark_<exp>s_g<ganancia>_b<binning>_t<grados>.fits. Sin el tramo
# _g si la ganancia no se fija desde la app; sin el _t si la cámara no da la
# temperatura. Un dark nuevo solo sustituye al de su mismo grado.
#
# Cada foto usa el dark más cercano a la temperatura leída al descargarla: la
# corriente de oscuridad depende mucho de la temperatura, y sin refrigeración
# el sensor sigue al ambiente (casa frente a campo, principio frente a final de
# la noche). Los ficheros sin _t siguen valiendo: su temperatura sale de
# CCD-TEMP. Los antiguos dark_<exp>s.fits valen mientras su cabecera no
# contradiga la ganancia ni el binning pedidos.

import os
import glob
import math
import re
import threading
import time
import logging
from datetime import datetime

import numpy as np
from astropy.io import fits

from hardware import capturar_frame, CapturaCancelada, ErrorHardware
from apilado import apilar_sigma_clip
from idioma import _, ngettext

log = logging.getLogger("scopepower.darks")

CARPETA = "calibraciones"
_MAP_BAYER = {2: 'RGGB', 3: 'CMYG', 4: 'CMYG2', 5: 'LRGB'}


# A partir de esta diferencia entre el dark y la foto se avisa de que conviene hacer otro. Orientativo:
# la corriente de oscuridad suele duplicarse cada 5-6 °C, pero no se ha medido con cada sensor.
AVISO_DIFERENCIA_C = 5.0
_TEMPERATURA_MIN, _TEMPERATURA_MAX = -60.0, 90.0     # fuera de aquí es un valor sin sentido del driver

_RE_NOMBRE = re.compile(r"^dark_(\d+(?:\.\d+)?)s(?:_g(\d+))?(?:_b(\d+))?(?:_t(-?\d+))?\.fits$")

_ultima_lectura = None      # {"valor", "hora", "instante"}: la última temperatura que dio la cámara


def leer_temperatura(cam):
    """
    Temperatura del sensor en °C, o None si la cámara no la da o no tiene sentido.
    Debe ejecutarse DENTRO del hilo de la cámara. Nunca lanza: una foto no se pierde por esto.
    """
    global _ultima_lectura
    try:
        t = float(cam.CCDTemperature)
    except Exception as e:
        log.debug("La cámara no da la temperatura: %s", e)
        return None
    if not math.isfinite(t) or not _TEMPERATURA_MIN <= t <= _TEMPERATURA_MAX:
        log.debug("Temperatura del sensor sin sentido: %s", t)
        return None
    _ultima_lectura = {"valor": t, "hora": datetime.now().strftime("%H:%M"), "instante": time.monotonic()}
    return t


def ultima_temperatura():
    """La última lectura de la cámara: {"valor", "hora", "edad" (segundos)}, o None si aún no hay ninguna."""
    u = _ultima_lectura
    if u is None:
        return None
    return {"valor": u["valor"], "hora": u["hora"], "edad": time.monotonic() - u["instante"]}


def _grado(temperatura):
    """Redondeo al grado para el nombre (siempre hacia arriba en el .5, también bajo cero)."""
    return int(math.floor(float(temperatura) + 0.5))


def nombre_dark(exposicion, ganancia=None, binning=1, temperatura=None):
    """Nombre del fichero: dark_25.00s_g300_b1_t21.fits (sin _g ni _t si la ganancia o la temperatura son None)."""
    partes = [f"dark_{float(exposicion):.2f}s"]
    if ganancia is not None:
        partes.append(f"g{int(ganancia)}")
    partes.append(f"b{int(binning)}")
    if temperatura is not None:
        partes.append(f"t{_grado(temperatura)}")
    return "_".join(partes) + ".fits"


def ruta_dark(exposicion, ganancia=None, binning=1, temperatura=None):
    return os.path.join(CARPETA, nombre_dark(exposicion, ganancia, binning, temperatura))


def _num_cabecera(hdr, clave):
    v = hdr.get(clave)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _temperatura_dark(hdr, nombre):
    """Temperatura de un dark: la exacta de la cabecera o, si no está, los grados del nombre."""
    t = _num_cabecera(hdr, "CCD-TEMP")
    if t is not None:
        return t
    m = _RE_NOMBRE.match(nombre)
    return float(m.group(4)) if m and m.group(4) else None


def _fecha_dark(hdr, ruta):
    """Cuándo se hizo el dark: la cabecera manda, porque copiar el fichero le cambia la fecha."""
    for clave in ("DATE-OBS", "DATE"):
        try:
            if hdr.get(clave):
                return datetime.fromisoformat(str(hdr[clave]))
        except ValueError:
            pass
    return datetime.fromtimestamp(os.path.getmtime(ruta))


def _cabecera_compatible(hdr, ganancia, binning):
    """Un fichero antiguo (sin ganancia ni binning en el nombre) sirve si su cabecera no lo desmiente."""
    g, b = _num_cabecera(hdr, "GAIN"), _num_cabecera(hdr, "XBINNING")
    if ganancia is not None and g is not None and int(round(g)) != int(ganancia):
        return False
    if b is not None and int(round(b)) != int(binning):
        return False
    return True


def _candidatos(exposicion, ganancia=None, binning=1):
    """Los darks que casan con exposición, ganancia y binning, a cualquier temperatura."""
    base = f"dark_{float(exposicion):.2f}s"
    prefijo = f"{base}_b{int(binning)}" if ganancia is None else f"{base}_g{int(ganancia)}_b{int(binning)}"
    de_la_biblioteca = re.compile(re.escape(prefijo) + r"(?:_t-?\d+)?\.fits$")
    antiguo = base + ".fits"
    candidatos = []
    for ruta in glob.glob(os.path.join(CARPETA, base + "*.fits")):
        nombre = os.path.basename(ruta)
        if nombre != antiguo and not de_la_biblioteca.match(nombre):
            continue
        try:
            hdr = fits.getheader(ruta)
        except Exception as e:
            log.warning("No se pudo leer %s: %s", ruta, e)      # uno estropeado no debe tirar la foto
            continue
        if nombre == antiguo and not _cabecera_compatible(hdr, ganancia, binning):
            continue
        candidatos.append({"ruta": ruta, "nombre": nombre, "hdr": hdr, "temperatura": _temperatura_dark(hdr, nombre),
                           "fecha": _fecha_dark(hdr, ruta)})
    return candidatos


def elegir_dark(exposicion, ganancia=None, binning=1, temperatura=None):
    """
    El dark que casa con exposición, ganancia y binning y está más cerca de `temperatura`, o None
    si no hay ninguno. Dentro del mismo grado de diferencia gana el más reciente, y también si la
    temperatura de la foto no se conoce. Un dark sin temperatura solo se usa si no hay otro.
    Devuelve {"ruta", "nombre", "hdr", "temperatura", "fecha", "diferencia", "lejos"}.
    """
    candidatos = _candidatos(exposicion, ganancia, binning)
    if not candidatos:
        return None
    if temperatura is None:
        elegido = max(candidatos, key=lambda c: c["fecha"].timestamp())
    else:
        def clave(c):
            if c["temperatura"] is None:
                return (1, 0, -c["fecha"].timestamp())
            return (0, _grado(abs(c["temperatura"] - temperatura)), -c["fecha"].timestamp())
        elegido = min(candidatos, key=clave)
    diferencia = None
    if temperatura is not None and elegido["temperatura"] is not None:
        diferencia = abs(elegido["temperatura"] - temperatura)
    return {**elegido, "diferencia": diferencia, "lejos": diferencia is not None and diferencia >= AVISO_DIFERENCIA_C}


def listar_darks():
    """
    Biblioteca de darks: una entrada por fichero de calibraciones/, ordenada por
    exposición, ganancia, binning y temperatura. Los valores salen del nombre y,
    si faltan, de la cabecera FITS (ficheros antiguos).
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
            temperatura = _temperatura_dark(hdr, os.path.basename(ruta))
            fecha = _fecha_dark(hdr, ruta)
        except Exception as e:
            log.warning("No se pudo leer %s: %s", ruta, e)
        biblioteca.append({
            "nombre": os.path.basename(ruta), "ruta": ruta,
            "exposicion": exposicion, "gain": ganancia, "binning": binning or 1,
            "antiguo": not (m.group(2) or m.group(3) or m.group(4)),
            "fecha": fecha.strftime("%Y-%m-%d %H:%M"), "ndarks": ndarks, "temperatura": temperatura,
        })
    biblioteca.sort(key=lambda d: (d["exposicion"], -1 if d["gain"] is None else d["gain"], d["binning"],
                                   -999 if d["temperatura"] is None else d["temperatura"]))
    return biblioteca


def info_dark(exposicion, ganancia=None, binning=1, temperatura=None):
    """Datos del dark que se usaría con exposición, ganancia, binning y temperatura del sensor (o existe=False)."""
    pedido = {"exposicion": float(exposicion), "ganancia": ganancia, "binning": int(binning),
              "temperatura_camara": temperatura}
    eleccion = elegir_dark(exposicion, ganancia, binning, temperatura)
    if eleccion is None:
        return {"existe": False, **pedido}
    hdr = eleccion["hdr"]
    fecha = eleccion["fecha"]
    antiguedad_min = int((datetime.now() - fecha).total_seconds() // 60)

    def _num(clave):
        v = hdr.get(clave)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    return {
        "existe": True,
        "ruta": eleccion["ruta"],
        "nombre": eleccion["nombre"],
        "exposicion": float(exposicion),
        "ganancia": ganancia,
        "fecha": fecha.strftime("%Y-%m-%d %H:%M"),
        "antiguedad_min": antiguedad_min,
        "binning": _num("XBINNING"),
        "temperatura": eleccion["temperatura"],
        "temperatura_camara": temperatura,
        "diferencia": eleccion["diferencia"],
        "lejos": eleccion["lejos"],
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
                raise RuntimeError(_("Ya se están generando darks"))
            if camara.ocupado:
                raise RuntimeError(_("La cámara está ocupada con otra captura"))
            self._reiniciar()
            self.en_curso = True
            self.total = n
            self.exposicion = exposicion
            self.ganancia = None if ganancia is None else int(ganancia)
            self.binning = int(binning)
            self.mensaje = _("Preparando la cámara...")

        def tarea(cam):
            frames = []
            temperaturas = []
            for i in range(n):
                with self._lock:
                    self.mensaje = _("Capturando dark {i} de {n}", i=i + 1, n=n)
                frame = capturar_frame(cam, exposicion, binning,
                                       descripcion=_("Dark {i}/{n}", i=i + 1, n=n), light=False, ganancia=ganancia)
                frames.append(frame)
                # En cada toma, tras descargarla como las fotos: sin refrigeración, el sensor se calienta durante la serie
                t = leer_temperatura(cam)
                if t is not None:
                    temperaturas.append(t)
                with self._lock:
                    self.completadas = i + 1
                log.info("Dark %d/%d capturado", i + 1, n)

            with self._lock:
                self.mensaje = _("Combinando darks...")

            extra = {}
            if temperaturas:
                extra["CCD-TEMP"] = round(sum(temperaturas) / len(temperaturas), 2)
                extra["TEMPMIN"] = min(temperaturas)
                extra["TEMPMAX"] = max(temperaturas)
            for clave, attr in (("GAIN", "Gain"), ("OFFSET", "Offset")):
                try:
                    extra[clave] = float(getattr(cam, attr))
                except Exception:
                    pass
            extra["BAYERPAT"] = _MAP_BAYER.get(getattr(cam, "SensorType", None), "Mono")
            return frames, extra

        futuro = camara.ejecutar_async(tarea)
        futuro.add_done_callback(lambda f: self._terminar(f, binning))

    def _retirar_sustituidos(self, ruta_nueva, binning, temperatura):
        """Un fichero sin _t (de antes de ordenar por temperatura) del mismo grado ya no se elegiría nunca."""
        if temperatura is None:
            return
        for c in _candidatos(self.exposicion, self.ganancia, binning):
            if (c["ruta"] != ruta_nueva and c["temperatura"] is not None
                    and _grado(c["temperatura"]) == _grado(temperatura)):
                try:
                    os.remove(c["ruta"])
                    log.info("Dark sustituido por %s: %s", os.path.basename(ruta_nueva), c["nombre"])
                except OSError as e:
                    log.warning("No se pudo retirar %s: %s", c["ruta"], e)

    def _terminar(self, futuro, binning):
        try:
            frames, extra = futuro.result()
            master = apilar_sigma_clip(frames) if len(frames) >= 3 else np.mean(
                [f.astype(np.float32) for f in frames], axis=0)
            master = np.clip(master, 0, 65535).astype(np.uint16)

            os.makedirs(CARPETA, exist_ok=True)
            temperatura = extra.get("CCD-TEMP")
            ruta = ruta_dark(self.exposicion, self.ganancia, binning, temperatura)
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
            self._retirar_sustituidos(ruta, binning, temperatura)

            with self._lock:
                self.ruta = ruta
                self.mensaje = ngettext("Dark maestro guardado ({n} toma)", "Dark maestro guardado ({n} tomas)", len(frames))
            log.info("Dark maestro guardado en %s", ruta)
        except CapturaCancelada:
            with self._lock:
                self.error = _("Generación de darks cancelada")
                self.mensaje = self.error
        except ErrorHardware as e:
            with self._lock:
                self.error = _("Error de cámara: {e}", e=e)
                self.mensaje = self.error
        except Exception as e:
            log.exception("Error generando el dark maestro")
            with self._lock:
                self.error = _("Error generando el dark: {e}", e=e)
                self.mensaje = self.error
        finally:
            with self._lock:
                self.en_curso = False
                self.terminado = True
                # También si falla o se cancela: la tapa sigue puesta y la siguiente foto saldría negra
                aviso = (_("{error}. Quita la tapa del telescopio antes de hacer fotos", error=self.error) if self.error
                         else _("Dark maestro guardado. Quita la tapa del telescopio antes de hacer fotos"))
            try:
                import voz
                voz.decir(aviso)
            except Exception:
                pass


trabajo_darks = TrabajoDarks()
