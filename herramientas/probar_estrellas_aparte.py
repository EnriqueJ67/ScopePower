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
#   7. reducir las estrellas (catálogo + campanas de Gauss);
#   8. el estirado propio de las estrellas (intensidad, compresión de los núcleos).
#   9. el contraste local sin estrellas no levanta el ruido del cielo (borbotones).
#  10. el catálogo no convierte en estrellas grandes las débiles sobre el halo de una brillante;
#  11. al reducir, las brillantes conservan la cima blanca (sobreexposición y recorte), y
#      una cima con huecos sigue siendo una sola estrella;
#  12. el suavizado del fondo sin estrellas no deja un resplandor ni una sombra alrededor del objeto.

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
    curvas_e = {"rgb": [[0.0, 0.0], [0.5, 0.35], [1.0, 1.0]]}
    ajustes = dict(aj, temperatura=0.3, brillo=0.2, scnr=0.8, scnr_estrellas=True, saturacion=1.7, saturacion_estrellas=1.2,
                   tamano_estrellas=60, curvas=None, curvas_estrellas=curvas_e)
    servidor = np.clip(procesado.ajustes_rapidos_capas(base, capa, ajustes, fondo=procesado.fondo_cielo(base)) * 255 + 0.5, 0, 255).astype(np.uint8)
    # Lo mismo en 8 bits, paso a paso como retoque.js (tablas de 256 + píxel a píxel)
    b8 = np.clip(base * 255 + 0.5, 0, 255).astype(np.uint8).astype(np.float32) / 255.0
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
    # El tamaño va antes que el color: la capa reducida (catálogo + gaussianas), cuantizada a 8 bits
    r8 = np.clip(np.round(procesado.reducir_estrellas(capa, 0.6) * 255), 0, 255).astype(np.float32) / 255.0
    e = saturar(scnr(previas(r8, np.zeros(3, np.float32), 0.3, 0.2), 0.8), 1.2)
    e = procesado.aplicar_curvas(np.clip(e, 0, 1).astype(np.float32), curvas_e)          # curvas propias de las estrellas
    nav = np.clip((1 - (1 - o) * (1 - e)) * 255 + 0.5, 0, 255).astype(np.uint8)
    dif = np.abs(servidor.astype(int) - nav.astype(int))
    comprobar(np.percentile(dif, 99.5) <= 2 and dif.max() <= 6, f"diferencia p99,5 = {np.percentile(dif, 99.5):.0f}, máx = {dif.max()} niveles")


def prueba_encogido():
    print("7. Reducir las estrellas (catálogo + campanas de Gauss)")
    h, w = 240, 1200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    capa = np.zeros((h, w, 3), np.float32)
    # Tres estrellas aisladas: pequeña débil, media y grande saturada con la cima plana (como
    # las deja el estirado), cada una con su color (x, y, pico, sigma, color)
    estrellas = ((200, 60, 0.4, 1.2, (1.0, 0.8, 0.6)), (600, 120, 0.8, 2.0, (0.7, 0.8, 1.0)), (1000, 180, 1.0, 3.0, (1.0, 1.0, 1.0)))
    for x0, y0, p, s, col in estrellas:
        g = np.minimum(1.4 * p * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * s * s)), p)    # cima plana en p
        capa = np.maximum(capa, g[..., None] * np.array(col, np.float32))
    cat = procesado.catalogo_estrellas(capa)
    comprobar(len(cat) == 3, f"catálogo: {len(cat)} estrellas (3 esperadas)")
    orden = np.argsort(cat[:, 0])
    cat_o = cat[orden]
    comprobar(all(abs(c[0] - e[0]) < 0.5 and abs(c[1] - e[1]) < 0.5 for c, e in zip(cat_o, estrellas)),
              "centros a menos de medio píxel")
    comprobar(cat_o[0, 2] < cat_o[1, 2] < cat_o[2, 2], f"sigmas en orden: {[round(float(v), 2) for v in cat_o[:, 2]]} px")
    comprobar(np.allclose(procesado.reducir_estrellas(capa, 1.0, cat), capa), "tamaño 100 %: la capa original intacta")

    def anchura(img, x0, y0):
        """Sigma (px) del perfil por el segundo momento a lo largo de la fila del centro."""
        fila = img[y0, x0 - 30:x0 + 31].max(axis=1).astype(np.float64)
        d = np.arange(-30, 31, dtype=np.float64)
        return float(np.sqrt(np.sum(d * d * fila) / max(np.sum(fila), 1e-9)))
    r50 = procesado.reducir_estrellas(capa, 0.5, cat)
    s_pintada = [float(c[2]) for c in cat_o]
    s_50 = [anchura(r50, x, y) for x, y, __, __, __ in estrellas]
    comprobar(s_50[0] < s_50[1] < s_50[2], f"al 50 % las brillantes siguen siendo mayores: {[round(v, 2) for v in s_50]} px")
    proporciones = [a / b for a, b in zip(s_50, s_pintada)]
    comprobar(all(0.45 <= q <= 0.75 for q in proporciones[1:]), f"y encogen en la misma proporción (≈ la mitad): {[round(q, 2) for q in proporciones]}")
    picos = [float(r50[y, x].max()) for x, y, __, __, __ in estrellas]
    comprobar(all(abs(a - e[2]) < 0.02 for a, e in zip(picos, estrellas)), f"conservan el pico: {[round(v, 2) for v in picos]}")
    color = r50[120, 600]
    comprobar(color[2] > color[0], f"y su color (la media, azulada: {np.round(color, 2).tolist()})")
    # La saturada conserva una cima, encogida en la misma proporción, y fuera de ella baja en campana
    fila = r50[180, 1000:1010].max(axis=1)
    cima_50 = int(np.sum(fila >= 0.99))
    cima_100 = int(np.sum(capa[180, 1000:1010].max(axis=1) >= 0.99))
    fuera = fila[cima_50:cima_50 + 4]
    comprobar(cima_100 >= 2 and 0.3 <= cima_50 / cima_100 <= 0.7 and all(a > b for a, b in zip(fuera, fuera[1:])),
              f"la saturada, con la cima encogida ({cima_100} → {cima_50} px) y en campana fuera: {np.round(fila, 2).tolist()}")
    r10 = procesado.reducir_estrellas(capa, 0.1, cat)
    visibles = [float(r10[y, x].max()) for x, y, __, __, __ in estrellas]
    comprobar(all(v > 0.02 for v in visibles), f"al 10 % siguen todas, más tenues: {[round(v, 2) for v in visibles]}")
    comprobar(procesado.reducir_estrellas(capa, 0.0, cat).max() == 0.0, "tamaño 0 %: desaparecen todas")
    # Empaquetado para el navegador (10 bytes por estrella) y vuelta
    import editor_fotos
    vuelta = editor_fotos.desempaquetar_catalogo(editor_fotos.empaquetar_catalogo(cat))
    comprobar(len(editor_fotos.empaquetar_catalogo(cat)) == 10 * len(cat) and np.allclose(vuelta[:, :6], cat[:, :6], atol=[0.04, 0.04, 0.006, 0.003, 0.003, 0.003])
              and np.allclose(vuelta[:, 6], cat[:, 6], rtol=0.012),
              "catálogo empaquetado en 10 bytes por estrella y desempaquetado igual")


def prueba_estirado_estrellas():
    print("8. Estirado propio de las estrellas")
    original, objeto, U, __ = cielo_sintetico()
    aj = dict(procesado.ESTILOS["seestar"], estirado="multiescala", curvas=None)
    balance = {"r": 1.1, "b": 1.3, "n": 50}

    def capa(**extra):
        return procesado.procesar_capas(objeto, U, 1, dict(aj, **extra), escala=1.0, balance=balance, solo_base=True)[1]
    c0, c_comp, c_mas, c_menos = capa(), capa(compresion_estrellas=0.8), capa(intensidad_estrellas=0.5), capa(intensidad_estrellas=-0.5)
    meseta = lambda c: int((c.max(axis=2) >= c.max() - 0.01).sum())
    comprobar(meseta(c_comp) < meseta(c0), f"la compresión de los núcleos achica la cima plana: {meseta(c0)} → {meseta(c_comp)} px")
    comprobar(c_menos.sum() < c0.sum() < c_mas.sum(), "la intensidad sube y baja la luz de las estrellas")
    base0 = procesado.procesar_capas(objeto, U, 1, aj, escala=1.0, balance=balance, solo_base=True)[0]
    base1 = procesado.procesar_capas(objeto, U, 1, dict(aj, intensidad_estrellas=0.5, compresion_estrellas=0.8), escala=1.0, balance=balance, solo_base=True)[0]
    comprobar(np.array_equal(base0, base1), "y ninguna de las dos toca el objeto ni el fondo")
    comprobar(np.allclose(capa(intensidad_estrellas=0.0, compresion_estrellas=0.0), c0), "con las dos a 0, el estirado de la foto")


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



def prueba_contraste_sin_estrellas():
    print("9. Contraste local sin estrellas")
    rng = np.random.default_rng(3)
    n = 400
    # Cielo con ruido de escala media (el moteado que deja la reducción de ruido) y una nebulosa
    ruido = rng.normal(0, 1, (n, n)).astype(np.float32)
    import cv2
    ruido = cv2.GaussianBlur(ruido, (0, 0), 6.0)
    ruido *= 0.012 / ruido.std()
    yy, xx = np.mgrid[0:n, 0:n]
    nebulosa = 0.45 * np.exp(-((xx - 200) ** 2 + (yy - 200) ** 2) / (2 * 50.0 ** 2))
    detalle = 0.06 * np.sin(xx / 6.0) * np.exp(-((xx - 200) ** 2 + (yy - 200) ** 2) / (2 * 40.0 ** 2))
    L = np.clip(0.10 + ruido + nebulosa + detalle, 0, 1).astype(np.float32)
    rgb = np.repeat(L[..., None], 3, axis=2)
    normal = procesado.contraste_local(rgb, 0.7).mean(axis=2)
    protegido = procesado.contraste_local(rgb, 0.7, proteger_cielo=True).mean(axis=2)
    cielo = (np.hypot(xx - 200, yy - 200) > 170)
    centro = (np.hypot(xx - 200, yy - 200) < 30)
    def moteado(img):
        return float(img[cielo].std())
    m0, mn, mp = moteado(L), moteado(normal), moteado(protegido)
    print(f"     moteado del cielo: original {m0:.4f}, contraste {mn:.4f}, protegido {mp:.4f}")
    comprobar(mn > 1.3 * m0, "sin proteger, CLAHE agranda el moteado del cielo")
    comprobar(mp < 1.15 * m0, "protegido, el cielo queda como estaba")
    dif = float(np.abs(normal[centro] - protegido[centro]).max())
    comprobar(dif < 0.01, f"la nebulosa recibe el mismo contraste (diferencia {dif:.4f})")



def prueba_halo_brillante():
    print("10. Estrellas débiles sobre el halo de una brillante")
    n = 240
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    r2 = (xx - 120) ** 2 + (yy - 120) ** 2
    # Una brillante naranja con el núcleo cortado (cima plana) y un halo ancho
    perfil = np.clip(2.5 * np.exp(-r2 / (2 * 10.0 ** 2)) + 0.5 * np.exp(-r2 / (2 * 22.0 ** 2)), 0, 1)
    capa = perfil[..., None] * np.array([1.0, 0.7, 0.35], np.float32)
    # Seis débiles blancas a 32 px, encima del halo, y una aislada
    debiles = [(120 + 32 * np.cos(a), 120 + 32 * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 6, endpoint=False)]
    for x, y in debiles + [(30.0, 30.0)]:
        g = 0.2 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.6 ** 2))
        capa = 1 - (1 - capa) * (1 - g[..., None])
    capa = capa.astype(np.float32)
    cat = procesado.catalogo_estrellas(capa)
    def cerca(x, y, r=3.0):
        d = np.hypot(cat[:, 0] - x, cat[:, 1] - y)
        return cat[d < r]
    halladas = [cerca(x, y) for x, y in debiles]
    comprobar(all(len(h) == 1 for h in halladas), "las seis débiles, una vez cada una")
    sig = [float(h[0, 2]) for h in halladas if len(h)]
    comprobar(sig and max(sig) < 2.6, f"con su anchura, no la del halo (sigma hasta {max(sig or [0]):.2f} px; reales 1,6)")
    color = [h[0, 3:6] / h[0, 3:6].max() for h in halladas if len(h)]
    comprobar(color and min(float(c[2]) for c in color) > 0.75, "con su color blanco, no el naranja de la brillante")
    sobran = [e for e in cat if np.hypot(e[0] - 120, e[1] - 120) < 28 and np.hypot(e[0] - 120, e[1] - 120) > 3]
    comprobar(not sobran, f"ninguna estrella inventada dentro del halo ({len(sobran)})")
    comprobar(len(cerca(120, 120)) == 1 and float(cerca(120, 120)[0, 2]) > 8, "la brillante sigue siendo una, y grande")
    aislada = cerca(30, 30)
    comprobar(len(aislada) == 1 and abs(float(aislada[0, 2]) - 1.6) < 0.4, "la aislada se mide igual")



def prueba_cima_blanca():
    print("11. La cima blanca de las brillantes al reducir")
    n = 120
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    # Como la deja el estirado: color naranja por una sobreexposición de 4, recortado canal a canal
    color = np.array([1.0, 0.7, 0.4], np.float32)
    G = np.exp(-((xx - 60) ** 2 + (yy - 60) ** 2) / (2 * 3.0 ** 2))
    capa = np.minimum(4.0 * G[..., None] * color, 1.0).astype(np.float32)
    cat = procesado.catalogo_estrellas(capa)
    comprobar(len(cat) == 1, f"una estrella ({len(cat)})")
    x, y, s, r, g, b, A = cat[0]
    comprobar(abs(A - 4.0) < 0.6, f"sobreexposición medida {A:.2f} (real 4)")
    comprobar(abs(s - 3.0) < 0.3, f"sigma de la gaussiana, no la de la cima plana: {s:.2f} px (real 3)")
    col = np.array([r, g, b]) / max(r, g, b)
    comprobar(np.allclose(col, color, atol=0.05), f"color de los píxeles sin cortar: {np.round(col, 2).tolist()}")
    pintada = procesado.pintar_estrellas(cat, capa.shape[:2], 1.0)
    comprobar(np.abs(pintada - capa).max() < 0.08, f"pintada al 100 %, igual que la original (máx {np.abs(pintada - capa).max():.3f})")
    r50 = procesado.pintar_estrellas(cat, capa.shape[:2], 0.5)
    comprobar(r50[60, 60].min() > 0.97, f"al 50 %, la cima sigue blanca: {np.round(r50[60, 60], 2).tolist()}")
    halo = r50[60, 60 + 4]
    comprobar(halo[2] < 0.6 * halo[0], f"y el halo sigue naranja: {np.round(halo, 2).tolist()}")
    def radio_cima(img):
        return int(np.sum(img[60, 60:90].min(axis=1) >= 0.97))
    comprobar(0.3 <= radio_cima(r50) / max(radio_cima(capa), 1) <= 0.7, f"cima blanca de {radio_cima(capa)} a {radio_cima(r50)} px")
    r10 = procesado.pintar_estrellas(cat, capa.shape[:2], 0.1)
    comprobar(r10[60, 60].max() < 0.97 and r10[60, 60, 2] < 0.6 * r10[60, 60, 0],
              f"al 10 % se apaga, cima incluida, y queda de su color: {np.round(r10[60, 60], 2).tolist()}")
    # Cima con huecos (como la brillante de M 17 en la vista previa: 0,93 entre zonas de 0,99):
    # varios máximos locales en la misma cima, pero una sola estrella
    hueco = 1.0 - 0.07 * np.exp(-((xx - 60) ** 2 + (yy - 60) ** 2) / (2 * 1.5 ** 2))
    con_huecos = (capa * np.minimum(hueco, 1.0)[..., None] * (1.0 - 0.06 * (np.sin(xx) * np.sin(yy) > 0.7))[..., None]).astype(np.float32)
    cat_h = procesado.catalogo_estrellas(con_huecos)
    d = np.hypot(cat_h[:, 0] - 60, cat_h[:, 1] - 60)
    comprobar(int((d < 15).sum()) == 1, f"cima con huecos: una sola estrella ({int((d < 15).sum())})")



def prueba_sombra_objeto():
    print("12. Sin resplandor ni sombra alrededor del objeto")
    rng = np.random.default_rng(7)
    n = 800
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    r = np.hypot(xx - 400, yy - 400)
    # Cielo con ruido y una nebulosa azulada brillante de 60 px de radio (en ADU, lineal)
    cielo = 1000.0 + rng.normal(0, 20, (n, n)).astype(np.float32)
    nebulosa = 4000.0 * np.exp(-(r / 45.0) ** 4)
    rgb = np.stack([cielo + 0.6 * nebulosa, cielo + 0.9 * nebulosa, cielo + 1.3 * nebulosa], axis=2).astype(np.float32)
    out = procesado.suavizar_fondo_sin_estrellas(rgb, 0.65, sigma_px=128.0)
    L, L0 = out.mean(axis=2), rgb.mean(axis=2)
    def anillo(img, r0, r1):
        return float(np.median(img[(r >= r0) & (r < r1)]))
    lejos = anillo(L0, 330, 380)
    subida = [(anillo(L, a, a + 20) - anillo(L0, a, a + 20)) / lejos * 100 for a in (120, 160, 200, 240)]
    comprobar(max(subida) < 0.5, f"el cielo alrededor no se levanta: {[round(v, 2) for v in subida]} %")
    color = out[(r >= 120) & (r < 200)].mean(axis=0)
    comprobar(abs(color[2] / color[0] - 1.0) < 0.01, f"ni se tiñe del color de la nebulosa (B/R {color[2] / color[0]:.3f})")


if __name__ == "__main__":
    prueba_pantalla()
    base, capa, aj = prueba_procesado()
    prueba_paridad(base, capa, aj)
    prueba_encogido()
    prueba_estirado_estrellas()
    prueba_contraste_sin_estrellas()
    prueba_halo_brillante()
    prueba_cima_blanca()
    prueba_sombra_objeto()
    prueba_capas_guardadas()
    prueba_progreso()
    print()
    if FALLOS:
        print(f"❌ {len(FALLOS)} fallo(s):")
        for f in FALLOS:
            print("   -", f)
        sys.exit(1)
    print("✅ Todo en orden")
