// static/js/paneles.js
//
// Pestañas de la columna de controles de Cámara, Apilado y el editor de fotos.
//
// Antes cada panel era una tarjeta plegable y reordenable, una debajo de otra: en la tablet, de
// noche, había que hacer scroll para encontrar el panel y tocar otra vez para desplegarlo. Ahora
// hay una fila de botones encima de la columna y se ve un solo panel cada vez: un toque y está.
//
// La plantilla solo marca el contenedor y sus paneles; la fila de pestañas la construye este
// script con lo que lee de ellos:
//
//   <div class="… paneles" data-paneles="foto_paneles">      clave de /preferencias
//       <section class="card panel" data-panel="captura"       identificador del panel
//                data-pestana="Captura" data-icono="i-foto">   texto corto e icono de la pestaña
//           <div class="panel-header"> título, .panel-sub… </div>
//           <div class="panel-cuerpo"> … </div>
//       </section>
//
// Un panel con el atributo hidden no tiene pestaña (Timelapse y Vídeo solo existen de día): se
// vigila el atributo, así que a la plantilla le basta con poner o quitar hidden como siempre.
//
// La pestaña abierta se recuerda en este dispositivo (localStorage) y en config.ini (/preferencias),
// para que un dispositivo nuevo arranque donde se dejó el último.
//
//   Paneles.activar("dia")            abre esa pestaña
//   Paneles.marcar("plan", "activo")  punto de color en la pestaña ("activo", "aviso" o "" para quitarlo)
//   Paneles.preferencia(valor)        lo que guarda /preferencias en esa clave, cuando llega
//   evento "paneles:cambio" en el contenedor, con detail.id

(function () {
    "use strict";
    const cont = document.querySelector("[data-paneles]");
    if (!cont) return;
    const clave = cont.dataset.paneles;
    const CLAVE_LOCAL = "scopepower_pestana_" + clave;
    const paneles = Array.from(cont.children).filter(p => p.classList.contains("panel") && p.dataset.panel);
    if (!paneles.length) return;

    const barra = document.createElement("div");
    barra.className = "pestanas";
    barra.setAttribute("role", "tablist");
    const pestanas = new Map();      // id del panel → botón

    paneles.forEach(panel => {
        // Lo que quedara del sistema de paneles plegables (una rama antigua fusionada después)
        panel.querySelectorAll(".panel-botones").forEach(b => b.remove());
        panel.classList.remove("plegado");
        const cabecera = panel.querySelector(".panel-header");
        if (cabecera) cabecera.removeAttribute("onclick");

        const titulo = panel.querySelector(".card-title");
        const usoIcono = titulo && titulo.querySelector("svg use");
        const icono = panel.dataset.icono || (usoIcono ? (usoIcono.getAttribute("href") || "").replace("#", "") : "");
        const texto = panel.dataset.pestana || (titulo ? titulo.textContent.trim() : panel.dataset.panel);

        const boton = document.createElement("button");
        boton.type = "button";
        boton.className = "pestana";
        boton.dataset.para = panel.dataset.panel;     // no data-panel: así [data-panel=…] solo encuentra el panel
        boton.setAttribute("role", "tab");
        boton.title = titulo ? titulo.textContent.trim() : texto;
        if (icono) {
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            const uso = document.createElementNS("http://www.w3.org/2000/svg", "use");
            uso.setAttribute("href", "#" + icono);
            svg.setAttribute("aria-hidden", "true");
            svg.appendChild(uso);
            boton.appendChild(svg);
        }
        const rotulo = document.createElement("span");
        rotulo.textContent = texto;
        boton.appendChild(rotulo);
        boton.addEventListener("click", () => activar(panel.dataset.panel, { usuario: true }));
        barra.appendChild(boton);
        pestanas.set(panel.dataset.panel, boton);
        panel.setAttribute("role", "tabpanel");
    });
    cont.insertBefore(barra, cont.firstChild);

    let activo = "";        // pestaña que se ve
    let deseado = "";       // la que se pidió: si su panel está oculto, se abre en cuanto aparezca
    let tocado = false;     // el usuario ya ha elegido pestaña en esta visita: /preferencias no la cambia

    const visible = panel => !panel.hidden;
    const panelDe = id => paneles.find(p => p.dataset.panel === id);

    function pintar(id) {
        activo = id;
        paneles.forEach(p => p.classList.toggle("activo", p.dataset.panel === id));
        pestanas.forEach((boton, k) => {
            boton.classList.toggle("activa", k === id);
            boton.setAttribute("aria-selected", k === id ? "true" : "false");
        });
        cont.dispatchEvent(new CustomEvent("paneles:cambio", { detail: { id: id }, bubbles: true }));
    }

    let guardado = null;
    function guardar(id) {
        try { localStorage.setItem(CLAVE_LOCAL, id); } catch (e) {}
        // Con un pequeño retardo: varios toques seguidos hacen una sola escritura en config.ini
        clearTimeout(guardado);
        guardado = setTimeout(() => {
            const datos = {};
            datos[clave] = { activo: id };
            fetch("/preferencias", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos) })
                .catch(err => console.warn("No se pudo guardar la pestaña:", err));
        }, 400);
    }

    function activar(id, opciones) {
        const o = opciones || {};
        const panel = panelDe(id);
        if (!panel) return;
        deseado = id;
        if (o.usuario) tocado = true;
        if (o.usuario || o.guardar) guardar(id);
        if (!visible(panel)) { ajustar(); return; }
        if (id !== activo) pintar(id);
        // En una sola columna la fila se queda pegada arriba al bajar por un panel largo: al cambiar
        // a otro más corto se vuelve al principio para no quedarse mirando lo de debajo
        if (o.usuario) {
            const cabecera = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--header-h")) || 56;
            if (cont.getBoundingClientRect().top < cabecera - 1) cont.scrollIntoView({ block: "start" });
        }
    }

    // Pestañas de los paneles ocultos fuera, y si el que se veía ya no está, el primero que sí
    function ajustar() {
        let n = 0;
        paneles.forEach(p => {
            pestanas.get(p.dataset.panel).hidden = !visible(p);
            if (visible(p)) n++;
        });
        const quiere = panelDe(deseado);
        if (quiere && visible(quiere)) { if (deseado !== activo) pintar(deseado); }
        else {
            const actual = panelDe(activo);
            if (!actual || !visible(actual)) {
                const primero = paneles.find(visible);
                if (primero) pintar(primero.dataset.panel);
            }
        }
        barra.style.setProperty("--pest-n", String(Math.max(1, n)));
        repartir();
    }

    // Una fila si caben (56 px por pestaña como poco) y si no, dos renglones iguales
    function repartir() {
        const n = Array.from(pestanas.values()).filter(b => !b.hidden).length || 1;
        const ancho = barra.clientWidth;
        const columnas = !ancho || ancho / n >= 56 ? n : Math.ceil(n / 2);
        barra.style.setProperty("--pest-cols", String(columnas));
    }

    const vigia = new MutationObserver(ajustar);
    paneles.forEach(p => vigia.observe(p, { attributes: true, attributeFilter: ["hidden"] }));
    // Dos columnas con la cabecera de la página dentro de la rejilla (.con-cabecera, scopepower.css):
    // la cabecera mide como poco lo que la fila de pestañas y la fila baja hasta que su borde inferior
    // coincide con el de la cabecera, así el panel empieza a la misma altura que la foto. En una
    // columna (la cabecera ocupa todo el ancho, encima de la foto) no se toca nada.
    const rejilla = cont.parentElement;
    const cabecera = rejilla && rejilla.classList.contains("con-cabecera") ? rejilla.querySelector(":scope > .pagina-cabecera") : null;
    function alinear() {
        if (!cabecera) return;
        const lado = cabecera.getBoundingClientRect().right <= cont.getBoundingClientRect().left + 1;
        rejilla.style.setProperty("--alto-pestanas", lado ? barra.offsetHeight + "px" : "0px");
        const hueco = lado ? Math.max(0, Math.round(cabecera.offsetHeight - barra.offsetHeight)) + "px" : "";
        if (barra.style.marginTop !== hueco) barra.style.marginTop = hueco;
    }
    function ajustarFila() { repartir(); alinear(); }
    if (typeof ResizeObserver !== "undefined") {
        const vigiaTam = new ResizeObserver(ajustarFila);
        vigiaTam.observe(barra);
        if (cabecera) vigiaTam.observe(cabecera);
    } else window.addEventListener("resize", ajustarFila);

    // Alto de la columna en dos columnas (scopepower.css): lo que queda de pantalla por debajo de donde
    // empieza. Arriba del todo empieza bajo el título de la página y, al bajar, se pega bajo la cabecera:
    // se mide para que el panel quepa entero sin tener que mover la página.
    let midiendo = false;
    function medir() {
        midiendo = false;
        cont.style.setProperty("--paneles-top", Math.max(0, Math.round(cont.getBoundingClientRect().top)) + "px");
    }
    function pedirMedida() { if (!midiendo) { midiendo = true; requestAnimationFrame(medir); } }
    window.addEventListener("scroll", pedirMedida, { passive: true });
    window.addEventListener("resize", pedirMedida);
    window.addEventListener("load", () => { alinear(); medir(); });
    medir();

    let guardada = "";
    try { guardada = localStorage.getItem(CLAVE_LOCAL) || ""; } catch (e) {}
    deseado = panelDe(guardada) ? guardada : (panelDe(cont.dataset.inicial) ? cont.dataset.inicial : paneles[0].dataset.panel);
    cont.classList.add("listo");
    ajustar();
    alinear();

    window.Paneles = {
        activar: (id, opciones) => activar(id, opciones),
        activo: () => activo,
        marcar(id, marca) {
            const boton = pestanas.get(id);
            if (!boton) return;
            if (marca) boton.dataset.marca = marca; else delete boton.dataset.marca;
        },
        // Lo guardado en config.ini: vale si este dispositivo no tiene su propia pestaña recordada
        // y el usuario aún no ha tocado ninguna. El formato antiguo ({orden, plegados}) se ignora.
        preferencia(valor) {
            if (tocado || guardada || !valor || typeof valor.activo !== "string") return;
            if (panelDe(valor.activo)) { deseado = valor.activo; ajustar(); }
        }
    };
    window.desplegarPanel = id => activar(id);
})();
