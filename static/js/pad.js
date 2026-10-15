// static/js/pad.js — pad táctil para mover la montura (templates/_pad.html).
// Lo usan el control manual y el panel "Mover el telescopio" de la Foto de prueba.
//   · velocidades por escalones: las mismas de los botones (0,1 / 0,5 / 1 / 3 °/s), que la
//     montura acepta seguro; la distancia del dedo al centro elige el escalón y la
//     "Velocidad máxima" cuántos escalones hay (con 0,5°/s solo 0,1 y 0,5)
//   · dirección en 8 sectores (N, NE, E…): en diagonal los dos ejes van a la misma velocidad
//   · zona muerta del 14 % para que un dedo quieto no mueva nada
//   · solo se manda una orden cuando cambia el escalón o el sector; cada 500 ms se repite la
//     misma para mantener vivo el vigilante del servidor (que para los ejes si hay silencio 1,2 s)
//   · al soltar, parada inmediata
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
    const ZONA_MUERTA = 0.14, LATIDO_MS = 500;
    const pad = document.getElementById("pad");
    if (!pad) return;
    const mando = document.getElementById("pad-mando");
    const svg = document.getElementById("pad-linea");
    const linea = svg.querySelector("line");
    const estado = document.getElementById("pad-estado");
    const invX = document.getElementById("inv-x"), invY = document.getElementById("inv-y");
    const speed = document.getElementById("speed");
    // Escalones de velocidad: las opciones del select, de menor a mayor (una sola fuente con los botones)
    const ESCALONES = Array.from(speed.options).map(o => parseFloat(o.value)).sort((a, b) => a - b);
    let puntero = null, ultimoVx = 0, ultimoVy = 0, temporizador = null;
    // Centro y radio del pad fijados al poner el dedo: si durante el arrastre la página se mueve
    // (en Cámara el pad va sobre la foto y el modo continuo la refresca), el mando no debe
    // interpretar ese desplazamiento como un movimiento del dedo
    let referencia = null;

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
    speed.addEventListener("change", ev => {
        try { localStorage.setItem("pad_velocidad", ev.target.value); } catch (e) {}
        pintarAnillos();
    });

    function formatear(v) { return fmtNum(v); }

    // Escalones disponibles con la velocidad máxima elegida
    function escalones() {
        const vmax = parseFloat(speed.value);
        const lista = ESCALONES.filter(v => v <= vmax + 1e-9);
        return lista.length ? lista : [vmax];
    }

    // Radio (0..1) donde termina el anillo k (el último llega al borde)
    function radioAnillo(k, n) { return ZONA_MUERTA + (k + 1) * (1 - ZONA_MUERTA) / n; }

    // Anillos y sus velocidades dibujados en el SVG, uno por escalón
    function pintarAnillos() {
        svg.querySelectorAll(".pad-anillo-svg, .pad-anillo-texto").forEach(e => e.remove());
        const lista = escalones(), n = lista.length;
        for (let k = 0; k < n; k++) {
            const rFin = radioAnillo(k, n);
            if (k < n - 1) {
                const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                c.setAttribute("class", "pad-anillo-svg");
                c.setAttribute("cx", "50%"); c.setAttribute("cy", "50%");
                c.setAttribute("r", (rFin * 50) + "%");
                svg.insertBefore(c, linea);
            }
            // Etiqueta en la diagonal NE, a media altura del anillo
            const rMedio = (k === 0 ? ZONA_MUERTA : radioAnillo(k - 1, n)) * 0.5 + rFin * 0.5;
            const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
            t.setAttribute("class", "pad-anillo-texto");
            t.setAttribute("x", (50 + rMedio * 50 * Math.SQRT1_2) + "%");
            t.setAttribute("y", (50 - rMedio * 50 * Math.SQRT1_2) + "%");
            t.textContent = formatear(lista[k]);
            svg.insertBefore(t, linea);
        }
    }
    pintarAnillos();

    function pintar(nx, ny) {
        mando.style.left = (50 + nx * 50) + "%";
        mando.style.top = (50 + ny * 50) + "%";
        linea.setAttribute("x2", (50 + nx * 50) + "%");
        linea.setAttribute("y2", (50 + ny * 50) + "%");
    }

    function enviar(vx, vy, forzar) {
        if (!forzar && vx === ultimoVx && vy === ultimoVy) return;
        ultimoVx = vx; ultimoVy = vy;
        fetch(pad.dataset.urlMover || "/pad/mover", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vx, vy }) })
            .then(r => r.json())
            .then(d => { if (!d.ok) { estado.textContent = "❌ " + (d.mensaje || _("Error")); soltar(); } })
            .catch(() => { estado.textContent = _("❌ Sin respuesta del servidor"); });
    }

    function parar(porBeacon) {
        ultimoVx = ultimoVy = 0;
        const url = pad.dataset.urlParar || "/pad/parar";
        if (porBeacon && navigator.sendBeacon) { navigator.sendBeacon(url); return; }
        fetch(url, { method: "POST", keepalive: true }).catch(() => {});
    }

    function procesar(ev) {
        const { cx, cy, r } = referencia;
        let nx = (ev.clientX - cx) / r;
        let ny = (ev.clientY - cy) / r;
        let d = Math.hypot(nx, ny);
        if (d > 1) { nx /= d; ny /= d; d = 1; }        // el mando no sale del círculo
        pintar(nx, ny);

        // Escalón por la distancia al centro
        const lista = escalones(), n = lista.length;
        let v = 0;
        if (d >= ZONA_MUERTA) {
            const k = Math.min(n - 1, Math.floor((d - ZONA_MUERTA) / (1 - ZONA_MUERTA) * n));
            v = lista[k];
        }
        // Sector de 45° por el ángulo (arriba en pantalla = eje 1 positivo = N)
        let dx = 0, dy = 0;
        if (v > 0) {
            const sector = Math.round(Math.atan2(-ny, nx) / (Math.PI / 4));
            dx = Math.round(Math.cos(sector * Math.PI / 4));
            dy = Math.round(Math.sin(sector * Math.PI / 4));
        }
        const vx = dx * v * (invX.checked ? -1 : 1);
        const vy = dy * v * (invY.checked ? -1 : 1);
        svg.querySelectorAll(".pad-anillo-texto").forEach((t, i) => t.classList.toggle("activo", v > 0 && lista.indexOf(v) === i));
        estado.textContent = (v === 0) ? _("● Centro: parado") :
            [vx ? `↔ ${vx > 0 ? "E" : "W"}` : "", vy ? `↕ ${vy > 0 ? "N" : "S"}` : ""].filter(Boolean).join("   ") + `   ${formatear(v)}°/s`;
        enviar(vx, vy, false);
    }

    function soltar() {
        if (puntero === null) return;
        try { pad.releasePointerCapture(puntero); } catch (e) {}
        puntero = null;
        clearInterval(temporizador); temporizador = null;
        pad.classList.remove("activo");
        svg.querySelectorAll(".pad-anillo-texto").forEach(t => t.classList.remove("activo"));
        pintar(0, 0);
        parar(false);
        estado.textContent = _("● Parado");
        vibrar(20);
    }

    pad.addEventListener("pointerdown", ev => {
        if (puntero !== null) return;                   // un solo dedo
        puntero = ev.pointerId;
        const rect = pad.getBoundingClientRect();
        referencia = { cx: rect.left + rect.width / 2, cy: rect.top + rect.height / 2, r: rect.width / 2 };
        try { pad.setPointerCapture(puntero); } catch (e) {}
        pad.classList.add("activo");
        vibrar(30);
        procesar(ev);
        // Latido aunque el dedo esté quieto: mantiene vivo el vigilante del servidor
        temporizador = setInterval(() => { if (puntero !== null) enviar(ultimoVx, ultimoVy, true); }, LATIDO_MS);
        ev.preventDefault();
    });
    pad.addEventListener("pointermove", ev => { if (ev.pointerId === puntero) { procesar(ev); ev.preventDefault(); } });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(t => pad.addEventListener(t, ev => { if (ev.pointerId === puntero) soltar(); }));

    // Si la página se oculta o se cierra con el dedo puesto, parar la montura
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") { if (puntero !== null) soltar(); parar(true); } });
    window.addEventListener("pagehide", () => parar(true));
})();
