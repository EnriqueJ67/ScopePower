# flats.py
#
# Generación del flat maestro en segundo plano.
#
# Un flat es una foto de una superficie uniformemente iluminada (camiseta
# blanca sobre el tubo apuntando a una pantalla o al cielo del crepúsculo).
# Registra el viñeteo y las sombras de polvo del sensor; al dividir cada foto
# por él, desaparecen. Pasos:
#   1. Exposición automática: se ajusta hasta que el nivel medio del sensor
#      queda en torno al 40 % del rango (ni oscuro ni saturado).
#   2. N flats (10 por defecto) con esa exposición.
#   3. (opcional) Tapar el telescopio y N darks de flat con la MISMA
#      exposición, para restar el pedestal del sensor.
#   4. Combinar con sigma-clipping, restar el dark de flat y normalizar cada
#      canal del mosaico Bayer a media 1, para que el flat no altere el
#      balance de color. Se guarda como float32 en
#      calibraciones/flat_b<binning>.fits (el flat normalizado no depende de la
#      exposición ni de la ganancia, pero sí del binning). El fichero antiguo
#      calibraciones/flat.fits sigue valiendo si su cabecera no lleva otro binning.
#
# La captura aplica el flat después del dark: foto = (foto - dark) / flat.

import os
import threading
import logging
import configparser
from datetime import datetime

import numpy as np
import cv2
from astropy.io import fits

from hardware import capturar_frame, ganancia_configurada, CapturaCancelada, ErrorHardware, estado_captura
from apilado import apilar_sigma_clip
from idioma import _, fmt_num

log = logging.getLogger("scopepower.flats")

CARPETA = "calibraciones"
RUTA_FLAT_ANTIGUA = os.path.join(CARPETA, "flat.fits")


def ruta_flat(binning=1):
    return os.path.join(CARPETA, f"flat_b{int(binning)}.fits")


def buscar_flat(binning=1):
    """Ruta del flat maestro para ese binning (o el flat.fits antiguo compatible), o None."""
    ruta = ruta_flat(binning)
    if os.path.exists(ruta):
        return ruta
    if os.path.exists(RUTA_FLAT_ANTIGUA):
        try:
            b = fits.getheader(RUTA_FLAT_ANTIGUA).get("XBINNING")
            if b is None or int(float(b)) == int(binning):
                return RUTA_FLAT_ANTIGUA
        except Exception:
            pass
    return None
_MAP_BAYER = {2: 'RGGB', 3: 'CMYG', 4: 'CMYG2', 5: 'LRGB'}


class FlatsCancelado(Exception):
    pass


def leer_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    f = cfg["Flats"] if "Flats" in cfg else {}
    d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}
    return {
        "numero_flats": int(f.get("numero_flats", 10)),
        "numero_flat_darks": int(f.get("numero_flat_darks", 5)),
        "nivel_objetivo": float(f.get("nivel_objetivo", 0.40)),      # fracción del rango de 16 bits
        "exposicion_inicial": float(f.get("exposicion_inicial", 1.0)),
        "exposicion_minima": float(f.get("exposicion_minima", 0.02)),
        "exposicion_maxima": float(f.get("exposicion_maxima", 15.0)),
        "binning": int(d.get("binning_camara", 1)),
    }


def info_flat(binning=None):
    """Datos del flat maestro guardado para ese binning (el de config.ini si es None), o existe=False."""
    if binning is None:
        binning = leer_config()["binning"]
    ruta = buscar_flat(binning)
    if ruta is None:
        return {"existe": False, "binning_pedido": int(binning)}
    try:
        hdr = fits.getheader(ruta)
    except Exception as e:
        return {"existe": False, "binning_pedido": int(binning), "error": str(e)}
    fecha = datetime.fromtimestamp(os.path.getmtime(ruta))
    try:
        if hdr.get("DATE-OBS"):
            fecha = datetime.fromisoformat(str(hdr["DATE-OBS"]))
    except ValueError:
        pass

    def _num(k):
        try:
            return None if hdr.get(k) is None else float(hdr[k])
        except (TypeError, ValueError):
            return None

    return {
        "existe": True, "ruta": ruta, "nombre": os.path.basename(ruta), "binning_pedido": int(binning),
        "fecha": fecha.strftime("%Y-%m-%d %H:%M"),
        "antiguedad_min": int((datetime.now() - fecha).total_seconds() // 60),
        "exposicion": _num("EXPOSURE"), "nflats": _num("NFLATS"), "nflatdk": _num("NFLATDK"),
        "vinyeteo": _num("VIGNET"), "nivel": _num("NIVEL"), "binning": _num("XBINNING"),
    }


def normalizar_por_canal(flat, patron="RGGB"):
    """Divide cada una de las 4 fases del mosaico por su media (media 1 en cada canal)."""
    flat = flat.astype(np.float32).copy()
    for dy in (0, 1):
        for dx in (0, 1):
            sub = flat[dy::2, dx::2]
            m = float(np.median(sub))
            if m > 0:
                sub /= m
    return flat


def porcentaje_vinyeteo(flat_norm):
    """Caída de luz en las esquinas respecto al centro, en %, sobre el flat suavizado."""
    peq = cv2.resize(flat_norm, (256, 256), interpolation=cv2.INTER_AREA)
    suave = cv2.GaussianBlur(peq, (0, 0), 8)
    centro = float(suave[112:144, 112:144].mean())
    esquinas = float(np.mean([suave[:24, :24].mean(), suave[:24, -24:].mean(),
                              suave[-24:, :24].mean(), suave[-24:, -24:].mean()]))
    return round(100.0 * (1.0 - esquinas / max(1e-6, centro)), 1)


class TrabajoFlats:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self._continuar = threading.Event()
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.fase = ""            # exposicion | flats | esperando_tapa | flat_darks | combinando
        self.completadas = 0
        self.total = 0
        self.exposicion = 0.0
        self.nivel = 0.0          # fracción del rango alcanzada
        self.mensaje = ""
        self.error = ""
        self.ruta = ""
        self.vinyeteo = None

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado, "fase": self.fase,
                "completadas": self.completadas, "total": self.total,
                "exposicion": round(self.exposicion, 3), "nivel": round(self.nivel, 3),
                "mensaje": self.mensaje, "error": self.error, "ruta": self.ruta,
                "vinyeteo": self.vinyeteo, "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto, fase=None):
        with self._lock:
            self.mensaje = texto
            if fase is not None:
                self.fase = fase
        log.info(texto)

    def cancelar(self):
        self._cancelar.set()
        self._continuar.set()
        estado_captura.cancelar.set()

    def continuar(self):
        """El usuario confirma que ha tapado el telescopio (o que no quiere darks de flat)."""
        self._continuar.set()

    def _comprobar(self):
        if self._cancelar.is_set():
            raise FlatsCancelado()

    # ──────────────────────────────────────────────────────────────────
    def iniciar(self, camara, conf=None, con_flat_darks=True, voz=None):
        conf = conf or leer_config()
        with self._lock:
            if self.en_curso:
                raise RuntimeError(_("Ya se están generando flats"))
            if camara.ocupado:
                raise RuntimeError(_("La cámara está ocupada con otra captura"))
            self._reiniciar()
            self._cancelar.clear()
            self._continuar.clear()
            self.en_curso = True
            self.mensaje = _("Preparando la cámara...")
        hilo = threading.Thread(target=self._ejecutar, name="flats", daemon=True,
                                args=(camara, conf, con_flat_darks, voz))
        hilo.start()
        return hilo

    def _decir(self, voz, texto):
        if voz is not None:
            try:
                voz.decir(texto)
            except Exception:
                pass

    def _capturar(self, camara, exposicion, binning, descripcion, timeout_extra=60):
        return camara.ejecutar(
            lambda cam: capturar_frame(cam, exposicion, binning, descripcion=descripcion, ganancia=ganancia_configurada()),
            timeout=exposicion + timeout_extra)

    def _ejecutar(self, camara, conf, con_flat_darks, voz):
        error = ""
        try:
            # ── 1. Exposición automática ─────────────────────────────
            objetivo = conf["nivel_objetivo"] * 65535.0
            exposicion = conf["exposicion_inicial"]
            self._msg(_("Ajustando la exposición del flat..."), fase="exposicion")
            self._decir(voz, _("Ajustando la exposición del flat"))
            for intento in range(6):
                self._comprobar()
                frame = self._capturar(camara, exposicion, conf["binning"], _("Flat prueba {n}", n=intento + 1))
                mediana = float(np.median(frame))
                with self._lock:
                    self.exposicion, self.nivel = exposicion, mediana / 65535.0
                self._msg(_("Prueba {n}: {s} s → nivel {nivel} %", n=intento + 1, s=fmt_num(exposicion, 3),
                             nivel=round(100 * mediana / 65535)), fase="exposicion")
                if 0.75 * objetivo <= mediana <= 1.35 * objetivo:
                    break
                if mediana >= 60000:
                    factor = 0.25                       # saturado: bajar mucho
                else:
                    factor = objetivo / max(mediana, 50.0)
                    factor = max(0.1, min(10.0, factor))
                nueva = max(conf["exposicion_minima"], min(conf["exposicion_maxima"], exposicion * factor))
                if abs(nueva - exposicion) < 1e-4:
                    break
                exposicion = nueva
            else:
                pass
            if mediana < 0.15 * 65535:
                raise RuntimeError(_("Luz insuficiente: nivel {nivel} % con {s} s. Acerca la pantalla o sube su brillo.",
                                     nivel=round(100 * mediana / 65535), s=fmt_num(exposicion, 2)))
            if mediana >= 60000:
                raise RuntimeError(_("Demasiada luz: el sensor satura incluso con la exposición mínima. "
                                     "Baja el brillo de la pantalla o añade otra capa de camiseta."))

            # ── 2. Flats ─────────────────────────────────────────────
            n = conf["numero_flats"]
            with self._lock:
                self.total, self.completadas = n, 0
            flats = []
            self._decir(voz, _("Capturando {n} flats de {s} segundos", n=n, s=fmt_num(exposicion, 2)))
            for i in range(n):
                self._comprobar()
                self._msg(_("Flat {i} de {n} ({s} s)", i=i + 1, n=n, s=fmt_num(exposicion, 3)), fase="flats")
                flats.append(self._capturar(camara, exposicion, conf["binning"], _("Flat {i}/{n}", i=i + 1, n=n)))
                with self._lock:
                    self.completadas = i + 1
            sensor = None
            try:
                sensor = camara.ejecutar(lambda cam: getattr(cam, "SensorType", None), timeout=10)
            except ErrorHardware:
                pass

            # ── 3. Darks de flat (misma exposición, tubo tapado) ─────
            flat_darks = []
            if con_flat_darks and conf["numero_flat_darks"] > 0:
                self._msg(_("Tapa el telescopio y pulsa Continuar para los darks de flat"), fase="esperando_tapa")
                self._decir(voz, _("Flats capturados. Tapa el telescopio y pulsa continuar"))
                while not self._continuar.wait(0.5):
                    self._comprobar()
                self._comprobar()
                nd = conf["numero_flat_darks"]
                with self._lock:
                    self.total, self.completadas = nd, 0
                for i in range(nd):
                    self._comprobar()
                    self._msg(_("Dark de flat {i} de {n}", i=i + 1, n=nd), fase="flat_darks")
                    flat_darks.append(self._capturar(camara, exposicion, conf["binning"], _("Dark flat {i}/{n}", i=i + 1, n=nd)))
                    with self._lock:
                        self.completadas = i + 1

            # ── 4. Combinar y normalizar ─────────────────────────────
            self._msg(_("Combinando flats..."), fase="combinando")
            master = apilar_sigma_clip(flats) if len(flats) >= 3 else np.mean([f.astype(np.float32) for f in flats], axis=0)
            if flat_darks:
                fd = apilar_sigma_clip(flat_darks) if len(flat_darks) >= 3 else np.mean([f.astype(np.float32) for f in flat_darks], axis=0)
                master = master - fd
            master = np.clip(master, 1.0, None)
            patron = _MAP_BAYER.get(sensor, "RGGB")
            flat_norm = normalizar_por_canal(master, patron)
            flat_norm = np.clip(flat_norm, 0.05, 5.0).astype(np.float32)
            vinyeteo = porcentaje_vinyeteo(flat_norm)

            os.makedirs(CARPETA, exist_ok=True)
            hdu = fits.PrimaryHDU(flat_norm)
            hdu.header["IMAGETYP"] = "FLAT"
            hdu.header["EXPOSURE"] = (round(exposicion, 4), "Exposicion de cada flat (s)")
            hdu.header["NFLATS"] = (len(flats), "Flats combinados (sigma-clipping)")
            hdu.header["NFLATDK"] = (len(flat_darks), "Darks de flat restados")
            hdu.header["NIVEL"] = (round(float(np.median(master)) / 65535.0, 3), "Nivel medio alcanzado (fraccion)")
            hdu.header["VIGNET"] = (vinyeteo, "Caida de luz en esquinas (%)")
            hdu.header["XBINNING"] = conf["binning"]
            hdu.header["YBINNING"] = conf["binning"]
            hdu.header["BAYERPAT"] = (patron, "Patron CFA; normalizado por canal a media 1")
            hdu.header["DATE-OBS"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            hdu.header["CREATOR"] = "ScopePower"
            ruta = ruta_flat(conf["binning"])
            hdu.writeto(ruta, overwrite=True)
            with self._lock:
                self.ruta, self.vinyeteo = ruta, vinyeteo
            self._msg(_("Flat maestro guardado ({n} flats, viñeteo {v} %)", n=len(flats), v=round(vinyeteo)), fase="")
            log.info("Flat maestro guardado en %s", ruta)
            self._decir(voz, _("Flat maestro guardado. Ya puedes destapar el telescopio"))
        except (FlatsCancelado, CapturaCancelada):
            error = _("Generación de flats cancelada")
        except ErrorHardware as e:
            error = _("Error de cámara: {e}", e=e)
        except RuntimeError as e:
            error = str(e)
        except Exception as e:
            log.exception("Error generando el flat maestro")
            error = _("Error generando el flat: {e}", e=e)
        finally:
            with self._lock:
                self.error = error
                if error:
                    self.mensaje = error
                self.en_curso, self.terminado = False, True
            if error:
                self._decir(voz, error if len(error) < 80 else _("Error generando los flats"))


trabajo_flats = TrabajoFlats()
