# herramientas/cambiar_password.py
#
# Cambia (o crea) la contraseña de un usuario de ScopePower en config.ini.
#
#   python herramientas\cambiar_password.py                 (pregunta usuario y contraseña)
#   python herramientas\cambiar_password.py admin nueva123  (sin preguntar)
#
# Hay que reiniciar la app después: los usuarios se leen al arrancar.

import os
import sys
import getpass
import configparser

import bcrypt

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUTA = os.path.join(RAIZ, "config.ini")

usuario = sys.argv[1] if len(sys.argv) > 1 else input("Usuario [admin]: ").strip() or "admin"
if len(sys.argv) > 2:
    clave = sys.argv[2]
else:
    clave = getpass.getpass("Nueva contraseña: ")
    if clave != getpass.getpass("Repite la contraseña: "):
        print("No coinciden.")
        sys.exit(1)
if not clave:
    print("La contraseña no puede estar vacía.")
    sys.exit(1)

cfg = configparser.ConfigParser()
cfg.read(RUTA)                     # config.ini está en la codificación del sistema
if "Usuarios" not in cfg:
    cfg["Usuarios"] = {}
cfg["Usuarios"][usuario.lower()] = bcrypt.hashpw(clave.encode(), bcrypt.gensalt()).decode()
with open(RUTA, "w") as f:
    cfg.write(f)
print(f"Contraseña de '{usuario.lower()}' guardada en config.ini. Reinicia ScopePower para que haga efecto.")
