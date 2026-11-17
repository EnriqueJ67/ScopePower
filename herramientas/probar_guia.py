# herramientas/probar_guia.py
#
# Prueba de la guía «¿Qué quieres hacer hoy?» (guia.py) sin equipo, sin Flask y sin tocar
# datos/: la sesión se guarda en una carpeta temporal y los datos del equipo se inventan.
# Comprueba:
#   - el menú: orden y chips de día y de noche,
#   - la sesión: empezar, avanzar, retroceder, saltar solo lo saltable, guardar en disco
#     y caducar a las 12 h,
#   - los semáforos de cada paso de cielo profundo (sin montura, disco lleno, sin dark,
#     dark lejos de la temperatura, sin flat, objeto que no cabe, centrado...),
#   - las rutas de día: el Sol no pasa del primer paso sin el filtro solar confirmado, el
#     disco bajo el horizonte, centrar a mano, el paisaje sin montura y foto/vídeo/timelapse,
#   - los cálculos: altura de un objeto, estrella para enfocar y exposición recomendada.
#
# Uso:
#   python herramientas\probar_guia.py

import os
import sys
import tempfile

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RAIZ)

import guia   # noqa: E402

fallos = []


def comprobar(condicion, texto):
    print(("  ok    " if condicion else "  FALLO ") + texto)
    if not condicion:
        fallos.append(texto)


def nivel(lista, clave):
    for c in lista:
        if c["clave"] == clave:
            return c["nivel"]
    return None


def datos_base(**cambios):
    """Una noche con todo en orden: equipo conectado, disco libre, dark y flat hechos."""
    d = {
        "montura": True, "camara": True, "conectando": False, "modo_montura": "altacimutal",
        "disco": {"ok": True, "libre_gb": 120.0, "bajo": False, "sin_espacio": False},
        "de_dia": False, "noche_desde": "21:40", "altura_luna": 30.0, "altura_minima": 20.0,
        "lat": 39.47, "lon": -0.38, "ahora": 1_800_000_000.0,
        "alineacion": {"en_curso": False, "terminado": False, "resultado": ""},
        "puesta_estacion": {"en_curso": False, "terminado": False, "resultado": ""},
        "captura": {"exposicion": 15.0, "ganancia": 300, "binning": 1, "fotos": 100},
        "dark": {"existe": True, "antiguedad_min": 30, "lejos": False, "diferencia": 1.0},
        "flat": {"existe": True, "antiguedad_min": 4000},
        "trabajo_darks": {"en_curso": False},
        "campo": {"ancho_deg": 2.5, "alto_deg": 2.5},
        "ultimo_goto": None, "moviendose": False, "centrado": {}, "centrado_objetivo": None,
        "bucle_activo": False, "trabajo": None,
        "filtro_solar": {"confirmado": False, "hora": None}, "altura_sol": 40.0,
        "foto_dia": {"en_curso": False}, "orientacion_camara": True, "video_disponible": True,
    }
    d.update(cambios)
    return d


def semaforos(datos):
    return guia.comprobaciones(guia.sesion(), guia.hechos(), datos)


def probar_menu():
    print("Menú")
    noche = guia.menu(datos_base())
    dia = guia.menu(datos_base(de_dia=True))
    comprobar(noche[0]["clave"] == "cielo_profundo", "de noche, cielo profundo va primero")
    comprobar(dia[0]["clave"] == "sol", "de día, el Sol va primero")
    comprobar({a["clave"] for a in noche} == {a["clave"] for a in guia.ACTIVIDADES}, "el menú trae todas las actividades")
    sol_noche = next(a for a in noche if a["clave"] == "sol")
    comprobar(sol_noche["apagada"], "de noche el Sol sale apagado")
    cp_dia = next(a for a in dia if a["clave"] == "cielo_profundo")
    comprobar(cp_dia["chip"]["nivel"] == "warn" and "21:40" in cp_dia["chip"]["texto"], "de día, cielo profundo dice desde qué hora")
    comprobar(all(os.path.exists(os.path.join(RAIZ, "static", "guia", a[k])) for a in noche for k in ("imagen", "imagen_ancha")),
              "cada tarjeta tiene su fondo en static/guia, también el apaisado de la primera")
    comprobar(dia[0]["imagen_ancha"] == "sol_ancho.jpg" and noche[0]["imagen_ancha"] == "inicio.jpg",
              "la primera tarjeta (Sol de día, cielo profundo de noche) lleva un recorte apaisado propio")


def probar_sesion():
    print("Sesión")
    comprobar(guia.empezar("galeria") is None and guia.sesion() is None, "un acceso directo no abre sesión")
    try:
        guia.empezar("no_existe")
        comprobar(False, "una actividad desconocida se rechaza")
    except ValueError:
        comprobar(True, "una actividad desconocida se rechaza")
    ses = guia.empezar("cielo_profundo")
    comprobar(ses["paso"] == "equipo", "cielo profundo empieza en el equipo")
    comprobar(guia.avanzar()["paso"] == "alineacion", "siguiente lleva a la alineación")
    comprobar(guia.avanzar(-1)["paso"] == "equipo", "atrás vuelve al equipo")
    try:
        guia.avanzar(saltar=True)
        comprobar(False, "el equipo no se puede saltar")
    except ValueError:
        comprobar(True, "el equipo no se puede saltar")
    guia.avanzar()
    ses = guia.avanzar(saltar=True)
    comprobar(ses["paso"] == "objeto" and "alineacion" in ses["saltados"], "la alineación se salta y queda apuntada")
    est = guia.estado(datos_base())
    comprobar(est["sesion"]["pasos"][1]["estado"] == "saltado", "el paso saltado sale como saltado")
    comprobar(est["sesion"]["indice"] == 2 and est["sesion"]["total"] == 8, "la sesión cuenta 8 pasos")
    guia.olvidar_todo()
    comprobar((guia.sesion() or {}).get("paso") == "objeto", "la sesión sobrevive a un reinicio (datos/guia.json)")
    guia.marcar("enfoque")
    reloj = guia._ahora
    guia._ahora = lambda: reloj() + guia.CADUCA_S + 60
    comprobar(guia.sesion() is None and not guia.hechos(), "a las 12 h caducan la sesión y lo marcado")
    guia._ahora = reloj


def probar_pasos():
    print("Semáforos de cielo profundo")
    guia.empezar("cielo_profundo")
    lista, puede = semaforos(datos_base())
    comprobar(puede and nivel(lista, "montura") == "ok", "equipo: con todo conectado se puede seguir")
    lista, puede = semaforos(datos_base(montura=False))
    comprobar(not puede and nivel(lista, "montura") == "err", "equipo: sin montura no se sigue")
    lista, puede = semaforos(datos_base(disco={"ok": True, "libre_gb": 0.4, "bajo": True, "sin_espacio": True}))
    comprobar(not puede and nivel(lista, "disco") == "err", "equipo: con el disco lleno no se sigue")
    lista, puede = semaforos(datos_base(de_dia=True))
    comprobar(puede and nivel(lista, "noche") == "warn", "equipo: de día avisa, pero deja seguir")

    guia.avanzar()
    lista, puede = semaforos(datos_base())
    comprobar(not puede and nivel(lista, "alineacion") == "warn", "alineación: sin constancia, aviso")
    guia.estado(datos_base(alineacion={"en_curso": False, "terminado": True, "resultado": "completada"}))
    lista, puede = semaforos(datos_base())
    comprobar(puede and nivel(lista, "alineacion") == "ok", "alineación: una alineación terminada se apunta sola")
    lista, __ = semaforos(datos_base(modo_montura="ecuatorial_alemana"))
    comprobar(nivel(lista, "puesta_estacion") == "warn", "alineación: en ecuatorial pide la puesta en estación")

    guia.avanzar()
    lista, puede = semaforos(datos_base())
    comprobar(not puede and nivel(lista, "objeto") == "falta", "objeto: sin elegir no se sigue")
    guia.fijar_objeto("M 31", 0.712, 41.27, "Galaxia", 190)
    lista, puede = semaforos(datos_base())
    comprobar(puede and nivel(lista, "mosaico") == "warn", "objeto: Andrómeda no cabe en 2,5° y propone mosaico")
    guia.fijar_mosaico(True)
    lista, __ = semaforos(datos_base())
    comprobar(nivel(lista, "mosaico") == "ok", "objeto: con mosaico elegido, en verde")
    guia.fijar_objeto("M 57", 18.893, 33.03, "Nebulosa", 1.4)
    lista, __ = semaforos(datos_base())
    comprobar(nivel(lista, "mosaico") is None, "objeto: la nebulosa del Anillo cabe de sobra")
    try:
        guia.fijar_objeto("X", 30, 0)
        comprobar(False, "objeto: coordenadas imposibles se rechazan")
    except ValueError:
        comprobar(True, "objeto: coordenadas imposibles se rechazan")

    guia.avanzar()
    lista, puede = semaforos(datos_base())
    comprobar(puede and nivel(lista, "captura") == "ok", "captura: 15 s en altacimutal, bien")
    lista, __ = semaforos(datos_base(captura={"exposicion": 60.0, "ganancia": 300, "fotos": 100}))
    comprobar(nivel(lista, "captura") == "warn", "captura: 60 s en altacimutal, aviso")
    lista, __ = semaforos(datos_base(captura={"exposicion": 60.0, "ganancia": 300, "fotos": 100}, modo_montura="ecuatorial"))
    comprobar(nivel(lista, "captura") == "ok", "captura: 60 s en ecuatorial, bien")

    guia.avanzar()
    lista, puede = semaforos(datos_base())
    comprobar(puede and nivel(lista, "dark") == "ok" and nivel(lista, "flat") == "ok", "calibración: con dark y flat, en verde")
    lista, __ = semaforos(datos_base(dark={"existe": False}, flat={"existe": False}))
    comprobar(nivel(lista, "dark") == "warn" and nivel(lista, "flat") == "warn", "calibración: sin dark ni flat, dos avisos")
    lista, __ = semaforos(datos_base(dark={"existe": True, "lejos": True, "diferencia": -7.0}))
    comprobar(nivel(lista, "dark") == "warn", "calibración: dark a 7 °C del sensor, aviso")
    lista, puede = semaforos(datos_base(trabajo_darks={"en_curso": True, "completadas": 3, "total": 10}))
    comprobar(not puede and nivel(lista, "dark") == "pendiente", "calibración: mientras se hacen darks no se sigue")

    guia.avanzar()
    lista, puede = semaforos(datos_base())
    comprobar(not puede and nivel(lista, "enfoque") == "warn", "enfoque: sin constancia, aviso")
    guia.marcar("enfoque")
    lista, puede = semaforos(datos_base())
    comprobar(puede and nivel(lista, "enfoque") == "ok", "enfoque: marcado como hecho, en verde")

    guia.avanzar()
    aqui = {"ra_h": 18.893, "dec_deg": 33.03}
    lista, puede = semaforos(datos_base())
    comprobar(not puede and lista[0]["accion"] == "goto" and lista[0]["nivel"] == "falta", "apuntar: sin GoTo, ofrece ir")
    lista, puede = semaforos(datos_base(ultimo_goto={"ra_h": 5.0, "dec_deg": 10.0}))
    comprobar(not puede, "apuntar: un GoTo a otro sitio (la estrella del enfoque) no cuenta")
    lista, puede = semaforos(datos_base(ultimo_goto=aqui, moviendose=True))
    comprobar(not puede and nivel(lista, "apuntar") == "pendiente", "apuntar: con la montura en marcha, espera")
    lista, puede = semaforos(datos_base(ultimo_goto=aqui, centrado={"en_curso": True, "mensaje": "Vuelta 1"}))
    comprobar(not puede and lista[0]["texto"] == "Vuelta 1", "apuntar: durante el centrado enseña su mensaje")
    lista, puede = semaforos(datos_base(ultimo_goto=aqui, centrado_objetivo=(18.893, 33.03),
                                        centrado={"resultado": "centrado", "error_final": 0.8}))
    comprobar(puede and nivel(lista, "apuntar") == "ok", "apuntar: centrado, en verde")
    lista, puede = semaforos(datos_base(ultimo_goto=aqui, centrado_objetivo=(5.0, 10.0),
                                        centrado={"resultado": "centrado", "error_final": 0.8}))
    comprobar(nivel(lista, "apuntar") == "warn", "apuntar: el centrado de otro objeto no vale")
    lista, puede = semaforos(datos_base(ultimo_goto=aqui, centrado_objetivo=(18.893, 33.03),
                                        centrado={"resultado": "error", "detalle": "ASTAP no resolvió"}))
    comprobar(not puede and nivel(lista, "apuntar") == "err", "apuntar: si el centrado falla, no se sigue sin reintentar")

    guia.avanzar()
    est = guia.estado(datos_base(ultimo_goto=aqui))
    comprobar(est["activa"] and est["sesion"]["pantalla"] == "/apilado", "listo: el último paso lleva a Apilado")
    est = guia.estado(datos_base(ultimo_goto=aqui, bucle_activo=True))
    comprobar(est["terminada"] and not est["activa"], "listo: al arrancar el apilado la guía se cierra sola")


def probar_rutas_de_dia():
    print("Rutas de día: Sol, Luna y paisaje")
    confirmado = {"confirmado": True, "hora": "12:30"}
    guia.marcar("enfoque", False)      # lo que quedara apuntado de la ruta de cielo profundo
    ses = guia.empezar("sol")
    comprobar(ses["paso"] == "filtro" and ses["modo"] == "foto", "el Sol empieza por el filtro solar")
    lista, puede = semaforos(datos_base(de_dia=True))
    comprobar(not puede and nivel(lista, "filtro") == "err", "Sol: sin confirmar el filtro no se sigue")
    try:
        guia.avanzar(saltar=True)
        comprobar(False, "Sol: el filtro solar no se puede saltar")
    except ValueError:
        comprobar(True, "Sol: el filtro solar no se puede saltar")
    lista, puede = semaforos(datos_base(de_dia=True, filtro_solar=confirmado))
    comprobar(puede and nivel(lista, "filtro") == "ok" and "12:30" in lista[0]["texto"], "Sol: con el filtro confirmado, en verde con su hora")
    guia.avanzar()
    lista, puede = semaforos(datos_base(de_dia=True, filtro_solar=confirmado))
    comprobar(puede and nivel(lista, "noche") is None, "Sol: en el equipo no avisa de que es de día")
    guia.avanzar()
    lista, puede = semaforos(datos_base(de_dia=True, filtro_solar=confirmado))
    comprobar(not puede and lista[0]["nivel"] == "falta" and lista[0]["accion"] == "goto_disco", "Sol: ofrece ir al disco y centrarlo")
    lista, puede = semaforos(datos_base(de_dia=True))
    comprobar(not puede and lista[0]["nivel"] == "err", "Sol: si el filtro deja de estar confirmado, no se apunta")
    lista, puede = semaforos(datos_base(de_dia=True, filtro_solar=confirmado, altura_sol=-5.0))
    comprobar(not puede and lista[0]["nivel"] == "err", "Sol: bajo el horizonte no se apunta")
    lista, __ = semaforos(datos_base(de_dia=True, filtro_solar=confirmado, moviendose=True))
    comprobar(lista[0]["nivel"] == "pendiente", "Sol: con la montura en marcha, espera")
    lista, __ = semaforos(datos_base(de_dia=True, filtro_solar=confirmado, orientacion_camara=False))
    comprobar(nivel(lista, "orientacion") == "warn", "Sol: sin orientación de la cámara avisa de que habrá que centrar a mano")
    guia.paso_hecho("apuntar_disco", "Montura sincronizada con el Sol: se equivocaba 12,3′.")
    lista, puede = semaforos(datos_base(de_dia=True, filtro_solar=confirmado))
    comprobar(puede and lista[0]["nivel"] == "ok" and "12,3" in lista[0]["texto"], "Sol: centrado, en verde con el resultado")
    try:
        guia.paso_hecho("equipo")
        comprobar(False, "solo se dan por hechos los pasos que se hacen a mano")
    except ValueError:
        comprobar(True, "solo se dan por hechos los pasos que se hacen a mano")
    guia.avanzar()
    guia.avanzar(saltar=True)
    ses = guia.sesion()
    comprobar(ses["paso"] == "modo" and "enfoque" in ses["saltados"], "Sol: el enfoque se puede saltar")
    guia.fijar_modo("video")
    lista, puede = semaforos(datos_base(de_dia=True, filtro_solar=confirmado, video_disponible=False))
    comprobar(puede and nivel(lista, "video") == "warn", "Sol: vídeo sin cámara por SDK, aviso")
    try:
        guia.fijar_modo("pelicula")
        comprobar(False, "un modo desconocido se rechaza")
    except ValueError:
        comprobar(True, "un modo desconocido se rechaza")
    guia.fijar_modo("timelapse")
    guia.avanzar()
    est = guia.estado(datos_base(de_dia=True, filtro_solar=confirmado))
    comprobar(est["sesion"]["paso"] == "listo_camara" and est["sesion"]["modo"] == "timelapse" and est["sesion"]["astro"] == "sol"
              and est["sesion"]["total"] == 6, "Sol: seis pasos y al final sabe qué se va a hacer")
    lista = est["sesion"]["comprobaciones"]
    comprobar(nivel(lista, "filtro") == "ok" and nivel(lista, "apuntado") == "ok" and nivel(lista, "enfoque") == "warn",
              "Sol: el resumen final repasa filtro, disco y enfoque")

    ses = guia.empezar("luna")
    comprobar(ses["paso"] == "equipo", "la Luna empieza por el equipo, sin filtro")
    guia.avanzar()
    lista, puede = semaforos(datos_base(altura_luna=-10.0))
    comprobar(not puede and lista[0]["nivel"] == "err", "Luna: bajo el horizonte no se apunta")
    lista, puede = semaforos(datos_base(altura_luna=35.0))
    comprobar(not puede and lista[0]["nivel"] == "falta", "Luna: sobre el horizonte, ofrece ir a ella sin pedir filtro")

    ses = guia.empezar("paisaje")
    lista, puede = semaforos(datos_base(de_dia=True, montura=False))
    comprobar(puede and nivel(lista, "montura") == "warn", "paisaje: sin montura se puede seguir")
    lista, puede = semaforos(datos_base(de_dia=True, camara=False))
    comprobar(not puede, "paisaje: sin cámara no")
    guia.avanzar()
    lista, puede = semaforos(datos_base(de_dia=True))
    comprobar(not puede and lista[0]["nivel"] == "falta", "paisaje: falta encuadrar")
    guia.paso_hecho("encuadre")
    lista, puede = semaforos(datos_base(de_dia=True))
    comprobar(puede and lista[0]["nivel"] == "ok", "paisaje: encuadrado, en verde")
    guia.terminar()


def probar_calculos():
    print("Cálculos")
    # La Polar está siempre a la altura de la latitud (± lo que dista del polo)
    alt = guia.altura(2.5303, 89.264, 39.47, -0.38, 1_800_000_000.0)
    comprobar(abs(alt - 39.47) < 1.0, "la Polar sale a la altura de la latitud (%.1f°)" % alt)
    comprobar(abs(guia.separacion(0.0, 0.0, 6.0, 0.0) - 90.0) < 1e-6, "separación entre dos puntos del ecuador a 6 h: 90°")
    for ts in range(1_800_000_000, 1_800_000_000 + 86400, 3600):
        estrella = guia.estrella_para_enfocar(39.47, -0.38, float(ts))
        if not estrella or not (guia.ALTURA_ENFOQUE[0] <= estrella["altura"] <= guia.ALTURA_ENFOQUE[1]):
            comprobar(False, "hay estrella para enfocar a cualquier hora (falla a las %d)" % ts)
            break
    else:
        comprobar(True, "hay estrella para enfocar a cualquier hora, a una altura cómoda")
    comprobar(guia.exposicion_recomendada("altacimutal", 50)["maximo"] == 25.0, "altacimutal: hasta 25 s")
    comprobar(guia.exposicion_recomendada("altacimutal", 80)["maximo"] == 10.0, "altacimutal cerca del cénit: hasta 10 s")
    comprobar(guia.exposicion_recomendada("ecuatorial", 80)["maximo"] == 60.0, "ecuatorial: hasta 60 s")
    comprobar(guia.cabe_en_el_campo(None, {"ancho_deg": 2, "alto_deg": 2}) is None, "sin tamaño conocido no se opina del campo")


def main():
    with tempfile.TemporaryDirectory() as carpeta:
        guia.RUTA = os.path.join(carpeta, "datos", "guia.json")
        guia.olvidar_todo()
        probar_menu()
        probar_sesion()
        probar_pasos()
        probar_rutas_de_dia()
        probar_calculos()
    if fallos:
        print("\n%d comprobaciones fallidas:" % len(fallos))
        for f in fallos:
            print("  - " + f)
        sys.exit(1)
    print("\nTodo bien.")


if __name__ == "__main__":
    main()
