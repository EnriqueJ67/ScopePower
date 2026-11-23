# herramientas/probar_lista_drivers.py
#
# Prueba de la lista de drivers del asistente y de Configuración (/ascom-drivers), sin montura
# ni cámara. El 23-nov-2026 una copia recién instalada se cerró sola con una violación de acceso
# (0xC0000005 en OLEAUT32.dll): el asistente pide a la vez la lista de monturas y la de cámaras
# y los dos hilos leyeron el perfil de ASCOM al mismo tiempo (ver perfil_ascom.py).
#
#   1. Con un ASCOM de mentira: muchos hilos piden las listas a la vez y nunca hay dos dentro de
#      ASCOM.Utilities; un fallo (ASCOM sin instalar) no deja el candado cogido; el selector
#      abierto en la pantalla del PC no impide pedir las listas.
#   2. Las rutas de verdad, en otro proceso (si se cerrara como el 23-nov, la prueba lo cuenta en
#      vez de caerse con él): ScopePower importado en una copia temporal, como un PC recién
#      instalado y sin cámaras, y las dos listas pedidas a la vez como hace el asistente, varias
#      veces, más varias listas de cámaras a la vez. Lee el perfil de ASCOM y busca las cámaras
#      por SDK con lo que haya instalado en el PC (en GitHub, nada), y comprueba que nunca hay
#      dos hilos a la vez leyendo el perfil o buscando cámaras.
#   3. El primer uso de ASCOM.Utilities en un proceso, que es cuando fallaba: varios procesos
#      nuevos con las dos listas pedidas con un desfase distinto cada vez. Solo dice algo en un
#      PC con la Plataforma ASCOM; sin ella se salta.
#
#   python herramientas\probar_lista_drivers.py
#
# Sale con código 0 si todo pasa y 1 si no. Lo ejecuta el workflow «Comprobar».

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Lo propio de cada PC y lo que pesa sin hacer falta (como probar_servidor.py)
NO_COPIAR = {".git", ".github", ".claude", ".vs", "__pycache__", "capturas", "calibraciones", "logs",
             "datos", "videos", "comparaciones", "fotos", "continuo", "timelapse", "evolucion",
             "seguimiento", "mosaico"}
NO_COPIAR_FICHEROS = {"config.ini", "secretos.ini", "presets_procesado.json", "copiar_a_campo.destino.txt"}

FALLOS = []


def comprobar(condicion, texto):
    print(("  ok    " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


class Sonda:
    """Cuenta cuántos hilos están a la vez dentro de lo que envuelve."""

    def __init__(self):
        self._lock = threading.Lock()
        self.dentro = self.maximo = self.veces = 0

    def entrar(self):
        with self._lock:
            self.dentro += 1
            self.veces += 1
            self.maximo = max(self.maximo, self.dentro)

    def salir(self):
        with self._lock:
            self.dentro -= 1

    def envolver(self, fn, pausa=0.03):
        def envuelta(*a, **k):
            self.entrar()
            try:
                time.sleep(pausa)          # tiempo de sobra para que otro hilo se cuele si puede
                return fn(*a, **k)
            finally:
                self.salir()
        return envuelta


def a_la_vez(funciones, espera=30):
    """Lanza las funciones cada una en su hilo, todas en el mismo instante. True si acaban todas."""
    barrera = threading.Barrier(len(funciones))

    def lanzar(fn):
        barrera.wait()
        fn()

    hilos = [threading.Thread(target=lanzar, args=(fn,), daemon=True) for fn in funciones]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(espera)
    return not any(h.is_alive() for h in hilos)


# ─── 1. ASCOM de mentira ─────────────────────────────────────────────────────
class _Par:
    def __init__(self, clave, valor):
        self.Key, self.Value = clave, valor


class AscomFalso:
    """Lo justo de ASCOM.Utilities.Profile y .Chooser, con una sonda en cada llamada."""

    REGISTRADOS = {"Telescope": [("ASCOM.Zeta.Telescope", "zeta"), ("ASCOM.Alfa.Telescope", "Alfa")],
                   "Camera": [("ASCOM.Beta.Camera", "Beta")]}

    def __init__(self):
        self.sonda = Sonda()
        self.instalado = True
        self.selector_abierto = threading.Event()
        self.cerrar_selector = threading.Event()
        self.elegido_con = None

    def crear(self, progid):
        self.sonda.entrar()
        try:
            time.sleep(0.02)
            if not self.instalado:
                raise OSError("Cadena de clase no válida")
            return _SelectorFalso(self) if progid.endswith("Chooser") else _PerfilFalso(self)
        finally:
            self.sonda.salir()


class _PerfilFalso:
    def __init__(self, ascom):
        self._ascom = ascom

    def RegisteredDevices(self, tipo):
        self._ascom.sonda.entrar()
        try:
            time.sleep(0.02)
            return [_Par(c, v) for c, v in self._ascom.REGISTRADOS.get(tipo, [])]
        finally:
            self._ascom.sonda.salir()


class _SelectorFalso:
    DeviceType = None

    def __init__(self, ascom):
        self._ascom = ascom

    def Choose(self, actual):
        self._ascom.elegido_con = (self.DeviceType, actual)
        self._ascom.selector_abierto.set()
        self._ascom.cerrar_selector.wait(20)        # el diálogo abierto en la pantalla del PC
        return "ASCOM.Alfa.Telescope"


def prueba_ascom_de_mentira():
    print("\n1. ASCOM de mentira: nunca dos hilos a la vez")
    sys.path.insert(0, RAIZ)
    import perfil_ascom
    crear_de_verdad = perfil_ascom._crear
    ascom = AscomFalso()
    perfil_ascom._crear = ascom.crear
    try:
        resultados = []

        def pedir(tipo):
            return lambda: resultados.append((tipo, perfil_ascom.drivers_registrados(tipo)))

        terminan = a_la_vez([pedir(("Telescope", "Camera")[i % 2]) for i in range(12)])
        comprobar(terminan and len(resultados) == 12, "12 hilos piden las listas a la vez y todos reciben la suya")
        comprobar(ascom.sonda.maximo == 1,
                  f"nunca hay dos hilos dentro de ASCOM.Utilities (máximo a la vez: {ascom.sonda.maximo}, "
                  f"{ascom.sonda.veces} llamadas)")
        monturas = next((d for t, d in resultados if t == "Telescope"), None)
        comprobar(monturas == [{"progid": "ASCOM.Alfa.Telescope", "name": "Alfa"},
                               {"progid": "ASCOM.Zeta.Telescope", "name": "zeta"}],
                  "la lista sale ordenada por nombre, sin distinguir mayúsculas")
        comprobar(all(d == [{"progid": "ASCOM.Beta.Camera", "name": "Beta"}] for t, d in resultados if t == "Camera"),
                  "cada hilo recibe la lista del tipo que pidió")

        ascom.instalado = False
        try:
            perfil_ascom.drivers_registrados("Telescope")
            comprobar(False, "sin ASCOM instalado, drivers_registrados lanza la excepción")
        except OSError:
            comprobar(True, "sin ASCOM instalado, drivers_registrados lanza la excepción")
        ascom.instalado = True
        despues = []
        comprobar(a_la_vez([lambda: despues.append(perfil_ascom.drivers_registrados("Camera"))], espera=5)
                  and len(despues) == 1, "tras el fallo el candado queda libre: otro hilo lee la lista")

        elegido = []
        selector = threading.Thread(target=lambda: elegido.append(perfil_ascom.elegir_driver("Telescope", "ASCOM.Zeta.Telescope")),
                                    daemon=True)
        selector.start()
        comprobar(ascom.selector_abierto.wait(5), "el selector se abre")
        comprobar(ascom.elegido_con == ("Telescope", "ASCOM.Zeta.Telescope"),
                  "el selector recibe el tipo de dispositivo y el driver marcado")
        mientras = []
        comprobar(a_la_vez([lambda: mientras.append(perfil_ascom.drivers_registrados("Camera"))], espera=5)
                  and len(mientras) == 1, "con el selector abierto en el PC las listas se siguen pudiendo pedir")
        ascom.cerrar_selector.set()
        selector.join(5)
        comprobar(elegido == ["ASCOM.Alfa.Telescope"], "al cerrar el selector se devuelve el driver elegido")
        comprobar(ascom.sonda.maximo == 1, "tampoco con el selector hubo dos hilos creando objetos a la vez")
    finally:
        perfil_ascom._crear = crear_de_verdad
        ascom.cerrar_selector.set()


# ─── 2. Las rutas de verdad, en otro proceso ─────────────────────────────────
def hijo_rutas():
    """Dentro del proceso hijo: importa ScopePower en una copia temporal y pide las listas a la vez."""
    copia = os.path.join(tempfile.gettempdir(), "scopepower_prueba_lista_drivers")
    shutil.rmtree(copia, ignore_errors=True)
    shutil.copytree(RAIZ, copia, ignore=lambda carpeta, nombres: [
        n for n in nombres if n in NO_COPIAR or n in NO_COPIAR_FICHEROS or n.endswith((".fits", ".wcs"))])
    os.chdir(copia)
    sys.path.insert(0, copia)
    import ScopePower as sp        # config.ini no existe: se crea desde config.example.ini

    sonda = Sonda()
    sp.perfil_ascom._leer_registrados = sonda.envolver(sp.perfil_ascom._leer_registrados)
    sp.camara_sdk.detectar_camaras = sonda.envolver(sp.camara_sdk.detectar_camaras)
    respuestas = []

    def pedir(tipo):
        def una():
            cliente = sp.app.test_client()
            with cliente.session_transaction() as sesion:
                sesion["usuario"] = "prueba"
            r = cliente.get("/ascom-drivers?tipo=" + tipo)
            datos = r.get_json(silent=True) or {}
            respuestas.append({"tipo": tipo, "estado": r.status_code, "drivers": datos.get("drivers")})
        return una

    terminan = True
    for _vuelta in range(3):                                   # como el asistente: las dos a la vez
        terminan = a_la_vez([pedir("Telescope"), pedir("Camera")], espera=120) and terminan
    terminan = a_la_vez([pedir("Camera") for _i in range(4)], espera=120) and terminan   # y varias de cámaras
    camaras = next((r["drivers"] for r in respuestas if r["tipo"] == "Camera" and r["drivers"] is not None), [])
    print("RESULTADO " + json.dumps({
        "terminan": terminan, "peticiones": len(respuestas),
        "bien": sum(1 for r in respuestas if r["estado"] == 200 and isinstance(r["drivers"], list)),
        "a_la_vez": sonda.maximo, "lecturas": sonda.veces,
        "monturas": max((len(r["drivers"] or []) for r in respuestas if r["tipo"] == "Telescope"), default=0),
        "camaras": len(camaras), "por_sdk": sum(1 for d in camaras if str(d.get("progid", "")).startswith("SDK:")),
    }), flush=True)


def _lanzar_hijo(argumentos, espera):
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__)] + argumentos, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=espera, env=entorno)
    except subprocess.TimeoutExpired:
        return None, None, ""
    resultado = None
    for linea in r.stdout.splitlines():
        if linea.startswith("RESULTADO "):
            resultado = json.loads(linea[len("RESULTADO "):])
    return r.returncode, resultado, (r.stderr or "")[-1500:]


def _texto_codigo(codigo):
    if codigo is None:
        return "no terminó a tiempo"
    return f"código {codigo & 0xFFFFFFFF:#010x}" + (" (violación de acceso)" if codigo & 0xFFFFFFFF == 0xC0000005 else "")


def prueba_rutas():
    print("\n2. Las rutas de verdad en otro proceso, como un PC recién instalado y sin cámaras")
    codigo, res, errores = _lanzar_hijo(["--hijo-rutas"], espera=600)
    comprobar(codigo == 0, f"el servidor no se cierra al pedir las listas a la vez ({_texto_codigo(codigo)})")
    if codigo != 0 or not res:
        if errores:
            print("    " + errores.replace("\n", "\n    "))
        comprobar(False, "el proceso hijo devuelve su resultado")
        return
    comprobar(res["terminan"] and res["bien"] == res["peticiones"] == 10,
              f"las {res['peticiones']} peticiones a /ascom-drivers responden 200 con su lista ({res['bien']} bien)")
    comprobar(res["a_la_vez"] == 1,
              f"nunca hay dos hilos a la vez leyendo el perfil de ASCOM o buscando cámaras por SDK "
              f"(máximo a la vez: {res['a_la_vez']}, {res['lecturas']} lecturas)")
    print(f"    en este PC: {res['monturas']} monturas ASCOM; {res['camaras']} entradas de cámara, "
          f"{res['por_sdk']} de ellas por SDK")


# ─── 3. El primer uso de ASCOM.Utilities en un proceso ───────────────────────
def hijo_primer_uso(desfase):
    sys.path.insert(0, RAIZ)
    import perfil_ascom
    res = {}

    def leer(tipo):
        try:
            res[tipo] = len(perfil_ascom.drivers_registrados(tipo))
        except Exception as e:
            res[tipo] = f"sin ASCOM ({type(e).__name__})"

    hilos = [threading.Thread(target=leer, args=(t,)) for t in ("Telescope", "Camera")]
    hilos[0].start()
    time.sleep(desfase)
    hilos[1].start()
    for h in hilos:
        h.join()
    print("RESULTADO " + json.dumps(res), flush=True)


def prueba_primer_uso():
    print("\n3. Primer uso de ASCOM.Utilities en procesos nuevos, con las dos listas desfasadas")
    desfases = (0, 0.005, 0.01, 0.02, 0.04, 0.08, 0.12, 0.2)
    cierres, hechos = [], 0
    for desfase in desfases:
        codigo, res, _errores = _lanzar_hijo(["--hijo-primer-uso", str(desfase)], espera=120)
        if codigo == 0 and res and any(isinstance(v, str) for v in res.values()):
            print(f"    este PC no tiene la Plataforma ASCOM ({next(v for v in res.values() if isinstance(v, str))}): "
                  f"se salta")
            return
        hechos += 1
        if codigo != 0:
            cierres.append(f"desfase {desfase} s: {_texto_codigo(codigo)}")
    comprobar(not cierres, f"{hechos} procesos nuevos leen las dos listas sin cerrarse" + (": " + "; ".join(cierres) if cierres else ""))


def main():
    if "--hijo-rutas" in sys.argv:
        hijo_rutas()
        return 0
    if "--hijo-primer-uso" in sys.argv:
        hijo_primer_uso(float(sys.argv[sys.argv.index("--hijo-primer-uso") + 1]))
        return 0
    prueba_ascom_de_mentira()
    prueba_rutas()
    prueba_primer_uso()
    print()
    if FALLOS:
        print(f"❌ {len(FALLOS)} comprobaciones han fallado:")
        for f in FALLOS:
            print("   - " + f)
        return 1
    print("✅ La lista de drivers aguanta que la pidan a la vez")
    return 0


if __name__ == "__main__":
    sys.exit(main())
