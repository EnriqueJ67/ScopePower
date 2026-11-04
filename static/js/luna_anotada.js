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
// grande caben más). Los destacados y los alunizajes (con `rango`, del servidor) tienen su
// marca reservada desde el principio: el nombre de otro accidente, aunque se coloque antes,
// busca otro sitio.
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
    const HOLGURA = 2;
    const ocupados = [];
    const solapan = (p, q) => !(p[2] < q[0] || p[0] > q[2] || p[3] < q[1] || p[1] > q[3]);
    const choca = rc => ocupados.some(o => solapan(rc, o));
    const dentro = rc => rc[0] >= 0 && rc[2] <= w && rc[1] >= 0 && rc[3] <= h;
    let puestos = 0;
    // Tamaño mínimo en pantalla (diámetro en px) por familia: con la foto pequeña solo los grandes.
    // Un cráter se mide por lo que se ve de él (aplastado contra el limbo es una raya); los
    // destacados de segunda valen más pequeños; los de primera y los alunizajes, de cualquier tamaño
    const MINIMO = { mar: 26, crater: 10, monte: 24, rima: 24, dorso: 24, estacion: 0 };
    const MINIMO_DESTACADO = { 1: 0, 2: 4 };
    const seVe = (o, a, b) => {
        if (o.rango) return 2 * a >= (MINIMO_DESTACADO[o.rango] ?? MINIMO.crater);
        return (o.familia === "crater" ? 2 * Math.sqrt(a * b) : 2 * a) >= (MINIMO[o.familia] ?? MINIMO.crater);
    };
    // La marca de un cráter pequeño o de un punto: si ya hay un nombre encima, el accidente se
    // queda fuera (un contorno cruzando un nombre lo hace ilegible)
    const marcaDe = (o, x, y, a, hx, hy) => {
        if (o.familia === "crater") return a <= 40 ? [x - hx - 1, y - hy - 1, x + hx + 1, y + hy + 1] : null;
        return o.familia !== "mar" ? [x - 4, y - 4, x + 4, y + 4] : null;
    };
    // Medio ancho y medio alto de la elipse girada: la caja de un cráter aplastado es estrecha
    const semicaja = (a, b, angulo) => {
        const c = Math.cos(angulo * Math.PI / 180), s = Math.sin(angulo * Math.PI / 180);
        return [Math.hypot(a * c, b * s), Math.hypot(a * s, b * c)];
    };
    // Los contornos de los destacados, reservados: ningún nombre se pone encima. No chocan entre
    // sí (Theophilus y Cyrillus se tocan), solo con los nombres. Los de segunda, solo con el
    // disco a buen tamaño: en el móvil no cabe todo y antes que un cráter diminuto va un mar
    const reservarSegunda = d.disco.r * k >= 150;
    const reservas = [], nombres = [];
    for (const o of d.objetos) {
        const a = o.a * k, b = o.b * k;
        if (!o.rango || !seVe(o, a, b) || (o.rango !== 1 && !reservarSegunda)) continue;
        const m = marcaDe(o, o.x * k, o.y * k, a, ...semicaja(a, b, o.angulo));
        if (m) reservas.push(m);
    }
    const reservado = rc => reservas.some(m => solapan(rc, m));
    const libre = rc => !choca(rc) && !reservado(rc);
    for (const o of d.objetos) {
        const x = o.x * k, y = o.y * k, a = o.a * k, b = o.b * k;
        if (!seVe(o, a, b)) continue;
        const clase = "luna-" + o.familia + (o.luz ? "" : " luna-noche");
        const [hx, hy] = semicaja(a, b, o.angulo);
        const marca = marcaDe(o, x, y, a, hx, hy);
        // Un destacado solo cede ante un nombre; los demás, también ante otro contorno
        if (marca && (o.rango ? nombres.some(n => solapan(marca, n)) : !libre(marca))) continue;
        const texto = nuevo("text", {}, clase);
        texto.textContent = o.nombre;
        // Medida real del texto (con el espaciado entre letras y la fuente de su familia)
        const caja0 = texto.getBBox();
        const tw = caja0.width, th = caja0.height;
        let sitios;
        if (o.familia === "mar") {
            // Sobre su centro y, si está ocupado, cerca de él pero dentro del mar: el mar se ve
            // aunque el nombre no caiga justo en medio, y así deja libres sus cráteres
            const desvios = [];
            for (const dy of [0, 0.5, -0.5, 1, -1, 1.5, -1.5, 2, -2])
                for (const dx of [0, 0.25, -0.25, 0.5, -0.5, 1, -1]) {
                    const lejos = Math.hypot(dx * tw, dy * (th + 2));
                    if (lejos <= a) desvios.push([lejos, x - tw / 2 + dx * tw, y - th / 2 + dy * (th + 2)]);
                }
            sitios = desvios.sort((p, q) => p[0] - q[0]).map(p => [p[1], p[2]]);
        } else if (o.familia === "crater") {
            // Fuera de su propia marca, con holgura: debajo, encima, a los lados y en las esquinas
            const abajo = y + Math.max(hy, 3) + HOLGURA + 2, arriba = y - Math.max(hy, 3) - HOLGURA - 2 - th;
            const dcha = x + Math.max(hx, 3) + HOLGURA + 3, izda = x - Math.max(hx, 3) - HOLGURA - 3 - tw;
            sitios = [[x - tw / 2, abajo], [x - tw / 2, arriba], [dcha, y - th / 2], [izda, y - th / 2],
                      [dcha, abajo], [izda, abajo], [dcha, arriba], [izda, arriba],
                      [x, abajo], [x - tw, abajo], [x, arriba], [x - tw, arriba]];
        } else {
            // Un punto: el nombre tiene que quedar fuera de su marca (4 px) también con la holgura
            const s = 5 + HOLGURA;
            sitios = [[x + s, y - th / 2], [x - tw - s, y - th / 2], [x - tw / 2, y + s], [x - tw / 2, y - th - s],
                      [x + s, y + 2], [x - tw - s, y + 2], [x + s, y - th - 2], [x - tw - s, y - th - 2],
                      [x, y + s], [x - tw, y + s], [x, y - th - s], [x - tw, y - th - s]];
        }
        // (tx, ty) es la esquina de arriba a la izquierda de la caja; la línea base va más abajo
        const base = -caja0.y;
        let puesto = false;
        for (const [tx, ty] of sitios) {
            const rc = [tx - HOLGURA, ty - HOLGURA, tx + tw + HOLGURA, ty + th + HOLGURA];
            if (dentro(rc) && libre(rc) && !(marca && solapan(rc, marca))) {
                texto.setAttribute("x", tx); texto.setAttribute("y", ty + base);
                ocupados.push(rc); nombres.push(rc); puesto = true;
                break;
            }
        }
        if (!puesto) { texto.remove(); continue; }
        puestos++;
        if (marca) (o.rango ? reservas : ocupados).push(marca);
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
