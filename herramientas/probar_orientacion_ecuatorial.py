# herramientas/probar_orientacion_ecuatorial.py
#
# Prueba sin montura ni cámara de la orientación de la cámara con una montura
# ecuatorial (orientacion_camara.ultima / para_ecuatorial), con los datos reales del
# 26 y el 28-nov-2026:
#
#   - La orientación guardada el 26-nov a las 20:21 (centrado, montura ya en ecuatorial)
#     vista con el Sol del 28-nov a las 13:32 está girada ~75°: es el giro que hizo que el
#     guiado por el disco se fuera en espiral (2,1′ → 2,8′ → 3,6′ → 4,4′ → 4,9′, ajuste de
#     la espiral: 74°). Se simula el bucle del guiado con la orientación vieja (diverge) y
#     con la nueva (converge).
#   - Ida y vuelta: H_eq sacada de la H horizontal y rehecha en el mismo instante y
#     dirección devuelve la H de partida; con H_eq guardada, igual.
#   - Altacimutal: ultima() devuelve la orientación tal cual.
#   - Pasar al otro lado del pilar gira la cámara 180°.
#   - Sin saber dónde apunta la montura, la H de la foto resuelta y un aviso.
#
#   python herramientas\probar_orientacion_ecuatorial.py
#
# Sale con código 0 si todo pasa y 1 si no.

import os
import sys
import json
import math
import shutil
import tempfile

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import orientacion_camara as oc       # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OBSERVATORIO = (39.63556, -0.5377, 224.0)
# datos/orientacion_camara_altaz.json de MINIBESTIA el 28-nov-2026 (centrado del 26-nov, 20:21)
GUARDADA = {
    "H": [[5.8202886056033343e-05, 0.000806715756509449], [-0.0008066967449208206, 5.784095910286169e-05]],
    "binning_foto": 1, "ancho": 3008, "alto": 3008, "escala_arcsec": 2.9115, "angulo_x_deg": 274.13,
    "paridad": 1, "alt": 57.08, "az": 294.89, "instante_utc": "2026-11-26T19:21:04.358",
    "fecha": "2026-11-26T20:21:05", "origen": "centrado",
}
INSTANTE_GUIADO = 1795869152.0     # 28-nov-2026 12:32:32 UTC (13:32:32 en España), 1.ª corrección del guiado

fallos = []


def comprobar(cond, texto):
    print(("  ✅ " if cond else "  ❌ ") + texto)
    if not cond:
        fallos.append(texto)


def giro_grados(A, B):
    """Ángulo del giro R tal que A ≈ R·B."""
    R = np.asarray(A) @ np.linalg.inv(np.asarray(B))
    return math.degrees(math.atan2(R[1, 0], R[0, 0]))


def sol(instante):
    import disco
    return disco.direccion_astro("sol", instante, *OBSERVATORIO)


def simular_guiado(H_usada, H_real, pasos=8, deriva=(0.3 / 60, -0.25 / 60), umbral=2 / 60):
    """
    Bucle del guiado: el disco se desplaza `deriva` (grados, horizontal) por foto; se mide
    con la H que cree la app (H_usada) y se corrige si pasa del umbral. Devuelve la lista
    de desvíos medidos (′).
    """
    verdad = np.zeros(2)               # desplazamiento real del disco desde el centro (horizontal, grados)
    medidos = []
    for __ in range(pasos * 6):
        verdad = verdad + np.array(deriva)
        pix = np.linalg.solve(H_real, verdad)          # donde sale el disco en la foto
        du_dv = H_usada @ pix                          # lo que la app cree que es
        if math.hypot(*du_dv) > umbral:
            medidos.append(math.hypot(*du_dv) * 60)
            verdad = verdad - du_dv                    # la montura mueve lo pedido
    return medidos


def main():
    previo = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="orient_eq_")
    try:
        os.chdir(tmp)
        os.makedirs("datos")
        with open("config.ini", "w", encoding="utf-8") as f:
            f.write("[Observatorio]\nlatitud_observatorio = %s\nlongitud_observatorio = %s\naltitud_observatorio = %s\n" % OBSERVATORIO)
        with open(oc.RUTA, "w", encoding="utf-8") as f:
            json.dump(GUARDADA, f)

        print("Ida y vuelta (mismo instante y dirección que la foto resuelta)")
        from astropy.coordinates import SkyCoord, EarthLocation, AltAz
        from astropy.time import Time
        import astropy.units as u
        t0 = Time(GUARDADA["instante_utc"], format="isot", scale="utc")
        lugar = EarthLocation(lat=OBSERVATORIO[0] * u.deg, lon=OBSERVATORIO[1] * u.deg, height=OBSERVATORIO[2] * u.m)
        c0 = SkyCoord(az=GUARDADA["az"] * u.deg, alt=GUARDADA["alt"] * u.deg, frame=AltAz(obstime=t0, location=lugar)).icrs
        o0 = oc.para_ecuatorial(GUARDADA, instante=t0, apuntado=(c0.ra.deg, c0.dec.deg), observatorio=OBSERVATORIO)
        comprobar(np.allclose(o0["H"], GUARDADA["H"], atol=2e-7), "la H rehecha en el instante de la foto es la guardada")
        H_eq = oc.matriz_ecuatorial_de(GUARDADA, OBSERVATORIO)
        comprobar(abs(abs(np.linalg.det(H_eq)) ** 0.5 * 3600 - 2.9115) < 0.01, "H_eq conserva la escala (2,91″/px)")
        comprobar(np.linalg.det(H_eq) < 0, "H_eq con el AR hacia el este (determinante negativo con paridad +1 en horizontal)")
        con_eq = dict(GUARDADA, H_eq=H_eq.tolist())
        o1 = oc.para_ecuatorial(con_eq, instante=t0, apuntado=(c0.ra.deg, c0.dec.deg), observatorio=OBSERVATORIO)
        comprobar(np.allclose(o1["H"], GUARDADA["H"], atol=2e-7), "con H_eq guardada, lo mismo")

        print("El Sol del 28-nov a las 13:32 con la orientación del 26-nov")
        ra_s, dec_s = sol(INSTANTE_GUIADO)
        oc.establecer_modo_montura("ecuatorial_alemana")
        oc.establecer_apuntado(lambda: None)
        o = oc.ultima(instante=INSTANTE_GUIADO, apuntado=(ra_s, dec_s))
        comprobar(o.get("marco") == "ecuatorial", "ultima() rehace la H en ecuatorial")
        g = giro_grados(o["H"], GUARDADA["H"])
        print("     giro entre la H guardada y la de ese momento: %.1f°" % g)
        comprobar(70 < g < 80, "el giro es el de la espiral del guiado (74° ajustados con el log)")
        vieja = simular_guiado(np.array(GUARDADA["H"]), np.array(o["H"]))
        nueva = simular_guiado(np.array(o["H"]), np.array(o["H"]))
        print("     guiado con la H vieja: " + ", ".join("%.1f′" % m for m in vieja[:6]))
        print("     guiado con la H nueva: " + ", ".join("%.1f′" % m for m in nueva[:6]))
        comprobar(len(vieja) >= 3 and vieja[2] > vieja[0], "con la H vieja el desvío crece en cada corrección")
        comprobar(all(m < 2.6 for m in nueva), "con la H nueva cada corrección se hace al pasar el umbral y lo deja en su sitio")

        print("Más tarde el giro cambia (el ángulo paraláctico sigue al Sol)")
        t2 = INSTANTE_GUIADO + 3 * 3600
        o2 = oc.ultima(instante=t2, apuntado=sol(t2))
        comprobar(abs(giro_grados(o2["H"], o["H"])) > 10, "tres horas después la H es otra (%.0f°)" % giro_grados(o2["H"], o["H"]))

        print("Dirección de la montura por el proveedor")
        oc.establecer_apuntado(lambda: {"ra": ra_s, "dec": dec_s, "lado": None})
        o3 = oc.ultima(instante=INSTANTE_GUIADO)
        comprobar(np.allclose(o3["H"], o["H"]), "sin dirección explícita usa la de la montura")

        print("Lado del pilar")
        with open(oc.RUTA, "w", encoding="utf-8") as f:
            json.dump(dict(GUARDADA, lado_pilar=0), f)
        oc.establecer_apuntado(lambda: {"ra": ra_s, "dec": dec_s, "lado": 1})
        o4 = oc.ultima(instante=INSTANTE_GUIADO)
        comprobar(np.allclose(o4["H"], -np.array(o["H"])) and o4["pilar_girado"], "al otro lado del pilar, la cámara girada 180°")
        oc.establecer_apuntado(lambda: {"ra": ra_s, "dec": dec_s, "lado": 0})
        comprobar(np.allclose(oc.ultima(instante=INSTANTE_GUIADO)["H"], o["H"]), "en el mismo lado, sin girar")

        print("Sin saber dónde apunta")
        oc.establecer_apuntado(lambda: None)
        o5 = oc.ultima()
        comprobar(o5["H"] == GUARDADA["H"] and "aviso" in o5, "H de la foto resuelta y un aviso")

        print("Altacimutal")
        oc.establecer_modo_montura("altacimutal")
        comprobar(oc.ultima(instante=INSTANTE_GUIADO, apuntado=(ra_s, dec_s))["H"] == GUARDADA["H"], "la H guardada, sin tocar")
        oc.establecer_modo_montura(None)
        with open(oc.RUTA, "w", encoding="utf-8") as f:
            json.dump(dict(GUARDADA, modo_montura="ecuatorial"), f)
        oc.establecer_apuntado(lambda: {"ra": ra_s, "dec": dec_s, "lado": None})
        comprobar(oc.ultima(instante=INSTANTE_GUIADO).get("marco") == "ecuatorial",
                  "sin montura conectada vale el modo con el que se resolvió la foto")
    finally:
        oc.establecer_modo_montura(None)
        oc.establecer_apuntado(None)
        os.chdir(previo)
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fallos:
        print("❌ %d comprobación(es) fallida(s)" % len(fallos))
        return 1
    print("✅ Orientación con montura ecuatorial: todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
