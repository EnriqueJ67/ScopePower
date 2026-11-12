# herramientas/probar_voz.py
#
# Prueba de la voz del PC (voz.py) cuando Windows se queda sin salida de audio, con una voz SAPI
# falsa (no suena nada):
#
#   - Mientras no hay salida de audio, cada aviso se intenta (dos veces) y se pierde, pero el log
#     lo cuenta UNA vez con el motivo, no dos líneas por aviso como antes (300 en una noche).
#   - El estado dice desde cuándo no suena, por qué y cuántos avisos se han perdido.
#   - En cuanto vuelve el sonido, el siguiente aviso suena, el log lo dice y el estado se limpia.
#   - El motivo distingue «no hay ninguna salida de audio» de SPERR_NOT_FOUND con salidas y de
#     otros errores.
#   - El aviso tras los darks pide quitar la tapa, también si se cancelan.
#
#   python herramientas\probar_voz.py
#
# Sale con código 0 si todo pasa y 1 si no.

import logging
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import voz          # noqa: E402

FALLOS = []


def comprobar(condicion, texto):
    print(("  ok   " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


class ErrorCOM(Exception):
    """Como pywintypes.com_error de SpVoice.Speak: (hresult, texto, excepinfo, arg)."""
    def __init__(self, scode):
        super().__init__(-2147352567, "Ocurrió una excepción.", (0, None, None, None, 0, scode), None)
        self.hresult = -2147352567
        self.excepinfo = (0, None, None, None, 0, scode)


class Mundo:
    """Lo que hay en Windows: si suena y qué se ha dicho."""
    def __init__(self):
        self.hay_audio = True
        self.dicho = []


class SapiFalsa:
    def __init__(self, mundo):
        self.mundo = mundo

    def Speak(self, texto):
        if not self.mundo.hay_audio:
            raise ErrorCOM(voz.SPERR_NOT_FOUND)
        self.mundo.dicho.append(texto)


class Registro(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lineas = []

    def emit(self, r):
        self.lineas.append((r.levelno, r.getMessage()))

    def de_nivel(self, nivel):
        return [m for n, m in self.lineas if n >= nivel]


def probar_racha():
    print("Sin salida de audio y vuelta")
    mundo = Mundo()
    loc = voz.Locutor.__new__(voz.Locutor)      # sin el hilo: se llama a _decir_con_reintento a mano
    import queue
    import threading
    loc._cola = queue.Queue(maxsize=voz._MAX_COLA)
    loc._lock = threading.Lock()
    loc.config = dict(voz._leer_config(), activada=True)
    loc.disponible = None
    loc.voz_usada = ""
    loc.voz_del_idioma = True
    loc.ultimo_error = loc.ultimo_texto = ""
    loc.voces = []
    loc.fallando_desde = None
    loc.motivo_fallo = ""
    loc.perdidos = 0
    loc._aplicar_ajustes = False
    loc._ajustar = lambda sapi: None
    loc._inicializar = lambda: SapiFalsa(mundo)
    loc._sapi = SapiFalsa(mundo)
    salidas = {"lista": []}
    voz._salidas_audio = lambda: salidas["lista"]

    reg = Registro()
    voz.log.addHandler(reg)
    voz.log.setLevel(logging.DEBUG)

    loc._decir_con_reintento("Empieza exposición de 20 segundos")
    comprobar(mundo.dicho == ["Empieza exposición de 20 segundos"], "con sonido, se dice")
    comprobar(loc.estado()["fallando_desde"] is None, "estado sin fallo")

    mundo.hay_audio = False
    for i in range(20):
        loc._decir_con_reintento(f"Foto añadida, {i} apiladas")
    avisos = reg.de_nivel(logging.WARNING)
    comprobar(len(avisos) == 1, f"20 avisos perdidos, una sola línea de aviso en el log (hay {len(avisos)})")
    comprobar("ninguna salida de audio" in avisos[0] and "Foto añadida, 0 apiladas" in avisos[0],
              "el aviso dice el motivo y qué se perdió: " + avisos[0][:90])
    e = loc.estado()
    comprobar(e["fallando_desde"] is not None and e["perdidos"] == 20 and "ninguna salida de audio" in e["motivo_fallo"],
              "estado: desde cuándo, 20 perdidos y el motivo")
    r = loc.resumen()
    comprobar(r["activada"] and r["fallando_desde"] == e["fallando_desde"] and r["perdidos"] == 20,
              "resumen para la cabecera")
    comprobar(mundo.dicho == ["Empieza exposición de 20 segundos"], "nada se ha dicho mientras no hay audio")

    mundo.hay_audio = True
    loc._decir_con_reintento("Exposición terminada")
    comprobar(mundo.dicho[-1] == "Exposición terminada", "vuelve el sonido: el siguiente aviso suena")
    info = [m for n, m in reg.lineas if n == logging.INFO]
    comprobar(any("vuelve a sonar" in m and "20 avisos" in m for m in info), "el log dice que vuelve y cuántos se perdieron")
    e = loc.estado()
    comprobar(e["fallando_desde"] is None and e["perdidos"] == 0 and e["motivo_fallo"] == "", "estado limpio otra vez")

    mundo.hay_audio = False
    loc._decir_con_reintento("Otro")
    comprobar(len(reg.de_nivel(logging.WARNING)) == 2, "una racha nueva vuelve a avisar una vez")
    voz.log.removeHandler(reg)


def probar_diagnostico():
    print("Motivo")
    e = ErrorCOM(voz.SPERR_NOT_FOUND)
    comprobar("ninguna salida de audio" in voz.diagnosticar(e, []), "sin salidas: ninguna salida de audio")
    m = voz.diagnosticar(e, ["Altavoces (Realtek)", "HDMI"])
    comprobar("no encuentra la salida de audio" in m and "Altavoces (Realtek), HDMI" in m, "SPERR_NOT_FOUND con salidas: las nombra")
    comprobar("no encuentra la salida de audio" in voz.diagnosticar(e, None), "sin poder listar salidas")
    comprobar("error de la voz de Windows" in voz.diagnosticar(ErrorCOM(-2147201015), ["Altavoces"]), "otro error de SAPI")
    comprobar(voz._codigo_sapi(e) == voz.SPERR_NOT_FOUND, "código SAPI sacado de excepinfo")


def probar_aviso_tapa():
    print("Aviso de la tapa tras los darks")
    import shutil
    import tempfile
    import numpy as np
    import darks
    dichos = []
    voz_real, carpeta_real = voz.decir, darks.CARPETA
    voz.decir = lambda texto, categoria="general": dichos.append(texto)
    darks.CARPETA = tempfile.mkdtemp(prefix="probar_voz_")
    try:
        class Bien:
            def result(self):
                return [np.full((4, 4), 100, np.uint16)] * 3, {"BAYERPAT": "RGGB"}

        class Cancelado:
            def result(self):
                raise darks.CapturaCancelada()
        for futuro in (Bien(), Cancelado()):
            t = darks.TrabajoDarks()
            t.exposicion = 1.0
            t._terminar(futuro, 1)
        comprobar(len(dichos) == 2 and all("Quita la tapa del telescopio" in d for d in dichos)
                  and dichos[0].startswith("Dark maestro guardado") and "cancelada" in dichos[1],
                  "termine bien o se cancele, la voz pide quitar la tapa: " + " | ".join(dichos))
    finally:
        voz.decir = voz_real
        shutil.rmtree(darks.CARPETA, ignore_errors=True)
        darks.CARPETA = carpeta_real


def main():
    probar_racha()
    probar_diagnostico()
    probar_aviso_tapa()
    if FALLOS:
        print(f"\n❌ {len(FALLOS)} comprobaciones fallidas")
        return 1
    print("\n✅ Voz sin salida de audio y aviso de la tapa correctos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
