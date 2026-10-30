# herramientas/probar_bahtinov.py
#
# Prueba sin equipo del enfoque con máscara de Bahtinov (enfoque.py): cruz, cruceta virtual,
# desvío y firma del foco por colores.
#
# La estrella se simula con óptica de Fourier: la pupila de un refractor de 60 mm y 260 mm de focal
# (la del equipo del autor) tapada con una Bahtinov de 1,5 mm de paso y rayas a ±20°, el desenfoque
# como una fase cuadrática y la luz de cada canal de la cámara como una suma de longitudes de onda.
# Píxeles de 3,76 µm (IMX533). Sale lo mismo que se vio con la Saturn-C el 29-oct-2026: con solo el
# verde los picos son trazos sueltos a ~25 px de la estrella; con los tres colores, casi seguidos.
#
#   1. medir_bahtinov en una serie de desenfoques de −0,3 a +0,3 mm: el desvío es casi cero en el
#      foco, cambia de signo al cruzarlo y crece con el desenfoque; los ángulos de la X salen a ±20°
#      del pico central. También con la máscara girada y con una X de 15° y de 30°.
#   2. Sin máscara (estrella sola) y con la araña de un reflector (cuatro picos a 90°) no hay cruz.
#   3. En vivo (superpíxel del mosaico RGGB, mitad de resolución) mide lo mismo que en la foto.
#   4. Con ruido de lectura y de fotones, y la estrella movida por el seeing, el desvío en el foco
#      varía poco de una toma a otra (menos que el umbral de «centrada»).
#   5. AyudaBahtinov con un reloj falso: la mediana de la ventana, «centrada» una sola vez y con
#      histéresis, el signo estable aunque la raya cruce los 90°, aprende la firma al quitar la
#      máscara (en una carpeta temporal), avisa «enfocado» al llegar a ella y no con el desenfoque;
#      una firma pegada a 1 no sirve. Los fotogramas movidos sin cruz al tocar el enfocador no quitan
#      la máscara ni aprenden (en vivo hay 3 s de gracia; una foto suelta movida no cuenta).
#   6. La estrella alargada por un golpe al tubo sale «movida»; la redonda, con o sin máscara, no.
#   7. imagen_foto_noche e imagen_vivo componen la imagen con el recorte de la Bahtinov.
#
# Uso: python herramientas/probar_bahtinov.py [--guardar CARPETA]   (desde la carpeta de ScopePower)
#      --guardar deja los recortes (verde solo y tres colores, en el foco y desenfocado) para mirarlos.

import os
import sys
import json
import time
import argparse
import tempfile

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import enfoque

PIXEL_MM = 0.00376
FOCAL_MM = 260.0
APERTURA_MM = 60.0
LADO = 320
PICO = 30000.0      # el pico del canal más brillante: en vivo la exposición lleva la estrella al 60 % (39 000)
BANDAS = {"r": (610.0, 30.0), "g": (535.0, 35.0), "b": (460.0, 30.0)}    # centro y anchura (nm) de cada canal


def pupila(n=360, angulo=20.0, periodo=1.5, arana=False):
    """(coordenadas en mm, transmisión): la Bahtinov (media pupila con rejas para el pico central y dos cuartos a ±angulo) o una araña."""
    x = (np.arange(n) - (n - 1) / 2.0) * (APERTURA_MM / n)
    xx, yy = np.meshgrid(x, x)
    dentro = np.hypot(xx, yy) <= APERTURA_MM / 2
    if arana:
        return x, (dentro & (np.abs(xx) > 0.6) & (np.abs(yy) > 0.6)).astype(np.float64)
    t = np.zeros_like(xx)
    arriba = yy > 0
    t[arriba] = np.mod(xx[arriba] / periodo, 1.0) < 0.5
    a = np.radians(angulo)
    for signo, zona in ((1, (yy <= 0) & (xx < 0)), (-1, (yy <= 0) & (xx >= 0))):
        u = xx * np.cos(signo * a) + yy * np.sin(signo * a)
        t[zona] = np.mod(u[zona] / periodo, 1.0) < 0.5
    return x, (t * dentro).astype(np.float64)


def psf(x, transmision, lam_mm, desenfoque_mm, n_o=LADO):
    """Intensidad en el plano del sensor (px de PIXEL_MM) por la transformada de Fourier de la pupila (MFT)."""
    xo = (np.arange(n_o) - n_o / 2) * PIXEL_MM
    fase = np.pi * desenfoque_mm * (x[None, :] ** 2 + x[:, None] ** 2) / (lam_mm * FOCAL_MM ** 2)
    m = np.exp(-2j * np.pi * np.outer(xo, x) / (lam_mm * FOCAL_MM))
    campo = m @ (transmision * np.exp(1j * fase)) @ m.T
    return np.abs(campo) ** 2


def canales(desenfoque_mm, angulo=20.0, giro=0.0, arana=False, seeing=0.6, desplaz=(0.0, 0.0), sin_mascara=False):
    """{"r", "g", "b"}: la estrella en cada canal (float32, el pico del más brillante = 1)."""
    if sin_mascara:
        yy, xx = np.mgrid[0:LADO, 0:LADO]
        d2 = (xx - LADO / 2 - desplaz[0]) ** 2 + (yy - LADO / 2 - desplaz[1]) ** 2
        s = max(0.8, 1.0 + 20.0 * abs(desenfoque_mm))
        return {c: np.exp(-d2 / (2 * s * s)).astype(np.float32) for c in "rgb"}
    x, t = pupila(angulo=angulo, arana=arana)
    salida = {}
    for c, (l0, s) in BANDAS.items():
        acc = np.zeros((LADO, LADO))
        for lam in np.linspace(l0 - 2 * s, l0 + 2 * s, 7):
            acc += np.exp(-0.5 * ((lam - l0) / s) ** 2) * psf(x, t, lam * 1e-6, desenfoque_mm)
        salida[c] = acc.astype(np.float32)
    m = max(float(v.max()) for v in salida.values())
    mat = cv2.getRotationMatrix2D((LADO / 2, LADO / 2), giro, 1.0)
    mat[:, 2] += desplaz
    for c in salida:
        v = cv2.GaussianBlur(salida[c] / m, (0, 0), seeing)
        salida[c] = cv2.warpAffine(v, mat, (LADO, LADO))
    return salida


def con_ruido(plano, pico=20000.0, fondo=1000.0, lectura=8.0, rng=None):
    rng = rng or np.random.default_rng(0)
    v = plano * pico + fondo
    return (v + rng.normal(0, 1, v.shape) * np.sqrt(v + lectura ** 2)).astype(np.float32)


def mosaico(ch, rng):
    """Mosaico RGGB (16 bits) con ruido, como lo da el SDK."""
    cfa = np.zeros((LADO, LADO), np.float32)
    cfa[0::2, 0::2] = ch["r"][0::2, 0::2]
    cfa[0::2, 1::2] = ch["g"][0::2, 1::2]
    cfa[1::2, 0::2] = ch["g"][1::2, 0::2]
    cfa[1::2, 1::2] = ch["b"][1::2, 1::2]
    return np.clip(con_ruido(cfa, pico=PICO, rng=rng), 0, 65535).astype(np.uint16)


def medir_foto(ch, rng, pico=PICO):
    lum = sum(con_ruido(ch[c], pico=pico, rng=rng) for c in "rgb")
    x, y, __ = enfoque.estrella_mas_brillante(lum)
    return enfoque.medir_bahtinov(lum, x, y, 1.0)


def probar_serie():
    rng = np.random.default_rng(1)
    serie = [-0.15, -0.06, -0.02, 0.0, 0.02, 0.06, 0.15]
    desvios = []
    for d in serie:
        t0 = time.perf_counter()
        m = medir_foto(canales(d, giro=33.0, desplaz=(0.3, -0.2)), rng)
        ms = 1000 * (time.perf_counter() - t0)
        assert m is not None, f"sin cruz con {d} mm"
        ac, a1, a2 = m["angulos"]
        assert abs(ac - 147.0) < 1.0, m["angulos"]                     # la máscara girada 33°: el pico central a 180 − 33
        if abs(d) <= 0.06:
            # Más desenfocado, los trazos de la X y los del pico central se juntan (manchas de 9 px a 8 px unos
            # de otros) y las rayas de la X salen más cerradas: el desvío sale de más, pero con su signo
            assert abs(abs(enfoque._dif_angulo(a1, ac)) - 20.0) < 2.0 and abs(abs(enfoque._dif_angulo(a2, ac)) - 20.0) < 2.0, m["angulos"]
        desvios.append(m["desvio"])
        print(f"  desenfoque {d:+.2f} mm: desvío {m['desvio']:+.2f} px, rayas a {ac:.1f}° {a1:.1f}° {a2:.1f}° ({ms:.0f} ms)")
    assert abs(desvios[serie.index(0.0)]) < 0.15, desvios
    antes = [v for v, d in zip(desvios, serie) if d < 0][::-1]          # de cerca a lejos del foco, a cada lado
    despues = [v for v, d in zip(desvios, serie) if d > 0]
    assert len({np.sign(v) for v in antes}) == 1 and len({np.sign(v) for v in despues}) == 1 and np.sign(antes[0]) != np.sign(despues[0]), desvios
    for lado in (antes, despues):
        assert all(abs(b) > abs(a) for a, b in zip(lado, lado[1:])), desvios
        assert abs(lado[0]) > enfoque.UMBRAL_BAHTINOV_PX, desvios       # 20 µm del foco ya no es «centrada»
    # Muy desenfocado (0,3 mm, un círculo de 18 px) los trazos son una mancha: puede no haber cruz, pero nunca una falsa
    m = medir_foto(canales(0.3, giro=33.0), rng)
    assert m is None or m["desvio"] < -enfoque.SALIDA_BAHTINOV_PX, m
    print("  desenfoque +0,30 mm: " + ("sin cruz (una mancha)" if m is None else f"desvío {m['desvio']:+.2f} px"))
    for angulo in (15.0, 30.0):
        m = medir_foto(canales(0.06, angulo=angulo), rng)
        assert m is not None and abs(abs(enfoque._dif_angulo(m["angulos"][1], m["angulos"][0])) - angulo) < 2.0, (angulo, m)
        print(f"  X de {angulo:.0f}°: rayas a {m['angulos']}, desvío {m['desvio']:+.2f} px con +0,06 mm")
    # Una estrella la mitad de brillante (exposición corta en la foto de prueba): en el foco sigue saliendo.
    # Con un cuarto, las rayas de la X ya no salen del ruido: hay que exponer más
    m = medir_foto(canales(0.0, giro=33.0), rng, pico=PICO / 2)
    assert m is not None and abs(m["desvio"]) < enfoque.UMBRAL_BAHTINOV_PX, m
    print(f"  estrella la mitad de brillante, en el foco: desvío {m['desvio']:+.2f} px")


def probar_sin_cruz():
    rng = np.random.default_rng(2)
    for nombre, ch in (("estrella sin máscara", canales(0.0, sin_mascara=True)),
                       ("estrella desenfocada sin máscara", canales(0.2, sin_mascara=True)),
                       ("araña de reflector", canales(0.0, arana=True, giro=10.0))):
        m = medir_foto(ch, rng)
        assert m is None, (nombre, m)
        print(f"  {nombre}: sin cruz")


def probar_vivo():
    rng = np.random.default_rng(3)
    for d in (0.0, 0.06):
        ch = canales(d, giro=-12.0)
        foto = medir_foto(ch, rng)
        cfa = mosaico(ch, rng)
        rojo, verde, azul = enfoque.planos_superpixel(cfa, "RGGB")
        lum = rojo + verde + azul
        x, y, __ = enfoque.estrella_mas_brillante(verde)
        vivo = enfoque.medir_bahtinov(lum, x, y, 2.0)
        assert vivo is not None, d
        assert abs(vivo["desvio"] - foto["desvio"]) < 0.25, (vivo["desvio"], foto["desvio"])
        print(f"  {d:+.2f} mm: foto {foto['desvio']:+.2f} px, vivo (superpíxel) {vivo['desvio']:+.2f} px")


def probar_repetibilidad():
    rng = np.random.default_rng(4)
    valores = []
    for i in range(8):
        ch = canales(0.0, giro=5.0, seeing=0.5 + 0.3 * rng.random(), desplaz=tuple(rng.uniform(-0.5, 0.5, 2)))
        m = medir_foto(ch, rng)
        assert m is not None
        valores.append(m["desvio"])
    dispersion = float(np.std(valores))
    # Cada toma suelta queda dentro de «centrada» (en vivo, además, se enseña la mediana de 2 s)
    assert dispersion < enfoque.UMBRAL_BAHTINOV_PX / 2 and max(abs(v) for v in valores) < enfoque.UMBRAL_BAHTINOV_PX, valores
    assert abs(float(np.mean(valores))) < enfoque.UMBRAL_BAHTINOV_PX / 2, valores
    print(f"  8 tomas en el foco con ruido y seeing: desvío {np.mean(valores):+.3f} ± {dispersion:.3f} px")


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def medida_falsa(desvio, normal=(0.0, 1.0)):
    return {"desvio": desvio, "angulos": [90.0, 70.0, 110.0], "rectas": [(normal[0], normal[1], 0.0)] * 3,
            "cruce": (0.0, 0.0), "radio": 56, "escala": 2.0}


def info_estrella(g, r, b):
    return {"x": 100, "y": 100, "pico": 30000, "fwhm_g": g, "fwhm_r": r, "fwhm_b": b, "saturada": False}


def probar_ayuda():
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = os.path.join(carpeta, "datos", "firma_enfoque.json")
        reloj = Reloj()
        ayuda = enfoque.AyudaBahtinov(ruta=ruta, reloj=reloj)
        avisos = []

        def toma(info, medida, dt=0.5, modo="vivo"):
            reloj.t += dt
            ayuda.anotar(info, medida, modo)
            if info.get("aviso"):
                avisos.append((reloj.t, info["aviso"]))
            return info

        # Girando el enfocador hacia el foco: el desvío baja de 2 px a 0,05 y se queda ahí
        for d in list(np.linspace(2.0, 0.05, 20)) + [0.05] * 3:
            i = toma(info_estrella(3.0, 3.0, 3.0), medida_falsa(float(d)))
        assert i["bahtinov"]["centrada"] and [a for __, a in avisos] == ["centrada"], avisos
        # El ruido alrededor del foco no la descentra ni repite el aviso (histéresis); el signo sigue aunque la normal salga al revés
        for d in (0.35, -0.2, 0.45, 0.1):
            i = toma(info_estrella(3.0, 3.0, 3.0), medida_falsa(-d, normal=(0.0, -1.0)))
        assert i["bahtinov"]["centrada"] and [a for __, a in avisos] == ["centrada"], (i, avisos)
        assert i["bahtinov"]["desvio"] > 0.0, i      # la mediana de (0,45, 0,1, −0,2, 0,35...) con el signo de antes
        print(f"  centrada a los {avisos[0][0]:.1f} s, una sola vez; desvío enseñado {i['bahtinov']['desvio']:+.2f} px")
        # Al tocar el enfocador el tubo vibra: unos fotogramas sin cruz (movidos o no) no quitan la máscara ni aprenden
        for movida in (True, False, True, False):
            i = toma(dict(info_estrella(3.6, 3.1, 3.2), movida=movida), None)
            assert "firma" not in i and "bahtinov" not in i, i
        i = toma(info_estrella(3.0, 3.0, 3.0), medida_falsa(0.1))
        assert i["bahtinov"]["centrada"] and [a for __, a in avisos] == ["centrada"], (i, avisos)
        assert not os.path.exists(ruta)
        print(f"  {enfoque.GRACIA_SIN_CRUZ_S:.0f} s de fotogramas movidos sin cruz: sigue centrada, sin repetir el aviso ni aprender")
        # Fuera de la máscara: los primeros segundos (y los fotogramas movidos al quitarla) no cuentan; luego aprende la
        # relación de colores con 10 fotogramas (refractor: en el foco R y B más anchos)
        rng = np.random.default_rng(5)
        t_quitada = reloj.t
        while reloj.t - t_quitada < enfoque.GRACIA_SIN_CRUZ_S:
            i = toma(info_estrella(2.8, 2.8 * 1.30, 2.8 * 1.55), None)
            assert "firma" not in i, i
        i = toma(dict(info_estrella(4.0, 4.1, 4.2), movida=True), None)
        assert "firma" not in i, i
        for k in range(enfoque.FOTOGRAMAS_APRENDER["vivo"]):
            g = 2.8 * (1 + 0.03 * rng.normal())
            i = toma(info_estrella(g, g * 1.30 * (1 + 0.01 * rng.normal()), g * 1.55 * (1 + 0.01 * rng.normal())), None)
            assert i.get("firma", {}).get("aprendiendo") == k + 1 or k + 1 == enfoque.FOTOGRAMAS_APRENDER["vivo"], (k, i)
        assert [a for __, a in avisos] == ["centrada", "aprendida"], avisos
        with open(ruta, encoding="utf-8") as f:
            firma = json.load(f)["vivo"]
        assert abs(firma["rg"] - 1.30) < 0.03 and abs(firma["bg"] - 1.55) < 0.03, firma
        print(f"  firma aprendida: R/G {firma['rg']}, B/G {firma['bg']} (dispersión {firma['dispersion']})")
        # Otra sesión (la firma sale del fichero): desenfocado, los tres colores casi iguales → no avisa
        reloj2 = Reloj()
        ayuda = enfoque.AyudaBahtinov(ruta=ruta, reloj=reloj2)
        reloj.t = 0.0
        avisos.clear()

        def toma2(g, rg, bg):
            reloj2.t += 0.5
            info = ayuda.anotar(info_estrella(g, g * rg, g * bg), None, "vivo")
            if info.get("aviso"):
                avisos.append((reloj2.t, info["aviso"]))
            return info

        for k in range(20):
            i = toma2(9.0 - 0.3 * k, 1.03 + 0.01 * k, 1.05 + 0.02 * k)      # acercándose: g de 9 a 3,3
        assert not avisos and not i["firma"]["enfocado"], (avisos, i)
        for k in range(8):
            i = toma2(2.85, 1.29, 1.53)                                      # en el foco
        assert [a for __, a in avisos] == ["enfocado"] and i["firma"]["enfocado"] and i["firma"]["util"], (avisos, i)
        for k in range(6):
            i = toma2(4.5, 1.08, 1.12)                                       # pasado del foco
        assert not i["firma"]["enfocado"], i
        print(f"  enfocado al llegar a la firma (a los {avisos[0][0]:.1f} s), y deja de estarlo al pasarse")
        # Una firma pegada a 1 (los tres colores enfocan igual) no sirve
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"vivo": {"rg": 1.02, "bg": 1.04, "dispersion": 0.01, "n": 10}}, f)
        ayuda = enfoque.AyudaBahtinov(ruta=ruta, reloj=reloj2)
        i = toma2(3.0, 1.02, 1.04)
        assert i["firma"]["util"] is False and not i["firma"]["enfocado"], i
        print("  firma con R/G y B/G casi 1: no sirve y no avisa")
        # Con fotos sueltas: una foto movida sin cruz justo después de centrar no se toma por la máscara quitada
        ruta_foto = os.path.join(carpeta, "foto", "firma_enfoque.json")
        reloj3 = Reloj()
        ayuda = enfoque.AyudaBahtinov(ruta=ruta_foto, reloj=reloj3)
        reloj3.t += 5
        i = ayuda.anotar(info_estrella(3.0, 3.0, 3.0), medida_falsa(0.1), "foto")
        assert i.get("aviso") == "centrada", i
        reloj3.t += 5
        i = ayuda.anotar(dict(info_estrella(5.0, 5.2, 5.3), movida=True), None, "foto")
        assert "aviso" not in i and "firma" not in i and not os.path.exists(ruta_foto), i
        reloj3.t += 5
        i = ayuda.anotar(info_estrella(2.8, 2.8 * 1.3, 2.8 * 1.55), None, "foto")
        assert i.get("aviso") == "aprendida", i
        with open(ruta_foto, encoding="utf-8") as f:
            firma = json.load(f)["foto"]
        assert abs(firma["rg"] - 1.3) < 0.01 and abs(firma["bg"] - 1.55) < 0.01, firma
        print("  foto suelta: la movida no aprende, la siguiente sí")


def probar_movida():
    """medir() marca «movida» la estrella alargada por un golpe al tubo durante la exposición, y no la redonda."""
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:LADO, 0:LADO]
    c = LADO / 2
    # (nombre, sigma de la estrella, pasos del trazo en diagonal (√2 px cada uno), ¿movida?)
    for nombre, sigma, trazo, esperada in (("redonda en el foco", 1.4, 0, False), ("redonda desenfocada", 3.5, 0, False),
                                          ("en el foco, movida 3 px", 1.4, 2, False), ("en el foco, movida 8 px", 1.4, 6, True),
                                          ("desenfocada, movida 6 px", 3.5, 4, False), ("desenfocada, movida 17 px", 3.5, 12, True)):
        estrella = np.exp(-((xx - c) ** 2 + (yy - c) ** 2) / (2 * sigma * sigma)).astype(np.float32)
        if trazo:
            nucleo = np.zeros((trazo + 1, trazo + 1), np.float32)
            np.fill_diagonal(nucleo, 1.0)       # en diagonal, el peor caso para los momentos por ejes
            estrella = cv2.filter2D(estrella, -1, nucleo / nucleo.sum())
            estrella /= estrella.max()
        plano = con_ruido(estrella, pico=PICO, rng=rng) - 1000.0
        info = enfoque.medir(plano, plano, plano)
        alarg = enfoque.alargamiento(plano, info["x"], info["y"])
        assert info["movida"] is esperada, (nombre, alarg)
        print(f"  {nombre}: ejes {alarg:.2f} → {'movida' if info['movida'] else 'bien'}")
    # La estrella con la máscara puesta (el núcleo, sin los picos lejanos) no sale movida
    ch = canales(0.0, giro=20.0)
    plano = con_ruido(ch["g"], pico=PICO, rng=rng) - 1000.0
    info = enfoque.medir(plano)
    assert not info["movida"], enfoque.alargamiento(plano, info["x"], info["y"])
    print(f"  con la máscara en el foco: ejes {enfoque.alargamiento(plano, info['x'], info['y']):.2f} → bien")
    # Con la máscara puesta y el tubo movido: o sigue saliendo la cruz o la estrella sale «movida» (nunca «sin máscara»)
    for pasos in (2, 4, 10):
        nucleo = np.zeros((pasos + 1, pasos + 1), np.float32)
        np.fill_diagonal(nucleo, 1.0)
        movido = {k: cv2.filter2D(v, -1, nucleo / nucleo.sum()) for k, v in ch.items()}
        m = max(float(v.max()) for v in movido.values())
        rgb = {k: con_ruido(v / m, pico=PICO, rng=rng) - 1000.0 for k, v in movido.items()}
        info = enfoque.medir(rgb["g"], rgb["r"], rgb["b"])
        cruz = enfoque.medir_bahtinov(rgb["r"] + rgb["g"] + rgb["b"], info["x"], info["y"], 1.0)
        assert cruz is not None or info["movida"], pasos
        print(f"  con la máscara, movida {pasos * 1.41:.0f} px: " + ("cruz" if cruz is not None else "sin cruz, pero movida"))


def probar_imagenes(guardar=None):
    rng = np.random.default_rng(6)
    with tempfile.TemporaryDirectory() as carpeta:
        enfoque.ayuda_bahtinov = enfoque.AyudaBahtinov(ruta=os.path.join(carpeta, "firma.json"))
        for d in (0.0, 0.1):
            ch = canales(d, giro=20.0)
            rgb = np.dstack([con_ruido(ch[c], pico=PICO, rng=rng) - 1000.0 for c in "rgb"])
            img, encuadre, info = enfoque.imagen_foto_noche(rgb, lambda im: (im, None))
            assert img.shape[:2] == (LADO, LADO) and "bahtinov" in info, info
            texto = enfoque.texto_info(info)
            assert "Bahtinov" in texto, texto
            print(f"  foto {d:+.2f} mm: {texto}")
            cfa = mosaico(ch, rng)
            img_v, __, info_v = enfoque.imagen_vivo(cfa, "RGGB", 1, lambda im: im, lambda im: (im, None))
            assert "bahtinov" in info_v, info_v
            if guardar:
                os.makedirs(guardar, exist_ok=True)
                verde = enfoque.revelar_gris(ch["g"])
                c = LADO // 2
                solo_verde = cv2.resize(verde[c - 60:c + 61, c - 60:c + 61], (480, 480), interpolation=cv2.INTER_CUBIC)
                lum = sum(con_ruido(ch[k], pico=PICO, rng=rng) for k in "rgb")
                x, y, __ = enfoque.estrella_mas_brillante(lum)
                m = enfoque.medir_bahtinov(lum, x, y, 1.0)
                rec = enfoque.recorte_bahtinov(lum, x, y, m, {"desvio": m["desvio"], "centrada": abs(m["desvio"]) <= enfoque.UMBRAL_BAHTINOV_PX})
                cv2.imwrite(os.path.join(guardar, f"bahtinov_{d:+.2f}.png"),
                            np.hstack([cv2.cvtColor(solo_verde, cv2.COLOR_GRAY2BGR), rec]))
                cv2.imwrite(os.path.join(guardar, f"foto_{d:+.2f}.jpg"), img)
        enfoque.ayuda_bahtinov = enfoque.AyudaBahtinov()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guardar", help="carpeta donde dejar los recortes para mirarlos")
    args = ap.parse_args()
    t0 = time.perf_counter()
    print("1. Serie de desenfoques")
    probar_serie()
    print("2. Sin cruz")
    probar_sin_cruz()
    print("3. En vivo (superpíxel)")
    probar_vivo()
    print("4. Repetibilidad")
    probar_repetibilidad()
    print("5. Centrada, firma del foco y enfocado")
    probar_ayuda()
    print("6. Estrella movida")
    probar_movida()
    print("7. Imágenes")
    probar_imagenes(args.guardar)
    print(f"✅ Bahtinov: todo bien ({time.perf_counter() - t0:.0f} s)")


if __name__ == "__main__":
    main()
