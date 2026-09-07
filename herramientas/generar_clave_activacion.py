# herramientas/generar_clave_activacion.py
#
# Genera la clave de activación de ScopePower para otro PC. En ese PC, la
# página /activar muestra un identificador en Base64; se pega aquí y se
# obtiene la clave. El secreto se lee de secretos.ini (activacion_secreto)
# de ESTA carpeta: debe ser el mismo que usa la app instalada en el otro PC.
#
#   python herramientas\generar_clave_activacion.py

import os
import sys
import base64
import hashlib
import configparser

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
cfg = configparser.ConfigParser()
cfg.read(os.path.join(RAIZ, "secretos.ini"), encoding="utf-8")
secreto = cfg.get("Secretos", "activacion_secreto", fallback="")
if not secreto:
    print("No hay activacion_secreto en secretos.ini (arranca la app una vez para que lo cree).")
    sys.exit(1)

print("Generador de claves de activación de ScopePower")
b64 = input("Pega el identificador en Base64 que muestra la página /activar del otro PC:\n").strip()
try:
    identificador = base64.b64decode(b64).decode()
except Exception as e:
    print(f"Identificador no válido: {e}")
    sys.exit(1)
print("\nClave de activación:\n")
print(hashlib.sha256((identificador + secreto).encode()).hexdigest())
