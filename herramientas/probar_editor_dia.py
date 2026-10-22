# herramientas/probar_editor_dia.py
#
# Prueba sin equipo del editor de día de la galería (editor_dia.py + revelado_dia.py) sobre
# una galería de mentira en una carpeta temporal:
#
#   1. Una foto suelta del Sol (mosaico Bayer en crudos/, MODO dia) y el apilado de un vídeo
#      (cubo en crudos/ + apilado lineal junto al SER, MODO video): las dos se reconocen como
#      fotos de día; una foto sin crudo, no.
#   2. La vista previa es un JPEG de 800 px revelado con los ajustes, sin blanco puro.
#   3. Guardar aparta el original, sustituye el JPG, anota los ajustes en el JSON; restaurar
#      vuelve a la foto de antes.
#   4. Volver a apilar lanza el apilado del SER con el porcentaje pedido y llama al guardado
#      con los ajustes.
#
# Uso: python herramientas/probar_editor_dia.py   (desde la carpeta de ScopePower)

import os
import sys
import json
import time
import shutil
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datos_iers
datos_iers.configurar()
import cv2
from astropy.io import fits
import editor_fotos
import editor_dia
import apilado_video
import revelado_dia
from probar_apilado_video import sol_con_manchas, a_cfa_rggb, escribir_ser

RAIZ = tempfile.mkdtemp(prefix="scopepower_editor_dia_")
FOTOS = os.path.join(RAIZ, "fotos")
CRUDOS = os.path.join(FOTOS, "crudos")
VIDEOS = os.path.join(RAIZ, "videos")
for d in (FOTOS, CRUDOS, VIDEOS):
    os.makedirs(d, exist_ok=True)
# La galería de mentira: los módulos miran estas carpetas
editor_fotos.CARPETA_FOTOS = FOTOS
editor_fotos.CARPETA_ORIGINALES = os.path.join(FOTOS, "originales")
editor_dia.CARPETA_CRUDOS = CRUDOS
editor_dia.carpeta_videos = lambda ruta_config="config.ini": VIDEOS

H = W = 320
CX, CY, R = 160.0, 162.0, 110.0


def jpg_cualquiera(ruta):
    cv2.imwrite(ruta, np.full((H, W, 3), 90, np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 80])


def foto_suelta():
    rng = np.random.default_rng(1)
    cfa = a_cfa_rggb(sol_con_manchas(H, W, CX, CY, R, 0.0, rng, sigma=1.2))
    hdu = fits.PrimaryHDU(cfa)
    for k, v in (("EXPTIME", 0.0005), ("BAYERPAT", "RGGB"), ("MODO", "dia"), ("OBJETIVO", "sol"), ("CAMPO", 1.0),
                 ("NEGRO", 1500.0), ("WB_R", 1.0), ("WB_B", 1.6), ("XBINNING", 1)):
        hdu.header[k] = v
    hdu.writeto(os.path.join(CRUDOS, "Sol_20261022_120000.fits"), overwrite=True)
    jpg_cualquiera(os.path.join(FOTOS, "Sol_20261022_120000.jpg"))
    with open(os.path.join(FOTOS, "Sol_20261022_120000.json"), "w", encoding="utf-8") as f:
        json.dump({"objeto": "Sol", "fecha": "22/10/2026 12:00", "firmada": True, "activa": True, "autor": "Prueba"}, f)
    return "Sol_20261022_120000.jpg"


def foto_de_video():
    """El apilado de un vídeo, como lo deja ScopePower: cubo en crudos/, apilado lineal + .json junto al SER."""
    rng = np.random.default_rng(2)
    n = 24
    frames = [a_cfa_rggb(sol_con_manchas(H, W, CX + rng.uniform(-3, 3), CY + rng.uniform(-3, 3), R, 0.0, rng, sigma=rng.uniform(0.4, 2.5)))
              for __ in range(n)]
    meta = {"objetivo": "sol", "orientacion": "identidad", "negro": 1500.0, "balance": {"r": 1.0, "b": 1.6}, "exposicion_s": 0.0005,
            "instante": time.time(), "patron": "RGGB"}
    ruta_ser = escribir_ser(VIDEOS, "Sol_20261022_130000", frames, meta)
    r = apilado_video.apilar(ruta_ser, pct=50)
    r["lector"].cerrar()
    limbo, __ = apilado_video.medir_limbo(r["media"])
    info = {k: r[k] for k in apilado_video.CLAVES_INFO}
    info.update({"pct": 50, "negro": 1500.0, "balance": meta["balance"], "limbo_px": limbo, "instante": time.time()})
    apilado_video.guardar_apilado(ruta_ser, r["media"], info)
    rgb8, cubo16, __ = apilado_video.revelar(r["media"], 1500.0, meta["balance"], fwhm_px=limbo)
    hdu = fits.PrimaryHDU(np.stack([cubo16[..., i] for i in range(3)]))
    for k, v in (("EXPTIME", 0.0005), ("MODO", "video"), ("OBJETIVO", "sol"), ("VIDEO", "Sol_20261022_130000"),
                 ("NFRAMES", n), ("NUSADOS", r["usados"]), ("XBINNING", 1)):
        hdu.header[k] = v
    hdu.writeto(os.path.join(CRUDOS, "Sol_20261022_130000_video.fits"), overwrite=True)
    cv2.imwrite(os.path.join(FOTOS, "Sol_20261022_130000_video.jpg"), cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    with open(os.path.join(FOTOS, "Sol_20261022_130000_video.json"), "w", encoding="utf-8") as f:
        json.dump({"objeto": "Sol", "fecha": "22/10/2026 13:00", "firmada": True, "activa": True,
                   "video": {"nombre": "Sol_20261022_130000", "frames": n, "usados": r["usados"], "pct": 50}}, f)
    return "Sol_20261022_130000_video.jpg", ruta_ser


def esperar(cond, segundos=120):
    for __ in range(int(segundos * 10)):
        if cond():
            return True
        time.sleep(0.1)
    return False


try:
    suelta = foto_suelta()
    video, ruta_ser = foto_de_video()
    jpg_cualquiera(os.path.join(FOTOS, "Otra_20261022_140000.jpg"))
    assert editor_dia.es_foto_dia(suelta) and editor_dia.es_foto_dia(video) and not editor_dia.es_foto_dia("Otra_20261022_140000.jpg")
    i1, i2 = editor_dia.info(suelta), editor_dia.info(video)
    assert i1["modo"] == "dia" and i1["video"] is None and i1["ajustes"] == revelado_dia.normalizar(None), i1
    assert i2["modo"] == "video" and i2["video"]["reapilable"] and i2["video"]["apilado_guardado"] and i2["video"]["usados"] >= 10, i2["video"]
    print(f"✅ fotos de día reconocidas: suelta ({i1['ancho']}×{i1['alto']}) y vídeo ({i2['video']['usados']} de {i2['video']['frames']} fotogramas, limbo {i2['video']['limbo_px']})")

    # 2. Vista previa
    for nombre in (suelta, video):
        t0 = time.perf_counter()
        jpg = editor_dia.vista_previa(nombre, {"detalle": 1.5, "color": 3, "nitidez": 1})
        dt = time.perf_counter() - t0
        im = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        assert im is not None and max(im.shape[:2]) <= editor_dia.ANCHO_PREVIEW, im.shape
        d = im.max(axis=2) > 25
        blanco = float(np.mean(im.min(axis=2)[d] >= 250))
        assert blanco < 0.005, f"{nombre}: blanco puro {blanco:.3%}"
        assert float(im[..., 2][d].mean()) > float(im[..., 0][d].mean()), "el Sol de prueba es cálido: más rojo que azul"
        print(f"✅ vista previa de {nombre}: {im.shape[1]}×{im.shape[0]} en {dt:.2f} s, blanco puro {blanco:.2%}")
    # Sin disco medido (paisaje) el afinado sencillo también vale
    assert editor_dia.cargar_lineal(suelta)[1] is not None

    # 3. Guardar y restaurar
    ruta_jpg = os.path.join(FOTOS, suelta)
    antes = open(ruta_jpg, "rb").read()
    editor_dia.guardar(suelta, {"exposicion": -0.3, "color": 2.0, "firma": True})
    assert esperar(lambda: not editor_dia.EDICION["en_curso"]), editor_dia.EDICION
    assert editor_dia.EDICION["ok"], editor_dia.EDICION
    despues = open(ruta_jpg, "rb").read()
    assert despues != antes and os.path.isfile(editor_fotos.ruta_original(os.path.splitext(ruta_jpg)[0]))
    im = cv2.imread(ruta_jpg)
    assert im.shape[:2] == (H, W), im.shape
    with open(os.path.splitext(ruta_jpg)[0] + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    assert abs(meta["ajustes_dia"]["exposicion"] + 0.3) < 1e-6 and meta["ajustes_dia"]["color"] == 2.0 and meta["firmada"], meta
    assert editor_dia.info(suelta)["hay_original"]
    editor_dia.restaurar(suelta)
    assert open(ruta_jpg, "rb").read() == antes and not editor_dia.info(suelta)["hay_original"]
    print("✅ guardar (original apartado, ajustes en el JSON) y restaurar")

    # 4. Volver a apilar
    llamadas = []

    def guardar_cb(rgb8, cubo16, info):
        llamadas.append(info)
        cv2.imwrite(os.path.join(FOTOS, video), cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR))
        return {"archivo": video}
    try:
        editor_dia.reapilar(suelta, 25, guardar_cb)
        raise AssertionError("una foto suelta no se puede volver a apilar")
    except editor_dia.ErrorEditorDia:
        pass
    editor_dia.reapilar(video, 25, guardar_cb, ajustes={"detalle": 2.0, "color": 1.5}, contexto={"rotacion_campo": "nunca"})
    t = apilado_video.trabajo_apilado_video
    t.esperar(120)
    e = t.estado()
    assert e["terminado"] and not e["error"], e
    assert llamadas and llamadas[0]["pct"] == 25 and abs(llamadas[0]["ajustes"]["detalle"] - 2.0) < 1e-6, llamadas[0].get("ajustes")
    assert llamadas[0]["usados"] >= 10 and llamadas[0]["usados"] < i2["video"]["usados"], (llamadas[0]["usados"], i2["video"]["usados"])
    assert editor_dia.info(video)["hay_original"], "al volver a apilar se aparta el original"
    print(f"✅ volver a apilar: {llamadas[0]['usados']} fotogramas con el 25 % (antes {i2['video']['usados']} con el 50 %), ajustes pasados al guardado")
    print("✅ probar_editor_dia: todo correcto")
finally:
    shutil.rmtree(RAIZ, ignore_errors=True)
