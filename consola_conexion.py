# consola_conexion.py
#
# Quien arranca ScopePower por primera vez ve una ventana negra llena de mensajes y no sabe
# cómo entrar en la app. Este módulo deja en esa consola, al terminar el arranque:
#
#   - un recuadro con las direcciones para el móvil o la tablet y un código QR de cada red
#     (dos como mucho, uno al lado del otro: el móvil solo llega a la dirección de la Wi-Fi en
#     la que está; se repite de vez en cuando hasta que alguien se conecta desde otro dispositivo),
#   - la dirección en el título de la ventana, que se ve en la barra de tareas,
#   - avisos si el cortafuegos de Windows no va a dejar entrar al móvil,
#   - una cuenta atrás que ofrece abrir la app en el navegador de este PC (por defecto no),
#   - un acceso directo «ScopePower en el navegador» en el escritorio, una sola vez, en los PC
#     instalados con instalar_scopepower.bat (llega también con «Actualizar desde GitHub»).
#
# El QR se genera con segno (Python puro). Si falta (instalación antigua sin actualizar las
# dependencias), todo lo demás sale igual, solo que sin el código.
#
# Opciones en config.ini, sección [Red]:
#     preguntar_navegador = true     cuenta atrás para abrir el navegador del PC al arrancar

import configparser
import ctypes
import io
import logging
import os
import sys
import threading
import time

from idioma import _, N_

# No «scopepower.consola...»: registro.py no saca por la consola los loggers con ese prefijo
log = logging.getLogger("scopepower.acceso_movil")

SEGUNDOS_PREGUNTA = 15
# Repeticiones del recuadro mientras nadie se conecta: al minuto y luego cada 5 minutos
REPETIR_PRIMERA_S = 60
REPETIR_CADA_S = 300
REGLA_CORTAFUEGOS = "ScopePower {puerto}"
NOMBRE_ACCESO_LNK = "ScopePower.lnk"           # el que crea instalar_scopepower.bat
MARCADOR_ACCESO = os.path.join("datos", "acceso_navegador_creado.txt")

ANCHO = 68
# Códigos QR del recuadro: uno por red, como mucho dos (caben uno al lado del otro)
MAX_QR = 2


# ─── Código QR en texto ──────────────────────────────────────────────────────

def matriz_qr(texto, borde=2):
    """Filas de 0/1 (1 = módulo oscuro) con su margen claro, o None si no hay segno."""
    try:
        import segno
    except ImportError:
        return None
    qr = segno.make(texto, error="m", micro=False)
    return [[1 if m else 0 for m in fila] for fila in qr.matrix_iter(scale=1, border=borde)]


def qr_en_texto(texto, borde=2):
    """
    Líneas de texto con el QR dibujado con medios bloques (dos filas del código por línea).
    Se pinta lo CLARO con bloques, pensado para letra blanca sobre fondo negro: así el código
    sale con la polaridad normal (oscuro sobre claro) que leen todos los móviles.
    Devuelve [] si no hay segno.
    """
    filas = matriz_qr(texto, borde)
    if not filas:
        return []
    if len(filas) % 2:
        filas.append([0] * len(filas[0]))      # el margen es claro
    lineas = []
    for arriba, abajo in zip(filas[0::2], filas[1::2]):
        car = []
        for a, b in zip(arriba, abajo):
            claro_a, claro_b = not a, not b
            car.append("█" if claro_a and claro_b else "▀" if claro_a else "▄" if claro_b else " ")
        lineas.append("".join(car))
    return lineas


def qr_svg(texto, escala=6, borde=2):
    """El QR como SVG (bytes) para la web, o None si no hay segno."""
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(texto, error="m", micro=False).save(buf, kind="svg", scale=escala, border=borde,
                                                     dark="#000", light="#fff", xmldecl=False)
    return buf.getvalue()


# ─── Cortafuegos de Windows ──────────────────────────────────────────────────

_PERFILES = ((1, N_("Dominio")), (2, N_("Privada")), (4, N_("Pública")))
_TCP, _CUALQUIERA = 6, 256
_ENTRADA = 1
_PERMITIR, _BLOQUEAR = 1, 0


def _puerto_incluido(puertos, puerto):
    """LocalPorts de una regla ('*', '5000', '5000-5020', '80,443') ¿incluye el puerto?"""
    puertos = (puertos or "").strip()
    if puertos in ("", "*"):
        return True
    for trozo in puertos.split(","):
        trozo = trozo.strip()
        if "-" in trozo:
            a, __, b = trozo.partition("-")
            if a.isdigit() and b.isdigit() and int(a) <= puerto <= int(b):
                return True
        elif trozo.isdigit() and int(trozo) == puerto:
            return True
    return False


def evaluar_cortafuegos(reglas, perfiles_activos, cortafuegos_activo, ejecutables, puerto,
                        bloquea_todo=None, entrada_permitida=None):
    """
    Decide si el móvil podrá entrar al puerto. Función pura (la prueba sin Windows).
      reglas:            dicts con nombre, entrada, activa, accion, protocolo, puertos, programa, perfiles
      perfiles_activos:  máscara de los perfiles de las redes conectadas (1 dominio, 2 privada, 4 pública)
      cortafuegos_activo:{bit: bool}
      ejecutables:       rutas del Python que escucha (en minúsculas)
    Devuelve una lista de (codigo, perfil) con los problemas: 'bloqueado' o 'sin_permiso'.
    """
    bloquea_todo = bloquea_todo or {}
    entrada_permitida = entrada_permitida or {}
    problemas = []
    for bit, perfil in _PERFILES:
        if not perfiles_activos & bit or not cortafuegos_activo.get(bit, False):
            continue
        if entrada_permitida.get(bit):
            continue
        if bloquea_todo.get(bit):
            problemas.append(("bloqueado", perfil))
            continue
        bloqueada = permitida = False
        for r in reglas:
            if not r.get("entrada") or not r.get("activa") or not (r.get("perfiles", 0) & bit):
                continue
            if r.get("protocolo") not in (_TCP, _CUALQUIERA):
                continue
            if not _puerto_incluido(r.get("puertos"), puerto):
                continue
            programa = (r.get("programa") or "").lower()
            es_nuestro = programa and programa in ejecutables
            puerto_explicito = (r.get("puertos") or "").strip() not in ("", "*")
            # Una regla sin programa solo cuenta si nombra el puerto (las de «todo» llevan
            # otras condiciones, IP o interfaz, que aquí no se miran)
            if not (es_nuestro or (not programa and puerto_explicito)):
                continue
            if r.get("accion") == _BLOQUEAR:
                bloqueada = True
            elif r.get("accion") == _PERMITIR:
                permitida = True
        if bloqueada:
            problemas.append(("bloqueado", perfil))
        elif not permitida:
            problemas.append(("sin_permiso", perfil))
    return problemas


def _ejecutables_python():
    rutas = {sys.executable, getattr(sys, "_base_executable", "") or ""}
    # Un venv de Windows lanza el Python base: el cortafuegos ve ese
    rutas |= {os.path.join(sys.base_prefix, "python.exe"), os.path.join(sys.base_prefix, "pythonw.exe")}
    return {os.path.normcase(os.path.abspath(r)).lower() for r in rutas if r}


def leer_cortafuegos():
    """Estado real del cortafuegos por su API COM (no hace falta ser administrador)."""
    import pythoncom
    pythoncom.CoInitialize()
    try:
        # En otra función: sus objetos COM se sueltan antes de CoUninitialize
        return _leer_cortafuegos_com()
    finally:
        pythoncom.CoUninitialize()


def _leer_cortafuegos_com():
    import win32com.client
    fw = win32com.client.Dispatch("HNetCfg.FwPolicy2")
    perfiles = int(fw.CurrentProfileTypes)
    activo = {bit: bool(fw.FirewallEnabled(bit)) for bit, __ in _PERFILES}
    bloquea = {bit: bool(fw.BlockAllInboundTraffic(bit)) for bit, __ in _PERFILES}
    permite = {bit: int(fw.DefaultInboundAction(bit)) == _PERMITIR for bit, __ in _PERFILES}
    reglas = []
    for r in fw.Rules:
        try:
            if int(r.Direction) != _ENTRADA:
                continue
            reglas.append({
                "nombre": r.Name, "entrada": True, "activa": bool(r.Enabled), "accion": int(r.Action),
                "protocolo": int(r.Protocol), "puertos": r.LocalPorts or "",
                "programa": os.path.normcase(r.ApplicationName or "").lower(), "perfiles": int(r.Profiles),
            })
        except Exception:
            continue
    return reglas, perfiles, activo, bloquea, permite


_cache_cortafuegos = {"t": 0.0, "avisos": None}
_lock_cortafuegos = threading.Lock()


def avisos_cortafuegos(puerto, max_edad_s=60):
    """Textos de aviso (ya traducidos) si el móvil no va a poder entrar; [] si todo bien o no se sabe."""
    if os.name != "nt":
        return []
    with _lock_cortafuegos:
        if _cache_cortafuegos["avisos"] is not None and time.time() - _cache_cortafuegos["t"] < max_edad_s:
            problemas = _cache_cortafuegos["avisos"]
        else:
            try:
                reglas, perfiles, activo, bloquea, permite = leer_cortafuegos()
                problemas = evaluar_cortafuegos(reglas, perfiles, activo, _ejecutables_python(), puerto,
                                                bloquea, permite)
            except Exception as e:
                log.info("No se pudo consultar el cortafuegos de Windows: %s", e)
                problemas = []
            _cache_cortafuegos.update(t=time.time(), avisos=problemas)
            if problemas:
                log.warning("Cortafuegos: el puerto %d puede estar cerrado para el móvil: %s", puerto, problemas)
    return [texto_problema(codigo, perfil, puerto) for codigo, perfil in problemas]


def texto_problema(codigo, perfil, puerto):
    arreglo = _("Para arreglarlo, haz doble clic en herramientas\\abrir_cortafuegos.bat (pide permiso de administrador).")
    if codigo == "bloqueado":
        return _("El cortafuegos de Windows BLOQUEA ScopePower en la red {perfil}: el móvil no podrá entrar.",
                 perfil=_(perfil)) + " " + arreglo
    if perfil == "Pública":
        return _("Windows tiene esta red como «Pública» y el cortafuegos no deja entrar al puerto {puerto}: "
                 "el móvil puede no cargar la página.", puerto=puerto) + " " + arreglo
    return _("El cortafuegos de Windows no tiene permiso para el puerto {puerto} en la red {perfil}: "
             "si Windows pregunta, pulsa «Permitir acceso».", puerto=puerto, perfil=_(perfil)) + " " + arreglo


# ─── Consola ─────────────────────────────────────────────────────────────────

def _consola():
    """Flujo de la consola de verdad (sin pasar por el duplicador del log) o None."""
    flujo = sys.__stdout__
    if flujo is None:
        return None
    try:
        if not flujo.isatty():
            return None
    except Exception:
        return None
    return flujo


def _activar_colores():
    """Activa las secuencias ANSI en la consola de Windows 10. True si se puede usar color."""
    if os.name != "nt":
        return True
    try:
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        modo = ctypes.c_uint32()
        if not k32.GetConsoleMode(h, ctypes.byref(modo)):
            return False
        return bool(k32.SetConsoleMode(h, modo.value | 0x0004))     # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def poner_titulo(texto):
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(str(texto))
    except Exception:
        pass


def _caja(lineas_dentro, titulo):
    cab = f" {titulo} "
    izq = (ANCHO - 2 - len(cab)) // 2
    arriba = "╔" + "═" * izq + cab + "═" * (ANCHO - 2 - izq - len(cab)) + "╗"
    abajo = "╚" + "═" * (ANCHO - 2) + "╝"
    return [arriba] + ["  " + x for x in lineas_dentro] + [abajo]


def _partir(texto, ancho):
    """Parte un texto largo en líneas de como mucho `ancho` caracteres."""
    lineas, actual = [], ""
    for palabra in texto.split():
        if actual and len(actual) + 1 + len(palabra) > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        lineas.append(actual)
    return lineas


def urls_para_qr(urls, maximo=MAX_QR):
    """
    [(url, etiqueta)] con las direcciones que llevan QR: una por red, en el orden de `urls`
    y sin la del nombre .local (Chrome en Android no la resuelve). Con el punto de acceso
    del PC y la Wi-Fi de casa salen las dos: con un solo QR, el del punto de acceso, el móvil
    que está en la Wi-Fi de casa se queda con la pantalla en blanco.
    """
    elegidas = []
    for url, desc in urls:
        if ".local:" in url or any(url == u for u, __ in elegidas):
            continue
        elegidas.append((url, desc))
    return elegidas[:maximo]


# Prefijos que pone ipconfig al nombre del adaptador (español e inglés): debajo del QR basta «Wi-Fi»
_PREFIJOS_ADAPTADOR = ("adaptador de lan inalámbrica ", "adaptador de ethernet ", "wireless lan adapter ",
                       "ethernet adapter ")


def nombre_corto_red(desc):
    for prefijo in _PREFIJOS_ADAPTADOR:
        if desc.lower().startswith(prefijo):
            return desc[len(prefijo):]
    return desc


def _qr_lado_a_lado(qrs, con_color):
    """Líneas con los QR [(url, etiqueta)] uno al lado del otro y su dirección y red debajo."""
    dibujos = [(qr_en_texto(url), url, desc) for url, desc in qrs]
    dibujos = [d for d in dibujos if d[0]]
    if not dibujos:
        return []
    ini, fin = ("\x1b[97;40m", "\x1b[0m") if con_color else ("", "")
    anchos = [len(d[0][0]) for d in dibujos]
    alto = max(len(d[0]) for d in dibujos)
    hueco = " " * 4
    lineas = []
    for i in range(alto):
        trozos = []
        for (dibujo, __, __), ancho in zip(dibujos, anchos):
            trozos.append(f"{ini}{dibujo[i]}{fin}" if i < len(dibujo) else " " * ancho)
        lineas.append("   " + hueco.join(trozos))
    # Debajo de cada código, la dirección (sin http://) y el nombre de la red
    for pie in (lambda url, desc: url.replace("http://", ""), lambda url, desc: nombre_corto_red(desc or "")):
        trozos = [pie(url, desc)[:ancho].center(ancho) for (__, url, desc), ancho in zip(dibujos, anchos)]
        if any(t.strip() for t in trozos):
            lineas.append(("   " + hueco.join(trozos)).rstrip())
    return lineas


def texto_recuadro(urls, url_local, version, avisos=(), con_color=False):
    """
    El recuadro completo (str) con direcciones, QR de cada red y avisos.
    urls: [(url, descripción)] de la red, la más probable primero.
    """
    dentro = []
    if urls:
        dentro.append(_("Desde el móvil o la tablet, en la misma Wi-Fi, abre:"))
        for url, desc in urls:
            dentro.append(f"   {url}" + (f"   ({desc})" if desc else ""))
        qrs = urls_para_qr(urls)
        dibujo = _qr_lado_a_lado(qrs, con_color)
        if dibujo:
            dentro.append("")
            if len(qrs) > 1:
                dentro.extend(_partir(_("O apunta la cámara del móvil al código de la Wi-Fi en la que esté:"),
                                      ANCHO - 4))
            else:
                dentro.append(_("O apunta la cámara del móvil a este código:"))
            dentro.append("")
            dentro.extend(dibujo)
        dentro.append("")
        dentro.extend(_partir(_("Si no carga: el móvil en la misma Wi-Fi que este PC y sin datos móviles. Si escribes la dirección a mano, pon también el http:// del principio."),
                              ANCHO - 4))
    else:
        dentro.extend(_partir(_("No encuentro ninguna red: conecta este PC a la Wi-Fi para entrar desde el móvil."),
                              ANCHO - 4))
    dentro.append("")
    dentro.append(_("En este mismo PC: {url}", url=url_local))
    for aviso in avisos:
        dentro.append("")
        partes = _partir("⚠ " + aviso, ANCHO - 4)
        if con_color:
            partes = [f"\x1b[93m{p}\x1b[0m" for p in partes]
        dentro.extend(partes)
    return "\n".join(_caja(dentro, _("ScopePower {version} listo", version=version))) + "\n"


def _escribir(flujo, texto):
    try:
        flujo.write("\n" + texto)
        flujo.flush()
    except Exception as e:
        log.debug("No se pudo escribir en la consola: %s", e)


def preguntar_abrir_navegador(flujo, url, segundos=SEGUNDOS_PREGUNTA):
    """Cuenta atrás en la consola: con «s» se abre el navegador de este PC. Cualquier otra
    tecla, o nada, lo deja cerrado. Devuelve True si se abrió."""
    try:
        import msvcrt
    except ImportError:
        return False
    # Una sola línea, sin reescribirla cada segundo: mientras tanto el log sigue escribiendo y
    # una cuenta atrás con \r acababa mezclada con sus mensajes
    pregunta = _("¿Abrir ScopePower en el navegador de este PC? Pulsa S en los próximos {s} s (si no, no se abre)",
                 s=segundos)
    try:
        while msvcrt.kbhit():          # lo que se tecleó durante el arranque no cuenta
            msvcrt.getwch()
        flujo.write(pregunta + "\n")
        flujo.flush()
    except Exception:
        return False
    respuesta = None
    fin = time.time() + segundos
    while time.time() < fin:
        if msvcrt.kbhit():
            respuesta = msvcrt.getwch()
            break
        time.sleep(0.05)
    si = respuesta is not None and respuesta.lower() in ("s", "y")
    try:
        flujo.write((_("Abriendo {url} en el navegador...", url=url) if si
                     else _("No se abre el navegador. Puedes entrar cuando quieras en {url}", url=url)) + "\n")
        flujo.flush()
    except Exception:
        pass
    if si:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            log.warning("No se pudo abrir el navegador: %s", e)
    return si


def leer_opciones(ruta_config="config.ini"):
    cfg = configparser.ConfigParser(strict=False)
    try:
        cfg.read(ruta_config, encoding="utf-8")
    except Exception:
        pass
    try:
        preguntar = cfg.getboolean("Red", "preguntar_navegador", fallback=True)
    except ValueError:
        preguntar = True
    return {"preguntar_navegador": preguntar}


# ─── Acceso directo en el escritorio ─────────────────────────────────────────

def carpeta_escritorio():
    if os.name != "nt":
        return None
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:    # CSIDL_DESKTOPDIRECTORY
            return buf.value
    except Exception:
        pass
    return None


def crear_acceso_navegador(puerto, carpeta_app, escritorio=None):
    """
    Crea una sola vez «ScopePower en el navegador.url» en el escritorio de los PC instalados
    con instalar_scopepower.bat (allí está ScopePower.lnk). Si el usuario lo borra no se
    vuelve a crear (marcador en datos/). Devuelve la ruta creada o None.
    """
    marcador = os.path.join(carpeta_app, MARCADOR_ACCESO)
    if os.path.exists(marcador):
        return None
    escritorio = escritorio or carpeta_escritorio()
    if not escritorio or not os.path.exists(os.path.join(escritorio, NOMBRE_ACCESO_LNK)):
        return None
    ruta = os.path.join(escritorio, _("ScopePower en el navegador") + ".url")
    icono = os.path.join(carpeta_app, "scopepower.ico")
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(f"[InternetShortcut]\nURL=http://127.0.0.1:{puerto}/\n")
            if os.path.exists(icono):
                f.write(f"IconFile={icono}\nIconIndex=0\n")
        os.makedirs(os.path.dirname(marcador), exist_ok=True)
        with open(marcador, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" {ruta}\n")
        log.info("Acceso directo creado en el escritorio: %s", ruta)
        return ruta
    except OSError as e:
        log.warning("No se pudo crear el acceso directo del navegador: %s", e)
        return None


# ─── Arranque ────────────────────────────────────────────────────────────────

def urls_de_red(direcciones, puerto, nombre_equipo, ip_punto_acceso=None):
    """[(url, descripción)] para el recuadro: la del punto de acceso primero si está activo."""
    urls = []
    if ip_punto_acceso:
        urls.append((f"http://{ip_punto_acceso}:{puerto}", _("punto de acceso del PC")))
    for ip, adaptador in direcciones:
        url = f"http://{ip}:{puerto}"
        if all(url != u for u, __ in urls):
            urls.append((url, adaptador))
    urls = urls[:3]
    if nombre_equipo:
        urls.append((f"http://{nombre_equipo}.local:{puerto}", _("nombre del equipo")))
    return urls


def iniciar(puerto, version, anunciador, direcciones, nombre_equipo, carpeta_app,
            ip_punto_acceso=None, preguntar=True):
    """Lanza en un hilo el recuadro, el título, el aviso del cortafuegos y la pregunta."""
    hilo = threading.Thread(target=_bucle, name="consola-conexion", daemon=True,
                            args=(puerto, version, anunciador, direcciones, nombre_equipo, carpeta_app,
                                  ip_punto_acceso, preguntar))
    hilo.start()
    return hilo


def _bucle(puerto, version, anunciador, direcciones, nombre_equipo, carpeta_app, ip_punto_acceso, preguntar):
    url_local = f"http://127.0.0.1:{puerto}"
    try:
        crear_acceso_navegador(puerto, carpeta_app)
    except Exception as e:
        log.warning("Acceso directo del navegador: %s", e)
    flujo = _consola()
    # Dar tiempo a que el servidor esté escuchando y a que pasen los mensajes del arranque
    time.sleep(3)
    try:
        urls = urls_de_red(direcciones(), puerto, nombre_equipo, ip_punto_acceso)
    except Exception as e:
        log.warning("No se pudieron leer las direcciones de red: %s", e)
        urls = []
    poner_titulo(f"ScopePower — {urls[0][0] if urls else url_local}")
    if urls:
        log.info("Direcciones para el móvil: %s", ", ".join(u for u, __ in urls))
    if flujo is None:
        return
    color = _activar_colores()
    avisos = avisos_cortafuegos(puerto)
    _escribir(flujo, texto_recuadro(urls, url_local, version, avisos, color))
    if preguntar:
        preguntar_abrir_navegador(flujo, url_local + "/")
    espera = REPETIR_PRIMERA_S
    while not anunciador.esperar_conexion(espera):
        try:
            urls = urls_de_red(direcciones(), puerto, nombre_equipo, ip_punto_acceso) or urls
        except Exception:
            pass
        _escribir(flujo, texto_recuadro(urls, url_local, version, avisos_cortafuegos(puerto), color))
        espera = REPETIR_CADA_S
    _escribir(flujo, _("✔ Conectado desde {origen}. Las direcciones siguen en Configuración > Conectar desde el móvil.",
                       origen=anunciador.origen or "?") + "\n")
