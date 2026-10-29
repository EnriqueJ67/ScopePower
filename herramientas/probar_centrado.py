# herramientas/probar_centrado.py
#
# Prueba sintética del centrado tras el GoTo (centrado.py) sin cámara, montura ni ASTAP:
# una montura falsa con un error de apuntado conocido y un "resolvedor" que devuelve
# dónde apunta de verdad. Casos:
#
#   - Sync normal: centra en dos vueltas (Sync + GoTo).
#   - Sync ignorado (SynScan Pro con la muestra pendiente de confirmar): se nota al leer
#     la posición tras el Sync y se corrige en relativo en la misma vuelta.
#   - Sync mezclado con el modelo de alineación (solo se aplica una parte, como el
#     centrado de Altair del 29-oct-2026: 23,2' -> 19,2'): no se rinde en la vuelta 2,
#     prueba un desplazamiento relativo y centra en la 3.
#   - El mismo caso con solo dos vueltas: se queda en «sin_mejora».
#   - Holgura de verdad sin Sync (la montura solo hace el 10 % de lo pedido): sigue
#     parando en «sin_mejora» en la vuelta 2, sin gastar más vueltas.
#
#   python herramientas\probar_centrado.py [--verboso]
#
# Sale con código 0 si todo pasa y 1 si no. Tarda unos segundos.

import os
import sys
import types
import argparse
import logging

from astropy.coordinates import SkyCoord
import astropy.units as u

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

# Sin voz ni humor: el centrado los llama al terminar
for nombre in ("voz", "humor"):
    sys.modules[nombre] = types.SimpleNamespace(decir=lambda *a, **k: None)

import coordenadas                                      # noqa: E402
import centrado                                         # noqa: E402

coordenadas._sistema = "J2000"      # la montura falsa habla en J2000: nada de JNow

FALLOS = []

# Altair, el objetivo del centrado que falló en el campo
OBJETIVO = (19.8464, 8.8683)
ERROR_GOTO = (0.33, 0.18)           # grados en AR y Dec que se equivoca la montura (≈ 23')


def comprobar(cond, texto):
    print(("  ✅ " if cond else "  ❌ ") + texto)
    if not cond:
        FALLOS.append(texto)


class MonturaFalsa:
    """Montura con un error de apuntado: apunta de verdad a lo que cree + `desvio`.

    sync: "normal" (el Sync borra el desvío), "ignorado" (no cambia nada) o
    "mezclado" (la montura dice estar donde le dicen, pero el GoTo siguiente solo corrige
    `fraccion_sync` del desvío, como un modelo de alineación que promedia la muestra
    nueva con las anteriores). `fraccion_mov`: parte de cada desplazamiento que hace de
    verdad (holgura)."""

    CanSlewAsync = False

    def __init__(self, sync="normal", fraccion_sync=0.15, fraccion_mov=1.0):
        self.sync, self.fraccion_sync, self.fraccion_mov = sync, fraccion_sync, fraccion_mov
        self.RightAscension, self.Declination = OBJETIVO       # recién llegada del GoTo
        self.desvio = list(ERROR_GOTO)                         # AR en grados, Dec en grados
        self.Tracking, self.Slewing = True, False
        self.syncs = 0
        self._desvio_tras_goto = None

    def real(self):
        return ((self.RightAscension * 15 + self.desvio[0]) % 360, self.Declination + self.desvio[1])

    def SyncToCoordinates(self, ra_h, dec):
        self.syncs += 1
        if self.sync == "ignorado":
            return
        # La montura pasa a creer que está donde le dicen; lo de verdad no cambia
        ra_real, dec_real = self.real()
        if self.sync == "mezclado":
            self._desvio_tras_goto = [(1 - self.fraccion_sync) * d for d in self.desvio]
        self.RightAscension, self.Declination = ra_h, dec
        self.desvio = [(ra_real - ra_h * 15 + 180) % 360 - 180, dec_real - dec]

    def SlewToCoordinates(self, ra_h, dec):
        if self._desvio_tras_goto:
            self.desvio, self._desvio_tras_goto = self._desvio_tras_goto, None
        # Lo que se mueve de verdad es `fraccion_mov` de lo pedido; la montura cree que ha llegado
        d_ra = ((ra_h * 15 - self.RightAscension * 15 + 180) % 360 - 180)
        d_dec = dec - self.Declination
        self.desvio[0] -= (1 - self.fraccion_mov) * d_ra
        self.desvio[1] -= (1 - self.fraccion_mov) * d_dec
        self.RightAscension, self.Declination = ra_h % 24, dec


class Ejecutor:
    """Lo mínimo de hardware.Dispositivo: ejecutar(fn) y ocupado."""

    def __init__(self, objeto=None):
        self.objeto, self.ocupado = objeto, False

    def ejecutar(self, fn, timeout=30):
        if self.objeto is None:        # cámara: la foto no importa, el resolvedor mira la montura
            return (None, 3.76, 3.76), None
        return fn(self.objeto)


def centrar(tel, max_vueltas=3, sync_primera_vuelta=True):
    conf = {"tolerancia_arcmin": 3.0, "max_vueltas": max_vueltas, "exposicion": 0.0,
            "sync_primera_vuelta": sync_primera_vuelta, "centrar_tras_goto": True,
            "asentamiento_s": 0.0, "binning": 1, "focal": 250.0, "ruta_astap": ""}
    trabajo = centrado.TrabajoCentrado()
    trabajo._guardar_fits = lambda *a, **k: None
    trabajo._guardar_preview = lambda *a, **k: None
    hilo = trabajo.iniciar(Ejecutor(), Ejecutor(tel), *OBJETIVO, objeto="Altair", conf=conf,
                           resolver=lambda ruta: tel.real())
    hilo.join(30)
    e = trabajo.estado()
    print(f"     {e['resultado']}: {e['detalle']} errores={e['errores']} syncs={tel.syncs}")
    return e


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verboso else logging.ERROR, format="     %(levelname)s %(message)s")

    inicial = float(SkyCoord(ra=OBJETIVO[0] * u.hourangle, dec=OBJETIVO[1] * u.deg).separation(
        SkyCoord(ra=(OBJETIVO[0] * 15 + ERROR_GOTO[0]) * u.deg, dec=(OBJETIVO[1] + ERROR_GOTO[1]) * u.deg)).arcmin)
    print(f"Error del GoTo de partida: {inicial:.1f}'")

    print("Sync normal")
    e = centrar(MonturaFalsa("normal"))
    comprobar(e["resultado"] == "centrado" and len(e["errores"]) == 2, "centra en dos vueltas")

    print("Sync ignorado (pendiente de confirmar en SynScan Pro)")
    e = centrar(MonturaFalsa("ignorado"))
    comprobar(e["resultado"] == "centrado" and len(e["errores"]) == 2,
              "se nota que no se aplicó y corrige en relativo en la misma vuelta")

    print("Sync mezclado con el modelo de alineación (15 %)")
    tel = MonturaFalsa("mezclado")
    e = centrar(tel)
    comprobar(len(e["errores"]) == 3 and e["errores"][1] > 0.8 * e["errores"][0],
              "la vuelta 2 no mejora, como en el campo")
    comprobar(e["resultado"] == "centrado", "no se rinde: el desplazamiento relativo lo centra en la vuelta 3")
    comprobar(tel.syncs == 1, "solo un Sync (las demás vueltas no tocan el modelo de alineación)")

    print("Sync mezclado con solo dos vueltas")
    e = centrar(MonturaFalsa("mezclado"), max_vueltas=2)
    comprobar(e["resultado"] == "sin_mejora", "sin vueltas libres, se queda en «sin_mejora»")

    print("Holgura sin Sync (la montura hace el 10 % de lo pedido)")
    e = centrar(MonturaFalsa("normal", fraccion_mov=0.1), sync_primera_vuelta=False)
    comprobar(e["resultado"] == "sin_mejora" and len(e["errores"]) == 2, "para en la vuelta 2, como antes")

    print()
    if FALLOS:
        print(f"❌ {len(FALLOS)} comprobaciones fallidas")
        sys.exit(1)
    print("✅ Todo bien")


if __name__ == "__main__":
    main()
