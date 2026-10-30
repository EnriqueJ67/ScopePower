# herramientas/probar_visor_video.py
#
# Prueba sin equipo del visor de vídeos de la galería (visor_video.py):
#
#   1. Sucesos: un Sol sintético de 120 fotogramas que tiembla un poco (seeing), con una
#      mancha y ruido, por delante del cual cruzan un pájaro grande (4 fotogramas) y un
#      puntito lejano (5 fotogramas). Deben salir los dos, en su sitio, y nada más: ni la
#      mancha ni el temblor del limbo.
#   2. Fotogramas: el JPG sale con el lado pedido, en la orientación de ASCOM, y con el mismo
#      blanco en todo el vídeo (al reproducir no parpadea). La miniatura se hace una vez.
#   3. Un punto del fotograma del SDK a la pantalla, con la traspuesta y con el giro de Cámara.
#   4. El trabajo en segundo plano: pedir, estado, resultado guardado y cancelar al borrar.
#
# Uso: python herramientas/probar_visor_video.py   (desde la carpeta de ScopePower)

import os
import sys
import time
import json
import shutil
import tempfile
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grabador_video
import visor_video

LADO = 256
N = 120
PAJARO = (70, 73)           # fotogramas en que cruza el pájaro
PUNTITO = (20, 24)          # y el lejano


def posicion_pajaro(i):
    """(x, y) del pájaro en el fotograma i, en píxeles del SDK (cruza de abajo a la izquierda hacia arriba a la derecha)."""
    f = (i - PAJARO[0]) / (PAJARO[1] - PAJARO[0])
    return 80 + 100 * f, 180 - 100 * f


def a_cfa_rggb(rgb):
    cfa = np.empty(rgb.shape[:2], np.float32)
    cfa[0::2, 0::2] = rgb[0::2, 0::2, 0]
    cfa[0::2, 1::2] = rgb[0::2, 1::2, 1]
    cfa[1::2, 0::2] = rgb[1::2, 0::2, 1]
    cfa[1::2, 1::2] = rgb[1::2, 1::2, 2]
    return np.clip(cfa, 0, 65535).astype(np.uint16)


def fotograma(i, rng):
    """Sol en color (16 bits, RGGB) que tiembla ±1,5 px, con una mancha, ruido y lo que cruce en ese fotograma."""
    import cv2
    yy, xx = np.mgrid[0:LADO, 0:LADO].astype(np.float32)
    cx, cy = LADO / 2 + rng.uniform(-1.5, 1.5), LADO / 2 + rng.uniform(-1.5, 1.5)
    r = 90.0
    d = np.hypot(xx - cx, yy - cy)
    disco = np.clip(r + 0.5 - d, 0.0, 1.0)                       # borde con antialiasing
    lum = 40000.0 * (0.55 + 0.45 * np.sqrt(np.clip(1 - (d / r) ** 2, 0, 1))) * disco
    lum *= 1.0 - 0.6 * (np.hypot(xx - cx - 30, yy - cy + 20) < 6)    # la mancha: está siempre
    if PAJARO[0] <= i <= PAJARO[1]:
        px, py = posicion_pajaro(i)
        lum *= 1.0 - 0.75 * (((xx - px) / 9) ** 2 + ((yy - py) / 5) ** 2 < 1)
    if PUNTITO[0] <= i <= PUNTITO[1]:
        lum *= 1.0 - 0.6 * (np.hypot(xx - 100 - 3 * (i - PUNTITO[0]), yy - 110) < 2.5)
    rgb = np.dstack([lum, lum * 0.8, lum * 0.55]) + 1500.0
    rgb = cv2.GaussianBlur(rgb, (0, 0), rng.uniform(0.8, 1.6))   # el seeing cambia de un fotograma a otro
    return a_cfa_rggb(rgb + rng.normal(0, 250, rgb.shape))


def escribir_video(carpeta, nombre="Sol_20261025_160129", orientacion="traspuesta"):
    """Escribe el SER sintético y su .json (como los del modo en vivo) en `carpeta`; devuelve la ruta del SER."""
    from datetime import datetime, timedelta
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre + ".ser")
    rng = np.random.default_rng(7)
    g = grabador_video.GrabadorSER(ruta, LADO, LADO, bayer="RGGB", bits=16)
    t0 = datetime(2026, 10, 25, 16, 1, 29)
    for i in range(N):
        g.escribir(fotograma(i, rng), t0 + timedelta(seconds=i / 20))
    g.cerrar()
    meta = {"objetivo": "sol", "patron": "RGGB", "negro": 1500.0, "balance": {"r": 0.8, "b": 1.45},
            "orientacion": orientacion, "instante": t0.timestamp(), "exposicion_s": 0.0002, "frames": N}
    with open(ruta[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return ruta


def probar_sucesos(ruta):
    t = time.time()
    r = visor_video.analizar(ruta)
    s = r["sucesos"]
    print(f"Análisis en {time.time() - t:.1f} s: {len(s)} sucesos: " +
          ", ".join(f"{x['inicio']}–{x['fin']} (z {x['z']:.0f})" for x in s))
    assert len(s) == 2, s
    lejano, pajaro = s
    assert lejano["inicio"] <= PUNTITO[0] + 1 and lejano["fin"] >= PUNTITO[1] - 1, lejano
    assert pajaro["inicio"] == PAJARO[0] and pajaro["fin"] == PAJARO[1], pajaro
    assert pajaro["z"] > lejano["z"] > visor_video.UMBRAL_SUCESO, s
    px, py = posicion_pajaro(pajaro["pico"])
    err = np.hypot(pajaro["x"] * LADO - px, pajaro["y"] * LADO - py)
    print(f"  pájaro en el fotograma {pajaro['pico']}: ({pajaro['x'] * LADO:.0f}, {pajaro['y'] * LADO:.0f}) px, "
          f"de verdad ({px:.0f}, {py:.0f}): {err:.1f} px de error")
    assert err < 12, err
    nit = np.array(r["nitidez"])
    # De la peor a la mejor del vídeo: el desenfoque sintético va de σ 0,8 a 1,6, la nitidez debe ocupar todo el rango
    assert len(nit) == N and nit.max() == 1.0 and nit.min() == 0.0 and 0.2 < np.median(nit) < 0.8, (nit.min(), np.median(nit), nit.max())
    assert r["fps"] and abs(r["fps"] - 20) < 0.5, r["fps"]


def probar_fotogramas(ruta):
    import cv2
    a = visor_video.fotograma_bgr(ruta, 10, lado=100)
    b = visor_video.fotograma_bgr(ruta, 11, lado=100)
    assert max(a.shape[:2]) <= 100 and a.shape[2] == 3, a.shape
    # Mismo blanco en todo el vídeo: dos fotogramas seguidos casi iguales de brillo
    assert abs(float(a.mean()) - float(b.mean())) < 3, (a.mean(), b.mean())
    # El pájaro en ASCOM (traspuesta): fila y columna cambiadas
    c = visor_video.fotograma_bgr(ruta, 71, lado=LADO)
    k = c.shape[0] / LADO
    px, py = posicion_pajaro(71)
    assert c[int(px * k), int(py * k)].mean() < 0.6 * c[int(py * k), int(px * k)].mean() or c[int(px * k), int(py * k)].mean() < 120, \
        "el pájaro no está traspuesto"
    jpg = visor_video.a_jpg(c)
    assert jpg[:2] == b"\xff\xd8"
    mini = visor_video.miniatura(ruta)
    t = os.path.getmtime(mini)
    assert visor_video.miniatura(ruta) == mini and os.path.getmtime(mini) == t, "la miniatura se rehace"
    assert cv2.imread(mini).shape[0] <= visor_video.LADO_MINIATURA
    rgb, meta, instante = visor_video.fotograma_lineal(ruta, 5)
    assert rgb.shape == (LADO, LADO, 3) and meta["negro"] == 1500.0 and abs(instante - (meta["instante"] + 0.25)) < 0.01, instante
    print(f"Fotogramas: {a.shape[1]}×{a.shape[0]} con lado 100, miniatura y fotograma lineal correctos")


def probar_a_pantalla():
    assert np.allclose(visor_video.a_pantalla(0.2, 0.7, "identidad", 200, 100), (0.2, 0.7), atol=0.02)
    assert np.allclose(visor_video.a_pantalla(0.2, 0.7, "traspuesta", 200, 100), (0.7, 0.2), atol=0.02)
    x, y = visor_video.a_pantalla(0.2, 0.7, "identidad", 200, 200, {"giro": 180.0, "espejo": False})
    assert np.allclose((x, y), (0.8, 0.3), atol=0.03), (x, y)
    print("Puntos del SDK a la pantalla: identidad, traspuesta y giro de 180° correctos")


def probar_trabajo(ruta):
    trabajo = visor_video.TrabajoAnalisis()
    os.remove(visor_video.ruta_analisis(ruta)) if os.path.exists(visor_video.ruta_analisis(ruta)) else None
    assert trabajo.estado(ruta)["estado"] == "sin_hacer"
    trabajo.pedir(ruta)
    for __ in range(600):
        if trabajo.estado(ruta)["estado"] == "hecho":
            break
        time.sleep(0.05)
    assert trabajo.estado(ruta)["estado"] == "hecho"
    assert len(visor_video.leer_analisis(ruta)["sucesos"]) == 2
    # Cancelar a medias (como al borrar el vídeo): suelta el SER y no deja análisis.
    # El análisis real se frena en su primera consulta de cancelar hasta que llegue la orden:
    # con una espera fija, el SER sintético se analizaba a veces antes de quitarlo y quedaba «hecho»
    os.remove(visor_video.ruta_analisis(ruta))
    original = visor_video.analizar
    empezado = threading.Event()

    def analizar_frenado(ruta_ser, progreso=None, cancelar=None):
        def cancelar_frenado():
            if not empezado.is_set():
                empezado.set()
                limite = time.time() + 10
                while not cancelar() and time.time() < limite:
                    time.sleep(0.01)
            return cancelar()
        return original(ruta_ser, progreso=progreso, cancelar=cancelar_frenado)

    visor_video.analizar = analizar_frenado
    try:
        trabajo.pedir(ruta)
        assert empezado.wait(10), "el análisis no empezó"
        assert trabajo.ocupado_con(ruta) and trabajo.estado(ruta)["estado"] == "en_curso", trabajo.estado(ruta)
        trabajo.quitar(ruta)
    finally:
        visor_video.analizar = original
    assert not trabajo.ocupado_con(ruta) and trabajo.estado(ruta)["estado"] == "sin_hacer", trabajo.estado(ruta)
    os.rename(ruta, ruta + ".mov")          # en Windows falla si alguien lo tiene abierto
    os.rename(ruta + ".mov", ruta)
    print("Trabajo en segundo plano: pedir, terminar y cancelar correctos")


def main():
    carpeta = tempfile.mkdtemp(prefix="visor_video_")
    try:
        ruta = escribir_video(carpeta)
        probar_sucesos(ruta)
        probar_fotogramas(ruta)
        probar_a_pantalla()
        probar_trabajo(ruta)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
    print("\n✅ Visor de vídeos correcto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
