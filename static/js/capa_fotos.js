// static/js/capa_fotos.js — Fotos de la galería sobre el cielo del planetario (ver fondo_planetario.py).
//
// Cada apilado o mosaico guardado con su posición se pinta deformado a su huella real: las
// cuatro esquinas de la imagen se proyectan con el motor y la imagen se dibuja con la afín
// que lleva tres de ellas a su sitio (en campos de pocos grados la perspectiva no se nota).
//
// Lo comparten el planetario de la app (templates/planetario.html) y el visor público de la
// galería. Por eso aquí no hay Jinja, ni fetch, ni textos para el usuario: la lista de fotos
// llega ya resuelta a cargar() y la URL de cada imagen la da la página con urlFoto(f).
//
//   crearCapaFotos({ lienzo, api, urlFoto, opacidad, alPulsar })
//     lienzo    <canvas> encima del mapa, del mismo tamaño que el contenedor del motor
//     api       el motor (projectEquatorial); cada página lo importa de donde lo sirva
//     urlFoto   f => URL de la imagen de la foto f
//     opacidad  () => 0–1 con que se pintan (opcional, 0,7 si no se da)
//     alPulsar  (foto, {x, y}) => … al pulsar el lienzo sobre una foto (opcional)
//   → { cargar(lista), pintar(vista, w, h), enPunto(x, y), fotos() }
//
// Cada foto de la lista trae nombre, modo, esquinas [[ra, dec] × 4] (en el orden (0,0), (ancho,0),
// (ancho,alto), (0,alto) de la imagen) y, a campo completo, esquinas_completa.

export function crearCapaFotos({ lienzo, api, urlFoto, opacidad, alPulsar }) {
    let fotos = [];          // [{...foto, img, esquinasUsadas}]
    let pintadas = [];       // [{foto, pts}] de la última llamada a pintar()

    // Las imágenes ya cargadas se reutilizan por nombre|modo, así refrescar la lista no las vuelve a pedir
    function cargar(lista) {
        const previas = new Map(fotos.map(f => [f.nombre + "|" + f.modo, f.img]));
        fotos = (lista ?? []).map(f => {
            const clave = f.nombre + "|" + f.modo;
            let img = previas.get(clave);
            if (!img) {
                img = new Image();
                img.decoding = "async";
                img.src = urlFoto(f);
            }
            // Con la versión a campo completo la huella es la zona útil del FITS; con el JPG, la del JPG
            return { ...f, img, esquinasUsadas: f.modo === "completa" && f.esquinas_completa ? f.esquinas_completa : f.esquinas };
        });
        pintadas = [];
        return fotos;
    }

    function pintar(vista, w, h) {
        const ctx = lienzo.getContext("2d");
        if (lienzo.width !== w || lienzo.height !== h) { lienzo.width = w; lienzo.height = h; }
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, w, h);
        ctx.globalAlpha = opacidad ? opacidad() : 0.7;
        pintadas = [];
        for (const f of fotos) {
            if (!f.img.complete || !f.img.naturalWidth) continue;
            const pts = [];
            let ok = true;
            for (const [raDeg, decDeg] of (f.esquinasUsadas || f.esquinas)) {
                let p = null;
                try { p = api.projectEquatorial({ raDeg, decDeg, frame: "ICRS" }, vista, w, h); } catch (e) {}
                if (!p || !isFinite(p.x) || !isFinite(p.y) || Math.abs(p.x) > 3 * w || Math.abs(p.y) > 3 * h) { ok = false; break; }
                pts.push(p);
            }
            if (!ok) continue;
            // Esquinas de la imagen: (0,0) → pts[0], (ancho,0) → pts[1], (0,alto) → pts[3]
            const iw = f.img.naturalWidth, ih = f.img.naturalHeight;
            const a = (pts[1].x - pts[0].x) / iw, b = (pts[1].y - pts[0].y) / iw;
            const c = (pts[3].x - pts[0].x) / ih, d = (pts[3].y - pts[0].y) / ih;
            const lado = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
            if (lado < 3 || lado > 6 * w) continue;      // ni un punto ni un campo que no cabe en el mapa
            ctx.setTransform(a, b, c, d, pts[0].x, pts[0].y);
            try { ctx.drawImage(f.img, 0, 0, iw, ih); } catch (e) {}
            pintadas.push({ foto: f, pts });
        }
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = 1;
    }

    // Punto dentro del cuadrilátero (trazado de rayo sobre los cuatro vértices proyectados)
    function dentro(pts, x, y) {
        let dentroDe = false;
        for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
            const { x: xi, y: yi } = pts[i], { x: xj, y: yj } = pts[j];
            if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) dentroDe = !dentroDe;
        }
        return dentroDe;
    }

    // La foto pintada en el último pintar() que contiene el punto (x, y) del lienzo, o null.
    // Si hay varias superpuestas, la pintada encima (la última).
    function enPunto(x, y) {
        for (let i = pintadas.length - 1; i >= 0; i--) {
            if (dentro(pintadas[i].pts, x, y)) return pintadas[i].foto;
        }
        return null;
    }

    if (typeof alPulsar === "function") {
        lienzo.addEventListener("click", e => {
            const r = lienzo.getBoundingClientRect();
            const x = e.clientX - r.left, y = e.clientY - r.top;
            const f = enPunto(x, y);
            if (f) alPulsar(f, { x, y });
        });
    }

    return { cargar, pintar, enPunto, fotos: () => fotos };
}
