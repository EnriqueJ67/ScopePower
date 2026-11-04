# herramientas/probar_luna_anotada.py
#
# Prueba de la Luna anotada (luna_anotada.py) sin fotos reales ni Internet:
#   1. La geometría (libración, punto subsolar, eje, fase) contra las efemérides de JPL
#      Horizons para Greenwich en tres fechas (valores copiados de Horizons el 20-oct-2026).
#   2. Lunas sintéticas (el mapa de albedo con la iluminación de cada fecha) en varias fases,
#      con tamaño, posición, giro y espejo al azar: se tienen que recuperar el disco (al 1,5 %
#      del radio), el giro (a 1°) y el espejo.
#   3. Los accidentes: los de una foto salen dentro del disco, con los grandes primero, y
#      Tycho cae donde debe; el terminador y los contornos de los mares salen bien formados.
#   4. El reparto de nombres con la geometría de una foto real (Luna del 21-oct-2026, disco de
#      317 px): los destacados por delante de lo que apenas se ve y, con el propio
#      static/js/luna_anotada.js ejecutado en Node, qué nombres caben y que no se solapan.
#
# Uso:
#   python herramientas\probar_luna_anotada.py

import os
import sys
import json
import math
import time
import shutil
import subprocess
from datetime import datetime, timezone

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RAIZ)

import luna_anotada as L          # noqa: E402

# Horizons (observador en Greenwich, 0° E 51,4769° N, 50 m): fracción iluminada, libración
# (lon, lat), punto subsolar (lon, lat) en longitudes este 0..360
HORIZONS = [
    ("2026-10-20", 0.6208649, 355.058066, 2.487662, 70.992566, -1.329104),
    ("2026-10-25", 0.9800584, 354.215866, -4.390306, 10.194278, -1.387223),
    ("2026-10-30", 0.8035932, 2.042521, -5.054116, 309.453449, -1.438742),
]

fallos = []


def comprobar(condicion, texto):
    print(("  OK   " if condicion else "  FALLO ") + texto)
    if not condicion:
        fallos.append(texto)


def dif_angulo(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def pintar(geo, ancho, alto, cx, cy, r, giro, espejo, semilla=0):
    """Luna sintética en una imagen uint8 de ancho×alto, con el disco y la orientación dados."""
    import cv2
    yy, xx = np.mgrid[0:alto, 0:ancho].astype(np.float64)
    Xr, Yr = (xx - cx) / r, -(yy - cy) / r
    g = math.radians(giro)
    m = -1.0 if espejo else 1.0
    X = math.cos(g) * Xr + math.sin(g) * Yr
    Y = m * (-math.sin(g) * Xr + math.cos(g) * Yr)
    dentro = X * X + Y * Y < 1
    img = np.zeros((alto, ancho), np.float32)
    f = L._desde_vista(geo, X[dentro], Y[dentro])
    lat = np.degrees(np.arcsin(np.clip(f[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(f[:, 1], f[:, 0]))
    mapa = L._albedo()
    h, w = mapa.shape
    a = mapa[np.clip(((90 - lat) / 180 * h).astype(int), 0, h - 1), ((lon + 180) / 360 * w).astype(int) % w]
    fi = f @ geo["R"]
    ci = np.clip(fi @ geo["s"], 0, None)
    ce = np.clip(-(fi @ geo["u"]), 1e-3, None)
    img[dentro] = 290 * a * ci / (ci + ce)
    img = cv2.GaussianBlur(img, (0, 0), 1.0) + np.random.default_rng(semilla).normal(8, 3, img.shape)
    return np.clip((np.clip(img, 0, None) / 255.0) ** (1 / 2.2) * 255, 0, 255).astype(np.uint8)


def probar_geometria():
    print("Geometría contra JPL Horizons (Greenwich)")
    for fecha, ilum, lib_lon, lib_lat, sub_lon, sub_lat in HORIZONS:
        t = datetime.fromisoformat(fecha + "T00:00:00+00:00").timestamp()
        g = L.geometria(t, 51.4769, 0.0, 50.0)
        errores = [abs(dif_angulo(g["libracion_lon"], lib_lon)), abs(g["libracion_lat"] - lib_lat),
                   abs(dif_angulo(g["subsolar_lon"], sub_lon)), abs(g["subsolar_lat"] - sub_lat)]
        comprobar(max(errores) < 0.05 and abs(g["iluminada"] - ilum) < 0.002,
                  f"{fecha}: libración y punto subsolar a {max(errores):.3f}° de Horizons, iluminada {g['iluminada']:.4f}")


def probar_sinteticas():
    print("Lunas sintéticas: disco, giro y espejo")
    rng = np.random.default_rng(1)
    inicio = datetime(2026, 10, 1, 21, tzinfo=timezone.utc).timestamp()
    tiempos = []
    for prueba in range(12):
        t = inicio + (2 + prueba * 2.3) * 86400
        geo = L.geometria(t, 39.95, -1.11)
        giro, espejo = float(rng.uniform(0, 360)), bool(rng.integers(0, 2))
        r = float(rng.uniform(150, 600))
        cx, cy = 800 + rng.uniform(-150, 150), 600 + rng.uniform(-100, 100)
        if geo["iluminada"] < 0.03:          # Luna nueva: no hay nada que anotar (y debe decirlo)
            img = pintar(geo, 1600, 1200, cx, cy, r, giro, espejo, semilla=prueba)
            try:
                L.anotar(img, t, 39.95, -1.11)
                comprobar(False, f"iluminada {geo['iluminada']:.2f}: debería avisar de que no se ve la Luna")
            except L.ErrorLuna as e:
                comprobar(True, f"iluminada {geo['iluminada']:.2f}: avisa ({e})")
            continue
        img = pintar(geo, 1600, 1200, cx, cy, r, giro, espejo, semilla=prueba)
        t0 = time.time()
        res = L.anotar(img, t, 39.95, -1.11)
        tiempos.append(time.time() - t0)
        d, o = res["disco"], res["orientacion"]
        e_centro = math.hypot(d["cx"] - cx, d["cy"] - cy)
        e_giro = dif_angulo(o["giro"], giro)
        comprobar(e_centro < 0.015 * r and abs(d["r"] - r) < 0.015 * r and abs(e_giro) < 1.0 and o["espejo"] == espejo,
                  f"iluminada {geo['iluminada']:.2f}, radio {r:.0f} px: centro a {e_centro:.1f} px, radio {d['r'] - r:+.1f} px, "
                  f"giro {e_giro:+.2f}°, espejo {'sí' if o['espejo'] else 'no'} (margen {o['margen']:.3f})")
    print(f"  {np.mean(tiempos[1:]):.2f} s por foto de 1600×1200")


def probar_accidentes():
    print("Accidentes sobre la foto")
    t = datetime(2026, 10, 21, 19, 30, tzinfo=timezone.utc).timestamp()
    geo = L.geometria(t, 39.95, -1.11)
    cx, cy, r, giro = 800.0, 600.0, 450.0, 37.0
    img = pintar(geo, 1600, 1200, cx, cy, r, giro, True)
    res = L.anotar(img, t, 39.95, -1.11)
    acc = res["accidentes"]
    dentro = all(math.hypot(a["x"] - cx, a["y"] - cy) < r * 1.02 for a in acc)
    comprobar(len(acc) > 100 and dentro, f"{len(acc)} accidentes, todos dentro del disco")
    comprobar(acc[0]["familia"] == "estacion" or acc[0]["familia"] == "mar", f"el primero es un alunizaje o un mar ({acc[0]['nombre']})")
    tycho = next((a for a in acc if a["nombre"] == "Tycho"), None)
    X, Y, __, __ = L.a_vista(geo, L._vectores_superficie(np.array([-43.296]), np.array([-11.215])))
    x, y = L._a_foto(X, Y, {"cx": cx, "cy": cy, "r": r}, {"giro": giro, "espejo": True})
    comprobar(tycho is not None and math.hypot(tycho["x"] - x[0], tycho["y"] - y[0]) < 3.0,
              "Tycho cae donde debe" + (f" (a {math.hypot(tycho['x'] - x[0], tycho['y'] - y[0]):.1f} px)" if tycho else ""))
    comprobar(len(res["terminador"]) > 20, f"terminador con {len(res['terminador'])} puntos")
    trazado = res["mares"]
    comprobar(trazado.startswith("M") and trazado.count("M") > 50 and "l M" not in trazado and not trazado.endswith("l"),
              f"contornos de los mares: {trazado.count('M')} trazos, {len(trazado) // 1024} KB")


def probar_trozos():
    print("Trozos de la Luna (disco cortado, interior sin limbo, seguimiento en vivo)")
    t = datetime(2026, 10, 21, 19, 30, tzinfo=timezone.utc).timestamp()
    geo = L.geometria(t, 39.95, -1.11)
    R, giro, espejo = 1300.0, 62.0, True
    grande = pintar(geo, 3000, 3000, 1500, 1500, R, giro, espejo, semilla=3)
    disco_ok = {"cx": 1500.0, "cy": 1500.0, "r": R}

    def _donde(lat, lon):
        X, Y, __, __ = L.a_vista(geo, L._vectores_superficie(np.array([lat]), np.array([lon])))
        x, y = L._a_foto(X, Y, disco_ok, {"giro": giro, "espejo": espejo})
        return float(x[0]), float(y[0])

    def _caso(texto, x0, y0, lado, debe_anotar=True, previo=None):
        trozo = np.ascontiguousarray(grande[y0:y0 + lado, x0:x0 + lado])
        t0 = time.time()
        try:
            res = L.anotar(trozo, t, 39.95, -1.11, previo=previo)
        except L.ErrorLuna as e:
            comprobar(not debe_anotar, f"{texto}: no anota ({e}) {time.time() - t0:.1f} s")
            return None
        d = res["disco"]
        error = math.hypot(d["cx"] - (1500 - x0), d["cy"] - (1500 - y0))
        bien = debe_anotar and error < 0.03 * R and abs(d["r"] - R) < 0.04 * R and res["orientacion"]["espejo"] == espejo
        comprobar(bien, f"{texto}: {res['metodo']}{' seguido' if res['seguido'] else ''}, centro a {error:.0f} px, "
                        f"radio {d['r']:.0f} (real {R:.0f}), {time.time() - t0:.1f} s")
        return res

    # Disco cortado: un arco largo del limbo a la vista
    _caso("disco cortado, medio disco y algo más", 0, 300, 1700)
    # Interior: alrededor de Mare Vaporum (mares, montes y tierras altas), sin limbo
    x, y = _donde(13.3, 3.6)
    r1 = _caso("interior, Mare Vaporum", int(x - 500), int(y - 500), 1000)
    # La misma zona un poco movida, como el fotograma siguiente del vivo: se sigue, no se busca
    if r1:
        _caso("fotograma siguiente del vivo", int(x - 492), int(y - 496), 1000, previo=r1)
    # Una foto sin Luna (negra, con la tapa puesta): se dice enseguida, sin buscarla como trozo
    t0 = time.time()
    try:
        L.anotar(np.full((600, 800), 12, np.uint8), t, 39.95, -1.11)
        comprobar(False, "foto negra: debería avisar de que no se ve la Luna")
    except L.SinLuna as e:
        comprobar(time.time() - t0 < 2.0, f"foto negra: avisa ({e}) en {time.time() - t0:.2f} s")
    except L.ErrorLuna as e:
        comprobar(False, f"foto negra: avisa con otro motivo ({e})")
    # Una zona lisa en medio de un mar grande: encaja en varios sitios, no se debe anotar
    x, y = _donde(-5.0, -40.0)
    _caso("interior liso (Oceanus Procellarum)", int(x - 250), int(y - 250), 500, debe_anotar=False)


# Ejecuta pintarLunaAnotada de verdad, con un SVG de mentira: la anchura de las letras es una
# aproximación a lo medido en el navegador (en em, con la fuente de cada familia de la hoja de estilos)
PINTAR_EN_NODE = r"""
const fs = require("fs"), vm = require("vm");
const entrada = JSON.parse(fs.readFileSync(0, "utf8"));
const TAM = c => c.includes("luna-mar") ? [13, 0.56] : c.includes("luna-crater") ? [12, 0.52] : [11, 0.49];
const hijos = [];
function elemento(tipo) {
    return { tipo, attrs: {}, textContent: "",
        setAttribute(a, v) { this.attrs[a] = v; },
        getBBox() { const [t, em] = TAM(this.attrs.class); return { x: 0, y: -t, width: this.textContent.length * t * em, height: t * 1.35 }; },
        remove() { hijos.splice(hijos.indexOf(this), 1); } };
}
globalThis.document = { createElementNS: (ns, tipo) => elemento(tipo) };
vm.runInThisContext(fs.readFileSync(entrada.js, "utf8") + "\n;globalThis.pintar = pintarLunaAnotada;");
const svg = { insertBefore(el) { hijos.push(el); return el; } };
const salida = {};
for (const ancho of entrada.anchos) {
    hijos.length = 0;
    const k = ancho / entrada.datos.ancho;
    const puestos = pintar(svg, entrada.datos, k, ancho, entrada.datos.alto * k);
    salida[ancho] = { puestos,
        textos: hijos.filter(e => e.tipo === "text").map(e => { const c = e.getBBox(); return { nombre: e.textContent, clase: e.attrs.class,
            x: +e.attrs.x, y: +e.attrs.y + c.y, w: c.width, h: c.height }; }),
        elipses: hijos.filter(e => e.tipo === "ellipse").map(e => ({ x: e.attrs.cx, y: e.attrs.cy, a: e.attrs.rx, b: e.attrs.ry })) };
}
console.log(JSON.stringify(salida));
"""


def probar_reparto():
    print("Reparto de nombres (geometría de la foto real del 21-oct-2026)")
    t = datetime(2026, 10, 21, 17, 39, 56, tzinfo=timezone.utc).timestamp()
    geo = L.geometria(t, 39.515, -0.377)
    disco, orient = {"cx": 431.8, "cy": 430.6, "r": 317.0}, {"giro": 146.06, "espejo": True}
    acc = L.accidentes(geo, disco, orient, 1052, 1052)
    orden = {a["nombre"]: i for i, a in enumerate(acc)}
    conocidos = {a["nombre"] for a in L._accidentes()}
    faltan = [n for n in L.DESTACADOS_PRIMERA + L.DESTACADOS_SEGUNDA if n not in conocidos]
    comprobar(not faltan, f"todos los destacados están en el nomenclátor {faltan or ''}")
    # Kepler (5,4 px en el JPG) pasa el mínimo; Aristarchus, con el terminador ya pasado, no es destacado
    primera = ("Tycho", "Copernicus", "Plato", "Clavius", "Kepler")
    comprobar(all(orden.get(n, 999) < 25 for n in primera), "los destacados de primera con luz, entre los 25 primeros: "
              + str([orden.get(n) for n in primera]))
    lagos = [i for a, i in ((a, orden[a["nombre"]]) for a in acc) if a["nombre"].split()[0] in ("Lacus", "Sinus", "Palus")]
    segunda = [orden[a["nombre"]] for a in acc if a.get("rango") == 2]
    comprobar(segunda and lagos and max(segunda) < min(lagos), "los destacados de segunda van antes que lagos, bahías y pantanos")
    arista = next((a for a in acc if a["nombre"] == "Aristarchus"), None)
    comprobar(arista is None or "rango" not in arista, "un destacado de noche (Aristarchus) no tiene prioridad")
    limbo = [n for n in ("Bailly", "Hedin", "Einstein") if n in orden]
    comprobar(not limbo, f"los cráteres de noche aplastados contra el limbo no salen {limbo or ''}")
    comprobar(orden.get("Gauss", 999) > orden["Tycho"] and orden.get("Humboldt", 999) > orden["Tycho"],
              "los del limbo con luz (Gauss, Humboldt), por detrás de Tycho")
    # Un disco pequeño: los destacados siguen, aunque midan menos que el mínimo
    pequeno = L.accidentes(geo, {"cx": 150.0, "cy": 150.0, "r": 120.0}, orient, 300, 300)
    comprobar(any(a["nombre"] == "Kepler" for a in pequeno), "con un disco de 120 px, Kepler sigue en la lista")
    comprobar(all(a.get("rango") == 1 for a in acc if a["familia"] == "estacion"), "los alunizajes llevan su sitio reservado")

    node = shutil.which("node")
    if not node:
        print("  OMITIDO: sin Node no se puede ejecutar el visor (static/js/luna_anotada.js)")
        return
    datos = {"ancho": 1052, "alto": 1052, "objetos": acc, "disco": disco, "terminador": [], "mares": ""}
    entrada = {"js": os.path.join(RAIZ, "static", "js", "luna_anotada.js"), "datos": datos, "anchos": [347, 375, 768, 900, 1400]}
    r = subprocess.run([node, "-e", PINTAR_EN_NODE], input=json.dumps(entrada), capture_output=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        comprobar(False, f"el visor no se ejecuta en Node: {r.stderr[:400]}")
        return
    salida = json.loads(r.stdout)
    for ancho, minimo in ((347, 12), (375, 12), (768, 40), (900, 55), (1400, 100)):
        v = salida[str(ancho)]
        nombres = [x["nombre"] for x in v["textos"]]
        comprobar(v["puestos"] == len(nombres) and v["puestos"] > minimo, f"a {ancho} px caben {v['puestos']} nombres (más de {minimo})")
        ausentes = [n for n in primera if n not in nombres]
        comprobar(not ausentes, f"a {ancho} px salen Tycho, Copernicus, Plato, Clavius y Kepler {ausentes or ''}")
        cajas = v["textos"]
        solapes = [(p["nombre"], q["nombre"]) for i, p in enumerate(cajas) for q in cajas[i + 1:]
                   if p["x"] < q["x"] + q["w"] and q["x"] < p["x"] + p["w"] and p["y"] < q["y"] + q["h"] and q["y"] < p["y"] + p["h"]]
        comprobar(not solapes, f"a {ancho} px ningún nombre pisa otro {solapes[:3] or ''}")
        dentro = all(c["x"] >= 0 and c["y"] >= 0 and c["x"] + c["w"] <= ancho and c["y"] + c["h"] <= ancho for c in cajas)
        comprobar(dentro, f"a {ancho} px todos los nombres caen dentro de la foto")
    v = salida["900"]
    nombres = [x["nombre"] for x in v["textos"]]
    clases = " ".join(x["clase"] for x in v["textos"])
    comprobar("luna-estacion" in clases and "luna-monte" in clases and "Montes Apenninus" in nombres,
              "a 900 px salen alunizajes y montes (sus nombres no chocan con su propio punto)")
    pedidos = [n for n in ("Ptolemaeus", "Theophilus", "Cyrillus", "Catharina", "Langrenus", "Petavius", "Gassendi", "Eudoxus", "Posidonius") if n in nombres]
    comprobar(len(pedidos) >= 7, f"a 900 px salen destacados de segunda: {pedidos}")
    limbo = [n for n in ("Bailly", "Hedin", "Einstein", "Gauss", "Humboldt") if n in nombres]
    comprobar(not limbo, f"a 900 px no salen cráteres aplastados del limbo {limbo or ''}")


if __name__ == "__main__":
    probar_geometria()
    probar_sinteticas()
    probar_accidentes()
    probar_reparto()
    probar_trozos()
    if fallos:
        print(f"\n{len(fallos)} comprobaciones fallidas")
        sys.exit(1)
    print("\nLuna anotada: todo bien")
