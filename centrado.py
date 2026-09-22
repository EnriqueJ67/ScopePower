# centrado.py
#
# Centrado automático en bucle cerrado tras un GOTO.
#
# En cada vuelta: foto corta -> plate solve -> error angular entre el centro
# real de la imagen y el objetivo -> si está dentro de tolerancia, fin; si no,
# corrección y otra vuelta. Se para cuando se alcanza la tolerancia, cuando se
# agota el número de vueltas o cuando una vuelta no mejora claramente a la
# anterior (holgura de la montura: seguir moviendo solo empeora).
#
# Corrección: en la primera vuelta se hace Sync a la posición resuelta y Slew al
# objetivo (así los GOTO siguientes también mejoran). En las demás vueltas NO se
# sincroniza: se ordena un desplazamiento relativo (posición que la montura cree
# tener + error medido). Las monturas baratas responden bien a movimientos
# pequeños aunque su idea absoluta de dónde apuntan sea mala, y encadenar Syncs
# en una SynScan toca su modelo de alineación.

import os
import time
import threading
import logging
import subprocess
import configparser

import numpy as np
from astropy.io import fits
from astropy.coordinates import SkyCoord
import astropy.units as u

from hardware import capturar_frame_con_pixel, pistas_platesolve, ganancia_configurada, asentamiento_montura, CapturaCancelada, ErrorHardware, estado_captura
import coordenadas
import rotacion_camara

log = logging.getLogger("scopepower.centrado")

RUTA_FITS_CENTRADO = os.path.join("static", "centrado_mono.fits")
# Vista previa JPG de la última foto del centrado (la enseña la pantalla GoTo)
RUTA_PREVIEW_CENTRADO = os.path.join("static", "centrado_preview.jpg")


class CentradoCancelado(Exception):
    pass


def leer_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    c = cfg["Centrado"] if "Centrado" in cfg else {}
    d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}
    return {
        "tolerancia_arcmin": float(c.get("tolerancia_arcmin", 3.0)),
        "max_vueltas": int(c.get("max_vueltas", 3)),
        "exposicion": float(c.get("exposicion_centrado", 5.0)),
        "sync_primera_vuelta": str(c.get("sync_primera_vuelta", "true")).lower() == "true",
        "centrar_tras_goto": str(c.get("centrar_tras_goto", "true")).lower() == "true",
        "asentamiento_s": asentamiento_montura("Centrado"),
        "binning": int(d.get("binning_camara", 1)),
        "focal": float(d.get("focal_len", 250)),
        "ruta_astap": cfg.get("General", "ruta_astap", fallback=""),
    }


def resolver_con_astap(ruta_fits, ruta_astap, timeout=90):
    """
    Resuelve un FITS con ASTAP. Devuelve (ra_grados, dec_grados) del centro de
    la imagen o None si no resuelve / se cuelga.
    """
    base = os.path.splitext(ruta_fits)[0]
    ini = base + ".ini"
    for f in (ini, base + ".wcs"):
        try:
            os.remove(f)
        except OSError:
            pass
    try:
        subprocess.run(f'"{ruta_astap}" -f "{ruta_fits}" -solve -r 50',
                       shell=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("ASTAP no terminó en %s s", timeout)
        return None
    if not os.path.exists(ini):
        return None
    ra = dec = None
    resuelto = False
    with open(ini) as f:
        for linea in f:
            if linea.startswith("PLTSOLVD=T"):
                resuelto = True
            elif linea.startswith("CRVAL1="):
                ra = float(linea.split("=", 1)[1])
            elif linea.startswith("CRVAL2="):
                dec = float(linea.split("=", 1)[1])
    rotacion_camara.anotar_ini(ini, "centrado")   # el giro de la cámara, para el planetario
    for f in (ini, base + ".wcs"):
        try:
            os.remove(f)
        except OSError:
            pass
    if not resuelto or ra is None or dec is None:
        return None
    return ra, dec


class TrabajoCentrado:
    """Estado observable del centrado en curso (uno a la vez)."""

    def __init__(self):
        self.antes_de_foto = None     # callback opcional antes de cada foto (vigilante del seguimiento)
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self.imagen_version = 0    # sube con cada vista previa nueva (la web la refresca por ella)
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.vuelta = 0
        self.max_vueltas = 0
        self.tolerancia = 0.0
        self.errores = []          # error en minutos de arco por vuelta
        self.mensaje = ""
        self.resultado = ""        # centrado | fuera_tolerancia | sin_mejora | error | cancelado
        self.detalle = ""
        self.objetivo = None       # (ra_h, dec_deg)
        self.objeto = ""
        self.imagen = None         # {"version", "hora", "vuelta"} de la última vista previa

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso,
                "terminado": self.terminado,
                "vuelta": self.vuelta,
                "max_vueltas": self.max_vueltas,
                "tolerancia": self.tolerancia,
                "errores": list(self.errores),
                "error_final": self.errores[-1] if self.errores else None,
                "mensaje": self.mensaje,
                "resultado": self.resultado,
                "detalle": self.detalle,
                "objeto": self.objeto,
                "imagen": dict(self.imagen) if self.imagen else None,
                "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto):
        with self._lock:
            self.mensaje = texto
        log.info(texto)

    def cancelar(self):
        self._cancelar.set()
        estado_captura.cancelar.set()

    def _comprobar_cancelacion(self):
        if self._cancelar.is_set():
            raise CentradoCancelado()

    # ──────────────────────────────────────────────────────────────────
    def iniciar(self, camara, montura, objetivo_ra_h, objetivo_dec, objeto="",
                conf=None, calcular_altitud=None, altura_minima=0.0, resolver=None):
        """
        Arranca el centrado en un hilo propio. `resolver(ruta_fits)` permite
        inyectar otro resolvedor (pruebas); por defecto ASTAP.
        """
        conf = conf or leer_config()
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya hay un centrado en curso")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self._cancelar.clear()
            self.en_curso = True
            self.max_vueltas = conf["max_vueltas"]
            self.tolerancia = conf["tolerancia_arcmin"]
            self.objetivo = (float(objetivo_ra_h), float(objetivo_dec))
            self.objeto = objeto
            self.mensaje = "Iniciando centrado..."

        hilo = threading.Thread(
            target=self._ejecutar, name="centrado", daemon=True,
            args=(camara, montura, conf, calcular_altitud, altura_minima,
                  resolver or (lambda ruta: resolver_con_astap(ruta, conf["ruta_astap"]))))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, montura, conf, calcular_altitud, altura_minima, resolver):
        objetivo = SkyCoord(ra=self.objetivo[0] * u.hourangle, dec=self.objetivo[1] * u.deg)
        resultado, detalle = "error", ""
        try:
            for vuelta in range(1, conf["max_vueltas"] + 1):
                with self._lock:
                    self.vuelta = vuelta
                self._comprobar_cancelacion()

                # 1) Foto corta
                self._msg(f"Vuelta {vuelta}/{conf['max_vueltas']}: exponiendo {conf['exposicion']:.0f} s")
                if self.antes_de_foto:
                    try:
                        self.antes_de_foto()
                    except Exception as e:
                        log.warning("Vigilante del seguimiento en el centrado: %s", e)
                frame, sensor_type = camara.ejecutar(
                    lambda cam: (capturar_frame_con_pixel(cam, conf["exposicion"], conf["binning"],
                                                          descripcion=f"Centrado {vuelta}/{conf['max_vueltas']}",
                                                          ganancia=ganancia_configurada()),
                                 getattr(cam, "SensorType", None)),
                    timeout=conf["exposicion"] + 90)
                self._guardar_fits(frame, objetivo, conf)
                self._guardar_preview(frame, sensor_type, vuelta)
                self._comprobar_cancelacion()

                # 2) Resolver
                self._msg(f"Vuelta {vuelta}/{conf['max_vueltas']}: resolviendo la imagen")
                solucion = resolver(RUTA_FITS_CENTRADO)
                if solucion is None:
                    resultado, detalle = "error", f"No se pudo resolver la imagen en la vuelta {vuelta}"
                    return
                ra_deg, dec_deg = solucion
                real = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)

                if calcular_altitud is not None:
                    if calcular_altitud(ra_deg / 15.0, dec_deg) < float(altura_minima):
                        resultado, detalle = "error", "El telescopio apunta por debajo de la altura mínima; centrado cancelado"
                        return

                # 3) Medir el error
                error = float(objetivo.separation(real).arcmin)
                with self._lock:
                    self.errores.append(round(error, 2))
                self._msg(f"Vuelta {vuelta}/{conf['max_vueltas']}: error {error:.1f}'")

                if error <= conf["tolerancia_arcmin"]:
                    resultado, detalle = "centrado", f"Centrado a {error:.1f}' del objetivo en {vuelta} vuelta(s)"
                    return
                if vuelta > 1 and error > 0.8 * self.errores[-2]:
                    resultado, detalle = "sin_mejora", (f"Se queda a {error:.1f}' (antes {self.errores[-2]:.1f}'): "
                                                        "la montura no mejora, probablemente por holgura")
                    return
                if vuelta == conf["max_vueltas"]:
                    resultado, detalle = "fuera_tolerancia", (f"Se queda a {error:.1f}' tras {vuelta} vueltas "
                                                              f"(tolerancia {conf['tolerancia_arcmin']:.1f}')")
                    return

                # 4) Corregir
                self._comprobar_cancelacion()
                self._msg(f"Vuelta {vuelta}/{conf['max_vueltas']}: corrigiendo {error:.1f}'")
                self._corregir(montura, objetivo, real, sincronizar=(vuelta == 1 and conf["sync_primera_vuelta"]),
                               asentamiento=conf["asentamiento_s"])

        except CentradoCancelado:
            resultado, detalle = "cancelado", "Centrado cancelado por el usuario"
        except CapturaCancelada:
            resultado, detalle = "cancelado", "Centrado cancelado por el usuario"
        except ErrorHardware as e:
            resultado, detalle = "error", f"Error de hardware: {e}"
        except Exception as e:
            log.exception("Error en el centrado")
            resultado, detalle = "error", f"Error inesperado: {e}"
        finally:
            with self._lock:
                self.resultado = resultado
                self.detalle = detalle
                self.mensaje = detalle
                self.en_curso = False
                self.terminado = True
            log.info("Centrado terminado: %s (%s) errores=%s", resultado, detalle, self.errores)
            try:
                import voz
                voz.decir(detalle.replace("'", " minutos de arco"))
            except Exception:
                pass

    def _guardar_fits(self, frame, objetivo, conf):
        os.makedirs(os.path.dirname(RUTA_FITS_CENTRADO), exist_ok=True)
        datos, px, py = frame
        hdu = fits.PrimaryHDU(np.asarray(datos, dtype=np.uint16))
        hdu.header["BZERO"] = 32768
        hdu.header["BSCALE"] = 1
        # Pistas para ASTAP: escala de placa y posición aproximada (sin ellas no resuelve)
        pistas_platesolve(hdu.header, conf["focal"], px, py, conf["binning"],
                          ra_h=objetivo.ra.hour, dec_deg=objetivo.dec.deg)
        hdu.writeto(RUTA_FITS_CENTRADO, overwrite=True)

    def _guardar_preview(self, frame, sensor_type, vuelta):
        """JPG pequeño de la foto del centrado para la pantalla GoTo. Nunca interrumpe el centrado."""
        try:
            import cv2
            datos = np.asarray(frame[0])
            patron = {2: "RGGB", 3: "CMYG", 4: "CMYG2", 5: "LRGB"}.get(sensor_type)
            if datos.ndim == 2 and patron:
                from AutoStrechSTF import miniatura_cfa_rgb
                mini = miniatura_cfa_rgb(datos, patron, ancho=600)
                mini = cv2.cvtColor(mini, cv2.COLOR_RGB2BGR)
            else:
                # Cámara mono (o ya en color): reducir y estirar por percentiles
                if datos.ndim == 3:
                    datos = datos.mean(axis=2)
                d = datos.astype(np.float32)
                alto = max(1, int(d.shape[0] * 600 / d.shape[1]))
                d = cv2.resize(d, (600, alto), interpolation=cv2.INTER_AREA)
                lo, hi = np.percentile(d, (0.5, 99.7))
                mini = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
                mini = (np.sqrt(mini) * 255).astype(np.uint8)
            os.makedirs(os.path.dirname(RUTA_PREVIEW_CENTRADO), exist_ok=True)
            cv2.imwrite(RUTA_PREVIEW_CENTRADO, mini, [cv2.IMWRITE_JPEG_QUALITY, 82])
            with self._lock:
                self.imagen_version += 1
                self.imagen = {"version": self.imagen_version, "hora": time.strftime("%H:%M:%S"), "vuelta": vuelta}
        except Exception as e:
            log.warning("No se pudo generar la vista previa del centrado: %s", e)

    def _corregir(self, montura, objetivo, real, sincronizar, asentamiento):
        # Error en el cielo: hacia dónde hay que mover (objetivo - real)
        d_ra = (objetivo.ra.deg - real.ra.deg + 180.0) % 360.0 - 180.0   # grados, con envoltura
        d_dec = objetivo.dec.deg - real.dec.deg

        def _mover(tel):
            if sincronizar:
                # real y objetivo son J2000 (plate solving / catálogo): a sistema de la montura
                tel.SyncToCoordinates(*coordenadas.a_montura(real.ra.hour, real.dec.deg))
                ra_dest, dec_dest = coordenadas.a_montura(objetivo.ra.hour, objetivo.dec.deg)
            else:
                # Desplazamiento relativo sobre lo que la montura cree que apunta
                ra_dest = (tel.RightAscension + d_ra / 15.0) % 24.0
                dec_dest = max(-90.0, min(90.0, tel.Declination + d_dec))
            tel.Tracking = True
            if getattr(tel, "CanSlewAsync", False):
                tel.SlewToCoordinatesAsync(ra_dest, dec_dest)
            else:
                tel.SlewToCoordinates(ra_dest, dec_dest)
            # Esperar a que termine el movimiento (máx. 120 s)
            inicio = time.time()
            time.sleep(0.5)
            while tel.Slewing:
                if time.time() - inicio > 120:
                    raise ErrorHardware("La montura no terminó el movimiento en 120 s")
                if self._cancelar.is_set():
                    try:
                        tel.AbortSlew()
                    except Exception:
                        pass
                    raise CentradoCancelado()
                time.sleep(0.5)
            return ra_dest, dec_dest

        montura.ejecutar(_mover, timeout=150)
        time.sleep(asentamiento)


trabajo_centrado = TrabajoCentrado()
