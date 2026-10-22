# herramientas/probar_puesta_estacion.py
#
# Prueba sintética del asistente de puesta en estación (puesta_estacion.py) sin
# ASTAP ni equipo: una montura falsa cuyo eje polar tiene un error conocido, con la
# API ASCOM que usa el trabajo (RA/Dec que avanzan con MoveAxis, Tracking que se
# apaga al mover como hace SynScan, Slewing, AtPark...), y un «resolver» falso que
# devuelve adónde apunta DE VERDAD el tubo: el apuntado ideal de los codificadores
# girado rígidamente con el cuerpo de la montura. Ese modelo físico es independiente
# del estimador: si un signo del módulo estuviera al revés, el ajuste no convergería.
#
#   python herramientas\probar_puesta_estacion.py [--verboso]
#
# Casos: álgebra pura; medición de tres puntos (recupera +15′ / −10′); ajuste en bucle
# (cada estimación acierta el error real y, corrigiendo el 60 % cada vez, entra en
# tolerancia); cancelación en mitad de un giro (ejes parados, seguimiento activo);
# inicio con el tubo en el polo (se aparta solo); hemisferio sur.
#
# Precisión: tres puntos a 20° de AR y Dec 60° forman un arco con una «flecha» de
# solo ~2°; un ruido de σ en el centro de la foto se amplifica a σ/0,035 en el eje
# (3″ → 1,4′). ASTAP sobre un campo de 3008 px baja de 1″, y aquí se simula 1″.
# Sale con código 0 si todo pasa y 1 si no.

import os
import sys
import time
import math
import argparse
import logging
import threading

import numpy as np
from astropy.coordinates import SkyCoord, AltAz, EarthLocation, TETE
from astropy.time import Time
import astropy.units as u

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import puesta_estacion as pe                              # noqa: E402

TOLERANCIA_MEDICION = 0.7      # arcmin (ver la nota de precisión de la cabecera)
TOLERANCIA_AJUSTE = 2.0        # arcmin: la del asistente
RUIDO_ARCSEC = 1.0


class _Rango:
    def __init__(self, lo, hi):
        self.Minimum, self.Maximum = lo, hi


class MonturaFalsa:
    """
    Montura ecuatorial cuyo cuerpo está girado (rot_alt, rot_az) grados respecto al
    polo. Los codificadores (RightAscension/Declination) creen apuntar al «ideal»;
    el tubo apunta de verdad a R(cuerpo)·ideal. Sirve también de TrabajadorCOM
    (ejecutar/ejecutar_async) y de cámara (ocupado).
    """

    def __init__(self, lat, lon, alt_m, err_alt_arcmin, err_az_arcmin, ra0_h, dec0_deg, apaga_tracking=True):
        self.lat = lat
        self.lugar = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=alt_m * u.m)
        self.rot_alt = err_alt_arcmin / 60.0
        self.rot_az = err_az_arcmin / 60.0
        self._ra_h, self._dec = float(ra0_h), float(dec0_deg)
        self._v = [0.0, 0.0]
        self._t = time.time()
        self._lock = threading.Lock()
        self.apaga_tracking = apaga_tracking
        self.Tracking = True
        self.Slewing = False
        self.AtPark = False
        self.AlignmentMode = 1
        self.ocupado = False
        self.movimientos = []

    # ── como TrabajadorCOM ──
    def ejecutar(self, fn, timeout=None, conectar=True):
        return fn(self)

    def ejecutar_async(self, fn, conectar=True):
        return fn(self)

    def esta_conectado(self):
        return True

    # ── API ASCOM ──
    def CanMoveAxis(self, eje):
        return True

    def AxisRates(self, eje):
        return [_Rango(0.0, 4.0)]

    def _avanzar(self):
        with self._lock:
            ahora = time.time()
            dt, self._t = ahora - self._t, ahora
            self._ra_h = (self._ra_h + self._v[0] * dt / 15.0) % 24.0
            d = self._dec + self._v[1] * dt
            if d > 90.0:
                d, self._ra_h = 180.0 - d, (self._ra_h + 12.0) % 24.0
            elif d < -90.0:
                d, self._ra_h = -180.0 - d, (self._ra_h + 12.0) % 24.0
            self._dec = d

    @property
    def RightAscension(self):
        self._avanzar()
        return self._ra_h

    @property
    def Declination(self):
        self._avanzar()
        return self._dec

    def MoveAxis(self, eje, v):
        self._avanzar()
        self._v[eje] = float(v)
        self.Slewing = any(self._v)
        if v != 0 and self.apaga_tracking:
            self.Tracking = False
        self.movimientos.append((eje, float(v)))

    def AbortSlew(self):
        self._avanzar()
        self._v = [0.0, 0.0]
        self.Slewing = False

    # ── la física ──
    def apuntado_real(self, t):
        """(ra_deg, dec_deg) J2000 adonde apunta el tubo de verdad en el instante t."""
        self._avanzar()
        # Los codificadores giran alrededor del polo DE LA FECHA (TETE), como una montura real;
        # el polo J2000 está hoy a unos 9′ de él por la precesión y falsearía la medida
        marco = AltAz(obstime=Time(t, format="unix"), location=self.lugar)
        h = SkyCoord(ra=self._ra_h * u.hourangle, dec=self._dec * u.deg,
                     frame=TETE(obstime=Time(t, format="unix"))).transform_to(marco)
        ideal = pe.vector_altaz(float(h.alt.deg), float(h.az.deg))
        az_polo = 0.0 if self.lat >= 0 else 180.0
        w = pe.rodrigues(pe.eje_horizontal(az_polo), pe._Z, self.rot_az)
        real = pe.rodrigues(pe.rodrigues(ideal, pe._Z, self.rot_az), w, self.rot_alt)
        alt_r, az_r = pe.altaz_de_vector(real)
        c = SkyCoord(alt=alt_r * u.deg, az=az_r * u.deg, frame=marco).icrs
        return float(c.ra.deg), float(c.dec.deg)

    def error_real(self):
        """Lo que diría un asistente perfecto: (err_alt, err_az, total) en arcmin."""
        return pe.a_instruccion(self.rot_alt, self.rot_az, self.lat >= 0)

    def corregir_segun(self, err_alt_arcmin, err_az_arcmin, fraccion):
        """El usuario toca los tornillos: deshace `fraccion` de la instrucción recibida."""
        norte = self.lat >= 0
        self.rot_alt -= fraccion * err_alt_arcmin / 60.0
        self.rot_az -= fraccion * (err_az_arcmin if norte else -err_az_arcmin) / 60.0


def hora_siderea_h(lon):
    return float(Time.now().sidereal_time("apparent", longitude=lon * u.deg).hour)


def montar(lat=40.0, lon=-3.7, err_alt=15.0, err_az=-10.0, dec0=60.0, desfase_h=1.0, **kw):
    ra0 = (hora_siderea_h(lon) + desfase_h) % 24.0
    return MonturaFalsa(lat, lon, 650.0, err_alt, err_az, ra0, dec0, **kw)


def conf_prueba(lat=40.0, lon=-3.7):
    return {"distancia_grados": 20.0, "velocidad_eje": 10.0, "tolerancia_arcmin": TOLERANCIA_AJUSTE,
            "exposicion": 0.1, "hacia_el_este": True, "voz": False, "asentamiento_s": 0.0,
            "binning": 1, "focal": 250.0, "ruta_astap": "", "latitud": lat, "longitud": lon, "altitud": 650.0}


class Banco:
    """Resolver y captura falsos; en el ajuste cada foto espera el permiso de la prueba (sin carreras)."""

    def __init__(self, montura, trabajo, ruido_arcsec=RUIDO_ARCSEC, semilla=1):
        self.montura, self.trabajo = montura, trabajo
        self.rng = np.random.default_rng(semilla)
        self.ruido = ruido_arcsec
        self.permiso = threading.Event()
        self.permiso.set()
        self.fotos = 0

    def capturar(self, descripcion, ra_h, dec_deg):
        if self.trabajo.estado()["fase"] == "ajustando":
            self.permiso.wait(10)
            self.permiso.clear()
        self.fotos += 1
        return "", time.time(), None

    def resolver(self, ruta):
        ra, dec = self.montura.apuntado_real(time.time())
        n = self.rng.normal(0.0, self.ruido / 3600.0, 2)
        return (ra + n[0] / max(math.cos(math.radians(dec)), 0.05)) % 360.0, dec + n[1]


def esperar(cond, segundos, paso=0.05):
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(paso)
    return False


def caso_algebra():
    import subprocess
    r = subprocess.run([sys.executable, "puesta_estacion.py"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print(f"  {'✅' if ok else '❌'} álgebra pura (python puesta_estacion.py){'' if ok else ': ' + (r.stderr or r.stdout)[-800:]}")
    return ok


def caso_medicion_y_ajuste(lat=40.0, lon=-3.7, err_alt=15.0, err_az=-10.0, dec0=60.0, nombre="norte", verboso=False):
    montura = montar(lat, lon, err_alt, err_az, dec0)
    trabajo = pe.TrabajoPuestaEstacion()
    banco = Banco(montura, trabajo)
    esperado = montura.error_real()
    trabajo.iniciar(montura, montura, conf=conf_prueba(lat, lon), resolver=banco.resolver, capturar=banco.capturar,
                    modo_montura="ecuatorial_alemana")
    ok = True
    if not esperar(lambda: trabajo.estado()["medicion"] is not None or trabajo.estado()["terminado"], 90):
        print("  ❌ la medición no terminó en 90 s")
        trabajo.cancelar()
        return False
    e = trabajo.estado()
    if e["terminado"]:
        print(f"  ❌ {nombre}: el trabajo terminó antes de medir: {e['detalle']}")
        return False
    m = e["medicion"]
    d_alt, d_az = m["err_alt_arcmin"] - esperado[0], m["err_az_arcmin"] - esperado[1]
    bien = abs(d_alt) < TOLERANCIA_MEDICION and abs(d_az) < TOLERANCIA_MEDICION
    ok &= bien
    print(f"  {'✅' if bien else '❌'} {nombre}: medición altura {m['err_alt_arcmin']:+.2f}′ (real {esperado[0]:+.2f}′) · "
          f"acimut {m['err_az_arcmin']:+.2f}′ (real {esperado[1]:+.2f}′) · {banco.fotos} fotos")
    estados = [p["estado"] for p in e["puntos"]]
    if estados != ["ok", "ok", "ok"] or montura._v != [0.0, 0.0] or not montura.Tracking:
        print(f"  ❌ {nombre}: tras medir, puntos {estados}, ejes {montura._v}, seguimiento {montura.Tracking}")
        ok = False
    if abs(montura.Declination) > pe.DEC_MAXIMA:
        print(f"  ❌ {nombre}: Dec {montura.Declination:.1f}° al acabar la medición")
        ok = False

    # Ajuste: cada estimación debe acertar el error real; se corrige el 60 % y se repite
    ultimo_n = 0
    ciclos_hasta_tolerancia = None
    for ciclo in range(12):
        if not esperar(lambda: (trabajo.estado()["ajuste"] or {}).get("n", 0) > ultimo_n or trabajo.estado()["terminado"], 30):
            print(f"  ❌ {nombre}: el ajuste {ultimo_n + 1} no llegó")
            ok = False
            break
        e = trabajo.estado()
        if e["terminado"]:
            print(f"  ❌ {nombre}: el trabajo terminó durante el ajuste: {e['detalle']}")
            ok = False
            break
        a = e["ajuste"]
        ultimo_n = a["n"]
        real = montura.error_real()
        acierta = abs(a["err_alt_arcmin"] - real[0]) < TOLERANCIA_MEDICION and abs(a["err_az_arcmin"] - real[1]) < TOLERANCIA_MEDICION
        if verboso or not acierta:
            print(f"     ajuste {a['n']}: estima {a['err_alt_arcmin']:+.2f}′/{a['err_az_arcmin']:+.2f}′ · real "
                  f"{real[0]:+.2f}′/{real[1]:+.2f}′ · total {a['total_arcmin']:.2f}′ · fiable {a['fiable']} cond {a['cond']}")
        if not acierta:
            ok = False
        if a["dentro_tolerancia"] and real[2] < TOLERANCIA_AJUSTE + TOLERANCIA_MEDICION:
            ciclos_hasta_tolerancia = ciclo + 1
            break
        ruido = np.random.default_rng(ciclo).normal(0, 0.3, 2)
        montura.corregir_segun(a["err_alt_arcmin"] + ruido[0], a["err_az_arcmin"] + ruido[1], 0.6)
        banco.permiso.set()      # ya se puede hacer la foto siguiente
    bien =ciclos_hasta_tolerancia is not None and ciclos_hasta_tolerancia <= 8
    ok &= bien
    real = montura.error_real()
    print(f"  {'✅' if bien else '❌'} {nombre}: ajuste en tolerancia tras {ciclos_hasta_tolerancia or '—'} ciclos · "
          f"error real final {real[2]:.2f}′ (altura {real[0]:+.2f}′, acimut {real[1]:+.2f}′)")
    trabajo.terminar()
    banco.permiso.set()
    if not esperar(lambda: trabajo.estado()["terminado"], 20):
        print(f"  ❌ {nombre}: no terminó tras terminar()")
        trabajo.cancelar()
        return False
    e = trabajo.estado()
    bien = e["resultado"] == "completada" and montura.Tracking and montura._v == [0.0, 0.0]
    ok &= bien
    print(f"  {'✅' if bien else '❌'} {nombre}: resultado «{e['resultado']}», seguimiento {montura.Tracking}, ejes {montura._v}")
    return ok


def caso_cancelacion():
    montura = montar()
    trabajo = pe.TrabajoPuestaEstacion()
    banco = Banco(montura, trabajo)
    trabajo.iniciar(montura, montura, conf=conf_prueba(), resolver=banco.resolver, capturar=banco.capturar)
    en_giro = esperar(lambda: trabajo.estado()["fase"] == "midiendo" and trabajo.estado()["subfase"] == "moviendo", 30)
    if not en_giro:
        print("  ❌ cancelación: no llegó a girar el eje")
        trabajo.cancelar()
        return False
    time.sleep(0.5)
    moviendo = montura._v[0] != 0.0
    trabajo.cancelar()
    if not esperar(lambda: trabajo.estado()["terminado"], 20):
        print("  ❌ cancelación: no terminó")
        return False
    e = trabajo.estado()
    bien = moviendo and e["resultado"] == "cancelado" and montura._v == [0.0, 0.0] and montura.Tracking
    print(f"  {'✅' if bien else '❌'} cancelación en mitad del giro: estaba moviendo {moviendo}, resultado «{e['resultado']}», "
          f"ejes {montura._v}, seguimiento {montura.Tracking}")
    return bien


def caso_inicio_en_el_polo():
    montura = montar(dec0=89.0)
    trabajo = pe.TrabajoPuestaEstacion()
    banco = Banco(montura, trabajo)
    esperado = montura.error_real()
    trabajo.iniciar(montura, montura, conf=conf_prueba(), resolver=banco.resolver, capturar=banco.capturar)
    aparto = esperar(lambda: trabajo.estado()["fase"] == "apartando_polo", 10)
    if not esperar(lambda: trabajo.estado()["medicion"] is not None or trabajo.estado()["terminado"], 90):
        print("  ❌ inicio en el polo: la medición no terminó")
        trabajo.cancelar()
        return False
    e = trabajo.estado()
    trabajo.cancelar()
    esperar(lambda: trabajo.estado()["terminado"], 20)
    m = e["medicion"]
    dec_fotos = [p["dec"] for p in e["puntos"]]
    bien = (aparto and m is not None and all(d is not None and abs(d) <= 90 - pe.APARTAR_DEG + 2 for d in dec_fotos)
            and abs(m["err_alt_arcmin"] - esperado[0]) < TOLERANCIA_MEDICION and abs(m["err_az_arcmin"] - esperado[1]) < TOLERANCIA_MEDICION)
    medido = f"{m['err_alt_arcmin']:+.2f}′/{m['err_az_arcmin']:+.2f}′" if m else "—"
    decs = [round(d, 1) if d is not None else None for d in dec_fotos]
    print(f"  {'✅' if bien else '❌'} inicio con el tubo en el polo: apartó {aparto}, fotos a Dec {decs}, "
          f"medición {medido} (real {esperado[0]:+.2f}′/{esperado[1]:+.2f}′)")
    return bien


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verboso else logging.WARNING, format="%(asctime)s %(message)s")
    print("Puesta en estación (montura falsa con error conocido, sin ASTAP):")
    t0 = time.time()
    resultados = [
        caso_algebra(),
        caso_medicion_y_ajuste(verboso=args.verboso),
        caso_cancelacion(),
        caso_inicio_en_el_polo(),
        caso_medicion_y_ajuste(lat=-33.9, lon=151.2, err_alt=15.0, err_az=10.0, dec0=-60.0, nombre="sur", verboso=args.verboso),
    ]
    ok = all(resultados)
    print(f"\n{'✅ Puesta en estación correcta' if ok else '❌ Puesta en estación con fallos'} ({time.time() - t0:.0f} s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
