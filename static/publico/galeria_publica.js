// static/publico/galeria_publica.js — la galería pública en el navegador del visitante.
//
// Lee galeria.json (lo escribe publicar.py en el hosting), pinta los chips por tipo de objeto,
// el orden (fecha, objeto, nota) y la rejilla con las miniaturas; al tocar una foto se abre a
// 1600 px con sus datos. Sin servidor: todo es estático. #<id> en la dirección abre esa foto.
// Los textos van por _() (static/js/i18n.js); los tipos vienen en español y se traducen al pintar.
(function () {
    "use strict";
    const P = window.PUBLICO || { raiz: ".", comun: "..", idioma: "es", url: "" };
    const $ = id => document.getElementById(id);
    // El orden de los chips es el del catálogo de la app (catalogo_offline.CATEGORIAS)
    const TIPOS = [N_("Galaxia"), N_("Nebulosa"), N_("Nebulosa planetaria"), N_("Resto de supernova"), N_("Cúmulo abierto"),
        N_("Cúmulo globular"), N_("Estrella"), N_("Planeta"), N_("Luna"), N_("Sol"), N_("Cometa"), N_("Asteroide"), N_("Meteoro"),
        N_("Satélite artificial"), N_("Avión"), N_("Paisaje")];
    const SIN_TIPO = N_("Sin tipo");

    let fotos = [], orden = "fecha", filtro = "", abierta = -1, visibles = [];

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

    function pintar() {
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
        if (f.wolf && f.wolf.r != null) partes.push(_("Número de Wolf {r}", { r: fmtNum(f.wolf.r, 0) }));
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
        $("lightbox").classList.add("abierto");
        $("lightbox").setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
        history.replaceState(null, "", "#" + encodeURIComponent(f.id));
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

    // ── Carga ──
    fetch(P.raiz + "/galeria.json").then(r => r.json()).then(d => {
        fotos = (d.fotos || []).filter(f => f && f.ficheros && f.ficheros.jpg);
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
