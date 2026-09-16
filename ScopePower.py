from flask import Flask, request, jsonify, render_template, render_template_string, send_file
from flask import session, redirect, url_for

from unidecode import unidecode
import win32com.client
import pythoncom
import subprocess
import time
import threading
import os
import re
import numpy as np
import unicodedata
import matplotlib
import wikipedia

matplotlib.use('Agg')  # Usa un backend sin GUI
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy.coordinates import EarthLocation, AltAz, SkyCoord
from astropy.time import Time
from astropy.coordinates import get_body
from astroquery.jplhorizons import Horizons
import astropy.units as u
import configparser
import sys
import bcrypt
from apilado import iniciar_hilo_apilado
from apilado import pausar_apilado, reanudar_apilado, esta_pausado
from apilado import aumentar_saturacion #, subir_imagen_ftp_async
import apilado
import firma
import rotacion_camara
import io
from AutoStrechSTF import apply_stf_color, debayer_rgb, miniatura_cfa_rgb, guardar_jpg_web
from ScopePower_ObjetosAnotados import resolver_y_generar_imagen_con_objetos

from datetime import datetime
import cv2
import winreg
import logging
from registro import configurar_logging
log = configurar_logging()   # fichero rotativo logs/scopepower.log + consola; print() también va al log
from hardware import (TrabajadorCOM, ErrorHardware, CapturaCancelada,
                      capturar_frame, estado_captura, ganancia_configurada, leer_ganancia,
                      asentamiento_montura)
import darks
import flats
import secretos
import centrado
import foto_dia
import foto_continua
import alineacion
import voz
import esta_noche
import cuerpos_menores
import catalogo_offline
import editor_fotos
import anuncio_ip
import coordenadas
import recorridos

def ruta_absoluta(relativa):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relativa)
    return os.path.join(os.path.abspath("."), relativa)


app = Flask(__name__,
            template_folder=ruta_absoluta("templates"),
            static_folder=ruta_absoluta("static"))
# Clave de sesión: aleatoria, generada una vez y guardada en secretos.ini
app.secret_key = secretos.obtener("flask_secret_key")

#from ajuste_mtf_interactivo import app_mtf
#app.register_blueprint(app_mtf, url_prefix='/ajuste-mtf')
#print(app.url_map)


# Cargar configuración
config = configparser.ConfigParser()
config.read("config.ini")

# Acceder a valores
USUARIOS_HASH = dict(config["Usuarios"])

RUTA_ASTAP = config["General"]["RUTA_ASTAP"]
RUTA_IMAGEN = ruta_absoluta("static/captura.fits") # config["General"]["RUTA_IMAGEN"]
# Versión para la web de la foto de prueba: JPG reducido (ver guardar_jpg_web), no el PNG entero
RUTA_IMAGEN_WEB = os.path.splitext(RUTA_IMAGEN)[0] + ".jpg"
RUTA_WCS = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.wcs"
RUTA_INI = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.ini"
ruta_stellarium = config["General"].get("RUTA_STELLARIUM", "")

ALTURA_MINIMA = config["General"]["ALTURA_MINIMA"]

DRIVER_CAMARA = config["Dispositivos"]["DRIVER_CAMARA"]
DRIVER_TELESCOPIO = config["Dispositivos"]["DRIVER_TELESCOPIO"]
BINNING = int(config["Dispositivos"]["BINNING_CAMARA"])
EXPOSICION = float(config["Dispositivos"]["EXPOSICION_CAPTURA"])
FOCAL_LEN = float(config["Dispositivos"]["FOCAL_LEN"])


def _dispositivos():
    """
    Drivers, binning y focal leídos de config.ini EN EL MOMENTO (no al arrancar),
    para que un cambio en Configuración se aplique sin reiniciar la app.
    """
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    d = cfg["Dispositivos"]
    return {
        "driver_camara": d.get("driver_camara", DRIVER_CAMARA),
        "driver_telescopio": d.get("driver_telescopio", DRIVER_TELESCOPIO),
        "binning": int(d.get("binning_camara", BINNING)),
        "focal": float(d.get("focal_len", FOCAL_LEN)),
        "ganancia": ganancia_configurada(),   # None = la que tenga el driver
    }
#PIXEL_SIZE = float(config["Dispositivos"]["PIXEL_SIZE"])
PALABRA_CLAVE_GOTO = config["General"].get("PALABRA_CLAVE_GOTO", "").lower().strip()


LATITUD_OBSERVATORIO = float(config["Observatorio"]["LATITUD_OBSERVATORIO"])
LONGITUD_OBSERVATORIO = float(config["Observatorio"]["LONGITUD_OBSERVATORIO"])
ALTITUD_OBSERVATORIO = int(config["Observatorio"]["ALTITUD_OBSERVATORIO"])

# Verificar valores cargados
print(f"Ruta ASTAP: {RUTA_ASTAP}")
#print(f"Ruta Imagen: {RUTA_IMAGEN}")
print(f"Ruta Stellarium: {ruta_stellarium}")
print(f"Binning Cámara: {BINNING}")
print(f"Esposición Cámara: {EXPOSICION}")
print(f"Cámara: {DRIVER_CAMARA}")
print(f"Telescopio: {DRIVER_TELESCOPIO}")
print(f"Longitud focal: {FOCAL_LEN}")
print(f"Latitud Observatorio: {LATITUD_OBSERVATORIO}")
print(f"Altitud minima: {ALTURA_MINIMA}")
print(f"Palabra clave goto: {PALABRA_CLAVE_GOTO}")
print(f"-------------------------")

# Lista de cuerpos del sistema solar válidos
CUERPOS_SISTEMA_SOLAR = {
    "sol", "luna", "mercurio", "venus", "marte",
    "jupiter", "saturno", "urano", "neptuno", "pluton"
}

from flask import send_from_directory

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'scopepower.ico', mimetype='image/vnd.microsoft.icon')


#A PARTIR DE AQUI SISTEMA DE ACTIVACION
def obtener_uuid_placa_base():
    try:
        resultado = subprocess.check_output("wmic csproduct get uuid", shell=True, encoding="cp1252")
        lineas = resultado.strip().split("\n")
        uuid = [linea.strip() for linea in lineas if linea.strip() and "UUID" not in linea]
        return uuid[0] if uuid else "UUID_DESCONOCIDO"
    except Exception as e:
        print("⚠️ Error obteniendo UUID:", e)
        return "UUID_ERROR"

def obtener_id_procesador():
    try:
        resultado = subprocess.check_output("wmic cpu get ProcessorId", shell=True, encoding="cp1252")
        lineas = resultado.strip().split("\n")
        procesador_id = [l.strip() for l in lineas if l.strip() and "ProcessorId" not in l]
        return procesador_id[0] if procesador_id else "CPU_ID_DESCONOCIDO"
    except Exception as e:
        print("⚠️ Error obteniendo ID del procesador:", e)
        return "CPU_ID_ERROR"

def obtener_identificador_unico():
    uuid_placa = obtener_uuid_placa_base()
    id_cpu = obtener_id_procesador()
    return f"{uuid_placa}-{id_cpu}"

@app.route("/identificador-cliente")
def identificador_cliente():
    identificador = obtener_identificador_unico()
    return jsonify({"identificador": identificador})

import hashlib

# Debe ser exactamente el mismo que uses tú al generar claves; vive en secretos.ini
SECRETO_ACTIVACION = secretos.obtener("activacion_secreto")

def generar_clave_activacion(identificador, secreto):
    return hashlib.sha256((identificador + secreto).encode()).hexdigest()


@app.route("/activar", methods=["GET", "POST"])
def activar():
    identificador = obtener_identificador_unico()
    codigo_para_usuario = base64.b64encode(identificador.encode()).decode()

    # 🧪 Días de prueba restantes
    try:
        fecha_inicio = obtener_fecha_primer_uso()
        dias_usados = (datetime.now() - fecha_inicio).days
        dias_restantes = max(0, 30 - dias_usados)
    except:
        dias_restantes = 0

    estado = estado_licencia()  # ← usa tu función

    # Determinar si la licencia está expirada
    expirada = estado == "expirado"


    if request.method == "POST":
        clave_introducida = request.form.get("clave", "").strip()
        clave_esperada = generar_clave_activacion(identificador, SECRETO_ACTIVACION)

        if clave_introducida == clave_esperada:
            valor_firmado = firmar_clave(clave_introducida)
            clave = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\WinUpdateTelemetry")
            winreg.SetValueEx(clave, "ProductKey", 0, winreg.REG_SZ, valor_firmado)
            winreg.CloseKey(clave)
            return render_template("activar.html", correcto=True, uuid=codigo_para_usuario, dias_restantes=dias_restantes)

        return render_template("activar.html", error="❌ Clave incorrecta.", uuid=codigo_para_usuario, dias_restantes=dias_restantes)

    return render_template(
    "activar.html",
    uuid=codigo_para_usuario,
    dias_restantes=dias_restantes,
    activado=estado == "activado",
    expirada=expirada
)

import base64

def obtener_codigo_para_usuario():
    identificador = obtener_identificador_unico()
    codificado = base64.b64encode(identificador.encode()).decode()
    return codificado

def firmar_clave(clave_str):
    firma = hashlib.sha256((clave_str + SECRETO_ACTIVACION).encode()).hexdigest()
    return f"{clave_str}|{firma}"

def activacion_requerida():
    """[General] activacion_requerida = false desactiva por completo la activación (pruebas)."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    return cfg.get("General", "activacion_requerida", fallback="true").strip().lower() == "true"


def estado_licencia():
    if not activacion_requerida():
        return "activado"
    try:
        # Verificar clave de activación
        clave = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\WinUpdateTelemetry")
        valor, _ = winreg.QueryValueEx(clave, "ProductKey")
        winreg.CloseKey(clave)

        clave_guardada, firma = valor.split("|")
        firma_esperada = hashlib.sha256((clave_guardada + SECRETO_ACTIVACION).encode()).hexdigest()

        identificador = obtener_identificador_unico()
        clave_correcta = generar_clave_activacion(identificador, SECRETO_ACTIVACION)

        if clave_guardada == clave_correcta and firma == firma_esperada:
            return "activado"
    except:
        pass

    try:
        fecha_inicio = obtener_fecha_primer_uso()
        dias_usados = (datetime.now() - fecha_inicio).days
        if dias_usados <= 30:
            return "prueba"
        else:
            return "expirado"
    except:
        return "expirado"

def validar_licencia():
    if not activacion_requerida():
        return True
    if not comprobar_reloj_no_atrasado():
        print("⚠️ Se detectó cambio en el reloj del sistema. Bloqueando acceso.")
        return False

    try:
        # Activación válida
        clave = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\WinUpdateTelemetry")
        valor, _ = winreg.QueryValueEx(clave, "ProductKey")
        winreg.CloseKey(clave)

        clave_guardada, firma = valor.split("|")
        firma_esperada = hashlib.sha256((clave_guardada + SECRETO_ACTIVACION).encode()).hexdigest()

        identificador = obtener_identificador_unico()
        clave_correcta = generar_clave_activacion(identificador, SECRETO_ACTIVACION)

        if clave_guardada == clave_correcta and firma == firma_esperada:
            guardar_fecha_ultimo_uso()
            return True

    except:
        pass

    # Modo prueba
    try:
        fecha_inicio = obtener_fecha_primer_uso()
        dias_usados = (datetime.now() - fecha_inicio).days
        if dias_usados <= 30:
            guardar_fecha_ultimo_uso()
            return True
    except:
        return False

    return False



def firmar_fecha(fecha_str):
    firma = hashlib.sha256((fecha_str + SECRETO_ACTIVACION).encode()).hexdigest()
    return f"{fecha_str}|{firma}"

def guardar_fecha_ultimo_uso():
    hoy = datetime.now().strftime("%Y-%m-%d")
    valor_firmado = firmar_fecha(hoy)
    clave = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\WinUpdateTelemetry")
    winreg.SetValueEx(clave, "LastUse", 0, winreg.REG_SZ, valor_firmado)
    winreg.CloseKey(clave)

def comprobar_reloj_no_atrasado():
    try:
        clave = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\WinUpdateTelemetry")
        valor, _ = winreg.QueryValueEx(clave, "LastUse")
        winreg.CloseKey(clave)

        valido, fecha_str = verificar_firma(valor)
        if not valido:
            raise Exception("firma inválida")

        fecha_ultima = datetime.strptime(fecha_str, "%Y-%m-%d")
        hoy = datetime.now()

        return hoy >= fecha_ultima  # ✅ Si la fecha actual no es anterior a la guardada
    except:
        return True  # Si no se puede leer → no bloquear, por si es el primer uso

def verificar_firma(valor_guardado):
    try:
        fecha_str, firma = valor_guardado.split("|")
        firma_esperada = hashlib.sha256((fecha_str + SECRETO_ACTIVACION).encode()).hexdigest()
        return firma == firma_esperada, fecha_str
    except:
        return False, None

def obtener_fecha_primer_uso():
    try:
        clave = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\WinUpdateTelemetry")
        valor, _ = winreg.QueryValueEx(clave, "TelemetryID")
        winreg.CloseKey(clave)

        valido, fecha_str = verificar_firma(valor)
        if not valido:
            raise Exception("firma inválida")

        return datetime.strptime(fecha_str, "%Y-%m-%d")

    except:
        hoy = datetime.now().strftime("%Y-%m-%d")
        valor_firmado = firmar_fecha(hoy)
        clave = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\WinUpdateTelemetry")
        winreg.SetValueEx(clave, "TelemetryID", 0, winreg.REG_SZ, valor_firmado)
        winreg.CloseKey(clave)
        return datetime.strptime(hoy, "%Y-%m-%d")

#HASTA AQUI SISTEMA DE ACTIVACION

@app.route('/config-objetos', methods=['GET'])
def get_config_objetos():
    # Devuelve el estado actual del checkbox
    return jsonify({"objetos_anotados": apilado.OBJETOS_ANOTADOS})

@app.route('/toggle-objetos-anotados', methods=['POST'])
def toggle_objetos_anotados():
    data = request.get_json()
    # Actualiza el flag y lo deja guardado para la próxima sesión ([Apilado] objetos_anotados)
    apilado.OBJETOS_ANOTADOS = bool(data.get("objetos", False))
    _guardar_en_config("Apilado", {"objetos_anotados": "true" if apilado.OBJETOS_ANOTADOS else "false"})
    return jsonify({"ok": True})


# ───────────────────────── Preferencias de la interfaz ─────────────────────────
# Cosas que el usuario quiere escribir una sola vez (orden de los paneles de Apilado,
# opción de darks al empezar, número de darks...). Se guardan en config.ini para que
# valgan desde cualquier móvil o tablet. Solo se aceptan las claves de esta lista.
PREFERENCIAS = {
    # clave web: (sección, clave en config.ini, tipo)
    "apilado_paneles": ("Interfaz", "apilado_paneles", "json"),
    "foto_paneles": ("Interfaz", "foto_paneles", "json"),         # orden y plegado de los paneles de la Foto de prueba
    "opcion_dark": ("Interfaz", "opcion_dark", "texto"),
    "numero_darks": ("Dispositivos", "numero_darks", "entero"),
    "numero_maximo_imagenes": ("Dispositivos", "numero_maximo_imagenes", "entero"),   # se puede cambiar con el apilado en marcha
    "modo_dia": ("Interfaz", "modo_dia", "texto"),                 # "on"/"off": la Foto de prueba arranca en modo día
    "foto_dia_objetivo": ("Interfaz", "foto_dia_objetivo", "texto"),   # sol | luna | paisaje
    "foto_dia_campo": ("Interfaz", "foto_dia_campo", "entero"),        # % del campo visible en el modo día (100, 75, 50, 35, 25)
    "foto_dia_compensacion": ("Interfaz", "foto_dia_compensacion", "decimal"),   # EV (−3..+3) sobre la exposición automática de día
    "foto_continua_guardar": ("Interfaz", "foto_continua_guardar", "texto"),   # no | jpg | fits: qué guardar en el modo continuo
}


_CONFIG_LOCK = threading.Lock()   # dos peticiones a la vez no deben pisarse al reescribir config.ini


def _guardar_en_config(seccion, valores):
    """Escribe (o sobreescribe) varias claves de una sección de config.ini."""
    with _CONFIG_LOCK:
        cfg = configparser.ConfigParser()
        cfg.read("config.ini")
        if seccion not in cfg:
            cfg[seccion] = {}
        for clave, valor in valores.items():
            cfg[seccion][clave] = str(valor)
        with open("config.ini", "w") as f:
            cfg.write(f)


def _leer_preferencias():
    import json
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    salida = {}
    for clave, (seccion, clave_ini, tipo) in PREFERENCIAS.items():
        if seccion not in cfg or clave_ini not in cfg[seccion]:
            continue
        bruto = cfg[seccion][clave_ini]
        try:
            if tipo == "json":
                salida[clave] = json.loads(bruto)
            elif tipo == "entero":
                salida[clave] = int(bruto)
            elif tipo == "decimal":
                salida[clave] = float(bruto)
            else:
                salida[clave] = bruto
        except (ValueError, TypeError):
            pass
    return salida


@app.route('/preferencias', methods=['GET', 'POST'])
def preferencias():
    import json
    if request.method == 'POST':
        datos = request.get_json(silent=True) or {}
        for clave, valor in datos.items():
            if clave not in PREFERENCIAS:
                return jsonify({"error": f"Preferencia desconocida: {clave}"}), 400
            seccion, clave_ini, tipo = PREFERENCIAS[clave]
            if tipo == "json":
                valor = json.dumps(valor, ensure_ascii=False)
            elif tipo == "entero":
                valor = int(valor)
            elif tipo == "decimal":
                valor = round(float(valor), 3)
            else:
                valor = str(valor)[:200]
            _guardar_en_config(seccion, {clave_ini: valor})
    return jsonify(_leer_preferencias())

@app.route('/pausar-apilado', methods=["POST"])
def pausar():
    pausar_apilado()
    bucle.pausar()
    return "⏸️ Apilado pausado"

@app.route('/reanudar-apilado', methods=["POST"])
def reanudar():
    reanudar_apilado()
    bucle.reanudar()
    return "▶️ Apilado reanudado"

@app.route('/estado-pausa-apilado')
def estado_pausa_apilado():
    return jsonify({"pausado": esta_pausado() or bucle.pausado})


@app.route('/apilado/iniciar', methods=["POST"])
def apilado_iniciar():
    """
    Arranca el bucle de captura en el servidor.
    JSON: {"nuevo": true|false (reiniciar el apilado), "usar_dark": true|false}
    """
    datos = request.get_json(silent=True) or {}
    nuevo = bool(datos.get("nuevo", True))
    usar_dark = bool(datos.get("usar_dark", True))
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    if nuevo:
        bucle.detener("Reiniciado")
        reiniciar_apilado_global()
    reanudar_apilado()
    bucle.iniciar(usar_dark=usar_dark)
    return jsonify({"ok": True, "bucle": bucle.estado()})


@app.route('/apilado/dithering', methods=["POST"])
def apilado_dithering():
    """Activa/desactiva el dithering en caliente y lo guarda en config.ini."""
    datos = request.get_json(silent=True) or {}
    activado = datos.get("activado")
    cada = datos.get("cada")
    bucle.configurar_dithering(activado, cada)
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if "Dithering" not in cfg:
        cfg["Dithering"] = {}
    if activado is not None:
        cfg["Dithering"]["activado"] = "true" if bucle.dithering else "false"
    if cada is not None:
        cfg["Dithering"]["cada_n_fotos"] = str(bucle.dither_cada)
    with open("config.ini", "w") as f:
        cfg.write(f)
    voz.decir("Dithering activado" if bucle.dithering else "Dithering desactivado")
    return jsonify({"ok": True, "bucle": bucle.estado()})


@app.route('/apilado/detener', methods=["POST"])
def apilado_detener():
    bucle.detener()
    pausar_apilado()
    return jsonify({"ok": True, "bucle": bucle.estado()})

# ─────────────────────────────────────────────────────────────────────────────
#  Dispositivos ASCOM
#
#  Cada dispositivo tiene su propio hilo (ver hardware.py). Las rutas Flask
#  NUNCA tocan el objeto COM directamente: envían una función al trabajador
#  con montura.ejecutar(lambda tel: ...) o camara.ejecutar(lambda cam: ...).
#  Así Flask puede atender varias peticiones a la vez (threaded=True) sin
#  los errores de COM entre hilos que obligaban a usar threaded=False.
# ─────────────────────────────────────────────────────────────────────────────

def limpiar_sincronizaciones(tel):
    """
    Borra en la app SynScan Pro las sincronizaciones y el modelo de apuntado de
    sesiones anteriores. Usa la acción ASCOM "ClearSyncTo" (driver SynScan App
    1.4+), la misma que emplea el script de alineación automática de Sky-Watcher.
    Tras esto la app vuelve al modelo por defecto; el primer centrado por plate
    solving de la noche crea el modelo nuevo. Devuelve (ok, mensaje).
    Debe ejecutarse en el hilo de la montura (vía montura.ejecutar).
    """
    try:
        tel.Action("ClearSyncTo", "")
        log.info("Sincronizaciones anteriores borradas en la app SynScan (ClearSyncTo)")
        return True, "Sincronizaciones anteriores borradas"
    except Exception as e:
        from hardware import _mensaje_com
        msg = f"Este driver no admite ClearSyncTo o la app no respondió: {_mensaje_com(e)}"
        log.warning(msg)
        return False, msg


def _activar_seguimiento(tel):
    # El driver SynScanMobile "conecta" con la app SynScan Pro aunque esta no tenga
    # enlace con la montura (apagada, sin WiFi): la conexión ASCOM tiene éxito y luego
    # cada orden falla con "Conversation timeout". Se comprueba antes que la montura
    # contesta; si no, la conexión se da por fallida con un aviso claro.
    try:
        tel.RightAscension
    except Exception as e:
        from hardware import _mensaje_com
        raise ErrorHardware(f"la montura no responde ({_mensaje_com(e)}): comprueba que está "
                            "encendida y que SynScan Pro tiene enlace con ella") from e
    try:
        tel.Tracking = True
    except Exception as e:
        print(f"⚠️ No se pudo activar el seguimiento al conectar: {e}")
    # ¿La montura trabaja en J2000 o en JNow? Condiciona todos los GOTO y Sync.
    try:
        sistema = coordenadas.establecer_desde_ascom(tel.EquatorialSystem)
        print(f"🧭 Sistema de coordenadas de la montura: {sistema}")
    except Exception as e:
        print(f"⚠️ No se pudo leer EquatorialSystem de la montura ({e}); se asume J2000")
    voz.decir("Telescopio conectado")

    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if cfg.get("Montura", "limpiar_sync_al_conectar", fallback="true").lower() == "true":
        ok, msg = limpiar_sincronizaciones(tel)
        voz.decir("Sincronizaciones anteriores borradas" if ok
                  else "No se han podido borrar las sincronizaciones anteriores")

# El driver se lee de config.ini al conectar: cambiarlo en Configuración y pulsar
# "Reconectar equipo" basta, sin reiniciar la app.
montura = TrabajadorCOM("montura", lambda: _dispositivos()["driver_telescopio"], al_conectar=_activar_seguimiento)
camara = TrabajadorCOM("cámara", lambda: _dispositivos()["driver_camara"], al_conectar=lambda cam: voz.decir("Cámara conectada"))

# Objetivo del último GOTO (ra en horas, dec en grados): es lo que usa el
# centrado automático como referencia, no "donde la montura cree que está".
ULTIMO_GOTO = {}

# Última RA/Dec leída de la montura (en su sistema, JNow o J2000) al capturar: si en
# una toma la montura no contesta, la cabecera del FITS lleva esta en vez de perder la foto.
_ULTIMA_POSICION_MONTURA = {}

# Miniatura de la última toma individual (static/ultima_captura.jpg) y su
# versión, para que la web la refresque solo cuando haya una nueva.
RUTA_ULTIMA_CAPTURA = ruta_absoluta("static/ultima_captura.jpg")
ULTIMA_CAPTURA = {"version": 0, "hora": "", "exposicion": 0.0, "dark": False}

def _config_dithering():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    d = cfg["Dithering"] if "Dithering" in cfg else {}
    return {
        "activado": str(d.get("activado", "false")).lower() == "true",
        "cada_n_fotos": int(d.get("cada_n_fotos", 3)),
        "amplitud_px": float(d.get("amplitud_px", 15)),
        "asentamiento_s": asentamiento_montura("Dithering"),
    }


def hacer_dither():
    """
    Desplaza la montura una distancia aleatoria de hasta `amplitud_px` píxeles
    en una dirección aleatoria (movimiento relativo, sin Sync) y espera a que
    termine y se asiente. Se ejecuta desde el bucle de captura entre fotos.
    Devuelve una descripción tipo "12 px hacia el NE".
    """
    import random, math
    conf = _config_dithering()
    # Escala de placa ("/px) a partir del tamaño de píxel de la última captura y la focal
    pixel_um = ULTIMA_CAPTURA.get("pixel_um") or 3.76
    disp = _dispositivos()
    escala = 206.265 * pixel_um * disp["binning"] / max(1.0, disp["focal"])
    px = random.uniform(0.5, 1.0) * conf["amplitud_px"]
    angulo = random.uniform(0, 2 * math.pi)
    arcsec = px * escala
    d_ra_deg, d_dec_deg = arcsec * math.cos(angulo) / 3600.0, arcsec * math.sin(angulo) / 3600.0

    def _mover(tel):
        dec = tel.Declination
        ra_dest = (tel.RightAscension + d_ra_deg / 15.0 / max(0.05, math.cos(math.radians(dec)))) % 24.0
        dec_dest = max(-90.0, min(90.0, dec + d_dec_deg))
        tel.Tracking = True
        if getattr(tel, "CanSlewAsync", False):
            tel.SlewToCoordinatesAsync(ra_dest, dec_dest)
        else:
            tel.SlewToCoordinates(ra_dest, dec_dest)
        inicio = time.time()
        time.sleep(0.3)
        while tel.Slewing:
            if time.time() - inicio > 60:
                raise ErrorHardware("La montura no terminó el movimiento de dithering en 60 s")
            time.sleep(0.3)

    montura.ejecutar(_mover, timeout=90)
    time.sleep(conf["asentamiento_s"])
    rumbos = ["E", "NE", "N", "NO", "O", "SO", "S", "SE"]
    rumbo = rumbos[int(((math.degrees(angulo) + 22.5) % 360) // 45)]
    return f"{px:.0f} px ({arcsec:.0f}\") hacia el {rumbo}"


# Bucle de captura del apilado en el servidor (ver bucle_captura.py). Se
# define aquí y se conecta a capturar_y_guardar más abajo, cuando exista.
from bucle_captura import BucleCaptura
bucle = BucleCaptura(
    capturar=lambda usar_dark: capturar_y_guardar(apilar=True, usar_dark=usar_dark,
                                                  generar_preview=False, descripcion="Apilado"),
    camara=camara,
    otros_trabajos=(darks.trabajo_darks, centrado.trabajo_centrado, foto_continua.trabajo_continuo),
    dither=hacer_dither,
)
_cfg_dither = _config_dithering()
bucle.configurar_dithering(_cfg_dither["activado"], _cfg_dither["cada_n_fotos"])


def conectar_telescopio():
    """Conecta la montura si no lo está. Devuelve True si está disponible."""
    try:
        montura.ejecutar(lambda tel: None, timeout=30)
        return True
    except ErrorHardware as e:
        print(f"Error al conectar telescopio: {e}")
        voz.decir("No se ha podido conectar con el telescopio")
        return False



#@app.before_request
#def verificar_autenticacion():
#    rutas_publicas = {'/login', '/static/', '/logout'}  # añadir más si hace falta
#    if request.path.startswith('/static/'):
#        return  # permitir archivos estáticos como CSS/JS

#    if 'usuario' not in session and request.path not in rutas_publicas:
#        return redirect(url_for('login'))
@app.before_request
def verificar_licencia_y_autenticacion():
    rutas_publicas = {
        '/login', '/logout', '/activar', '/static/', '/identificador-cliente'
    }

    # Primera petición desde otro dispositivo: ya no hace falta anunciar la IP
    if request.remote_addr not in ("127.0.0.1", "::1", None):
        anunciador.marcar_conectado(request.remote_addr)

    if any(request.path.startswith(r) for r in rutas_publicas):
        return  # Permitir rutas públicas

    # Verificar sesión iniciada
    if 'usuario' not in session:
        return redirect(url_for('login'))

    # Si la licencia está expirada, solo permitimos activar
    if estado_licencia() == "expirado":
        return redirect(url_for('activar'))



# Conexión automática al entrar: montura Y cámara, en segundo plano para no
# bloquear el login (la montura puede tardar 30 s en fallar). El botón
# Conectar/Reconectar del menú queda para reintentar o aplicar otro driver.
_conexion_auto = {"en_curso": False, "lock": threading.Lock()}


def _conectar_equipo_completo():
    """Stellarium + montura + cámara. Devuelve el mismo dict que /conectar-equipo."""
    resultado = {"montura": False, "camara": False, "errores": []}
    if usar_stellarium():
        try:
            lanzar_stellarium_si_no_esta_activo()
        except Exception as e:
            resultado["errores"].append(f"Stellarium: {e}")
    try:
        if montura.esta_conectado():
            montura.reconectar(timeout=30)
        else:
            montura.ejecutar(lambda tel: None, timeout=30)
        resultado["montura"] = True
    except ErrorHardware as e:
        resultado["errores"].append(f"Montura: {e}")
        voz.decir("No se ha podido conectar con el telescopio")
    try:
        if camara.esta_conectado():
            camara.reconectar(timeout=30)
        else:
            camara.ejecutar(lambda cam: None, timeout=30)
        resultado["camara"] = True
    except ErrorHardware as e:
        resultado["errores"].append(f"Cámara: {e}")
        voz.decir("No se ha podido conectar con la cámara")
    resultado["ok"] = resultado["montura"] and resultado["camara"]
    resultado["sistema"] = coordenadas.sistema() if resultado["montura"] else None
    return resultado


def iniciar_conexion():
    """Tras el login: conectar todo el equipo sin hacer esperar al usuario."""
    with _conexion_auto["lock"]:
        if _conexion_auto["en_curso"]:
            return
        _conexion_auto["en_curso"] = True

    def _tarea():
        try:
            r = _conectar_equipo_completo()
            log.info("Conexión automática tras el login: montura=%s cámara=%s %s", r["montura"], r["camara"], r["errores"] or "")
        except Exception:
            log.exception("Error en la conexión automática")
        finally:
            with _conexion_auto["lock"]:
                _conexion_auto["en_curso"] = False

    threading.Thread(target=_tarea, name="conexion-auto", daemon=True).start()

@app.route("/")
def home():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    estado = estado_licencia()
    dias_restantes = 0

    if estado == "prueba":
        try:
            fecha_inicio = obtener_fecha_primer_uso()
            dias_usados = (datetime.now() - fecha_inicio).days
            dias_restantes = max(0, 30 - dias_usados)
        except:
            dias_restantes = 0

  
    return render_template("home.html", estado_licencia=estado, dias_restantes=dias_restantes)


import threading
import ctypes

@app.route('/salir')
def salir():
    # 1) Desconectar telescopio
    desconectar_telescopio()
    # 2) Reiniciar info GOTO
    eliminar_info_goto()
    # 3) Desconectar cámara si es posible
    desconectar_camara()
    # 4) Cerrar sesión
    session.pop('usuario', None)

    # 5) Intentar shutdown de Werkzeug (modo desarrollo)
    shutdown = request.environ.get('werkzeug.server.shutdown')
    if shutdown:
        shutdown()

    # 6) En 0.5 s liberamos la consola y matamos el proceso
    def kill_process():
        # Suelta la consola de Windows
        ctypes.windll.kernel32.FreeConsole()
        # Cierra el intérprete de Python inmediatamente
        os._exit(0)

    threading.Timer(0.5, kill_process).start()

    # 7) Renderiza la página de confirmación (después la consola desaparecerá)
    return render_template(
        'salir.html',
        mensaje="✅ Servidor detenido y proceso finalizado",
        mostrar_volver=False
    )

# ─── Apagar o reiniciar el PC de campo desde el móvil ─────────────────────
# Windows programa el apagado con `shutdown` y deja unos segundos para
# cancelarlo (`shutdown /a`) por si se ha pulsado sin querer.
APAGADO_ESPERA_S = 15
_apagado = {"accion": None, "hasta": 0.0}


def _ejecutar_shutdown(argumentos):
    """Separado para poder sustituirlo en las pruebas."""
    return subprocess.run(["shutdown"] + argumentos, capture_output=True, text=True, timeout=15)


@app.route('/apagar')
def vista_apagar():
    return render_template('apagar.html', mostrar_volver=True, espera=APAGADO_ESPERA_S)


@app.route('/apagar/equipo', methods=['POST'])
def apagar_equipo():
    d = request.get_json(silent=True) or {}
    accion = d.get("accion")
    if accion not in ("apagar", "reiniciar"):
        return jsonify({"ok": False, "mensaje": "Acción no válida"}), 400
    # Parar lo que haya en marcha y soltar el equipo antes de que Windows lo mate
    try:
        if bucle.activo:
            bucle.detener("Apagado del equipo")
    except Exception:
        pass
    desconectar_telescopio()
    desconectar_camara()
    voz.decir("Apagando el equipo" if accion == "apagar" else "Reiniciando el equipo")
    args = ["/s" if accion == "apagar" else "/r", "/t", str(APAGADO_ESPERA_S), "/f",
            "/c", "ScopePower: " + ("apagado" if accion == "apagar" else "reinicio") + " pedido desde el movil"]
    try:
        r = _ejecutar_shutdown(args)
    except Exception as e:
        log.error("No se pudo programar el %s: %s", accion, e)
        return jsonify({"ok": False, "mensaje": f"No se pudo programar el {accion}: {e}"}), 500
    if r.returncode != 0:
        detalle = (r.stderr or r.stdout or "").strip()
        log.error("shutdown devolvió %s: %s", r.returncode, detalle)
        return jsonify({"ok": False, "mensaje": f"Windows no aceptó la orden: {detalle or r.returncode}"}), 500
    _apagado.update(accion=accion, hasta=time.time() + APAGADO_ESPERA_S)
    log.warning("%s del equipo programado en %s s", "Apagado" if accion == "apagar" else "Reinicio", APAGADO_ESPERA_S)
    return jsonify({"ok": True, "accion": accion, "espera": APAGADO_ESPERA_S})


@app.route('/apagar/cancelar', methods=['POST'])
def apagar_cancelar():
    try:
        r = _ejecutar_shutdown(["/a"])
    except Exception as e:
        return jsonify({"ok": False, "mensaje": str(e)}), 500
    _apagado.update(accion=None, hasta=0.0)
    if r.returncode != 0:
        # 1116: no había ningún apagado en marcha (ya se canceló o ya pasó el plazo)
        return jsonify({"ok": False, "mensaje": "No había ningún apagado pendiente"}), 409
    voz.decir("Apagado cancelado")
    log.warning("Apagado del equipo cancelado desde el móvil")
    return jsonify({"ok": True})


# ─── Reiniciar solo ScopePower (el PC sigue encendido) ─────────────────────
# Si la app la arrancó lanzar_scopepower.bat (variable SCOPEPOWER_LANZADOR), basta
# con salir con código 3: el bat vuelve a arrancarla en la misma ventana. Si se
# lanzó a mano con python, se abre una consola nueva con la app y se sale.
CODIGO_SALIDA_REINICIO = 3


def _relanzar_a_mano():
    raiz = os.path.dirname(os.path.abspath(__file__))
    bat = os.path.join(raiz, "lanzar_scopepower.bat")
    if os.path.exists(bat):
        orden = f'ping -n 3 127.0.0.1 >nul & start "ScopePower" /D "{raiz}" "{bat}"'
    else:
        orden = f'ping -n 3 127.0.0.1 >nul & start "ScopePower" /D "{raiz}" "{sys.executable}" "{os.path.join(raiz, "ScopePower.py")}"'
    DETACHED = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", orden], creationflags=DETACHED, close_fds=True)


def _terminar_para_reiniciar():
    try:
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass
    if os.environ.get("SCOPEPOWER_LANZADOR") == "1":
        os._exit(CODIGO_SALIDA_REINICIO)     # lanzar_scopepower.bat lo vuelve a arrancar
    try:
        _relanzar_a_mano()
    except Exception as e:
        log.error("No se pudo relanzar ScopePower: %s", e)
    os._exit(0)


@app.route('/reiniciar-app', methods=['POST'])
def reiniciar_app():
    try:
        if bucle.activo:
            bucle.detener("Reinicio de ScopePower")
    except Exception:
        pass
    desconectar_telescopio()
    desconectar_camara()
    voz.decir("Reiniciando ScopePower")
    log.warning("Reinicio de ScopePower pedido desde el móvil (%s)",
                "lo relanza lanzar_scopepower.bat" if os.environ.get("SCOPEPOWER_LANZADOR") == "1" else "se abre en una consola nueva")
    threading.Timer(0.8, _terminar_para_reiniciar).start()
    return jsonify({"ok": True, "espera": 8})


@app.route('/apagar/estado')
def apagar_estado():
    restante = max(0.0, _apagado["hasta"] - time.time()) if _apagado["accion"] else 0.0
    return jsonify({"accion": _apagado["accion"] if restante > 0 else None, "restante": round(restante)})


RUTA_INFO_GOTO = "static/info_objeto_goto.txt"


def eliminar_info_goto():
    with open(RUTA_INFO_GOTO, "w", encoding="utf-8") as f:
        f.write("ℹ️ No hay datos del objeto GOTO disponibles en esta sesión.\n")
    print("📄 info_objeto_goto.txt reiniciado.")


def guardar_info_goto(nombre, info=None, ra=None, dec=None, fuente=""):
    """
    Escribe static/info_objeto_goto.txt, de donde el apilado saca el nombre del
    objeto para el rótulo de la firma y el nombre del archivo en la galería.
    Se llama en TODOS los GoTo (página GoTo, planetario, Esta noche, recorrido),
    tengan o no ficha de Stellarium; `info` es la ficha (Stellarium o cuerpos
    menores) y puede faltar.
    """
    info = info or {}
    with open(RUTA_INFO_GOTO, "w", encoding="utf-8") as f:
        f.write(f"Nombre: {nombre}\n")
        if info.get("type"):
            f.write(f"Tipo: {info['type']}\n")
        constelacion = info.get("constellation") or info.get("iauConstellation")
        if constelacion:
            f.write(f"Constelación: {constelacion}\n")
        mag = info.get("vmag") or info.get("mag") or info.get("vmage")
        if mag is not None:
            f.write(f"Magnitud: {mag:.2f}\n")
        if ra is not None and dec is not None:
            ra_str, dec_str = formatear_ra_dec(float(ra), float(dec))
            f.write(f"RA: {ra_str}\nDEC: {dec_str}\n")
        if info.get("alt") is not None:
            f.write(f"Altitud: {info['alt']:.2f}°\n")
        if info.get("az") is not None:
            f.write(f"Azimut: {info['az']:.2f}°\n")
        if info.get("distance") is not None:
            f.write(f"Distancia: {info['distance']:.2f} AU\n")
        if info.get("phase") is not None:
            f.write(f"Fase: {info['phase'] * 100:.1f}%\n")
        if info.get("illumination") is not None:
            f.write(f"Iluminación: {info['illumination'] * 100:.1f}%\n")
        if fuente:
            f.write(f"Fuente: {fuente}\n")
        f.write(f"\n📅 Fecha y hora UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")


def cambiar_nombre_objeto_goto(nombre):
    """Sustituye solo la línea «Nombre:» del fichero (edición manual desde el apilado)."""
    lineas = []
    if os.path.isfile(RUTA_INFO_GOTO):
        with open(RUTA_INFO_GOTO, "r", encoding="utf-8", errors="replace") as f:
            lineas = [l for l in f if not l.lower().startswith("nombre:")]
    if not lineas or lineas[0].startswith("ℹ️"):
        lineas = ["\n", f"📅 Fecha y hora UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"]
    with open(RUTA_INFO_GOTO, "w", encoding="utf-8") as f:
        f.write(f"Nombre: {nombre}\n")
        f.writelines(lineas)

def desconectar_telescopio():
    if montura.esta_conectado():
        montura.desconectar()
        print("🔌 Telescopio desconectado.")

def desconectar_camara():
    if camara.esta_conectado():
        camara.desconectar()
        print("🔌 Cámara desconectada.")


def ejecutar_astap(ruta_fits_mono, timeout=90):
    """
    Lanza ASTAP sobre un FITS monocromo. Borra los .ini/.wcs anteriores para
    no leer una solución vieja si ASTAP falla, y no bloquea más de `timeout` s.
    Devuelve True si ASTAP terminó (haya resuelto o no), False si se colgó.
    """
    base = os.path.splitext(ruta_fits_mono)[0]
    for antiguo in (base + ".ini", base + ".wcs"):
        try:
            if os.path.exists(antiguo):
                os.remove(antiguo)
        except Exception:
            pass
    comando_astap = f'"{RUTA_ASTAP}" -f "{ruta_fits_mono}" -solve -r 50'
    try:
        subprocess.run(comando_astap, shell=True, capture_output=True, text=True, timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        print(f"⏱️ ASTAP no terminó en {timeout} s; se cancela la resolución.")
        return False



def calcular_altitud( ra=None, dec=None):

    observatorio = EarthLocation(lat=LATITUD_OBSERVATORIO * u.deg, lon=LONGITUD_OBSERVATORIO * u.deg, height=ALTITUD_OBSERVATORIO * u.m)
    tiempo_actual = Time.now()

    coordenadas_equatoriales = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg, frame='icrs')
    coordenadas_altaz = coordenadas_equatoriales.transform_to(AltAz(obstime=tiempo_actual, location=observatorio))

    return coordenadas_altaz.alt.deg

def generar_fits_monocromo_para_platesolve(ruta_rgb, ruta_monocromo):
    from astropy.io import fits
    with fits.open(ruta_rgb) as hdul:
        original = hdul[0]
        data = original.data.astype(np.float32)

        if data.ndim == 3 and data.shape[0] == 3:
            gris = np.mean(data, axis=0)
        else:
            gris = data

        gris = np.clip(gris, 0, 65535).astype(np.uint16)

        # Copiar cabecera
        header = original.header.copy()

        fits.PrimaryHDU(gris, header=header).writeto(ruta_monocromo, overwrite=True)


def formatear_ra_dec(ra_horas, dec_grados):
    # RA en horas → HH MM SS.S
    horas = int(ra_horas)
    minutos_ra = int((ra_horas - horas) * 60)
    segundos_ra = ((ra_horas - horas) * 60 - minutos_ra) * 60

    # DEC en grados → ±DD MM SS.S
    signo = '+' if dec_grados >= 0 else '-'
    grados = int(abs(dec_grados))
    minutos_dec = int((abs(dec_grados) - grados) * 60)
    segundos_dec = ((abs(dec_grados) - grados) * 60 - minutos_dec) * 60

    ra_str = f"{horas:02d} {minutos_ra:02d} {segundos_ra:04.1f}"
    dec_str = f"{signo}{grados:02d} {minutos_dec:02d} {segundos_dec:04.1f}"
    return ra_str, dec_str

def aplicar_flat_maestro(imagen_fits, ruta_flat):
    """
    Aplica un flat maestro a una imagen FITS RGB (formato (3, H, W)).
    Devuelve la imagen corregida y un booleano que indica si se aplicó.
    """
    if not os.path.exists(ruta_flat):
        print("ℹ️ No se encontró flat maestro.")
        return imagen_fits, False

    try:
        with fits.open(ruta_flat) as hdul_flat:
            flat_data = hdul_flat[0].data.astype(np.float32)

        if flat_data.shape != imagen_fits.shape:
            print("⚠️ Tamaño del flat no coincide con la imagen. Saltando corrección.")
            return imagen_fits, False

        # El flat de flats.py ya viene normalizado por canal Bayer a media 1 (así no
        # altera el balance de color). Un flat antiguo en ADU se normaliza aquí por
        # su media global. Se limita por abajo para no amplificar el ruido de las
        # esquinas muy viñeteadas.
        media_flat = float(np.mean(flat_data))
        if media_flat > 10:
            flat_data = flat_data / media_flat
        flat_data = np.clip(flat_data, 0.2, 5.0)
        imagen_corr = imagen_fits.astype(np.float32) / flat_data
        imagen_corr = np.clip(imagen_corr, 0, 65535).astype(np.uint16)

        print(f"📸 Flat maestro aplicado correctamente: {ruta_flat}")
        return imagen_corr, True

    except Exception as e:
        print(f"❌ Error al aplicar flat: {e}")
        return imagen_fits, False


import csv

import requests


_estado_stellarium_previo = {"ok": None}


def usar_stellarium():
    """
    [General] lanzar_stellarium_al_arrancar. Desde sep-2026 el GoTo resuelve los
    nombres con el catálogo local (catalogo_offline.py, los mismos datos que el
    planetario), así que Stellarium ya no hace falta y por defecto no se lanza:
    en un mini PC sin ventilador su renderizado continuo era el mayor consumo de
    la noche. Si está abierto (a mano o por esta opción) se sigue usando como
    fuente adicional de coordenadas.
    """
    return config["General"].get("lanzar_stellarium_al_arrancar", "false").strip().lower() == "true"


def estado_stellarium():
    try:
        url = "http://localhost:8090/api/objects/info?name=Polaris&format=json"
        r = requests.get(url, timeout=2)
        # Sin print: la pantalla GoTo pregunta cada 10 s y llenaba el log.
        # Solo se anota cuando cambia el estado.
        ok = r.status_code == 200 and "found" in r.json()
    except Exception:
        ok = False
    if ok != _estado_stellarium_previo.get("ok"):
        log.info("Stellarium %s", "responde" if ok else "no responde")
        _estado_stellarium_previo["ok"] = ok
    # No se lanza desde aquí: la pantalla GoTo pregunta cada 10 s y relanzarlo en
    # cada consulta lo mantenía siempre abierto. Solo arranca al iniciar la app o al
    # conectar el equipo, y solo si [General] lanzar_stellarium_al_arrancar = true.
    return ok

def lanzar_stellarium_si_no_esta_activo():
    try:
        # Verificar si ya está en ejecución
        tareas = subprocess.check_output('tasklist', shell=True, encoding='cp1252')  # ← Aquí está el cambio
        if "stellarium.exe" in tareas.lower():
            return          # ya está (o está arrancando): sin mensaje, se llama cada 10 s desde GoTo

        ruta_stellarium = config["General"].get("RUTA_STELLARIUM", "")
        if not os.path.exists(ruta_stellarium):
            print(f"❌ Stellarium no encontrado en: {ruta_stellarium}")
            return

        # Lanzar Stellarium en segundo plano
        #subprocess.Popen([ruta_stellarium], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.startfile(ruta_stellarium)
        print("🚀 Stellarium lanzado automáticamente.")

    except Exception as e:
        print(f"❌ Error al lanzar Stellarium: {e}")

import requests

def obtener_coords_desde_stellarium(nombre_objeto):
    try:
        url = f"http://localhost:8090/api/objects/info?name={nombre_objeto}&format=json"
        r = requests.get(url, timeout=3)
        data = r.json()
        if not data.get("found"):
            print(f"🔍 Stellarium no encontró '{nombre_objeto}'")
            return None

        info = {
            "ra": (data.get("raJ2000", 0) % 360) / 15,  #"ra": data.get("raJ2000", 0) / 15,
            "dec": data.get("decJ2000", 0),
            "alt": data.get("altitude"),
            "az": data.get("azimuth"),
            "mag": data.get("magnitude"),
            "vmag": data.get("vmag"),             # Añadir esto 👈
            "vmage": data.get("vmage"),           # Añadir esto 👈
            "type": data.get("type"),
            "name": data.get("name") or (data.get("designations") or "").split("-")[0].strip(),  #designation para M 106
            "constellation": data.get("constellation"),
            "iauConstellation": data.get("iauConstellation"),  # Por si acaso
            "distance": data.get("distance"),
            "phase": data.get("phase"),
            "illumination": data.get("illumination"),
        }

        print(f"✅ Stellarium info: {info}")
        return info
    except Exception as e:
        print(f"❌ Error al consultar Stellarium: {e}")
        return None

def obtener_coords_cometa(nombre_cometa):
    try:
        ahora = Time.now()
        nombre_cometa = nombre_cometa.upper().strip()
        obj = Horizons(id=nombre_cometa, location='500', epochs=ahora.jd, id_type='smallbody')
        eph = obj.ephemerides()
        ra = eph['RA'][0] / 15  # Convertir a horas
        dec = eph['DEC'][0]
        return ra, dec
    except Exception as e:
        print(f"Error al obtener coordenadas de JPL Horizons: {e}")
        return None, None

@app.route('/control')
def control_telescopio():
    return render_template('control.html', mostrar_volver=True)

@app.route('/estado-conexion')
def estado_conexion():
    """Estado de conexión de montura y cámara (menú principal)."""
    return jsonify({
        "conectado": montura.esta_conectado(),
        "conectando": _conexion_auto["en_curso"],
        "montura": montura.esta_conectado(),
        "camara": camara.esta_conectado(),
        "sistema": coordenadas.sistema() if montura.esta_conectado() else None,
        "error_montura": montura.ultimo_error,
        "error_camara": camara.ultimo_error,
        # Hora del PC: el móvil la compara con la suya y avisa si difieren (sin Internet el reloj del PC puede irse)
        "hora_utc_ms": int(time.time() * 1000),
        "hora_local": datetime.now().strftime("%H:%M:%S"),
    })


# ───────────────────────── Ubicación del observatorio ─────────────────────────
# Latitud, longitud y altitud viven en config.ini ([Observatorio]) y en las constantes
# de este módulo. Se pueden cambiar desde Configuración, a mano o con el GPS del móvil.
@app.route('/observatorio', methods=['GET', 'POST'])
def observatorio():
    global LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO
    if request.method == 'POST':
        datos = request.get_json(silent=True) or {}
        try:
            lat = float(datos["latitud"])
            lon = float(datos["longitud"])
            alt = float(datos.get("altitud", ALTITUD_OBSERVATORIO))
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "Latitud y longitud deben ser números"}), 400
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or not (-500 <= alt <= 9000):
            return jsonify({"ok": False, "error": "Valores fuera de rango"}), 400
        LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO = round(lat, 6), round(lon, 6), int(round(alt))
        valores = {"latitud_observatorio": LATITUD_OBSERVATORIO,
                   "longitud_observatorio": LONGITUD_OBSERVATORIO,
                   "altitud_observatorio": ALTITUD_OBSERVATORIO}
        if "autor" in datos:   # autor de las fotos, para la firma
            valores["autor"] = str(datos["autor"]).strip()[:60]
        _guardar_en_config("Observatorio", valores)
        if "firma" in datos:
            _guardar_en_config("Apilado", {"firma": "true" if datos["firma"] else "false"})
        log.info("Ubicación del observatorio cambiada: %.5f, %.5f, %d m (%s)", lat, lon, ALTITUD_OBSERVATORIO, datos.get("origen", "manual"))
        voz.decir("Ubicación del observatorio actualizada")
        stellarium = _stellarium_poner_ubicacion(LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO)
    else:
        stellarium = None
    obs = firma.datos_observatorio()
    return jsonify({"ok": True, "latitud": LATITUD_OBSERVATORIO, "longitud": LONGITUD_OBSERVATORIO,
                    "altitud": ALTITUD_OBSERVATORIO, "autor": obs["autor"], "firma": obs["activa"],
                    "hora_utc_ms": int(time.time() * 1000),
                    "hora_local": datetime.now().strftime("%H:%M:%S"),
                    "zona_horaria": time.strftime("%Z"), "stellarium": stellarium})


def _stellarium_poner_ubicacion(lat, lon, alt):
    """Misma ubicación en Stellarium (si está abierto), para que sus alturas y acimuts coincidan."""
    try:
        r = requests.post("http://localhost:8090/api/location/setlocationfields",
                          data={"latitude": lat, "longitude": lon, "altitude": int(alt), "name": "ScopePower", "planet": "Earth"},
                          timeout=3)
        ok = r.status_code == 200
        log.info("Ubicación enviada a Stellarium: %s", "ok" if ok else f"respuesta {r.status_code}")
        return ok
    except Exception as e:
        log.info("Stellarium no disponible para actualizar la ubicación: %s", e)
        return False


@app.route('/hora', methods=['POST'])
def poner_pc_en_hora():
    """Pone el reloj del PC con la hora del móvil (milisegundos UTC, como Date.now())."""
    import hora_sistema
    datos = request.get_json(silent=True) or {}
    try:
        hora_ms = float(datos["hora_utc_ms"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "Falta la hora del móvil"}), 400
    try:
        antes = hora_sistema.poner_en_hora(hora_ms)
    except hora_sistema.SinPermisoHora as e:
        return jsonify({"ok": False, "error": str(e), "sin_permiso": True}), 403
    except Exception as e:
        log.exception("No se pudo poner el PC en hora")
        return jsonify({"ok": False, "error": f"No se pudo cambiar la hora: {e}"}), 500
    voz.decir("Reloj del ordenador puesto en hora")
    return jsonify({"ok": True, "corregido_s": round(-antes, 1), "hora_utc_ms": int(time.time() * 1000),
                    "hora_local": datetime.now().strftime("%H:%M:%S"), "zona_horaria": time.strftime("%Z")})


@app.route('/conectar-equipo', methods=['POST'])
def conectar_equipo():
    """
    Botón Conectar/Reconectar del menú: montura y cámara (y Stellarium).
    Reconecta de verdad (desconectar + conectar) para aplicar un cambio de driver.
    """
    return jsonify(_conectar_equipo_completo())


@app.route('/ascom-drivers')
def ascom_drivers():
    """Drivers ASCOM registrados (Telescope o Camera) sin abrir el diálogo Chooser en el PC."""
    tipo = request.args.get("tipo", "Telescope")
    drivers = []
    try:
        pythoncom.CoInitialize()
        profile = win32com.client.Dispatch("ASCOM.Utilities.Profile")
        for kv in profile.RegisteredDevices(tipo):
            drivers.append({"progid": str(kv.Key), "name": str(kv.Value)})
    except Exception as e:
        log.warning("No se pudo leer el perfil ASCOM (%s): %s", tipo, e)
    drivers.sort(key=lambda d: d["name"].lower())
    return jsonify({"drivers": drivers})


@app.route('/montura/limpiar-sync', methods=["POST"])
def montura_limpiar_sync():
    """Borra a mano las sincronizaciones/modelo de apuntado guardados en la app SynScan."""
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "mensaje": "Pausa o detén el apilado antes de borrar las sincronizaciones"}), 409
    try:
        ok, msg = montura.ejecutar(limpiar_sincronizaciones, timeout=20)
    except ErrorHardware as e:
        return jsonify({"ok": False, "mensaje": f"Montura no disponible: {e}"}), 503
    voz.decir("Sincronizaciones anteriores borradas" if ok else "No se han podido borrar las sincronizaciones")
    return jsonify({"ok": ok, "mensaje": msg})


@app.route('/montura/asentamiento', methods=["GET", "POST"])
def montura_asentamiento():
    """
    Segundos de asentamiento de la montura ([Montura] asentamiento_s): espera entre el
    final de un movimiento y la siguiente foto. GET lo devuelve; POST {segundos} lo guarda.
    """
    if request.method == "POST":
        datos = request.get_json(silent=True) or {}
        try:
            segundos = float(datos.get("segundos"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "mensaje": "Indica un número de segundos"}), 400
        if not 0 <= segundos <= 60:
            return jsonify({"ok": False, "mensaje": "El asentamiento debe estar entre 0 y 60 s"}), 400
        cfg = configparser.ConfigParser()
        cfg.read("config.ini")
        if "Montura" not in cfg:
            cfg["Montura"] = {}
        cfg["Montura"]["asentamiento_s"] = f"{segundos:g}"
        # Las secciones con su propio valor mandarían sobre este: se vacían para que
        # el ajuste de la web sea el que se aplica en todos los movimientos.
        for sec in ("Centrado", "Alineacion", "Dithering"):
            if sec in cfg and cfg[sec].get("asentamiento_s", "").strip():
                cfg[sec]["asentamiento_s"] = ""
        with open("config.ini", "w") as f:
            cfg.write(f)
        log.info("Asentamiento de la montura: %.1f s", segundos)
        return jsonify({"ok": True, "segundos": asentamiento_montura(), "mensaje": f"Asentamiento guardado: {segundos:g} s"})
    return jsonify({"ok": True, "segundos": asentamiento_montura()})


@app.route('/reconectar-telescopio')
def reconectar_telescopio():
    try:
        montura.reconectar()
    except ErrorHardware as e:
        return f"❌ Error: {e}"
    return "✅ Telescopio reconectado correctamente"

@app.route('/move')
def move():
    direction = request.args.get('dir')
    speed = float(request.args.get('speed', 0.5))

    def _mover(telescope):
        # Guardar estado previo de Tracking
        estaba_tracking = telescope.Tracking

        if direction == 'N' and telescope.CanMoveAxis(1):
            telescope.MoveAxis(1, speed)
        elif direction == 'S' and telescope.CanMoveAxis(1):
            telescope.MoveAxis(1, -speed)
        elif direction == 'E' and telescope.CanMoveAxis(0):
            telescope.MoveAxis(0, speed)
        elif direction == 'W' and telescope.CanMoveAxis(0):
            telescope.MoveAxis(0, -speed)
        else:
            return "❌ Movimiento no permitido por el driver (CanMoveAxis = False)"

        # Restaurar el estado de Tracking si era True
        if estaba_tracking and not telescope.Tracking:
            telescope.Tracking = True

        return "Moving"

    try:
        return montura.ejecutar(_mover, timeout=10)
    except ErrorHardware as e:
        return f"❌ {e}"



@app.route('/stop')
def stop():
    def _parar(telescope):
        # Detener movimiento
        telescope.MoveAxis(0, 0)
        telescope.MoveAxis(1, 0)
        telescope.Tracking = True #Esto es para que mantenga el tranking funcionando despues de mover el telescopio, si no se queda parado
        # Reactivar seguimiento
        if not telescope.Tracking:
            telescope.Tracking = True
            print("✅ Seguimiento reactivado después de parar.")
        return "Stopped"

    try:
        return montura.ejecutar(_parar, timeout=10)
    except ErrorHardware as e:
        return f"❌ {e}"


# ─── Pad de movimiento (joystick virtual) ─────────────────────────────────
# El navegador manda la velocidad de cada eje en grados/segundo cuando cambia
# (escalones fijos: los mismos de las flechas, 0,1 / 0,5 / 1 / 3 °/s) y la
# repite cada 500 ms como latido. Si deja de mandar (se pierde el toque, se cae
# la red, se cierra la pestaña) un vigilante para los ejes al segundo: un
# MoveAxis sin su parada dejaría la montura girando. Las velocidades se pasan
# al driver tal cual, igual que hace /move con las flechas.
_pad = {"activo": False, "ultimo": 0.0, "vx": 0.0, "vy": 0.0, "lock": threading.Lock()}
PAD_TIMEOUT_S = 1.2


def _rangos_axisrates(tel, eje):
    """
    Rangos (min, max) en °/s que admite MoveAxis en ese eje, o [] si el driver
    no lo dice. La colección AxisRates se recorre por enumeración COM: el
    índice de Item() es 1-based en unos drivers y 0-based en otros.
    """
    try:
        rates = tel.AxisRates(eje)
    except Exception:
        return []
    rangos = []
    try:
        for r in rates:
            rangos.append((float(r.Minimum), float(r.Maximum)))
    except Exception:
        rangos = []
    if not rangos:
        try:
            n = int(rates.Count)
        except Exception:
            return []
        for i in list(range(1, n + 1)) + list(range(0, n)):
            try:
                r = rates.Item(i)
                par = (float(r.Minimum), float(r.Maximum))
                if par not in rangos:
                    rangos.append(par)
            except Exception:
                continue
    return rangos


_pad_rates_anotados = False


def _anotar_axisrates(tel):
    """Deja en el log, una vez, las velocidades que anuncia el driver (para saber qué acepta la montura)."""
    global _pad_rates_anotados
    if _pad_rates_anotados:
        return
    _pad_rates_anotados = True
    for eje in (0, 1):
        rangos = _rangos_axisrates(tel, eje)
        log.info("Pad: AxisRates eje %d: %s °/s", eje,
                 ", ".join(f"{lo:g}–{hi:g}" if lo != hi else f"{lo:g}" for lo, hi in rangos) or "no lo dice")


def _pad_aplicar(vx, vy):
    def _mover(tel):
        _anotar_axisrates(tel)
        estaba_tracking = tel.Tracking
        if tel.CanMoveAxis(0):
            tel.MoveAxis(0, vx)
        if tel.CanMoveAxis(1):
            tel.MoveAxis(1, vy)
        if vx == 0 and vy == 0 and estaba_tracking and not tel.Tracking:
            tel.Tracking = True
        return "ok"
    return montura.ejecutar(_mover, timeout=10)


def _pad_parar(motivo=""):
    with _pad["lock"]:
        estaba = _pad["activo"]
        _pad["activo"] = False
        _pad["vx"] = _pad["vy"] = 0.0
    if estaba:
        try:
            _pad_aplicar(0.0, 0.0)
            if motivo:
                log.warning("Pad: ejes parados por %s", motivo)
        except ErrorHardware as e:
            log.error("Pad: no se pudo parar la montura: %s", e)


def _pad_vigilante():
    while True:
        time.sleep(0.3)
        with _pad["lock"]:
            caducado = _pad["activo"] and (time.time() - _pad["ultimo"] > PAD_TIMEOUT_S)
        if caducado:
            _pad_parar(f"silencio de más de {PAD_TIMEOUT_S} s")


threading.Thread(target=_pad_vigilante, name="pad-vigilante", daemon=True).start()


@app.route('/pad/mover', methods=['POST'])
def pad_mover():
    """JSON {vx, vy} en °/s (signo = sentido; eje 0 = acimut/AR, eje 1 = altura/DEC)."""
    d = request.get_json(silent=True) or {}
    try:
        vx = max(-20.0, min(20.0, float(d.get("vx", 0))))
        vy = max(-20.0, min(20.0, float(d.get("vy", 0))))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Velocidades no válidas"}), 400
    if vx == 0 and vy == 0:
        _pad_parar()
        return jsonify({"ok": True, "parado": True})
    with _pad["lock"]:
        # Latido con la misma velocidad: basta refrescar el vigilante, sin molestar a la montura
        repetido = _pad["activo"] and _pad["vx"] == vx and _pad["vy"] == vy
        _pad.update(activo=True, ultimo=time.time(), vx=vx, vy=vy)
    if repetido:
        return jsonify({"ok": True, "repetido": True})
    try:
        _pad_aplicar(vx, vy)
        return jsonify({"ok": True})
    except ErrorHardware as e:
        _pad_parar()
        return jsonify({"ok": False, "mensaje": str(e)}), 409


@app.route('/pad/parar', methods=['POST'])
def pad_parar():
    _pad_parar()
    return jsonify({"ok": True})


# Ruta para capturar una imagen con la cámara ASCOM
import numpy as np
from astropy.io import fits

def capturar_y_guardar(apilar=False, usar_dark=True, generar_preview=True, descripcion="Captura", ganancia=None, copia=None,
                       exposicion=None):
    """
    Expone, calibra (dark/flat), escribe static/captura.fits y, si `apilar`,
    una copia en capturas/ que recoge el apilador. Devuelve un dict:
      {"ok": True, "mensaje": ..., "ruta": ...}  o
      {"ok": False, "mensaje": ..., "cancelada": bool}
    No usa `request`: sirve tanto para la ruta web como para el bucle del servidor.
    `ganancia`, `exposicion`: solo para esta captura (Cámara); si son None, las de config.ini.
    Con una exposición distinta se resta el dark de esa exposición, si lo hay.
    `copia`: None o {"carpeta", "formato" ("fits" | "jpg"), "nombre"} para guardar además
    esta foto en una carpeta (modo continuo). El FITS es el mismo que static/captura.fits;
    el JPG es la foto revelada a resolución completa. Su ruta vuelve en "archivo".
    """
    # Si la cámara ya está exponiendo, no encolar otra captura detrás
    if camara.ocupado:
        return {"ok": False, "mensaje": "⏳ Ya hay una captura en curso", "ocupada": True}
    if foto_dia.trabajo_timelapse.en_curso:
        return {"ok": False, "mensaje": "⏳ Hay un timelapse en marcha; párala antes desde Cámara", "ocupada": True}

    nuevo_config = configparser.ConfigParser()
    nuevo_config.read("config.ini")
    EXPOSICION = float(nuevo_config["Dispositivos"].get("exposicion_captura", 10)) if exposicion is None else float(exposicion)
    disp = _dispositivos()
    BINNING, FOCAL_LEN = disp["binning"], disp["focal"]     # valores en vivo, no los de arranque
    GANANCIA = disp["ganancia"] if ganancia is None else int(ganancia)

    def _exponer(cam):
        print(f"📸 Iniciando exposición de {EXPOSICION} segundos...")
        voz.decir(f"Empieza exposición de {EXPOSICION:.0f} segundos", categoria="capturas")
        frame = capturar_frame(cam, EXPOSICION, BINNING, descripcion=descripcion, ganancia=GANANCIA)
        print("✅ Exposición finalizada.")
        voz.decir("Exposición terminada", categoria="capturas")
        return frame, cam.SensorType, cam.PixelSizeX, cam.PixelSizeY, leer_ganancia(cam)["actual"]

    try:
        imagen_fits, sensor_type, pixel_size_x, pixel_size_y, ganancia_real = camara.ejecutar(
            _exponer, timeout=EXPOSICION + 90)
    except CapturaCancelada:
        voz.decir("Captura cancelada")
        return {"ok": False, "mensaje": "⏹️ Captura cancelada", "cancelada": True}
    except ErrorHardware as e:
        voz.decir("Error de cámara")
        return {"ok": False, "mensaje": f"Error: {e}"}

    # RA/Dec solo van a la cabecera del FITS (OBJCTRA/OBJCTDEC). Si la montura no
    # contesta (SynScan sin enlace, wifi caído) la foto sigue siendo buena: se usa la
    # última posición conocida y se avisa, en vez de tirar la toma y parar el bucle.
    try:
        ra_actual, dec_actual = montura.ejecutar(
            lambda tel: (tel.RightAscension, tel.Declination), timeout=10)
        _ULTIMA_POSICION_MONTURA.update({"ra": ra_actual, "dec": dec_actual})
    except ErrorHardware as e:
        if _ULTIMA_POSICION_MONTURA:
            ra_actual, dec_actual = _ULTIMA_POSICION_MONTURA["ra"], _ULTIMA_POSICION_MONTURA["dec"]
            log.warning("Montura sin respuesta al leer RA/Dec (%s): la cabecera lleva la última posición conocida", e)
        elif ULTIMO_GOTO:
            ra_actual, dec_actual = coordenadas.a_montura(ULTIMO_GOTO["ra"], ULTIMO_GOTO["dec"])
            log.warning("Montura sin respuesta al leer RA/Dec (%s): la cabecera lleva el destino del último GoTo", e)
        else:
            return {"ok": False, "mensaje": f"Error: {e}"}

    try:
        # ───── Aplicar dark maestro ─────
        ruta_dark = f"calibraciones/dark_{EXPOSICION:.2f}s.fits"
        dark_aplicado = False
        if usar_dark and os.path.exists(ruta_dark):
            print(f"🌑 Aplicando dark maestro: {ruta_dark}")
            with fits.open(ruta_dark) as hdul_dark:
                dark_data = hdul_dark[0].data.astype(np.float32)

                if dark_data.shape == imagen_fits.shape:
                    imagen_fits = imagen_fits.astype(np.float32) - dark_data
                    imagen_fits = np.clip(imagen_fits, 0, 65535).astype(np.uint16)
                    dark_aplicado = True
                else:
                    print(f"⚠️ Tamaño del dark no coincide. Saltando corrección.")
        else:
            print("ℹ️ No se encontró dark maestro para esta exposición.")

        # ───── Aplicar flat maestro ─────
        ruta_flat = "calibraciones/flat.fits"
        imagen_fits, flat_aplicado = aplicar_flat_maestro(imagen_fits, ruta_flat)


        # ───── Crear FITS ─────
        hdu = fits.PrimaryHDU(imagen_fits)
        # Mapa de enum → nombre
        _map = {2: 'RGGB', 3: 'CMYG', 4: 'CMYG2', 5: 'LRGB'}
        patron_bayer = _map.get(sensor_type, 'Mono/Colour')
        hdu.header['BAYERPAT'] = (patron_bayer, 'Patron Bayer segun ASCOM SensorType')
        hdu.header['XBAYROFF'] = (0, 'Desplazamiento X del patron Bayer')
        hdu.header['YBAYROFF'] = (0, 'Desplazamiento Y del patron Bayer')

        hdu.header['BZERO'] = 32768
        hdu.header['BSCALE'] = 1
        hdu.header['COMMENT'] = f"Imagen capturada con binning {BINNING}x{BINNING}"
        ra_str, dec_str = formatear_ra_dec(ra_actual, dec_actual)
        hdu.header['OBJCTRA'] = ra_str
        hdu.header['OBJCTDEC'] = dec_str
        hdu.header['XPIXSZ'] = pixel_size_x * BINNING
        hdu.header['YPIXSZ'] = pixel_size_y * BINNING
        hdu.header['FOCALLEN'] = FOCAL_LEN
        hdu.header['XBINNING'] = BINNING
        hdu.header['YBINNING'] = BINNING
        hdu.header['IMAGETYP'] = 'LIGHT'
        if ganancia_real is not None:
            hdu.header['GAIN'] = (ganancia_real, 'Ganancia de la camara')
        hdu.header['DATE-OBS'] = time.strftime("%Y%m%d_%H%M%S")
        hdu.header['DARKCORR'] = ('T' if dark_aplicado else 'F', 'Dark master aplicado')
        if dark_aplicado:
            hdu.header['DARKFILE'] = (os.path.basename(ruta_dark), 'Archivo dark utilizado')
        hdu.header['FLATCORR'] = ('T' if flat_aplicado else 'F', 'Flat master aplicado')
        if flat_aplicado:
            hdu.header['FLATFILE'] = (os.path.basename(ruta_flat), 'Archivo flat utilizado')

        hdu.writeto(RUTA_IMAGEN, overwrite=True)
        ruta_guardada = RUTA_IMAGEN

        # ───── Copia de esta foto en la carpeta de la serie (modo continuo) ─────
        archivo_copia = None
        if copia and copia.get("formato") == "fits":
            os.makedirs(copia["carpeta"], exist_ok=True)
            archivo_copia = os.path.join(copia["carpeta"], copia["nombre"] + ".fits")
            hdu.writeto(archivo_copia, overwrite=True)

        # ───── Guardar para apilado si se solicita ─────
        if apilar:
            os.makedirs("capturas", exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            ruta_destino = os.path.join("capturas", f"img_{timestamp}.fits")
            hdu.writeto(ruta_destino, overwrite=True)
            ruta_guardada = ruta_destino

        # ───── Miniatura rápida de esta toma (superpíxel, ~0,2 s) ─────
        try:
            mini = miniatura_cfa_rgb(imagen_fits, patron_bayer, ancho=600) if imagen_fits.ndim == 2 \
                else apply_stf_color(cv2.resize(imagen_fits, (600, int(imagen_fits.shape[0] * 600 / imagen_fits.shape[1]))))
            cv2.imwrite(RUTA_ULTIMA_CAPTURA, cv2.cvtColor(mini, cv2.COLOR_RGB2BGR) if mini.ndim == 3 else mini,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
            ULTIMA_CAPTURA.update({"version": ULTIMA_CAPTURA["version"] + 1,
                                   "hora": time.strftime("%H:%M:%S"),
                                   "exposicion": EXPOSICION, "dark": dark_aplicado,
                                   "ganancia": ganancia_real, "pixel_um": float(pixel_size_x)})
        except Exception as e:
            log.warning("No se pudo generar la miniatura de la captura: %s", e)

        if not generar_preview:
            return {"ok": True, "mensaje": f"Captura de {EXPOSICION:.0f} s guardada", "ruta": ruta_guardada,
                    "dark": dark_aplicado, "ganancia": ganancia_real, "exposicion": EXPOSICION, "archivo": archivo_copia}

        # ───   Debayer para preview en RGB   ───
        # imagen_fits aquí es un array 2D (raw CFA de la cámara, uint16).
        # Se debayeriza directamente a 16 bits con el patrón escrito en la
        # cabecera FITS; nunca reducir a 8 bits antes de este paso.
        if imagen_fits.ndim == 2 and patron_bayer in ('RGGB', 'BGGR', 'GRBG', 'GBRG'):
            rgb = debayer_rgb(imagen_fits, patron_bayer)
        else:
            # Sensor mono (o patrón no Bayer): mostrar en escala de grises
            rgb = imagen_fits

        # ───   Estirado de histograma y saturación   ───
        imagen_mtf = apply_stf_color(rgb)

        # Cargar intensidad de saturación
        nuevo_config = configparser.ConfigParser()
        nuevo_config.read("config.ini")
        intensidad_color = float(nuevo_config["Dispositivos"].get("color_intensity", 1.0))

        # Aplicar saturación real
        imagen_mtf = aumentar_saturacion(imagen_mtf, factor=intensidad_color)

        # Guardar la versión para la web, reducida y en JPG (cv2 espera BGR)
        if imagen_mtf.ndim == 3:
            imagen_web = cv2.cvtColor(imagen_mtf, cv2.COLOR_RGB2BGR)
        else:
            imagen_web = imagen_mtf
        guardar_jpg_web(RUTA_IMAGEN_WEB, imagen_web)
        if copia and copia.get("formato") == "jpg":
            # La foto revelada entera (no la reducida de la web), para tener la serie sin el peso del FITS
            os.makedirs(copia["carpeta"], exist_ok=True)
            archivo_copia = os.path.join(copia["carpeta"], copia["nombre"] + ".jpg")
            cv2.imwrite(archivo_copia, imagen_web, [cv2.IMWRITE_JPEG_QUALITY, 92])

        return {"ok": True, "mensaje": f"Captura de {EXPOSICION:.0f} s guardada", "ruta": ruta_guardada,
                "dark": dark_aplicado, "ganancia": ganancia_real, "exposicion": EXPOSICION, "archivo": archivo_copia}

    except Exception as e:
        log.exception("Error al procesar la captura")
        return {"ok": False, "mensaje": f"❌ Error al capturar la imagen: {e}"}


def _exposicion_pedida(valor):
    """Exposición opcional de una foto de Cámara (segundos, 0.001–3600); None = la de config.ini."""
    try:
        exposicion = float(str(valor if valor is not None else "").strip().replace(",", "."))
    except ValueError:
        return None
    return exposicion if 0.001 <= exposicion <= 3600 else None


# Función para capturar imagen y mostrarla en la web
@app.route('/capturar-imagen')
def capturar_imagen():
    apilar = request.args.get("apilar", "0") == "1"
    usar_dark = request.args.get("dark", "1") != "0"   # ?dark=0: no restar dark aunque exista
    quiere_json = request.args.get("json", "0") == "1"
    try:
        ganancia = int(request.args["ganancia"]) if request.args.get("ganancia", "").strip() else None
    except ValueError:
        ganancia = None
    exposicion = _exposicion_pedida(request.args.get("exposicion"))
    resultado = capturar_y_guardar(apilar=apilar, usar_dark=usar_dark, ganancia=ganancia, exposicion=exposicion)
    if quiere_json:
        codigo = 200 if resultado["ok"] else (409 if resultado.get("ocupada") else 500)
        return jsonify(resultado), codigo
    if not resultado["ok"]:
        if resultado.get("ocupada"):
            return resultado["mensaje"], 409
        return resultado["mensaje"]
    if apilar:
        return resultado["mensaje"]
    return render_template('imagen.html', mostrar_volver=True, hay_foto=True)

@app.route('/imagen-capturada')
def imagen_capturada():
    if not os.path.exists(RUTA_IMAGEN_WEB):
        return send_file("static/esperando.png", mimetype="image/png")
    response = send_file(RUTA_IMAGEN_WEB, mimetype='image/jpeg')
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route('/buscar-objetos')
def buscar_objetos():
    """
    Sugerencias para el GoTo: ?q=patrón. '*' vale por cualquier texto y '?'
    por un carácter ("*tsuchinshan*", "c/2023*", "12p*"); sin comodines
    busca que lo contenga. Mira en cometas y asteroides (listas del MPC) y
    en el catálogo de Esta noche. Devuelve nombre exacto, tipo, magnitud y,
    para los del catálogo, coordenadas para ir directo.
    """
    q = request.args.get("q", "").strip()
    if len(q.replace("*", "").replace("?", "").strip()) < 2:
        return jsonify({"resultados": []})
    resultados = []
    try:
        rx = cuerpos_menores._patron_a_regex(q)
        for o in esta_noche.cargar_catalogo():
            # Cada nombre por separado: "*31" debe encajar con "M 31", no con un
            # texto pegado donde "NGC 6531" del nombre alternativo acabe en 31
            if not rx:
                break
            principal = any(rx.search(cuerpos_menores._normalizar(x)) for x in (o["id"], o["nombre"]) if x)
            por_alt = (not principal) and bool(o["nombre_alt"]) and bool(rx.search(cuerpos_menores._normalizar(o["nombre_alt"])))
            if principal or por_alt:
                # Si encaja por el nombre alternativo (p. ej. "NGC 6531" de M21), mostrarlo para que se entienda
                detalle = f"{o['id']}" + (f" · {o['nombre_alt']}" if por_alt else "") + f" · {o['tipo']}" + (f" · mag {o['mag']}" if o["mag"] is not None else "")
                resultados.append({"nombre": o["nombre"], "detalle": detalle, "tipo": o["tipo"], "mag": o["mag"],
                                   "ra": o["ra_h"], "dec": o["dec_deg"], "_orden": 1 if por_alt else 0})
    except Exception as e:
        log.warning("Búsqueda en el catálogo: %s", e)
    try:
        for c in cuerpos_menores.buscar_patron(q, maximo=40):
            mag = None
            try:
                _, _, info = cuerpos_menores.coordenadas(c["nombre"], LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO)
                mag = info["mag"]
            except Exception:
                pass
            resultados.append({"nombre": c["nombre"], "detalle": ("Cometa" if c["tipo"] == "cometa" else "Asteroide") + (f" · mag {mag}" if mag is not None else ""),
                               "tipo": c["tipo"], "mag": mag, "_orden": 0})
    except Exception as e:
        log.warning("Búsqueda en cuerpos menores: %s", e)
    # Después, el catálogo completo del planetario (OpenNGC y estrellas): lo que no
    # está en Esta noche, p. ej. "ngc 7331" o "albireo"
    try:
        ya = [o["id"] for o in esta_noche.cargar_catalogo()]
        for f in catalogo_offline.buscar_patron(q, maximo=15, excluir_ids=ya):
            detalle = f["id"]
            if f["name_catalogo"] not in (f["id"], f["name"]):
                detalle += f" · {f['name_catalogo']}"
            detalle += f" · {f['type']}" + (f" · mag {f['mag']}" if f["mag"] is not None else "")
            resultados.append({"nombre": f["name"], "detalle": detalle, "tipo": f["type"], "mag": f["mag"],
                               "ra": f["ra"], "dec": f["dec"], "_orden": 2})
    except Exception as e:
        log.warning("Búsqueda en el catálogo local: %s", e)
    # Primero los de Esta noche (por nombre, luego por el alternativo), después cometas y
    # asteroides, y al final el catálogo completo; dentro de cada grupo, los más brillantes
    resultados.sort(key=lambda r: (r.pop("_orden", 0), r["mag"] is None, r["mag"] if r["mag"] is not None else 99))
    return jsonify({"resultados": resultados[:40]})


@app.route('/cuerpos-menores/estado')
def cuerpos_menores_estado():
    e = cuerpos_menores.estado()
    try:
        n = len(cuerpos_menores.cargar())
    except Exception:
        n = 0
    return jsonify({"edad_cometas_dias": e["cometas"], "edad_asteroides_dias": e["asteroides"], "cuerpos": n})


@app.route('/cuerpos-menores/actualizar', methods=['POST'])
def cuerpos_menores_actualizar():
    n, msg = cuerpos_menores.actualizar(forzar=True)
    return jsonify({"ok": n > 0, "mensaje": msg, **cuerpos_menores.estado()})


@app.route('/esta-noche')
def vista_esta_noche():
    return render_template('esta_noche.html', mostrar_volver=True)


@app.route('/planetario')
def vista_planetario():
    """Mapa del cielo offline (Celestia Atlas, static/atlas): tocar un objeto y hacer GOTO."""
    return render_template('planetario.html', mostrar_volver=True)


# Geometría del sensor (px y tamaño de píxel) para dibujar el encuadre de la
# cámara sobre el mapa. Se lee una sola vez de la cámara cuando está libre.
_geometria_camara = {}
# Última vez que la montura no contestó: hasta cuándo no volver a leerla y por qué
_montura_sin_respuesta = {"hasta": 0.0, "error": ""}


@app.route('/planetario/estado')
def planetario_estado():
    """
    Lo que el planetario necesita del PC en cada refresco: observador y hora,
    dónde apunta la montura (J2000), el encuadre de la cámara y cómo va el centrado.
    """
    montura_info = {"conectada": False}
    if not montura.esta_conectado():
        pass
    elif time.time() < _montura_sin_respuesta["hasta"]:
        # El driver está conectado pero la montura no contesta (SynScan sin enlace, montura
        # apagada): no insistir cada 2 s, que cada intento tarda y deja un error en el log.
        montura_info = {"conectada": False, "error": _montura_sin_respuesta["error"]}
    else:
        try:
            ra_m, dec_m, slewing = montura.ejecutar(
                lambda tel: (tel.RightAscension, tel.Declination, bool(tel.Slewing)),
                timeout=5, conectar=False)
            ra_h, dec_deg = coordenadas.desde_montura(ra_m, dec_m)
            montura_info = {"conectada": True, "ra_h": ra_h, "dec_deg": dec_deg, "slewing": slewing}
        except Exception as e:   # ErrorHardware o driver que no responde: el mapa sigue sin marcador
            _montura_sin_respuesta.update({"hasta": time.time() + 20, "error": str(e)})
            montura_info = {"conectada": False, "error": str(e)}
    if not _geometria_camara and camara.esta_conectado() and not camara.ocupado:
        try:
            ancho, alto, px = camara.ejecutar(
                lambda cam: (int(cam.CameraXSize), int(cam.CameraYSize), float(cam.PixelSizeX)),
                timeout=5, conectar=False)
            _geometria_camara.update({"ancho_px": ancho, "alto_px": alto, "pixel_um": px})
        except Exception as e:
            log.debug("Geometría de la cámara no disponible aún: %s", e)
    return jsonify({
        "observador": {"latitud": LATITUD_OBSERVATORIO, "longitud": LONGITUD_OBSERVATORIO,
                       "altitud": ALTITUD_OBSERVATORIO},
        "hora_utc_ms": int(time.time() * 1000),
        "altura_minima": float(ALTURA_MINIMA),
        "focal_mm": FOCAL_LEN,
        "camara": dict(_geometria_camara),
        "montura": montura_info,
        "ultimo_goto": ({"objeto": ULTIMO_GOTO.get("objeto", ""), "ra_h": ULTIMO_GOTO.get("ra"),
                         "dec_deg": ULTIMO_GOTO.get("dec")} if ULTIMO_GOTO else None),
        "centrar_tras_goto": bool(centrado.leer_config().get("centrar_tras_goto", True)),
        "centrado": centrado.trabajo_centrado.estado(),
        # Giro de la cámara (CROTA2 del último plate solving), para dibujar el encuadre real
        "rotacion_camara": rotacion_camara.leer(),
    })


@app.route('/esta-noche/objetos')
def esta_noche_objetos():
    try:
        datos = esta_noche.obtener(LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO,
                                   altura_minima=float(ALTURA_MINIMA))
    except Exception as e:
        log.exception("Error calculando Esta noche")
        return jsonify({"error": str(e)}), 500
    return jsonify(datos)


@app.route('/foto-prueba')
def foto_prueba():
    """Página de foto de prueba: no dispara nada al abrirse; el botón de dentro captura."""
    return render_template('imagen.html', mostrar_volver=True, hay_foto=os.path.exists(RUTA_IMAGEN_WEB),
                           niveles_dia={k: v["nivel"] for k, v in foto_dia.OBJETIVOS.items()})


def sincronizar_con_ultima_foto():
    """
    Resuelve la última foto de prueba con ASTAP y sincroniza la montura con
    esas coordenadas (un punto, sin mover el telescopio). Devuelve un dict
    con ok, mensaje y, si se resolvió, el error que tenía la montura en
    minutos de arco.
    """
    if not os.path.exists(RUTA_IMAGEN):
        return {"ok": False, "mensaje": "No hay ninguna foto que resolver; haz una foto primero."}
    if bucle.activo and not bucle.pausado:
        return {"ok": False, "mensaje": "El apilado está capturando; páusalo antes de sincronizar."}
    try:
        ra_m, dec_m = montura.ejecutar(lambda tel: (tel.RightAscension, tel.Declination), timeout=10)
    except ErrorHardware as e:
        return {"ok": False, "mensaje": f"Montura no disponible: {e}"}

    ruta_mono = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.fits"
    generar_fits_monocromo_para_platesolve(RUTA_IMAGEN, ruta_mono)
    if not ejecutar_astap(ruta_mono):
        return {"ok": False, "mensaje": "ASTAP no respondió a tiempo."}
    coords_res = extraer_coordenadas_ini(RUTA_INI)
    if coords_res is None:
        return {"ok": False, "mensaje": "ASTAP no ha podido resolver la foto (¿pocas estrellas, desenfoque, nubes?)."}
    ra_deg, dec_deg = coords_res
    ra_h = float(ra_deg) / 15.0
    if calcular_altitud(ra_h, dec_deg) < float(ALTURA_MINIMA):
        return {"ok": False, "mensaje": "La foto apunta por debajo de la altura mínima; no se sincroniza."}

    # Error que tenía la montura (comparado en J2000)
    ra_i, dec_i = coordenadas.desde_montura(ra_m, dec_m)
    error = float(SkyCoord(ra=ra_i * u.hourangle, dec=dec_i * u.deg).separation(
        SkyCoord(ra=ra_h * u.hourangle, dec=dec_deg * u.deg)).arcmin)

    ra_s, dec_s = coordenadas.a_montura(ra_h, dec_deg)
    try:
        montura.ejecutar(lambda tel: tel.SyncToCoordinates(ra_s, dec_s), timeout=20)
    except ErrorHardware as e:
        return {"ok": False, "mensaje": f"Error al sincronizar: {e}"}
    ra_str, dec_str = formatear_ra_dec(ra_h, dec_deg)
    voz.decir(f"Telescopio sincronizado. Se equivocaba {error:.0f} minutos de arco")
    log.info("Sincronizado con la foto de prueba: RA %s DEC %s (error previo %.1f')", ra_str, dec_str, error)
    return {"ok": True, "mensaje": f"Sincronizado con la foto: RA {ra_str}, DEC {dec_str}. La montura se equivocaba {error:.1f}′.",
            "ra": ra_h, "dec": dec_deg, "error_arcmin": round(error, 2)}


@app.route('/sincronizar-foto', methods=['POST'])
def sincronizar_foto():
    resultado = sincronizar_con_ultima_foto()
    return jsonify(resultado), (200 if resultado["ok"] else 409)


def _wcs_ultima_foto():
    """
    WCS de la última foto de prueba. Reutiliza la solución de ASTAP si ya se
    resolvió esta misma foto (p. ej. al sincronizar); si no, la resuelve.
    Devuelve (wcs, ancho, alto) o un texto con el motivo del fallo.
    """
    import anotaciones
    with fits.open(RUTA_IMAGEN) as hdul:
        alto, ancho = hdul[0].data.shape[-2:]
    res = None
    if os.path.exists(RUTA_INI) and os.path.getmtime(RUTA_INI) >= os.path.getmtime(RUTA_IMAGEN):
        res = anotaciones.wcs_desde_ini(RUTA_INI)
    if res is None:
        ruta_mono = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.fits"
        generar_fits_monocromo_para_platesolve(RUTA_IMAGEN, ruta_mono)
        if not ejecutar_astap(ruta_mono):
            return "ASTAP no respondió a tiempo."
        res = anotaciones.wcs_desde_ini(RUTA_INI)
    if res is None:
        return "ASTAP no ha podido resolver la foto (¿pocas estrellas, desenfoque, nubes?)."
    return res[0], ancho, alto


def apuntar_a_punto_de_la_foto(fx, fy):
    """
    Centra el telescopio en el punto (fx, fy) de la última foto de prueba, dado
    como fracción del ancho y del alto (0,5, 0,5 es el centro). Resuelve la
    foto con ASTAP, convierte el píxel a RA/DEC, sincroniza la montura con el
    centro real de la foto y mueve al punto elegido. Devuelve un dict con ok y
    mensaje (y, si fue bien, las coordenadas y la distancia recorrida).
    """
    if not os.path.exists(RUTA_IMAGEN):
        return {"ok": False, "mensaje": "No hay ninguna foto; haz una foto primero."}
    if bucle.activo and not bucle.pausado:
        return {"ok": False, "mensaje": "El apilado está capturando; páusalo antes de mover el telescopio."}
    if centrado.trabajo_centrado.en_curso:
        return {"ok": False, "mensaje": "Hay un centrado automático en marcha; espera a que termine."}
    res = _wcs_ultima_foto()
    if isinstance(res, str):
        return {"ok": False, "mensaje": res}
    wcs, ancho, alto = res
    # Fracción de la imagen -> píxel (origen 0 en la esquina superior izquierda,
    # como el JPG de la web, que es la misma imagen reducida sin recortar)
    px, py = fx * ancho - 0.5, fy * alto - 0.5
    cx, cy = (ancho - 1) / 2.0, (alto - 1) / 2.0
    try:
        ra_obj, dec_obj = (float(v) for v in wcs.all_pix2world([[px, py]], 0)[0])
        ra_cen, dec_cen = (float(v) for v in wcs.all_pix2world([[cx, cy]], 0)[0])
    except Exception as e:
        return {"ok": False, "mensaje": f"No se pudo convertir el punto a coordenadas: {e}"}
    if not all(np.isfinite([ra_obj, dec_obj, ra_cen, dec_cen])):
        return {"ok": False, "mensaje": "El punto elegido cae fuera de la proyección de la foto."}
    ra_obj_h, ra_cen_h = ra_obj / 15.0, ra_cen / 15.0
    if calcular_altitud(ra_obj_h, dec_obj) < float(ALTURA_MINIMA):
        return {"ok": False, "mensaje": "El punto elegido está por debajo de la altura mínima; no se mueve el telescopio."}
    salto = float(SkyCoord(ra=ra_cen * u.deg, dec=dec_cen * u.deg).separation(
        SkyCoord(ra=ra_obj * u.deg, dec=dec_obj * u.deg)).arcmin)

    # Como en la primera vuelta del centrado automático: Sync con el centro real
    # de la foto y Slew al punto elegido; así el movimiento es exacto aunque la
    # montura estuviera desalineada.
    ra_sync, dec_sync = coordenadas.a_montura(ra_cen_h, dec_cen)
    ra_dest, dec_dest = coordenadas.a_montura(ra_obj_h, dec_obj)

    def _mover(tel):
        tel.SyncToCoordinates(ra_sync, dec_sync)
        tel.Tracking = True
        if getattr(tel, "CanSlewAsync", False):
            tel.SlewToCoordinatesAsync(ra_dest, dec_dest)
        else:
            tel.SlewToCoordinates(ra_dest, dec_dest)
        inicio = time.time()
        time.sleep(0.5)
        while tel.Slewing:
            if time.time() - inicio > 120:
                raise ErrorHardware("La montura no terminó el movimiento en 120 s")
            time.sleep(0.5)

    try:
        montura.ejecutar(_mover, timeout=150)
    except ErrorHardware as e:
        return {"ok": False, "mensaje": f"Error al mover el telescopio: {e}"}
    time.sleep(asentamiento_montura())   # asentamiento antes de la siguiente foto
    ra_str, dec_str = formatear_ra_dec(ra_obj_h, dec_obj)
    log.info("Apuntado al punto (%.3f, %.3f) de la foto de prueba: RA %s DEC %s (salto %.1f')", fx, fy, ra_str, dec_str, salto)
    voz.decir("Telescopio centrado en el punto elegido")
    return {"ok": True, "mensaje": f"Telescopio centrado en RA {ra_str}, DEC {dec_str} (se ha movido {salto:.1f}′).",
            "ra": ra_obj_h, "dec": dec_obj, "salto_arcmin": round(salto, 2)}


@app.route('/apuntar-foto', methods=['POST'])
def apuntar_foto():
    """Centra el telescopio en el punto de la última foto de prueba que marcó el usuario ({x, y} en 0–1)."""
    datos = request.get_json(silent=True) or {}
    try:
        fx, fy = float(datos.get("x")), float(datos.get("y"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Punto no válido."}), 400
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return jsonify({"ok": False, "mensaje": "El punto tiene que estar dentro de la foto."}), 400
    resultado = apuntar_a_punto_de_la_foto(fx, fy)
    return jsonify(resultado), (200 if resultado["ok"] else 409)

# Ruta para resolver plate solve con ASTAP
@app.route('/platesolve')
def platesolve():
    if not os.path.exists(RUTA_IMAGEN):
        mensaje_error = f"⚠️ Error: No se encontró la imagen <b>{RUTA_IMAGEN}</b>."
        return render_template('platesolve.html', resultado=mensaje_error, mostrar_volver=True)


    try:
        RUTA_IMAGEN_MONO = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.fits"
        generar_fits_monocromo_para_platesolve(RUTA_IMAGEN, RUTA_IMAGEN_MONO)

        if not ejecutar_astap(RUTA_IMAGEN_MONO):
            return render_template('platesolve.html', resultado="⏱️ ASTAP no respondió a tiempo.", mostrar_volver=True)

        coordenadas = extraer_coordenadas_ini(RUTA_INI)
        if coordenadas is None:
            print("⚠️ ASTAP falló. Intentando resolver con Astrometry.net...")
            #coordenadas = platesolve_astrometry(RUTA_IMAGEN_MONO)
            if coordenadas is None:
                return render_template('platesolve.html', resultado="❌ ASTAP y Astrometry.net no pudieron resolver la imagen.", mostrar_volver=True)

        ra_resuelto, dec_resuelto = coordenadas

        # Convertir RA a horas, minutos y segundos
        horas = int(ra_resuelto / 15)
        minutos = int((ra_resuelto / 15 - horas) * 60)
        segundos = ((ra_resuelto / 15 - horas) * 60 - minutos) * 60

        # Convertir DEC a grados, minutos y segundos
        grados = int(dec_resuelto)
        minutos_dec = int(abs(dec_resuelto - grados) * 60)
        segundos_dec = (abs(dec_resuelto - grados) * 60 - minutos_dec) * 60

        resultado_html = f"""✅ Plate Solve completado. Coordenadas resueltas: <br>
        RA: {horas}h {minutos}m {segundos:.2f}s <br>
        DEC: {grados}° {minutos_dec}' {segundos_dec:.2f}\""""

        return render_template('platesolve.html', resultado=resultado_html, mostrar_volver=True)

    except Exception as e:
        return str(e)

def platesolve_astrometry(ruta_imagen):
    try:
        from client import Client, RequestError  # Asegúrate de que client.py está en el mismo directorio

        api_key = secretos.obtener("astrometry_api_key")
        if not api_key:
            raise Exception("API Key no configurada en config.ini")

        client = Client()
        client.login(api_key)

        print(f"📤 Subiendo imagen a Astrometry.net: {ruta_imagen}")
        respuesta = client.upload(ruta_imagen)

        subid = respuesta.get("subid")
        if not subid:
            print("❌ No se obtuvo subid en la respuesta")
            return None

        # Esperar a que se cree el job
        print(f"⌛ Esperando a que se cree job para subid={subid}")
        job_id = None
        for _ in range(60):  # 60 intentos máx (5 min)
            estado = client.sub_status(subid, justdict=True)
            jobs = estado.get("jobs", [])
            for j in jobs:
                if j is not None:
                    job_id = j
                    break
            if job_id:
                print(f"✅ Job creado: {job_id}")
                break
            time.sleep(5)

        if not job_id:
            print("❌ No se creó ningún job")
            return None

        # Esperar resolución del job
        for _ in range(60):
            resultado = client.job_status(job_id, justdict=True)
            if resultado.get("status") == "success":
                calibration = client.send_request(f"jobs/{job_id}/calibration")
                ra = calibration.get("ra")
                dec = calibration.get("dec")
                print(f"✅ Resolución exitosa: RA={ra}, DEC={dec}")
                return float(ra), float(dec)
            elif resultado.get("status") == "failure":
                print("❌ Resolución fallida")
                return None
            time.sleep(5)

        print("❌ Tiempo de espera agotado para resolución")
        return None

    except RequestError as e:
        print(f"❌ Error en Astrometry.net: {e}")
        return None
    except Exception as e:
        print(f"⚠️ Error general en platesolve_astrometry: {e}")
        return None


# Ruta para sincronizar y recentrar el telescopio
@app.route('/corregir-posicion')
def corregir_posicion():
    try:
        ra_inicial, dec_inicial = montura.ejecutar(
            lambda tel: (tel.RightAscension, tel.Declination), timeout=10)
    except ErrorHardware as e:
        return render_template('corregir_posicion.html', resultado=f"❌ Error: {e}", mostrar_volver=True)

    if not os.path.exists(RUTA_IMAGEN):
        return render_template('corregir_posicion.html', resultado="⚠️ No hay ninguna imagen capturada que resolver.", mostrar_volver=True)

    RUTA_IMAGEN_MONO = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.fits"
    generar_fits_monocromo_para_platesolve(RUTA_IMAGEN, RUTA_IMAGEN_MONO)

    if not ejecutar_astap(RUTA_IMAGEN_MONO):
        return render_template('corregir_posicion.html', resultado="⏱️ ASTAP no respondió a tiempo.", mostrar_volver=True)

    coords_res = extraer_coordenadas_ini(RUTA_INI)   # (no llamar 'coordenadas': es el módulo)
    if coords_res is None:
        print("⚠️ ASTAP falló. Intentando con Astrometry.net...")
        coords_res = platesolve_astrometry(RUTA_IMAGEN_MONO)

        if coords_res is None:
            return render_template('corregir_posicion.html', resultado="❌ ASTAP y Astrometry.net no pudieron resolver la imagen.", mostrar_volver=True)


    ra_resuelto, dec_resuelto = coords_res
    ra_resuelto = float(ra_resuelto) / 15.0  # Convertimos RA de grados a horas

    altitud = calcular_altitud(ra_resuelto, dec_resuelto)

    if altitud < int(ALTURA_MINIMA):
        return render_template('corregir_posicion.html', resultado="⚠️ Advertencia: El objeto está por debajo de altura mínima. Proceso cancelado.", mostrar_volver=True)

    def _sincronizar(tel):
        # ra_resuelto/dec_resuelto vienen del plate solving (J2000);
        # ra_inicial/dec_inicial se leyeron de la montura (ya en su sistema)
        ra_s, dec_s = coordenadas.a_montura(ra_resuelto, dec_resuelto)
        tel.SyncToCoordinates(ra_s, dec_s)
        tel.SlewToCoordinates(ra_inicial, dec_inicial)

    try:
        montura.ejecutar(_sincronizar, timeout=120)
    except ErrorHardware as e:
        return render_template('corregir_posicion.html', resultado=f"❌ Error al sincronizar: {e}", mostrar_volver=True)

    resultado_html = "✅ Telescopio sincronizado y recentrado a su posición original."
    return render_template('corregir_posicion.html', resultado=resultado_html, mostrar_volver=True)


# 🚀 Centrado automático en bucle cerrado (ver centrado.py)
@app.route('/modo-automatico')
def modo_automatico():
    """Página que lanza el centrado y muestra el progreso (menú principal)."""
    return render_template('modo_automatico.html', mostrar_volver=True)


@app.route('/centrado/iniciar', methods=["POST"])
def centrado_iniciar():
    """
    Arranca el centrado en segundo plano. Objetivo: el último GOTO; si no hay,
    la posición que la montura dice tener ahora mismo.
    """
    if ULTIMO_GOTO:
        ra, dec, objeto, origen = ULTIMO_GOTO["ra"], ULTIMO_GOTO["dec"], ULTIMO_GOTO.get("objeto", ""), "goto"
    else:
        try:
            ra, dec = coordenadas.desde_montura(
                *montura.ejecutar(lambda tel: (tel.RightAscension, tel.Declination), timeout=10))
        except ErrorHardware as e:
            return jsonify({"ok": False, "error": f"No hay GOTO previo y no se pudo leer la montura: {e}"}), 409
        objeto, origen = "posición actual de la montura", "montura"
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes de centrar"}), 409
    try:
        centrado.trabajo_centrado.iniciar(
            camara, montura, ra, dec, objeto=objeto,
            calcular_altitud=calcular_altitud, altura_minima=float(ALTURA_MINIMA))
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "objeto": objeto, "origen": origen})


# ─────────────────────────────────────────────────────────────────────────────
#  Recorrido por constelación (portado de la versión de junio; ver recorridos.py)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/constelacion')
def vista_constelacion():
    return render_template("constelacion.html", mostrar_volver=True)


@app.route('/recorrido-constelacion', methods=['POST'])
def recorrido_constelacion():
    raw = request.form.get('nombre', '').strip()
    if not raw:
        return jsonify({'error': '❌ Falta el nombre de la constelación.'}), 400
    try:
        descripcion, estrellas = recorridos.recorrido_constelacion(raw, reemplazar_numeros_en_texto)
    except RuntimeError as e:
        return jsonify({'error': f"⚠️ {e}"}), 404
    except Exception as e:
        log.exception("Error preparando el recorrido de %s", raw)
        return jsonify({'error': f"❌ Error al consultar SIMBAD: {e}"}), 500
    return jsonify({'descripcion': descripcion, 'estrellas': estrellas})


def _config_recorrido():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    r = cfg["Recorrido"] if "Recorrido" in cfg else {}
    return {"centrar": str(r.get("centrar", "false")).lower() == "true",
            "espera_centrado_s": int(r.get("espera_centrado_s", 120))}


@app.route("/iniciar-recorrido", methods=["POST"])
def iniciar_recorrido():
    """Mueve el telescopio a un objeto del recorrido y espera a que llegue (opcionalmente centra)."""
    datos = request.get_json(silent=True) or {}
    estrella = datos.get("estrella", {})
    nombre, ra, dec = estrella.get("nombre"), estrella.get("ra"), estrella.get("dec")
    if not nombre or ra is None or dec is None:
        return jsonify({"error": "❌ Datos de estrella incompletos."})
    if bucle.activo and not bucle.pausado:
        return jsonify({"error": "El apilado está capturando; páusalo antes del recorrido."})

    if calcular_altitud(ra=float(ra), dec=float(dec)) < float(ALTURA_MINIMA):
        return jsonify({"skipped": True, "nombre": nombre})

    ULTIMO_GOTO.clear()
    ULTIMO_GOTO.update({"ra": float(ra), "dec": float(dec), "objeto": nombre, "fuente": "recorrido",
                        "hora": datetime.now().isoformat(timespec="seconds")})
    guardar_info_goto(nombre, None, ra, dec, "Recorrido por constelación")
    voz.decir(f"Moviendo el telescopio a {nombre}")

    def _mover(tel):
        tel.Tracking = True
        ra_m, dec_m = coordenadas.a_montura(float(ra), float(dec))
        if getattr(tel, "CanSlewAsync", False):
            tel.SlewToCoordinatesAsync(ra_m, dec_m)
        else:
            tel.SlewToCoordinates(ra_m, dec_m)
        inicio = time.time()
        time.sleep(0.5)
        while tel.Slewing:
            if time.time() - inicio > 180:
                raise ErrorHardware("La montura no terminó el movimiento en 180 s")
            time.sleep(0.5)

    try:
        montura.ejecutar(_mover, timeout=200)
    except ErrorHardware as e:
        return jsonify({"error": f"❌ Error durante GOTO: {e}"})

    conf = _config_recorrido()
    if conf["centrar"] and not camara.ocupado:
        try:
            centrado.trabajo_centrado.iniciar(camara, montura, float(ra), float(dec), objeto=nombre,
                                              calcular_altitud=calcular_altitud, altura_minima=float(ALTURA_MINIMA))
            inicio = time.time()
            while centrado.trabajo_centrado.estado()["en_curso"] and time.time() - inicio < conf["espera_centrado_s"]:
                time.sleep(0.5)
        except RuntimeError as e:
            print(f"⚠️ Centrado omitido en el recorrido: {e}")
    return jsonify({"ok": True, "nombre": nombre})


@app.route('/listar-txt-constelaciones')
def listar_txt_constelaciones():
    return jsonify(recorridos.listar_recorridos_txt())


@app.route('/leer-txt-constelacion')
def leer_txt_constelacion():
    resultado = recorridos.leer_recorrido_txt(request.args.get('file', ''))
    if resultado is None:
        return jsonify({"error": "Fichero no encontrado"}), 404
    descripcion, lista = resultado
    return jsonify({"descripcion": descripcion, "lista": lista})


@app.route('/alineacion')
def vista_alineacion():
    return render_template('alineacion.html', mostrar_volver=True, conf=alineacion.leer_config())


@app.route('/alineacion/iniciar', methods=["POST"])
def alineacion_iniciar():
    """Alineación automática de varios puntos por plate solving (ver alineacion.py)."""
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes de alinear"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    datos = request.get_json(silent=True) or {}
    conf = alineacion.leer_config()
    if "puntos" in datos:
        conf["puntos"] = max(2, min(20, int(datos["puntos"])))
    if "limpiar_antes" in datos:
        conf["limpiar_antes"] = bool(datos["limpiar_antes"])
    if "puntos" in datos or "limpiar_antes" in datos:
        _guardar_en_config("Alineacion", {"puntos": conf["puntos"],
                                          "limpiar_antes": "true" if conf["limpiar_antes"] else "false"})
    try:
        alineacion.trabajo_alineacion.iniciar(camara, montura, conf=conf,
                                              limpiar_sync=limpiar_sincronizaciones, voz=voz)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "puntos": conf["puntos"]})


@app.route('/alineacion/estado')
def alineacion_estado():
    return jsonify(alineacion.trabajo_alineacion.estado())


@app.route('/alineacion/cancelar', methods=["POST"])
def alineacion_cancelar():
    alineacion.trabajo_alineacion.cancelar()
    return jsonify({"mensaje": "⏹️ Cancelando alineación..."})


@app.route('/centrado/estado')
def centrado_estado():
    return jsonify(centrado.trabajo_centrado.estado())


@app.route('/centrado/cancelar', methods=["POST"])
def centrado_cancelar():
    centrado.trabajo_centrado.cancelar()
    return jsonify({"mensaje": "⏹️ Cancelando centrado..."})


@app.route('/centrado/imagen')
def centrado_imagen():
    """JPG de la última foto del centrado (centrado.py la genera en cada vuelta)."""
    ruta = ruta_absoluta(centrado.RUTA_PREVIEW_CENTRADO)
    if not os.path.exists(ruta):
        return send_file("static/esperando.png", mimetype="image/png")
    respuesta = send_file(ruta, mimetype="image/jpeg")
    respuesta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return respuesta

# Función para leer coordenadas de ASTAP desde el archivo WCS
def extraer_coordenadas_wcs(ruta_wcs):
    
    if not os.path.exists(ruta_wcs):
        return None

    ra, dec = None, None

    with open(ruta_wcs, "r") as f:
        for linea in f:
            if "RA      =" in linea:
                ra_match = re.search(r'RA\s*=\s*([-\d.]+)', linea)
                if ra_match:
                    ra = float(ra_match.group(1)) / 15  # Convertimos de grados a horas
            elif "DEC     =" in linea:
                dec_match = re.search(r'DEC\s*=\s*([-\d.]+)', linea)
                if dec_match:
                    dec = float(dec_match.group(1))
    # Eliminamos el archivo después de leerlo
    os.remove(ruta_wcs)
    return (ra, dec) if ra is not None and dec is not None else None

def extraer_coordenadas_ini(ruta_ini):
    if not os.path.exists(ruta_ini):
        return None

    ra_resuelto = None
    dec_resuelto = None
    solucionado = False

    with open(ruta_ini, 'r') as file:
        for linea in file:
            if "PLTSOLVD=T" in linea:
                solucionado = True
            elif "CRVAL1=" in linea:
                match = re.search(r"[-+]?\d*\.\d+E[-+]?\d+|\d+\.\d+", linea)
                if match:
                    ra_resuelto = float(match.group())
            elif "CRVAL2=" in linea:
                match = re.search(r"[-+]?\d*\.\d+E[-+]?\d+|\d+\.\d+", linea)
                if match:
                    dec_resuelto = float(match.group())

    if not solucionado or ra_resuelto is None or dec_resuelto is None:
        return None  # No se resolvió o falta información

    rotacion_camara.anotar_ini(ruta_ini, "foto de prueba")   # el giro de la cámara, para el planetario
    return ra_resuelto, dec_resuelto

import re
def normalizar_nombre_objeto(objeto):
    numeros = {
        "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
        "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
        "once": "11", "doce": "12", "trece": "13", "catorce": "14", "quince": "15",
        "dieciséis": "16", "diecisiete": "17", "dieciocho": "18", "diecinueve": "19", "veinte": "20",
        "veintiuno": "21", "veintidós": "22", "veintitrés": "23", "veinticuatro": "24", "veinticinco": "25",
        "veintiséis": "26", "veintisiete": "27", "veintiocho": "28", "veintinueve": "29", "treinta": "30",
        "treinta y uno": "31", "treinta y dos": "32", "treinta y tres": "33", "treinta y cuatro": "34", "treinta y cinco": "35",
        "treinta y seis": "36", "treinta y siete": "37", "treinta y ocho": "38", "treinta y nueve": "39",
        "cuarenta": "40", "cuarenta y uno": "41", "cuarenta y dos": "42", "cuarenta y tres": "43", "cuarenta y cuatro": "44",
        "cuarenta y cinco": "45", "cuarenta y seis": "46", "cuarenta y siete": "47", "cuarenta y ocho": "48", "cuarenta y nueve": "49",
        "cincuenta": "50", "cincuenta y uno": "51", "cincuenta y dos": "52", "cincuenta y tres": "53", "cincuenta y cuatro": "54",
        "cincuenta y cinco": "55", "cincuenta y seis": "56", "cincuenta y siete": "57", "cincuenta y ocho": "58", "cincuenta y nueve": "59",
        "sesenta": "60", "setenta": "70", "ochenta": "80", "ochenta y uno": "81", "ochenta y dos": "82", "ochenta y tres": "83", "ochenta y cuatro": "84",
        "ochenta y cinco": "85", "ochenta y seis": "86", "ochenta y siete": "87", "ochenta y ocho": "88", "ochenta y nueve": "89",
        "noventa": "90", "noventa y uno": "91", "noventa y dos": "92", "noventa y tres": "93", "noventa y cuatro": "94",
        "noventa y cinco": "95", "noventa y seis": "96", "noventa y siete": "97", "noventa y ocho": "98", "noventa y nueve": "99",
        "cien": "100", "ciento": "100", "doscientos": "200", "trescientos": "300",
        "cuatrocientos": "400", "quinientos": "500", "seiscientos": "600", "setecientos": "700", "ochocientos": "800", "novecientos": "900"
    }
    objeto = objeto.lower()
    
    # Reemplazar nombres de números por sus equivalentes numéricos
    for palabra, numero in sorted(numeros.items(), key=lambda x: -len(x[0])):
        objeto = re.sub(rf'\b{palabra}\b', numero, objeto, flags=re.IGNORECASE)
    
    # Manejo correcto de "mil" en diferentes contextos
    objeto = re.sub(r'\bmil\b', "1000", objeto, flags=re.IGNORECASE)
    
    # Manejo adecuado de combinaciones como "tres mil veinte"
    objeto = re.sub(r'\b(\d+)\s*mil\s*(\d+)\b', lambda m: str(int(m.group(1)) * 1000 + int(m.group(2).zfill(3))), objeto)
    objeto = re.sub(r'\b(\d+)\s*mil\b', lambda m: str(int(m.group(1)) * 1000), objeto)
    
    # Corrección para evitar números incorrectos como "3100020"
    objeto = re.sub(r'\b(\d{1,2})000(\d{1,2})\b', r'\1\2', objeto)
    
    # Eliminar la " y " en números como "noventa y seis" después de la conversión
    objeto = re.sub(r'\b(\d+)\sy\s(\d+)\b', r'\1\2', objeto)
    
    objeto = re.sub(r'[^a-zA-Z0-9]', '', objeto)  # Elimina guiones u otros caracteres no alfanuméricos
    objeto = objeto.upper()
    
    return objeto

# Página para GOTO con formulario HTML y reconocimiento de voz
@app.route("/goto", methods=["GET", "POST"])
def goto_objeto():
    if request.method == "POST":
        objeto = request.form.get("objeto", "").strip()
        if not objeto:
            return jsonify({"error": "Debe proporcionar un nombre de objeto."})

        objeto_original = objeto          # tal como lo escribió el usuario, para el rótulo
        objeto = reemplazar_numeros_en_texto(objeto)
        nombre_objeto = objeto.lower()

        try:
            coords = None
            fuente_coords = ""
            altitud = None
            info = {}
            # Coordenadas directas (página Esta noche): no hace falta resolver el nombre
            ra_directa, dec_directa = request.form.get("ra"), request.form.get("dec")
            if ra_directa and dec_directa:
                try:
                    ra, dec = float(ra_directa), float(dec_directa)
                    coords = (ra, dec)
                    fuente_coords = "Catálogo de ScopePower"
                except ValueError:
                    return jsonify({"error": "Coordenadas no válidas."})
            # Orden de las fuentes: planetas y Luna (efemérides), catálogo local
            # (OpenNGC + estrellas HYG, sin Internet), cometas y asteroides del MPC,
            # Stellarium si está abierto, y por último Internet (JPL Horizons, SIMBAD).
            if coords is None and nombre_objeto not in CUERPOS_SISTEMA_SOLAR:
                ficha = catalogo_offline.buscar(objeto)
                if ficha:
                    ra, dec = ficha["ra"], ficha["dec"]
                    coords = (ra, dec)
                    info = ficha
                    fuente_coords = ficha["fuente"]

            if coords is None and cuerpos_menores.buscar(objeto) is None and estado_stellarium():
                info = obtener_coords_desde_stellarium(objeto)
                if info and "ra" in info and "dec" in info:
                    ra = info["ra"]
                    dec = info["dec"]
                    alt = info.get("alt", None)
                    altitud = alt
                    fuente_coords = "Stellarium"
                    coords = (ra, dec)
                else:
                    info = None  # ← Así forzamos fallback a métodos alternativos

            # Si el catálogo local y Stellarium no lo tienen
            if coords is None:
                if nombre_objeto in {
                    "sol", "luna", "mercurio", "venus", "marte",
                    "jupiter", "saturno", "urano", "neptuno", "pluton"
                }:
                    nombre_ingles = {
                        "sol": "sun", "luna": "moon", "mercurio": "mercury", "venus": "venus",
                        "marte": "mars", "jupiter": "jupiter", "saturno": "saturn",
                        "urano": "uranus", "neptuno": "neptune", "pluton": "pluto"
                    }[nombre_objeto]
                    ubicacion = EarthLocation(
                        lat=LATITUD_OBSERVATORIO * u.deg,
                        lon=LONGITUD_OBSERVATORIO * u.deg,
                        height=ALTITUD_OBSERVATORIO * u.m
                    )
                    ahora = Time.now()
                    from astropy.coordinates import ICRS
                    coords = get_body(nombre_ingles, ahora, ubicacion)

                    ra = coords.ra.hour
                    dec = coords.dec.degree
                    #coords = coords_aparentes.transform_to(ICRS())  # convertir a J2000

                    fuente_coords = "Efemérides planetarias (Astropy)"
                
                elif cuerpos_menores.buscar(objeto) is not None:
                    # Cometa o asteroide de las listas del MPC guardadas: se calcula aquí, sin Internet
                    ra, dec, info_cm = cuerpos_menores.coordenadas(objeto, LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO)
                    coords = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    fuente_coords = f"Elementos orbitales del MPC ({info_cm['tipo']}, sin Internet)"
                    info = {"name": info_cm["nombre"], "type": info_cm["tipo"], "mag": info_cm["mag"],
                            "distance": info_cm["distancia_ua"], "alt": info_cm["alt"]}

                elif '/' in objeto or objeto.lower().startswith(('c/', 'p/')):
                    ra, dec = obtener_coords_cometa(objeto)
                    if ra is None or dec is None:
                        return jsonify({"error": f"No se pudo obtener coordenadas para el cometa '{objeto}' desde JPL Horizons."})
                    coords = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    fuente_coords = "JPL Horizons (cometa)"

                else:
                    try:
                        coords = SkyCoord.from_name(objeto)
                        fuente_coords = "Resolución por Internet (Astropy)"
                    except Exception:
                        coords = None

                    if coords is None:
                        return jsonify({"error": f"No se pudo obtener coordenadas para el objeto '{objeto}'. Verifica el nombre o intenta con otro método."})

                    ra = coords.ra.hour
                    dec = coords.dec.degree

            ra_str, dec_str = formatear_ra_dec(ra, dec)
            if altitud is None:
                altitud = calcular_altitud(ra=ra, dec=dec)


            if altitud < int(ALTURA_MINIMA):
                return jsonify({"warning": "El objeto está por debajo de altura mínima. GOTO cancelado."})

            # Nombre para el rótulo del apilado, los mensajes y la voz: el pedido con sus
            # mayúsculas y símbolos (M31, Vega, Campo 9h56m +69°22'), salvo que llevara
            # números en letras; el rótulo prefiere el de la ficha si la hay
            nombre_rotulo = objeto_original if quitar_tildes(objeto_original.lower()) == objeto else objeto
            ULTIMO_GOTO.clear()
            ULTIMO_GOTO.update({"ra": float(ra), "dec": float(dec), "objeto": nombre_rotulo,
                                "fuente": fuente_coords, "hora": datetime.now().isoformat(timespec="seconds")})
            guardar_info_goto((info or {}).get("name") or nombre_rotulo, info, ra, dec, fuente_coords)

            def _activar_tracking(tel):
                if not tel.Tracking:
                    tel.Tracking = True
                    time.sleep(1)

            try:
                montura.ejecutar(_activar_tracking, timeout=15)
            except ErrorHardware as e:
                return jsonify({"error": f"No se pudo activar el seguimiento: {e}"})

            def _mover_telescopio(tel, ra=ra, dec=dec):
                tel.Tracking = True
                # Las coordenadas resueltas son J2000; la montura puede pedir JNow
                ra_m, dec_m = coordenadas.a_montura(ra, dec)
                if getattr(tel, "CanSlewAsync", False):
                    tel.SlewToCoordinatesAsync(ra_m, dec_m)
                else:
                    tel.SlewToCoordinates(ra_m, dec_m)
                # Esperar hasta que comience a moverse (máx. 5 s)
                for _ in range(10):
                    if tel.Slewing:
                        break
                    time.sleep(0.5)

            # Lanzar movimiento en el hilo de la montura sin esperar a que termine
            voz.decir(f"Moviendo el telescopio a {nombre_rotulo}")
            futuro = montura.ejecutar_async(_mover_telescopio)
            futuro.add_done_callback(
                lambda f: print(f"❌ Error al mover el telescopio: {f.exception()}") if f.exception() else None)

            # Al terminar el movimiento (sondeo aparte, sin ocupar el hilo de la
            # montura): avisar por voz y, como un Seestar, centrar automáticamente
            # con plate solving si [Centrado] centrar_tras_goto = true.
            def _al_llegar(nombre=nombre_rotulo, ra_obj=float(ra), dec_obj=float(dec)):
                inicio = time.time()
                time.sleep(3)
                while time.time() - inicio < 180:
                    try:
                        if not montura.ejecutar(lambda tel: bool(tel.Slewing), timeout=5):
                            break
                    except ErrorHardware:
                        return
                    time.sleep(1)
                else:
                    return
                voz.decir(f"Telescopio apuntando a {nombre}")
                if not centrado.leer_config().get("centrar_tras_goto", True):
                    return
                if (bucle.activo and not bucle.pausado) or camara.ocupado or centrado.trabajo_centrado.en_curso:
                    print("ℹ️ Centrado tras GOTO omitido: cámara ocupada o apilado en marcha")
                    return
                time.sleep(asentamiento_montura())   # asentamiento de la montura
                try:
                    centrado.trabajo_centrado.iniciar(
                        camara, montura, ra_obj, dec_obj, objeto=nombre,
                        calcular_altitud=calcular_altitud, altura_minima=float(ALTURA_MINIMA))
                    print(f"🎯 Centrado automático tras el GOTO a {nombre}")
                except RuntimeError as e:
                    print(f"⚠️ No se pudo iniciar el centrado tras el GOTO: {e}")
            threading.Thread(target=_al_llegar, name="llegada-goto", daemon=True).start()

            # Responder inmediatamente
            return jsonify({
                "message": f"✅ Moviendo el telescopio a <b>{nombre_rotulo}</b><br>📍 RA: <code>{ra_str}</code> | DEC: <code>{dec_str}</code><br><small>🔎 Fuente: <b>{fuente_coords}</b></small>",
                "extra_info": info
            })


        except Exception as e:
            return jsonify({"error": f"Error durante GOTO: {e}"})

    return render_template("goto.html", mostrar_volver=True, palabra_clave=PALABRA_CLAVE_GOTO)

@app.route("/estado_telescopio")
def estado_telescopio():
    if not montura.esta_conectado():
        return jsonify({"slewing": False, "conectado": False})

    def _slewing(tel):
        # SimulaSlewing (config [General]): para drivers cuya propiedad Slewing no
        # es fiable, se detecta el movimiento por el cambio de RA/DEC en 1,5 s
        if config["General"].getboolean("SimulaSlewing", fallback=False):
            ra1, dec1 = tel.RightAscension, tel.Declination
            time.sleep(1.5)
            ra2, dec2 = tel.RightAscension, tel.Declination
            return abs(ra2 - ra1) > 0.0011 or abs(dec2 - dec1) > 0.011
        return bool(tel.Slewing)

    try:
        slewing = montura.ejecutar(_slewing, timeout=8)
        return jsonify({"slewing": slewing, "conectado": True, "sistema": coordenadas.sistema()})
    except ErrorHardware as e:
        return jsonify({"slewing": False, "conectado": False, "error": str(e)})

@app.route("/info-wikipedia", methods=["POST"])
def info_wikipedia():
    """
    Descripción del objeto para leer en el móvil (descripciones.py): catálogo propio con
    texto escrito para voz; si no está, caché de Wikipedia en info_objetos/; si no, Wikipedia.
    """
    raw_objeto = request.form.get("objeto", "").strip()
    if not raw_objeto:
        return jsonify({"error": "Debe proporcionar un nombre de objeto."})
    import descripciones
    d = descripciones.describir(raw_objeto, reemplazar_numeros_en_texto)
    return jsonify({"wikipedia_info": d["texto"], "texto_hablar": d["texto_hablar"], "fuente": d["fuente"], "objeto": d["objeto"]})


# Generar el diccionario de números en palabras hasta 9999
def generar_numeros_en_palabras():
    unidades = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
    decenas = ["", "diez", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
    especiales = ["once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve"]

    numeros_texto = {}

    for i in range(10000):
        texto = ""
        mil = i // 1000
        cen = (i % 1000) // 100
        dec = (i % 100) // 10
        uni = i % 10

        if mil > 0:
            texto += "mil" if mil == 1 else unidades[mil] + " mil"

        if cen > 0:
            if texto:
                texto += " "
            if cen == 1 and dec == 0 and uni == 0:
                texto += "cien"
            elif cen == 1:
                texto += "ciento"
            elif cen == 5:
                texto += "quinientos"
            elif cen == 7:
                texto += "setecientos"
            elif cen == 9:
                texto += "novecientos"
            else:
                texto += unidades[cen] + "cientos"

        if dec == 1 and uni > 0:
            if texto:
                texto += " "
            texto += especiales[uni - 1]
        else:
            if dec > 0:
                if texto:
                    texto += " "
                if dec == 2 and uni > 0:
                    texto += "veinti" + unidades[uni]
                else:
                    texto += decenas[dec]
                    if uni > 0:
                        texto += " y " + unidades[uni]
            elif uni > 0 or i == 0:
                if texto:
                    texto += " "
                texto += unidades[uni]

        numeros_texto[texto.strip()] = str(i)

    return numeros_texto

# Diccionario de números en palabras
NUMEROS_EN_PALABRAS = generar_numeros_en_palabras()

# Función para quitar tildes
def quitar_tildes(texto):
    return unidecode(texto)

# Función para reemplazar números en palabras por su versión numérica
def reemplazar_numeros_en_texto(texto):
    texto_sin_tildes = quitar_tildes(texto.lower())
    
    for palabra, numero in sorted(NUMEROS_EN_PALABRAS.items(), key=lambda x: -len(x[0])):
        palabra_sin_tildes = quitar_tildes(palabra)
        texto_sin_tildes = re.sub(r'\b' + re.escape(palabra_sin_tildes) + r'\b', numero, texto_sin_tildes, flags=re.IGNORECASE)

    return texto_sin_tildes

@app.route("/estado_stellarium")
def verificar_estado_stellarium():
    """Fuentes de coordenadas del GoTo: catálogo local (siempre) y Stellarium (si está abierto)."""
    disponible = estado_stellarium()
    return jsonify({"disponible": disponible, "catalogo": catalogo_offline.resumen()})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        clave = request.form.get('clave')

        if usuario in USUARIOS_HASH:
            hash_guardado = USUARIOS_HASH[usuario].encode()
            if bcrypt.checkpw(clave.encode(), hash_guardado):
                session['usuario'] = usuario
                eliminar_info_goto()
                iniciar_conexion()  # ← aquí arrancamos Stellarium y telescopio tras login
                # 🔐 Verificar activación aquí
                if not validar_licencia():
                    return redirect(url_for("activar"))

                return redirect(url_for('home'))

        return render_template('login.html', error="Credenciales incorrectas")

    return render_template('login.html')

from apilado import reiniciar_apilado_global

@app.route('/resultado-apilado')
def mostrar_apilado():
    ruta = "static/resultado_apilado_preview.jpg"
    if not os.path.exists(ruta):
        print("⚠️ No se encontró resultado_apilado_preview.jpg")
        return send_file("static/esperando.png", mimetype="image/png")

    try:
        response = send_file(ruta, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as e:
        print(f"❌ Error al enviar imagen apilada: {e}")
        return send_file("static/esperando.png", mimetype="image/png")


@app.route('/ultima-captura')
def mostrar_ultima_captura():
    """Miniatura de la última toma individual (se regenera en cada captura)."""
    if not os.path.exists(RUTA_ULTIMA_CAPTURA):
        return send_file("static/esperando.png", mimetype="image/png")
    response = send_file(RUTA_ULTIMA_CAPTURA, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


from apilado import obtener_estado_apilado

@app.route('/estado-apilado')
def estado_apilado():
    estado = obtener_estado_apilado()
    estado["captura"] = estado_captura.como_dict()
    estado["camara_ocupada"] = camara.ocupado
    estado["bucle"] = bucle.estado()
    estado["ultima_toma"] = dict(ULTIMA_CAPTURA)
    return jsonify(estado)


@app.route('/estado-captura')
def estado_captura_actual():
    """Progreso de la exposición en curso (para la cuenta atrás de la web)."""
    estado = estado_captura.como_dict()
    estado["camara_ocupada"] = camara.ocupado
    return jsonify(estado)


# ───────────────────────── Foto de día (exposición automática) ─────────────────────────
def _campo_foto_dia(datos):
    """Fracción del campo visible pedida por la web (100 % = todo el sensor)."""
    try:
        return max(0.2, min(1.0, float(datos.get("campo", 100)) / 100.0))
    except (TypeError, ValueError):
        return 1.0


def _compensacion_foto_dia():
    """EV de compensación de la exposición automática de día (slider de la web), releído en cada foto."""
    try:
        ev = float(_leer_preferencias().get("foto_dia_compensacion", 0.0))
    except (TypeError, ValueError):
        ev = 0.0
    return max(-foto_dia.COMPENSACION_MAX_EV, min(foto_dia.COMPENSACION_MAX_EV, ev))


def _config_foto_dia():
    """Última exposición que funcionó para cada objetivo ([FotoDia] en config.ini)."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    f = cfg["FotoDia"] if "FotoDia" in cfg else {}
    salida = {}
    for objetivo in foto_dia.OBJETIVOS:
        try:
            salida[objetivo] = float(f.get(f"exposicion_{objetivo}", 0)) or None
        except ValueError:
            salida[objetivo] = None
    return salida


@app.route('/foto-dia/iniciar', methods=["POST"])
def foto_dia_iniciar():
    """Foto de prueba en modo día: busca sola la exposición (Sol con filtro, Luna o paisaje)."""
    datos = request.get_json(silent=True) or {}
    objetivo = str(datos.get("objetivo", "paisaje"))
    if objetivo not in foto_dia.OBJETIVOS:
        return jsonify({"ok": False, "error": "Objetivo desconocido"}), 400
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("flats", flats.trabajo_flats), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    ultimas = _config_foto_dia()

    def _al_terminar(estado):
        # Solo se recuerda una exposición que llegó cerca del nivel buscado (ni saturada ni a oscuras)
        objetivo_nivel = foto_dia.OBJETIVOS[objetivo]["nivel"]
        if not estado["error"] and estado["exposicion"] and not estado["saturada"]                 and estado["nivel"] is not None and estado["nivel"] >= 0.5 * objetivo_nivel:
            _guardar_en_config("FotoDia", {f"exposicion_{objetivo}": f"{estado['exposicion']:.6f}"})
    try:
        foto_dia.trabajo_foto_dia.iniciar(camara, objetivo, _dispositivos()["binning"],
                                          exposicion_inicial=ultimas.get(objetivo), montura=montura,
                                          ruta_fits=RUTA_IMAGEN, ruta_web=RUTA_IMAGEN_WEB,
                                          al_terminar=_al_terminar, voz=voz, campo=_campo_foto_dia(datos),
                                          compensacion=_compensacion_foto_dia)
    except (RuntimeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    voz.decir(f"Foto de día de {foto_dia.OBJETIVOS[objetivo]['nombre']}", categoria="capturas")
    return jsonify({"ok": True, "objetivo": objetivo, "exposicion_inicial": ultimas.get(objetivo)})


@app.route('/foto-dia/estado')
def foto_dia_estado():
    e = foto_dia.trabajo_foto_dia.estado()
    e["ultimas"] = {k: {"s": v, "texto": foto_dia.formatear_exposicion(v)} for k, v in _config_foto_dia().items() if v}
    return jsonify(e)


@app.route('/timelapse/iniciar', methods=["POST"])
def timelapse_iniciar():
    """Serie de fotos de día: {"objetivo", "fotos", "intervalo"}."""
    datos = request.get_json(silent=True) or {}
    objetivo = str(datos.get("objetivo", "paisaje"))
    if objetivo not in foto_dia.OBJETIVOS:
        return jsonify({"ok": False, "error": "Objetivo desconocido"}), 400
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("flats", flats.trabajo_flats),
                            ("centrado", centrado.trabajo_centrado), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("foto continua", foto_continua.trabajo_continuo)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    ultimas = _config_foto_dia()

    def _al_terminar(estado):
        objetivo_nivel = foto_dia.OBJETIVOS[objetivo]["nivel"]
        if estado["exposicion"] and estado["nivel"] is not None and estado["nivel"] >= 0.5 * objetivo_nivel:
            _guardar_en_config("FotoDia", {f"exposicion_{objetivo}": f"{estado['exposicion']:.6f}"})
    try:
        foto_dia.trabajo_timelapse.iniciar(camara, objetivo, _dispositivos()["binning"],
                                           total=datos.get("fotos", 100), intervalo=datos.get("intervalo", 10),
                                           exposicion_inicial=ultimas.get(objetivo), montura=montura,
                                           al_terminar=_al_terminar, voz=voz,
                                           raiz=ruta_absoluta(foto_dia.CARPETA_TIMELAPSE), campo=_campo_foto_dia(datos),
                                           compensacion=_compensacion_foto_dia)
    except (RuntimeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "carpeta": os.path.basename(foto_dia.trabajo_timelapse.carpeta)})


@app.route('/timelapse/estado')
def timelapse_estado():
    e = foto_dia.trabajo_timelapse.estado()
    e["carpeta"] = os.path.basename(e["carpeta"]) if e["carpeta"] else ""
    if e.get("video") and e["video"].get("carpeta"):
        e["video"]["carpeta"] = os.path.basename(e["video"]["carpeta"])
        e["video"]["ruta"] = os.path.basename(e["video"]["ruta"]) if e["video"].get("ruta") else ""
    e["anteriores"] = foto_dia.listar_timelapses(ruta_absoluta(foto_dia.CARPETA_TIMELAPSE))
    return jsonify(e)


@app.route('/timelapse/cancelar', methods=["POST"])
def timelapse_cancelar():
    foto_dia.trabajo_timelapse.cancelar()
    return jsonify({"mensaje": "⏹️ Parando el timelapse..."})


@app.route('/timelapse/video', methods=["POST"])
def timelapse_video():
    """Monta el MP4 de una carpeta de timelapse: {"carpeta", "fps"}."""
    datos = request.get_json(silent=True) or {}
    nombre = os.path.basename(str(datos.get("carpeta", "")))
    carpeta = os.path.join(ruta_absoluta(foto_dia.CARPETA_TIMELAPSE), nombre)
    if not nombre or not os.path.isdir(carpeta):
        return jsonify({"ok": False, "error": "Carpeta de timelapse desconocida"}), 404
    fps = max(1, min(60, int(datos.get("fps", 25))))
    try:
        foto_dia.trabajo_timelapse.montar_video(carpeta, fps=fps)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True})


# ───────────────────────── Modo continuo de la Foto de prueba ─────────────────────────
def _capturador_dia_continuo(objetivo, campo, binning):
    """
    Captura de día para el modo continuo: ganancia al mínimo y exposición automática
    (como la foto de día suelta), pero la cámara se prepara una sola vez y cada foto
    parte de la exposición de la anterior con como mucho tres correcciones, para no
    perder ritmo. Devuelve (capturar, al_terminar) para TrabajoContinuo.
    """
    conf = foto_dia.OBJETIVOS[objetivo]
    memoria = {"prep": None, "exposicion": _config_foto_dia().get(objetivo) or conf["inicial"], "n": 0}

    def capturar(copia):
        def _sesion(cam):
            if memoria["prep"] is None:
                memoria["prep"] = foto_dia.preparar_camara(cam, conf)
            prep = memoria["prep"]
            max_intentos = foto_dia.MAX_INTENTOS if memoria["n"] == 0 else 3
            conf_n = foto_dia.con_compensacion(conf, _compensacion_foto_dia())   # el slider vale desde la siguiente foto
            frame, exposicion, nivel, saturada, _ = foto_dia.capturar_ajustando(
                cam, conf_n, binning, prep, memoria["exposicion"], max_intentos,
                descripcion=f"Continuo {memoria['n'] + 1}", campo=campo)
            return frame, exposicion, nivel, saturada, prep, conf_n["ev"]
        try:
            frame, exposicion, nivel, saturada, prep, ev = camara.ejecutar(_sesion, timeout=180)
        except CapturaCancelada:
            return {"ok": False, "mensaje": "⏹️ Captura cancelada", "cancelada": True}
        except ErrorHardware as e:
            return {"ok": False, "mensaje": f"Error de cámara: {e}"}
        memoria["exposicion"], memoria["n"] = exposicion, memoria["n"] + 1
        try:
            imagen = foto_dia.frame_a_imagen8(foto_dia.recortar_centro(frame, campo), prep["sensor_type"])
            guardar_jpg_web(RUTA_IMAGEN_WEB, imagen)
            archivo = None
            if copia and copia.get("formato") == "fits":
                archivo = os.path.join(copia["carpeta"], copia["nombre"] + ".fits")
                foto_dia.guardar_fits(frame, exposicion, prep, binning, archivo)     # campo completo, como la foto suelta
            elif copia and copia.get("formato") == "jpg":
                os.makedirs(copia["carpeta"], exist_ok=True)
                archivo = os.path.join(copia["carpeta"], copia["nombre"] + ".jpg")
                cv2.imwrite(archivo, imagen, [cv2.IMWRITE_JPEG_QUALITY, 92])
        except Exception as e:
            log.exception("Error al guardar la foto de día del modo continuo")
            return {"ok": False, "mensaje": f"Error al guardar: {e}"}
        return {"ok": True, "mensaje": f"Foto de día: {foto_dia.formatear_exposicion(exposicion)}, nivel {100 * nivel:.0f} %"
                                       + (" (saturada)" if saturada else ""),
                "exposicion": exposicion, "nivel": round(nivel, 3), "ganancia": prep["ganancia"], "dark": False,
                "archivo": archivo, "ev": ev}

    def al_terminar(estado):
        if memoria["prep"] is not None:
            try:
                camara.ejecutar(lambda cam: foto_dia.restaurar_camara(cam, memoria["prep"]), timeout=30)
            except Exception:
                pass
        # La última exposición buena se recuerda para la próxima foto de día (como hace el timelapse)
        u = estado.get("ultima") or {}
        if memoria["n"] and u.get("exposicion") and u.get("nivel") is not None and u["nivel"] >= 0.5 * conf["nivel"]:
            _guardar_en_config("FotoDia", {f"exposicion_{objetivo}": f"{u['exposicion']:.6f}"})

    return capturar, al_terminar


@app.route('/continuo/iniciar', methods=["POST"])
def continuo_iniciar():
    """
    Foto de prueba tras foto de prueba hasta que se pare (ver foto_continua.py).
    JSON: {"guardar": "no" | "jpg" | "fits", "ganancia": entero opcional, "exposicion": segundos opcional,
           "modo": "noche" | "dia", y de día "objetivo" (sol | luna | paisaje) y "campo" (%)}
    """
    datos = request.get_json(silent=True) or {}
    guardar = str(datos.get("guardar", "no")).lower().strip()
    if guardar not in foto_continua.FORMATOS:
        return jsonify({"ok": False, "error": "Formato desconocido (no, jpg o fits)"}), 400
    modo = "dia" if str(datos.get("modo", "noche")) == "dia" else "noche"
    objetivo = str(datos.get("objetivo", "paisaje"))
    if modo == "dia" and objetivo not in foto_dia.OBJETIVOS:
        return jsonify({"ok": False, "error": "Objetivo desconocido"}), 400
    try:
        ganancia = int(datos["ganancia"]) if str(datos.get("ganancia", "")).strip() != "" else None
    except (TypeError, ValueError):
        ganancia = None
    exposicion = _exposicion_pedida(datos.get("exposicion"))
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("flats", flats.trabajo_flats),
                            ("centrado", centrado.trabajo_centrado), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("timelapse", foto_dia.trabajo_timelapse), ("alineación", alineacion.trabajo_alineacion)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    seguimiento = None
    if modo == "dia":
        conf = foto_dia.OBJETIVOS[objetivo]
        # Seguimiento solar/lunar como en la foto de día suelta; la web lo devuelve a sidéreo al salir del modo día
        if conf["seguimiento"] and montura.esta_conectado() and foto_dia.poner_seguimiento(montura, conf["seguimiento"]):
            seguimiento = conf["seguimiento"]
        capturar, al_terminar = _capturador_dia_continuo(objetivo, _campo_foto_dia(datos), _dispositivos()["binning"])
        ganancia = None
    else:
        capturar = lambda copia: capturar_y_guardar(descripcion="Continuo", ganancia=ganancia, exposicion=exposicion, copia=copia)
        al_terminar = None
    try:
        foto_continua.trabajo_continuo.iniciar(
            capturar=capturar, camara=camara, guardar=guardar, ganancia=ganancia,
            raiz=ruta_absoluta(foto_continua.CARPETA_CONTINUO), voz=voz, al_terminar=al_terminar, modo=modo)
    except (RuntimeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "seguimiento": seguimiento, "estado": foto_continua.trabajo_continuo.estado()})


@app.route('/continuo/estado')
def continuo_estado():
    return jsonify(foto_continua.trabajo_continuo.estado())


@app.route('/continuo/parar', methods=["POST"])
def continuo_parar():
    foto_continua.trabajo_continuo.parar()
    return jsonify({"mensaje": "⏹️ Parando el modo continuo..."})


@app.route('/foto-dia/seguimiento', methods=["POST"])
def foto_dia_seguimiento():
    """Velocidad de seguimiento de la montura: sidereo | solar | lunar (al salir del modo día se vuelve a sidéreo)."""
    datos = request.get_json(silent=True) or {}
    velocidad = str(datos.get("velocidad", "sidereo"))
    if velocidad not in foto_dia.VELOCIDADES:
        return jsonify({"ok": False, "error": "Velocidad desconocida"}), 400
    if not montura.esta_conectado():
        return jsonify({"ok": False, "error": "La montura no está conectada"}), 409
    ok = foto_dia.poner_seguimiento(montura, velocidad)
    return jsonify({"ok": ok, "velocidad": velocidad,
                    "mensaje": f"Seguimiento {velocidad}" if ok else "El driver de la montura no admite cambiar la velocidad de seguimiento"})


@app.route('/cancelar-captura', methods=["POST"])
def cancelar_captura():
    """Aborta la exposición en curso; la petición de captura devuelve 'cancelada'."""
    if not estado_captura.como_dict()["en_curso"]:
        return jsonify({"mensaje": "No hay ninguna captura en curso"})
    estado_captura.cancelar.set()
    return jsonify({"mensaje": "⏹️ Cancelando captura..."})

@app.route('/reiniciar-apilado', methods=["POST"])
def reiniciar_apilado():
    bucle.detener("Reiniciado")
    reiniciar_apilado_global()
    return redirect(url_for('vista_apilado'))


@app.route('/apilado/nombre-objeto', methods=["POST"])
def apilado_nombre_objeto():
    """
    Cambia a mano el nombre del objeto que sale en el rótulo de la firma y en el
    nombre del archivo de la galería (por si el del GoTo no gusta). Solo toca la
    línea «Nombre:» de info_objeto_goto.txt; el resto de la ficha se conserva.
    """
    datos = request.get_json(silent=True) or {}
    nombre = " ".join(str(datos.get("nombre", "")).split())[:60]
    if not nombre:
        return jsonify({"ok": False, "mensaje": "El nombre no puede estar vacío."}), 400
    cambiar_nombre_objeto_goto(nombre)
    if ULTIMO_GOTO:
        ULTIMO_GOTO["objeto"] = nombre
    return jsonify({"ok": True, "nombre": nombre})


@app.route('/apilado')
def vista_apilado():
    return render_template("apilado.html", mostrar_volver=True)

@app.route('/generar-dark', methods=["GET"])
def generar_dark():
    return render_template("dark.html", mostrar_volver=True)


def _config_darks():
    """Exposición, binning y número de darks según config.ini (valores en vivo)."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    disp = cfg["Dispositivos"]
    return {
        "exposicion": float(disp.get("exposicion_captura", 10)),
        "binning": int(disp.get("binning_camara", 1)),
        "numero_darks": int(disp.get("numero_darks", 10)),
        "ganancia": ganancia_configurada(),
    }


@app.route('/darks/estado')
def darks_estado():
    """Trabajo de darks en curso + datos del último dark para la exposición actual."""
    conf = _config_darks()
    return jsonify({
        "trabajo": darks.trabajo_darks.estado(),
        "ultimo": darks.info_dark(conf["exposicion"]),
        "flat": flats.info_flat(),
        "captura": estado_captura.como_dict(),
        "camara_ocupada": camara.ocupado,
        "config": conf,
    })


@app.route('/darks/generar', methods=["POST"])
def darks_generar():
    """Arranca la generación de darks en segundo plano. El telescopio debe estar tapado."""
    conf = _config_darks()
    datos = request.get_json(silent=True) or {}
    n = int(datos.get("n", conf["numero_darks"]))
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes de generar darks"}), 409
    if foto_continua.trabajo_continuo.en_curso:
        return jsonify({"ok": False, "error": "El modo continuo de Cámara está en marcha; páralo antes"}), 409
    if n != conf["numero_darks"]:
        _guardar_en_config("Dispositivos", {"numero_darks": n})   # se recuerda para la próxima vez
    try:
        darks.trabajo_darks.iniciar(camara, conf["exposicion"], conf["binning"], n, ganancia=conf["ganancia"])
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "n": n, "exposicion": conf["exposicion"]})


@app.route('/calibracion')
def vista_calibracion():
    return render_template('calibracion.html', mostrar_volver=True)


@app.route('/flats')
def vista_flats():
    return render_template('flats.html', mostrar_volver=True, conf=flats.leer_config())


@app.route('/flats/estado')
def flats_estado():
    return jsonify({"trabajo": flats.trabajo_flats.estado(), "ultimo": flats.info_flat(),
                    "config": flats.leer_config(), "camara_ocupada": camara.ocupado})


@app.route('/flats/iniciar', methods=["POST"])
def flats_iniciar():
    """Arranca la generación del flat maestro (telescopio con la camiseta y la pantalla)."""
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("centrado", centrado.trabajo_centrado),
                            ("alineación", alineacion.trabajo_alineacion), ("foto continua", foto_continua.trabajo_continuo)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    datos = request.get_json(silent=True) or {}
    conf = flats.leer_config()
    if "numero_flats" in datos:
        conf["numero_flats"] = max(1, min(50, int(datos["numero_flats"])))
    con_flat_darks = bool(datos.get("flat_darks", True))
    try:
        flats.trabajo_flats.iniciar(camara, conf=conf, con_flat_darks=con_flat_darks, voz=voz)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True})


@app.route('/flats/continuar', methods=["POST"])
def flats_continuar():
    flats.trabajo_flats.continuar()
    return jsonify({"ok": True})


@app.route('/flats/cancelar', methods=["POST"])
def flats_cancelar():
    flats.trabajo_flats.cancelar()
    return jsonify({"mensaje": "⏹️ Cancelando flats..."})


@app.route('/procesar-dark', methods=["POST"])
def procesar_dark():
    """Compatibilidad con el formulario antiguo: lanza el trabajo y vuelve a dark.html."""
    conf = _config_darks()
    try:
        darks.trabajo_darks.iniciar(camara, conf["exposicion"], conf["binning"], conf["numero_darks"])
    except RuntimeError as e:
        return render_template("dark.html", mensaje=f"❌ {e}", mostrar_volver=True)
    return render_template("dark.html", mostrar_volver=True)


@app.route("/config-zoom")
def obtener_config_zoom():
    config.read("config.ini")
    zoom = int(config["Dispositivos"].get("zoom_preview", 0))
    return jsonify({"zoom": zoom})

@app.route("/guardar-config-zoom", methods=["POST"])
def guardar_config_zoom():
    try:
        data = request.get_json()
        zoom = min(90, max(0, int(float(data["zoom"]))))
        config.read("config.ini")
        config["Dispositivos"]["zoom_preview"] = str(zoom)
        with open("config.ini", "w") as configfile:
            config.write(configfile)
        return "✅ Zoom guardado correctamente"
    except Exception as e:
        return f"❌ Error al guardar zoom: {e}", 500


@app.route("/config-color")
def obtener_config_color():
    config.read("config.ini")
    intensidad = float(config["Dispositivos"].get("color_intensity", 1.0))
    return jsonify({"intensidad": intensidad})

@app.route("/guardar-config-color", methods=["POST"])
def guardar_config_color():
    try:
        data = request.get_json()
        config.read("config.ini")
        config["Dispositivos"]["color_intensity"] = str(data["intensidad"])
        with open("config.ini", "w") as configfile:
            config.write(configfile)
        return "✅ Intensidad guardada correctamente"
    except Exception as e:
        return f"❌ Error al guardar: {e}", 500


@app.route('/descargar-fits-apilado')
def descargar_fits_apilado():
    ruta = "static/resultado_apilado.fits"
    if os.path.exists(ruta):
        return send_file(ruta, as_attachment=True)
    else:
        return "❌ El archivo FITS aún no ha sido generado.", 404

# Guardado a resolución completa en segundo plano (tarda 20-30 s en 9 Mpx)
GUARDADO = {"en_curso": False, "mensaje": "", "archivo": "", "ok": None}


@app.route('/guardar-imagen-jpg', methods=['POST'])
def guardar_imagen_jpg():
    """
    Guarda el apilado en la galería: el FITS se copia al momento (campo completo)
    y el JPG se procesa a resolución completa en segundo plano con el estilo y el
    recorte de zoom actuales.
    Consultar el progreso en /guardar-imagen-estado.
    """
    if GUARDADO["en_curso"]:
        return jsonify({"error": "⏳ Ya hay un guardado en curso"}), 409
    origenFits = "static/resultado_apilado.fits"
    if not os.path.exists(origenFits):
        return jsonify({"error": "❌ Archivo FITS no encontrado."}), 404

    # Nombre a partir del objeto del último GOTO
    nombre_objeto = "Objeto"
    ruta_info = "static/info_objeto_goto.txt"
    if os.path.exists(ruta_info):
        with open(ruta_info, "r", encoding="utf-8") as f:
            for linea in f:
                if linea.lower().startswith("nombre:"):
                    nombre_objeto = linea.split(":", 1)[1].strip()
                    break
    # Solo ASCII: las rutas de la galería (compartir, editar) no admiten tildes ni eñes
    nombre_objeto = re.sub(r'[^A-Za-z0-9\-]', '_', unidecode(nombre_objeto)).strip('_') or "Objeto"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"{nombre_objeto}_{timestamp}.jpg"
    nombre_archivo_fits = f"{nombre_objeto}_{timestamp}.fits"
    carpeta_destino = os.path.join("static", "fotos")
    os.makedirs(carpeta_destino, exist_ok=True)
    destino = os.path.join(carpeta_destino, nombre_archivo)
    destinoFits = os.path.join(carpeta_destino, nombre_archivo_fits)

    import shutil
    shutil.copy(origenFits, destinoFits)
    GUARDADO.update({"en_curso": True, "ok": None, "archivo": nombre_archivo,
                     "mensaje": "⏳ Procesando a resolución completa..."})

    def _procesar_y_guardar():
        import procesado
        try:
            with fits.open(destinoFits) as hdul:
                cubo = hdul[0].data.astype(np.float32)
                n = int(hdul[0].header.get("NSTACK", 1))
                exp_total = float(hdul[0].header.get("EXPTOT", 0) or 0)
                zona = procesado.leer_zona(hdul[0].header)
            # Mismo recorte que la vista previa (zona cubierta por todas las fotos
            # y zoom); el FITS se guarda entero
            cubo, _, _ = procesado.recortar_campo(cubo, procesado.fraccion_campo(), zona)
            rgb8 = procesado.procesar(cubo, n, procesado.leer_config(), escala=1.0)
            imagen_bgr = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
            # Firma (logo, autor, objeto, tiempo, coordenadas). El FITS queda limpio y
            # el JSON hermano guarda los datos para el botón de compartir.
            meta = apilado.metadatos_firma(None, destinoFits, n, round(exp_total / n, 3) if n and exp_total else 0)
            if meta["activa"]:
                imagen_bgr = firma.firmar(imagen_bgr, meta)
            cv2.imwrite(destino, imagen_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
            meta["firmada"] = bool(meta["activa"])
            firma.guardar_meta(destino, meta)
            GUARDADO.update({"ok": True, "mensaje": f"✅ Imagen guardada como {nombre_archivo} (y su FITS)"})
            voz.decir("Imagen guardada en la galería")
        except Exception as e:
            log.exception("Error procesando la imagen para la galería")
            # Que no se pierda: al menos la vista previa
            try:
                shutil.copy("static/resultado_apilado_preview.jpg", destino)
            except Exception:
                pass
            GUARDADO.update({"ok": False, "mensaje": f"❌ Error al procesar; guardada la vista previa: {e}"})
        finally:
            GUARDADO["en_curso"] = False

    threading.Thread(target=_procesar_y_guardar, name="guardar-galeria", daemon=True).start()
    return jsonify({"mensaje": GUARDADO["mensaje"], "archivo": nombre_archivo, "en_curso": True})


@app.route('/guardar-imagen-estado')
def guardar_imagen_estado():
    return jsonify(GUARDADO)


@app.route('/config-procesado')
def config_procesado():
    import procesado
    return jsonify({"ajustes": procesado.leer_config(), "estilos": procesado.ESTILOS})


@app.route('/guardar-config-procesado', methods=['POST'])
def guardar_config_procesado():
    import procesado
    datos = request.get_json(silent=True) or {}
    try:
        procesado.guardar_config(datos)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ajustes": procesado.leer_config()})


# Reprocesado de la vista previa en segundo plano: el procesado completo tarda
# varios segundos en el mini PC y la petición no debe quedarse colgada. La web
# sigue el progreso por /estado-apilado (procesando, fase, eta_segundos) y
# recarga la imagen cuando sube la versión; aquí solo queda el resultado.
REPROCESADO = {"en_curso": False, "mensaje": "", "ok": None}


@app.route('/presets-procesado')
def presets_procesado():
    import procesado
    return jsonify({"presets": procesado.leer_presets()})


@app.route('/presets-procesado', methods=['POST'])
def presets_procesado_guardar():
    """Crea o sustituye un preset: {"nombre": ..., "ajustes": {...}}."""
    import procesado
    datos = request.get_json(silent=True) or {}
    try:
        presets = procesado.guardar_preset(datos.get("nombre", ""), datos.get("ajustes") or {})
    except (ValueError, OSError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"presets": presets})


@app.route('/presets-procesado/borrar', methods=['POST'])
def presets_procesado_borrar():
    import procesado
    datos = request.get_json(silent=True) or {}
    return jsonify({"presets": procesado.borrar_preset(datos.get("nombre", ""))})


@app.route('/apilado/reprocesar', methods=['POST'])
def apilado_reprocesar():
    """Lanza la regeneración de la vista previa con los ajustes actuales, sin capturar nada."""
    a = apilado.apilador_global
    if REPROCESADO["en_curso"] or (a is not None and a.procesando):
        return jsonify({"ok": False, "mensaje": "El apilador está ocupado; espera a que termine", "en_curso": True}), 409
    if a is None or not os.path.exists("static/resultado_apilado.fits"):
        return jsonify({"ok": False, "mensaje": "No hay ningún apilado que reprocesar", "en_curso": False}), 409
    REPROCESADO.update({"en_curso": True, "mensaje": "Regenerando la vista previa...", "ok": None})

    def _reprocesar():
        try:
            ok, msg = apilado.reprocesar_preview()
            REPROCESADO.update({"ok": ok, "mensaje": msg})
        except Exception as e:
            log.exception("Error reprocesando la vista previa")
            REPROCESADO.update({"ok": False, "mensaje": f"Error: {e}"})
        finally:
            REPROCESADO["en_curso"] = False

    threading.Thread(target=_reprocesar, name="reprocesar-preview", daemon=True).start()
    return jsonify({"ok": True, "mensaje": REPROCESADO["mensaje"], "en_curso": True})


@app.route('/apilado/reprocesar-estado')
def apilado_reprocesar_estado():
    return jsonify(REPROCESADO)

@app.route('/galeria')

def galeria():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    carpeta = "static/fotos"
    if not os.path.exists(carpeta):
        os.makedirs(carpeta)

    imagenes = sorted([f for f in os.listdir(carpeta) if f.lower().endswith(".jpg")])
    # Solo las fotos con su FITS apilado al lado se pueden reprocesar en el editor
    con_fits = {f for f in imagenes if os.path.isfile(os.path.join(carpeta, os.path.splitext(f)[0] + ".fits"))}
    return render_template("galeria.html", imagenes=imagenes, con_fits=con_fits)

@app.route("/eliminar-imagen", methods=["POST"])
def eliminar_imagen():
    if 'usuario' not in session:
        return jsonify({"error": "No autorizado"}), 403

    datos = request.get_json(silent=True) or {}
    nombre = str(datos.get("nombre", ""))

    # Solo nombres de fichero simples dentro de static/fotos: nada de "..", barras
    # ni rutas absolutas (antes {"nombre": "../../config.ini"} borraba ese fichero)
    if nombre != os.path.basename(nombre) or not re.fullmatch(r"[A-Za-z0-9_.\-]+\.jpg", nombre):
        return jsonify({"error": "Nombre de imagen no válido"}), 400

    carpeta_fotos = os.path.abspath(os.path.join("static", "fotos"))
    ruta_jpg = os.path.abspath(os.path.join(carpeta_fotos, nombre))
    if os.path.dirname(ruta_jpg) != carpeta_fotos:
        return jsonify({"error": "Nombre de imagen no válido"}), 400

    # Se obtiene el nombre base para generar el nombre del FITS
    nombre_base, extension = os.path.splitext(nombre)
    nombre_fits = f"{nombre_base}.fits"
    ruta_fits = os.path.join(carpeta_fotos, nombre_fits)

    mensajes = []
    try:
        # Intentar eliminar el archivo JPG
        if os.path.exists(ruta_jpg):
            os.remove(ruta_jpg)
            mensajes.append(f"Imagen '{nombre}' eliminada.")
        else:
            mensajes.append(f"Imagen '{nombre}' no encontrada.")

        # Intentar eliminar el archivo FITS
        if os.path.exists(ruta_fits):
            os.remove(ruta_fits)
            mensajes.append(f"Archivo FITS '{nombre_fits}' eliminado.")
        else:
            mensajes.append(f"Archivo FITS '{nombre_fits}' no encontrado.")

        # Datos de la firma (JSON hermano), si los hay
        ruta_json = os.path.join(carpeta_fotos, f"{nombre_base}.json")
        if os.path.exists(ruta_json):
            os.remove(ruta_json)
        # Original apartado por el editor (static/fotos/originales), si lo hay
        editor_fotos.borrar_original(os.path.join(carpeta_fotos, nombre_base))

        return jsonify({"ok": True, "mensaje": "✅ " + " ".join(mensajes)})
    except Exception as e:
        return jsonify({"error": f"❌ Error al eliminar: {e}"})


@app.route("/galeria/compartir/<nombre>")
def galeria_compartir(nombre):
    """
    Devuelve la foto firmada para compartirla o descargarla. Las guardadas con firma
    se sirven tal cual; las antiguas (sin JSON hermano) se firman al vuelo con lo que
    se pueda recuperar del nombre del archivo y del FITS. ?descargar=1 fuerza la descarga.
    """
    if 'usuario' not in session:
        return redirect(url_for('login'))
    carpeta_fotos = os.path.abspath(os.path.join("static", "fotos"))
    ruta_jpg = os.path.abspath(os.path.join(carpeta_fotos, nombre))
    if (nombre != os.path.basename(nombre) or not re.fullmatch(r"[A-Za-z0-9_.\-]+\.jpg", nombre)
            or os.path.dirname(ruta_jpg) != carpeta_fotos or not os.path.isfile(ruta_jpg)):
        return "Imagen no encontrada", 404
    descargar = request.args.get("descargar") == "1"
    nombre_salida = f"{os.path.splitext(nombre)[0]}_ScopePower.jpg"
    meta = firma.meta_de_archivo(ruta_jpg)
    if meta.get("firmada"):
        return send_file(ruta_jpg, mimetype="image/jpeg", as_attachment=descargar, download_name=nombre_salida)
    imagen = cv2.imread(ruta_jpg)
    if imagen is None:
        return "No se pudo leer la imagen", 500
    meta.update(firma.datos_observatorio())
    firmada = firma.firmar(imagen, meta)
    ok, jpg = cv2.imencode(".jpg", firmada, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return "No se pudo codificar la imagen", 500
    return send_file(io.BytesIO(jpg.tobytes()), mimetype="image/jpeg", as_attachment=descargar, download_name=nombre_salida)

# ─── Editor de fotos de la galería (editor_fotos.py) ─────────────────────────
# Reprocesa el FITS guardado junto al JPG con los controles del apilado, el
# estirado nuevo o el clásico, la firma y el nombre del objeto.

@app.route("/galeria/editar/<nombre>")
def galeria_editar(nombre):
    if 'usuario' not in session:
        return redirect(url_for('login'))
    try:
        info = editor_fotos.info_foto(nombre)
    except editor_fotos.ErrorEditor as e:
        return str(e), 404
    return render_template("editar_foto.html", info=info, mostrar_volver=True)


@app.route("/galeria/editar/<nombre>/original")
def galeria_editar_original(nombre):
    """El JPG con el que se compara: el original apartado si ya se guardó alguna vez, si no el actual."""
    try:
        ruta = editor_fotos.jpg_original(nombre)
    except editor_fotos.ErrorEditor as e:
        return str(e), 404
    if not os.path.isfile(ruta):
        return "Imagen no encontrada", 404
    r = send_file(ruta, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return r


@app.route("/galeria/editar/<nombre>/vista", methods=["POST"])
def galeria_editar_vista(nombre):
    """Vista previa (JPEG) del FITS reprocesado con los ajustes que manda la web."""
    datos = request.get_json(silent=True) or {}
    try:
        jpg = editor_fotos.vista_previa(nombre, datos)
    except editor_fotos.ErrorEditor as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        log.exception("Error en la vista previa del editor")
        return jsonify({"error": f"No se pudo procesar: {e}"}), 500
    r = send_file(io.BytesIO(jpg), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return r


@app.route("/galeria/editar/<nombre>/guardar", methods=["POST"])
def galeria_editar_guardar(nombre):
    datos = request.get_json(silent=True) or {}
    try:
        return jsonify(editor_fotos.guardar(nombre, datos))
    except editor_fotos.ErrorEditor as e:
        return jsonify({"ok": False, "mensaje": str(e)}), 409


@app.route("/galeria/editar-estado")
def galeria_editar_estado():
    return jsonify(editor_fotos.EDICION)


@app.route("/galeria/editar/<nombre>/restaurar", methods=["POST"])
def galeria_editar_restaurar(nombre):
    try:
        editor_fotos.restaurar(nombre)
    except editor_fotos.ErrorEditor as e:
        return jsonify({"ok": False, "mensaje": str(e)}), 409
    return jsonify({"ok": True, "mensaje": "Foto restaurada a su original"})


@app.route("/configuracion")
def vista_configuracion():
    return render_template("configuracion.html")

@app.route("/config-datos")
def obtener_config_datos():
    config.read("config.ini")
    return jsonify({
        "driver_telescopio": config["Dispositivos"]["driver_telescopio"],
        "driver_camara": config["Dispositivos"]["driver_camara"],
        "binning": config["Dispositivos"].getint("binning_camara"),
        "exposicion": config["Dispositivos"].getfloat("exposicion_captura"),
        "numero_maximo_imagenes": config["Dispositivos"].getint("numero_maximo_imagenes"),
        "ganancia_camara": config["Dispositivos"].get("ganancia_camara", "").strip(),   # "" = la del driver
        "focal_len": config["Dispositivos"].getfloat("focal_len"),
        "color_intensity": config["Dispositivos"].getfloat("color_intensity"),
        "zoom_preview": config["Dispositivos"].getint("zoom_preview")
    })

@app.route("/camara/ganancia")
def camara_ganancia():
    """
    Ganancia configurada y, si la cámara está conectada, la actual y el rango
    del driver (GainMin/GainMax). Sin conectar solo devuelve la de config.ini.
    """
    salida = {"configurada": ganancia_configurada(), "conectada": camara.esta_conectado()}
    if salida["conectada"]:
        try:
            salida.update(camara.ejecutar(leer_ganancia, timeout=10))
        except ErrorHardware as e:
            salida["error"] = str(e)
    return jsonify(salida)


@app.route("/guardar-config-datos", methods=["POST"])
def guardar_config_datos():
    data = request.get_json()
    config.read("config.ini")
    antes = _dispositivos()
    for clave, valor in data.items():
        if clave in config["Dispositivos"] or clave in ("ganancia_camara",):
            config["Dispositivos"][clave] = str(valor).strip()
    with open("config.ini", "w") as f:
        config.write(f)
    # Si cambia un driver, desconectar ese dispositivo: la siguiente conexión usa el nuevo
    despues = _dispositivos()
    avisos = []
    if despues["driver_camara"] != antes["driver_camara"] and camara.esta_conectado():
        camara.desconectar()
        avisos.append("cámara desconectada; se conectará con el driver nuevo")
    if despues["driver_telescopio"] != antes["driver_telescopio"] and montura.esta_conectado():
        montura.desconectar()
        avisos.append("montura desconectada; pulsa Reconectar equipo en el inicio")
    return "✅ Configuración guardada correctamente" + (" (" + "; ".join(avisos) + ")" if avisos else "")

PUERTO_WEB = 5000


def _config_anuncio_ip():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    v = cfg["Voz"] if "Voz" in cfg else {}
    return {
        "anunciar_ip": str(v.get("anunciar_ip", "true")).lower() == "true",
        "intervalo_s": int(v.get("intervalo_anuncio_ip", 30)),
    }


anunciador = anuncio_ip.AnunciadorIP(PUERTO_WEB, voz.decir, intervalo_s=_config_anuncio_ip()["intervalo_s"])


@app.route("/red/estado")
def red_estado():
    """Direcciones de este PC para conectar desde el móvil (y estado del anuncio por voz)."""
    return jsonify({
        "direcciones": [{"ip": ip, "adaptador": ad, "url": f"http://{ip}:{PUERTO_WEB}"} for ip, ad in anuncio_ip.direcciones_ip()],
        "nombre": anuncio_ip.nombre_equipo(),
        "url_nombre": f"http://{anuncio_ip.nombre_equipo()}.local:{PUERTO_WEB}",
        "puerto": PUERTO_WEB,
        "anuncios": anunciador.anuncios,
        "conectado": anunciador.esta_conectado(),
        "anunciar_ip": _config_anuncio_ip()["anunciar_ip"],
    })


@app.route("/voz/anunciar-ip", methods=["POST"])
def voz_anunciar_ip():
    """Dicta ahora mismo la IP y el puerto por el altavoz (aunque los avisos estén apagados)."""
    activada = voz.locutor.config["activada"]
    voz.locutor.config["activada"] = True
    try:
        texto = anunciador.anunciar_ahora()
    finally:
        voz.locutor.config["activada"] = activada
    return jsonify({"ok": True, "texto": texto})


@app.route("/voz/estado")
def voz_estado():
    return jsonify(voz.locutor.estado())


@app.route("/voz/toggle", methods=["POST"])
def voz_toggle():
    datos = request.get_json(silent=True) or {}
    if "activada" in datos:
        activada = bool(datos["activada"])
    else:
        activada = not voz.locutor.config["activada"]
    voz.locutor.activar(activada)
    if activada:
        voz.decir("Avisos por voz activados")
    return jsonify(voz.locutor.estado())


@app.route("/voz/config", methods=["POST"])
def voz_config():
    datos = request.get_json(silent=True) or {}
    try:
        voz.locutor.configurar(**datos)
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(voz.locutor.estado())


@app.route("/voz/probar", methods=["POST"])
def voz_probar():
    datos = request.get_json(silent=True) or {}
    texto = datos.get("texto") or "ScopePower listo. Telescopio y cámara conectados."
    # La prueba suena aunque los avisos estén desactivados
    activada = voz.locutor.config["activada"]
    voz.locutor.config["activada"] = True
    try:
        encolado = voz.decir(texto)
    finally:
        voz.locutor.config["activada"] = activada
    return jsonify({"ok": encolado, **voz.locutor.estado()})


@app.errorhandler(Exception)
def error_no_capturado(e):
    """Registra el error con su traza y muestra un mensaje en vez de la página 500 en blanco."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    log.exception("Error no capturado atendiendo %s %s", request.method, request.path)
    return render_template_string(
        "{% extends 'base.html' %}{% block title %}Error{% endblock %}{% block content %}"
        "<div class='pagina-cabecera'><div><h1 class='pagina-titulo'>Error inesperado</h1></div></div>"
        "<div class='card card-centrada'><div class='aviso aviso-err'>{{ error }}</div>"
        "<p class='texto-3' style='margin:0'>Los detalles están en logs/scopepower.log</p></div>{% endblock %}",
        error=str(e)), 500


@app.route("/ascom-selector")
def ascom_selector():
    import pythoncom
    import win32com.client
    tipo = request.args.get("tipo", "Telescope")
    pythoncom.CoInitialize()
    chooser = win32com.client.Dispatch("ASCOM.Utilities.Chooser")
    chooser.DeviceType = tipo
    driver = chooser.Choose("")
    return jsonify({"driver": driver})


if __name__ == '__main__':
    os.makedirs(ruta_absoluta("capturas"), exist_ok=True)
    os.makedirs(ruta_absoluta("static"), exist_ok=True)
    # Objetos anotados: lo que dejó puesto el usuario la última vez ([Apilado] objetos_anotados)
    apilado.OBJETOS_ANOTADOS = config["Apilado"].get("objetos_anotados", "false").strip().lower() == "true" \
        if "Apilado" in config else False
    iniciar_hilo_apilado(ruta_absoluta("capturas"), alinear=True)
    # Stellarium tarda en arrancar: lanzarlo ya, en un hilo, para que esté listo
    # cuando el usuario entre (config: [General] lanzar_stellarium_al_arrancar)
    # Elementos orbitales de cometas y asteroides (MPC): renovar si hay red, en un hilo
    cuerpos_menores.actualizar_en_segundo_plano()
    catalogo_offline.precargar()
    if usar_stellarium():
        threading.Thread(target=lanzar_stellarium_si_no_esta_activo, name="stellarium-arranque", daemon=True).start()
    if _config_anuncio_ip()["anunciar_ip"]:
        # Dicta IP y puerto cada 30 s hasta la primera conexión desde el móvil
        anunciador.iniciar()
    else:
        voz.decir("ScopePower iniciado. Servidor listo.")
    # threaded=True: cada petición en su hilo. El acceso a la montura y a la
    # cámara está serializado en hardware.py, así que la web sigue respondiendo
    # (estado, parar montura, cancelar) mientras hay una exposición en curso.
    app.run(host='0.0.0.0', port=PUERTO_WEB, threaded=True)

