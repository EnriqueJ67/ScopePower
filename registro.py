# registro.py
#
# Logging a fichero para ScopePower.
#
# La app usa print() en cientos de sitios y al cerrar (/salir) libera la
# consola, con lo que todo el diagnóstico de la noche se perdía. Aquí:
#   - se configura logging con un fichero rotativo en logs/scopepower.log
#     (5 MB x 5 copias) además de la consola,
#   - stdout/stderr se duplican al log, así los print() existentes también
#     quedan registrados sin tocarlos,
#   - las excepciones no capturadas en hilos (apilado, hardware) se registran.

import os
import sys
import logging
import threading
from logging.handlers import RotatingFileHandler

CARPETA_LOGS = "logs"
FICHERO_LOG = "scopepower.log"

_configurado = False


class _Duplicador:
    """Flujo que escribe en el flujo original y, por líneas, en un logger."""

    def __init__(self, original, logger, nivel):
        self._original = original
        self._logger = logger
        self._nivel = nivel
        self._resto = ""
        self._lock = threading.Lock()
        self.encoding = "utf-8"

    def write(self, texto):
        if self._original is not None:
            try:
                self._original.write(texto)
            except Exception:
                pass
        with self._lock:
            self._resto += texto
            while "\n" in self._resto:
                linea, self._resto = self._resto.split("\n", 1)
                if linea.strip():
                    self._logger.log(self._nivel, linea.rstrip())

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        if self._original is None:
            raise OSError("sin descriptor")
        return self._original.fileno()


def configurar_logging(carpeta=CARPETA_LOGS, nivel=logging.INFO):
    """Configura fichero rotativo + consola y duplica print() al log. Idempotente."""
    global _configurado
    if _configurado:
        return logging.getLogger("scopepower")
    _configurado = True

    # Consola en UTF-8 (los mensajes llevan emojis)
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    os.makedirs(carpeta, exist_ok=True)
    formato = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    for h in list(raiz.handlers):
        raiz.removeHandler(h)

    fichero = RotatingFileHandler(os.path.join(carpeta, FICHERO_LOG), maxBytes=5 * 1024 * 1024,
                                  backupCount=5, encoding="utf-8")
    fichero.setFormatter(formato)
    raiz.addHandler(fichero)

    consola_original = sys.__stdout__ if sys.__stdout__ is not None else None
    if consola_original is not None:
        consola = logging.StreamHandler(consola_original)
        consola.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        # Lo que viene de print() ya se ve en consola por el duplicador: no repetirlo
        consola.addFilter(lambda r: not r.name.startswith("scopepower.consola"))
        raiz.addHandler(consola)

    # Silenciar el ruido de peticiones de Werkzeug en el fichero (una línea por sondeo cada 2 s)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    # Duplicar print() y errores al log
    sys.stdout = _Duplicador(sys.stdout, logging.getLogger("scopepower.consola"), logging.INFO)
    sys.stderr = _Duplicador(sys.stderr, logging.getLogger("scopepower.consola.err"), logging.ERROR)

    # Excepciones no capturadas en hilos
    def _hook_hilo(args):
        logging.getLogger("scopepower").error(
            "Excepción no capturada en el hilo %s", args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = _hook_hilo

    def _hook_principal(tipo, valor, tb):
        logging.getLogger("scopepower").critical("Excepción no capturada", exc_info=(tipo, valor, tb))
    sys.excepthook = _hook_principal

    log = logging.getLogger("scopepower")
    log.info("Logging iniciado: %s", os.path.abspath(os.path.join(carpeta, FICHERO_LOG)))
    return log
