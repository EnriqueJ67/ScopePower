# herramientas/probar_montaje_timelapse.py
#
# Prueba sintética del vídeo de un timelapse del Sol (montaje_timelapse.py), sin cámara:
#
#   - Serie de 24 «fotos» de un Sol con oscurecimiento del limbo que pasea por el encuadre
#     (como el 28-nov-2026, con el guiado mal y correcciones a mano) y con 5 tapadas por
#     una rama oscura; una sin disco (fuera de campo).
#   - El vídeo se puede leer, con un códec que el navegador reproduce (VP8 o H.264), el
#     disco queda centrado en todos los fotogramas, se quitan las tapadas y la de sin disco,
#     y no se amplía por encima del tamaño de las fotos.
#   - Los ajustes neutros no cambian nada; la exposición aclara; los valores fuera de
#     límites se recortan; el resumen guarda ajustes y opciones para rehacerlo.
#   - Sin centrar ni quitar: todos los fotogramas, y el disco donde estaba.
#
#   python herramientas\probar_montaje_timelapse.py
#
# Sale con código 0 si todo pasa y 1 si no.

import os
import sys
import json
import math
import shutil
import tempfile

import numpy as np
import cv2

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import montaje_timelapse as m      # noqa: E402

LADO, RADIO = 400, 100
fallos = []


def comprobar(cond, texto):
    print(("  ✅ " if cond else "  ❌ ") + texto)
    if not cond:
        fallos.append(texto)


def sol(cx, cy, tapado=False, sin_disco=False):
    yy, xx = np.mgrid[:LADO, :LADO].astype(np.float32)
    r = np.hypot(xx - cx, yy - cy) / RADIO
    cobertura = np.clip(RADIO * (1.0 - r) + 0.5, 0.0, 1.0)          # borde con antialiasing
    limbo = 1.0 - 0.6 * (1.0 - np.sqrt(np.clip(1.0 - r * r, 0.0, 1.0)))
    lum = 12.0 + 190.0 * cobertura * limbo
    if sin_disco:
        lum = np.full_like(lum, 12.0)
    if tapado:          # rama oscura que cruza el disco
        rama = np.abs((yy - cy) - 0.4 * (xx - cx)) < 18
        lum[rama] = 15.0
    rgb = np.stack([lum * 0.35, lum * 0.75, lum], axis=-1)            # BGR anaranjado
    ruido = np.random.default_rng(int(cx * 7 + cy)).normal(0, 1.5, rgb.shape)
    return np.clip(rgb + ruido, 0, 255).astype(np.uint8)


def main():
    tmp = tempfile.mkdtemp(prefix="tl_")
    carpeta = os.path.join(tmp, "20261128_135015_sol")
    os.makedirs(carpeta)
    try:
        tapadas = {6, 7, 8, 15, 16}
        posiciones = []
        for i in range(24):
            cx = 200 + 60 * math.sin(i / 4.0)
            cy = 200 + 45 * math.cos(i / 5.0)
            posiciones.append((cx, cy))
            img = sol(cx, cy, tapado=i in tapadas, sin_disco=(i == 20))
            cv2.imwrite(os.path.join(carpeta, "foto_%04d.jpg" % (i + 1)), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        fotos = sorted(f for f in os.listdir(carpeta) if f.startswith("foto_"))

        print("Montaje con el disco centrado y sin las fotos tapadas")
        r = m.montar(carpeta, fotos, fps=10, ancho=1920)
        comprobar(r["codec"] in ("vp8", "h264"), "códec reproducible en el navegador (%s, %s)" % (r["codec"], r["archivo"]))
        comprobar(r["ancho"] == LADO, "no amplía por encima de las fotos (%d px)" % r["ancho"])
        quitadas = {q[0] for q in r["quitados"]}
        esperadas = {"foto_%04d.jpg" % (i + 1) for i in tapadas | {20}}
        comprobar(quitadas == esperadas, "quita las 5 tapadas y la de sin disco (%s)" % sorted(quitadas))
        comprobar(r["fotogramas"] == 24 - len(esperadas), "%d fotogramas en el vídeo" % r["fotogramas"])
        comprobar(abs(r["radio_px"] - RADIO) < 3, "radio de la serie %.1f px (de %d)" % (r["radio_px"], RADIO))
        cap = cv2.VideoCapture(os.path.join(carpeta, r["archivo"]))
        desvios, n = [], 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            n += 1
            p = m.localizar(f, RADIO)
            if p:
                desvios.append(math.hypot(p[0] - (LADO - 1) / 2.0, p[1] - (LADO - 1) / 2.0))
        cap.release()
        comprobar(n == r["fotogramas"], "el vídeo se lee entero (%d fotogramas)" % n)
        comprobar(desvios and max(desvios) < 2.5, "disco en el centro en todos (máx. %.1f px)" % (max(desvios) if desvios else -1))
        with open(os.path.join(carpeta, m.RESUMEN), encoding="utf-8") as f:
            guardado = json.load(f)
        comprobar(guardado["ajustes"] == m.AJUSTES_NEUTROS and guardado["alinear"] and guardado["quitar_tapadas"],
                  "el resumen guarda ajustes y opciones")
        comprobar(m.video_de(carpeta) == r["archivo"] and m.reproducible(carpeta), "la lista lo da como reproducible")
        comprobar(m.foto_de_muestra(carpeta, fotos) == "foto_0001.jpg", "la foto de muestra es la primera útil")
        mini = cv2.imread(os.path.join(carpeta, m.MINIATURA_VIDEO))
        comprobar(mini is not None and max(mini.shape[:2]) == 320, "miniatura del primer fotograma para la galería")

        print("Ajustes")
        img = cv2.imread(os.path.join(carpeta, fotos[0]))
        comprobar(m.aplicar_ajustes(img, {}) is img, "sin ajustes la foto no cambia")
        clara = m.aplicar_ajustes(img, {"exposicion": 1.0})
        comprobar(clara.mean() > img.mean() * 1.5, "+1 EV aclara (%.0f → %.0f)" % (img.mean(), clara.mean()))
        calida = m.aplicar_ajustes(img, {"temperatura": 1.0})
        comprobar(calida[..., 2].mean() > img[..., 2].mean() and calida[..., 0].mean() < img[..., 0].mean(), "más cálido: más rojo y menos azul")
        gris = m.aplicar_ajustes(img, {"saturacion": 0.0})
        comprobar(np.abs(gris[..., 0].astype(int) - gris[..., 2]).mean() < 2, "saturación 0: blanco y negro")
        a = m.ajustes_validos({"exposicion": 9, "contraste": "x", "nitidez": -1})
        comprobar(a["exposicion"] == 2.0 and a["contraste"] == 1.0 and a["nitidez"] == 0.0, "valores fuera de límites recortados")

        print("Sin centrar ni quitar")
        r2 = m.montar(carpeta, fotos, fps=10, alinear=False, quitar_tapadas=False, ajustes={"exposicion": 0.5})
        comprobar(r2["fotogramas"] == 24 and not r2["quitados"] and not r2["centrado"], "todas las fotos, sin centrar")
        comprobar(len([f for f in os.listdir(carpeta) if f.startswith("timelapse.") and f != m.RESUMEN]) == 1,
                  "queda un solo vídeo en la carpeta")
        comprobar(m.leer_resumen(carpeta)["ajustes"]["exposicion"] == 0.5, "el resumen guarda los ajustes nuevos")

        print("Un MP4 viejo (mp4v, sin resumen) no se da por reproducible")
        vieja = os.path.join(tmp, "20261101_100000_sol")
        os.makedirs(vieja)
        open(os.path.join(vieja, "timelapse.mp4"), "wb").close()
        comprobar(m.video_de(vieja) == "timelapse.mp4" and not m.reproducible(vieja), "se ofrece rehacerlo")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fallos:
        print("❌ %d comprobación(es) fallida(s)" % len(fallos))
        return 1
    print("✅ Montaje del timelapse: todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
