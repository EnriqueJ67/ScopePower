# herramientas/probar_luna_3d.py
#
# Prueba sin equipo del montaje «Luna 3D» (luna_3d.py) sobre una galería de mentira en una
# carpeta temporal, con Lunas sintéticas (el mapa de albedo con la iluminación de cada fecha):
#
#   1. Qué fotos sirven: las de la Luna con su crudo; la más llena rellena a la menos llena y
#      no al revés; los apilados de noche dan las estrellas.
#   2. El relleno de la zona en sombra con otra foto (otra fecha, otro tamaño, otro giro, en
#      espejo y con otra libración) cae en su sitio: se parece al mapa punto a punto. Con el
#      centro del disco de la otra foto mal a propósito, el desplazamiento se mide y se quita.
#   3. La mezcla: en la zona de día manda la fase; en la de noche aparece el albedo; el halo
#      rodea el disco; las estrellas quedan fuera del disco.
#   4. Con la galería: la vista previa es un JPEG 4:5 de 800 px; guardar deja una foto nueva
#      «Luna (montaje)» que ya no admite otro montaje y que se anota con el norte arriba.
#
# Uso: python herramientas/probar_luna_3d.py   (desde la carpeta de ScopePower)

import os
import sys
import json
import math
import shutil
import tempfile
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datos_iers
datos_iers.configurar()
import cv2
from astropy.io import fits
import editor_fotos
import editor_dia
import luna_anotada as L
import luna_3d

RAIZ = tempfile.mkdtemp(prefix="scopepower_luna_3d_")
FOTOS = os.path.join(RAIZ, "fotos")
CRUDOS = os.path.join(FOTOS, "crudos")
for d in (FOTOS, CRUDOS):
    os.makedirs(d, exist_ok=True)
# La galería de mentira: los módulos miran estas carpetas
editor_fotos.CARPETA_FOTOS = FOTOS
editor_fotos.CARPETA_ORIGINALES = os.path.join(FOTOS, "originales")
editor_dia.CARPETA_CRUDOS = CRUDOS

LAT, LON = 39.95, -1.11
fallos = []


def comprobar(condicion, texto):
    print(("  OK   " if condicion else "  FALLO ") + texto)
    if not condicion:
        fallos.append(texto)


def luna_lineal(geo, lado, cx, cy, r, giro, espejo, semilla=0):
    """Luna sintética lineal (float32 0..1, lado×lado): el mapa de albedo con la luz de ese instante."""
    yy, xx = np.mgrid[0:lado, 0:lado].astype(np.float64)
    Xr, Yr = (xx - cx) / r, -(yy - cy) / r
    g = math.radians(giro)
    m = -1.0 if espejo else 1.0
    X = math.cos(g) * Xr + math.sin(g) * Yr
    Y = m * (-math.sin(g) * Xr + math.cos(g) * Yr)
    dentro = X * X + Y * Y < 1
    img = np.zeros((lado, lado), np.float32)
    f = L._desde_vista(geo, X[dentro], Y[dentro])
    lat = np.degrees(np.arcsin(np.clip(f[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(f[:, 1], f[:, 0]))
    fi = f @ geo["R"]
    ci = np.clip(fi @ geo["s"], 0, None)
    ce = np.clip(-(fi @ geo["u"]), 1e-3, None)
    img[dentro] = 0.45 * L._albedo_en(lat, lon) * ci / (ci + ce)
    img = cv2.GaussianBlur(img, (0, 0), 1.0) + np.random.default_rng(semilla).normal(0.004, 0.002, img.shape)
    return np.clip(img, 0.0, 1.0).astype(np.float32)


def guardar_luna(nombre, instante, lineal):
    """Una foto de la Luna como las deja el apilado de un vídeo: cubo lineal en crudos/, JPG y JSON."""
    cubo = np.stack([np.clip(lineal * 60000.0, 0, 65535).astype(np.uint16)] * 3)
    hdu = fits.PrimaryHDU(cubo)
    for k, v in (("EXPTIME", 0.008), ("MODO", "video"), ("OBJETIVO", "luna"), ("NFRAMES", 100), ("NUSADOS", 25), ("XBINNING", 1)):
        hdu.header[k] = v
    hdu.writeto(os.path.join(CRUDOS, nombre + ".fits"), overwrite=True)
    gris = np.clip(np.power(lineal / max(float(lineal.max()), 1e-6), 1 / 2.2) * 255, 0, 255).astype(np.uint8)
    cv2.imwrite(os.path.join(FOTOS, nombre + ".jpg"), cv2.cvtColor(gris, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    fecha = datetime.fromtimestamp(instante, timezone.utc)
    with open(os.path.join(FOTOS, nombre + ".json"), "w", encoding="utf-8") as f:
        json.dump({"objeto": "Luna", "tipo": "Luna", "fecha": fecha.strftime("%d/%m/%Y %H:%M"), "instante": instante,
                   "latitud": LAT, "longitud": LON, "autor": "Prueba", "n_fotos": 25, "exposicion": 0.008, "firmada": False,
                   "ajustes_dia": {"exposicion": 0.0, "contraste": 1.0, "detalle": 0.0, "nitidez": 0.0, "color": 1.0}}, f)
    return nombre + ".jpg"


def guardar_campo(nombre, objeto, tipo):
    """Un apilado de noche de mentira: estrellas sobre un fondo con gradiente y una nebulosa, con su FITS al lado."""
    rng = np.random.default_rng(7)
    h, w = 900, 1200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = 0.08 + 0.05 * xx / w + 0.25 * np.exp(-((xx - 700) ** 2 + (yy - 500) ** 2) / (2 * 180.0 ** 2))
    estrellas = np.zeros((h, w), np.float32)
    for __ in range(400):
        estrellas[int(rng.integers(5, h - 5)), int(rng.integers(5, w - 5))] = float(rng.uniform(4, 30))
    img = np.clip(img + cv2.GaussianBlur(estrellas, (0, 0), 1.3), 0, 1)
    cv2.imwrite(os.path.join(FOTOS, nombre + ".jpg"), cv2.cvtColor((img * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    fits.PrimaryHDU(np.zeros((4, 4), np.uint16)).writeto(os.path.join(FOTOS, nombre + ".fits"), overwrite=True)
    with open(os.path.join(FOTOS, nombre + ".json"), "w", encoding="utf-8") as f:
        json.dump({"objeto": objeto, "tipo": tipo, "firmada": False}, f)
    return nombre + ".jpg"


def correlacion(a, b, zona):
    a, b = a[zona] - a[zona].mean(), b[zona] - b[zona].mean()
    return float((a * b).sum() / math.sqrt(float((a * a).sum() * (b * b).sum()) or 1.0))


try:
    # Fase de un 60 % y Luna casi llena cuatro días después, con otro tamaño, otro giro y en espejo
    t_fase = datetime(2026, 10, 20, 19, 30, tzinfo=timezone.utc).timestamp()
    t_llena = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc).timestamp()
    g_fase, g_llena = L.geometria(t_fase, LAT, LON), L.geometria(t_llena, LAT, LON)
    lin_fase = luna_lineal(g_fase, 900, 430.0, 470.0, 300.0, 143.0, False, semilla=1)
    lin_llena = luna_lineal(g_llena, 800, 410.0, 385.0, 255.0, 37.0, True, semilla=2)
    fase = guardar_luna("Luna_20261020_193000_video", t_fase, lin_fase)
    llena = guardar_luna("Luna_20261025_210000_video", t_llena, lin_llena)
    campo = guardar_campo("M29_20261107_191655", "M 29", "Cúmulo abierto")
    nebulosa = guardar_campo("Nebulosa_del_Alma_20261113_210840", "Nebulosa del Alma", "Nebulosa")
    cv2.imwrite(os.path.join(FOTOS, "Luna_20261021_192740.jpg"), np.full((200, 200, 3), 60, np.uint8))    # de la Luna, pero sin crudo

    print("Qué fotos sirven")
    comprobar(luna_3d.admite(fase) and luna_3d.admite(llena), "las dos fotos de la Luna con crudo admiten el montaje")
    comprobar(not luna_3d.admite(campo) and not luna_3d.admite("Luna_20261021_192740.jpg") and not luna_3d.admite("../x.jpg"),
              "un apilado de noche, una Luna sin crudo y un nombre raro, no")
    info = luna_3d.info(fase)
    comprobar([r["nombre"] for r in info["rellenos"]] == [llena] and luna_3d.rellenos(llena) == [],
              f"la llena ({100 * g_llena['iluminada']:.0f} %) rellena a la fase ({100 * g_fase['iluminada']:.0f} %) y no al revés")
    comprobar([c["nombre"] for c in info["campos"]] == [campo, nebulosa], "campos de estrellas: el cúmulo abierto, el primero")
    comprobar(luna_3d.nombre_montaje(fase) == "Luna_20261020_193000_montaje.jpg" and info["montaje"] == luna_3d.nombre_montaje(fase),
              "el montaje se llama Luna_20261020_193000_montaje.jpg")
    o = luna_3d.normalizar({"sombra": "9", "halo": -3, "estrellas": "nada", "relleno": "", "firma": 0})
    comprobar(o["sombra"] == 2.5 and o["halo"] == 0.0 and o["estrellas"] == 1.0 and o["relleno"] == "auto" and o["firma"] is False,
              "las opciones se acotan a sus límites")

    print("El relleno de la sombra con la otra foto")
    __, meta_f = luna_3d._meta(fase)
    __, meta_l = luna_3d._meta(llena)
    rgb_f, rgb_l = luna_3d._revelada(fase, meta_f), luna_3d._revelada(llena, meta_l)
    sit = luna_3d.situar(rgb_f, t_fase, LAT, LON)
    otra = dict(luna_3d.situar(rgb_l, t_llena, LAT, LON), rgb=rgb_l)
    comprobar(sit["metodo"] == "disco" and abs(sit["disco"]["r"] - 300.0) < 4 and otra["orient"]["espejo"] is True,
              f"las dos se sitúan solas (fase: radio {sit['disco']['r']:.1f} px, giro {sit['orient']['giro']:.1f}°; llena en espejo)")
    solo_mapa = luna_3d.preparar(rgb_f, sit)
    con_foto = luna_3d.preparar(rgb_f, sit, otra)
    r, ci = con_foto["r"], con_foto["cos_i"]
    interior = (r < 0.8)
    # Se compara el detalle fino (sin la pendiente suave de la iluminación que queda al aplanar la otra foto)
    det = lambda img: cv2.GaussianBlur(img, (0, 0), 1.5) - cv2.GaussianBlur(img, (0, 0), 8.0)
    c0 = correlacion(det(con_foto["albedo"]), det(solo_mapa["albedo"]), interior)
    comprobar(con_foto["cubierto"] > 0.85 and c0 > 0.85,
              f"la otra foto cubre el {100 * con_foto['cubierto']:.0f} % del disco y coincide con el mapa (correlación {c0:.3f})")
    comprobar(abs(con_foto["radio"] - 300.0) < 4 and abs(con_foto["ancho"] - con_foto["radio"] / luna_3d.FRACCION_DISCO) <= 2
              and abs(con_foto["alto"] - con_foto["ancho"] * 1.25) <= 2,
              f"lienzo de {con_foto['ancho']}×{con_foto['alto']} (4:5) con el disco a su tamaño ({con_foto['radio']:.0f} px de radio)")
    # El centro del disco de la otra foto, 4 px mal a propósito (1,6 % del radio): se mide y se quita
    torcida = dict(otra, disco=dict(otra["disco"], cx=otra["disco"]["cx"] + 3.2, cy=otra["disco"]["cy"] - 2.4))
    corregida = luna_3d.preparar(rgb_f, sit, torcida)
    c1 = correlacion(det(corregida["albedo"]), det(solo_mapa["albedo"]), interior)
    maximo, luna_3d.DESFASE_MAX = luna_3d.DESFASE_MAX, 0.0
    sin_corregir = luna_3d.preparar(rgb_f, sit, torcida)
    luna_3d.DESFASE_MAX = maximo
    c2 = correlacion(det(sin_corregir["albedo"]), det(solo_mapa["albedo"]), interior)
    escala = sit["disco"]["r"] / otra["disco"]["r"]
    comprobar(corregida["desfase_px"] is not None and abs(corregida["desfase_px"] - 4.0 * escala) < 2.0 and c1 > c0 - 0.05 and c1 > c2 + 0.1,
              f"centro 4 px mal en la otra foto: mide {corregida['desfase_px']} px en el lienzo (unos {4.0 * escala:.1f} más los "
              f"{con_foto['desfase_px']} que ya había) y la "
              f"correlación vuelve a {c1:.3f} (sin corregir, {c2:.3f})")

    print("La mezcla")
    estrellas = luna_3d.estrellas_de(os.path.join(FOTOS, campo), con_foto["ancho"], con_foto["alto"], firmada=False)
    gris = lambda img: cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    sin_nada = gris(luna_3d.mezclar(con_foto, None, {"sombra": 0, "halo": 0, "estrellas": 0}))
    con_sombra = gris(luna_3d.mezclar(con_foto, None, {"sombra": 1, "halo": 0, "estrellas": 0}))
    con_halo = gris(luna_3d.mezclar(con_foto, None, {"sombra": 1, "halo": 1, "estrellas": 0}))
    completo = gris(luna_3d.mezclar(con_foto, estrellas, {"sombra": 1, "halo": 1, "estrellas": 1}))
    dia, noche = (ci > 0.6) & interior, (ci < -0.15) & interior
    foto = gris(con_foto["foto"])
    comprobar(float(np.abs(sin_nada - foto).max()) < 1e-3, "con todo a cero queda la fase tal cual, con el norte arriba")
    comprobar(float(np.abs(con_sombra - foto)[dia].mean()) < 0.01, "en la zona de día manda la fase")
    c_noche = correlacion(con_sombra, con_foto["albedo"], noche)
    comprobar(float(foto[noche].mean()) < 0.04 and float(foto[noche].mean()) + 0.04 < float(con_sombra[noche].mean()) < 0.3 and c_noche > 0.8,
              f"la zona en sombra pasa de {foto[noche].mean():.3f} a {con_sombra[noche].mean():.3f} y dibuja el albedo (correlación {c_noche:.2f})")
    doble = gris(luna_3d.mezclar(con_foto, None, {"sombra": 2, "halo": 0, "estrellas": 0}))
    comprobar(float(doble[noche].mean()) > 1.6 * float(con_sombra[noche].mean()), "«Luz en la sombra» la aclara")
    anillo, lejos = (r > 1.04) & (r < 1.15), r > 2.0
    comprobar(float(con_sombra[anillo].mean()) < 0.01 and float(con_halo[anillo].mean()) > 0.05
              and float(con_halo[lejos].mean()) < 0.5 * float(con_halo[anillo].mean()),
              f"el halo rodea el disco ({con_halo[anillo].mean():.3f} junto al limbo, {con_halo[lejos].mean():.3f} lejos)")
    disco = r < 0.99
    n_estrellas = int(((completo - con_halo) > 0.15)[r > 1.3].sum())
    comprobar(float(np.abs(completo - con_halo)[disco].max()) < 1e-3 and n_estrellas > 200,
              f"estrellas solo fuera del disco ({n_estrellas} píxeles brillantes en el cielo, ninguno sobre la Luna)")
    fondo = estrellas.max(axis=2)
    comprobar(float(np.median(fondo)) < 0.01 and float(fondo[540:660, 560:700].mean()) < 0.03,
              "de la foto de noche solo quedan las estrellas (ni el fondo ni la nebulosa)")

    print("Con la galería")
    jpg, datos = luna_3d.vista_previa(fase, {"relleno": "auto", "campo": "auto"})
    previa = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    comprobar(previa is not None and max(previa.shape[:2]) == 800 and abs(previa.shape[0] / previa.shape[1] - 1.25) < 0.01,
              f"vista previa: JPEG de {previa.shape[1]}×{previa.shape[0]} ({len(jpg) // 1024} KB)")
    comprobar(datos["relleno"] == llena and datos["campo"] == campo and datos["fotos"] == 3, "automático: la llena para la sombra y el cúmulo para las estrellas")
    __, datos = luna_3d.componer(fase, {"relleno": "mapa", "campo": "ninguno"})
    comprobar(datos["relleno"] is None and datos["campo"] is None and datos["fotos"] == 1 and datos["cubierto"] == 0.0,
              "con «mapa» y «sin estrellas» entra una sola foto")
    for pedido in ({"relleno": fase}, {"relleno": campo}, {"campo": llena}):
        try:
            luna_3d.componer(fase, pedido)
            comprobar(False, f"{pedido}: debería rechazarlo")
        except luna_3d.ErrorLuna3D as e:
            comprobar(True, f"{pedido}: rechazado ({e})")
    try:
        luna_3d.componer(campo)
        comprobar(False, "una foto que no es de la Luna: debería rechazarla")
    except luna_3d.ErrorLuna3D:
        comprobar(True, "una foto que no es de la Luna: rechazada")
    guardado = luna_3d.guardar(fase, {"firma": True})
    montaje = guardado["archivo"]
    ruta_m = os.path.join(FOTOS, montaje)
    with open(os.path.splitext(ruta_m)[0] + ".json", encoding="utf-8") as f:
        meta_m = json.load(f)
    img_m = cv2.imread(ruta_m)
    comprobar(montaje == "Luna_20261020_193000_montaje.jpg" and img_m is not None and img_m.shape[:2] == (con_foto["alto"], con_foto["ancho"]),
              f"guardado como {montaje}, {img_m.shape[1]}×{img_m.shape[0]}")
    comprobar(meta_m["objeto"] == "Luna (montaje)" and meta_m["tipo"] == "Luna" and meta_m["n_fotos"] == 3 and not meta_m["exposicion"]
              and {k: meta_m["montaje"][k] for k in ("base", "relleno", "campo", "opciones")}
              == {"base": fase, "relleno": llena, "campo": campo, "opciones": {"sombra": 1.0, "halo": 1.0, "estrellas": 1.0}}
              and meta_m["firmada"] is True and "activa" not in meta_m and meta_m["autor"] == "Prueba",
              "su JSON dice que es un montaje, de qué fotos sale y cuántas son, sin exposición")
    comprobar(luna_3d.es_montaje(montaje) and L.es_luna(montaje, meta_m) and not luna_3d.admite(montaje)
              and montaje not in [d["nombre"] for d in luna_3d.lunas()],
              "el montaje sigue siendo una foto de la Luna, pero con él no se hace otro montaje")
    comprobar(os.path.isfile(os.path.join(FOTOS, fase)) and not os.path.isdir(os.path.join(FOTOS, "originales")),
              "la foto original no se toca")
    anot = L.objetos_foto(montaje)
    d, orient = anot["disco"], anot["orientacion"]
    giro = (orient["giro"] + 180.0) % 360.0 - 180.0
    comprobar(abs(d["cx"] - con_foto["ancho"] / 2) < 1 and abs(d["cy"] - con_foto["alto"] * luna_3d.CENTRO_Y) < 1
              and abs(d["r"] - con_foto["radio"]) < 0.1 and giro == 0.0 and orient["espejo"] is False and len(anot["objetos"]) > 100,
              f"el montaje se anota con el disco que guardó (radio {d['r']:.1f} px, norte arriba): {len(anot['objetos'])} accidentes")
    # Tycho, donde está en el montaje: con el norte arriba y sin espejo, donde lo pone la vista del cielo
    tycho = next(a for a in anot["objetos"] if a["nombre"] == "Tycho")
    X, Y, __, __ = L.a_vista(g_fase, L._vectores_superficie(np.array([-43.296]), np.array([-11.215])))
    comprobar(math.hypot(tycho["x"] - (d["cx"] + d["r"] * X[0]), tycho["y"] - (d["cy"] - d["r"] * Y[0])) < 2.0, "Tycho cae en su sitio en el montaje")
    luna_3d.olvidar()
finally:
    shutil.rmtree(RAIZ, ignore_errors=True)

if fallos:
    print(f"\n❌ {len(fallos)} comprobaciones fallidas:")
    for f in fallos:
        print("   - " + f)
    sys.exit(1)
print("\n✅ Luna 3D: todo bien")
