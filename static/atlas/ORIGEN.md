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

Cambio local: en `src/core/coordinates.js`, `comets.js` y `solar-system.js` el import de
`astronomy-engine` apunta a `../../vendor/astronomy-engine-2.1.19.esm.js` en vez de al nombre
del paquete, para no depender de un *import map* (Chrome < 89 no los soporta). Al actualizar
el motor hay que repetir ese cambio.

- `assets/landscapes/guereins/`: el paisaje Guéreins de Stellarium (12 teselas HiPS, ~400 KB),
  GPL-2.0-or-later (licenses/Stellarium-GPL-2.0.txt). Se dibuja con `setLandscape()` y se quita
  con el interruptor «Paisaje» del planetario.

No se incluye el suplemento de catálogos de Stellarium ni las teselas DSS: el planetario
funciona 100 % sin Internet con lo que hay aquí. Para actualizar, copiar de nuevo
`src/`, `vendor/`, `data/comets.js`, `assets/milky-way.webp`, `assets/landscapes/guereins/`, los
tres catálogos y regenerar `estrellas-brillantes.js` a partir de `data/bright-sky.json`.
