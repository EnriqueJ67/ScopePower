# apuntado_teorico.py
#
# "Apunta aquí" sin plate solving (modo día): de día no hay estrellas que resolver,
# pero la cámara va siempre montada y enfocada en el mismo tubo de la altacimutal,
# así que su orientación respecto a los ejes y el tamaño del píxel en el cielo no
# cambian: los da el último plate solving que salió bien, de cualquier foto
# (orientacion_camara.py guarda la matriz H tras cada resolución).
#
# Con H, un toque en la foto se convierte en un desplazamiento del apuntado en el
# plano tangente horizontal (Δacimut·cos(altura), Δaltura); se aplica a la posición
# actual de la montura y el resultado se pasa a AR/Dec para un GoTo corto. El
# movimiento es relativo: vale aunque la montura esté mal alineada, igual que al
# mover con el mando.
#
# Precisión: la orientación varía ±2° entre resoluciones y la escala es exacta, así
# que en un salto de medio campo (~0,6° con la Saturn-C a 260 mm) el error es de un
# par de minutos de arco, más lo que falle el GoTo corto de la montura.

import math

import numpy as np

import foto_dia
import giro_camara
import orientacion_camara
from idioma import _, fmt_num, formato_fecha


class SinDatos(Exception):
    """Falta la orientación de la cámara o una foto en pantalla."""


def geometria_visible(vista):
    """(w, h) en píxeles del sensor (con binning) de la parte de la foto que se ve en la web."""
    __, __, w, h = foto_dia.zona_recorte(vista["ancho"], vista["alto"], vista.get("campo", 1.0))
    return w, h


def desplazamiento(fx, fy, vista, orientacion):
    """
    (du, dv) en grados en el plano tangente horizontal (Δaz·cos alt, Δalt) desde el
    centro de la foto hasta el punto (fx, fy) de la foto que se ve (fracciones 0–1).
    Mover el apuntado esa cantidad deja el punto en el centro.
    """
    if not vista or not vista.get("ancho"):
        raise SinDatos(_("No hay ninguna foto en pantalla; haz una foto primero."))
    if not orientacion or "H" not in orientacion:
        raise SinDatos(_("Todavía no se conoce la orientación de la cámara: hace falta un plate solving "
                         "que salga bien (centrado, alineación, apilado o foto de noche) con la cámara como está ahora."))
    w, h = geometria_visible(vista)
    # El toque viene sobre la foto como se ve (con el giro de Cámara): al fotograma sin girar
    fx, fy = giro_camara.original(fx, fy, w, h, vista.get("encuadre"))
    # Píxeles del fotograma con binning -> píxeles de la foto resuelta
    factor = float(vista.get("binning", 1) or 1) / float(orientacion.get("binning_foto", 1) or 1)
    d = np.array([(fx - 0.5) * w, (fy - 0.5) * h]) * factor
    du, dv = np.array(orientacion["H"], dtype=float) @ d
    return float(du), float(dv)


def campo_visible_grados(vista, orientacion):
    """(ancho, alto) en grados de la foto que se ve (con su giro), con la escala del plate solving."""
    w, h = giro_camara.tamano(*geometria_visible(vista), vista.get("encuadre"))
    factor = float(vista.get("binning", 1) or 1) / float(orientacion.get("binning_foto", 1) or 1)
    esc = float(orientacion["escala_arcsec"]) / 3600.0
    return w * factor * esc, h * factor * esc


def mover_en_horizonte(az0, alt0, du, dv):
    """
    Dirección (az, alt) en grados que queda a (du, dv) grados del apuntado (az0, alt0)
    en su plano tangente (proyección gnomónica inversa, válida lejos del cénit).
    """
    a, h = math.radians(az0), math.radians(alt0)
    c = np.array([math.cos(h) * math.cos(a), math.cos(h) * math.sin(a), math.sin(h)])
    este = np.array([-math.sin(a), math.cos(a), 0.0])                                  # hacia más acimut
    arriba = np.array([-math.sin(h) * math.cos(a), -math.sin(h) * math.sin(a), math.cos(h)])   # hacia más altura
    p = c + math.tan(math.radians(du)) * este + math.tan(math.radians(dv)) * arriba
    p /= np.linalg.norm(p)
    return math.degrees(math.atan2(p[1], p[0])) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, p[2]))))


def describir_orientacion(orientacion):
    """Texto corto de dónde sale la orientación (para la web)."""
    if not orientacion:
        return ""
    origen = str(orientacion.get("origen") or "plate solving")
    fecha = ""
    try:        # instante de la foto resuelta (UTC) en hora local
        from datetime import datetime, timezone
        utc = datetime.fromisoformat(str(orientacion.get("instante_utc"))[:19]).replace(tzinfo=timezone.utc)
        fecha = utc.astimezone().strftime(formato_fecha(hora=True, corta=True))
    except ValueError:
        pass
    escala = fmt_num(float(orientacion["escala_arcsec"]), 2)
    angulo = round(float(orientacion["angulo_x_deg"]))
    if fecha:
        return _("{origen} del {fecha}: {escala}″/px, eje x de la foto a {angulo}° del acimut",
                 origen=origen, fecha=fecha, escala=escala, angulo=angulo)
    return _("{origen}: {escala}″/px, eje x de la foto a {angulo}° del acimut",
             origen=origen, escala=escala, angulo=angulo)


if __name__ == "__main__":
    # El punto de la derecha de la foto, con el eje x hacia +acimut, pide mover a más acimut
    H = [[2.91 / 3600, 0.0], [0.0, -2.91 / 3600]]        # x -> +az, fila hacia abajo -> −alt
    o = {"H": H, "binning_foto": 1, "escala_arcsec": 2.91, "angulo_x_deg": 0.0}
    v = {"ancho": 3008, "alto": 3008, "binning": 1, "campo": 0.5}
    du, dv = desplazamiento(1.0, 0.5, v, o)
    assert abs(du - 752 * 2.91 / 3600) < 1e-6 and abs(dv) < 1e-9, (du, dv)
    du, dv = desplazamiento(0.5, 0.0, v, o)               # arriba en la foto -> más altura
    assert dv > 0 and abs(du) < 1e-9
    # Binning 2 en la foto de día: mismos grados por fracción de foto
    v2 = dict(v, ancho=1504, alto=1504, binning=2)
    assert abs(desplazamiento(1.0, 0.5, v2, o)[0] - desplazamiento(1.0, 0.5, v, o)[0]) < 1e-3
    # Proyección: a 60° de altura, 1° en el plano tangente son ~2° de acimut
    az, alt = mover_en_horizonte(100.0, 60.0, 1.0, 0.0)
    assert abs(az - 102.0) < 0.01 and abs(alt - 60.0) < 0.02, (az, alt)   # círculo máximo: baja un poco
    az, alt = mover_en_horizonte(359.5, 30.0, 0.0, -0.5)
    assert abs(az - 359.5) < 1e-6 and abs(alt - 29.5) < 1e-3, (az, alt)
    print("✅ apuntado_teorico: desplazamientos y proyección correctos")
