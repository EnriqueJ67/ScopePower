# luna_3d.py
#
# «Luna 3D»: un montaje con una foto de la Luna de la galería, para que se vea la esfera
# entera y no solo la parte iluminada. Es lo que se hace a mano por capas en un editor
# (fase bien expuesta + Luna llena oscurecida en la zona en sombra + halo + estrellas), pero
# colocado con la geometría de luna_anotada.py, así que no hay nada que alinear:
#
#   1. La fase: la foto elegida, revelada otra vez sin firma (editor_dia.py), llevada a un
#      lienzo vertical con el disco centrado, el norte arriba y el este a la izquierda.
#   2. La zona en sombra: cada punto del disco se busca por su latitud y longitud lunares en
#      otra foto de la Luna más iluminada (la más llena de la galería) y, donde esa no llega,
#      en el mapa de albedo del LROC que trae la app. A la otra foto se le quita la
#      iluminación de su noche y se corrigen su tamaño, su giro y su libración; lo que quede
#      de desplazamiento entre las dos se mide por correlación y se quita. Se mezcla como
#      «aclarar»: donde la fase tiene luz manda la fase.
#   3. El halo: el propio disco iluminado, desenfocado a varias escalas.
#   4. Las estrellas de una foto de noche de la galería (sin su fondo ni su nebulosa), fuera
#      del disco y más tenues cuanto más cerca.
#
# No es una fotografía: la sombra es de otra noche y las estrellas, de otro campo. La foto
# que se guarda lo dice en su nombre de objeto («Luna (montaje)», que es lo que pintan la
# firma y la galería pública) y en su JSON ("montaje"); en vez de la exposición lleva
# cuántas fotos entran en el montaje.

import os
import re
import math
import time
import logging
import threading

import numpy as np

from idioma import _

import editor_dia
import editor_fotos
import firma
import luna_anotada
import manchas_solares
import revelado_dia

log = logging.getLogger("scopepower.luna_3d")

SUFIJO = "_montaje"            # Luna_20261021_193956_montaje.jpg
ETIQUETA_OBJETO = "montaje"    # «Luna (montaje)»: dato guardado, en español como los demás nombres de objeto
ASPECTO = 1.25                 # alto / ancho del lienzo (4:5)
FRACCION_DISCO = 0.293         # radio del disco / ancho del lienzo
CENTRO_Y = 0.46                # el disco, algo por encima del centro (abajo va la firma)
RADIO_MIN, RADIO_MAX = 280.0, 720.0     # el disco se pinta a su tamaño en la foto, entre estos radios (px)
MARGEN_RELLENO = 0.05          # la foto del relleno debe estar al menos así de más iluminada que la fase
NIVEL_RELLENO = 0.14           # brillo de la sombra (0..1 de pantalla) para un albedo medio, con «sombra» = 1
RAMPA_RELLENO = (0.45, 0.08)   # coseno del Sol sobre el suelo entre los que el relleno pasa de 0 a 1
TINTE_RELLENO = (0.90, 0.95, 1.00)      # la luz cenicienta es algo azulada
TINTE_HALO = (0.86, 0.93, 1.00)
ESCALAS_HALO = ((0.55, 0.035), (0.50, 0.11), (0.42, 0.30), (0.25, 0.70))   # (peso, sigma en radios)
DESFASE_MAX = 0.03             # desplazamiento entre las dos fotos que se acepta corregir (en radios)
ANCHO_PREVIEW = 800
CALIDAD_PREVIEW = 85
CALIDAD_GUARDADO = 92
LIMITES = {"sombra": (0.0, 2.5), "halo": (0.0, 2.0), "estrellas": (0.0, 2.0)}
DEFECTO = {"relleno": "auto", "campo": "auto", "sombra": 1.0, "halo": 1.0, "estrellas": 1.0, "firma": True}
AUTO, MAPA, NINGUNO = "auto", "mapa", "ninguno"
CUMULO_ABIERTO = "Cúmulo abierto"       # los campos de estrellas que mejor quedan de fondo

_lock = threading.Lock()                # una composición a la vez (el mini PC es pequeño)
_lock_datos = threading.Lock()
_preparado = {"clave": None, "datos": None}
_estrellas = {"clave": None, "imagen": None}
_iluminadas = {}


class ErrorLuna3D(Exception):
    """Algo que el usuario tiene que saber (la foto no sirve, no se ve el disco entero...)."""


# ─────────────────────────────────────────────────────────────────────────────
#  Qué fotos sirven
# ─────────────────────────────────────────────────────────────────────────────
def es_montaje(nombre, meta=None):
    """Si la foto de la galería es un montaje de estos (por su JSON o por su nombre)."""
    if (meta or {}).get("montaje"):
        return True
    return os.path.splitext(os.path.basename(str(nombre)))[0].endswith(SUFIJO)


def _meta(nombre):
    __, ruta_jpg, __, __ = editor_fotos.rutas(nombre)
    return ruta_jpg, luna_anotada.meta_de(ruta_jpg)


def admite(nombre):
    """Si con esa foto se puede hacer el montaje: es de la Luna, tiene su crudo y no es ya un montaje."""
    try:
        ruta_jpg, meta = _meta(nombre)
    except editor_fotos.ErrorEditor:
        return False
    return (os.path.isfile(ruta_jpg) and luna_anotada.es_luna(nombre, meta) and not es_montaje(nombre, meta)
            and editor_dia.es_foto_dia(nombre))


def _lugar(meta, observatorio):
    lat, lon = meta.get("latitud"), meta.get("longitud")
    if lat is None or lon is None:
        lat, lon = observatorio or (None, None)
    if lat is None or lon is None:
        raise ErrorLuna3D(_("No se sabe desde dónde se hizo la foto: falta el observatorio en Configuración."))
    return float(lat), float(lon)


def _instante(nombre, meta):
    instante = luna_anotada.instante_de(nombre, meta)
    if instante is None:
        raise ErrorLuna3D(_("No se sabe cuándo se hizo la foto: hace falta para saber cómo se veía la Luna."))
    return float(instante)


def _iluminada(nombre, meta, observatorio):
    """Fracción iluminada (0..1) de la Luna de esa foto; None si no se sabe cuándo o desde dónde se hizo."""
    try:
        instante = _instante(nombre, meta)
        lat, lon = _lugar(meta, observatorio)
    except ErrorLuna3D:
        return None
    clave = (round(instante), round(lat, 2), round(lon, 2))
    if clave not in _iluminadas:
        _iluminadas[clave] = luna_anotada.geometria(instante, lat, lon)["iluminada"]
    return _iluminadas[clave]


def _fotos():
    carpeta = editor_fotos.CARPETA_FOTOS
    if not os.path.isdir(carpeta):
        return []
    return sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".jpg"))


def lunas(observatorio=None):
    """Las fotos de la Luna de la galería que sirven (con crudo, sin montajes), con su fracción iluminada."""
    salida = []
    for f in _fotos():
        if not admite(f):
            continue
        __, meta = _meta(f)
        iluminada = _iluminada(f, meta, observatorio)
        if iluminada is not None:
            salida.append({"nombre": f, "iluminada": round(iluminada, 3), "fecha": str(meta.get("fecha") or "")})
    return salida


def rellenos(nombre, observatorio=None):
    """
    Las otras fotos de la Luna que pueden rellenar la zona en sombra de `nombre`: las que están
    más iluminadas que ella, la más llena primero.
    """
    todas = lunas(observatorio)
    propia = next((d["iluminada"] for d in todas if d["nombre"] == nombre), None)
    if propia is None:
        return []
    return sorted((d for d in todas if d["nombre"] != nombre and d["iluminada"] >= propia + MARGEN_RELLENO),
                  key=lambda d: -d["iluminada"])


def campos_estrellas():
    """
    Las fotos de noche de la galería (apilados con su FITS) de las que sacar las estrellas del
    fondo: los cúmulos abiertos primero, que son campos de estrellas sin nebulosa.
    """
    import catalogo_offline
    salida = []
    for f in _fotos():
        ruta_jpg = os.path.join(editor_fotos.CARPETA_FOTOS, f)
        if not os.path.isfile(os.path.splitext(ruta_jpg)[0] + ".fits"):
            continue
        meta = luna_anotada.meta_de(ruta_jpg)
        if luna_anotada.es_luna(f, meta) or manchas_solares.es_sol(f, meta):
            continue
        objeto = str(meta.get("objeto") or editor_fotos.partes_nombre(f)[0].replace("_", " "))
        tipo = catalogo_offline.categoria_objeto(meta.get("tipo") or "", objeto)
        salida.append({"nombre": f, "objeto": objeto, "tipo": tipo})
    salida.sort(key=lambda d: (d["tipo"] != CUMULO_ABIERTO, d["nombre"]))
    return salida


def normalizar(opciones):
    """Opciones completas y dentro de sus límites a partir de lo que mande la web (o nada)."""
    o = dict(DEFECTO)
    datos = opciones or {}
    for clave, (lo, hi) in LIMITES.items():
        try:
            v = float(datos.get(clave, o[clave]))
        except (TypeError, ValueError):
            v = o[clave]
        o[clave] = min(hi, max(lo, v)) if math.isfinite(v) else DEFECTO[clave]
    for clave in ("relleno", "campo"):
        o[clave] = str(datos.get(clave) or o[clave])
    o["firma"] = bool(datos.get("firma", o["firma"]))
    return o


def info(nombre, observatorio=None):
    """Lo que necesita la pantalla del montaje: la foto, sus rellenos posibles y los campos de estrellas."""
    if not admite(nombre):
        raise ErrorLuna3D(_("Con esta foto no se puede hacer el montaje: tiene que ser de la Luna y tener su imagen en crudo guardada."))
    __, meta = _meta(nombre)
    iluminada = _iluminada(nombre, meta, observatorio)
    if iluminada is None:
        raise ErrorLuna3D(_("No se sabe cuándo o desde dónde se hizo la foto: hace falta para saber cómo se veía la Luna."))
    return {"nombre": nombre, "objeto": str(meta.get("objeto") or "Luna"), "fecha": str(meta.get("fecha") or ""),
            "iluminada": round(iluminada, 3), "rellenos": rellenos(nombre, observatorio), "campos": campos_estrellas(),
            "montaje": nombre_montaje(nombre), "hay_montaje": os.path.isfile(os.path.join(editor_fotos.CARPETA_FOTOS, nombre_montaje(nombre))),
            "opciones": dict(DEFECTO)}


def nombre_montaje(nombre):
    """'Luna_20261021_193956_video.jpg' → 'Luna_20261021_193956_montaje.jpg'."""
    base = os.path.splitext(os.path.basename(nombre))[0]
    m = re.fullmatch(r"(.+_\d{8}_\d{6})(?:_[A-Za-z]+)?", base)
    return (m.group(1) if m else base) + SUFIJO + ".jpg"


# ─────────────────────────────────────────────────────────────────────────────
#  Piezas
# ─────────────────────────────────────────────────────────────────────────────
def escalon(x, a, b):
    """0 en a, 1 en b, con entrada y salida suaves (a y b en cualquier orden)."""
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def pantalla(a, b):
    """Mezcla «trama»: aclara sin saturar."""
    return 1.0 - (1.0 - a) * (1.0 - b)


def _tomar(img, xs, ys):
    import cv2
    return cv2.remap(img, xs.astype(np.float32), ys.astype(np.float32), cv2.INTER_CUBIC,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _desenfocar(img, sigma):
    """Desenfoque gaussiano; los muy anchos se hacen sobre la imagen reducida (el mismo resultado, mucho antes)."""
    import cv2
    if sigma <= 6.0:
        return cv2.GaussianBlur(img, (0, 0), max(sigma, 0.3))
    k = sigma / 4.0
    h, w = img.shape[:2]
    pequena = cv2.resize(img, (max(8, int(round(w / k))), max(8, int(round(h / k)))), interpolation=cv2.INTER_AREA)
    pequena = cv2.GaussianBlur(pequena, (0, 0), 4.0)
    return cv2.resize(pequena, (w, h), interpolation=cv2.INTER_LINEAR)


def situar(rgb01, instante, lat, lon, alt_m=0.0):
    """Disco, orientación y geometría de una foto de la Luna (RGB 0..1)."""
    import cv2
    gris = cv2.cvtColor(np.clip(rgb01 * 255.0, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    try:
        r = luna_anotada.anotar(gris, instante, lat, lon, alt_m)
    except luna_anotada.ErrorLuna as e:
        raise ErrorLuna3D(str(e))
    return {"disco": r["disco"], "orient": r["orientacion"], "geo": luna_anotada.geometria(instante, lat, lon, alt_m),
            "metodo": r["metodo"], "iluminada": r["iluminada"]}


def lienzo(radio):
    """Tamaño del lienzo y coordenadas de cada píxel en radios lunares (X a la derecha, Y arriba) para ese radio del disco."""
    ancho = int(round(radio / FRACCION_DISCO / 2.0)) * 2
    alto = int(round(ancho * ASPECTO / 2.0)) * 2
    cx, cy = ancho / 2.0 - 0.5, alto * CENTRO_Y            # el mismo centro que guarda preparar()
    x, y = np.meshgrid(np.arange(ancho, dtype=np.float64), np.arange(alto, dtype=np.float64))
    X, Y = (x - cx) / radio, -(y - cy) / radio
    return ancho, alto, X, Y


def relleno_de_foto(otra, f, forma, dentro):
    """
    La otra foto de la Luna (dict de situar() más "rgb") reproyectada sobre los puntos f del
    disco (N×3, ejes de la Luna; `dentro`: la máscara de esos puntos en un lienzo de `forma`).
    Devuelve (albedo aparente con media 1, validez 0..1), los dos del tamaño del lienzo: vale
    donde en la otra foto era de día, no estaba aplastado contra su limbo y caía dentro de ella.
    """
    import cv2
    geo2 = otra["geo"]
    X2, Y2, cos_e2, cos_i2 = luna_anotada.a_vista(geo2, f)
    x2, y2 = luna_anotada._a_foto(X2, Y2, otra["disco"], otra["orient"])
    lum2 = cv2.cvtColor(np.ascontiguousarray(otra["rgb"], dtype=np.float32), cv2.COLOR_RGB2GRAY)
    h2, w2 = lum2.shape
    xs = np.full(forma, -10.0, np.float32)
    ys = np.full(forma, -10.0, np.float32)
    xs[dentro], ys[dentro] = x2, y2
    muestra = _tomar(lum2, xs, ys)[dentro]
    # Se quita la iluminación de aquella noche (Lommel-Seeliger): queda el albedo, como en Luna llena
    ci, ce = np.clip(cos_i2, 1e-3, None), np.clip(cos_e2, 1e-3, None)
    plano = muestra / np.maximum(ci / (ci + ce), 0.05)
    en_foto = (x2 >= 2) & (x2 <= w2 - 3) & (y2 >= 2) & (y2 <= h2 - 3)
    valida = escalon(cos_i2, 0.12, 0.30) * escalon(cos_e2, 0.10, 0.25) * en_foto
    peso = float(valida.sum())
    if peso > 100:
        plano = plano / (float((plano * valida).sum() / peso) or 1.0)
    albedo = np.zeros(forma, np.float32)
    validez = np.zeros(forma, np.float32)
    albedo[dentro], validez[dentro] = plano, valida
    return albedo, validez


def _detalle(img):
    import cv2
    return cv2.GaussianBlur(img, (0, 0), 1.2) - cv2.GaussianBlur(img, (0, 0), 6.0)


def desfase(a, b, zona):
    """
    Desplazamiento (dx, dy) en píxeles que hay que darle a `b` para que su detalle caiga sobre
    el de `a`, medido por correlación de fase en el recuadro de `zona` (máscara de los píxeles
    con detalle fiable en las dos). (0, 0, 0) si la zona es pequeña.
    """
    import cv2
    ys, xs = np.nonzero(zona)
    if len(xs) < 4000:
        return 0.0, 0.0, 0.0
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    m = cv2.GaussianBlur(zona[y0:y1, x0:x1].astype(np.float32), (0, 0), 6.0)
    da = np.float32(_detalle(np.float32(a))[y0:y1, x0:x1] * m)
    db = np.float32(_detalle(np.float32(b))[y0:y1, x0:x1] * m)
    (dx, dy), respuesta = cv2.phaseCorrelate(db, da)
    return float(dx), float(dy), float(respuesta)


def estrellas_de(ruta_jpg, ancho, alto, firmada=True):
    """
    Solo las estrellas de una foto de la galería (RGB 0..1 de ancho×alto): sin el fondo ni la
    nebulosa (apertura morfológica), afinadas y con menos color para que no compitan con la Luna.
    """
    import cv2
    bgr = cv2.imread(ruta_jpg, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ErrorLuna3D(_("No se pudo leer la foto de las estrellas"))
    img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    h, w = img.shape[:2]
    if firmada:                                           # fuera la banda de la firma
        img = img[: h - int(0.24 * min(w, h))]
        h = img.shape[0]
    k = max(ancho / w, alto / h)
    tw, th = min(w, int(math.ceil(ancho / k))), min(h, int(math.ceil(alto / k)))
    x0, y0 = (w - tw) // 2, (h - th) // 2
    img = cv2.resize(img[y0:y0 + th, x0:x0 + tw], (ancho, alto), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    lado = max(9, int(round(ancho / 83.0)) | 1)
    nucleo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (lado, lado))
    fondo = cv2.GaussianBlur(cv2.morphologyEx(img, cv2.MORPH_OPEN, nucleo), (0, 0), lado / 3.0)
    est = np.clip((np.clip(img - fondo, 0.0, 1.0) - 0.05) / 0.75, 0.0, 1.0)
    est = np.power(est, 1.7)                              # puntos finos: fuera el halo de cada estrella
    gris = est.max(axis=2, keepdims=True)
    return 0.55 * est + 0.45 * gris


# ─────────────────────────────────────────────────────────────────────────────
#  Composición
# ─────────────────────────────────────────────────────────────────────────────
def preparar(fase_rgb, sit, otra=None):
    """
    Lo que no depende de los controles: la fase en el lienzo, el albedo de cada punto del disco
    (de la otra foto donde llega y del mapa donde no) y la geometría de la luz. `sit` y `otra`
    son dicts de situar(); `otra` lleva además "rgb".
    """
    disco, orient, geo = sit["disco"], sit["orient"], sit["geo"]
    R = float(min(RADIO_MAX, max(RADIO_MIN, disco["r"])))
    ancho, alto, X, Y = lienzo(R)
    r = np.hypot(X, Y)
    dentro = r < 1.0
    alfa = np.clip((1.0 - r) * R + 0.5, 0.0, 1.0)              # el borde del disco, sin dientes
    xs, ys = luna_anotada._a_foto(X, Y, disco, orient)
    foto = np.clip(_tomar(np.ascontiguousarray(fase_rgb, dtype=np.float32), xs, ys), 0.0, 1.0)
    foto *= np.clip((1.012 - r) * R / 3.0 + 1.0, 0.0, 1.0)[..., None].astype(np.float32)   # fuera del disco, nada de la foto

    f = luna_anotada._desde_vista(geo, X[dentro], Y[dentro])   # cada píxel del disco, en ejes de la Luna
    cos_i = np.zeros(r.shape, np.float32)
    cos_e = np.zeros(r.shape, np.float32)
    cos_i[dentro] = (f @ geo["R"]) @ geo["s"]
    cos_e[dentro] = np.sqrt(np.clip(1.0 - r[dentro] ** 2, 0.0, 1.0))
    lat = np.degrees(np.arcsin(np.clip(f[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(f[:, 1], f[:, 0]))
    albedo = np.zeros(r.shape, np.float32)
    albedo[dentro] = luna_anotada._albedo_en(lat, lon)
    datos = {"cubierto": 0.0, "desfase_px": None}
    if otra is not None:
        import cv2
        plano, validez = relleno_de_foto(otra, f, r.shape, dentro)
        # Lo que quede de desplazamiento entre las dos fotos (el centro de un disco se ajusta a ~1 px)
        # se mide donde las dos tienen luz y se le quita a la otra
        gris = cv2.cvtColor(foto, cv2.COLOR_RGB2GRAY)
        zona = (cos_i > 0.35) & (validez > 0.95) & (r < 0.88)
        dx, dy, respuesta = desfase(gris, plano, zona)
        if respuesta > 0.05 and math.hypot(dx, dy) <= DESFASE_MAX * R:
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            plano = cv2.warpAffine(plano, M, (ancho, alto), flags=cv2.INTER_LINEAR)
            validez = cv2.warpAffine(validez, M, (ancho, alto), flags=cv2.INTER_LINEAR)
            datos["desfase_px"] = round(math.hypot(dx, dy), 2)
        albedo = validez * plano + (1.0 - validez) * albedo
        datos["cubierto"] = round(float((validez[dentro] > 0.5).mean()), 3)
    # Aplastado contra el limbo el mapa sale en rayas: ahí se queda en su tono medio
    borde = escalon(cos_e, 0.12, 0.38)
    albedo = borde * albedo + (1.0 - borde) * float(np.median(albedo[dentro]))
    albedo[~dentro] = 0.0
    datos.update({"ancho": ancho, "alto": alto, "radio": R, "centro": (ancho / 2.0 - 0.5, alto * CENTRO_Y), "r": r.astype(np.float32), "alfa": alfa.astype(np.float32),
                  "foto": foto, "albedo": albedo.astype(np.float32), "cos_i": cos_i, "cos_e": cos_e})
    return datos


def mezclar(prep, estrellas=None, opciones=None):
    """El montaje (RGB float32 0..1) con las piezas de preparar(), las estrellas (o None) y los controles."""
    import cv2
    o = normalizar(opciones)
    R, r, alfa, foto = prep["radio"], prep["r"], prep["alfa"], prep["foto"]
    # 2. La zona en sombra: el albedo con un poco de sombra hacia el limbo, que da la esfera
    noche = escalon(prep["cos_i"], *RAMPA_RELLENO)
    volumen = 0.50 + 0.50 * np.sqrt(prep["cos_e"])
    nivel = NIVEL_RELLENO * o["sombra"] * np.power(np.clip(prep["albedo"], 0.0, None), 1.25) * volumen * noche * alfa
    relleno = nivel[..., None] * np.float32(TINTE_RELLENO)
    img = np.power(np.power(foto, 3.0) + np.power(relleno, 3.0), 1.0 / 3.0)   # «aclarar», sin escalón donde se cruzan
    img = np.clip(img, 0.0, 1.0).astype(np.float32)
    # 3. El halo: el disco desenfocado a varias escalas; sobre el disco, casi nada (no vela la sombra)
    if o["halo"] > 0:
        lum = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        halo = np.zeros_like(lum)
        for peso, sigma in ESCALAS_HALO:
            halo += peso * _desenfocar(lum, sigma * R)
        halo *= o["halo"] * (1.0 - 0.80 * alfa)
        img = pantalla(img, np.clip(halo, 0.0, 1.0)[..., None] * np.float32(TINTE_HALO))
    # 4. Las estrellas: ninguna sobre el disco y más tenues cuanto más cerca
    if estrellas is not None and o["estrellas"] > 0:
        cerca = escalon(r, 1.02, 1.9)
        capa = np.clip(estrellas * o["estrellas"], 0.0, 1.0) * ((1.0 - alfa) * (0.15 + 0.85 * cerca))[..., None]
        img = pantalla(img, capa)
    return np.clip(img, 0.0, 1.0).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
#  Con las fotos de la galería
# ─────────────────────────────────────────────────────────────────────────────
def _revelada(nombre, meta):
    """La foto de día revelada con sus ajustes guardados, sin firma ni giro (RGB float32 0..1)."""
    try:
        lineal, limbo = editor_dia.cargar_lineal(nombre)
    except (editor_dia.ErrorEditorDia, editor_fotos.ErrorEditor) as e:
        raise ErrorLuna3D(str(e))
    rgb8 = revelado_dia.revelar(lineal, revelado_dia.normalizar(meta.get("ajustes_dia")), limbo_px=limbo)
    return rgb8.astype(np.float32) / 255.0


def _sello(nombre):
    ruta_jpg, __ = _meta(nombre)
    crudo = editor_dia.ruta_crudo(nombre)
    return tuple(os.path.getmtime(p) if os.path.isfile(p) else 0 for p in (ruta_jpg, os.path.splitext(ruta_jpg)[0] + ".json", crudo))


def _elegir_relleno(nombre, pedido, observatorio):
    """Nombre de la foto que rellena la sombra (None: solo el mapa) según lo pedido en la web."""
    if pedido == MAPA:
        return None
    posibles = [d["nombre"] for d in rellenos(nombre, observatorio)]
    if pedido == AUTO:
        return posibles[0] if posibles else None
    if pedido not in posibles:
        raise ErrorLuna3D(_("Esa foto no sirve para rellenar la sombra: tiene que ser de la Luna, con su crudo, y estar más iluminada."))
    return pedido


def _elegir_campo(pedido):
    if pedido == NINGUNO:
        return None
    posibles = [d["nombre"] for d in campos_estrellas()]
    if pedido == AUTO:
        return posibles[0] if posibles else None
    if pedido not in posibles:
        raise ErrorLuna3D(_("Esa foto no sirve para las estrellas: tiene que ser un apilado de noche de la galería."))
    return pedido


def _preparar_foto(nombre, relleno, observatorio):
    clave = (nombre, relleno, _sello(nombre), _sello(relleno) if relleno else None)
    with _lock_datos:
        if _preparado["clave"] == clave:
            return _preparado["datos"]
    t0 = time.time()
    __, meta = _meta(nombre)
    lat, lon = _lugar(meta, observatorio)
    fase = _revelada(nombre, meta)
    sit = situar(fase, _instante(nombre, meta), lat, lon)
    if sit["metodo"] != "disco":
        raise ErrorLuna3D(_("La Luna tiene que salir entera en la foto para hacer el montaje."))
    otra = None
    if relleno:
        __, meta2 = _meta(relleno)
        lat2, lon2 = _lugar(meta2, observatorio)
        rgb2 = _revelada(relleno, meta2)
        otra = situar(rgb2, _instante(relleno, meta2), lat2, lon2)
        otra["rgb"] = rgb2
    datos = preparar(fase, sit, otra)
    datos.update({"instante": sit["geo"]["instante"], "iluminada": sit["iluminada"], "relleno": relleno})
    log.info("Luna 3D preparada con %s (%.0f %% iluminada)%s: lienzo %dx%d, %.1f s", nombre, 100 * sit["iluminada"],
             f", sombra de {relleno} ({100 * datos['cubierto']:.0f} % del disco, desfase {datos['desfase_px']} px)" if relleno
             else ", sombra del mapa", datos["ancho"], datos["alto"], time.time() - t0)
    with _lock_datos:
        _preparado.update({"clave": clave, "datos": datos})
    return datos


def _estrellas_de_campo(campo, ancho, alto):
    if not campo:
        return None
    ruta_jpg, meta = _meta(campo)
    clave = (campo, os.path.getmtime(ruta_jpg), ancho, alto)
    with _lock_datos:
        if _estrellas["clave"] == clave:
            return _estrellas["imagen"]
    imagen = estrellas_de(ruta_jpg, ancho, alto, firmada=bool(meta.get("firmada", True)))
    with _lock_datos:
        _estrellas.update({"clave": clave, "imagen": imagen})
    return imagen


def componer(nombre, opciones=None, observatorio=None):
    """
    El montaje de la foto `nombre` de la galería: (RGB uint8, datos). `datos` dice con qué se
    hizo: relleno (foto o None), campo (foto de las estrellas o None), cubierto (fracción del
    disco que rellena la otra foto), fotos (cuántas fotos entran) e instante.
    """
    if not admite(nombre):
        raise ErrorLuna3D(_("Con esta foto no se puede hacer el montaje: tiene que ser de la Luna y tener su imagen en crudo guardada."))
    o = normalizar(opciones)
    relleno = _elegir_relleno(nombre, o["relleno"], observatorio)
    campo = _elegir_campo(o["campo"]) if o["estrellas"] > 0 else None
    prep = _preparar_foto(nombre, relleno, observatorio)
    estrellas = _estrellas_de_campo(campo, prep["ancho"], prep["alto"])
    img = mezclar(prep, estrellas, o)
    cx, cy = prep["centro"]
    datos = {"relleno": relleno, "campo": campo, "cubierto": prep["cubierto"], "desfase_px": prep["desfase_px"],
             "disco": {"cx": round(cx, 1), "cy": round(cy, 1), "r": round(prep["radio"], 1)},
             "instante": prep["instante"], "iluminada": round(prep["iluminada"], 3), "fotos": 1 + bool(relleno) + bool(campo),
             "opciones": {k: o[k] for k in ("sombra", "halo", "estrellas")}}
    return (img * 255.0 + 0.5).astype(np.uint8), datos


def _ocupado():
    raise ErrorLuna3D(_("Hay otro montaje en marcha; espera a que termine"))


def vista_previa(nombre, opciones=None, observatorio=None):
    """(JPEG en bytes con el lado mayor a ANCHO_PREVIEW, datos de componer()). Solo una a la vez."""
    import cv2
    if not _lock.acquire(blocking=False):
        _ocupado()
    try:
        rgb, datos = componer(nombre, opciones, observatorio)
        k = ANCHO_PREVIEW / float(max(rgb.shape[:2]))
        if k < 1:
            rgb = cv2.resize(rgb, (int(round(rgb.shape[1] * k)), int(round(rgb.shape[0] * k))), interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_PREVIEW])
        if not ok:
            raise ErrorLuna3D(_("No se pudo codificar la vista previa"))
        return jpg.tobytes(), datos
    finally:
        _lock.release()


def guardar(nombre, opciones=None, observatorio=None):
    """
    Compone el montaje a su tamaño, lo firma como «<objeto> (montaje)» con el número de fotos
    que entran y lo deja en la galería como una foto más (<objeto>_<fecha>_montaje.jpg, con su
    JSON). Si ya había un montaje de esa foto, lo sustituye. Devuelve {"archivo", ...datos de componer()}.
    """
    import cv2
    if not _lock.acquire(blocking=False):
        _ocupado()
    try:
        rgb, datos = componer(nombre, opciones, observatorio)
        o = normalizar(opciones)
        __, meta_foto = _meta(nombre)
        meta = {k: v for k, v in firma.datos_observatorio().items() if k != "activa"}
        for k in ("autor", "latitud", "longitud", "fecha"):
            if meta_foto.get(k) not in (None, ""):
                meta[k] = meta_foto[k]
        # Sin exposición: la firma y la galería pública dicen «N fotos», no un tiempo que no es de esta imagen
        meta.update({"objeto": f"{str(meta_foto.get('objeto') or 'Luna').strip()} ({ETIQUETA_OBJETO})", "tipo": "Luna",
                     "n_fotos": datos["fotos"], "exposicion": 0, "instante": datos["instante"], "firmada": bool(o["firma"]),
                     # El disco va con el norte arriba y en un sitio fijo: las anotaciones lo toman de aquí
                     # (luna_anotada.objetos_foto), que con el halo el limbo no se ajusta bien
                     "montaje": {"base": nombre, "relleno": datos["relleno"], "campo": datos["campo"],
                                 "cubierto": datos["cubierto"], "opciones": datos["opciones"],
                                 "disco": datos["disco"], "ancho": int(rgb.shape[1]), "alto": int(rgb.shape[0])}})
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if o["firma"]:
            bgr = firma.firmar(bgr, meta)
        archivo = nombre_montaje(nombre)
        ruta = os.path.join(editor_fotos.CARPETA_FOTOS, archivo)
        if not cv2.imwrite(ruta, bgr, [cv2.IMWRITE_JPEG_QUALITY, CALIDAD_GUARDADO]):
            raise ErrorLuna3D(_("No se pudo guardar la foto en la galería"))
        firma.guardar_meta(ruta, meta)
        log.info("Montaje Luna 3D guardado en la galería: %s (fase %s, sombra %s, estrellas %s)", archivo, nombre,
                 datos["relleno"] or "del mapa", datos["campo"] or "ninguna")
        return dict(datos, archivo=archivo)
    finally:
        _lock.release()


def olvidar():
    """Suelta lo que queda en memoria del último montaje (unas decenas de MB)."""
    with _lock_datos:
        _preparado.update({"clave": None, "datos": None})
        _estrellas.update({"clave": None, "imagen": None})
