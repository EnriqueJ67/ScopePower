# herramientas/probar_servidor.py
#
# Prueba de humo del servidor web, sin montura, cámara, ASTAP ni red: importa ScopePower en una
# copia temporal de la carpeta (como en un PC recién instalado, sin config.ini), pide las pantallas
# principales con el cliente de pruebas de Flask, en español y en inglés, y firma una foto
# sintética con Pillow. Falla si alguna página no responde 200 o si Flask, Werkzeug o Pillow avisan
# de algo que van a quitar.
#
# Existe porque compilar y pyflakes no importan Flask: una combinación de versiones rota
# (Flask 2.2 con Werkzeug 3 falla nada más importar) pasaba la comprobación en verde.
#
#   python herramientas\probar_servidor.py
#
# Sale con código 0 si todo va bien y 1 si algo falla. Lo ejecuta el workflow «Comprobar».

import os
import shutil
import sys
import tempfile
import time
import warnings

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Lo propio de cada PC y lo que pesa sin hacer falta: la prueba parte de un equipo recién instalado
NO_COPIAR = {".git", ".github", ".claude", ".vs", "__pycache__", "capturas", "calibraciones", "logs",
             "datos", "videos", "comparaciones", "fotos", "continuo", "timelapse", "evolucion",
             "seguimiento", "mosaico"}
NO_COPIAR_FICHEROS = {"config.ini", "secretos.ini", "presets_procesado.json", "copiar_a_campo.destino.txt"}

PANTALLAS = ["/", "/configuracion", "/goto", "/foto-prueba", "/apilado", "/galeria", "/planetario",
             "/esta-noche", "/seguimiento", "/calibracion", "/flats", "/alineacion", "/control"]


def preparar_copia():
    copia = os.path.join(tempfile.gettempdir(), "scopepower_prueba_servidor")
    shutil.rmtree(copia, ignore_errors=True)

    def ignorar(carpeta, nombres):
        return [n for n in nombres if n in NO_COPIAR or n in NO_COPIAR_FICHEROS or n.endswith((".fits", ".wcs"))]

    shutil.copytree(RAIZ, copia, ignore=ignorar)
    return copia


def main():
    # Lo que Flask, Werkzeug y Pillow anuncian que van a quitar es un fallo: así se ve antes de subir
    # de versión y no cuando ya no funciona
    for patron in (r".*will be removed in Pillow.*", r".*is deprecated and will be removed.*",
                   r".*will be removed in (Flask|Werkzeug).*"):
        warnings.filterwarnings("error", message=patron)

    copia = preparar_copia()
    os.chdir(copia)
    sys.path.insert(0, copia)
    t = time.time()
    import ScopePower as sp   # config.ini no existe: se crea desde config.example.ini
    print(f"ScopePower {sp.version.VERSION} importado en {time.time() - t:.1f} s")
    from importlib.metadata import version
    print("  " + " · ".join(f"{p} {version(p)}" for p in ("flask", "werkzeug", "jinja2", "Pillow", "requests")))

    fallos = []
    cliente = sp.app.test_client()

    def pedir(ruta, esperado=200):
        r = cliente.get(ruta)
        bien = r.status_code == esperado
        print(f"  {'✅' if bien else '❌'} {ruta} → {r.status_code}" + (f" (esperado {esperado})" if not bien else ""))
        if not bien:
            fallos.append(ruta)
        return r

    print("Sin sesión:")
    pedir("/login", 302)              # sin usuarios: lleva a la bienvenida
    pedir("/bienvenida")
    pedir("/static/logo.png")
    pedir("/static/css/scopepower.css")
    pedir("/", 302)                   # sin sesión: al login

    with cliente.session_transaction() as sesion:
        sesion["usuario"] = "prueba"
    for codigo in ("es", "en"):
        sp.idioma.cambiar(codigo)
        print(f"Con sesión, idioma {codigo}:")
        for ruta in PANTALLAS:
            pedir(ruta)
    sp.idioma.cambiar("es")

    print("Firma de una foto sintética (Pillow):")
    import numpy as np
    import firma
    from datetime import datetime
    foto = (np.random.default_rng(1).random((900, 1200, 3)) * 60).astype(np.uint8)
    firmada = firma.firmar(foto, {"objeto": "Nebulosa de prueba", "autor": "Prueba", "latitud": 40.0,
                                  "longitud": -3.7, "fecha": datetime(2026, 10, 16, 22, 30), "n_fotos": 30,
                                  "exposicion": 20, "ra": 83.82, "dec": -5.39})
    bien = firmada.shape == foto.shape and not np.array_equal(firmada, foto)
    print(f"  {'✅' if bien else '❌'} firma pintada en {firmada.shape[1]}×{firmada.shape[0]}")
    if not bien:
        fallos.append("firma")

    if fallos:
        print(f"\n❌ Fallan {len(fallos)}: {', '.join(fallos)}")
        return 1
    print("\n✅ Servidor correcto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
