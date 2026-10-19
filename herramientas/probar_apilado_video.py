# herramientas/probar_apilado_video.py
#
# Prueba sin equipo del vídeo del modo en vivo y del apilado de los mejores fotogramas:
#
#   1. Desmosaicado: un mosaico RGGB sintético vuelve con los colores en su sitio (OpenCV
#      nombra el patrón al revés y es fácil equivocarse).
#   2. Apilado: un Sol sintético de 16 bits, 48 fotogramas con desplazamientos de hasta
#      ±6 px, desenfoque variable y ruido, escrito en SER con grabador_video.py. El apilado
#      debe elegir los nítidos, alinearlos al subpíxel y bajar el ruido.
#   3. El trabajo en segundo plano (TrabajoApiladoVideo) con la llamada de guardado.
#   4. La grabación desde el modo en vivo con una cámara falsa: el SER y su .json quedan en
#      disco con fotogramas, y la cadencia de la cámara se sube al grabar y se baja al parar.
#   5. Rotación de campo: el norte celeste calculado en la imagen apunta donde debe, gira al
#      ritmo de la fórmula de la altacimutal y en sentido contrario si la imagen va en espejo;
#      un Sol sintético que gira 4° durante el vídeo sale nítido con la corrección y
#      emborronado sin ella.
#
# Uso: python herramientas/probar_apilado_video.py   (desde la carpeta de ScopePower)

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
import apilado_video
import grabador_video
import vivo_dia

datos_iers.configurar()          # astropy sin red, como en la app
OBSERVATORIO = (39.6, -0.5, 200.0)
H_PRUEBA = [[2.9 / 3600, 0.0], [0.0, -2.9 / 3600]]     # columna → +acimut, fila hacia abajo → −altura


def sol_rgb(h, w, cx, cy, r, sigma, rng, ruido=300.0):
    """Disco solar en color (RGB float, 16 bits) con oscurecimiento del limbo, una mancha, desenfoque y ruido."""
    import cv2
    yy, xx = np.mgrid[0:h, 0:w]
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    disco = np.clip(1.0 - d2 / r ** 2, 0.0, 1.0)
    lum = 40000.0 * (0.55 + 0.45 * np.sqrt(disco)) * (d2 < r ** 2)
    mancha = ((xx - cx - r * 0.3) ** 2 + (yy - cy + r * 0.2) ** 2) < (r * 0.08) ** 2
    lum[mancha] *= 0.35
    rgb = np.dstack([lum * 1.0, lum * 0.8, lum * 0.55]) + 1500.0
    if sigma > 0:
        rgb = cv2.GaussianBlur(rgb.astype(np.float32), (0, 0), sigma)
    return rgb + rng.normal(0, ruido, rgb.shape)


def a_cfa_rggb(rgb):
    cfa = np.empty(rgb.shape[:2], np.float32)
    cfa[0::2, 0::2] = rgb[0::2, 0::2, 0]
    cfa[0::2, 1::2] = rgb[0::2, 1::2, 1]
    cfa[1::2, 0::2] = rgb[1::2, 0::2, 1]
    cfa[1::2, 1::2] = rgb[1::2, 1::2, 2]
    return np.clip(cfa, 0, 65535).astype(np.uint16)


def centroide(g, umbral):
    m = g > umbral
    yy, xx = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    return float(xx[m].mean()), float(yy[m].mean())


def probar_desmosaicado():
    rgb = np.zeros((16, 16, 3), np.float32)
    rgb[..., 0], rgb[..., 1], rgb[..., 2] = 10000.0, 30000.0, 50000.0
    out = apilado_video.desmosaicar(a_cfa_rggb(rgb), "RGGB")
    centro = out[6:10, 6:10].reshape(-1, 3).mean(axis=0)
    assert np.allclose(centro, [10000, 30000, 50000], atol=1.0), centro
    g = apilado_video.verde(a_cfa_rggb(rgb), "RGGB")
    assert g.shape == (8, 8) and abs(float(g.mean()) - 30000.0) < 1.0, g.mean()
    print("✅ desmosaicado RGGB: R, G y B en su sitio")


def escribir_ser(carpeta, nombre, frames, meta):
    g = grabador_video.Grabacion(carpeta, ("ser",), ancho=frames[0].shape[1], alto=frames[0].shape[0], fps=20,
                                 bayer="RGGB", bits=16, nombre=nombre, disco_minimo=0)
    for f in frames:
        g.encolar(f)
    for __ in range(100):
        if g.frames >= len(frames):
            break
        time.sleep(0.05)
    g.cerrar()
    with open(os.path.join(carpeta, nombre + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return g.rutas["ser"]


def probar_apilado(carpeta):
    rng = np.random.default_rng(7)
    h = w = 320
    n, nitidos = 48, 12
    desplazamientos, frames = [], []
    for i in range(n):
        dx, dy = rng.uniform(-6, 6, 2)
        sigma = rng.uniform(0.3, 0.8) if i < nitidos else rng.uniform(2.0, 4.0)
        frames.append(a_cfa_rggb(sol_rgb(h, w, 160.0 + dx, 165.0 + dy, 90.0, sigma, rng)))
        desplazamientos.append((dx, dy))
    # Un fotograma con una nube (mucho más oscuro) que debe descartarse por brillo
    frames[20] = (frames[20] // 4).astype(np.uint16)
    meta = {"objetivo": "sol", "campo": 0.35, "exposicion_s": 0.0005, "ganancia": 0, "patron": "RGGB", "formato": "RAW16",
            "bits": 16, "orientacion": "traspuesta", "px_um": 3.76, "negro": 1500.0, "balance": {"r": 1.0, "b": 1.6},
            "instante": time.time()}
    ruta = escribir_ser(carpeta, "Sol_20261017_120000", frames, meta)
    cab = grabador_video.leer_cabecera_ser(ruta)
    assert cab["frames"] == n and cab["bits"] == 16 and cab["color"] == 8 and cab["con_tiempos"], cab

    lector = apilado_video.LectorSER(ruta)
    assert lector.frames == n and lector.patron == "RGGB" and lector.duracion_s() is not None
    assert np.array_equal(lector.fotograma(5), frames[5]), "el memmap no devuelve el fotograma tal cual"
    info = apilado_video.info_video(ruta)
    assert info["frames"] == n and info["objetivo"] == "sol" and info["mb"] > 9, info

    t0 = time.perf_counter()
    r = apilado_video.apilar(ruta, pct=25)
    dt = time.perf_counter() - t0
    elegidos = set(r["indices"])
    assert r["descartados_brillo"] == 1 and 20 not in elegidos, (r["descartados_brillo"], elegidos)
    assert len(elegidos & set(range(nitidos))) >= 10, f"elegidos {sorted(elegidos)}: deberían ser los nítidos"
    assert r["no_alineados"] == 0 and r["usados"] == len(elegidos), (r["no_alineados"], r["usados"])
    # Alineado: el disco del apilado está donde estaba en la referencia
    ref = frames[r["referencia"]]
    g_ref = apilado_video.verde(ref, "RGGB")
    g_media = apilado_video.verde(a_cfa_rggb(r["media"]), "RGGB")
    cx_ref, cy_ref = centroide(g_ref, 15000)
    cx_med, cy_med = centroide(g_media, 15000)
    error = float(np.hypot(cx_ref - cx_med, cy_ref - cy_med))
    assert error < 0.15, f"el apilado está a {error:.2f} px (a media resolución) de la referencia"
    # Ruido: en el centro del disco el apilado es más liso que un fotograma suelto
    zona = (slice(150, 180), slice(140, 170))
    ruido_uno = float(apilado_video.desmosaicar(ref, "RGGB")[zona][..., 1].std())
    ruido_media = float(r["media"][zona][..., 1].std())
    assert ruido_media < ruido_uno * 0.6, (ruido_uno, ruido_media)
    rgb8, cubo16, afinado = apilado_video.revelar(r["media"], 1500.0, {"r": 1.0, "b": 1.6}, cantidad_nitidez=1.0)
    assert rgb8.dtype == np.uint8 and rgb8.shape == (h, w, 3) and cubo16.dtype == np.uint16 and cubo16.max() > 60000
    assert rgb8[165, 160, 0] > 200, "el centro del Sol revelado debería estar cerca del blanco"
    assert afinado["iteraciones"] == 0 and afinado["psf_px"] is None, afinado      # sin limbo medido: afinado sencillo
    # Limbo y deconvolución: un apilado desenfocado a propósito (σ 1,5 px → FWHM ≈ 3,5 px) se mide y se afina
    import cv2
    borroso = cv2.GaussianBlur(r["media"], (0, 0), 1.5)
    limbo, info_limbo = apilado_video.medir_limbo(borroso)
    assert limbo and 2.5 < limbo < 5.5, (limbo, info_limbo)
    nitida = apilado_video.deconvolucionar_disco(np.clip(borroso - 1500.0, 0, None), 8, limbo)
    limbo2, __ = apilado_video.medir_limbo(nitida)
    assert limbo2 and limbo2 < 0.8 * limbo, f"el limbo no se estrecha: {limbo:.2f} → {limbo2}"
    pico_antes, pico_despues = float(borroso[..., 1].max() - 1500.0), float(nitida[..., 1].max())
    assert pico_despues < 1.08 * pico_antes, f"anilla: pico {pico_antes:.0f} → {pico_despues:.0f}"
    rgb8n, __, afinado = apilado_video.revelar(borroso, 1500.0, {"r": 1.0, "b": 1.6}, cantidad_nitidez=2.0, fwhm_px=limbo)
    assert afinado["iteraciones"] == 8 and abs(afinado["psf_px"] - 0.85 * limbo) < 0.02, afinado
    assert rgb8n.shape == (h, w, 3)
    print(f"✅ limbo: {limbo:.2f} px desenfocado → {limbo2:.2f} px tras 8 iteraciones (pico {pico_despues / pico_antes:.3f}×)")
    r["lector"].cerrar()
    print(f"✅ apilado: {r['usados']} de {n} fotogramas (los {nitidos} nítidos entre ellos), desplazamiento máximo "
          f"{r['desplazamiento_max_px']} px, apilado a {error:.3f} px de la referencia, ruido ×{ruido_media / ruido_uno:.2f}, {dt:.1f} s")
    return ruta


def probar_trabajo(ruta):
    guardado = {}

    def guardar(rgb8, cubo16, info):
        guardado.update({"forma": rgb8.shape, "usados": info["usados"], "meta": info["meta"].get("objetivo")})
        return {"archivo": "Sol_prueba_video.jpg"}
    t = apilado_video.TrabajoApiladoVideo()
    t.iniciar(ruta, guardar, pct=25, cantidad_nitidez=1.0,
              contexto={"observatorio": OBSERVATORIO, "orientacion": {"H": H_PRUEBA, "binning_foto": 1}})
    t.esperar(120)
    e = t.estado()
    assert not e["en_curso"] and e["terminado"] and not e["error"], e
    assert e["resultado"]["giro_max_deg"] is not None and not e["resultado"]["giro_motivo"], e["resultado"]
    assert e["resultado"]["archivo"] == "Sol_prueba_video.jpg" and guardado["usados"] == e["resultado"]["usados"], (e, guardado)
    assert guardado["meta"] == "sol" and e["progreso"] == 1.0
    assert e["resultado"]["limbo_px"] and e["resultado"]["iteraciones"] == 4 and not e["resultado"]["reafinado"], e["resultado"]
    assert apilado_video.hay_apilado(ruta), "el apilado lineal debería quedar guardado junto al SER"
    print("✅ trabajo en segundo plano:", e["mensaje"])
    # Reafinar: sin volver a apilar, con otro afinado; vuelve a guardar en la galería
    guardado.clear()
    t.iniciar(ruta, guardar, cantidad_nitidez=2.5, reafinar=True)
    t.esperar(60)
    e = t.estado()
    assert not e["error"] and e["resultado"]["reafinado"] and e["resultado"]["iteraciones"] == 10, e
    assert guardado["usados"] == e["resultado"]["usados"] and e["resultado"]["segundos"] < 30, (guardado, e["resultado"])
    print("✅ reafinado:", e["mensaje"])


class CamaraFalsa:
    """Lo justo de la interfaz de camara_nativa.CamaraNativa que usa el modo en vivo."""

    def __init__(self):
        self.propiedades = {"modelo": "Falsa", "ancho": 256, "alto": 256, "bayer": "RGGB", "pixel_um": 3.76}
        self.limites_fps = []
        self.rng = np.random.default_rng(3)
        self.exposicion_ms = 1.0
        self.config_ids = {"FrameLimit": 26}

    def atributos(self):
        return [{"nombre": "Gain", "min": 0, "max": 600}, {"nombre": "Exposure", "min": 50, "max": 2_000_000}]

    def configurar(self, roi_px=256, binning=1, formato="RAW16", exposicion_ms=1.0, ganancia=None, fps_max=None, **kw):
        self.exposicion_ms = exposicion_ms
        self.limites_fps.append(fps_max)
        return {"x0": 0, "y0": 0, "lado": 256, "ancho": 256, "alto": 256, "binning": 1, "formato": "RAW16", "exposicion_ms": exposicion_ms}

    def poner(self, nombre, valor, auto=False):
        if nombre == "FrameLimit":
            self.limites_fps.append(valor)

    def exposicion(self, exposicion_ms):
        self.exposicion_ms = exposicion_ms

    def iniciar_video(self):
        pass

    def leer_frame(self, timeout_ms=None):
        time.sleep(0.02)
        return a_cfa_rggb(sol_rgb(256, 256, 128.0, 128.0, 70.0, 0.5, self.rng))

    def cerrar(self):
        pass


def probar_grabacion_vivo(carpeta):
    cam = CamaraFalsa()
    vivo = vivo_dia.TrabajoVivo()
    vivo.iniciar(None, "sol", 1.0, compensacion=0.0, fabrica_camara=lambda: cam)
    for __ in range(100):
        if vivo.estado()["fase"] == "en vivo":
            break
        time.sleep(0.1)
    assert vivo.estado()["fase"] == "en vivo", vivo.estado()
    time.sleep(0.5)
    vivo.grabar(duracion_s=1.5, fps=30, carpeta=carpeta, extra_meta={"observatorio": list(OBSERVATORIO), "H_camara": H_PRUEBA})
    time.sleep(0.5)
    e = vivo.estado()
    assert e["grabacion"] and e["grabacion"]["activa"] and e["grabacion"]["nombre"].startswith("Sol_"), e["grabacion"]
    time.sleep(2.0)
    e = vivo.estado()
    assert e["grabacion"] is None and e["ultima_grabacion"] and e["ultima_grabacion"]["frames"] >= 20, e
    assert not e["error_grabacion"], e["error_grabacion"]
    ruta = e["ultima_grabacion"]["ruta"]
    cab = grabador_video.leer_cabecera_ser(ruta)
    assert cab["frames"] == e["ultima_grabacion"]["frames"] and cab["bits"] == 16 and cab["color"] == 8, cab
    with open(os.path.splitext(ruta)[0] + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["objetivo"] == "sol" and meta["frames"] == cab["frames"] and meta["patron"] == "RGGB" and meta["bits"] == 16, meta
    assert meta["observatorio"] == list(OBSERVATORIO) and meta["H_camara"] == H_PRUEBA, meta
    assert 30 in cam.limites_fps and cam.limites_fps[-1] == vivo_dia.FPS_CAMARA, cam.limites_fps
    vivo.parar()
    vivo.esperar(10)
    print(f"✅ grabación desde el vivo: {cab['frames']} fotogramas en {e['ultima_grabacion']['segundos']} s, "
          f"cadencia {cam.limites_fps}")


def probar_angulos_norte():
    """El norte calculado en la imagen, su ritmo de giro y el sentido con la imagen en espejo."""
    import math
    from astropy.time import Time
    import astropy.units as u
    lat, lon, __ = OBSERVATORIO
    t0 = Time("2024-03-20T12:00:00", scale="utc")
    tsl = t0.sidereal_time("apparent", longitude=lon * u.deg).deg
    dec = -10.0                                     # en el meridiano, al sur, a 90 − 39,6 − 10 = 40,4° de altura
    tiempos = t0.unix + np.array([-60.0, 0.0, 60.0])
    th = apilado_video.angulos_norte(tiempos, OBSERVATORIO, np.array(H_PRUEBA), radec=(tsl, dec))
    # Al sur en el meridiano el norte celeste queda hacia el cénit: arriba en la imagen (fila decreciente)
    assert abs(th[1] - (-90.0)) < 0.5, th
    alt = 90.0 - (lat - dec)
    w = math.degrees(7.2921e-5 * math.cos(math.radians(lat)) / math.cos(math.radians(alt))) * 120.0
    giro = th[2] - th[0]
    assert abs(abs(giro) - w) < 0.03 * w, (giro, w)
    espejo = np.array(H_PRUEBA) @ np.diag([-1.0, 1.0])
    th_e = apilado_video.angulos_norte(tiempos, OBSERVATORIO, espejo, radec=(tsl, dec))
    assert abs((th_e[2] - th_e[0]) + giro) < 0.01 * w, (th_e, th)
    # Al este o al oeste del observador no hay giro de campo en el instante en que el astro pasa por el primer vertical:
    # basta con comprobar que el ritmo crece con la altura en el meridiano
    th_alto = apilado_video.angulos_norte(tiempos, OBSERVATORIO, np.array(H_PRUEBA), radec=(tsl, 25.0))
    assert abs(th_alto[2] - th_alto[0]) > 2 * abs(giro), (th_alto, giro)
    print(f"✅ rotación de campo: norte arriba al sur ({th[1]:.2f}°), {abs(giro):.3f}° en 2 min a {alt:.1f}° de altura "
          f"(fórmula {w:.3f}°), sentido contrario con la imagen en espejo")


def sol_con_manchas(h, w, cx, cy, r, giro_deg, rng=None, sigma=0.5):
    """Sol en color con manchas cerca del limbo, girado `giro_deg` alrededor del centro (sentido de desplazar)."""
    import cv2
    # Bordes con antialiasing (cobertura del píxel): con máscaras de píxel entero un desplazamiento de
    # 0,6 px no mueve el borde 0,6 px y la prueba no podría comprobar el alineado al subpíxel
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    d = np.hypot(xx - cx, yy - cy)
    cobertura = np.clip(r - d + 0.5, 0.0, 1.0)
    lum = 40000.0 * (0.55 + 0.45 * np.sqrt(np.clip(1.0 - (d / r) ** 2, 0.0, 1.0))) * cobertura
    a = np.radians(giro_deg)
    for ang, frac, rad in ((20, 0.85, 4.0), (110, 0.8, 3.0), (200, 0.88, 5.0), (300, 0.75, 3.5), (250, 0.5, 4.0)):
        vx, vy = frac * r * np.cos(np.radians(ang)), frac * r * np.sin(np.radians(ang))
        mx, my = cx + vx * np.cos(a) - vy * np.sin(a), cy + vx * np.sin(a) + vy * np.cos(a)
        lum *= 1.0 - 0.7 * np.clip(rad - np.hypot(xx - mx, yy - my) + 0.5, 0.0, 1.0)
    rgb = np.dstack([lum, lum * 0.8, lum * 0.55]) + 1500.0
    rgb = cv2.GaussianBlur(rgb.astype(np.float32), (0, 0), sigma)
    if rng is not None:
        rgb = rgb + rng.normal(0, 300.0, rgb.shape)
    return rgb


def probar_apilado_con_giro(carpeta):
    """Un vídeo cuyo disco gira 4° de principio a fin: con la corrección el apilado se parece a la referencia."""
    h = w = 320
    cx, cy, r = 160.0, 162.0, 110.0
    n = 24
    giros = np.linspace(-2.0, 2.0, n)
    rng = np.random.default_rng(11)
    frames = []
    for i in range(n):
        dx, dy = rng.uniform(-4, 4, 2)
        frames.append(a_cfa_rggb(sol_con_manchas(h, w, cx + dx, cy + dy, r, giros[i], rng)))
    ruta = escribir_ser(carpeta, "Sol_con_giro", frames, {"objetivo": "sol"})

    def error(res):
        """Diferencia (RMS, verde) entre el apilado y el fotograma de referencia dentro del disco: los dos en
        los ejes de la referencia, así no hace falta saber dónde quedó el disco al subpíxel."""
        ref = apilado_video.desmosaicar(frames[res["referencia"]], "RGGB")[..., 1]
        yy, xx = np.mgrid[0:h, 0:w]
        dentro = np.hypot(xx - cx, yy - cy) < 0.9 * r
        return float(np.sqrt(np.mean((res["media"][..., 1] - ref)[dentro] ** 2)))

    sin = apilado_video.apilar(ruta, pct=100)
    con = apilado_video.apilar(ruta, pct=100, angulos=giros)
    e_sin, e_con = error(sin), error(con)
    for res in (sin, con):
        res["lector"].cerrar()
    assert con["giro_max_deg"] > 1.5 and sin["giro_max_deg"] is None, (con["giro_max_deg"], sin["giro_max_deg"])
    assert e_con < 0.6 * e_sin, (e_con, e_sin)
    print(f"✅ apilado con giro de 4°: diferencia con la referencia {e_sin:.0f} sin corregir → {e_con:.0f} corregido "
          f"(giro máximo {con['giro_max_deg']:.2f}°; ruido de un fotograma 300)")


if __name__ == "__main__":
    carpeta = tempfile.mkdtemp(prefix="scopepower_video_")
    try:
        probar_angulos_norte()
        probar_apilado_con_giro(carpeta)
        probar_desmosaicado()
        ruta = probar_apilado(carpeta)
        probar_trabajo(ruta)
        probar_grabacion_vivo(carpeta)
        print("✅ probar_apilado_video: todo correcto")
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
