# herramientas/comprobar_equipo.py
#
# Comprueba que el PC tiene todo lo que ScopePower necesita y lo resume en
# pantalla. Lo ejecuta instalar_scopepower.bat al final, y se puede lanzar a
# mano:  python herramientas\comprobar_equipo.py
#
# Sale con código 0 si lo imprescindible está, 1 si falta algo.

import os
import sys
import importlib
import configparser

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(RAIZ)

OK, FALTA, AVISO = "  [OK]   ", "  [FALTA]", "  [AVISO]"
faltan_imprescindibles = []


def linea(estado, texto, extra=""):
    print(f"{estado} {texto}" + (f"  ->  {extra}" if extra else ""))


def falta(texto, extra="", imprescindible=True):
    linea(FALTA, texto, extra)
    if imprescindible:
        faltan_imprescindibles.append(texto)


print("\n=== Python y librerías ===")
print(f"  Python {sys.version.split()[0]} en {sys.executable}")
for modulo, paquete in [("numpy", "numpy"), ("cv2", "opencv-python"), ("astropy", "astropy"),
                        ("astroquery", "astroquery"), ("flask", "flask"), ("unidecode", "Unidecode"),
                        ("win32com.client", "pywin32"), ("matplotlib", "matplotlib"), ("wikipedia", "wikipedia"),
                        ("bcrypt", "bcrypt"), ("requests", "requests"), ("watchdog", "watchdog"),
                        ("PIL", "Pillow"), ("photutils", "photutils")]:
    try:
        m = importlib.import_module(modulo)
        linea(OK, f"{paquete} {getattr(m, '__version__', '')}".strip())
    except Exception as e:
        falta(paquete, f"pip install {paquete}", imprescindible=(paquete != "photutils"))

print("\n=== Configuración ===")
if os.path.exists("config.ini"):
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    linea(OK, "config.ini")
    ruta_astap = cfg.get("General", "ruta_astap", fallback="").replace("\\\\", "\\")
    ruta_stellarium = cfg.get("General", "ruta_stellarium", fallback="").replace("\\\\", "\\")
    driver_tel = cfg.get("Dispositivos", "driver_telescopio", fallback="")
    driver_cam = cfg.get("Dispositivos", "driver_camara", fallback="")
else:
    falta("config.ini", "copia config.example.ini a config.ini y rellénalo")
    ruta_astap, ruta_stellarium, driver_tel, driver_cam = r"C:\Program Files\Astap\astap.exe", "", "", ""
if os.path.exists("secretos.ini"):
    linea(OK, "secretos.ini")
else:
    linea(AVISO, "secretos.ini no existe (se creará al arrancar; pon ahí la contraseña FTP si la usas)")

print("\n=== Programas externos ===")
sys.path.insert(0, RAIZ)
import instalar_astap   # busca también en Program Files y en la carpeta donde lo instala el asistente
astap = instalar_astap.estado(ruta_astap)
if astap["ok"]:
    linea(OK, f"ASTAP con catálogo de estrellas {', '.join(c.upper() for c in astap['catalogos'])} ({astap['ruta']})")
    if os.path.normcase(astap["ruta"]) != os.path.normcase(ruta_astap):
        linea(AVISO, f"config.ini apunta a {ruta_astap or '(nada)'}: el asistente de configuración lo corrige al terminar")
elif astap["programa"]:
    falta("Catálogo de estrellas de ASTAP", "instálalo desde el asistente de configuración de la web (o la D50 de https://www.hnsky.org/astap.htm)")
else:
    falta("ASTAP", "instálalo desde el asistente de configuración de la web (Configuración > Asistente) o desde https://www.hnsky.org/astap.htm")
if ruta_stellarium and os.path.exists(ruta_stellarium):
    linea(OK, "Stellarium")
else:
    linea(OK, "Stellarium no instalado (no hace falta: el GoTo usa el catálogo local)")
for ruta in (r"C:\SynScanPro\SynScanPro\SynScanPro.exe", r"C:\SynScanPro\SynScanPro.exe"):
    if os.path.exists(ruta):
        linea(OK, f"SynScan Pro ({ruta})")
        break
else:
    linea(AVISO, "SynScan Pro no encontrado en C:\\SynScanPro (necesario para la montura SynScan)")

print("\n=== ASCOM ===")
try:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\ASCOM") as k:
        version = winreg.QueryValueEx(k, "PlatformVersion")[0]
    linea(OK, f"ASCOM Platform {version}")
except Exception:
    falta("ASCOM Platform", "instala la 7.x desde https://ascom-standards.org")

try:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    perfil = win32com.client.Dispatch("ASCOM.Utilities.Profile")
    for tipo, driver in (("Telescope", driver_tel), ("Camera", driver_cam)):
        registrados = {str(kv.Key): str(kv.Value) for kv in perfil.RegisteredDevices(tipo)}
        if driver and driver in registrados:
            linea(OK, f"Driver de {tipo.lower()} '{driver}' ({registrados[driver]})")
        elif driver:
            falta(f"Driver de {tipo.lower()} '{driver}'", f"registrados: {', '.join(registrados) or 'ninguno'}")
        else:
            linea(AVISO, f"Sin driver de {tipo.lower()} en config.ini; registrados: {', '.join(registrados) or 'ninguno'}")
except Exception as e:
    falta("Lectura del perfil ASCOM", str(e))

print("\n=== Voz ===")
try:
    import win32com.client
    sapi = win32com.client.Dispatch("SAPI.SpVoice")
    voces = [sapi.GetVoices().Item(i) for i in range(sapi.GetVoices().Count)]
    es = [v.GetDescription() for v in voces if str(v.GetAttribute("Language")).upper().endswith("0A")]
    if es:
        linea(OK, f"Voz en español: {es[0]}")
    else:
        linea(AVISO, "Sin voz en español (Configuración de Windows > Hora e idioma > Voz > Añadir voces > Español)")
except Exception as e:
    linea(AVISO, f"SAPI no disponible: {e}")

print()
if faltan_imprescindibles:
    print("FALTA: " + "; ".join(faltan_imprescindibles))
    sys.exit(1)
print("Todo lo imprescindible está instalado.")
