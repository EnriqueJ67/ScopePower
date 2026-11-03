// static/publico/galeria_publica.js — la galería pública en el navegador del visitante.
//
// Lee galeria.json (lo escribe publicar.py en el hosting), pinta los chips por tipo de objeto,
// el orden (fecha, objeto, nota), las gráficas del Sol y la rejilla con las miniaturas; al tocar una foto se abre a
// 1600 px con sus datos y, si las tiene, sus anotaciones. Sin servidor: todo es estático.
// #<id> en la dirección abre esa foto. Los textos van por _() (static/js/i18n.js); los tipos vienen en español y se traducen al pintar.
(function () {
    "use strict";
    const P = window.PUBLICO || { raiz: ".", comun: "..", idioma: "es", url: "" };
    const $ = id => document.getElementById(id);
    // El orden de los chips es el del catálogo de la app (catalogo_offline.CATEGORIAS)
    const TIPOS = [N_("Galaxia"), N_("Nebulosa"), N_("Nebulosa planetaria"), N_("Resto de supernova"), N_("Cúmulo abierto"),
        N_("Cúmulo globular"), N_("Estrella"), N_("Planeta"), N_("Luna"), N_("Sol"), N_("Cometa"), N_("Asteroide"), N_("Meteoro"),
        N_("Satélite artificial"), N_("Avión"), N_("Paisaje")];
    const SIN_TIPO = N_("Sin tipo");

    let fotos = [], sol = null, orden = "fecha", filtro = "", abierta = -1, visibles = [];

    // ── Tema ──
    $("tema").onclick = () => {
        const raiz = document.documentElement, oscuro = raiz.getAttribute("data-tema") !== "oscuro";
        if (oscuro) raiz.setAttribute("data-tema", "oscuro"); else raiz.removeAttribute("data-tema");
        try { localStorage.setItem("publico.tema", oscuro ? "oscuro" : "noche"); } catch (e) {}
    };
    // Al cambiar de idioma se conserva la foto abierta (#id)
    document.querySelectorAll("[data-idioma]").forEach(a => { a.onclick = () => { a.href = a.getAttribute("href") + location.hash; }; });

    const url = f => P.raiz + "/" + encodeURIComponent(f.ficheros.jpg) + "?v=" + (f.v && f.v.jpg || "");
    const urlMini = f => P.raiz + "/" + encodeURIComponent(f.ficheros.mini || f.ficheros.jpg) + "?v=" + (f.v && (f.v.mini || f.v.jpg) || "");
    const fecha = f => f.fecha_iso ? fmtFecha(new Date(f.fecha_iso)) : (f.fecha || "");
    const tipoDe = f => f.tipo || SIN_TIPO;
    const nota = f => f.nota && isFinite(f.nota.puntuacion) ? Number(f.nota.puntuacion) : null;
    const minutos = f => f.n_fotos && f.exposicion ? f.n_fotos * f.exposicion / 60 : 0;

    function pintarChips() {
        const conteo = {};
        fotos.forEach(f => { conteo[tipoDe(f)] = (conteo[tipoDe(f)] || 0) + 1; });
        const tipos = Object.keys(conteo).sort((a, b) => (TIPOS.indexOf(a) + 1 || 99) - (TIPOS.indexOf(b) + 1 || 99));
        const cont = $("filtros");
        cont.replaceChildren();
        if (tipos.length < 2) return;
        const chip = (tipo, texto, n) => {
            const b = document.createElement("button");
            b.type = "button"; b.className = "chip" + (tipo === filtro ? " activo" : ""); b.dataset.tipo = tipo;
            b.innerHTML = texto + ' <span class="n">' + n + "</span>";
            b.onclick = () => { filtro = tipo; pintar(); };
            cont.appendChild(b);
        };
        chip("", _("Todas"), fotos.length);
        tipos.forEach(t => chip(t, _(t), conteo[t]));
    }

    function ordenar(lista) {
        const porFecha = (a, b) => (b.fecha_iso || "").localeCompare(a.fecha_iso || "");
        const porNota = (a, b) => (nota(b) ?? -Infinity) - (nota(a) ?? -Infinity);
        if (orden === "nota") return lista.sort((a, b) => porNota(a, b) || porFecha(a, b));
        if (orden === "objeto") return lista.sort((a, b) => a.objeto.localeCompare(b.objeto, IDIOMA.locale) || porNota(a, b) || porFecha(a, b));
        return lista.sort(porFecha);
    }

    // Con el filtro Sol: el Número de Wolf y el diámetro frente a la efeméride, como en la galería de la app
    function pintarSol() {
        const wolf = filtro === "Sol" && sol && sol.wolf.length > 0, diametro = filtro === "Sol" && sol && sol.diametro.length > 0;
        $("wolf-serie").hidden = !wolf;
        $("sol-diametro").hidden = !diametro;
        if (wolf) {
            pintarSerieWolf($("wolf-serie-svg"), sol.wolf);
            const u = sol.wolf[sol.wolf.length - 1];
            $("wolf-serie-texto").textContent = ngettext("{n} día con conteo", "{n} días con conteo", sol.wolf.length) + " · "
                + _("último: R = {R} el {fecha} ({g} grupos, {f} manchas)", { R: fmtNum(u.R, 0), fecha: fmtFecha(new Date(u.fecha), false), g: u.g, f: u.f })
                + (u.k !== 1 ? " · " + _("k = {k}", { k: fmtNum(u.k, 2) }) : "");
        }
        if (diametro) {
            pintarSerieDiametro($("sol-diametro-svg"), sol.diametro, sol.curva);
            const fiables = sol.diametro.filter(s => s.fiable), usar = fiables.length ? fiables : sol.diametro;
            const media = usar.reduce((a, s) => a + s.desvio_pct, 0) / usar.length, o = sol.orbita;
            $("sol-diametro-texto").textContent = ngettext("{n} foto medida", "{n} fotos medidas", sol.diametro.length) + " · "
                + _("el radio difiere de la efeméride un {m} % de media", { m: (media > 0 ? "+" : "") + fmtNum(media, 2) });
            $("sol-orbita-texto").textContent = o
                ? _("Excentricidad de la órbita medida: {e} (la real, 0,0167) · perihelio hacia el {fecha} (el real, hacia el 3 de enero), con {n} fotos de {dias} días.",
                    { e: fmtNum(o.e, 4), fecha: new Date(o.perihelio + "T12:00").toLocaleDateString(IDIOMA.lang, { day: "numeric", month: "long" }), n: o.n, dias: o.dias })
                : _("Con fotos fiables de al menos cuatro meses se calculan la excentricidad de la órbita de la Tierra y la fecha del perihelio.");
        }
    }

    function pintar() {
        pintarSol();
        document.querySelectorAll("#filtros .chip").forEach(c => c.classList.toggle("activo", c.dataset.tipo === filtro));
        document.querySelectorAll("#orden button").forEach(b => b.classList.toggle("activo", b.dataset.orden === orden));
        visibles = ordenar(fotos.filter(f => !filtro || tipoDe(f) === filtro));
        const g = $("galeria");
        g.replaceChildren();
        visibles.forEach((f, i) => {
            const fig = document.createElement("figure");
            fig.className = "foto"; fig.dataset.id = f.id;
            const n = nota(f);
            fig.innerHTML = '<img loading="lazy" alt="">' +
                '<figcaption class="foto-pie"><div class="foto-nombre"></div><div class="foto-tipo"></div>' +
                (n != null ? '<div class="foto-nota">★ <b>' + fmtNum(n, 2) + "</b>" + (f.nota.estrellas ? " · " + ngettext("{n} estrella", "{n} estrellas", f.nota.estrellas) : "") + "</div>" : "") +
                '<div class="foto-fecha"></div></figcaption>';
            fig.querySelector("img").src = urlMini(f);
            fig.querySelector("img").alt = f.objeto;
            fig.querySelector(".foto-nombre").textContent = f.objeto;
            fig.querySelector(".foto-tipo").textContent = _(tipoDe(f));
            fig.querySelector(".foto-fecha").textContent = fecha(f);
            fig.onclick = () => abrir(i);
            g.appendChild(fig);
        });
        $("vacia").hidden = visibles.length > 0 || fotos.length === 0;
        $("resumen").textContent = filtro
            ? _("{n} de {total} fotos · {tipo}", { n: visibles.length, total: fotos.length, tipo: _(filtro) })
            : ngettext("{n} foto del cielo", "{n} fotos del cielo", fotos.length);
    }

    // ── Visor ──
    function datos(f) {
        const partes = [];
        if (f.fecha_iso || f.fecha) partes.push(fecha(f));
        if (minutos(f)) partes.push(_("{min} min · {n} × {s} s", { min: fmtNum(minutos(f), minutos(f) < 10 ? 1 : 0), n: f.n_fotos, s: fmtNum(f.exposicion, 0) }));
        if (f.video && f.video.frames) partes.push(_("apilado de un vídeo de {n} fotogramas", { n: f.video.frames }) + (f.video.duracion_s ? " · " + fmtNum(f.video.duracion_s, 0) + " s" : ""));
        if (f.ra && f.dec) partes.push("RA " + f.ra + " · Dec " + f.dec);
        if (nota(f) != null) partes.push(_("nota {n}", { n: fmtNum(nota(f), 2) }));
        if (f.wolf && f.wolf.R != null) partes.push(_("Número de Wolf {r}", { r: fmtNum(f.wolf.R, f.wolf.k === 1 ? 0 : 1) }));
        if (f.autor) partes.push(f.autor);
        return partes;
    }
    function abrir(i) {
        if (i < 0 || i >= visibles.length) return;
        abierta = i;
        const f = visibles[i];
        const img = $("lb-imagen");
        img.src = url(f); img.alt = f.objeto;
        const d = $("lb-datos");
        d.replaceChildren();
        const b = document.createElement("b");
        b.textContent = f.objeto + " · " + _(tipoDe(f));
        d.appendChild(b);
        datos(f).forEach(t => { const s = document.createElement("span"); s.textContent = t; d.appendChild(s); });
        $("lb-descargar").href = url(f);
        $("lb-descargar").download = f.ficheros.jpg;
        const cielo = $("lb-cielo");
        cielo.hidden = !(f.cielo && $("planetario") && !$("planetario").hidden);
        cielo.href = $("planetario").getAttribute("href") + "#" + encodeURIComponent(f.id);
        $("lb-prev").hidden = i === 0; $("lb-next").hidden = i === visibles.length - 1;
        $("lb-anotar").hidden = !f.ficheros.anotaciones;
        $("lightbox").classList.add("abierto");
        $("lightbox").setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
        history.replaceState(null, "", "#" + encodeURIComponent(f.id));
        actualizarAnotaciones();
    }
    function cerrar() {
        abierta = -1;
        $("lightbox").classList.remove("abierto");
        $("lightbox").setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
        history.replaceState(null, "", location.pathname + location.search);
    }
    $("lb-cerrar").onclick = cerrar;
    $("lightbox").onclick = e => { if (e.target === $("lightbox") || e.target === $("lb-imagen")) cerrar(); };
    $("lb-prev").onclick = e => { e.stopPropagation(); abrir(abierta - 1); };
    $("lb-next").onclick = e => { e.stopPropagation(); abrir(abierta + 1); };
    document.addEventListener("keydown", e => {
        if (abierta < 0) return;
        if (e.key === "Escape") cerrar();
        if (e.key === "ArrowLeft") abrir(abierta - 1);
        if (e.key === "ArrowRight") abrir(abierta + 1);
    });
    // Deslizar en el móvil para pasar de foto
    let x0 = null;
    $("lightbox").addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, { passive: true });
    $("lightbox").addEventListener("touchend", e => {
        if (x0 == null) return;
        const dx = e.changedTouches[0].clientX - x0; x0 = null;
        if (Math.abs(dx) > 60) abrir(abierta + (dx < 0 ? 1 : -1));
    }, { passive: true });
    $("lb-compartir").onclick = async () => {
        const f = visibles[abierta];
        const enlace = (P.url || location.href.split("#")[0]) + "#" + encodeURIComponent(f.id);
        if (navigator.share) {
            try { await navigator.share({ title: f.objeto + " · ScopePower", url: enlace }); return; } catch (e) { if (e.name === "AbortError") return; }
        }
        try { await navigator.clipboard.writeText(enlace); alert(_("Enlace copiado: {url}", { url: enlace })); }
        catch (e) { prompt(_("Enlace de esta foto"), enlace); }
    };
    document.querySelectorAll("#orden button").forEach(b => { b.onclick = () => { orden = b.dataset.orden; pintar(); }; });

    // ── Anotaciones: los objetos del campo pintados encima de la foto, como en el visor de la app ──
    // <id>_anotaciones.json los da en píxeles del JPG publicado; aquí se dibujan a tamaño de
    // pantalla, así los nombres se leen igual en el móvil. La Luna y el Sol, con sus pintores de la app.
    const anotadas = {};
    const svgAnot = $("lb-anotaciones"), chkAnot = $("lb-anotar-activo");
    try { chkAnot.checked = localStorage.getItem("publico.anotaciones") === "1"; } catch (e) {}
    chkAnot.onchange = () => {
        try { localStorage.setItem("publico.anotaciones", chkAnot.checked ? "1" : "0"); } catch (e) {}
        actualizarAnotaciones();
    };
    function textoAnotaciones(texto) {
        let s = $("lb-datos").querySelector(".lb-anot");
        if (!s && texto) {
            s = document.createElement("span");
            s.className = "lb-anot";
            $("lb-datos").appendChild(s);
        }
        if (s) { s.textContent = texto; s.hidden = !texto; }
    }
    function actualizarAnotaciones() {
        const f = visibles[abierta];
        svgAnot.replaceChildren();
        verGrupos(false);
        textoAnotaciones("");
        if (!f || !f.ficheros.anotaciones || !chkAnot.checked) return;
        if (anotadas[f.id]) { pintarAnotaciones(); return; }
        fetch(P.raiz + "/" + encodeURIComponent(f.ficheros.anotaciones) + "?v=" + (f.v && f.v.anotaciones || "")).then(r => {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        }).then(d => {
            anotadas[f.id] = d;
            if (visibles[abierta] === f) pintarAnotaciones();       // puede haberse cerrado o cambiado de foto mientras tanto
        }).catch(e => {
            console.error(f.ficheros.anotaciones, e);
            if (visibles[abierta] === f) textoAnotaciones(_("No se pudieron cargar las anotaciones."));
        });
    }
    function pintarAnotaciones() {
        svgAnot.replaceChildren();
        const f = visibles[abierta], d = f && anotadas[f.id], img = $("lb-imagen");
        verGrupos(false);
        if (!d || !chkAnot.checked || !img.complete || !img.naturalWidth) return;      // al cargar la foto se vuelve a pintar
        if (d.sol) verGrupos(true);                                  // antes de medir: la foto encoge para dejarle sitio
        // La capa ocupa justo lo que ocupa la foto en pantalla (la imagen se encaja dentro de su caja)
        const caja = img.getBoundingClientRect();
        const s = Math.min(caja.width / img.naturalWidth, caja.height / img.naturalHeight);
        const w = img.naturalWidth * s, h = img.naturalHeight * s;
        svgAnot.style.left = (caja.left + (caja.width - w) / 2) + "px";
        svgAnot.style.top = (caja.top + (caja.height - h) / 2) + "px";
        svgAnot.style.width = w + "px";
        svgAnot.style.height = h + "px";
        svgAnot.setAttribute("viewBox", `0 0 ${w} ${h}`);
        const k = w / d.ancho;                                       // de píxeles del JPG a píxeles de pantalla
        if (d.sol) {
            pintarManchasSolares(svgAnot, d, k, w, h, null);
            textoAnotaciones(textoWolf(d));
            colocarGrupos(d);
        } else if (d.luna) {
            textoAnotaciones(textoLunaAnotada(d, pintarLunaAnotada(svgAnot, d, k, w, h)));
        } else {
            pintarObjetos(d, k, w, h);
            textoAnotaciones(ngettext("{n} objeto en el campo", "{n} objetos en el campo", d.objetos.length));
        }
    }
    function verGrupos(ver) {
        $("lb-grupos").hidden = !ver;
        $("lightbox").classList.toggle("con-grupos", ver);
    }
    // La lista de grupos: a la derecha de la foto si hay hueco; si no, debajo; si tampoco, sobre la parte de abajo de la foto
    function colocarGrupos(d) {
        const lista = $("lb-grupos");
        rellenarListaGrupos(lista, d, resaltarGrupo, true);
        Object.assign(lista.style, { left: "", top: "", bottom: "", width: "", maxHeight: "" });
        const img = svgAnot.getBoundingClientRect(), caja = $("lightbox").getBoundingClientRect();
        const pie = document.querySelector(".lb-pie").getBoundingClientRect();
        const MARGEN = 12, ANCHO = 240, FLECHA = $("lb-next").offsetWidth ? 56 : 0, CERRAR = 64;
        const derecha = caja.right - img.right - 2 * MARGEN - FLECHA;
        if (derecha >= ANCHO) {
            const arriba = Math.max(img.top, CERRAR);
            Object.assign(lista.style, { left: (img.right + MARGEN) + "px", top: arriba + "px", width: Math.min(320, derecha) + "px",
                                         maxHeight: Math.max(120, pie.top - arriba - MARGEN) + "px" });
            return;
        }
        const ancho = Math.min(img.width, caja.width - 16);
        Object.assign(lista.style, { left: Math.max(8, img.left + (img.width - ancho) / 2) + "px", width: ancho + "px" });
        const debajo = pie.top - img.bottom - MARGEN;
        if (debajo >= Math.min(lista.scrollHeight, 110))
            Object.assign(lista.style, { top: (img.bottom + 8) + "px", maxHeight: debajo + "px" });
        else
            Object.assign(lista.style, { bottom: (caja.bottom - pie.top + 6) + "px", maxHeight: "38%" });
    }
    function resaltarGrupo(id) {
        svgAnot.querySelectorAll("[data-grupo]").forEach(el => el.classList.toggle("sol-resaltada", id != null && Number(el.dataset.grupo) === id));
        document.querySelectorAll("#lb-grupos .wolf-fila").forEach(f => f.classList.toggle("activa", id != null && Number(f.dataset.grupo) === id));
    }
    function pintarObjetos(d, k, w, h) {
        // Sitio ocupado: los nombres ya puestos y los círculos pequeños (un nombre encima los taparía)
        const ocupados = [];
        const libre = rc => ocupados.every(o => rc[2] < o[0] || rc[0] > o[2] || rc[3] < o[1] || rc[1] > o[3]);
        for (const o of d.objetos) {
            const x = o.x * k, y = o.y * k, a = Math.max(o.a * k, 7);
            if (a <= 24) ocupados.push([x - a - 1, y - a - 1, x + a + 1, y + a + 1]);
        }
        const NS = "http://www.w3.org/2000/svg";
        for (const o of d.objetos) {
            const x = o.x * k, y = o.y * k, a = Math.max(o.a * k, 7), b = Math.max(o.b * k, 7);
            const clase = o.fuente === "catalogo" ? "anot-catalogo" : o.fuente === "ngc" ? "anot-ngc" : "anot-simbad";
            const elipse = document.createElementNS(NS, "ellipse");
            elipse.setAttribute("cx", x); elipse.setAttribute("cy", y);
            elipse.setAttribute("rx", a); elipse.setAttribute("ry", o.angulo === null ? a : b);
            if (o.angulo !== null) elipse.setAttribute("transform", `rotate(${o.angulo} ${x} ${y})`);
            elipse.setAttribute("class", clase);
            svgAnot.appendChild(elipse);
            const texto = document.createElementNS(NS, "text");
            texto.textContent = o.mag !== null ? `${o.nombre} (${fmtNum(o.mag, 1)})` : o.nombre;
            texto.setAttribute("class", clase);
            svgAnot.appendChild(texto);
            const tw = texto.getComputedTextLength(), th = 13, r = a + 5;
            // Debajo, encima, a la derecha o a la izquierda: el primer sitio libre; sin sitio, se queda el círculo solo
            let puesto = false;
            for (let [tx, ty] of [[x - tw / 2, y + r + th], [x - tw / 2, y - r - 2], [x + r + 2, y + th / 3], [x - r - tw - 2, y + th / 3]]) {
                tx = Math.min(Math.max(3, tx), w - tw - 3);
                ty = Math.min(Math.max(th + 2, ty), h - 4);
                const rc = [tx - 3, ty - th - 1, tx + tw + 3, ty + 4];
                if (libre(rc)) {
                    texto.setAttribute("x", tx); texto.setAttribute("y", ty);
                    ocupados.push(rc); puesto = true;
                    break;
                }
            }
            if (!puesto) texto.remove();
        }
    }
    $("lb-imagen").addEventListener("load", pintarAnotaciones);
    window.addEventListener("resize", pintarAnotaciones);

    // ── Carga ──
    fetch(P.raiz + "/galeria.json").then(r => r.json()).then(d => {
        fotos = (d.fotos || []).filter(f => f && f.ficheros && f.ficheros.jpg);
        sol = d.sol || null;
        if (d.planetario && fotos.some(f => f.cielo)) $("planetario").hidden = false;
        $("orden-nota").hidden = !fotos.some(f => nota(f) != null);
        pintarChips();
        pintar();
        const id = decodeURIComponent(location.hash.slice(1));
        if (id) {
            const i = visibles.findIndex(f => f.id === id);
            if (i >= 0) abrir(i);
        }
    }).catch(e => {
        console.error("galeria.json", e);
        $("resumen").textContent = "";
        $("error").hidden = false;
    });
})();
