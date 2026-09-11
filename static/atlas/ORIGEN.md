# Celestia Atlas (motor del planetario)

Copia del motor de planetario offline **Celestia Atlas**, de acocalypso
(https://github.com/acocalypso/celestia_atlas), commit 573330b del 2026-09-08.
Es el mismo motor que usa Touch-N-Stars. Código MIT (ver LICENSE); los datos
tienen sus propias licencias (ver THIRD_PARTY_NOTICES.md):

- `hyg-star-catalog.js`: estrellas HYG v4.1, CC-BY-SA 4.0 (licenses/HYG-CC-BY-SA-4.0.md).
- `estrellas-brillantes.js`: las 130 estrellas con nombre propio (Sirio, Vega…) de `data/bright-sky.json`,
  que HYG excluye a propósito; generado con `window.STAR_DATA = [...]`.
- `dso-catalog.js`: OpenNGC + Messier, CC-BY-SA 4.0.
- `western-constellations.js`: líneas de constelaciones de la cultura occidental de Stellarium.
- `data/comets.js`: elementos de cometas del Minor Planet Center.
- `vendor/astronomy-engine-2.1.19.esm.js`: efemérides (MIT).

No se incluye el suplemento de Stellarium (GPL) ni las teselas DSS: el planetario
funciona 100 % sin Internet con lo que hay aquí. Para actualizar, copiar de nuevo
`src/`, `vendor/`, `data/comets.js`, `assets/milky-way.webp`, los tres catálogos y regenerar
`estrellas-brillantes.js` a partir de `data/bright-sky.json`.
