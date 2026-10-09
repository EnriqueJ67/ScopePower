// static/js/clic.js — confirmación al pulsar un enlace que cambia de pantalla.
//
// De noche, con la pantalla táctil y la wifi lenta del SynScan, la página nueva tarda en
// llegar y no se sabe si el toque entró, si la pantalla no respondió o si solo va lenta.
// Al pulsar cualquier enlace que lleve a otra página (barra lateral o inferior, cabecera,
// enlaces dentro de las páginas) suena el mismo click.wav que los botones de Inicio, vibra
// un poco y el enlace queda marcado (clase "yendo") hasta que llega la página nueva.
// Los botones de Inicio (.clic-audio) ya suenan por su cuenta y no se tocan.
(function () {
    const sonido = new Audio("/static/click.wav");
    sonido.preload = "auto";

    function cambiaDePagina(a, ev) {
        if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
        if (a.classList.contains("clic-audio")) return false;
        if ((a.target && a.target !== "_self") || a.hasAttribute("download")) return false;
        const href = a.getAttribute("href") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:")) return false;
        let url;
        try { url = new URL(a.href, location.href); } catch (e) { return false; }
        if (url.origin !== location.origin) return false;
        // Solo cambia el ancla de esta misma página: no hay nada que esperar
        if (url.pathname === location.pathname && url.search === location.search && url.hash) return false;
        return true;
    }

    document.addEventListener("click", ev => {
        const a = ev.target.closest && ev.target.closest("a[href]");
        if (!a || !cambiaDePagina(a, ev)) return;
        ev.preventDefault();
        try { sonido.currentTime = 0; sonido.play().catch(() => {}); } catch (e) { /* sin sonido, se navega igual */ }
        try { if (navigator.vibrate) navigator.vibrate(15); } catch (e) { /* idem */ }
        a.classList.add("yendo");
        // Un instante para que el clic se oiga antes de que empiece a cargar la otra página
        setTimeout(() => { location.href = a.href; }, 90);
    });

    // Al volver con Atrás (página guardada por el navegador), quitar la marca
    window.addEventListener("pageshow", () => document.querySelectorAll("a.yendo").forEach(a => a.classList.remove("yendo")));
})();
