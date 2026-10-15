"""
Defectos de una sola foto que estropean el apilado aunque la foto tenga buenas
estrellas: rastros de satélites y aviones, y luces parásitas (una linterna, los
faros de un coche) que solo salen en esa foto.

- detectar_rastro(plano): busca líneas rectas largas y finas. Se trabaja a media
  resolución; las manchas gordas (el núcleo de un cúmulo, una galaxia, las
  estrellas brillantes) se borran antes de buscar líneas para que no las
  confunda con un rastro.
- rejilla_fondo(plano) + detectar_luz(rejilla, historial, ruido): compara el
  fondo de cielo por zonas con el de las últimas fotos. Una luz que ilumina el
  tubo sube el fondo de golpe en parte de la foto; el cielo cambia despacio
  (crepúsculo, objeto que baja), y si el cambio se queda (una farola que se
  enciende) deja de marcarse en cuanto domina el historial.
- clasificar_rastro(peq, rastro): avión, satélite o posible meteoro, mirando el
  brillo a lo largo del rastro (ver la función).
- guardar_rastro(...): la foto descartada se guarda aparte (static/fotos/rastros)
  con una miniatura marcada y un JSON, para revisar si alguno era un meteoro.
"""

import json
import os
import shutil
from datetime import datetime

import cv2
import numpy as np
from idioma import N_

# Rastros (en píxeles de media resolución)
SIGMA_RASTRO = 2.0          # umbral sobre el fondo, en sigmas del ruido suavizado
ANCHO_MANCHA = 9            # lo que sobrevive a una apertura de este tamaño es "gordo"
FRACCION_LONGITUD = 0.20    # longitud mínima del rastro, fracción del lado corto
HUECO_MAX = 12              # huecos tolerados dentro de una línea (estrellas, parpadeo)

# Luces parásitas
CELDAS = 12                 # rejilla CELDAS × CELDAS de medianas del fondo
SIGMA_LUZ = 1.5             # subida del fondo por zonas, en sigmas del ruido por píxel
SALTO_GENERAL = 0.25        # subida de toda la foto a la vez, fracción del fondo
FOTOS_HISTORIAL = 3         # fotos anteriores con las que se compara

# Clasificación de rastros (px de media resolución)
FRANJA = 80                 # medio ancho de la franja enderezada (las luces de un avión van hasta ~40 px)
SUAVIZADO = 15              # mediana a lo largo del rastro: borra las estrellas que cruza
DESTELLOS_AVION = 3         # tramos mucho más brillantes que el resto: las luces que destellan
IRREGULARIDAD_METEORO = 0.35   # lo que el brillo se aparta de una rampa recta, fracción del brillo medio
TIPOS_RASTRO = {
    "avion": {"nombre": N_("Avión"), "icono": "✈️", "objeto": "Avión"},
    "satelite": {"nombre": N_("Satélite"), "icono": "🛰️", "objeto": "Satélite artificial"},
    "meteoro": {"nombre": N_("Posible meteoro"), "icono": "☄️", "objeto": "Meteoro"},
}

# Rastros guardados
CARPETA_RASTROS = os.path.join("static", "fotos", "rastros")
ANCHO_MINIATURA = 900


def media_resolucion(plano):
    img = np.asarray(plano, dtype=np.float32)
    return cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)


def mediana_mad(img):
    muestra = img[::4, ::4]
    mediana = float(np.median(muestra))
    mad = float(np.median(np.abs(muestra - mediana))) * 1.4826 + 1e-3
    return mediana, mad


def detectar_rastro(plano, peq=None):
    """
    Busca un rastro (línea recta larga y fina) en el plano (H, W).
    Devuelve None si no hay, o un dict con "longitud" (px de la foto completa),
    "angulo" (grados) y "segmento" (x1, y1, x2, y2 en px de la foto completa).
    peq: el plano ya reducido a media resolución, si se tiene.
    """
    if peq is None:
        peq = media_resolucion(plano)
    # Un suavizado ligero sube la señal de una línea de 2-3 px frente al ruido
    suave = cv2.GaussianBlur(peq, (0, 0), 1.2)
    mediana, mad = mediana_mad(suave)
    mascara = (suave > mediana + SIGMA_RASTRO * mad).astype(np.uint8)

    # Fuera lo gordo: una línea fina desaparece con la apertura, un cúmulo no.
    # Se dilata un poco para llevarse también el halo de las estrellas brillantes.
    nucleo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ANCHO_MANCHA, ANCHO_MANCHA))
    gordo = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, nucleo)
    gordo = cv2.dilate(gordo, nucleo)
    mascara[gordo > 0] = 0

    # Las estrellas sueltas son manchas pequeñas y redondas: fuera también,
    # quedan los trozos alargados (y el ruido, que no forma líneas largas)
    n, etiquetas, stats, __ = cv2.connectedComponentsWithStats(mascara, connectivity=8)
    if n > 1:
        ancho = stats[1:, cv2.CC_STAT_WIDTH]
        alto = stats[1:, cv2.CC_STAT_HEIGHT]
        cortos = np.concatenate([[False], np.maximum(ancho, alto) < 6])
        mascara[cortos[etiquetas]] = 0

    lado = min(peq.shape)
    longitud_min = FRACCION_LONGITUD * lado
    lineas = cv2.HoughLinesP(mascara, rho=1, theta=np.pi / 360, threshold=int(longitud_min * 0.5),
                             minLineLength=int(longitud_min), maxLineGap=HUECO_MAX)
    if lineas is None:
        return None
    x1, y1, x2, y2 = max(lineas[:, 0, :], key=lambda s: np.hypot(s[2] - s[0], s[3] - s[1]))
    longitud = float(np.hypot(x2 - x1, y2 - y1))
    return {
        "longitud": round(2 * longitud),
        "angulo": round(float(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180, 1),
        "segmento": [int(2 * x1), int(2 * y1), int(2 * x2), int(2 * y2)],
    }


def rejilla_fondo(peq):
    """Mediana del fondo en una rejilla CELDAS × CELDAS (robusta a las estrellas)."""
    h, w = peq.shape
    rejilla = np.empty((CELDAS, CELDAS), dtype=np.float32)
    for i in range(CELDAS):
        for j in range(CELDAS):
            celda = peq[i * h // CELDAS:(i + 1) * h // CELDAS:2, j * w // CELDAS:(j + 1) * w // CELDAS:2]
            rejilla[i, j] = np.median(celda)
    return rejilla


def detectar_luz(rejilla, historial, ruido):
    """
    ¿Se ha iluminado el fondo de golpe en esta foto? rejilla: la de esta foto;
    historial: rejillas de las fotos anteriores (misma exposición); ruido: sigma
    del ruido por píxel. Devuelve None o un dict con "subida" (en sigmas, la
    subida de la zona más iluminada sobre el resto de la foto, o la de toda la
    foto si el salto es general) y "zona" (fracción de la foto afectada).

    El cielo cambia casi igual en toda la foto (crepúsculo, amanecer, objeto
    que baja): esa parte común no cuenta mientras no sea un salto brusco. Una
    linterna o unos faros iluminan más un lado que otro.
    """
    if len(historial) < 1:
        return None
    referencia = np.median(np.stack(historial[-FOTOS_HISTORIAL:]), axis=0)
    diferencia = rejilla - referencia
    comun = float(np.median(diferencia))
    local = (diferencia - comun) / max(ruido, 1e-3)
    maxima = float(local.max())
    if maxima >= SIGMA_LUZ:
        return {"subida": round(maxima, 2), "zona": round(float((local >= SIGMA_LUZ).mean()), 2)}
    if comun > SALTO_GENERAL * float(np.median(referencia)):
        return {"subida": round(comun / max(ruido, 1e-3), 2), "zona": 1.0}
    return None


# ───────────────────────── Clasificación del rastro ─────────────────────────

def _tramo_en_foto(x0, y0, ux, uy, ancho, alto):
    """Valores de t con (x0 + t·ux, y0 + t·uy) dentro de la foto, o None."""
    tmin, tmax = -np.inf, np.inf
    for p, u, limite in ((x0, ux, ancho - 1), (y0, uy, alto - 1)):
        if abs(u) < 1e-9:
            if p < 0 or p > limite:
                return None
            continue
        a, b = -p / u, (limite - p) / u
        tmin, tmax = max(tmin, min(a, b)), min(tmax, max(a, b))
    return (tmin, tmax) if tmax - tmin > 10 else None


def _tramos(mascara):
    """Tramos seguidos a True: lista de (inicio, fin) con fin excluido."""
    d = np.diff(np.concatenate([[0], np.asarray(mascara, dtype=np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _mediana_movil(v, ventana):
    medio = ventana // 2
    relleno = np.pad(v, medio, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(relleno, ventana), axis=1)


def _franja(peq, x0, y0, ux, uy):
    """
    Endereza la foto alrededor de la recta: filas = posición a lo largo del rastro
    (de borde a borde de la foto), columnas = distancia a la recta (-FRANJA..FRANJA).
    A cada fila se le resta su fondo (lo que queda lejos de la recta).
    """
    alto, ancho = peq.shape
    tramo = _tramo_en_foto(x0, y0, ux, uy, ancho, alto)
    if tramo is None:
        return None, None
    t = np.arange(np.ceil(tramo[0]), np.floor(tramo[1]) + 1, dtype=np.float32)
    d = np.arange(-FRANJA, FRANJA + 1, dtype=np.float32)
    nx, ny = -uy, ux
    mx = (x0 + t[:, None] * ux + d[None, :] * nx).astype(np.float32)
    my = (y0 + t[:, None] * uy + d[None, :] * ny).astype(np.float32)
    franja = cv2.remap(peq, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    lejos = np.concatenate([franja[:, :FRANJA - 12], franja[:, FRANJA + 13:]], axis=1)
    return franja - np.median(lejos, axis=1)[:, None], t


def _afinar_recta(resto, t, x0, y0, ux, uy, ruido):
    """
    El segmento de Hough tiene el ángulo redondeado a medio grado: a lo largo de
    toda la foto la recta se desvía varios píxeles. Se busca el pico del rastro en
    bloques de la franja y se ajusta una recta a esas posiciones.
    """
    bloque = 64
    puntos = []
    for i in range(0, len(t) - bloque + 1, bloque):
        perfil = np.median(resto[i:i + bloque], axis=0)
        j = int(np.argmax(perfil[3:-3])) + 3
        if perfil[j] > 4.0 * 1.25 * ruido / np.sqrt(bloque):
            puntos.append((float(t[i:i + bloque].mean()), float(j - FRANJA)))
    if len(puntos) < 3:
        return x0, y0, ux, uy
    pt, pd = np.array(puntos).T
    for __ in range(2):   # ajuste, fuera los puntos que se van (la otra línea de un avión), y otra vez
        b, a = np.polyfit(pt, pd, 1)
        bien = np.abs(pd - (a + b * pt)) < 3
        if bien.sum() < 3:
            return x0, y0, ux, uy
        pt, pd = pt[bien], pd[bien]
    b, a = np.polyfit(pt, pd, 1)
    nx, ny = -uy, ux
    x0, y0 = x0 + a * nx, y0 + a * ny
    vx, vy = ux + b * nx, uy + b * ny
    norma = float(np.hypot(vx, vy))
    return x0, y0, vx / norma, vy / norma


def clasificar_rastro(peq, rastro):
    """
    Avión, satélite o posible meteoro, mirando la foto enderezada a lo largo del rastro:
    - avión: luces que destellan (tramos mucho más brillantes, varios) o varias
      líneas paralelas (luces de posición de las alas);
    - posible meteoro: el brillo cambia mucho a lo largo del rastro (se enciende,
      estalla, se apaga) sin destellos repetidos;
    - satélite: brillo casi uniforme. Un satélite bajo cruza el campo de borde a
      borde en pocos segundos; uno lento puede empezar y acabar dentro.
    No es infalible (un destello de satélite parece un meteoro y un meteoro que
    cruza un campo pequeño puede parecer uniforme): por eso "posible".
    peq: plano verde a media resolución; rastro: lo que devuelve detectar_rastro.
    Devuelve dict con "tipo" (clave de TIPOS_RASTRO), "nombre", "icono" y "rasgos".
    """
    peq = np.asarray(peq, dtype=np.float32)
    x1, y1, x2, y2 = [v / 2.0 for v in rastro["segmento"]]
    largo = float(np.hypot(x2 - x1, y2 - y1)) or 1.0
    ux, uy = (x2 - x1) / largo, (y2 - y1) / largo
    __, ruido = mediana_mad(peq)

    resto, t = _franja(peq, x1, y1, ux, uy)
    if resto is None:
        return _clase("satelite", {})
    x0, y0, ux, uy = _afinar_recta(resto, t, x1, y1, ux, uy, ruido)
    resto, t = _franja(peq, x0, y0, ux, uy)
    if resto is None:
        return _clase("satelite", {})
    c = FRANJA

    # Brillo a lo largo del rastro, sin las estrellas que cruza; el ruido se mide
    # igual en una banda paralela, en el borde de la franja (lejos de las luces de un avión)
    perfil = _mediana_movil(resto[:, c - 2:c + 3].mean(axis=1), SUAVIZADO)
    referencia = _mediana_movil(resto[:, -6:-1].mean(axis=1), SUAVIZADO)
    ruido_perfil = float(np.median(np.abs(referencia - np.median(referencia)))) * 1.4826 + 1e-3

    # Dónde está encendido (tolerando huecos) y el tramo más largo
    encendido = perfil > 3.0 * ruido_perfil
    for ini, fin in _tramos(~encendido):
        if 0 < ini and fin < len(encendido) and fin - ini <= 60:
            encendido[ini:fin] = True
    tramos = _tramos(encendido)
    if not tramos:
        return _clase("satelite", {})
    ini, fin = max(tramos, key=lambda r: r[1] - r[0])
    if fin - ini < 50:
        return _clase("satelite", {})
    margen = 15
    rasgos = {
        "longitud": int(2 * (fin - ini)),
        "empieza_dentro": bool(ini > margen),
        "termina_dentro": bool(fin < len(perfil) - margen),
    }
    tramo = perfil[ini:fin]
    base = float(np.median(tramo))

    # Cada columna de la franja (cada distancia a la recta) suavizada a lo largo
    # del rastro: sin estrellas, quedan las líneas y los destellos
    columnas = np.median(np.lib.stride_tricks.sliding_window_view(
        np.pad(resto, ((SUAVIZADO // 2, SUAVIZADO // 2), (0, 0)), mode="edge"), SUAVIZADO, axis=0), axis=-1)
    muestra = columnas[::3, ::3]
    ruido_columna = float(np.median(np.abs(muestra - np.median(muestra)))) * 1.4826 + 1e-3

    # Destellos: trazos muy por encima del brillo típico en cualquier carril
    # cercano (las luces de un avión no van todas por la misma línea), alargados
    # a lo largo del rastro: el halo de una estrella brillante es redondo
    brillante = (columnas[ini:fin] > 2.5 * max(base, 0.0) + 6.0 * ruido_columna).astype(np.uint8)
    n, __, stats, __ = cv2.connectedComponentsWithStats(brillante, connectivity=8)
    alto = np.zeros(fin - ini, dtype=bool)
    for k in range(1, n):
        largo_k, ancho_k = stats[k, cv2.CC_STAT_HEIGHT], stats[k, cv2.CC_STAT_WIDTH]
        if largo_k >= 15 and largo_k >= 3 * ancho_k:
            alto[stats[k, cv2.CC_STAT_TOP]:stats[k, cv2.CC_STAT_TOP] + largo_k] = True
    for i, f in _tramos(~alto):   # el ruido parte un pico en trozos: se vuelven a unir
        if 0 < i and f < len(alto) and f - i < 40:
            alto[i:f] = True
    destellos = _tramos(alto)
    rasgos["destellos"] = len(destellos)
    # Las luces de un avión destellan a ritmo fijo: separaciones parecidas
    centros = np.array([(i + f) / 2.0 for i, f in destellos])
    separaciones = np.diff(centros)
    regulares = len(separaciones) >= 2 and float(separaciones.std() / separaciones.mean()) < 0.4
    rasgos["destellos_regulares"] = bool(regulares)

    # Líneas paralelas: columnas iluminadas durante buena parte del recorrido,
    # separadas de la principal por una columna más oscura (el halo de una sola
    # línea gruesa baja sin hacer valle)
    iluminado = columnas[ini:fin] > 4.0 * ruido_columna
    cobertura = np.zeros(columnas.shape[1])
    for j in range(columnas.shape[1]):
        cobertura[j] = sum(f - i for i, f in _tramos(iluminado[:, j]) if f - i >= 10)
    minima = max(60, 0.1 * (fin - ini))
    paralelas = 0
    for j in range(5, len(cobertura) - 5):
        if abs(j - c) < 4 or cobertura[j] < minima or cobertura[j] < cobertura[j - 2:j + 3].max():
            continue
        # Estrecha como una línea (a 5 px ya no hay luz): el halo de una galaxia o
        # un degradado que acompañan al rastro un trecho son anchos
        if max(cobertura[j - 5], cobertura[j + 5]) >= 0.5 * cobertura[j]:
            continue
        entre = cobertura[min(j, c) + 1:max(j, c)]
        if len(entre) and entre.min() < 0.5 * cobertura[j]:
            paralelas += 1
    rasgos["paralelas"] = paralelas

    # Brillo por tramos (8) y cuánto se aparta de una rampa recta: un satélite se
    # apaga o se aviva poco a poco (fase, sombra de la Tierra, viñeteo); un meteoro
    # se enciende, estalla y se apaga. Solo cuenta si se sale del ruido de cada tramo.
    trozos = np.array([np.median(p) for p in np.array_split(tramo, 8)])
    x = np.arange(len(trozos))
    desvio = float(np.abs(trozos - np.polyval(np.polyfit(x, trozos, 1), x)).max())
    ruido_trozo = 1.25 * ruido_perfil / np.sqrt(max(len(tramo) / 8.0 / SUAVIZADO, 1.0))
    rasgos["irregularidad"] = round(desvio / max(float(trozos.mean()), ruido_perfil), 2)
    rasgos["irregularidad_sigmas"] = round(desvio / ruido_trozo, 1)

    # Extremos del tramo encendido en px de la foto completa (para marcarlo)
    pa, pb = float(t[ini]), float(t[fin - 1])
    rasgos["extremos"] = [int(2 * (x0 + pa * ux)), int(2 * (y0 + pa * uy)),
                          int(2 * (x0 + pb * ux)), int(2 * (y0 + pb * uy))]

    if (len(destellos) >= DESTELLOS_AVION and regulares) or paralelas:
        return _clase("avion", rasgos)
    if rasgos["irregularidad"] > IRREGULARIDAD_METEORO and rasgos["irregularidad_sigmas"] > 4:
        return _clase("meteoro", rasgos)
    return _clase("satelite", rasgos)


def _clase(tipo, rasgos):
    return dict(TIPOS_RASTRO[tipo], tipo=tipo, rasgos=rasgos)


# ───────────────────────── Guardar las fotos con rastro ─────────────────────────

COLORES_MARCA = {"avion": (60, 160, 255), "satelite": (255, 200, 80), "meteoro": (80, 255, 120)}   # BGR


def miniatura(r, g, b, rastro, clase):
    """JPG pequeño (BGR uint8) estirado deprisa, con el rastro marcado a los lados."""
    escala = ANCHO_MINIATURA / float(g.shape[1])
    tam = (ANCHO_MINIATURA, int(round(g.shape[0] * escala)))
    canales = [cv2.resize(np.asarray(c, dtype=np.float32), tam, interpolation=cv2.INTER_AREA) for c in (b, g, r)]
    __, mad = mediana_mad(canales[1])
    salida = []
    for c in canales:
        mediana, __ = mediana_mad(c)
        x = np.maximum((c - mediana) / mad + 2.0, 0.0)
        salida.append(np.clip(np.arcsinh(x / 3.0) / np.arcsinh(80.0), 0, 1))
    img = (np.dstack(salida) * 255).astype(np.uint8)

    x1, y1, x2, y2 = (clase.get("rasgos") or {}).get("extremos") or rastro["segmento"]
    p1, p2 = np.array([x1, y1], float) * escala, np.array([x2, y2], float) * escala
    u = (p2 - p1) / max(np.linalg.norm(p2 - p1), 1e-6)
    n = np.array([-u[1], u[0]])
    color = COLORES_MARCA.get(clase["tipo"], (255, 255, 255))
    for lado in (-1, 1):
        a, b_ = p1 + lado * 12 * n, p2 + lado * 12 * n
        cv2.line(img, tuple(int(v) for v in a), tuple(int(v) for v in b_), color, 1, cv2.LINE_AA)
    return img


def nombre_base(nombre_fits):
    """rastro_AAAAMMDD_HHMMSS a partir de img_AAAAMMDD_HHMMSS.fits (o de la hora actual)."""
    raiz = os.path.splitext(os.path.basename(nombre_fits))[0]
    partes = raiz.split("_")
    if len(partes) >= 3 and partes[-2].isdigit() and partes[-1].isdigit():
        return f"rastro_{partes[-2]}_{partes[-1]}"
    return f"rastro_{datetime.now():%Y%m%d_%H%M%S}"


def guardar_rastro(ruta_fits, r, g, b, rastro, clase, datos, copiar_fits=True, maximo=50,
                   carpeta=CARPETA_RASTROS):
    """
    Guarda la foto descartada: miniatura marcada (JPG), JSON con el tipo y los
    rasgos y, si copiar_fits, el FITS original (para pasarla a la galería o
    estudiarla). Luego deja como mucho `maximo` rastros. Devuelve el nombre base.
    """
    os.makedirs(carpeta, exist_ok=True)
    base = nombre_base(ruta_fits)
    ruta = os.path.join(carpeta, base)
    cv2.imwrite(ruta + ".jpg", miniatura(r, g, b, rastro, clase), [cv2.IMWRITE_JPEG_QUALITY, 82])
    if copiar_fits:
        shutil.copy2(ruta_fits, ruta + ".fits")
    meta = dict(datos)
    meta.update({
        "tipo": clase["tipo"], "nombre": clase["nombre"], "icono": clase["icono"],
        "longitud": (clase.get("rasgos") or {}).get("longitud", rastro["longitud"]),
        "angulo": rastro["angulo"], "rasgos": clase.get("rasgos") or {},
        "original": os.path.basename(ruta_fits),
    })
    with open(ruta + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    recortar(maximo, carpeta)
    return base


def listar(carpeta=CARPETA_RASTROS):
    """Rastros guardados (dicts del JSON + "base" y "fits"), del más reciente al más antiguo."""
    if not os.path.isdir(carpeta):
        return []
    lista = []
    for nombre in os.listdir(carpeta):
        if not (nombre.startswith("rastro_") and nombre.endswith(".json")):
            continue
        base = nombre[:-5]
        try:
            with open(os.path.join(carpeta, nombre), "r", encoding="utf-8") as f:
                datos = json.load(f)
        except Exception:
            continue
        datos["base"] = base
        datos["fits"] = os.path.isfile(os.path.join(carpeta, base + ".fits"))
        lista.append(datos)
    return sorted(lista, key=lambda d: d["base"], reverse=True)


def borrar(base, carpeta=CARPETA_RASTROS):
    for ext in (".jpg", ".json", ".fits"):
        try:
            os.remove(os.path.join(carpeta, base + ext))
        except FileNotFoundError:
            pass


def recortar(maximo, carpeta=CARPETA_RASTROS):
    """Deja como mucho `maximo` rastros: se van antes los aviones y satélites que los meteoros."""
    lista = listar(carpeta)
    sobran = len(lista) - max(1, int(maximo))
    if sobran <= 0:
        return
    orden = sorted(lista, key=lambda d: (d.get("tipo") == "meteoro", d["base"]))   # lo menos valioso y antiguo primero
    for d in orden[:sobran]:
        borrar(d["base"], carpeta)
