// static/js/ayudas.js
//
// Explicaciones plegadas tras un botón ⓘ.
//
// Casi cada control lleva debajo su explicación (<small class="ayuda">): viene bien la primera
// vez, pero en la tablet, de noche, son renglones y renglones que alargan el panel y obligan a
// hacer scroll. Dentro de un contenedor con data-ayudas, cada explicación queda plegada y junto
// al nombre de su control sale un botón ⓘ que la despliega y la vuelve a plegar.
//
// La plantilla no cambia: el botón se cuelga solo de la etiqueta del control (.rango-titulo, label
// o la casilla o el renglón que la explicación tiene justo encima). Si no hay etiqueta, la explicación es del
// panel entero y el botón va junto a su título. Se quedan siempre a la vista:
//   - las que llevan id: su texto lo escribe el JS de la pantalla (lo que está pasando ahora);
//   - las que llevan data-fija: avisos que no deben esconderse.
// data-ayuda-panel manda el botón al título del panel aunque haya una etiqueta cerca.
// Sin JavaScript no se pliega nada: se ven todas, como antes.

(function () {
    "use strict";
    const zonas = document.querySelectorAll("[data-ayudas]");
    if (!zonas.length) return;
    let serie = 0;

    // La etiqueta del control al que explica: la casilla de justo encima o, subiendo por sus
    // hermanos, el primer título de deslizador o label. Otra explicación por el camino corta la
    // búsqueda: ese título ya tiene la suya.
    function etiquetaDe(ayuda) {
        const justo = ayuda.previousElementSibling;
        if (justo && justo.matches("label.fila-check, label.switch")) return justo;
        // Un renglón con el nombre y su casilla o su número (no uno de botones) también es etiqueta
        if (justo && justo.classList.contains("fila") && !justo.querySelector("button, .btn")) return justo;
        for (let e = justo; e; e = e.previousElementSibling) {
            if (e.classList.contains("ayuda")) return null;
            if (e.classList.contains("rango-titulo")) return e.firstElementChild || e;
            if (e.tagName === "LABEL" && !e.matches(".fila-check, .switch")) return e;
        }
        return null;
    }

    function tituloDelPanel(ayuda) {
        const panel = ayuda.closest(".panel");
        return panel ? panel.querySelector(".panel-header .card-title") : null;
    }

    function boton(ayuda) {
        if (!ayuda.id) ayuda.id = "ayuda-" + (++serie);
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ayuda-btn";
        b.setAttribute("aria-expanded", "false");
        b.setAttribute("aria-controls", ayuda.id);
        b.setAttribute("aria-label", _("Explicación"));
        b.title = _("Explicación");
        b.innerHTML = '<svg aria-hidden="true"><use href="#i-info"/></svg>';
        b.addEventListener("click", ev => {
            // Dentro de un label, el toque marcaría la casilla o abriría el desplegable
            ev.preventDefault();
            ev.stopPropagation();
            const abierta = ayuda.classList.toggle("plegada") === false;
            b.setAttribute("aria-expanded", abierta ? "true" : "false");
        });
        return b;
    }

    zonas.forEach(zona => {
        zona.querySelectorAll(".ayuda").forEach(ayuda => {
            if (ayuda.hasAttribute("data-fija") || (ayuda.id && !ayuda.hasAttribute("data-ayuda-panel"))) return;
            let ancla = ayuda.hasAttribute("data-ayuda-panel") ? null : etiquetaDe(ayuda);
            if (!ancla || ancla.querySelector(".ayuda-btn")) {
                const titulo = tituloDelPanel(ayuda);
                ancla = titulo && !titulo.querySelector(".ayuda-btn") ? titulo : null;
            }
            const b = boton(ayuda);
            if (ancla) ancla.appendChild(b);
            else {
                // Ni etiqueta ni título libres: el botón va en su propio renglón, encima de la explicación
                const fila = document.createElement("div");
                fila.className = "ayuda-suelta";
                fila.appendChild(b);
                ayuda.parentNode.insertBefore(fila, ayuda);
            }
            ayuda.classList.add("plegada");
        });
    });
})();
