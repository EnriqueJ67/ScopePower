# perfil_ascom.py
#
# Lo que la app le pregunta a ASCOM.Utilities por COM desde los hilos de las peticiones web:
# los drivers registrados (el «perfil» de ASCOM, para los desplegables de Configuración y del
# asistente) y el selector («Chooser») que se abre en la pantalla del PC.
#
# Tiene módulo propio porque estas llamadas NO pueden coincidir en dos hilos. El 23-nov-2026
# una copia recién instalada se cerró sola con una violación de acceso (código 0xC0000005,
# módulo OLEAUT32.dll): el asistente pide a la vez la lista de monturas y la de cámaras
# (Promise.all en asistente.js), Flask atiende cada petición en su hilo y los dos hilos
# crearon y recorrieron el perfil a la vez. pywin32, al envolver cada objeto COM, le pregunta
# sus tipos (ITypeInfo.GetTypeAttr, win32com.client.__WrapDispatch); con dos hilos dentro la
# primera vez que el proceso usa ASCOM.Utilities, Windows falla dentro de OLEAUT32 y se lleva
# el proceso entero: no es una excepción de Python y ningún try la para. Pasa pocas veces
# (3 cierres en 184 arranques de prueba; ninguno en 144 con el candado), y solo con el
# asistente: Configuración pide las dos listas una detrás de otra.
#
# Lo último que quedó en el log aquel día fue la carga de los SDK de las cámaras, pero no
# tenían nada que ver: el otro hilo siguió trabajando los cuatro segundos que Windows tardó en
# recoger el informe del fallo.
#
# Solución: un candado. Mientras un hilo está con ASCOM.Utilities, el siguiente espera.
# /ascom-drivers lo mantiene también mientras busca las cámaras por SDK (camara_sdk), porque
# la búsqueda de webcams usa el mismo mecanismo (WMI con pywin32) y los SDK de los fabricantes
# no prometen aguantar dos búsquedas a la vez. El selector solo lo tiene mientras se crea: con
# el diálogo abierto en la pantalla del PC las listas se siguen pudiendo pedir.
#
# La montura y la cámara no pasan por aquí: cada una tiene su hilo propio (hardware.py).

import threading

import pythoncom
import win32com.client

# Reentrante: /ascom-drivers lo toma para toda la petición y drivers_registrados() otra vez dentro
CANDADO = threading.RLock()


def _crear(progid):
    """Objeto COM de ASCOM.Utilities en el hilo que llama (las pruebas lo cambian por uno falso)."""
    pythoncom.CoInitialize()
    return win32com.client.Dispatch(progid)


def _leer_registrados(tipo):
    perfil = _crear("ASCOM.Utilities.Profile")
    return [{"progid": str(kv.Key), "name": str(kv.Value)} for kv in perfil.RegisteredDevices(tipo)]


def drivers_registrados(tipo):
    """
    Drivers ASCOM registrados de un tipo ("Telescope", "Camera"...), ordenados por nombre:
    [{"progid", "name"}]. Lanza la excepción de COM si ASCOM no está instalado.
    """
    with CANDADO:
        # En una función aparte: los objetos COM se sueltan antes de dejar el candado
        drivers = _leer_registrados(tipo)
    drivers.sort(key=lambda d: d["name"].lower())
    return drivers


def _preparar_selector(tipo):
    selector = _crear("ASCOM.Utilities.Chooser")
    selector.DeviceType = tipo
    return selector.Choose          # pywin32 resuelve aquí el método; la llamada es ya solo el diálogo


def elegir_driver(tipo, actual=""):
    """
    Abre el selector de ASCOM EN LA PANTALLA DEL PC con `actual` marcado y espera a que se
    cierre. Devuelve el driver elegido ("" si se cancela).
    """
    with CANDADO:
        elegir = _preparar_selector(tipo)
    return elegir(actual)
