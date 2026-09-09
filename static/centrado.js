// Centrado automático en bucle cerrado: arranque, sondeo y presentación.
// Lo usan goto.html (botón "Centrado Automático") y modo_automatico.html.
//
//   iniciarCentrado(idEstado, opciones)
//     idEstado : id del elemento donde pintar el progreso y el resultado
//     opciones : { alTerminar(estado), hablar: true|false, siguientePaso: true|false }
//                siguientePaso: al terminar, botones para ir al apilado o a la foto de prueba

(function () {
    let sondeo = null;

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
            `<a class="btn btn-secondary" href="/foto-prueba"><svg><use href="#i-foto"/></svg>Foto de prueba</a></div></div>`;
    }

    function pintarProgreso(el, e) {
        const errores = e.errores.length ? " · errores: " + e.errores.map(x => x.toFixed(1) + "'").join(" → ") : "";
        const cuenta = (e.captura && e.captura.en_curso) ? ` (quedan ${Math.ceil(e.captura.restante)} s)` : "";
        el.innerHTML = `<span class="spinner"></span> ${e.mensaje}${cuenta}<br>` +
            `<small>Objetivo: ${e.objeto || "?"} · tolerancia ${e.tolerancia}'${errores}</small><br>` +
            `<button type="button" class="btn btn-sm btn-ghost" onclick="cancelarCentrado()">Cancelar centrado</button>` +
            htmlImagen(e);
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

    function sondear(el, opciones) {
        fetch("/centrado/estado").then(r => r.json()).then(e => {
            if (e.en_curso) { pintarProgreso(el, e); return; }
            clearInterval(sondeo); sondeo = null;
            if (e.terminado) pintarFinal(el, e, opciones);
        }).catch(err => console.warn("Error leyendo el estado del centrado:", err));
    }

    window.iniciarCentrado = function (idEstado, opciones) {
        opciones = opciones || {};
        const el = document.getElementById(idEstado);
        el.innerHTML = `<span class="spinner"></span> Iniciando centrado automático...`;
        fetch("/centrado/iniciar", { method: "POST" })
            .then(r => r.json())
            .then(d => {
                if (!d.ok) {
                    el.innerHTML = `<span style="color:var(--err)">❌ ${d.error || "No se pudo iniciar el centrado"}</span>`;
                    if (opciones.alTerminar) opciones.alTerminar(null);
                    return;
                }
                if (!sondeo) sondeo = setInterval(() => sondear(el, opciones), 1000);
                sondear(el, opciones);
            })
            .catch(err => {
                el.innerHTML = `<span style="color:var(--err)">❌ Error al iniciar el centrado: ${err}</span>`;
                if (opciones.alTerminar) opciones.alTerminar(null);
            });
    };

    // Si al abrir la página ya hay un centrado en marcha, engancharse a él
    window.engancharCentradoEnCurso = function (idEstado, opciones) {
        fetch("/centrado/estado").then(r => r.json()).then(e => {
            if (e.en_curso && !sondeo) {
                sondeo = setInterval(() => sondear(document.getElementById(idEstado), opciones || {}), 1000);
            }
        }).catch(() => { });
    };

    window.cancelarCentrado = function () {
        fetch("/centrado/cancelar", { method: "POST" }).catch(console.error);
    };
})();
