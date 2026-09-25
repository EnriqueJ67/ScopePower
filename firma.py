"""
Firma de las fotos, al estilo de la que pone el Seestar pero con más datos:
banda inferior con degradado oscuro sobre la imagen; a la izquierda el logo de
ScopePower y, en pequeño, autor · lugar · fecha; a la derecha el objeto en
grande, el tiempo total de integración (con el detalle de fotos × exposición)
y las coordenadas AR/Dec.

Se aplica a la vista previa del apilado y al JPG que va a la galería. El FITS
se guarda siempre limpio, así que la foto se puede reprocesar sin firma.
Sustituye al marco blanco antiguo (frame_and_text.py).

El lugar se redondea a grados enteros, como hace el Seestar, para no publicar
la posición exacta del observatorio al compartir la foto.
"""
import configparser
import json
import os
import re
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

RUTA_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo_transparente.png")
FUENTES = {
    False: ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
    True: ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
}
_fuentes = {}
_logo = None


# ───────────────────────── Configuración ─────────────────────────
def datos_observatorio(cfg=None):
    """Autor, lugar y si la firma está activa, leídos de config.ini (o del ConfigParser dado)."""
    if cfg is None:
        cfg = configparser.ConfigParser()
        cfg.read("config.ini")
    obs = cfg["Observatorio"] if "Observatorio" in cfg else {}
    api = cfg["Apilado"] if "Apilado" in cfg else {}
    # "marco_decorativo" era el nombre del interruptor del marco blanco antiguo
    activa = str(api.get("firma", api.get("marco_decorativo", "true"))).strip().lower() == "true"
    try:
        lat, lon = float(obs.get("latitud_observatorio", 0)), float(obs.get("longitud_observatorio", 0))
    except ValueError:
        lat, lon = 0.0, 0.0
    return {"autor": str(obs.get("autor", "")).strip(), "latitud": lat, "longitud": lon, "activa": activa}


# ───────────────────────── Textos ─────────────────────────
def texto_lugar(lat, lon):
    """39.95, -1.11 -> '40° N, 1° W' (grados enteros, como el Seestar)."""
    if lat is None or lon is None or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
        return ""
    return f"{abs(round(lat))}° {'N' if lat >= 0 else 'S'}, {abs(round(lon))}° {'E' if lon >= 0 else 'W'}"


def texto_integracion(n_fotos, exposicion):
    """(30, 20) -> ('10 min', '30 × 20 s'). Sin exposición conocida -> ('8 fotos', '')."""
    n = int(n_fotos or 0)
    exp = float(exposicion or 0)
    if n <= 0:
        return "", ""
    if exp <= 0:
        return f"{n} foto{'s' if n != 1 else ''}", ""
    if n == 1 and exp < 1:
        # Foto suelta de Cámara (de día, milisegundos): solo su exposición
        return (f"{exp * 1e6:.0f} µs" if exp < 0.001 else f"{exp * 1000:.3g} ms"), ""
    total = n * exp
    if total >= 3600:
        h, m = divmod(round(total / 60), 60)
        principal = f"{h} h {m} min" if m else f"{h} h"
    elif total >= 60:
        principal = f"{round(total / 60)} min"
    else:
        principal = f"{total:g} s"
    return principal, f"{n} × {exp:g} s"


def texto_coordenadas(ra, dec):
    """'16 25 13.9', '-26 35 15.8' -> 'AR 16h 25m · Dec −26° 35′'. Cadena vacía si no se pueden leer."""
    def partes(valor):
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", str(valor or ""))
        return nums[:2] if len(nums) >= 2 else None
    r, d = partes(ra), partes(dec)
    if not r or not d:
        return ""
    signo = "−" if str(dec).strip().startswith("-") else "+"
    return f"AR {int(float(r[0]))}h {int(float(r[1])):02d}m · Dec {signo}{abs(int(float(d[0])))}° {int(float(d[1])):02d}′"


def texto_fecha(fecha):
    if isinstance(fecha, datetime):
        return fecha.strftime("%d/%m/%Y %H:%M")
    return str(fecha or "")


# ───────────────────────── Dibujo ─────────────────────────
def _fuente(tamano, negrita=False):
    tamano = max(8, int(tamano))
    clave = (tamano, negrita)
    if clave not in _fuentes:
        fuente = None
        for nombre in FUENTES[negrita]:
            try:
                fuente = ImageFont.truetype(nombre, tamano)
                break
            except OSError:
                continue
        if fuente is None:
            try:
                fuente = ImageFont.load_default(size=tamano)
            except TypeError:
                fuente = ImageFont.load_default()
        _fuentes[clave] = fuente
    return _fuentes[clave]


def _logo_rgba():
    global _logo
    if _logo is None:
        _logo = Image.open(RUTA_LOGO).convert("RGBA")
    return _logo


def _texto(draw, xy, texto, fuente, color, anchor, sombra):
    if not texto:
        return
    x, y = xy
    draw.text((x + sombra, y + sombra), texto, font=fuente, fill=(0, 0, 0, 200), anchor=anchor)
    draw.text((x, y), texto, font=fuente, fill=color, anchor=anchor)


def firmar(imagen_bgr, meta):
    """
    Devuelve la imagen BGR con la firma en la banda inferior.
    meta: objeto, autor, latitud, longitud, fecha (datetime o texto), n_fotos,
    exposicion (s), ra, dec. Todos opcionales: lo que falte no se pinta.
    """
    rgb = cv2.cvtColor(imagen_bgr, cv2.COLOR_BGR2RGB) if imagen_bgr.ndim == 3 else cv2.cvtColor(imagen_bgr, cv2.COLOR_GRAY2RGB)
    img = Image.fromarray(rgb).convert("RGBA")
    w, h = img.size
    s = min(w, h) / 1600.0           # escala respecto a una foto de 1600 px
    m = int(40 * s)                  # margen
    sombra = max(1, int(2 * s))

    # Degradado oscuro en la parte baja (transparente arriba, casi opaco abajo)
    alto_deg = int(0.24 * min(w, h))
    t = np.linspace(0.0, 1.0, alto_deg, dtype=np.float32) ** 1.6
    alfa = (t * 215).astype(np.uint8)
    capa = np.zeros((alto_deg, w, 4), dtype=np.uint8)
    capa[:, :, 3] = alfa[:, None]
    img.alpha_composite(Image.fromarray(capa, "RGBA"), (0, h - alto_deg))

    draw = ImageDraw.Draw(img)
    blanco, gris = (255, 255, 255, 255), (205, 205, 205, 255)

    # ── Bloque derecho: objeto, integración, coordenadas ──
    y = h - m
    coords = texto_coordenadas(meta.get("ra"), meta.get("dec"))
    if coords:
        _texto(draw, (w - m, y), coords, _fuente(24 * s), gris, "rs", sombra)
        y -= int(24 * s + 12 * s)
    principal, detalle = texto_integracion(meta.get("n_fotos"), meta.get("exposicion"))
    if principal:
        linea = principal + (f"  ·  {detalle}" if detalle else "")
        _texto(draw, (w - m, y), linea, _fuente(34 * s), blanco, "rs", sombra)
        y -= int(34 * s + 14 * s)
    objeto = str(meta.get("objeto") or "").strip()
    if objeto:
        tam = 56 * s
        f_obj = _fuente(tam, negrita=True)
        while draw.textlength(objeto, font=f_obj) > 0.55 * w and tam > 20 * s:   # nombres largos: se encoge
            tam *= 0.9
            f_obj = _fuente(tam, negrita=True)
        _texto(draw, (w - m, y), objeto, f_obj, blanco, "rs", sombra)

    # ── Bloque izquierdo: logo y autor · lugar · fecha ──
    y = h - m
    partes = [p for p in (meta.get("autor"), texto_lugar(meta.get("latitud"), meta.get("longitud")),
                          texto_fecha(meta.get("fecha"))) if p]
    if partes:
        _texto(draw, (m, y), "  ·  ".join(partes), _fuente(26 * s), gris, "ls", sombra)
        y -= int(26 * s + 18 * s)
    logo = _logo_rgba()
    alto_logo = int(66 * s)
    ancho_logo = int(logo.width * alto_logo / logo.height)
    if ancho_logo > 0 and alto_logo > 0:
        logo_r = logo.resize((ancho_logo, alto_logo), Image.LANCZOS)
        img.alpha_composite(logo_r, (m, y - alto_logo))

    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


# ───────────────────────── Metadatos de una foto de la galería ─────────────────────────
def meta_de_archivo(ruta_jpg):
    """
    Datos para firmar una foto ya guardada: del JSON hermano si existe (fotos guardadas
    con firma), si no del nombre del archivo (Objeto_AAAAMMDD_HHMMSS.jpg) y de la
    cabecera del FITS hermano (coordenadas, nº de fotos y tiempo total).
    """
    base = os.path.splitext(ruta_jpg)[0]
    ruta_json = base + ".json"
    if os.path.isfile(ruta_json):
        try:
            with open(ruta_json, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    meta = {"firmada": False}
    nombre = os.path.basename(base)
    partes = nombre.rsplit("_", 2)
    if len(partes) == 3 and len(partes[1]) == 8 and len(partes[2]) == 6:
        meta["objeto"] = partes[0].replace("_", " ")
        try:
            meta["fecha"] = datetime.strptime(partes[1] + partes[2], "%Y%m%d%H%M%S").strftime("%d/%m/%Y %H:%M")
        except ValueError:
            pass
    else:
        meta["objeto"] = nombre.replace("_", " ")
    ruta_fits = base + ".fits"
    if os.path.isfile(ruta_fits):
        try:
            from astropy.io import fits
            hdr = fits.getheader(ruta_fits)
            meta["ra"], meta["dec"] = hdr.get("OBJCTRA"), hdr.get("OBJCTDEC")
            n = int(hdr.get("NSTACK", 0) or 0)
            total = float(hdr.get("EXPTOT", 0) or 0)
            meta["n_fotos"] = n
            meta["exposicion"] = round(total / n, 3) if n and total else 0
        except Exception:
            pass
    return meta


def guardar_meta(ruta_jpg, meta):
    """Escribe el JSON hermano del JPG (fecha como texto, sin objetos no serializables)."""
    datos = {k: (texto_fecha(v) if k == "fecha" else v) for k, v in meta.items() if k != "activa"}
    with open(os.path.splitext(ruta_jpg)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
