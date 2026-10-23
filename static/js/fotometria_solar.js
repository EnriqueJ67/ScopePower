// static/js/fotometria_solar.js
//
// Lo que se mide del disco del Sol (fotometria_solar.py), pintado en la galería:
//
//   rellenarPanelSol(caja, d)               -> el panel del visor de una foto: oscurecimiento del limbo por
//                                              colores, temperatura según la profundidad, diámetro y avisos
//   pintarSerieDiametro(svg, serie, curva)  -> el radio medido de cada foto sobre la curva de la efeméride
//                                              del año (la órbita de la Tierra)
//
// Las gráficas del visor van sobre fondo negro (el visor siempre lo es); la de la serie usa los
// colores del tema.

const COLOR_CANAL = { R: "#ff7a6b", G: "#6fdc96", B: "#7fb2ff", L: "#e8e8e8" };

function _svgNuevo(svg, tipo, attrs, clase, texto) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tipo);
    for (const [a, v] of Object.entries(attrs)) el.setAttribute(a, v);
    if (clase) el.setAttribute("class", clase);
    if (texto != null) el.textContent = texto;
    svg.appendChild(el);
    return el;
}

// Ejes de una gráfica pequeña: marco, rejilla en y con sus valores y rótulos de los ejes
function _ejes(svg, W, H, m, x0, x1, y0, y1, ticksX, ticksY, fmtX, fmtY, rotX, rotY) {
    const px = x => m.l + (x - x0) / (x1 - x0) * (W - m.l - m.r);
    const py = y => H - m.b - (y - y0) / (y1 - y0) * (H - m.t - m.b);
    ticksY.forEach(y => {
        _svgNuevo(svg, "line", { x1: m.l, y1: py(y), x2: W - m.r, y2: py(y) }, "fs-rejilla");
        _svgNuevo(svg, "text", { x: m.l - 5, y: py(y) + 4, "text-anchor": "end" }, "fs-eje", fmtY(y));
    });
    ticksX.forEach(x => _svgNuevo(svg, "text", { x: px(x), y: H - m.b + 13, "text-anchor": "middle" }, "fs-eje", fmtX(x)));
    if (rotX) _svgNuevo(svg, "text", { x: (m.l + W - m.r) / 2, y: H - 2, "text-anchor": "middle" }, "fs-rotulo", rotX);
    if (rotY) _svgNuevo(svg, "text", { x: m.l, y: m.t - 6, "text-anchor": "start" }, "fs-rotulo", rotY);
    return { px, py };
}

function _ley(mu, a) { const o = 1 - mu; return 1 - a.u * o - a.v * o * o; }

// Brillo relativo al centro frente a la distancia al centro: los puntos medidos y la ley ajustada
function pintarPerfilLimbo(svg, d) {
    svg.replaceChildren();
    const W = 340, H = 170, m = { l: 34, r: 8, t: 18, b: 30 };
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let ymin = 1;
    d.canales.forEach(c => d.perfil[c].forEach(v => { if (v != null) ymin = Math.min(ymin, v); }));
    const y0 = Math.max(0, Math.floor((ymin - 0.05) * 10) / 10);
    const ticksY = []; for (let y = y0; y <= 1.0001; y += 0.1) ticksY.push(Math.round(y * 10) / 10);
    const { px, py } = _ejes(svg, W, H, m, 0, 1, y0, 1.05, [0, 0.25, 0.5, 0.75, 1], ticksY.filter((y, i) => i % 2 === 0 || ticksY.length <= 5),
        x => fmtNum(x, 2), y => fmtNum(y, 1), _("distancia al centro (fracción del radio)"), _("brillo relativo al centro"));
    d.canales.forEach(c => {
        const a = d.ajuste[c];
        const pts = [];
        for (let r = 0; r <= 0.985; r += 0.01) pts.push(`${px(r)},${py(_ley(Math.sqrt(1 - r * r), a))}`);
        _svgNuevo(svg, "polyline", { points: pts.join(" "), stroke: COLOR_CANAL[c] }, "fs-linea");
        d.perfil.rho.forEach((r, i) => {
            const v = d.perfil[c][i];
            if (v != null && i % 2 === 0) _svgNuevo(svg, "circle", { cx: px(r), cy: py(v), r: 1.8, fill: COLOR_CANAL[c] }, "fs-punto");
        });
    });
}

// Temperatura frente a la profundidad óptica: un canal por línea y el modelo de Eddington discontinuo
function pintarTemperaturas(svg, d) {
    svg.replaceChildren();
    const W = 340, H = 160, m = { l: 42, r: 8, t: 18, b: 30 };
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const t = d.temperatura;
    let tmin = Infinity, tmax = -Infinity;
    d.canales.concat(["eddington"]).forEach(c => t[c].forEach(v => { tmin = Math.min(tmin, v); tmax = Math.max(tmax, v); }));
    const y0 = Math.floor((tmin - 50) / 200) * 200, y1 = Math.ceil((tmax + 50) / 200) * 200;
    const ticksY = []; for (let y = y0; y <= y1; y += 200) ticksY.push(y);
    const { px, py } = _ejes(svg, W, H, m, 0.2, 1, y0, y1, [0.2, 0.4, 0.6, 0.8, 1], ticksY.filter((y, i) => i % Math.ceil(ticksY.length / 5) === 0),
        x => fmtNum(x, 1), y => fmtNum(y, 0), _("profundidad óptica τ (más hondo a la derecha)"), _("temperatura (K)"));
    _svgNuevo(svg, "polyline", { points: t.tau.map((x, i) => `${px(x)},${py(t.eddington[i])}`).join(" ") }, "fs-modelo");
    d.canales.forEach(c => _svgNuevo(svg, "polyline", { points: t.tau.map((x, i) => `${px(x)},${py(t[c][i])}`).join(" "), stroke: COLOR_CANAL[c] }, "fs-linea"));
}

function _parrafo(caja, texto, clase) {
    const p = document.createElement("p");
    p.textContent = texto;
    if (clase) p.className = clase;
    caja.appendChild(p);
    return p;
}

const AVISOS_SOL = {
    calima: N_("Mucha calima: la luz difusa del cielo altera el perfil; esta medida es poco fiable."),
    saturado: N_("El centro del disco está saturado: el perfil no es fiable."),
    disco_cortado: N_("El disco sale cortado por el borde de la foto: sin diámetro."),
    escala_dudosa: N_("La escala de placa no casa con el tamaño del Sol: ¿otro telescopio o reductor desde el último plate solving?"),
    sin_cielo: N_("No se ve cielo alrededor del Sol: no se ha restado su brillo.")
};

// El panel del visor con todas las medidas de una foto
function rellenarPanelSol(caja, d) {
    caja.replaceChildren();
    const pct = x => fmtNum(x * 100, 0);
    const rgb = d.canales.length === 3;

    const h1 = document.createElement("h3");
    h1.textContent = _("Oscurecimiento del limbo");
    caja.appendChild(h1);
    const s1 = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s1.setAttribute("class", "fs-grafica");
    caja.appendChild(s1);
    pintarPerfilLimbo(s1, d);
    if (rgb)
        _parrafo(caja, _("Cerca del borde (95 % del radio) brilla el {r} % en rojo, el {g} % en verde y el {b} % en azul de lo que brilla en el centro. En el borde se ven capas más altas y frías, y el azul es el color que más cae al bajar la temperatura.",
            { r: pct(d.ajuste.R.i95), g: pct(d.ajuste.G.i95), b: pct(d.ajuste.B.i95) }));
    else
        _parrafo(caja, _("Cerca del borde (95 % del radio) brilla el {l} % de lo que brilla en el centro.", { l: pct(d.ajuste[d.canales[0]].i95) }));

    const h2 = document.createElement("h3");
    h2.textContent = _("Temperatura según la profundidad");
    caja.appendChild(h2);
    const s2 = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s2.setAttribute("class", "fs-grafica");
    caja.appendChild(s2);
    pintarTemperaturas(s2, d);
    const t = d.temperatura, e03 = t.eddington[t.tau.indexOf(0.3)];
    if (rgb)
        _parrafo(caja, _("A profundidad óptica 0,3 los tres colores dan {r}, {g} y {b} K (el modelo gris de Eddington, {e} K, en discontinua). Si los tres coinciden, la medida cuadra con la física.",
            { r: t.t03.R, g: t.t03.G, b: t.t03.B, e: e03 }));
    else
        _parrafo(caja, _("A profundidad óptica 0,3: {l} K (el modelo gris de Eddington, {e} K, en discontinua).", { l: t.t03[d.canales[0]], e: e03 }));
    _parrafo(caja, _("Aproximación de Eddington-Barbier: supone {t1} K a profundidad 1 y longitudes de onda efectivas aproximadas de los canales de la cámara.", { t1: t.t1 }), "fs-nota");

    const h3 = document.createElement("h3");
    h3.textContent = _("Diámetro");
    caja.appendChild(h3);
    const di = d.diametro;
    if (!di) {
        _parrafo(caja, _("Sin diámetro: el disco sale cortado o falta la fecha de la foto."));
    } else if (di.r_arcsec == null) {
        _parrafo(caja, _("Radio {r} px (efeméride {e}″). Sin escala de placa: haz un plate solving de noche o pon la focal en Configuración.",
            { r: fmtNum(di.r_px, 2), e: fmtNum(di.r_efem, 1) }));
    } else {
        _parrafo(caja, _("Radio {r}″ medido · {e}″ según la efeméride ({d} %).",
            { r: fmtNum(di.r_arcsec, 1), e: fmtNum(di.r_efem, 1), d: (di.desvio_pct > 0 ? "+" : "") + fmtNum(di.desvio_pct, 2) }));
        _parrafo(caja, _("Distancia Tierra-Sol: {d} ua medida · {e} ua según la efeméride.", { d: fmtNum(di.distancia, 4), e: fmtNum(di.distancia_efem, 4) }));
        _parrafo(caja, di.escala_origen === "plate_solving"
            ? _("Escala de placa {s}″/px, del último plate solving.", { s: fmtNum(di.escala, 4) })
            : _("Escala de placa {s}″/px, de la focal de Configuración (aproximada: un plate solving la afina).", { s: fmtNum(di.escala, 4) }), "fs-nota");
        if (di.seeing_arcsec != null) _parrafo(caja, _("El borde se emborrona en {s}″: el desenfoque más el seeing.", { s: fmtNum(di.seeing_arcsec, 1) }), "fs-nota");
    }
    if (di && di.achatamiento != null) {
        if (di.achatamiento_previsto != null)
            _parrafo(caja, _("Achatamiento vertical {a} %; la refracción con el Sol a {h}° de altura da {p} %. Por eso el radio es el semieje mayor.",
                { a: fmtNum(di.achatamiento, 2), p: fmtNum(di.achatamiento_previsto, 2), h: fmtNum(di.altura, 0) }));
        else
            _parrafo(caja, _("Achatamiento vertical {a} %.", { a: fmtNum(di.achatamiento, 2) }));
    }
    if (d.calima != null) _parrafo(caja, _("Cielo junto al Sol: {c} % del brillo del centro.", { c: fmtNum(d.calima * 100, 1) }), "fs-nota");
    (d.avisos || []).forEach(a => { if (AVISOS_SOL[a]) _parrafo(caja, _(AVISOS_SOL[a]), "fs-aviso"); });
}

// La serie del año: el radio medido de cada foto sobre la curva de la efeméride
function pintarSerieDiametro(svg, serie, curva) {
    svg.replaceChildren();
    if (!curva.length) return;
    const W = 600, H = 200, m = { l: 46, r: 10, t: 16, b: 26 };
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const t0 = curva[0].instante, t1 = curva[curva.length - 1].instante;
    // El eje lo deciden la efeméride y las medidas fiables: una dudosa (calima, otra escala) muy fuera no lo estira
    let r0 = Infinity, r1 = -Infinity;
    curva.forEach(c => { r0 = Math.min(r0, c.r); r1 = Math.max(r1, c.r); });
    serie.forEach(s => { if (s.fiable) { r0 = Math.min(r0, s.r_arcsec); r1 = Math.max(r1, s.r_arcsec); } });
    r0 = Math.floor(r0 - 2); r1 = Math.ceil(r1 + 2);
    const paso = [2, 5, 10, 20, 50].find(p => (r1 - r0) / p <= 6) || 100;
    const ticksY = []; for (let y = Math.ceil(r0 / paso) * paso; y <= r1; y += paso) ticksY.push(y);
    serie = serie.filter(s => s.r_arcsec >= r0 && s.r_arcsec <= r1);
    const meses = [];
    const ini = new Date(t0 * 1000);
    for (let i = 0; ; i += 2) {
        const f = new Date(ini.getFullYear(), ini.getMonth() + i, 1);
        if (f.getTime() / 1000 > t1) break;
        meses.push(f.getTime() / 1000);
    }
    const { px, py } = _ejes(svg, W, H, m, t0, t1, r0, r1, meses, ticksY,
        x => new Date(x * 1000).toLocaleDateString(IDIOMA.lang, { month: "short" }), y => fmtNum(y, 0), null, _("radio del Sol (″)"));
    _svgNuevo(svg, "polyline", { points: curva.map(c => `${px(c.instante)},${py(c.r)}`).join(" ") }, "fs-efemeride");
    serie.forEach(s => {
        const c = _svgNuevo(svg, "circle", { cx: px(s.instante), cy: py(s.r_arcsec), r: 4 }, s.fiable ? "fs-medida" : "fs-medida fs-dudosa");
        const tt = document.createElementNS("http://www.w3.org/2000/svg", "title");
        tt.textContent = `${fmtFecha(new Date(s.instante * 1000))} · ${fmtNum(s.r_arcsec, 1)}″ (${fmtNum(s.r_efem, 1)}″) · ${fmtNum(s.distancia, 4)} ua`;
        c.appendChild(tt);
    });
}
