"""
Espacio libre en el disco del PC donde ScopePower guarda las fotos.

El mini PC de campo tiene un disco pequeño y una noche de capturas (FITS de cada foto,
evolución del apilado, vídeos SER, timelapses) lo va llenando. Si se llena del todo,
las fotos dejan de guardarse y hasta config.ini puede quedarse vacío al reescribirlo.

- estado(): libre/total del disco y si está por debajo del umbral (se pide cada 15 s
  desde la cabecera de todas las páginas; shutil.disk_usage es instantáneo).
- carpetas(): lo que ocupa cada carpeta de datos (solo en Configuración: recorre ficheros).
- Vigilante: cada 30 s mira el disco y avisa por voz y en el log al bajar del umbral,
  y otra vez al entrar en la zona crítica. No repite hasta que se recupera espacio.
  Con menos de MINIMO_GB llama a al_quedar_sin_espacio (ScopePower pausa el apilado).

Config: [Disco] umbral_aviso_pct (20 por defecto).
"""

import logging
import os
import shutil
import threading
import time

log = logging.getLogger(__name__)

UMBRAL_POR_DEFECTO = 20      # % libre por debajo del cual se avisa
CRITICO_GB = 2.0             # por debajo de esto se avisa siempre, sea cual sea el umbral
MINIMO_GB = 1.0              # por debajo de esto se pausa el apilado (una foto FITS son ~18 MB)
INTERVALO_S = 30
HISTERESIS_PCT = 2           # hay que recuperar umbral + 2 % para volver a avisar

# Carpetas donde la app escribe datos (relativas a la carpeta de ScopePower)
CARPETAS_DATOS = [
    ("capturas", "Fotos de la sesión (FITS)"),
    (os.path.join("static", "fotos"), "Galería"),
    (os.path.join("static", "evolucion"), "Evolución del apilado"),
    (os.path.join("static", "continuo"), "Foto continua"),
    (os.path.join("static", "timelapse"), "Timelapses"),
    (os.path.join("static", "seguimiento"), "Seguimiento"),
    ("videos", "Vídeos"),
    ("calibraciones", "Darks y flats"),
    ("comparaciones", "Comparaciones"),
    ("logs", "Registros (logs)"),
]

_GB = 1024 ** 3


class VigilanteDisco:
    def __init__(self):
        self.raiz = os.path.abspath(".")
        self.umbral_pct = UMBRAL_POR_DEFECTO
        self._nivel_avisado = 0   # 0 = bien, 1 = bajo, 2 = crítico
        self._hilo = None
        self._voz = None
        self._al_quedar_sin_espacio = None
        self._cache_carpetas = (0.0, None)

    # ── Medida ───────────────────────────────────────────────────────────
    def estado(self):
        try:
            u = shutil.disk_usage(self.raiz)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        pct = 100.0 * u.free / u.total if u.total else 0.0
        libre_gb = u.free / _GB
        critico = libre_gb < CRITICO_GB or pct < self.umbral_pct / 2
        return {
            "ok": True,
            "unidad": os.path.splitdrive(self.raiz)[0] or self.raiz,
            "total_gb": round(u.total / _GB, 1),
            "libre_gb": round(libre_gb, 1),
            "usado_gb": round(u.used / _GB, 1),
            "pct_libre": round(pct, 1),
            "umbral_pct": self.umbral_pct,
            "bajo": pct < self.umbral_pct or critico,
            "critico": critico,
            "sin_espacio": libre_gb < MINIMO_GB,
            "minimo_gb": MINIMO_GB,
        }

    def carpetas(self, max_edad_s=30):
        """Tamaño de cada carpeta de datos (con caché: recorrer miles de FITS tarda)."""
        hora, datos = self._cache_carpetas
        if datos is not None and time.time() - hora < max_edad_s:
            return datos
        salida = []
        for relativa, nombre in CARPETAS_DATOS:
            ruta = os.path.join(self.raiz, relativa)
            if not os.path.isdir(ruta):
                continue
            tam, n = _tamano_carpeta(ruta)
            salida.append({"carpeta": relativa.replace("\\", "/"), "nombre": nombre,
                           "gb": round(tam / _GB, 2), "ficheros": n})
        salida.sort(key=lambda c: -c["gb"])
        self._cache_carpetas = (time.time(), salida)
        return salida

    # ── Vigilancia en segundo plano ──────────────────────────────────────
    def configurar(self, umbral_pct):
        try:
            u = float(umbral_pct)
        except (TypeError, ValueError):
            return
        self.umbral_pct = max(1, min(90, int(round(u))))
        self._nivel_avisado = 0   # umbral nuevo: que vuelva a avisar si procede

    def iniciar(self, voz=None, al_quedar_sin_espacio=None):
        """al_quedar_sin_espacio(estado): se llama en cada comprobación mientras queden menos de
        MINIMO_GB libres; decide ella si hay algo que pausar (así se vuelve a pausar si alguien reanuda)."""
        self._voz = voz
        self._al_quedar_sin_espacio = al_quedar_sin_espacio
        if self._hilo and self._hilo.is_alive():
            return
        self._hilo = threading.Thread(target=self._bucle, name="vigilante-disco", daemon=True)
        self._hilo.start()

    def _bucle(self):
        while True:
            try:
                self.comprobar()
            except Exception:
                log.exception("Vigilante del disco")
            time.sleep(INTERVALO_S)

    def comprobar(self):
        e = self.estado()
        if not e.get("ok"):
            return e
        nivel = 2 if e["critico"] else (1 if e["bajo"] else 0)
        if nivel > self._nivel_avisado:
            gigas = e["libre_gb"]
            if nivel == 2:
                log.warning("Disco %s casi lleno: %.1f GB libres (%.1f %%)", e["unidad"], gigas, e["pct_libre"])
                texto = f"Atención: el disco está casi lleno. Quedan {_gigas_hablados(gigas)}."
            else:
                log.warning("Disco %s por debajo del %d %%: %.1f GB libres (%.1f %%)",
                            e["unidad"], self.umbral_pct, gigas, e["pct_libre"])
                texto = f"Queda poco espacio en el disco: {e['pct_libre']:.0f} por ciento libre, {_gigas_hablados(gigas)}."
            if self._voz:
                self._voz.decir(texto)
                try:
                    import humor
                    humor.decir("disco_bajo")
                except Exception:
                    pass
            self._nivel_avisado = nivel
        elif nivel == 0 and self._nivel_avisado and e["pct_libre"] >= self.umbral_pct + HISTERESIS_PCT:
            log.info("Disco %s recuperado: %.1f GB libres (%.1f %%)", e["unidad"], e["libre_gb"], e["pct_libre"])
            self._nivel_avisado = 0
        if e["sin_espacio"] and self._al_quedar_sin_espacio:
            try:
                self._al_quedar_sin_espacio(e)
            except Exception:
                log.exception("Pausa por disco lleno")
        return e


def _tamano_carpeta(ruta):
    total, n = 0, 0
    pendientes = [ruta]
    while pendientes:
        actual = pendientes.pop()
        try:
            with os.scandir(actual) as it:
                for ent in it:
                    try:
                        if ent.is_dir(follow_symlinks=False):
                            pendientes.append(ent.path)
                        elif ent.is_file(follow_symlinks=False):
                            total += ent.stat(follow_symlinks=False).st_size
                            n += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return total, n


def _gigas_hablados(gb):
    if gb < 1:
        return f"{int(gb * 1024)} megas"
    return f"{gb:.0f} gigas" if gb >= 10 else f"{gb:.1f} gigas".replace(".", ",")


vigilante = VigilanteDisco()
