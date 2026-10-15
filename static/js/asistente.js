// static/js/asistente.js — asistente de configuración inicial (templates/_asistente.html).
//
//   abrirAsistente()   lo abre con los datos ya guardados (botón de Configuración)
//   cerrarAsistente()  lo cierra; si se probaron otros drivers y no se ha terminado, vuelven los de antes
//
// Se abre solo al cargar cualquier página mientras config.ini tenga asistente_pendiente = true
// (data-pendiente del modal). «Cerrar» lo aparca hasta la próxima vez que se abra el navegador.
// Rutas del PC: /asistente/datos, /asistente/probar, /asistente/restaurar-drivers,
// /asistente/astap(/instalar|/cancelar) y /asistente/guardar (ScopePower.py).
(function () {
    const PASOS = ["idioma", "inicio", "telescopio", "montura", "camara", "captura", "astap", "ubicacion", "resumen"];
    const SALTABLES = new Set(["montura", "camara", "astap"]);
    const AVISO_SALTAR = {
        montura: N_("Sin montura no funcionarán el GoTo, el pad ni el centrado. Podrás elegir el driver más tarde en Configuración."),
        camara: N_("Sin cámara no se pueden hacer fotos ni apilar. Podrás elegir el driver más tarde en Configuración."),
        astap: N_("Sin ASTAP no funcionarán el centrado tras el GoTo, la sincronización por foto ni la alineación del apilado."),
    };
    const NOMBRE_CATALOGO = { d05: "D05", d20: "D20", d50: "D50", d80: "D80", h17: "H17", h18: "H18", w08: "W08", v50: "V50", g05: "G05" };

    let A = null;            // estado de esta apertura del asistente
    let sondeoAstap = null;
    const $ = id => document.getElementById(id);
    const modal = () => $("asistente");

    function estadoNuevo() {
        return {
            paso: 0, guardado: false, originales: {},
            prueba: { montura: null, camara: null },    // datos devueltos por /asistente/probar si conectó
            driverProbado: { montura: "", camara: "" },
            saltado: new Set(), astap: null, pendiente: false,
        };
    }

    // ——— Abrir y cerrar ———
    async function abrirAsistente() {
        A = estadoNuevo();
        ocultarError();
        $("asis-listo").hidden = true;
        $("asis-res-tel").innerHTML = ""; $("asis-res-cam").innerHTML = ""; $("asis-ubic-estado").textContent = "";
        modal().classList.add("abierto");
        irA(0);
        try {
            const d = await (await fetch("/asistente/datos")).json();
            A.pendiente = d.pendiente;
            A.originales = { driver_telescopio: d.driver_telescopio, driver_camara: d.driver_camara };
            $("asis-titulo-inicio").textContent = d.pendiente ? _("Vamos a dejar ScopePower listo") : _("Asistente de configuración");
            $("asis-texto-inicio").textContent = d.pendiente
                ? _("En unos minutos configuramos tu equipo y comprobamos que todo responde:")
                : _("Repasamos la configuración guardada paso a paso; cambia lo que quieras y comprueba que todo responde:");
            $("asis-focal").value = d.focal_len || "";
            $("asis-exposicion").value = d.exposicion || "";
            $("asis-ganancia").value = d.ganancia_camara || "";
            const sinUbicacion = Math.abs(+d.latitud) < 1e-6 && Math.abs(+d.longitud) < 1e-6;
            $("asis-lat").value = sinUbicacion ? "" : d.latitud;
            $("asis-lon").value = sinUbicacion ? "" : d.longitud;
            $("asis-alt").value = sinUbicacion ? "" : d.altitud;
            $("asis-autor").value = d.autor || "";
            await Promise.all([cargarDrivers("Telescope", "asis-driver-tel", d.driver_telescopio),
                               cargarDrivers("Camera", "asis-driver-cam", d.driver_camara)]);
        } catch (e) {
            mostrarError(_("No se pudieron leer los datos guardados: {e}", { e: e }));
        }
    }

    async function cerrarAsistente() {
        if (!modal().classList.contains("abierto")) return;
        modal().classList.remove("abierto");
        clearInterval(sondeoAstap); sondeoAstap = null;
        if (!A) return;
        if (!A.guardado) {
            try { sessionStorage.setItem("asistente_aparcado", "1"); } catch (e) {}
            // Probar un driver lo deja escrito en config.ini: si no se termina, vuelve el de antes
            if ((A.driverProbado.montura || A.driverProbado.camara) && (A.originales.driver_telescopio || A.originales.driver_camara)) {
                fetch("/asistente/restaurar-drivers", { method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(A.originales) }).catch(() => {});
            }
        } else {
            location.reload();     // la cabecera y la página se pintan con la configuración nueva
        }
    }

    async function cargarDrivers(tipo, idSelect, guardado) {
        const sel = $(idSelect);
        let drivers = [];
        try { drivers = (await (await fetch("/ascom-drivers?tipo=" + tipo)).json()).drivers || []; } catch (e) {}
        sel.innerHTML = "";
        drivers.forEach(d => sel.add(new Option(`${d.name} (${d.progid})`, d.progid)));
        const instalado = drivers.some(d => d.progid === guardado);
        if (guardado && !instalado && A.pendiente) {
            // Primera configuración: el driver de la plantilla (SynScan, Player One) es solo una suposición
            sel.add(new Option(tipo === "Telescope" ? _("— Elige el driver de tu montura —") : _("— Elige el driver de tu cámara —"), ""), 0);
            sel.value = "";
        } else {
            if (guardado && !instalado) sel.add(new Option(_("{driver} (no está instalado en este PC)", { driver: guardado }), guardado), 0);
            if (guardado) sel.value = guardado;
        }
        if (tipo === "Telescope") $("asis-sin-drivers-tel").hidden = drivers.length > 0;
        sel.onchange = () => {    // otro driver: la prueba anterior ya no vale
            const disp = tipo === "Telescope" ? "montura" : "camara";
            A.prueba[disp] = null;
            $(tipo === "Telescope" ? "asis-res-tel" : "asis-res-cam").innerHTML = "";
            if (tipo === "Telescope") pintarAyudaMontura();
        };
        if (tipo === "Telescope") pintarAyudaMontura();
    }

    // ——— Navegación ———
    function irA(i) {
        A.paso = Math.max(0, Math.min(PASOS.length - 1, i));
        const nombre = PASOS[A.paso];
        document.querySelectorAll("#asistente .asis-paso").forEach(s => s.hidden = s.dataset.paso !== nombre);
        $("asis-puntos").innerHTML = PASOS.map((__, j) => `<span class="${j < A.paso ? "hecho" : j === A.paso ? "actual" : ""}"></span>`).join("");
        $("asis-contador").textContent = `${A.paso + 1} / ${PASOS.length}`;
        $("asis-atras").hidden = A.paso === 0 || A.guardado;
        $("asis-saltar").hidden = !SALTABLES.has(nombre);
        $("asis-siguiente").hidden = A.guardado;
        $("asis-ir-inicio").hidden = !A.guardado;
        $("asis-siguiente").innerHTML = nombre === "resumen" ? '<svg><use href="#i-guardar"/></svg>' + _("Guardar y terminar")
            : nombre === "inicio" ? _("Empezar") + '<svg><use href="#i-flecha"/></svg>' : _("Siguiente") + '<svg><use href="#i-flecha"/></svg>';
        ocultarError();
        if (nombre === "captura") pintarGanancia();
        if (nombre === "astap") comprobarAstap();
        if (nombre === "resumen") pintarResumen();
        const caja = document.querySelector("#asistente .modal-caja");
        if (caja) caja.scrollTop = 0;
    }

    function asisAtras() { irA(A.paso - 1); }

    // Primer paso: el idioma se guarda al momento y la página se recarga para pintarse entera en el
    // idioma nuevo; al volver, el asistente se abre solo en la bienvenida (asistente_reabrir).
    async function asisIdioma(codigo) {
        if (codigo === (window.IDIOMA || {}).codigo) { irA(1); return; }
        try {
            const r = await (await fetch("/idioma", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ idioma: codigo }) })).json();
            if (!r.ok) { mostrarError(r.mensaje); return; }
            try { sessionStorage.setItem("asistente_reabrir", "1"); sessionStorage.removeItem("asistente_aparcado"); } catch (e) {}
            location.reload();
        } catch (e) {
            mostrarError(_("No se pudo hablar con el PC: {e}", { e: e }));
        }
    }

    function asisSaltar() {
        const nombre = PASOS[A.paso];
        if (!SALTABLES.has(nombre)) return;
        A.saltado.add(nombre);
        irA(A.paso + 1);
        mostrarAviso(_(AVISO_SALTAR[nombre]));
    }

    async function asisSiguiente() {
        const nombre = PASOS[A.paso];
        const error = validar(nombre);
        if (error) { mostrarError(error); return; }
        A.saltado.delete(nombre);
        if (nombre === "resumen") { await guardar(); return; }
        irA(A.paso + 1);
    }

    function validar(nombre) {
        if (nombre === "telescopio") {
            const f = parseFloat($("asis-focal").value);
            if (!(f >= 50 && f <= 5000)) return _("Escribe la focal del telescopio en milímetros (entre 50 y 5000).");
        }
        if (nombre === "montura" && !A.prueba.montura) return _("Pulsa «Probar conexión» para comprobar que la montura responde, o «Saltar este paso» si ahora no la tienes.");
        if (nombre === "camara" && !A.prueba.camara) return _("Pulsa «Probar conexión» para comprobar que la cámara responde, o «Saltar este paso» si ahora no la tienes.");
        if (nombre === "captura") {
            const e = $("asis-exposicion").value.trim();
            if (e && !(parseFloat(e) > 0 && parseFloat(e) <= 3600)) return _("La exposición tiene que estar entre 0,1 y 3600 segundos.");
            const g = $("asis-ganancia").value.trim();
            if (g && !/^\d+$/.test(g)) return _("La ganancia tiene que ser un número entero, o déjala vacía.");
        }
        if (nombre === "astap" && !(A.astap && A.astap.ok) && !(A.astap && A.astap.progreso.en_curso))
            return _("Instala ASTAP con su catálogo, o pulsa «Saltar este paso».");
        if (nombre === "ubicacion") {
            const lat = parseFloat($("asis-lat").value), lon = parseFloat($("asis-lon").value);
            if (isNaN(lat) || isNaN(lon)) return _("Falta la latitud o la longitud. Pulsa «Usar la ubicación de este móvil» o escríbelas (las da Google Maps al tocar un punto del mapa).");
            if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return _("La latitud va de −90 a 90 y la longitud de −180 a 180.");
            if (Math.abs(lat) < 1e-6 && Math.abs(lon) < 1e-6) return _("0°, 0° está en mitad del océano: escribe la ubicación de tu observatorio.");
        }
        return "";
    }

    // ——— Montura y cámara ———
    async function asisProbar(disp) {
        const sel = $(disp === "montura" ? "asis-driver-tel" : "asis-driver-cam");
        const res = $(disp === "montura" ? "asis-res-tel" : "asis-res-cam");
        const boton = $(disp === "montura" ? "asis-probar-tel" : "asis-probar-cam");
        ocultarError();
        if (!sel.value) { mostrarError(_("Elige un driver de la lista.")); return; }
        boton.disabled = true;
        res.innerHTML = '<div class="estado"><span class="spinner"></span>'
            + (disp === "montura" ? _("Conectando con la montura... (puede tardar hasta 30 s)") : _("Conectando con la cámara... (puede tardar hasta 30 s)")) + "</div>";
        A.prueba[disp] = null;
        A.driverProbado[disp] = sel.value;
        try {
            const r = await (await fetch("/asistente/probar", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ dispositivo: disp, driver: sel.value }) })).json();
            if (r.ok) {
                A.prueba[disp] = r.datos;
                res.innerHTML = disp === "montura" ? textoMonturaOk(r.datos) : textoCamaraOk(r.datos);
            } else {
                const error = r.error || _("error desconocido");
                // Si la montura conecta pero no contesta, el error del PC ya trae el consejo general
                // (_activar_seguimiento): solo falta lo propio de su familia de drivers
                let extra = consejos(disp);
                if (disp === "montura" && r.consejo)
                    extra = r.consejo === "synscan" ? "" : ayudaMontura($("asis-driver-tel"));
                extra = extra ? "<br>" + extra : "";
                res.innerHTML = '<div class="aviso aviso-err"><svg class="ico"><use href="#i-info"/></svg><div>'
                    + _("<b>No conecta:</b> {error}", { error: escapar(error) }) + extra + "</div></div>";
            }
        } catch (e) {
            res.innerHTML = '<div class="aviso aviso-err"><svg class="ico"><use href="#i-info"/></svg><div>'
                + _("No se pudo hablar con el PC: {e}", { e: escapar(String(e)) }) + "</div></div>";
        } finally {
            boton.disabled = false;
            refrescarChips();
        }
    }

    function consejos(disp) {
        return disp === "montura"
            ? _("Comprueba que la montura está encendida y conectada al PC y que el driver es el de tu modelo.") + " " + ayudaMontura($("asis-driver-tel"))
            : _("Comprueba que la cámara está enchufada al USB del PC (y alimentada si lo necesita), que ningún otro programa la está usando y que el driver es el de tu cámara.");
    }

    // Qué necesita cada familia de drivers de montura para conectar. Se mira el ProgID y el nombre
    // que da el perfil ASCOM; lo que no se reconoce recibe el consejo general.
    const AYUDA_MONTURA = [
        [/simulat|scopesim|omnisim|_sim\b/i, N_("Simulador: sirve para probar ScopePower sin montura; no mueve nada real.")],
        [/synscan/i, N_("Sky-Watcher a través de la app <b>SynScan Pro</b>: ábrela en el PC y conéctala a la montura (por su wifi o por cable) antes de probar.")],
        [/eqmod/i, N_("<b>EQMOD</b>: montura Sky-Watcher u Orion por cable EQDirect al PC. Al conectar se abre su ventana en el PC; el puerto COM se elige ahí o con «Configurar el driver en el PC».")],
        [/gss|greenswamp/i, N_("<b>GSServer</b>: ábrelo en el PC y conéctalo a la montura antes de probar.")],
        [/celestron|nexstar/i, N_("<b>Celestron</b>: mando NexStar o StarSense por cable al PC (o CPWI abierto, según el driver). El puerto COM se elige con «Configurar el driver en el PC».")],
        [/ioptron/i, N_("<b>iOptron</b>: cable USB o serie al PC. El puerto se elige con «Configurar el driver en el PC».")],
        [/onstep/i, N_("<b>OnStep</b>: por USB o por wifi. El puerto o la dirección IP se ponen con «Configurar el driver en el PC».")],
        [/lx200|meade|autostar/i, N_("<b>Meade / LX200</b>: cable al PC. El puerto COM se elige con «Configurar el driver en el PC».")],
        [/esp32go/i, N_("<b>Esp32Go</b>: por wifi. La dirección IP se pone con «Configurar el driver en el PC».")],
        [/alpaca/i, N_("Montura <b>Alpaca</b> por red, dada de alta en el PC con el selector de ASCOM: tiene que estar encendida y en la misma red que el PC.")],
        [/hub/i, N_("Es un <b>concentrador</b> que reparte la montura entre varios programas: antes elige en el PC qué montura real usa, con «Configurar el driver en el PC».")],
    ];
    function ayudaMontura(sel) {
        const o = sel.options[sel.selectedIndex];
        const texto = o ? o.value + " " + o.text : "";
        const familia = AYUDA_MONTURA.find(([patron]) => patron.test(texto));
        return familia ? _(familia[1])
            : _("Enciende la montura y conéctala al PC como indique su fabricante. Si es la primera vez que usas este driver en este PC, elige su puerto COM o su dirección IP con «Configurar el driver en el PC».");
    }
    function pintarAyudaMontura() { $("asis-ayuda-tel").innerHTML = ayudaMontura($("asis-driver-tel")); }

    // Selector de ASCOM en la pantalla del PC (botón Properties del driver: puerto COM, IP, modelo)
    async function asisSelectorAscom(disp) {
        const tel = disp === "montura";
        const sel = $(tel ? "asis-driver-tel" : "asis-driver-cam"), res = $(tel ? "asis-res-tel" : "asis-res-cam");
        const boton = $(tel ? "asis-selector-tel" : "asis-selector-cam");
        ocultarError();
        boton.disabled = true;
        res.innerHTML = '<div class="estado"><span class="spinner"></span>'
            + _("Se ha abierto el selector de ASCOM en la pantalla del PC: elige el driver, pulsa <b>Properties</b> para configurarlo y cierra con <b>OK</b>.") + "</div>";
        try {
            const d = await (await fetch(`/ascom-selector?tipo=${tel ? "Telescope" : "Camera"}&actual=${encodeURIComponent(sel.value)}`)).json();
            if (d.driver) {
                if (![...sel.options].some(o => o.value === d.driver)) sel.add(new Option(d.driver, d.driver));
                sel.value = d.driver;
                sel.onchange();
                res.innerHTML = '<div class="aviso aviso-info"><svg class="ico"><use href="#i-info"/></svg><div>'
                    + _("Driver elegido en el PC: <b>{driver}</b>. Ahora pulsa «Probar conexión».", { driver: escapar(d.driver) }) + "</div></div>";
            } else {
                res.innerHTML = '<div class="estado">' + _("Se cerró el selector sin elegir driver.") + "</div>";
            }
        } catch (e) {
            res.innerHTML = '<div class="aviso aviso-err"><svg class="ico"><use href="#i-info"/></svg><div>'
                + _("No se pudo abrir el selector de ASCOM en el PC: {e}", { e: escapar(String(e)) }) + "</div></div>";
        } finally {
            boton.disabled = false;
        }
    }

    function textoMonturaOk(d) {
        const ra = d.ra, h = Math.floor(ra), m = Math.round((ra - h) * 60);
        const dec = d.dec, sg = dec < 0 ? "−" : "+", ad = Math.abs(dec), g = Math.floor(ad), mm = Math.round((ad - g) * 60);
        const titulo = d.nombre ? _("La montura responde ({nombre}).", { nombre: escapar(d.nombre) }) : _("La montura responde.");
        const apunta = _("Apunta ahora a AR {h} h {m} min, Dec {dec}.", { h: h, m: m, dec: sg + g + "° " + mm + "′" });
        return `<div class="aviso aviso-ok"><svg class="ico"><use href="#i-ok"/></svg><div><b>${titulo}</b><br>${apunta}</div></div>`;
    }

    function campoCamara() {
        const c = A && A.prueba.camara, f = parseFloat($("asis-focal").value);
        if (!c || !c.pixel_um || !c.ancho_px || !c.alto_px || !(f > 0)) return null;
        const escala = 206.265 * c.pixel_um / f;    // ″/píxel (ScopePower.py, hacer_dither)
        return { escala, ancho: c.ancho_px * escala / 3600, alto: c.alto_px * escala / 3600 };
    }

    function textoCamaraOk(d) {
        const campo = campoCamara();
        const sensor = !d.ancho_px ? ""
            : d.pixel_um ? _("Sensor de {ancho} × {alto} píxeles de {pixel} µm.", { ancho: d.ancho_px, alto: d.alto_px, pixel: fmtNum(d.pixel_um, 2) })
            : _("Sensor de {ancho} × {alto} píxeles.", { ancho: d.ancho_px, alto: d.alto_px });
        const titulo = d.nombre ? _("La cámara responde ({nombre}).", { nombre: escapar(d.nombre) }) : _("La cámara responde.");
        return `<div class="aviso aviso-ok"><svg class="ico"><use href="#i-ok"/></svg><div><b>${titulo}</b>`
            + (sensor ? "<br>" + sensor : "")
            + (campo ? "<br>" + _("Con {focal} mm de focal verás un campo de <b>{ancho}° × {alto}°</b> ({escala}″ por píxel).",
                                  { focal: escapar($("asis-focal").value), ancho: fmtNum(campo.ancho, 2), alto: fmtNum(campo.alto, 2), escala: fmtNum(campo.escala, 2) }) : "")
            + `</div></div>`;
    }

    function pintarGanancia() {
        const g = A.prueba.camara && A.prueba.camara.ganancia, ayuda = $("asis-ganancia-ayuda"), campo = $("asis-ganancia");
        campo.removeAttribute("max");
        if (!g) { ayuda.textContent = _("Vacío = no tocarla (queda la que tenga el driver)."); return; }
        if (!g.soportada) { ayuda.textContent = _("Esta cámara (o su driver) no deja ajustar la ganancia: déjala vacía."); return; }
        if (g.minima !== null && g.maxima !== null) {
            campo.min = g.minima; campo.max = g.maxima;
            ayuda.textContent = _("La cámara admite de {minima} a {maxima} (ahora tiene {actual}). Vacío = no tocarla. Si la cambias, haz darks nuevos.", { minima: g.minima, maxima: g.maxima, actual: g.actual });
        } else {
            ayuda.textContent = _("Ahora tiene {actual}. Vacío = no tocarla.", { actual: g.actual });
        }
    }

    // ——— ASTAP ———
    async function comprobarAstap() {
        const campo = campoCamara();
        const lado = campo ? Math.min(campo.ancho, campo.alto) : "";
        try {
            A.astap = await (await fetch("/asistente/astap?campo=" + lado)).json();
        } catch (e) {
            $("asis-astap-estado").innerHTML = '<div class="aviso aviso-err"><div>' + _("No se pudo comprobar ASTAP: {e}", { e: escapar(String(e)) }) + "</div></div>";
            return;
        }
        pintarAstap();
        if (A.astap.progreso.en_curso) sondearAstap();
    }

    function pintarAstap() {
        const s = A.astap, est = $("asis-astap-estado"), bloque = $("asis-astap-instalar");
        const nombres = s.catalogos.map(c => NOMBRE_CATALOGO[c] || c.toUpperCase()).join(", ");
        if (s.ok) {
            est.innerHTML = '<div class="aviso aviso-ok"><svg class="ico"><use href="#i-ok"/></svg><div>'
                + _("<b>ASTAP está instalado</b> con el catálogo {catalogos}.", { catalogos: nombres }) + `<br><small>${escapar(s.ruta)}</small></div></div>`;
            bloque.hidden = true;
            return;
        }
        est.innerHTML = `<div class="aviso aviso-warn"><svg class="ico"><use href="#i-info"/></svg><div>`
            + (s.programa ? _("<b>ASTAP está instalado pero sin catálogo de estrellas</b> ({ruta}).", { ruta: escapar(s.ruta) }) : _("<b>ASTAP no está instalado en este PC.</b>"))
            + " " + _("Se puede instalar ahora sin salir de aquí.") + "</div></div>";
        bloque.hidden = false;
        const sel = $("asis-catalogo"), rec = s.recomendado;
        if (!sel.options.length) {
            s.opciones.forEach(o => sel.add(new Option(`${o.nombre} · ${o.mb >= 1000 ? fmtNum(o.mb / 1000, 1) + " GB" : o.mb + " MB"} · ${o.nota}`, o.id)));
        }
        const recomendado = " " + _("(recomendado)");
        [...sel.options].forEach(o => { o.text = o.text.replace(recomendado, "") + (o.value === rec ? recomendado : ""); });
        sel.value = rec === "d80" ? "d50" : rec;
        const v = { campo: fmtNum(s.campo_deg, 2), web: s.web };
        $("asis-catalogo-ayuda").innerHTML = rec === "d80"
            ? (s.campo_deg
                ? _('Con tu campo de {campo}° (menos de 0,6°) hace falta el catálogo <b>D80</b> (1,2 GB), que solo existe como instalador para Windows: descárgalo en el PC desde <a href="{web}" target="_blank" rel="noopener">hnsky.org</a> e instálalo en la carpeta de ASTAP. El D50 puede valer si el campo no es mucho menor.', v)
                : _('Sin conocer todavía el campo de la cámara (menos de 0,6°) hace falta el catálogo <b>D80</b> (1,2 GB), que solo existe como instalador para Windows: descárgalo en el PC desde <a href="{web}" target="_blank" rel="noopener">hnsky.org</a> e instálalo en la carpeta de ASTAP. El D50 puede valer si el campo no es mucho menor.', v))
            : (s.campo_deg
                ? _("Con tu campo de {campo}° basta con el recomendado. Cuanto mayor es el catálogo, más estrellas débiles tiene (útil con campos pequeños), pero más tarda en bajar.", v)
                : _("Sin conocer todavía el campo de la cámara basta con el recomendado. Cuanto mayor es el catálogo, más estrellas débiles tiene (útil con campos pequeños), pero más tarda en bajar."));
        $("asis-astap-donde").textContent = _("Se descarga en el PC (no en el móvil) desde SourceForge, la web oficial de ASTAP, y se instala sin permisos de administrador en {carpeta}. Puedes seguir con los demás pasos mientras se descarga.", { carpeta: s.carpeta_local });
        pintarProgreso(s.progreso);
    }

    function pintarProgreso(p) {
        const caja = $("asis-astap-progreso"), barra = $("asis-astap-barra"), texto = $("asis-astap-texto");
        const enCurso = p.en_curso;
        $("asis-btn-instalar").hidden = enCurso;
        $("asis-btn-cancelar-astap").hidden = !enCurso;
        $("asis-catalogo").disabled = enCurso;
        if (!enCurso && !p.error && p.fase !== "cancelado") { caja.hidden = true; return; }
        caja.hidden = false;
        barra.className = "progress-bar";
        if (p.error) {
            barra.style.width = "100%"; barra.className = "progress-bar esperando";
            texto.className = "estado err mt-1";
            texto.textContent = _("❌ No se pudo instalar: {error}. Puedes reintentarlo o instalar ASTAP a mano desde hnsky.org.", { error: p.error });
            return;
        }
        if (p.fase === "cancelado") { caja.hidden = true; return; }
        texto.className = "estado mt-1";
        const pct = p.total ? Math.min(100, 100 * p.bytes / p.total) : 0;
        barra.style.width = pct.toFixed(1) + "%";
        if (p.fase === "descargando") {
            const mb = p.bytes / 1e6, total = p.total / 1e6;
            const quedan = p.mb_s > 0 && p.total ? (total - mb) / p.mb_s : null;
            texto.innerHTML = '<span class="spinner"></span>' + _("{texto}: {mb} de {total} MB", { texto: escapar(p.texto), mb: fmtNum(mb, 0), total: total ? fmtNum(total, 0) : "?" })
                + (p.mb_s ? ` · ${fmtNum(p.mb_s, 1)} MB/s` : "") + (quedan !== null ? " · " + _("quedan {tiempo}", { tiempo: duracion(quedan) }) : "");
        } else if (p.fase === "descomprimiendo") {
            texto.innerHTML = '<span class="spinner"></span>' + _("Descomprimiendo {texto}: {n} de {total} ficheros", { texto: escapar(p.texto), n: p.bytes, total: p.total });
        } else {
            texto.innerHTML = '<span class="spinner"></span>' + _("Preparando la descarga...");
        }
    }

    async function asisInstalarAstap() {
        ocultarError();
        const base = $("asis-catalogo").value;
        try {
            const r = await (await fetch("/asistente/astap/instalar", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ base }) })).json();
            if (!r.ok) { mostrarError(r.error || _("No se pudo empezar la instalación")); return; }
            A.astap.progreso = r.progreso;
            pintarProgreso(r.progreso);
            sondearAstap();
        } catch (e) { mostrarError(_("No se pudo hablar con el PC: {e}", { e: e })); }
    }

    function asisCancelarAstap() {
        fetch("/asistente/astap/cancelar", { method: "POST" }).catch(() => {});
    }

    function sondearAstap() {
        clearInterval(sondeoAstap);
        sondeoAstap = setInterval(async () => {
            let s;
            try { s = await (await fetch("/asistente/astap")).json(); } catch (e) { return; }
            const p = s.progreso;
            if (A) A.astap = Object.assign(A.astap || {}, { progreso: p });
            if (PASOS[A.paso] === "astap") pintarProgreso(p);
            if (!p.en_curso) {
                clearInterval(sondeoAstap); sondeoAstap = null;
                if (PASOS[A.paso] === "astap") comprobarAstap();
                else { A.astap = s; if (PASOS[A.paso] === "resumen") pintarResumen(); }
            }
        }, 1000);
    }

    // ——— Ubicación ———
    function asisUbicacionMovil() {
        const est = $("asis-ubic-estado");
        pedirUbicacionMovil({
            alEstado: (tipo, texto) => {
                if (tipo === "error") { est.className = "estado err"; est.textContent = "❌ " + texto; }
                else { est.className = "estado"; est.innerHTML = '<span class="spinner"></span>' + texto; }
            },
            alPosicion: c => {
                $("asis-lat").value = c.latitude.toFixed(5);
                $("asis-lon").value = c.longitude.toFixed(5);
                if (c.altitude !== null && c.altitude !== undefined) $("asis-alt").value = Math.round(c.altitude);
                est.className = "estado ok";
                est.textContent = _("✅ Posición del móvil recibida (precisión {m} m). Se guardará al terminar.", { m: Math.round(c.accuracy || 0) });
            }
        });
    }

    // ——— Resumen y guardado ———
    function linea(ok, titulo, detalle) {
        return `<li class="${ok ? "ok" : "warn"}"><svg class="ico"><use href="#i-${ok ? "ok" : "info"}"/></svg><div><b>${titulo}</b><small>${detalle}</small></div></li>`;
    }

    function nombreDriver(idSelect) {
        const sel = $(idSelect), o = sel.options[sel.selectedIndex];
        return o && o.value ? escapar(o.text) : _("Sin driver elegido");
    }

    function pintarResumen() {
        const campo = campoCamara();
        const s = A.astap;
        const ganancia = $("asis-ganancia").value.trim();
        let astap;
        if (s && s.ok) astap = linea(true, _("ASTAP instalado"), _("Catálogo {catalogos} · {ruta}", { catalogos: s.catalogos.map(c => NOMBRE_CATALOGO[c] || c).join(", "), ruta: escapar(s.ruta) }));
        else if (s && s.progreso && s.progreso.en_curso) astap = linea(false, _("ASTAP instalándose"), _("La descarga sigue en el PC aunque cierres el asistente; la ruta se guardará sola al terminar."));
        else astap = linea(false, _("ASTAP sin instalar"), _(AVISO_SALTAR.astap) + " " + _("Se puede instalar más tarde volviendo a abrir el asistente."));
        const lat = $("asis-lat").value, lon = $("asis-lon").value, alt = $("asis-alt").value;
        $("asis-resumen").innerHTML = [
            linea(true, _("Focal: {focal} mm", { focal: escapar($("asis-focal").value) }),
                  campo ? _("Campo de {ancho}° × {alto}° · {escala}″/píxel", { ancho: fmtNum(campo.ancho, 2), alto: fmtNum(campo.alto, 2), escala: fmtNum(campo.escala, 2) })
                        : _("Campo de visión sin calcular (la cámara no se ha probado)")),
            A.prueba.montura ? linea(true, _("Montura conectada"), nombreDriver("asis-driver-tel"))
                             : linea(false, _("Montura sin probar"), nombreDriver("asis-driver-tel") + ". " + _(AVISO_SALTAR.montura)),
            A.prueba.camara ? linea(true, _("Cámara conectada"), nombreDriver("asis-driver-cam"))
                            : linea(false, _("Cámara sin probar"), nombreDriver("asis-driver-cam") + ". " + _(AVISO_SALTAR.camara)),
            linea(true, _("Captura"), ganancia
                ? _("Exposición {exposicion} s · ganancia {ganancia}", { exposicion: escapar($("asis-exposicion").value || "—"), ganancia: escapar(ganancia) })
                : _("Exposición {exposicion} s · ganancia la del driver", { exposicion: escapar($("asis-exposicion").value || "—") })),
            astap,
            linea(true, _("Ubicación"), `${escapar(lat)}°, ${escapar(lon)}°` + (alt ? `, ${escapar(alt)} m` : "") + ($("asis-autor").value.trim() ? " · " + _("Autor: {autor}", { autor: escapar($("asis-autor").value.trim()) }) : "")),
        ].join("");
    }

    async function guardar() {
        const boton = $("asis-siguiente");
        boton.disabled = true;
        boton.innerHTML = '<span class="spinner"></span>' + _("Guardando...");
        const datos = {
            focal_len: $("asis-focal").value,
            exposicion: $("asis-exposicion").value,
            ganancia_camara: $("asis-ganancia").value.trim(),
            driver_telescopio: $("asis-driver-tel").value,
            driver_camara: $("asis-driver-cam").value,
            latitud: $("asis-lat").value, longitud: $("asis-lon").value, altitud: $("asis-alt").value || 0,
            autor: $("asis-autor").value.trim(),
        };
        try {
            const r = await fetch("/asistente/guardar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos) });
            const d = await r.json();
            if (!d.ok) { mostrarError(d.error || _("No se pudo guardar")); return; }
            A.guardado = true;
            try { sessionStorage.removeItem("asistente_aparcado"); } catch (e) {}
            $("asis-titulo-resumen").textContent = _("Configuración guardada");
            $("asis-listo").hidden = false;
            irA(PASOS.length - 1);
        } catch (e) {
            mostrarError(_("No se pudo guardar: {e}", { e: e }));
        } finally {
            boton.disabled = false;
            if (!A.guardado) boton.innerHTML = '<svg><use href="#i-guardar"/></svg>' + _("Guardar y terminar");
        }
    }

    // ——— Utilidades ———
    function mostrarError(t) { const e = $("asis-error"); e.className = "aviso aviso-err mt-2"; e.textContent = t; e.hidden = false; }
    function mostrarAviso(t) { const e = $("asis-error"); e.className = "aviso aviso-warn mt-2"; e.textContent = t; e.hidden = false; }
    function ocultarError() { $("asis-error").hidden = true; }
    function escapar(t) { return String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
    function duracion(s) { return s < 90 ? Math.max(1, Math.round(s)) + " s" : Math.round(s / 60) + " min"; }
    function refrescarChips() { if (typeof refrescarEstadoEquipo === "function") refrescarEstadoEquipo(); }

    window.abrirAsistente = abrirAsistente;
    window.cerrarAsistente = cerrarAsistente;
    window.asisAtras = asisAtras;
    window.asisSaltar = asisSaltar;
    window.asisSiguiente = asisSiguiente;
    window.asisProbar = asisProbar;
    window.asisSelectorAscom = asisSelectorAscom;
    window.asisInstalarAstap = asisInstalarAstap;
    window.asisCancelarAstap = asisCancelarAstap;
    window.asisUbicacionMovil = asisUbicacionMovil;
    window.asisIdioma = asisIdioma;

    // En captura: así se ve si la ayuda de la ubicación estaba abierta antes de que ayuda_voz.js la cierre
    // (entonces Escape solo cierra esa ayuda, no el asistente)
    document.addEventListener("keydown", e => {
        if (e.key === "Escape" && modal() && modal().classList.contains("abierto") && !$("modal-voz")?.classList.contains("abierto")) cerrarAsistente();
    }, true);
    // Configuración sin terminar: se abre solo (salvo que se haya cerrado en esta sesión del navegador)
    document.addEventListener("DOMContentLoaded", () => {
        if (modal()) modal().querySelectorAll("input").forEach(i => i.addEventListener("input", ocultarError));
        let aparcado = false, reabrir = false;
        try {
            aparcado = sessionStorage.getItem("asistente_aparcado") === "1";
            reabrir = sessionStorage.getItem("asistente_reabrir") === "1";
            sessionStorage.removeItem("asistente_reabrir");
        } catch (e) {}
        if (modal() && reabrir) { abrirAsistente(); irA(1); }     // vuelta de cambiar el idioma en el paso 1
        else if (modal() && modal().dataset.pendiente === "true" && !aparcado) abrirAsistente();
    });
})();
