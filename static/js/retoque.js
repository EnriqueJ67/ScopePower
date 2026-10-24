// static/js/retoque.js — los ajustes rápidos del editor de la galería, hechos en el navegador.
//
// El servidor manda la BASE de la vista previa (la foto estirada, con el fondo
// neutralizado y el color de las estrellas, en 8 bits), las dos máscaras de
// estrellas y la firma como capa transparente (editor_fotos.vista_previa). Aquí se
// aplican sobre esa base, en vivo y sin pedir nada al mini PC, los mismos pasos que
// procesado.ajustes_rapidos hace en coma flotante al guardar, EN EL MISMO ORDEN:
//
//   1. temperatura de color   afín alrededor del nivel del cielo (fondo), por canal
//   2. brillo general         potencia 2^(-brillo), por canal
//   3. SCNR                   G -= factor · max(G - (R + B) / 2, 0), sin las estrellas si se pide
//   4. saturación             S × factor en HSV (V y H fijos), × factor_estrellas en las estrellas pequeñas
//   5. curvas                 la de los tres canales y después la de cada uno (curvas.js)
//
// Con las ESTRELLAS APARTE (estrellas_aparte.py) el paquete trae además la capa de
// estrellas sobre negro, y se hace lo que procesado.ajustes_rapidos_capas: sobre la
// base (el objeto, sin estrellas) los pasos 1-4 con la saturación general; sobre la
// capa, temperatura (sobre negro) y brillo, el SCNR si se pide también en las
// estrellas, su propia saturación y el tamaño (0..100 %); pantalla de las dos
// (1 - (1 - a)(1 - b)) y, al final, las curvas.
//
// El tamaño REDUCE las estrellas pintándolas de nuevo como campanas de Gauss
// (procesado.pintar_estrellas, la misma aritmética) desde el catálogo que manda el
// servidor (procesado.catalogo_estrellas: x, y, sigma, r, g, b de cada estrella): sigma
// nueva = raíz((sigma·tamaño)² + 0,5²), la misma proporción para todas, hasta 3,5 sigmas;
// donde se solapan manda la más brillante; por debajo del 25 % el pico baja con
// (tamaño/0,25)², y entre el 100 y el 85 % se funde con la capa original. Se pinta una
// vez por valor del deslizador (_reducida, con caché).
//
// Los pasos 1, 2 y 5 son por tabla (256 entradas por canal); 3 y 4 píxel a píxel.
// Si se cambia algo aquí hay que cambiarlo también en procesado.py, y al revés.
//
//   const r = new Retoque(canvas);
//   await r.cargar(paquete)       {base: Blob, mascaras: Blob, firma: Blob, fondo: [r, g, b], estrellas?: Blob}
//   r.aplicar(ajustes, editorCurvas.tablas(), conFirma)   → pinta el lienzo; devuelve el histograma {r, g, b, l}
(function () {
    "use strict";

    async function decodificar(blob) {
        if (typeof createImageBitmap === "function") {
            try { return await createImageBitmap(blob); } catch (e) { /* al <img> */ }
        }
        return new Promise((ok, mal) => {
            const img = new Image();
            const url = URL.createObjectURL(blob);
            img.onload = () => { URL.revokeObjectURL(url); ok(img); };
            img.onerror = () => { URL.revokeObjectURL(url); mal(new Error("imagen")); };
            img.src = url;
        });
    }

    function pixeles(imagen, w, h) {
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        const ctx = c.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(imagen, 0, 0, w, h);
        return ctx.getImageData(0, 0, w, h);
    }

    // Catálogo de estrellas, 8 bytes por estrella (editor_fotos.empaquetar_catalogo: x·16 y y·16
    // en uint16 little-endian, sigma·16 y r, g, b ·255 en uint8) → Float32Array(6·N) con los valores reales
    function leerCatalogo(buffer) {
        const v = new DataView(buffer), n = Math.floor(buffer.byteLength / 8), cat = new Float32Array(n * 6);
        for (let i = 0; i < n; i++) {
            const o = i * 8, k = i * 6;
            cat[k] = v.getUint16(o, true) / 16; cat[k + 1] = v.getUint16(o + 2, true) / 16; cat[k + 2] = v.getUint8(o + 4) / 16;
            cat[k + 3] = v.getUint8(o + 5) / 255; cat[k + 4] = v.getUint8(o + 6) / 255; cat[k + 5] = v.getUint8(o + 7) / 255;
        }
        return cat;
    }

    class Retoque {
        constructor(canvas) {
            this.canvas = canvas;
            this.ctx = canvas.getContext("2d");
            this.base = null;         // ImageData de la base
            this.estrellas = null;    // Uint8ClampedArray (w·h) 0..255
            this.pequenas = null;
            this.capa = null;         // ImageData de la capa de estrellas (estrellas aparte) o null
            this.catalogo = null;     // Float32Array(6·N): x, y, sigma, r, g, b de cada estrella (leerCatalogo)
            this.reducida = null;     // {tamano, datos: Uint8ClampedArray (w·h·4)} la última capa reducida
            this.firma = null;        // ImageBitmap / Image con transparencia
            this.fondo = [0, 0, 0];
            this.salida = null;       // ImageData de trabajo
            this.ancho = 0; this.alto = 0;
        }

        async cargar(paquete) {
            const [imgBase, imgMasc, imgFirma, imgCapa, bufCatalogo] = await Promise.all([
                decodificar(paquete.base), decodificar(paquete.mascaras), paquete.firma ? decodificar(paquete.firma) : null,
                paquete.estrellas ? decodificar(paquete.estrellas) : null,
                paquete.catalogo ? paquete.catalogo.arrayBuffer() : null]);
            const w = imgBase.width, h = imgBase.height;
            this.ancho = w; this.alto = h;
            this.base = pixeles(imgBase, w, h);
            // Máscaras: estrellas | pequeñas lado a lado, a media resolución; se estiran al tamaño de la base
            const mw = imgMasc.width / 2, mh = imgMasc.height;
            const c = document.createElement("canvas");
            c.width = w; c.height = h;
            const cx = c.getContext("2d", { willReadFrequently: true });
            cx.imageSmoothingEnabled = true;
            cx.drawImage(imgMasc, 0, 0, mw, mh, 0, 0, w, h);
            const e = cx.getImageData(0, 0, w, h).data;
            cx.drawImage(imgMasc, mw, 0, mw, mh, 0, 0, w, h);
            const p = cx.getImageData(0, 0, w, h).data;
            const n = w * h;
            this.estrellas = new Uint8ClampedArray(n);
            this.pequenas = new Uint8ClampedArray(n);
            for (let i = 0, j = 0; i < n; i++, j += 4) { this.estrellas[i] = e[j]; this.pequenas[i] = p[j]; }
            this.capa = imgCapa ? pixeles(imgCapa, w, h) : null;
            this.catalogo = bufCatalogo ? leerCatalogo(bufCatalogo) : null;
            this.reducida = null;
            this.firma = imgFirma;
            this.fondo = (paquete.fondo || [0, 0, 0]).map(Number);
            this.canvas.width = w; this.canvas.height = h;
            this.salida = this.ctx.createImageData(w, h);
            if (imgBase.close) imgBase.close();
            if (imgMasc.close) imgMasc.close();
            if (imgCapa && imgCapa.close) imgCapa.close();
        }

        get lista() { return !!this.base; }
        get conCapas() { return !!this.capa; }

        // Capa de estrellas con el tamaño 0..1 (procesado.reducir_estrellas): las estrellas del
        // catálogo pintadas como gaussianas, fundidas con la original entre el 100 y el 85 %
        _reducida(tamano) {
            if (this.reducida && this.reducida.tamano === tamano) return this.reducida.datos;
            const w = this.ancho, h = this.alto, n = w * h, cat = this.catalogo || new Float32Array(0);
            const pintada = new Float32Array(n * 3), Lout = new Float32Array(n);
            if (tamano > 0) {
                const apagado = tamano < 0.25 ? Math.pow(tamano / 0.25, 2) : 1;          // procesado.TAMANO_PICO
                for (let e = 0; e < cat.length; e += 6) {
                    const x = cat[e], y = cat[e + 1], s = cat[e + 2], r = cat[e + 3], g = cat[e + 4], b = cat[e + 5];
                    const P = Math.max(r, g, b);
                    if (P <= 0) continue;
                    const s2 = Math.sqrt((s * tamano) * (s * tamano) + 0.25);          // procesado.SIGMA_MINIMA = 0,5
                    const R = Math.ceil(3.5 * s2), xc = Math.round(x), yc = Math.round(y);
                    const x0 = Math.max(0, xc - R), x1 = Math.min(w, xc + R + 1), y0 = Math.max(0, yc - R), y1 = Math.min(h, yc + R + 1);
                    const dos = 2 * s2 * s2;
                    for (let yy = y0; yy < y1; yy++) {
                        const gy = Math.exp(-((yy - y) * (yy - y)) / dos);
                        for (let xx = x0; xx < x1; xx++) {
                            const v = P * apagado * gy * Math.exp(-((xx - x) * (xx - x)) / dos);
                            const i = yy * w + xx;
                            if (v > Lout[i]) {
                                Lout[i] = v;
                                const f = v / P;
                                pintada[i * 3] = f * r; pintada[i * 3 + 1] = f * g; pintada[i * 3 + 2] = f * b;
                            }
                        }
                    }
                }
            }
            const a = Math.min(1, Math.max(0, (1 - tamano) / (1 - 0.85)));              // procesado.TAMANO_FUNDIDO
            const d = this.capa.data, datos = new Uint8ClampedArray(n * 4);
            for (let i = 0, j = 0; i < n; i++, j += 4) {
                datos[j] = Math.round(d[j] * (1 - a) + pintada[i * 3] * 255 * a);
                datos[j + 1] = Math.round(d[j + 1] * (1 - a) + pintada[i * 3 + 1] * 255 * a);
                datos[j + 2] = Math.round(d[j + 2] * (1 - a) + pintada[i * 3 + 2] * 255 * a);
                datos[j + 3] = 255;
            }
            this.reducida = { tamano, datos };
            return datos;
        }

        // Tablas por canal de temperatura + brillo (pasos 1 y 2), en float 0..1.
        // fondo: el nivel del cielo alrededor del que gira la temperatura ([0, 0, 0] para la capa de estrellas)
        _tablasPrevias(aj, fondo) {
            const t = Math.min(1, Math.max(-1, Number(aj.temperatura) || 0));
            const b = Math.min(1, Math.max(-1, Number(aj.brillo) || 0));
            const gan = [1 + 0.3 * t, 1, 1 - 0.3 * t];
            const exp = Math.pow(2, -b);
            const tablas = [];
            for (let c = 0; c < 3; c++) {
                const tb = new Float32Array(256), f = fondo[c] || 0;
                for (let i = 0; i < 256; i++) {
                    let v = i / 255;
                    if (Math.abs(t) >= 1e-3) v = f + (v - f) * gan[c];
                    v = Math.min(1, Math.max(0, v));
                    if (Math.abs(b) >= 1e-3) v = Math.pow(v, exp);
                    tb[i] = v;
                }
                tablas.push(tb);
            }
            return tablas;
        }

        // Tablas finales de las curvas (paso 5), de 1024 entradas (índice = valor float × 1023) a
        // byte: la de los tres canales y después la del canal. Con 1024 entradas el redondeo a 8
        // bits se hace una sola vez, al final, y el resultado queda a ±1 nivel del servidor en float
        _tablasCurvas(curvas) {
            const salida = [];
            const rgb = curvas && curvas.rgb;
            for (const k of ["r", "g", "b"]) {
                const propia = curvas && curvas[k];
                const tb = new Uint8ClampedArray(1024);
                for (let i = 0; i < 1024; i++) {
                    let v = rgb ? rgb[i] : i / 1023;
                    if (propia) v = propia[Math.round(v * 1023)];
                    tb[i] = Math.round(v * 255);
                }
                salida.push(tb);
            }
            return salida;
        }

        /**
         * Aplica los ajustes y pinta. aj: {temperatura, brillo, scnr, scnr_estrellas, saturacion,
         * saturacion_estrellas, tamano_estrellas}; curvas: tablas de EditorCurvas.tablas() (o null);
         * conFirma: pintar la firma. Devuelve el histograma {r, g, b, l} (Uint32Array(256)) de la
         * imagen ANTES de las curvas, que es lo que el editor de curvas enseña de fondo.
         */
        aplicar(aj, curvas, conFirma) {
            if (!this.base) return null;
            const src = this.base.data, dst = this.salida.data, n = this.ancho * this.alto;
            const previas = this._tablasPrevias(aj, this.fondo);
            const tR = previas[0], tG = previas[1], tB = previas[2];
            const scnr = Math.min(1, Math.max(0, Number(aj.scnr) || 0));
            const scnrEstrellas = aj.scnr_estrellas !== false;
            const sat = Number(aj.saturacion); const satG = isFinite(sat) ? sat : 1;
            const satE = aj.saturacion_estrellas === undefined || aj.saturacion_estrellas === null || !isFinite(Number(aj.saturacion_estrellas)) ? satG : Number(aj.saturacion_estrellas);
            const haySat = Math.abs(satG - 1) >= 1e-3 || Math.abs(satE - 1) >= 1e-3;
            const cR = this._tablasCurvas(curvas), hayCurvas = !!(curvas && (curvas.rgb || curvas.r || curvas.g || curvas.b));
            const hR = new Uint32Array(256), hG = new Uint32Array(256), hB = new Uint32Array(256), hL = new Uint32Array(256);
            const est = this.estrellas, peq = this.pequenas;
            // Estrellas aparte: la capa con sus propias tablas (temperatura sobre negro) y el tamaño
            let capa = this.capa ? this.capa.data : null;
            let cRt = null, cGt = null, cBt = null;
            if (capa) {
                const tc = this._tablasPrevias(aj, [0, 0, 0]);
                cRt = tc[0]; cGt = tc[1]; cBt = tc[2];
                const t = Number(aj.tamano_estrellas);
                const tamano = isFinite(t) ? Math.min(100, Math.max(0, t)) / 100 : 1;
                if (tamano < 1) capa = this._reducida(tamano);      // el tamaño va antes que el color, como en el servidor
            }
            // Saturación en HSV (V y H fijos), sin crear arrays por píxel: deja el resultado en `rgb3`
            const rgb3 = new Float32Array(3);
            const saturar = (r, g, b, gan) => {
                const V = r > g ? (r > b ? r : b) : (g > b ? g : b);
                const mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
                if (V <= 0 || V <= mn) { rgb3[0] = r; rgb3[1] = g; rgb3[2] = b; return; }
                const S = (V - mn) / V;
                if (gan * S > 1) gan = 1 / S;       // la saturación no pasa de 1 (como el clip del servidor)
                rgb3[0] = V - gan * (V - r); rgb3[1] = V - gan * (V - g); rgb3[2] = V - gan * (V - b);
            };
            for (let i = 0, j = 0; i < n; i++, j += 4) {
                let r = tR[src[j]], g = tG[src[j + 1]], b = tB[src[j + 2]];
                if (scnr > 0) {
                    let exceso = g - (r + b) / 2;
                    if (exceso > 0) {
                        if (!scnrEstrellas && !capa) exceso *= 1 - est[i] / 255;
                        g -= scnr * exceso;
                        if (g < 0) g = 0;
                    }
                }
                if (haySat) {
                    // Sin capas, la saturación de las estrellas va por la máscara de las pequeñas; con
                    // capas el objeto lleva la general y la capa la suya
                    const pe = capa ? 0 : peq[i] / 255;
                    saturar(r, g, b, satG * (1 - pe) + satE * pe); r = rgb3[0]; g = rgb3[1]; b = rgb3[2];
                }
                if (capa) {
                    let er = cRt[capa[j]], eg = cGt[capa[j + 1]], eb = cBt[capa[j + 2]];
                    if (scnr > 0 && scnrEstrellas) {
                        const exceso = eg - (er + eb) / 2;
                        if (exceso > 0) { eg -= scnr * exceso; if (eg < 0) eg = 0; }
                    }
                    if (Math.abs(satE - 1) >= 1e-3) { saturar(er, eg, eb, satE); er = rgb3[0]; eg = rgb3[1]; eb = rgb3[2]; }
                    // Pantalla: 1 - (1 - objeto)(1 - estrellas)
                    r = 1 - (1 - r) * (1 - er); g = 1 - (1 - g) * (1 - eg); b = 1 - (1 - b) * (1 - eb);
                }
                r = r < 0 ? 0 : r > 1 ? 1 : r; g = g < 0 ? 0 : g > 1 ? 1 : g; b = b < 0 ? 0 : b > 1 ? 1 : b;
                let r8 = Math.round(r * 255), g8 = Math.round(g * 255), b8 = Math.round(b * 255);
                hR[r8]++; hG[g8]++; hB[b8]++; hL[(r8 + g8 + b8) / 3 | 0]++;
                if (hayCurvas) { r8 = cR[0][Math.round(r * 1023)]; g8 = cR[1][Math.round(g * 1023)]; b8 = cR[2][Math.round(b * 1023)]; }
                dst[j] = r8; dst[j + 1] = g8; dst[j + 2] = b8; dst[j + 3] = 255;
            }
            this.ctx.putImageData(this.salida, 0, 0);
            if (conFirma && this.firma) this.ctx.drawImage(this.firma, 0, 0, this.ancho, this.alto);
            return { r: hR, g: hG, b: hB, l: hL };
        }
    }

    window.Retoque = Retoque;
})();
