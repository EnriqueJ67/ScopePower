// static/js/curvas.js — editor de curvas por puntos de control (como el del Seestar).
//
// Una curva es una lista de puntos [x, y] en [0, 1] ordenados por x, con los extremos
// en x = 0 y x = 1, que pasa por todos ellos con un spline cúbico monótono
// (Fritsch-Carlson). Hay cuatro: "rgb" (los tres canales a la vez, se aplica
// primero) y "r", "g", "b". El MISMO algoritmo está en Python en
// procesado.evaluar_curva: lo que se ve aquí es lo que se guarda.
//
//   const ed = new EditorCurvas(canvas, { alCambiar: curvas => ..., alArrastrar: curvas => ..., alSeleccionar: i => ... });
//   ed.curvas                       {rgb: [[x, y], ...], r: ..., g: ..., b: ...} (solo las que no son la identidad)
//   ed.poner(curvas)                carga unas curvas guardadas (sin avisar)
//   ed.tablas()                     {rgb, r, g, b}: Float32Array(1024) de cada curva, o null si es la recta
//   ed.histograma(hist)             {r, g, b, l}: Uint32Array(256) de cada canal, de fondo del lienzo
//   ed.canal("r") / ed.reiniciar() / ed.borrarPunto()
//
// Gestos: tocar la curva añade un punto; arrastrar mueve el más cercano; tocar un punto
// lo selecciona (se borra con el botón); los extremos solo se mueven en vertical.
(function () {
    "use strict";

    const CANALES = ["rgb", "r", "g", "b"];
    const COLOR = { rgb: "#ffffff", r: "#ff5a5a", g: "#5ad26b", b: "#5a9bff" };
    const IDENTIDAD = () => [[0, 0], [1, 1]];
    const MAX_PUNTOS = 16;

    function esIdentidad(p) {
        return !p || p.length < 2 || (p.length === 2 && p[0][0] === 0 && p[0][1] === 0 && p[1][0] === 1 && p[1][1] === 1);
    }

    // Tangentes de Fritsch-Carlson (igual que procesado._pendientes_curva)
    function pendientes(xs, ys) {
        const n = xs.length, h = [], d = [], m = new Array(n);
        for (let i = 0; i < n - 1; i++) { h.push(xs[i + 1] - xs[i]); d.push((ys[i + 1] - ys[i]) / h[i]); }
        m[0] = d[0]; m[n - 1] = d[n - 2];
        for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
        for (let i = 0; i < n - 1; i++) {
            if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
            const a = m[i] / d[i], b = m[i + 1] / d[i], s2 = a * a + b * b;
            if (s2 > 9) { const t = 3 / Math.sqrt(s2); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
        }
        return m;
    }

    // Tabla de `largo` entradas (float 0..1) de una curva; null = identidad. 256 para dibujar,
    // 1024 para aplicarla (retoque.js): así el redondeo a 8 bits se hace una sola vez, al final
    function tabla(puntos, largo) {
        largo = largo || 256;
        const t = new Float32Array(largo), ult = largo - 1;
        if (esIdentidad(puntos)) { for (let i = 0; i < largo; i++) t[i] = i / ult; return t; }
        const xs = puntos.map(p => p[0]), ys = puntos.map(p => p[1]), n = xs.length;
        if (n === 2) {
            for (let i = 0; i < largo; i++) t[i] = Math.min(1, Math.max(0, ys[0] + (ys[1] - ys[0]) * (i / ult - xs[0]) / (xs[1] - xs[0])));
            return t;
        }
        const m = pendientes(xs, ys);
        let k = 0;
        for (let i = 0; i < largo; i++) {
            const x = i / ult;
            while (k < n - 2 && x > xs[k + 1]) k++;
            const h = xs[k + 1] - xs[k], u = (x - xs[k]) / h, u2 = u * u, u3 = u2 * u;
            const y = (2 * u3 - 3 * u2 + 1) * ys[k] + (u3 - 2 * u2 + u) * h * m[k] + (-2 * u3 + 3 * u2) * ys[k + 1] + (u3 - u2) * h * m[k + 1];
            t[i] = Math.min(1, Math.max(0, y));
        }
        return t;
    }

    // Puntos limpios: ordenados, extremos en 0 y 1, sin dos con la misma x (como procesado.normalizar_curva)
    function normalizar(puntos) {
        const limpios = [];
        for (const p of puntos || []) {
            const x = Number(p[0]), y = Number(p[1]);
            if (isFinite(x) && isFinite(y)) limpios.push([Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))]);
        }
        if (limpios.length < 2) return IDENTIDAD();
        limpios.sort((a, b) => a[0] - b[0]);
        limpios[0][0] = 0; limpios[limpios.length - 1][0] = 1;
        const salida = [limpios[0]];
        for (const p of limpios.slice(1)) if (p[0] - salida[salida.length - 1][0] >= 0.002) salida.push(p);
        if (salida.length < 2) return IDENTIDAD();
        salida[salida.length - 1][0] = 1;
        return salida.slice(0, MAX_PUNTOS).map(p => [Math.round(p[0] * 1e4) / 1e4, Math.round(p[1] * 1e4) / 1e4]);
    }

    class EditorCurvas {
        constructor(canvas, opciones) {
            this.canvas = canvas;
            this.ctx = canvas.getContext("2d");
            this.op = opciones || {};
            this.puntos = { rgb: IDENTIDAD(), r: IDENTIDAD(), g: IDENTIDAD(), b: IDENTIDAD() };
            this.activo = "rgb";
            this.seleccion = -1;          // índice del punto seleccionado en la curva activa
            this.hist = null;
            this.arrastre = null;
            this.margen = 10;
            canvas.style.touchAction = "none";
            canvas.addEventListener("pointerdown", e => this._abajo(e));
            canvas.addEventListener("pointermove", e => this._mueve(e));
            canvas.addEventListener("pointerup", e => this._arriba(e));
            canvas.addEventListener("pointercancel", e => this._arriba(e));
            if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => this.pintar()).observe(canvas);
            this.pintar();
        }

        get curvas() {
            const c = {};
            for (const k of CANALES) if (!esIdentidad(this.puntos[k])) c[k] = this.puntos[k].map(p => p.slice());
            return c;
        }
        poner(curvas) {
            for (const k of CANALES) this.puntos[k] = normalizar(curvas && curvas[k]);
            this.seleccion = -1;
            this.pintar();
        }
        tablas(largo) {
            const t = {};
            for (const k of CANALES) t[k] = esIdentidad(this.puntos[k]) ? null : tabla(this.puntos[k], largo || 1024);
            return t;
        }
        tocada(canal) { return !esIdentidad(this.puntos[canal]); }
        canal(k) {
            if (!CANALES.includes(k)) return;
            this.activo = k; this.seleccion = -1;
            this.pintar();
            if (this.op.alSeleccionar) this.op.alSeleccionar(-1);
        }
        reiniciar() { this.puntos[this.activo] = IDENTIDAD(); this.seleccion = -1; this._cambio(); }
        reiniciarTodo() { for (const k of CANALES) this.puntos[k] = IDENTIDAD(); this.seleccion = -1; this._cambio(); }
        borrarPunto() {
            const p = this.puntos[this.activo];
            if (this.seleccion <= 0 || this.seleccion >= p.length - 1) return;
            p.splice(this.seleccion, 1);
            this.seleccion = -1;
            this._cambio();
        }
        histograma(h) { this.hist = h; this.pintar(); }

        _cambio() {
            this.pintar();
            if (this.op.alCambiar) this.op.alCambiar(this.curvas);
            if (this.op.alSeleccionar) this.op.alSeleccionar(this.seleccion);
        }

        // ── Geometría: lienzo ↔ [0, 1] ──
        _tam() {
            const r = this.canvas.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            const w = Math.max(60, Math.round(r.width * dpr)), h = Math.max(60, Math.round(r.height * dpr));
            if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
            return { w, h, dpr, m: this.margen * dpr };
        }
        _aXY(x, y, t) { return [t.m + x * (t.w - 2 * t.m), t.h - t.m - y * (t.h - 2 * t.m)]; }
        _aUnidad(ev, t) {
            const r = this.canvas.getBoundingClientRect();
            const px = (ev.clientX - r.left) * t.dpr, py = (ev.clientY - r.top) * t.dpr;
            return [(px - t.m) / (t.w - 2 * t.m), (t.h - t.m - py) / (t.h - 2 * t.m)];
        }
        _masCercano(ev, t) {
            const r = this.canvas.getBoundingClientRect();
            const px = (ev.clientX - r.left) * t.dpr, py = (ev.clientY - r.top) * t.dpr;
            let mejor = -1, dist = 22 * t.dpr;
            this.puntos[this.activo].forEach((p, i) => {
                const [cx, cy] = this._aXY(p[0], p[1], t), d = Math.hypot(cx - px, cy - py);
                if (d < dist) { dist = d; mejor = i; }
            });
            return mejor;
        }

        // ── Gestos ──
        _abajo(ev) {
            ev.preventDefault();
            const t = this._tam();
            let i = this._masCercano(ev, t);
            const p = this.puntos[this.activo];
            if (i < 0) {
                const [x, y] = this._aUnidad(ev, t);
                if (x < -0.05 || x > 1.05 || y < -0.1 || y > 1.1 || p.length >= MAX_PUNTOS) return;
                // Nuevo punto: en la x tocada, sobre la curva (el dedo lo mueve después)
                const tb = tabla(p), xi = Math.min(1, Math.max(0, x));
                const nuevo = [Math.round(xi * 1e4) / 1e4, Math.round(tb[Math.round(xi * 255)] * 1e4) / 1e4];
                p.push(nuevo);
                p.sort((a, b) => a[0] - b[0]);
                i = p.indexOf(nuevo);
            }
            this.seleccion = i;
            this.arrastre = { i, movido: false, id: ev.pointerId };
            try { this.canvas.setPointerCapture(ev.pointerId); } catch (e) { /* sin captura */ }
            this.pintar();
        }
        _mueve(ev) {
            if (!this.arrastre || ev.pointerId !== this.arrastre.id) return;
            ev.preventDefault();
            const t = this._tam(), p = this.puntos[this.activo], i = this.arrastre.i;
            let [x, y] = this._aUnidad(ev, t);
            y = Math.min(1, Math.max(0, y));
            if (i === 0) x = 0;
            else if (i === p.length - 1) x = 1;
            else x = Math.min(p[i + 1][0] - 0.01, Math.max(p[i - 1][0] + 0.01, x));
            p[i] = [Math.round(x * 1e4) / 1e4, Math.round(y * 1e4) / 1e4];
            this.arrastre.movido = true;
            this.pintar();
            if (this.op.alArrastrar) this.op.alArrastrar(this.curvas);
        }
        _arriba(ev) {
            if (!this.arrastre || ev.pointerId !== this.arrastre.id) return;
            this.arrastre = null;
            this._cambio();
        }

        // ── Dibujo ──
        pintar() {
            const t = this._tam(), ctx = this.ctx, w = t.w, h = t.h, m = t.m;
            ctx.clearRect(0, 0, w, h);
            ctx.fillStyle = "rgba(0,0,0,0.35)";
            ctx.fillRect(0, 0, w, h);
            // Histograma (raíz cuadrada: el pico del cielo no aplasta lo demás)
            if (this.hist) {
                const canal = this.activo === "rgb" ? "l" : this.activo;
                const datos = this.hist[canal] || this.hist.l;
                if (datos) {
                    let max = 0;
                    for (let i = 1; i < 255; i++) max = Math.max(max, datos[i]);   // sin los extremos (negro recortado, blanco quemado)
                    if (max > 0) {
                        ctx.fillStyle = this.activo === "rgb" ? "rgba(255,255,255,0.28)" : COLOR[this.activo].replace(")", ",0.35)").replace("#", "rgba(") ;
                        ctx.fillStyle = this.activo === "rgb" ? "rgba(255,255,255,0.28)" : this._rgba(COLOR[this.activo], 0.35);
                        ctx.beginPath();
                        ctx.moveTo(m, h - m);
                        for (let i = 0; i < 256; i++) {
                            const [x, y] = this._aXY(i / 255, Math.min(1, Math.sqrt(datos[i] / max)), t);
                            ctx.lineTo(x, y);
                        }
                        ctx.lineTo(w - m, h - m);
                        ctx.closePath();
                        ctx.fill();
                    }
                }
            }
            // Rejilla 4 × 4 y diagonal
            ctx.strokeStyle = "rgba(255,255,255,0.14)";
            ctx.lineWidth = 1 * t.dpr;
            ctx.beginPath();
            for (let i = 0; i <= 4; i++) {
                const [x0, y0] = this._aXY(i / 4, 0, t), [x1, y1] = this._aXY(i / 4, 1, t);
                ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
                const [a0, b0] = this._aXY(0, i / 4, t), [a1, b1] = this._aXY(1, i / 4, t);
                ctx.moveTo(a0, b0); ctx.lineTo(a1, b1);
            }
            ctx.stroke();
            ctx.setLineDash([4 * t.dpr, 4 * t.dpr]);
            ctx.strokeStyle = "rgba(255,255,255,0.22)";
            ctx.beginPath();
            const [d0, e0] = this._aXY(0, 0, t), [d1, e1] = this._aXY(1, 1, t);
            ctx.moveTo(d0, e0); ctx.lineTo(d1, e1); ctx.stroke();
            ctx.setLineDash([]);
            // Las otras curvas, tenues, para no perderlas de vista
            for (const k of CANALES) {
                if (k === this.activo || esIdentidad(this.puntos[k])) continue;
                this._trazo(tabla(this.puntos[k]), this._rgba(COLOR[k], 0.35), 1.2 * t.dpr, t);
            }
            // La curva activa y sus puntos
            const p = this.puntos[this.activo];
            this._trazo(tabla(p), COLOR[this.activo], 2.2 * t.dpr, t);
            p.forEach((q, i) => {
                const [x, y] = this._aXY(q[0], q[1], t);
                ctx.beginPath();
                ctx.arc(x, y, (i === this.seleccion ? 7 : 5) * t.dpr, 0, Math.PI * 2);
                ctx.fillStyle = i === this.seleccion ? COLOR[this.activo] : "#1b1f2a";
                ctx.fill();
                ctx.lineWidth = 2 * t.dpr;
                ctx.strokeStyle = COLOR[this.activo];
                ctx.stroke();
            });
        }
        _trazo(tb, color, grosor, t) {
            const ctx = this.ctx;
            ctx.strokeStyle = color; ctx.lineWidth = grosor; ctx.lineJoin = "round";
            ctx.beginPath();
            for (let i = 0; i < 256; i++) {
                const [x, y] = this._aXY(i / 255, tb[i], t);
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.stroke();
        }
        _rgba(hex, a) {
            const n = parseInt(hex.slice(1), 16);
            return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
        }
    }

    window.EditorCurvas = EditorCurvas;
    window.EditorCurvas.tabla = tabla;
    window.EditorCurvas.normalizar = normalizar;
    window.EditorCurvas.esIdentidad = esIdentidad;
})();
