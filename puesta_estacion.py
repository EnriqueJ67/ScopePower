# puesta_estacion.py
#
# Asistente de puesta en estación para monturas ecuatoriales, al estilo del plugin
# «Three Point Polar Alignment» de NINA o del Seestar:
#
#   1. Medición: tres fotos resueltas con ASTAP girando solo el eje de AR entre ellas.
#      Con el seguimiento en marcha, los tres centros pasados a alt/az del lugar están
#      sobre un círculo alrededor del eje REAL de la montura: la normal del plano que
#      forman es ese eje. Comparado con el polo (altura = latitud, acimut 0 en el norte,
#      180 en el sur) sale el error en altura y en acimut.
#   2. Ajuste: el usuario toca los tornillos de altura y acimut mientras la app hace
#      fotos en bucle. Cada foto se compara con dónde debería estar el campo si el error
#      que queda fuera (r_alt, r_az): se resuelven esas dos incógnitas por mínimos
#      cuadrados (Gauss-Newton) y se le dice al usuario cuánto falta de cada tornillo.
#
# Convenciones (todo el álgebra en numpy; astropy solo para pasar RA/Dec a alt/az):
#   - Marco topocéntrico x = norte, y = este, z = cénit (dextrógiro). Acimut desde el
#     norte hacia el este, como astropy.
#   - rodrigues(v, k, θ) gira v alrededor de k por +θ con la regla de la mano derecha:
#     alrededor de z, +θ AUMENTA el acimut. El eje horizontal de la altura para un eje
#     polar de acimut A es w_A = (sin A, −cos A, 0) (al oeste si A = 0): girar alrededor
#     de w_A por +θ SUBE la altura del vector de acimut A.
#   - Estado del cuerpo de la montura e = (rot_alt, rot_az) en grados: su eje es
#     R(w_A0 girado rot_az, rot_alt) · R(z, rot_az) · polo. rot_alt > 0 = eje demasiado
#     alto; rot_az > 0 = eje girado hacia el este (norte) o hacia el oeste (sur).
#   - Para el usuario (a_instruccion): err_alt > 0 → «baja la montura»; err_az > 0 → eje
#     al ESTE del polo → «gira hacia el oeste». Vale igual en los dos hemisferios.

import os
import time
import math
import threading
import logging
import configparser

import numpy as np
from astropy.io import fits
from astropy.coordinates import SkyCoord, AltAz, EarthLocation
from astropy.time import Time
import astropy.units as u

from hardware import (capturar_frame_con_pixel, pistas_platesolve, ganancia_configurada,
                      asentamiento_montura, CapturaCancelada, ErrorHardware, estado_captura)
from centrado import resolver_con_astap, CentradoCancelado
import coordenadas
import config_inicial
import montura_ejes
from idioma import _, fmt_num

log = logging.getLogger("scopepower.puesta_estacion")

RUTA_FITS = os.path.join("static", "puesta_estacion_mono.fits")
RUTA_PREVIEW = os.path.join("static", "puesta_estacion_preview.jpg")

DEC_INICIO = 85.0          # |Dec| mayor: el tubo apunta al polo (posición de inicio) → apartar el eje Dec
APARTAR_DEG = 25.0         # cuánto se aparta
ALTURA_MINIMA = 15.0       # por debajo, la refracción y los árboles estropean la medida
DEC_MAXIMA = 85.0          # más cerca del polo el giro en AR apenas mueve el campo
FACTOR_FAILSAFE = 2.0      # tiempo máximo de un giro = distancia / velocidad × 2 + 5 s
REINTENTOS_RESOLVER = 3    # por foto, en la medición
FALLOS_AVISO, FALLOS_PARAR = 5, 10   # fotos sin resolver seguidas en el ajuste
MIN_SEPARACION_PUNTOS = 2.0          # grados entre fotos consecutivas de la medición
ESTE_OESTE_AVISO = 5.0               # grados: el campo al este/oeste exactos condiciona mal el ajuste
SILENCIO_VOZ_S = 8.0
OMEGA_SIDEREA = 360.0 / 86164.0905   # °/s: lo que gira el cielo (y la montura siguiendo) en el marco de la Tierra

_Z = np.array([0.0, 0.0, 1.0])


# ═════════════════════════════════════════════════════════════════════════════
# Álgebra (funciones puras)
# ═════════════════════════════════════════════════════════════════════════════
def vector_altaz(alt_deg, az_deg):
    """Vector unitario (norte, este, cénit) de una dirección alt/az en grados."""
    a, z = math.radians(alt_deg), math.radians(az_deg)
    return np.array([math.cos(a) * math.cos(z), math.cos(a) * math.sin(z), math.sin(a)])


def altaz_de_vector(v):
    """(alt, az) en grados de un vector (norte, este, cénit); el acimut en [0, 360)."""
    v = np.asarray(v, dtype=float)
    v = v / np.linalg.norm(v)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
    az = math.degrees(math.atan2(v[1], v[0])) % 360.0
    return alt, az


def rodrigues(v, k, angulo_deg):
    """Gira v alrededor del eje k (se normaliza) por `angulo_deg`, regla de la mano derecha."""
    k = np.asarray(k, dtype=float)
    k = k / np.linalg.norm(k)
    v = np.asarray(v, dtype=float)
    t = math.radians(angulo_deg)
    return v * math.cos(t) + np.cross(k, v) * math.sin(t) + k * np.dot(k, v) * (1.0 - math.cos(t))


def wrap180(grados):
    return (grados + 180.0) % 360.0 - 180.0


def eje_horizontal(az_deg):
    """Eje horizontal de la altura para un eje polar de acimut `az_deg`: girar por +θ lo sube."""
    a = math.radians(az_deg)
    return np.array([math.sin(a), -math.cos(a), 0.0])


def eje_desde_tres_puntos(v1, v2, v3, norte=True):
    """
    Eje de giro (unitario) del círculo que pasa por tres direcciones: la normal del plano
    (v2 − v1) × (v3 − v2), orientada hacia el polo del hemisferio (componente norte > 0
    en el norte, < 0 en el sur). ValueError si los puntos están alineados (la montura no giró).
    """
    v1, v2, v3 = (np.asarray(v, dtype=float) for v in (v1, v2, v3))
    n = np.cross(v2 - v1, v3 - v2)
    modulo = np.linalg.norm(n)
    if modulo < 1e-9:
        raise ValueError("los tres puntos están alineados: la montura no ha girado")
    n = n / modulo
    if (norte and n[0] < 0) or (not norte and n[0] > 0):
        n = -n
    return n


def error_del_eje(eje, latitud):
    """
    Estado del cuerpo de la montura (rot_alt, rot_az) en grados a partir del eje medido:
    rot_alt = altura del eje − |latitud|; rot_az = acimut del eje respecto al polo (0 norte, 180 sur).
    """
    alt_e, az_e = altaz_de_vector(eje)
    a0 = 0.0 if latitud >= 0 else 180.0
    return alt_e - abs(latitud), wrap180(az_e - a0)


def a_instruccion(rot_alt, rot_az, norte=True):
    """
    Lo que ve el usuario, en minutos de arco: (err_alt, err_az, total).
    err_alt > 0 → el eje está por encima del polo → «baja la montura».
    err_az > 0 → el eje está al ESTE del polo → «gira hacia el oeste». Igual en el sur.
    """
    err_alt = rot_alt * 60.0
    err_az = (rot_az if norte else -rot_az) * 60.0
    return err_alt, err_az, math.hypot(err_alt, err_az)


def posicion_prevista(v_ref, rot0, r, az_polo):
    """
    Dónde apuntaría el tubo si el cuerpo de la montura pasara del estado medido rot0 al
    estado r (lo que quede de error), siendo v_ref el apuntado medido. Exacto: se deshace
    la altura medida, se gira el acimut y se pone la altura que queda. Con r = rot0 no cambia.
    rot0 y r son (rot_alt, rot_az) en grados.
    """
    alt0, az0 = rot0
    alt_r, az_r = r
    w0 = rodrigues(eje_horizontal(az_polo), _Z, az0)
    wr = rodrigues(eje_horizontal(az_polo), _Z, az_r)
    v = rodrigues(v_ref, w0, -alt0)
    v = rodrigues(v, _Z, az_r - az0)
    return rodrigues(v, wr, alt_r)


def referencia_seguida(p_ref, eje, segundos):
    """
    Dónde apunta el tubo `segundos` después de apuntar a p_ref (marco de la Tierra) si la montura
    sigue a velocidad sidérea alrededor de SU eje real: gira alrededor de `eje` en el mismo sentido
    que el cielo. Con el eje en el polo coincide con el cielo; con error, el campo deriva, y esta es
    la deriva que hay que descontar en el ajuste (unos 2″/min por cada 15′ de error).
    """
    # Signo comprobado contra astropy en el __main__: positivo con la regla de la mano derecha
    return rodrigues(p_ref, eje, OMEGA_SIDEREA * segundos)


def separacion_arcsec(v1, v2):
    c = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600.0


def estimar_residuo(v_ref, v_obs, rot0, semilla, az_polo, paso_deg=5.0 / 3600.0, max_iter=20):
    """
    Error que queda (rot_alt, rot_az) tal que posicion_prevista(v_ref, rot0, r) = v_obs.
    Gauss-Newton con jacobiano numérico (3 ecuaciones, 2 incógnitas). `fiable` es falso si
    el ajuste no cierra (residuo > 30″) o el sistema está mal condicionado (campo al este
    u oeste exactos), y entonces no conviene usar el resultado como semilla siguiente.
    """
    r = np.array(semilla, dtype=float)
    v_obs = np.asarray(v_obs, dtype=float)
    J = np.zeros((3, 2))
    iteraciones = 0
    for iteraciones in range(1, max_iter + 1):
        f0 = posicion_prevista(v_ref, rot0, r, az_polo) - v_obs
        for j in range(2):
            rp = r.copy()
            rp[j] += paso_deg
            J[:, j] = (posicion_prevista(v_ref, rot0, rp, az_polo) - v_obs - f0) / paso_deg
        delta, *_resto = np.linalg.lstsq(J, -f0, rcond=None)
        r = r + delta
        if np.max(np.abs(delta)) < 0.01 / 3600.0:
            break
    sv = np.linalg.svd(J, compute_uv=False)
    cond = float(sv[0] / max(sv[-1], 1e-30))
    residuo = separacion_arcsec(posicion_prevista(v_ref, rot0, r, az_polo), v_obs)
    return {"rot_alt": float(r[0]), "rot_az": float(r[1]), "iteraciones": iteraciones,
            "residuo_arcsec": residuo, "cond": cond, "fiable": residuo < 30.0 and cond < 50.0}


def cerca_este_oeste(az_deg, umbral=ESTE_OESTE_AVISO):
    return min(abs(wrap180(az_deg - 90.0)), abs(wrap180(az_deg - 270.0))) <= umbral


def velocidad_admitida(rangos, v):
    """
    Velocidad (°/s, positiva) que acepta el driver para la pedida: la misma si algún rango
    de AxisRates la contiene, si no el máximo del rango más alto por debajo, o el mínimo
    de todos si ninguno llega. Sin rangos se devuelve la pedida.
    """
    v = abs(float(v))
    if not rangos:
        return v
    for lo, hi in rangos:
        if lo <= v <= hi:
            return v
    por_debajo = [hi for lo, hi in rangos if hi < v]
    if por_debajo:
        return max(por_debajo)
    return min(lo for lo, hi in rangos)


def distancia_ra(ra1_deg, ra2_deg):
    """Distancia angular en AR (grados, 0–180) con vuelta por 0/360."""
    d = abs(ra1_deg - ra2_deg) % 360.0
    return min(d, 360.0 - d)


def altaz_de_radec(ra_deg, dec_deg, t_unix, lugar):
    """(alt, az) en grados de una posición J2000 vista desde `lugar` en el instante t_unix (sin refracción)."""
    c = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    h = c.transform_to(AltAz(obstime=Time(t_unix, format="unix"), location=lugar))
    return float(h.alt.deg), float(h.az.deg)


def hora_siderea_deg(t_unix, longitud_deg):
    return float(Time(t_unix, format="unix").sidereal_time("apparent", longitude=longitud_deg * u.deg).deg)


# ═════════════════════════════════════════════════════════════════════════════
# Configuración
# ═════════════════════════════════════════════════════════════════════════════
def leer_config():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    p = cfg["PuestaEnEstacion"] if "PuestaEnEstacion" in cfg else {}
    c = cfg["Centrado"] if "Centrado" in cfg else {}
    d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}
    o = cfg["Observatorio"] if "Observatorio" in cfg else {}
    exp = str(p.get("exposicion", "")).strip()
    return {
        "distancia_grados": float(p.get("distancia_grados", 20)),
        "velocidad_eje": float(p.get("velocidad_eje", 2)),
        "tolerancia_arcmin": float(p.get("tolerancia_arcmin", 2)),
        "exposicion": float(exp) if exp else float(c.get("exposicion_centrado", 5.0)),
        "hacia_el_este": str(p.get("hacia_el_este", "true")).lower() == "true",
        "voz": str(p.get("voz", "true")).lower() == "true",
        "asentamiento_s": asentamiento_montura("PuestaEnEstacion"),
        "binning": int(d.get("binning_camara", 1)),
        "focal": float(d.get("focal_len", 250)),
        "ruta_astap": config_inicial.ruta_astap(),
        "latitud": float(o.get("latitud_observatorio", 0)),
        "longitud": float(o.get("longitud_observatorio", 0)),
        "altitud": float(o.get("altitud_observatorio", 0)),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Trabajo en hilo (mismo patrón que alineacion.TrabajoAlineacion)
# ═════════════════════════════════════════════════════════════════════════════
class TrabajoPuestaEstacion:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelar = threading.Event()
        self._terminar = threading.Event()
        self._repetir = threading.Event()
        self._montura = None
        self.antes_de_foto = None       # gancho: el vigilante del seguimiento (lo pone ScopePower.py)
        self.imagen_version = 0
        self._reiniciar()

    def _reiniciar(self):
        self.en_curso = False
        self.terminado = False
        self.fase = ""                  # comprobando | apartando_polo | midiendo | calculando | ajustando | fin
        self.subfase = ""               # moviendo | asentando | exponiendo | resolviendo
        self.paso = 0                   # foto de la medición en curso (1–3)
        self.mensaje = ""
        self.resultado = ""             # completada | error | cancelado
        self.detalle = ""
        self.hemisferio = "norte"
        self.latitud = 0.0
        self.tolerancia_arcmin = 2.0
        self._reiniciar_vuelta()

    def _reiniciar_vuelta(self):
        self.avance = None
        self.puntos = [{"n": i + 1, "estado": "pendiente", "ra": None, "dec": None, "alt": None, "az": None, "intentos": 0}
                       for i in range(3)]
        self.medicion = None
        self.ajuste = None
        self.imagen = None
        self.avisos = []

    def estado(self):
        with self._lock:
            return {
                "en_curso": self.en_curso, "terminado": self.terminado,
                "fase": self.fase, "subfase": self.subfase, "paso": self.paso, "total_pasos": 3,
                "mensaje": self.mensaje, "resultado": self.resultado, "detalle": self.detalle,
                "avance": dict(self.avance) if self.avance else None,
                "puntos": [dict(p) for p in self.puntos],
                "medicion": dict(self.medicion) if self.medicion else None,
                "ajuste": dict(self.ajuste) if self.ajuste else None,
                "imagen": dict(self.imagen) if self.imagen else None,
                "hemisferio": self.hemisferio, "latitud": self.latitud,
                "tolerancia_arcmin": self.tolerancia_arcmin,
                "avisos": [dict(a) for a in self.avisos],
                "captura": estado_captura.como_dict(),
            }

    # ── Estado ──────────────────────────────────────────────────────────
    def _msg(self, texto):
        with self._lock:
            self.mensaje = texto
        log.info(texto)

    def _fase(self, fase, subfase=""):
        with self._lock:
            self.fase, self.subfase = fase, subfase

    def _subfase(self, subfase):
        with self._lock:
            self.subfase = subfase

    def _marcar(self, idx, **cambios):
        with self._lock:
            self.puntos[idx].update(cambios)
            self.paso = idx + 1

    def _avisar(self, codigo, texto):
        with self._lock:
            self.avisos = [a for a in self.avisos if a["codigo"] != codigo] + [{"codigo": codigo, "texto": texto}]
        log.info("Puesta en estación, aviso %s: %s", codigo, texto)

    def _quitar_aviso(self, codigo):
        with self._lock:
            self.avisos = [a for a in self.avisos if a["codigo"] != codigo]

    def _decir(self, voz, texto):
        if voz is not None:
            try:
                voz.decir(texto)
            except Exception:
                pass

    # ── Órdenes desde la web ───────────────────────────────────────────
    def cancelar(self):
        self._cancelar.set()
        estado_captura.cancelar.set()
        # Por si el hilo del trabajo ya no está: una parada más de los ejes, en cola tras lo que haya
        m = self._montura
        if m is not None:
            try:
                m.ejecutar_async(lambda t: (t.MoveAxis(0, 0.0), t.MoveAxis(1, 0.0)), conectar=False)
            except Exception:
                pass

    def terminar(self):
        self._terminar.set()

    def repetir(self):
        self._repetir.set()

    def _comprobar(self):
        if self._cancelar.is_set():
            raise CentradoCancelado()

    def _esperar(self, segundos):
        fin = time.time() + segundos
        while time.time() < fin:
            self._comprobar()
            time.sleep(0.2)

    # ── Arranque ────────────────────────────────────────────────────────
    def iniciar(self, camara, montura, conf=None, resolver=None, capturar=None, voz=None, modo_montura=None):
        """
        Arranca el asistente en un hilo propio.
          resolver(ruta_fits) -> (ra_deg, dec_deg) | None          (por defecto ASTAP)
          capturar(descripcion, ra_pista_h, dec_pista_deg) -> (ruta_fits, t_mitad_unix, frame)
                                                                   (por defecto la cámara real)
          voz: módulo con decir(texto) (opcional); modo_montura: "ecuatorial" | "ecuatorial_alemana"
        """
        conf = conf or leer_config()
        with self._lock:
            if self.en_curso:
                raise RuntimeError(_("Ya hay una puesta en estación en curso"))
            if camara.ocupado:
                raise RuntimeError(_("La cámara está ocupada con otra captura"))
            self._reiniciar()
            self._cancelar.clear()
            self._terminar.clear()
            self._repetir.clear()
            self.en_curso = True
            self.hemisferio = "norte" if conf["latitud"] >= 0 else "sur"
            self.latitud = conf["latitud"]
            self.tolerancia_arcmin = conf["tolerancia_arcmin"]
            self.mensaje = _("Iniciando la puesta en estación...")
        self._montura = montura
        hilo = threading.Thread(
            target=self._ejecutar, name="puesta_estacion", daemon=True,
            args=(camara, montura, conf,
                  resolver or (lambda ruta: resolver_con_astap(ruta, conf["ruta_astap"])),
                  capturar or (lambda descripcion, ra_h, dec: self._capturar_real(camara, conf, descripcion, ra_h, dec)),
                  voz, modo_montura))
        hilo.start()
        return hilo

    def _ejecutar(self, camara, montura, conf, resolver, capturar, voz, modo_montura):
        resultado, detalle = "error", ""
        lugar = EarthLocation(lat=conf["latitud"] * u.deg, lon=conf["longitud"] * u.deg, height=conf["altitud"] * u.m)
        try:
            while True:
                with self._lock:
                    self._reiniciar_vuelta()
                sentido = self._comprobar_inicio(montura, conf, modo_montura)
                medicion = self._medir(montura, conf, resolver, capturar, voz, lugar, sentido)
                self._ajustar(montura, conf, resolver, capturar, voz, lugar, medicion)
                if not self._repetir.is_set():
                    break
                self._repetir.clear()
            resultado, detalle = "completada", _("Puesta en estación terminada")
        except (CentradoCancelado, CapturaCancelada):
            resultado, detalle = "cancelado", _("Puesta en estación cancelada")
        except ErrorHardware as e:
            resultado, detalle = "error", str(e)
        except Exception as e:
            log.exception("Error en la puesta en estación")
            resultado, detalle = "error", _("Error inesperado: {e}", e=e)
        finally:
            # Pase lo que pase, la montura se queda siguiendo y con los ejes parados
            try:
                montura.ejecutar(self._parar_y_seguir, timeout=20)
            except Exception as e:
                log.warning("No se pudo dejar la montura siguiendo al terminar: %s", e)
            with self._lock:
                self.fase, self.subfase = "fin", ""
                self.resultado, self.detalle, self.mensaje = resultado, detalle, detalle
                self.en_curso, self.terminado = False, True
            log.info("Puesta en estación terminada: %s (%s)", resultado, detalle)
            self._decir(voz, detalle)

    @staticmethod
    def _parar_y_seguir(tel):
        for eje in (0, 1):
            try:
                if tel.CanMoveAxis(eje):
                    tel.MoveAxis(eje, 0.0)
            except Exception:
                pass
        try:
            tel.Tracking = True
        except Exception:
            pass

    # ── Comprobaciones previas ──────────────────────────────────────────
    def _comprobar_inicio(self, montura, conf, modo_montura):
        """Montura lista y sentido del giro (+1 este, −1 oeste). Aparta el tubo del polo si hace falta."""
        self._fase("comprobando")
        self._msg(_("Comprobando la montura"))

        def _leer(tel):
            try:
                aparcada = bool(tel.AtPark)
            except Exception:
                aparcada = False
            return aparcada, bool(tel.CanMoveAxis(0)), tel.RightAscension, tel.Declination

        aparcada, puede_mover, ra_m, dec_m = montura.ejecutar(_leer, timeout=15)
        if aparcada:
            raise ErrorHardware(_("La montura está aparcada: desapárcala antes de empezar"))
        if not puede_mover:
            raise ErrorHardware(_("El driver de la montura no permite mover el eje de AR (MoveAxis): no se puede medir"))
        ra_h, dec = coordenadas.desde_montura(ra_m, dec_m)
        if abs(dec) > DEC_INICIO:
            self._apartar_del_polo(montura, conf, dec)
            ra_m, dec_m = montura.ejecutar(lambda t: (t.RightAscension, t.Declination), timeout=10)
            ra_h, dec = coordenadas.desde_montura(ra_m, dec_m)

        sentido = 1 if conf["hacia_el_este"] else -1
        if modo_montura == "ecuatorial_alemana":
            # Una alemana da la vuelta (meridian flip) al cruzar el meridiano: si el recorrido de
            # la medición lo cruzaría, se gira hacia el otro lado
            ha = wrap180(hora_siderea_deg(time.time(), conf["longitud"]) - ra_h * 15.0)   # > 0 = oeste del meridiano
            recorrido = 2 * conf["distancia_grados"] + 5.0
            if sentido > 0 and 0 < ha < recorrido:
                sentido = -1
                self._avisar("meridiano", _("Se gira hacia el oeste para no cruzar el meridiano"))
            elif sentido < 0 and -recorrido < ha < 0:
                sentido = 1
                self._avisar("meridiano", _("Se gira hacia el este para no cruzar el meridiano"))
        return sentido

    def _apartar_del_polo(self, montura, conf, dec0):
        self._fase("apartando_polo", "moviendo")
        self._msg(_("El tubo apunta al polo: apartando el eje de Dec {d}°", d=round(APARTAR_DEG)))
        # MoveAxis(1, +v) sube la Dec: para alejarse del polo norte hay que bajarla, del sur subirla.
        # Si no se aleja (driver con el signo al revés), _girar_eje prueba el otro sentido.
        v = -conf["velocidad_eje"] if dec0 > 0 else conf["velocidad_eje"]
        self._girar_eje(montura, 1, v, APARTAR_DEG,
                        medir_inicio=lambda tel: 90.0 - abs(tel.Declination),
                        medir=lambda tel, d0: (90.0 - abs(tel.Declination)) - d0,
                        reintentar_sentido=True)

    # ── Giro de un eje con MoveAxis ─────────────────────────────────────
    def _girar_eje(self, montura, eje, v, objetivo, medir_inicio, medir, reintentar_sentido=False):
        """
        MoveAxis(eje, v) hasta que `medir(tel, ref)` (grados recorridos desde `medir_inicio(tel)`)
        llegue a `objetivo`. Todo en una sola llamada al hilo COM, con la parada en el finally: pase
        lo que pase (cancelación, error, tiempo agotado) el eje se para y vuelve el seguimiento.
        Devuelve (referencia inicial, recorrido final).
        """
        timeout_hilo = objetivo / max(abs(v), 0.05) * FACTOR_FAILSAFE + 5.0

        def _en_hilo(tel):
            va = velocidad_admitida(montura_ejes.rangos_axisrates(tel, eje), v) * (1 if v > 0 else -1)
            limite = objetivo / max(abs(va), 0.05) * FACTOR_FAILSAFE + 5.0
            ref = medir_inicio(tel)
            recorrido = 0.0
            cambiado = False
            try:
                log.info("Puesta en estación: MoveAxis(%d, %+.2f °/s) hasta %.1f° (referencia %.3f)", eje, va, objetivo, ref)
                tel.MoveAxis(eje, va)
                t0 = time.time()
                while True:
                    time.sleep(0.2)
                    recorrido = float(medir(tel, ref))
                    with self._lock:
                        self.avance = {"recorrido": round(max(recorrido, 0.0), 2), "objetivo": objetivo}
                    if recorrido >= objetivo:
                        break
                    if reintentar_sentido and not cambiado and time.time() - t0 > 3.0 and recorrido < 0.5:
                        # No se aleja: el signo de este driver va al revés
                        log.info("Puesta en estación: el eje %d no avanza con %+.2f °/s, se prueba el otro sentido", eje, va)
                        tel.MoveAxis(eje, 0.0)
                        time.sleep(0.5)
                        va, cambiado = -va, True
                        tel.MoveAxis(eje, va)
                        t0 = time.time()
                        continue
                    if time.time() - t0 > limite:
                        raise ErrorHardware(_("La montura no ha recorrido {d}° en {s} s: comprueba que se mueve",
                                              d=fmt_num(objetivo, 0), s=round(limite)))
                    if self._cancelar.is_set():
                        raise CentradoCancelado()
            finally:
                for intento in range(3):
                    try:
                        tel.MoveAxis(eje, 0.0)
                        break
                    except Exception as e:
                        log.warning("Puesta en estación: MoveAxis(%d, 0) falló (%d): %s", eje, intento + 1, e)
                        time.sleep(0.5)
                fin = time.time() + 30
                try:
                    while tel.Slewing and time.time() < fin:
                        time.sleep(0.3)
                except Exception:
                    pass
                try:
                    tel.Tracking = True   # SynScan apaga el seguimiento con MoveAxis
                except Exception as e:
                    log.warning("Puesta en estación: no se pudo reactivar el seguimiento: %s", e)
                log.info("Puesta en estación: eje %d parado tras %.2f°", eje, recorrido)
            return ref, recorrido

        return montura.ejecutar(_en_hilo, timeout=timeout_hilo + 60)

    # ── Fotos ───────────────────────────────────────────────────────────
    def _foto_resuelta(self, montura, conf, resolver, capturar, descripcion, reintentos, idx=None):
        """Foto + plate solving: (ra_deg, dec_deg, t_unix) J2000 del centro, o None si no se resolvió."""
        for intento in range(1, reintentos + 1):
            self._comprobar()
            if self.antes_de_foto is not None:
                try:
                    self.antes_de_foto()
                except Exception as e:
                    log.warning("Puesta en estación: vigilante del seguimiento: %s", e)

            def _lista(tel):
                try:
                    if not tel.Tracking:
                        tel.Tracking = True
                except Exception:
                    pass
                fin = time.time() + 30
                while getattr(tel, "Slewing", False) and time.time() < fin:
                    time.sleep(0.3)
                return tel.RightAscension, tel.Declination

            ra_m, dec_m = montura.ejecutar(_lista, timeout=45)
            if idx is not None:
                self._marcar(idx, estado="exponiendo", intentos=intento)
            self._subfase("exponiendo")
            ruta, t_mid, _frame = capturar(descripcion, ra_m, dec_m)
            self._comprobar()
            if idx is not None:
                self._marcar(idx, estado="resolviendo")
            self._subfase("resolviendo")
            solucion = resolver(ruta)
            if solucion is not None:
                return float(solucion[0]), float(solucion[1]), float(t_mid)
            log.info("Puesta en estación: %s sin resolver (intento %d/%d)", descripcion, intento, reintentos)
        return None

    def _capturar_real(self, camara, conf, descripcion, ra_h, dec_deg):
        t0 = time.time()
        (frame, px, py), sensor = camara.ejecutar(
            lambda cam: (capturar_frame_con_pixel(cam, conf["exposicion"], conf["binning"],
                                                  descripcion=descripcion, ganancia=ganancia_configurada()),
                         getattr(cam, "SensorType", 0)),
            timeout=conf["exposicion"] + 90)
        t_mid = t0 + conf["exposicion"] / 2.0
        hdu = fits.PrimaryHDU(np.asarray(frame, dtype=np.uint16))
        hdu.header["BZERO"], hdu.header["BSCALE"] = 32768, 1
        # Sin FOCALLEN/XPIXSZ ASTAP usa el campo de sus ajustes y no resuelve nada
        pistas_platesolve(hdu.header, conf["focal"], px, py, conf["binning"], ra_h=ra_h, dec_deg=dec_deg)
        hdu.writeto(RUTA_FITS, overwrite=True)
        self._guardar_miniatura(frame, sensor)
        return RUTA_FITS, t_mid, frame

    def _guardar_miniatura(self, frame, sensor_type):
        """JPG pequeño de la última foto para el modal (como centrado._guardar_preview). Nunca interrumpe."""
        try:
            import cv2
            datos = np.asarray(frame)
            patron = {2: "RGGB", 3: "CMYG", 4: "CMYG2", 5: "LRGB"}.get(sensor_type)
            if datos.ndim == 2 and patron:
                from AutoStrechSTF import miniatura_cfa_rgb
                mini = miniatura_cfa_rgb(datos, patron, ancho=600)
                mini = cv2.cvtColor(mini, cv2.COLOR_RGB2BGR)
            else:
                if datos.ndim == 3:
                    datos = datos.mean(axis=2)
                d = datos.astype(np.float32)
                alto = max(1, int(d.shape[0] * 600 / d.shape[1]))
                d = cv2.resize(d, (600, alto), interpolation=cv2.INTER_AREA)
                lo, hi = np.percentile(d, (0.5, 99.7))
                mini = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
                mini = (np.sqrt(mini) * 255).astype(np.uint8)
            os.makedirs(os.path.dirname(RUTA_PREVIEW), exist_ok=True)
            cv2.imwrite(RUTA_PREVIEW, mini, [cv2.IMWRITE_JPEG_QUALITY, 82])
            with self._lock:
                self.imagen_version += 1
                self.imagen = {"version": self.imagen_version, "hora": time.strftime("%H:%M:%S")}
        except Exception as e:
            log.warning("No se pudo generar la vista previa de la puesta en estación: %s", e)

    # ── Medición de tres puntos ─────────────────────────────────────────
    def _medir(self, montura, conf, resolver, capturar, voz, lugar, sentido):
        norte = conf["latitud"] >= 0
        az_polo = 0.0 if norte else 180.0
        self._fase("midiendo")
        self._decir(voz, _("Midiendo el eje de la montura con tres fotos"))
        puntos = []
        for i in range(3):
            self._comprobar()
            if i > 0:
                self._marcar(i, estado="moviendo")
                self._subfase("moviendo")
                self._msg(_("Girando el eje de AR {d}° para la foto {n}", d=fmt_num(conf["distancia_grados"], 0), n=i + 1))
                self._girar_eje(montura, 0, sentido * conf["velocidad_eje"], conf["distancia_grados"],
                                medir_inicio=lambda tel: tel.RightAscension * 15.0,
                                medir=lambda tel, ra0: distancia_ra(ra0, tel.RightAscension * 15.0))
                with self._lock:
                    self.avance = None
                self._marcar(i, estado="asentando")
                self._subfase("asentando")
                self._msg(_("Estabilizando el seguimiento ({s} s)", s=round(conf["asentamiento_s"])))
                self._esperar(conf["asentamiento_s"])
            self._msg(_("Foto {n} de 3", n=i + 1))
            solucion = self._foto_resuelta(montura, conf, resolver, capturar,
                                           _("Puesta en estación {n}/3", n=i + 1), REINTENTOS_RESOLVER, idx=i)
            if solucion is None:
                self._marcar(i, estado="fallido")
                raise ErrorHardware(_("No se ha podido resolver la foto {n}: comprueba el enfoque, la exposición y que se ven estrellas", n=i + 1))
            ra, dec, t = solucion
            alt, az = altaz_de_radec(ra, dec, t, lugar)
            if i == 0:
                if alt < ALTURA_MINIMA:
                    raise ErrorHardware(_("El telescopio apunta demasiado bajo ({alt}°): apúntalo a más de {minimo}° de altura",
                                          alt=fmt_num(alt, 0), minimo=fmt_num(ALTURA_MINIMA, 0)))
                if abs(dec) > DEC_MAXIMA:
                    raise ErrorHardware(_("El telescopio apunta al polo (Dec {dec}°): apártalo unos 20° con el pad",
                                          dec=fmt_num(dec, 0)))
            if puntos and separacion_arcsec(vector_altaz(*puntos[-1][3:5]), vector_altaz(alt, az)) < MIN_SEPARACION_PUNTOS * 3600:
                raise ErrorHardware(_("La montura no se ha movido entre la foto {a} y la {b}: comprueba que gira el eje de AR", a=i, b=i + 1))
            puntos.append((ra, dec, t, alt, az))
            self._marcar(i, estado="ok", ra=round(ra, 4), dec=round(dec, 4), alt=round(alt, 2), az=round(az, 2))
            log.info("Puesta en estación: foto %d resuelta RA %.4f Dec %.4f → alt %.3f az %.3f", i + 1, ra, dec, alt, az)

        self._fase("calculando")
        self._msg(_("Calculando el eje de la montura"))
        vectores = [vector_altaz(p[3], p[4]) for p in puntos]
        try:
            eje = eje_desde_tres_puntos(*vectores, norte=norte)
        except ValueError:
            raise ErrorHardware(_("Las tres fotos están alineadas: la montura no ha girado alrededor de su eje"))
        rot_alt, rot_az = error_del_eje(eje, conf["latitud"])
        err_alt, err_az, total = a_instruccion(rot_alt, rot_az, norte)
        eje_alt, eje_az = altaz_de_vector(eje)
        medicion = {"err_alt_arcmin": round(err_alt, 2), "err_az_arcmin": round(err_az, 2), "total_arcmin": round(total, 2),
                    "eje_alt": round(eje_alt, 3), "eje_az": round(eje_az, 3), "hora": time.strftime("%H:%M:%S")}
        with self._lock:
            self.medicion = medicion
        log.info("Puesta en estación: eje alt %.3f az %.3f → error altura %+.2f' acimut %+.2f' (total %.2f')",
                 eje_alt, eje_az, err_alt, err_az, total)
        self._msg(_("Medición terminada: error de {t}′", t=fmt_num(total, 1)))
        self._decir(voz, self._frase_medicion(err_alt, err_az))
        # Referencia del ajuste: el apuntado de la foto 3 en el marco de la Tierra, su instante y el
        # eje real; con ellos referencia_seguida() sabe dónde estará el tubo en cada foto del ajuste
        return {"p3": vectores[-1], "t3": puntos[-1][2], "eje": eje,
                "rot0": (rot_alt, rot_az), "norte": norte, "az_polo": az_polo}

    @staticmethod
    def _frase_medicion(err_alt, err_az):
        alt = (_("{v} minutos por encima del polo", v=fmt_num(abs(err_alt), 1)) if err_alt > 0
               else _("{v} minutos por debajo del polo", v=fmt_num(abs(err_alt), 1)))
        az = (_("{v} minutos al este", v=fmt_num(abs(err_az), 1)) if err_az > 0
              else _("{v} minutos al oeste", v=fmt_num(abs(err_az), 1)))
        return _("El eje está {alt} y {az}", alt=alt, az=az)

    @staticmethod
    def _frase_ajuste(err_alt, err_az, dentro):
        if dentro:
            return _("Dentro de la tolerancia: la montura está en estación")
        partes = []
        if abs(err_alt) >= 0.1:
            partes.append(_("baja {v} minutos", v=fmt_num(abs(err_alt), 1)) if err_alt > 0
                          else _("sube {v} minutos", v=fmt_num(abs(err_alt), 1)))
        if abs(err_az) >= 0.1:
            partes.append(_("gira al oeste {v} minutos", v=fmt_num(abs(err_az), 1)) if err_az > 0
                          else _("gira al este {v} minutos", v=fmt_num(abs(err_az), 1)))
        return _(" y ").join(partes) if partes else _("Bien")

    # ── Ajuste en bucle ─────────────────────────────────────────────────
    def _ajustar(self, montura, conf, resolver, capturar, voz, lugar, medicion):
        rot0 = medicion["rot0"]
        norte, az_polo = medicion["norte"], medicion["az_polo"]
        self._fase("ajustando")
        self._msg(_("Ajusta los tornillos de altura y acimut; la app va midiendo"))
        semilla = np.array(rot0, dtype=float)
        n = 0
        fallos = 0
        ultima_voz = (None, None, 0.0)
        while True:
            if self._terminar.is_set() or self._repetir.is_set():
                return
            self._comprobar()
            solucion = self._foto_resuelta(montura, conf, resolver, capturar, _("Ajuste {n}", n=n + 1), 1)
            if self._terminar.is_set() or self._repetir.is_set():
                return
            if solucion is None:
                fallos += 1
                with self._lock:
                    if self.ajuste:
                        self.ajuste["fallos_seguidos"] = fallos
                if fallos >= FALLOS_PARAR:
                    raise ErrorHardware(_("{n} fotos seguidas sin resolver: comprueba el enfoque y que la montura está quieta al disparar", n=fallos))
                if fallos >= FALLOS_AVISO:
                    self._avisar("fallos", _("{n} fotos seguidas sin resolver: para de tocar la montura mientras expone", n=fallos))
                continue
            fallos = 0
            self._quitar_aviso("fallos")
            n += 1
            ra, dec, t = solucion
            v_ref = referencia_seguida(medicion["p3"], medicion["eje"], t - medicion["t3"])
            alt_o, az_o = altaz_de_radec(ra, dec, t, lugar)
            v_obs = vector_altaz(alt_o, az_o)
            est = estimar_residuo(v_ref, v_obs, rot0, semilla, az_polo)
            if est["fiable"]:
                semilla = np.array([est["rot_alt"], est["rot_az"]])
                self._quitar_aviso("no_fiable")
            else:
                self._avisar("no_fiable", _("La estimación no es fiable en esta posición del cielo: mueve el tubo lejos del este y del oeste"))
            err_alt, err_az, total = a_instruccion(est["rot_alt"], est["rot_az"], norte)
            dentro = total <= conf["tolerancia_arcmin"]
            este_oeste = cerca_este_oeste(az_o)
            if este_oeste:
                self._avisar("este_oeste", _("El campo está casi al este u oeste: la medida del acimut es poco precisa"))
            else:
                self._quitar_aviso("este_oeste")
            ajuste = {"n": n, "err_alt_arcmin": round(err_alt, 2), "err_az_arcmin": round(err_az, 2),
                      "total_arcmin": round(total, 2), "dentro_tolerancia": dentro, "fiable": est["fiable"],
                      "cond": round(est["cond"], 1), "residuo_arcsec": round(est["residuo_arcsec"], 1),
                      "fallos_seguidos": 0, "cerca_este_oeste": este_oeste, "hora": time.strftime("%H:%M:%S")}
            with self._lock:
                self.ajuste = ajuste
            log.info("Puesta en estación: ajuste %d → altura %+.2f' acimut %+.2f' total %.2f' (residuo %.1f\", cond %.1f, fiable %s)",
                     n, err_alt, err_az, total, est["residuo_arcsec"], est["cond"], est["fiable"])
            if conf["voz"] and voz is not None:
                v_alt, v_az, cuando = ultima_voz
                cambio = (v_alt is None or abs(err_alt - v_alt) > 0.5 or abs(err_az - v_az) > 0.5
                          or dentro != (math.hypot(v_alt, v_az) <= conf["tolerancia_arcmin"]))
                if cambio and time.time() - cuando >= SILENCIO_VOZ_S:
                    self._decir(voz, self._frase_ajuste(err_alt, err_az, dentro))
                    ultima_voz = (err_alt, err_az, time.time())


trabajo_puesta_estacion = TrabajoPuestaEstacion()


# ═════════════════════════════════════════════════════════════════════════════
# Autocomprobación del álgebra:  python puesta_estacion.py
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    X, Y = np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    assert np.allclose(rodrigues(X, _Z, 90), Y), "girar alrededor del cénit por +90° lleva el norte al este"
    assert np.allclose(rodrigues(X, eje_horizontal(0), 90), _Z), "girar alrededor de w_0 por +90° sube el norte al cénit"
    assert np.allclose(vector_altaz(*altaz_de_vector(vector_altaz(33, 210))), vector_altaz(33, 210))

    # Tres puntos de un círculo alrededor de un eje conocido recuperan el eje
    lat = 40.0
    eje_real = vector_altaz(lat + 0.25, 0.0 - 0.1667)      # +15' en altura, −10' en acimut
    p0 = vector_altaz(45.0, 20.0)
    v = [rodrigues(p0, eje_real, ang) for ang in (0, 20, 40)]
    eje = eje_desde_tres_puntos(*v, norte=True)
    assert separacion_arcsec(eje, eje_real) < 0.01, separacion_arcsec(eje, eje_real)
    rot_alt, rot_az = error_del_eje(eje, lat)
    assert abs(rot_alt * 60 - 15) < 0.01 and abs(rot_az * 60 + 10) < 0.01, (rot_alt * 60, rot_az * 60)
    err_alt, err_az, total = a_instruccion(rot_alt, rot_az, True)
    assert err_alt > 0 and err_az < 0, "eje por encima → baja; eje al oeste → gira al este"

    # Sur: el eje se orienta hacia el polo sur y la instrucción de acimut se invierte
    eje_sur = vector_altaz(33.0 + 0.25, 180.0 + 0.1667)   # eje girado hacia el oeste del polo sur
    v = [rodrigues(vector_altaz(40.0, 200.0), eje_sur, ang) for ang in (0, 20, 40)]
    rot_alt, rot_az = error_del_eje(eje_desde_tres_puntos(*v, norte=False), -33.0)
    err_alt, err_az, total = a_instruccion(rot_alt, rot_az, False)
    assert abs(err_alt - 15) < 0.01 and abs(err_az + 10) < 0.01, (err_alt, err_az)

    # Estimador: el cuerpo pasa de e0 a r y la foto nueva delata r
    e0 = (0.25, -0.1667)
    v_ref = vector_altaz(50.0, 10.0)
    for r_real in ((0.0, 0.0), (0.1, -0.05), (0.25, -0.1667), (-0.3, 0.4)):
        v_obs = posicion_prevista(v_ref, e0, r_real, 0.0)
        est = estimar_residuo(v_ref, v_obs, e0, e0, 0.0)
        assert abs(est["rot_alt"] - r_real[0]) * 3600 < 0.1 and abs(est["rot_az"] - r_real[1]) * 3600 < 0.1, est
        assert est["fiable"], est
    assert np.allclose(posicion_prevista(v_ref, e0, e0, 0.0), v_ref)
    assert cerca_este_oeste(92) and not cerca_este_oeste(120)

    # El sentido del giro sidéreo: una estrella vista 10 minutos después está donde dice astropy
    lugar = EarthLocation(lat=40.0 * u.deg, lon=-3.7 * u.deg, height=650 * u.m)
    t0 = Time("2026-10-22T22:00:00").unix
    v_antes = vector_altaz(*altaz_de_radec(83.8, 22.0, t0, lugar))
    v_despues = vector_altaz(*altaz_de_radec(83.8, 22.0, t0 + 600, lugar))
    v_girada = referencia_seguida(v_antes, vector_altaz(40.0, 0.0), 600)
    assert separacion_arcsec(v_girada, v_despues) < 5.0, separacion_arcsec(v_girada, v_despues)
    assert velocidad_admitida([(0.0, 4.0)], 2) == 2 and velocidad_admitida([(0.0, 1.0)], 2) == 1.0 and velocidad_admitida([], 3) == 3
    assert distancia_ra(350, 10) == 20 and distancia_ra(10, 30) == 20
    print("✅ puesta_estacion: álgebra correcta")
