# correccion_ejes.py
#
# Corrección pequeña del apuntado moviendo los ejes a velocidad baja (MoveAxis),
# en vez de con un GoTo. La usan el guiado por el disco del Sol o la Luna y el
# dithering del apilado (dither.py).
#
# Por qué: un GoTo corto en la AZ GoTo no es corto. La montura remata todo GoTo
# acercándose siempre por el mismo lado de cada eje (compensación de holgura):
# medido con la Luna real el 21-oct-2026, una corrección de 2′ hizo que un eje se
# fuera unos 25′ (casi un diámetro lunar), parase 2 s y volviera; trece segundos
# de bandazo que sacan el disco del encuadre del vivo y estropean el vídeo que se
# esté grabando. Con MoveAxis el eje recorre justo lo que se le pide.
#
# Cómo: el destino se calcula igual que en el GoTo (desplazamiento (du, dv) en el
# plano tangente horizontal desde donde apunta la montura, pasado a AR/Dec) y se
# va a él en bucle cerrado con los codificadores de la propia montura: se mueven
# los dos ejes a la vez durante al menos un segundo (así la latencia del driver
# pesa poco), se paran, se vuelve a leer dónde apunta y se repite hasta quedar a
# menos de TOLERANCIA_ARCMIN o agotar los pasos. El error se mide en los ejes de la
# montura: acimut y altura en una altacimutal, AR y Dec en una ecuatorial
# (AlignmentMode de ASCOM). El sentido de cada eje se aprende solo: si en dos pasos
# seguidos el error ha crecido, ese eje va al revés y se invierte su signo (y se guarda
# en datos/ para la próxima vez). Si la montura no se mueve en dos pasos, se lanza
# ErrorCorreccion y quien llama puede volver al GoTo.
#
# Lo que hace cada eje se mide descontando el cielo: SynScan apaga el seguimiento
# mientras MoveAxis mueve, y en un dither de medio minuto de arco el cielo se va casi
# tanto como lo que se pide (unos 8″/s con el Doble Cúmulo subiendo, el 29-oct-2026).
# Sin descontarlo, y con un primer paso que no arranca, cada dither invertía un signo
# y lo volvía a invertir, o acababa en un GoTo.
#
# La parada es sagrada: un MoveAxis sin su parada deja la montura girando (una
# parada perdida en sept 2026 giró la montura 39°). Se envía siempre, también
# cuando algo falla, con reintentos, y se restaura el seguimiento que hubiera.

import os
import json
import math
import time
import logging

from astropy.coordinates import SkyCoord, AltAz
from astropy.time import Time
import astropy.units as u

from idioma import _
import coordenadas
import montura_ejes
import apuntado_teorico

log = logging.getLogger("scopepower.correccion_ejes")

VELOCIDAD_DEFECTO = 0.1       # °/s como máximo por eje ([FotoDia] guiado_velocidad_deg_s; 0 = usar GoTo)
DURACION_MINIMA_S = 1.0       # cada paso dura al menos esto: la latencia del driver (0,1–0,3 s) pesa menos
DURACION_MAXIMA_S = 10.0      # y como mucho esto (a la velocidad máxima: 1° por paso con 0,1 °/s)
TOLERANCIA_ARCMIN = 0.3       # se da por corregido por debajo de esto
PASOS_MAXIMOS = 4
REINTENTOS_PARADA = 3
ESPERA_QUIETA_S = 5.0         # tiempo máximo a que el driver deje de decir Slewing tras parar
ASENTAMIENTO_S = 0.5          # tras parar, antes de leer los codificadores
RUTA_SIGNOS = os.path.join("datos", "signos_correccion_ejes.json")
EJES = {"altaz": ("az", "alt"), "eq": ("ra", "dec")}
NOMBRE_EJE = {"az": "acimut", "alt": "altura", "ra": "AR", "dec": "Dec"}


class ErrorCorreccion(Exception):
    """La corrección por ejes no se pudo hacer (la montura no se mueve, no se pudo parar...)."""


def modo_montura(tel):
    """'altaz' o 'eq' según AlignmentMode de ASCOM (0 altacimutal, 1 polar, 2 alemana)."""
    try:
        return "altaz" if int(tel.AlignmentMode) == 0 else "eq"
    except Exception:
        return "altaz"


# ─────────────────────────────────────────────────────────────────────────────
#  Signos aprendidos (+1: una velocidad positiva de ese eje hace crecer el acimut / la altura,
#  o la AR / la Dec en una ecuatorial)
# ─────────────────────────────────────────────────────────────────────────────
def leer_signos(modo="altaz", ruta=RUTA_SIGNOS):
    e0, e1 = EJES[modo]
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        return (-1 if d.get(e0) == -1 else 1), (-1 if d.get(e1) == -1 else 1)
    except (OSError, ValueError):
        return 1, 1


def guardar_signos(signo0, signo1, modo="altaz", ruta=RUTA_SIGNOS):
    e0, e1 = EJES[modo]
    d = {}
    try:
        with open(ruta, encoding="utf-8") as f:
            d = dict(json.load(f))
    except (OSError, ValueError):
        pass
    d[e0], d[e1] = (-1 if signo0 == -1 else 1), (-1 if signo1 == -1 else 1)
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(d, f)


# ─────────────────────────────────────────────────────────────────────────────
#  Geometría
# ─────────────────────────────────────────────────────────────────────────────
def _apuntado(tel, lugar):
    """(SkyCoord ICRS, az, alt) adonde dicen apuntar los codificadores ahora mismo."""
    ra_i, dec_i = coordenadas.desde_montura(tel.RightAscension, tel.Declination)
    icrs = SkyCoord(ra=ra_i * u.hourangle, dec=dec_i * u.deg)
    h = icrs.transform_to(AltAz(obstime=Time.now(), location=lugar))
    return icrs, float(h.az.deg), float(h.alt.deg)


def _destino(tel, du, dv, lugar):
    """SkyCoord ICRS a (du, dv) grados del apuntado actual en el plano tangente horizontal (como el GoTo relativo)."""
    icrs, az0, alt0 = _apuntado(tel, lugar)
    az_d, alt_d = apuntado_teorico.mover_en_horizonte(az0, alt0, du, dv)
    return SkyCoord(az=az_d * u.deg, alt=alt_d * u.deg, frame=AltAz(obstime=Time.now(), location=lugar)).icrs


def _error(tel, destino, lugar, modo):
    """
    (e0, e1, factor0) en grados: lo que falta para llegar a `destino` visto desde los ejes de la
    montura. e0 es el error en el cielo a lo largo del eje primario (Δaz·cos alt o ΔAR·cos Dec),
    e1 el del secundario (Δalt o ΔDec); factor0 pasa e0 a grados de giro del eje primario.
    """
    if modo == "eq":
        ra_d, dec_d = coordenadas.a_montura(float(destino.ra.hour), float(destino.dec.deg))
        ra_m, dec_m = float(tel.RightAscension), float(tel.Declination)
        dra = (((ra_d - ra_m) * 15.0 + 180.0) % 360.0) - 180.0
        cosd = max(math.cos(math.radians(dec_m)), 0.2)
        return dra * cosd, dec_d - dec_m, 1.0 / cosd
    icrs, az, alt = _apuntado(tel, lugar)
    d = destino.transform_to(AltAz(obstime=Time.now(), location=lugar))
    daz = ((float(d.az.deg) - az + 180.0) % 360.0) - 180.0
    cosa = max(math.cos(math.radians(alt)), 0.2)
    return daz * cosa, float(d.alt.deg) - alt, 1.0 / cosa


def _deriva(destino, lugar, modo, t_inicio, t_fin):
    """
    (d0, d1) en grados, como _error: cuánto ha cambiado el error hacia `destino` entre t_inicio y
    t_fin solo porque el cielo se ha movido con la montura sin seguimiento (apuntado fijo en acimut
    y altura, o en ángulo horario en una ecuatorial). Restándolo queda lo que han hecho los ejes.
    """
    dt = max(0.0, t_fin - t_inicio)
    if modo == "eq":
        # Sin seguimiento la AR del apuntado crece al ritmo sidéreo: el error en AR baja
        return -dt * 15.041 / 3600.0 * max(math.cos(math.radians(float(destino.dec.deg))), 0.2), 0.0
    a = destino.transform_to(AltAz(obstime=Time(t_inicio, format="unix"), location=lugar))
    b = destino.transform_to(AltAz(obstime=Time(t_fin, format="unix"), location=lugar))
    daz = ((float(b.az.deg) - float(a.az.deg) + 180.0) % 360.0) - 180.0
    return daz * max(math.cos(math.radians(float(a.alt.deg))), 0.2), float(b.alt.deg) - float(a.alt.deg)


def _parar(tel, seguia, velocidad_seguimiento=None):
    """Para los ejes con reintentos y deja el seguimiento como estaba. Lanza ErrorCorreccion si no lo consigue."""
    ultimo = None
    for intento in range(REINTENTOS_PARADA):
        try:
            montura_ejes.mover_ejes_ascom(tel, 0.0, 0.0, tracking_tras_parar=seguia)
            if velocidad_seguimiento is not None:
                try:
                    if int(tel.TrackingRate) != int(velocidad_seguimiento):
                        tel.TrackingRate = velocidad_seguimiento
                except Exception:
                    pass
            return
        except Exception as e:          # COM: el tipo depende del driver
            ultimo = e
            log.warning("Corrección por ejes: la parada falló (intento %d/%d): %s", intento + 1, REINTENTOS_PARADA, e)
            time.sleep(0.3)
    try:
        tel.AbortSlew()
        log.warning("Corrección por ejes: ejes parados con AbortSlew")
        return
    except Exception:
        pass
    raise ErrorCorreccion(_("No se pudo parar la montura tras moverla: {e}", e=ultimo))


def _esperar_quieta(tel, dormir):
    inicio = time.time()
    while time.time() - inicio < ESPERA_QUIETA_S:
        try:
            if not tel.Slewing:
                break
        except Exception:
            break
        dormir(0.2)
    dormir(ASENTAMIENTO_S)


# ─────────────────────────────────────────────────────────────────────────────
#  La corrección
# ─────────────────────────────────────────────────────────────────────────────
def corregir(tel, du, dv, lugar, velocidad_max=VELOCIDAD_DEFECTO, signos=None, dormir=time.sleep,
             guardar=guardar_signos, tolerancia_arcmin=TOLERANCIA_ARCMIN):
    """
    Mueve el apuntado (du, dv) grados en el plano tangente horizontal (Δaz·cos alt, Δalt)
    con MoveAxis, en bucle cerrado con los codificadores. Se ejecuta DENTRO del hilo de la
    montura (montura.ejecutar). Devuelve un dict con modo, pasos, residuo_arcmin, signos y el
    detalle de cada paso; lanza ErrorCorreccion si la montura no se mueve o no se para.
    `guardar(signo0, signo1, modo)` se llama si se ha aprendido un signo (None = no guardar).
    """
    destino = _destino(tel, du, dv, lugar)      # fijo en el cielo: el seguimiento no lo mueve
    return ir_a(tel, destino, lugar, velocidad_max=velocidad_max, signos=signos, dormir=dormir,
                guardar=guardar, tolerancia_arcmin=tolerancia_arcmin)


def ir_a(tel, destino, lugar, velocidad_max=VELOCIDAD_DEFECTO, signos=None, dormir=time.sleep,
         guardar=guardar_signos, tolerancia_arcmin=TOLERANCIA_ARCMIN):
    """
    Como corregir(), pero hacia un `destino` dado (SkyCoord ICRS, fijo en el cielo) en vez de
    un desplazamiento desde donde apunta la montura. Lo usa el dithering, que vuelve siempre
    alrededor del mismo punto de partida.
    """
    modo = modo_montura(tel)
    signo0, signo1 = signos if signos else leer_signos(modo)
    nombre0, nombre1 = (NOMBRE_EJE[e] for e in EJES[modo])
    aprendidos = False
    seguia = bool(tel.Tracking)
    try:
        velocidad_seguimiento = int(tel.TrackingRate)
    except Exception:
        velocidad_seguimiento = None

    tol = tolerancia_arcmin / 60.0

    pasos = []
    al_reves = [0, 0]          # pasos seguidos en que cada eje ha ido al revés
    quietos_seguidos, se_ha_movido = 0, False
    e0, e1, factor0 = _error(tel, destino, lugar, modo)
    pedido0, pedido1 = e0, e1
    try:
        for n in range(PASOS_MAXIMOS):
            if math.hypot(e0, e1) <= tol:
                break
            # Grados que tiene que girar cada EJE (el primario, más de lo que se ve en el cielo)
            mov0 = e0 * factor0 if abs(e0) >= tol / 2 else 0.0
            mov1 = e1 if abs(e1) >= tol / 2 else 0.0
            mayor = max(abs(mov0), abs(mov1))
            t = min(DURACION_MAXIMA_S, max(DURACION_MINIMA_S, mayor / velocidad_max))
            v0, v1 = signo0 * mov0 / t, signo1 * mov1 / t
            montura_ejes.mover_ejes_ascom(tel, v0, v1)
            t_inicio = time.time()
            try:
                # SynScan apaga el seguimiento mientras MoveAxis mueve los ejes: el cielo se va
                sin_seguimiento = not bool(tel.Tracking)
            except Exception:
                sin_seguimiento = False
            try:
                dormir(t)
            finally:
                _parar(tel, seguia, velocidad_seguimiento)
            t_fin = time.time()
            _esperar_quieta(tel, dormir)
            n0, n1, factor0 = _error(tel, destino, lugar, modo)
            d0, d1 = _deriva(destino, lugar, modo, t_inicio, t_fin) if sin_seguimiento else (0.0, 0.0)
            pasos.append({"t": round(t, 2), "v0": round(v0, 4), "v1": round(v1, 4),
                          "antes": (round(e0 * 60, 2), round(e1 * 60, 2)), "despues": (round(n0 * 60, 2), round(n1 * 60, 2)),
                          "deriva": (round(d0 * 60, 2), round(d1 * 60, 2))})
            log.info("Corrección por ejes, paso %d: error (%+.2f′, %+.2f′) → (%+.2f′, %+.2f′); deriva del cielo sin "
                     "seguimiento (%+.2f′, %+.2f′) en %.1f s", n + 1, e0 * 60, e1 * 60, n0 * 60, n1 * 60,
                     d0 * 60, d1 * 60, t_fin - t_inicio if sin_seguimiento else 0.0)

            # Qué fracción del movimiento pedido ha hecho cada eje, descontando lo que se ha ido el
            # cielo mientras no seguía: ≈1 bien, ≈0 no se movió, ≈−1 al revés (el error se dobló)
            quietos, evaluados, bien = 0, 0, 0
            for eje, pedido, antes, despues in ((0, mov0, e0, n0 - d0), (1, mov1, e1, n1 - d1)):
                if pedido == 0.0:
                    continue
                evaluados += 1
                fraccion = (antes - despues) / antes
                if fraccion < -0.3:
                    # Un paso al revés no basta: el primer paso tras un rato siguiendo el cielo
                    # puede no arrancar (29-oct-2026: signos que se invertían y volvían en cada
                    # dither). Se invierte si el siguiente también va al revés.
                    al_reves[eje] += 1
                    if al_reves[eje] < 2:
                        log.info("Corrección por ejes: el eje de %s ha ido al revés en este paso; se espera a otro "
                                 "antes de invertir su signo", nombre0 if eje == 0 else nombre1)
                        continue
                    al_reves[eje] = 0
                    if eje == 0:
                        signo0 = -signo0
                    else:
                        signo1 = -signo1
                    aprendidos = True
                    log.warning("Corrección por ejes: el eje de %s va al revés de lo esperado; signo invertido (%+d)",
                                nombre0 if eje == 0 else nombre1, signo0 if eje == 0 else signo1)
                else:
                    al_reves[eje] = 0
                    if abs(fraccion) < 0.25:
                        quietos += 1
                    elif fraccion > 0:
                        bien += 1
            if bien:
                se_ha_movido = True
            if evaluados and quietos == evaluados:
                quietos_seguidos += 1
                if not se_ha_movido:
                    # El primer paso puede no arrancar; dos seguidos sin moverse ya es una
                    # montura que no responde a MoveAxis: quien llama vuelve al GoTo
                    if quietos_seguidos >= 2:
                        raise ErrorCorreccion(_("La montura no se ha movido con MoveAxis (error {antes}′ → {despues}′)",
                                                antes=f"{math.hypot(e0, e1) * 60:.1f}", despues=f"{math.hypot(n0, n1) * 60:.1f}"))
                    e0, e1 = n0, n1
                    continue
                # Tras un paso bueno, un paso que no gana nada no es una montura que no responde:
                # es el límite de precisión. Se para aquí; volver al GoTo daría el bandazo que se
                # quería evitar.
                e0, e1 = n0, n1
                break
            quietos_seguidos = 0
            e0, e1 = n0, n1
    finally:
        _parar(tel, seguia, velocidad_seguimiento)
        if aprendidos and guardar:
            try:
                guardar(signo0, signo1, modo)
            except OSError as e:
                log.warning("Corrección por ejes: no se pudieron guardar los signos: %s", e)

    residuo = math.hypot(e0, e1) * 60.0
    log.info("Corrección por ejes (%s): pedido (%+.2f′, %+.2f′), %d paso(s), residuo %.2f′, signos %s %+d / %s %+d",
             modo, pedido0 * 60, pedido1 * 60, len(pasos), residuo, nombre0, signo0, nombre1, signo1)
    return {"modo": modo, "pasos": len(pasos), "residuo_arcmin": round(residuo, 3), "signos": (signo0, signo1), "detalle": pasos}
