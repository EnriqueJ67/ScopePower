# herramientas/probar_manchas_solares.py
#
# Prueba del Número de Wolf (manchas_solares.py) sin fotos reales ni Internet:
#   1. Las efemérides físicas del Sol (P, B0, L0) contra el ejemplo 29.a de Meeus
#      (1992-10-13 0h TD: P = +26,27°, B0 = +5,99°, L0 = 238,63°) y contra el modelo IAU.
#   2. Las coordenadas heliográficas de puntos conocidos del disco (centro, polo, limbo oeste).
#   3. Un Sol sintético con oscurecimiento del limbo, ruido y cuatro grupos conocidos (un par
#      bipolar, una mancha suelta, una penumbra con tres umbras y un poro aplastado cerca del
#      limbo): salen g = 4, f = 7, R = 47, y lo mismo con la imagen girada.
#   4. Una galería de mentira (crudo en color MODO video, JPG con giro de Cámara, JSON «Sol»):
#      objetos_foto da las manchas en píxeles del JPG (girados), guardar deja el conteo en el
#      JSON y en la serie (la segunda vez sustituye la fila), olvidar lo quita, y la orientación
#      de la cámara, si se conoce, gira con la foto.
#   5. es_sol por el objeto del JSON y por el nombre.
#
# Uso: python herramientas/probar_manchas_solares.py   (desde la carpeta de ScopePower)

import os
import sys
import json
import math
import shutil
import tempfile

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import datos_iers                      # noqa: E402
datos_iers.configurar()
import cv2                             # noqa: E402
from astropy.io import fits            # noqa: E402
from astropy.time import Time          # noqa: E402
import editor_fotos                    # noqa: E402
import editor_dia                      # noqa: E402
import procesado                       # noqa: E402
import orientacion_camara              # noqa: E402
import manchas_solares as M            # noqa: E402

fallos = []


def comprobar(condicion, texto):
    print(("  OK   " if condicion else "  FALLO ") + texto)
    if not condicion:
        fallos.append(texto)


# ─── 1. Efemérides ───────────────────────────────────────────────────────────
def probar_geometria():
    print("Efemérides físicas del Sol (Meeus 29.a)")
    g = M.geometria_sol(Time("1992-10-13T00:00:00", scale="tt").unix)
    comprobar(abs(g["P"] - 26.27) < 0.05 and abs(g["B0"] - 5.99) < 0.05 and abs(g["L0"] - 238.63) < 0.15,
              f"P={g['P']:.2f}° B0={g['B0']:.2f}° L0={g['L0']:.2f}° (esperado 26,27 / 5,99 / 238,63)")
    g = M.geometria_sol(Time("2026-10-23T12:00:00", scale="utc").unix)
    comprobar(-90 < g["P"] < 90 and abs(g["B0"]) < 7.3 and 0 <= g["L0"] < 360, f"23-oct-2026: P={g['P']:.1f}° B0={g['B0']:.1f}° L0={g['L0']:.1f}°")


# ─── 2. Coordenadas heliográficas ────────────────────────────────────────────
def probar_coordenadas():
    print("Coordenadas heliográficas de puntos conocidos")
    disco = {"cx": 500.0, "cy": 500.0, "r": 400.0}
    cielo = {"norte_sol": (0.0, -1.0), "este_sol": (-1.0, 0.0), "geometria": {"P": 0.0, "B0": 5.0, "L0": 100.0}}
    lat, dlon, lon = M._coordenadas(500, 500, disco, cielo)
    comprobar(abs(lat - 5.0) < 0.01 and abs(dlon) < 0.01 and abs(lon - 100.0) < 0.01, f"centro del disco: lat {lat:.2f}° dlon {dlon:.2f}° (B0, 0)")
    lat, dlon, lon = M._coordenadas(500, 500 - 400 * math.sin(math.radians(30)), disco, cielo)
    comprobar(abs(lat - 35.0) < 0.01 and abs(dlon) < 0.01, f"30° hacia el norte solar: lat {lat:.2f}° dlon {dlon:.2f}° (35, 0)")
    # 30° hacia el oeste sobre el plano del disco: con B0 = 5° la esfera está inclinada y la longitud
    # sale de sin(L − L0) = sin θ · sin ρ / cos B (Smart), un poco más de 30°
    lat, dlon, lon = M._coordenadas(500 + 400 * math.sin(math.radians(30)), 500, disco, cielo)
    lat_esp = math.degrees(math.asin(math.cos(math.radians(30)) * math.sin(math.radians(5))))
    dlon_esp = math.degrees(math.asin(math.sin(math.radians(30)) / math.cos(math.radians(lat_esp))))
    comprobar(abs(dlon - dlon_esp) < 0.01 and abs(lat - lat_esp) < 0.01, f"30° hacia el oeste (derecha): dlon {dlon:.2f}° lat {lat:.2f}° ({dlon_esp:.2f}, {lat_esp:.2f})")
    # La separación sobre la esfera no depende del norte: dos puntos a 7° sobre el ecuador central
    a = {"x": 500 + 400 * math.sin(math.radians(-3.5)), "y": 500}
    b = {"x": 500 + 400 * math.sin(math.radians(3.5)), "y": 500}
    comprobar(abs(M.separacion_deg(a, b, disco) - 7.0) < 0.01, f"separación de dos manchas a ±3,5°: {M.separacion_deg(a, b, disco):.2f}°")


# ─── 3. El Sol sintético ─────────────────────────────────────────────────────
def sol_sintetico(lado=900, r=400.0, semilla=0):
    """
    Sol lineal (float32) con oscurecimiento del limbo y cuatro grupos: A (par bipolar, dos manchas
    con umbra), B (una mancha con umbra), C (una penumbra con tres umbras) y D (un poro sin umbra
    cerca del limbo). Devuelve (imagen, lista de manchas esperadas en px (x, y, grupo, umbra)).
    """
    cx = cy = lado / 2.0
    yy, xx = np.mgrid[0:lado, 0:lado].astype(np.float64)
    d = np.hypot(xx - cx, yy - cy) / r
    mu = np.sqrt(np.clip(1.0 - d * d, 0.0, 1.0))
    cobertura = np.clip(r - d * r + 0.5, 0.0, 1.0)
    foto = (0.4 + 0.6 * mu) * cobertura

    def pos(X, Y):
        return cx + X * r, cy + Y * r

    factor = np.ones_like(foto)
    esperadas = []

    def mancha(X, Y, grupo, r_pen, r_umb):
        x, y = pos(X, Y)
        dd = np.hypot(xx - x, yy - y)
        if r_pen:
            factor[dd < r_pen] = np.minimum(factor[dd < r_pen], 0.65)
        if r_umb:
            factor[dd < r_umb] = np.minimum(factor[dd < r_umb], 0.25)
        esperadas.append((x, y, grupo, bool(r_umb)))

    # A: par bipolar separado 7° sobre la esfera (a X ≈ −0,3 el seno de 3,5° vale 0,061)
    mancha(-0.36, 0.20, "A", 12, 5)
    mancha(-0.24, 0.20, "A", 11, 4)
    # B: suelta
    mancha(0.40, -0.30, "B", 13, 5)
    # C: una penumbra grande con tres umbras
    x, y = pos(0.10, 0.55)
    factor[np.hypot(xx - x, yy - y) < 22] = 0.65
    for ddx, ddy in ((-9, 0), (8, -5), (6, 7)):
        factor[np.hypot(xx - (x + ddx), yy - (y + ddy)) < 3.5] = 0.25
        esperadas.append((x + ddx, y + ddy, "C", True))
    # D: poro aplastado cerca del limbo, sin umbra
    x, y = pos(0.85, 0.10)
    factor[(np.hypot((xx - x) / 0.52, yy - y) < 4.5)] = 0.70
    esperadas.append((x, y, "D", False))
    foto = foto * factor
    rng = np.random.default_rng(semilla)
    foto = cv2.GaussianBlur(foto.astype(np.float32), (0, 0), 1.0) + rng.normal(0.0, 0.01, foto.shape).astype(np.float32)
    return np.clip(foto * 0.9, 0.0, None).astype(np.float32) + 0.002, esperadas


def _resumen(manchas):
    grupos = {}
    for m in manchas:
        grupos.setdefault(m["grupo"], []).append(m)
    return ", ".join(f"g{gid}: {len(ms)}" for gid, ms in sorted(grupos.items()))


def probar_sintetico():
    print("Sol sintético con cuatro grupos (g = 4, f = 7, R = 47)")
    lum, esperadas = sol_sintetico()
    disco = M.localizar_disco(lum)
    comprobar(abs(disco["cx"] - 450) < 2 and abs(disco["cy"] - 450) < 2 and abs(disco["r"] - 400) < 3 and not disco["cortado"],
              f"disco en ({disco['cx']:.1f}, {disco['cy']:.1f}) r {disco['r']:.1f} (450, 450, 400)")
    for sensibilidad in ("normal", "alta", "baja"):
        manchas, area = M.detectar(lum, disco, sensibilidad)
        M.agrupar(manchas, disco)
        g, f = len(M.grupos_de(manchas)), len(manchas)
        bien = g == 4 and f == 7
        comprobar(bien, f"sensibilidad {sensibilidad}: g={g} f={f} R={M.numero_wolf(g, f)} ({_resumen(manchas)}; penumbra {area} px)")
        if sensibilidad == "normal":
            # Cada mancha esperada tiene una detectada a menos de 3 px, con su umbra, y las del mismo grupo van juntas
            asignado = {}
            todas = True
            for x, y, grupo, umbra in esperadas:
                cerca = [m for m in manchas if math.hypot(m["x"] - x, m["y"] - y) < 3.0 and m["umbra"] == umbra]
                if not cerca:
                    todas = False
                    continue
                asignado.setdefault(grupo, set()).add(cerca[0]["grupo"])
            comprobar(todas and all(len(v) == 1 for v in asignado.values()) and len({next(iter(v)) for v in asignado.values()}) == 4,
                      "cada mancha esperada está donde debe y los grupos son los esperados")
            poro = [m for m in manchas if not m["umbra"]]
            comprobar(len(poro) == 1 and poro[0]["area_mhs"] > 0 and all(m["area_mhs"] > 0 for m in manchas),
                      f"el poro cuenta como una mancha sin umbra ({poro[0]['area_mhs'] if poro else '-'} millonésimas)")
    # Girada un cuarto de vuelta: el mismo conteo
    lum90 = np.ascontiguousarray(np.rot90(lum))
    d90 = M.localizar_disco(lum90)
    m90, __ = M.detectar(lum90, d90, "normal")
    M.agrupar(m90, d90)
    comprobar(len(M.grupos_de(m90)) == 4 and len(m90) == 7, f"girada 90°: g={len(M.grupos_de(m90))} f={len(m90)}")
    # Umbrales por sensibilidad y texto del factor k
    comprobar(M.sensibilidad_valida("ALTA") == "alta" and M.sensibilidad_valida("x") == "normal", "sensibilidad normalizada")
    comprobar(M.numero_wolf(4, 7, 0.6) == 28.2, f"R con k = 0,6: {M.numero_wolf(4, 7, 0.6)}")
    # Una foto sin disco avisa en vez de contar
    try:
        M.localizar_disco(np.full((400, 400), 0.01, np.float32))
        comprobar(False, "foto sin Sol: debería avisar")
    except M.ErrorSol as e:
        comprobar(True, f"foto sin Sol: avisa ({e})")
    return lum


# ─── 4. La galería de mentira ────────────────────────────────────────────────
def probar_galeria(lum):
    print("Galería de mentira: crudo en color, JPG girado, guardar, serie, olvidar")
    raiz = tempfile.mkdtemp(prefix="scopepower_wolf_")
    fotos = os.path.join(raiz, "fotos")
    crudos = os.path.join(fotos, "crudos")
    os.makedirs(crudos)
    editor_fotos.CARPETA_FOTOS = fotos
    editor_fotos.CARPETA_ORIGINALES = os.path.join(fotos, "originales")
    editor_dia.CARPETA_CRUDOS = crudos
    editor_dia.carpeta_videos = lambda ruta_config="config.ini": os.path.join(raiz, "videos")
    M.RUTA_SERIE = os.path.join(raiz, "datos", "numero_wolf.csv")
    editor_dia.olvidar_cache()
    nombre = "Sol_20261023_120000_video.jpg"
    # El crudo: cubo (3, h, w) uint16 lineal como lo deja el apilado de vídeo, en los ejes de ASCOM
    cubo = np.stack([np.clip(lum * 65535.0 * k, 0, 65535).astype(np.uint16) for k in (1.0, 0.85, 0.6)])
    hdu = fits.PrimaryHDU(cubo)
    for k, v in (("EXPTIME", 0.0005), ("MODO", "video"), ("OBJETIVO", "sol"), ("VIDEO", "Sol_20261023_120000"), ("XBINNING", 1)):
        hdu.header[k] = v
    hdu.writeto(os.path.join(crudos, "Sol_20261023_120000_video.fits"), overwrite=True)
    # El JPG de la galería: revelado con gamma y con el giro de Cámara (90° en horario) que se anota en el JSON
    giro = {"giro": 90.0, "espejo": False}
    gamma = np.clip(lum / lum.max(), 0, 1) ** (1 / 2.2) * 255
    bgr = cv2.cvtColor(gamma.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    h, w = lum.shape
    geo = procesado.geometria_giro(w, h, 1.0, giro["giro"], giro["espejo"])
    jpg = procesado.girar_campo(bgr, 1.0, giro["giro"], zona=(0, 0, w, h), espejo=False)[0]
    cv2.imwrite(os.path.join(fotos, nombre), jpg, [cv2.IMWRITE_JPEG_QUALITY, 92])
    meta = {"objeto": "Sol", "fecha": "23/10/2026 12:00", "latitud": 40.0, "longitud": -3.7, "giro": giro, "firmada": False}
    with open(os.path.join(fotos, nombre.replace(".jpg", ".json")), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    # Sin orientación de la cámara guardada: sin coordenadas, pero con conteo
    orientacion_camara.ultima = lambda buscar=True: None
    d = M.objetos_foto(nombre, (40.0, -3.7))
    comprobar(d["ok"] and d["sol"] and d["origen"] == "crudo" and (d["ancho"], d["alto"]) == (jpg.shape[1], jpg.shape[0]),
              f"objetos_foto: {d['origen']}, JPG {d['ancho']}×{d['alto']}")
    comprobar(d["wolf"] == {"g": 4, "f": 7, "R": 47.0, "k": 1.0} and not d["guardado"], f"conteo {d['wolf']}, sin guardar")
    comprobar(d["geometria"]["norte_deg"] is None and all(m["lat"] is None for m in d["manchas"]), "sin orientación: sin coordenadas heliográficas")
    # Las manchas vienen en píxeles del JPG girado: el centro del disco y la mancha B en su sitio
    T = np.asarray(geo["T"])
    cxj, cyj, __ = T @ [449.5, 449.5, 1.0]
    comprobar(math.hypot(d["disco"]["cx"] - cxj, d["disco"]["cy"] - cyj) < 2.5 and abs(d["disco"]["r"] - 400) < 3,
              f"disco en el JPG: ({d['disco']['cx']}, {d['disco']['cy']}) r {d['disco']['r']} (esperado {cxj:.0f}, {cyj:.0f}, 400)")
    xb, yb, __ = T @ [450 + 0.40 * 400, 450 - 0.30 * 400, 1.0]
    comprobar(any(math.hypot(m["x"] - xb, m["y"] - yb) < 3 for m in d["manchas"]), f"la mancha B está en ({xb:.0f}, {yb:.0f}) del JPG")
    # Con orientación de la cámara: hay coordenadas, y el norte gira con la foto
    orientacion_camara.ultima = lambda buscar=True: {"H": [[0.001, 0.0], [0.0, -0.001]], "binning_foto": 1}
    instante = M._fecha_local_de(nombre, meta)
    geo0, c0 = M.contexto_cielo(nombre, meta, instante, np.eye(3), (40.0, -3.7))
    geo90, c90 = M.contexto_cielo(nombre, meta, instante, T, (40.0, -3.7))
    comprobar(c0 is not None and c90 is not None and abs((c90["norte_deg"] - c0["norte_deg"] - 90.0 + 180) % 360 - 180) < 0.01,
              f"el norte celeste gira con la foto: {c0['norte_deg'] if c0 else '-'}° → {c90['norte_deg'] if c90 else '-'}° (+90°)")
    M._olvidar_cache(nombre)
    d = M.objetos_foto(nombre, (40.0, -3.7))
    comprobar(d["geometria"]["norte_deg"] is not None and all(m["lat"] is not None and -90 <= m["lat"] <= 90 for m in d["manchas"])
              and all(g["lat"] is not None for g in d["grupos"]) and d["wolf"]["g"] == 4 and d["wolf"]["f"] == 7,
              f"con orientación: coordenadas en las {len(d['manchas'])} manchas y los {len(d['grupos'])} grupos, mismo conteo")
    # Guardar un conteo corregido a mano: una umbra menos del grupo de tres y dos grupos unidos
    mayor = max(d["grupos"], key=lambda g: g["n"])["id"]
    manchas = [dict(m) for m in d["manchas"]]
    manchas.remove(next(m for m in manchas if m["grupo"] == mayor))
    for m in manchas:
        if m["grupo"] == 2:
            m["grupo"] = 1
    r = M.guardar(nombre, {"manchas": manchas, "disco": d["disco"], "sensibilidad": "normal"}, (40.0, -3.7))
    comprobar(r["guardado"] and r["wolf"]["f"] == 6 and r["wolf"]["g"] == 3 and r["wolf"]["R"] == 36.0, f"guardado: {r['wolf']}")
    with open(os.path.join(fotos, nombre.replace(".jpg", ".json")), encoding="utf-8") as f:
        w = json.load(f)["wolf"]
    comprobar(w["g"] == 3 and w["f"] == 6 and w["a_mano"] and len(w["manchas"]) == 6 and w["fecha"].startswith("2026-10-23T12:00"),
              f"el JSON de la foto lleva el conteo (fecha {w['fecha']})")
    s = M.serie()
    comprobar(len(s) == 1 and s[0]["foto"] == nombre and s[0]["R"] == 36.0, f"serie: {s}")
    # Volver a guardar sustituye la fila; otra foto añade otra
    r = M.guardar(nombre, {"manchas": manchas[:-1], "disco": d["disco"]}, (40.0, -3.7))
    s = M.serie()
    comprobar(len(s) == 1 and s[0]["f"] == 5, f"segunda vez: una sola fila con f = {s[0]['f'] if s else '-'}")
    d2 = M.objetos_foto(nombre, (40.0, -3.7))
    comprobar(d2["guardado"] and d2["wolf"]["f"] == 5 and d2["origen"] == "guardado", "al volver a pedir la foto sale lo guardado")
    # Olvidar: vuelve a detectar y la serie queda vacía
    r = M.olvidar(nombre, (40.0, -3.7), "alta")
    comprobar(not r["guardado"] and r["wolf"]["f"] == 7 and r["sensibilidad"] == "alta" and M.serie() == [], "olvidar: vuelve a detectar y quita la fila")
    # Sin crudo, se cuenta sobre el JPG (aproximado a lineal)
    os.remove(os.path.join(crudos, "Sol_20261023_120000_video.fits"))
    editor_dia.olvidar_cache()
    M._olvidar_cache(nombre)
    d = M.objetos_foto(nombre, (40.0, -3.7), forzar=True)
    comprobar(d["origen"] == "jpg" and d["wolf"]["g"] == 4 and d["wolf"]["f"] == 7, f"sin crudo, sobre el JPG: {d['wolf']}")
    shutil.rmtree(raiz, ignore_errors=True)


# ─── 5. es_sol ───────────────────────────────────────────────────────────────
def probar_es_sol():
    print("Qué fotos son del Sol")
    comprobar(M.es_sol("Sol_20261017_120000.jpg") and M.es_sol("Sun_20261017_120000_video.jpg") and not M.es_sol("Luna_20261017_120000.jpg"), "por el nombre")
    comprobar(M.es_sol("Otra.jpg", {"objeto": "Sol"}) and M.es_sol("Otra.jpg", {"objeto": "Sun (closeup)"}) and not M.es_sol("Sol_x.jpg", {"objeto": "Luna"}), "por el objeto del JSON")


if __name__ == "__main__":
    probar_geometria()
    probar_coordenadas()
    lum = probar_sintetico()
    probar_galeria(lum)
    probar_es_sol()
    if fallos:
        print(f"\n{len(fallos)} comprobaciones fallidas")
        sys.exit(1)
    print("\nNúmero de Wolf: todo bien")
