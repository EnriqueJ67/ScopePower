# procesado.py
#
# Procesado "estilo Seestar" del apilado, en este orden:
#   1. Gradiente: se ajusta una superficie polinómica de 2º orden al fondo
#      (con estrellas y objeto enmascarados) y se resta. Espacio lineal.
#      Después, calibración de color: a cada canal se le resta su cielo y se
#      equilibran con las estrellas del campo (la estrella mediana, blanca).
#   2. Reducción de ruido, todavía en lineal (donde el ruido es uniforme y
#      simétrico): por escalas de 1 a 16 px, cada una atenuada según lo que
#      destaca sobre el ruido medido en ella; más fuerte en color que en
#      luminancia. Estrellas, bordes y gradientes suaves pasan intactos.
#   3. Neutralización de fondo + estirado adaptativo multiescala (tipo
#      MultiscaleAdaptiveStretch de PixInsight): se estira la luminancia con una
#      ganancia LOCAL (alta en el fondo, baja en estrellas y núcleos) y el color
#      se escala en proporción, así lo brillante no se quema ni pierde el color.
#   4. Contraste local (CLAHE en luminancia) + curva en S suave.
#   5. Color, en dos partes:
#      - base (color_base): reneutralizar el fondo y un solo color por estrella;
#        necesita el vecindario y las máscaras de estrellas.
#      - ajustes rápidos (ajustes_rapidos): temperatura de color, brillo general,
#        SCNR (quitar dominante verde), saturación y curvas por puntos de control.
#        Son píxel a píxel (o por tabla), así que el editor de la galería los
#        repite en el navegador sobre la base en 8 bits para verlos al instante
#        (static/js/retoque.js); al guardar se hacen aquí en coma flotante a
#        resolución completa. Las dos versiones han de dar lo mismo: cualquier
#        cambio en una va también en la otra.
#
# Tres estilos: natural, seestar (por defecto) y agresivo, con ajustes finos
# (ruido, contraste, saturación) en la sección [Procesado] de config.ini.

import logging
import math
import configparser
import json
import threading

import numpy as np
import cv2

from AutoStrechSTF import neutralizar_fondo
from idioma import _

log = logging.getLogger("scopepower.procesado")

ESTILOS = {
    #            gradiente ruido  contraste saturacion scnr  + estirado (fondo_objetivo, agresividad, compresion, recuperacion)
    "natural":  {"gradiente": True, "aplanar": False, "ruido": 0.30, "contraste": 0.25, "saturacion": 1.3, "scnr": 0.5, "scnr_estrellas": True,
                 "saturacion_estrellas": 1.0, "color_estrellas_uniforme": True, "fondo_objetivo": 0.10, "agresividad": 0.6, "compresion": 0.3, "recuperacion": 1.0, "nitidez": 0.0},
    "seestar":  {"gradiente": True, "aplanar": False, "ruido": 0.55, "contraste": 0.50, "saturacion": 1.7, "scnr": 0.8, "scnr_estrellas": True,
                 "saturacion_estrellas": 1.2, "color_estrellas_uniforme": True, "fondo_objetivo": 0.13, "agresividad": 0.7, "compresion": 0.3, "recuperacion": 1.0, "nitidez": 0.0},
    "agresivo": {"gradiente": True, "aplanar": False, "ruido": 0.80, "contraste": 0.80, "saturacion": 2.1, "scnr": 1.0, "scnr_estrellas": True,
                 "saturacion_estrellas": 1.2, "color_estrellas_uniforme": True, "fondo_objetivo": 0.16, "agresividad": 0.8, "compresion": 0.2, "recuperacion": 1.1, "nitidez": 0.0},
    # saturacion_estrellas: la saturación general multiplica también los halos de las
    # estrellas, y en un doblete con cámara de color el halo es rojo + azul (los dos
    # enfocan fuera del verde; medido en la Pacman: halo R/G 1,04 y B/G 1,89 frente a
    # 0,71 y 1,21 en el núcleo), así que las estrellas azules salen violeta. Con su
    # propia saturación (sobre la máscara de estrellas) quedan blancas, azules o
    # naranjas según su color real y la nebulosa conserva la general.
    # scnr_estrellas: el SCNR se aplica también a las estrellas. Se excluían por
    # miedo a teñirlas de magenta, pero el método «average neutral» solo baja el
    # verde hasta la media de rojo y azul, nunca por debajo: una estrella azul o
    # naranja no cambia y una con el halo verdoso (M 13, 8-oct-2026: a 4-10 px
    # del núcleo el verde dominaba, B/G 0,5-0,85, por el foco de esa noche) queda
    # neutra. Con false, el comportamiento anterior.
    # color_estrellas_uniforme: cada estrella con un solo color, el promedio de sus
    # píxeles ponderado por la luz. Con el refractor y la cámara de color el azul y
    # el verde enfocan en el núcleo y el rojo desenfocado forma el halo: la misma
    # estrella sale con un punto azul dentro de una mancha naranja (M 92, 8-oct-2026:
    # núcleo B/G 1,11 y halo 0,86 en lineal). Una estrella no tiene dos colores.
    # "aplanar" (fondo local a gran escala) queda como opción manual: no elimina
    # las sombras de polvo del sensor, que tienen el tamaño de una galaxia
    # (eso solo lo arreglan los flats), y puede comerse nebulosas extensas.
}


def leer_config():
    """Estilo y ajustes. Un valor explícito en [Procesado] manda sobre el del estilo."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    p = cfg["Procesado"] if "Procesado" in cfg else {}
    estilo = str(p.get("estilo", "seestar")).lower()
    if estilo not in ESTILOS:
        estilo = "seestar"
    ajustes = dict(ESTILOS[estilo])
    ajustes["estilo"] = estilo
    for clave in ("ruido", "contraste", "saturacion", "saturacion_estrellas", "scnr", "brillo", "temperatura",
                  "fondo_objetivo", "agresividad", "compresion", "recuperacion", "escala"):
        v = p.get(clave, "")
        if str(v).strip() != "":
            ajustes[clave] = float(v)
    for clave in ("gradiente", "aplanar", "scnr_estrellas", "color_estrellas_uniforme"):
        if str(p.get(clave, "")).strip() != "":
            ajustes[clave] = str(p.get(clave)).lower() == "true"
    if str(p.get("escala_aplanado", "")).strip() != "":
        ajustes["escala_aplanado"] = float(p.get("escala_aplanado"))
    if str(p.get("curva", "")).strip() != "":
        ajustes["curva"] = str(p.get("curva")).strip().lower()
    # Curvas por puntos de control del editor, guardadas como JSON (ver normalizar_curvas)
    if str(p.get("curvas", "")).strip() != "":
        try:
            ajustes["curvas"] = normalizar_curvas(json.loads(p.get("curvas")))
        except ValueError:
            log.warning("[Procesado] curvas no es un JSON válido; se ignora")
    return ajustes


# Presets: conjuntos de ajustes con nombre, guardados en presets_procesado.json
# (propio de cada PC; copiar_a_campo.bat no lo copia). Se crean desde el editor
# de la galería o desde el apilado y se cargan en cualquiera de los dos.
PRESETS_RUTA = "presets_procesado.json"
CLAVES_PRESET = ("estilo", "estirado", "curva", "fondo_objetivo", "compresion", "brillo", "temperatura",
                 "ruido", "contraste", "saturacion", "saturacion_estrellas", "scnr", "scnr_estrellas", "color_estrellas_uniforme",
                 "gradiente", "aplanar", "nitidez", "curvas")
_lock_presets = threading.Lock()


def leer_presets():
    """{nombre: ajustes} ordenado por nombre; {} si no hay fichero o está roto."""
    try:
        with open(PRESETS_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
        if not isinstance(datos, dict):
            return {}
        return {str(k): v for k, v in sorted(datos.items(), key=lambda kv: kv[0].lower()) if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


def _filtrar_preset(ajustes):
    limpio = {}
    for k in CLAVES_PRESET:
        if k not in ajustes or ajustes[k] is None or (not isinstance(ajustes[k], dict) and str(ajustes[k]).strip() == ""):
            continue
        v = ajustes[k]
        if k in ("gradiente", "aplanar", "scnr_estrellas", "color_estrellas_uniforme"):
            limpio[k] = (str(v).lower() == "true") if not isinstance(v, bool) else v
        elif k in ("estilo", "estirado", "curva"):
            limpio[k] = str(v).lower()
        elif k == "curvas":
            # Siempre presente (aunque vacío): al cargar el preset borra las curvas anteriores
            limpio[k] = normalizar_curvas(v if isinstance(v, dict) else {})
        else:
            try:
                limpio[k] = float(v)
            except (TypeError, ValueError):
                pass
    return limpio


def guardar_preset(nombre, ajustes):
    """Crea o sustituye un preset. Devuelve el diccionario completo de presets."""
    nombre = " ".join(str(nombre or "").split())[:40]
    if not nombre:
        raise ValueError(_("El preset necesita un nombre"))
    with _lock_presets:
        presets = leer_presets()
        presets[nombre] = _filtrar_preset(ajustes or {})
        with open(PRESETS_RUTA, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=1)
    return leer_presets()


# ── Preset automático por tipo de objeto ─────────────────────────────────────
# Al apuntar a un objeto (GoTo, planetario, Esta noche, plan de la noche) el
# apilado en tiempo real carga el preset que corresponde a su familia, si existe
# uno con ese nombre: «Galaxias» para las galaxias, «Nebulosas» para nebulosas,
# nebulosas planetarias y restos de supernova, «Cúmulos» (o «Cúmulos abiertos» /
# «Cúmulos globulares») para los cúmulos. Sin preset para esa familia no se toca
# nada. Se desactiva con [Procesado] preset_automatico = false.
PRESETS_POR_TIPO = {
    "Galaxia": ("galaxias", "galaxia"),
    "Nebulosa": ("nebulosas", "nebulosa"),
    "Nebulosa planetaria": ("nebulosas planetarias", "nebulosas", "nebulosa"),
    "Resto de supernova": ("restos de supernova", "nebulosas", "nebulosa"),
    "Cúmulo abierto": ("cumulos abiertos", "cumulos", "cumulo"),
    "Cúmulo globular": ("cumulos globulares", "cumulos", "cumulo"),
    "Cometa": ("cometas", "cometa"),
    "Planeta": ("planetas", "planeta"),
    "Luna": ("luna",),
    "Sol": ("sol",),
}
# Último preset cargado automáticamente, para que la pantalla de Apilado lo refleje
PRESET_AUTOMATICO = {"contador": 0, "nombre": "", "tipo": "", "hora": ""}


def _normalizar(texto):
    try:
        from unidecode import unidecode
        texto = unidecode(str(texto or ""))
    except Exception:
        texto = str(texto or "")
    return " ".join(texto.lower().split())


def preset_para_tipo(tipo, presets=None):
    """Nombre del preset que corresponde a la familia `tipo` (categoría de la galería), o None."""
    candidatos = PRESETS_POR_TIPO.get(str(tipo or ""), ())
    if not candidatos:
        return None
    presets = leer_presets() if presets is None else presets
    por_nombre = {_normalizar(n): n for n in presets}
    for c in candidatos:
        if c in por_nombre:
            return por_nombre[c]
    return None


def preset_automatico_activo():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    try:
        return str(cfg.get("Procesado", "preset_automatico", fallback="true")).strip().lower() not in ("false", "0", "no", "off")
    except Exception:
        return True


def aplicar_preset_por_tipo(tipo):
    """
    Carga en config.ini el preset de la familia `tipo` (el apilado lee los ajustes
    del config en cada foto). Devuelve el nombre cargado o None si no procede.
    """
    if not preset_automatico_activo():
        return None
    presets = leer_presets()
    nombre = preset_para_tipo(tipo, presets)
    if not nombre:
        return None
    # Un preset sin curva o sin saturación de estrellas es de antes de esos controles
    ajustes = dict({"curva": "gamma"}, **presets[nombre])
    guardar_config(ajustes)
    from datetime import datetime
    PRESET_AUTOMATICO.update({"contador": PRESET_AUTOMATICO["contador"] + 1, "nombre": nombre,
                              "tipo": str(tipo or ""), "hora": datetime.now().strftime("%H:%M:%S")})
    log.info("Preset «%s» cargado por el tipo de objeto (%s)", nombre, tipo)
    return nombre


def borrar_preset(nombre):
    with _lock_presets:
        presets = leer_presets()
        presets.pop(str(nombre), None)
        with open(PRESETS_RUTA, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=1)
    return leer_presets()


def guardar_config(cambios):
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if "Procesado" not in cfg:
        cfg["Procesado"] = {}
    for k, v in cambios.items():
        if k in ("estilo", "ruido", "contraste", "saturacion", "saturacion_estrellas", "scnr", "scnr_estrellas", "color_estrellas_uniforme",
                 "gradiente", "aplanar", "escala_aplanado",
                 "fondo_objetivo", "agresividad", "compresion", "recuperacion", "escala", "curva",
                 "brillo", "temperatura"):
            cfg["Procesado"][k] = str(v).lower() if isinstance(v, bool) else str(v)
        elif k == "curvas":
            # Puede venir como dict (de la web) o como el JSON ya hecho (de un preset); vacío = sin curvas
            if isinstance(v, str):
                try:
                    v = json.loads(v) if v.strip() else {}
                except ValueError:
                    v = {}
            curvas = normalizar_curvas(v)
            cfg["Procesado"]["curvas"] = json.dumps(curvas, separators=(",", ":")) if curvas else ""
    with open("config.ini", "w") as f:
        cfg.write(f)


# ─────────────────────────────────────────────────────────────────────────────
#  0. Recorte de zoom (campo visible)
# ─────────────────────────────────────────────────────────────────────────────
# Con un sensor grande y focal corta los objetos pequeños (globulares,
# planetarias, galaxias) salen diminutos. zoom_preview ([Dispositivos] de
# config.ini) es el porcentaje que se quita por los bordes: 0 = campo completo,
# 50 = se ve el 50 % del lado, 75 = el 25 %. El recorte se hace sobre el cubo
# lineal, ANTES de procesar: el plate solve, el alineado y el FITS apilado
# siguen a campo completo (cuantas más estrellas, mejor resuelve).

def fraccion_campo(config=None):
    """Fracción del lado del campo que se muestra (1.0 = todo), según zoom_preview."""
    try:
        if config is None:
            config = configparser.ConfigParser()
            config.read("config.ini")
        zoom = int(float(config["Dispositivos"].get("zoom_preview", 0)))
    except Exception:
        zoom = 0
    zoom = min(90, max(0, zoom))
    return (100 - zoom) / 100.0


def recortar_centro(cubo, fraccion):
    """
    Recorte central de un cubo (3, H, W) o imagen (H, W, 3) conservando
    `fraccion` del ancho y del alto. Devuelve (recorte, x0, y0): el
    desplazamiento en píxeles de la esquina superior izquierda respecto a la
    imagen completa, que necesitan las anotaciones.
    """
    if fraccion >= 0.999:
        return cubo, 0, 0
    canales_primero = cubo.ndim == 3 and cubo.shape[0] == 3 and cubo.shape[-1] != 3
    h, w = (cubo.shape[1], cubo.shape[2]) if canales_primero else cubo.shape[:2]
    nh, nw = max(8, int(round(h * fraccion))), max(8, int(round(w * fraccion)))
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    if canales_primero:
        rec = cubo[:, y0:y0 + nh, x0:x0 + nw]
    else:
        rec = cubo[y0:y0 + nh, x0:x0 + nw]
    return np.ascontiguousarray(rec), x0, y0


def recortar_campo(cubo, fraccion, zona=None, margen=0.0):
    """
    Recorte completo del campo: primero la `zona` (x0, y0, ancho, alto) cubierta
    por todas las fotos del apilado (con montura altacimutal la rotación de campo
    deja los bordes con menos fotos; si no viene, se detecta en la imagen),
    después un `margen` extra por cada lado (fracción del lado, 0..0.3; el
    "Recorte de bordes" del editor) y, dentro, el recorte central de zoom.
    Devuelve (recorte, x0, y0) con el desplazamiento respecto a la imagen
    completa, como recortar_centro.
    """
    zx, zy = 0, 0
    canales_primero = cubo.ndim == 3 and cubo.shape[0] == 3 and cubo.shape[-1] != 3
    if not zona:
        zona = zona_automatica(cubo)
    if zona:
        x0, y0, ancho, alto = [int(v) for v in zona]
        cubo = cubo[:, y0:y0 + alto, x0:x0 + ancho] if canales_primero else cubo[y0:y0 + alto, x0:x0 + ancho]
        zx, zy = x0, y0
    margen = float(min(max(margen, 0.0), 0.3))
    if margen > 0:
        h, w = (cubo.shape[1], cubo.shape[2]) if canales_primero else cubo.shape[:2]
        mx, my = int(w * margen), int(h * margen)
        cubo = cubo[:, my:h - my, mx:w - mx] if canales_primero else cubo[my:h - my, mx:w - mx]
        zx, zy = zx + mx, zy + my
    rec, x1, y1 = recortar_centro(cubo, fraccion)
    return np.ascontiguousarray(rec), zx + x1, zy + y1


def fraccion_con_giro(ancho, alto, giro):
    """
    Mayor fracción del lado que cabe entera dentro de un campo de ancho × alto
    cuando el encuadre se gira `giro` grados sin cambiar de proporción: 1.0 en
    los múltiplos de 180° (y de 90° si el campo es cuadrado), 0,71 a 45° en un
    campo cuadrado.
    """
    c, s = abs(math.cos(math.radians(giro))), abs(math.sin(math.radians(giro)))
    return min(1.0, ancho / (ancho * c + alto * s), alto / (ancho * s + alto * c))


def _traslacion(dx, dy):
    return np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]])


def geometria_giro(ancho, alto, fraccion, giro, espejo=False, centro=None):
    """
    Geometría del encuadre de girar_campo sobre una zona útil de ancho × alto, sin
    tocar la imagen (la usa también «Apunta aquí» para deshacer el giro de un
    toque). `centro`: (x, y) en píxeles de la zona alrededor del cual se recorta
    el zoom (None = el centro de la zona); si el recorte no cabe, se arrima lo
    justo para no salirse del campo. Devuelve un dict:
      T        matriz 3×3 que lleva un píxel (x, y) de la zona al recorte
      ancho, alto   tamaño del recorte
      cuartos  cuartos de vuelta en horario (0..3), tras el espejo
      M        matriz 2×3 del warpAffine si hay que interpolar; None si no
      recorte  (x, y): sin interpolar, la esquina del recorte
      ventana  con M, (x0, y0, x1, y1): el trozo de la zona (ya volteada y con los
               cuartos dados) que pisa el encuadre girado, lo único que se interpola
    """
    w, h = int(ancho), int(alto)
    T = np.eye(3)
    if espejo:
        T = np.array([[-1.0, 0.0, w - 1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]) @ T
    giro = (float(giro) + 180.0) % 360.0 - 180.0
    cuartos = int(round(giro / 90.0))
    resto = giro - 90.0 * cuartos
    cuartos %= 4
    for __ in range(cuartos):                         # un cuarto en horario: (x, y) -> (alto - 1 - y, x)
        T = np.array([[0.0, -1.0, h - 1.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]) @ T
        w, h = h, w
    # Centro del encuadre, llevado al espacio ya volteado y girado en cuartos; por
    # defecto el centro de la zona (dx = dy = 0: el mismo recorte central de siempre)
    dx = dy = 0.0
    if centro is not None:
        try:
            cx, cy, __ = T @ np.array([float(centro[0]), float(centro[1]), 1.0])
            if math.isfinite(cx) and math.isfinite(cy):
                dx, dy = cx - (w - 1) / 2.0, cy - (h - 1) / 2.0
        except (TypeError, ValueError, IndexError):
            pass
    if abs(resto) < 0.05:
        if fraccion >= 0.999:
            return {"T": T, "ancho": w, "alto": h, "cuartos": cuartos, "M": None, "recorte": (0, 0)}
        nh, nw = max(8, int(round(h * fraccion))), max(8, int(round(w * fraccion)))      # como recortar_centro
        y1, x1 = (h - nh) // 2, (w - nw) // 2
        x1 = min(max(x1 + int(round(dx)), 0), w - nw)
        y1 = min(max(y1 + int(round(dy)), 0), h - nh)
        return {"T": _traslacion(-x1, -y1) @ T, "ancho": nw, "alto": nh, "cuartos": cuartos, "M": None, "recorte": (x1, y1)}
    f = min(fraccion, fraccion_con_giro(w, h, resto))
    nw, nh = max(8, int(w * f)), max(8, int(h * f))
    # Solo hace falta el trozo que pisa el encuadre girado (más 3 px para la bicúbica):
    # medio ancho y medio alto del rectángulo girado, y el centro arrimado para que quepa
    c, s = abs(math.cos(math.radians(resto))), abs(math.sin(math.radians(resto)))
    hx, hy = (nw * c + nh * s) / 2.0, (nw * s + nh * c) / 2.0
    cx = (w - 1) / 2.0 + (min(max(dx, hx - (w - 1) / 2.0), (w - 1) / 2.0 - hx) if (w - 1) / 2.0 > hx else 0.0)
    cy = (h - 1) / 2.0 + (min(max(dy, hy - (h - 1) / 2.0), (h - 1) / 2.0 - hy) if (h - 1) / 2.0 > hy else 0.0)
    x0, y0 = max(0, int(cx - hx) - 3), max(0, int(cy - hy) - 3)
    x1, y1 = min(w, int(cx + hx) + 4), min(h, int(cy + hy) + 4)
    M = cv2.getRotationMatrix2D((cx - x0, cy - y0), -resto, 1.0)
    M[0, 2] += (nw - 1) / 2.0 - (cx - x0)
    M[1, 2] += (nh - 1) / 2.0 - (cy - y0)
    T = np.vstack([M, [0.0, 0.0, 1.0]]) @ _traslacion(-x0, -y0) @ T
    return {"T": T, "ancho": nw, "alto": nh, "cuartos": cuartos, "M": M, "recorte": (x0, y0), "ventana": (x0, y0, x1, y1)}


def _centro_en_zona(centro, ancho, alto, zx, zy):
    """
    El centro del encuadre, dado como fracciones (fx, fy) de la imagen completa
    de ancho × alto (0..1, lo que guarda el editor en los ajustes), en píxeles de
    la zona útil que empieza en (zx, zy). None si no viene o no vale.
    """
    if centro is None:
        return None
    try:
        fx, fy = float(centro[0]), float(centro[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(fx) and math.isfinite(fy)):
        return None
    return fx * ancho - zx, fy * alto - zy


def girar_campo(cubo, fraccion, giro, zona=None, margen=0.0, espejo=False, centro=None):
    """
    Como recortar_campo, pero con el encuadre girado `giro` grados alrededor del
    centro de la zona útil (positivo = la imagen gira en sentido horario) y, con
    `espejo`, volteado antes de izquierda a derecha. `centro`: (fx, fy),
    fracciones de la imagen completa alrededor de las cuales se recorta el zoom
    (None = el centro de la zona; ver geometria_giro). Devuelve (recorte, M): M es
    la matriz afín 2×3 que lleva un píxel (x, y) de la imagen completa al
    recorte, que necesitan las anotaciones. Sin giro ni espejo el recorte es el
    mismo de recortar_campo. Vale para un cubo (3, H, W), una imagen (H, W, 3) o
    un plano (H, W), y conserva el tipo (float32, uint16, uint8).

    Los cuartos de vuelta se hacen cambiando filas por columnas, sin interpolar
    ni perder campo. El resto (entre -45° y 45°) se interpola (bicúbica, acotada
    a los valores vecinos para que las estrellas no dejen anillos oscuros), y
    como las esquinas se salen del campo el recorte se encoge lo justo: con
    `fraccion` por debajo de fraccion_con_giro() no se pierde nada más.
    """
    canales_primero = cubo.ndim == 3 and cubo.shape[0] == 3 and cubo.shape[-1] != 3
    ejes = (1, 2) if canales_primero else (0, 1)
    rec, zx, zy = recortar_campo(cubo, 1.0, zona, margen)
    g = geometria_giro(rec.shape[ejes[1]], rec.shape[ejes[0]], fraccion, giro, espejo,
                       centro=_centro_en_zona(centro, cubo.shape[ejes[1]], cubo.shape[ejes[0]], zx, zy))
    T = (g["T"] @ _traslacion(-zx, -zy))[:2]
    if espejo:
        rec = np.flip(rec, axis=ejes[1])
    if g["cuartos"]:
        rec = np.rot90(rec, -g["cuartos"], axes=ejes)     # np.rot90 gira en antihorario
    x1, y1 = g["recorte"]
    nw, nh = g["ancho"], g["alto"]
    M = g["M"]
    if M is None:
        rec = rec[:, y1:y1 + nh, x1:x1 + nw] if canales_primero else rec[y1:y1 + nh, x1:x1 + nw]
        return np.ascontiguousarray(rec), T
    x0, y0, x1, y1 = g["ventana"]
    rec = rec[:, y0:y1, x0:x1] if canales_primero else rec[y0:y1, x0:x1]
    nucleo = np.ones((3, 3), np.uint8)

    def _girar(img):
        img = np.ascontiguousarray(img if img.dtype in (np.uint8, np.uint16, np.float32) else img.astype(np.float32))
        sal = cv2.warpAffine(img, M, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        bajo = cv2.warpAffine(cv2.erode(img, nucleo), M, (nw, nh), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)
        alto_ = cv2.warpAffine(cv2.dilate(img, nucleo), M, (nw, nh), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)
        return np.clip(sal, bajo, alto_)

    if canales_primero:
        return np.stack([_girar(rec[i]) for i in range(3)]), T
    return _girar(rec), T


def matriz_campo(ancho, alto, fraccion, giro, zona=None, margen=0.0, espejo=False, centro=None):
    """
    La geometría de girar_campo sin la imagen: (M, ancho_recorte, alto_recorte),
    con M la afín 2×3 que lleva un píxel de una imagen completa de ancho × alto
    a su recorte. `zona` ya resuelta: None es la imagen entera (girar_campo, si
    no se le da, la detecta con zona_automatica); `centro` como en girar_campo.
    La usan las anotaciones de la galería, que colocan los objetos sobre un JPG
    ya guardado, y el editor para saber qué píxel del FITS se ha tocado.
    """
    w, h = int(ancho), int(alto)
    zx, zy = 0, 0
    if zona:
        x0, y0, zw, zh = [int(v) for v in zona]
        w, h = min(zw, w - x0), min(zh, h - y0)
        zx, zy = x0, y0
    margen = float(min(max(margen, 0.0), 0.3))
    if margen > 0:
        mx, my = int(w * margen), int(h * margen)
        w, h = w - 2 * mx, h - 2 * my
        zx, zy = zx + mx, zy + my
    g = geometria_giro(w, h, fraccion, giro, espejo, centro=_centro_en_zona(centro, int(ancho), int(alto), zx, zy))
    return (g["T"] @ _traslacion(-zx, -zy))[:2], g["ancho"], g["alto"]


def encuadre_con_arriba(ux, uy, rx, ry):
    """
    (giro, espejo) de girar_campo que dejan hacia arriba la dirección (ux, uy) de
    la imagen (píxeles, y hacia abajo) y hacia la derecha la (rx, ry), que va a
    90° de la primera: el norte y el oeste para "norte arriba", la altura y el
    acimut para "cénit arriba". `espejo` sale True si la segunda cae al otro
    lado (hay que voltear la imagen antes de girar); giro en grados, horario,
    -180..180. None si las direcciones no valen.
    """
    if not all(math.isfinite(v) for v in (ux, uy, rx, ry)) or (ux == 0 and uy == 0):
        return None
    espejo = ux * ry - uy * rx < 0
    if espejo:
        ux = -ux
    # (ux, uy) cae a atan2(ux, -uy) en horario desde arriba: se deshace
    return round((-math.degrees(math.atan2(ux, -uy)) + 180.0) % 360.0 - 180.0, 1), espejo


# Encuadre de la vista previa del apilado y del JPG de la galería ([Dispositivos]
# de config.ini), además del zoom: giro_preview (grados en horario) y
# espejo_preview son el encuadre a mano; con norte_arriba = true mandan el giro
# y el espejo que dejan el norte arriba y el este a la izquierda, que el
# apilador calcula con el plate solving de la referencia y guarda en la
# cabecera del FITS apilado (NORTEGIR, NORTEESP).

def leer_norte(header):
    """(giro, espejo) que dejan el norte arriba, de la cabecera del FITS apilado; None si no los lleva."""
    try:
        if header is None or "NORTEGIR" not in header:
            return None
        return float(header["NORTEGIR"]), bool(header.get("NORTEESP", False))
    except Exception:
        return None


def encuadre_vista(config=None, header=None):
    """
    Encuadre de la vista previa según config.ini: dict con fraccion, giro,
    espejo y norte ("auto" si se ha aplicado el norte arriba de la cabecera,
    "pendiente" si está pedido pero el FITS aún no lo lleva, "" si es a mano).
    """
    if config is None:
        config = configparser.ConfigParser()
        config.read("config.ini")
    d = config["Dispositivos"] if "Dispositivos" in config else {}
    try:
        giro = float(d.get("giro_preview", 0) or 0) % 360.0
    except ValueError:
        giro = 0.0
    espejo = str(d.get("espejo_preview", "false")).strip().lower() == "true"
    norte = ""
    if str(d.get("norte_arriba", "false")).strip().lower() == "true":
        n = leer_norte(header)
        norte = "auto" if n else "pendiente"
        if n:
            giro, espejo = n
    return {"fraccion": fraccion_campo(config), "giro": giro, "espejo": espejo, "norte": norte}


def zona_automatica(cubo, margen=12):
    """
    Zona útil de un apilado que no trae ZONA* en la cabecera (guardado antes de
    que existiera): el mayor rectángulo sin bordes negros de la alineación. La
    rotación de campo deja cuñas diagonales en las esquinas, así que no basta
    con quitar filas y columnas enteras: se parte de una máscara de "hay
    imagen" (a 1/8 de resolución, > 0.8 del cielo) y se encoge el rectángulo por
    el lado menos cubierto hasta que queda entero dentro, como zona_comun del
    apilado. Devuelve (x0, y0, ancho, alto) o None si no hay nada que recortar.
    """
    canales_primero = cubo.ndim == 3 and cubo.shape[0] == 3 and cubo.shape[-1] != 3
    lum = cubo.mean(axis=0) if canales_primero else (cubo.mean(axis=2) if cubo.ndim == 3 else cubo)
    h, w = lum.shape
    f = max(1, min(h, w) // 376)
    peq = cv2.resize(np.asarray(lum, dtype=np.float32), (max(8, w // f), max(8, h // f)), interpolation=cv2.INTER_AREA)
    peq = cv2.medianBlur(peq, 5)                      # sin estrellas ni ruido
    valido = peq > 0.8 * float(np.median(peq))
    if valido.all():
        return None
    fil, col = np.nonzero(valido)
    if len(fil) == 0:
        return None
    y0, y1, x0, x1 = int(fil.min()), int(fil.max()) + 1, int(col.min()), int(col.max()) + 1
    while y1 - y0 > 2 and x1 - x0 > 2:
        sub = valido[y0:y1, x0:x1]
        if sub.all():
            break
        bordes = {"arriba": sub[0].mean(), "abajo": sub[-1].mean(), "izq": sub[:, 0].mean(), "der": sub[:, -1].mean()}
        peor = min(bordes, key=bordes.get)
        if peor == "arriba": y0 += 1
        elif peor == "abajo": y1 -= 1
        elif peor == "izq": x0 += 1
        else: x1 -= 1
    X0, Y0 = x0 * f, y0 * f
    X1, Y1 = min(w, x1 * f), min(h, y1 * f)
    if X0 > 0: X0 += margen
    if Y0 > 0: Y0 += margen
    if X1 < w: X1 -= margen
    if Y1 < h: Y1 -= margen
    if X1 - X0 < w // 2 or Y1 - Y0 < h // 2:
        return None
    if (X0, Y0, X1, Y1) == (0, 0, w, h):
        return None
    return (int(X0), int(Y0), int(X1 - X0), int(Y1 - Y0))


def leer_zona(header):
    """Zona cubierta por todas las fotos, guardada en la cabecera del FITS apilado
    (ZONAX0, ZONAY0, ZONAW, ZONAH); None si el FITS no la lleva."""
    try:
        if header is None or "ZONAW" not in header:
            return None
        return (int(header["ZONAX0"]), int(header["ZONAY0"]), int(header["ZONAW"]), int(header["ZONAH"]))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  1. Gradiente de fondo (espacio lineal)
# ─────────────────────────────────────────────────────────────────────────────

def _rellenar_huecos(malla):
    """Rellena los NaN de una malla (n, m, 3) con la media de sus vecinos válidos, por oleadas."""
    malla = malla.copy()
    for __ in range(malla.shape[0] + malla.shape[1]):
        nan = np.isnan(malla[..., 0])
        if not nan.any():
            break
        ok = (~nan).astype(np.float32)
        suma = cv2.blur(np.where(nan[..., None], 0, malla).astype(np.float32), (3, 3), borderType=cv2.BORDER_CONSTANT)
        cuenta = cv2.blur(ok, (3, 3), borderType=cv2.BORDER_CONSTANT)
        nuevos = nan & (cuenta > 0)
        malla[nuevos] = (suma / np.maximum(cuenta, 1e-6)[..., None])[nuevos]
    if np.isnan(malla).any():
        malla[np.isnan(malla)] = np.nanmean(malla)
    return malla


def quitar_gradiente(rgb, rejilla=12):
    """
    rgb: (H, W, 3) float lineal. Devuelve la imagen con el gradiente de fondo
    restado (se conserva el nivel medio del fondo para no dejar negativos).

    Modelo de fondo: la mediana de cada celda de una rejilla (sin estrellas ni
    bordes negros) suavizada e interpolada, no un polinomio: un polinomio de 2º
    grado no puede seguir un cielo plano por arriba que baja por abajo (viñeteo
    o gradiente asimétrico) y "rebota" dejando una franja clara en un borde.
    Las celdas con objeto se detectan como envolvente inferior (residuo positivo
    frente al modelo, con la dispersión medida solo en los residuos negativos)
    y se rellenan con sus vecinas, así el halo de una galaxia grande no entra
    en el fondo.
    """
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    # Muestras: mediana de cada celda de una rejilla, calculada sobre una
    # versión reducida (rápido) y con los píxeles brillantes excluidos.
    peq = cv2.resize(rgb, (rejilla * 16, rejilla * 16), interpolation=cv2.INTER_AREA)
    lum = peq.mean(axis=2)
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) * 1.4826 + 1e-6
    # excluir estrellas y objeto, y también los bordes negros que deja el alineado
    fondo_ok = (lum < med + 2.5 * mad) & (lum > 0.3 * med)
    ch = peq.shape[0] // rejilla
    malla = np.full((rejilla, rejilla, 3), np.nan, dtype=np.float32)
    for j in range(rejilla):
        for i in range(rejilla):
            celda = peq[j * ch:(j + 1) * ch, i * ch:(i + 1) * ch]
            mask = fondo_ok[j * ch:(j + 1) * ch, i * ch:(i + 1) * ch]
            if mask.sum() < 0.3 * mask.size:
                continue                       # celda dominada por el objeto
            malla[j, i] = np.median(celda[mask], axis=0)
    if np.count_nonzero(~np.isnan(malla[..., 0])) < 10:
        return rgb

    def modelo_de(m):
        return cv2.GaussianBlur(_rellenar_huecos(m), (0, 0), 1.0, borderType=cv2.BORDER_REPLICATE)

    usar = ~np.isnan(malla[..., 0])
    modelo = None
    for __ in range(6):
        modelo = modelo_de(np.where(usar[..., None], malla, np.nan))
        resid = (malla - modelo).mean(axis=2)                          # + = más brillante que el modelo
        negativos = np.abs(resid[usar & (resid < 0)])
        sigma = (np.median(negativos) * 1.4826 if negativos.size >= 5
                 else np.nanmedian(np.abs(resid[usar])) * 1.4826) + 1e-6
        nuevo = usar & ~np.isnan(resid) & (resid <= 2.0 * sigma)
        if nuevo.sum() < 10 or np.array_equal(nuevo, usar):
            break
        usar = nuevo

    # Superficie a resolución completa (los centros de celda quedan en su sitio)
    superficie = cv2.resize(modelo, (w, h), interpolation=cv2.INTER_CUBIC)
    nivel = superficie.reshape(-1, 3).mean(axis=0)   # nivel medio que se conserva
    return rgb - superficie + nivel


def aplanar_fondo(rgb, escala=0.16):
    """
    Aplanado local del fondo (espacio lineal): elimina las sombras de polvo del
    sensor, el viñeteo y restos de gradiente que el polinomio no recoge, sin
    necesitar flats. Modelo de fondo = mediana a gran escala de la imagen con
    estrellas y objeto enmascarados; se resta conservando el nivel medio.
    `escala` es la fracción del lado de la imagen que define el tamaño de la
    mediana: todo lo más pequeño que eso se considera fondo. Con 0,16 sobre
    3008 px son ~480 px: las sombras de polvo (100-250 px) desaparecen y las
    galaxias y nebulosas de tamaño medio se conservan. Nebulosas que llenan el
    campo pierden brillo difuso: para ellas, desactivarlo.
    """
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    # Trabajar a 1/4 de resolución: sobra para un fondo de gran escala
    factor = 4
    peq = cv2.resize(rgb, (max(32, w // factor), max(32, h // factor)), interpolation=cv2.INTER_AREA)
    lum = peq.mean(axis=2)
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) * 1.4826 + 1e-6
    fondo_ok = (lum < med + 2.5 * mad) & (lum > 0.3 * med)   # ni estrellas/objeto ni bordes negros

    # Mediana por bloques de tamaño `escala` (solo con píxeles de fondo), luego
    # suavizado e interpolación: el modelo de fondo no contiene nada más pequeño
    # que un bloque.
    lado = max(8, int(escala * peq.shape[1]))
    nb_y, nb_x = max(2, peq.shape[0] // lado), max(2, peq.shape[1] // lado)
    modelo = np.zeros((nb_y, nb_x, 3), dtype=np.float32)
    for j in range(nb_y):
        for i in range(nb_x):
            y0, y1 = j * peq.shape[0] // nb_y, (j + 1) * peq.shape[0] // nb_y
            x0, x1 = i * peq.shape[1] // nb_x, (i + 1) * peq.shape[1] // nb_x
            celda = peq[y0:y1, x0:x1].reshape(-1, 3)
            ok = fondo_ok[y0:y1, x0:x1].ravel()
            modelo[j, i] = np.median(celda[ok], axis=0) if ok.sum() > 20 else np.nan
    # Rellenar celdas sin fondo (objeto grande) con la media de las válidas
    for c in range(3):
        m = modelo[..., c]
        if np.isnan(m).any():
            m[np.isnan(m)] = np.nanmean(m)
    fondo = cv2.resize(modelo, (peq.shape[1], peq.shape[0]), interpolation=cv2.INTER_CUBIC)
    fondo = cv2.GaussianBlur(fondo, (0, 0), lado / 3.0)
    fondo_grande = cv2.resize(fondo, (w, h), interpolation=cv2.INTER_LINEAR)
    nivel = fondo_grande.reshape(-1, 3).mean(axis=0)
    return rgb - fondo_grande + nivel


# ─────────────────────────────────────────────────────────────────────────────
#  1b. Calibración de color con las estrellas (espacio lineal)
# ─────────────────────────────────────────────────────────────────────────────
#
# Igualar el cielo multiplicando cada canal (neutralizar_fondo, lo que se hacía
# hasta oct-2026) deja el fondo gris, pero ese mismo factor se lo lleva la luz
# de los objetos: su color acaba dependiendo del color del cielo. Con el cielo
# anaranjado de ciudad la estrella media salía un 5-14 % magenta, y con el
# azulado del crepúsculo un 20 % amarilla (M13 del 24-sep-2026). La luz del
# cielo se SUMA a la del objeto: lo correcto es restarla, y equilibrar los
# canales con algo cuyo color se conoce. Aquí, las estrellas del campo: la
# estrella mediana se deja blanca (como el balance de luz de día, porque la
# estrella típica se parece al Sol). Medido en cuatro campos distintos, las
# ganancias salen casi iguales (R x1,3-1,5 y B x1,6-1,9 con la Saturn-C): son
# sobre todo la respuesta del sensor, más la extinción de esa noche.

MIN_ESTRELLAS_COLOR = 25
LADO_MEDIDA_COLOR = 1600        # el color se mide con la imagen reducida a este lado como mucho


def balance_estrellas(imagen, zona=None):
    """
    Balance de color medido en las estrellas: {"r", "b", "n"} con las ganancias
    de rojo y azul (respecto al verde) que dejan blanca la estrella mediana de
    las `n` medidas, o None si no hay bastantes (campo vacío, de día, imagen sin
    color...). imagen: cubo (3, H, W) o (H, W, 3) lineal, a ser posible el campo
    completo y no el recorte de zoom (más estrellas, y el mismo color con
    cualquier zoom); zona: (x0, y0, ancho, alto) cubierta por todas las fotos.

    Fotometría de apertura con el fondo local (un anillo) restado en cada canal,
    así no dependen del cielo ni de su gradiente; solo estrellas aisladas, bien
    por encima del ruido y sin saturar.
    """
    imagen = np.asarray(imagen)
    if imagen.ndim != 3:
        return None
    canales_primero = imagen.shape[0] == 3 and imagen.shape[-1] != 3
    planos = [imagen[c] if canales_primero else imagen[..., c] for c in range(3)]
    if zona:
        x0, y0, ancho, alto = [int(v) for v in zona]
        planos = [p[y0:y0 + alto, x0:x0 + ancho] for p in planos]
    h, w = planos[0].shape
    f = max(1, int(math.ceil(max(h, w) / float(LADO_MEDIDA_COLOR))))
    if f > 1:
        planos = [cv2.resize(np.ascontiguousarray(p, dtype=np.float32), (w // f, h // f), interpolation=cv2.INTER_AREA)
                  for p in planos]
    rgb = np.stack([np.asarray(p, dtype=np.float32) for p in planos], axis=2)
    h, w = rgb.shape[:2]
    r_ap, r_in, r_out = 5, 9, 13
    if min(h, w) < 8 * r_out:
        return None

    lum = cv2.GaussianBlur(rgb.mean(axis=2), (0, 0), 1.5)
    fondo = cv2.medianBlur(cv2.resize(lum, (max(8, w // 4), max(8, h // 4)), interpolation=cv2.INTER_AREA), 5)
    lum -= cv2.resize(cv2.GaussianBlur(fondo, (0, 0), 8), (w, h), interpolation=cv2.INTER_LINEAR)
    sigma = float(np.median(np.abs(lum[::4, ::4]))) * 1.4826
    if sigma <= 0:
        return None
    picos = (lum == cv2.dilate(lum, np.ones((2 * r_out + 1, 2 * r_out + 1), np.uint8))) & (lum > 25.0 * sigma)
    picos[:r_out] = picos[-r_out:] = False
    picos[:, :r_out] = picos[:, -r_out:] = False
    ys, xs = np.nonzero(picos)
    saturado = 0.7 * float(rgb.max())
    yy, xx = np.mgrid[-r_out:r_out + 1, -r_out:r_out + 1]
    radio = np.hypot(xx, yy)
    apertura, anillo = radio <= r_ap, (radio >= r_in) & (radio <= r_out)
    flujos = []
    for i in np.argsort(-lum[ys, xs])[:3000]:
        x, y = int(xs[i]), int(ys[i])
        trozo = rgb[y - r_out:y + r_out + 1, x - r_out:x + r_out + 1]
        if trozo.max() > saturado:
            continue
        flujo = trozo[apertura].sum(axis=0) - np.median(trozo[anillo], axis=0) * apertura.sum()
        if flujo.min() > 0:
            flujos.append(flujo)
    if len(flujos) < MIN_ESTRELLAS_COLOR:
        return None
    flujos = np.array(flujos)
    r = 1.0 / float(np.median(flujos[:, 0] / flujos[:, 1]))
    b = 1.0 / float(np.median(flujos[:, 2] / flujos[:, 1]))
    if not (0.25 <= r <= 4.0 and 0.25 <= b <= 4.0):
        return None
    return {"r": r, "b": b, "n": len(flujos)}


def calibrar_color(rgb, balance):
    """
    rgb: (H, W, 3) float lineal (ya sin gradiente). Resta a cada canal su cielo,
    le aplica la ganancia de `balance` (balance_estrellas) y devuelve los tres
    sobre un mismo fondo gris, del nivel medio que tenía. Sin balance la imagen
    vuelve intacta y el fondo lo iguala el estirado multiplicando, como antes.
    No modifica `rgb`.
    """
    if not balance:
        return rgb
    rgb = np.asarray(rgb, dtype=np.float32)
    cielo = np.array([np.median(rgb[::4, ::4, c]) for c in range(3)], dtype=np.float32)
    ganancias = np.array([balance["r"], 1.0, balance["b"]], dtype=np.float32)
    # El nivel del fondo es el brillo del cielo ya equilibrado, llevado a la escala que tenía la imagen
    nivel = float(cielo.mean())
    equilibrado = float((cielo * ganancias).mean())
    if nivel > 0 and equilibrado > 0:
        ganancias *= nivel / equilibrado
    out = rgb - cielo.reshape(1, 1, 3)
    out *= ganancias.reshape(1, 1, 3)
    out += nivel
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  2. Ruido (espacio lineal, antes de estirar)
# ─────────────────────────────────────────────────────────────────────────────
#
# El ruido de un apilado en color no es grano fino: el debayer y el alineado lo
# dejan en manchas de 2 a 8 px. Un filtro de parecidos (medias no locales, el
# que había hasta oct-2026, aplicado tras el estirado) toma esas manchas por
# detalle: quitaba el grano, dejaba el cielo en mesetas con huecos oscuros,
# borraba las estrellas débiles y recortaba el halo de los objetos en un
# contorno duro. Aquí la imagen se separa en escalas (diferencias de suavizados
# sucesivos, como una transformada à trous) y en cada escala un coeficiente se
# conserva en la medida en que la energía de su entorno supera la del ruido
# (filtro de Wiener local). No hay umbral duro ni máscara de estrellas: lo que
# destaca pasa entero, lo que no se distingue del ruido se atenúa, y los
# gradientes suaves (el residuo) no se tocan.
#
#   - Se hace en lineal porque ahí el ruido es gaussiano y casi uniforme; tras
#     el estirado el del cielo queda mucho más amplificado que el del objeto.
#     Donde hay objeto el ruido de fotones crece con la raíz del nivel, y el
#     umbral sube con él.
#   - El ruido de cada escala no se puede medir en la luminancia: en un campo
#     de la Vía Láctea la dispersión a 4-16 px son estrellas débiles, no ruido.
#     Se mide en los planos de color (canal - luminancia), donde las estrellas
#     casi se cancelan; con canales independientes var(L) = suma var(C) / 6.
#   - El color se limpia más fuerte que la luminancia, pero se conserva donde
#     la luminancia tiene estructura (estrellas, bordes): ahí su ruido no se ve
#     y sin ello las estrellas débiles perdían el color.
#   - En el cielo no hay detalles oscuros: un hueco de la luminancia solo se
#     conserva si tiene algo brillante al lado (ver ganancia_huecos).

# Dos pasadas de caja de ancho n suavizan como una gaussiana de ~0,41 n px, y
# cuestan lo mismo con cualquier n: escalas de 1, 2, 4, 8 y 16 px
ANCHOS_ESCALA = (3, 5, 9, 19, 39)
ANCHOS_ENERGIA = (3, 5)                 # entorno de la energía en las escalas finas; en las demás, a resolución reducida
REDUCCION_ESCALA = (1, 1, 2, 4, 8)
PESO_LUMINANCIA = (1.0, 1.0, 0.9, 0.7, 0.0)     # la escala de 16 px de la luminancia no se toca: ahí ya es cielo real
RAZON_RUIDO_MAX = 0.7                   # el ruido de una escala no pasa de 0,7 veces el de la anterior: lo demás es señal


def _caja2(img, n):
    return cv2.blur(cv2.blur(img, (n, n), borderType=cv2.BORDER_REFLECT), (n, n), borderType=cv2.BORDER_REFLECT)


def _sigma_mad(w, paso=3):
    v = w[::paso, ::paso]
    return float(np.median(np.abs(v - np.median(v)))) * 1.4826


def reducir_ruido(rgb, fuerza, progreso=None):
    """
    rgb: (H, W, 3) float lineal (ADU), antes de estirar. fuerza 0..1: con 0,15 se
    queda algo de grano y con 0,4 o más se quita todo lo que no destaca del
    ruido; a partir de ahí solo sube el umbral (de 1 a 2,5 sigmas). El ruido se
    mide en la propia imagen, así que se adapta solo al número de fotos y a la
    resolución. progreso(fraccion): opcional. Devuelve float32 (H, W, 3) lineal.
    """
    fuerza = float(np.clip(fuerza, 0, 1))
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    if fuerza <= 0 or min(h, w) < 64:
        return rgb
    k = 1.0 + 1.5 * fuerza
    cantidad = min(1.0, 0.6 + fuerza)
    k_color = 1.5 * k + 0.5
    cantidad_color = min(1.0, 0.7 + fuerza)

    def tam(f):
        return (max(8, w // f), max(8, h // f)) if f > 1 else (w, h)

    cL = (rgb[..., 0] + rgb[..., 1] + rgb[..., 2]) * np.float32(1.0 / 3.0)
    cR = rgb[..., 0] - cL
    cB = rgb[..., 2] - cL
    # Nivel local respecto al cielo (de la imagen a 1/8): la varianza del ruido de fotones va con él
    peq = cv2.GaussianBlur(cv2.resize(cL, tam(8), interpolation=cv2.INTER_AREA), (0, 0), 2.0, borderType=cv2.BORDER_REFLECT)
    cielo = float(np.median(peq))
    nivel = {8: np.clip(peq / cielo, 1.0, 25.0) if cielo > 0 else np.ones_like(peq)}
    for f in (1, 2, 4):
        nivel[f] = cv2.resize(nivel[8], tam(f), interpolation=cv2.INTER_LINEAR)

    def energia(cuadrados, j, ancha=False):
        """Media local de los cuadrados de los coeficientes, a la resolución de la escala."""
        f = REDUCCION_ESCALA[j]
        if f > 1:
            return _caja2(cv2.resize(cuadrados, tam(f), interpolation=cv2.INTER_AREA), 5 if ancha else 3)
        return _caja2(cuadrados, 2 * ANCHOS_ENERGIA[j] + 1 if ancha else ANCHOS_ENERGIA[j])

    def ganancia(wj, j, sig, umbral):
        """
        Wiener local, a la resolución de la escala: 1 donde la energía del entorno
        supera de largo la del ruido (umbral² sigma² por el nivel local), 0 donde
        es solo ruido.
        """
        r = nivel[REDUCCION_ESCALA[j]] / np.maximum(energia(cv2.multiply(wj, wj), j), 1e-20)
        r *= -((umbral * sig) ** 2)
        r += 1.0
        return np.clip(r, 0.0, 1.0, out=r)

    def ganancia_huecos(wj, j, sig, umbral, g):
        """
        Ganancia de los coeficientes NEGATIVOS de la luminancia. En el cielo no hay
        detalles oscuros: un hueco es el borde de algo brillante (el anillo
        negativo de una estrella, que hay que conservar o el fondo subiría) o es
        ruido, y un hueco de ruido que se salva queda como una mancha negra en un
        cielo ya liso. Se conserva solo si además hay energía POSITIVA en un
        entorno más ancho (x2: la mitad de la de un ruido simétrico). Sobre un
        objeto (nivel por encima del cielo) los huecos son bandas de polvo y vale
        la ganancia normal.
        """
        f = REDUCCION_ESCALA[j]
        r = nivel[f] / np.maximum(energia(np.square(np.maximum(wj, 0)), j, ancha=True) * 2.0, 1e-20)
        r *= -((umbral * sig) ** 2)
        r += 1.0
        np.clip(r, 0.0, 1.0, out=r)
        np.minimum(r, g, out=r)
        sobre_objeto = np.clip((nivel[f] - 1.05) * 4.0, 0.0, 1.0)
        r += sobre_objeto * (g - r)
        return r

    def sumar(salida, wj, ganancia, cuanto, huecos=None):
        """salida += wj * (1 - cuanto * (1 - ganancia)); `ganancia` se pisa. `huecos`: la de los coeficientes negativos."""
        if ganancia.shape != wj.shape:
            ganancia = cv2.resize(ganancia, (w, h), interpolation=cv2.INTER_LINEAR)
        if huecos is not None:
            if huecos.shape != wj.shape:
                huecos = cv2.resize(huecos, (w, h), interpolation=cv2.INTER_LINEAR)
            np.copyto(ganancia, huecos, where=wj < 0)
        ganancia *= cuanto
        ganancia += 1.0 - cuanto
        ganancia *= wj
        salida += ganancia

    oL, oR, oB = np.zeros_like(cL), np.zeros_like(cL), np.zeros_like(cL)
    anterior = None
    for j, n in enumerate(ANCHOS_ESCALA):
        sL, sR, sB = _caja2(cL, n), _caja2(cR, n), _caja2(cB, n)
        wL, wR, wB = cL - sL, cR - sR, cB - sB
        cL, cR, cB = sL, sR, sB
        # Ruido de la escala: R-L, G-L (= -(R-L) - (B-L)) y B-L, y de ellos el de la luminancia
        sig_r, sig_g, sig_b = _sigma_mad(wR), _sigma_mad(wR[::3, ::3] + wB[::3, ::3], 1), _sigma_mad(wB)
        sig_l = math.sqrt((sig_r ** 2 + sig_g ** 2 + sig_b ** 2) / 6.0)
        sig_l_directo = _sigma_mad(wL)
        if sig_l < 0.3 * sig_l_directo:
            sig_l = sig_l_directo            # imagen sin color (gris): no hay planos de color donde medir
        if anterior is not None:
            sig_l, sig_r, sig_b = (min(s, RAZON_RUIDO_MAX * a) for s, a in zip((sig_l, sig_r, sig_b), anterior))
        anterior = (sig_l, sig_r, sig_b)

        guia = ganancia(wL, j, sig_l, 1.5)                # dónde tiene estructura la luminancia
        if PESO_LUMINANCIA[j] > 0:
            umbral = k * PESO_LUMINANCIA[j]
            g = ganancia(wL, j, sig_l, umbral)
            sumar(oL, wL, g, cantidad, huecos=ganancia_huecos(wL, j, sig_l, umbral, g))
        else:
            oL += wL
        for wC, oC, sig in ((wR, oR, sig_r), (wB, oB, sig_b)):
            g = ganancia(wC, j, sig, k_color)
            np.maximum(g, guia, out=g)
            sumar(oC, wC, g, cantidad_color)
        log.debug("ruido escala %d: sigma L %.3f, R-L %.3f, B-L %.3f", j + 1, sig_l, sig_r, sig_b)
        if progreso:
            try:
                progreso((j + 1) / len(ANCHOS_ESCALA))
            except Exception:
                pass
    oL += cL
    oR += cR
    oB += cB
    out = np.empty_like(rgb)
    out[..., 0] = oL + oR
    out[..., 1] = oL - oR - oB
    out[..., 2] = oL + oB
    return out


def suavizar_fondo_sin_estrellas(rgb, fuerza, escala_imagen=1.0, sigma_px=128.0):
    """
    rgb: (H, W, 3) float lineal (ADU), ya con el ruido reducido. Para la imagen SIN
    estrellas (estrellas_aparte.py): al quitarlas aparece un moteado de gran
    escala (manchas de color y huecos oscuros de 50-100 px, medido en la Pacman
    el 24-oct-2026) que en la foto normal queda tapado por el campo de estrellas
    y que reducir_ruido, que llega a 16 px, no toca. Dos cosas, solo donde no
    hay señal (el peso cae con (L - cielo)/sigma, así una nebulosa conserva su
    color y su forma):
      - el COLOR (cada canal respecto a la luminancia) se suaviza con una
        gaussiana de `sigma_px` píxeles: el fondo queda de un tinte uniforme;
      - los HUECOS de la luminancia (lo que queda por debajo de su media a gran
        escala) se rellenan: en el cielo no hay detalles oscuros, un hueco es
        ruido o una sombra de polvo; lo que sobresale (nebulosidad débil) se deja.
    fuerza 0..1 (la de la reducción de ruido).
    """
    fuerza = float(np.clip(fuerza, 0, 1))
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    if fuerza <= 0 or min(h, w) < 64:
        return rgb
    L = rgb.mean(axis=2)
    m, sigma = estadistica_fondo(L)
    if sigma <= 0:
        return rgb
    s = max(2.0, sigma_px * float(escala_imagen))
    pw, ph = max(16, w // 4), max(16, h // 4)

    def gran_escala(img):
        # A 1/4 de resolución: la gaussiana grande sale 16 veces más barata y a esta escala no hay detalle fino
        peq = cv2.resize(img, (pw, ph), interpolation=cv2.INTER_AREA)
        peq = cv2.GaussianBlur(peq, (0, 0), s / 4.0, borderType=cv2.BORDER_REFLECT)
        return cv2.resize(peq, (w, h), interpolation=cv2.INTER_LINEAR)

    # Peso: 1 en el cielo, 0 a partir de unas sigmas de señal (menos sigmas cuanta más fuerza)
    umbral = (2.0 + 6.0 * (1.0 - fuerza)) * sigma
    senal = np.clip((L - m) / umbral, 0.0, None)
    peso = (fuerza * np.exp(-senal * senal)).astype(np.float32)
    # Color
    cociente = rgb / np.maximum(L, 1e-3)[..., None]
    suave = gran_escala(cociente)
    cociente += (suave - cociente) * peso[..., None]
    # Huecos de la luminancia, a dos escalas: la grande (sombras de polvo, viñeteo) y
    # una intermedia (el moteado de 30-100 px que el contraste local, CLAHE, convierte
    # en celdas oscuras al guardar a resolución completa)
    L2 = L.astype(np.float32)
    for s_hueco in (s / 4.0, s):
        B = gran_escala(L2) if s_hueco == s else cv2.GaussianBlur(L2, (0, 0), s_hueco, borderType=cv2.BORDER_REFLECT)
        hueco = np.minimum(L2 - B, 0.0)
        L2 = (L2 - hueco * peso).astype(np.float32)
    return (cociente * L2[..., None]).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
#  3. Estirado adaptativo multiescala (de lineal a no lineal)
# ─────────────────────────────────────────────────────────────────────────────
#
# Una curva global (STF, MTF, gamma) no puede a la vez sacar la señal débil y
# conservar lo brillante: para subir el fondo hay que llevar estrellas y
# núcleos a saturación (engordan, pierden detalle y color). Aquí el estirado no
# es una curva sino un campo de ganancia que varía por la imagen:
#
#   1. L = luminancia. B = componente de gran escala de L (fondo de cielo y
#      brillo difuso de los objetos; sin estrellas ni estructuras pequeñas).
#   2. S(B) = B estirado con una curva (gamma por defecto; asinh o la MTF de
#      PixInsight como opción) con sombras y medios calculados de la
#      estadística robusta del fondo (mediana y MAD) y el blanco en el
#      percentil 99,8 %, como el STF clásico: así lo extenso (nebulosas) tiene
#      la misma pegada que con él.
#   3. El detalle fino (L - B) se estira con la misma familia de curva pero con
#      el blanco desplazado hacia 1.0 según `compresion`:
#          L' = S(B) + recuperacion * (S_d(L) - S_d(B))
#      compresion = 0: S_d = S, es decir, el STF clásico sobre la luminancia
#      (las estrellas saturan igual); compresion = 1: el blanco del detalle es
#      el máximo real y nada se recorta (perfil y color de las estrellas
#      intactos). Como la curva es cóncava, donde B ya es alto (núcleos,
#      cúmulos) el detalle recibe menos ganancia: esa es la parte "local".
#   4. Color: cada canal recibe la misma ganancia que la luminancia sobre lo
#      que tiene por encima del cielo, RGB' = S(m) + (RGB - m) * (L' - S(m)) /
#      (L - m), como hace el STF canal a canal; lo que rebasa 1.0 se desatura
#      hacia L' en vez de recortarse canal a canal.
#
# Parámetros ([Procesado] de config.ini, con defectos por estilo):
#   fondo_objetivo  brillo del fondo de cielo en la imagen final (0.10-0.20)
#   agresividad     0..1: cuánto se acerca el punto negro al pico del fondo
#   compresion      0..1: protección de lo brillante (0 = como el STF clásico,
#                   1 = nada se recorta)
#   recuperacion    peso del detalle fino reinyectado (1 = contraste del STF)
#   escala          tamaño en px (a resolución completa) por encima del cual una
#                   estructura se considera fondo (1024 por defecto, como MAS)
#   curva           gamma (por defecto), asinh o mtf: familia de la curva del fondo

ESCALA_FONDO_PX = 1024.0


# ─────────────────────────────────────────────────────────────────────────────
#  3b. Nitidez: deconvolución Richardson-Lucy
# ─────────────────────────────────────────────────────────────────────────────

# Tanteo del 14-oct-2026 con cuatro apilados reales (M13, Pacman, Triángulo,
# Pléyades; comparaciones/deconvolucion_2026-10-14): Richardson-Lucy por canal
# con PSF gaussiana medida en las estrellas de la propia foto, 8 iteraciones,
# baja la FWHM de 4,4 a 2,9 px y resuelve M13 en estrellas sin subir el ruido
# del cielo. Lo que NO funcionó: Wiener y la máscara de desenfoque (anillan),
# 20 iteraciones o una PSF empírica (anillo oscuro alrededor de las estrellas
# medias y brillantes). Las estrellas brillantes se dejan como están (su perfil
# no es la PSF de las medias y es donde salen los anillos) y el cielo también
# (solo amplificaría el ruido).
ITERACIONES_NITIDEZ_MAX = 12          # nitidez 1,0 = 12 iteraciones; 0,65 = las 8 del tanteo
FACTOR_PSF_NITIDEZ = 0.85             # PSF algo más estrecha que la FWHM medida (menos anillos)
UMBRAL_ESTRELLA_PROTEGIDA = 12000.0   # ADU sobre el cielo: por encima, la estrella no se toca


def _estrellas_medibles(plano, maximo=80):
    """(y, x) de hasta `maximo` estrellas brillantes sin saturar, aisladas y lejos del borde."""
    img = np.asarray(plano, dtype=np.float32)
    suav = cv2.GaussianBlur(img, (0, 0), 1.2)
    sub = suav[::8, ::8]
    fondo = float(np.median(sub))
    mad = float(np.median(np.abs(sub - fondo))) * 1.4826 + 1e-3
    dil = cv2.dilate(suav, np.ones((15, 15), np.uint8))
    cand = np.argwhere((suav == dil) & (suav - fondo > 20.0 * mad) & (img < 50000))
    h, w = img.shape
    cand = [(float(suav[y, x]), int(y), int(x)) for y, x in cand if 20 < y < h - 20 and 20 < x < w - 20]
    cand.sort(reverse=True)
    return [(y, x) for __, y, x in cand[:maximo]]


def _fwhm_momentos(p, x, y, radio=7):
    """FWHM (px) por el segundo momento de la luz alrededor de (x, y), con el fondo del borde restado (como enfoque.fwhm)."""
    y0, y1, x0, x1 = max(0, y - radio), min(p.shape[0], y + radio + 1), max(0, x - radio), min(p.shape[1], x + radio + 1)
    c = p[y0:y1, x0:x1]
    if c.size < 9:
        return float("nan")
    borde = np.concatenate([c[0], c[-1], c[:, 0], c[:, -1]])
    w = np.clip(c - float(np.median(borde)), 0, None)
    if w.sum() <= 0:
        return float("nan")
    yy, xx = np.mgrid[y0:y1, x0:x1]
    cx, cy = (xx * w).sum() / w.sum(), (yy * w).sum() / w.sum()
    s2 = (((xx - cx) ** 2 + (yy - cy) ** 2) * w).sum() / w.sum() / 2.0
    return float(2.355 * np.sqrt(max(s2, 1e-3)))


def fwhm_canales(rgb, maximo=80):
    """
    FWHM mediana (px) de cada canal, medida en las estrellas encontradas en el
    verde; NaN donde no se pueda medir. En un doblete R y B salen más anchos que
    G, y cada canal se deconvoluciona con su propia PSF.
    """
    rgb = np.asarray(rgb, dtype=np.float32)
    cand = _estrellas_medibles(rgb[..., 1], maximo)
    if len(cand) < 5:
        return [float("nan")] * 3
    salida = []
    for c in range(3):
        anchos = [_fwhm_momentos(rgb[..., c], x, y) for y, x in cand]
        anchos = [a for a in anchos if np.isfinite(a)]
        salida.append(float(np.median(anchos)) if anchos else float("nan"))
    return salida


def _mascara_nitidez(rgb, fwhm_px, escala=1.0):
    """
    Dónde se aplica la deconvolución (0..1): la señal por encima de 3 sigmas del
    cielo (fuera de ahí solo se amplificaría el ruido) menos las estrellas
    brillantes y su entorno (3 FWHM). El umbral de brillo baja con la escala:
    a media resolución el pico de una estrella queda repartido en 4 píxeles.
    """
    L = rgb.mean(axis=2)
    Ls = cv2.GaussianBlur(L, (0, 0), 1.5)
    sub = Ls[::4, ::4]
    med = float(np.median(sub))
    s = float(np.median(np.abs(sub - med))) * 1.4826 + 1e-6
    senal = np.clip((Ls - med) / (3.0 * s) - 1.0, 0, 1).astype(np.float32)   # 0 en 3 s, 1 en 6 s
    senal = cv2.dilate(senal, np.ones((5, 5), np.uint8))
    senal = cv2.GaussianBlur(senal, (0, 0), 2.0)
    pico = rgb.max(axis=2)
    umbral = UMBRAL_ESTRELLA_PROTEGIDA * float(np.sqrt(max(escala, 0.05)))
    brillantes = (pico - float(np.median(pico[::8, ::8])) > umbral).astype(np.uint8)
    r = max(3, int(round(3.0 * fwhm_px)))
    brillantes = cv2.dilate(brillantes, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    brillantes = cv2.GaussianBlur(brillantes.astype(np.float32), (0, 0), max(1.0, fwhm_px / 2.0))
    return senal * (1.0 - brillantes)


def deconvolucionar(rgb, fuerza, progreso=None, escala=1.0):
    """
    rgb: (H, W, 3) float lineal (ADU), ya sin gradiente y con el ruido reducido.
    fuerza 0..1: número de iteraciones de Richardson-Lucy (hasta ITERACIONES_NITIDEZ_MAX).
    La PSF es una gaussiana por canal con la FWHM medida en las estrellas de la
    propia imagen (× FACTOR_PSF_NITIDEZ); así se adapta sola al foco de la noche
    y a la resolución (la vista previa va a la mitad). progreso(fraccion): opcional.
    Devuelve float32 (H, W, 3) lineal.
    """
    fuerza = float(np.clip(fuerza, 0, 1))
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    iteraciones = int(round(ITERACIONES_NITIDEZ_MAX * fuerza))
    if iteraciones <= 0 or min(h, w) < 64:
        return rgb
    fwhm = fwhm_canales(rgb)
    if not all(np.isfinite(f) and f > 0 for f in fwhm):
        log.warning("nitidez: no hay estrellas que medir; no se deconvoluciona")
        return rgb
    log.debug("nitidez: %d iteraciones, FWHM R/G/B %.2f/%.2f/%.2f px", iteraciones, *fwhm)
    mascara = _mascara_nitidez(rgb, float(np.mean(fwhm)), escala)[..., None]
    salida = np.empty_like(rgb)
    paso = 0
    total = 3 * iteraciones
    for c in range(3):
        y = rgb[..., c]
        piso = float(np.percentile(y[::8, ::8], 1.0))
        y = np.clip(y - piso, 1e-3, None) + 1e-3          # RL exige datos positivos
        sigma = max(fwhm[c] * FACTOR_PSF_NITIDEZ, 1.0) / 2.355
        x = y.copy()
        for __ in range(iteraciones):
            est = cv2.GaussianBlur(x, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
            # cociente acotado: un píxel aislado de ruido no dispara la corrección
            ratio = np.clip(y / np.maximum(est, 1e-6), 0.2, 5.0)
            x *= cv2.GaussianBlur(ratio, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
            paso += 1
            if progreso:
                try:
                    progreso(paso / total)
                except Exception:
                    pass
        salida[..., c] = x + piso
    return (rgb + mascara * (salida - rgb)).astype(np.float32)


def mtf(x, mt):
    """Midtones transfer function de PixInsight: x en [0, 1], mt = balance de medios."""
    x = np.clip(np.asarray(x, dtype=np.float32), 0, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = (mt - 1.0) * x / ((2.0 * mt - 1.0) * x - mt)
    y = np.where(np.isfinite(y), y, 0)
    return np.clip(y, 0, 1).astype(np.float32)


def asinh_norm(x, a):
    """Estirado asinh normalizado: 0 -> 0, 1 -> 1; `a` pequeño = más agresivo en las sombras."""
    x = np.clip(np.asarray(x, dtype=np.float32), 0, 1)
    return (np.arcsinh(x / a) / np.arcsinh(1.0 / a)).astype(np.float32)


class CurvaFondo:
    """
    Curva S que lleva el fondo de cielo (mediana m) a `fondo_objetivo`, con las
    sombras en c0 = m - k*sigma (k según agresividad, como la AutoSTF de
    PixInsight) y el blanco en 1.0. Las tres familias pasan por los mismos
    puntos pero difieren en la pendiente en el cielo (= contraste de lo débil)
    y en lo que comprimen por arriba:
      gamma (por defecto): x**gamma, la del STF antiguo pero sin recorte por
            arriba; la más suave de las tres en el cielo (pendiente ~0.23 de
            la secante) y la que más comprime lo brillante.
      asinh: logarítmica en lo brillante; ~3 veces más pendiente en el cielo.
      mtf:  la MTF de PixInsight; la más brusca (con un fondo limpio, mt ~ 0.001).
    """

    def __init__(self, m, sigma, fondo_objetivo=0.13, agresividad=0.7, tipo="gamma", blanco=1.0):
        k = 4.0 - 2.5 * float(np.clip(agresividad, 0, 1))     # 0 -> 4 sigma, 1 -> 1.5 sigma
        self.c0 = max(0.0, m - k * sigma)
        # Punto blanco: con 1.0 la gamma sale muy baja (~0.2) y aplasta todo lo
        # extenso (una nebulosa a 10x el cielo se queda en 0.4). El STF clásico
        # lo ponía en el percentil 99.8 % de la imagen (gamma ~0.35) y por eso
        # las nebulosas tenían más pegada; aquí se recibe ese blanco y lo que
        # queda por encima lo comprime la rodilla del detalle, no la curva.
        self.blanco = float(min(1.0, max(blanco, m + 20.0 * sigma, self.c0 + 1e-4)))
        self.tipo = tipo if tipo in ("gamma", "asinh", "mtf") else "gamma"
        x_med = min(max((m - self.c0) / (self.blanco - self.c0), 1e-6), 1 - 1e-6)
        objetivo = float(np.clip(fondo_objetivo, 0.02, 0.6))
        if self.tipo == "gamma":
            self.param = float(np.log(objetivo) / np.log(x_med))
        elif self.tipo == "mtf":
            # mt tal que mtf(x_med, mt) = objetivo: la MTF es involutiva en ese sentido
            self.param = float(mtf(np.float32(x_med), objetivo))
        else:
            # a por bisección en log(a): asinh_norm(x_med, a) crece al bajar a
            lo, hi = -18.0, 2.0
            for __ in range(50):
                mid = 0.5 * (lo + hi)
                if float(asinh_norm(np.float32(x_med), np.exp(mid))) > objetivo:
                    lo = mid
                else:
                    hi = mid
            self.param = float(np.exp(0.5 * (lo + hi)))

    def __call__(self, v):
        x = (np.asarray(v, dtype=np.float32) - self.c0) / (self.blanco - self.c0)
        if self.tipo == "gamma":
            return np.power(np.clip(x, 0, 1), self.param).astype(np.float32)
        return mtf(x, self.param) if self.tipo == "mtf" else asinh_norm(x, self.param)

    def derivada(self, v):
        """Pendiente dS/dv (ganancia local de la curva) en el valor lineal v."""
        x = np.clip((np.asarray(v, dtype=np.float32) - self.c0) / (self.blanco - self.c0), 1e-9, 1)
        if self.tipo == "gamma":
            d = self.param * np.power(x, self.param - 1.0)
        elif self.tipo == "mtf":
            mt = self.param
            d = mt * (1.0 - mt) / np.square((2.0 * mt - 1.0) * x - mt)
        else:
            a = self.param
            d = 1.0 / (np.arcsinh(1.0 / a) * np.sqrt(x * x + a * a))
        return (d / (self.blanco - self.c0)).astype(np.float32)


def estadistica_fondo(L):
    """Mediana y sigma robusto (1.4826*MAD) de la luminancia, sobre un submuestreo."""
    sub = L[::4, ::4]
    m = float(np.median(sub))
    sigma = float(np.median(np.abs(sub - m))) * 1.4826 + 1e-7
    return m, sigma


def fondo_gran_escala(L, m, sigma, escala_px, factor=8):
    """
    Componente de gran escala B de la luminancia: todo lo más pequeño que
    `escala_px` (en píxeles de L) queda fuera. Se trabaja a 1/factor de
    resolución con una mediana de tamaño escala_px/factor: estrellas, cúmulos,
    galaxias medianas y sus halos desaparecen del fondo (no se esparcen como
    haría un desenfoque). Para poder usar una mediana grande (OpenCV solo la
    tiene en 8 bits) el fondo se cuantiza entre m - 5 sigma y ~3 veces el cielo:
    lo que está por encima cuenta como "brillante" y la mediana lo ignora
    mientras no llene media ventana. Devuelve B (H, W) > 0.
    """
    h, w = L.shape
    pw, ph = max(16, w // factor), max(16, h // factor)
    peq = cv2.resize(L, (pw, ph), interpolation=cv2.INTER_AREA)
    lo = max(0.0, m - 5.0 * sigma)
    hi = m + max(10.0 * sigma, 2.0 * m)
    u8 = np.clip((peq - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    k = int(max(3, round(escala_px / factor)))
    k = min(k, 2 * min(pw, ph) - 1) | 1          # impar (lo exige medianBlur) y acotado a la imagen reducida
    # Bordes por reflexión: la mediana de OpenCV rellena con ceros lo que queda
    # fuera de la imagen y, con una ventana tan grande, el fondo se hundía en las
    # esquinas (un arco oscuro de un cuarto de círculo tras el desenfoque).
    s = max(1.0, k / 4.0)
    borde = min(k // 2 + int(3 * s), min(pw, ph) - 1)
    u8 = cv2.copyMakeBorder(u8, borde, borde, borde, borde, cv2.BORDER_REFLECT_101)
    med = cv2.medianBlur(u8, k).astype(np.float32) / 255.0 * (hi - lo) + lo
    med = cv2.GaussianBlur(med, (0, 0), s, borderType=cv2.BORDER_REPLICATE)
    med = med[borde:borde + ph, borde:borde + pw]
    B = cv2.resize(med, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.maximum(B, 1e-6).astype(np.float32)


def sigma_fondo(rgb):
    """Sigma del fondo de cielo tal como lo mide estirar_multiescala (luminancia neutralizada, 0..1)."""
    L = np.clip(neutralizar_fondo(np.asarray(rgb, dtype=np.float32)) / 65535.0, 0, None).mean(axis=2)
    return estadistica_fondo(L)[1]


def estirar_multiescala(rgb, ajustes=None, escala_imagen=1.0, progreso=None, sigma=None, devolver_curvas=False):
    """
    rgb: (H, W, 3) float lineal en ADU (0..65535), ya sin gradiente.
    escala_imagen: 0.5 si la imagen viene a media resolución (la escala de fondo
    en px se reduce en proporción). progreso(fraccion): opcional, 0..1.
    sigma: el del fondo antes de reducir el ruido (sigma_fondo); el punto negro
    va a k sigmas del cielo y, medido en la imagen ya limpia, se pegaría a él.
    Devuelve float32 (H, W, 3) en [0, 1], no lineal. Con devolver_curvas, además
    un dict con la mediana del cielo (m, en [0, 1]), sigma y la curva del detalle
    (S_detalle), con los que estirar_capa_estrellas estira las estrellas igual
    que las habría estirado esta función si siguieran en la foto.
    """
    aj = ajustes or {}
    fondo_objetivo = float(aj.get("fondo_objetivo", 0.13))
    agresividad = float(aj.get("agresividad", 0.7))
    compresion = float(aj.get("compresion", 0.3))
    recuperacion = float(aj.get("recuperacion", 1.0))
    curva = str(aj.get("curva", "gamma")).lower()
    escala_px = float(aj.get("escala", ESCALA_FONDO_PX)) * float(escala_imagen)

    def avisar(f):
        if progreso:
            try:
                progreso(f)
            except Exception:
                pass

    rgb = neutralizar_fondo(np.asarray(rgb, dtype=np.float32))
    rgb = np.clip(rgb / 65535.0, 0, None)
    L = rgb.mean(axis=2)
    m, sigma_medido = estadistica_fondo(L)
    sigma = float(sigma) if sigma else sigma_medido
    blanco = float(np.percentile(L[::4, ::4], 99.82))        # el mismo blanco que el STF clásico
    S_curva = CurvaFondo(m, sigma, fondo_objetivo, agresividad, curva, blanco=blanco)
    avisar(0.2)

    B = fondo_gran_escala(L, m, sigma, escala_px)
    avisar(0.5)

    # Fondo estirado con la curva (blanco del STF) y detalle fino con la MISMA
    # familia de curva pero con el blanco desplazado hacia 1.0 según
    # `compresion`: con 0 es exactamente el STF clásico sobre la luminancia
    # (las estrellas saturan igual); con 1 el blanco del detalle es el máximo
    # real y nada se recorta (perfil y color de las estrellas intactos). Al ser
    # una curva cóncava, el detalle se frena desde el principio como en el STF
    # (una ganancia lineal con rodilla subía demasiado lo de brillo medio).
    # Donde el fondo B es alto (núcleos, cúmulos) la curva ya da menos a lo que
    # va por encima: esa es la ganancia local.
    S = S_curva(B)
    c = float(np.clip(compresion, 0, 1))
    blanco_detalle = float(S_curva.blanco ** (1.0 - c))          # entre el blanco del STF y 1.0
    S_detalle = CurvaFondo(m, sigma, fondo_objetivo, agresividad, curva, blanco=blanco_detalle)
    L2 = S + recuperacion * (S_detalle(L) - S_detalle(B))
    np.clip(L2, 0, 1, out=L2)
    avisar(0.8)

    # Color: a cada canal se le aplica la ganancia que ha recibido la luminancia,
    # pero sobre lo que tiene POR ENCIMA del fondo de cielo (no sobre el total).
    # Es lo que hace el STF clásico al estirar canal a canal con el negro justo
    # bajo el cielo: en una nebulosa débil la relación (R-cielo)/(G-cielo) es
    # mucho mayor que R/G, y con la de los totales la nebulosa salía gris. El
    # fondo (medianas iguales tras neutralizar) queda en S(m) en los tres.
    S_cielo = float(S_curva(np.float32(m)))
    dL = L - m
    pendiente_cielo = float(S_detalle.derivada(np.float32(m)))
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(np.abs(dL) > 0.1 * sigma, (L2 - S_cielo) / dL, pendiente_cielo).astype(np.float32)
    k = np.clip(k, 0, 4.0 * pendiente_cielo)
    out = S_cielo + (rgb - m) * k[..., None]
    mx = out.max(axis=2)
    exceso = mx > 1.0
    if exceso.any():
        L2e = L2[exceso]
        f = ((1.0 - L2e) / np.maximum(mx[exceso] - L2e, 1e-6))[..., None]
        out[exceso] = L2e[..., None] + (out[exceso] - L2e[..., None]) * f
    out = np.clip(out, 0, 1).astype(np.float32)
    avisar(1.0)
    log.debug("estirado %s: mediana %.5f sigma %.5f c0 %.5f blanco %.4f (detalle %.4f) param %.3g",
              S_curva.tipo, m, sigma, S_curva.c0, S_curva.blanco, blanco_detalle, S_curva.param)
    if devolver_curvas:
        return out, {"m": float(m), "sigma": float(sigma), "S_detalle": S_detalle, "S_curva": S_curva,
                     "fondo_objetivo": fondo_objetivo, "agresividad": agresividad, "tipo": curva}
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  4. Contraste local + curva
# ─────────────────────────────────────────────────────────────────────────────

def mascara_estrellas(lum8, dilatar=2, solo_pequenas=False):
    """
    Máscara 0..1 de las estrellas: lo que está 6 sigmas por encima del cielo.
    Con solo_pequenas, por encima del FONDO LOCAL (mediana de 15 px) en vez del
    cielo: una nebulosa brillante supera el umbral global y entraría entera en
    la máscara (la Pacman, 10-oct-2026: su centro se desaturaba con la
    saturación de las estrellas y salía una mancha gris); lo extenso no sobresale
    de su propio fondo local y queda fuera, las estrellas y sus halos sí.
    """
    lum = lum8.astype(np.float32)
    med = float(np.median(lum8))
    mad = float(np.median(np.abs(lum - med))) * 1.4826 + 1.0
    base = cv2.medianBlur(lum8, 15).astype(np.float32) if solo_pequenas else med
    m = (lum > base + 6 * mad).astype(np.uint8)
    if dilatar > 0:
        m = cv2.dilate(m, np.ones((2 * dilatar + 1, 2 * dilatar + 1), np.uint8))
    return cv2.GaussianBlur(m.astype(np.float32), (0, 0), 1.0)   # borde suave 0..1


# Sigmas del cielo (MAD de la luminancia estirada) a partir de las que el contraste
# local actúa del todo con las estrellas aparte
UMBRAL_CIELO_CONTRASTE = 3.0


def contraste_local(rgb, cantidad, proteger_cielo=False):
    """
    CLAHE en luminancia mezclado con el original según `cantidad` (0..1), más curva en S suave.
    rgb: (H, W, 3) float32 en [0, 1] (devuelve float32) o uint8 (devuelve uint8).
    proteger_cielo: el ajuste de CLAHE solo donde hay señal (la imagen sin estrellas).

    La luminancia se retoca en coma flotante: con el fondo muy oscuro (objetivo
    0,03 = nivel 5 de 255) un solo nivel es un 20 % de brillo, y cuantizada a 8
    bits cualquier ajuste pequeño dejaba escalones de bordes duros alrededor de
    los objetos y de las estrellas, que el brillo general amplificaba después.
    """
    cantidad = float(np.clip(cantidad, 0, 1))
    if cantidad <= 0:
        return rgb
    es_uint8 = np.asarray(rgb).dtype == np.uint8
    rgbf = np.asarray(rgb, dtype=np.float32) / 255.0 if es_uint8 else np.clip(np.asarray(rgb, dtype=np.float32), 0, 1)
    lab = cv2.cvtColor(rgbf, cv2.COLOR_RGB2LAB)          # L 0..100, a/b -127..127
    L = lab[..., 0] * 2.55                               # 0..255, sin cuantizar
    L8 = np.clip(L + 0.5, 0, 255).astype(np.uint8)
    mask = mascara_estrellas(L8, dilatar=4)

    # CLAHE se calcula sobre una luminancia SIN estrellas (sustituidas por el
    # fondo local) para que las estrellas brillantes no oscurezcan su entorno.
    # Después se aplica al original solo la DIFERENCIA que introduce CLAHE
    # (un mapa suave de ajuste local); así estrellas y halos quedan intactos y
    # no aparecen anillos oscuros alrededor.
    fondo_local = cv2.medianBlur(L8, 21).astype(np.float32)
    L_sin = L * (1 - mask) + fondo_local * mask
    # CLAHE solo admite 8 bits: se cuantiza con tramado (el desenfoque del ajuste
    # lo promedia) para que el redondeo no deje el ajuste a parches en el cielo
    tramado = np.random.default_rng(0).random(L.shape, dtype=np.float32)
    L_q = np.clip(np.floor(L_sin + tramado), 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=1.0 + 2.0 * cantidad, tileGridSize=(6, 6))
    delta = cv2.GaussianBlur(clahe.apply(L_q).astype(np.float32) - L_q.astype(np.float32), (0, 0), 2.0)
    # Solo el contraste local: CLAHE también desplaza el nivel medio de cada
    # mosaico según su histograma y en cielo plano eso se ve como bandas del
    # tamaño del mosaico (sobre todo junto a los bordes). Se le resta al ajuste
    # su componente de gran escala (medio mosaico) MEDIDA SOLO EN EL CIELO: con
    # la de toda la imagen, la subida del objeto se esparcía a su alrededor y al
    # restarla quedaba un cerco oscuro en torno a nebulosas y galaxias.
    sub = L_sin[::4, ::4]
    med = float(np.median(sub))
    mad = float(np.median(np.abs(sub - med))) * 1.4826 + 0.25
    cielo = (L_sin <= med + 2.0 * mad).astype(np.float32)
    h, w = L.shape
    sigma = max(h, w) / 12.0
    f = max(1, int(sigma / 8.0))                         # la gran escala no necesita resolución completa
    peq = (max(8, w // f), max(8, h // f))
    num = cv2.GaussianBlur(cv2.resize(delta * cielo, peq, interpolation=cv2.INTER_AREA), (0, 0), sigma / f,
                           borderType=cv2.BORDER_REPLICATE)
    den = cv2.GaussianBlur(cv2.resize(cielo, peq, interpolation=cv2.INTER_AREA), (0, 0), sigma / f,
                           borderType=cv2.BORDER_REPLICATE)
    nivel = float(np.median(delta[cielo > 0])) if cielo.any() else 0.0
    # donde no hay cielo cerca (un objeto que llena el campo) vale el nivel medio del cielo
    delta -= cv2.resize((num + 0.02 * nivel) / (den + 0.02), (w, h), interpolation=cv2.INTER_LINEAR)
    if proteger_cielo:
        # Sin estrellas (estrellas_aparte) el cielo no tiene detalle que realzar: lo que
        # CLAHE sube ahí es el ruido de escala media y deja borbotones oscuros (M 27,
        # 24-oct-2026). El ajuste solo se aplica donde hay señal, medida en suave.
        L_suave = cv2.GaussianBlur(L_sin, (0, 0), 4.0)
        senal = np.clip((L_suave - med) / (UMBRAL_CIELO_CONTRASTE * mad), 0.0, None)
        delta *= (1.0 - np.exp(-senal * senal)).astype(np.float32)
    L_m = L + cantidad * delta

    # Curva en S suave alrededor del gris medio (mezcla máxima 35 %)
    x = np.clip(L_m / 255.0, 0, 1)
    k = 1.0 + 1.0 * cantidad
    s = 1.0 / (1.0 + np.exp(-k * (x - 0.5) * 6.0))
    s0, s1 = 1.0 / (1.0 + np.exp(k * 3.0)), 1.0 / (1.0 + np.exp(-k * 3.0))
    s = (s - s0) / (s1 - s0)
    peso = 0.35 * cantidad
    lab[..., 0] = (x * (1 - peso) + s * peso) * 100.0

    out = np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB), 0, 1).astype(np.float32)
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8) if es_uint8 else out


# ─────────────────────────────────────────────────────────────────────────────
#  5. Color
# ─────────────────────────────────────────────────────────────────────────────

# Toda la etapa de color va en coma flotante [0, 1] y se cuantiza a 8 bits una
# sola vez al final: neutralizar, quitar verde y saturar en uint8 encadenaba
# redondeos de ±1 nivel que la saturación convertía en un tinte del fondo.

def neutralizar_fondo_final(rgbf):
    """
    Iguala las medianas R, G y B del fondo de cielo desplazando cada canal. La
    reducción de ruido (ida y vuelta por LAB) deja un sesgo de ~1 nivel que la
    saturación multiplicaría después hasta un tinte visible; aquí se elimina.
    """
    L = rgbf.mean(axis=2)
    m = float(np.median(L))
    mad = float(np.median(np.abs(L - m))) * 1.4826 + 1e-4
    fondo = L < m + 2.0 * mad
    if fondo.sum() < 1000:
        return rgbf
    medianas = np.median(rgbf[fondo], axis=0)
    return np.clip(rgbf - (medianas - medianas.mean()).reshape(1, 1, 3), 0, 1).astype(np.float32)


def scnr_verde(rgbf, factor, estrellas=None):
    """
    Elimina el exceso de verde (media de R y B como referencia), estilo SCNR
    average neutral. Las estrellas (máscara 0..1) se dejan fuera: con el
    estirado multiescala ya no saturan a blanco y quitarles el verde las
    teñiría de magenta.
    """
    factor = float(np.clip(factor, 0, 1))
    if factor <= 0:
        return rgbf
    f = rgbf.copy()
    ref = (f[..., 0] + f[..., 2]) / 2
    exceso = np.clip(f[..., 1] - ref, 0, None)
    if estrellas is not None:
        exceso *= (1.0 - estrellas)
    f[..., 1] -= factor * exceso
    return np.clip(f, 0, 1).astype(np.float32)


def color_estrellas_uniforme(rgbf, estrellas, sigma_px=4.0, cociente=False):
    """
    Dentro de las estrellas (máscara 0..1) sustituye el color de cada píxel por el
    promedio local ponderado por la luz sobre el fondo (al cuadrado: manda el
    núcleo): el núcleo y el halo comparten el color medio de la estrella, que es
    el que tiene su luz integrada. Fuera de la máscara no cambia nada.

    cociente: el color se comparte como RGB / luminancia en vez de como
    diferencia. Tras deconvolucionar, el halo queda 10 veces más débil que el
    núcleo y al sumarle la croma absoluta del núcleo salía un anillo violeta
    exagerado y recortado (Pacman, 14-oct-2026: R/G del halo 3,0 frente a 1,2
    en el original); con el cociente el halo toma el tono del núcleo a su propio
    brillo (1,3). Solo se usa con nitidez > 0 para no cambiar las fotos de siempre.
    """
    f = np.clip(np.asarray(rgbf, dtype=np.float32), 0, 1)
    L = f.mean(axis=2, keepdims=True)
    fondo = float(np.median(L))
    w = np.clip(L[..., 0] - fondo, 0, None) ** 2 * estrellas
    den = cv2.GaussianBlur(w, (0, 0), sigma_px)[..., None]
    mezcla = estrellas[..., None]
    if cociente:
        R = f / np.maximum(L, 1e-4)
        num = cv2.GaussianBlur(R * w[..., None], (0, 0), sigma_px)
        Rm = np.where(den > 1e-8, num / np.maximum(den, 1e-8), R)
        return np.clip(L * (R * (1.0 - mezcla) + Rm * mezcla), 0, 1).astype(np.float32)
    C = f - L
    num = cv2.GaussianBlur(C * w[..., None], (0, 0), sigma_px)
    Cm = np.where(den > 1e-8, num / np.maximum(den, 1e-8), C)
    return np.clip(L + C * (1.0 - mezcla) + Cm * mezcla, 0, 1).astype(np.float32)


def saturar(rgbf, factor, estrellas=None, factor_estrellas=None):
    """
    Multiplica la saturación por `factor`; donde `estrellas` (máscara 0..1) vale 1
    se usa `factor_estrellas` en su lugar (None = el mismo), para que los halos
    de las estrellas no hereden la saturación pensada para la nebulosa.
    """
    factor = float(factor)
    fe = factor if factor_estrellas is None or estrellas is None else float(factor_estrellas)
    if abs(factor - 1.0) < 1e-3 and abs(fe - 1.0) < 1e-3:
        return rgbf
    hsv = cv2.cvtColor(np.clip(rgbf, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)   # H 0..360, S y V 0..1
    ganancia = factor if abs(fe - factor) < 1e-3 else factor * (1.0 - estrellas) + fe * estrellas
    hsv[..., 1] = np.clip(hsv[..., 1] * ganancia, 0, 1)
    return np.clip(cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB), 0, 1).astype(np.float32)


def temperatura_color(rgbf, temperatura, fondo=None):
    """
    Desplaza la tonalidad de lo que está por encima del fondo: temperatura > 0
    hacia el cálido (más rojo, menos azul), < 0 hacia el frío. Se aplica sobre la
    señal respecto al nivel del fondo, así el cielo (ya neutralizado) no se tiñe
    y solo cambian objetos y estrellas. Rango útil -1..1 (±30 % en R y B).
    fondo: nivel del cielo por canal (fondo_cielo); si no viene, se mide aquí.
    """
    t = float(np.clip(temperatura, -1, 1))
    if abs(t) < 1e-3:
        return rgbf
    if fondo is None:
        fondo = fondo_cielo(rgbf)
    fondo = np.asarray(fondo, dtype=np.float32).reshape(1, 1, 3)
    ganancia = np.array([1.0 + 0.3 * t, 1.0, 1.0 - 0.3 * t], dtype=np.float32).reshape(1, 1, 3)
    return np.clip(fondo + (rgbf - fondo) * ganancia, 0, 1).astype(np.float32)


def brillo_general(rgbf, brillo):
    """Curva de medios sobre los tres canales: brillo 1 = raíz cuadrada (mucho más claro), -1 = cuadrado."""
    b = float(np.clip(brillo, -1, 1))
    if abs(b) < 1e-3:
        return rgbf
    return np.power(np.clip(rgbf, 0, 1), 2.0 ** (-b)).astype(np.float32)


# ── Curvas por puntos de control (como las del Seestar) ──
#
# Cada curva es una lista de puntos [x, y] en [0, 1], ordenados por x, que pasa
# por todos ellos con un spline cúbico monótono (Fritsch-Carlson: sin
# oscilaciones ni inversiones entre puntos). Hay una curva para los tres canales
# ("rgb") y una por canal ("r", "g", "b"); la de los tres se aplica primero. Una
# curva sin puntos, o con solo los extremos [0, 0] y [1, 1], es la identidad.
# El mismo algoritmo está en JavaScript en static/js/curvas.js.

CANALES_CURVAS = ("rgb", "r", "g", "b")
MAX_PUNTOS_CURVA = 16


def normalizar_curva(puntos):
    """
    Puntos de una curva tal como llegan de la web o del JSON -> lista limpia
    [[x, y], ...] ordenada por x, o None si es la identidad (así el JSON de la
    foto no arrastra curvas vacías). Los extremos no tienen por qué estar en
    x=0 y x=1: fuera de ellos la curva vale lo mismo que en el extremo, así el
    primer punto corrido a la derecha recorta el negro (oscurece el fondo) y el
    último corrido a la izquierda recorta el blanco.
    """
    limpios = []
    for p in puntos or ():
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            limpios.append([min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)])
    if len(limpios) < 2:
        return None
    limpios.sort(key=lambda p: p[0])
    # dos puntos no pueden compartir x (el spline divide por la distancia)
    salida = [limpios[0]]
    for p in limpios[1:]:
        if p[0] - salida[-1][0] >= 0.002:
            salida.append(p)
    salida = salida[:MAX_PUNTOS_CURVA]
    salida = [[round(x, 4), round(y, 4)] for x, y in salida]
    if len(salida) == 2 and salida[0] == [0.0, 0.0] and salida[1] == [1.0, 1.0]:
        return None
    return salida


def normalizar_curvas(curvas):
    """{"rgb": puntos, "r": ..., "g": ..., "b": ...} solo con las curvas que no son la identidad; {} si ninguna."""
    salida = {}
    if isinstance(curvas, dict):
        for canal in CANALES_CURVAS:
            p = normalizar_curva(curvas.get(canal))
            if p:
                salida[canal] = p
    return salida


def _pendientes_curva(xs, ys):
    """Tangentes del spline cúbico monótono de Fritsch-Carlson en cada punto."""
    n = len(xs)
    h = np.diff(xs)
    d = np.diff(ys) / h
    m = np.empty(n, dtype=np.float64)
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2.0
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s2 = a * a + b * b
        if s2 > 9.0:
            t = 3.0 / math.sqrt(s2)
            m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    return m


def evaluar_curva(puntos, x):
    """Aplica la curva (lista de puntos normalizada) a x (array float en [0, 1]); None = identidad."""
    if not puntos or len(puntos) < 2:
        return x
    xs = np.array([p[0] for p in puntos], dtype=np.float64)
    ys = np.array([p[1] for p in puntos], dtype=np.float64)
    x = np.asarray(x, dtype=np.float32)
    forma = x.shape
    # Fuera de los extremos la curva es constante (negro recortado, blanco recortado)
    xf = np.clip(x.ravel().astype(np.float64), xs[0], xs[-1])
    if len(puntos) == 2:
        y = ys[0] + (ys[1] - ys[0]) * (xf - xs[0]) / (xs[1] - xs[0])
        return np.clip(y, 0, 1).astype(np.float32).reshape(forma)
    m = _pendientes_curva(xs, ys)
    i = np.clip(np.searchsorted(xs, xf, side="right") - 1, 0, len(xs) - 2)
    h = xs[i + 1] - xs[i]
    t = (xf - xs[i]) / h
    t2, t3 = t * t, t * t * t
    y = ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
         + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1])
    return np.clip(y, 0, 1).astype(np.float32).reshape(forma)


def aplicar_curvas(rgbf, curvas):
    """Curvas del editor sobre la imagen float [0, 1]: primero la de los tres canales, después la de cada uno."""
    curvas = normalizar_curvas(curvas)
    if not curvas:
        return rgbf
    f = np.clip(np.asarray(rgbf, dtype=np.float32), 0, 1)
    # Las curvas son monótonas por tramos: se evalúan por tabla de 1024 entradas
    # (interpolada) en vez de píxel a píxel, que con 9 Mpx sería lento
    malla = np.linspace(0.0, 1.0, 1025, dtype=np.float32)
    tabla_rgb = evaluar_curva(curvas.get("rgb"), malla)
    for c, canal in enumerate(("r", "g", "b")):
        tabla = evaluar_curva(curvas.get(canal), tabla_rgb)
        if curvas.get("rgb") or curvas.get(canal):
            f[..., c] = np.interp(f[..., c], malla, tabla).astype(np.float32)
    return f


def fondo_cielo(rgbf):
    """
    Nivel del fondo de cielo por canal (mediana R, G, B de los píxeles de cielo),
    el punto alrededor del que gira la temperatura de color. El editor lo manda
    al navegador para que use el mismo valor que el servidor.
    """
    L = rgbf.mean(axis=2)
    m = float(np.median(L))
    mad = float(np.median(np.abs(L - m))) * 1.4826 + 1e-4
    cielo = L < m + 2.0 * mad
    if cielo.sum() > 1000:
        return np.median(rgbf[cielo], axis=0).astype(np.float32)
    return np.array([m, m, m], dtype=np.float32)


def _float(v, defecto=0.0):
    """Un ajuste numérico tal como llega (número, texto o None); el defecto si no lo es."""
    try:
        return float(v) if v is not None and str(v).strip() != "" else defecto
    except (TypeError, ValueError):
        return defecto


def _bool(v, defecto=True):
    if v is None:
        return defecto
    return v if isinstance(v, bool) else str(v).lower() == "true"


def color_base(rgb, ajustes):
    """
    Primera parte de la etapa de color, la que necesita el vecindario: máscaras
    de estrellas, fondo reneutralizado y un solo color por estrella.
    rgb: imagen estirada, float [0, 1] o uint8. Devuelve (f float32 [0, 1],
    estrellas, pequenas): las dos máscaras 0..1 que usan los ajustes rápidos
    (SCNR sin las estrellas y saturación propia de las estrellas).
    """
    if np.asarray(rgb).dtype == np.uint8:
        f = rgb.astype(np.float32) / 255.0
    else:
        f = np.clip(np.asarray(rgb, dtype=np.float32), 0, 1)
    gris = np.clip(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    estrellas = mascara_estrellas(gris, dilatar=2)
    pequenas = mascara_estrellas(gris, dilatar=3, solo_pequenas=True)
    f = neutralizar_fondo_final(f)
    if _bool(ajustes.get("color_estrellas_uniforme", True)):
        try:
            con_nitidez = float(ajustes.get("nitidez", 0) or 0) > 0
        except (TypeError, ValueError):
            con_nitidez = False
        f = color_estrellas_uniforme(f, pequenas, cociente=con_nitidez)
    return f, estrellas, pequenas


def ajustes_rapidos(f, ajustes, estrellas=None, pequenas=None, fondo=None):
    """
    Segunda parte de la etapa de color, píxel a píxel: temperatura, brillo, SCNR,
    saturación y curvas, en este orden. Es lo que el navegador repite en vivo
    sobre la base en 8 bits (static/js/retoque.js); aquí se hace en float.
    fondo: nivel del cielo por canal (fondo_cielo) si ya está medido.
    """
    f = temperatura_color(f, ajustes.get("temperatura", 0.0), fondo=fondo)
    f = brillo_general(f, ajustes.get("brillo", 0.0))
    f = scnr_verde(f, ajustes.get("scnr", 0.8), None if _bool(ajustes.get("scnr_estrellas", True)) else estrellas)
    f = saturar(f, ajustes.get("saturacion", 1.5), pequenas, ajustes.get("saturacion_estrellas"))
    return aplicar_curvas(f, ajustes.get("curvas"))


def color(rgb, ajustes):
    """Etapa de color completa sobre la imagen estirada (float [0, 1] o uint8): devuelve uint8."""
    f, estrellas, pequenas = color_base(rgb, ajustes)
    f = ajustes_rapidos(f, ajustes, estrellas, pequenas)
    return np.clip(f * 255.0 + 0.5, 0, 255).astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
#  Estrellas aparte (estrellas_aparte.py separa las capas con StarNet2)
# ─────────────────────────────────────────────────────────────────────────────
#
# Con las estrellas separadas, el objeto y el fondo pasan por el mismo pipeline
# (con dos diferencias: sin nitidez, que mide su PSF en las estrellas, y con el
# color del fondo suavizado a gran escala, suavizar_color_fondo) y las estrellas
# por el suyo: la capa unscreen U (fracción 0..1 sobre negro) se estira con la
# MISMA curva del detalle que les habría aplicado estirar_multiescala si
# siguieran en la foto, así salen con el brillo de siempre y con su color (la
# ganancia multiplica los tres canales por igual), y después tienen su propia
# saturación y SCNR. Las dos capas se juntan con una pantalla (screen):
#   final = 1 - (1 - objeto)·(1 - estrellas·tamaño)
# y «tamaño» (tamano_estrellas, 0..100 %) es el deslizador del editor: 0 = sin
# estrellas, 100 = como salieron. El navegador repite la pantalla y los ajustes
# de la capa en vivo (static/js/retoque.js); al guardar se hacen aquí en float.
#
# El tamaño REDUCE las estrellas, no las apaga ni las recorta: cada estrella se
# vuelve a pintar como una campana de Gauss más estrecha, con su pico y su color.
# Primero se hace un catálogo de la capa (catalogo_estrellas: posición, anchura y
# color en el pico de cada una) y luego se pinta (pintar_estrellas). Todas
# encogen en la MISMA proporción, sigma·tamaño, para que conserven su jerarquía
# (las brillantes siguen siendo mayores que las débiles; encogiendo más las
# brillantes, todas acababan del mismo tamaño y la foto perdía naturalidad), y
# con un suelo suave, raíz((sigma·tamaño)² + SIGMA_MINIMA²): una estrella
# pequeña nunca baja de medio píxel ni desaparece por quedarse entre píxeles,
# y como conserva su pico se sigue viendo. Por debajo del 25 % el pico baja
# con (tamaño/0,25)², hasta desaparecer todas al 0 %. Entre el 100 y el 85 % la
# capa original y la pintada se funden poco a poco. Las brillantes, que el
# estirado corta en su tope, se pintan igual que salen del estirado: su color
# por una sobreexposición y recortado a 1, así conservan la cima blanca y el
# color en el halo (con un solo color de arriba abajo parecían bolas pintadas).
#
# Se probó antes a encoger el perfil de la propia capa (erosión, potencias con
# el pico buscado en una ventana): las estrellas brillantes tienen la cima plana
# (el estirado las lleva a su tope) y quedaban como discos de borde duro, y con
# la ventana más pequeña que la estrella salía un anillo oscuro dentro (M 27 en
# el mini PC, 24-oct-2026). La misma aritmética de pintar en static/js/retoque.js;
# el catálogo lo hace solo el servidor y se manda al navegador con la vista previa.

TAMANO_ESTRELLAS = 100.0      # por defecto: las estrellas tal como salen
TAMANO_PICO = 0.25            # por debajo de este tamaño el pico baja con (tamaño / 0,25)²
TAMANO_FUNDIDO = 0.85         # entre 1 y este tamaño se funde la capa original con la pintada
UMBRAL_ESTRELLA = 0.03        # pico mínimo (capa estirada, 0..1) para entrar en el catálogo
SIGMA_MINIMA = 0.5            # px: suelo suave de la anchura (se perdería entre píxeles)
SIGMA_HALO = 2.5              # px: anchura mínima de una estrella cuyo halo se resta antes de medir las demás
HALO_RELATIVO = 1.5           # ...y al menos estas veces la anchura mediana de la foto
HALO_SIGMAS = 6.0             # radio del modelo del halo, en sigmas de la estrella
HALO_RADIO_MAX = 120          # px
CIMA = 0.97                   # luminancia (canal mayor) a partir de la que la cima de una estrella está cortada
CIMA_Q_MAX = 0.85             # tope de cima / media altura (sobreexposición hasta 2^5,7 = 50)


def catalogo_estrellas(capa):
    """
    capa: capa de estrellas estirada (H, W, 3) float [0, 1]. Devuelve un array
    float32 (N, 7): x, y (centro, px), sigma (px), r, g, b (color en el pico) y la
    sobreexposición A (1 sin cima cortada; ver _medir_estrellas) de cada estrella
    con pico por encima de UMBRAL_ESTRELLA.

    En dos pasadas: las estrellas grandes (sigma de al menos HALO_RELATIVO veces
    la mediana y SIGMA_HALO px) se miden en la
    capa tal cual, y las demás en la capa sin el halo de las grandes. Medidas
    sobre el halo, las débiles que lo rodean se llevaban su luz: el pico, la
    anchura a media altura y el color de la brillante, y al reducir salía un
    anillo de bolas naranjas que no existen (M 17, 25-oct-2026). El halo de cada
    grande se modela con la mediana por anillos (las vecinas son pocos píxeles
    de cada anillo y la mediana no las ve).
    """
    c = np.clip(np.asarray(capa, dtype=np.float32), 0, 1)
    cat = _medir_estrellas(c)
    # Las grandes, de mayor a menor: una que cae dentro del halo de otra mayor ya
    # elegida no cuenta (su anchura la infla ese halo) y se mide en el resto
    if not len(cat):
        return cat
    # Para el halo cuenta el tamaño que se ve (el radio a media altura / 1,1774), no
    # la sigma de la gaussiana recortada, que en las brillantes es menor
    aparente = cat[:, 2] * np.sqrt(2.0 * np.log(2.0 * cat[:, 6])) / 1.1774
    minima = max(SIGMA_HALO, HALO_RELATIVO * float(np.median(aparente)))
    elegidas = []
    for i in np.argsort(-aparente):
        x, y = cat[i, :2]
        if aparente[i] < minima:
            break
        if all((x - cat[j, 0]) ** 2 + (y - cat[j, 1]) ** 2 > (HALO_SIGMAS * aparente[j]) ** 2 for j in elegidas):
            elegidas.append(i)
    if not elegidas:
        return cat
    grandes = cat[elegidas]
    s_grandes = aparente[elegidas]
    h, w = c.shape[:2]
    halos = np.zeros_like(c)
    for (x, y), sg in zip(grandes[:, :2], s_grandes):
        R = int(min(HALO_SIGMAS * sg, HALO_RADIO_MAX))
        xi, yi = int(round(x)), int(round(y))
        x0, x1, y0, y1 = max(0, xi - R), min(w, xi + R + 1), max(0, yi - R), min(h, yi + R + 1)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        r = np.hypot(xx - x, yy - y).astype(np.int32)
        dentro = r <= R
        ventana = c[y0:y1, x0:x1]
        perfil = np.zeros((R + 1, 3), np.float32)
        orden = np.argsort(r[dentro], kind="stable")
        rs = r[dentro][orden]
        vs = ventana[dentro][orden]
        cortes = np.searchsorted(rs, np.arange(R + 2))
        for k in range(R + 1):
            if cortes[k + 1] > cortes[k]:
                perfil[k] = np.median(vs[cortes[k]:cortes[k + 1]], axis=0)
        perfil = np.minimum.accumulate(perfil, axis=0)      # el halo solo baja hacia fuera
        modelo = np.where(dentro[..., None], perfil[np.minimum(r, R)], 0.0)
        halos[y0:y1, x0:x1] = np.maximum(halos[y0:y1, x0:x1], modelo)
    resto = _medir_estrellas(np.clip(c - halos, 0, 1))
    # En el resto, lo que queda dentro de una grande hasta su radio a media altura
    # (1,18 sigmas) es su propio residuo, no estrellas: restos de color del núcleo
    if len(resto):
        d2 = (resto[:, 0, None] - grandes[None, :, 0]) ** 2 + (resto[:, 1, None] - grandes[None, :, 1]) ** 2
        resto = resto[(d2 >= (1.25 * s_grandes[None, :]) ** 2).all(axis=1)]
    return np.vstack([grandes, resto]).astype(np.float32)


def _medir_estrellas(c):
    """
    Una pasada del catálogo sobre c (H, W, 3) float [0, 1]:
      - semillas: máximos locales (5×5) de la luminancia suavizada (0,8 px); las
        de una misma cima plana se unen (dilatación 3×3 + componentes);
      - cada píxel de estrella va a la semilla más cercana (distanceTransformWithLabels);
      - pico P = máximo de la luminancia; sigma por el área por encima de P/2
        (radio a media altura / 1,1774); centro y color pesados con L² en esa área.
    Con la cima cortada (pico >= CIMA, las brillantes que el estirado lleva a su
    tope) la estrella es A·gaussiana recortada a 1: de la cima (radio r_c) y la
    media altura (r_m) sale la sobreexposición A = 2^(q/(1-q)), q = r_c²/r_m², y
    la sigma de verdad, r_m / raíz(2·ln(2A)) (sin cima, A = 1 y r_m / 1,1774); el
    color, de los píxeles sin cortar, que en la cima son blancos.
    """
    L = c.max(axis=2)
    Ls = cv2.GaussianBlur(L, (0, 0), 0.8)
    semillas = ((Ls >= cv2.dilate(Ls, np.ones((5, 5), np.uint8)) - 1e-6) & (Ls > UMBRAL_ESTRELLA)).astype(np.uint8)
    if not semillas.any():
        return np.zeros((0, 7), np.float32)
    semillas = cv2.dilate(semillas, np.ones((3, 3), np.uint8))
    __, etiquetas = cv2.distanceTransformWithLabels(1 - semillas, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_CCOMP)
    ys, xs = np.nonzero(L > UMBRAL_ESTRELLA * 0.5)
    lab = etiquetas[ys, xs].astype(np.int64)
    Lm = L[ys, xs]
    n = int(etiquetas.max()) + 1
    P = np.zeros(n, np.float32)
    np.maximum.at(P, lab, Lm)
    # Color sin cortar de las estrellas con cima: la estrella entera menos la cima (y menos lo muy débil)
    peso_t = np.where((Lm < CIMA) & (Lm >= P[lab] * 0.25), Lm.astype(np.float64) ** 2, 0.0)
    sw_t = np.bincount(lab, peso_t, minlength=n)
    rgb_t = np.stack([np.bincount(lab, peso_t * c[ys, xs, k], minlength=n) for k in range(3)], axis=1)
    sel = Lm >= P[lab] * 0.5
    ys, xs, lab, Lm = ys[sel], xs[sel], lab[sel], Lm[sel]
    area = np.bincount(lab, minlength=n).astype(np.float64)
    area_cima = np.bincount(lab, (Lm >= CIMA).astype(np.float64), minlength=n)
    peso = Lm.astype(np.float64) ** 2
    sw = np.bincount(lab, peso, minlength=n)
    ok = (area > 0) & (P > UMBRAL_ESTRELLA) & (sw > 0)
    sw = np.where(sw > 0, sw, 1.0)
    cx = np.bincount(lab, peso * xs, minlength=n) / sw
    cy = np.bincount(lab, peso * ys, minlength=n) / sw
    rgb = np.stack([np.bincount(lab, peso * c[ys, xs, k], minlength=n) / sw for k in range(3)], axis=1)
    con_cima = (P >= CIMA) & (area_cima > 0)
    q = np.where(con_cima, np.clip(area_cima / np.maximum(area, 1.0), 0.0, CIMA_Q_MAX), 0.0)
    A = np.power(2.0, q / (1.0 - q))
    sigma = np.sqrt(area / np.pi) / np.sqrt(2.0 * np.log(2.0 * A))
    usar_t = con_cima & (sw_t > 0)
    rgb = np.where(usar_t[:, None], rgb_t / np.where(sw_t > 0, sw_t, 1.0)[:, None], rgb)
    tope = np.maximum(rgb.max(axis=1), 1e-6)
    rgb = rgb / tope[:, None] * P[:, None]               # el color medio, llevado al pico
    return np.column_stack([cx, cy, sigma, rgb, A])[ok].astype(np.float32)


def pintar_estrellas(cat, forma, tamano):
    """
    Pinta el catálogo (catalogo_estrellas) en una capa (H, W, 3) float [0, 1] con
    el tamaño 0..1: cada estrella una gaussiana de sigma = raíz((sigma·tamaño)² +
    SIGMA_MINIMA²) hasta 3,5 sigmas, con su color (r, g, b) por la sobreexposición
    A y recortada a 1 canal a canal: las brillantes conservan la cima blanca y el
    color en el halo, y la cima encoge con la estrella (sin cima, A = 1, pico
    P = max(r, g, b)). Donde se solapan, manda la más brillante en ese píxel.
    Por debajo de TAMANO_PICO la luz baja con (tamaño/TAMANO_PICO)². Igual que en
    static/js/retoque.js.
    """
    h, w = forma
    out = np.zeros((h, w, 3), np.float32)
    tamano = float(np.clip(tamano, 0.0, 1.0))
    if tamano <= 0.0 or len(cat) == 0:
        return out
    Lout = np.zeros((h, w), np.float32)
    apagado = (tamano / TAMANO_PICO) ** 2 if tamano < TAMANO_PICO else 1.0
    for x, y, s, r, g, b, A in np.asarray(cat, dtype=np.float32).reshape(-1, 7):
        P = float(max(r, g, b))
        if P <= 0:
            continue
        s2 = math.sqrt((float(s) * tamano) ** 2 + SIGMA_MINIMA ** 2)
        R = int(math.ceil(3.5 * s2))
        xc, yc = int(round(float(x))), int(round(float(y)))
        x0, x1 = max(0, xc - R), min(w, xc + R + 1)
        y0, y1 = max(0, yc - R), min(h, yc + R + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        gx = np.exp(-((np.arange(x0, x1, dtype=np.float32) - np.float32(x)) ** 2) / np.float32(2 * s2 * s2))
        gy = np.exp(-((np.arange(y0, y1, dtype=np.float32) - np.float32(y)) ** 2) / np.float32(2 * s2 * s2))
        k = np.float32(float(A) * apagado) * gy[:, None] * gx[None, :]      # la luz por el color, sin recortar
        v = np.minimum(np.float32(P) * k, 1.0)
        ventana = Lout[y0:y1, x0:x1]
        mayor = v > ventana
        ventana[mayor] = v[mayor]
        f = k[mayor]
        bloque = out[y0:y1, x0:x1]
        bloque[mayor] = np.minimum(np.stack([f * r, f * g, f * b], axis=1), 1.0)
    return out


def reducir_estrellas(capa, tamano, cat=None):
    """
    La capa de estrellas con el tamaño 0..1: la original al 100 %; entre el 100 y
    el 85 % la original fundida con la pintada; por debajo, solo la pintada.
    """
    capa = np.clip(np.asarray(capa, dtype=np.float32), 0, 1)
    tamano = float(np.clip(tamano, 0.0, 1.0))
    if tamano >= 1.0:
        return capa
    if cat is None:
        cat = catalogo_estrellas(capa)
    pintada = pintar_estrellas(cat, capa.shape[:2], tamano)
    a = float(np.clip((1.0 - tamano) / (1.0 - TAMANO_FUNDIDO), 0.0, 1.0))
    return (capa * (1.0 - a) + pintada * a).astype(np.float32)


# Sin estrellas, el fondo se modela con más detalle: una rejilla más fina en
# quitar_gradiente (sin estrellas la mediana de cada celda es limpia) para que
# se lleve las sombras de polvo y los restos del viñeteo, que la foto normal
# esconde detrás del campo de estrellas y que al subir el brillo salen como
# manchas de otro tono; y el suavizado del color y los huecos a esta escala.
REJILLA_SIN_ESTRELLAS = 32
SIGMA_SUAVIZADO_SIN_ESTRELLAS = 128.0


def pantalla(objeto, estrellas):
    """Mezcla «pantalla» (screen) de dos imágenes float [0, 1] del mismo tamaño."""
    return (1.0 - (1.0 - objeto) * (1.0 - estrellas)).astype(np.float32)


def estirar_capa_estrellas(U, curvas, balance=None, intensidad=0.0, compresion=0.0):
    """
    U: capa unscreen (H, W, 3) float 0..1 (las estrellas sobre negro, lineal).
    curvas: el dict que devuelve estirar_multiescala(devolver_curvas=True) para
    el objeto. balance: ganancias {"r", "b"} de balance_estrellas (el color de las
    estrellas se calibra con las mismas que el objeto, sin restar cielo: no lo
    tienen). Devuelve float32 (H, W, 3) en [0, 1], no lineal: cada estrella con
    el brillo que le daría la curva del detalle sobre el cielo y su color intacto.

    El estirado propio de las estrellas (panel «Estrellas aparte»), con los dos a 0
    el de siempre, el mismo que el objeto:
      intensidad -1..1: la luz de las estrellas se multiplica por 2^(2·intensidad)
                 antes de la curva (×4 a ×1/4): más o menos estrellas y más o menos
                 brillantes, sin tocar la nebulosa ni el fondo;
      compresion 0..1: el blanco de la curva sube del de la foto hacia el máximo
                 (como «Compresión de lo brillante», pero solo en las estrellas):
                 con 0 los núcleos de las brillantes se cortan en su tope y quedan
                 planos; con 1 nada se corta y conservan el perfil y el color hasta
                 el centro.
    """
    U = np.clip(np.asarray(U, dtype=np.float32), 0, 1)
    if balance:
        U = np.clip(U * np.array([balance["r"], 1.0, balance["b"]], dtype=np.float32), 0, 1)
    L = U.mean(axis=2)
    S = curvas["S_detalle"]
    m = np.float32(curvas["m"])
    compresion = float(np.clip(compresion or 0.0, 0.0, 1.0))
    if compresion > 0 and "fondo_objetivo" in curvas:
        S = CurvaFondo(float(m), curvas["sigma"], curvas["fondo_objetivo"], curvas["agresividad"], curvas["tipo"],
                       blanco=float(S.blanco ** (1.0 - compresion)))
    ganancia_luz = np.float32(2.0 ** (2.0 * float(np.clip(intensidad or 0.0, -1.0, 1.0))))
    Ls = np.clip(S(m + L * ganancia_luz) - float(S(m)), 0, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ganancia = np.where(L > 1e-6, Ls / np.maximum(L, 1e-6), 0.0).astype(np.float32)
    return np.clip(U * ganancia[..., None], 0, 1).astype(np.float32)


# Radio (sigma, px) del color compartido en la capa de estrellas. Con los 4 px de
# la foto normal, en un campo denso (M 17) se mezclaba el color de estrellas
# vecinas: la variedad de color bajaba un 17 %; con 1,5 px, un 6 %.
SIGMA_COLOR_CAPA = 1.5


def capa_estrellas_base(capa, ajustes):
    """
    La capa de estrellas estirada, lista para los ajustes rápidos: un solo color
    por estrella (como en color_base) y la máscara de dónde hay estrella.
    Devuelve (capa float32 [0, 1], mascara 0..1).
    """
    capa = np.clip(np.asarray(capa, dtype=np.float32), 0, 1)
    gris = np.clip(capa.mean(axis=2) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    mascara = cv2.GaussianBlur((gris > 2).astype(np.float32), (0, 0), 1.0)
    if _bool(ajustes.get("color_estrellas_uniforme", True)):
        capa = color_estrellas_uniforme(capa, mascara, sigma_px=SIGMA_COLOR_CAPA, cociente=True)
    return capa, mascara


def ajustes_rapidos_capas(f, capa, ajustes, fondo=None):
    """
    Los ajustes rápidos con las estrellas aparte, en este orden (el navegador
    hace lo mismo, static/js/retoque.js, modo capas): sobre el objeto,
    temperatura, brillo, SCNR y saturación general; sobre la capa de estrellas,
    temperatura (sobre negro) y brillo, el SCNR si scnr_estrellas, su propia
    saturación y sus propias curvas (curvas_estrellas; el tamaño, 0..100 %, va
    antes de todo: la capa se vuelve a pintar con reducir_estrellas y su
    catálogo); pantalla de las dos y, al final, las curvas de la foto.
    """
    f = temperatura_color(f, ajustes.get("temperatura", 0.0), fondo=fondo)
    f = brillo_general(f, ajustes.get("brillo", 0.0))
    f = scnr_verde(f, ajustes.get("scnr", 0.8))
    f = saturar(f, ajustes.get("saturacion", 1.5))
    try:
        tamano = float(np.clip(float(ajustes.get("tamano_estrellas", TAMANO_ESTRELLAS)), 0, 100)) / 100.0
    except (TypeError, ValueError):
        tamano = 1.0
    c = reducir_estrellas(capa, tamano)
    c = temperatura_color(c, ajustes.get("temperatura", 0.0), fondo=np.zeros(3, dtype=np.float32))
    c = brillo_general(c, ajustes.get("brillo", 0.0))
    if _bool(ajustes.get("scnr_estrellas", True)):
        c = scnr_verde(c, ajustes.get("scnr", 0.8))
    sat_e = ajustes.get("saturacion_estrellas")
    c = saturar(c, ajustes.get("saturacion", 1.5) if sat_e is None else sat_e)
    c = aplicar_curvas(c, ajustes.get("curvas_estrellas"))     # las curvas propias de las estrellas, antes de juntarlas
    out = pantalla(f, c)
    return aplicar_curvas(out, ajustes.get("curvas"))


def procesar_capas(objeto_lineal, capa_U, n_fotos=1, ajustes=None, escala=1.0, progreso=None, balance=None, solo_base=False):
    """
    Como procesar(), pero con las estrellas aparte: objeto_lineal es el apilado
    sin estrellas (3, H, W) o (H, W, 3) en ADU y capa_U la capa unscreen del mismo
    tamaño (fracción 0..1), las dos de estrellas_aparte.separar. balance: el de
    balance_estrellas medido en el apilado ORIGINAL (en el objeto ya no hay
    estrellas con que medirlo; None deja el color sin calibrar).
    Devuelve uint8 (h, w, 3) RGB. Con solo_base, (base del objeto float32 [0, 1],
    capa de estrellas float32 [0, 1], mascara de estrellas): lo que el editor
    manda al navegador para que haga él los ajustes rápidos y la pantalla.
    """
    ajustes = ajustes or leer_config()
    U = np.asarray(capa_U, dtype=np.float32)
    if U.ndim == 3 and U.shape[0] == 3:
        U = np.transpose(U, (1, 2, 0))
    if escala != 1.0:
        h, w = U.shape[:2]
        U = cv2.resize(U, (max(8, int(w * escala)), max(8, int(h * escala))), interpolation=cv2.INTER_AREA)
    rgbf, curvas = _estirar_objeto(objeto_lineal, ajustes, escala, progreso, balance, sin_estrellas=True)
    capa = estirar_capa_estrellas(U, curvas, balance, intensidad=_float(ajustes.get("intensidad_estrellas"), 0.0),
                                  compresion=_float(ajustes.get("compresion_estrellas"), 0.0))
    f, __, __ = color_base(rgbf, ajustes)
    capa, mascara = capa_estrellas_base(capa, ajustes)
    if solo_base:
        return f, capa, mascara
    out = ajustes_rapidos_capas(f, capa, ajustes)
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline completo
# ─────────────────────────────────────────────────────────────────────────────

# Sub-fases del procesado y la fracción del tiempo total que se lleva cada una
# (medidas en la vista previa; la reducción de ruido es la que manda). Sirven
# para que la web pinte el progreso dentro de la fase "estirando" del apilador.
SUBFASES = (("gradiente", 0.05), ("ruido", 0.35), ("nitidez", 0.10), ("estirando", 0.20), ("contraste", 0.12), ("color", 0.18))


def _estirar_objeto(cubo_lineal, ajustes, escala, progreso, balance, sin_estrellas=False):
    """
    Del cubo lineal a la imagen estirada con el contraste local, en float [0, 1]:
    los pasos 1 a 4 de la cabecera. Devuelve (rgbf, curvas), con `curvas` el dict
    de estirar_multiescala(devolver_curvas=True) (None con el STF). sin_estrellas:
    la imagen es el objeto sin estrellas (procesar_capas): sin nitidez y con el
    color del fondo suavizado a gran escala.
    """
    inicio = {}
    acumulado = 0.0
    for nombre, peso in SUBFASES:
        inicio[nombre] = acumulado
        acumulado += peso
    pesos = dict(SUBFASES)

    def avisar(subfase, dentro=0.0):
        if progreso:
            try:
                progreso(subfase, inicio[subfase] + pesos[subfase] * float(dentro))
            except Exception:
                pass
    rgb = np.asarray(cubo_lineal, dtype=np.float32)
    if rgb.ndim == 3 and rgb.shape[0] == 3:
        rgb = np.transpose(rgb, (1, 2, 0))
    if escala != 1.0:
        h, w = rgb.shape[:2]
        rgb = cv2.resize(rgb, (max(8, int(w * escala)), max(8, int(h * escala))), interpolation=cv2.INTER_AREA)

    avisar("gradiente")
    if ajustes.get("gradiente", True):
        rgb = quitar_gradiente(rgb, rejilla=REJILLA_SIN_ESTRELLAS if sin_estrellas else 12)
    # Sin estrellas no se aplana: la rejilla fina ya se lleva las sombras de polvo y el
    # aplanado (pensado para una foto con estrellas) dejaba manchas al guardar (Pacman, 24-oct-2026)
    if ajustes.get("aplanar", False) and not sin_estrellas:
        rgb = aplanar_fondo(rgb, float(ajustes.get("escala_aplanado", 0.16)))
    if isinstance(balance, str):
        balance = balance_estrellas(rgb)
    if balance:
        log.debug("color: R x%.2f, B x%.2f (%d estrellas)", balance["r"], balance["b"], balance.get("n", 0))
    rgb = calibrar_color(rgb, balance)

    # Los dos estirados ponen el punto negro según el ruido del fondo: se mide
    # ANTES de reducirlo, para que el estirado sea el mismo con cualquier fuerza
    # de ruido (medido en la imagen limpia, el negro se pegaría al cielo)
    stf = str(ajustes.get("estirado", "multiescala")).lower() == "stf"
    if stf:
        # Estirado clásico (STF enlazado de AutoStrechSTF, el sistema anterior a
        # sep-2026): lo ofrece el editor de la galería para comparar con el nuevo.
        # Mismos parámetros que apply_stf_color(linked=True, neutralizar=True)
        from AutoStrechSTF import calcular_parametros_stf, aplicar_parametros_stf
        parametros_stf = calcular_parametros_stf(neutralizar_fondo(rgb).mean(axis=2), clipping=0.0018, target_median=0.10)
    else:
        sigma = sigma_fondo(rgb)

    avisar("ruido")
    rgb = reducir_ruido(rgb, ajustes.get("ruido", 0.5), progreso=lambda f: avisar("ruido", f))
    if sin_estrellas:
        rgb = suavizar_fondo_sin_estrellas(rgb, ajustes.get("ruido", 0.5), escala_imagen=escala,
                                           sigma_px=SIGMA_SUAVIZADO_SIN_ESTRELLAS)

    # Nitidez (deconvolución) en lineal, con el ruido ya reducido y antes de
    # estirar; 0 (lo normal) no hace nada. Sin estrellas no hay con qué medir la PSF.
    avisar("nitidez")
    try:
        nitidez = float(ajustes.get("nitidez", 0) or 0)
    except (TypeError, ValueError):
        nitidez = 0.0
    if nitidez > 0 and not sin_estrellas:
        rgb = deconvolucionar(rgb, nitidez, progreso=lambda f: avisar("nitidez", f), escala=escala)

    # Neutralización + estirado, en coma flotante; de aquí al final todo sigue
    # así y se cuantiza a 8 bits una sola vez, al salir de color()
    avisar("estirando")
    curvas = None
    if stf:
        rgbf = aplicar_parametros_stf(neutralizar_fondo(rgb), *parametros_stf).astype(np.float32)
        if sin_estrellas:
            # Para estirar la capa de estrellas hace falta una curva: la del multiescala con los ajustes
            L = np.clip(neutralizar_fondo(rgb) / 65535.0, 0, None).mean(axis=2)
            m, s = estadistica_fondo(L)
            S = CurvaFondo(m, sigma_fondo(rgb) or s, float(ajustes.get("fondo_objetivo", 0.13)),
                           float(ajustes.get("agresividad", 0.7)), str(ajustes.get("curva", "gamma")).lower())
            curvas = {"m": float(m), "sigma": float(s), "S_detalle": S, "S_curva": S,
                      "fondo_objetivo": float(ajustes.get("fondo_objetivo", 0.13)),
                      "agresividad": float(ajustes.get("agresividad", 0.7)), "tipo": S.tipo}
    else:
        rgbf, curvas = estirar_multiescala(rgb, ajustes, escala_imagen=escala, sigma=sigma,
                                           progreso=lambda f: avisar("estirando", f), devolver_curvas=True)
    avisar("contraste")
    rgbf = contraste_local(rgbf, ajustes.get("contraste", 0.5), proteger_cielo=sin_estrellas)
    avisar("color")
    return rgbf, curvas


def procesar(cubo_lineal, n_fotos=1, ajustes=None, escala=1.0, progreso=None, balance="auto", solo_base=False):
    """
    cubo_lineal: (3, H, W) o (H, W, 3) float lineal (el apilado).
    escala: 0.5 para la vista previa (4 veces más rápido), 1.0 para guardar.
    progreso(subfase, fraccion): opcional; se llama al empezar cada paso (y
    dentro del estirado) con la fracción 0..1 del procesado completo.
    balance: el de balance_estrellas() medido en el campo completo, cuando lo
    que llega aquí es un recorte de zoom; "auto" lo mide en la propia imagen y
    None deja el color como antes (fondo igualado multiplicando).
    Devuelve uint8 (h, w, 3) RGB listo para JPG. Con solo_base, se para antes
    de los ajustes rápidos y devuelve (base float32 [0, 1], estrellas, pequenas),
    lo que el editor de la galería manda al navegador para que los haga él.
    """
    ajustes = ajustes or leer_config()
    rgbf, __ = _estirar_objeto(cubo_lineal, ajustes, escala, progreso, balance)
    if solo_base:
        return color_base(rgbf, ajustes)
    return color(rgbf, ajustes)
