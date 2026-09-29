// static/js/plan.js — Plan de la noche (ver plan_sesion.py y el panel "Plan de la noche" de Apilado).
//
// Dos cosas:
//   - anadirAlPlan(objeto, boton): botón "Añadir al plan" del planetario, de Esta noche y del panel.
//   - El objeto global Plan: lista, cronograma, ajustes y control del plan en la pantalla de Apilado.
//     Solo se activa si la página tiene el panel (#panel-plan). apilado.html le pasa el estado del
//     plan en cada sondeo con Plan.actualizarEstado(estado.plan).
//
// Sin sintaxis moderna (?. ?? campos de clase): la tableta de campo lleva un Chrome antiguo.

(function () {
    function post(url, cuerpo) {
        var opciones = { method: "POST", headers: { "Content-Type": "application/json" } };
        if (cuerpo !== undefined) opciones.body = JSON.stringify(cuerpo);
        return fetch(url, opciones).then(function (r) {
            return r.json().then(function (d) { d._status = r.status; return d; });
        });
    }

    // objeto: {nombre, ra (horas), dec (grados), tipo}; ra/dec opcionales (el servidor resuelve el nombre)
    window.anadirAlPlan = function (objeto, boton) {
        var texto = boton ? boton.innerHTML : "";
        if (boton) { boton.disabled = true; }
        var cuerpo = { nombre: objeto.nombre };
        if (objeto.ra !== undefined && objeto.ra !== null && objeto.dec !== undefined && objeto.dec !== null) {
            cuerpo.ra = objeto.ra; cuerpo.dec = objeto.dec;
        }
        if (objeto.tipo) cuerpo.tipo = objeto.tipo;
        return post("/plan/objetos", cuerpo).then(function (d) {
            if (boton) {
                boton.disabled = false;
                if (d.ok) boton.innerHTML = d.duplicado ? "Ya estaba en el plan" : "✅ Añadido al plan (" + d.total + ")";
                else boton.innerHTML = "⚠️ " + (d.error || "No se pudo añadir");
                setTimeout(function () { boton.innerHTML = texto; }, 2500);
            }
            return d;
        }).catch(function (e) {
            if (boton) { boton.disabled = false; boton.innerHTML = "⚠️ Sin conexión"; setTimeout(function () { boton.innerHTML = texto; }, 2500); }
            return { ok: false, error: String(e) };
        });
    };

    if (!document.getElementById("panel-plan")) return;

    var Plan = window.Plan = {};
    var $ = function (id) { return document.getElementById(id); };
    var datos = null;              // respuesta de GET /plan
    var estado = null;             // estado del trabajo (de /estado-apilado o de /plan)
    var versionLista = null;
    var versionEstado = null;
    var modalId = null;            // objeto abierto en el modal
    var timerSugerir = null;
    var timerNoche = null;
    var cargando = false;
    var iconos = { "Galaxia": "🌌", "Nebulosa": "☁️", "Nebulosa planetaria": "💍", "Cúmulo abierto": "✨", "Cúmulo globular": "🔮",
                   "Resto de supernova": "💥", "Planeta": "🪐", "Luna": "🌙", "Nebulosa oscura": "🐴", "Cometa": "☄️", "Asteroide": "🪨" };
    var fases = { esperando: "esperando", goto: "moviendo el telescopio", centrando: "centrando", apilando: "apilando",
                  guardando: "guardando en la galería", pausado: "en pausa", terminado: "terminado" };

    function icono(tipo) {
        if (!tipo) return "🔭";
        for (var k in iconos) if (tipo.indexOf(k) === 0) return iconos[k];
        return "🔭";
    }
    function esc(t) { return String(t === undefined || t === null ? "" : t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
    function error(t) { $("plan-error").textContent = t ? "⚠️ " + t : ""; }
    function porId(id) {
        if (!datos) return null;
        for (var i = 0; i < datos.objetos.length; i++) if (datos.objetos[i].id === id) return datos.objetos[i];
        return null;
    }
    function textoZoom(z) { return z ? (100 - z) + " % del campo" : "campo entero"; }

    // ——— Carga y pintado ———
    Plan.cargar = function () {
        if (cargando) return Promise.resolve();
        cargando = true;
        return fetch("/plan").then(function (r) { return r.json(); }).then(function (d) {
            cargando = false;
            if (d.error) { error("No se pudo calcular el plan: " + d.error); }
            datos = d;
            versionLista = d.version_lista;
            estado = d.estado;
            pintarLista();
            pintarAjustes();
            pintarEstado();
            if ($("modal-noche").classList.contains("abierto")) pintarNoche();
            if (modalId) rellenarModal();
        }).catch(function (e) { cargando = false; console.warn("Plan:", e); });
    };

    // Desde el sondeo de Apilado (cada 2 s): estado ligero; la lista se recarga solo cuando cambia
    Plan.actualizarEstado = function (p) {
        if (!p) return;
        var recargar = (p.version_lista !== versionLista) || (p.version_estado !== versionEstado && p.en_curso === false && estado && estado.en_curso);
        estado = p;
        versionEstado = p.version_estado;
        pintarEstado();
        if (recargar) Plan.cargar();
    };
    Plan.enCurso = function () { return !!(estado && estado.en_curso); };
    Plan.pausado = function () { return !!(estado && estado.pausado); };
    Plan.textoCabecera = function () {
        if (!estado || !estado.en_curso) return "";
        var o = estado.objeto;
        var t = "🗓️ Plan de la noche";
        if (o) t += " · " + o.nombre + (estado.fase === "apilando" ? " " + o.fotos + "/" + o.fotos_objetivo : "");
        t += " · " + (estado.pausado ? "en pausa" : (fases[estado.fase] || estado.fase));
        if (estado.fase === "esperando" && estado.espera_hasta && !estado.pausado) t += " hasta las " + estado.espera_hasta;
        return t;
    };

    function pintarEstado() {
        var enCurso = !!(estado && estado.en_curso);
        var pausado = !!(estado && estado.pausado);
        $("plan-btn-empezar").hidden = enCurso;
        $("plan-btn-pausar").hidden = !enCurso || pausado;
        $("plan-btn-reanudar").hidden = !enCurso || !pausado;
        $("plan-btn-saltar").hidden = !enCurso || !(estado.objeto);
        $("plan-btn-detener").hidden = !enCurso;
        var caja = $("plan-estado");
        var r = datos ? datos.resumen : null;
        var nTramos = datos && datos.programa ? datos.programa.tramos.length : 0;
        if (enCurso) {
            var o = estado.objeto;
            var linea = "<b>" + esc(o ? o.nombre : "") + "</b> · " + esc(pausado ? "en pausa" : (fases[estado.fase] || estado.fase));
            if (estado.fase === "esperando" && estado.espera_hasta) linea = "⏳ " + esc(estado.mensaje || "Esperando") ;
            var html = "<div>" + linea + "</div>";
            if (o && o.fotos_objetivo) {
                var pct = Math.min(100, Math.round(100 * (o.fotos || 0) / o.fotos_objetivo));
                html += '<div class="progress"><div class="progress-bar" style="width:' + pct + '%"></div></div>';
                html += '<div class="texto-2">' + (o.fotos || 0) + " de " + o.fotos_objetivo + " fotos apiladas";
                var obj = porId(o.id);
                if (obj && obj.vis && obj.vis.hasta) html += " · visible hasta las " + esc(obj.vis.hasta);
                html += "</div>";
            }
            var ult = estado.eventos && estado.eventos.length ? estado.eventos[estado.eventos.length - 1] : null;
            if (ult && (!o || ult.texto !== estado.mensaje || estado.fase === "esperando")) html += '<div class="texto-3 plan-evento">' + esc(ult.hora) + " " + esc(ult.texto) + "</div>";
            html += '<div class="texto-3 plan-evento">' + estado.hechos_noche + " hechos esta noche" + (datos && datos.programa && datos.programa.fin_previsto ? " · termina hacia las " + esc(datos.programa.fin_previsto) : "") + "</div>";
            caja.innerHTML = html;
            caja.hidden = false;
            $("plan-sub").textContent = (o ? o.nombre + " · " : "") + (pausado ? "en pausa" : (fases[estado.fase] || "")) + (o && estado.fase === "apilando" ? " " + o.fotos + "/" + o.fotos_objetivo : "");
        } else {
            var ultimo = estado && estado.eventos && estado.eventos.length ? estado.eventos[estado.eventos.length - 1] : null;
            if (ultimo && estado.fase === "terminado") {
                caja.innerHTML = '<div class="texto-2">' + esc(ultimo.hora) + " " + esc(ultimo.texto) + "</div>";
                caja.hidden = false;
            } else caja.hidden = true;
            if (r) {
                var partes = [];
                if (r.hechos) partes.push(r.hechos + " hechos");
                partes.push(r.pendientes + " pendientes");
                if (nTramos) partes.push(nTramos + " esta noche");
                $("plan-sub").textContent = r.total ? partes.join(" · ") : "";
            }
        }
    }

    function filaObjeto(o, contexto) {
        var li = document.createElement("li");
        li.className = "plan-fila estado-" + o.estado;
        li.dataset.id = o.id;
        var chip = "", detalle = [], motivo = "";
        var v = o.vis || {};
        var p = o.previsto;
        if (o.estado === "en_curso") {
            var f = estado && estado.objeto && estado.objeto.id === o.id ? estado.objeto.fotos : 0;
            chip = '<span class="chip chip-warn chip-busy"><span class="dot"></span>en curso ' + f + "/" + o.fotos + "</span>";
        } else if (o.estado === "hecho") {
            chip = '<span class="chip chip-ok">hecho</span>';
            if (o.resultado) detalle.push(esc(o.resultado.fecha || "") + (o.resultado.fotos ? " · " + o.resultado.fotos + " fotos" : ""));
        } else if (contexto === "noche" && p && p.programado) {
            chip = '<span class="chip ' + (o.prioritario ? "chip-warn" : "chip-info") + '">' + (o.prioritario ? "⭐ " : "") + esc(p.inicio) + "–" + esc(p.fin) + "</span>";
        } else if (o.estado === "error") {
            chip = '<span class="chip chip-err">error</span>';
            motivo = o.motivo;
        } else {
            chip = '<span class="chip">otra noche</span>';
            motivo = p ? p.motivo : (o.motivo || "");
        }
        var ajustes = o.fotos + " fotos · " + textoZoom(o.zoom) + (o.exposicion ? " · " + o.exposicion + " s" : "");
        if (o.resultado && o.resultado.parcial && o.estado !== "hecho") motivo = (motivo ? motivo + " · " : "") + "guardado parcial de " + o.resultado.fotos + " fotos";
        if (o.estado !== "hecho" && v.alt_ahora !== undefined && contexto !== "hechos") {
            if (v.visible_ahora) detalle.push("ahora a " + v.alt_ahora + "°" + (v.hasta ? " · hasta las " + esc(v.hasta) : ""));
            else if (v.sube && v.desde) detalle.push((v.desde_el_anochecer ? "desde el anochecer" : "sube a las " + esc(v.desde)) + (v.hasta ? " hasta las " + esc(v.hasta) : ""));
        }
        li.innerHTML = '<span class="plan-ico">' + icono(o.tipo) + '</span>' +
            '<div class="plan-texto"><div class="plan-nombre">' + (o.prioritario && !(p && p.programado && contexto === "noche") ? "⭐ " : "") + esc(o.nombre) + (o.tipo ? ' <span class="texto-3 plan-tipo">' + esc(o.tipo) + "</span>" : "") + "</div>" +
            '<div class="plan-detalle">' + esc(ajustes) + (detalle.length ? " · " + detalle.join(" · ") : "") + "</div>" +
            (motivo ? '<div class="plan-motivo">' + esc(motivo) + "</div>" : "") + "</div>" +
            '<div class="plan-chip">' + chip + "</div>";
        li.onclick = function () { abrirObjeto(o.id); };
        return li;
    }

    function seccion(titulo, extra) {
        var li = document.createElement("li");
        li.className = "plan-seccion";
        li.innerHTML = "<span>" + esc(titulo) + "</span>" + (extra ? "<span>" + esc(extra) + "</span>" : "");
        return li;
    }

    function pintarLista() {
        var ul = $("plan-lista");
        ul.innerHTML = "";
        if (!datos) return;
        var objetos = datos.objetos;
        $("plan-vacio").hidden = objetos.length > 0;
        if (!objetos.length) return;
        var orden = {};
        datos.programa.tramos.forEach(function (t, i) { orden[t.id] = i; });
        var noche = [], otra = [], hechos = [];
        objetos.forEach(function (o) {
            if (o.estado === "hecho") hechos.push(o);
            else if (o.estado === "en_curso" || (o.previsto && o.previsto.programado)) noche.push(o);
            else otra.push(o);
        });
        noche.sort(function (a, b) {
            if (a.estado === "en_curso") return -1;
            if (b.estado === "en_curso") return 1;
            return (orden[a.id] || 0) - (orden[b.id] || 0);
        });
        var ahora = datos.de_noche ? (datos.ajustes.probar_de_dia ? "modo prueba" : "") : (datos.noche_desde ? "anochece a las " + datos.noche_desde : "");
        if (noche.length) {
            ul.appendChild(seccion("Esta noche", ahora || (datos.programa.fin_previsto ? "hasta las " + datos.programa.fin_previsto : "")));
            noche.forEach(function (o) { ul.appendChild(filaObjeto(o, "noche")); });
        }
        if (otra.length) {
            ul.appendChild(seccion("Otra noche", otra.length + ""));
            otra.forEach(function (o) { ul.appendChild(filaObjeto(o, "otra")); });
        }
        if (hechos.length) {
            ul.appendChild(seccion("Hechos", hechos.length + " de " + objetos.length));
            hechos.forEach(function (o) { ul.appendChild(filaObjeto(o, "hechos")); });
        }
    }

    function pintarAjustes() {
        if (!datos) return;
        var a = datos.ajustes;
        $("plan-aj-altura").value = a.altura_minima;
        $("plan-aj-fotos").value = a.fotos_por_defecto;
        $("plan-aj-zoom").value = a.zoom_por_defecto;
        $("plan-aj-minutos").value = a.minutos_sin_fotos;
        $("plan-aj-dark").checked = a.usar_dark;
        $("plan-aj-centrar").checked = a.centrar;
        $("plan-aj-crepusculo").value = a.crepusculo;
        $("plan-aj-probar").checked = !!a.probar_de_dia;
        var nota = "Altura mínima efectiva: " + datos.altura_efectiva + "°";
        if (datos.altura_general > a.altura_minima) nota += " (la general de config.ini es " + datos.altura_general + "°)";
        nota += " · noche de " + datos.noche_desde + " a " + datos.noche_hasta + " · " + datos.tiempo_por_foto_s + " s por foto apilada con " + datos.exposicion_config + " s de exposición";
        if (a.probar_de_dia) nota = "⚠️ MODO PRUEBA: la noche empieza ahora y dura 6 h. Desactívalo antes de una sesión real. " + nota;
        $("plan-aj-nota").textContent = nota;
    }

    Plan.guardarAjustes = function () {
        var cuerpo = {
            altura_minima: parseFloat($("plan-aj-altura").value), fotos_por_defecto: parseInt($("plan-aj-fotos").value),
            zoom_por_defecto: parseInt($("plan-aj-zoom").value), minutos_sin_fotos: parseInt($("plan-aj-minutos").value),
            usar_dark: $("plan-aj-dark").checked, centrar: $("plan-aj-centrar").checked, crepusculo: $("plan-aj-crepusculo").value,
            probar_de_dia: $("plan-aj-probar").checked
        };
        post("/plan/ajustes", cuerpo).then(function (d) { if (!d.ok) error(d.error); else { error(""); Plan.cargar(); } });
    };

    // ——— Control ———
    Plan.iniciar = function () {
        error("");
        post("/plan/iniciar").then(function (d) { if (!d.ok) error(d.error); else { estado = d.estado; pintarEstado(); Plan.cargar(); } });
    };
    Plan.pausar = function () { return post("/plan/pausar").then(function (d) { if (d.ok) { estado = d.estado; pintarEstado(); } else error(d.error); }); };
    Plan.reanudar = function () { return post("/plan/reanudar").then(function (d) { if (d.ok) { estado = d.estado; pintarEstado(); } else error(d.error); }); };
    Plan.saltar = function () {
        if (!confirm("¿Saltar el objeto actual? Si ya tiene fotos apiladas se guardan y el objeto queda pendiente para otra noche.")) return;
        post("/plan/saltar").then(function (d) { if (!d.ok) error(d.error); });
    };
    Plan.detener = function (sinConfirmar) {
        if (!sinConfirmar && !confirm("¿Detener el plan? Lo apilado del objeto actual se guarda en la galería y el objeto queda pendiente.")) return Promise.resolve(false);
        return post("/plan/detener").then(function (d) { if (!d.ok) error(d.error); else { estado = d.estado; pintarEstado(); } return d.ok; });
    };

    // ——— Añadir, buscar, importar ———
    Plan.anadirNombre = function (nombre, extra) {
        error("");
        var cuerpo = extra || {};
        cuerpo.nombre = nombre;
        $("plan-buscar").disabled = true;
        return post("/plan/objetos", cuerpo).then(function (d) {
            $("plan-buscar").disabled = false;
            if (!d.ok) { error(d.error || "No se pudo añadir"); return; }
            $("plan-buscar").value = "";
            $("plan-sugerencias").hidden = true;
            if (d.duplicado) error(d.objeto.nombre + " ya estaba en el plan");
            Plan.cargar();
        }).catch(function () { $("plan-buscar").disabled = false; error("Sin conexión"); });
    };
    function sugerir() {
        var q = $("plan-buscar").value.trim();
        var caja = $("plan-sugerencias");
        clearTimeout(timerSugerir);
        if (q.replace(/[*?]/g, "").length < 2) { caja.hidden = true; return; }
        timerSugerir = setTimeout(function () {
            fetch("/buscar-objetos?q=" + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (d) {
                caja.innerHTML = "";
                if (!d.resultados.length) {
                    caja.innerHTML = '<div class="nada">Sin coincidencias en los catálogos locales. Pulsa Intro para intentarlo por nombre.</div>';
                } else {
                    d.resultados.forEach(function (r) {
                        var div = document.createElement("div");
                        div.innerHTML = esc(r.nombre) + "<small>" + esc(r.detalle) + "</small>";
                        div.onclick = function () {
                            var extra = { tipo: r.tipo };
                            if (r.ra !== undefined && r.ra !== null) { extra.ra = r.ra; extra.dec = r.dec; }
                            Plan.anadirNombre(r.nombre, extra);
                        };
                        caja.appendChild(div);
                    });
                }
                caja.hidden = false;
            }).catch(function () { caja.hidden = true; });
        }, 250);
    }
    $("plan-buscar").addEventListener("input", sugerir);
    $("plan-buscar").addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            var q = $("plan-buscar").value.trim();
            if (q) Plan.anadirNombre(q);
        } else if (ev.key === "Escape") $("plan-sugerencias").hidden = true;
    });
    document.addEventListener("click", function (ev) {
        if (!ev.target.closest("#plan-sugerencias") && ev.target.id !== "plan-buscar") $("plan-sugerencias").hidden = true;
    });

    Plan.messier = function () {
        error("");
        post("/plan/messier").then(function (d) {
            if (!d.ok) { error(d.error); return; }
            error("");
            $("plan-error").textContent = "✅ Messier: " + d.anadidos + " añadidos" + (d.duplicados ? ", " + d.duplicados + " ya estaban" : "");
            Plan.cargar();
        });
    };
    Plan.importar = function (input) {
        var f = input.files && input.files[0];
        if (!f) return;
        var lector = new FileReader();
        lector.onload = function () {
            var texto = String(lector.result || "");
            var cuerpo;
            try { var j = JSON.parse(texto); cuerpo = (j && typeof j === "object" && j.objetos) ? { plan: j } : { nombres: texto }; }
            catch (e) { cuerpo = { nombres: texto }; }
            post("/plan/importar", cuerpo).then(function (d) {
                if (!d.ok) { error(d.error); return; }
                var t = "✅ Importados " + d.anadidos + (d.duplicados ? " · " + d.duplicados + " ya estaban" : "");
                if (d.no_encontrados && d.no_encontrados.length) t += " · no encontrados: " + d.no_encontrados.join(", ");
                $("plan-error").textContent = t;
                Plan.cargar();
            });
            input.value = "";
        };
        lector.readAsText(f);
    };
    Plan.vaciar = function () {
        if (!datos || !datos.objetos.length) return;
        if (!confirm("¿Vaciar el plan? Se borran los " + datos.objetos.length + " objetos y sus resultados. Exporta antes si quieres conservarlo.")) return;
        post("/plan/vaciar").then(function (d) { if (!d.ok) error(d.error); else Plan.cargar(); });
    };
    Plan.reiniciarResultados = function () {
        if (!confirm("¿Poner todos los objetos como pendientes? Los hechos volverán a fotografiarse.")) return;
        post("/plan/reiniciar-resultados").then(function (d) { if (!d.ok) error(d.error); else Plan.cargar(); });
    };

    // ——— Modal de un objeto ———
    function abrirObjeto(id) {
        modalId = id;
        rellenarModal();
        $("modal-plan-objeto").classList.add("abierto");
    }
    function rellenarModal() {
        var o = porId(modalId);
        if (!o) { cerrarObjeto(); return; }
        $("po-nombre").textContent = o.nombre;
        $("po-icono").textContent = icono(o.tipo);
        var v = o.vis || {};
        var datosTxt = [o.tipo, "RA " + o.ra_h.toFixed(3) + " h · Dec " + o.dec_deg.toFixed(2) + "°"];
        if (v.alt_ahora !== undefined) datosTxt.push("ahora a " + v.alt_ahora + "° · máximo " + v.alt_max + "° a las " + v.hora_max);
        if (o.prioritario) datosTxt.push("⭐ prioritario");
        $("po-datos").textContent = datosTxt.filter(Boolean).join(" · ");
        var prev = "";
        if (o.estado === "hecho") prev = "✅ Hecho" + (o.resultado ? " el " + o.resultado.fecha + (o.resultado.archivo ? " · " + o.resultado.archivo : "") : "");
        else if (o.estado === "en_curso") prev = "▶ En curso";
        else if (o.previsto && o.previsto.programado) prev = "🗓️ Esta noche de " + o.previsto.inicio + " a " + o.previsto.fin + " · " + o.previsto.fotos + " fotos";
        else if (o.previsto) prev = "⏳ Otra noche: " + o.previsto.motivo;
        else if (o.motivo) prev = "⏳ " + o.motivo;
        if (o.resultado && o.resultado.parcial && o.estado !== "hecho") prev += " · hay un guardado parcial de " + o.resultado.fotos + " fotos";
        $("po-previsto").textContent = prev;
        $("po-fotos").value = o.fotos;
        var botones = $("po-zoom").querySelectorAll("button");
        for (var i = 0; i < botones.length; i++) botones[i].classList.toggle("activo", parseInt(botones[i].dataset.zoom) === o.zoom);
        $("po-exposicion").value = o.exposicion || "";
        $("po-prioritario").checked = !!o.prioritario;
        $("po-exposicion").placeholder = datos ? "la de config (" + datos.exposicion_config + " s)" : "la de config";
        $("po-btn-pendiente").hidden = !(o.estado === "hecho" || (o.resultado && o.resultado.parcial));
        $("po-btn-hecho").hidden = o.estado === "hecho" || o.estado === "en_curso";
        $("po-nota").textContent = o.estado === "en_curso" ? "El número de fotos y el zoom se aplican al momento; la exposición, al siguiente objeto." : "";
    }
    function cerrarObjeto() { $("modal-plan-objeto").classList.remove("abierto"); modalId = null; }
    Plan.cerrarObjeto = cerrarObjeto;
    Plan.zoomObjeto = function (btn) {
        var botones = $("po-zoom").querySelectorAll("button");
        for (var i = 0; i < botones.length; i++) botones[i].classList.toggle("activo", botones[i] === btn);
    };
    Plan.fotosObjeto = function (delta) {
        var c = $("po-fotos");
        c.value = Math.min(999, Math.max(1, (parseInt(c.value) || 20) + delta));
    };
    Plan.guardarObjeto = function () {
        if (!modalId) return;
        var activo = $("po-zoom").querySelector("button.activo");
        var exp = parseFloat($("po-exposicion").value);
        var cuerpo = { fotos: parseInt($("po-fotos").value) || 20, zoom: activo ? parseInt(activo.dataset.zoom) : 0, exposicion: exp > 0 ? exp : null,
                       prioritario: $("po-prioritario").checked };
        post("/plan/objetos/" + modalId, cuerpo).then(function (d) { if (!d.ok) error(d.error); else { cerrarObjeto(); Plan.cargar(); } });
    };
    Plan.estadoObjeto = function (estadoNuevo) {
        if (!modalId) return;
        post("/plan/objetos/" + modalId + "/estado", { estado: estadoNuevo }).then(function (d) { if (!d.ok) error(d.error); else { cerrarObjeto(); Plan.cargar(); } });
    };
    Plan.borrarObjeto = function () {
        var o = porId(modalId);
        if (!o || !confirm("¿Quitar " + o.nombre + " del plan?")) return;
        post("/plan/objetos/" + modalId + "/borrar").then(function (d) { if (!d.ok) error(d.error); else { cerrarObjeto(); Plan.cargar(); } });
    };

    // ——— Cronograma de la noche ———
    Plan.verNoche = function () {
        $("modal-noche").classList.add("abierto");
        pintarNoche();
        clearInterval(timerNoche);
        timerNoche = setInterval(function () { Plan.cargar(); }, 60000);
        Plan.cargar();
    };
    Plan.cerrarNoche = function () { $("modal-noche").classList.remove("abierto"); clearInterval(timerNoche); timerNoche = null; };
    function pintarNoche() {
        var cont = $("crono");
        if (!datos) { cont.innerHTML = '<div class="texto-3">Cargando...</div>'; return; }
        var d0 = datos.noche_desde_ms, d1 = datos.noche_hasta_ms, ahora = datos.ahora_ms;
        if (!d0 || !d1 || d1 <= d0) { cont.innerHTML = '<div class="texto-3">Sin ventana nocturna.</div>'; return; }
        var pct = function (ms) { return Math.max(0, Math.min(100, 100 * (ms - d0) / (d1 - d0))); };
        var html = "";
        // Eje de horas
        html += '<div class="crono-eje">';
        var h = new Date(d0); h.setMinutes(0, 0, 0); h = h.getTime() + 3600000;
        for (; h < d1; h += 3600000) html += '<span style="left:' + pct(h).toFixed(2) + '%">' + new Date(h).getHours() + "h</span>";
        html += "</div>";
        // Carril del plan: tramos y esperas
        var prog = datos.programa;
        html += '<div class="crono-fila crono-plan"><div class="crono-etq">Plan</div><div class="crono-pista">';
        prog.esperas.forEach(function (e) {
            html += '<div class="crono-espera" style="left:' + pct(e.inicio_ms).toFixed(2) + '%;width:' + (pct(e.fin_ms) - pct(e.inicio_ms)).toFixed(2) + '%" title="espera ' + esc(e.inicio) + '–' + esc(e.fin) + '"></div>';
        });
        prog.tramos.forEach(function (t) {
            var o = porId(t.id);
            html += '<div class="crono-tramo" style="left:' + pct(t.inicio_ms).toFixed(2) + '%;width:' + Math.max(0.4, pct(t.fin_ms) - pct(t.inicio_ms)).toFixed(2) + '%" title="' + esc(o ? o.nombre : "") + " " + esc(t.inicio) + "–" + esc(t.fin) + '"></div>';
        });
        if (ahora >= d0 && ahora <= d1) html += '<div class="crono-ahora" style="left:' + pct(ahora).toFixed(2) + '%"></div>';
        html += "</div></div>";
        // Una fila por objeto: programados en su orden, luego el resto que sube esta noche, luego hechos
        var orden = {};
        prog.tramos.forEach(function (t, i) { orden[t.id] = i; });
        var filas = datos.objetos.slice().sort(function (a, b) {
            var ka = a.estado === "en_curso" ? -1 : (orden[a.id] !== undefined ? orden[a.id] : (a.estado === "hecho" ? 2000 : 1000));
            var kb = b.estado === "en_curso" ? -1 : (orden[b.id] !== undefined ? orden[b.id] : (b.estado === "hecho" ? 2000 : 1000));
            return ka - kb;
        });
        var tramoDe = {};
        prog.tramos.forEach(function (t) { tramoDe[t.id] = t; });
        filas.forEach(function (o) {
            var v = o.vis || {};
            if (!v.sube && o.estado !== "hecho" && o.estado !== "en_curso") return;   // no sube esta noche: no ocupa fila
            var t = tramoDe[o.id];
            html += '<div class="crono-fila"><div class="crono-etq" title="' + esc(o.nombre) + (o.prioritario ? " (prioritario)" : "") + '">' + (o.prioritario ? "⭐" : icono(o.tipo)) + " " + esc(o.nombre) + '</div><div class="crono-pista">';
            if (v.sube && v.desde_ms && v.hasta_ms) html += '<div class="crono-vis" style="left:' + pct(v.desde_ms).toFixed(2) + '%;width:' + (pct(v.hasta_ms) - pct(v.desde_ms)).toFixed(2) + '%" title="visible ' + esc(v.desde) + '–' + esc(v.hasta) + '"></div>';
            if (t) html += '<div class="crono-tramo' + (o.estado === "hecho" ? " hecho" : "") + '" style="left:' + pct(t.inicio_ms).toFixed(2) + '%;width:' + Math.max(0.4, pct(t.fin_ms) - pct(t.inicio_ms)).toFixed(2) + '%" title="' + esc(t.inicio) + "–" + esc(t.fin) + " · " + t.fotos + ' fotos"></div>';
            if (o.estado === "en_curso" && ahora >= d0) html += '<div class="crono-tramo en-curso" style="left:' + pct(ahora).toFixed(2) + '%;width:1.5%"></div>';
            if (ahora >= d0 && ahora <= d1) html += '<div class="crono-ahora" style="left:' + pct(ahora).toFixed(2) + '%"></div>';
            html += "</div></div>";
        });
        var pie = prog.tramos.length + " objetos programados";
        if (prog.fin_previsto) pie += " · termina hacia las " + prog.fin_previsto;
        var otra = datos.objetos.filter(function (o) { return o.estado !== "hecho" && o.estado !== "en_curso" && !(o.previsto && o.previsto.programado); }).length;
        if (otra) pie += " · " + otra + " quedan para otra noche";
        pie += " · noche de " + datos.noche_desde + " a " + datos.noche_hasta;
        html += '<div class="crono-pie">' + esc(pie) + "</div>";
        cont.innerHTML = html;
    }

    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { cerrarObjeto(); Plan.cerrarNoche(); } });
    Plan.cargar();
})();
