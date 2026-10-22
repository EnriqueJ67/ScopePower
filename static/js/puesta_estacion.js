// static/js/puesta_estacion.js: asistente de puesta en estación (modal de templates/_puesta_estacion.html).
// Sondea /puesta-estacion/estado y pinta los tres pasos; el trabajo está en puesta_estacion.py.
// Los signos ya vienen del servidor en convención de presentación: err_alt > 0 → bajar la montura;
// err_az > 0 → el eje está al este del polo → girar hacia el oeste. Aquí solo se miran signos y campos.
// La tableta de campo lleva un Chrome antiguo: sin ?. ni ??.
(function () {
    "use strict";
    const $ = id => document.getElementById(id);
    const PASOS = ["preparacion", "medicion", "ajuste"];
    const E = { paso: 0, sondeo: null, version: 0, abierto: false, enCurso: false, fase: "", norte: true, terminado: false };

    function modal() { return $("pe-modal"); }

    // ——— Tarjeta de Alineación: solo con montura ecuatorial ———
    function esEcuatorial(d) {
        return !!(d && d.montura && (d.modo_montura === "ecuatorial" || d.modo_montura === "ecuatorial_alemana"));
    }
    function mostrarTarjeta(d) {
        const card = $("pe-card");
        if (card) card.hidden = !esEcuatorial(d);
    }

    function gradosMinutos(lat) {
        const a = Math.abs(lat), g = Math.floor(a), m = Math.round((a - g) * 60);
        return g + "° " + (m < 10 ? "0" : "") + m + "′ " + (lat >= 0 ? _("N") : _("S"));
    }
    function fmtArcmin(v) {
        if (v >= 60) return fmtNum(v / 60, 2) + "°";
        return fmtNum(v, v < 10 ? 1 : 0) + "′";
    }

    // ——— Navegación ———
    function irA(i) {
        E.paso = Math.max(0, Math.min(PASOS.length - 1, i));
        const nombre = PASOS[E.paso];
        document.querySelectorAll("#pe-modal .asis-paso").forEach(s => { s.hidden = s.dataset.paso !== nombre; });
        $("pe-puntos").innerHTML = PASOS.map((__, j) => '<span class="' + (j < E.paso ? "hecho" : j === E.paso ? "actual" : "") + '"></span>').join("");
        $("pe-contador").textContent = (E.paso + 1) + " / " + PASOS.length;
        pintarBotones();
        const caja = document.querySelector("#pe-modal .modal-caja");
        if (caja) caja.scrollTop = 0;
    }

    function pintarBotones() {
        const nombre = PASOS[E.paso];
        const ajustando = E.enCurso && E.fase === "ajustando";
        $("pe-empezar").hidden = nombre !== "preparacion" || E.enCurso;
        $("pe-cancelar").hidden = !E.enCurso || nombre === "ajuste";
        $("pe-ajustar").hidden = !(nombre === "medicion" && ajustando);
        $("pe-repetir").hidden = !(nombre === "ajuste" && ajustando);
        $("pe-terminar").hidden = !(nombre === "ajuste" && ajustando);
        $("pe-cerrar").hidden = !(E.terminado && !E.enCurso);
    }

    function mostrarAviso(texto, clase) {
        const a = $("pe-aviso");
        a.className = "aviso mt-2 " + (clase || "aviso-err");
        a.textContent = texto;
        a.hidden = false;
    }
    function ocultarAviso() { $("pe-aviso").hidden = true; }

    function abrirPuestaEstacion() {
        $("pe-latitud").textContent = gradosMinutos(parseFloat(modal().dataset.latitud) || 0);
        $("pe-aviso-hora").hidden = !(window.desfaseHoraPC != null && Math.abs(window.desfaseHoraPC) > 60);
        ocultarAviso();
        E.abierto = true;
        E.terminado = false;
        modal().classList.add("abierto");
        if (E.enCurso) {
            irA(E.fase === "ajustando" ? 2 : 1);
            arrancarSondeo();
        } else {
            irA(0);
        }
    }

    function cerrarPuestaEstacion() {
        if (E.enCurso) {
            if (!confirm(_("¿Cancelar la puesta en estación?"))) return;
            peCancelar();
        }
        modal().classList.remove("abierto");
        E.abierto = false;
        pararSondeo();
    }

    // ——— Órdenes ———
    function ajustes() {
        return {
            distancia_grados: parseFloat($("pe-distancia").value),
            velocidad_eje: parseFloat($("pe-velocidad").value),
            tolerancia_arcmin: parseFloat($("pe-tolerancia").value),
            hacia_el_este: $("pe-este").checked,
            voz: $("pe-voz").checked
        };
    }

    function enviar(ruta, cuerpo) {
        const opciones = { method: "POST" };
        if (cuerpo) {
            opciones.headers = { "Content-Type": "application/json" };
            opciones.body = JSON.stringify(cuerpo);
        }
        return fetch(ruta, opciones).then(r => r.json().catch(() => ({})).then(d => ({ ok: r.ok && d.ok !== false, d: d })));
    }

    function peEmpezar() {
        ocultarAviso();
        $("pe-empezar").disabled = true;
        enviar("/puesta-estacion/iniciar", ajustes()).then(({ ok, d }) => {
            $("pe-empezar").disabled = false;
            if (!ok) { mostrarAviso(d.error || _("No se pudo empezar la puesta en estación")); return; }
            E.enCurso = true;
            E.fase = "comprobando";
            E.terminado = false;
            reiniciarMedicion();
            irA(1);
            arrancarSondeo();
        }).catch(err => {
            $("pe-empezar").disabled = false;
            mostrarAviso(_("Error: {e}", { e: err }));
        });
    }
    function peCancelar() { enviar("/puesta-estacion/cancelar").catch(() => {}); }
    function peAjustar() { irA(2); }
    function peRepetir() {
        enviar("/puesta-estacion/repetir").then(({ ok, d }) => {
            if (!ok) { mostrarAviso(d.error || _("No se pudo repetir la medición")); return; }
            reiniciarMedicion();
            irA(1);
        }).catch(() => {});
    }
    function peTerminar() {
        enviar("/puesta-estacion/terminar").then(({ ok, d }) => {
            if (!ok) mostrarAviso(d.error || _("No se pudo terminar"));
        }).catch(() => {});
    }

    // ——— Sondeo ———
    function arrancarSondeo() {
        pararSondeo();
        sondear();
        E.sondeo = setInterval(sondear, 1000);
    }
    function pararSondeo() {
        if (E.sondeo) clearInterval(E.sondeo);
        E.sondeo = null;
    }
    function sondear() {
        fetch("/puesta-estacion/estado", { cache: "no-store" }).then(r => r.json()).then(pintar).catch(() => {});
    }

    function reiniciarMedicion() {
        document.querySelectorAll("#pe-fotos .paso").forEach(el => { el.className = "paso"; });
        $("pe-resultado").hidden = true;
        $("pe-progreso").hidden = true;
        $("pe-mensaje").hidden = false;
        $("pe-avisos-medicion").innerHTML = "";
        $("pe-avisos").innerHTML = "";
    }

    function pintar(e) {
        E.enCurso = e.en_curso;
        E.fase = e.fase;
        E.norte = e.hemisferio !== "sur";
        if (e.terminado && !e.en_curso) { pintarFinal(e); return; }
        if (!e.en_curso) return;
        pintarMedicion(e);
        if (e.fase === "ajustando") pintarAjuste(e);
        pintarBotones();
    }

    const ESTADOS_ACTIVOS = ["moviendo", "asentando", "exponiendo", "resolviendo"];
    function pintarMedicion(e) {
        document.querySelectorAll("#pe-fotos .paso").forEach((el, i) => {
            const p = e.puntos[i] || {};
            el.className = "paso" + (p.estado === "ok" ? " hecho" : ESTADOS_ACTIVOS.indexOf(p.estado) >= 0 ? " activo" : "");
        });
        let texto = e.mensaje || "";
        if (e.captura && e.captura.en_curso) texto += " " + _("(quedan {s} s)", { s: Math.ceil(e.captura.restante) });
        $("pe-mensaje-texto").textContent = texto;
        $("pe-mensaje").hidden = !!e.medicion;
        const av = e.avance;
        $("pe-progreso").hidden = !av;
        if (av && av.objetivo) $("pe-barra").style.width = Math.min(100, 100 * av.recorrido / av.objetivo) + "%";
        pintarAvisos(e, "pe-avisos-medicion");
        const m = e.medicion;
        $("pe-resultado").hidden = !m;
        if (m) {
            $("pe-resultado-texto").textContent = fraseMedicion(m);
            $("pe-resultado-total").textContent = _("Error total: {t}′", { t: fmtNum(m.total_arcmin, 1) });
        }
    }

    function fraseMedicion(m) {
        const alt = m.err_alt_arcmin, az = m.err_az_arcmin;
        const pAlt = alt > 0 ? _("{v}′ por encima del polo", { v: fmtNum(Math.abs(alt), 1) })
            : _("{v}′ por debajo del polo", { v: fmtNum(Math.abs(alt), 1) });
        const pAz = az > 0 ? _("{v}′ al este", { v: fmtNum(Math.abs(az), 1) })
            : _("{v}′ al oeste", { v: fmtNum(Math.abs(az), 1) });
        return _("El eje está {alt} y {az}", { alt: pAlt, az: pAz });
    }

    function pintarAjuste(e) {
        const a = e.ajuste || e.medicion;   // antes de la primera foto del ajuste vale la medición
        const tol = e.tolerancia_arcmin || 2;
        if (!a) return;
        const alt = a.err_alt_arcmin, az = a.err_az_arcmin, total = a.total_arcmin;
        instruccion("alt", alt, alt > 0 ? _("Baja la montura") : _("Sube la montura"), alt > 0 ? "↓" : "↑", _("Altura"));
        // Mirando al polo norte desde detrás de la montura el oeste queda a la izquierda; al polo sur, a la derecha
        const haciaOeste = az > 0;
        instruccion("az", az, haciaOeste ? _("Gira hacia el oeste") : _("Gira hacia el este"),
            (haciaOeste === E.norte) ? "←" : "→", _("Acimut"));
        const dentro = total <= tol;
        $("pe-semaforo").className = "pe-semaforo " + (dentro ? "verde" : total <= 3 * tol ? "ambar" : "rojo");
        $("pe-total").className = "pe-total" + (dentro ? " ok" : "");
        $("pe-total-texto").textContent = dentro
            ? _("Dentro de la tolerancia ({t}′): la montura está en estación", { t: fmtNum(tol, 1) })
            : _("Error total {v}′ · objetivo {t}′", { v: fmtNum(total, 1), t: fmtNum(tol, 1) });
        let estado = e.ajuste ? _("Foto {n} · {hora}", { n: e.ajuste.n, hora: e.ajuste.hora }) : _("Esperando la primera foto del ajuste...");
        if (e.subfase === "exponiendo") estado += " · " + _("exponiendo") + (e.captura && e.captura.en_curso ? " " + _("(quedan {s} s)", { s: Math.ceil(e.captura.restante) }) : "");
        else if (e.subfase === "resolviendo") estado += " · " + _("resolviendo");
        if (e.ajuste && e.ajuste.fallos_seguidos) estado += " · " + _("{n} sin resolver", { n: e.ajuste.fallos_seguidos });
        $("pe-estado-ajuste").textContent = estado;
        pintarAvisos(e, "pe-avisos");
        if (e.imagen && e.imagen.version !== E.version) {
            E.version = e.imagen.version;
            const img = $("pe-foto");
            img.src = "/puesta-estacion/imagen?v=" + E.version;
            img.hidden = false;
        }
    }

    function instruccion(eje, valor, texto, flecha, nombreEje) {
        const bien = Math.abs(valor) < 0.1;
        $("pe-flecha-" + eje).textContent = bien ? "✓" : flecha;
        $("pe-valor-" + eje).textContent = bien ? _("bien") : fmtArcmin(Math.abs(valor));
        $("pe-texto-" + eje).textContent = bien ? nombreEje : texto;
        $("pe-instr-" + eje).className = "pe-instr" + (bien ? " ok" : "");
    }

    function pintarAvisos(e, id) {
        const caja = $(id);
        const avisos = e.avisos || [];
        const html = avisos.map(a => '<div class="aviso aviso-warn mt-2"><svg class="ico"><use href="#i-info"/></svg><span></span></div>').join("");
        if (caja.dataset.n !== String(avisos.length) || caja.dataset.claves !== avisos.map(a => a.codigo).join(",")) {
            caja.innerHTML = html;
            caja.dataset.n = String(avisos.length);
            caja.dataset.claves = avisos.map(a => a.codigo).join(",");
        }
        caja.querySelectorAll(".aviso span").forEach((s, i) => { s.textContent = avisos[i].texto; });
    }

    function pintarFinal(e) {
        pararSondeo();
        E.enCurso = false;
        E.terminado = true;
        if (e.resultado === "completada") {
            mostrarAviso(e.detalle, "aviso-ok");
        } else if (e.resultado === "cancelado") {
            modal().classList.remove("abierto");
            E.abierto = false;
            E.terminado = false;
        } else {
            irA(0);
            mostrarAviso(e.detalle || _("La puesta en estación ha fallado"));
            E.terminado = false;
        }
        pintarBotones();
    }

    // ——— Arranque ———
    document.addEventListener("estado-equipo", ev => mostrarTarjeta(ev.detail));
    mostrarTarjeta(window.estadoEquipo);
    document.addEventListener("keydown", ev => { if (ev.key === "Escape" && E.abierto) cerrarPuestaEstacion(); });
    // Si ya hay una puesta en estación en marcha (la página se recargó), reabrir el modal donde toca
    fetch("/puesta-estacion/estado", { cache: "no-store" }).then(r => r.json()).then(e => {
        if (!e.en_curso) return;
        E.enCurso = true;
        E.fase = e.fase;
        abrirPuestaEstacion();
    }).catch(() => {});

    window.abrirPuestaEstacion = abrirPuestaEstacion;
    window.cerrarPuestaEstacion = cerrarPuestaEstacion;
    window.peEmpezar = peEmpezar;
    window.peCancelar = peCancelar;
    window.peAjustar = peAjustar;
    window.peRepetir = peRepetir;
    window.peTerminar = peTerminar;
})();
