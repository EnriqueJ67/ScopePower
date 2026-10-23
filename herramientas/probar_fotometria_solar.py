# herramientas/probar_fotometria_solar.py
#
# Prueba de la fotometría del Sol (fotometria_solar.py) sin fotos reales ni Internet:
#   1. La física: la inversión de Eddington-Barbier devuelve la temperatura con la que se calculó el
#      brillo, y el achatamiento por la refracción crece al bajar el Sol.
#   2. Un Sol sintético con oscurecimiento del limbo conocido en cada canal, calima del 3 %,
#      achatamiento vertical del 0,4 %, desenfoque, ruido y nivel de negro, guardado como crudo Bayer
#      de una foto suelta: se recuperan el brillo a 0,95 radios en cada canal, la calima, el semieje
#      mayor, el achatamiento con su dirección y el radio en segundos de arco frente a la efeméride.
#   3. El mismo Sol como apilado de un vídeo (cubo en color); con calima del 12 % y con el centro
#      saturado, la medida sale marcada.
#   4. Las medidas se guardan y no se repiten mientras el crudo no cambie; la escala sale de la focal
#      si no hay plate solving.
#   5. La órbita: con una serie de radios de la efeméride a lo largo de 10 meses se recuperan la
#      excentricidad y la fecha del perihelio; con pocas semanas no se ajusta.
#
# Uso: python herramientas/probar_fotometria_solar.py   (desde la carpeta de ScopePower)

import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timezone

import numpy as np

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import datos_iers                      # noqa: E402
datos_iers.configurar()
import cv2                             # noqa: E402
from astropy.io import fits            # noqa: E402
import editor_fotos                    # noqa: E402
import editor_dia                      # noqa: E402
import orientacion_camara              # noqa: E402
import disco                           # noqa: E402
import fotometria_solar as F           # noqa: E402

fallos = []
ESCALA = 2.9109                        # ″/px, la del plate solving del mini PC (12-oct-2026)
LAT, LON = 39.63, -0.54
# Ley del limbo de cada canal (u, v) y brillo del centro (ADU), parecidos a los medidos el 10-oct-2026
CANALES = {"R": (0.45, 0.17, 40000.0), "G": (0.53, 0.15, 34000.0), "B": (0.61, 0.11, 10000.0)}


def comprobar(condicion, texto):
    print(("  OK   " if condicion else "  FALLO ") + texto)
    if not condicion:
        fallos.append(texto)


# ─── 1. Física ───────────────────────────────────────────────────────────────
def probar_fisica():
    print("Física: Eddington-Barbier y refracción")
    taus = np.array(F.TAUS)
    t = F.t_eddington(taus)
    for c, lam in F.LAMBDA_EF_UM.items():
        planck = lambda T: 1.0 / np.expm1(F.C2_UM_K / (lam * T))
        cociente = planck(t) / planck(F.t_eddington(1.0))
        rec = F.temperatura_eddington_barbier(cociente, lam)
        comprobar(np.max(np.abs(rec - t)) < 0.5, f"canal {c}: la inversión devuelve la temperatura del modelo (error {np.max(np.abs(rec - t)):.3f} K)")
    comprobar(abs(float(F.t_eddington(1.0)) - 6103) < 2 and abs(float(F.t_eddington(2 / 3)) - F.TEFF) < 0.01,
              f"modelo de Eddington: {float(F.t_eddington(1.0)):.0f} K a τ = 1 y Teff a τ = 2/3")
    # Los brillos medidos el 10-oct-2026 a 0,95 radios dan la misma temperatura en los tres canales
    mu = float(F.mu_de_rho(0.95))
    ts = {c: float(F.temperatura_eddington_barbier(i, F.LAMBDA_EF_UM[c])) for c, i in (("R", 0.600), ("G", 0.558), ("B", 0.522))}
    comprobar(max(ts.values()) - min(ts.values()) < 40, f"brillos reales del 10-oct a μ = {mu:.2f}: {ts} K, coinciden")
    a15, a45, a80 = (F.achatamiento_refraccion(h) for h in (15, 45, 80))
    comprobar(0.0035 < a15 < 0.0045 and 0.0003 < a45 < 0.0008 and a80 < a45 < a15 and F.achatamiento_refraccion(1.0) is None,
              f"achatamiento por refracción: {a15 * 100:.2f} % a 15°, {a45 * 100:.3f} % a 45°, {a80 * 100:.4f} % a 80°")


# ─── 2–3. Soles sintéticos ───────────────────────────────────────────────────
def sol_sintetico(lado, cx, cy, a, achatamiento=0.004, calima=0.03, sigma=1.5, ruido=0.005, semilla=0, saturar=False):
    """Planos R, G, B (ADU, sin negro) de un Sol elíptico (eje menor vertical) con limbo oscurecido y calima."""
    b = a * (1.0 - achatamiento)
    yy, xx = np.mgrid[0:lado, 0:lado].astype(np.float64)
    d = np.hypot((xx - cx) / a, (yy - cy) / b)
    cobertura = np.clip((1.0 - d) * a + 0.5, 0.0, 1.0)
    mu = np.sqrt(np.clip(1.0 - d * d, 0.0, 1.0))
    rng = np.random.default_rng(semilla)
    planos = {}
    for c, (u, v, i0) in CANALES.items():
        p = i0 * (1.0 - u * (1.0 - mu) - v * (1.0 - mu) ** 2) * cobertura + calima * i0
        if saturar:
            p *= 2.2
        p = cv2.GaussianBlur(p.astype(np.float32), (0, 0), sigma)
        planos[c] = p * (1.0 + rng.normal(0.0, ruido, p.shape).astype(np.float32))
    return planos


def a_cfa(planos, negro):
    h, w = planos["G"].shape
    cfa = np.zeros((h, w), np.float32)
    cfa[0::2, 0::2] = planos["R"][0::2, 0::2]
    cfa[0::2, 1::2] = planos["G"][0::2, 1::2]
    cfa[1::2, 0::2] = planos["G"][1::2, 0::2]
    cfa[1::2, 1::2] = planos["B"][1::2, 1::2]
    return np.clip(cfa + negro, 0, 65535).astype(np.uint16)


def verdad_i95(c):
    u, v, __ = CANALES[c]
    return float(F.ley_limbo(F.mu_de_rho(0.95), u, v))


def galeria_falsa():
    raiz = tempfile.mkdtemp(prefix="scopepower_fotometria_")
    fotos = os.path.join(raiz, "fotos")
    crudos = os.path.join(fotos, "crudos")
    os.makedirs(crudos)
    editor_fotos.CARPETA_FOTOS = fotos
    editor_fotos.CARPETA_ORIGINALES = os.path.join(fotos, "originales")
    editor_dia.CARPETA_CRUDOS = crudos
    F.RUTA_MEDIDAS = os.path.join(raiz, "datos", "medidas_sol.json")
    editor_dia.olvidar_cache()
    return raiz, fotos, crudos


def utc(*a):
    """Instante unix de una fecha y hora UTC. Las fotos de prueba llevan el instante en su JSON: con la
    hora del nombre (local) la prueba dependería del huso horario del PC (en GitHub es UTC)."""
    return datetime(*a, tzinfo=timezone.utc).timestamp()


def guardar_foto(fotos, crudos, base, datos, cabecera, instante):
    hdu = fits.PrimaryHDU(datos)
    for k, v in cabecera.items():
        hdu.header[k] = v
    hdu.writeto(os.path.join(crudos, base + ".fits"), overwrite=True)
    cv2.imwrite(os.path.join(fotos, base + ".jpg"), np.full((64, 64, 3), 128, np.uint8))
    with open(os.path.join(fotos, base + ".json"), "w", encoding="utf-8") as f:
        json.dump({"objeto": "Sol", "latitud": LAT, "longitud": LON, "instante": instante, "firmada": False}, f)
    return base + ".jpg"


def probar_soles():
    print("Soles sintéticos: foto suelta (Bayer) y apilado de vídeo")
    raiz, fotos, crudos = galeria_falsa()
    orientacion_camara.ultima = lambda buscar=True: {"escala_arcsec": ESCALA, "binning_foto": 1}
    base = "Sol_20261010_180632"
    instante = utc(2026, 10, 10, 16, 6, 32)             # 18:06 en España: el Sol a 15° en Valencia
    r_efem = disco.radio_esperado_arcsec("sol", instante, LAT, LON, 0.0)
    a = r_efem / ESCALA                                    # el radio en píxeles que daría la efeméride
    cx, cy = 612.3, 597.8
    planos = sol_sintetico(1216, cx, cy, a)
    cab = {"EXPTIME": 0.0004, "BAYERPAT": "RGGB", "MODO": "dia", "OBJETIVO": "sol", "CAMPO": 1.0, "NEGRO": 1500.0,
           "XBINNING": 1, "XPIXSZ": 3.76}
    nombre = guardar_foto(fotos, crudos, base, a_cfa(planos, 1500.0), cab, instante)
    d = F.medir(nombre, (LAT, LON))
    i95 = {c: d["ajuste"][c]["i95"] for c in "RGB"}
    comprobar(all(abs(i95[c] - verdad_i95(c)) < 0.01 for c in "RGB"),
              f"brillo a 0,95 radios: {i95} (verdad { {c: round(verdad_i95(c), 4) for c in 'RGB'} })")
    # La calima es cielo / centro tal como salen en la foto (el centro lleva también el velo): 0,03 / 1,03
    comprobar(abs(d["calima"] - 0.03 / 1.03) < 0.004 and not d["avisos"], f"calima {d['calima']} (verdad {0.03 / 1.03:.4f}), sin avisos {d['avisos']}")
    dd, di = d["disco"], d["diametro"]
    comprobar(abs(dd["cx"] - cx) < 0.2 and abs(dd["cy"] - cy) < 0.2, f"centro ({dd['cx']}, {dd['cy']}) (verdad {cx}, {cy})")
    comprobar(abs(dd["a"] - a) < 0.35 and di["medida"] == "elipse", f"semieje mayor {dd['a']} px (verdad {a:.3f})")
    comprobar(abs(di["achatamiento"] - 0.4) < 0.1 and abs(((dd["angulo_menor"] - 90 + 90) % 180) - 90) < 10,
              f"achatamiento {di['achatamiento']} % (verdad 0,4) con el eje menor a {dd['angulo_menor']}° (vertical: 90°)")
    # El punto de inflexión de un limbo oscurecido y emborronado queda un poco hacia dentro: −0,1 % aquí
    # (σ = 1,5 px) y −0,08 % de media en las fotos reales; el margen deja sitio a otras versiones de OpenCV
    comprobar(abs(di["desvio_pct"]) < 0.2 and di["escala_origen"] == "plate_solving" and abs(di["distancia"] - di["distancia_efem"]) < 0.002,
              f"radio {di['r_arcsec']}″ frente a {di['r_efem']}″ de la efeméride ({di['desvio_pct']} %), distancia {di['distancia']} ua")
    comprobar(di["altura"] is not None and abs(di["altura"] - 15.0) < 1.0 and abs(di["achatamiento_previsto"] - 0.41) < 0.03,
              f"altura del Sol {di['altura']}° (unos 15°), achatamiento previsto {di['achatamiento_previsto']} %")
    t = d["temperatura"]
    comprobar(len(t["tau"]) == len(t["R"]) == len(t["eddington"]) and all(4000 < x < 7500 for x in t["R"] + t["G"] + t["B"]),
              f"temperaturas a τ = 0,3: {t['t03']} K")
    # 4. Guardadas: la segunda vez no se mide; si cambia el crudo, sí
    comprobar(os.path.isfile(F.RUTA_MEDIDAS) and F.guardada(nombre) is not None and F.pendientes([nombre]) == [], "medida guardada en datos/medidas_sol.json")
    F.medir_foto = lambda *a_, **k_: (_ for _ in ()).throw(AssertionError("no debería volver a medir"))
    try:
        d2 = F.medir(nombre, (LAT, LON))
        comprobar(d2["disco"]["a"] == dd["a"], "la segunda vez sale lo guardado")
    except AssertionError as e:
        comprobar(False, str(e))
    import importlib
    importlib.reload(F)
    F.RUTA_MEDIDAS = os.path.join(raiz, "datos", "medidas_sol.json")
    os.utime(os.path.join(crudos, base + ".fits"), ns=(1, 1))
    comprobar(F.pendientes([nombre]) == [nombre], "con el crudo cambiado hay que volver a medir")
    s = F.serie({nombre})
    comprobar(len(s) == 1 and s[0]["fiable"] is True, f"serie: {len(s)} foto fiable")

    # 3. El apilado de un vídeo: cubo en color normalizado al blanco
    base_v = "Sol_20261017_131524_video"
    instante_v = utc(2026, 10, 17, 11, 15, 24)
    a_v = disco.radio_esperado_arcsec("sol", instante_v, LAT, LON, 0.0) / ESCALA
    pv = sol_sintetico(760, 380.4, 371.1, a_v, achatamiento=0.0007, semilla=2)
    m = max(float(p.max()) for p in pv.values())
    cubo = np.stack([np.clip(pv[c] / m * 65535.0, 0, 65535).astype(np.uint16) for c in "RGB"])
    nombre_v = guardar_foto(fotos, crudos, base_v, cubo, {"MODO": "video", "OBJETIVO": "sol", "XBINNING": 1, "XPIXSZ": 3.76}, instante_v)
    dv = F.medir(nombre_v, (LAT, LON))
    comprobar(all(abs(dv["ajuste"][c]["i95"] - verdad_i95(c)) < 0.01 for c in "RGB") and abs(dv["diametro"]["desvio_pct"]) < 0.1,
              f"apilado de vídeo: i95 { {c: dv['ajuste'][c]['i95'] for c in 'RGB'} }, radio {dv['diametro']['desvio_pct']} % de la efeméride")

    # Con calima del 12 %, y con el centro saturado, la medida sale marcada
    base_c = "Sol_20260927_130740"
    instante_c = utc(2026, 9, 27, 11, 7, 40)
    a_c = disco.radio_esperado_arcsec("sol", instante_c, LAT, LON, 0.0) / ESCALA
    nombre_c = guardar_foto(fotos, crudos, base_c, a_cfa(sol_sintetico(1216, 600, 600, a_c, calima=0.12, semilla=3), 1500.0), cab, instante_c)
    dc = F.medir(nombre_c, (LAT, LON))
    comprobar("calima" in dc["avisos"] and abs(dc["calima"] - 0.12 / 1.12) < 0.01, f"velo del 12 %: calima {dc['calima']}, avisos {dc['avisos']}")
    base_s = "Sol_20261017_130747"
    nombre_s = guardar_foto(fotos, crudos, base_s, a_cfa(sol_sintetico(1216, 600, 600, a_v, saturar=True, semilla=4), 1500.0), cab,
                            utc(2026, 10, 17, 11, 7, 47))
    ds = F.medir(nombre_s, (LAT, LON))
    comprobar("saturado" in ds["avisos"], f"centro saturado: avisos {ds['avisos']}")
    s = F.serie()
    comprobar(len(s) == 4 and sum(x["fiable"] for x in s) == 2, f"serie: {len(s)} fotos, {sum(x['fiable'] for x in s)} fiables")

    # La escala, de la focal si no hay plate solving
    cfg = os.path.join(raiz, "config.ini")
    with open(cfg, "w") as f:
        f.write("[Dispositivos]\nfocal_len = 260\n")
    orientacion_camara.ultima = lambda buscar=True: None
    esc, origen = F.escala_de_placa({"XBINNING": 2, "XPIXSZ": 7.52}, cfg)
    comprobar(origen == "focal" and abs(esc - 206.264806 * 7.52 / 260) < 1e-6, f"sin plate solving: {esc:.4f}″/px de la focal")
    comprobar(F.escala_de_placa({"XBINNING": 1}, os.path.join(raiz, "no_existe.ini")) == (None, ""), "sin plate solving ni focal: sin escala")

    # Un sensor monocromo: un solo plano
    pm = sol_sintetico(600, 300, 300, 200.0, semilla=5)
    dm = F.medir_planos({"L": pm["G"]})
    comprobar(dm["canales"] == ["L"] and abs(dm["ajuste"]["L"]["i95"] - verdad_i95("G")) < 0.01, f"monocromo: i95 {dm['ajuste']['L']['i95']}")
    shutil.rmtree(raiz, ignore_errors=True)


# ─── 5. La órbita ────────────────────────────────────────────────────────────
def probar_orbita():
    print("La órbita de la Tierra a partir de los radios")
    rng = np.random.default_rng(7)
    t0 = datetime(2026, 2, 1).timestamp()
    ts = t0 + np.arange(0, 300, 12) * 86400.0
    rs = F.semidiametro_geocentrico(ts) + rng.normal(0, 0.4, len(ts))
    serie = [{"instante": float(t), "r_arcsec": float(r), "fiable": True} for t, r in zip(ts, rs)]
    serie.append({"instante": t0 + 40 * 86400.0, "r_arcsec": 990.0, "fiable": False})      # una con calima: no cuenta
    o = F.ajustar_orbita(serie)
    dia = datetime.fromisoformat(o["perihelio"]).timetuple().tm_yday if o else None
    comprobar(o is not None and abs(o["e"] - 0.0167) < 0.0015 and dia is not None and (dia <= 12 or dia >= 360),
              f"excentricidad {o and o['e']} (real 0,0167), perihelio {o and o['perihelio']} (real, hacia el 3 de enero)")
    comprobar(F.ajustar_orbita(serie[:4]) is None, "con pocas semanas de fotos no se ajusta")
    curva = F.curva_anual([{"instante": t0}, {"instante": t0 + 86400.0}])
    rmax, rmin = max(c["r"] for c in curva), min(c["r"] for c in curva)
    jul = datetime.fromtimestamp(min(curva, key=lambda c: c["r"])["instante"]).month
    comprobar(70 <= len(curva) <= 75 and 974 < rmax < 977 and 943 < rmin < 946.5 and jul == 7,
              f"curva del año: {len(curva)} puntos, de {rmin:.1f}″ (julio) a {rmax:.1f}″ (enero)")


if __name__ == "__main__":
    probar_fisica()
    probar_soles()
    probar_orbita()
    if fallos:
        print(f"\n{len(fallos)} comprobaciones fallidas")
        sys.exit(1)
    print("\nFotometría del Sol: todo bien")
