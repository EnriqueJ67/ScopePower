# herramientas/probar_consola_conexion.py
#
# Prueba de consola_conexion.py (cómo entrar en la app al arrancar), sin red ni Windows de verdad:
#
#   1. El QR dibujado con medios bloques en la consola se lee: se reconstruye la imagen tal como
#      se ve (blanco sobre negro) y la descifra el lector de QR de OpenCV.
#   2. El recuadro lleva las direcciones, el QR y los avisos, y sin segno sale igual sin código.
#   3. El cortafuegos: con reglas inventadas, cuándo el móvil puede entrar y cuándo no (regla
#      del instalador, Python solo en redes privadas con la red como pública, bloqueo de Python
#      tras pulsar «Cancelar», cortafuegos apagado).
#   4. El acceso directo «ScopePower en el navegador» se crea una sola vez y solo donde se
#      instaló con instalar_scopepower.bat.
#
#   python herramientas\probar_consola_conexion.py
#
# Sale con código 0 si todo pasa y 1 si no. Lo ejecuta el workflow «Comprobar».

import os
import shutil
import sys
import tempfile

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RAIZ)

import consola_conexion as cc  # noqa: E402

FALLOS = []


def comprobar(condicion, texto):
    print(("  ok    " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


def leer_qr_de_texto(lineas):
    """Imagen del QR tal como se ve en la consola (bloque = blanco) y su lectura con OpenCV."""
    import cv2
    filas = []
    for linea in lineas:
        arriba, abajo = [], []
        for c in linea:
            arriba.append(255 if c in "█▀" else 0)
            abajo.append(255 if c in "█▄" else 0)
        filas += [arriba, abajo]
    img = np.array(filas, dtype=np.uint8)
    # Margen claro de sobra y módulos grandes, como lo ve la cámara del móvil
    img = np.pad(img, 4, constant_values=255)
    img = cv2.resize(img, None, fx=10, fy=10, interpolation=cv2.INTER_NEAREST)
    texto, __, __ = cv2.QRCodeDetector().detectAndDecode(img)
    return texto


def prueba_qr():
    print("\n1. QR en la consola")
    for url in ("http://192.168.137.1:5000", "http://192.168.1.26:5000", "http://DESKTOP-ABC1234.local:5000"):
        lineas = cc.qr_en_texto(url)
        comprobar(bool(lineas), f"se dibuja el QR de {url} ({len(lineas)} líneas)")
        comprobar(all(len(x) == len(lineas[0]) for x in lineas), "todas las líneas del QR miden lo mismo")
        comprobar(len(lineas[0]) <= cc.ANCHO - 8, f"el QR cabe en el recuadro ({len(lineas[0])} columnas)")
        comprobar(leer_qr_de_texto(lineas) == url, f"OpenCV lee {url} en el QR de la consola")
    svg = cc.qr_svg("http://192.168.1.26:5000")
    comprobar(svg is not None and b"<svg" in svg, "el QR en SVG para Configuración")


def prueba_recuadro():
    print("\n2. Recuadro de la consola")
    urls = cc.urls_de_red([("192.168.1.26", "Wi-Fi"), ("10.0.0.5", "Ethernet")], 5000, "MIPC",
                          ip_punto_acceso="192.168.137.1")
    comprobar(urls[0][0] == "http://192.168.137.1:5000", "con punto de acceso, su dirección va la primera")
    comprobar(urls[-1][0] == "http://MIPC.local:5000", "el nombre del equipo va al final")
    urls2 = cc.urls_de_red([("192.168.137.1", "Conexión de área local"), ("192.168.1.26", "Wi-Fi")], 5000, "MIPC",
                           ip_punto_acceso="192.168.137.1")
    comprobar(sum(1 for u, __ in urls2 if u == "http://192.168.137.1:5000") == 1, "sin direcciones repetidas")

    texto = cc.texto_recuadro(urls, "http://127.0.0.1:5000", "1.4.0", ["Aviso de prueba del cortafuegos"])
    for u, __ in urls:
        comprobar(u in texto, f"el recuadro lleva {u}")
    comprobar("http://127.0.0.1:5000" in texto, "el recuadro lleva la dirección de este PC")
    comprobar("█" in texto, "el recuadro lleva el QR")
    comprobar("Aviso de prueba del cortafuegos" in texto, "el recuadro lleva los avisos")
    comprobar("\x1b[" not in texto, "sin colores ANSI cuando la consola no los admite")
    comprobar(max(len(x) for x in texto.splitlines()) <= cc.ANCHO + 2, "ninguna línea se sale del recuadro")
    color = cc.texto_recuadro(urls, "http://127.0.0.1:5000", "1.4.0", [], con_color=True)
    comprobar("\x1b[97;40m" in color, "con color, el QR fuerza blanco sobre negro (también en consolas claras)")

    original = cc.matriz_qr
    cc.matriz_qr = lambda *a, **k: None          # como si faltara segno
    try:
        sin_qr = cc.texto_recuadro(urls, "http://127.0.0.1:5000", "1.4.0")
    finally:
        cc.matriz_qr = original
    comprobar("█" not in sin_qr and urls[0][0] in sin_qr, "sin segno sale el recuadro sin el QR")
    sin_red = cc.texto_recuadro([], "http://127.0.0.1:5000", "1.4.0")
    comprobar("http://127.0.0.1:5000" in sin_red and "█" not in sin_red, "sin red: solo la dirección de este PC")


def regla(**k):
    r = {"nombre": "x", "entrada": True, "activa": True, "accion": 1, "protocolo": 6, "puertos": "*",
         "programa": "", "perfiles": 7}
    r.update(k)
    return r


def prueba_cortafuegos():
    print("\n3. Cortafuegos")
    py = r"c:\users\alguien\anaconda3\python.exe"
    exes = {py}
    activo = {1: True, 2: True, 4: True}
    PRIV, PUB = 2, 4

    comprobar(cc._puerto_incluido("5000-5020", 5000) and cc._puerto_incluido("80,5000", 5000)
              and not cc._puerto_incluido("5001", 5000) and cc._puerto_incluido("*", 5000),
              "puertos de una regla: rangos, listas y *")

    def ev(reglas, perfiles=PRIV, act=activo, **k):
        return cc.evaluar_cortafuegos(reglas, perfiles, act, exes, 5000, **k)

    comprobar(ev([]) == [("sin_permiso", "Privada")], "sin reglas: el móvil no entra")
    comprobar(ev([regla(nombre="ScopePower 5000", puertos="5000")], PUB) == [],
              "la regla del instalador vale en una red pública")
    comprobar(ev([regla(programa=py, perfiles=PRIV)], PRIV) == [], "Python permitido en redes privadas, red privada")
    comprobar(ev([regla(programa=py, perfiles=PRIV)], PUB) == [("sin_permiso", "Pública")],
              "Python permitido solo en privadas y la red está como pública: aviso")
    comprobar(ev([regla(programa=py, perfiles=PRIV), regla(programa=py, accion=0, perfiles=PRIV)], PRIV)
              == [("bloqueado", "Privada")], "Python bloqueado (pulsaron «Cancelar»): pesa más que el permiso")
    comprobar(ev([regla(programa=r"c:\otro\python.exe")]) == [("sin_permiso", "Privada")],
              "un permiso para otro Python no sirve")
    comprobar(ev([regla(puertos="*")]) == [("sin_permiso", "Privada")],
              "una regla sin programa ni puerto concreto no cuenta")
    comprobar(ev([regla(programa=py, protocolo=17)]) == [("sin_permiso", "Privada")], "un permiso solo UDP no sirve")
    comprobar(ev([regla(programa=py, activa=False)]) == [("sin_permiso", "Privada")], "una regla desactivada no cuenta")
    comprobar(ev([], act={1: False, 2: False, 4: False}) == [], "cortafuegos apagado: nada que avisar")
    comprobar(ev([], entrada_permitida={2: True}) == [], "entrada permitida por defecto: nada que avisar")
    comprobar(ev([regla(programa=py)], bloquea_todo={2: True}) == [("bloqueado", "Privada")],
              "«bloquear todas las conexiones entrantes» activado")
    comprobar(ev([regla(programa=py, perfiles=PRIV)], PRIV | PUB) == [("sin_permiso", "Pública")],
              "dos redes conectadas: avisa solo de la que falla")
    for codigo, perfil in (("bloqueado", "Privada"), ("sin_permiso", "Pública"), ("sin_permiso", "Privada")):
        t = cc.texto_problema(codigo, perfil, 5000)
        comprobar("abrir_cortafuegos.bat" in t, f"el aviso {codigo}/{perfil} dice cómo arreglarlo")
    if os.name == "nt":
        try:
            reglas, perfiles, __, __, __ = cc.leer_cortafuegos()
            comprobar(isinstance(reglas, list) and perfiles >= 0,
                      f"se lee el cortafuegos de este PC sin ser administrador ({len(reglas)} reglas de entrada)")
        except Exception as e:
            print(f"  (sin acceso al cortafuegos en este equipo: {e})")


def prueba_acceso_directo():
    print("\n4. Acceso directo en el escritorio")
    tmp = tempfile.mkdtemp(prefix="sp_consola_")
    try:
        app, escritorio = os.path.join(tmp, "app"), os.path.join(tmp, "Escritorio")
        os.makedirs(app)
        os.makedirs(escritorio)
        comprobar(cc.crear_acceso_navegador(5000, app, escritorio) is None,
                  "sin ScopePower.lnk (no se instaló con el instalador): no se crea nada")
        open(os.path.join(escritorio, "ScopePower.lnk"), "wb").close()
        ruta = cc.crear_acceso_navegador(5000, app, escritorio)
        comprobar(ruta is not None and os.path.exists(ruta), "con ScopePower.lnk se crea el acceso del navegador")
        if ruta:
            with open(ruta, encoding="utf-8") as f:
                contenido = f.read()
            comprobar("URL=http://127.0.0.1:5000/" in contenido, "el acceso abre http://127.0.0.1:5000/")
            os.remove(ruta)
        comprobar(cc.crear_acceso_navegador(5000, app, escritorio) is None,
                  "si el usuario lo borra, no se vuelve a crear")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    prueba_qr()
    prueba_recuadro()
    prueba_cortafuegos()
    prueba_acceso_directo()
    print()
    if FALLOS:
        print(f"FALLAN {len(FALLOS)} comprobaciones:")
        for f in FALLOS:
            print("  - " + f)
        sys.exit(1)
    print("Todo bien.")
