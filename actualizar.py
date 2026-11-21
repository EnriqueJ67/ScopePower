# actualizar.py
#
# Actualizar ScopePower desde la página Configuración («Actualizar ScopePower»), sin Git: se
# baja el ZIP de la última release de GitHub y se copia encima de la instalación, sea la
# carpeta una copia, un ZIP descomprimido o un clon. El trabajo de verdad está en
# aplicar_actualizacion.py (solo biblioteca estándar); aquí está lo que ve la web:
#
#   1. estado():    versión instalada, la anterior guardada (para volver), el resultado de la
#                   última actualización y cómo va la descarga en curso. No toca la red.
#   2. comprobar(): pregunta a GitHub cuál es la última release. Solo se llama al pulsar
#                   «Comprobar»: en el campo no hay Internet y no tiene sentido mirar solo.
#   3. empezar():   descarga y comprueba la versión nueva en segundo plano, CON LA APP EN
#                   MARCHA; si falla (sin red, ZIP dañado...) la app sigue como estaba. Si va
#                   bien, deja el pedido en datos/actualizar_pendiente.txt y cierra la app.
#   4. pedir():     lo mismo sin descarga, para volver a la versión anterior.
#
# Después ScopePower sale con código 5 y lanzar_scopepower.bat ejecuta
# actualizar_desde_github.bat /lanzador, que copia los ficheros con la app parada (guardando
# antes lo que había en _backup\ y restaurándolo si algo falla) y vuelve a arrancarla.
#
# Los nombres de fichero y el código de salida tienen que coincidir con los del .bat y con
# aplicar_actualizacion.py.

import json
import logging
import os
import re
import threading
import time

import aplicar_actualizacion as act
import version
from idioma import _

log = logging.getLogger("scopepower.actualizar")

RAIZ = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(RAIZ, "actualizar_desde_github.bat")
FICHERO_PENDIENTE = os.path.join(RAIZ, act.FICHERO_PENDIENTE)
LOG_ACTUALIZACION = os.path.join(RAIZ, "logs", "actualizacion.log")
CODIGO_SALIDA_ACTUALIZAR = 5          # lo entiende lanzar_scopepower.bat
# «release» baja la última versión, «volver» deshace la última actualización y «reparar»
# recupera la versión anterior si una actualización se quedó a medias.
DESTINOS = ("release", "volver", "reparar")

# Cómo va la descarga pedida desde la web: fase = None | consultando | descargando |
# comprobando | aplicando (la app se cierra) | al_dia | error
_cerrojo = threading.Lock()
_descarga = {"fase": None, "mensaje": "", "bytes": 0, "total": None, "version": None}
_EN_MARCHA = ("consultando", "descargando", "comprobando", "aplicando")


def _poner(**valores):
    with _cerrojo:
        _descarga.update(valores)


def mensaje_error(e):
    """Un ErrorActualizacion contado para quien no sabe de ordenadores, en el idioma de la app."""
    if e.motivo == "sin_red":
        return _("Sin conexión a internet. En el campo es lo normal: actualiza en casa, con el PC "
                 "en una red con salida a Internet.")
    if e.motivo == "limite":
        return _("GitHub no admite más consultas desde esta conexión por ahora. Prueba dentro de una hora.")
    if e.motivo == "sin_release":
        return _("Todavía no hay ninguna versión publicada en GitHub.")
    if e.motivo == "descarga":
        return _("La descarga se ha cortado. Vuelve a intentarlo.")
    if e.motivo == "zip_corrupto":
        return _("La descarga ha llegado dañada. Vuelve a intentarlo.")
    if e.motivo == "contenido":
        return _("Lo descargado no es un ScopePower completo. No se ha tocado nada.")
    return _("No se pudo actualizar: {detalle}", detalle=e.detalle)


def ultimo_resultado():
    """Resumen de logs/actualizacion.log (lo escribe el .bat en modo /lanzador), o None."""
    try:
        with open(LOG_ACTUALIZACION, encoding="utf-8", errors="replace") as f:
            texto = f.read()
        fecha = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(LOG_ACTUALIZACION)))
    except OSError:
        return None
    lineas = re.findall(r"^\[RESULTADO\] (ok|error)[ \t]*(\S*)[ \t]*(.*)$", texto, re.MULTILINE)
    if not lineas:
        return {"ok": False, "fecha": fecha, "mensaje": _("La actualización no llegó a terminar"),
                "detalle": texto[-3000:]}
    resultado, clave, resto = lineas[-1]
    ok = resultado == "ok"
    resto = resto.strip()
    if not ok:
        mensaje = _("La actualización falló y no se pudo dejar todo como estaba. Ejecuta "
                    "actualizar_desde_github.bat en el PC.") if clave == "sin_restaurar" \
            else _("No se pudo actualizar, se ha mantenido la versión anterior")
    elif clave == "actualizado":
        mensaje = _("Actualizado a la versión {version}", version=resto)
    elif clave == "vuelta":
        mensaje = _("Se ha vuelto a la versión {version}", version=resto)
    elif clave == "reparada":
        mensaje = _("Se ha recuperado la versión {version}", version=resto)
    elif clave == "sin_cambios" or "sin cambios" in resto:
        mensaje = _("No había nada que actualizar")
    else:                                                  # log de un actualizador anterior
        mensaje = _("La última actualización terminó bien")
    # Para el fallo, las líneas de [ERROR]/[AVISO] son lo que interesa
    errores = [l.strip() for l in texto.splitlines() if "[ERROR]" in l or "[AVISO]" in l]
    return {"ok": ok, "fecha": fecha, "mensaje": mensaje, "detalle": "\n".join(errores) if errores else texto[-3000:]}


def estado():
    """Lo que enseña la tarjeta al abrir Configuración y mientras descarga (sin tocar la red)."""
    copia = act.copia_anterior(RAIZ)
    with _cerrojo:
        descarga = dict(_descarga)
    return {
        "version": version.VERSION,
        "anterior": {"nombre": copia["version"]} if copia else None,
        "ultimo_resultado": ultimo_resultado(),
        "pendiente": os.path.exists(FICHERO_PENDIENTE) and descarga["fase"] != "aplicando",
        "interrumpida": act.interrumpida(RAIZ),
        "desarrollo": act.carpeta_desarrollo(RAIZ),
        "lanzador": os.environ.get("SCOPEPOWER_LANZADOR") == "1",
        "bat": os.path.exists(BAT),
        "descarga": descarga,
    }


def comprobar():
    """
    Consulta GitHub. Devuelve un dict con 'ok' y, si todo va bien: 'ultima_release' (número de
    versión), 'al_dia_release', 'fecha_release' y 'notas' (lo que trae la versión nueva).
    Si no, 'motivo' (sin_red | limite | sin_release | error) y un 'mensaje' para enseñar.
    """
    try:
        release = act.consultar_release()
    except act.ErrorActualizacion as e:
        log.warning("Comprobar actualización: %s (%s)", e.motivo, e.detalle)
        return {"ok": False, "motivo": e.motivo, "mensaje": mensaje_error(e)}
    al_dia = act.clave_version(version.VERSION) >= act.clave_version(release["version"])
    log.info("Comprobada la actualización: instalada %s, última release %s", version.VERSION, release["version"])
    return {"ok": True, "ultima_release": release["version"], "al_dia_release": al_dia,
            "fecha_release": release["fecha"], "notas": "" if al_dia else release["notas"].strip()}


def _impedimento():
    """Por qué no se puede actualizar esta carpeta desde la web, o None."""
    if not os.path.exists(BAT):
        return _("Falta actualizar_desde_github.bat en la carpeta de ScopePower")
    rama = act.carpeta_desarrollo(RAIZ)
    if rama:
        return _("La carpeta está en la rama {rama}: es una carpeta de desarrollo, actualízala con git", rama=rama)
    return None


def _dejar_pedido(destino, **datos):
    os.makedirs(os.path.dirname(FICHERO_PENDIENTE), exist_ok=True)
    with open(FICHERO_PENDIENTE, "w", encoding="utf-8") as f:
        json.dump(dict(datos, destino=destino), f, ensure_ascii=False)


def _descargar_y_cerrar(al_terminar):
    def progreso(recibido, total):
        _poner(bytes=recibido, total=total)

    try:
        release = act.consultar_release()
        if act.clave_version(version.VERSION) >= act.clave_version(release["version"]):
            _poner(fase="al_dia", mensaje=_("Ya tienes la última versión"))
            return
        _poner(version=release["version"])
        origen = act.preparar(RAIZ, release, progreso=progreso, fase=lambda f: _poner(fase=f))
        _dejar_pedido("release", origen=origen, version=release["version"])
        _poner(fase="aplicando")
        log.warning("Versión %s descargada y comprobada: ScopePower se cierra para actualizarse", release["version"])
        al_terminar()
    except act.ErrorActualizacion as e:
        log.warning("La actualización no ha pasado de la descarga: %s (%s)", e.motivo, e.detalle)
        _poner(fase="error", mensaje=mensaje_error(e))
    except Exception as e:
        log.exception("Fallo inesperado al preparar la actualización")
        _poner(fase="error", mensaje=_("No se pudo actualizar: {detalle}", detalle=repr(e)))


def empezar(al_terminar):
    """
    Descarga y comprueba la última release en segundo plano; la web sigue el avance con
    estado()['descarga']. Si todo va bien llama a al_terminar(), que suelta el equipo y cierra
    la app para que el .bat copie los ficheros. Devuelve (ok, mensaje).
    """
    problema = _impedimento()
    if problema:
        return False, problema
    with _cerrojo:
        if _descarga["fase"] in _EN_MARCHA:
            return False, _("Ya hay una actualización en marcha")
        _descarga.update(fase="consultando", mensaje="", bytes=0, total=None, version=None)
    threading.Thread(target=_descargar_y_cerrar, args=(al_terminar,), daemon=True, name="actualizar").start()
    return True, _("Descargando la versión nueva")


def pedir(destino):
    """
    Deja apuntado un pedido sin descarga («volver» o «reparar») para
    actualizar_desde_github.bat /lanzador. Devuelve (ok, mensaje). El que llama (ScopePower.py)
    para después la app con CODIGO_SALIDA_ACTUALIZAR.
    """
    if destino not in ("volver", "reparar"):
        return False, _("Destino no válido")
    problema = _impedimento()
    if problema:
        return False, problema
    if destino == "volver" and not act.copia_anterior(RAIZ):
        return False, _("No hay ninguna versión anterior guardada")
    with _cerrojo:
        if _descarga["fase"] in _EN_MARCHA:
            return False, _("Ya hay una actualización en marcha")
        _descarga.update(fase="aplicando", mensaje="", bytes=0, total=None, version=None)
    _dejar_pedido(destino)
    log.warning("Pedido desde la web: %s", "volver a la versión anterior" if destino == "volver"
                else "recuperar la versión anterior tras una actualización a medias")
    return True, _("ScopePower se cierra y vuelve a arrancar solo")


def orden_actualizar_a_mano():
    """
    Cuando la app no la arrancó lanzar_scopepower.bat: la orden para cmd que hace lo pedido
    con la app ya cerrada y vuelve a arrancarla (equivalente a lo que hace el lanzador
    con el código de salida 5).
    """
    return f'ping -n 3 127.0.0.1 >nul & "{BAT}" /lanzador'
