# herramientas/probar_darks_temperatura.py
#
# Prueba sintética de la biblioteca de darks por temperatura (darks.py), sin cámara y en una
# carpeta temporal:
#
#   - Nombres con _t, también bajo cero, y lectura de la temperatura desde el nombre o la cabecera.
#   - Se elige el dark más cercano a la temperatura de la foto; en el mismo grado de diferencia gana
#     el más reciente, y sin temperatura también; nunca uno de otra ganancia, binning o exposición.
#   - Los darks sin _t y los antiguos dark_<exp>s.fits siguen valiendo; uno estropeado no molesta.
#   - Aviso a partir de la diferencia fijada.
#   - Generar con una cámara falsa que se calienta: guarda la media y los extremos, un dark a otra
#     temperatura no pisa al anterior, y uno al mismo grado sí (también si es un fichero sin _t).
#   - Una cámara sin temperatura (o con un valor sin sentido) sigue funcionando como antes.
#   - Aviso de la sesión (voz y chip): se enciende a 5 °C, no parpadea, se apaga con un dark cercano
#     y caduca sin fotos.
#
#   python herramientas\probar_darks_temperatura.py
#
# Sale con código 0 si todo pasa y 1 si no.

import concurrent.futures
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta

import numpy as np
from astropy.io import fits

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import darks                                            # noqa: E402

FALLOS = []


def comprobar(condicion, texto):
    print(("  ok   " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


def escribir_dark(nombre, temperatura=None, gain=None, binning=None, dias=0):
    """Un dark diminuto con la cabecera que se pida; `dias`: hace cuántos días se hizo."""
    hdu = fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16))
    if temperatura is not None:
        hdu.header["CCD-TEMP"] = temperatura
    if gain is not None:
        hdu.header["GAIN"] = gain
    if binning is not None:
        hdu.header["XBINNING"] = binning
    hdu.header["DATE-OBS"] = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")
    hdu.writeto(os.path.join(darks.CARPETA, nombre), overwrite=True)


def vaciar():
    for f in os.listdir(darks.CARPETA):
        os.remove(os.path.join(darks.CARPETA, f))


def elegido(*args, **kw):
    e = darks.elegir_dark(*args, **kw)
    return e and e["nombre"]


class CamaraFalsa:
    """Se calienta 0,2 °C por lectura. `temperatura` None: el driver no la da (lanza, como ASCOM)."""
    SensorType = 2
    Gain = 130
    Offset = 35

    def __init__(self, temperatura):
        self.t = temperatura

    @property
    def CCDTemperature(self):
        if self.t is None:
            raise RuntimeError("Property CCDTemperature is not implemented")
        self.t += 0.2
        return self.t


class TrabajadorFalso:
    """Lo mínimo de TrabajadorCOM que usa TrabajoDarks."""
    ocupado = False

    def __init__(self, cam):
        self.cam = cam
        self._ejecutor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def ejecutar_async(self, fn):
        return self._ejecutor.submit(fn, self.cam)


def generar(temperatura, n=5):
    """Genera un dark de 20 s, ganancia 130, bin 1 y devuelve la ruta (espera a que acabe)."""
    trabajo = darks.TrabajoDarks()
    acabado = threading.Event()
    terminar = trabajo._terminar

    def _terminar(futuro, binning):
        terminar(futuro, binning)
        acabado.set()
    trabajo._terminar = _terminar
    trabajo.iniciar(TrabajadorFalso(CamaraFalsa(temperatura)), 20.0, 1, n, ganancia=130)
    if not acabado.wait(30):
        raise RuntimeError("La generación de darks no terminó")
    if trabajo.error:
        raise RuntimeError(trabajo.error)
    return trabajo.ruta


def probar_nombres():
    print("Nombres")
    comprobar(darks.nombre_dark(20, 130, 1, 21.3) == "dark_20.00s_g130_b1_t21.fits", "con temperatura")
    comprobar(darks.nombre_dark(20, 130, 1, -3.6) == "dark_20.00s_g130_b1_t-4.fits", "bajo cero")
    comprobar(darks.nombre_dark(20, 130, 1, -0.4) == "dark_20.00s_g130_b1_t0.fits", "sin «-0»")
    comprobar(darks.nombre_dark(20, 130, 1) == "dark_20.00s_g130_b1.fits", "sin temperatura, como antes")
    comprobar(darks.nombre_dark(20, None, 2, 5) == "dark_20.00s_b2_t5.fits", "sin ganancia")


def probar_eleccion():
    print("Elección")
    vaciar()
    escribir_dark("dark_20.00s_g130_b1_t21.fits", 21.1, 130, 1)
    escribir_dark("dark_20.00s_g130_b1_t8.fits", 8.2, 130, 1)
    escribir_dark("dark_20.00s_g130_b1_t-4.fits", -3.9, 130, 1)
    escribir_dark("dark_20.00s_g300_b1_t12.fits", 12.0, 300, 1)      # otra ganancia, más cerca
    escribir_dark("dark_20.00s_g130_b2_t12.fits", 12.0, 130, 2)      # otro binning, más cerca
    escribir_dark("dark_25.00s_g130_b1_t12.fits", 12.0, 130, 1)      # otra exposición, más cerca
    comprobar(elegido(20, 130, 1, 19.0) == "dark_20.00s_g130_b1_t21.fits", "19 °C: el de 21 °C")
    comprobar(elegido(20, 130, 1, 12.0) == "dark_20.00s_g130_b1_t8.fits", "12 °C: el de 8 °C, sin mezclar otra ganancia, binning ni exposición")
    comprobar(elegido(20, 130, 1, -10.0) == "dark_20.00s_g130_b1_t-4.fits", "-10 °C: el de -4 °C")
    comprobar(elegido(20, 130, 3, 12.0) is None, "sin ese binning: ninguno")

    e = darks.elegir_dark(20, 130, 1, 15.0)
    comprobar(e["nombre"] == "dark_20.00s_g130_b1_t21.fits" and abs(e["diferencia"] - 6.1) < 1e-6 and e["lejos"],
              "15 °C: a 6,1 °C del más cercano, con aviso")
    e = darks.elegir_dark(20, 130, 1, 20.0)
    comprobar(not e["lejos"], "20 °C: cerca, sin aviso")

    vaciar()
    escribir_dark("dark_20.00s_g130_b1_t21.fits", 21.1, 130, 1, dias=30)
    escribir_dark("dark_20.00s_g130_b1.fits", 21.3, 130, 1, dias=1)
    comprobar(elegido(20, 130, 1, 21.0) == "dark_20.00s_g130_b1.fits", "mismo grado de diferencia: el más reciente")

    vaciar()
    escribir_dark("dark_20.00s_g130_b1_t21.fits", 21.1, 130, 1, dias=1)
    escribir_dark("dark_20.00s_g130_b1_t8.fits", 8.2, 130, 1, dias=5)
    e = darks.elegir_dark(20, 130, 1, None)
    comprobar(e["nombre"] == "dark_20.00s_g130_b1_t21.fits" and e["diferencia"] is None and not e["lejos"],
              "foto sin temperatura: el más reciente, sin aviso")

    vaciar()
    escribir_dark("dark_20.00s_g130_b1_t21.fits", None, 130, 1)
    e = darks.elegir_dark(20, 130, 1, 19.0)
    comprobar(e["temperatura"] == 21.0, "sin CCD-TEMP: la temperatura sale del nombre")


def probar_antiguos():
    print("Ficheros antiguos")
    vaciar()
    escribir_dark("dark_20.00s.fits", None, 130, 1)
    comprobar(elegido(20, 130, 1, 15.0) == "dark_20.00s.fits", "antiguo con cabecera compatible: vale")
    comprobar(elegido(20, 300, 1, 15.0) is None, "antiguo de otra ganancia: no vale")
    escribir_dark("dark_20.00s_g130_b1_t3.fits", 3.0, 130, 1, dias=100)
    comprobar(elegido(20, 130, 1, 15.0) == "dark_20.00s_g130_b1_t3.fits", "un dark sin temperatura solo si no hay otro")
    comprobar(elegido(20, 130, 1, None) == "dark_20.00s.fits", "foto sin temperatura: el más reciente aunque sea antiguo")
    with open(os.path.join(darks.CARPETA, "dark_20.00s_g130_b1_t15.fits"), "wb") as f:
        f.write(b"esto no es un FITS")
    comprobar(elegido(20, 130, 1, 15.0) == "dark_20.00s_g130_b1_t3.fits", "un fichero estropeado se salta")

    lista = {d["nombre"]: d for d in darks.listar_darks()}
    d = lista.get("dark_20.00s_g130_b1_t3.fits")
    comprobar(d is not None and d["temperatura"] == 3.0 and d["gain"] == 130 and not d["antiguo"], "listar_darks entiende el _t")
    comprobar(lista["dark_20.00s.fits"]["antiguo"], "listar_darks sigue marcando los antiguos")


def probar_generar():
    print("Generar")
    vaciar()
    ruta = generar(20.0)
    comprobar(os.path.basename(ruta) == "dark_20.00s_g130_b1_t21.fits", "nombre con la media de la serie: " + os.path.basename(ruta))
    hdr = fits.getheader(ruta)
    comprobar(abs(hdr["CCD-TEMP"] - 20.6) < 1e-6, "CCD-TEMP es la media de las lecturas de cada toma")
    comprobar(abs(hdr["TEMPMIN"] - 20.2) < 1e-6 and abs(hdr["TEMPMAX"] - 21.0) < 1e-6, "guarda los extremos")

    generar(5.0)
    comprobar(sorted(os.listdir(darks.CARPETA)) == ["dark_20.00s_g130_b1_t21.fits", "dark_20.00s_g130_b1_t6.fits"],
              "otra temperatura no pisa al anterior")
    primera = fits.getheader(ruta)["DATE-OBS"]
    generar(20.2)
    comprobar(sorted(os.listdir(darks.CARPETA)) == ["dark_20.00s_g130_b1_t21.fits", "dark_20.00s_g130_b1_t6.fits"],
              "el mismo grado sustituye, sin añadir fichero")
    comprobar(abs(fits.getheader(ruta)["CCD-TEMP"] - 20.8) < 1e-6, "y queda el nuevo (fecha anterior: %s)" % primera)

    vaciar()
    escribir_dark("dark_20.00s_g130_b1.fits", 20.7, 130, 1, dias=5)
    escribir_dark("dark_20.00s.fits", 10.0, 130, 1, dias=5)
    generar(20.0)
    comprobar(sorted(os.listdir(darks.CARPETA)) == ["dark_20.00s.fits", "dark_20.00s_g130_b1_t21.fits"],
              "retira el fichero sin _t del mismo grado y deja el de otra temperatura")


def probar_sin_temperatura():
    print("Cámara sin temperatura")
    vaciar()
    ruta = generar(None)
    comprobar(os.path.basename(ruta) == "dark_20.00s_g130_b1.fits", "nombre sin _t")
    comprobar("CCD-TEMP" not in fits.getheader(ruta), "cabecera sin CCD-TEMP")
    comprobar(elegido(20, 130, 1, None) == "dark_20.00s_g130_b1.fits", "se elige igual que antes")
    generar(None)
    comprobar(os.listdir(darks.CARPETA) == ["dark_20.00s_g130_b1.fits"], "generar de nuevo lo sustituye")

    class Absurda:
        CCDTemperature = -273.15

    class Nan:
        CCDTemperature = float("nan")
    comprobar(darks.leer_temperatura(Absurda()) is None, "valor sin sentido: None")
    comprobar(darks.leer_temperatura(Nan()) is None, "NaN: None")
    comprobar(darks.leer_temperatura(CamaraFalsa(None)) is None, "el driver lanza: None, sin propagar el error")


def probar_aviso_sesion():
    print("Aviso a lo largo de la sesión")
    aviso = darks.AvisoTemperatura()

    def foto(sensor, dark=20.6):
        return aviso.registrar(sensor, {"temperatura": dark, "diferencia": abs(sensor - dark), "nombre": "dark"})

    eventos = [foto(t) for t in (20.4, 19.5, 18.0, 16.5)]
    comprobar(eventos == [None, None, None, None] and aviso.estado() is None, "el sensor baja hasta 4,1 °C del dark: sin aviso")
    comprobar(foto(15.4) == "empieza", "a 5,2 °C: se enciende (una vez)")
    e = aviso.estado()
    comprobar(e == {"sensor": 15.4, "dark": 20.6, "diferencia": 5.2, "hora": e["hora"]}, "el estado lleva sensor, dark y diferencia")
    comprobar(foto(14.0) is None and foto(16.0) is None, "sigue lejos o vuelve a 4,6 °C: no se repite (histéresis)")
    comprobar(aviso.estado() is not None, "y el chip sigue")
    comprobar(foto(16.8, dark=20.6) == "termina" and aviso.estado() is None, "a 3,8 °C: se apaga")
    comprobar(foto(14.0) == "empieza", "si se vuelve a alejar, avisa otra vez")
    comprobar(foto(14.0, dark=13.5) == "termina", "una foto con un dark nuevo a su temperatura lo apaga")

    foto(14.0)
    comprobar(aviso.registrar(14.0, None) is None and aviso.estado() is None, "foto sin dark: se apaga sin anunciarlo")
    comprobar(foto(14.0) == "empieza", "y con dark otra vez, vuelve a avisar")

    reloj = darks.time.monotonic
    try:
        ahora = reloj()
        darks.time.monotonic = lambda: ahora + darks.CADUCA_S + 1
        comprobar(aviso.estado() is None, "sin fotos en 15 minutos el chip desaparece")
        comprobar(foto(14.0) == "empieza", "y la sesión siguiente avisa de nuevo")
    finally:
        darks.time.monotonic = reloj

    otro = darks.AvisoTemperatura()
    comprobar(otro.registrar(None, {"temperatura": 20.0, "diferencia": None, "nombre": "x"}) is None and otro.estado() is None,
              "cámara sin temperatura: nunca avisa")


def main():
    carpeta = tempfile.mkdtemp(prefix="darks_temperatura_")
    darks.CARPETA = carpeta
    try:
        probar_nombres()
        probar_eleccion()
        probar_antiguos()
        probar_generar()
        probar_sin_temperatura()
        probar_aviso_sesion()
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
    if FALLOS:
        print(f"\n❌ {len(FALLOS)} comprobaciones fallidas")
        return 1
    print("\n✅ Biblioteca de darks por temperatura correcta")
    return 0


if __name__ == "__main__":
    # capturar_frame necesita una cámara de verdad: aquí basta un fotograma de mentira
    darks.capturar_frame = lambda *a, **k: np.full((4, 4), 100, dtype=np.uint16)
    sys.exit(main())
