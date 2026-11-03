// static/js/atlas_catalogos.js — Catálogos, opciones y vista inicial del planetario (motor Celestia Atlas).
//
// Lo comparten el planetario de la app (templates/planetario.html) y el visor público de la
// galería, que se sirve desde un hosting sin ScopePower detrás. Por eso aquí no hay Jinja, ni
// fetch, ni textos para el usuario, y el motor (static/atlas/src/index.js) no se importa por
// ruta fija: cada página lo importa de donde lo sirva y lo pasa como `api`.

// Los catálogos vienen como globales (window.STAR_DATA, HYG_STAR_DATA, DSO_CATALOG_DATA,
// WESTERN_CONSTELLATIONS) de los scripts clásicos de static/atlas. STAR_DATA son las 130
// estrellas con nombre propio; HYG las excluye, así que se juntan.
export function catalogosDesdeGlobales(w = window) {
    const stars = [...(w.STAR_DATA ?? []), ...(w.HYG_STAR_DATA ?? [])].map(s => ({ ...s, id: s.id || s.name, name: s.name || s.id,
        aliases: [...(s.aliases ?? []), ...(s.alias ? [s.alias] : [])],
        raDeg: Number.isFinite(s.raDeg) ? s.raDeg : s.ra * 15, decDeg: Number.isFinite(s.decDeg) ? s.decDeg : s.dec,
        frame: s.frame || "ICRS", type: "Star" }));
    const catalog = (w.DSO_CATALOG_DATA ?? []).map(o => ({ ...o, name: o.name || o.id,
        typeCode: o.typeCode || o.objectType || o.type,
        angularSizeArcMin: o.angularSizeArcMin ?? { major: o.major, minor: o.minor } }));
    return { stars, catalog, constellations: w.WESTERN_CONSTELLATIONS ?? {} };
}

// Opciones fijas de viewer.setDisplayOptions. Los nombres del Sistema Solar (solarSystemNames)
// van traducidos, así que los pone cada página: viewer.setDisplayOptions({ ...OPCIONES_VISTA, solarSystemNames }).
export const OPCIONES_VISTA = Object.freeze({ cardinals: true, constellations: true, labels: true, horizon: true, hideBelowHorizon: true,
    milkyWay: true, solarSystem: true, comets: true, atmosphere: false,
    starMagnitudeLimit: 6.5, deepSkyMagnitudeLimit: 11.5, galaxyMagnitudeLimit: 11.5 });

// Vista inicial: mirando al sur, a 45° de altura, campo ancho.
// `api` es el motor (horizontalToEquatorial…), `observador` {latitudeDeg, longitudeDeg, elevationM}.
export function vistaInicial(api, viewer, observador, utcMs) {
    const centro = api.horizontalToEquatorial({ azimuthDeg: 180, altitudeDeg: 45 }, observador, utcMs, "ICRS");
    viewer.setView({ center: { raDeg: centro.raDeg, decDeg: centro.decDeg, frame: "ICRS" }, fovDeg: 90 });
}
