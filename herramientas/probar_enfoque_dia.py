# herramientas/probar_enfoque_dia.py
#
# Prueba sin equipo de la ayuda de enfoque de día (enfoque.py, Sol y Luna):
#
#   1. medir_disco con una Luna sintética en cuarto: encuentra el disco, mide el limbo cerca del
#      desenfoque con que se ha dibujado y manda el recorte al terminador. Con la Luna cortada por
#      el borde de la imagen (medio disco fuera) no da número: pocos ángulos de limbo (con el
#      umbral viejo de 12 el círculo se ajustaba al terminador y el limbo salía falso y más fino).
#   2. HistorialLimbo con un reloj falso: el número es el percentil bajo de la ventana (no la
#      última medida), con histéresis; el «mejor» solo cuenta ventanas llenas; se descartan las
#      medidas con otro radio o con el disco moviéndose deprisa; tras un corte, el número cae.
#   3. veredicto(): calla mientras la ventana anterior no está llena, dice «empeora» al subir el
#      limbo, «igual» una sola vez al estabilizarse, «mejora» al bajar, y no habla con el ruido
#      (fotogramas que varían un 7 % como los reales).
#   4. componer con la etiqueta del recorte y la gráfica de tendencia: dibuja sin fallar en los
#      dos tamaños de vista (móvil y tablet) y la gráfica queda dentro de la imagen.
#
# Uso: python herramientas/probar_enfoque_dia.py   (desde la carpeta de ScopePower)

import os
import sys

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import enfoque


def luna_cuarto(lado=600, cx=300.0, cy=300.0, r=200.0, sigma=1.2, ruido=40.0, semilla=0):
    """Plano verde (float32, 16 bits) de una Luna en cuarto creciente: disco con el limbo a la derecha, terminador vertical con cráteres."""
    rng = np.random.default_rng(semilla)
    yy, xx = np.mgrid[0:lado, 0:lado].astype(np.float32)
    d = np.hypot(xx - cx, yy - cy)
    disco = (d <= r).astype(np.float32)
    iluminado = disco * np.clip((xx - cx) / 8.0 + 0.5, 0, 1)            # el terminador, una rampa suave en x = cx
    textura = cv2.GaussianBlur(rng.normal(0, 1, (lado, lado)).astype(np.float32), (0, 0), 3.0)
    textura = 1.0 + 0.25 * textura / max(float(textura.std()), 1e-6)
    oscurecimiento = 1.0 - 0.3 * (d / r) ** 2
    plano = 30000.0 * iluminado * np.clip(textura, 0.3, 1.7) * np.clip(oscurecimiento, 0, 1) + 800.0
    plano = cv2.GaussianBlur(plano, (0, 0), sigma)
    return (plano + rng.normal(0, ruido, (lado, lado))).astype(np.float32)


def probar_medida():
    verde = luna_cuarto()
    info = enfoque.medir_disco(verde, escala_px=1.0, terminador=True)
    assert info.get("limbo"), info
    assert abs(info["cx"] - 300) < 3 and abs(info["cy"] - 300) < 3 and abs(info["r"] - 200) < 3, info
    assert 2.0 < info["limbo"] < 4.0, info          # FWHM de una gaussiana de 1,2 px ≈ 2,8
    assert info["n"] >= enfoque.ANGULOS_MINIMOS and info["recorte"] == "terminador", info
    assert abs(info["x"] - 300) < 12, info          # el recorte, en el terminador
    print(f"  Luna sintética: limbo {info['limbo']:.2f} px, {info['n']} ángulos, recorte en el {info['recorte']}")
    borroso = enfoque.medir_disco(luna_cuarto(sigma=3.0), escala_px=1.0)
    assert borroso["limbo"] > 1.8 * info["limbo"], (borroso, info)
    # Media Luna fuera de la imagen: el limbo que queda no basta para fiarse del círculo
    cortada = enfoque.medir_disco(luna_cuarto(cx=560.0), escala_px=1.0, terminador=True)
    assert cortada.get("limbo") is None, cortada
    print(f"  Luna cortada por el borde: sin número ({cortada.get('motivo')}, {cortada.get('n', 0)} ángulos)")


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def medida(limbo, r=200.0, cx=300.0, cy=300.0):
    return {"limbo": limbo, "r": r, "cx": cx, "cy": cy, "x": 300, "y": 100, "recorte": "terminador", "n": 100}


def probar_historial():
    reloj = Reloj()
    h = enfoque.HistorialLimbo(reloj=reloj)
    rng = np.random.default_rng(1)
    paso = 1.0 / 20                                          # 20 fotogramas por segundo, como el vídeo real
    # 3 s a 3,2 px con un 7 % de dispersión: el número es el percentil 25, por debajo de la mediana
    valores = []
    for i in range(60):
        reloj.t += paso
        v = 3.2 * (1 + 0.07 * rng.normal())
        valores.append(v)
        assert h.anotar(medida(v), recorte=i), i
    actual, mejor, recorte, n = h.resumen()
    esperado = np.percentile(valores[-50:], enfoque.PERCENTIL_VENTANA)
    assert abs(actual - esperado) <= enfoque.HISTERESIS_PX + 1e-6, (actual, esperado)
    assert actual < np.median(valores[-50:]) and n in (50, 51), (actual, n)
    assert mejor is not None and mejor <= actual + 1e-9, (mejor, actual)
    assert recorte == int(np.argmin(valores)) or valores[recorte] <= np.percentile(valores[-50:], 5), recorte
    print(f"  ventana de 2,5 s: número {actual:.2f} (percentil {enfoque.PERCENTIL_VENTANA} de {n} fotogramas), mejor {mejor:.2f}")
    # Histéresis: un cambio pequeño de la ventana no mueve el número enseñado
    mostrado = h.mostrado
    h.mostrado = actual + 0.5 * enfoque.HISTERESIS_PX
    assert h.resumen()[0] == actual + 0.5 * enfoque.HISTERESIS_PX
    h.mostrado = mostrado
    # Otro radio (el círculo se ajustó al terminador) y el disco moviéndose deprisa: se descartan
    reloj.t += paso
    assert not h.anotar(medida(1.5, r=240.0), recorte=None) and h.rechazadas == 1
    reloj.t += paso
    assert not h.anotar(medida(3.0, cx=300.0 + 5.0), recorte=None) and h.movidas == 1   # 5 px en 50 ms = 100 px/s
    reloj.t += paso
    assert h.anotar(medida(3.1, cx=305.2), recorte=None)                                 # 0,2 px en 50 ms: seeing
    assert h.mejor is not None and h.mejor > 2.5, h.mejor                                # el 1,5 falso no es el mejor
    # Tras 3 s sin medidas válidas, el número desaparece y el mejor se conserva
    reloj.t += 3.0
    actual, mejor, recorte, n = h.resumen()
    assert actual is None and n == 0 and mejor is not None, (actual, n, mejor)
    print(f"  descartadas: {h.rechazadas} por el radio, {h.movidas} por moverse; sin medidas 3 s el número se va y el mejor ({mejor:.2f}) se queda")


def probar_veredicto():
    reloj = Reloj()
    h = enfoque.HistorialLimbo(reloj=reloj)
    rng = np.random.default_rng(2)
    paso = 1.0 / 20
    dichos = []

    def correr(segundos, nivel):
        for __ in range(int(round(segundos / paso))):
            reloj.t += paso
            h.anotar(medida(nivel * (1 + 0.07 * rng.normal())), recorte=None)
            h.resumen()
            v = h.veredicto()
            if v:
                dichos.append((round(reloj.t, 1), v))

    correr(2.0, 3.2)
    assert not dichos, dichos                        # la ventana anterior aún no está llena: calla
    correr(8.0, 3.2)
    assert not dichos, dichos                        # sin cambio no dice nada (ni «igual»)
    correr(6.0, 4.5)                                 # el enfocador se pasa: +40 %
    assert [v for __, v in dichos] == ["empeora", "igual"] or [v for __, v in dichos][:1] == ["empeora"], dichos
    assert dichos[0][0] <= 10.0 + 2 * enfoque.VENTANA_S + enfoque.VOZ_CADA_S, dichos      # lo dice en pocos segundos
    n_antes = len(dichos)
    correr(6.0, 4.5)
    assert all(v == "igual" for __, v in dichos[n_antes:]) and len(dichos) <= n_antes + 1, dichos   # «igual» como mucho una vez
    correr(6.0, 3.0)                                 # se vuelve: −33 %
    assert "mejora" in [v for __, v in dichos[n_antes:]], dichos
    assert "empeora" not in [v for __, v in dichos[n_antes:]], dichos
    print(f"  veredictos: {dichos}")


def probar_composicion():
    verde = luna_cuarto()
    info = enfoque.medir_disco(verde, escala_px=1.0, terminador=True)
    gris = enfoque.revelar_gris_disco(verde)
    recorte = enfoque.recorte_estrella(gris, info["x"], info["y"])
    info.update({"mejor": info["limbo"] - 0.2})
    tendencia = [(t, info["limbo"] + 0.8 * np.sin(t / 7.0)) for t in np.arange(0, 60, 0.25)]
    for lado in (600, 1200):
        g = cv2.resize(gris, (lado, lado))
        img = enfoque.componer(g, recorte, info, pulsado=(info["x"], info["y"]), tendencia=tendencia, etiqueta="terminador")
        assert img.shape == (lado, lado, 3) and img.dtype == np.uint8, img.shape
        # La gráfica (fondo oscurecido con marco verde) queda bajo el recorte, dentro de la imagen
        m = max(6, lado // 100)
        rl = int(lado * (0.4 if lado < 800 else 0.3))
        franja = img[m + rl + 2 * m: m + rl + 2 * m + int(rl * 0.4), lado - m - rl: lado - m]
        verde_marco = np.sum((franja[..., 1] > 150) & (franja[..., 0] < 120))
        assert verde_marco > 50, verde_marco
    sin = enfoque.componer(gris, recorte, info, tendencia=[], etiqueta="limbo")
    assert sin.shape == (600, 600, 3)
    print("  composición: etiqueta del recorte y gráfica dibujadas en 600 y 1200 px")


if __name__ == "__main__":
    print("1. medir_disco con la Luna sintética y cortada por el borde")
    probar_medida()
    print("2. HistorialLimbo: ventana, histéresis, descartes")
    probar_historial()
    print("3. veredicto por voz")
    probar_veredicto()
    print("4. componer con etiqueta y gráfica")
    probar_composicion()
    print("Todo bien.")
