// static/js/luna_anotada.js
//
// La capa de la Luna anotada (luna_anotada.py) sobre una foto: la usan el visor de la galería
// y la foto de Cámara. El servidor da todo en píxeles del JPG; aquí se pinta a tamaño de
// pantalla (k = píxeles de pantalla por píxel del JPG), así los nombres se leen igual en el
// móvil que en el PC y caben más cuanto más grande se ve la foto.
//
//   pintarLunaAnotada(svg, d, k, w, h) -> nombres puestos; svg ya vacío y de w×h px
//   textoLunaAnotada(d, puestos)       -> «46 accidentes con nombre · 89 % iluminada»

// La Luna: el limbo, el terminador (discontinuo), los límites de los mares y los
// accidentes con nombre. Los mares llevan el nombre encima de su centro; los cráteres, su
// contorno (elipse, aplastada cerca del borde) y el nombre al lado; montes, rimas y
// dorsos, un punto. Los de la parte de noche, más tenues. Ningún nombre pisa otro ni el
// contorno de un cráter pequeño: el que no cabe se queda sin poner (al ver la foto más
// grande caben más).
function pintarLunaAnotada(svg, d, k, w, h) {
    const NS = "http://www.w3.org/2000/svg";
    const nuevo = (tipo, attrs, clase, antes) => {
        const el = document.createElementNS(NS, tipo);
        for (const [a, v] of Object.entries(attrs)) el.setAttribute(a, v);
        if (clase) el.setAttribute("class", clase);
        svg.insertBefore(el, antes || null);
        return el;
    };
    if (d.mares) nuevo("path", { d: d.mares, transform: `scale(${k})` }, "luna-contorno");
    nuevo("circle", { cx: d.disco.cx * k, cy: d.disco.cy * k, r: d.disco.r * k }, "luna-limbo");
    if (d.terminador.length > 1)
        nuevo("polyline", { points: d.terminador.map(p => `${p[0] * k},${p[1] * k}`).join(" ") }, "luna-terminador");
    const HOLGURA = 3;
    const ocupados = [];
    const choca = rc => ocupados.some(o => !(rc[2] < o[0] || rc[0] > o[2] || rc[3] < o[1] || rc[1] > o[3]));
    const dentro = rc => rc[0] >= 0 && rc[2] <= w && rc[1] >= 0 && rc[3] <= h;
    let puestos = 0;
    // Tamaño mínimo en pantalla (diámetro en px) por familia: con la foto pequeña solo los grandes
    const MINIMO = { mar: 26, crater: 12, monte: 24, rima: 24, dorso: 24, estacion: 0 };
    for (const o of d.objetos) {
        const x = o.x * k, y = o.y * k, a = o.a * k, b = o.b * k;
        if (2 * a < (MINIMO[o.familia] ?? 12)) continue;
        const clase = "luna-" + o.familia + (o.luz ? "" : " luna-noche");
        // La marca de un cráter pequeño o de un punto también ocupa sitio: si ya hay un nombre
        // encima, este accidente se queda fuera (un contorno cruzando un nombre lo hace ilegible)
        let marca = null;
        if (o.familia === "crater" && a <= 40) marca = [x - a - 1, y - a - 1, x + a + 1, y + a + 1];
        else if (o.familia !== "mar" && o.familia !== "crater") marca = [x - 4, y - 4, x + 4, y + 4];
        if (marca && choca(marca)) continue;
        const texto = nuevo("text", {}, clase);
        texto.textContent = o.nombre;
        // Medida real del texto (con el espaciado entre letras y la fuente de su familia)
        const caja0 = texto.getBBox();
        const tw = caja0.width, th = caja0.height;
        const r = Math.max(a, 3) + HOLGURA + 2;           // fuera de su propia marca, con holgura
        let sitios;
        if (o.familia === "mar") sitios = [[x - tw / 2, y - th / 2], [x - tw / 2, y + th / 2 + 2], [x - tw / 2, y - 1.5 * th - 2]];
        else if (o.familia === "crater")
            sitios = [[x - tw / 2, y + r], [x - tw / 2, y - r - th], [x + r + 1, y - th / 2], [x - r - tw - 1, y - th / 2]];
        else sitios = [[x + 6, y - th / 2], [x - tw - 6, y - th / 2], [x - tw / 2, y + 5], [x - tw / 2, y - th - 5]];
        // (tx, ty) es la esquina de arriba a la izquierda de la caja; la línea base va más abajo
        const base = -caja0.y;
        let puesto = false;
        for (const [tx, ty] of sitios) {
            const rc = [tx - HOLGURA, ty - HOLGURA, tx + tw + HOLGURA, ty + th + HOLGURA];
            if (dentro(rc) && !choca(rc) && !(marca && !(rc[2] < marca[0] || rc[0] > marca[2] || rc[3] < marca[1] || rc[1] > marca[3]))) {
                texto.setAttribute("x", tx); texto.setAttribute("y", ty + base);
                ocupados.push(rc); puesto = true;
                break;
            }
        }
        if (!puesto) { texto.remove(); continue; }
        puestos++;
        if (marca) ocupados.push(marca);
        if (o.familia === "crater")
            nuevo("ellipse", { cx: x, cy: y, rx: a, ry: Math.max(b, 1), transform: `rotate(${o.angulo} ${x} ${y})` }, clase, texto);
        else if (o.familia !== "mar")
            nuevo("circle", { cx: x, cy: y, r: 2.5 }, clase + " luna-punto", texto);
    }
    return puestos;
}

// Resumen para el pie de la foto: cuántos nombres caben, la fase y si el espejo es dudoso
function textoLunaAnotada(d, puestos) {
    let texto = ngettext("{n} accidente con nombre", "{n} accidentes con nombre", puestos) + " · "
        + _("{p} % iluminada", { p: Math.round(d.iluminada * 100) });
    if (d.dudosa) texto += " · " + _("orientación dudosa: puede salir en espejo");
    return texto;
}
