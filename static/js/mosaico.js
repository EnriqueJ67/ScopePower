// static/js/mosaico.js — Modo Mosaico (ver mosaico.py y el panel "Mosaico" de Apilado).
//
// El objeto global Mosaico: formulario (centro, filas, columnas, solape, fotos por tesela),
// control del trabajo (empezar, pausar, saltar, detener), rejilla con el estado de cada
// tesela, recombinar y guardar. Solo se activa si la página tiene el panel (#panel-mosaico).
// apilado.html le pasa el estado en cada sondeo con Mosaico.actualizarEstado(estado.mosaico).
//
// Sin sintaxis moderna (?. ?? campos de clase): la tableta de campo lleva un Chrome antiguo.

(function () {
    if (!document.getElementById("panel-mosaico")) return;

    function post(url, cuerpo) {
        var opciones = { method: "POST", headers: { "Content-Type": "application/json" } };
        if (cuerpo !== undefined) opciones.body = JSON.stringify(cuerpo);
        return fetch(url, opciones).then(function (r) {
            return r.json().then(function (d) { d._status = r.status; return d; });
        });
    }

    var Mosaico = window.Mosaico = {};
    var $ = function (id) { return document.getElementById(id); };
    var datos = null;              // respuesta de GET /mosaico
    var estado = null;             // estado del trabajo (de /estado-apilado)
    var versionEstado = null;
    var timerPrevia = null;
    var fases = { goto: _("moviendo el telescopio"), centrando: _("centrando"), apilando: _("apilando"), combinando: _("combinando las teselas"),
                  guardando: _("guardando en la galería"), pausado: _("en pausa"), terminado: _("terminado") };
    var estadosTesela = { pendiente: _("pendiente"), en_curso: _("en curso"), hecha: _("hecha"), fallida: _("fallida") };

    function esc(t) { return String(t === undefined || t === null ? "" : t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/[>]/g, "&gt;").replace(/"/g, "&quot;"); }
    function error(t) { $("mosaico-error").textContent = t ? "⚠️ " + t : ""; }
    function num(id, defecto) { var v = parseFloat(String($(id).value).replace(",", ".")); return isFinite(v) ? v : defecto; }
    function grados(v) { return fmtNum(Math.round(v * 10) / 10, 1) + "°"; }
    function horasCortas(raH) { var h = Math.floor(raH), m = Math.round((raH - h) * 60); return h + "h" + (m < 10 ? "0" : "") + m + "m"; }
    function gradosCortos(d) { var a = Math.abs(d), g = Math.floor(a), m = Math.round((a - g) * 60); return (d < 0 ? "-" : "+") + g + "°" + (m < 10 ? "0" : "") + m + "'"; }

    // ——— Carga y formulario ———
    Mosaico.cargar = function () {
        return fetch("/mosaico").then(function (r) { return r.json(); }).then(function (d) {
            datos = d;
            var c = d.config || {};
            if (!$("mosaico-filas").value) $("mosaico-filas").value = c.filas;
            if (!$("mosaico-columnas").value) $("mosaico-columnas").value = c.columnas;
            if (!$("mosaico-solape").value) $("mosaico-solape").value = c.solape_pct;
            if (!$("mosaico-fotos").value) $("mosaico-fotos").value = c.fotos_por_tesela;
            if (!$("mosaico-centrar").dataset.tocado) $("mosaico-centrar").checked = !!c.centrar;
            if (!$("mosaico-guardar").dataset.tocado) $("mosaico-guardar").checked = !!c.guardar_galeria;
            pintarCentro();
            if (d.estado) Mosaico.actualizarEstado(d.estado);
            Mosaico.previsualizar();
        }).catch(function (e) { error(_("No se pudo leer el mosaico: {e}", { e: e })); });
    };

    // El servidor guarda la fuente del centro y el nombre por defecto en español: se traducen aquí
    function nombreCentro(c) { return c.nombre === "Mosaico" ? _("Mosaico") : c.nombre; }

    function pintarCentro() {
        if (!datos) return;
        var c = datos.centro || {};
        var campo = datos.campo || {};
        var t = "";
        if (c.error) t = c.error;
        else t = _("Centro: <b>{nombre}</b> ({fuente})", { nombre: esc(nombreCentro(c)), fuente: esc(_(c.fuente)) }) + " · RA " + horasCortas(c.ra_h) + " Dec " + gradosCortos(c.dec_deg);
        if (campo.ancho_deg) t += "<br>" + _("Campo de la cámara: {ancho} × {alto} ({origen})", { ancho: grados(campo.ancho_deg), alto: grados(campo.alto_deg), origen: esc(campo.origen) });
        $("mosaico-centro-texto").innerHTML = t;
        $("mosaico-nombre").placeholder = c.nombre ? nombreCentro(c) + " (" + _(c.fuente) + ")" : _("Objeto o coordenadas del centro");
    }

    function cuerpoFormulario() {
        var cuerpo = { filas: Math.round(num("mosaico-filas", 2)), columnas: Math.round(num("mosaico-columnas", 2)),
                       solape_pct: num("mosaico-solape", 20), fotos_por_tesela: Math.round(num("mosaico-fotos", 20)),
                       centrar: $("mosaico-centrar").checked, guardar_galeria: $("mosaico-guardar").checked };
        var nombre = $("mosaico-nombre").value.trim();
        if (nombre) cuerpo.nombre = nombre;
        return cuerpo;
    }

    // Previsualización (tamaño y giro previsto de la cámara) al cambiar el formulario
    Mosaico.previsualizar = function () {
        clearTimeout(timerPrevia);
        timerPrevia = setTimeout(function () {
            var cuerpo = cuerpoFormulario();
            post("/mosaico/previsualizar", cuerpo).then(function (d) {
                if (!d.ok) { $("mosaico-previa").textContent = d.error || ""; return; }
                var tam = d.tamano_deg || [0, 0];
                var n = d.filas * d.columnas;
                var t = _("Mosaico {filas}×{columnas} ({n} teselas): {ancho} × {alto}", { filas: d.filas, columnas: d.columnas, n: n, ancho: grados(tam[0]), alto: grados(tam[1]) }) +
                        " · " + _("cámara a {angulo}° ({origen})", { angulo: Math.round(d.angulo_deg), origen: d.origen_angulo });
                if (d.centro && cuerpo.nombre) t += " · " + _("centro {nombre}", { nombre: d.centro.nombre });
                $("mosaico-previa").textContent = t;
                if (!estado || !estado.en_curso) pintarRejilla(d.teselas, d.filas, d.columnas);
            }).catch(function () {});
        }, 300);
    };
    ["mosaico-filas", "mosaico-columnas", "mosaico-solape", "mosaico-nombre"].forEach(function (id) {
        $(id).addEventListener("input", Mosaico.previsualizar);
    });
    $("mosaico-nombre").addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); Mosaico.previsualizar(); } });
    $("mosaico-centrar").addEventListener("change", function () { this.dataset.tocado = "1"; });
    $("mosaico-guardar").addEventListener("change", function () { this.dataset.tocado = "1"; });
    Mosaico.cambiarFilas = function (id, delta) {
        var c = $(id);
        c.value = Math.min(6, Math.max(1, (parseInt(c.value) || 2) + delta));
        Mosaico.previsualizar();
    };

    // ——— Estado del trabajo ———
    Mosaico.actualizarEstado = function (e) {
        if (!e) return;
        var terminoAhora = estado && estado.en_curso && e.en_curso === false;
        estado = e;
        if (e.version_estado !== versionEstado) {
            versionEstado = e.version_estado;
            pintarEstado();
        }
        if (terminoAhora) Mosaico.cargar();
    };
    Mosaico.enCurso = function () { return !!(estado && estado.en_curso); };
    Mosaico.pausado = function () { return !!(estado && estado.pausado); };
    Mosaico.versionMosaico = function () { return estado ? estado.version_mosaico : null; };
    Mosaico.existe = function () { return !!(estado && estado.existe); };
    Mosaico.textoCabecera = function () {
        if (!estado || !estado.en_curso) return "";
        var n = estado.teselas ? estado.teselas.length : 0;
        var t = _("🧩 Mosaico {filas}×{columnas} de {nombre}", { filas: estado.filas, columnas: estado.columnas, nombre: estado.nombre });
        if (estado.actual !== null && estado.actual !== undefined) t += " · " + _("tesela {i}/{n}", { i: estado.actual + 1, n: n });
        if (estado.fase === "apilando") t += " " + estado.fotos + "/" + estado.fotos_por_tesela;
        t += " · " + (estado.pausado ? _("en pausa") : (fases[estado.fase] || estado.fase));
        return t;
    };

    function pintarRejilla(teselas, filas, columnas) {
        var caja = $("mosaico-rejilla");
        if (!teselas || !teselas.length || !filas || !columnas) { caja.innerHTML = ""; caja.hidden = true; return; }
        caja.style.gridTemplateColumns = "repeat(" + columnas + ", 1fr)";
        var porPos = {};
        teselas.forEach(function (t) { porPos[t.fila + "," + t.col] = t; });
        var html = "";
        for (var f = 0; f < filas; f++) {
            for (var c = 0; c < columnas; c++) {
                var t = porPos[f + "," + c];
                if (!t) { html += '<div class="tesela"></div>'; continue; }
                var est = t.estado || "prevista";
                var titulo = _("Tesela {i}", { i: t.i + 1 }) + " · RA " + horasCortas(t.ra_h) + " Dec " + gradosCortos(t.dec_deg) +
                             (t.estado ? " · " + (estadosTesela[t.estado] || t.estado) : "") + (t.motivo ? " · " + t.motivo : "");
                var texto = String(t.i + 1);
                if (t.estado === "hecha" || t.estado === "en_curso") texto += '<small>' + (t.fotos || 0) + (t.estado === "en_curso" && estado ? "/" + estado.fotos_por_tesela : "") + "</small>";
                else if (t.estado === "fallida") texto += "<small>✗</small>";
                html += '<div class="tesela ' + esc(est) + '" title="' + esc(titulo) + '">' + texto + "</div>";
            }
        }
        caja.innerHTML = html;
        caja.hidden = false;
    }

    function pintarEstado() {
        var enCurso = !!(estado && estado.en_curso);
        var pausado = !!(estado && estado.pausado);
        $("mosaico-btn-empezar").hidden = enCurso;
        $("mosaico-btn-pausar").hidden = !enCurso || pausado;
        $("mosaico-btn-reanudar").hidden = !enCurso || !pausado;
        $("mosaico-btn-saltar").hidden = !enCurso || estado.fase !== "apilando";
        $("mosaico-btn-detener").hidden = !enCurso;
        var hayTeselas = !!(estado && estado.teselas && estado.teselas.some(function (t) { return t.estado === "hecha"; }));
        $("mosaico-btn-recombinar").disabled = !hayTeselas || !!(estado && estado.combinando);
        $("mosaico-btn-guardar").disabled = !(estado && estado.existe) || enCurso;
        $("mosaico-btn-fits").classList.toggle("deshabilitado", !(estado && estado.existe));
        var form = $("mosaico-form");
        Array.prototype.forEach.call(form.querySelectorAll("input, button.mas-menos"), function (el) { el.disabled = enCurso; });
        var caja = $("mosaico-estado");
        if (enCurso) {
            var n = estado.teselas.length;
            var linea = "<b>" + esc(estado.nombre) + "</b> " + estado.filas + "×" + estado.columnas +
                        " · " + _("tesela {i}/{n}", { i: estado.actual !== null ? estado.actual + 1 : "-", n: n }) + " · " + esc(pausado ? _("en pausa") : (fases[estado.fase] || estado.fase));
            var html = "<div>" + linea + "</div>";
            if (estado.fase === "apilando" || estado.fase === "pausado") {
                var pct = Math.min(100, Math.round(100 * (estado.fotos || 0) / Math.max(1, estado.fotos_por_tesela)));
                html += '<div class="progress"><div class="progress-bar" style="width:' + pct + '%"></div></div>';
                html += '<div class="texto-2">' + _("{n} de {total} fotos de esta tesela", { n: estado.fotos || 0, total: estado.fotos_por_tesela }) + "</div>";
            }
            var ult = estado.eventos && estado.eventos.length ? estado.eventos[estado.eventos.length - 1] : null;
            if (ult) html += '<div class="texto-3 plan-evento">' + esc(ult.hora) + " " + esc(ult.texto) + "</div>";
            if (estado.combinando) html += '<div class="texto-3 plan-evento">' + _("🧩 Combinando el mosaico: {mensaje}", { mensaje: esc(estado.mensaje_combinar || "") }) + "</div>";
            else if (estado.ultimo_combinado && !estado.ultimo_combinado.error) html += '<div class="texto-3 plan-evento">' + _("🧩 Mosaico de {n} teselas, {ancho}×{alto} px ({hora})", { n: estado.ultimo_combinado.teselas, ancho: estado.ultimo_combinado.ancho, alto: estado.ultimo_combinado.alto, hora: esc(estado.ultimo_combinado.hora) }) + "</div>";
            caja.innerHTML = '<div class="mosaico-fin">' + html + "</div>";
            caja.hidden = false;
            $("mosaico-sub").textContent = _("tesela {i}/{n}", { i: estado.actual !== null ? estado.actual + 1 : "-", n: n }) + " · " + (pausado ? _("en pausa") : (fases[estado.fase] || ""));
        } else {
            var ultimo = estado && estado.eventos && estado.eventos.length ? estado.eventos[estado.eventos.length - 1] : null;
            var partes = [];
            if (ultimo && estado.fase === "terminado") partes.push('<div class="texto-2">' + esc(ultimo.hora) + " " + esc(ultimo.texto) + "</div>");
            if (estado && estado.combinando) partes.push('<div class="texto-3 plan-evento">' + _("🧩 Combinando el mosaico: {mensaje}", { mensaje: esc(estado.mensaje_combinar || "") }) + "</div>");
            else if (estado && estado.ultimo_combinado) {
                var u = estado.ultimo_combinado;
                partes.push('<div class="texto-3 plan-evento">' + (u.error ? _("⚠️ No se pudo combinar: {e}", { e: esc(u.error) })
                    : _("🧩 Mosaico de {n} teselas, {ancho}×{alto} px, {pct} % cubierto ({hora}, {s} s)", { n: u.teselas, ancho: u.ancho, alto: u.alto, pct: u.cobertura_pct, hora: esc(u.hora), s: u.segundos })) + "</div>");
            }
            if (estado && estado.existe && !estado.combinando) {
                var archivo = estado.archivo_galeria || "";
                var enlaces = '<button type="button" class="btn btn-sm btn-secondary" onclick="elegirVista(\'mosaico\')">' + _("Ver en el visor") + "</button>";
                if (archivo) {
                    enlaces += ' <a class="btn btn-sm btn-secondary" href="/galeria/compartir/' + encodeURIComponent(archivo) + '" target="_blank">' + _("Ver en la galería") + "</a>";
                    enlaces += ' <a class="btn btn-sm btn-ghost" href="/galeria/editar/' + encodeURIComponent(archivo) + '">' + _("Editar") + "</a>";
                } else if (estado.fase === "terminado") {
                    enlaces += ' <span class="texto-3">' + _("Sin guardar en la galería: usa «Guardar en la galería»") + "</span>";
                }
                partes.unshift('<a class="mosaico-mini" href="#" onclick="elegirVista(\'mosaico\'); return false;" title="' + _("Ver el mosaico en el visor") + '"><img src="/resultado-mosaico?' + (estado.version_mosaico || 0) + '" alt=""></a>');
                partes.push('<div class="mosaico-enlaces">' + enlaces + "</div>");
            }
            caja.innerHTML = partes.length ? '<div class="mosaico-fin">' + partes.join("") + "</div>" : "";
            caja.hidden = partes.length === 0;
            var hechas = estado ? estado.hechas : 0;
            $("mosaico-sub").textContent = estado && estado.nombre && estado.teselas && estado.teselas.length
                ? estado.nombre + " " + estado.filas + "×" + estado.columnas + " · " + _("{n}/{total} teselas", { n: hechas, total: estado.teselas.length }) + (estado.existe ? "" : " · " + _("sin combinar"))
                : "";
        }
        if (estado && estado.teselas && estado.teselas.length && (enCurso || estado.teselas.some(function (t) { return t.estado && t.estado !== "pendiente"; })))
            pintarRejilla(estado.teselas, estado.filas, estado.columnas);
    }

    // ——— Control ———
    Mosaico.iniciar = function () {
        error("");
        var cuerpo = cuerpoFormulario();
        cuerpo.usar_dark = (typeof usarDark !== "undefined") ? !!usarDark : true;
        var n = cuerpo.filas * cuerpo.columnas;
        if (n < 2) { error(_("El mosaico necesita al menos 2 teselas")); return; }
        $("mosaico-btn-empezar").disabled = true;
        post("/mosaico/iniciar", cuerpo).then(function (d) {
            $("mosaico-btn-empezar").disabled = false;
            if (!d.ok) { error(d.error); return; }
            if (d.estado) Mosaico.actualizarEstado(d.estado);
            if (d.centro) { datos = datos || {}; datos.centro = d.centro; pintarCentro(); }
        }).catch(function (e) { $("mosaico-btn-empezar").disabled = false; error(_("Sin conexión: {e}", { e: e })); });
    };
    Mosaico.pausar = function () { return post("/mosaico/pausar").then(function (d) { if (d.ok) Mosaico.actualizarEstado(d.estado); else error(d.error); }); };
    Mosaico.reanudar = function () { return post("/mosaico/reanudar").then(function (d) { if (d.ok) Mosaico.actualizarEstado(d.estado); else error(d.error); }); };
    Mosaico.saltar = function () {
        if (!confirm(_("¿Dar por terminada la tesela actual con las fotos que tenga y pasar a la siguiente?"))) return;
        post("/mosaico/saltar").then(function (d) { if (!d.ok) error(d.error); });
    };
    Mosaico.detener = function (sinConfirmar) {
        if (!sinConfirmar && !confirm(_("¿Detener el mosaico? Las teselas ya apiladas se conservan y se pueden combinar."))) return Promise.resolve(false);
        return post("/mosaico/detener").then(function (d) { if (!d.ok) error(d.error); return d.ok; });
    };
    Mosaico.recombinar = function () {
        error("");
        post("/mosaico/combinar").then(function (d) { if (!d.ok) error(d.error); else $("mosaico-btn-recombinar").disabled = true; });
    };
    Mosaico.guardar = function () {
        var caja = $("mosaico-error");
        caja.textContent = _("⏳ Guardando el mosaico...");
        post("/mosaico/guardar").then(function (d) {
            caja.textContent = d.mensaje || d.error || "";
            if (d.en_curso) {
                var sondeo = setInterval(function () {
                    fetch("/guardar-imagen-estado").then(function (r) { return r.json(); }).then(function (g) {
                        caja.textContent = g.mensaje;
                        if (!g.en_curso) clearInterval(sondeo);
                    }).catch(function () { clearInterval(sondeo); });
                }, 1500);
            }
        });
    };

    Mosaico.cargar();
})();
