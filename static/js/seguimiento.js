// static/js/seguimiento.js
//
// Página de seguimiento de objetos en movimiento (experimental). Sondea
// /seguimiento/estado cada 700 ms y recarga la vista previa solo cuando el
// servidor tiene una nueva (contador `version`) y la anterior ya terminó de
// cargar: por el wifi del campo (0,5–2 Mbit/s) no conviene encolar imágenes.

let sondeo = null;
let ultimaVersion = -1;
let cargando = false;
let signos = window.SIGNOS_INICIALES || { az: 1, alt: 1 };

function el(id) { return document.getElementById(id); }

function iniciar() {
    const est = el("estado");
    est.className = "estado mt-2";
    est.innerHTML = '<span class="spinner"></span>Preparando cámara y montura...';
    el("btn-iniciar").disabled = true;
    const cuerpo = {
        backend: el("backend").value,
        exposicion_ms: parseFloat(el("exposicion").value),
        ganancia: parseInt(el("ganancia").value, 10),
        kp: parseFloat(el("kp").value), ki: parseFloat(el("ki").value),
        frecuencia_hz: parseFloat(el("frecuencia").value),
        grabar: el("grabar").checked, formato_video: el("formato-video").value, grabar_desde: el("grabar-desde").value,
        color: el("color").checked, exposicion_auto: el("exposicion-auto").checked,
        roi_px: parseInt(el("campo").value, 10),
        signo_az: signos.az, signo_alt: signos.alt,
    };
    fetch("/seguimiento/iniciar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) })
        .then(r => r.json())
        .then(d => {
            if (!d.ok) { est.className = "estado err mt-2"; est.textContent = "❌ " + (d.error || "No se pudo empezar"); el("btn-iniciar").disabled = false; return; }
            if (!sondeo) sondeo = setInterval(sondear, 700);
            sondear();
        })
        .catch(e => { est.className = "estado err mt-2"; est.textContent = "❌ " + e; el("btn-iniciar").disabled = false; });
}

function parar() {
    el("btn-parar").disabled = true;
    fetch("/seguimiento/parar", { method: "POST" }).catch(() => {});
}

function elegirObjetivo(ev) {
    const img = el("vista");
    const r = img.getBoundingClientRect();
    const fx = (ev.clientX - r.left) / r.width, fy = (ev.clientY - r.top) / r.height;
    fetch("/seguimiento/objetivo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fx, fy }) })
        .then(r => r.json()).then(d => { if (!d.ok) el("estado").textContent = "❌ " + (d.error || ""); }).catch(() => {});
}

function calibrarAstrometria() {
    const t = el("calibracion-texto");
    t.textContent = "Releyendo la última foto resuelta...";
    fetch("/seguimiento/calibrar/astrometria", { method: "POST" }).then(r => r.json()).then(d => {
        t.textContent = d.ok ? "✅ " + d.mensaje : "❌ " + (d.error || "No se pudo calibrar");
    }).catch(e => { t.textContent = "❌ " + e; });
}

function comprobarSdk() {
    const t = el("sdk-texto");
    el("btn-sdk").disabled = true;
    t.innerHTML = '<span class="spinner"></span>Foto por ASCOM y por el SDK de la misma escena...';
    fetch("/seguimiento/orientacion-sdk", { method: "POST" }).then(r => r.json()).then(d => {
        t.textContent = d.ok ? (d.resultado.fiable ? "✅ " : "⚠️ ") + d.mensaje : "❌ " + (d.error || "No se pudo comprobar");
    }).catch(e => { t.textContent = "❌ " + e; }).finally(() => { el("btn-sdk").disabled = false; });
}

function calibrarEmpirica() {
    const t = el("calibracion-texto");
    t.textContent = "Moviendo los ejes y midiendo...";
    fetch("/seguimiento/calibrar/empirica", { method: "POST" }).then(r => r.json()).then(d => {
        if (!d.ok) t.textContent = "❌ " + (d.error || "No se pudo calibrar");
    }).catch(e => { t.textContent = "❌ " + e; });
}

function cambiarSigno(eje) {
    signos[eje] = -signos[eje];
    el("signo-" + eje).textContent = signos[eje] > 0 ? "+" : "−";
    fetch("/seguimiento/signos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(signos) }).catch(() => {});
}

function texto(v, dec) {
    if (v === null || v === undefined) return "—";
    if (Array.isArray(v)) return v.map(x => texto(x, dec)).join(" / ");
    if (typeof v === "number") return v.toFixed(dec === undefined ? 2 : dec).replace(".", ",");
    return String(v);
}

const NOMBRES_FASE = { parado: "Parado", preparando: "Preparando", buscando: "Buscando", manual: "Pad", bloqueado: "Bloqueado",
                       perdido: "Perdido", calibrando: "Calibrando", parando: "Parando" };
const CLASE_FASE = { parado: "", preparando: "chip-busy", buscando: "chip-warn chip-busy", manual: "chip-info", bloqueado: "chip-ok",
                     perdido: "chip-err", calibrando: "chip-info chip-busy", parando: "chip-busy" };

function pintar(e) {
    const activo = !!e.en_curso;
    el("btn-iniciar").hidden = activo;
    el("btn-iniciar").disabled = false;
    el("btn-parar").hidden = !activo;
    if (activo) el("btn-parar").disabled = false;
    el("btn-cal-empirica").disabled = !activo;
    el("btn-sdk").disabled = activo;
    ["campo", "backend", "exposicion", "ganancia", "kp", "ki", "frecuencia", "grabar", "formato-video", "grabar-desde", "color", "exposicion-auto"].forEach(id => el(id).disabled = activo);
    el("chip-fase").className = "chip " + (CLASE_FASE[e.fase] || "");
    el("chip-fase-texto").textContent = NOMBRES_FASE[e.fase] || e.fase;
    const est = el("estado");
    if (activo) { est.className = "estado mt-2"; est.innerHTML = '<span class="spinner"></span>' + (e.mensaje || ""); }
    else if (e.terminado) { est.className = "estado " + (e.error ? "err" : "ok") + " mt-2"; est.textContent = (e.error ? "❌ " : "✅ ") + e.mensaje; }
    const t = e.telemetria || {};
    el("t-fase").textContent = NOMBRES_FASE[e.fase] || e.fase;
    el("t-fps").textContent = texto(t.fps, 1);
    el("t-error").textContent = texto(t.error_px, 1);
    el("t-manchas").textContent = texto(t.manchas, 0);
    el("t-v").textContent = texto([t.v_az, t.v_alt], 3);
    el("t-integral").textContent = texto(t.integral, 3);
    el("t-vobj").textContent = texto(t.v_obj_px_s, 1);
    el("t-alt").textContent = t.alt_deg === null || t.alt_deg === undefined ? "—" : texto(t.alt_deg, 1) + "°";
    const m = t.montura || {};
    el("t-lat").textContent = t.latencia_ms === null || t.latencia_ms === undefined ? "—" : texto(t.latencia_ms, 0) + " ms (" + (m.backend || "") + ")";
    el("t-ordenes").textContent = texto(m.enviadas, 0) + " / " + texto(m.errores, 0);
    el("t-exp").textContent = (t.exposicion_ms === undefined ? "—" : texto(t.exposicion_ms, 2) + " ms")
        + (t.pico === null || t.pico === undefined ? "" : " · pico " + texto(t.pico, 0) + " %" + (t.saturada ? " (saturado)" : ""))
        + (t.fondo === undefined ? "" : ` · fondo ${texto(t.fondo, 0)} ±${texto(t.ruido, 1)}, máx ${texto(t.maximo, 0)} (de 255)`);
    el("t-desc").textContent = texto(t.descartados, 0);
    const g = e.grabacion;
    el("t-video").textContent = !g ? (e.error_grabacion ? "⚠️ Sin grabación: " + e.error_grabacion
                                       : (activo && el("grabar").checked ? "esperando al bloqueo" : "—"))
        : `${g.nombre} · ${texto(g.frames, 0)} fotogramas · ${texto(g.mb, 1)} MB` + (g.descartados ? ` · ${g.descartados} descartados` : "")
          + (g.libre_gb !== null && g.libre_gb !== undefined ? ` · ${texto(g.libre_gb, 1)} GB libres` : "") + (g.error ? ` · ⚠️ ${g.error}` : "") + (g.activa ? "" : " · cerrada");
    if (e.calibracion) {
        const c = e.calibracion;
        signos = { az: c.signo_az || 1, alt: c.signo_alt || 1 };
        el("signo-az").textContent = signos.az > 0 ? "+" : "−";
        el("signo-alt").textContent = signos.alt > 0 ? "+" : "−";
    }
    if (e.version !== ultimaVersion && !cargando) {
        ultimaVersion = e.version;
        cargando = true;
        const img = el("vista");
        img.onload = img.onerror = () => { cargando = false; };
        img.src = "/seguimiento/vista?" + Date.now();
        el("visor-pie").textContent = activo ? `Toca el objeto para elegirlo · ${texto(t.lado, 0)} px de lado` : "Última vista previa";
    }
    if (!activo && sondeo && e.terminado) { clearInterval(sondeo); sondeo = null; }
}

function sondear() {
    fetch("/seguimiento/estado").then(r => r.json()).then(pintar).catch(() => {});
}

document.addEventListener("DOMContentLoaded", () => {
    sondear();
    sondeo = setInterval(sondear, 700);
});
