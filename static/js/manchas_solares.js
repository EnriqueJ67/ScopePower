// static/js/manchas_solares.js
//
// La capa de manchas solares y el Número de Wolf (manchas_solares.py) sobre una foto del Sol
// en el visor de la galería. El servidor da las manchas en píxeles del JPG; aquí se pintan a
// tamaño de pantalla (k = píxeles de pantalla por píxel del JPG) y se corrige el conteo a mano:
//
//   modo «manchas»: tocar una mancha la quita; tocar el disco donde no hay ninguna la añade
//   modo «grupos»:  tocar una mancha la selecciona; tocar otra une los dos grupos; tocar la
//                   misma otra vez la separa en un grupo nuevo
//
//   pintarManchasSolares(svg, d, k, w, h, estado) -> pinta; estado = {modo, seleccionada}
//   wolfTocar(d, x, y, estado)                     -> aplica un toque (x, y en px del JPG); true si cambió algo
//   wolfRecontar(d)                                -> rehace d.grupos y d.wolf a partir de d.manchas
//   textoWolf(d)                                   -> «R = 10·3 + 11 = 41 · 3 grupos, 11 manchas · guardado»
//   pintarSerieWolf(svg, serie)                    -> la gráfica de la serie guardada
//
// Los grupos se enlazan por la separación angular sobre la esfera (igual que en el servidor):
// sale de la posición en el disco, sin saber dónde está el norte.

function _wolfXYZ(m, disco) {
    let X = (m.x - disco.cx) / disco.r, Y = (m.y - disco.cy) / disco.r;
    let r2 = X * X + Y * Y;
    if (r2 >= 1) { const n = Math.sqrt(r2); X = X / n * 0.9999; Y = Y / n * 0.9999; r2 = 0.9998; }
    return [X, Y, Math.sqrt(1 - r2)];
}

function wolfSeparacion(a, b, disco) {
    const p = _wolfXYZ(a, disco), q = _wolfXYZ(b, disco);
    return Math.acos(Math.max(-1, Math.min(1, p[0] * q[0] + p[1] * q[1] + p[2] * q[2]))) * 180 / Math.PI;
}

// Grupos 1..g de izquierda a derecha y el Número de Wolf con el k de la foto
function wolfRecontar(d) {
    const centros = {};
    d.manchas.forEach(m => { (centros[m.grupo] = centros[m.grupo] || []).push(m.x); });
    const orden = Object.keys(centros).sort((a, b) => {
        const ma = centros[a].reduce((s, v) => s + v, 0) / centros[a].length, mb = centros[b].reduce((s, v) => s + v, 0) / centros[b].length;
        return ma - mb || Number(a) - Number(b);
    });
    const nuevo = {};
    orden.forEach((g, i) => { nuevo[g] = i + 1; });
    d.manchas.forEach(m => { m.grupo = nuevo[m.grupo]; });
    d.grupos = orden.map((g, i) => {
        const ms = d.manchas.filter(m => m.grupo === i + 1);
        const media = clave => ms.every(m => m[clave] != null) ? ms.reduce((s, m) => s + m[clave], 0) / ms.length : null;
        return { id: i + 1, n: ms.length, x: media("x"), y: media("y"), lat: media("lat"), dlon: media("dlon") };
    });
    const g = d.grupos.length, f = d.manchas.length, k = (d.wolf && d.wolf.k) || 1;
    d.wolf = { g, f, R: Math.round(k * (10 * g + f) * 10) / 10, k };
    return d.wolf;
}

function _wolfCerca(d, x, y, k) {
    let mejor = -1, dist = Infinity;
    d.manchas.forEach((m, i) => {
        const dd = Math.hypot(m.x - x, m.y - y);
        if (dd < Math.max(m.r_px + 6 / k, 14 / k) && dd < dist) { mejor = i; dist = dd; }
    });
    return mejor;
}

// Un toque en (x, y) píxeles del JPG; k = píxeles de pantalla por píxel del JPG (para el radio del toque)
function wolfTocar(d, x, y, estado, k) {
    const i = _wolfCerca(d, x, y, k || 1);
    if (estado.modo === "grupos") {
        if (i < 0) return false;
        const m = d.manchas[i];
        if (estado.seleccionada == null) { estado.seleccionada = i; return true; }
        const s = d.manchas[estado.seleccionada];
        if (s === m) {
            // Separar: a un grupo nuevo (si estaba sola, no cambia nada)
            if (d.manchas.filter(o => o.grupo === m.grupo).length > 1) m.grupo = Math.max(...d.manchas.map(o => o.grupo)) + 1;
        } else {
            const de = m.grupo, a = s.grupo;
            d.manchas.forEach(o => { if (o.grupo === de) o.grupo = a; });
        }
        estado.seleccionada = null;
    } else {
        if (i >= 0) {
            d.manchas.splice(i, 1);
        } else {
            if (Math.hypot(x - d.disco.cx, y - d.disco.cy) > 0.97 * d.disco.r) return false;
            const nueva = { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, r_px: Math.max(2, d.disco.r * 0.008), umbra: false, lat: null, dlon: null, lon: null, grupo: 0 };
            // Al grupo más cercano si está a menos de la separación de grupo; si no, a uno nuevo
            let grupo = 0, mejor = Infinity;
            d.manchas.forEach(m => { const s = wolfSeparacion(nueva, m, d.disco); if (s < mejor) { mejor = s; grupo = m.grupo; } });
            nueva.grupo = (grupo && mejor <= (d.sep_grupo_deg || 8)) ? grupo : Math.max(0, ...d.manchas.map(o => o.grupo)) + 1;
            d.manchas.push(nueva);
        }
        estado.seleccionada = null;
    }
    wolfRecontar(d);
    d.guardado = false;
    d.editado = true;
    return true;
}

function _wolfHelio(lat, dlon) {
    const la = Math.round(Math.abs(lat)), lo = Math.round(Math.abs(dlon));
    return (lat >= 0 ? _("N{lat}", { lat: la }) : _("S{lat}", { lat: la })) + " " + (dlon >= 0 ? _("O{lon}", { lon: lo }) : _("E{lon}", { lon: lo }));
}

function pintarManchasSolares(svg, d, k, w, h, estado) {
    const NS = "http://www.w3.org/2000/svg";
    const nuevo = (tipo, attrs, clase) => {
        const el = document.createElementNS(NS, tipo);
        for (const [a, v] of Object.entries(attrs)) el.setAttribute(a, v);
        if (clase) el.setAttribute("class", clase);
        svg.appendChild(el);
        return el;
    };
    nuevo("circle", { cx: d.disco.cx * k, cy: d.disco.cy * k, r: d.disco.r * k }, "sol-limbo");
    // Las manchas: la umbra rellena, la penumbra sola (un poro) solo el contorno discontinuo
    d.manchas.forEach((m, i) => {
        const x = m.x * k, y = m.y * k, r = Math.max(m.r_px * k, 3) + 2;
        const clase = "sol-mancha " + (m.umbra ? "sol-umbra" : "sol-penumbra") + (estado && estado.seleccionada === i ? " sol-seleccionada" : "");
        nuevo("circle", { cx: x, cy: y, r: r }, clase);
    });
    // Cada grupo: su número (y sus coordenadas heliográficas si se conocen) encima de su centro
    d.grupos.forEach(g => {
        const ms = d.manchas.filter(m => m.grupo === g.id);
        const arriba = Math.min(...ms.map(m => m.y - Math.max(m.r_px, 3 / k)));
        const texto = nuevo("text", { x: g.x * k, y: Math.max(12, arriba * k - 6), "text-anchor": "middle" }, "sol-grupo");
        texto.textContent = g.lat != null ? `${g.id} · ${_wolfHelio(g.lat, g.dlon)}` : String(g.id);
    });
}

function textoWolf(d) {
    const w = d.wolf;
    let texto = _("R = 10·{g} + {f} = {R}", { g: w.g, f: w.f, R: fmtNum(w.R, w.k === 1 ? 0 : 1) });
    if (w.k !== 1) texto += " " + _("(k = {k})", { k: fmtNum(w.k, 2) });
    texto += " · " + ngettext("{n} grupo", "{n} grupos", w.g) + ", " + ngettext("{n} mancha", "{n} manchas", w.f);
    if (d.disco && d.disco.cortado) texto += " · " + _("disco cortado: conteo parcial");
    if (d.guardado) texto += " · " + _("guardado");
    else if (d.editado) texto += " · " + _("sin guardar");
    return texto;
}

// La serie de Números de Wolf guardados: fecha → R, los puntos unidos, en un SVG de 600×160
function pintarSerieWolf(svg, serie) {
    const NS = "http://www.w3.org/2000/svg";
    svg.replaceChildren();
    if (!serie.length) return;
    const W = 600, H = 160, mx = 34, my = 14;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const nuevo = (tipo, attrs, clase, contenido) => {
        const el = document.createElementNS(NS, tipo);
        for (const [a, v] of Object.entries(attrs)) el.setAttribute(a, v);
        if (clase) el.setAttribute("class", clase);
        if (contenido != null) el.textContent = contenido;
        svg.appendChild(el);
        return el;
    };
    const ts = serie.map(s => new Date(s.fecha).getTime());
    const t0 = Math.min(...ts), t1 = Math.max(...ts), rmax = Math.max(10, ...serie.map(s => s.R));
    const px = t => t1 > t0 ? mx + (t - t0) / (t1 - t0) * (W - 2 * mx) : W / 2;
    const py = r => H - my - r / rmax * (H - 2 * my - 12);
    [0, 0.5, 1].forEach(f => {
        const r = Math.round(rmax * f);
        nuevo("line", { x1: mx, y1: py(r), x2: W - mx, y2: py(r) }, "wolf-rejilla");
        nuevo("text", { x: mx - 6, y: py(r) + 4, "text-anchor": "end" }, "wolf-eje", r);
    });
    nuevo("polyline", { points: serie.map((s, i) => `${px(ts[i])},${py(s.R)}`).join(" ") }, "wolf-linea");
    serie.forEach((s, i) => {
        const c = nuevo("circle", { cx: px(ts[i]), cy: py(s.R), r: 4 }, "wolf-punto");
        const t = nuevo("title", {});
        t.textContent = `${fmtFecha(new Date(s.fecha))} · R = ${fmtNum(s.R, 0)} (g ${s.g}, f ${s.f})`;
        c.appendChild(t);
    });
    nuevo("text", { x: mx, y: H - 2 }, "wolf-eje", fmtFecha(new Date(t0), false));
    if (t1 > t0) nuevo("text", { x: W - mx, y: H - 2, "text-anchor": "end" }, "wolf-eje", fmtFecha(new Date(t1), false));
}
