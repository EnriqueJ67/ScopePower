from flask import Flask, request, jsonify, render_template, render_template_string, send_file
from flask import session, redirect, url_for

from unidecode import unidecode
import win32com.client
import pythoncom
import subprocess
import time
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
from AutoStrechSTF import apply_stf_color, debayer_rgb, miniatura_cfa_rgb
from ScopePower_ObjetosAnotados import resolver_y_generar_imagen_con_objetos

from datetime import datetime
import cv2
from apilado import aplicar_estirado_mtf
import winreg
import logging
from registro import configurar_logging
log = configurar_logging()   # fichero rotativo logs/scopepower.log + consola; print() también va al log
from hardware import (TrabajadorCOM, ErrorHardware, CapturaCancelada,
                      capturar_frame, estado_captura)
import darks
import secretos
import centrado
import voz

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
RUTA_IMAGEN_PNG = os.path.splitext(RUTA_IMAGEN)[0] + ".png" # config["General"]["RUTA_IMAGEN_PNG"]
RUTA_WCS = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.wcs"
RUTA_INI = os.path.splitext(RUTA_IMAGEN)[0] + "_mono.ini"
ruta_stellarium = config["General"].get("RUTA_STELLARIUM", "")

ALTURA_MINIMA = config["General"]["ALTURA_MINIMA"]

DRIVER_CAMARA = config["Dispositivos"]["DRIVER_CAMARA"]
DRIVER_TELESCOPIO = config["Dispositivos"]["DRIVER_TELESCOPIO"]
BINNING = int(config["Dispositivos"]["BINNING_CAMARA"])
EXPOSICION = float(config["Dispositivos"]["EXPOSICION_CAPTURA"])
FOCAL_LEN = float(config["Dispositivos"]["FOCAL_LEN"])
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

def estado_licencia():
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
    # Actualiza el flag
    apilado.OBJETOS_ANOTADOS = bool(data.get("objetos", False))
    return jsonify({"ok": True})

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
    for nombre, trabajo in (("darks", darks.trabajo_darks), ("centrado", centrado.trabajo_centrado)):
        if trabajo.en_curso:
            return jsonify({"ok": False, "error": f"Espera a que termine el trabajo de {nombre}"}), 409
    if nuevo:
        bucle.detener("Reiniciado")
        reiniciar_apilado_global()
    reanudar_apilado()
    bucle.iniciar(usar_dark=usar_dark)
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
    try:
        tel.Tracking = True
    except Exception as e:
        print(f"⚠️ No se pudo activar el seguimiento al conectar: {e}")
    voz.decir("Telescopio conectado")

    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if cfg.get("Montura", "limpiar_sync_al_conectar", fallback="true").lower() == "true":
        ok, msg = limpiar_sincronizaciones(tel)
        voz.decir("Sincronizaciones anteriores borradas" if ok
                  else "No se han podido borrar las sincronizaciones anteriores")

montura = TrabajadorCOM("montura", lambda: DRIVER_TELESCOPIO, al_conectar=_activar_seguimiento)
camara = TrabajadorCOM("cámara", lambda: DRIVER_CAMARA, al_conectar=lambda cam: voz.decir("Cámara conectada"))

# Objetivo del último GOTO (ra en horas, dec en grados): es lo que usa el
# centrado automático como referencia, no "donde la montura cree que está".
ULTIMO_GOTO = {}

# Miniatura de la última toma individual (static/ultima_captura.jpg) y su
# versión, para que la web la refresque solo cuando haya una nueva.
RUTA_ULTIMA_CAPTURA = ruta_absoluta("static/ultima_captura.jpg")
ULTIMA_CAPTURA = {"version": 0, "hora": "", "exposicion": 0.0, "dark": False}

# Bucle de captura del apilado en el servidor (ver bucle_captura.py). Se
# define aquí y se conecta a capturar_y_guardar más abajo, cuando exista.
from bucle_captura import BucleCaptura
bucle = BucleCaptura(
    capturar=lambda usar_dark: capturar_y_guardar(apilar=True, usar_dark=usar_dark,
                                                  generar_preview=False, descripcion="Apilado"),
    camara=camara,
    otros_trabajos=(darks.trabajo_darks, centrado.trabajo_centrado),
)


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

    if any(request.path.startswith(r) for r in rutas_publicas):
        return  # Permitir rutas públicas

    # Verificar sesión iniciada
    if 'usuario' not in session:
        return redirect(url_for('login'))

    # Si la licencia está expirada, solo permitimos activar
    if estado_licencia() == "expirado":
        return redirect(url_for('activar'))



def iniciar_conexion():
    lanzar_stellarium_si_no_esta_activo()
    conectar_telescopio()

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

def eliminar_info_goto():
    ruta = "static/info_objeto_goto.txt"
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("ℹ️ No hay datos del objeto GOTO disponibles en esta sesión.\n")
    print("📄 info_objeto_goto.txt reiniciado.")

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

        flat_data = np.where(flat_data == 0, 1, flat_data)  # evitar división por cero
        media_flat = np.mean(flat_data)
        #median_flat=np.median(flat_data)
        imagen_corr = imagen_fits.astype(np.float32) / flat_data * media_flat
        imagen_corr = np.clip(imagen_corr, 0, 65535).astype(np.uint16)

        print(f"📸 Flat maestro aplicado correctamente: {ruta_flat}")
        return imagen_corr, True

    except Exception as e:
        print(f"❌ Error al aplicar flat: {e}")
        return imagen_fits, False


import csv

import requests


def estado_stellarium():
    try:
        url = "http://localhost:8090/api/objects/info?name=Polaris&format=json"
        r = requests.get(url, timeout=2)
        if r.status_code == 200 and "found" in r.json():
            print("✅ Stellarium está activo y responde.")
            return True
        else:
            print("⚠️ Stellarium está en ejecución pero no responde como se espera.")
            return False
    except Exception as e:

        print(f"❌ Stellarium no responde. Intentando lanzarlo: {e}")
        lanzar_stellarium_si_no_esta_activo()
        # Darle unos segundos para arrancar antes de verificar de nuevo
        time.sleep(3)
        return False

def lanzar_stellarium_si_no_esta_activo():
    try:
        # Verificar si ya está en ejecución
        tareas = subprocess.check_output('tasklist', shell=True, encoding='cp1252')  # ← Aquí está el cambio
        if "stellarium.exe" in tareas.lower():
            print("ℹ️ Stellarium ya está en ejecución.")
            return

        ruta_stellarium = config["General"].get("RUTA_STELLARIUM", "")
        if not os.path.exists(ruta_stellarium):
            print(f"❌ Stellarium no encontrado en: {ruta_stellarium}")
            return

        # Lanzar Stellarium en segundo plano
        #subprocess.Popen([ruta_stellarium], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.startfile(ruta_stellarium)
        print("🚀 Stellarium lanzado automáticamente.")
        time.sleep(3)

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

        # ✅ Guardar info en TXT
        with open("static/info_objeto_goto.txt", "w", encoding="utf-8") as f:
            f.write(f"Nombre: {info.get('name', '')}\n")
            f.write(f"Tipo: {info.get('type', '')}\n")
    
            constelacion = info.get("constellation") or info.get("iauConstellation") or "Desconocida"
            f.write(f"Constelación: {constelacion}\n")
    
            mag = info.get("vmag") or info.get("mag") or info.get("vmage")
            if mag is not None:
                f.write(f"Magnitud: {mag:.2f}\n")
            if info.get("alt") is not None:
                f.write(f"Altitud: {info.get('alt'):.2f}°\n")
            if info.get("az") is not None:
                f.write(f"Azimut: {info.get('az'):.2f}°\n")
            if info.get("distance") is not None:
                f.write(f"Distancia: {info.get('distance'):.2f} AU\n")
            if info.get("phase") is not None:
                f.write(f"Fase: {info.get('phase') * 100:.1f}%\n")
            if info.get("illumination") is not None:
                f.write(f"Iluminación: {info.get('illumination') * 100:.1f}%\n")

            # ⏱️ Ahora añadimos la fecha y hora UTC al final
            f.write(f"\n📅 Fecha y hora UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

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


# Ruta para capturar una imagen con la cámara ASCOM
import numpy as np
from astropy.io import fits

def capturar_y_guardar(apilar=False, usar_dark=True, generar_preview=True, descripcion="Captura"):
    """
    Expone, calibra (dark/flat), escribe static/captura.fits y, si `apilar`,
    una copia en capturas/ que recoge el apilador. Devuelve un dict:
      {"ok": True, "mensaje": ..., "ruta": ...}  o
      {"ok": False, "mensaje": ..., "cancelada": bool}
    No usa `request`: sirve tanto para la ruta web como para el bucle del servidor.
    """
    # Si la cámara ya está exponiendo, no encolar otra captura detrás
    if camara.ocupado:
        return {"ok": False, "mensaje": "⏳ Ya hay una captura en curso", "ocupada": True}

    nuevo_config = configparser.ConfigParser()
    nuevo_config.read("config.ini")
    EXPOSICION = float(nuevo_config["Dispositivos"].get("exposicion_captura", 10))

    def _exponer(cam):
        print(f"📸 Iniciando exposición de {EXPOSICION} segundos...")
        voz.decir(f"Empieza exposición de {EXPOSICION:.0f} segundos", categoria="capturas")
        frame = capturar_frame(cam, EXPOSICION, BINNING, descripcion=descripcion)
        print("✅ Exposición finalizada.")
        voz.decir("Exposición terminada", categoria="capturas")
        return frame, cam.SensorType, cam.PixelSizeX, cam.PixelSizeY

    try:
        imagen_fits, sensor_type, pixel_size_x, pixel_size_y = camara.ejecutar(
            _exponer, timeout=EXPOSICION + 90)
    except CapturaCancelada:
        voz.decir("Captura cancelada")
        return {"ok": False, "mensaje": "⏹️ Captura cancelada", "cancelada": True}
    except ErrorHardware as e:
        voz.decir("Error de cámara")
        return {"ok": False, "mensaje": f"Error: {e}"}

    try:
        ra_actual, dec_actual = montura.ejecutar(
            lambda tel: (tel.RightAscension, tel.Declination), timeout=10)
    except ErrorHardware as e:
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
        hdu.header['DATE-OBS'] = time.strftime("%Y%m%d_%H%M%S")
        hdu.header['DARKCORR'] = ('T' if dark_aplicado else 'F', 'Dark master aplicado')
        if dark_aplicado:
            hdu.header['DARKFILE'] = (os.path.basename(ruta_dark), 'Archivo dark utilizado')
        hdu.header['FLATCORR'] = ('T' if flat_aplicado else 'F', 'Flat master aplicado')
        if flat_aplicado:
            hdu.header['FLATFILE'] = (os.path.basename(ruta_flat), 'Archivo flat utilizado')

        hdu.writeto(RUTA_IMAGEN, overwrite=True)
        ruta_guardada = RUTA_IMAGEN

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
                                   "exposicion": EXPOSICION, "dark": dark_aplicado})
        except Exception as e:
            log.warning("No se pudo generar la miniatura de la captura: %s", e)

        if not generar_preview:
            return {"ok": True, "mensaje": f"Captura de {EXPOSICION:.0f} s guardada", "ruta": ruta_guardada,
                    "dark": dark_aplicado}

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

        # Guardar PNG a resolución completa (cv2 espera BGR)
        if imagen_mtf.ndim == 3:
            imagen_png = cv2.cvtColor(imagen_mtf, cv2.COLOR_RGB2BGR)
        else:
            imagen_png = imagen_mtf
        cv2.imwrite(RUTA_IMAGEN_PNG, imagen_png)

        return {"ok": True, "mensaje": f"Captura de {EXPOSICION:.0f} s guardada", "ruta": ruta_guardada,
                "dark": dark_aplicado}

    except Exception as e:
        log.exception("Error al procesar la captura")
        return {"ok": False, "mensaje": f"❌ Error al capturar la imagen: {e}"}


# Función para capturar imagen y mostrarla en la web
@app.route('/capturar-imagen')
def capturar_imagen():
    apilar = request.args.get("apilar", "0") == "1"
    usar_dark = request.args.get("dark", "1") != "0"   # ?dark=0: no restar dark aunque exista
    resultado = capturar_y_guardar(apilar=apilar, usar_dark=usar_dark)
    if not resultado["ok"]:
        if resultado.get("ocupada"):
            return resultado["mensaje"], 409
        return resultado["mensaje"]
    if apilar:
        return resultado["mensaje"]
    return render_template('imagen.html', mostrar_volver=True)

@app.route('/imagen-capturada')
def imagen_capturada():
    return send_file(RUTA_IMAGEN_PNG, mimetype='image/png')
    
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

    coordenadas = extraer_coordenadas_ini(RUTA_INI)
    if coordenadas is None:
        print("⚠️ ASTAP falló. Intentando con Astrometry.net...")
        coordenadas = platesolve_astrometry(RUTA_IMAGEN_MONO)

        if coordenadas is None:
            return render_template('corregir_posicion.html', resultado="❌ ASTAP y Astrometry.net no pudieron resolver la imagen.", mostrar_volver=True)


    ra_resuelto, dec_resuelto = coordenadas
    ra_resuelto = float(ra_resuelto) / 15.0  # Convertimos RA de grados a horas

    altitud = calcular_altitud(ra_resuelto, dec_resuelto)

    if altitud < int(ALTURA_MINIMA):
        return render_template('corregir_posicion.html', resultado="⚠️ Advertencia: El objeto está por debajo de altura mínima. Proceso cancelado.", mostrar_volver=True)
    
    def _sincronizar(tel):
        tel.SyncToCoordinates(ra_resuelto, dec_resuelto)
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
            ra, dec = montura.ejecutar(lambda tel: (tel.RightAscension, tel.Declination), timeout=10)
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


@app.route('/centrado/estado')
def centrado_estado():
    return jsonify(centrado.trabajo_centrado.estado())


@app.route('/centrado/cancelar', methods=["POST"])
def centrado_cancelar():
    centrado.trabajo_centrado.cancelar()
    return jsonify({"mensaje": "⏹️ Cancelando centrado..."})

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

        objeto = reemplazar_numeros_en_texto(objeto)
        nombre_objeto = objeto.lower()

        try:
            coords = None
            fuente_coords = ""
            altitud = None
            info = {}
            # Primero intentar con Stellarium
            if estado_stellarium():
                info = obtener_coords_desde_stellarium(objeto)
                if info and "ra" in info and "dec" in info:
                    ra = info["ra"]
                    dec = info["dec"]
                    alt = info.get("alt", None)
                    altitud = alt
                    fuente_coords = "Stellarium (prioritario)"
                    coords = (ra, dec)
                else:
                    info = None  # ← Así forzamos fallback a métodos alternativos
            
            # Si Stellarium no está disponible o no encontró el objeto
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

            ULTIMO_GOTO.clear()
            ULTIMO_GOTO.update({"ra": float(ra), "dec": float(dec), "objeto": objeto,
                                "fuente": fuente_coords, "hora": datetime.now().isoformat(timespec="seconds")})

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
                if getattr(tel, "CanSlewAsync", False):
                    tel.SlewToCoordinatesAsync(ra, dec)
                else:
                    tel.SlewToCoordinates(ra, dec)
                # Esperar hasta que comience a moverse (máx. 5 s)
                for _ in range(10):
                    if tel.Slewing:
                        break
                    time.sleep(0.5)

            # Lanzar movimiento en el hilo de la montura sin esperar a que termine
            voz.decir(f"Moviendo el telescopio a {objeto}")
            futuro = montura.ejecutar_async(_mover_telescopio)
            futuro.add_done_callback(
                lambda f: print(f"❌ Error al mover el telescopio: {f.exception()}") if f.exception() else None)

            # Avisar por voz cuando la montura termine el movimiento (sondeo aparte,
            # sin ocupar el hilo de la montura durante el GOTO)
            def _avisar_llegada(nombre=objeto):
                inicio = time.time()
                time.sleep(3)
                while time.time() - inicio < 180:
                    try:
                        if not montura.ejecutar(lambda tel: bool(tel.Slewing), timeout=5):
                            voz.decir(f"Telescopio apuntando a {nombre}")
                            return
                    except ErrorHardware:
                        return
                    time.sleep(1)
            threading.Thread(target=_avisar_llegada, name="aviso-goto", daemon=True).start()

            # Responder inmediatamente
            return jsonify({
                "message": f"✅ Moviendo el telescopio a <b>{objeto}</b><br>📍 RA: <code>{ra_str}</code> | DEC: <code>{dec_str}</code><br><small>🔎 Fuente: <b>{fuente_coords}</b></small>",
                "extra_info": info
            })


        except Exception as e:
            return jsonify({"error": f"Error durante GOTO: {e}"})

    return render_template("goto.html", mostrar_volver=True, palabra_clave=PALABRA_CLAVE_GOTO)

@app.route("/estado_telescopio")
def estado_telescopio():
    if not montura.esta_conectado():
        return jsonify({"slewing": False})
    try:
        slewing = montura.ejecutar(lambda tel: bool(tel.Slewing), timeout=5)
        return jsonify({"slewing": slewing})
    except ErrorHardware as e:
        return jsonify({"slewing": False, "error": str(e)})

@app.route("/info-wikipedia", methods=["POST"])
def info_wikipedia():
    raw_objeto = request.form.get("objeto", "").strip()
    if not raw_objeto:
        return jsonify({"error": "Debe proporcionar un nombre de objeto."})

    import re, wikipedia
    wikipedia.set_lang("es")

    # 1) Normalizamos: quitamos espacios letra→número para hacer "M42" de "M 42"
    sin_espacios = re.sub(r'([A-Za-z])\s+(?=\d)', r'\1', raw_objeto)

    # 2) Reemplazamos números en texto ("trece"→"13"), si aplica
    candidato1 = reemplazar_numeros_en_texto(sin_espacios)

    # 3) Generamos alternativa con espacio ("M106"→"M 106")
    candidato2 = re.sub(r'([A-Za-z])(?=\d)', r'\1 ', candidato1)

    resumen = None
    ultima_excepcion = None

    for nombre in (candidato1, candidato2):
        try:
            # intentar página y resumen
            _ = wikipedia.page(nombre)
            resumen = wikipedia.summary(nombre)
            break
        except wikipedia.exceptions.DisambiguationError as e:
            # si hay ambigüedad, tomamos la primera opción
            try:
                opcion = e.options[0]
                resumen = wikipedia.summary(opcion)
                break
            except Exception as e2:
                ultima_excepcion = e2
        except wikipedia.exceptions.PageError as e:
            # no existe esa forma — lo guardamos y probamos la siguiente
            ultima_excepcion = e
        except Exception as e:
            # otro error inesperado: salimos del bucle
            ultima_excepcion = e
            break

    if not resumen:
        # si no encontró con ninguno de los dos formatos
        resumen = f"No se encontró información sobre '{raw_objeto}'."

    return jsonify({"wikipedia_info": resumen})

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
    disponible = estado_stellarium()
    return jsonify({"disponible": disponible})

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
    }


@app.route('/darks/estado')
def darks_estado():
    """Trabajo de darks en curso + datos del último dark para la exposición actual."""
    conf = _config_darks()
    return jsonify({
        "trabajo": darks.trabajo_darks.estado(),
        "ultimo": darks.info_dark(conf["exposicion"]),
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
    try:
        darks.trabajo_darks.iniciar(camara, conf["exposicion"], conf["binning"], n)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "n": n, "exposicion": conf["exposicion"]})


@app.route('/procesar-dark', methods=["POST"])
def procesar_dark():
    """Compatibilidad con el formulario antiguo: lanza el trabajo y vuelve a dark.html."""
    conf = _config_darks()
    try:
        darks.trabajo_darks.iniciar(camara, conf["exposicion"], conf["binning"], conf["numero_darks"])
    except RuntimeError as e:
        return render_template("dark.html", mensaje=f"❌ {e}", mostrar_volver=True)
    return render_template("dark.html", mostrar_volver=True)


@app.route("/config-mtf")
def obtener_config_mtf():
    try:
        nuevo_config = configparser.ConfigParser()
        nuevo_config.read("config.ini")
        shadows = float(nuevo_config["Dispositivos"].get("MTF_SHADOWS", 0.02))
        highlights = float(nuevo_config["Dispositivos"].get("MTF_HIGHLIGHTS", 0.98))
        midtone = float(nuevo_config["Dispositivos"].get("MTF_MIDTONE", 0.4))
        return jsonify({"shadows": shadows, "highlights": highlights, "midtone": midtone})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/guardar-config-mtf", methods=["POST"])
def guardar_config_mtf():
    try:
        data = request.get_json()
        config.read("config.ini")
        config["Dispositivos"]["MTF_SHADOWS"] = str(data["shadows"])
        config["Dispositivos"]["MTF_HIGHLIGHTS"] = str(data["highlights"])
        config["Dispositivos"]["MTF_MIDTONE"] = str(data["midtone"])
        with open("config.ini", "w") as configfile:
            config.write(configfile)
        return "✅ Parámetros guardados correctamente"
    except Exception as e:
        return f"❌ Error al guardar: {e}", 500

@app.route("/config-zoom")
def obtener_config_zoom():
    config.read("config.ini")
    zoom = int(config["Dispositivos"].get("zoom_preview", 0))
    return jsonify({"zoom": zoom})

@app.route("/guardar-config-zoom", methods=["POST"])
def guardar_config_zoom():
    try:
        data = request.get_json()
        config.read("config.ini")
        config["Dispositivos"]["zoom_preview"] = str(data["zoom"])
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

@app.route('/guardar-imagen-jpg', methods=['POST'])
def guardar_imagen_jpg():
    try:
        # Leer info del objeto
        nombre_objeto = "Objeto"
        ruta_info = "static/info_objeto_goto.txt"
        if os.path.exists(ruta_info):
            with open(ruta_info, "r", encoding="utf-8") as f:
                for linea in f:
                    if linea.lower().startswith("nombre:"):
                        nombre_objeto = linea.split(":", 1)[1].strip()
                        break

        nombre_objeto = re.sub(r'[^\w\-]', '_', nombre_objeto)  # Sanitizar
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_archivo = f"{nombre_objeto}_{timestamp}.jpg"
        nombre_archivo_fits = f"{nombre_objeto}_{timestamp}.fits"

        # Ruta destino correcta para galería
        carpeta_destino = os.path.join("static", "fotos")
        os.makedirs(carpeta_destino, exist_ok=True)

        origen = "static/resultado_apilado_preview.jpg"
        destino = os.path.join(carpeta_destino, nombre_archivo)

        origenFits = "static/resultado_apilado.fits"
        destinoFits = os.path.join(carpeta_destino, nombre_archivo_fits)

        if os.path.exists(origen) and os.path.exists(origenFits):
            import shutil
            shutil.copy(origen, destino)
            shutil.copy(origenFits, destinoFits)
            return jsonify({"mensaje": f"✅ Imagen guardada como: {nombre_archivo} y FITS guardado como: {nombre_archivo_fits}"})

        elif not os.path.exists(origen):
            return jsonify({"error": "❌ Imagen JPG no encontrada."}), 404
        else:
            return jsonify({"error": "❌ Archivo FITS no encontrado."}), 404

    except Exception as e:
        return jsonify({"error": f"❌ Error al guardar: {e}"}), 500

@app.route('/galeria')

def galeria():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    carpeta = "static/fotos"
    if not os.path.exists(carpeta):
        os.makedirs(carpeta)

    imagenes = sorted([f for f in os.listdir(carpeta) if f.lower().endswith(".jpg")])
    return render_template("galeria.html", imagenes=imagenes)

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

        return jsonify({"ok": True, "mensaje": "✅ " + " ".join(mensajes)})
    except Exception as e:
        return jsonify({"error": f"❌ Error al eliminar: {e}"})

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
        "focal_len": config["Dispositivos"].getfloat("focal_len"),
        "color_intensity": config["Dispositivos"].getfloat("color_intensity"),
        "zoom_preview": config["Dispositivos"].getint("zoom_preview")
    })

@app.route("/guardar-config-datos", methods=["POST"])
def guardar_config_datos():
    data = request.get_json()
    config.read("config.ini")
    for clave, valor in data.items():
        if clave in config["Dispositivos"]:
            config["Dispositivos"][clave] = str(valor)
    with open("config.ini", "w") as f:
        config.write(f)
    return "✅ Configuración guardada correctamente"

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
        "{% extends 'base.html' %}{% block content %}"
        "<h2>❌ Error inesperado</h2><p>{{ error }}</p>"
        "<p style='color:#aaa'>Los detalles están en logs/scopepower.log</p>{% endblock %}",
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
    iniciar_hilo_apilado(ruta_absoluta("capturas"), alinear=True)
    voz.decir("ScopePower iniciado. Servidor listo.")
    # threaded=True: cada petición en su hilo. El acceso a la montura y a la
    # cámara está serializado en hardware.py, así que la web sigue respondiendo
    # (estado, parar montura, cancelar) mientras hay una exposición en curso.
    app.run(host='0.0.0.0', port=5000, threaded=True)

