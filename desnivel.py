# desnivel.py
#
# Desnivel del trípode de una montura altacimutal, sacado de las fotos resueltas de la
# alineación automática, sin tocar la montura ni depender de lo que diga SynScan.
#
# La cámara va fija al tubo: respecto a los ejes de la montura no gira nunca
# (orientacion_camara.py). Lo que la app mide tras cada plate solving es el ángulo del
# eje x de la foto respecto al acimut VERDADERO (el del horizonte del lugar). Con el eje
# de acimut vertical ese ángulo es el mismo en todo el cielo. Si el eje está inclinado un
# ángulo ε hacia el acimut A0, la dirección «acimut de la montura» deja de coincidir con
# el acimut verdadero y el ángulo medido en un punto de acimut A y altura h pasa a ser
#
#       ángulo(A, h) = giro de la cámara + ε · sen(A − A0) / cos(h)
#
# (triángulo esférico cénit – polo de la montura – punto; primer orden en ε). Con cuatro
# puntos o más repartidos en acimut se ajustan el giro de la cámara y la inclinación por
# mínimos cuadrados. El término 1/cos(h) es el que hace daño cerca del cénit.
#
# Por qué importa: SynScan sigue el cielo en una altacimutal calculando la velocidad de
# cada eje como si el de acimut fuese vertical. Con el trípode desnivelado, cerca del
# cénit la velocidad de acimut sale mal y la montura deriva dentro de cada foto. El
# 7-nov-2026, con unos 1,5° de desnivel, la Nebulosa Creciente (a 73° de altura) salió
# con 6 px de deriva en cada foto de 20 s y M29 (a 82°) peor; los puntos de la alineación
# de aquella noche están en herramientas/probar_desnivel.py.

import math

import numpy as np

MINIMO_PUNTOS = 4
# Lo que se equivoca el ángulo de cada punto aunque el ajuste salga redondo (grados). El
# residuo de las noches reales es de 0,5–1°; con pocos puntos el ajuste no lo ve.
RUIDO_MINIMO_DEG = 0.5
# Altura a partir de la cual el desnivel se nota en las estrellas (texto de los avisos)
ALTURA_SENSIBLE = 70
RUMBOS = ("N", "NE", "E", "SE", "S", "SO", "O", "NO")       # se traducen al decirlos (O → W)


def rumbo(acimut_deg):
    """Punto cardinal (de RUMBOS) más cercano a un acimut en grados (0 = norte, 90 = este)."""
    return RUMBOS[int(((float(acimut_deg) % 360.0) + 22.5) // 45) % 8]


def angulo_previsto(az, alt, giro_camara, inclinacion, hacia_az):
    """Ángulo (grados) del eje x de la foto respecto al acimut verdadero que predice el modelo."""
    return giro_camara + inclinacion * math.sin(math.radians(az - hacia_az)) / max(math.cos(math.radians(alt)), 0.05)


def ajustar(puntos):
    """
    Ajusta la inclinación del eje de acimut a `puntos` = [(acimut, altura, ángulo del eje x
    de la foto respecto al acimut), ...] en grados, uno por foto resuelta. Devuelve None con
    menos de MINIMO_PUNTOS o si están tan mal repartidos que no se puede saber, y si no:

      inclinacion_deg     cuánto se aparta el eje de acimut de la vertical
      hacia_az            acimut hacia el que se inclina la parte de arriba del eje: el
                          trípode está BAJO por ese lado
      incertidumbre_deg   1 sigma de la inclinación
      giro_camara_deg     ángulo de la cámara respecto a los ejes, ya sin el desnivel
      residuo_deg         lo que no explica el ajuste (RMS)
      n                   puntos usados
    """
    pts = [(float(a), float(h), float(g)) for a, h, g in puntos
           if all(math.isfinite(float(v)) for v in (a, h, g)) and abs(float(h)) < 87.0]
    if len(pts) < MINIMO_PUNTOS:
        return None
    az = np.radians([p[0] for p in pts])
    cosh = np.cos(np.radians([p[1] for p in pts]))
    ang = np.array([p[2] for p in pts])
    # Los ángulos son circulares (359,6° y 2,8° están a 3,2°): se miden desde su media
    base = math.degrees(math.atan2(np.sin(np.radians(ang)).mean(), np.cos(np.radians(ang)).mean()))
    d = (ang - base + 180.0) % 360.0 - 180.0
    # d = t0 + p·sen(A)/cos(h) − q·cos(A)/cos(h), con p = ε·cos(A0) y q = ε·sen(A0)
    X = np.column_stack([np.ones(len(pts)), np.sin(az) / cosh, -np.cos(az) / cosh])
    if np.linalg.matrix_rank(X) < 3 or np.linalg.cond(X) > 50.0:
        return None            # todos los puntos hacia el mismo lado: el desnivel no se distingue del giro
    sol, *_ = np.linalg.lstsq(X, d, rcond=None)
    t0, p, q = (float(v) for v in sol)
    residuos = d - X @ sol
    gl = len(pts) - 3
    sigma = max(RUIDO_MINIMO_DEG, math.sqrt(float((residuos ** 2).sum()) / gl) if gl > 0 else 0.0)
    cov = sigma ** 2 * np.linalg.inv(X.T @ X)
    inclinacion = math.hypot(p, q)
    return {
        "inclinacion_deg": round(inclinacion, 2),
        "hacia_az": round(math.degrees(math.atan2(q, p)) % 360.0, 1),
        "incertidumbre_deg": round(math.sqrt((float(cov[1, 1]) + float(cov[2, 2])) / 2.0), 2),
        "giro_camara_deg": round((base + t0) % 360.0, 2),
        "residuo_deg": round(math.sqrt(float((residuos ** 2).mean())), 2),
        "n": len(pts),
    }


def es_de_fiar(ajuste):
    """¿La inclinación medida se distingue del ruido? (más de dos sigmas)"""
    return bool(ajuste) and ajuste["inclinacion_deg"] >= 2.0 * ajuste["incertidumbre_deg"]


def hay_que_avisar(ajuste, umbral_deg):
    """True si el desnivel medido llega al umbral ([Alineacion] aviso_desnivel; 0 = no avisar) y es de fiar."""
    return bool(ajuste) and umbral_deg > 0 and ajuste["inclinacion_deg"] >= umbral_deg and es_de_fiar(ajuste)
