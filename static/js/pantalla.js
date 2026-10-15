// static/js/pantalla.js — mantener la pantalla del móvil encendida mientras se usa ScopePower.
//
// Los navegadores no permiten bloquear la pantalla sin un gesto del usuario, así que
// se activa con el primer toque en cada página. Hasta entonces se muestra un aviso
// discreto sobre la barra de navegación. Con HTTPS se usa la API Wake Lock; con HTTP
// (lo normal en la red local) se usa NoSleep.js (vídeo silencioso en bucle).
(function () {
    const CLAVE = "scopepower_pantalla";              // "off" si el usuario la desactivó
    let bloqueo = null;                                // WakeLockSentinel
    let noSleep = null;
    let activa = false;
    let pill = null;

    function preferenciaOff() {
        try { return localStorage.getItem(CLAVE) === "off"; } catch (e) { return false; }
    }
    function guardar(v) {
        try { localStorage.setItem(CLAVE, v); } catch (e) {}
    }

    function pintar() {
        if (!pill) return;
        if (activa) {
            pill.textContent = _("🔆 Pantalla siempre encendida");
            pill.className = "pantalla-pill activa";
        } else if (preferenciaOff()) {
            pill.textContent = _("🌙 La pantalla puede apagarse (toca para evitarlo)");
            pill.className = "pantalla-pill";
        } else {
            pill.textContent = _("🌙 Toca la pantalla para que no se apague");
            pill.className = "pantalla-pill aviso";
        }
    }

    async function activar() {
        if (activa) return true;
        try {
            if ("wakeLock" in navigator) {
                bloqueo = await navigator.wakeLock.request("screen");
                bloqueo.addEventListener("release", () => { activa = false; bloqueo = null; pintar(); });
            } else if (window.NoSleep) {
                noSleep = noSleep || new NoSleep();
                noSleep.enable();
            } else {
                return false;
            }
            activa = true;
        } catch (e) {
            console.warn("No se pudo mantener la pantalla encendida:", e);
            activa = false;
        }
        pintar();
        return activa;
    }

    function desactivar() {
        try {
            if (bloqueo) bloqueo.release();
            if (noSleep) noSleep.disable();
        } catch (e) {}
        bloqueo = null; activa = false; pintar();
    }

    function primerGesto() {
        if (preferenciaOff()) return;
        activar();
    }

    function alternar(ev) {
        ev.preventDefault(); ev.stopPropagation();
        if (activa) { desactivar(); guardar("off"); }
        else { guardar("on"); activar(); }
        pintar();
    }

    document.addEventListener("DOMContentLoaded", () => {
        pill = document.createElement("div");
        pill.id = "pantalla-pill";
        pill.title = _("Mantener la pantalla encendida");
        pill.addEventListener("click", alternar);
        document.body.appendChild(pill);
        pintar();
        // Ocultar el aviso si en 20 s el usuario no ha tocado nada (no molestar)
        setTimeout(() => { if (!activa && pill) pill.classList.add("discreta"); }, 20000);
    });

    // Cualquier gesto vale para activarla (hay que llamar dentro del propio gesto)
    ["touchstart", "click", "keydown"].forEach(ev => document.addEventListener(ev, primerGesto, { passive: true }));

    // Al volver a la pestaña, el Wake Lock se ha soltado: pedirlo otra vez
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && !activa && !preferenciaOff() && "wakeLock" in navigator) activar();
    });
})();
