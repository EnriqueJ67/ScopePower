// static/js/guia.js — guía «¿Qué quieres hacer hoy?» (templates/_guia.html, guia.py).
//
//   Guia.abrir()            abre la ventana: el menú de actividades o, si hay una guía a medias, su paso
//   Guia.cerrar()           la cierra; la guía sigue en la banda de arriba
//   Guia.hechoSiguiente()   botón de la banda: da por hecho el paso que se hace en esta pantalla
//
// Lo que está bien y lo que falta lo decide el PC (GET /guia/estado): esta página solo lo pinta y
// llama a las rutas de siempre (/goto, /darks/generar, /conectar-equipo...). Al cargar cualquier
// pantalla pregunta una vez; solo sondea cada pocos segundos mientras hay algo en marcha.
// Las pantallas que preparan algo al llegar desde la guía definen window.guiaAlLlegar(paso)
// (Cámara: modo enfoque; Apilado: panel de inicio y botón Empezar resaltado).
// El evento "guia-estado" avisa del estado a quien quiera pintarlo (la franja del inicio).
(function () {
    "use strict";
    const $ = id => document.getElementById(id);
    const CLAVE_LLEGAR = "guia_al_llegar";
    const PANTALLAS_OBJETO = ["/planetario", "/goto", "/esta-noche"];
    const ICONO = { ok: "i-ok", warn: "i-info", err: "i-cerrar", falta: "i-flecha" };
    const FALTA = {
        equipo: N_("Conecta la montura y la cámara para seguir."),
        alineacion: N_("Alinea la montura, di que ya está alineada o salta este paso."),
        objeto: N_("Elige un objeto para seguir."),
        calibracion: N_("Espera a que terminen los darks."),
        enfoque: N_("Enfoca, di que ya está enfocado o salta este paso."),
        apuntar: N_("Pulsa el botón para ir al objeto y espera a que termine el centrado."),
        filtro: N_("Confirma que el filtro solar está puesto para seguir."),
        apuntar_disco: N_("Centra el disco, o di que ya está centrado, para seguir."),
        encuadre: N_("Encuadra, di que ya está encuadrado o salta este paso."),
        apuntar_enfoque: N_("Apunta a algo con que enfocar, o salta este paso."),
        flats: N_("Haz los flats, o salta este paso."),
    };

    let E = null;               // última respuesta de /guia/estado
    let vista = "menu";         // qué enseña la ventana: menu | ruta | galeria
    let pasoPintado = "";       // para rellenar los campos de un paso solo al entrar en él
    let sondeo = null;
    let recomendados = null;    // respuesta de /esta-noche/objetos
    let resultados = [];        // última búsqueda
    let darksLanzados = false;
    let gotoLanzado = 0;        // cuándo se pidió el GoTo: unos segundos de gracia hasta que la montura arranca
    const espera = ms => new Promise(r => setTimeout(r, ms));

    const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const abierta = () => $("guia").classList.contains("abierto");
    const sesion = () => (E && E.sesion) || null;
    const semaforo = clave => ((sesion() || {}).comprobaciones || []).find(c => c.clave === clave) || null;

    function mostrarError(texto) { const e = $("guia-error"); e.textContent = texto; e.hidden = !texto; }

    async function pedir(ruta, cuerpo) {
        const opciones = cuerpo === undefined ? { cache: "no-store" }
            : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) };
        try {
            return await (await fetch(ruta, opciones)).json();
        } catch (e) {
            return { ok: false, error: _("No se pudo hablar con el PC: {e}", { e: e }) };
        }
    }

    // Respuesta de una ruta /guia/*: el estado nuevo, o {ok: false, error}
    function aplicar(d) {
        if (!d || d.ok === false || !d.menu) {
            if (d && d.error) mostrarError(d.error);
            return false;
        }
        E = d;
        pintar();
        return true;
    }

    async function refrescar() { return aplicar(await pedir("/guia/estado")); }

    // ——— Pintar ———
    function pintar() {
        if (!E) return;
        if (!sesion() && vista === "ruta") vista = "menu";
        document.dispatchEvent(new CustomEvent("guia-estado", { detail: E }));
        pintarBanda();
        if (abierta()) {
            $("guia-menu").hidden = vista !== "menu";
            $("guia-ruta").hidden = vista !== "ruta";
            $("guia-galeria").hidden = vista !== "galeria";
            if (vista === "ruta") pintarRuta(); else if (vista === "menu") pintarMenu();
        }
        programar();
    }

    function pintarBanda() {
        const banda = $("guia-banda"), s = sesion();
        const visible = !!s && !abierta() && location.pathname !== "/";
        banda.hidden = !visible;
        document.body.classList.toggle("con-guia", visible);
        if (!visible) return;
        const pantallas = s.paso === "objeto" ? PANTALLAS_OBJETO : (s.pantalla ? [s.pantalla] : []);
        const aqui = pantallas.some(p => location.pathname.startsWith(p));
        $("guia-banda-paso").textContent = _("{actividad} · paso {n} de {total}", { actividad: s.titulo, n: s.indice + 1, total: s.total });
        $("guia-banda-que").textContent = aqui && s.banda ? s.banda : s.pasos[s.indice].titulo;
        $("guia-banda-hecho").hidden = !aqui || s.paso === "listo";
        $("guia-banda-seguir").hidden = aqui;
    }

    function pintarMenu() {
        const s = sesion();
        $("guia-seguir").hidden = !s;
        if (s) $("guia-seguir-texto").textContent = _("Tienes a medias: {actividad}, paso {n} de {total}.", { actividad: s.titulo, n: s.indice + 1, total: s.total });
        $("guia-tarjetas").innerHTML = E.menu.map((a, i) =>
            `<button type="button" class="guia-tarjeta${i === 0 ? " principal" : ""}${a.apagada ? " apagada" : ""}" data-actividad="${esc(a.clave)}"` +
            ` style="background-image: url('/static/guia/${esc(i === 0 ? a.imagen_ancha : a.imagen)}?v=2')">` +
            (a.chip ? `<span class="guia-tarjeta-chip ${esc(a.chip.nivel)}">${esc(a.chip.texto)}</span>` : "") +
            `<span class="guia-tarjeta-titulo">${esc(a.titulo)}</span><span class="guia-tarjeta-desc">${esc(a.desc)}</span></button>`).join("");
    }

    function pintarRuta() {
        const s = sesion();
        const entrando = pasoPintado !== s.paso;
        if (entrando) mostrarError("");
        $("guia-foto").style.backgroundImage = `url('/static/guia/${s.imagen}?v=2')`;
        $("guia-actividad").textContent = s.titulo;
        $("guia-contador").textContent = _("Paso {n} de {total}", { n: s.indice + 1, total: s.total });
        $("guia-pasos").innerHTML = s.pasos.map((p, i) =>
            `<button type="button" class="guia-paso ${esc(p.estado)}" data-paso="${esc(p.clave)}"><b>${i + 1}</b><span>${esc(p.titulo)}</span></button>`).join("");
        $("guia-paso-titulo").textContent = s.pasos[s.indice].titulo;
        $("guia-semaforos").innerHTML = s.comprobaciones.map(c =>
            `<li class="${esc(c.nivel)}">` + (c.nivel === "pendiente" ? '<span class="spinner"></span>' : `<svg class="ico"><use href="#${ICONO[c.nivel] || "i-info"}"/></svg>`) +
            `<span>${esc(c.texto)}</span></li>`).join("");
        document.querySelectorAll("#guia .guia-cuerpo").forEach(c => c.hidden = !c.dataset.paso.split(" ").includes(s.paso));
        (PASOS[s.paso] || (() => {}))(s, entrando);
        pasoPintado = s.paso;
        $("guia-atras").hidden = s.indice === 0;
        $("guia-otra").hidden = s.indice !== 0;
        $("guia-saltar").hidden = !s.saltable || s.puede_seguir;
        $("guia-siguiente").hidden = s.paso.startsWith("listo");
        if (entrando) { const caja = document.querySelector("#guia .modal-caja"); if (caja) caja.scrollTop = 0; }
    }

    // Lo propio de cada paso; `entrando` es true la primera vez que se pinta
    const PASOS = {
        equipo(s) {
            $("guia-conectar").hidden = !s.comprobaciones.some(c => c.accion === "conectar");
        },
        alineacion(s) {
            $("guia-alineada").hidden = (semaforo("alineacion") || {}).nivel === "ok";
        },
        objeto(s, entrando) {
            if (entrando) {
                resultados = [];
                $("guia-resultados").innerHTML = "";
                if (!recomendados) cargarRecomendados(); else pintarRecomendados();
            } else {
                pintarRecomendados();
                pintarResultados();
            }
            const mosaico = semaforo("mosaico");
            $("guia-mosaico").hidden = !(mosaico && mosaico.accion === "mosaico");
            $("guia-sin-mosaico").hidden = !s.mosaico;
            const ug = s.ultimo_goto, boton = $("guia-ultimo-goto");
            boton.hidden = !(ug && ug.objeto) || (s.objeto && s.objeto.nombre === ug.objeto);
            if (!boton.hidden) boton.textContent = _("Usar {objeto}, donde apunta la montura", { objeto: ug.objeto });
        },
        captura(s, entrando) {
            if (entrando) {
                $("guia-exposicion").value = s.captura.exposicion;
                $("guia-ganancia").value = s.captura.ganancia == null ? "" : s.captura.ganancia;
                $("guia-fotos").value = s.captura.fotos;
            }
            const calibracion = s.actividad === "calibracion";
            $("guia-fotos").closest(".form-group").hidden = calibracion;
            $("guia-captura-calibracion").hidden = !calibracion;
            $("guia-recomendacion").textContent = (s.recomendacion || {}).texto || "";
            pintarDuracion();
        },
        calibracion(s, entrando) {
            const trabajo = s.trabajo_darks || {};
            if (entrando) {
                darksLanzados = false;
                $("guia-darks-n").value = s.captura.numero_darks || 10;
                // En la ruta de calibración se viene a hacer darks: «nuevos» salvo que ya haya uno bueno para estos valores
                const opcion = s.actividad === "calibracion" ? ((semaforo("dark") || {}).nivel === "ok" ? "ultimo" : "nuevo")
                    : (!s.hay_dark && s.opcion_dark === "ultimo" ? "nuevo" : (s.opcion_dark || "ultimo"));
                (document.querySelector(`input[name="guia-dark"][value="${opcion}"]`) || $("guia-dark-ultimo")).checked = true;
                $("guia-darks-tapar").hidden = true;
            }
            $("guia-dark-ultimo").disabled = !s.hay_dark;
            $("guia-darks-opciones").hidden = !!trabajo.en_curso || !$("guia-darks-tapar").hidden;
            $("guia-darks-progreso").hidden = !trabajo.en_curso;
            if (trabajo.en_curso) {
                $("guia-darks-barra").style.width = (trabajo.total ? 100 * trabajo.completadas / trabajo.total : 0) + "%";
                $("guia-darks-texto").textContent = trabajo.mensaje || "";
            } else if (darksLanzados) {
                if (trabajo.error) {
                    mostrarError(trabajo.error + " " + _("Quita la tapa del telescopio antes de hacer fotos."));
                } else {
                    $("guia-dark-ultimo").disabled = false;
                    $("guia-dark-ultimo").checked = true;
                }
            }
            $("guia-darks-hechos").hidden = !(darksLanzados && !trabajo.en_curso && !trabajo.error);
            $("guia-flats").hidden = !!s.hay_flat || s.actividad === "calibracion";      // allí los flats tienen su paso
            $("guia-darks-biblioteca").textContent = s.biblioteca || "";
        },
        enfoque(s) {
            const deDia = s.actividad === "enfocar" ? !!E.de_dia : s.actividad !== "cielo_profundo";
            $("guia-enfoque-noche").hidden = deDia;
            $("guia-enfoque-dia").hidden = !deDia;
            const boton = $("guia-ir-estrella");
            boton.hidden = !s.estrella;
            if (s.estrella) {
                boton.innerHTML = '<svg><use href="#i-goto"/></svg>' +
                    esc(_("Ir a {estrella} ({alt}° de altura) y enfocar", { estrella: s.estrella.nombre, alt: fmtNum(s.estrella.altura, 0) }));
            }
            $("guia-enfocado").hidden = (semaforo("enfoque") || {}).nivel === "ok";
        },
        apuntar(s) {
            const c = semaforo("apuntar") || {}, boton = $("guia-goto");
            const esperando = Date.now() - gotoLanzado < 8000;
            boton.hidden = c.accion !== "goto" || esperando || !s.objeto;
            if (!boton.hidden) {
                boton.innerHTML = '<svg><use href="#i-goto"/></svg>' + esc(c.nivel === "falta"
                    ? _("Ir a {objeto} y centrar", { objeto: s.objeto.nombre })
                    : _("Repetir: ir a {objeto} y centrar", { objeto: s.objeto.nombre }));
            }
            if (esperando && c.nivel !== "ok") {
                $("guia-semaforos").innerHTML = '<li class="pendiente"><span class="spinner"></span><span>' +
                    esc(_("La montura va hacia {objeto}...", { objeto: s.objeto.nombre })) + "</span></li>";
            }
        },
        apuntar_enfoque(s) {
            const hecho = (semaforo("apuntar_enfoque") || {}).nivel === "ok", estrella = $("guia-enf-estrella");
            estrella.hidden = hecho || !s.estrella;
            if (s.estrella) {
                estrella.innerHTML = '<svg><use href="#i-goto"/></svg>' +
                    esc(_("Ir a {estrella} ({alt}° de altura)", { estrella: s.estrella.nombre, alt: fmtNum(s.estrella.altura, 0) }));
            }
            $("guia-enf-luna").hidden = hecho || !s.luna_visible;
            $("guia-enf-sol").hidden = hecho || !s.sol_visible;
            $("guia-enf-paisaje").hidden = hecho || !E.de_dia;
            $("guia-enf-aqui").hidden = hecho;
        },
        listo_enfoque() {
            $("guia-fin-texto").textContent = _("El enfoque vale mientras no toques el enfocador. Si la temperatura baja mucho a lo largo de la noche, conviene repasarlo.");
        },
        listo_calibracion() {
            $("guia-fin-texto").textContent = _("Lo que has hecho queda guardado: el apilado y la cámara usan solos los darks y el flat mientras no cambies la exposición, la ganancia ni nada del tren óptico.");
        },
        filtro(s) {
            $("guia-filtro").hidden = (semaforo("filtro") || {}).nivel === "ok";
        },
        apuntar_disco(s) {
            const c = semaforo("apuntar_disco") || {}, boton = $("guia-ir-disco"), esSol = s.astro === "sol";
            const visible = c.nivel !== "ok" && c.nivel !== "err";
            boton.hidden = !visible;
            $("guia-disco-mano").hidden = !visible;
            $("guia-disco-hecho").hidden = !visible;
            const texto = s.orientacion_camara
                ? (esSol ? _("Ir al Sol y centrarlo") : _("Ir a la Luna y centrarla"))
                : (esSol ? _("Ir al Sol") : _("Ir a la Luna"));
            boton.innerHTML = '<svg><use href="#i-goto"/></svg>' + esc(texto);
        },
        encuadre(s) {
            $("guia-encuadrado").hidden = (semaforo("encuadre") || {}).nivel === "ok";
        },
        modo(s) {
            document.querySelectorAll("#guia-modos .guia-modo").forEach(b => b.classList.toggle("elegido", b.dataset.modo === s.modo));
        },
        listo_camara(s) {
            const textos = {
                foto: _("Abre Cámara y pulsa «Hacer foto»: la exposición la busca sola."),
                video: _("Abre Cámara en la pestaña del vídeo: pon el modo en vivo y pulsa grabar."),
                timelapse: _("Abre Cámara en la pestaña del timelapse: elige cuántas fotos y cada cuánto, y empieza."),
            };
            $("guia-listo-camara-texto").textContent = textos[s.modo] || textos.foto;
        },
        listo(s) {
            $("guia-listo-texto").textContent = s.mosaico
                ? _("Abre Apilado en la pestaña Mosaico: el centro ya es tu objeto. Elige las teselas y pulsa «Empezar mosaico».")
                : _("Abre Apilado y pulsa «Empezar»: el objeto está en su sitio y todo lo demás, preparado.");
            $("guia-abrir-apilado").textContent = s.mosaico ? _("Abrir el mosaico") : _("Abrir Apilado");
        },
    };

    // ——— Sondeo: solo mientras hay algo en marcha ———
    function programar() {
        clearTimeout(sondeo);
        sondeo = null;
        const s = sesion();
        if (!s) return;
        let cada = 0;
        if (abierta() && vista === "ruta") {
            if (s.paso === "apuntar") cada = 2000;
            else if (s.paso === "calibracion" && (s.trabajo_darks || {}).en_curso) cada = 2000;
            else if (s.paso === "equipo" && !s.puede_seguir) cada = 3000;
            else if (s.paso === "alineacion" && !s.puede_seguir) cada = 6000;
            else if (s.paso === "apuntar_disco" && !s.puede_seguir) cada = 3000;
            else if (s.paso === "apuntar_enfoque" && !s.puede_seguir) cada = 3000;
        } else if (!abierta() && s.paso === "listo" && location.pathname.startsWith("/apilado")) {
            cada = 4000;      // al arrancar el apilado la guía se cierra sola y la banda desaparece
        }
        if (cada) sondeo = setTimeout(refrescar, cada);
    }

    // ——— Ventana ———
    async function abrir() {
        if (!E && !(await refrescar())) return;
        vista = sesion() ? "ruta" : "menu";
        pasoPintado = "";
        mostrarError("");
        $("guia").classList.add("abierto");
        pintar();
        refrescar();
    }

    function cerrar() {
        $("guia").classList.remove("abierto");
        pintar();
    }

    function verMenu() { vista = "menu"; pintar(); }

    // Galería: las últimas fotos en pequeño; cada una lleva a su editor si se puede volver a revelar, o a la galería
    async function verGaleria() {
        vista = "galeria";
        pintar();
        const d = await pedir("/guia/fotos");
        const fotos = d.fotos || [];
        const editar = _("Editar"), ver = _("Ver"), vacio = _("Todavía no hay fotos en la galería.");
        $("guia-ultimas").innerHTML = fotos.length ? fotos.map(f =>
            `<a class="guia-tarjeta" href="${esc(f.editar || "/galeria")}" style="background-image: url('${esc(f.miniatura)}')">` +
            `<span class="guia-tarjeta-chip ${f.editar ? "ok" : ""}">${esc(f.editar ? editar : ver)}</span>` +
            `<span class="guia-tarjeta-titulo">${esc(f.objeto)}</span><span class="guia-tarjeta-desc">${esc(f.fecha)}</span></a>`).join("")
            : `<div class="estado">${esc(vacio)}</div>`;
    }

    // Fin de una ruta que no acaba en otra pantalla (enfocar, calibración): se cierra y vuelve el menú
    async function terminarAlMenu() {
        vista = "menu";
        aplicar(await pedir("/guia/terminar", {}));
    }
    function verRuta() { vista = "ruta"; pasoPintado = ""; pintar(); }

    async function dejar() {
        if (!confirm(_("¿Dejar la guía? Lo que ya has hecho (alineación, enfoque, darks) no se pierde."))) return;
        vista = "menu";
        aplicar(await pedir("/guia/terminar", {}));
    }

    // Ir a otra pantalla sin soltar la guía: la banda sigue arriba y la pantalla se prepara al llegar
    function irA(destino, alLlegar) {
        const s = sesion();
        try { sessionStorage.setItem(CLAVE_LLEGAR, alLlegar || (s ? s.paso : "")); } catch (e) {}
        location.href = destino;
    }

    // ——— Menú ———
    async function empezar(clave) {
        const a = E.menu.find(x => x.clave === clave);
        if (!a || a.apagada) return;
        if (a.vista === "galeria") { verGaleria(); return; }
        const d = await pedir("/guia/empezar", { actividad: clave });
        if (d.ok && d.destino) { irA(d.destino, d.al_llegar || ""); return; }
        vista = "ruta";
        pasoPintado = "";
        if (!aplicar(d)) { vista = "menu"; pintar(); }
    }

    // ——— Pasos ———
    async function paso(accion, clave) {
        mostrarError("");
        return aplicar(await pedir("/guia/paso", { accion: accion, paso: clave }));
    }

    async function siguiente() {
        const s = sesion();
        if (!s) return;
        mostrarError("");
        if (s.paso === "captura") {
            const d = await pedir("/guia/captura", { exposicion: $("guia-exposicion").value, ganancia: $("guia-ganancia").value, fotos: $("guia-fotos").value });
            if (!aplicar(d)) return;
        } else if (s.paso === "calibracion") {
            if ((s.trabajo_darks || {}).en_curso) { mostrarError(_(FALTA.calibracion)); return; }
            const opcion = (document.querySelector('input[name="guia-dark"]:checked') || {}).value || "ultimo";
            if (opcion === "nuevo" && !darksLanzados) {
                $("guia-darks-tapar").hidden = false;
                $("guia-darks-opciones").hidden = true;
                return;
            }
            await pedir("/preferencias", { opcion_dark: opcion === "sin" ? "sin" : "ultimo" });
        } else if (!s.puede_seguir) {
            const falta = FALTA[s.paso];
            mostrarError(falta ? _(falta) : _("Falta algo en este paso."));
            return;
        }
        paso("siguiente");
    }

    async function saltar() {
        const s = sesion();
        if (!s || (s.aviso_saltar && !confirm(s.aviso_saltar + "\n\n" + _("¿Saltar este paso?")))) return;
        paso("saltar");
    }

    async function marcar(clave) {
        mostrarError("");
        aplicar(await pedir("/guia/marcar", { clave: clave }));
    }

    async function conectar() {
        const boton = $("guia-conectar");
        boton.disabled = true;
        mostrarError("");
        const d = await pedir("/conectar-equipo", {});
        boton.disabled = false;
        if (d.errores && d.errores.length) mostrarError(d.errores.join(" · "));
        else if (d.error) mostrarError(d.error);
        if (window.refrescarEstadoEquipo) refrescarEstadoEquipo();
        refrescar();
    }

    // ——— Objeto ———
    function claseTipo(tipo) {
        const t = String(tipo || "").toLowerCase();
        return /galax/.test(t) ? "galaxia" : /nebul|remanente|supernova/.test(t) ? "nebulosa" : /c[uú]mul|cluster/.test(t) ? "cumulo" : "otro";
    }

    function tarjetaObjeto(o, lista, i) {
        const elegido = sesion() && sesion().objeto && sesion().objeto.nombre === o.nombre;
        const alt = o.alt_ahora;
        const tipo = o.tipo ? _(o.tipo) : "";
        const detalle = [tipo, o.mag != null ? _("mag {m}", { m: fmtNum(o.mag, 1) }) : "",
                         o.tamano_arcmin ? fmtNum(o.tamano_arcmin, 0) + "′" : ""].filter(Boolean).join(" · ");
        // Los textos de _() van fuera de las plantillas: dentro, la extracción del catálogo no los ve
        const textoAltura = alt != null ? _("{alt}° de altura", { alt: fmtNum(alt, 0) }) : "";
        const alterno = o.nombre_alt ? " <small>" + esc(o.nombre_alt) + "</small>" : "";
        return `<button type="button" class="guia-objeto ${claseTipo(o.tipo)}${elegido ? " elegido" : ""}" data-lista="${lista}" data-i="${i}">` +
            `<span class="guia-objeto-nombre">${esc(o.nombre)}${alterno}</span>` +
            `<span class="guia-objeto-detalle">${esc(o.detalle || detalle)}</span>` +
            (alt != null ? `<span class="guia-altura"><span style="width: ${Math.max(0, Math.min(100, alt / 0.9))}%"></span></span>` +
                           `<span class="guia-altura-texto">${esc(textoAltura)}</span>` : "") +
            "</button>";
    }

    async function cargarRecomendados() {
        const d = await pedir("/esta-noche/objetos");
        if (!d.objetos) {
            const fallo = d.error || _("No se pudo calcular qué se ve ahora.");
            $("guia-recomendados").innerHTML = `<div class="estado err">${esc(fallo)}</div>`;
            return;
        }
        // Cielo profundo: fuera planetas, Luna y cuerpos menores. Primero lo que está alto ahora
        const dso = d.objetos.filter(o => !/planeta|luna|cometa|asteroide/i.test(o.tipo || ""));
        const ahora = dso.filter(o => o.visible_ahora);
        recomendados = (ahora.length >= 4 ? ahora : dso).slice(0, 8);
        if (sesion() && sesion().paso === "objeto") pintarRecomendados();
    }

    function pintarRecomendados() {
        if (!recomendados) return;
        const nada = _("Ahora mismo no hay nada alto. Busca un objeto por su nombre.");
        $("guia-recomendados").innerHTML = recomendados.length
            ? recomendados.map((o, i) => tarjetaObjeto(o, "recomendados", i)).join("")
            : `<div class="estado">${esc(nada)}</div>`;
    }

    function pintarResultados() {
        $("guia-resultados").innerHTML = resultados.map((o, i) => tarjetaObjeto(o, "resultados", i)).join("");
    }

    async function buscar() {
        const q = $("guia-buscar").value.trim();
        if (q.length < 2) return;
        mostrarError("");
        const d = await pedir("/buscar-objetos?q=" + encodeURIComponent(q));
        resultados = (d.resultados || []).slice(0, 6).map(r => ({ nombre: r.nombre, detalle: r.detalle, tipo: r.tipo, mag: r.mag, ra_h: r.ra, dec_deg: r.dec }));
        pintarResultados();
        // Nada en el catálogo de sugerencias: que lo busque el PC por su nombre (catálogo completo, efemérides, Internet)
        if (!resultados.length) aplicar(await pedir("/guia/objeto", { nombre: q }));
    }

    async function elegirObjeto(o) {
        mostrarError("");
        const conocido = (recomendados || []).find(r => r.nombre === o.nombre) || {};
        const cuerpo = o.ra_h != null && o.dec_deg != null
            ? { nombre: o.nombre, ra_h: o.ra_h, dec_deg: o.dec_deg, tipo: o.tipo || "", tamano_arcmin: o.tamano_arcmin || conocido.tamano_arcmin || null }
            : { nombre: o.nombre };
        aplicar(await pedir("/guia/objeto", cuerpo));
    }

    async function usarUltimoGoto() { mostrarError(""); aplicar(await pedir("/guia/objeto", { desde_goto: true })); }
    async function mosaico(activado) { aplicar(await pedir("/guia/objeto", { mosaico: activado })); }

    // ——— Exposición ———
    function pintarDuracion() {
        if (sesion() && sesion().actividad === "calibracion") { $("guia-duracion").textContent = ""; return; }
        const exp = parseFloat($("guia-exposicion").value), n = parseInt($("guia-fotos").value);
        $("guia-duracion").textContent = exp > 0 && n > 0
            ? _("{n} fotos de {exp} s son {min} min de exposición.", { n: n, exp: fmtNum(exp), min: fmtNum(exp * n / 60, 0) }) : "";
    }

    // ——— Darks ———
    function cancelarTapar() { $("guia-darks-tapar").hidden = true; $("guia-darks-opciones").hidden = false; }

    async function hacerDarks() {
        mostrarError("");
        const n = parseInt($("guia-darks-n").value) || 10;
        const d = await pedir("/darks/generar", { n: n });
        $("guia-darks-tapar").hidden = true;
        if (!d.ok) { mostrarError(d.error || _("No se pudo iniciar")); $("guia-darks-opciones").hidden = false; return; }
        darksLanzados = true;
        refrescar();
    }

    // ——— GoTo (estrella del enfoque y objeto) ———
    // POST /goto como la pantalla GoTo: true si la montura ha salido; pide el filtro solar si hace falta
    async function irPorGoto(nombre, ra_h, dec_deg, filtroSolar, sinCentrado) {
        let cuerpo = "objeto=" + encodeURIComponent(nombre);
        if (ra_h != null && dec_deg != null) cuerpo += "&ra=" + ra_h + "&dec=" + dec_deg;
        if (filtroSolar) cuerpo += "&filtro_solar=si";
        if (sinCentrado) cuerpo += "&sin_centrado=si";
        let d;
        try {
            d = await (await fetch("/goto", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: cuerpo })).json();
        } catch (e) {
            mostrarError(_("No se pudo hablar con el PC: {e}", { e: e }));
            return false;
        }
        if (d.confirmar_filtro_solar) {
            if (await confirmarFiltroSolar(d.confirmar_filtro_solar)) return irPorGoto(nombre, ra_h, dec_deg, true, sinCentrado);
            mostrarError(_("GoTo cancelado: filtro solar sin confirmar."));
            return false;
        }
        if (d.error || d.warning) { mostrarError(d.error || d.warning); return false; }
        return true;
    }

    async function irAlObjeto() {
        const s = sesion();
        if (!s || !s.objeto) return;
        mostrarError("");
        $("guia-goto").hidden = true;
        if (await irPorGoto(s.objeto.nombre, s.objeto.ra_h, s.objeto.dec_deg)) gotoLanzado = Date.now();
        refrescar();
    }

    // Espera a que la montura llegue y el centrado termine (o se omita); devuelve el resultado del centrado
    async function esperarGoto(pasoEsperado, alCambiar) {
        const inicio = Date.now();
        for (;;) {
            await new Promise(r => setTimeout(r, 2000));
            const tel = await pedir("/estado_telescopio"), cen = await pedir("/centrado/estado");
            const s = sesion();
            if (!abierta() || !s || s.paso !== pasoEsperado) return null;      // el usuario se ha ido a otra cosa
            const ocupado = tel.slewing || cen.en_curso || cen.previo;
            alCambiar(tel.slewing ? _("La montura se está moviendo...") : (cen.mensaje || (cen.previo || {}).mensaje || ""));
            if (!ocupado && Date.now() - inicio > 8000) return cen;
            if (Date.now() - inicio > 300000) return cen;
        }
    }

    async function irAEstrella() {
        const s = sesion();
        if (!s || !s.estrella) return;
        mostrarError("");
        const boton = $("guia-ir-estrella"), estado = $("guia-estrella-estado");
        boton.disabled = true;
        if (await irPorGoto(s.estrella.nombre, s.estrella.ra_h, s.estrella.dec_deg)) {
            estado.innerHTML = '<span class="spinner"></span>' + esc(_("La montura va hacia {objeto}...", { objeto: s.estrella.nombre }));
            const fin = await esperarGoto("enfoque", t => { if (t) estado.innerHTML = '<span class="spinner"></span>' + esc(t); });
            estado.textContent = "";
            boton.disabled = false;
            if (fin) enfocarAqui();
            return;
        }
        boton.disabled = false;
    }

    // Espera a que la montura deje de moverse (sin centrado por estrellas); false si el usuario se ha ido del paso
    async function esperarLlegada(sigue) {
        const inicio = Date.now();
        for (;;) {
            await espera(2000);
            if (!sigue()) return false;
            const tel = await pedir("/estado_telescopio");
            if ((!tel.slewing && Date.now() - inicio > 6000) || Date.now() - inicio > 240000) return true;
        }
    }

    // Ruta «Enfocar»: llevar el telescopio a algo con que enfocar y dejar Cámara en el modo que le toca
    async function apuntarEnfoque(que) {
        const s = sesion();
        if (!s) return;
        const estado = $("guia-enf-estado");
        const decir = t => { estado.innerHTML = t ? '<span class="spinner"></span>' + esc(t) : ""; };
        const sigue = () => abierta() && sesion() && sesion().paso === "apuntar_enfoque";
        let nota = "";
        mostrarError("");
        try {
            if (que === "estrella") {
                const e = s.estrella;
                if (!e) return;
                await pedir("/preferencias", { modo_dia: "off" });
                decir(_("La montura va hacia {objeto}...", { objeto: e.nombre }));
                if (!(await irPorGoto(e.nombre, e.ra_h, e.dec_deg))) return;
                if (!(await esperarGoto("apuntar_enfoque", t => { if (t) decir(t); }))) return;
                nota = _("El telescopio apunta a {objeto}", { objeto: e.nombre });
            } else if (que === "sol" || que === "luna") {
                const esSol = que === "sol";
                if (esSol) {
                    if (!(await confirmarFiltroSolar({ es_sol: true, objeto: _("Sol"), texto: _("Vas a apuntar el telescopio al Sol.") }))) return;
                    await pedir("/filtro-solar", { confirmado: true });
                }
                await pedir("/preferencias", { modo_dia: "on", foto_dia_objetivo: que });
                decir(esSol ? _("La montura va hacia el Sol...") : _("La montura va hacia la Luna..."));
                if (!(await irPorGoto(esSol ? "Sol" : "Luna", null, null, esSol, true))) return;
                if (!(await esperarLlegada(sigue))) return;
                nota = esSol ? _("El telescopio apunta al Sol") : _("El telescopio apunta a la Luna");
            } else if (que === "paisaje") {
                await pedir("/preferencias", { modo_dia: "on", foto_dia_objetivo: "paisaje" });
                nota = _("Un detalle lejano del paisaje");
            }
            if (await hecho("apuntar_enfoque", nota)) await paso("siguiente");
        } finally {
            decir("");
        }
    }

    async function enfocarAqui() {
        const s = sesion();
        if (s && s.actividad === "enfocar") {
            // El modo enfoque mide una estrella o el borde de un disco; con un paisaje no pinta nada
            const p = await pedir("/preferencias");
            await pedir("/enfoque", { activo: !(p.modo_dia === "on" && p.foto_dia_objetivo === "paisaje") });
        } else if (s && s.actividad !== "cielo_profundo") {
            // Rutas de día: Cámara ya está en modo día con su objetivo; el modo enfoque mide el borde del disco
            await pedir("/enfoque", { activo: !!s.astro });
        } else if (!E.de_dia) {
            // De noche, Cámara en modo enfoque (solo verde, para la máscara de Bahtinov) y sin el modo día
            await pedir("/enfoque", { activo: true });
            await pedir("/preferencias", { modo_dia: "off" });
        }
        irA("/foto-prueba", "enfoque");
    }

    // ——— Rutas de día: filtro solar, disco y Cámara ———
    async function confirmarFiltro() {
        mostrarError("");
        if (!(await confirmarFiltroSolar({ es_sol: true, objeto: _("Sol"), texto: _("Vas a apuntar el telescopio al Sol.") }))) return;
        await pedir("/filtro-solar", { confirmado: true });
        refrescar();
    }

    async function hecho(clave, nota) {
        mostrarError("");
        return aplicar(await pedir("/guia/hecho", { paso: clave, nota: nota || "" }));
    }

    // GoTo al Sol o a la Luna, foto de día y «sincronizar y centrar» con el disco; si algo falla, queda centrarlo a mano
    async function irAlDisco() {
        const s = sesion();
        if (!s || !s.astro) return;
        const esSol = s.astro === "sol", boton = $("guia-ir-disco"), estado = $("guia-disco-estado");
        const decir = t => { estado.innerHTML = t ? '<span class="spinner"></span>' + esc(t) : ""; };
        const sigue = () => abierta() && sesion() && sesion().paso === "apuntar_disco";
        mostrarError("");
        boton.disabled = true;
        try {
            decir(esSol ? _("La montura va hacia el Sol...") : _("La montura va hacia la Luna..."));
            if (!(await irPorGoto(esSol ? "Sol" : "Luna", null, null, esSol, true))) return;
            if (!(await esperarLlegada(sigue))) return;
            if (!s.orientacion_camara) {
                decir("");
                mostrarError(esSol ? _("La montura ya apunta hacia el Sol. Céntralo a mano en Cámara y pulsa «Hecho, siguiente».")
                                   : _("La montura ya apunta hacia la Luna. Céntrala a mano en Cámara y pulsa «Hecho, siguiente»."));
                return;
            }
            decir(_("Haciendo una foto para encontrar el disco..."));
            const foto = await pedir("/foto-dia/iniciar", { objetivo: s.astro, campo: 100, filtro_solar: esSol });
            if (!foto.ok) { mostrarError(foto.error || _("No se pudo empezar")); return; }
            for (;;) {
                await espera(1000);
                if (!sigue()) return;
                const e = await pedir("/foto-dia/estado");
                if (e.error) { mostrarError(e.error); return; }
                if (!e.en_curso) break;
            }
            decir(_("Buscando el disco y centrándolo..."));
            const d = await pedir("/disco/sincronizar", { centrar: true });
            if (!d.ok) {
                mostrarError((d.mensaje || d.error || "") + " " + _("Puedes centrarlo a mano en Cámara."));
                return;
            }
            await hecho("apuntar_disco", d.mensaje);
        } finally {
            decir("");
            boton.disabled = false;
        }
    }

    // Último paso de las rutas de día: la guía termina y Cámara abre la pestaña de lo elegido
    async function abrirCamara() {
        const s = sesion();
        const modo = s ? s.modo : "foto";
        await pedir("/enfoque", { activo: false });
        await pedir("/guia/terminar", {});
        irA("/foto-prueba", modo);
    }

    function abrirApilado() {
        const s = sesion();
        irA("/apilado", s && s.mosaico ? "mosaico" : "listo");
    }

    // ——— Banda: el paso que se hace en esta pantalla ya está ———
    async function hechoSiguiente() {
        const s = sesion();
        if (!s) return;
        let d = null;
        if (s.paso === "objeto") {
            d = await pedir("/guia/objeto", { desde_goto: true });
            if (!d.ok) { await abrir(); mostrarError(d.error || ""); return; }
        } else if (s.paso === "enfoque") {
            await pedir("/enfoque", { activo: false });      // el apilado no debe quedarse en solo verde
            d = await pedir("/guia/marcar", { clave: "enfoque" });
        } else if (s.paso === "alineacion") {
            d = await pedir("/guia/marcar", { clave: "alineacion" });
        } else if (s.paso === "apuntar_disco" || s.paso === "encuadre") {
            d = await pedir("/guia/hecho", { paso: s.paso });
        }
        if (d && d.menu) E = d;
        await paso("siguiente");
        abrir();
    }

    // ——— Eventos ———
    document.addEventListener("click", ev => {
        const tarjeta = ev.target.closest("#guia-tarjetas .guia-tarjeta");
        if (tarjeta) { empezar(tarjeta.dataset.actividad); return; }
        const chip = ev.target.closest("#guia-pasos .guia-paso");
        if (chip) { paso("ir", chip.dataset.paso); return; }
        const modo = ev.target.closest("#guia-modos .guia-modo");
        if (modo) { pedir("/guia/modo", { modo: modo.dataset.modo }).then(aplicar); return; }
        const objeto = ev.target.closest("#guia .guia-objeto");
        if (objeto) {
            const lista = objeto.dataset.lista === "resultados" ? resultados : (recomendados || []);
            if (lista[objeto.dataset.i]) elegirObjeto(lista[objeto.dataset.i]);
        }
    });
    $("guia").addEventListener("click", ev => { if (ev.target === $("guia")) cerrar(); });
    document.addEventListener("keydown", ev => { if (ev.key === "Escape" && abierta()) cerrar(); });
    $("guia-buscar").addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); buscar(); } });
    ["guia-exposicion", "guia-fotos"].forEach(id => $(id).addEventListener("input", pintarDuracion));
    // La cabecera ya sondea el equipo cada 15 s: con una guía en marcha se aprovecha para ponerla al día
    document.addEventListener("estado-equipo", () => { if (E && (sesion() || abierta()) && !sondeo) refrescar(); });

    window.Guia = { abrir, cerrar, verMenu, verRuta, dejar, irA, paso, siguiente, saltar, marcar, conectar, buscar, usarUltimoGoto,
                    mosaico, cancelarTapar, hacerDarks, irAlObjeto, irAEstrella, enfocarAqui, abrirApilado, hechoSiguiente,
                    confirmarFiltro, hecho, irAlDisco, abrirCamara, apuntarEnfoque, terminarAlMenu, verGaleria };

    // Al cargar: una consulta para la banda y la franja del inicio, y lo que la pantalla prepare al llegar
    refrescar().then(() => {
        let alLlegar = "";
        try { alLlegar = sessionStorage.getItem(CLAVE_LLEGAR) || ""; sessionStorage.removeItem(CLAVE_LLEGAR); } catch (e) {}
        if (alLlegar && typeof window.guiaAlLlegar === "function") window.guiaAlLlegar(alLlegar);
    });
})();
