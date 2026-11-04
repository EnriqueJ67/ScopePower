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
// contorno de un cráter pequeño: el que no cabe prueba con la letra algo menor y, si tampoco,
// se queda sin poner (al ver la foto más grande caben más). Los destacados y los alunizajes (con `rango`, del servidor) tienen su
// marca reservada desde el principio: el nombre de otro accidente, aunque se coloque antes,
// busca otro sitio. Una leyenda pequeña dice qué es cada color.
const LEYENDA_LUNA = [["mar", N_("Mares y lagos")], ["crater", N_("Cráteres")], ["monte", N_("Montes y escarpes")],
                      ["rima", N_("Rimas y valles")], ["dorso", N_("Dorsos")], ["estacion", N_("Alunizajes")]];
// Los nombres de la UAI son latinos y no se traducen; estos, que no son suyos (luna_anotada.py), sí
const NOMBRES_TRADUCIBLES_LUNA = new Set([N_("Apolo 11"), N_("Apolo 12"), N_("Apolo 14"), N_("Apolo 15"), N_("Apolo 16"), N_("Apolo 17")]);

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
    const ESCALAS_LETRA = [1, 0.9, 0.8], LETRA_MINIMA = 9.5;
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
        if (o.familia === "estacion") return true;
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
    // sí (Theophilus y Cyrillus se tocan), solo con los nombres. Los de segunda y los alunizajes,
    // solo con el disco a buen tamaño: en el móvil no cabe todo, y antes que un cráter diminuto
    // va un mar y antes que un alunizaje, Copernicus
    const reservarSegunda = d.disco.r * k >= 150;
    const reservas = [], nombres = [];
    for (const o of d.objetos) {
        const a = o.a * k, b = o.b * k;
        if (!o.rango || !seVe(o, a, b) || ((o.rango !== 1 || o.familia === "estacion") && !reservarSegunda)) continue;
        const m = marcaDe(o, o.x * k, o.y * k, a, ...semicaja(a, b, o.angulo));
        if (m) reservas.push(m);
    }
    const reservado = rc => reservas.some(m => solapan(rc, m));
    const libre = rc => !choca(rc) && !reservado(rc);
    const leyenda = prepararLeyendaLuna(nuevo, d, w, h, o => seVe(o, o.a * k, o.b * k));
    if (leyenda) { ocupados.push(leyenda.caja); nombres.push(leyenda.caja); }
    const salieron = new Set();
    for (const o of d.objetos) {
        const x = o.x * k, y = o.y * k, a = o.a * k, b = o.b * k;
        if (!seVe(o, a, b)) continue;
        const clase = "luna-" + o.familia + (o.luz ? "" : " luna-noche");
        const [hx, hy] = semicaja(a, b, o.angulo);
        const marca = marcaDe(o, x, y, a, hx, hy);
        // Un destacado solo cede ante un nombre; los demás, también ante otro contorno
        if (marca && (o.rango ? nombres.some(n => solapan(marca, n)) : !libre(marca))) continue;
        const texto = nuevo("text", {}, clase);
        texto.textContent = NOMBRES_TRADUCIBLES_LUNA.has(o.nombre) ? _(o.nombre) : o.nombre;
        const sitiosDe = (tw, th) => {
        if (o.familia === "mar") {
            // Sobre su centro y, si está ocupado, cerca de él pero dentro del mar: el mar se ve
            // aunque el nombre no caiga justo en medio, y así deja libres sus cráteres
            const desvios = [];
            for (const dy of [0, 0.5, -0.5, 1, -1, 1.5, -1.5, 2, -2])
                for (const dx of [0, 0.25, -0.25, 0.5, -0.5, 1, -1]) {
                    const lejos = Math.hypot(dx * tw, dy * (th + 2));
                    if (lejos <= a) desvios.push([lejos, x - tw / 2 + dx * tw, y - th / 2 + dy * (th + 2)]);
                }
            return desvios.sort((p, q) => p[0] - q[0]).map(p => [p[1], p[2]]);
        }
        if (o.familia === "crater") {
            // Fuera de su propia marca, con holgura: debajo, encima, a los lados y en las esquinas
            const abajo = y + Math.max(hy, 3) + HOLGURA + 2, arriba = y - Math.max(hy, 3) - HOLGURA - 2 - th;
            const dcha = x + Math.max(hx, 3) + HOLGURA + 3, izda = x - Math.max(hx, 3) - HOLGURA - 3 - tw;
            return [[x - tw / 2, abajo], [x - tw / 2, arriba], [dcha, y - th / 2], [izda, y - th / 2],
                    [dcha, abajo], [izda, abajo], [dcha, arriba], [izda, arriba],
                    [x, abajo], [x - tw, abajo], [x, arriba], [x - tw, arriba]];
        }
        // Un punto: el nombre tiene que quedar fuera de su marca (4 px) también con la holgura
        const s = 5 + HOLGURA;
        return [[x + s, y - th / 2], [x - tw - s, y - th / 2], [x - tw / 2, y + s], [x - tw / 2, y - th - s],
                [x + s, y + 2], [x - tw - s, y + 2], [x + s, y - th - 2], [x - tw - s, y - th - 2],
                [x, y + s], [x - tw, y + s], [x, y - th - s], [x - tw, y - th - s]];
        };
        // Si no cabe a su tamaño, se prueba con la letra algo menor antes de dejarlo fuera
        const letra = parseFloat(getComputedStyle(texto).fontSize);
        let puesto = false;
        for (const escala of ESCALAS_LETRA) {
            if (escala < 1) {
                if (letra * escala < LETRA_MINIMA) break;
                texto.style.fontSize = letra * escala + "px";
            }
            // Medida real del texto (con el espaciado entre letras y la fuente de su familia).
            // (tx, ty) es la esquina de arriba a la izquierda de la caja; la línea base va más abajo
            const caja = texto.getBBox();
            const tw = caja.width, th = caja.height, base = -caja.y;
            for (const [tx, ty] of sitiosDe(tw, th)) {
                const rc = [tx - HOLGURA, ty - HOLGURA, tx + tw + HOLGURA, ty + th + HOLGURA];
                if (dentro(rc) && libre(rc) && !(marca && solapan(rc, marca))) {
                    texto.setAttribute("x", tx); texto.setAttribute("y", ty + base);
                    ocupados.push(rc); nombres.push(rc); puesto = true;
                    break;
                }
            }
            if (puesto) break;
        }
        if (!puesto) { texto.remove(); continue; }
        puestos++;
        salieron.add(o.familia);
        if (!o.luz) salieron.add("noche");
        if (marca) (o.rango ? reservas : ocupados).push(marca);
        if (o.familia === "crater")
            nuevo("ellipse", { cx: x, cy: y, rx: a, ry: Math.max(b, 1), transform: `rotate(${o.angulo} ${x} ${y})` }, clase, texto);
        else if (o.familia !== "mar")
            nuevo("circle", { cx: x, cy: y, r: 2.5 }, clase + " luna-punto", texto);
    }
    if (leyenda) leyenda.pintar(salieron);
    return puestos;
}

// La leyenda se mide y se le busca sitio antes de poner los nombres, para que la esquiven, con
// todas las familias que podrían salir; al final se pinta solo con las que han salido. Va en
// un lateral, fuera del disco si se puede y por encima de la firma de la foto. Devuelve
// { caja, pintar(salieron) }, o null si la foto se ve tan pequeña que taparía demasiado.
function prepararLeyendaLuna(nuevo, d, w, h, seVe) {
    if (w < 240 || h < 200) return null;
    const visibles = d.objetos.filter(seVe);
    const filas = LEYENDA_LUNA.filter(([familia]) => visibles.some(o => o.familia === familia))
        .map(([familia, texto]) => ({ clave: familia, texto: _(texto), clase: "luna-leyenda luna-" + familia }));
    if (visibles.some(o => !o.luz)) filas.push({ clave: "noche", texto: _("Más tenue: de noche"), clase: "luna-leyenda luna-leyenda-nota" });
    if (!filas.length) return null;
    const MARGEN = 6, ALTO = 16;
    for (const f of filas) {
        f.el = nuevo("text", {}, f.clase);
        f.el.textContent = f.texto;
        f.base = -f.el.getBBox().y;
        f.ancho = f.el.getBBox().width;
    }
    const ancho = Math.max(...filas.map(f => f.ancho)) + 2 * MARGEN, alto = filas.length * ALTO + 2 * MARGEN;
    // Cuánto del disco tapa la caja (de una rejilla de puntos): gana el primer sitio que menos tape,
    // sin subir hasta los botones que el visor pone sobre la parte de arriba de la foto
    const cx = d.disco.cx * w / d.ancho, cy = d.disco.cy * w / d.ancho, r = d.disco.r * w / d.ancho;
    const tapa = (x, y) => {
        let n = 0;
        for (let i = 0; i <= 4; i++) for (let j = 0; j <= 4; j++)
            if (Math.hypot(x + ancho * i / 4 - cx, y + alto * j / 4 - cy) < r) n++;
        return n;
    };
    let mejor = null;
    for (const x of [w - ancho - 4, 4])
        for (const pie of [0.86, 0.7, 0.5, 0.3]) {
            const y = Math.max(0.14 * h, pie * h - alto), n = tapa(x, y);
            if (!mejor || n < mejor.n) mejor = { x, y, n };
        }
    const { x, y } = mejor;
    return {
        caja: [x, y, x + ancho, y + alto],
        pintar(salieron) {
            const quedan = filas.filter(f => {
                if (salieron.has(f.clave)) return true;
                f.el.remove();
                return false;
            });
            if (!quedan.length) return;
            quedan.forEach((f, i) => {
                f.el.setAttribute("x", x + MARGEN);
                f.el.setAttribute("y", y + MARGEN + i * ALTO + f.base);
            });
            nuevo("rect", { x, y, width: ancho, height: quedan.length * ALTO + 2 * MARGEN, rx: 6 }, "luna-leyenda-fondo", quedan[0].el);
        },
    };
}

// Resumen para el pie de la foto: cuántos nombres caben, la fase y si el espejo es dudoso
function textoLunaAnotada(d, puestos) {
    let texto = ngettext("{n} accidente con nombre", "{n} accidentes con nombre", puestos) + " · "
        + _("{p} % iluminada", { p: Math.round(d.iluminada * 100) });
    if (d.dudosa) texto += " · " + _("orientación dudosa: puede salir en espejo");
    return texto;
}
