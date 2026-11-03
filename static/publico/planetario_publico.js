// static/publico/planetario_publico.js — el planetario público: el cielo con las fotos publicadas.
//
// Es la misma vista que el planetario de la app, con el mismo motor (Celestia Atlas) y la misma
// capa de fotos (static/js/capa_fotos.js: cada foto deformada a su huella real), pero sin
// ScopePower detrás: el motor y los módulos compartidos vienen de _comun/<versión>/ (se importan
// aquí por la ruta que deja la página en window.PUBLICO) y las fotos, con sus esquinas en RA/Dec,
// de galeria.json. El observador es la posición redondeada que publica el dueño (o una genérica)
// y la hora, la del navegador. #<id> centra el mapa en esa foto; tocar una foto dice cuál es.
(async function () {
    "use strict";
    const P = window.PUBLICO || { raiz: ".", comun: "..", idioma: "es", url: "" };
    const $ = id => document.getElementById(id);
    const estadoEl = $("estado");

    // ── Tema e idioma, como en la galería ──
    $("tema").onclick = () => {
        const raiz = document.documentElement, oscuro = raiz.getAttribute("data-tema") !== "oscuro";
        if (oscuro) raiz.setAttribute("data-tema", "oscuro"); else raiz.removeAttribute("data-tema");
        try { localStorage.setItem("publico.tema", oscuro ? "oscuro" : "noche"); } catch (e) {}
    };
    document.querySelectorAll("[data-idioma]").forEach(a => { a.onclick = () => { a.href = a.getAttribute("href") + location.hash; }; });

    // Los nombres del Sistema Solar van traducidos (el motor los llama en inglés)
    const nombresSistemaSolar = { Sun: _("Sol"), Moon: _("Luna"), Mercury: _("Mercurio"), Venus: _("Venus"), Mars: _("Marte"),
        Jupiter: _("Júpiter"), Saturn: _("Saturno"), Uranus: _("Urano"), Neptune: _("Neptuno"), Pluto: _("Plutón") };

    let atlas, viewer, observador, capa, datos;
    try {
        // import() resuelve las rutas relativas respecto a este módulo (que vive en _comun/…/publico),
        // no respecto a la página: se pasan por la dirección del documento
        const desdePagina = ruta => new URL(ruta, document.baseURI).href;
        const [motor, catalogos, capaFotos, respuesta] = await Promise.all([
            import(desdePagina(P.comun + "/atlas/src/index.js")),
            import(desdePagina(P.comun + "/js/atlas_catalogos.js")),
            import(desdePagina(P.comun + "/js/capa_fotos.js")),
            fetch(P.raiz + "/galeria.json"),
        ]);
        atlas = motor;
        datos = await respuesta.json();
        // Observador: la posición redondeada del dueño o, si no la publica, una genérica (sin paisaje)
        const o = datos.observador;
        observador = o ? { latitudeDeg: o.latitud, longitudeDeg: o.longitud, elevationM: 0 } : { latitudeDeg: 40, longitudeDeg: 0, elevationM: 0 };
        $("aviso-posicion").hidden = !!o;
        const { stars, catalog, constellations } = catalogos.catalogosDesdeGlobales();
        viewer = atlas.createCelestiaAtlasViewer({
            container: $("atlas"), catalog, stars, constellations,
            observer: observador, utcMs: Date.now(),
            skySurveySource: null,          // sin teselas DSS: todo local
            onError: e => console.error("atlas", e),
        });
        window.viewer = viewer;
        viewer.setCoordinateMode("horizontal");
        viewer.setDisplayOptions({ ...catalogos.OPCIONES_VISTA, solarSystemNames: nombresSistemaSolar });
        aplicarTema();
        try { catalogos.vistaInicial(atlas, viewer, observador, Date.now()); } catch (e) { console.warn("vista inicial", e); }

        // ── Las fotos, con la misma capa que la app ──
        const lienzo = $("capa-fotos");
        capa = capaFotos.crearCapaFotos({ lienzo, api: atlas,
            urlFoto: f => P.raiz + "/" + encodeURIComponent(f.fichero) + "?v=" + (f.v || ""),
            opacidad: () => (+$("fotos-opacidad").value || 70) / 100 });
        try { const op = parseInt(localStorage.getItem("publico.fotos_opacidad")); if (op >= 10 && op <= 100) $("fotos-opacidad").value = op; } catch (e) {}
        $("fotos-opacidad").oninput = () => { try { localStorage.setItem("publico.fotos_opacidad", $("fotos-opacidad").value); } catch (e) {} };
        const fotos = (datos.fotos || []).filter(f => f && f.cielo && f.cielo.esquinas && f.ficheros && f.ficheros.cielo)
            .map(f => ({ ...f, nombre: f.id, fichero: f.ficheros.cielo, v: f.v && f.v.cielo, modo: f.cielo.modo,
                         esquinas: f.cielo.esquinas, area_deg2: f.cielo.area_deg2 }));
        capa.cargar(fotos);
        const n = fotos.length;
        $("fotos-cuenta").textContent = n ? ngettext("{n} foto sobre el cielo", "{n} fotos sobre el cielo", n) : _("ninguna foto con posición");
        $("resumen").textContent = _("{n} de {total} fotos tienen posición en el cielo", { n, total: (datos.fotos || []).length });
        estadoEl.hidden = true;

        // ── Paisaje ──
        try { if (localStorage.getItem("publico.paisaje") === "no") $("paisaje").checked = false; } catch (e) {}
        function ponerPaisaje() {
            const si = $("paisaje").checked && !!o;
            viewer.setLandscape(si ? { url: P.comun + "/atlas/assets/landscapes/guereins", key: "guereins" } : null).catch(e => console.warn("paisaje", e));
        }
        $("paisaje").onchange = e => { try { localStorage.setItem("publico.paisaje", e.target.checked ? "si" : "no"); } catch (err) {} ponerPaisaje(); };
        $("paisaje").parentElement.hidden = !o;
        ponerPaisaje();

        // ── Dibujo: ~12 veces por segundo, con la vista alineada al horizonte como en la app ──
        let ultimo = 0;
        function pintar(t) {
            requestAnimationFrame(pintar);
            if (t - ultimo < 80) return;
            ultimo = t;
            const w = $("atlas").clientWidth, h = $("atlas").clientHeight;
            if (!w || !h || !capa.fotos().length) return;
            let vista;
            try { vista = atlas.alignViewToHorizon(viewer.getView(), observador, viewer.getTime()); } catch (e) { return; }
            capa.pintar(vista, w, h);
        }
        requestAnimationFrame(pintar);

        // ── Tocar una foto (el lienzo no recibe eventos: el mapa se arrastra debajo) ──
        let toque = null;
        $("atlas").addEventListener("pointerdown", e => { toque = { x: e.clientX, y: e.clientY }; });
        $("atlas").addEventListener("pointerup", e => {
            if (!toque || Math.hypot(e.clientX - toque.x, e.clientY - toque.y) > 6) { toque = null; return; }
            toque = null;
            const r = lienzo.getBoundingClientRect();
            const f = capa.enPunto(e.clientX - r.left, e.clientY - r.top);
            if (f) mostrarFoto(f); else $("panel-foto").hidden = true;
        });
        $("pf-cerrar").onclick = () => { $("panel-foto").hidden = true; };
        function mostrarFoto(f) {
            $("pf-nombre").textContent = f.objeto + (f.tipo ? " · " + _(f.tipo) : "");
            const partes = [];
            if (f.fecha_iso) partes.push(fmtFecha(new Date(f.fecha_iso)));
            if (f.n_fotos && f.exposicion) partes.push(_("{min} min", { min: fmtNum(f.n_fotos * f.exposicion / 60, 0) }));
            if (f.area_deg2) partes.push(_("{a} grados cuadrados", { a: fmtNum(f.area_deg2, 1) }));
            $("pf-datos").textContent = partes.join(" · ");
            $("pf-ver").href = "index.html#" + encodeURIComponent(f.id);
            $("panel-foto").hidden = false;
        }

        // ── #<id>: centrar el mapa en esa foto ──
        const id = decodeURIComponent(location.hash.slice(1));
        const elegida = id && fotos.find(f => f.id === id);
        if (elegida) centrarEn(elegida);
        function centrarEn(f) {
            // Centro: media de las esquinas (RA con cuidado al cruzar 0h); campo: tres veces el lado de la foto
            const ra0 = f.esquinas[0][0];
            let ra = 0, dec = 0;
            for (const [a, d] of f.esquinas) { ra += ((a - ra0 + 540) % 360) - 180; dec += d; }
            ra = ((ra0 + ra / f.esquinas.length) % 360 + 360) % 360; dec /= f.esquinas.length;
            const lado = Math.sqrt(f.area_deg2 || 1);
            try { viewer.setView({ center: { raDeg: ra, decDeg: dec, frame: "ICRS" }, fovDeg: Math.min(90, Math.max(5, lado * 3)) }); } catch (e) {}
            mostrarFoto(f);
        }

        // ── Visibilidad y tamaño ──
        function sincronizarVisibilidad() {
            const caja = $("atlas");
            const visible = !document.hidden && caja.clientWidth > 0 && caja.clientHeight > 0;
            if (visible) { viewer.resume(); viewer.resize(); } else viewer.pause();
        }
        new ResizeObserver(sincronizarVisibilidad).observe($("atlas"));
        document.addEventListener("visibilitychange", sincronizarVisibilidad);
        window.addEventListener("resize", sincronizarVisibilidad);
        sincronizarVisibilidad();
        window.planetarioPublico = { viewer, capa, fotos };
    } catch (e) {
        console.error("planetario público", e);
        estadoEl.hidden = false;
        estadoEl.textContent = "❌ " + _("El navegador no puede dibujar el planetario: {error}", { error: e && e.message || e });
    }

    function aplicarTema() {
        const noche = document.documentElement.getAttribute("data-tema") !== "oscuro";
        viewer?.setDisplayOptions({ nightMode: noche });
    }
    new MutationObserver(aplicarTema).observe(document.documentElement, { attributes: true, attributeFilter: ["data-tema"] });
})();
