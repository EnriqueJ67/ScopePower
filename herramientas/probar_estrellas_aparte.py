# herramientas/probar_estrellas_aparte.py
#
# Pruebas sintéticas de las estrellas aparte (estrellas_aparte.py + la parte de
# procesado.py), SIN StarNet2: su licencia no permite usar sus salidas como datos
# de prueba y además no está en todos los PC. Aquí la «separación» es un cielo
# sintético: un fondo con gradiente, una nebulosa gaussiana y estrellas, de los
# que se conocen las dos capas por construcción.
#
#   python herramientas/probar_estrellas_aparte.py
#
# Comprueba:
#   1. la pantalla deshace la capa unscreen: objeto + U·(1 - objeto) == original;
#   2. procesar_capas devuelve una imagen del tamaño esperado, con las estrellas
#      donde estaban y el fondo más uniforme en color que procesar() sobre la foto
#      con estrellas; con tamano_estrellas = 0 las estrellas desaparecen;
#   3. ajustes_rapidos_capas y la pantalla del servidor coinciden con lo que hace
#      el navegador (la misma fórmula, en 8 bits, a ±1 nivel);
#   4. las capas guardadas: rutas, hay_capas (fecha del FITS), renombrar y borrar;
#   5. el lector de --machine-progress con las líneas reales de StarNet2 2.6.2;
#  15. la cola de después de guardar (cola_estrellas.py) y un solo StarNet2 a la vez.

import os
import sys
import tempfile

import numpy as np
from astropy.io import fits

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import procesado                # noqa: E402
import estrellas_aparte          # noqa: E402

FALLOS = []


def comprobar(cond, texto):
    print(("  ✅ " if cond else "  ❌ ") + texto)
    if not cond:
        FALLOS.append(texto)


def cielo_sintetico(h=640, w=640, semilla=1):
    """(original, objeto, U) en ADU / fracción, (3, H, W): fondo + nebulosa + estrellas + ruido."""
    rng = np.random.default_rng(semilla)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    fondo = 400.0 + 40.0 * (xx / w) + 20.0 * (yy / h)
    nebulosa = 300.0 * np.exp(-(((xx - w * 0.55) ** 2 + (yy - h * 0.45) ** 2) / (2 * 60.0 ** 2)))
    objeto = np.stack([fondo + 0.6 * nebulosa, fondo + 0.4 * nebulosa, fondo + 0.9 * nebulosa])
    objeto += rng.normal(0, 12.0, objeto.shape).astype(np.float32)
    estrellas = np.zeros((3, h, w), dtype=np.float32)
    posiciones = []
    for __ in range(120):
        x, y = rng.uniform(10, w - 10), rng.uniform(10, h - 10)
        brillo = 10 ** rng.uniform(2.5, 4.6)
        color = rng.uniform(0.6, 1.4, 3)
        sig = rng.uniform(1.2, 2.2)
        g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sig ** 2))
        for c in range(3):
            estrellas[c] += brillo * color[c] * g
        posiciones.append((int(y), int(x)))
    # Capa unscreen: U tal que objeto + U·(65535 - objeto) = objeto + estrellas (sobre blanco 65535)
    U = np.clip(estrellas / np.maximum(65535.0 - objeto, 1.0), 0, 1).astype(np.float32)
    original = objeto + U * (65535.0 - objeto)
    return original.astype(np.float32), objeto.astype(np.float32), U, posiciones


def prueba_pantalla():
    print("1. Pantalla y capa unscreen")
    original, objeto, U, __ = cielo_sintetico()
    rec = objeto + U * (65535.0 - objeto)
    comprobar(np.abs(rec - original).max() < 0.01, "objeto + U·(blanco - objeto) devuelve el original")
    a = np.random.default_rng(2).uniform(0, 1, (8, 8, 3)).astype(np.float32)
    b = np.random.default_rng(3).uniform(0, 1, (8, 8, 3)).astype(np.float32)
    p = procesado.pantalla(a, b)
    comprobar(np.allclose(p, 1 - (1 - a) * (1 - b)) and p.min() >= max(a.min(), b.min()) - 1e-6,
              "pantalla: 1 - (1 - a)(1 - b), nunca más oscura que las dos capas")
    comprobar(np.allclose(procesado.pantalla(a, np.zeros_like(b)), a), "pantalla con estrellas a 0 = el objeto")


def prueba_procesado():
    print("2. procesar_capas frente a procesar")
    original, objeto, U, posiciones = cielo_sintetico()
    aj = dict(procesado.ESTILOS["seestar"], estirado="multiescala", curvas=None)
    balance = {"r": 1.1, "b": 1.3, "n": 50}
    normal = procesado.procesar(original, 1, aj, escala=1.0, balance=balance)
    capas = procesado.procesar_capas(objeto, U, 1, dict(aj, tamano_estrellas=100), escala=1.0, balance=balance)
    sin = procesado.procesar_capas(objeto, U, 1, dict(aj, tamano_estrellas=0), escala=1.0, balance=balance)
    comprobar(capas.shape == normal.shape == sin.shape and capas.dtype == np.uint8, f"misma forma y uint8 ({capas.shape})")
    # Las estrellas brillantes están donde estaban (y no en la imagen a 0 %)
    L_capas, L_sin = capas.mean(axis=2), sin.mean(axis=2)
    picos = [(L_capas[y, x], L_sin[y, x]) for y, x in posiciones]
    con = sum(1 for c, s in picos if c > s + 20)
    comprobar(con >= len(picos) * 0.5, f"{con} de {len(picos)} estrellas se ven con tamaño 100 % y no al 0 %")
    # Fondo más uniforme en color sin las estrellas (sigma a-b en LAB sobre el cielo)
    import cv2

    def moteado(img8):
        lab = cv2.cvtColor(img8, cv2.COLOR_RGB2LAB).astype(np.float32)
        cielo = lab[..., 0] < np.percentile(lab[..., 0], 50)
        return float(np.hypot(np.std(cv2.GaussianBlur(lab[..., 1], (0, 0), 8)[cielo]), np.std(cv2.GaussianBlur(lab[..., 2], (0, 0), 8)[cielo])))
    m_normal, m_capas = moteado(normal), moteado(sin)
    comprobar(m_capas <= m_normal * 1.05, f"color del fondo a 8 px: {m_capas:.2f} con capas frente a {m_normal:.2f}")
    # Media resolución y solo_base
    base, capa, mascara = procesado.procesar_capas(objeto, U, 1, aj, escala=0.5, balance=balance, solo_base=True)
    comprobar(base.shape == (320, 320, 3) and capa.shape == base.shape and mascara.shape == (320, 320),
              "solo_base a media resolución: base, capa y máscara del tamaño esperado")
    comprobar(0 <= capa.min() and capa.max() <= 1 and capa.max() > 0.3, f"la capa estirada llega a {capa.max():.2f}")
    return base, capa, aj


def prueba_paridad(base, capa, aj):
    print("3. Paridad servidor / navegador de los ajustes rápidos con capas")
    ajustes = dict(aj, temperatura=0.3, brillo=0.2, scnr=0.8, scnr_estrellas=True, saturacion=1.7, saturacion_estrellas=1.2,
                   tamano_estrellas=60, curvas=None)
    servidor = np.clip(procesado.ajustes_rapidos_capas(base, capa, ajustes, fondo=procesado.fondo_cielo(base)) * 255 + 0.5, 0, 255).astype(np.uint8)
    # Lo mismo en 8 bits, paso a paso como retoque.js (tablas de 256 + píxel a píxel)
    b8 = np.clip(base * 255 + 0.5, 0, 255).astype(np.uint8).astype(np.float32) / 255.0
    c8 = np.clip(capa * 255 + 0.5, 0, 255).astype(np.uint8).astype(np.float32) / 255.0
    fondo = procesado.fondo_cielo(base)

    def previas(img, fondo, t, b):
        gan = np.array([1 + 0.3 * t, 1, 1 - 0.3 * t], dtype=np.float32)
        v = fondo + (img - fondo) * gan
        return np.power(np.clip(v, 0, 1), 2 ** (-b))

    def scnr(img, f):
        ref = (img[..., 0] + img[..., 2]) / 2
        exceso = np.clip(img[..., 1] - ref, 0, None)
        out = img.copy()
        out[..., 1] = np.clip(out[..., 1] - f * exceso, 0, None)
        return out

    def saturar(img, gan):
        V = img.max(axis=2); mn = img.min(axis=2)
        S = np.where(V > 0, (V - mn) / np.maximum(V, 1e-9), 0)
        g = np.where(gan * S > 1, 1 / np.maximum(S, 1e-9), gan)[..., None]
        return np.where((V > 0)[..., None] & (V > mn)[..., None], V[..., None] - g * (V[..., None] - img), img)
    o = saturar(scnr(previas(b8, fondo, 0.3, 0.2), 0.8), 1.7)
    e = saturar(scnr(previas(c8, np.zeros(3, np.float32), 0.3, 0.2), 0.8), 1.2)
    nav = np.clip((1 - (1 - o) * (1 - e * 0.6)) * 255 + 0.5, 0, 255).astype(np.uint8)
    dif = np.abs(servidor.astype(int) - nav.astype(int))
    comprobar(np.percentile(dif, 99.5) <= 2 and dif.max() <= 6, f"diferencia p99,5 = {np.percentile(dif, 99.5):.0f}, máx = {dif.max()} niveles")


def prueba_capas_guardadas():
    print("4. Capas guardadas junto a la galería")
    carpeta_antes = estrellas_aparte.CARPETA_CAPAS
    with tempfile.TemporaryDirectory() as tmp:
        estrellas_aparte.CARPETA_CAPAS = os.path.join(tmp, "capas")
        try:
            ruta_fits = os.path.join(tmp, "Objeto_20261024_210000.fits")
            fits.writeto(ruta_fits, np.zeros((3, 16, 16), np.float32))
            comprobar(not estrellas_aparte.hay_capas(ruta_fits) and estrellas_aparte.cargar_capas(ruta_fits) is None, "sin capas: hay_capas False y cargar_capas None")
            os.makedirs(estrellas_aparte.CARPETA_CAPAS)
            r_obj, r_est = estrellas_aparte.rutas_capas(ruta_fits)
            hdr = fits.Header(); hdr["SRCMTIME"] = float(os.path.getmtime(ruta_fits))
            fits.writeto(r_obj, np.full((3, 16, 16), 100.0, np.float32), hdr)
            fits.writeto(r_est, np.full((3, 16, 16), 32768, np.uint16), hdr)
            comprobar(estrellas_aparte.hay_capas(ruta_fits), "con la pareja y la misma fecha: hay_capas True")
            objeto, U = estrellas_aparte.cargar_capas(ruta_fits)
            comprobar(objeto.shape == (3, 16, 16) and abs(float(U.mean()) - 0.5) < 1e-3, "cargar_capas: objeto en ADU y U en fracción (uint16 / 65535)")
            hdr["SRCMTIME"] = float(os.path.getmtime(ruta_fits)) - 100
            fits.writeto(r_obj, np.zeros((3, 16, 16), np.float32), hdr, overwrite=True)
            comprobar(not estrellas_aparte.hay_capas(ruta_fits), "un FITS más nuevo que sus capas invalida la pareja")
            base_nueva = os.path.join(tmp, "Pacman_20261024_210000")
            estrellas_aparte.renombrar_capas(os.path.splitext(ruta_fits)[0], base_nueva)
            comprobar(os.path.isfile(os.path.join(estrellas_aparte.CARPETA_CAPAS, "Pacman_20261024_210000" + estrellas_aparte.SUFIJO_OBJETO)), "renombrar_capas sigue a la foto")
            estrellas_aparte.borrar_capas(base_nueva)
            comprobar(not os.listdir(estrellas_aparte.CARPETA_CAPAS), "borrar_capas deja la carpeta vacía")
        finally:
            estrellas_aparte.CARPETA_CAPAS = carpeta_antes


def prueba_progreso():
    print("5. Lector de --machine-progress")
    f, e = estrellas_aparte._leer_progreso('{"schema":"starnetastro.cli.progress.v1","event":"progress","stage":"inference","current":9,"total":36,"percent":25.0}')
    comprobar(f == 0.25 and not e, "progress percent 25 → 0,25")
    f, e = estrellas_aparte._leer_progreso('{"schema":"starnetastro.cli.progress.v1","event":"error","stage":"processing","ok":false,"message":"Creating failed for FITS image\\nmas"}')
    comprobar(f is None and e == "Creating failed for FITS image", "error → mensaje de la primera línea")
    comprobar(estrellas_aparte._leer_progreso("Info [input_range]: scalar_samples=1") == (None, ""), "texto suelto → nada")
    comprobar(estrellas_aparte._leer_progreso('{"schema":"x","event":"finish"}')[0] == 1.0, "finish → 1,0")



def prueba_cola():
    print("15. Cola de estrellas aparte y un solo StarNet2 a la vez")
    import time
    import threading
    import cola_estrellas
    hechas, errores = [], []

    def trabajo(nombre, segundos, fallar=False):
        def t(progreso):
            progreso("trabajando en " + nombre)
            time.sleep(segundos)
            if fallar:
                raise RuntimeError("fallo a propósito")
            hechas.append(nombre)
            return "hecha " + nombre
        return t
    inicio = time.time()
    d1 = cola_estrellas.encolar("a.jpg", trabajo("a", 0.3))
    d2 = cola_estrellas.encolar("b.jpg", trabajo("b", 0.1, fallar=True))
    d3 = cola_estrellas.encolar("c.jpg", trabajo("c", 0.1))
    comprobar(time.time() - inicio < 0.1, "encolar vuelve al momento (el guardado no espera)")
    comprobar((d1, d2, d3) == (0, 1, 2), f"cuántas hay por delante: {(d1, d2, d3)}")
    time.sleep(0.1)
    e = cola_estrellas.estado()
    comprobar(e["archivo"] == "a.jpg" and e["pendientes"] == ["b.jpg", "c.jpg"] and "a" in e["mensaje"], f"estado para la web: {e['archivo']}, {e['pendientes']}")
    comprobar(cola_estrellas.esperar(5), "la cola se vacía")
    e = cola_estrellas.estado()
    comprobar(hechas == ["a", "c"], f"de una en una y por orden; un fallo no para las demás: {hechas}")
    comprobar(e["hechas"] >= 2 and e["ultima"] == "hecha c" and e["ultimo_ok"], f"la última: {e['ultima']}")

    # Un solo StarNet2 a la vez: dos separaciones del mismo FITS a la vez → una pasa StarNet2, la otra usa sus capas
    carpeta_antes, separar_antes = estrellas_aparte.CARPETA_CAPAS, estrellas_aparte.separar
    with tempfile.TemporaryDirectory() as tmp:
        estrellas_aparte.CARPETA_CAPAS = os.path.join(tmp, "capas")
        ruta_fits = os.path.join(tmp, "Objeto_20261025_210000.fits")
        fits.writeto(ruta_fits, np.full((3, 16, 16), 1000.0, np.float32))
        a_la_vez, maximo, llamadas, prioridades = [0], [0], [0], []
        def falso(cubo, progreso=None, cabecera=None, prioridad_baja=False):
            a_la_vez[0] += 1; llamadas[0] += 1; prioridades.append(prioridad_baja)
            maximo[0] = max(maximo[0], a_la_vez[0])
            time.sleep(0.3)
            a_la_vez[0] -= 1
            return cubo * 0.5, np.zeros_like(cubo)
        estrellas_aparte.separar = falso
        try:
            mensajes = []
            hilos = [threading.Thread(target=estrellas_aparte.separar_fits, args=(ruta_fits,),
                                      kwargs={"progreso": lambda f, t: mensajes.append(t), "prioridad_baja": i == 0}) for i in range(2)]
            for h in hilos:
                h.start(); time.sleep(0.05)
            for h in hilos:
                h.join()
        finally:
            estrellas_aparte.separar, estrellas_aparte.CARPETA_CAPAS = separar_antes, carpeta_antes
        comprobar(maximo[0] == 1 and llamadas[0] == 1, f"un solo StarNet2 a la vez y la segunda usa las capas de la primera ({llamadas[0]} llamada)")
        comprobar(any("Esperando" in (t or "") for t in mensajes), "la segunda dice que espera")
        comprobar(prioridades == [True], "la de la cola, con prioridad baja")


if __name__ == "__main__":
    prueba_pantalla()
    base, capa, aj = prueba_procesado()
    prueba_paridad(base, capa, aj)
    prueba_capas_guardadas()
    prueba_progreso()
    prueba_cola()
    print()
    if FALLOS:
        print(f"❌ {len(FALLOS)} fallo(s):")
        for f in FALLOS:
            print("   -", f)
        sys.exit(1)
    print("✅ Todo en orden")
