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
import math
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


def guardar_linea(clave, valor, seccion):
    """
    clave = valor en config.ini cambiando solo esa línea (las claves que se tocan aquí son
    únicas en el fichero): configparser lo reescribiría entero sin los comentarios de la plantilla.
    """
    if not os.path.exists("config.ini"):
        return
    with open("config.ini", "rb") as f:
        datos = f.read()
    linea = f"{clave} = {valor}".encode(locale.getpreferredencoding(False), errors="replace")
    datos, cambios = re.subn(rb"(?m)^" + clave.encode() + rb"[ \t]*=[^\r\n]*", lambda m: linea, datos, count=1)
    if not cambios:
        salto = b"\r\n" if b"\r\n" in datos else b"\n"
        datos, cambios = re.subn(rb"(?m)^\[" + seccion.encode() + rb"\][ \t]*\r?\n", lambda m: m.group(0) + linea + salto, datos, count=1)
    if cambios:
        with open("config.ini", "wb") as f:
            f.write(datos)


def guardar_ruta(ruta):
    guardar_linea("ruta_astap", ruta, "General")


# Sensores frecuentes en astrofotografía: (descripción, lado largo y lado corto en mm)
SENSORES = [
    ("Sony IMX533 cuadrado (Player One Saturn-C/Ares-C, ZWO ASI533)", 11.3, 11.3),
    ("Sony IMX585 (Player One Uranus-C, ZWO ASI585)", 11.2, 6.3),
    ("Sony IMX571 APS-C (Player One Poseidon-C, ZWO ASI2600)", 23.5, 15.7),
    ("Sony IMX294 4/3\" (ZWO ASI294)", 19.1, 13.0),
    ("Sony IMX183 1\" (ZWO ASI183)", 13.2, 8.8),
    ("Sony IMX678 (ZWO ASI678)", 7.7, 4.3),
    ("Sony IMX462 / IMX290 (cámaras planetarias pequeñas: ZWO ASI462, Player One Mars-C)", 5.6, 3.2),
    ("Cámara réflex o sin espejo APS-C", 23.5, 15.6),
    ("Cámara réflex o sin espejo de formato completo", 36.0, 24.0),
]


def preguntar_numero(texto, minimo, maximo):
    while True:
        try:
            r = input(texto).strip().replace(",", ".")
        except EOFError:
            return None
        if not r:
            return None
        try:
            v = float(r)
            if minimo <= v <= maximo:
                return v
        except ValueError:
            pass
        print(f"    Escribe un número entre {minimo:g} y {maximo:g} (o Intro para dejarlo).")


def ayudar_a_elegir():
    """
    Pregunta la focal y la cámara, calcula el campo y devuelve el número de opción
    recomendado ('1'/'2'/'3') o None si no se completa. La focal se apunta en config.ini.
    """
    print()
    print("  ASTAP reconoce las estrellas comparando la foto con su catálogo. Cuanto más pequeño es")
    print("  el trozo de cielo que ve la cámara (el campo), más estrellas débiles necesita y más")
    print("  grande tiene que ser el catálogo. El campo sale de la focal del telescopio y del sensor.")
    focal = preguntar_numero("  Focal del telescopio en mm (con reductor o Barlow, la resultante): ", 50, 5000)
    if focal is None:
        return None
    print("  ¿Qué sensor tiene tu cámara?")
    for i, (nombre, _, _) in enumerate(SENSORES, 1):
        print(f"    {i}) {nombre}")
    print(f"    {len(SENSORES) + 1}) Otro: escribo el tamaño del sensor")
    n = preguntar_numero("  Elige: ", 1, len(SENSORES) + 1)
    if n is None:
        return None
    if int(n) <= len(SENSORES):
        _, largo, corto = SENSORES[int(n) - 1]
    else:
        largo = preguntar_numero("    Lado largo del sensor en mm: ", 1, 60)
        corto = preguntar_numero("    Lado corto del sensor en mm: ", 1, 60)
        if not largo or not corto:
            return None
    ancho, alto = (math.degrees(lado / focal) for lado in (largo, corto))
    guardar_linea("focal_len", f"{focal:g}", "Dispositivos")
    base = instalar_astap.recomendado(alto)
    grados = lambda x: f"{x:.2f}".replace(".", ",")
    print(f"  Con {focal:g} mm verás un campo de {grados(ancho)}° × {grados(alto)}°.")
    if base == "d80":
        print("  Es un campo pequeño (menos de 0,6°): lo ideal es el catálogo D80 (1,2 GB), que solo existe")
        print(f"  como instalador para Windows: descárgalo a mano de {instalar_astap.URL_WEB} y ponlo en la")
        print("  carpeta de ASTAP. Mientras tanto se puede instalar el D50, que a veces basta.")
        return "3"
    textos = {"d05": "te basta el D05, el más pequeño. Si quieres margen para objetos con pocas estrellas, el D20",
              "d20": "te conviene el D20. Con el D50 irá aún más seguro",
              "d50": "te conviene el D50"}
    print(f"  Para ese campo {textos[base]}.")
    return {v: k for k, v in OPCIONES.items()}[base]


def elegir_catalogo():
    recomendada = "3"
    while True:
        print("  Catálogos de estrellas que se pueden instalar:")
        print("    1) D05   102 MB   campos de 1° o más")
        print("    2) D20   400 MB   campos de 0,6° o más")
        print("    3) D50   901 MB   el más usado; vale para casi todo")
        print("    A) Ayúdame a elegir (pregunta la focal y la cámara y calcula el campo)")
        print("    N) No instalar ahora (el asistente de configuración de la web puede hacerlo después)")
        try:
            r = input(f"  Elige [{recomendada}]: ").strip().lower() or recomendada
        except EOFError:
            r = recomendada
        if r in ("n", "no"):
            return None
        if r == "a":
            recomendada = ayudar_a_elegir() or recomendada
            print()
            continue
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
                dec = 1 if p["total"] < 10e6 else 0     # el programa solo son 0,3 MB
                texto = (f"  {p['texto']}: {p['bytes'] / 1e6:.{dec}f} de {p['total'] / 1e6:.{dec}f} MB"
                         + (f" ({p['mb_s']:.1f} MB/s)" if p["mb_s"] else ""))
            elif p["fase"] == "descomprimiendo":
                texto = f"  Descomprimiendo {p['texto']}: {p['bytes']} de {p['total']} ficheros"
            else:
                texto = "  Preparando..." if p["en_curso"] else ultimo
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
