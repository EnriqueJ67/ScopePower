// Centrado automático en bucle cerrado: arranque, sondeo y presentación.
// Lo usan goto.html (botón "Centrado Automático" y el centrado que el servidor
// lanza solo al llegar el GoTo) y modo_automatico.html.
//
//   iniciarCentrado(idEstado, opciones)
//     idEstado : id del elemento donde pintar el progreso y el resultado
//     opciones : { alTerminar(estado), hablar: true|false, siguientePaso: true|false }
//                siguientePaso: al terminar, botones para ir al apilado o a la foto de prueba
//
//   engancharCentradoEnCurso(idEstado, opciones)
//     Se engancha a un centrado que haya lanzado el servidor. Con
//     opciones.esperarArranqueS = N sigue sondeando hasta N segundos aunque todavía
//     no haya empezado: mientras tanto enseña la fase previa que anuncia el servidor
//     (esperando a la montura, asentamiento) o el motivo por el que se omite.
//
// El servidor (/centrado/estado) informa en todo momento: fase previa, vuelta,
// paso dentro de la vuelta (foto / resolver / medir / corregir / asentar), segundos
// que lleva en ese paso y cuenta atrás de la exposición.

(function () {
    let sondeo = null;        // { temporizador, el, opciones, vistoEnCurso, pintado, limite }

    const PASOS = [
        ["foto", "Foto"], ["resolver", "Resolver"], ["medir", "Medir"], ["corregir", "Corregir"]];

    function textoResultado(e) {
        const err = e.error_final !== null && e.error_final !== undefined ? `${e.error_final.toFixed(1)}'` : "?";
        switch (e.resultado) {
            case "centrado": return { color: "var(--ok)", icono: "✅", texto: `Centrado: ${e.detalle}` };
            case "fuera_tolerancia": return { color: "var(--warn)", icono: "🟡", texto: `Mejorado pero fuera de tolerancia: ${e.detalle}` };
            case "sin_mejora": return { color: "var(--warn)", icono: "🟠", texto: `Sin mejora: ${e.detalle}` };
            case "cancelado": return { color: "var(--text-3)", icono: "⏹️", texto: e.detalle };
            default: return { color: "var(--err)", icono: "❌", texto: e.detalle || ("Error (" + err + ")") };
        }
    }

    // Miniatura de la última foto del centrado (centrado.py la genera en cada vuelta)
    function htmlImagen(e) {
        if (!e.imagen) return "";
        return `<div class="centrado-foto"><a href="/centrado/imagen?v=${e.imagen.version}" target="_blank" title="Abrir a tamaño completo">` +
            `<img src="/centrado/imagen?v=${e.imagen.version}" alt="Foto del centrado"></a>` +
            `<div class="centrado-foto-pie">Foto del centrado · vuelta ${e.imagen.vuelta} · ${e.imagen.hora}</div></div>`;
    }
    function htmlSiguiente(opciones) {
        if (!opciones.siguientePaso) return "";
        return `<div class="centrado-siguiente"><div class="seccion-titulo">Siguiente paso</div><div class="acciones">` +
            `<a class="btn btn-primary" href="/apilado"><svg><use href="#i-apilado"/></svg>Empezar apilado</a>` +
            `<a class="btn btn-secondary" href="/foto-prueba"><svg><use href="#i-foto"/></svg>Cámara</a></div></div>`;
    }

    // Los cuatro pasos de cada vuelta, con el que está en marcha resaltado y los
    // anteriores marcados: así se ve de un vistazo en qué punto va el bucle.
    function htmlPasos(e) {
        const actual = e.paso === "asentar" ? "corregir" : e.paso;
        let idx = PASOS.findIndex(p => p[0] === actual);
        return `<div class="centrado-pasos">` + PASOS.map((p, i) => {
            const clase = i === idx ? "paso activo" : (idx >= 0 && i < idx ? "paso hecho" : "paso");
            return `<span class="${clase}">${i < idx && idx >= 0 ? "✓ " : (i + 1) + ". "}${p[1]}</span>`;
        }).join("") + `</div>`;
    }

    function pintarProgreso(el, e) {
        const errores = e.errores.length ? " · errores: " + e.errores.map(x => x.toFixed(1) + "'").join(" → ") : "";
        let tiempo = "";
        if (e.captura && e.captura.en_curso) tiempo = ` (quedan ${Math.ceil(e.captura.restante)} s)`;
        else if (e.paso_s >= 2) tiempo = ` (${Math.round(e.paso_s)} s)`;
        const vuelta = e.max_vueltas ? `<b>Vuelta ${e.vuelta} de ${e.max_vueltas}</b> · ` : "";
        el.innerHTML = `<span class="spinner"></span> ${vuelta}centrando por plate solving, espera a que termine<br>` +
            `<span class="centrado-mensaje">${e.mensaje}${tiempo}</span>` + htmlPasos(e) +
            `<small>Objetivo: ${e.objeto || "?"} · tolerancia ${e.tolerancia}'${errores}</small><br>` +
            `<button type="button" class="btn btn-sm btn-ghost" onclick="cancelarCentrado()">Cancelar centrado</button>` +
            htmlImagen(e);
    }

    // Fase previa: el servidor todavía no ha empezado a hacer fotos (espera a la
    // montura, asentamiento). Cuenta atrás si se sabe cuánto falta.
    function pintarPrevio(el, e) {
        const p = e.previo;
        let tiempo = "";
        if (p.restante !== null && p.restante !== undefined) tiempo = ` (quedan ${Math.ceil(p.restante)} s)`;
        else if (p.transcurrido >= 2) tiempo = ` (${Math.round(p.transcurrido)} s)`;
        el.innerHTML = `<span class="spinner"></span> <b>Preparando el centrado</b><br>` +
            `<span class="centrado-mensaje">${p.mensaje}${tiempo}</span>` +
            `<small>Después vendrán las vueltas de plate solving; espera a que termine.</small>`;
    }

    function pintarOmitido(el, e) {
        el.innerHTML = `<span style="color:var(--text-3)">ℹ️ ${e.omitido.mensaje}</span>`;
    }

    function pintarFinal(el, e, opciones) {
        const r = textoResultado(e);
        const errores = e.errores.length ? `<br><small>Error por vuelta: ${e.errores.map(x => x.toFixed(1) + "'").join(" → ")}</small>` : "";
        el.innerHTML = `<span style="color:${r.color}; font-weight:bold;">${r.icono} ${r.texto}</span>${errores}` +
            htmlImagen(e) + htmlSiguiente(opciones);
        // El PC ya dice el resultado por su altavoz (centrado.py); el móvil solo lo
        // lee si la voz del PC está apagada (window.vozPCActiva la pone base.html)
        if (opciones.hablar && !window.vozPCActiva && "speechSynthesis" in window) {
            const u = new SpeechSynthesisUtterance(r.texto.replace(/'/g, " minutos"));
            u.lang = "es-ES"; u.rate = 0.9;
            speechSynthesis.speak(u);
        }
        if (opciones.alTerminar) opciones.alTerminar(e);
    }

    function parar() {
        if (sondeo && sondeo.temporizador) clearInterval(sondeo.temporizador);
        sondeo = null;
    }

    function sondear() {
        const s = sondeo;
        if (!s) return;
        fetch("/centrado/estado").then(r => r.json()).then(e => {
            if (sondeo !== s) return;                       // alguien arrancó otro sondeo mientras tanto
            if (e.en_curso) { s.vistoEnCurso = true; s.pintado = true; pintarProgreso(s.el, e); return; }
            if (e.previo) {
                // Aún no ha empezado: seguir esperando (lo que diga el servidor + margen)
                s.pintado = true;
                s.limite = Math.max(s.limite, Date.now() + 1000 * ((e.previo.restante || 0) + 60));
                pintarPrevio(s.el, e);
                return;
            }
            if (e.terminado && s.vistoEnCurso) { parar(); pintarFinal(s.el, e, s.opciones); return; }
            if (e.omitido && (s.pintado || e.omitido.hace < 30)) {
                parar(); pintarOmitido(s.el, e);
                if (s.opciones.alTerminar) s.opciones.alTerminar(null);
                return;
            }
            if (Date.now() > s.limite) {
                // No ha llegado a arrancar y el servidor no dice por qué: no dejar un spinner colgado
                parar();
                if (s.pintado) s.el.innerHTML = "";
                if (s.opciones.alTerminar) s.opciones.alTerminar(null);
            }
        }).catch(err => console.warn("Error leyendo el estado del centrado:", err));
    }

    function arrancarSondeo(el, opciones, vistoEnCurso, esperaS) {
        parar();
        sondeo = { el, opciones: opciones || {}, vistoEnCurso: !!vistoEnCurso, pintado: false,
                   limite: Date.now() + 1000 * (esperaS || 0) };
        sondeo.temporizador = setInterval(sondear, 1000);
        sondear();
    }

    window.iniciarCentrado = function (idEstado, opciones) {
        opciones = opciones || {};
        const el = document.getElementById(idEstado);
        parar();
        el.innerHTML = `<span class="spinner"></span> Iniciando centrado automático...`;
        fetch("/centrado/iniciar", { method: "POST" })
            .then(r => r.json())
            .then(d => {
                if (!d.ok) {
                    el.innerHTML = `<span style="color:var(--err)">❌ ${d.error || "No se pudo iniciar el centrado"}</span>`;
                    if (opciones.alTerminar) opciones.alTerminar(null);
                    return;
                }
                arrancarSondeo(el, opciones, true, 30);
            })
            .catch(err => {
                el.innerHTML = `<span style="color:var(--err)">❌ Error al iniciar el centrado: ${err}</span>`;
                if (opciones.alTerminar) opciones.alTerminar(null);
            });
    };

    // Engancharse a un centrado que lanza (o va a lanzar) el servidor. Sin
    // esperarArranqueS solo se engancha si ya está en marcha o en la fase previa.
    window.engancharCentradoEnCurso = function (idEstado, opciones) {
        opciones = opciones || {};
        const el = document.getElementById(idEstado);
        if (sondeo && sondeo.el === el && sondeo.vistoEnCurso) return;   // ya lo seguimos
        arrancarSondeo(el, opciones, false, opciones.esperarArranqueS || 0);
    };

    // Deja de seguir el centrado y vacía el hueco (p. ej. al lanzar otro GoTo)
    window.olvidarCentrado = function (idEstado) {
        parar();
        const el = idEstado ? document.getElementById(idEstado) : null;
        if (el) el.innerHTML = "";
    };

    window.cancelarCentrado = function () {
        fetch("/centrado/cancelar", { method: "POST" }).catch(console.error);
    };
})();
