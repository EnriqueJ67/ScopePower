# herramientas/probar_config_ini.py
#
# Prueba de los tres fallos de la noche del 19-nov-2026, sin montura ni cámara:
#
#   1. config.ini roto: la exposición (Configuración) y el asentamiento de la montura se guardaron
#      en el mismo segundo, los dos reescribieron config.ini a la vez y quedó una clave `voz`
#      repetida en [PuestaEnEstacion]; desde entonces casi todas las pantallas daban error.
#      Aquí muchos hilos guardan a la vez por las rutas de la web y por config_ini, mientras
#      otros leen con el ConfigParser estricto de siempre: config.ini debe quedar siempre entero,
#      sin repeticiones y con el último valor de cada uno. También se comprueba que un config.ini
#      ya roto (el del 19-nov) se lee igual y se repara al arrancar, y que si otro programa tiene
#      el fichero abierto el guardado no se pierde.
#   2. Modo en vivo: tras enfocar, el centrado tras el GoTo encontraba la cámara reservada por el
#      vivo. Ahora el vivo se para y devuelve la cámara antes de centrar (salvo si graba un vídeo).
#   3. Aparcar una ecuatorial (la AZ-GTi en modo EQ): se usa el Park del driver, no el cero de los
#      motores en altura/acimut de la altacimutal.
#
# Importa ScopePower en una copia temporal de la carpeta (como probar_servidor.py).
#
#   python herramientas\probar_config_ini.py
#
# Sale con código 0 si todo pasa y 1 si no. Lo ejecuta el workflow «Comprobar».

import configparser
import os
import shutil
import sys
import tempfile
import threading
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(RAIZ, "herramientas"))

FALLOS = []


def comprobar(condicion, texto):
    print(("  ok    " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


def leer_estricto(ruta):
    cfg = configparser.ConfigParser()        # como lo leía toda la app el 19-nov
    cfg.read(ruta)
    return cfg


# ─── config_ini por sí solo ──────────────────────────────────────────────────
def probar_modulo(carpeta):
    import config_ini
    print("config_ini: guardados a la vez desde muchos hilos")
    ruta = os.path.join(carpeta, "prueba.ini")
    shutil.copyfile(os.path.join(RAIZ, "config.example.ini"), ruta)
    errores_lectura, parar = [], threading.Event()

    def escritor(n):
        for i in range(30):
            config_ini.guardar_valores(f"Hilo{n}", {"i": i, "texto": "x" * (50 * n)}, ruta=ruta)
            with config_ini.editar(ruta) as cfg:
                cfg["Dispositivos"][f"hilo{n}"] = str(i)

    def lector():
        while not parar.is_set():
            try:
                leer_estricto(ruta)
            except Exception as e:      # noqa: BLE001 — cualquier cosa es un fallo
                errores_lectura.append(repr(e))
            time.sleep(0.005)           # las pantallas leen a menudo, pero no sin parar

    lectores = [threading.Thread(target=lector) for __ in range(3)]
    escritores = [threading.Thread(target=escritor, args=(n,)) for n in range(8)]
    for h in lectores + escritores:
        h.start()
    for h in escritores:
        h.join()
    parar.set()
    for h in lectores:
        h.join()
    cfg = leer_estricto(ruta)
    comprobar(not errores_lectura, f"ninguna lectura estricta falló durante los guardados ({len(errores_lectura)} fallos"
              + (f", p. ej. {errores_lectura[0]}" if errores_lectura else "") + ")")
    comprobar(all(cfg.get(f"Hilo{n}", "i", fallback="") == "29" and cfg.get("Dispositivos", f"hilo{n}", fallback="") == "29"
                  for n in range(8)), "config.ini queda con el último valor de cada hilo")
    comprobar("exposicion_captura" in cfg["Dispositivos"], "las claves que nadie tocó siguen ahí")
    comprobar(not [f for f in os.listdir(carpeta) if f.endswith(".tmp")], "no quedan ficheros temporales")

    print("config_ini: el config.ini roto del 19-nov")
    with open(ruta, "a") as f:
        f.write("\n[PuestaEnEstacion]\nvoz = true\nvoz = false\n")
    try:
        leer_estricto(ruta)
        roto = False
    except (configparser.DuplicateOptionError, configparser.DuplicateSectionError):
        roto = True
    comprobar(roto, "el fichero de prueba está roto como el del 19-nov (clave y sección repetidas)")
    cfg = config_ini.leer(ruta)
    comprobar(cfg.get("PuestaEnEstacion", "voz") == "false", "config_ini.leer lo lee igual (se queda con la última)")
    comprobar(config_ini.reparar(ruta), "reparar() lo arregla")
    try:
        leer_estricto(ruta)
        bien = True
    except configparser.Error:
        bien = False
    comprobar(bien, "tras reparar, el ConfigParser estricto lo lee")
    comprobar(any(f.startswith("prueba.ini.roto-") for f in os.listdir(carpeta)), "queda una copia del roto al lado")
    comprobar(not config_ini.reparar(ruta), "un config.ini sano no se toca")

    print("config_ini: otro programa tiene config.ini abierto")
    antes = config_ini._REINTENTOS_CAMBIO
    config_ini._REINTENTOS_CAMBIO = 3
    try:
        with open(ruta, encoding=None) as abierto:
            config_ini.guardar_valores("Abierto", {"clave": "valor"}, ruta=ruta)
            abierto.read()
    finally:
        config_ini._REINTENTOS_CAMBIO = antes
    comprobar(leer_estricto(ruta).get("Abierto", "clave", fallback="") == "valor", "el guardado no se pierde")

    print("config_ini: cabecera de secretos.ini")
    ruta_s = os.path.join(carpeta, "secretos.ini")
    cfg = config_ini.nuevo()
    cfg["Secretos"] = {"a": "b"}
    config_ini.escribir(cfg, ruta_s, encoding="utf-8", cabecera="; cabecera\n")
    with open(ruta_s, encoding="utf-8") as f:
        texto = f.read()
    comprobar(texto.startswith("; cabecera\n") and "a = b" in texto, "la cabecera y el contenido se escriben")


# ─── la app: rutas de la web, vivo y aparcado ────────────────────────────────
class VivoFalso:
    """Lo que ScopePower usa de vivo_dia.trabajo_vivo; parar() suelta la cámara tras un rato."""
    def __init__(self, grabando=False):
        self.en_curso, self.grabando, self.paradas = True, grabando, 0

    def estado(self):
        return {"en_curso": self.en_curso, "grabacion": {"segundos": 3} if self.grabando else None}

    def parar(self):
        self.paradas += 1
        if not self.grabando:
            threading.Timer(0.3, lambda: setattr(self, "en_curso", False)).start()

    def esperar(self, timeout=None):
        fin = time.time() + (timeout or 5)
        while self.en_curso and time.time() < fin:
            time.sleep(0.05)


class MonturaFalsa:
    """Telescopio ASCOM mínimo para _aparcar(); modo: 0 altacimutal, 2 ecuatorial alemana."""
    def __init__(self, modo, can_park=True):
        self.AlignmentMode, self.CanPark = modo, can_park
        self.AtPark, self.Slewing, self.Tracking = False, False, True
        self.Altitude, self.Azimuth = 40.0, 120.0
        self.llamadas = []
        self.CanSlewAltAz = self.CanSlewAltAzAsync = False

    def Park(self):
        self.llamadas.append("Park")
        self.AtPark, self.Tracking, self.Altitude, self.Azimuth = True, False, 42.0, 0.0

    def Unpark(self):
        self.llamadas.append("Unpark")
        self.AtPark = False

    def SlewToAltAz(self, az, alt):
        self.llamadas.append("SlewToAltAz")
        self.Azimuth, self.Altitude = az, alt

    def SlewToCoordinates(self, ra, dec):
        self.llamadas.append("SlewToCoordinates")


def probar_app():
    import probar_servidor
    copia = probar_servidor.preparar_copia()
    os.chdir(copia)
    sys.path.insert(0, copia)
    import ScopePower as sp
    cliente = sp.app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["usuario"] = "prueba"

    print("Rutas de la web: exposición y asentamiento a la vez (lo del 19-nov)")
    fallos_peticion, parar = [], threading.Event()

    def guardar_exposicion(n):
        c = sp.app.test_client()
        with c.session_transaction() as s:
            s["usuario"] = "prueba"
        for i in range(25):
            r = c.post("/guardar-config-datos", json={"exposicion_captura": str(10 + n * 100 + i)})
            if r.status_code != 200:
                fallos_peticion.append(("exposición", r.status_code))

    def guardar_asentamiento(n):
        c = sp.app.test_client()
        with c.session_transaction() as s:
            s["usuario"] = "prueba"
        for i in range(25):
            r = c.post("/montura/asentamiento", json={"segundos": (n * 25 + i) % 60})
            if r.status_code != 200:
                fallos_peticion.append(("asentamiento", r.status_code))

    def guardar_voz_y_dither():
        c = sp.app.test_client()
        with c.session_transaction() as s:
            s["usuario"] = "prueba"
        for i in range(25):
            sp.voz._guardar_config({"volumen": i})
            c.post("/apilado/dithering", json={"cada": 3 + i % 4})

    def leer_pantalla():
        c = sp.app.test_client()
        with c.session_transaction() as s:
            s["usuario"] = "prueba"
        while not parar.is_set():
            r = c.get("/config-datos")
            if r.status_code != 200:
                fallos_peticion.append(("/config-datos", r.status_code))

    hilos = ([threading.Thread(target=guardar_exposicion, args=(n,)) for n in range(3)]
             + [threading.Thread(target=guardar_asentamiento, args=(n,)) for n in range(3)]
             + [threading.Thread(target=guardar_voz_y_dither)])
    lector = threading.Thread(target=leer_pantalla)
    lector.start()
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    parar.set()
    lector.join()
    comprobar(not fallos_peticion, f"todas las peticiones responden 200 ({len(fallos_peticion)} fallos"
              + (f", p. ej. {fallos_peticion[0]}" if fallos_peticion else "") + ")")
    try:
        cfg = leer_estricto("config.ini")
        entero = True
    except configparser.Error as e:
        entero, cfg = False, None
        print(f"        {e}")
    comprobar(entero, "config.ini queda entero, sin claves repetidas")
    if cfg is not None:
        comprobar(cfg.get("Dispositivos", "exposicion_captura") in {str(10 + n * 100 + 24) for n in range(3)},
                  "la exposición es la última que guardó uno de los hilos")
        comprobar(cfg.get("Voz", "volumen", fallback="") == "24", "la voz conserva su último guardado")
        comprobar(cfg.has_section("Usuarios") and cfg.has_section("Montura"), "no se ha perdido ninguna sección")
    r = cliente.get("/config-datos")
    comprobar(r.status_code == 200 and r.get_json()["exposicion"] >= 10, "/config-datos sigue respondiendo")

    print("Modo en vivo: el centrado tras el GoTo le quita la cámara")
    real = sp.vivo_dia.trabajo_vivo
    try:
        sp.vivo_dia.trabajo_vivo = VivoFalso()
        comprobar(sp._soltar_camara_del_vivo("la prueba"), "con el vivo en marcha, la cámara queda libre")
        comprobar(sp.vivo_dia.trabajo_vivo.paradas == 1 and not sp.vivo_dia.trabajo_vivo.en_curso,
                  "el vivo se ha parado una vez")
        sp.vivo_dia.trabajo_vivo = VivoFalso(grabando=True)
        comprobar(not sp._soltar_camara_del_vivo("la prueba"), "grabando un vídeo, el vivo no se toca")
        comprobar(sp.vivo_dia.trabajo_vivo.paradas == 0, "ni se le pide parar")
        sp.vivo_dia.trabajo_vivo = VivoFalso()
        r = cliente.post("/vivo/parar", json={"esperar": True})
        comprobar(r.status_code == 200 and r.get_json()["ok"] and not sp.vivo_dia.trabajo_vivo.en_curso,
                  "/vivo/parar con esperar responde cuando la cámara ha vuelto")
    finally:
        sp.vivo_dia.trabajo_vivo = real
    with open(os.path.join(copia, "static", "js", "guia.js"), encoding="utf-8") as f:
        guia = f.read()
    tramo = guia[guia.index('} else if (s.paso === "enfoque") {'):]
    tramo = tramo[:tramo.index("} else if", 1)]
    comprobar('"/vivo/parar", { esperar: true }' in tramo, "«Hecho, siguiente» tras enfocar para el vivo")

    print("Aparcar: ecuatorial con Park, altacimutal al cero de los motores")
    tel = MonturaFalsa(modo=2)
    r = sp._aparcar(tel)
    comprobar(tel.llamadas == ["Park"], f"la ecuatorial usa Park y nada más ({tel.llamadas})")
    comprobar(r["ok"] and r["ecuatorial"] and r["park"] and r["en_cero"] and not r["avisos"], "y queda aparcada")
    tel = MonturaFalsa(modo=1, can_park=False)
    r = sp._aparcar(tel)
    comprobar(not tel.llamadas and not tel.Tracking and r["ecuatorial"] and not r["park"] and r["avisos"],
              "una ecuatorial sin Park solo para el seguimiento y avisa")
    tel = MonturaFalsa(modo=0)
    sp._aparcado["sin_udp_hasta"] = 0.0
    r = sp._aparcar(tel)        # sin montura por UDP: a altura 0 / acimut 0 por AR/Dec, como siempre
    comprobar("Park" not in tel.llamadas and not r.get("ecuatorial"), "la altacimutal no usa Park")
    tel = MonturaFalsa(modo=2)
    tel.AtPark = True
    sp._desaparcar_si_hace_falta(tel)     # al conectar y antes de cada GoTo
    comprobar(tel.llamadas == ["Unpark"] and not tel.AtPark, "aparcada al conectar o al hacer un GoTo: se desaparca")
    tel = MonturaFalsa(modo=2)
    tel.AtPark = True
    sp.montura._dispositivo = tel        # «conectada» con la montura falsa
    try:
        r = cliente.post("/montura/reanudar")
        comprobar(r.status_code == 200 and not tel.AtPark and tel.Tracking, "Seguir observando la desaparca y sigue el cielo")
    finally:
        sp.montura._dispositivo = None
    with open(os.path.join(copia, "templates", "apagar.html"), encoding="utf-8") as f:
        comprobar("d.ecuatorial" in f.read(), "Apagar cambia los textos para la ecuatorial")


def main():
    carpeta = tempfile.mkdtemp(prefix="scopepower_config_ini_")
    sys.path.insert(0, RAIZ)
    antes = os.getcwd()
    os.chdir(carpeta)
    try:
        probar_modulo(carpeta)
    finally:
        os.chdir(antes)
        shutil.rmtree(carpeta, ignore_errors=True)
    sys.path.remove(RAIZ)
    probar_app()
    print()
    if FALLOS:
        print(f"❌ {len(FALLOS)} comprobaciones fallan")
        return 1
    print("✅ config.ini, modo en vivo y aparcado correctos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
