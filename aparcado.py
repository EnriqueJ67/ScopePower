# aparcado.py
# ─────────────────────────────────────────────────────────────────────────────
# Aparcar la montura en el cero de sus motores antes de apagarla (cambio de batería).
#
# Al encenderse, el controlador de una Sky-Watcher pone los dos codificadores a
# 0x800000 en la posición en que esté la montura. SynScan Pro, al reconectar, carga
# solo su último modelo de apuntado (la alineación), que relaciona los codificadores
# con el cielo. Si antes de apagar la montura se lleva justo al cero de los motores,
# al encenderla los codificadores vuelven a valer lo mismo en la misma posición y la
# alineación sigue valiendo.
#
# No se usa la «Hibernación» de SynScan Pro (Park): al reconectar pregunta «Restore
# state to mount?» en la ventana de la app del mini PC, que no se ve desde la tablet.
#
# Ojo: el cero de los motores NO es la altura 0 / acimut 0 del cielo: es donde estaba
# el tubo al encender la montura (horizontal y hacia el norte «a ojo»). Tras alinear,
# SynScan Pro sabe la diferencia, así que el cielo 0/0 queda a unos grados del cero.
#
# Los movimientos se hacen por ASCOM (SlewToAltAz): SynScan Pro sabe en todo momento
# dónde está la montura. Por UDP solo se LEEN los codificadores (:a, :j); nunca se
# manda mover por UDP.
# ─────────────────────────────────────────────────────────────────────────────
import json
import logging
import os
import time

import montura_ejes

log = logging.getLogger("scopepower.aparcado")

CERO_MOTORES = 0x800000
TOLERANCIA_GRADOS = 0.02       # ~1,2' por eje: de sobra para que el modelo siga valiendo
MAX_MOVIMIENTOS = 5
ALTURA_MIN, ALTURA_MAX = -8.0, 88.0     # SynScan Pro tiene el límite inferior en -9°


def _envolver(grados):
    return ((grados + 180.0) % 360.0) - 180.0


class LectorCodificadores:
    """
    Lee por UDP la posición de los dos motores (solo consultas, nunca órdenes de movimiento).
    El módulo wifi nos reenvía a veces la respuesta a una consulta de SynScan Pro con la misma
    longitud (15–20 % medido el 22-sep-2026, p. ej. su :j del otro eje): cada valor es la
    mediana de varias lecturas, que con la montura quieta coinciden.
    """

    def __init__(self, host="192.168.4.1", puerto=11880, timeout_s=0.5):
        self._m = montura_ejes.MonturaSkyWatcherUDP(host=host, puerto=puerto, timeout_s=timeout_s, reintentos=1)
        try:
            self.cpr = [self._mediana("a", c, 3) for c in ("1", "2")]
        except Exception:
            self.cerrar()
            raise
        if min(self.cpr) <= 0:
            self.cerrar()
            raise montura_ejes.ErrorMontura(f"pasos por vuelta no válidos: {self.cpr}")

    def _mediana(self, cmd, canal, n):
        valores = sorted(self._m.leer_24(cmd, canal) for _ in range(n))
        return valores[n // 2]

    def crudos(self):
        return [self._mediana("j", c, 5) for c in ("1", "2")]

    def desfase(self):
        """(acimut, altura) en grados de eje desde el cero de los motores."""
        return tuple(_envolver((p - CERO_MOTORES) * 360.0 / cpr) for p, cpr in zip(self.crudos(), self.cpr))

    def cerrar(self):
        self._m.cerrar()


# Mismo fichero y significado que montura_ejes (signo del codificador frente al
# acimut/altura de ASCOM): lo que aprende uno le sirve al otro.
RUTA_SIGNOS = montura_ejes.MonturaSkyWatcherUDP.RUTA_SIGNOS


def leer_signos():
    try:
        with open(RUTA_SIGNOS, encoding="utf-8") as f:
            d = json.load(f)
        return [d.get("az"), d.get("alt")]
    except (OSError, ValueError):
        return [None, None]


def guardar_signos(signos):
    try:
        os.makedirs(os.path.dirname(RUTA_SIGNOS), exist_ok=True)
        with open(RUTA_SIGNOS, "w", encoding="utf-8") as f:
            json.dump({"az": signos[0], "alt": signos[1], "fecha": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
    except OSError as e:
        log.warning("No se pudieron guardar los signos de los ejes: %s", e)


def mover_altaz(tel, az, alt, esperar_quieta):
    if getattr(tel, "CanSlewAltAzAsync", False):
        tel.SlewToAltAzAsync(az % 360.0, alt)
    else:
        tel.SlewToAltAz(az % 360.0, alt)
    esperar_quieta(tel, 180, "el movimiento")


def ir_al_cero(tel, lector, esperar_quieta, signos=None, fase=lambda texto: None,
               tolerancia=TOLERANCIA_GRADOS, max_movimientos=MAX_MOVIMIENTOS, mover=mover_altaz):
    """
    Lleva la montura al cero de sus motores con GoTo en altura/acimut por ASCOM,
    corrigiendo con lo que leen los codificadores. Se ejecuta en el hilo de la montura
    con el seguimiento ya parado. Devuelve un dict con el desfase final y los movimientos.
    """
    s = list(signos) if signos else [None, None]
    leer = lambda: ((float(tel.Azimuth), float(tel.Altitude)), lector.desfase())
    cielo, d = leer()
    movimientos = []
    # Primer movimiento a la altura 0 / acimut 0 del cielo: no depende de los signos
    # y deja el tubo a pocos grados del cero de los motores (lo que se desvió al
    # colocar la montura); de él se aprende hacia dónde gira cada motor.
    objetivo = (0.0, 0.0)
    for n in range(max_movimientos + 1):
        if n > 0:
            if max(abs(d[0]), abs(d[1])) <= tolerancia:
                break
            sa, sb = (s[0] or 1), (s[1] or 1)
            objetivo = ((cielo[0] - sa * d[0]) % 360.0,
                        min(ALTURA_MAX, max(ALTURA_MIN, cielo[1] - sb * d[1])))
        if n == max_movimientos:
            break
        fase(f"Llevando el tubo al cero de los motores (movimiento {n + 1})...")
        mover(tel, objetivo[0], objetivo[1], esperar_quieta)
        nuevo_cielo, nuevo_d = leer()
        for i, (dc, de) in enumerate(((_envolver(nuevo_cielo[0] - cielo[0]), nuevo_d[0] - d[0]),
                                      (nuevo_cielo[1] - cielo[1], nuevo_d[1] - d[1]))):
            if abs(dc) > 0.3 and abs(de) > 0.3 and 0.5 < abs(de / dc) < 2.0:
                s[i] = 1 if de * dc > 0 else -1
        movimientos.append({"objetivo": [round(objetivo[0], 3), round(objetivo[1], 3)],
                            "desfase": [round(nuevo_d[0], 4), round(nuevo_d[1], 4)]})
        log.info("Aparcar: movimiento %d a az %.3f alt %.3f → desfase del cero %+.4f° / %+.4f°",
                 n + 1, objetivo[0], objetivo[1], nuevo_d[0], nuevo_d[1])
        cielo, d = nuevo_cielo, nuevo_d
    return {"en_cero": max(abs(d[0]), abs(d[1])) <= tolerancia, "desfase": [d[0], d[1]],
            "signos": s, "movimientos": movimientos, "alt": cielo[1], "az": cielo[0]}


if __name__ == "__main__":
    # Prueba con una montura falsa: codificadores girados, con signo cambiado en la
    # altura y una colocación inicial 4° al este y 1,5° hacia arriba.
    class TelFalso:
        CanSlewAltAzAsync = True

        def __init__(self, signo_az, signo_alt, err_az, err_alt, enc_az, enc_alt):
            self.sg = (signo_az, signo_alt)
            self.err = (err_az, err_alt)
            self.enc = [enc_az, enc_alt]          # grados de eje desde el cero
            self.Slewing = False
            self.Tracking = False

        # cielo = cero colocado + signo · eje (+ un pelín de no linealidad del modelo)
        @property
        def Azimuth(self):
            return (self.err[0] + self.sg[0] * self.enc[0] + 0.01 * self.enc[1]) % 360.0

        @property
        def Altitude(self):
            return self.err[1] + self.sg[1] * self.enc[1]

        def SlewToAltAzAsync(self, az, alt):
            e1 = (alt - self.err[1]) * self.sg[1]
            e0 = _envolver(az - self.err[0] - 0.01 * e1) * self.sg[0]
            self.enc = [e0, e1]

    class LectorFalso:
        def __init__(self, tel):
            self.tel = tel

        def desfase(self):
            return tuple(_envolver(x) for x in self.tel.enc)

    for args in ((1, 1, 4.0, 1.5, 120.0, 40.0), (1, -1, -3.0, 0.5, -170.0, 60.0), (-1, 1, 2.0, -1.0, 5.0, 2.0),
                 (-1, -1, 0.0, 0.0, 300.0, 10.0), (-1, -1, 3.0, 1.0, 3.0, 1.0)):
        tel = TelFalso(*args)
        r = ir_al_cero(tel, LectorFalso(tel), lambda *a: None)
        assert r["en_cero"], (args, r)
        assert r["signos"] == [args[0], args[1]] or r["signos"][1] is None or r["signos"][0] is None, (args, r)
        print(f"✅ {args}: {len(r['movimientos'])} movimientos, desfase final "
              f"{r['desfase'][0]:+.4f}° / {r['desfase'][1]:+.4f}°, signos {r['signos']}")
    print("✅ aparcado: pruebas correctas")
