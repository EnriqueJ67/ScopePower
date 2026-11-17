# herramientas/probar_servidor.py
#
# Prueba de humo del servidor web, sin montura, cámara, ASTAP ni red: importa ScopePower en una
# copia temporal de la carpeta (como en un PC recién instalado, sin config.ini), pide las pantallas
# principales con el cliente de pruebas de Flask, en español y en inglés, y firma una foto
# sintética con Pillow. Falla si alguna página no responde 200 o si Flask, Werkzeug o Pillow avisan
# de algo que van a quitar.
#
# Existe porque compilar y pyflakes no importan Flask: una combinación de versiones rota
# (Flask 2.2 con Werkzeug 3 falla nada más importar) pasaba la comprobación en verde.
#
#   python herramientas\probar_servidor.py
#
# Sale con código 0 si todo va bien y 1 si algo falla. Lo ejecuta el workflow «Comprobar».

import os
import shutil
import sys
import tempfile
import time
import warnings

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Lo propio de cada PC y lo que pesa sin hacer falta: la prueba parte de un equipo recién instalado
NO_COPIAR = {".git", ".github", ".claude", ".vs", "__pycache__", "capturas", "calibraciones", "logs",
             "datos", "videos", "comparaciones", "fotos", "continuo", "timelapse", "evolucion",
             "seguimiento", "mosaico"}
NO_COPIAR_FICHEROS = {"config.ini", "secretos.ini", "presets_procesado.json", "copiar_a_campo.destino.txt"}

PANTALLAS = ["/", "/configuracion", "/goto", "/foto-prueba", "/apilado", "/galeria", "/planetario",
             "/esta-noche", "/seguimiento", "/calibracion", "/flats", "/alineacion", "/control"]


def preparar_copia():
    copia = os.path.join(tempfile.gettempdir(), "scopepower_prueba_servidor")
    shutil.rmtree(copia, ignore_errors=True)

    def ignorar(carpeta, nombres):
        return [n for n in nombres if n in NO_COPIAR or n in NO_COPIAR_FICHEROS or n.endswith((".fits", ".wcs"))]

    shutil.copytree(RAIZ, copia, ignore=ignorar)
    return copia


def main():
    # Lo que Flask, Werkzeug y Pillow anuncian que van a quitar es un fallo: así se ve antes de subir
    # de versión y no cuando ya no funciona
    for patron in (r".*will be removed in Pillow.*", r".*is deprecated and will be removed.*",
                   r".*will be removed in (Flask|Werkzeug).*"):
        warnings.filterwarnings("error", message=patron)

    copia = preparar_copia()
    os.chdir(copia)
    sys.path.insert(0, copia)
    t = time.time()
    import ScopePower as sp   # config.ini no existe: se crea desde config.example.ini
    print(f"ScopePower {sp.version.VERSION} importado en {time.time() - t:.1f} s")
    from importlib.metadata import version
    print("  " + " · ".join(f"{p} {version(p)}" for p in ("flask", "werkzeug", "jinja2", "Pillow", "requests")))

    fallos = []
    cliente = sp.app.test_client()

    def pedir(ruta, esperado=200):
        r = cliente.get(ruta)
        bien = r.status_code == esperado
        print(f"  {'✅' if bien else '❌'} {ruta} → {r.status_code}" + (f" (esperado {esperado})" if not bien else ""))
        if not bien:
            fallos.append(ruta)
        return r

    print("Sin sesión:")
    pedir("/login", 302)              # sin usuarios: lleva a la bienvenida
    pedir("/bienvenida")
    pedir("/static/logo.png")
    pedir("/static/css/scopepower.css")
    pedir("/", 302)                   # sin sesión: al login

    with cliente.session_transaction() as sesion:
        sesion["usuario"] = "prueba"
    for codigo in ("es", "en"):
        sp.idioma.cambiar(codigo)
        print(f"Con sesión, idioma {codigo}:")
        for ruta in PANTALLAS:
            pedir(ruta)
    sp.idioma.cambiar("es")

    print("Pestañas de la columna de controles (static/js/paneles.js):")
    import re
    pedir("/static/js/paneles.js")
    pedir("/static/js/ayudas.js")       # explicaciones plegadas tras un botón, en las mismas columnas
    for ruta, clave in (("/foto-prueba", "foto_paneles"), ("/apilado", "apilado_paneles")):
        html = cliente.get(ruta).get_data(as_text=True)
        n = len(re.findall(r'<section class="card panel" data-panel="', html))
        # Del sistema anterior (paneles plegables y reordenables) no debe quedar nada en la página
        bien = (f'data-paneles="{clave}"' in html and "js/paneles.js" in html and n >= 5
                and " data-ayudas>" in html and "js/ayudas.js" in html
                and "panel-botones" not in html and "alternarPanel(" not in html and "moverPanel(" not in html)
        print(f"  {'✅' if bien else '❌'} {ruta}: {n} paneles con pestaña")
        if not bien:
            fallos.append("pestañas de " + ruta)
    html = cliente.get("/foto-prueba").get_data(as_text=True)
    bien = 'id="disparador"' in html and "pulsarDisparador()" in html
    print(f"  {'✅' if bien else '❌'} /foto-prueba: disparador encima de la foto, para el móvil")
    if not bien:
        fallos.append("disparador de Cámara")
    r = cliente.post("/preferencias", json={"foto_paneles": {"activo": "dia"}})
    guardada = (cliente.get("/preferencias").get_json() or {}).get("foto_paneles")
    bien = r.status_code == 200 and guardada == {"activo": "dia"}
    print(f"  {'✅' if bien else '❌'} la pestaña abierta se guarda en /preferencias: {guardada}")
    if not bien:
        fallos.append("pestaña en /preferencias")

    print("Guía «¿Qué quieres hacer hoy?» (guia.py, static/js/guia.js):")
    pedir("/static/js/guia.js")
    html = cliente.get("/").get_data(as_text=True)
    e = cliente.get("/guia/estado").get_json() or {}
    menu = e.get("menu") or []
    baldosas = re.findall(r"""class="tile[^"]* tile-foto" style="--foto: url\('/static/guia/(\w+\.jpg)'\)\"""", html)
    fondos = [a["imagen"] for a in menu] + ["inicio.jpg"] + baldosas
    sin_fondo = [f for f in fondos if cliente.get("/static/guia/" + f).status_code != 200]
    bien = ('id="guia-franja"' in html and 'id="guia"' in html and 'id="guia-banda"' in html and "js/guia.js" in html
            and len(menu) == 7 and len(baldosas) == 12 and not e.get("activa") and not sin_fondo)
    print(f"  {'✅' if bien else '❌'} franja, ventana y banda en el inicio; {len(menu)} actividades y {len(baldosas)} baldosas con su foto"
          + (f" (faltan {sin_fondo})" if sin_fondo else ""))
    if not bien:
        fallos.append("guía en el inicio")
    ses = (cliente.post("/guia/empezar", json={"actividad": "cielo_profundo"}).get_json() or {}).get("sesion") or {}
    sin_equipo = ses.get("paso") == "equipo" and ses.get("total") == 8 and not ses.get("puede_seguir")
    cliente.post("/guia/paso", json={"accion": "ir", "paso": "objeto"})
    ses = (cliente.post("/guia/objeto", json={"nombre": "M 31", "ra_h": 0.712, "dec_deg": 41.27, "tipo": "Galaxia",
                                               "tamano_arcmin": 190}).get_json() or {}).get("sesion") or {}
    objeto = (ses.get("objeto") or {}).get("nombre") == "M 31" and ses.get("puede_seguir")
    ses = (cliente.post("/guia/captura", json={"exposicion": "12,5", "ganancia": "", "fotos": 40}).get_json() or {}).get("sesion") or {}
    captura = (ses.get("captura") or {}).get("exposicion") == 12.5 and (ses.get("captura") or {}).get("fotos") == 40
    rechazo = cliente.post("/guia/captura", json={"exposicion": "mucho", "fotos": 1}).status_code == 400
    sp.idioma.cambiar("en")
    en_ingles = "Deep sky" in [a["titulo"] for a in (cliente.get("/guia/estado").get_json() or {}).get("menu", [])]
    sp.idioma.cambiar("es")
    directo = cliente.post("/guia/empezar", json={"actividad": "calibracion"}).get_json() or {}
    acceso = directo.get("destino") == "/calibracion"
    cerrada = (cliente.post("/guia/terminar", json={}).get_json() or {}).get("activa") is False
    bien = sin_equipo and objeto and captura and rechazo and en_ingles and acceso and cerrada
    print(f"  {'✅' if bien else '❌'} cielo profundo: sin equipo no pasa del primer paso ({sin_equipo}), objeto ({objeto}), "
          f"exposición ({captura}, rechaza la mala: {rechazo}), menú en inglés ({en_ingles}), acceso directo a Calibración ({acceso}), cierre ({cerrada})")
    if not bien:
        fallos.append("rutas de la guía")

    print("Guía del Sol y filtro solar comprobado en el PC:")
    cliente.post("/filtro-solar", json={"confirmado": False})
    sin_filtro = [cliente.post(ruta, json=dict(cuerpo, objetivo="sol")) for ruta, cuerpo in (
        ("/foto-dia/iniciar", {}), ("/vivo/iniciar", {}), ("/timelapse/iniciar", {"fotos": 2, "intervalo": 5}),
        ("/continuo/iniciar", {"modo": "dia", "guardar": "no"}))]
    rechazadas = all(r.status_code == 409 and (r.get_json() or {}).get("confirmar_filtro_solar") for r in sin_filtro)
    luna_pasa = sp._falta_filtro_solar({}, "luna") is None and sp._falta_filtro_solar({}, "paisaje") is None
    ses = (cliente.post("/guia/empezar", json={"actividad": "sol"}).get_json() or {}).get("sesion") or {}
    prefs = cliente.get("/preferencias").get_json() or {}
    empieza = (ses.get("paso") == "filtro" and not ses.get("puede_seguir") and prefs.get("modo_dia") == "on"
               and prefs.get("foto_dia_objetivo") == "sol")
    confirmado = (cliente.post("/filtro-solar", json={"confirmado": True}).get_json() or {}).get("confirmado") is True
    ses = (cliente.get("/guia/estado").get_json() or {}).get("sesion") or {}
    sigue = bool(ses.get("puede_seguir"))
    cliente.post("/guia/paso", json={"accion": "ir", "paso": "modo"})
    ses = (cliente.post("/guia/modo", json={"modo": "timelapse"}).get_json() or {}).get("sesion") or {}
    modo = ses.get("modo") == "timelapse" and cliente.post("/guia/modo", json={"modo": "otro"}).status_code == 400
    hecho = cliente.post("/guia/hecho", json={"paso": "apuntar_disco", "nota": "prueba"}).status_code == 200 \
        and cliente.post("/guia/hecho", json={"paso": "equipo"}).status_code == 400
    cliente.post("/guia/terminar", json={})
    cliente.post("/filtro-solar", json={"confirmado": False})
    bien = rechazadas and luna_pasa and empieza and confirmado and sigue and modo and hecho
    print(f"  {'✅' if bien else '❌'} sin confirmar el filtro el PC no fotografía el Sol ({rechazadas}) y la Luna no lo pide ({luna_pasa}); "
          f"la guía del Sol empieza por el filtro ({empieza}), sigue al confirmarlo ({confirmado}, {sigue}), modo ({modo}), paso hecho ({hecho})")
    if not bien:
        fallos.append("guía del Sol y filtro solar")

    print("Firma de una foto sintética (Pillow):")
    import numpy as np
    import firma
    from datetime import datetime
    foto = (np.random.default_rng(1).random((900, 1200, 3)) * 60).astype(np.uint8)
    firmada = firma.firmar(foto, {"objeto": "Nebulosa de prueba", "autor": "Prueba", "latitud": 40.0,
                                  "longitud": -3.7, "fecha": datetime(2026, 10, 16, 22, 30), "n_fotos": 30,
                                  "exposicion": 20, "ra": 83.82, "dec": -5.39})
    bien = firmada.shape == foto.shape and not np.array_equal(firmada, foto)
    print(f"  {'✅' if bien else '❌'} firma pintada en {firmada.shape[1]}×{firmada.shape[0]}")
    if not bien:
        fallos.append("firma")

    print("Luna anotada en Cámara (fotograma del modo en vivo sintético):")
    import cv2
    sys.path.insert(0, os.path.join(copia, "herramientas"))
    from probar_luna_anotada import pintar
    # En vivo se anota con la hora actual y el observatorio de config.ini: la Luna sintética, igual
    obs = firma.datos_observatorio()
    geo = sp.luna_anotada.geometria(time.time(), obs["latitud"], obs["longitud"])
    __, jpg = cv2.imencode(".jpg", pintar(geo, 1052, 1052, 540, 500, 331, 120.0, True))
    original = sp.vivo_dia.trabajo_vivo.imagen
    sp.vivo_dia.trabajo_vivo.imagen = lambda despues=None, espera=3.0: (7, jpg.tobytes())
    try:
        if geo["iluminada"] < 0.25:
            # Cerca de la Luna nueva puede no haber Luna que anotar: basta con que la ruta conteste bien
            r = cliente.get("/camara/luna-anotada?vivo=1")
            bien = r.status_code in (200, 409) and (r.get_json() or {}).get("mensaje", "ok")
            print(f"  {'✅' if bien else '❌'} Luna casi nueva hoy ({100 * geo['iluminada']:.0f} %): la ruta contesta {r.status_code}")
        else:
            r = pedir("/camara/luna-anotada?vivo=1")
            d = r.get_json() or {}
            bien = d.get("ok") and d.get("luna") and len(d.get("objetos") or []) > 30 and abs(d["orientacion"]["giro"] - 120.0) < 1.0
            print(f"  {'✅' if bien else '❌'} {len(d.get('objetos') or [])} accidentes, giro {d.get('orientacion', {}).get('giro')}°, "
                  f"espejo {d.get('orientacion', {}).get('espejo')}")
        if not bien:
            fallos.append("luna anotada en Cámara")
    finally:
        sp.vivo_dia.trabajo_vivo.imagen = original

    print("Modo en vivo de noche (solo para enfocar una estrella, con el modo enfoque):")
    llamadas = []
    iniciar_original, es_sdk_original = sp.vivo_dia.trabajo_vivo.iniciar, sp.camara_sdk.es_sdk
    sp.vivo_dia.trabajo_vivo.iniciar = lambda camara, objetivo, campo, **kw: llamadas.append(objetivo)
    sp.camara_sdk.es_sdk = lambda driver: True
    try:
        sp.enfoque.poner(False)
        sin_enfoque = cliente.post("/vivo/iniciar", json={"objetivo": "paisaje", "noche": True})
        sp.enfoque.poner(True)
        con_sol = cliente.post("/vivo/iniciar", json={"objetivo": "sol", "noche": True})
        bueno = cliente.post("/vivo/iniciar", json={"objetivo": "paisaje", "noche": True})
        bien = (sin_enfoque.status_code == 409 and con_sol.status_code == 400 and bueno.status_code == 200
                and llamadas == ["paisaje"] and not (bueno.get_json() or {}).get("seguimiento"))
        print(f"  {'✅' if bien else '❌'} sin enfoque {sin_enfoque.status_code}, con el Sol {con_sol.status_code}, "
              f"con enfoque {bueno.status_code} {llamadas}")
        if not bien:
            fallos.append("vivo de noche")
    finally:
        sp.vivo_dia.trabajo_vivo.iniciar, sp.camara_sdk.es_sdk = iniciar_original, es_sdk_original
        sp.enfoque.poner(False)

    print("Número de Wolf en la galería (Sol sintético solo con JPG, sin crudo):")
    from probar_manchas_solares import sol_sintetico
    import numpy as np
    lum, __ = sol_sintetico()
    os.makedirs(os.path.join("static", "fotos"), exist_ok=True)
    nombre_sol = "Sol_20261023_120000.jpg"
    cv2.imwrite(os.path.join("static", "fotos", nombre_sol), (np.clip(lum / lum.max(), 0, 1) ** (1 / 2.2) * 255).astype(np.uint8),
                [cv2.IMWRITE_JPEG_QUALITY, 92])
    with open(os.path.join("static", "fotos", "Sol_20261023_120000.json"), "w", encoding="utf-8") as f:
        f.write('{"objeto": "Sol", "fecha": "23/10/2026 12:00", "latitud": 40.0, "longitud": -3.7}')
    r = pedir("/galeria")
    bien = nombre_sol in r.get_data(as_text=True)
    d = pedir("/galeria/anotaciones/" + nombre_sol + "?sensibilidad=normal").get_json() or {}
    bien = bien and d.get("ok") and d.get("sol") and d.get("origen") == "jpg" and d["wolf"]["g"] == 4 and d["wolf"]["f"] == 7
    bien = bien and all(g.get("largo_km") for g in d.get("grupos") or [])
    print(f"  {'✅' if bien else '❌'} conteo automático: {d.get('wolf')} ({d.get('origen')}), "
          f"grupos de {', '.join(str(g.get('largo_km')) for g in d.get('grupos') or [])} km")
    cuerpo = {"manchas": (d.get("manchas") or [])[:-1], "disco": d.get("disco"), "sensibilidad": "normal"}
    g = cliente.post("/galeria/wolf/" + nombre_sol, json=cuerpo).get_json() or {}
    o = cliente.post("/galeria/wolf/" + nombre_sol + "/olvidar", json={"sensibilidad": "alta"}).get_json() or {}
    bien2 = g.get("ok") and g.get("guardado") and g["wolf"]["f"] == 6 and o.get("ok") and not o.get("guardado") and o["wolf"]["f"] == 7
    print(f"  {'✅' if bien2 else '❌'} guardar ({g.get('wolf')}) y volver a contar ({o.get('wolf')})")
    r = pedir("/galeria")
    if not (bien and bien2):
        fallos.append("Número de Wolf")

    print("Apilado de un vídeo en la galería (nombre con _video): se ordena por su fecha:")
    nombre_av = "Sol_20261023_130000_video.jpg"
    shutil.copy(os.path.join("static", "fotos", nombre_sol), os.path.join("static", "fotos", nombre_av))
    try:
        html = pedir("/galeria").get_data(as_text=True)
        bien = 'data-objeto="Sol" data-fecha="20261023130000"' in html and "23/10/2026 · 13:00" in html
    finally:
        os.remove(os.path.join("static", "fotos", nombre_av))
    print(f"  {'✅' if bien else '❌'} tarjeta de {nombre_av} con objeto Sol y fecha 23/10/2026 13:00")
    if not bien:
        fallos.append("orden de los apilados de vídeo")

    print("Fotometría del Sol (la foto solo tiene JPG: el brillo no se mide sobre el JPG):")
    r = cliente.get("/galeria/sol/medidas/" + nombre_sol)
    d = r.get_json() or {}
    bien = r.status_code == 409 and not d.get("ok") and "crudo" in (d.get("mensaje") or "")
    print(f"  {'✅' if bien else '❌'} /galeria/sol/medidas/{nombre_sol} → {r.status_code}: {d.get('mensaje')}")
    e = pedir("/galeria/sol/medir/estado").get_json() or {}
    bien = bien and e.get("en_curso") is False
    if not bien:
        fallos.append("fotometría del Sol")

    print("Vídeos en la galería y su visor (SER sintético en videos/):")
    from probar_visor_video import escribir_video
    nombre_v = "Sol_20261025_160129"
    ruta_v = escribir_video(sp._config_video()["carpeta"], nombre_v)
    r = pedir("/galeria")
    html = r.get_data(as_text=True)
    bien = f'data-video="{nombre_v}"' in html and "/videos/miniatura/" + nombre_v in html
    for codigo in ("es", "en"):
        sp.idioma.cambiar(codigo)
        pedir("/galeria/video/" + nombre_v + "?f=70")
    sp.idioma.cambiar("es")
    bien = bien and pedir("/videos/miniatura/" + nombre_v).mimetype == "image/jpeg"
    bien = bien and pedir(f"/videos/fotograma/{nombre_v}/71?lado=320").mimetype == "image/jpeg"
    pedir(f"/videos/fotograma/{nombre_v}/9999", 404)
    d = cliente.post("/videos/analisis/" + nombre_v).get_json() or {}
    for __ in range(600):
        if d.get("estado") not in ("en_curso", "en_cola"):
            break
        time.sleep(0.1)
        d = cliente.get("/videos/analisis/" + nombre_v).get_json() or {}
    sucesos = (d.get("resultado") or {}).get("sucesos") or []
    bien = bien and d.get("estado") == "hecho" and len(sucesos) == 2 and all(0 <= s["px"] <= 1 and 0 <= s["py"] <= 1 for s in sucesos)
    print(f"  {'✅' if bien else '❌'} tarjeta en la galería, miniatura, fotograma y {len(sucesos)} sucesos ({d.get('estado')})")
    g = cliente.post("/videos/guardar-fotograma", json={"nombre": nombre_v, "fotograma": 71}).get_json() or {}
    archivo = g.get("archivo") or ""
    html = pedir("/galeria").get_data(as_text=True)
    bien2 = g.get("ok") and os.path.isfile(os.path.join("static", "fotos", archivo)) and f'"fotograma": 71' in html
    bien2 = bien2 and sp.editor_dia.es_foto_dia(archivo) and pedir("/galeria/editar-dia/" + archivo).status_code == 200
    b = cliente.post("/videos/borrar", json={"nombre": nombre_v}).get_json() or {}
    bien2 = bien2 and b.get("ok") and not any(os.path.exists(p) for p in [ruta_v] + sp.visor_video.archivos_auxiliares(ruta_v))
    print(f"  {'✅' if bien2 else '❌'} fotograma guardado en la galería ({archivo}), editable, y vídeo borrado con lo suyo")
    if not (bien and bien2):
        fallos.append("visor de vídeos")

    if fallos:
        print(f"\n❌ Fallan {len(fallos)}: {', '.join(fallos)}")
        return 1
    print("\n✅ Servidor correcto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
