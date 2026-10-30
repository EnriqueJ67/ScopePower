# herramientas/probar_tolerancia_astap.py
#
# Prueba sintética del reintento de ASTAP con más tolerancia (anotaciones.resolver_ini, que
# usan la galería y el alineado del apilado), sin ASTAP: un ASTAP de mentira deja en el .ini
# lo que toque en cada llamada.
#
#   - Si ASTAP resuelve a la primera, no se repite.
#   - Si no resuelve, se repite con -t 0,01 y la solución vale si la escala cuadra con
#     FOCALLEN/XPIXSZ y el centro con OBJCTRA/OBJCTDEC (el caso real de M 17 del 8-oct:
#     escala un 2,5 % por debajo de la nominal y la montura a 23′).
#   - Con escala o centro que no cuadran, o sin focal en la cabecera, se descarta y queda
#     un aviso en el log. Si ASTAP no termina a tiempo, no se repite.
#   - El alineado del apilado hace lo mismo (una foto de M 11 se descartó el 29-oct-2026),
#     con la ruta de ASTAP del apilado, sin dejar ficheros y con un motivo legible.
#   - El aviso rojo de una foto descartada se borra cuando entra la siguiente.
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
    def lanzar(ruta_mono, ruta_ini, timeout, opciones="", ruta_astap=None):
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


def caso_apilado(titulo, respuestas, espera_resuelta=True, espera_llamadas=2):
    """El alineado del apilado (apilado.compute_astap_transform) con el mismo reintento."""
    import apilado
    print(titulo)
    with tempfile.TemporaryDirectory(prefix="scopepower_prueba_astap_") as carpeta:
        ruta = os.path.join(carpeta, "img_20261029_190250.fits")
        hdu = fits.PrimaryHDU(np.full((3, LADO, LADO), 100.0, dtype=np.float32))
        for k, v in {"FOCALLEN": 260.0, "XPIXSZ": 3.76, "XBINNING": 1, "OBJCTRA": "18 22 24.1", "OBJCTDEC": "-16 09 48.5"}.items():
            hdu.header[k] = v
        hdu.writeto(ruta)
        ref_ini = os.path.join(carpeta, "ref.ini")
        with open(ref_ini, "w") as f:
            f.write(ini_resuelto(M17, 2.908))
        ref_info = apilado.parse_ini_wcs(ref_ini)
        llamadas, rutas = [], []
        falso = astap_falso(list(respuestas), llamadas)

        def lanzar(ruta_mono, ruta_ini, timeout, opciones="", ruta_astap=None):
            rutas.append(ruta_astap)
            return falso(ruta_mono, ruta_ini, timeout, opciones)
        original = anotaciones._lanzar_astap
        anotaciones._lanzar_astap = lanzar
        try:
            M, motivo = apilado.compute_astap_transform(ruta, ref_info, r"C:\ASTAP\astap.exe")
        finally:
            anotaciones._lanzar_astap = original
        sobrantes = [f for f in os.listdir(carpeta) if "_mono" in f]
    comprobar((M is not None) == espera_resuelta, f"alineada ({motivo})" if espera_resuelta else f"descartada: «{motivo}»")
    comprobar(len(llamadas) == espera_llamadas, f"{espera_llamadas} llamada(s) a ASTAP ({len(llamadas)})")
    comprobar(all(r == r"C:\ASTAP\astap.exe" for r in rutas), "usa la ruta de ASTAP del apilado")
    comprobar(not sobrantes, f"no quedan ficheros intermedios ({sobrantes})")
    if M is not None:
        comprobar(np.allclose(M, [[1, 0, 0], [0, 1, 0]], atol=1e-6), "misma solución que la referencia: matriz identidad")
    else:
        comprobar("exit status" not in motivo, "el motivo no es el texto de la orden de ASTAP")


def caso_aviso_rojo():
    import apilado
    print("El aviso rojo de una foto descartada se borra cuando entra la siguiente")
    apilado.voz.decir = lambda *a, **k: None
    with tempfile.TemporaryDirectory(prefix="scopepower_prueba_astap_") as carpeta:
        a = apilado.ApiladorEnTiempoReal(carpeta, os.path.join(carpeta, "salida.png"))
        a._descartar("img_20261029_190250.fits: ASTAP no resolvió el fotograma")
        comprobar(bool(a.ultimo_error), f"tras descartarla: «{a.ultimo_error}»")
        with a.lock:
            a._anotar_resultado("descartada", "por debajo del umbral")
        comprobar(bool(a.ultimo_error), "otra descartada por calidad no lo borra")
        with a.lock:
            a._anotar_resultado("apilada")
        comprobar(a.ultimo_error == "", "una foto que entra lo borra")


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
    caso_apilado("Apilado: M 11 falla con la tolerancia de serie y se alinea con 0,01", [falla, ini_resuelto(M17, 2.908)])
    caso_apilado("Apilado: resuelve a la primera", [ini_resuelto(M17, 2.908)], espera_llamadas=1)
    caso_apilado("Apilado: tampoco con 0,01, se descarta con un motivo legible", [falla, falla], espera_resuelta=False)
    caso_apilado("Apilado: el reintento da otra escala, se descarta", [falla, ini_resuelto(M17, 5.82)], espera_resuelta=False)
    caso_aviso_rojo()
    if FALLOS:
        print(f"\n{len(FALLOS)} comprobación(es) fallida(s):")
        for f in FALLOS:
            print("  - " + f)
        return 1
    print("\nTodo bien.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
