# herramientas/probar_pausa_darks.py
#
# Prueba sintética de «Pausar y hacer darks» (pausa_darks.py), sin cámara y en una carpeta temporal:
#
#   - El bucle de captura de verdad (bucle_captura.py) con una captura falsa que tarda un poco: la
#     pausa espera a que acabe la foto en curso antes de pedir la tapa, y mientras tanto no hay más fotos.
#   - Tapa puesta: los darks se hacen con la exposición, ganancia y binning de las fotos del apilado
#     (darks.TrabajoDarks de verdad, con una cámara falsa a 14,8 °C) y el maestro va a la biblioteca
#     como dark_<exp>s_g<gan>_b<bin>_t<temp>.fits.
#   - Quitar la tapa y seguir: el bucle vuelve a capturar, y la foto siguiente elige sola el dark
#     nuevo (darks.elegir_dark) en vez del de 20 °C.
#   - Cancelar antes de tapar reanuda sin darks; cancelar durante los darks pide quitar la tapa y
#     reanuda sin dark nuevo; si el apilado se detiene a medias, no se reanuda nada.
#
#   python herramientas\probar_pausa_darks.py
#
# Sale con código 0 si todo pasa y 1 si no.

import concurrent.futures
import os
import shutil
import sys
import tempfile
import threading
import time

import numpy as np
from astropy.io import fits

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import darks                                            # noqa: E402
from bucle_captura import BucleCaptura                  # noqa: E402
from hardware import CapturaCancelada                   # noqa: E402
from pausa_darks import PausaDarks                      # noqa: E402

FALLOS = []


def comprobar(condicion, texto):
    print(("  ok   " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


def esperar(condicion, tope=10.0):
    fin = time.monotonic() + tope
    while time.monotonic() < fin:
        if condicion():
            return True
        time.sleep(0.02)
    return condicion()


class CamaraFalsa:
    SensorType = 2
    Gain = 130
    Offset = 35

    def __init__(self, temperatura):
        self.t = temperatura

    @property
    def CCDTemperature(self):
        return self.t


class TrabajadorFalso:
    """Lo mínimo de TrabajadorCOM: .ocupado mientras ejecuta y ejecutar_async."""

    def __init__(self, cam):
        self.cam = cam
        self.ocupado = False
        self._ejecutor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def ejecutar_async(self, fn):
        def envuelta():
            self.ocupado = True
            try:
                return fn(self.cam)
            finally:
                self.ocupado = False
        return self._ejecutor.submit(envuelta)


class Escenario:
    """Bucle de verdad con una captura falsa de 0,3 s y la pausa para darks conectada a él."""

    EXPOSICION, GANANCIA, BINNING = 60.0, 130, 1

    def __init__(self, temperatura=14.8):
        self.camara = TrabajadorFalso(CamaraFalsa(temperatura))
        self.cancelar = threading.Event()
        self.fotos = []            # (instante, nombre del dark elegido)
        self.exponiendo = False
        self.apilado_pausado = False
        self.voz = []
        self.trabajo = darks.TrabajoDarks()
        self.bucle = BucleCaptura(capturar=self._capturar, camara=self.camara, otros_trabajos=(self.trabajo,))
        self.pausa = PausaDarks(
            bucle=self.bucle, camara=self.camara, trabajo_darks=self.trabajo,
            pausar_apilado=lambda: setattr(self, "apilado_pausado", True),
            reanudar_apilado=lambda: setattr(self, "apilado_pausado", False),
            parametros=lambda: {"exposicion": self.EXPOSICION, "ganancia": self.GANANCIA,
                                "binning": self.BINNING, "numero_darks": 10},
            cancelar_captura=self.cancelar.set, captura_en_curso=lambda: self.exponiendo,
            decir=self.voz.append, intervalo=0.05)

    def _capturar(self, usar_dark):
        self.exponiendo = True
        self.camara.ocupado = True
        try:
            time.sleep(0.3)
        finally:
            self.camara.ocupado = False
            self.exponiendo = False
        eleccion = darks.elegir_dark(self.EXPOSICION, self.GANANCIA, self.BINNING, self.camara.cam.t)
        self.fotos.append((time.monotonic(), eleccion["nombre"] if eleccion else None))
        return {"ok": True, "mensaje": "foto"}

    def fin(self):
        self.bucle.detener()
        self.bucle.esperar_fin(5)


def frame_falso(cam, exposicion, binning, descripcion="", light=True, ganancia=None, cancelar=None):
    """Sustituye a hardware.capturar_frame: un dark diminuto; respeta la cancelación."""
    for _ in range(5):
        if ESCENARIO_ACTUAL and ESCENARIO_ACTUAL.cancelar.is_set():
            ESCENARIO_ACTUAL.cancelar.clear()
            raise CapturaCancelada()
        time.sleep(0.01)
    return np.full((4, 4), 100, dtype=np.uint16)


ESCENARIO_ACTUAL = None


def escribir_dark_viejo():
    """El dark de 20 °C del principio de la noche."""
    hdu = fits.PrimaryHDU(np.full((4, 4), 120, dtype=np.uint16))
    hdu.header["CCD-TEMP"] = 20.0
    hdu.header["GAIN"] = 130
    hdu.header["XBINNING"] = 1
    hdu.header["DATE-OBS"] = "2026-11-26T21:00:00"
    hdu.writeto(darks.ruta_dark(60.0, 130, 1, 20.0), overwrite=True)


def prueba_flujo_completo():
    global ESCENARIO_ACTUAL
    print("Flujo completo: pausa, tapa, darks, quitar tapa y seguir")
    e = ESCENARIO_ACTUAL = Escenario(14.8)
    escribir_dark_viejo()
    e.bucle.iniciar()
    comprobar(esperar(lambda: len(e.fotos) >= 2), "el apilado hace fotos con el dark de 20 °C")
    comprobar(e.fotos[-1][1] == "dark_60.00s_g130_b1_t20.fits", "antes de la pausa se usa el dark de 20 °C")

    # Pausar justo cuando una foto está exponiendo: la tapa no se pide hasta que acabe
    esperar(lambda: e.exponiendo)
    e.pausa.iniciar()
    comprobar(e.pausa.fase == "esperando_foto", "con una foto exponiendo, primero se espera a que acabe")
    comprobar(e.apilado_pausado and e.bucle.pausado, "el apilado y el bucle quedan en pausa")
    comprobar(esperar(lambda: e.pausa.fase == "poner_tapa"), "al acabar la foto se pide la tapa")
    comprobar(not e.camara.ocupado, "la cámara está libre al pedir la tapa")
    comprobar(any("tapa" in v.lower() for v in e.voz), "la voz pide poner la tapa")
    n_fotos = len(e.fotos)
    time.sleep(0.5)
    comprobar(len(e.fotos) == n_fotos, "en pausa no se hacen más fotos")

    try:
        e.pausa.continuar()
        comprobar(False, "no se puede seguir apilando con la tapa sin confirmar")
    except RuntimeError:
        comprobar(True, "no se puede «seguir apilando» antes de hacer los darks")

    e.pausa.tapa_puesta()
    comprobar(e.pausa.fase == "darks", "con la tapa puesta empiezan los darks")
    comprobar(esperar(lambda: e.pausa.fase == "quitar_tapa"), "al terminar se pide quitar la tapa")
    est = e.pausa.estado()
    nombre = os.path.basename(est["ruta"])
    comprobar(nombre == "dark_60.00s_g130_b1_t15.fits", f"maestro con el nombre de la biblioteca ({nombre})")
    with fits.open(est["ruta"]) as h:
        comprobar(int(h[0].header["NDARKS"]) == 10, "10 tomas combinadas")
        comprobar(abs(float(h[0].header["CCD-TEMP"]) - 14.8) < 0.01, "temperatura del sensor en la cabecera")
    comprobar(len(e.fotos) == n_fotos, "durante los darks no se apila ninguna foto")
    comprobar(e.bucle.pausado, "sigue en pausa hasta que se quite la tapa")

    e.pausa.continuar()
    comprobar(e.pausa.fase == "" and e.pausa.reanudado, "al quitar la tapa se reanuda")
    comprobar(not e.apilado_pausado and not e.bucle.pausado, "apilado y bucle reanudados")
    comprobar(esperar(lambda: len(e.fotos) > n_fotos), "el bucle vuelve a hacer fotos")
    comprobar(e.fotos[-1][1] == "dark_60.00s_g130_b1_t15.fits", "la foto siguiente coge sola el dark nuevo de 15 °C")
    comprobar(os.path.exists(darks.ruta_dark(60.0, 130, 1, 20.0)), "el dark de 20 °C sigue en la biblioteca")
    e.fin()


def prueba_cancelar_antes_de_tapar():
    global ESCENARIO_ACTUAL
    print("Cancelar antes de tapar")
    e = ESCENARIO_ACTUAL = Escenario(14.8)
    e.bucle.iniciar()
    esperar(lambda: len(e.fotos) >= 1)
    e.pausa.iniciar()
    esperar(lambda: e.pausa.fase == "poner_tapa")
    e.pausa.cancelar()
    comprobar(e.pausa.fase == "" and e.pausa.cancelado and e.pausa.reanudado, "se reanuda sin darks nuevos")
    comprobar(not e.bucle.pausado and not e.apilado_pausado, "el bucle sigue")
    comprobar(not e.trabajo.en_curso and not e.trabajo.terminado, "no se ha hecho ningún dark")
    n = len(e.fotos)
    comprobar(esperar(lambda: len(e.fotos) > n), "vuelve a hacer fotos")
    e.fin()


def prueba_cancelar_durante_darks():
    global ESCENARIO_ACTUAL
    print("Cancelar durante los darks")
    e = ESCENARIO_ACTUAL = Escenario(10.2)
    e.bucle.iniciar()
    esperar(lambda: len(e.fotos) >= 1)
    e.pausa.iniciar(n=50)
    esperar(lambda: e.pausa.fase == "poner_tapa")
    e.pausa.tapa_puesta()
    esperar(lambda: e.trabajo.completadas >= 2)
    e.pausa.cancelar()
    comprobar(esperar(lambda: e.pausa.fase == "quitar_tapa"), "tras cancelar se pide igualmente quitar la tapa")
    est = e.pausa.estado()
    comprobar(bool(est["error"]) and not est["ruta"], "sin dark nuevo y con el motivo")
    comprobar(not os.path.exists(darks.ruta_dark(60.0, 130, 1, 10.2)), "no se ha guardado un maestro a medias")
    comprobar(e.bucle.pausado, "no se reanuda con la tapa puesta")
    e.pausa.cancelar()      # en quitar_tapa, cancelar = seguir
    comprobar(e.pausa.fase == "" and not e.bucle.pausado, "se reanuda sin darks nuevos")
    comprobar(any("sin darks nuevos" in v for v in e.voz), "la voz dice que sigue sin darks nuevos")
    e.fin()


def prueba_apilado_detenido():
    global ESCENARIO_ACTUAL
    print("Apilado detenido a medias y casos no permitidos")
    e = ESCENARIO_ACTUAL = Escenario(14.8)
    try:
        e.pausa.iniciar()
        comprobar(False, "sin apilado en marcha no se puede pausar para darks")
    except RuntimeError:
        comprobar(True, "sin apilado en marcha no se puede pausar para darks")
    e.bucle.iniciar()
    esperar(lambda: len(e.fotos) >= 1)
    e.pausa.iniciar()
    try:
        e.pausa.iniciar()
        comprobar(False, "no se puede empezar dos veces")
    except RuntimeError:
        comprobar(True, "no se puede empezar dos veces")
    esperar(lambda: e.pausa.fase == "poner_tapa")
    e.bucle.detener()
    comprobar(esperar(lambda: e.pausa.fase == ""), "si el apilado se detiene, el flujo termina")
    comprobar(not e.pausa.reanudado and not e.bucle.activo, "y no se reanuda nada")
    e.fin()


def main():
    carpeta = tempfile.mkdtemp(prefix="scopepower_pausa_darks_")
    darks.CARPETA = carpeta
    darks.capturar_frame = frame_falso
    try:
        prueba_flujo_completo()
        prueba_cancelar_antes_de_tapar()
        prueba_cancelar_durante_darks()
        prueba_apilado_detenido()
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
    print()
    if FALLOS:
        print(f"{len(FALLOS)} comprobaciones fallidas")
        return 1
    print("Todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
