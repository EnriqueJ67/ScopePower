from flask import Flask, request, jsonify, render_template, render_template_string, send_file
from flask import session, redirect, url_for

from unidecode import unidecode
import win32com.client
import pythoncom
import subprocess
import time
import math
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
import foto_actual
import giro_camara
import vivo_dia
import camara_sdk
import disco
import espacio_disco
import rastros
import apuntado_teorico
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
import montura_ejes
import seguimiento
import vigilante_seguimiento
import plan_sesion

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
    "editor_paneles": ("Interfaz", "editor_paneles", "json"),     # orden y plegado de los paneles del editor de fotos
    "opcion_dark": ("Interfaz", "opcion_dark", "texto"),
    "numero_darks": ("Dispositivos", "numero_darks", "entero"),
    "numero_maximo_imagenes": ("Dispositivos", "numero_maximo_imagenes", "entero"),   # se puede cambiar con el apilado en marcha
    "modo_dia": ("Interfaz", "modo_dia", "texto"),                 # "on"/"off": la Foto de prueba arranca en modo día
    "foto_dia_objetivo": ("Interfaz", "foto_dia_objetivo", "texto"),   # sol | luna | paisaje
    "foto_dia_campo": ("Interfaz", "foto_dia_campo", "entero"),        # % del campo visible en el modo día (100, 75, 50, 35, 25)
    "foto_dia_compensacion": ("Interfaz", "foto_dia_compensacion", "decimal"),   # EV (−3..+3) sobre la exposición automática de día
    "foto_continua_guardar": ("Interfaz", "foto_continua_guardar", "texto"),   # no | jpg | fits: qué guardar en el modo continuo
    "foto_pad_flotante": ("Interfaz", "foto_pad_flotante", "texto"),   # "on"/"off": pad de la montura encima de la foto en Cámara
    "foto_dia_color_sol": ("Interfaz", "foto_dia_color_sol", "texto"),  # amarillo | natural | blanco (foto_dia.COLORES_SOL)
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
    motivo = _sin_espacio_en_disco()
    if motivo:
        return "⚠️ " + motivo, 409
    reanudar_apilado()
    bucle.reanudar()
    return "▶️ Apilado reanudado"

@app.route('/estado-pausa-apilado')
def estado_pausa_apilado():
    return jsonify({"pausado": esta_pausado() or bucle.pausado})


def _trabajos_en_curso():
    """Nombre del trabajo que tiene la cámara o la montura (darks, centrado...), o None."""
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("modo en vivo", vivo_dia.trabajo_vivo), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo),
                            ("seguimiento", seguimiento.trabajo_seguimiento), ("alineación", alineacion.trabajo_alineacion)):
        if trabajo.en_curso:
            return nombre
    return None


def _plan_en_marcha():
    """Motivo por el que el plan de la noche bloquea una acción manual, o None (ver plan_sesion.py)."""
    t = plan_sesion.trabajo_plan
    if t.en_curso and not t.pausado:
        return "El plan de la noche está en marcha; páusalo o detenlo antes."
    return None


def _arrancar_apilado(nuevo, usar_dark):
    """Arranca el bucle de captura (lo usan la ruta /apilado/iniciar y el plan de la noche)."""
    if nuevo:
        bucle.detener("Reiniciado")
        reiniciar_apilado_global()
    reanudar_apilado()
    bucle.iniciar(usar_dark=usar_dark)


@app.route('/apilado/iniciar', methods=["POST"])
def apilado_iniciar():
    """
    Arranca el bucle de captura en el servidor.
    JSON: {"nuevo": true|false (reiniciar el apilado), "usar_dark": true|false}
    """
    datos = request.get_json(silent=True) or {}
    nuevo = bool(datos.get("nuevo", True))
    usar_dark = bool(datos.get("usar_dark", True))
    motivo = _plan_en_marcha() or _sin_espacio_en_disco()
    if motivo:
        return jsonify({"ok": False, "error": motivo}), 409
    otro = _trabajos_en_curso()
    if otro:
        return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {otro}"}), 409
    _arrancar_apilado(nuevo, usar_dark)
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
    # El botón Detener de la cabecera para también el plan de la noche (guarda lo apilado del objeto en curso)
    if plan_sesion.trabajo_plan.en_curso:
        plan_sesion.trabajo_plan.detener()
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
camara = TrabajadorCOM("cámara", lambda: _dispositivos()["driver_camara"], al_conectar=lambda cam: voz.decir("Cámara conectada"),
                       fabricas={camara_sdk.DRIVER_SDK: camara_sdk.crear})    # Player One por la DLL, como en N.I.N.A.


def _asegurar_seguimiento(motivo):
    """Vigilante del seguimiento sidéreo antes de una foto de noche (ver vigilante_seguimiento.py).
    No actúa mientras el pad mueve los ejes: MoveAxis apaga el seguimiento a propósito."""
    return vigilante_seguimiento.vigilante.asegurar(
        montura, motivo, voz=voz, permitir=lambda: not _pad["activo"])


# El centrado hace sus propias fotos (centrado.py): mismo vigilante antes de cada una
centrado.trabajo_centrado.antes_de_foto = lambda: _asegurar_seguimiento("la foto del centrado")

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


# GoTo cerca del Sol: sin filtro solar el Sol quema el sensor de la cámara en segundos.
# SynScan Pro solo protege su propia pantalla (probado el 22-sep-2026 con la AZ GoTo real:
# por ASCOM aceptó el GoTo al Sol con "Observe Sun" desactivado), así que la barrera es esta.
SEPARACION_SOLAR_MINIMA = 10.0   # grados: a menos de esto del Sol se pide confirmar el filtro
ALTURA_SOL_DE_DIA = -6.0         # grados: con el Sol por encima no hay estrellas para el plate solving

def posicion_del_sol():
    """(SkyCoord del Sol ahora, altura en grados) desde el observatorio; (None, None) si falla."""
    try:
        ubicacion = EarthLocation(lat=LATITUD_OBSERVATORIO * u.deg, lon=LONGITUD_OBSERVATORIO * u.deg,
                                  height=ALTITUD_OBSERVATORIO * u.m)
        ahora = Time.now()
        sol = get_body("sun", ahora, ubicacion)
        return sol, float(sol.transform_to(AltAz(obstime=ahora, location=ubicacion)).alt.deg)
    except Exception as e:
        print(f"⚠️ No se pudo calcular la posición del Sol: {e}")
        return None, None

def es_de_dia():
    """True con el Sol por encima de ALTURA_SOL_DE_DIA: el centrado por plate solving no tiene
    estrellas que resolver (y al Sol o la Luna con filtro, menos aún)."""
    _, altura_sol = posicion_del_sol()
    return altura_sol is not None and altura_sol > ALTURA_SOL_DE_DIA

def aviso_solar(nombre_objeto, ra, dec):
    """Si el GoTo va al Sol, o a un objeto a menos de SEPARACION_SOLAR_MINIMA del Sol con el
    Sol sobre el horizonte, devuelve los datos del aviso que la interfaz debe confirmar antes
    de mover la montura; None si no hay peligro."""
    es_sol = nombre_objeto in ("sol", "sun")
    sol, altura_sol = posicion_del_sol()
    if sol is not None:
        separacion = float(sol.separation(SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)).deg)
    else:
        if not es_sol:
            return None
        separacion = 0.0
    if not es_sol and (altura_sol is None or altura_sol < 0 or separacion > SEPARACION_SOLAR_MINIMA):
        return None
    return {"es_sol": es_sol, "separacion_deg": round(separacion, 1),
            "altura_sol_deg": None if altura_sol is None else round(altura_sol, 1)}


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
        # Espacio libre en el disco de las fotos: la cabecera avisa si baja del umbral
        "disco": espacio_disco.vigilante.estado(),
    })


# ───────────────────────── Espacio en disco ─────────────────────────
# El vigilante (espacio_disco.py) avisa por voz y en el log; el móvil lo ve en la cabecera
# y en Configuración. Umbral en config.ini: [Disco] umbral_aviso_pct.
if "Disco" in config:
    espacio_disco.vigilante.configurar(config["Disco"].get("umbral_aviso_pct", espacio_disco.UMBRAL_POR_DEFECTO))


def _sin_espacio_en_disco():
    """Motivo para no empezar ni reanudar capturas (menos de 1 GB libre), o None."""
    e = espacio_disco.vigilante.estado()
    if e.get("sin_espacio"):
        return (f"El disco está casi lleno ({e['libre_gb']} GB libres, hace falta al menos "
                f"{e['minimo_gb']:.0f} GB). Libera espacio en el PC para seguir capturando.")
    return None


def _pausar_por_disco_lleno(e):
    """
    La llama el vigilante cada 30 s mientras queden menos de 1 GB libres: pausa el plan de la
    noche y el apilado (la exposición en curso termina y se guarda). Si alguien reanuda sin
    liberar espacio, las rutas de reanudar lo impiden; y si se colara, aquí se vuelve a pausar.
    Una pausa puesta por el usuario se respeta tal cual.
    """
    motivo = f"Disco casi lleno: quedan {e['libre_gb']} GB. Libera espacio y reanuda."
    pausado = False
    plan = plan_sesion.trabajo_plan
    if plan.en_curso and not plan.pausado:
        plan.pausar()
        pausado = True
    if bucle.activo and (pausado or not bucle.pausado):
        pausar_apilado()
        bucle.pausar(motivo)
        pausado = True
    if pausado:
        log.warning("Apilado pausado por falta de espacio en %s: %.2f GB libres", e["unidad"], e["libre_gb"])
        voz.decir("Apilado pausado: queda menos de un giga libre en el disco.")


@app.route('/disco', methods=['GET', 'POST'])
def espacio_en_disco():
    if request.method == 'POST':
        datos = request.get_json(silent=True) or {}
        try:
            umbral = int(round(float(datos["umbral_pct"])))
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "Indica el porcentaje"}), 400
        if not 1 <= umbral <= 90:
            return jsonify({"ok": False, "error": "El aviso debe estar entre 1 y 90 %"}), 400
        espacio_disco.vigilante.configurar(umbral)
        _guardar_en_config("Disco", {"umbral_aviso_pct": umbral})
        log.info("Aviso de disco: por debajo del %d %% libre", umbral)
        e = espacio_disco.vigilante.comprobar()
    else:
        e = espacio_disco.vigilante.estado()
    if request.args.get("carpetas") or request.method == 'POST':
        e["carpetas"] = espacio_disco.vigilante.carpetas()
    return jsonify(e)


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


@app.route('/observatorio/diagnostico', methods=['POST'])
def observatorio_diagnostico():
    """
    La ubicación se pide en el navegador del móvil y el PC solo se enteraba si llegaba a
    guardarse: cuando fallaba (27-sep-2026, la petición se quedó colgada) el log no decía
    nada. La página de Configuración anota aquí cada paso: permiso, error o posición.
    """
    datos = request.get_json(silent=True) or {}
    evento = " ".join(str(datos.get("evento", "")).split())[:200]
    segura = "https o dirección segura" if datos.get("segura") else "http NO seguro"
    donde = "app instalada" if datos.get("app") else "pestaña de Chrome"
    log.info("Ubicación desde el móvil %s (%s, %s, %s): %s", request.remote_addr,
             str(datos.get("origen", ""))[:60], segura, donde, evento)
    return jsonify({"ok": True})


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
    if tipo == "Camera":
        # La cámara Player One también se puede usar sin ASCOM, por su DLL (camara_sdk.py)
        drivers.insert(0, {"progid": camara_sdk.DRIVER_SDK, "name": camara_sdk.NOMBRE_DRIVER})
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


# Las velocidades se pasan al driver con montura_ejes.mover_ejes_ascom (la misma
# lógica que usa el seguimiento de objetos); AxisRates se anota en el log una vez.
_rangos_axisrates = montura_ejes.rangos_axisrates
_anotar_axisrates = montura_ejes.anotar_axisrates


def _pad_aplicar(vx, vy):
    return montura.ejecutar(lambda tel: montura_ejes.mover_ejes_ascom(tel, vx, vy), timeout=10)


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
    if seguimiento.trabajo_seguimiento.en_curso:
        return jsonify({"ok": False, "mensaje": "El seguimiento de objetos lleva la montura; páralo antes"}), 409
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

def revelar_foto_noche(imagen_fits, patron_bayer):
    """
    Foto de noche revelada como en la vista de Cámara: debayer a 16 bits con el patrón
    de la cabecera, auto-stretch STF y la saturación de Configuración. Devuelve BGR/gris uint8.
    """
    # imagen_fits es un array 2D (raw CFA de la cámara). Se debayeriza directamente a
    # 16 bits; nunca reducir a 8 bits antes de este paso.
    if imagen_fits.ndim == 2 and patron_bayer in ('RGGB', 'BGGR', 'GRBG', 'GBRG'):
        rgb = debayer_rgb(imagen_fits, patron_bayer)
    else:
        # Sensor mono (o patrón no Bayer): mostrar en escala de grises
        rgb = imagen_fits
    imagen_mtf = apply_stf_color(rgb)
    nuevo_config = configparser.ConfigParser()
    nuevo_config.read("config.ini")
    intensidad_color = float(nuevo_config["Dispositivos"].get("color_intensity", 1.0))
    imagen_mtf = aumentar_saturacion(imagen_mtf, factor=intensidad_color)
    # cv2 espera BGR
    return cv2.cvtColor(imagen_mtf, cv2.COLOR_RGB2BGR) if imagen_mtf.ndim == 3 else imagen_mtf


# Nombre en la web de cada paso del procesado (procesado.SUBFASES)
FASES_REVELADO = {"gradiente": "Quitando el gradiente", "estirando": "Estirando", "ruido": "Reduciendo el ruido",
                  "contraste": "Contraste local", "color": "Color"}


def revelar_foto_noche_apilado(imagen_fits, patron_bayer, escala=0.5, progreso=None, encuadre=None):
    """
    La misma foto de noche revelada como si fuera un apilado de una sola foto: debayer a
    16 bits, el recorte de zoom de Configuración (zoom_preview) y el procesado multiescala
    de procesado.py con el preset de Apilado ([Procesado] de config.ini). Tarda bastante
    más que revelar_foto_noche (la reducción de ruido manda), por eso se llama desde un
    hilo. `escala`: 0.5 para la vista de Cámara, 1.0 para la galería. `encuadre`: el giro
    de Cámara (giro_camara), que se aplica con el zoom sobre la imagen lineal. Devuelve BGR uint8.
    """
    import procesado
    if imagen_fits.ndim == 2 and patron_bayer in ('RGGB', 'BGGR', 'GRBG', 'GBRG'):
        rgb = debayer_rgb(imagen_fits, patron_bayer)
    elif imagen_fits.ndim == 2:
        rgb = np.repeat(imagen_fits[:, :, None], 3, axis=2)       # sensor mono: los tres canales iguales
    else:
        rgb = imagen_fits
    rgb = np.asarray(rgb, dtype=np.float32)
    e = encuadre or giro_camara.SIN_GIRO
    balance = procesado.balance_estrellas(rgb)                 # color: con las estrellas del campo completo
    rgb, _ = procesado.girar_campo(rgb, procesado.fraccion_campo(), e.get("giro", 0.0),
                                   zona=(0, 0, rgb.shape[1], rgb.shape[0]), espejo=bool(e.get("espejo")))
    rgb8 = procesado.procesar(rgb, 1, procesado.leer_config(), escala=escala, progreso=progreso, balance=balance)
    return cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)


def _escala_revelado_vista():
    """Como la vista previa del apilado: media resolución salvo con el zoom al 50 % o menos."""
    import procesado
    return 0.5 if procesado.fraccion_campo() > 0.5 else 1.0


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

    # Antes de exponer: que la montura siga el cielo (si algo apagó el seguimiento, se reactiva y queda en el log)
    _asegurar_seguimiento(descripcion.lower() if descripcion else "la foto")

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
        hdu.header['EXPTIME'] = (float(EXPOSICION), 'Exposicion (s)')
        hdu.header['EXPOSURE'] = (float(EXPOSICION), 'Exposicion (s)')
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
            return {"ok": True, "mensaje": f"Captura de {EXPOSICION:g} s guardada", "ruta": ruta_guardada,
                    "dark": dark_aplicado, "ganancia": ganancia_real, "exposicion": EXPOSICION, "archivo": archivo_copia}

        # ───   Revelado (debayer, STF, saturación) y versión reducida para la web   ───
        sin_girar = revelar_foto_noche(imagen_fits, patron_bayer)
        imagen_web, encuadre = giro_camara.girar(sin_girar)      # el giro de Cámara; el FITS queda sin girar
        guardar_jpg_web(RUTA_IMAGEN_WEB, imagen_web)
        # La foto que se ve ahora en Cámara está en static/captura.fits (para "Apunta aquí" y la galería)
        foto_actual.registrar("noche", imagen_fits.shape[1], imagen_fits.shape[0], BINNING,
                              exposicion=EXPOSICION, ganancia=ganancia_real, encuadre=encuadre, sin_girar=sin_girar)
        if copia and copia.get("formato") == "jpg":
            # La foto revelada entera (no la reducida de la web), para tener la serie sin el peso del FITS
            os.makedirs(copia["carpeta"], exist_ok=True)
            archivo_copia = os.path.join(copia["carpeta"], copia["nombre"] + ".jpg")
            cv2.imwrite(archivo_copia, imagen_web, [cv2.IMWRITE_JPEG_QUALITY, 92])

        return {"ok": True, "mensaje": f"Captura de {EXPOSICION:g} s guardada", "ruta": ruta_guardada,
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
    return _pagina_camara(hay_foto=True)

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
    return _pagina_camara(hay_foto=os.path.exists(RUTA_IMAGEN_WEB))


def _pagina_camara(hay_foto):
    """La página Cámara (imagen.html) con todo lo que necesita su script, venga de la ruta que venga."""
    return render_template('imagen.html', mostrar_volver=True, hay_foto=hay_foto,
                           niveles_dia={k: v["nivel"] for k, v in foto_dia.OBJETIVOS.items()},
                           camara_sdk=camara_sdk.es_sdk(_dispositivos()["driver_camara"]))


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
    # El toque viene sobre la foto como se ve (con el giro de Cámara): a la imagen del FITS
    fx, fy = giro_camara.original(fx, fy, ancho, alto, (_foto_en_pantalla() or {}).get("encuadre"))
    # Fracción de la imagen -> píxel (origen 0 en la esquina superior izquierda,
    # como el JPG de la web, que es la misma imagen reducida)
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


# ───────────────────────── Apunta aquí sin plate solving (modo día) ─────────────────────────
def _foto_en_pantalla():
    """La foto que enseña ahora Cámara (foto_actual.py); tras reiniciar, la de static/captura.fits."""
    f = foto_actual.actual()
    if f is None and os.path.exists(RUTA_IMAGEN):
        f = foto_actual.desde_fits(RUTA_IMAGEN)
        if f is not None and not f.get("instante"):
            f["instante"] = os.path.getmtime(RUTA_IMAGEN)
        if f is not None:
            f["encuadre"] = giro_camara.leer()      # el JPG que quedó en disco se hizo con el giro de config.ini
    if f is not None and not f.get("encuadre"):
        f["encuadre"] = dict(giro_camara.SIN_GIRO)
    return f


def _revelar_captura_en_disco():
    """La foto de static/captura.fits revelada otra vez como la enseña Cámara, sin girar; None si no hay."""
    f = foto_actual.desde_fits(RUTA_IMAGEN) if os.path.exists(RUTA_IMAGEN) else None
    if f is None:
        return None
    with fits.open(RUTA_IMAGEN) as hdul:
        datos = np.asarray(hdul[0].data)
        patron = str(hdul[0].header.get("BAYERPAT") or "").strip().upper()
    if f["modo"] == "dia":
        return foto_dia.revelar(foto_dia.recortar_centro(datos, f["campo"]), foto_dia.sensor_type_de_patron(patron),
                                f.get("objetivo"), balance=f.get("balance"), negro=f.get("negro"), aprender=False,
                                ev=f.get("ev") or 0.0)[0]
    return revelar_foto_noche(datos, patron)


@app.route('/camara/giro')
def camara_giro():
    """Giro y espejo con que Cámara enseña y guarda las fotos (giro_camara.py)."""
    import orientacion_camara
    return jsonify(dict(giro_camara.leer(), cenit=orientacion_camara.ultima() is not None))


@app.route('/camara/giro', methods=['POST'])
def camara_giro_guardar():
    """
    {giro, espejo}: los guarda y vuelve a girar la foto que hay en pantalla (sin repetir la
    captura). Con el modo en vivo o el continuo en marcha se ve en el fotograma siguiente.
    """
    datos = request.get_json(silent=True) or {}
    try:
        encuadre = giro_camara.guardar(float(datos.get("giro", 0) or 0), bool(datos.get("espejo")))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Giro no válido."}), 400
    regenerada = False
    try:
        f = foto_actual.actual()
        if f is None:
            base = _revelar_captura_en_disco()          # tras reiniciar o al parar el modo en vivo
        else:
            base = f.get("sin_girar") if f.get("modo") in ("noche", "dia") else None
        if base is not None:
            guardar_jpg_web(RUTA_IMAGEN_WEB, giro_camara.aplicar(base, encuadre))
            foto_actual.actualizar(encuadre=encuadre)
            regenerada = True
    except Exception as e:
        log.exception("No se pudo volver a girar la foto de Cámara")
        return jsonify({"ok": False, "mensaje": f"Giro guardado, pero no se pudo volver a girar la foto: {e}", **encuadre}), 500
    log.info("Giro de Cámara: %g°%s", encuadre["giro"], " con espejo" if encuadre["espejo"] else "")
    return jsonify({"ok": True, "regenerada": regenerada, **encuadre})


@app.route('/camara/giro/cenit')
def camara_giro_cenit():
    """Giro y espejo que dejan la foto como se ve a simple vista (cénit arriba), con la orientación de la cámara."""
    import orientacion_camara
    orientacion = orientacion_camara.ultima()
    cenit = giro_camara.cenit_arriba(orientacion)
    if cenit is None:
        return jsonify({"ok": False, "mensaje": "Aún no hay ningún plate solving con esta cámara: haz uno de noche "
                                                "(centrado, alineación, apilado o sincronizar la foto) y luego se podrá calcular."}), 409
    return jsonify({"ok": True, "giro": cenit[0] % 360.0, "espejo": cenit[1],
                    "orientacion": apuntado_teorico.describir_orientacion(orientacion)})


def _montura_ocupada():
    """Motivo por el que no se debe mover la montura ahora, o None."""
    motivo_plan = _plan_en_marcha()
    if motivo_plan:
        return motivo_plan
    if bucle.activo and not bucle.pausado:
        return "El apilado está capturando; páusalo antes de mover el telescopio."
    for nombre, trabajo in (("centrado", centrado.trabajo_centrado), ("alineación", alineacion.trabajo_alineacion),
                            ("seguimiento", seguimiento.trabajo_seguimiento)):
        if trabajo.en_curso:
            return f"Hay un trabajo de {nombre} en marcha; espera a que termine."
    return None


def _observatorio():
    return (LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO)


def mover_relativo_horizonte(du, dv):
    """
    Mueve el apuntado (du, dv) grados en el plano tangente horizontal (Δaz·cos alt,
    Δalt) desde donde está la montura: GoTo corto en AR/Dec calculado con la misma
    hora y lugar, así es un movimiento relativo aunque la montura esté mal alineada.
    Devuelve la altura de partida; lanza ErrorHardware si falla.
    """
    ra_m, dec_m = montura.ejecutar(lambda tel: (tel.RightAscension, tel.Declination), timeout=10)

    # Desplazamiento en acimut/altura desde donde apunta la montura, y de vuelta a AR/Dec
    # con la misma hora y lugar: el GoTo resultante es un movimiento relativo
    ra_i, dec_i = coordenadas.desde_montura(ra_m, dec_m)
    marco = AltAz(obstime=Time.now(), location=EarthLocation(lat=LATITUD_OBSERVATORIO * u.deg, lon=LONGITUD_OBSERVATORIO * u.deg,
                                                               height=ALTITUD_OBSERVATORIO * u.m))
    ahora = SkyCoord(ra=ra_i * u.hourangle, dec=dec_i * u.deg).transform_to(marco)
    az_d, alt_d = apuntado_teorico.mover_en_horizonte(float(ahora.az.deg), float(ahora.alt.deg), du, dv)
    destino = SkyCoord(az=az_d * u.deg, alt=alt_d * u.deg, frame=marco).icrs
    ra_dest, dec_dest = coordenadas.a_montura(float(destino.ra.hour), float(destino.dec.deg))

    def _mover(tel):
        # ASCOM exige seguimiento para un GoTo en AR/Dec; en paisaje estaba apagado y se vuelve a apagar
        seguia = bool(tel.Tracking)
        if not seguia:
            tel.Tracking = True
        try:
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
        finally:
            if not seguia:
                tel.Tracking = False

    montura.ejecutar(_mover, timeout=150)
    time.sleep(asentamiento_montura())
    return float(ahora.alt.deg)


def apuntar_sin_plate_solving(fx, fy):
    """
    Centra el telescopio en el punto (fx, fy) de la foto que se ve (fracciones 0–1)
    con la orientación y la escala de la cámara del último plate solving, sin
    resolver la foto (ver apuntado_teorico.py). Devuelve un dict con ok y mensaje.
    """
    import orientacion_camara
    ocupada = _montura_ocupada()
    if ocupada:
        return {"ok": False, "mensaje": ocupada}
    orientacion = orientacion_camara.ultima()
    try:
        du, dv = apuntado_teorico.desplazamiento(fx, fy, _foto_en_pantalla(), orientacion)
    except apuntado_teorico.SinDatos as e:
        return {"ok": False, "mensaje": str(e)}
    salto = math.hypot(du, dv) * 60.0
    if salto < 0.2:
        return {"ok": True, "mensaje": "El punto ya está en el centro; no hace falta mover.", "salto_arcmin": 0.0, "movido": False}
    try:
        altura = mover_relativo_horizonte(du, dv)
    except ErrorHardware as e:
        return {"ok": False, "mensaje": f"Error al mover el telescopio: {e}"}
    disco.guiado.nueva_referencia()      # si se está guiando por el disco, su sitio es el nuevo
    log.info("Apuntado sin plate solving al punto (%.3f, %.3f): Δaz·cos(alt) %+.1f', Δalt %+.1f' (alt %.1f°)",
             fx, fy, du * 60, dv * 60, altura)
    voz.decir("Telescopio centrado en el punto elegido")
    return {"ok": True, "movido": True, "salto_arcmin": round(salto, 2),
            "mensaje": f"Telescopio movido {salto:.1f}′ ({abs(du) * 60:.1f}′ en acimut, {abs(dv) * 60:.1f}′ en altura) "
                       f"con la orientación de la cámara, sin plate solving."}


@app.route('/apuntar-foto-dia', methods=['POST'])
def apuntar_foto_dia():
    """"Apunta aquí" de día: {x, y} en 0–1 sobre la foto que se ve; sin ASTAP."""
    datos = request.get_json(silent=True) or {}
    try:
        fx, fy = float(datos.get("x")), float(datos.get("y"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Punto no válido."}), 400
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return jsonify({"ok": False, "mensaje": "El punto tiene que estar dentro de la foto."}), 400
    resultado = apuntar_sin_plate_solving(fx, fy)
    return jsonify(resultado), (200 if resultado["ok"] else 409)


@app.route('/apuntar-foto-dia/info')
def apuntar_foto_dia_info():
    """De dónde sale la orientación de la cámara y cuánto cielo abarca la foto que se ve (para el puntero)."""
    import orientacion_camara
    orientacion = orientacion_camara.ultima()
    if not orientacion:
        return jsonify({"ok": False, "mensaje": "Aún no hay ningún plate solving con esta cámara: haz uno de noche "
                                                "(centrado, alineación, apilado o sincronizar la foto) y luego funcionará también de día."})
    salida = {"ok": True, "orientacion": apuntado_teorico.describir_orientacion(orientacion)}
    vista = _foto_en_pantalla()
    if vista and vista.get("ancho"):
        ancho, alto = apuntado_teorico.campo_visible_grados(vista, orientacion)
        salida.update({"campo_ancho_deg": round(ancho, 4), "campo_alto_deg": round(alto, 4)})
    return jsonify(salida)


# ───────────────────────── Sol y Luna: sincronizar y guiar por el disco (disco.py) ─────────────────────────
def _leer_captura():
    with fits.open(RUTA_IMAGEN) as hdul:
        return np.asarray(hdul[0].data), hdul[0].header.copy()


def _medir_disco(foto, objetivo=None):
    import orientacion_camara
    orientacion = orientacion_camara.ultima()
    if orientacion is None:
        raise disco.SinDisco("Todavía no se conoce la orientación de la cámara: hace falta un plate solving de noche.")
    objetivo = objetivo or (foto or {}).get("objetivo")
    return disco.localizar(foto, orientacion, objetivo, _observatorio(), leer_fits=_leer_captura)


def sincronizar_con_disco(centrar=False):
    """
    Sincroniza la montura con el Sol o la Luna de la foto que se ve: el centro de la foto
    apunta a la posición del astro menos lo descentrado que esté el disco. Si `centrar`,
    después lleva el disco al centro con un movimiento corto.
    """
    f = _foto_en_pantalla()
    objetivo = (f or {}).get("objetivo")
    if objetivo not in disco.RADIO_KM:
        return {"ok": False, "mensaje": "Haz antes una foto de día del Sol o de la Luna (o pon el continuo o el modo en vivo)."}
    ocupada = _montura_ocupada()
    if ocupada:
        return {"ok": False, "mensaje": ocupada}
    try:
        d = _medir_disco(f, objetivo)
        ra_c, dec_c, alt_astro = disco.centro_de_la_foto(objetivo, d, _observatorio())
        ra_m, dec_m = montura.ejecutar(lambda tel: (tel.RightAscension, tel.Declination), timeout=10)
    except disco.SinDisco as e:
        return {"ok": False, "mensaje": str(e)}
    except ErrorHardware as e:
        return {"ok": False, "mensaje": f"Montura no disponible: {e}"}
    if alt_astro < 0:
        return {"ok": False, "mensaje": f"El {disco.NOMBRE[objetivo]} está bajo el horizonte según las efemérides: revisa la hora del PC y la ubicación."}
    ra_i, dec_i = coordenadas.desde_montura(ra_m, dec_m)
    error = float(SkyCoord(ra=ra_i * u.hourangle, dec=dec_i * u.deg).separation(
        SkyCoord(ra=ra_c * u.hourangle, dec=dec_c * u.deg)).arcmin)
    ra_s, dec_s = coordenadas.a_montura(ra_c, dec_c)
    try:
        montura.ejecutar(lambda tel: tel.SyncToCoordinates(ra_s, dec_s), timeout=20)
    except ErrorHardware as e:
        return {"ok": False, "mensaje": f"La montura no ha aceptado la sincronización: {e}"}
    nombre = disco.NOMBRE[objetivo]
    log.info("Sincronizado con el disco de %s: centro de la foto AR %.4f h Dec %.3f° (disco a %.1f', ajuste %.0f %%%s); "
             "la montura se equivocaba %.1f'", nombre, ra_c, dec_c, d["desvio_arcmin"], 100 * d["ajuste"],
             ", cortado por el borde" if d["toca_borde"] else "", error)
    mensaje = f"Montura sincronizada con el {nombre}: se equivocaba {error:.1f}′."
    if d["toca_borde"]:
        mensaje += " El disco salía cortado por el borde: la medida es algo menos precisa."
    movido = False
    if centrar and d["desvio_arcmin"] >= 0.2:
        try:
            mover_relativo_horizonte(d["du"], d["dv"])
            movido = True
            disco.guiado.nueva_referencia()
            mensaje += f" Disco centrado (estaba a {d['desvio_arcmin']:.1f}′)."
        except ErrorHardware as e:
            mensaje += f" No se pudo centrar: {e}"
    voz.decir(f"Montura sincronizada con el {nombre}")
    return {"ok": True, "mensaje": mensaje, "error_arcmin": round(error, 2), "desvio_arcmin": round(d["desvio_arcmin"], 2),
            "movido": movido}


@app.route('/disco/sincronizar', methods=['POST'])
def disco_sincronizar():
    datos = request.get_json(silent=True) or {}
    resultado = sincronizar_con_disco(centrar=bool(datos.get("centrar")))
    return jsonify(resultado), (200 if resultado["ok"] else 409)


def _pad_en_uso():
    with _pad["lock"]:
        return bool(_pad["activo"]) or time.time() - float(_pad.get("ultimo") or 0) < disco.PAUSA_PAD_S


def _umbral_guiado_disco():
    """Minutos de arco que puede apartarse el disco antes de corregir ([FotoDia] guiado_umbral_arcmin; 2′ si no está)."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    try:
        umbral = float(cfg.get("FotoDia", "guiado_umbral_arcmin", fallback=disco.UMBRAL_DEFECTO_ARCMIN))
    except ValueError:
        umbral = disco.UMBRAL_DEFECTO_ARCMIN
    return max(disco.UMBRAL_MINIMO_ARCMIN, umbral)


@app.route('/disco/guiado', methods=['POST'])
def disco_guiado():
    """{activar: bool}: mantener el disco del Sol o la Luna donde está, corrigiendo la montura."""
    datos = request.get_json(silent=True) or {}
    if not datos.get("activar"):
        disco.guiado.desactivar()
        return jsonify({"ok": True, "estado": disco.guiado.estado()})
    f = _foto_en_pantalla()
    objetivo = (f or {}).get("objetivo") or str(datos.get("objetivo") or "")
    if objetivo not in disco.RADIO_KM:
        return jsonify({"ok": False, "mensaje": "El guiado es para el Sol o la Luna en modo día."}), 409
    import apuntado_teorico, orientacion_camara
    orientacion = orientacion_camara.ultima()
    if orientacion is None:
        return jsonify({"ok": False, "mensaje": "Todavía no se conoce la orientación de la cámara: hace falta un plate solving de noche."}), 409
    umbral = _umbral_guiado_disco()

    def mover(du, dv):
        ocupada = _montura_ocupada()
        if ocupada:
            raise ErrorHardware(ocupada)
        mover_relativo_horizonte(du, dv)
    try:
        disco.guiado.activar(objetivo, _foto_en_pantalla, lambda foto: _medir_disco(foto, objetivo), mover, _pad_en_uso,
                             umbral_arcmin=umbral)
    except RuntimeError as e:
        return jsonify({"ok": False, "mensaje": str(e)}), 409
    log.info("Guiado por el disco de %s activado (corrige a partir de %.1f')", disco.NOMBRE[objetivo], umbral)
    voz.decir(f"Guiado por el disco del {disco.NOMBRE[objetivo]}")
    return jsonify({"ok": True, "estado": disco.guiado.estado()})


@app.route('/disco/estado')
def disco_estado():
    return jsonify(disco.guiado.estado())


# ───────────────────────── Guardar la foto de Cámara en la galería ─────────────────────────
CARPETA_CRUDOS_GALERIA = os.path.join("static", "fotos", "crudos")
_lock_guardar_foto = threading.Lock()


def guardar_foto_en_galeria():
    """
    Guarda en la galería la foto que se ve en Cámara: JPG revelado a resolución
    completa (con el recorte y el color del modo día) y firmado como los apilados, y
    su FITS en bruto en static/fotos/crudos/ (aparte, para que el editor no lo tome
    por un apilado). Devuelve un dict con ok, mensaje y archivo.
    """
    f = _foto_en_pantalla()
    if f is None:
        return {"ok": False, "mensaje": "No hay ninguna foto que guardar; haz una foto primero."}
    fecha = datetime.fromtimestamp(f.get("instante") or time.time())
    if f["modo"] in ("dia", "vivo"):
        objeto = foto_dia.OBJETIVOS.get(f.get("objetivo") or "", {}).get("nombre", "Foto de día")
        objeto = objeto[:1].upper() + objeto[1:]
    else:
        objeto = apilado.nombre_objeto_goto()
    base = f"{editor_fotos.nombre_archivo_objeto(objeto)}_{fecha:%Y%m%d_%H%M%S}"
    ruta_jpg = os.path.join("static", "fotos", base + ".jpg")
    ruta_crudo = os.path.join(CARPETA_CRUDOS_GALERIA, base + ".fits")
    if os.path.exists(ruta_jpg):
        return {"ok": True, "mensaje": f"Esta foto ya estaba en la galería ({base}.jpg).", "archivo": base + ".jpg"}
    os.makedirs(CARPETA_CRUDOS_GALERIA, exist_ok=True)

    exposicion = f.get("exposicion")
    if f["modo"] == "vivo":
        # Fotograma del modo en vivo: ya revelado a la resolución de la vista, sin FITS
        imagen = giro_camara.aplicar(f["imagen"], f["encuadre"]).copy()
        ruta_crudo = None
    elif f["modo"] == "dia":
        frame, prep = f.get("frame"), f.get("prep") or {}
        if frame is None:                      # foto suelta (o tras reiniciar): está en static/captura.fits
            with fits.open(RUTA_IMAGEN) as hdul:
                frame = np.asarray(hdul[0].data)
                patron = str(hdul[0].header.get("BAYERPAT") or "")
                exposicion = exposicion or hdul[0].header.get("EXPTIME")
            sensor_type = prep.get("sensor_type", foto_dia.sensor_type_de_patron(patron))
            import shutil
            shutil.copy(RUTA_IMAGEN, ruta_crudo)
        else:
            sensor_type = prep["sensor_type"]
            foto_dia.guardar_fits(frame, exposicion or 0, prep, f["binning"], ruta_crudo,
                                  extra=foto_dia.cabecera_revelado(f.get("objetivo"), f["campo"],
                                                                   {"negro": f.get("negro") or 0, "balance": f.get("balance")},
                                                                   f.get("instante")))
        imagen, _ = foto_dia.revelar(foto_dia.recortar_centro(frame, f["campo"]), sensor_type, f.get("objetivo"),
                                     balance=f.get("balance"), negro=f.get("negro"), aprender=False, ev=f.get("ev") or 0.0)
        imagen = giro_camara.aplicar(imagen, f["encuadre"])
    else:
        # Foto de noche: se revela en segundo plano con el procesado y el preset del apilado
        # (procesado.py a resolución completa, como el JPG de la galería de Apilado). Tarda
        # de medio minuto a unos minutos según el PC; la web sigue el progreso por
        # /foto/guardar-galeria/estado. Si ese procesado falla, se guarda con el revelado
        # rápido (STF) para que la foto no se pierda.
        if GUARDADO_FOTO["en_curso"]:
            return {"ok": False, "mensaje": "Ya se está guardando una foto; espera a que termine."}
        with fits.open(RUTA_IMAGEN) as hdul:
            datos_fits = np.asarray(hdul[0].data)
            patron = str(hdul[0].header.get("BAYERPAT") or "").strip().upper()
            exposicion = exposicion or hdul[0].header.get("EXPTIME") or hdul[0].header.get("EXPOSURE")
        imagen = None
        import shutil
        shutil.copy(RUTA_IMAGEN, ruta_crudo)

    meta = apilado.metadatos_firma(None, ruta_crudo, 1, float(exposicion or 0))
    meta.update({"objeto": objeto, "fecha": fecha})
    if f["modo"] in ("dia", "vivo"):
        # Sol, Luna o paisaje: el tipo sale del objetivo del modo día, no del GoTo
        meta["tipo"] = catalogo_offline.categoria_objeto(objeto)

    if imagen is not None:
        _firmar_y_guardar_en_galeria(imagen, ruta_jpg, meta, base)
        return {"ok": True, "mensaje": f"Guardada en la galería como {base}.jpg", "archivo": base + ".jpg"}
    return _revelar_noche_en_galeria(datos_fits, patron, ruta_jpg, meta, base, f["encuadre"])


def _firmar_y_guardar_en_galeria(imagen, ruta_jpg, meta, base):
    if meta["activa"]:
        imagen = firma.firmar(imagen, meta)
    cv2.imwrite(ruta_jpg, imagen, [cv2.IMWRITE_JPEG_QUALITY, 92])
    meta["firmada"] = bool(meta["activa"])
    firma.guardar_meta(ruta_jpg, meta)
    log.info("Foto guardada en la galería: %s", base)
    voz.decir("Foto guardada en la galería")


def _revelar_noche_en_galeria(datos_fits, patron, ruta_jpg, meta, base, encuadre=None):
    """
    Revela una foto de noche (FITS en bruto) en segundo plano con el procesado y el
    preset del apilado (procesado.py a resolución completa), la firma y la guarda en
    la galería. Tarda de medio minuto a unos minutos según el PC; la web sigue el
    progreso por /foto/guardar-galeria/estado. Si ese procesado falla, se guarda con
    el revelado rápido (STF) para que la foto no se pierda.
    """
    if GUARDADO_FOTO["en_curso"]:
        return {"ok": False, "mensaje": "Ya se está guardando una foto; espera a que termine."}
    GUARDADO_FOTO.update({"en_curso": True, "ok": None, "archivo": base + ".jpg",
                          "mensaje": "Revelando con el procesado del apilado..."})

    def _avisar(subfase, fraccion):
        GUARDADO_FOTO["mensaje"] = f"{FASES_REVELADO.get(subfase, subfase)}... {int(fraccion * 100)} %"

    def _fondo():
        aviso = ""
        try:
            try:
                imagen = revelar_foto_noche_apilado(datos_fits, patron, escala=1.0, progreso=_avisar, encuadre=encuadre)
            except Exception:
                log.exception("Falló el procesado del apilado sobre la foto; se guarda con el revelado rápido")
                imagen = giro_camara.aplicar(revelar_foto_noche(datos_fits, patron), encuadre or giro_camara.SIN_GIRO)
                aviso = " (falló el procesado del apilado; guardada con el revelado rápido)"
            _firmar_y_guardar_en_galeria(imagen, ruta_jpg, meta, base)
            GUARDADO_FOTO.update({"ok": True, "mensaje": f"Guardada en la galería como {base}.jpg{aviso}"})
        except Exception as e:
            log.exception("Error guardando la foto en la galería")
            GUARDADO_FOTO.update({"ok": False, "mensaje": f"Error al guardar: {e}"})
        finally:
            GUARDADO_FOTO["en_curso"] = False

    threading.Thread(target=_fondo, name="guardar-foto-galeria", daemon=True).start()
    return {"ok": True, "en_curso": True, "mensaje": GUARDADO_FOTO["mensaje"], "archivo": base + ".jpg"}


# Guardado en la galería de la foto de noche de Cámara (segundo plano) y revelado
# "como el apilado" de la foto en pantalla: la web sigue los dos por sus rutas /estado
GUARDADO_FOTO = {"en_curso": False, "ok": None, "mensaje": "", "archivo": ""}
REVELADO_FOTO = {"en_curso": False, "ok": None, "mensaje": "", "version": 0}
RUTA_IMAGEN_REVELADA = os.path.splitext(RUTA_IMAGEN)[0] + "_apilado.jpg"


@app.route('/foto/guardar-galeria', methods=['POST'])
def foto_guardar_galeria():
    if not _lock_guardar_foto.acquire(blocking=False):
        return jsonify({"ok": False, "mensaje": "Ya se está guardando una foto; espera un momento."}), 409
    try:
        resultado = guardar_foto_en_galeria()
    except Exception as e:
        log.exception("Error guardando la foto de Cámara en la galería")
        resultado = {"ok": False, "mensaje": f"Error al guardar: {e}"}
    finally:
        _lock_guardar_foto.release()
    return jsonify(resultado), (200 if resultado["ok"] else 409)


@app.route('/foto/guardar-galeria/estado')
def foto_guardar_galeria_estado():
    return jsonify(GUARDADO_FOTO)


@app.route('/foto/revelar-apilado', methods=['POST'])
def foto_revelar_apilado():
    """
    Vuelve a revelar la foto de noche que se ve en Cámara con el procesado y el preset de
    Apilado, a la escala de la vista previa del apilado, y la deja en captura_apilado.jpg
    (/imagen-revelada). El revelado rápido de captura.jpg no se toca: la web alterna entre
    los dos y la foto siguiente vuelve al rápido.
    """
    f = _foto_en_pantalla()
    if f is None or f.get("modo") != "noche" or not os.path.exists(RUTA_IMAGEN):
        return jsonify({"ok": False, "mensaje": "Solo para fotos de noche; haz una foto primero."}), 409
    if REVELADO_FOTO["en_curso"]:
        return jsonify({"ok": False, "mensaje": "Ya se está revelando; espera a que termine."}), 409
    REVELADO_FOTO.update({"en_curso": True, "ok": None, "mensaje": "Leyendo la foto..."})
    encuadre = f["encuadre"]

    def _avisar(subfase, fraccion):
        REVELADO_FOTO["mensaje"] = f"{FASES_REVELADO.get(subfase, subfase)}... {int(fraccion * 100)} %"

    def _fondo():
        inicio = time.time()
        try:
            with fits.open(RUTA_IMAGEN) as hdul:
                datos_fits = np.asarray(hdul[0].data)
                patron = str(hdul[0].header.get("BAYERPAT") or "").strip().upper()
            imagen = revelar_foto_noche_apilado(datos_fits, patron, escala=_escala_revelado_vista(), progreso=_avisar,
                                                encuadre=encuadre)
            guardar_jpg_web(RUTA_IMAGEN_REVELADA, imagen)
            REVELADO_FOTO.update({"ok": True, "version": REVELADO_FOTO["version"] + 1,
                                  "mensaje": f"Revelada como el apilado en {time.time() - inicio:.0f} s"})
        except Exception as e:
            log.exception("Error revelando la foto de Cámara como el apilado")
            REVELADO_FOTO.update({"ok": False, "mensaje": f"Error al revelar: {e}"})
        finally:
            REVELADO_FOTO["en_curso"] = False

    threading.Thread(target=_fondo, name="revelar-foto-apilado", daemon=True).start()
    return jsonify({"ok": True, "en_curso": True, "mensaje": REVELADO_FOTO["mensaje"]})


@app.route('/foto/revelar-apilado/estado')
def foto_revelar_apilado_estado():
    return jsonify(REVELADO_FOTO)


@app.route('/imagen-revelada')
def imagen_revelada():
    if not os.path.exists(RUTA_IMAGEN_REVELADA):
        return send_file("static/esperando.png", mimetype="image/png")
    response = send_file(RUTA_IMAGEN_REVELADA, mimetype='image/jpeg')
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

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


def goto_sincrono(nombre, ra, dec, fuente, centrar=True, cancelar=None, info=None, al_centrar=None,
                  espera_centrado_s=None):
    """
    GoTo que ESPERA a que la montura llegue y, si se pide, centra por plate solving y
    espera a que el centrado termine. Lo usan el recorrido por constelación y el plan
    de la noche (el GoTo de la página GoTo es asíncrono: responde al momento).
    Devuelve {"ok": False, "motivo": ...} si no se movió, o
    {"ok": True, "centrado": resultado del centrado (o None si no se centró), "detalle": texto}.
    `cancelar` (threading.Event) aborta el movimiento y el centrado; `al_centrar()` se
    llama cuando empieza el centrado (para que el plan cambie de fase).
    """
    ra, dec = float(ra), float(dec)
    if calcular_altitud(ra=ra, dec=dec) < float(ALTURA_MINIMA):
        return {"ok": False, "motivo": "por debajo de la altura mínima"}
    ULTIMO_GOTO.clear()
    ULTIMO_GOTO.update({"ra": ra, "dec": dec, "objeto": nombre, "fuente": fuente,
                        "hora": datetime.now().isoformat(timespec="seconds")})
    guardar_info_goto(nombre, info, ra, dec, fuente)
    voz.decir(f"Moviendo el telescopio a {nombre}")

    def _mover(tel):
        tel.Tracking = True
        ra_m, dec_m = coordenadas.a_montura(ra, dec)
        if getattr(tel, "CanSlewAsync", False):
            tel.SlewToCoordinatesAsync(ra_m, dec_m)
        else:
            tel.SlewToCoordinates(ra_m, dec_m)
        inicio = time.time()
        time.sleep(0.5)
        while tel.Slewing:
            if cancelar is not None and cancelar.is_set():
                try:
                    tel.AbortSlew()
                except Exception:
                    pass
                raise ErrorHardware("movimiento cancelado")
            if time.time() - inicio > 180:
                raise ErrorHardware("La montura no terminó el movimiento en 180 s")
            time.sleep(0.5)

    try:
        montura.ejecutar(_mover, timeout=200)
    except ErrorHardware as e:
        return {"ok": False, "motivo": f"error de montura: {e}"}
    if not centrar:
        return {"ok": True, "centrado": None, "detalle": ""}
    if es_de_dia():
        return {"ok": True, "centrado": None, "detalle": "de día no hay centrado (sin estrellas que resolver)"}
    time.sleep(asentamiento_montura())
    # Si una exposición está acabando, esperar a que la cámara quede libre
    fin = time.time() + 30
    while camara.ocupado and time.time() < fin:
        if cancelar is not None and cancelar.is_set():
            return {"ok": True, "centrado": "cancelado", "detalle": "cancelado"}
        time.sleep(0.5)
    try:
        centrado.trabajo_centrado.iniciar(camara, montura, ra, dec, objeto=nombre,
                                          calcular_altitud=calcular_altitud, altura_minima=float(ALTURA_MINIMA))
    except RuntimeError as e:
        return {"ok": True, "centrado": "error", "detalle": str(e)}
    if al_centrar:
        al_centrar()
    conf = centrado.leer_config()
    tope = espera_centrado_s or (conf["max_vueltas"] * (conf["exposicion"] + 60) + 120)
    inicio = time.time()
    while centrado.trabajo_centrado.estado()["en_curso"] and time.time() - inicio < tope:
        if cancelar is not None and cancelar.is_set():
            centrado.trabajo_centrado.cancelar()
        time.sleep(0.5)
    e = centrado.trabajo_centrado.estado()
    return {"ok": True, "centrado": e["resultado"] or ("error" if e["en_curso"] else ""), "detalle": e["detalle"]}


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
    motivo_plan = _plan_en_marcha()
    if motivo_plan:
        return jsonify({"error": motivo_plan})

    conf = _config_recorrido()
    r = goto_sincrono(nombre, ra, dec, "Recorrido por constelación",
                      centrar=conf["centrar"] and not camara.ocupado, espera_centrado_s=conf["espera_centrado_s"])
    if not r["ok"]:
        if r["motivo"].startswith("por debajo"):
            return jsonify({"skipped": True, "nombre": nombre})
        return jsonify({"error": f"❌ Error durante GOTO: {r['motivo']}"})
    if r.get("centrado") == "error":
        print(f"⚠️ Centrado omitido en el recorrido: {r.get('detalle')}")
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
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("modo en vivo", vivo_dia.trabajo_vivo), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo),
                            ("seguimiento", seguimiento.trabajo_seguimiento)):
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
        motivo_plan = _plan_en_marcha()
        if motivo_plan:
            return jsonify({"error": motivo_plan})

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

            # Al Sol (o muy cerca de él) solo se va si el usuario confirma que el filtro solar
            # está puesto: la interfaz muestra el aviso y repite la petición con filtro_solar=si
            aviso = aviso_solar(nombre_objeto, float(ra), float(dec))
            if aviso and request.form.get("filtro_solar") != "si":
                if aviso["es_sol"]:
                    texto = "Vas a apuntar el telescopio al Sol."
                else:
                    texto = f"{nombre_rotulo} está a solo {aviso['separacion_deg']}° del Sol."
                voz.decir(f"Atención. {texto} ¿Has puesto el filtro solar?")
                print(f"☀️ GoTo a {nombre_rotulo} pendiente de confirmar el filtro solar ({aviso})")
                return jsonify({"confirmar_filtro_solar": dict(aviso, objeto=nombre_rotulo, texto=texto)})
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
            de_dia = es_de_dia()

            def _al_llegar(nombre=nombre_rotulo, ra_obj=float(ra), dec_obj=float(dec), de_dia=de_dia):
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
                if de_dia:
                    # Al Sol (con filtro), o a lo que sea con el cielo claro: no hay estrellas
                    # que resolver, así que no se gasta una foto ni se anuncia un fallo
                    voz.decir(f"Telescopio apuntando a {nombre}. De día no hay centrado automático.")
                    print(f"ℹ️ Centrado tras el GOTO a {nombre} omitido: es de día, sin estrellas para resolver")
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
            nota_dia = "<br><small>☀️ De día no hay centrado automático: sin estrellas no hay plate solving</small>" if de_dia else ""
            return jsonify({
                "message": f"✅ Moviendo el telescopio a <b>{nombre_rotulo}</b><br>📍 RA: <code>{ra_str}</code> | DEC: <code>{dec_str}</code><br><small>🔎 Fuente: <b>{fuente_coords}</b></small>{nota_dia}",
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
        # El formulario puede llegar incompleto (petición manual, script, campo
        # vacío): nunca llamar a .encode() sobre None ni dejar escapar una traza.
        usuario = (request.form.get('usuario') or '').strip()
        clave = request.form.get('clave') or ''

        if not usuario or not clave:
            return render_template('login.html', error="Introduce usuario y contraseña"), 400

        hash_guardado = USUARIOS_HASH.get(usuario) or ''   # '' o None si no existe o está vacío en [Usuarios]
        valido = False
        if hash_guardado:
            try:
                valido = bcrypt.checkpw(clave.encode(), hash_guardado.encode())
            except ValueError:
                log.warning("Hash bcrypt inválido para el usuario %r en [Usuarios] de config.ini", usuario)

        if valido:
            session['usuario'] = usuario
            # La ficha del último GoTo (rótulo y nombre de archivo de la galería) solo se borra si
            # no hay nada en marcha: entrar desde otro móvil con el apilado o el plan corriendo
            # dejaba el rótulo en "Objeto"
            if not bucle.activo and not plan_sesion.trabajo_plan.en_curso:
                eliminar_info_goto()
            iniciar_conexion()  # ← aquí arrancamos Stellarium y telescopio tras login
            # 🔐 Verificar activación aquí
            if not validar_licencia():
                return redirect(url_for("activar"))

            return redirect(url_for('home'))

        return render_template('login.html', error="Credenciales incorrectas"), 401

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
    estado["plan"] = plan_sesion.trabajo_plan.estado()
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
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("modo en vivo", vivo_dia.trabajo_vivo), ("flats", flats.trabajo_flats), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo),
                            ("seguimiento", seguimiento.trabajo_seguimiento)):
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


# ───────────────────────── Calibración del color con algo blanco ─────────────────────────
def _balance_actual():
    """Balance de luz de día en uso, con de dónde sale (para la pantalla Calibración)."""
    b = foto_dia.leer_balance_luz_dia()
    if b is None:
        return dict(foto_dia.BALANCE_DEFECTO, origen="típico", fecha="", calibrado=False)
    return dict(b, calibrado=b.get("origen") == foto_dia.ORIGEN_BLANCO)


@app.route('/color/calibrar', methods=["POST"])
def color_calibrar():
    """Foto de algo blanco al Sol: su gris da el balance de luz de día (foto_dia.TrabajoBalanceBlanco)."""
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("modo en vivo", vivo_dia.trabajo_vivo), ("flats", flats.trabajo_flats), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("timelapse", foto_dia.trabajo_timelapse), ("alineación", alineacion.trabajo_alineacion),
                            ("seguimiento", seguimiento.trabajo_seguimiento)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    try:
        foto_dia.trabajo_balance_blanco.iniciar(camara, _dispositivos()["binning"])
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    voz.decir("Calibrando el color con el blanco", categoria="capturas")
    return jsonify({"ok": True})


@app.route('/color/estado')
def color_estado():
    return jsonify({"trabajo": foto_dia.trabajo_balance_blanco.estado(), "balance": _balance_actual()})


@app.route('/color/olvidar', methods=["POST"])
def color_olvidar():
    """Borra el balance guardado: se vuelve al típico hasta la próxima calibración (o la próxima Luna)."""
    ok = foto_dia.olvidar_balance_luz_dia()
    return jsonify({"ok": ok, "balance": _balance_actual()}), (200 if ok else 500)


# ───────────────────────── Modo en vivo de día (SDK nativo de la cámara) ─────────────────────────
@app.route('/vivo/iniciar', methods=["POST"])
def vivo_iniciar():
    """Imagen casi continua para centrar y enfocar de día (vivo_dia.py). JSON: {objetivo, campo (%)}."""
    datos = request.get_json(silent=True) or {}
    objetivo = str(datos.get("objetivo", "paisaje"))
    if objetivo not in foto_dia.OBJETIVOS:
        return jsonify({"ok": False, "error": "Objetivo desconocido"}), 400
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("flats", flats.trabajo_flats), ("centrado", centrado.trabajo_centrado),
                            ("foto continua", foto_continua.trabajo_continuo), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("timelapse", foto_dia.trabajo_timelapse), ("alineación", alineacion.trabajo_alineacion),
                            ("seguimiento", seguimiento.trabajo_seguimiento),
                            ("calibración del color", foto_dia.trabajo_balance_blanco)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    if not camara_sdk.es_sdk(_dispositivos()["driver_camara"]):
        return jsonify({"ok": False, "error": "El modo en vivo necesita la cámara por SDK: elige el driver "
                                              f"«{camara_sdk.NOMBRE_DRIVER}» en Configuración"}), 409
    # Seguimiento solar/lunar como en la foto de día; la web lo devuelve a sidéreo al salir del modo día
    seguimiento_montura = None
    conf = foto_dia.OBJETIVOS[objetivo]
    if conf["seguimiento"] and montura.esta_conectado() and foto_dia.poner_seguimiento(montura, conf["seguimiento"]):
        seguimiento_montura = conf["seguimiento"]
    try:
        vivo_dia.trabajo_vivo.iniciar(camara, objetivo, _campo_foto_dia(datos), compensacion=_compensacion_foto_dia)
    except (RuntimeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    voz.decir(f"En vivo: {conf['nombre']}", categoria="capturas")
    return jsonify({"ok": True, "seguimiento": seguimiento_montura})


@app.route('/vivo/parar', methods=["POST"])
def vivo_parar():
    vivo_dia.trabajo_vivo.parar()
    return jsonify({"ok": True})


@app.route('/vivo/estado')
def vivo_estado():
    return jsonify(vivo_dia.trabajo_vivo.estado())


@app.route('/vivo/imagen')
def vivo_imagen():
    """
    Último fotograma del modo en vivo (JPG pequeño); 204 si aún no hay ninguno. Con
    ?despues=<versión> espera (hasta 3 s) a que haya uno más nuevo que el que ya tiene la web.
    """
    try:
        despues = int(request.args["despues"]) if "despues" in request.args else None
    except ValueError:
        despues = None
    version, jpg = vivo_dia.trabajo_vivo.imagen(despues=despues)
    if jpg is None:
        return ("", 204)
    respuesta = app.response_class(jpg, mimetype="image/jpeg")
    respuesta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    respuesta.headers["X-Vivo-Version"] = str(version)
    return respuesta


@app.route('/timelapse/iniciar', methods=["POST"])
def timelapse_iniciar():
    """Serie de fotos de día: {"objetivo", "fotos", "intervalo"}."""
    datos = request.get_json(silent=True) or {}
    objetivo = str(datos.get("objetivo", "paisaje"))
    if objetivo not in foto_dia.OBJETIVOS:
        return jsonify({"ok": False, "error": "Objetivo desconocido"}), 400
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("modo en vivo", vivo_dia.trabajo_vivo), ("flats", flats.trabajo_flats),
                            ("centrado", centrado.trabajo_centrado), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("foto continua", foto_continua.trabajo_continuo),
                            ("seguimiento", seguimiento.trabajo_seguimiento)):
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
            instante = time.time()
            sin_girar, info = foto_dia.revelar(foto_dia.recortar_centro(frame, campo), prep["sensor_type"], objetivo, ev=ev)
            imagen, encuadre = giro_camara.girar(sin_girar)      # el giro de Cámara; el FITS queda sin girar
            guardar_jpg_web(RUTA_IMAGEN_WEB, imagen)
            foto_dia.registrar_vista(frame, objetivo, campo, binning, prep, exposicion, info, encuadre=encuadre, sin_girar=sin_girar)
            archivo = None
            if copia and copia.get("formato") == "fits":
                archivo = os.path.join(copia["carpeta"], copia["nombre"] + ".fits")
                foto_dia.guardar_fits(frame, exposicion, prep, binning, archivo,     # campo completo, como la foto suelta
                                      extra=foto_dia.cabecera_revelado(objetivo, campo, info, instante))
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
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("modo en vivo", vivo_dia.trabajo_vivo), ("flats", flats.trabajo_flats),
                            ("centrado", centrado.trabajo_centrado), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("timelapse", foto_dia.trabajo_timelapse), ("alineación", alineacion.trabajo_alineacion),
                            ("seguimiento", seguimiento.trabajo_seguimiento)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    seguimiento_montura = None
    if modo == "dia":
        conf = foto_dia.OBJETIVOS[objetivo]
        # Seguimiento solar/lunar como en la foto de día suelta; la web lo devuelve a sidéreo al salir del modo día
        if conf["seguimiento"] and montura.esta_conectado() and foto_dia.poner_seguimiento(montura, conf["seguimiento"]):
            seguimiento_montura = conf["seguimiento"]
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
    return jsonify({"ok": True, "seguimiento": seguimiento_montura, "estado": foto_continua.trabajo_continuo.estado()})


@app.route('/continuo/estado')
def continuo_estado():
    return jsonify(foto_continua.trabajo_continuo.estado())


@app.route('/continuo/parar', methods=["POST"])
def continuo_parar():
    foto_continua.trabajo_continuo.parar()
    return jsonify({"mensaje": "⏹️ Parando el modo continuo..."})


# ─── Seguimiento de objetos en movimiento (experimental) ─────────────────
# Aviones, satélites, la ISS: la cámara hace de "visión" por el SDK nativo y la
# montura corrige la velocidad de los ejes (ver seguimiento.py). Mientras dura,
# la cámara ASCOM de la app queda desconectada y el pad no puede mover.
_sim_seguimiento = {"servidor": None}


def _conf_seguimiento():
    cfg = configparser.ConfigParser()
    cfg.read("config.ini", encoding="utf-8")
    return seguimiento.leer_conf(cfg, focal_mm=_dispositivos()["focal"])


@app.route('/seguimiento')
def vista_seguimiento():
    import orientacion_camara
    conf = _conf_seguimiento()
    cal = seguimiento.elegir_calibracion(conf)
    return render_template('seguimiento.html', mostrar_volver=True, conf=conf,
                           calibracion=cal.como_dict(), orientacion=orientacion_camara.ultima(buscar=False),
                           sdk=orientacion_camara.leer_sdk(), signos=seguimiento.signos_manuales(),
                           estado=seguimiento.trabajo_seguimiento.estado(),
                           version=seguimiento.trabajo_seguimiento.version,
                           pad_url_mover="/seguimiento/mover", pad_url_parar="/seguimiento/soltar")


@app.route('/seguimiento/iniciar', methods=["POST"])
def seguimiento_iniciar():
    """JSON opcional: backend, exposicion_ms, ganancia, kp, ki, frecuencia_hz, signo_az, signo_alt."""
    datos = request.get_json(silent=True) or {}
    conf = _conf_seguimiento()
    if str(datos.get("backend", "")).lower() in ("ascom", "udp"):
        conf["backend"] = str(datos["backend"]).lower()
    for k in ("exposicion_ms", "ganancia", "kp", "ki", "frecuencia_hz"):
        try:
            if datos.get(k) not in (None, ""):
                conf[k] = int(datos[k]) if k == "ganancia" else float(datos[k])
        except (TypeError, ValueError):
            pass
    if "grabar" in datos:
        conf["grabar"] = bool(datos["grabar"])
    if "exposicion_auto" in datos:
        conf["exposicion_auto"] = bool(datos["exposicion_auto"])
    if "color" in datos:
        conf["color"] = bool(datos["color"])
        if conf["color"]:
            conf["binning"] = 1
    if str(datos.get("formato_video", "")).lower() in ("ser", "mp4", "ambos", "ser,mp4"):
        conf["formato_video"] = str(datos["formato_video"]).lower()
    if str(datos.get("grabar_desde", "")).lower() in ("inicio", "bloqueo"):
        conf["grabar_desde"] = str(datos["grabar_desde"]).lower()
    try:
        if int(datos.get("roi_px", 0)) in (1024, 1504, 3008):
            conf["roi_px"] = int(datos["roi_px"])
    except (TypeError, ValueError):
        pass
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("flats", flats.trabajo_flats),
                            ("centrado", centrado.trabajo_centrado), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("timelapse", foto_dia.trabajo_timelapse), ("alineación", alineacion.trabajo_alineacion),
                            ("foto continua", foto_continua.trabajo_continuo), ("modo en vivo", vivo_dia.trabajo_vivo)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    _pad_parar()
    cal = None                 # la elige el trabajo: la última astrometría o la empírica, la más reciente
    fab_cam = fab_mont = None
    cam_ascom, mont_ascom = camara, montura
    if conf["simulador"]:
        import simulador_seguimiento
        if _sim_seguimiento["servidor"] is not None:
            _sim_seguimiento["servidor"].parar()
        conf["udp_host"] = "127.0.0.1"
        fab_cam, fab_mont, _, _, cam_sim, servidor = simulador_seguimiento.fabricas(
            conf, udp=(conf["backend"] == "udp"), v_obj=conf["sim_v_obj"], rumbo_deg=conf["sim_rumbo"],
            angulo_deg=conf["sim_angulo"], retardo_montura_s=conf["sim_retardo_s"])
        _sim_seguimiento["servidor"] = servidor
        # La cámara simulada tiene su propio giro: calibración a juego
        cal = seguimiento.Calibracion.analitica(escala=cam_sim.escala, angulo=conf["sim_angulo"], origen="simulador")
        cam_ascom = mont_ascom = None
    try:
        seguimiento.trabajo_seguimiento.iniciar(cam_ascom, mont_ascom, conf, fabrica_camara=fab_cam, fabrica_montura=fab_mont,
                                                calibracion=cal, voz=voz,
                                                lat_lon=(LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO))
    except (RuntimeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "estado": seguimiento.trabajo_seguimiento.estado()})


@app.route('/seguimiento/parar', methods=["POST"])
def seguimiento_parar():
    seguimiento.trabajo_seguimiento.parar()
    return jsonify({"ok": True, "mensaje": "⏹️ Parando el seguimiento..."})


@app.route('/seguimiento/estado')
def seguimiento_estado():
    return jsonify(seguimiento.trabajo_seguimiento.estado())


@app.route('/seguimiento/objetivo', methods=["POST"])
def seguimiento_objetivo():
    """JSON {fx, fy}: punto pulsado en la vista previa como fracción del ancho y alto."""
    d = request.get_json(silent=True) or {}
    try:
        fx, fy = float(d["fx"]), float(d["fy"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "Punto no válido"}), 400
    if not seguimiento.trabajo_seguimiento.en_curso:
        return jsonify({"ok": False, "error": "El seguimiento no está en marcha"}), 409
    seguimiento.trabajo_seguimiento.objetivo(max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy)))
    return jsonify({"ok": True})


@app.route('/seguimiento/signos', methods=["POST"])
def seguimiento_signos():
    """JSON {az: ±1, alt: ±1}: inversión manual de cada eje (si al bloquear el objeto se aleja)."""
    d = request.get_json(silent=True) or {}
    try:
        az, alt = int(d.get("az", 1)), int(d.get("alt", 1))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Signos no válidos"}), 400
    seguimiento.guardar_signos_manuales(az, alt)
    cal = seguimiento.trabajo_seguimiento.calibracion
    if seguimiento.trabajo_seguimiento.en_curso and cal is not None:
        cal.signo_az, cal.signo_alt = (-1 if az == -1 else 1), (-1 if alt == -1 else 1)
    return jsonify({"ok": True, "signos": seguimiento.signos_manuales()})


@app.route('/seguimiento/mover', methods=["POST"])
def seguimiento_mover():
    """Pad de la página del seguimiento: JSON {vx, vy} en °/s (acimut, altura). Latido cada 0,5 s."""
    d = request.get_json(silent=True) or {}
    try:
        vx, vy = float(d.get("vx", 0)), float(d.get("vy", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Velocidades no válidas"}), 400
    if not seguimiento.trabajo_seguimiento.en_curso:
        return jsonify({"ok": False, "mensaje": "Pulsa Iniciar para mover con la vista en directo"}), 409
    seguimiento.trabajo_seguimiento.mover_manual(vx, vy)
    return jsonify({"ok": True})


@app.route('/seguimiento/soltar', methods=["POST"])
def seguimiento_soltar():
    seguimiento.trabajo_seguimiento.mover_manual(0.0, 0.0)
    return jsonify({"ok": True})


@app.route('/seguimiento/vista')
def seguimiento_vista():
    if os.path.exists(seguimiento.RUTA_VISTA):
        respuesta = send_file(ruta_absoluta(seguimiento.RUTA_VISTA), mimetype='image/jpeg')
    else:
        ok, buf = cv2.imencode(".jpg", np.zeros((200, 200, 3), dtype=np.uint8))
        respuesta = app.response_class(buf.tobytes(), mimetype='image/jpeg')
    respuesta.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return respuesta


@app.route('/seguimiento/calibrar/astrometria', methods=["POST"])
def seguimiento_calibrar_astrometria():
    """
    Relee la orientación de la cámara de la resolución más reciente que quede en
    disco (foto de prueba, alineación, centrado). Cada plate solving ya la guarda
    solo; esto sirve para recuperarla o para comprobar qué hay.
    """
    import orientacion_camara
    guardada = orientacion_camara.leer()
    candidatos = [r for r in orientacion_camara.INIS_CONOCIDOS if os.path.exists(r)]
    nueva = None
    for ruta in sorted(candidatos, key=os.path.getmtime, reverse=True):
        nueva = orientacion_camara.registrar(ruta, origen=f"resolución guardada ({os.path.basename(ruta)})")
        if nueva is not None:
            break
    o = nueva or guardada
    if o is None:
        return jsonify({"ok": False, "error": "No hay ninguna foto resuelta: basta cualquier plate solving (un GoTo con "
                                              "centrado, la alineación o el apilado) de esta noche o de otra"}), 409
    return jsonify({"ok": True, "orientacion": o,
                    "mensaje": f"Orientación de la cámara: eje x de la imagen a {o['angulo_x_deg']:.1f}° del acimut, "
                               f"{o['escala_arcsec']:.2f}\"/px (resolución del {o['instante_utc'][:16].replace('T', ' ')} UTC, "
                               f"{o.get('origen', '')}). Se usa al iniciar el seguimiento."})


@app.route('/seguimiento/orientacion-sdk', methods=["POST"])
def seguimiento_orientacion_sdk():
    """Compara una foto ASCOM y una del SDK de la misma escena (orientación de la imagen del SDK)."""
    if seguimiento.trabajo_seguimiento.en_curso:
        return jsonify({"ok": False, "error": "Para el seguimiento antes"}), 409
    if _conf_seguimiento()["simulador"]:
        return jsonify({"ok": False, "error": "Con el simulador no hay cámara real que comparar"}), 409
    if bucle.activo and not bucle.pausado:
        return jsonify({"ok": False, "error": "El apilado está capturando; páusalo o detenlo antes"}), 409
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("flats", flats.trabajo_flats),
                            ("centrado", centrado.trabajo_centrado), ("foto de día", foto_dia.trabajo_foto_dia),
                            ("timelapse", foto_dia.trabajo_timelapse), ("alineación", alineacion.trabajo_alineacion),
                            ("foto continua", foto_continua.trabajo_continuo), ("modo en vivo", vivo_dia.trabajo_vivo)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    try:
        d = seguimiento.comprobar_orientacion_sdk(camara, ganancia=_dispositivos()["ganancia"])
    except Exception as e:
        log.exception("Orientación SDK")
        return jsonify({"ok": False, "error": str(e)}), 500
    if d["fiable"]:
        msg = f"Orientación de la imagen del SDK: {d['transformacion']} (correlación {d['correlaciones'][0][1]:.2f})"
    else:
        msg = ("No se ha podido decidir (escena con poca estructura): apunta a algo con detalle, "
               "de día o una habitación iluminada, y repite")
    if d.get("bayer_en_binning") is not None:
        msg += " · el binning 2 " + ("conserva" if d["bayer_en_binning"] else "no conserva") + " el color"
    return jsonify({"ok": True, "resultado": d, "mensaje": msg})


@app.route('/seguimiento/calibrar/empirica', methods=["POST"])
def seguimiento_calibrar_empirica():
    if not seguimiento.trabajo_seguimiento.en_curso:
        return jsonify({"ok": False, "error": "Inicia el seguimiento primero (con un objeto fijo a la vista)"}), 409
    seguimiento.trabajo_seguimiento.calibrar_empirica()
    return jsonify({"ok": True})


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
    if _plan_en_marcha():
        # Con el plan en marcha no se reinicia nada (la web ya lo avisa antes de enviar)
        return redirect(url_for('vista_apilado'))
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
    if vivo_dia.trabajo_vivo.en_curso:
        return jsonify({"ok": False, "error": "El modo en vivo de Cámara tiene la cámara; páralo antes"}), 409
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
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("modo en vivo", vivo_dia.trabajo_vivo), ("centrado", centrado.trabajo_centrado),
                            ("alineación", alineacion.trabajo_alineacion), ("foto continua", foto_continua.trabajo_continuo),
                            ("seguimiento", seguimiento.trabajo_seguimiento)):
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
    import procesado
    config.read("config.ini")
    d = config["Dispositivos"]
    zoom = int(d.get("zoom_preview", 0))
    manual = procesado.encuadre_vista(config)       # sin cabecera: el giro y el espejo puestos a mano
    return jsonify({"zoom": zoom, "giro": manual["giro"], "espejo": manual["espejo"],
                    "norte": str(d.get("norte_arriba", "false")).strip().lower() == "true"})

@app.route("/guardar-config-zoom", methods=["POST"])
def guardar_config_zoom():
    try:
        data = request.get_json()
        zoom = min(90, max(0, int(float(data["zoom"]))))
        config.read("config.ini")
        config["Dispositivos"]["zoom_preview"] = str(zoom)
        # Giro del encuadre (grados en horario), espejo y norte arriba automático: opcionales
        if "giro" in data:
            giro = float(data["giro"])
            config["Dispositivos"]["giro_preview"] = f"{(giro if np.isfinite(giro) else 0.0) % 360.0:g}"
        if "espejo" in data:
            config["Dispositivos"]["espejo_preview"] = "true" if data["espejo"] else "false"
        if "norte" in data:
            config["Dispositivos"]["norte_arriba"] = "true" if data["norte"] else "false"
        with open("config.ini", "w") as configfile:
            config.write(configfile)
        return "✅ Zoom guardado correctamente"
    except Exception as e:
        return f"❌ Error al guardar zoom: {e}", 500




@app.route('/descargar-fits-apilado')
def descargar_fits_apilado():
    ruta = "static/resultado_apilado.fits"
    if os.path.exists(ruta):
        return send_file(ruta, as_attachment=True)
    else:
        return "❌ El archivo FITS aún no ha sido generado.", 404

# Guardado a resolución completa en segundo plano (tarda 20-30 s en 9 Mpx)
GUARDADO = {"en_curso": False, "mensaje": "", "archivo": "", "ok": None}


def guardar_apilado_en_galeria():
    """
    Guarda el apilado en la galería: el FITS se copia al momento (campo completo)
    y el JPG se procesa a resolución completa en segundo plano con el estilo y el
    recorte de zoom actuales. Devuelve el dict que la ruta /guardar-imagen-jpg
    manda a la web (con "codigo" HTTP); el plan de la noche lo llama directamente
    y espera el final con esperar_guardado() (GUARDADO).
    """
    if GUARDADO["en_curso"]:
        return {"ok": False, "error": "⏳ Ya hay un guardado en curso", "codigo": 409}
    origenFits = "static/resultado_apilado.fits"
    if not os.path.exists(origenFits):
        return {"ok": False, "error": "❌ Archivo FITS no encontrado.", "codigo": 404}

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
                encuadre = procesado.encuadre_vista(header=hdul[0].header)
            # Mismo encuadre que la vista previa (zona cubierta por todas las fotos,
            # zoom, giro y espejo); el FITS se guarda entero y sin girar
            balance = procesado.balance_estrellas(cubo, zona)      # color: con las estrellas del campo completo
            cubo, _ = procesado.girar_campo(cubo, encuadre["fraccion"], encuadre["giro"], zona, espejo=encuadre["espejo"])
            rgb8 = procesado.procesar(cubo, n, procesado.leer_config(), escala=1.0, balance=balance)
            imagen_bgr = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
            # Firma (logo, autor, objeto, tiempo, coordenadas). El FITS queda limpio y
            # el JSON hermano guarda los datos para el botón de compartir.
            meta = apilado.metadatos_firma(None, destinoFits, n, round(exp_total / n, 3) if n and exp_total else 0)
            if meta["activa"]:
                imagen_bgr = firma.firmar(imagen_bgr, meta)
            cv2.imwrite(destino, imagen_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
            meta["firmada"] = bool(meta["activa"])
            # El encuadre con que se guarda, para que el editor de fotos parta del mismo
            meta["encuadre"] = {"zoom": int(round((1.0 - encuadre["fraccion"]) * 100)), "giro": encuadre["giro"],
                                "espejo": encuadre["espejo"]}
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
    return {"ok": True, "mensaje": GUARDADO["mensaje"], "archivo": nombre_archivo, "en_curso": True, "codigo": 200}


@app.route('/guardar-imagen-jpg', methods=['POST'])
def guardar_imagen_jpg():
    r = guardar_apilado_en_galeria()
    codigo = r.pop("codigo", 200)
    return jsonify(r), codigo


def esperar_guardado(timeout=180):
    """Espera a que termine el guardado en la galería en segundo plano; devuelve GUARDADO."""
    fin = time.time() + timeout
    while GUARDADO["en_curso"] and time.time() < fin:
        time.sleep(1)
    return dict(GUARDADO)


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


def _reprocesar_preview_en_segundo_plano():
    """
    Regenera la vista previa con los ajustes actuales (zoom, estilo) sin capturar nada.
    Devuelve (ok, mensaje); si el apilador está ocupado no hace nada (la siguiente foto ya
    saldrá con los ajustes nuevos). Lo usan la ruta /apilado/reprocesar y el plan de la noche.
    """
    a = apilado.apilador_global
    if REPROCESADO["en_curso"] or (a is not None and a.procesando):
        return False, "El apilador está ocupado; espera a que termine"
    if a is None or not os.path.exists("static/resultado_apilado.fits"):
        return False, "No hay ningún apilado que reprocesar"
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
    return True, REPROCESADO["mensaje"]


@app.route('/apilado/reprocesar', methods=['POST'])
def apilado_reprocesar():
    """Lanza la regeneración de la vista previa con los ajustes actuales, sin capturar nada."""
    ok, mensaje = _reprocesar_preview_en_segundo_plano()
    if not ok:
        return jsonify({"ok": False, "mensaje": mensaje, "en_curso": REPROCESADO["en_curso"]}), 409
    return jsonify({"ok": True, "mensaje": mensaje, "en_curso": True})


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
    # Tipo de objeto (galaxia, nebulosa, cúmulo...) para filtrar: del JSON hermano si la
    # foto se guardó con él; las antiguas se deducen del nombre y el catálogo local
    tipos = {f: _tipo_foto_galeria(carpeta, f) for f in imagenes}
    conteo = {}
    for t in tipos.values():
        conteo[t] = conteo.get(t, 0) + 1
    orden = list(catalogo_offline.CATEGORIAS) + [SIN_TIPO]
    filtros = sorted(conteo.items(), key=lambda kv: orden.index(kv[0]) if kv[0] in orden else len(orden))
    lista_rastros = rastros.listar()
    n_meteoros = sum(1 for d in lista_rastros if d.get("tipo") == "meteoro")
    return render_template("galeria.html", imagenes=imagenes, con_fits=con_fits, tipos=tipos, filtros=filtros,
                           n_rastros=len(lista_rastros), n_meteoros=n_meteoros)


SIN_TIPO = "Sin tipo"


def _tipo_foto_galeria(carpeta, nombre):
    """Familia del objeto de una foto de la galería (ver catalogo_offline.categoria_objeto)."""
    base = os.path.splitext(nombre)[0]
    tipo = ""
    ruta_json = os.path.join(carpeta, base + ".json")
    if os.path.isfile(ruta_json):
        try:
            import json
            with open(ruta_json, "r", encoding="utf-8") as f:
                datos = json.load(f)
            tipo = catalogo_offline.categoria_objeto(datos.get("tipo") or "", datos.get("objeto") or "")
        except Exception:
            pass
    if not tipo:
        tipo = catalogo_offline.categoria_objeto("", editor_fotos.partes_nombre(nombre)[0].replace("_", " "))
    return tipo or SIN_TIPO


# ─── Rastros guardados (rastros.py): fotos que el apilado descartó por un rastro ───
# de avión, satélite o posible meteoro, para revisarlas y rescatar los meteoros.

def _rastro_valido(base):
    return isinstance(base, str) and re.fullmatch(r"rastro_\d{8}_\d{6}", base) is not None


@app.route("/galeria/rastros")
def galeria_rastros():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    lista = rastros.listar()
    conteo = {}
    for d in lista:
        conteo[d.get("tipo")] = conteo.get(d.get("tipo"), 0) + 1
    filtros = [(clave, info, conteo[clave]) for clave, info in rastros.TIPOS_RASTRO.items() if clave in conteo]
    filtros.sort(key=lambda f: f[0] != "meteoro")   # los meteoros, lo primero
    return render_template("rastros.html", rastros=lista, filtros=filtros)


@app.route("/galeria/rastros/borrar", methods=["POST"])
def galeria_rastros_borrar():
    """{"base": "rastro_..."} borra uno; {"menos_meteoros": true} borra aviones y satélites."""
    if 'usuario' not in session:
        return jsonify({"ok": False, "mensaje": "No autorizado"}), 403
    datos = request.get_json(silent=True) or {}
    if datos.get("menos_meteoros"):
        borrados = [d["base"] for d in rastros.listar() if d.get("tipo") != "meteoro"]
        for base in borrados:
            rastros.borrar(base)
        return jsonify({"ok": True, "borrados": borrados, "mensaje": f"{len(borrados)} rastros borrados"})
    base = datos.get("base")
    if not _rastro_valido(base):
        return jsonify({"ok": False, "mensaje": "Rastro no válido"}), 400
    rastros.borrar(base)
    return jsonify({"ok": True, "borrados": [base], "mensaje": "Rastro borrado"})


@app.route("/galeria/rastros/a-galeria", methods=["POST"])
def galeria_rastros_a_galeria():
    """Revela el FITS del rastro como una foto de la galería (en segundo plano, como Cámara)."""
    if 'usuario' not in session:
        return jsonify({"ok": False, "mensaje": "No autorizado"}), 403
    base = (request.get_json(silent=True) or {}).get("base")
    if not _rastro_valido(base):
        return jsonify({"ok": False, "mensaje": "Rastro no válido"}), 400
    carpeta = rastros.CARPETA_RASTROS
    ruta_fits_rastro = os.path.join(carpeta, base + ".fits")
    if not os.path.isfile(ruta_fits_rastro):
        return jsonify({"ok": False, "mensaje": "Este rastro se guardó sin FITS (había poco disco): solo queda la miniatura."}), 404
    try:
        import json
        with open(os.path.join(carpeta, base + ".json"), "r", encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        datos = {}
    objeto = rastros.TIPOS_RASTRO.get(datos.get("tipo"), rastros.TIPOS_RASTRO["satelite"])["objeto"]
    fecha = datetime.strptime(base[len("rastro_"):], "%Y%m%d_%H%M%S")
    nombre = f"{editor_fotos.nombre_archivo_objeto(objeto)}_{fecha:%Y%m%d_%H%M%S}"
    ruta_jpg = os.path.join("static", "fotos", nombre + ".jpg")
    if os.path.exists(ruta_jpg):
        return jsonify({"ok": True, "mensaje": f"Ya estaba en la galería ({nombre}.jpg).", "archivo": nombre + ".jpg"})
    if GUARDADO_FOTO["en_curso"]:
        return jsonify({"ok": False, "mensaje": "Ya se está guardando una foto; espera a que termine."}), 409
    # El FITS en bruto va con los de las fotos sueltas (se borra con la foto de la galería)
    os.makedirs(CARPETA_CRUDOS_GALERIA, exist_ok=True)
    ruta_crudo = os.path.join(CARPETA_CRUDOS_GALERIA, nombre + ".fits")
    import shutil
    shutil.copy2(ruta_fits_rastro, ruta_crudo)
    with fits.open(ruta_crudo) as hdul:
        datos_fits = np.asarray(hdul[0].data)
        patron = str(hdul[0].header.get("BAYERPAT") or "").strip().upper()
        exposicion = hdul[0].header.get("EXPTIME") or hdul[0].header.get("EXPOSURE") or datos.get("exposicion") or 0
    meta = apilado.metadatos_firma(None, ruta_crudo, 1, float(exposicion))
    meta.update({"objeto": objeto, "fecha": fecha, "tipo": catalogo_offline.categoria_objeto(objeto)})
    return jsonify(_revelar_noche_en_galeria(datos_fits, patron, ruta_jpg, meta, nombre))


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

        # FITS en bruto de una foto suelta de Cámara (static/fotos/crudos), si lo hay
        ruta_crudo = os.path.join(carpeta_fotos, "crudos", nombre_fits)
        if os.path.exists(ruta_crudo):
            os.remove(ruta_crudo)
            mensajes.append("FITS en bruto eliminado.")

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


@app.route("/galeria/editar/<nombre>/norte", methods=["POST"])
def galeria_editar_norte(nombre):
    """Giro y espejo que dejan el norte arriba y el este a la izquierda (plate solving del FITS; unos segundos)."""
    try:
        giro, espejo = editor_fotos.giro_norte(nombre)
        return jsonify({"ok": True, "giro": giro, "espejo": espejo})
    except editor_fotos.ErrorEditor as e:
        return jsonify({"ok": False, "mensaje": str(e)}), 409
    except Exception as e:
        log.exception("Error buscando el norte de la foto")
        return jsonify({"ok": False, "mensaje": f"No se pudo resolver: {e}"}), 500


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
        "zoom_preview": config["Dispositivos"].getint("zoom_preview"),
        "modo_apilado": apilado.leer_config_calidad()["modo"],    # mejores | incremental ([Apilado])
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
    # El modo de apilado va en [Apilado]; el apilador lo relee con cada foto
    if "modo_apilado" in data:
        if "Apilado" not in config:
            config["Apilado"] = {}
        modo = str(data["modo_apilado"]).strip().lower()
        config["Apilado"]["modo_apilado"] = modo if modo in ("mejores", "incremental") else "mejores"
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

# ─────────────────────────────────────────────────────────────────────────────
#  Plan de la noche (ver plan_sesion.py): lista de objetos que se fotografían solos
# ─────────────────────────────────────────────────────────────────────────────

def _deps_plan():
    """Las piezas de ScopePower que necesita el hilo del plan (inyectadas para poder probarlo en seco)."""
    from types import SimpleNamespace

    def detener_apilado():
        bucle.detener("Plan de la noche: siguiente objeto")
        pausar_apilado()
        if estado_captura.como_dict()["en_curso"]:
            estado_captura.cancelar.set()      # no esperar toda la exposición
        bucle.esperar_fin(90)

    def estado_apilado():
        e = apilado.obtener_estado_apilado()
        return {"total": e.get("total", 0), "procesando": e.get("procesando", False),
                "duracion_ultima_foto": e.get("duracion_ultima_foto")}

    def leer_dispositivos():
        cfg = configparser.ConfigParser()
        cfg.read("config.ini")
        d = cfg["Dispositivos"] if "Dispositivos" in cfg else {}
        return {"numero_maximo_imagenes": d.get("numero_maximo_imagenes", "20"),
                "zoom_preview": d.get("zoom_preview", "0"),
                "exposicion_captura": d.get("exposicion_captura", "10")}

    def exposicion_config():
        try:
            return float(leer_dispositivos()["exposicion_captura"])
        except ValueError:
            return 10.0

    def reiniciar():
        bucle.detener("Plan de la noche")
        reiniciar_apilado_global()

    return SimpleNamespace(
        lugar=lambda: (LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO),
        altura_general=lambda: float(ALTURA_MINIMA),
        exposicion_config=exposicion_config,
        goto_sincrono=goto_sincrono,
        arrancar_apilado=lambda usar_dark: _arrancar_apilado(True, usar_dark),
        detener_apilado=detener_apilado,
        estado_apilado=estado_apilado,
        guardar_galeria=guardar_apilado_en_galeria,
        esperar_guardado=esperar_guardado,
        reiniciar_apilado=reiniciar,
        anotar_objeto=lambda nombre, ra, dec, tipo: guardar_info_goto(nombre, {"type": tipo} if tipo else None, ra, dec, "Plan de la noche"),
        reprocesar_preview=_reprocesar_preview_en_segundo_plano,
        leer_dispositivos=leer_dispositivos,
        guardar_config=_guardar_en_config,
        calcular_altitud=lambda ra, dec: calcular_altitud(ra=ra, dec=dec),
        es_de_dia=es_de_dia,
        otro_trabajo_en_curso=_trabajos_en_curso,
        cancelar_centrado=lambda: centrado.trabajo_centrado.cancelar(),
        bucle=bucle,
        decir=voz.decir,
    )


def _resolver_nombre_plan(nombre):
    """
    Ficha para el plan: {nombre, ra_h, dec_deg, tipo, dinamico} o None. Misma cadena que el
    GoTo pero sin Stellarium: Luna y planetas (efemérides, se recalculan cada noche),
    catálogo local, cometas y asteroides del MPC y, por último, Internet.
    """
    nombre = " ".join(str(nombre or "").split())
    if not nombre:
        return None
    normalizado = reemplazar_numeros_en_texto(nombre)
    clave = quitar_tildes(normalizado.lower())
    if clave in ("sol", "sun"):
        raise ValueError("Al Sol no se va con el plan: usa el GoTo con el filtro solar puesto")
    if clave in plan_sesion.CUERPOS:
        d = plan_sesion.CUERPOS[clave]
        ra, dec = plan_sesion.coordenadas_dinamicas({"dinamico": d, "ra_h": 0, "dec_deg": 0, "nombre": nombre},
                                                    LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO, Time.now())
        return {"nombre": plan_sesion.NOMBRES_CUERPOS[d], "ra_h": ra, "dec_deg": dec,
                "tipo": "Luna" if d == "moon" else "Planeta", "dinamico": d}
    # El nombre tal como lo escribió el usuario (M31, Vega), salvo que llevara números en letras
    nombre_bonito = nombre if quitar_tildes(nombre.lower()) == clave else normalizado
    ficha = catalogo_offline.buscar(normalizado)
    if ficha:
        return {"nombre": nombre_bonito, "ra_h": float(ficha["ra"]), "dec_deg": float(ficha["dec"]),
                "tipo": ficha.get("type") or "", "dinamico": None}
    if cuerpos_menores.buscar(normalizado) is not None:
        ra, dec, info_cm = cuerpos_menores.coordenadas(normalizado, LATITUD_OBSERVATORIO, LONGITUD_OBSERVATORIO, ALTITUD_OBSERVATORIO)
        return {"nombre": info_cm["nombre"], "ra_h": float(ra), "dec_deg": float(dec),
                "tipo": "Cometa" if info_cm["tipo"] == "cometa" else "Asteroide", "dinamico": "mpc:" + info_cm["nombre"]}
    try:
        c = SkyCoord.from_name(normalizado)
        return {"nombre": nombre_bonito, "ra_h": float(c.ra.hour), "dec_deg": float(c.dec.deg), "tipo": "", "dinamico": None}
    except Exception:
        return None


@app.route('/plan')
def plan_ver():
    """Lista, ajustes, visibilidad de cada objeto esta noche y el cronograma previsto."""
    plan = plan_sesion.leer()
    t = plan_sesion.trabajo_plan
    deps = _deps_plan()
    try:
        vis, programa = t.calcular(plan, deps=deps)
    except Exception as e:
        log.exception("Error calculando el plan de la noche")
        return jsonify({"error": str(e), "ajustes": plan["ajustes"], "objetos": plan["objetos"],
                        "estado": t.estado(), "resumen": plan_sesion.resumen(plan)}), 500
    pj = plan_sesion.programa_json(programa)
    previstos = {tr["id"]: tr for tr in pj["tramos"]}
    no_prog = {n["id"]: n for n in pj["no_programados"]}
    objetos = []
    for o in plan["objetos"]:
        d = dict(o)
        d["vis"] = vis.json(o["id"])
        if o["id"] in previstos:
            d["previsto"] = dict(previstos[o["id"]], programado=True)
        elif o["id"] in no_prog:
            d["previsto"] = {"programado": False, "motivo": no_prog[o["id"]]["motivo"],
                             "fotos_posibles": no_prog[o["id"]]["fotos_posibles"]}
        else:
            d["previsto"] = None
        objetos.append(d)
    altura_efectiva = max(float(plan["ajustes"]["altura_minima"]), float(ALTURA_MINIMA))
    return jsonify({
        "ajustes": plan["ajustes"], "objetos": objetos, "programa": pj,
        "noche_desde": plan_sesion._hhmm(vis.inicio), "noche_hasta": plan_sesion._hhmm(vis.fin),
        "noche_desde_ms": plan_sesion._ms(vis.inicio), "noche_hasta_ms": plan_sesion._ms(vis.fin),
        "ahora_ms": int(time.time() * 1000), "de_noche": vis.de_noche,
        "altura_efectiva": altura_efectiva, "altura_general": float(ALTURA_MINIMA),
        "tiempo_por_foto_s": round(plan_sesion.tiempo_por_foto(deps.exposicion_config(), plan.get("medido")), 1),
        "exposicion_config": deps.exposicion_config(),
        "estado": t.estado(), "version_lista": plan_sesion.version(), "resumen": plan_sesion.resumen(plan),
    })


@app.route('/plan/estado')
def plan_estado():
    return jsonify(plan_sesion.trabajo_plan.estado())


@app.route('/plan/objetos', methods=['POST'])
def plan_anadir():
    """{nombre, ra?, dec?, tipo?, fotos?, zoom?, exposicion?}: sin ra/dec se resuelve el nombre."""
    datos = request.get_json(silent=True) or {}
    nombre = " ".join(str(datos.get("nombre", "")).split())
    if not nombre:
        return jsonify({"ok": False, "error": "Falta el nombre del objeto"}), 400
    try:
        if datos.get("ra") is not None and datos.get("dec") is not None:
            clave = quitar_tildes(nombre.lower())
            if clave in ("sol", "sun"):
                raise ValueError("Al Sol no se va con el plan: usa el GoTo con el filtro solar puesto")
            dinamico = plan_sesion.CUERPOS.get(clave)   # Luna y planetas: efemérides de cada noche
            ficha = {"nombre": plan_sesion.NOMBRES_CUERPOS[dinamico] if dinamico else nombre,
                     "ra_h": float(datos["ra"]), "dec_deg": float(datos["dec"]),
                     "tipo": str(datos.get("tipo") or ""), "dinamico": dinamico}
        else:
            ficha = _resolver_nombre_plan(nombre)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not ficha:
        return jsonify({"ok": False, "error": f"No encuentro «{nombre}» en los catálogos"}), 404
    try:
        o, dup = plan_sesion.anadir(ficha["nombre"], ficha["ra_h"], ficha["dec_deg"], ficha.get("tipo", ""),
                                    ficha.get("dinamico"), fotos=datos.get("fotos"), zoom=datos.get("zoom"),
                                    exposicion=datos.get("exposicion"), prioritario=bool(datos.get("prioritario")))
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": f"Datos no válidos: {e}"}), 400
    return jsonify({"ok": True, "objeto": o, "duplicado": dup, "total": plan_sesion.resumen()["total"]})


@app.route('/plan/objetos/<id_>', methods=['POST'])
def plan_editar(id_):
    datos = request.get_json(silent=True) or {}
    try:
        o = plan_sesion.editar(id_, **{k: datos[k] for k in ("fotos", "zoom", "exposicion", "nombre", "prioritario") if k in datos})
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": f"Datos no válidos: {e}"}), 400
    if o is None:
        return jsonify({"ok": False, "error": "Ese objeto ya no está en el plan"}), 404
    return jsonify({"ok": True, "objeto": o})


@app.route('/plan/objetos/<id_>/borrar', methods=['POST'])
def plan_borrar(id_):
    t = plan_sesion.trabajo_plan
    actual = (t.estado().get("objeto") or {}).get("id")
    if t.en_curso and actual == id_:
        t.saltar()
    plan_sesion.borrar(id_)
    return jsonify({"ok": True, "total": plan_sesion.resumen()["total"]})


@app.route('/plan/objetos/<id_>/estado', methods=['POST'])
def plan_cambiar_estado(id_):
    datos = request.get_json(silent=True) or {}
    try:
        o = plan_sesion.cambiar_estado(id_, datos.get("estado"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if o is None:
        return jsonify({"ok": False, "error": "Ese objeto ya no está en el plan"}), 404
    return jsonify({"ok": True, "objeto": o})


@app.route('/plan/vaciar', methods=['POST'])
def plan_vaciar():
    if plan_sesion.trabajo_plan.en_curso:
        return jsonify({"ok": False, "error": "Detén el plan antes de vaciarlo"}), 409
    plan_sesion.vaciar()
    return jsonify({"ok": True})


@app.route('/plan/reiniciar-resultados', methods=['POST'])
def plan_reiniciar_resultados():
    if plan_sesion.trabajo_plan.en_curso:
        return jsonify({"ok": False, "error": "Detén el plan antes de reiniciar los resultados"}), 409
    plan_sesion.reiniciar_resultados()
    return jsonify({"ok": True})


@app.route('/plan/ajustes', methods=['POST'])
def plan_ajustes():
    datos = request.get_json(silent=True) or {}
    try:
        ajustes = plan_sesion.guardar_ajustes(datos)
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": f"Ajuste no válido: {e}"}), 400
    return jsonify({"ok": True, "ajustes": ajustes})


@app.route('/plan/messier', methods=['POST'])
def plan_messier():
    r = plan_sesion.messier_completo()
    return jsonify({"ok": True, **r, "total": plan_sesion.resumen()["total"]})


@app.route('/plan/importar', methods=['POST'])
def plan_importar():
    """{"plan": {...exportado por ScopePower...}} o {"nombres": "M31\\nM42\\n..."} (un nombre por línea)."""
    datos = request.get_json(silent=True) or {}
    if isinstance(datos.get("plan"), dict):
        r = plan_sesion.importar(datos["plan"], _resolver_nombre_plan)
    elif datos.get("nombres"):
        r = plan_sesion.importar(str(datos["nombres"]), _resolver_nombre_plan)
    else:
        return jsonify({"ok": False, "error": "No hay nada que importar: un plan de ScopePower o una lista de nombres"}), 400
    return jsonify({"ok": True, **r, "total": plan_sesion.resumen()["total"]})


@app.route('/plan/exportar')
def plan_exportar():
    import json as _json
    datos = _json.dumps(plan_sesion.exportar(), ensure_ascii=False, indent=1).encode("utf-8")
    nombre = f"plan_{datetime.now().strftime('%Y%m%d')}.json"
    return send_file(io.BytesIO(datos), mimetype="application/json", as_attachment=True, download_name=nombre)


@app.route('/plan/iniciar', methods=['POST'])
def plan_iniciar():
    motivo = _sin_espacio_en_disco()
    if motivo:
        return jsonify({"ok": False, "error": motivo}), 409
    if bucle.activo:
        return jsonify({"ok": False, "error": "Hay un apilado en marcha: detenlo (y guárdalo si quieres) antes de empezar el plan"}), 409
    try:
        plan_sesion.trabajo_plan.iniciar(_deps_plan())
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "estado": plan_sesion.trabajo_plan.estado()})


@app.route('/plan/pausar', methods=['POST'])
def plan_pausar():
    t = plan_sesion.trabajo_plan
    if not t.en_curso:
        return jsonify({"ok": False, "error": "El plan no está en marcha"}), 409
    t.pausar()
    return jsonify({"ok": True, "estado": t.estado()})


@app.route('/plan/reanudar', methods=['POST'])
def plan_reanudar():
    t = plan_sesion.trabajo_plan
    if not t.en_curso:
        return jsonify({"ok": False, "error": "El plan no está en marcha"}), 409
    motivo = _sin_espacio_en_disco()
    if motivo:
        return jsonify({"ok": False, "error": motivo}), 409
    t.reanudar()
    return jsonify({"ok": True, "estado": t.estado()})


@app.route('/plan/detener', methods=['POST'])
def plan_detener():
    t = plan_sesion.trabajo_plan
    if not t.en_curso:
        return jsonify({"ok": False, "error": "El plan no está en marcha"}), 409
    t.detener()
    return jsonify({"ok": True, "estado": t.estado()})


@app.route('/plan/saltar', methods=['POST'])
def plan_saltar():
    t = plan_sesion.trabajo_plan
    if not t.en_curso:
        return jsonify({"ok": False, "error": "El plan no está en marcha"}), 409
    t.saltar()
    return jsonify({"ok": True, "estado": t.estado()})


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


# ─── Instancia única ─────────────────────────────────────────────────────────
# En Windows, dos servidores Werkzeug pueden escuchar A LA VEZ en el mismo puerto
# (SO_REUSEADDR): la segunda copia de ScopePower no falla al arrancar, las dos
# lanzan sus hilos (apilado, disco, plan de la noche pisando config.ini) y el
# navegador habla con una u otra sin criterio. Por eso, antes de arrancar nada,
# se reserva el puerto con un socket SIN SO_REUSEADDR (con él ligado, ningún
# otro proceso puede ligarse). Se suelta justo antes de app.run.
CODIGO_SALIDA_YA_EN_MARCHA = 4


def _reservar_puerto_web(puerto):
    """Socket ligado al puerto (cerrarlo justo antes de app.run) o None si está ocupado."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", puerto))
        return s
    except OSError:
        s.close()
        return None


def _quien_ocupa_el_puerto(puerto):
    """'scopepower' si responde una web de Flask/Werkzeug, 'otro' si responde otra cosa,
    'nadie' si no contesta (puerto en un estado raro: se deja que Flask lo intente)."""
    import http.client
    try:
        con = http.client.HTTPConnection("127.0.0.1", puerto, timeout=3)
        con.request("GET", "/login")
        servidor = con.getresponse().getheader("Server", "") or ""
        con.close()
    except OSError:
        return "nadie"
    except Exception:
        return "otro"
    return "scopepower" if "Werkzeug" in servidor else "otro"


def _comprobar_instancia_unica(puerto):
    """Devuelve el socket de reserva, o sale del proceso si ScopePower ya está en marcha."""
    reserva = _reservar_puerto_web(puerto)
    if reserva is not None:
        return reserva
    quien = _quien_ocupa_el_puerto(puerto)
    if quien == "nadie":
        log.warning("El puerto %d aparece ocupado pero nadie responde: se sigue arrancando", puerto)
        return None
    if quien == "scopepower":
        log.warning("ScopePower ya está en marcha en este equipo (puerto %d): esta segunda copia se cierra", puerto)
        print(f"\nAVISO: ScopePower ya está en marcha en este equipo: http://127.0.0.1:{puerto}\n"
              "    Esta segunda copia se cierra; usa la que ya estaba abierta.\n", flush=True)
        voz.decir("ScopePower ya estaba en marcha. Esta segunda copia se cierra.")
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{puerto}/")
        except Exception:
            pass
        voz.esperar(timeout=8)
        sys.exit(CODIGO_SALIDA_YA_EN_MARCHA)
    log.error("El puerto %d lo está usando otro programa: ScopePower no puede arrancar", puerto)
    print(f"\nERROR: El puerto {puerto} lo está usando otro programa. Ciérralo y vuelve a arrancar ScopePower.\n", flush=True)
    voz.decir(f"El puerto {puerto} está ocupado por otro programa. ScopePower no puede arrancar.")
    voz.esperar(timeout=8)
    sys.exit(1)


if __name__ == '__main__':
    # Antes de arrancar hilos ni tocar config.ini: ¿ya hay un ScopePower en este equipo?
    _reserva_puerto = _comprobar_instancia_unica(PUERTO_WEB)
    os.makedirs(ruta_absoluta("capturas"), exist_ok=True)
    os.makedirs(ruta_absoluta("static"), exist_ok=True)
    # Objetos anotados: lo que dejó puesto el usuario la última vez ([Apilado] objetos_anotados)
    apilado.OBJETOS_ANOTADOS = config["Apilado"].get("objetos_anotados", "false").strip().lower() == "true" \
        if "Apilado" in config else False
    iniciar_hilo_apilado(ruta_absoluta("capturas"), alinear=True)
    # Espacio en disco: aviso por voz si baja del umbral de Configuración y pausa con menos de 1 GB
    espacio_disco.vigilante.iniciar(voz, al_quedar_sin_espacio=_pausar_por_disco_lleno)
    # Si la app se cerró con el plan de la noche a medias, devolver a config.ini sus valores
    plan_sesion.restaurar_pendiente(_guardar_en_config)
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
    if _reserva_puerto is not None:
        _reserva_puerto.close()      # soltar la reserva justo antes de que Flask abra el puerto
    app.run(host='0.0.0.0', port=PUERTO_WEB, threaded=True)

