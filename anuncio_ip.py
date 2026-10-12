# anuncio_ip.py
#
# Al arrancar en el campo no hay pantalla y es fácil no recordar qué dirección
# escribir en el móvil. Este módulo anuncia por voz la IP y el puerto del
# servidor (y el nombre del equipo como alternativa) cada 30 s hasta que llega
# la primera petición desde otro dispositivo. Tras 10 minutos sin conexión
# pasa a repetir cada 5 minutos. Un botón en Configuración lo repite a mano.

import os
import re
import socket
import subprocess
import threading
import logging
import configparser

log = logging.getLogger("scopepower.anuncio_ip")

_ADAPTADORES_IGNORAR = ("virtual", "vmware", "hyper-v", "vethernet", "bluetooth", "loopback", "tunnel", "tap-",
                        "npcap", "zerotier", "tailscale", "wireguard", "openvpn", "vpn", "hamachi")


def _prioridad_adaptador(nombre):
    n = nombre.lower()
    if "wi-fi" in n or "wifi" in n or "inal" in n or "wireless" in n or "wlan" in n:
        return 0          # el móvil casi siempre está en el Wi-Fi
    if "ethernet" in n:
        return 1
    return 2
_DIGITOS = {"0": "cero", "1": "uno", "2": "dos", "3": "tres", "4": "cuatro",
            "5": "cinco", "6": "seis", "7": "siete", "8": "ocho", "9": "nueve"}


def _prioridad(ip):
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip):
        return 2
    return 3


def direcciones_ip():
    """
    Lista de (ip, nombre_adaptador) IPv4 útiles del equipo, la más probable
    para el móvil primero. Usa `ipconfig` (Windows) y, si falla, el nombre de host.
    """
    encontradas = []
    try:
        salida = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=10,
                                encoding="cp850", errors="replace").stdout
        adaptador = ""
        for linea in salida.splitlines():
            if linea and not linea.startswith(" ") and ":" in linea:
                adaptador = linea.strip().rstrip(":")
                continue
            m = re.search(r"IPv4[^:]*:\s*([\d.]+)", linea)
            if m:
                ip = m.group(1)
                if ip.startswith(("127.", "169.254.")):
                    continue
                if any(p in adaptador.lower() for p in _ADAPTADORES_IGNORAR):
                    continue
                encontradas.append((ip, adaptador))
    except Exception as e:
        log.warning("ipconfig no disponible: %s", e)
    if not encontradas:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith(("127.", "169.254.")) and ip not in [x[0] for x in encontradas]:
                    encontradas.append((ip, "host"))
        except Exception as e:
            log.warning("No se pudo resolver el nombre de host: %s", e)
    # La del punto de acceso de Windows (punto_acceso.py) es la que usa la tablet
    encontradas.sort(key=lambda x: (x[0] != "192.168.137.1", _prioridad_adaptador(x[1]), _prioridad(x[0])))
    return encontradas


def nombre_equipo():
    return socket.gethostname()


def dictar_ip(ip):
    """'192.168.1.23' -> 'uno nueve dos, punto, uno seis ocho, punto, uno, punto, dos tres'"""
    grupos = []
    for octeto in ip.split("."):
        grupos.append(" ".join(_DIGITOS.get(d, d) for d in octeto))
    return ", punto, ".join(grupos)


def texto_anuncio(puerto, incluir_nombre=True):
    ips = direcciones_ip()
    if not ips:
        return "ScopePower listo, pero no encuentro ninguna dirección de red. Comprueba el wifi."
    partes = ["ScopePower listo. Para conectar, escribe en el navegador:"]
    for ip, _ in ips[:2]:
        partes.append(f"{dictar_ip(ip)}, dos puntos, puerto {puerto}.")
    if incluir_nombre:
        partes.append(f"También vale el nombre {nombre_equipo()} punto local, puerto {puerto}.")
    return " ".join(partes)


class AnunciadorIP:
    def __init__(self, puerto, decir, intervalo_s=30, intervalo_lento_s=300, minutos_hasta_lento=10):
        self.puerto = puerto
        self._decir = decir
        self.intervalo_s = intervalo_s
        self.intervalo_lento_s = intervalo_lento_s
        self.minutos_hasta_lento = minutos_hasta_lento
        self._conectado = threading.Event()
        self._parar = threading.Event()
        self.anuncios = 0
        self.ultimo_texto = ""
        self._hilo = None

    def iniciar(self):
        if self._hilo is not None:
            return
        self._hilo = threading.Thread(target=self._bucle, name="anuncio-ip", daemon=True)
        self._hilo.start()

    def marcar_conectado(self, origen=""):
        if not self._conectado.is_set():
            log.info("Primera conexión remota desde %s: se dejan de anunciar las direcciones", origen)
            self._conectado.set()

    def esta_conectado(self):
        return self._conectado.is_set()

    def anunciar_ahora(self):
        texto = texto_anuncio(self.puerto)
        self.ultimo_texto = texto
        self.anuncios += 1
        self._decir(texto)
        return texto

    def detener(self):
        self._parar.set()

    def _bucle(self):
        import time
        inicio = time.time()
        # Pequeña espera para que el servidor esté escuchando y la voz lista
        if self._parar.wait(3):
            return
        while not self._parar.is_set() and not self._conectado.is_set():
            try:
                self.anunciar_ahora()
            except Exception as e:
                log.warning("Error anunciando la IP: %s", e)
            lento = (time.time() - inicio) > self.minutos_hasta_lento * 60
            espera = self.intervalo_lento_s if lento else self.intervalo_s
            if self._conectado.wait(espera):
                break
