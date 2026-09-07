# alineacion.py
#
# Alineación automática de varios puntos por plate solving (estilo Seestar),
# siguiendo el procedimiento del script "AutoAlign" que Sky-Watcher incluye
# con SynScan Pro para SharpCap:
#
#   1. (opcional) ClearSyncTo: borrar el modelo de apuntado anterior en la app
#   2. Resolver a ciegas donde apunta el telescopio y SyncToCoordinates
#      (alineación de una estrella)
#   3. Recorrer varias posiciones repartidas por el cielo (altura/acimut);
#      en cada una: mover, esperar a que el seguimiento se estabilice, foto
#      corta, resolver, SyncToCoordinates. Cada sync es una "muestra" del
#      modelo de apuntado de la app.
#
# Antes de cada sync se mide cuánto se equivocaba la montura en ese punto
# (posición que decía tener frente a la resuelta). Esa lista de errores es el
# informe: debería ir bajando conforme el modelo acumula puntos.

import time
import threading
import logging
import configparser
from datetime import datetime

import numpy as np
from astropy.io import fits
from astropy.coordinates import SkyCoord, AltAz, EarthLocation
from astropy.time import Time
import astropy.units as u

from hardware import capturar_frame_con_pixel, pistas_platesolve, CapturaCancelada, ErrorHardware, estado_captura
from centrado import resolver_con_astap, CentradoCancelado
import coordenadas

log = logging.getLogger("scopepower.alineacion")

RUTA_FITS_ALINEACION = "static/alineacion_mono.fits"


def leer_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    a = cfg["Alineacion"] if "Alineacion" in cfg else {}
    c = cfg["Centrado"] if "Centrado" in cfg else {}
    d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}
    o = cfg["Observatorio"] if "Observatorio" in cfg else {}
    return {
        "puntos": int(a.get("puntos", 6)),
        "altura_maxima": float(a.get("altura_maxima", 65)),
        "altura_minima": float(a.get("altura_minima", 35)),
        "asentamiento_s": float(a.get("asentamiento_s", 5)),
        "limpiar_antes": str(a.get("limpiar_antes", "true")).lower() == "true",
        "exposicion": float(c.get("exposicion_centrado", 5.0)),
        "binning": int(d.get("binning_camara", 1)),
        "focal": float(d.get("focal_len", 250)),
        "ruta_astap": cfg.get("General", "ruta_astap", fallback=""),
        "latitud": float(o.get("latitud_observatorio", 0)),
        "longitud": float(o.get("longitud_observatorio", 0)),
        "altitud": float(o.get("altitud_observatorio", 0)),
    }


def plan_de_puntos(n, altura_maxima, altura_minima, acimut_inicial=0.0):
    """
    Reparte `n` puntos en el cielo: capas de altura (1 capa con ≤3 puntos,
    2 capas con ≤6, 3 con más) y acimuts equiespaciados que alternan de
    sentido entre capas para minimizar el recorrido, como en el script del
    fabricante. Devuelve una lista de (altura, acimut) en grados.
    """
    n = max(1, int(n))
    capas = 1 if n <= 3 else (2 if n <= 6 else 3)
    if capas == 1:
        alturas = [(altura_maxima + altura_minima) / 2]
    else:
        alturas = list(np.linspace(altura_maxima, altura_minima, capas))
    por_capa = [n // capas + (1 if i < n % capas else 0) for i in range(capas)]
    puntos = []
    for i, (alt, k) in enumerate(zip(alturas, por_capa)):
        acimuts = [(acimut_inicial + 360.0 * j / k) % 360.0 for j in range(k)]
        if i % 2 == 1:
            acimuts.reverse()
        puntos += [(round(alt, 1), round(az, 1)) for az in acimuts]
    return puntos


class TrabajoAlineacion:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.paso = 0
        self.total_pasos = 0
        self.mensaje = ""
        self.resultado = ""       # completada | parcial | error | cancelado
        self.detalle = ""
        self.puntos = []          # [{"n", "alt", "az", "estado", "error_arcmin"}]
        self.sincronizados = 0
        self.fallidos = 0

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado,
                "paso": self.paso, "total_pasos": self.total_pasos,
                "mensaje": self.mensaje, "resultado": self.resultado, "detalle": self.detalle,
                "puntos": [dict(p) for p in self.puntos],
                "sincronizados": self.sincronizados, "fallidos": self.fallidos,
                "captura": estado_captura.como_dict(),
            }

    def _msg(self, texto):
        with self._lock:
            self.mensaje = texto
        log.info(texto)

    def cancelar(self):
        self._cancelar.set()
        estado_captura.cancelar.set()

    def _comprobar(self):
        if self._cancelar.is_set():
            raise CentradoCancelado()

    # ──────────────────────────────────────────────────────────────────
    def iniciar(self, camara, montura, conf=None, limpiar_sync=None, resolver=None, voz=None):
        """
        Arranca la alineación en un hilo propio.
          limpiar_sync(tel) -> (ok, msg)   función que envía ClearSyncTo (opcional)
          resolver(ruta_fits) -> (ra_deg, dec_deg) | None   (por defecto ASTAP)
          voz: módulo con decir(texto) (opcional)
        """
        conf = conf or leer_config()
        with self._lock:
            if self.en_curso:
                raise RuntimeError("Ya hay una alineación en curso")
            if camara.ocupado:
                raise RuntimeError("La cámara está ocupada con otra captura")
            self._reiniciar()
            self._cancelar.clear()
            self.en_curso = True
            self.mensaje = "Iniciando alineación automática..."
        hilo = threading.Thread(
            target=self._ejecutar, name="alineacion", daemon=True,
            args=(camara, montura, conf, limpiar_sync,
                  resolver or (lambda ruta: resolver_con_astap(ruta, conf["ruta_astap"])), voz))
        hilo.start()
        return hilo

    def _decir(self, voz, texto):
        if voz is not None:
            try:
                voz.decir(texto)
            except Exception:
                pass

    def _ejecutar(self, camara, montura, conf, limpiar_sync, resolver, voz):
        resultado, detalle = "error", ""
        lugar = EarthLocation(lat=conf["latitud"] * u.deg, lon=conf["longitud"] * u.deg,
                              height=conf["altitud"] * u.m)
        try:
            # ── Plan ─────────────────────────────────────────────────
            ra0, dec0 = coordenadas.desde_montura(*montura.ejecutar(lambda t: (t.RightAscension, t.Declination), timeout=10))
            inicial = SkyCoord(ra=ra0 * u.hourangle, dec=dec0 * u.deg)
            az0 = float(inicial.transform_to(AltAz(obstime=Time.now(), location=lugar)).az.deg)
            plan = plan_de_puntos(conf["puntos"], conf["altura_maxima"], conf["altura_minima"], az0)
            with self._lock:
                self.puntos = [{"n": 0, "alt": None, "az": None, "estado": "pendiente", "error_arcmin": None,
                                "nombre": "posición actual"}]
                self.puntos += [{"n": i + 1, "alt": alt, "az": az, "estado": "pendiente", "error_arcmin": None,
                                 "nombre": f"alt {alt:.0f}° az {az:.0f}°"} for i, (alt, az) in enumerate(plan)]
                self.total_pasos = len(self.puntos)
            self._decir(voz, f"Alineación automática de {len(plan) + 1} puntos")

            # ── 1. Borrar modelo anterior ─────────────────────────────
            if conf["limpiar_antes"] and limpiar_sync is not None:
                self._msg("Borrando sincronizaciones anteriores (ClearSyncTo)")
                ok, msg = montura.ejecutar(limpiar_sync, timeout=20)
                if not ok:
                    log.warning("ClearSyncTo no disponible: %s", msg)
                self._comprobar()

            # ── 2. Punto inicial: resolver donde apunta y sincronizar ──
            self._sincronizar_punto(0, camara, montura, conf, resolver, voz, mover_a=None)

            # ── 3. Recorrido ──────────────────────────────────────────
            for i, (alt, az) in enumerate(plan, start=1):
                self._comprobar()
                self._sincronizar_punto(i, camara, montura, conf, resolver, voz, mover_a=(alt, az, lugar))

            with self._lock:
                s, f = self.sincronizados, self.fallidos
            if s >= 2 and f == 0:
                resultado, detalle = "completada", f"Alineación completada con {s} puntos"
            elif s >= 2:
                resultado, detalle = "parcial", f"Alineación con {s} puntos; {f} posiciones no se pudieron resolver"
            else:
                resultado, detalle = "error", f"Solo {s} punto(s) sincronizado(s); alineación insuficiente"
        except (CentradoCancelado, CapturaCancelada):
            resultado, detalle = "cancelado", "Alineación cancelada por el usuario"
        except ErrorHardware as e:
            resultado, detalle = "error", f"Error de hardware: {e}"
        except Exception as e:
            log.exception("Error en la alineación automática")
            resultado, detalle = "error", f"Error inesperado: {e}"
        finally:
            with self._lock:
                self.resultado, self.detalle, self.mensaje = resultado, detalle, detalle
                self.en_curso, self.terminado = False, True
                errores = [p["error_arcmin"] for p in self.puntos if p["error_arcmin"] is not None]
            log.info("Alineación terminada: %s (%s) errores=%s", resultado, detalle, errores)
            self._decir(voz, detalle)

    def _marcar(self, idx, **cambios):
        with self._lock:
            self.puntos[idx].update(cambios)
            self.paso = idx + 1

    def _sincronizar_punto(self, idx, camara, montura, conf, resolver, voz, mover_a):
        nombre = self.puntos[idx]["nombre"]
        total = self.total_pasos
        try:
            if mover_a is not None:
                alt, az, lugar = mover_a
                self._marcar(idx, estado="moviendo")
                self._msg(f"Punto {idx + 1}/{total}: moviendo a {nombre}")
                self._decir(voz, f"Punto {idx + 1} de {total}")
                objetivo = SkyCoord(alt=alt * u.deg, az=az * u.deg,
                                    frame=AltAz(obstime=Time.now(), location=lugar)).icrs
                if abs(objetivo.dec.deg) > 85:
                    self._marcar(idx, estado="omitido", detalle="demasiado cerca del polo")
                    return
                self._mover(montura, *coordenadas.a_montura(objetivo.ra.hour, objetivo.dec.deg))
                self._msg(f"Punto {idx + 1}/{total}: estabilizando seguimiento ({conf['asentamiento_s']:.0f} s)")
                self._esperar(conf["asentamiento_s"])

            self._comprobar()
            self._marcar(idx, estado="exponiendo")
            self._msg(f"Punto {idx + 1}/{total}: exponiendo {conf['exposicion']:.0f} s")
            # Dónde cree estar la montura: pista para ASTAP y referencia del error
            ra_m, dec_m = montura.ejecutar(lambda t: (t.RightAscension, t.Declination), timeout=10)
            frame, px, py = camara.ejecutar(
                lambda cam: capturar_frame_con_pixel(cam, conf["exposicion"], conf["binning"],
                                                     descripcion=f"Alineación {idx + 1}/{total}"),
                timeout=conf["exposicion"] + 90)
            hdu = fits.PrimaryHDU(np.asarray(frame, dtype=np.uint16))
            hdu.header["BZERO"], hdu.header["BSCALE"] = 32768, 1
            # Sin FOCALLEN/XPIXSZ ASTAP usa el campo de sus ajustes y no resuelve nada
            pistas_platesolve(hdu.header, conf["focal"], px, py, conf["binning"], ra_h=ra_m, dec_deg=dec_m)
            hdu.writeto(RUTA_FITS_ALINEACION, overwrite=True)

            self._comprobar()
            self._marcar(idx, estado="resolviendo")
            self._msg(f"Punto {idx + 1}/{total}: resolviendo")
            solucion = resolver(RUTA_FITS_ALINEACION)
            if solucion is None:
                self._marcar(idx, estado="fallido", detalle="no resuelta")
                with self._lock:
                    self.fallidos += 1
                self._decir(voz, "No se ha podido resolver este punto")
                return
            ra_deg, dec_deg = solucion
            real = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)

            # Error de la montura en este punto (antes de sincronizar), comparando en J2000
            ra_i, dec_i = coordenadas.desde_montura(ra_m, dec_m)
            creia = SkyCoord(ra=ra_i * u.hourangle, dec=dec_i * u.deg)
            error = float(creia.separation(real).arcmin)

            self._marcar(idx, estado="sincronizando", error_arcmin=round(error, 2))
            ra_s, dec_s = coordenadas.a_montura(real.ra.hour, real.dec.deg)
            montura.ejecutar(lambda t: t.SyncToCoordinates(ra_s, dec_s), timeout=20)
            time.sleep(1.0)   # la app necesita un momento para procesar el sync
            self._marcar(idx, estado="ok")
            with self._lock:
                self.sincronizados += 1
            self._msg(f"Punto {idx + 1}/{total}: sincronizado (la montura se equivocaba {error:.1f}')")
        except (CentradoCancelado, CapturaCancelada):
            self._marcar(idx, estado="cancelado")
            raise
        except ErrorHardware:
            self._marcar(idx, estado="fallido", detalle="error de hardware")
            raise

    def _mover(self, montura, ra_h, dec_deg):
        def _slew(tel):
            tel.Tracking = True
            if getattr(tel, "CanSlewAsync", False):
                tel.SlewToCoordinatesAsync(ra_h, dec_deg)
            else:
                tel.SlewToCoordinates(ra_h, dec_deg)
            inicio = time.time()
            time.sleep(0.5)
            while tel.Slewing:
                if time.time() - inicio > 180:
                    raise ErrorHardware("La montura no terminó el movimiento en 180 s")
                if self._cancelar.is_set():
                    try:
                        tel.AbortSlew()
                    except Exception:
                        pass
                    raise CentradoCancelado()
                time.sleep(0.5)
        montura.ejecutar(_slew, timeout=200)

    def _esperar(self, segundos):
        fin = time.time() + segundos
        while time.time() < fin:
            self._comprobar()
            time.sleep(0.2)


trabajo_alineacion = TrabajoAlineacion()
