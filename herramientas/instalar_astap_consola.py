# herramientas/instalar_astap_consola.py
#
# Instala ASTAP y su catálogo de estrellas desde la consola, con el mismo código que
# el asistente de configuración de la web (instalar_astap.py). Lo lanza
# instalar_scopepower.bat: así el catálogo (hasta 900 MB) se baja al instalar, en casa
# y con internet, y no en el campo, donde el PC suele estar en la wifi del SynScan.
#
#   python herramientas\instalar_astap_consola.py          pregunta qué catálogo
#   python herramientas\instalar_astap_consola.py d50      sin preguntar
#
# Si ya hay un ASTAP con catálogo (en Program Files o el que instala la app) no hace
# nada salvo apuntarlo en config.ini. Sale con 0 si al terminar ASTAP está listo.

import locale
import os
import re
import sys
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)

import config_inicial   # noqa: E402
import instalar_astap   # noqa: E402

OPCIONES = {"1": "d05", "2": "d20", "3": "d50"}


def guardar_ruta(ruta):
    """
    ruta_astap en [General] de config.ini cambiando solo esa línea: configparser
    reescribiría el fichero entero sin los comentarios de la plantilla.
    """
    if not os.path.exists("config.ini"):
        return
    with open("config.ini", "rb") as f:
        datos = f.read()
    linea = b"ruta_astap = " + ruta.encode(locale.getpreferredencoding(False), errors="replace")
    datos, cambios = re.subn(rb"(?m)^ruta_astap[ \t]*=[^\r\n]*", lambda m: linea, datos, count=1)
    if not cambios:
        salto = b"\r\n" if b"\r\n" in datos else b"\n"
        datos, cambios = re.subn(rb"(?m)^\[General\][ \t]*\r?\n", lambda m: m.group(0) + linea + salto, datos, count=1)
    if cambios:
        with open("config.ini", "wb") as f:
            f.write(datos)


def elegir_catalogo():
    print("  Catálogos de estrellas que se pueden instalar:")
    print("    1) D05   102 MB   campos de 1° o más")
    print("    2) D20   400 MB   campos de 0,6° o más")
    print("    3) D50   901 MB   el más usado; elige este si no sabes el campo de tu cámara")
    print("    N) no instalar ahora (el asistente de configuración de la web puede hacerlo después)")
    print("  Para campos de menos de 0,6° hace falta el D80, que se instala a mano desde " + instalar_astap.URL_WEB)
    while True:
        try:
            r = input("  Elige [3]: ").strip().lower() or "3"
        except EOFError:
            r = "3"
        if r in ("n", "no"):
            return None
        if r in OPCIONES:
            return OPCIONES[r]
        if r in OPCIONES.values():
            return r


def main():
    ruta_config = config_inicial.ruta_astap()
    estado = instalar_astap.estado(ruta_config)
    if estado["ok"]:
        catalogos = ", ".join(c.upper() for c in estado["catalogos"])
        print(f"[OK] ASTAP ya está instalado con el catálogo {catalogos} ({estado['ruta']})")
        if os.path.normcase(estado["ruta"]) != os.path.normcase(ruta_config):
            guardar_ruta(estado["ruta"])
            print("     Ruta de ASTAP apuntada en config.ini")
        return 0

    print("ASTAP no está instalado" if not estado["programa"] else
          f"ASTAP está en {estado['ruta']} pero sin catálogo de estrellas", "(sirve para reconocer las estrellas de cada foto).")
    base = sys.argv[1].lower() if len(sys.argv) > 1 else elegir_catalogo()
    if not base:
        print("[AVISO] ASTAP sin instalar: el centrado, la sincronización por foto y el apilado no funcionarán hasta instalarlo.")
        return 1
    ok, motivo = instalar_astap.instalar(base, al_terminar=guardar_ruta)
    if not ok:
        print(f"[FALTA] No se pudo instalar ASTAP: {motivo}")
        return 1
    print(f"  Descargando de SourceForge a {instalar_astap.CARPETA_LOCAL} (Ctrl+C cancela)")
    ultimo = ""
    try:
        while True:
            p = instalar_astap.progreso()
            if p["fase"] == "descargando" and p["total"]:
                texto = (f"  {p['texto']}: {p['bytes'] / 1e6:.0f} de {p['total'] / 1e6:.0f} MB"
                         + (f" ({p['mb_s']:.1f} MB/s)" if p["mb_s"] else ""))
            elif p["fase"] == "descomprimiendo":
                texto = f"  Descomprimiendo {p['texto']}: {p['bytes']} de {p['total']} ficheros"
            else:
                texto = "  Preparando..."
            if texto != ultimo:
                print("\r" + texto.ljust(78), end="", flush=True)
                ultimo = texto
            if not p["en_curso"]:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        instalar_astap.cancelar()
        while instalar_astap.progreso()["en_curso"]:
            time.sleep(0.2)
    print()
    p = instalar_astap.progreso()
    if p["ok"]:
        print(f"[OK] {p['texto']} ({instalar_astap.CLI_LOCAL})")
        return 0
    print(f"[FALTA] ASTAP no se ha instalado: {p['error'] or p['texto']}. Se puede repetir con el asistente de la web.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
