# herramientas/probar_tolerancia_astap.py
#
# Prueba sintética del reintento de ASTAP con más tolerancia (anotaciones.resolver_wcs),
# sin ASTAP: un ASTAP de mentira deja en el .ini lo que toque en cada llamada.
#
#   - Si ASTAP resuelve a la primera, no se repite.
#   - Si no resuelve, se repite con -t 0,01 y la solución vale si la escala cuadra con
#     FOCALLEN/XPIXSZ y el centro con OBJCTRA/OBJCTDEC (el caso real de M 17 del 8-oct:
#     escala un 2,5 % por debajo de la nominal y la montura a 23′).
#   - Con escala o centro que no cuadran, o sin focal en la cabecera, se descarta y queda
#     un aviso en el log. Si ASTAP no termina a tiempo, no se repite.
#
#   python herramientas\probar_tolerancia_astap.py
#
# Sale con código 0 si todo pasa y 1 si no.

import os
import sys
import math
import logging
import tempfile

import numpy as np
from astropy.io import fits

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import anotaciones                                   # noqa: E402

FALLOS = []
LADO = 64
M17 = (275.2056, -16.1671)                           # centro que dio ASTAP con -t 0.008


def comprobar(condicion, texto):
    print(("  ok    " if condicion else "  FALLA ") + texto)
    if not condicion:
        FALLOS.append(texto)


class Avisos(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.textos = []

    def emit(self, registro):
        self.textos.append(registro.getMessage())


def ini_resuelto(centro, escala_arcsec, angulo=-27.0):
    s, a = escala_arcsec / 3600.0, math.radians(angulo)
    return (f"PLTSOLVD=T\nCRPIX1={(LADO + 1) / 2}\nCRPIX2={(LADO + 1) / 2}\n"
            f"CRVAL1={centro[0]}\nCRVAL2={centro[1]}\n"
            f"CD1_1={-s * math.cos(a)}\nCD1_2={s * math.sin(a)}\nCD2_1={s * math.sin(a)}\nCD2_2={s * math.cos(a)}\n")


def astap_falso(respuestas, llamadas):
    """Sustituto de anotaciones._lanzar_astap: cada llamada escribe la siguiente respuesta ("timeout" = no terminó)."""
    def lanzar(ruta_mono, ruta_ini, timeout, opciones=""):
        llamadas.append(opciones)
        respuesta = respuestas.pop(0)
        if os.path.exists(ruta_ini):
            os.remove(ruta_ini)
        if respuesta == "timeout":
            return False
        with open(ruta_ini, "w") as f:
            f.write(respuesta)
        return True
    return lanzar


def caso(titulo, respuestas, cabecera=None, espera_resuelta=True, espera_llamadas=2, espera_aviso=None):
    print(titulo)
    with tempfile.TemporaryDirectory(prefix="scopepower_prueba_astap_") as carpeta:
        ruta = os.path.join(carpeta, "apilado.fits")
        hdu = fits.PrimaryHDU(np.full((3, LADO, LADO), 100.0, dtype=np.float32))
        cab = {"FOCALLEN": 260.0, "XPIXSZ": 3.76, "XBINNING": 1, "OBJCTRA": "18 22 24.1", "OBJCTDEC": "-16 09 48.5"}
        cab.update(cabecera or {})
        for k, v in cab.items():
            if v is not None:
                hdu.header[k] = v
        hdu.writeto(ruta)
        llamadas, avisos = [], Avisos()
        original = anotaciones._lanzar_astap
        anotaciones._lanzar_astap = astap_falso(list(respuestas), llamadas)
        anotaciones.log.addHandler(avisos)
        try:
            res = anotaciones.resolver_wcs(ruta, ruta_mono=os.path.join(carpeta, "mono.fits"))
        finally:
            anotaciones._lanzar_astap = original
            anotaciones.log.removeHandler(avisos)
    comprobar((res is not None) == espera_resuelta, "resuelta" if espera_resuelta else "sin solución")
    comprobar(len(llamadas) == espera_llamadas, f"{espera_llamadas} llamada(s) a ASTAP ({len(llamadas)})")
    if len(llamadas) > 1:
        comprobar(f"-t {anotaciones.TOLERANCIA_REINTENTO}" in llamadas[1], f"el reintento lleva -t ({llamadas[1]!r})")
    if espera_aviso:
        comprobar(any(espera_aviso in t for t in avisos.textos), f"aviso en el log con «{espera_aviso}» ({avisos.textos})")
    return res


def main():
    falla = "PLTSOLVD=F\n"
    res = caso("Resuelve a la primera: no se repite", [ini_resuelto(M17, 2.908)], espera_llamadas=1)
    if res:
        comprobar(abs(res[1] - 2.908) < 0.01, f"escala {res[1]:.3f}\"/px")
    caso("M 17: falla con la tolerancia de serie y vale con 0,01", [falla, ini_resuelto(M17, 2.908)])
    caso("Reintento con la escala al doble: descartado", [falla, ini_resuelto(M17, 5.82)],
         espera_resuelta=False, espera_aviso="escala")
    caso("Reintento con el centro a 30° de la montura: descartado", [falla, ini_resuelto((245.0, -16.2), 2.908)],
         espera_resuelta=False, espera_aviso="montura")
    caso("Sin FOCALLEN en la cabecera: no se puede comprobar, descartado", [falla, ini_resuelto(M17, 2.908)],
         cabecera={"FOCALLEN": None}, espera_resuelta=False, espera_aviso="FOCALLEN")
    caso("Sin OBJCTRA: basta con la escala", [falla, ini_resuelto(M17, 2.908)],
         cabecera={"OBJCTRA": None, "OBJCTDEC": None})
    caso("Tampoco resuelve con 0,01", [falla, falla], espera_resuelta=False, espera_aviso="ni con tolerancia")
    caso("ASTAP no termina a tiempo: no se repite", ["timeout"], espera_resuelta=False, espera_llamadas=1)
    if FALLOS:
        print(f"\n{len(FALLOS)} comprobación(es) fallida(s):")
        for f in FALLOS:
            print("  - " + f)
        return 1
    print("\nTodo bien.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
