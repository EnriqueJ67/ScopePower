// static/js/humor.js — Nivel de humor de la voz del PC (humor.py), estilo TARS.
// Necesita {% include '_modal_humor.html' %} y el botón #nav-humor de la barra (base.html),
// y ayuda_voz.js para explicar los fallos del micrófono.
//
//   abrirHumor(ev)                 abre la ventana con el nivel actual del PC
//   fijarNivelHumor(n, origen)     POST /humor; el PC confirma por voz y la frase sale en la ventana
//   escucharNivelHumor()           escucha una frase («humor al sesenta por ciento») y fija el nivel
//   nivelDeFrase(texto)            0-100 a partir de lo dicho, o null si no hay número
(function () {
    let reconocimiento = null;
    let ultimaFrase = "", ultimaFraseHora = 0;

    const ERRORES_VOZ = {
        "not-allowed": "El navegador no deja usar el micrófono.",
        "service-not-allowed": "El reconocimiento de voz está desactivado en este navegador.",
        "network": "Chrome reconoce la voz en Internet: el móvil necesita datos o wifi con salida a Internet.",
        "no-speech": "No se ha oído nada. Vuelve a pulsar y habla enseguida.",
        "audio-capture": "No se encuentra ningún micrófono.",
        "aborted": "Escucha cancelada.",
        "language-not-supported": "El navegador no reconoce español.",
    };

    function $(id) { return document.getElementById(id); }
    function estado(texto, clase) {
        const e = $("humor-estado");
        if (!e) return;
        e.className = "estado mt-1" + (clase ? " " + clase : "");
        e.textContent = texto || "";
    }

    // ——— Pintado ———
    function pintarBarra(nivel) {
        const a = $("nav-humor"), t = $("nav-humor-texto");
        if (!a || !t) return;
        // En el móvil solo cabe el porcentaje bajo el icono; en pantallas anchas, «Humor 75 %»
        t.textContent = nivel > 0 ? `${nivel} %` : "Humor";
        const pre = $("nav-humor-prefijo");
        if (pre) pre.textContent = nivel > 0 ? "Humor " : "";
        a.classList.toggle("encendido", nivel > 0);
        a.title = nivel > 0 ? `Nivel de humor de ScopePower: ${nivel} %` : "Nivel de humor de ScopePower (apagado)";
    }
    window.pintarNivelHumor = function (nivel) {
        nivel = parseInt(nivel) || 0;
        if ($("humor-valor")) $("humor-valor").textContent = nivel;
        if ($("humor-rango") && parseInt($("humor-rango").value) !== nivel) $("humor-rango").value = nivel;
    };
    // La ventana no explica de dónde salen las frases: tienen que parecer cosa de la propia app
    function aplicarEstado(d) {
        pintarNivelHumor(d.nivel);
        pintarBarra(d.nivel);
        if (d.errores_csv) console.warn(`Humor: ${d.errores_csv} filas de frases_humor.csv con errores (ver el log del PC)`);
    }

    // ——— Servidor ———
    function cargar() {
        return fetch("/humor", { cache: "no-store" }).then(r => r.json()).then(d => { aplicarEstado(d); return d; });
    }
    window.abrirHumor = function (ev) {
        if (ev) ev.preventDefault();
        estado("");
        cargar().catch(err => estado("❌ " + err, "err"));
        $("modal-humor").classList.add("abierto");
    };
    window.cerrarHumor = function () {
        pararEscucha();
        $("modal-humor").classList.remove("abierto");
    };
    window.fijarNivelHumor = function (nivel, origen) {
        nivel = Math.max(0, Math.min(100, parseInt(nivel) || 0));
        pintarNivelHumor(nivel);
        estado("⏳ Ajustando...");
        return fetch("/humor", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nivel, origen: origen || "" }) })
            .then(r => r.json())
            .then(d => {
                if (!d.ok) { estado("❌ " + (d.error || "No se pudo cambiar el nivel"), "err"); return d; }
                aplicarEstado(d);
                estado(`✅ Humor al ${d.nivel} %` + (d.frase ? ` · «${d.frase}»` : ""), "ok");
                return d;
            })
            .catch(err => estado("❌ " + err, "err"));
    };
    window.probarHumor = function () {
        estado("🔊 Hablando por el altavoz del PC...");
        fetch("/humor/probar", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
            .then(r => r.json())
            .then(d => estado(d.frase ? `🔊 «${d.frase}»` : "❌ " + (d.error || "Sin frase"), d.frase ? "" : "err"))
            .catch(err => estado("❌ " + err, "err"));
    };

    // ——— Entender el nivel dicho en voz alta ———
    // Chrome suele devolver los números en cifras («humor al 60 %»), pero no siempre:
    // también se componen en palabras («sesenta y cinco por ciento»).
    const UNIDADES = { cero: 0, uno: 1, una: 1, dos: 2, tres: 3, cuatro: 4, cinco: 5, seis: 6, siete: 7, ocho: 8, nueve: 9,
        diez: 10, once: 11, doce: 12, trece: 13, catorce: 14, quince: 15, dieciseis: 16, diecisiete: 17, dieciocho: 18, diecinueve: 19,
        veinte: 20, veintiuno: 21, veintidos: 22, veintitres: 23, veinticuatro: 24, veinticinco: 25, veintiseis: 26, veintisiete: 27,
        veintiocho: 28, veintinueve: 29, treinta: 30, cuarenta: 40, cincuenta: 50, sesenta: 60, setenta: 70, ochenta: 80, noventa: 90,
        cien: 100, ciento: 100 };
    window.nivelDeFrase = function (texto) {
        let t = (texto || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[¿?.,!¡;:]/g, " ");
        t = t.replace(/(\d)\s*%/g, "$1 ").replace(/\s+/g, " ").trim();
        if (!t) return null;
        const cifras = t.match(/\b(\d{1,3})\b/);
        if (cifras) return rango(parseInt(cifras[1]));
        if (/\b(apaga|apagalo|quita|quitalo|nada|serio|desactiva|silencio)\b/.test(t)) return 0;
        if (/\b(maximo|al maximo|todo|a tope|total)\b/.test(t)) return 100;
        const palabras = t.split(" ");
        let valor = null;
        for (let i = 0; i < palabras.length; i++) {
            const p = palabras[i];
            if (!(p in UNIDADES)) continue;
            valor = UNIDADES[p];
            // «sesenta y cinco», «treinta y cinco»
            if (valor >= 30 && valor < 100 && palabras[i + 1] === "y" && palabras[i + 2] in UNIDADES && UNIDADES[palabras[i + 2]] < 10) {
                valor += UNIDADES[palabras[i + 2]];
            }
            break;
        }
        return valor === null ? null : rango(valor);
    };
    function rango(n) { return (n >= 0 && n <= 100) ? n : null; }

    // ——— Escucha ———
    function textoFinalDe(event) {
        for (let i = event.results.length - 1; i >= 0; i--) {
            const r = event.results[i];
            if (r.isFinal === undefined || r.isFinal) return r[0].transcript.trim();
        }
        return null;
    }
    function fraseRepetida(texto) {
        const ahora = Date.now();
        if (texto === ultimaFrase && ahora - ultimaFraseHora < 8000) return true;
        ultimaFrase = texto; ultimaFraseHora = ahora;
        return false;
    }
    function pararEscucha() {
        if (reconocimiento) { try { reconocimiento.stop(); } catch (e) {} reconocimiento = null; }
        if ($("humor-mic")) $("humor-mic").classList.remove("yendo");
    }
    window.escucharNivelHumor = function () {
        if (reconocimiento) { pararEscucha(); estado("Escucha cancelada."); return; }
        const Clase = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!Clase) { estado("❌ Este navegador no tiene reconocimiento de voz. En Android usa Chrome.", "err"); return; }
        const r = new Clase();
        r.lang = "es-ES";
        r.interimResults = false;
        reconocimiento = r;
        $("humor-mic").classList.add("yendo");
        estado("🎙️ Escuchando... di por ejemplo «humor al sesenta por ciento»");
        r.onresult = function (event) {
            const texto = textoFinalDe(event);
            if (!texto || fraseRepetida(texto)) return;
            pararEscucha();
            const nivel = nivelDeFrase(texto);
            if (nivel === null) { estado(`❓ He oído «${texto}», pero no un nivel. Di un número de 0 a 100.`, "warn"); return; }
            estado(`🎙️ «${texto}» → ${nivel} %`);
            fijarNivelHumor(nivel, "voz");
        };
        r.onerror = function (event) {
            pararEscucha();
            const msg = ERRORES_VOZ[event.error] || ("Error de reconocimiento: " + event.error);
            estado("❌ " + msg, "err");
            console.warn("Reconocimiento de voz (humor):", event.error, event.message || "");
            if (window.explicarFalloVoz) explicarFalloVoz(event.error);
        };
        r.onend = function () {
            if (reconocimiento === r) {
                pararEscucha();
                if (($("humor-estado").textContent || "").startsWith("🎙️ Escuchando")) estado("🎙️ No se ha entendido nada; prueba otra vez.", "warn");
            }
        };
        try { r.start(); } catch (e) { pararEscucha(); estado("❌ No se pudo iniciar la escucha: " + e.message, "err"); }
    };

    document.addEventListener("keydown", e => {
        if (e.key === "Escape" && $("modal-humor") && $("modal-humor").classList.contains("abierto")) cerrarHumor();
    });

    // Nivel actual en el botón de la barra al cargar la página
    if ($("nav-humor")) cargar().catch(() => {});
})();
