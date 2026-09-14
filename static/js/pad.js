// static/js/pad.js — pad táctil para mover la montura (templates/_pad.html).
// Lo usan el control manual y el panel "Mover el telescopio" de la Foto de prueba.
//   · velocidad proporcional a la distancia del dedo al centro
//   · zona muerta del 14 % para que un dedo quieto no mueva nada
//   · curva exponente 1.7: precisión fina cerca del centro, rapidez en el borde
//   · se envía como mucho cada 150 ms y solo si la velocidad cambia; al soltar, parada inmediata
//   · el servidor para solo los ejes si deja de recibir órdenes (1,2 s)
// Define vibrar() y pintarVelocidad() como globales por si la página tiene más botones (flechas del control manual).

function vibrar(ms) {
    if (navigator.vibrate) {
        navigator.vibrate(ms);
    }
}

// ── Velocidad: grupo de botones que escribe en el <select id="speed"> ──
function pintarVelocidad() {
    const v = document.getElementById("speed").value;
    document.querySelectorAll("#speed-seg button").forEach(b => b.classList.toggle("activo", b.dataset.v === v));
}

(function () {
    const ZONA_MUERTA = 0.14, EXPONENTE = 1.7, INTERVALO_MS = 150;
    const pad = document.getElementById("pad");
    if (!pad) return;
    const mando = document.getElementById("pad-mando");
    const linea = document.querySelector("#pad-linea line");
    const estado = document.getElementById("pad-estado");
    const invX = document.getElementById("inv-x"), invY = document.getElementById("inv-y");
    const speed = document.getElementById("speed");
    let puntero = null, ultimoEnvio = 0, ultimoVx = 0, ultimoVy = 0, temporizador = null, maxDriver = [null, null];

    document.querySelectorAll("#speed-seg button").forEach(b => b.addEventListener("click", () => {
        speed.value = b.dataset.v;
        speed.dispatchEvent(new Event("change"));
        pintarVelocidad();
        vibrar(15);
    }));

    // Velocidad e inversiones se recuerdan en el navegador (valen para las dos pantallas)
    try {
        invX.checked = localStorage.getItem("pad_inv_x") === "1";
        invY.checked = localStorage.getItem("pad_inv_y") === "1";
        const v = localStorage.getItem("pad_velocidad"); if (v) speed.value = v;
    } catch (e) {}
    pintarVelocidad();
    invX.addEventListener("change", () => { try { localStorage.setItem("pad_inv_x", invX.checked ? "1" : "0"); } catch (e) {} });
    invY.addEventListener("change", () => { try { localStorage.setItem("pad_inv_y", invY.checked ? "1" : "0"); } catch (e) {} });
    speed.addEventListener("change", ev => { try { localStorage.setItem("pad_velocidad", ev.target.value); } catch (e) {} });

    fetch("/montura/velocidades").then(r => r.json()).then(d => { maxDriver = [d.max_eje0, d.max_eje1]; }).catch(() => {});

    function curva(x) {
        const a = Math.abs(x);
        if (a < ZONA_MUERTA) return 0;
        return Math.sign(x) * Math.pow((a - ZONA_MUERTA) / (1 - ZONA_MUERTA), EXPONENTE);
    }

    function pintar(nx, ny) {
        mando.style.left = (50 + nx * 50) + "%";
        mando.style.top = (50 + ny * 50) + "%";
        linea.setAttribute("x2", (50 + nx * 50) + "%");
        linea.setAttribute("y2", (50 + ny * 50) + "%");
    }

    function enviar(vx, vy, forzar) {
        const ahora = Date.now();
        const cambio = Math.abs(vx - ultimoVx) + Math.abs(vy - ultimoVy);
        if (!forzar && (ahora - ultimoEnvio < INTERVALO_MS || cambio < 0.01)) return;
        ultimoEnvio = ahora; ultimoVx = vx; ultimoVy = vy;
        fetch("/pad/mover", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vx, vy }) })
            .then(r => r.json())
            .then(d => { if (!d.ok) { estado.textContent = "❌ " + (d.mensaje || "Error"); soltar(); } })
            .catch(() => { estado.textContent = "❌ Sin respuesta del servidor"; });
    }

    function parar(porBeacon) {
        ultimoVx = ultimoVy = 0; ultimoEnvio = 0;
        if (porBeacon && navigator.sendBeacon) { navigator.sendBeacon("/pad/parar"); return; }
        fetch("/pad/parar", { method: "POST", keepalive: true }).catch(() => {});
    }

    function procesar(ev) {
        const rect = pad.getBoundingClientRect();
        const r = rect.width / 2;
        let nx = (ev.clientX - rect.left - r) / r;
        let ny = (ev.clientY - rect.top - r) / r;
        const d = Math.hypot(nx, ny);
        if (d > 1) { nx /= d; ny /= d; }               // el mando no sale del círculo
        pintar(nx, ny);
        const vmax = parseFloat(speed.value);
        let vx = curva(nx) * vmax * (invX.checked ? -1 : 1);
        let vy = -curva(ny) * vmax * (invY.checked ? -1 : 1);   // arriba en pantalla = eje 1 positivo (N)
        if (maxDriver[0]) vx = Math.max(-maxDriver[0], Math.min(maxDriver[0], vx));
        if (maxDriver[1]) vy = Math.max(-maxDriver[1], Math.min(maxDriver[1], vy));
        vx = Math.round(vx * 1000) / 1000; vy = Math.round(vy * 1000) / 1000;
        estado.textContent = (vx === 0 && vy === 0) ? "● Centro: parado" :
            `↔ ${vx >= 0 ? "E" : "W"} ${Math.abs(vx).toFixed(2)}°/s   ↕ ${vy >= 0 ? "N" : "S"} ${Math.abs(vy).toFixed(2)}°/s`;
        enviar(vx, vy, false);
    }

    function soltar() {
        if (puntero === null) return;
        try { pad.releasePointerCapture(puntero); } catch (e) {}
        puntero = null;
        clearInterval(temporizador); temporizador = null;
        pad.classList.remove("activo");
        pintar(0, 0);
        parar(false);
        estado.textContent = "● Parado";
        vibrar(20);
    }

    pad.addEventListener("pointerdown", ev => {
        if (puntero !== null) return;                   // un solo dedo
        puntero = ev.pointerId;
        pad.setPointerCapture(puntero);
        pad.classList.add("activo");
        vibrar(30);
        procesar(ev);
        // Reenvío periódico aunque el dedo esté quieto: mantiene vivo el vigilante del servidor
        temporizador = setInterval(() => { if (puntero !== null) enviar(ultimoVx, ultimoVy, true); }, 500);
        ev.preventDefault();
    });
    pad.addEventListener("pointermove", ev => { if (ev.pointerId === puntero) { procesar(ev); ev.preventDefault(); } });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(t => pad.addEventListener(t, ev => { if (ev.pointerId === puntero) soltar(); }));

    // Si la página se oculta o se cierra con el dedo puesto, parar la montura
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") { if (puntero !== null) soltar(); parar(true); } });
    window.addEventListener("pagehide", () => parar(true));
})();
