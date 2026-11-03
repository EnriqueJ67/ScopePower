// Prueba de static/js/capa_fotos.js sin navegador: node herramientas/probar_capa_fotos.mjs
//
// Lienzo e imágenes falsos y un motor que proyecta RA/Dec como si fueran píxeles. Comprueba que
// cargar() reutiliza las imágenes ya cargadas, que pintar() dibuja solo las fotos que caben y que
// enPunto() devuelve la foto cuyo cuadrilátero contiene el punto (la de encima si hay varias).
import { crearCapaFotos } from "../static/js/capa_fotos.js";

let fallos = 0;
function comprobar(condicion, texto) {
    console.log((condicion ? "  ok  " : "  FALLO ") + texto);
    if (!condicion) fallos++;
}

// Imagen falsa: cargada al instante, 100×50 píxeles
globalThis.Image = class {
    constructor() { this.complete = true; this.naturalWidth = 100; this.naturalHeight = 50; this.src = ""; }
};
const llamadas = { drawImage: 0, transforms: [] };
const ctx = {
    setTransform(...m) { llamadas.transforms.push(m); }, clearRect() {}, drawImage() { llamadas.drawImage++; },
    globalAlpha: 1,
};
const lienzo = { width: 0, height: 0, getContext: () => ctx, addEventListener() {} };
// Motor falso: x = RA, y = Dec, sin proyección
const api = { projectEquatorial: ({ raDeg, decDeg }) => ({ x: raDeg, y: decDeg }) };
const urls = [];
const capa = crearCapaFotos({ lienzo, api, urlFoto: f => { urls.push(f.nombre + "|" + f.modo); return "/foto/" + f.nombre; },
    opacidad: () => 0.5 });

// Dos fotos: un cuadrado de 100×50 en (10,10) y otro girado 90° encima de la esquina del primero
const lista = [
    { nombre: "a", modo: "jpg", esquinas: [[10, 10], [110, 10], [110, 60], [10, 60]] },
    { nombre: "b", modo: "completa", esquinas: [[0, 0], [1, 0], [1, 1], [0, 1]],
      esquinas_completa: [[150, 20], [150, 120], [100, 120], [100, 20]] },
    { nombre: "c", modo: "jpg", esquinas: [[500, 500], [501, 500], [501, 501], [500, 501]] },   // un punto: no se pinta
];
comprobar(capa.cargar(lista).length === 3, "cargar() devuelve las tres fotos");
comprobar(capa.fotos()[1].esquinasUsadas === lista[1].esquinas_completa, "a campo completo usa esquinas_completa");
comprobar(urls.length === 3, "pide una URL por foto nueva");
comprobar(capa.enPunto(50, 30) === null, "sin pintar, enPunto() no encuentra nada");

capa.pintar({}, 400, 300);
comprobar(lienzo.width === 400 && lienzo.height === 300, "ajusta el lienzo al tamaño del mapa");
comprobar(llamadas.drawImage === 2, "pinta las dos fotos que caben (no el punto): " + llamadas.drawImage);
comprobar(ctx.globalAlpha === 1, "deja la opacidad a 1 al terminar");
comprobar(capa.enPunto(50, 30)?.nombre === "a", "enPunto() dentro de la primera foto");
comprobar(capa.enPunto(120, 100)?.nombre === "b", "enPunto() dentro de la foto girada");
comprobar(capa.enPunto(105, 30)?.nombre === "b", "donde se solapan gana la pintada encima");
comprobar(capa.enPunto(5, 5) === null, "fuera de todas, null");
comprobar(capa.enPunto(500.5, 500.5) === null, "la foto que no se pintó no cuenta");

// Al refrescar la lista se reutilizan las imágenes de las fotos que ya estaban
const imgA = capa.fotos()[0].img;
capa.cargar([lista[0], { nombre: "d", modo: "jpg", esquinas: lista[2].esquinas }]);
comprobar(capa.fotos()[0].img === imgA, "reutiliza la imagen ya cargada por nombre|modo");
comprobar(urls.length === 4 && urls[3] === "d|jpg", "solo pide la URL de la foto nueva");
comprobar(capa.enPunto(50, 30) === null, "tras cargar() hay que volver a pintar");

console.log(fallos ? `❌ ${fallos} fallos` : "✅ capa_fotos.js en orden");
process.exit(fallos ? 1 : 0);
