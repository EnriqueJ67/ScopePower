# Colaborar en ScopePower

Gracias por querer echar una mano. ScopePower es un proyecto personal que se usa de verdad en el
campo, así que lo que más ayuda es: probarlo con tu equipo y contar qué pasa, arreglar fallos y
mejorar lo que ya hay antes que añadir cosas nuevas.

## Idioma

El código, los comentarios, los commits y la documentación están en **español** y así siguen.
Los nombres de módulos y funciones también (`apilado.py`, `centrado.py`...). Las *issues* y los
*pull requests* pueden escribirse en español o en inglés.

*In English:* code, comments and commits are in Spanish, but the app itself speaks Spanish and
English (Settings > Language). Issues and pull requests are welcome in either language. To improve
the English texts, edit `traducciones/en/LC_MESSAGES/scopepower.po` (see below).

## Textos de la interfaz y traducción

La app se usa en español o en inglés (`[Interfaz] idioma` de `config.ini`, ver `idioma.py`). Los
textos se escriben **en español** en el código, marcados para traducir, y la traducción inglesa
vive en `traducciones/en/LC_MESSAGES/scopepower.po`. Todo texto nuevo que vea u oiga el usuario
(pantalla, voz del PC, mensajes de error que llegan a la web, texto pintado en una imagen) va
marcado:

| Dónde | Cómo |
|---|---|
| Python | `from idioma import _, ngettext, N_` · `voz.decir(_("Telescopio conectado"))` · `_("No conecta: {e}", e=e)` |
| Plural | `ngettext("{n} foto", "{n} fotos", total)` (`{n}` se rellena solo) |
| Constante de módulo | `TIPOS = {"galaxy": N_("Galaxia")}` y `_(TIPOS[k])` al mostrarla |
| Plantilla (HTML) | `{{ _("Guardar") }}`, en atributos `title="{{ _('Cerrar') }}"`, con marcadores `{{ _("Quedan {n}", n=x) }}` |
| JavaScript (también en los `<script>` de las plantillas) | `_("Guardando...")` · `_("Quedan {n} fotos", { n: 3 })` · `ngettext("{n} foto", "{n} fotos", n)` |
| Tabla en JavaScript | `const TIPOS = { gx: N_("Galaxia") }` y `_(TIPOS[c])` al pintarla |

Reglas:

- **Marcadores siempre con llaves y nombre**: `{n}`, `{e}`, `{objeto}`. Nunca f-strings, `%`,
  concatenaciones ni plantillas `` `...` `` dentro de `_()`: la frase entera va en un solo texto para
  que se pueda traducir con su orden propio.
- **Nunca `_()` en una constante de módulo**: se quedaría en el idioma del arranque. Se marca con
  `N_()` y se traduce al usarla.
- **`_` no puede ser una variable** en un módulo que traduce (`for _ in ...`, `a, _ = ...`): tapa la
  función. Se usa `__` u otro nombre.
- Los decimales que se dicen o se pintan pasan por `idioma.fmt_num(x, dec)` (coma en español, punto
  en inglés) y las fechas por `idioma.fmt_fecha()`; en JavaScript `fmtNum()`, `fmtHora()`,
  `fmtFecha()` e `IDIOMA.lang` para la voz del navegador.
- Un dato que es a la vez clave y texto (tipos de objeto, nombres de proceso) sigue guardándose en
  español; solo se traduce al pintarlo (`_(tipo)`).
- Los registros (`log.info`, `print`) **no** se traducen: el log siempre en español.
- No se compara en JavaScript con un texto que manda el servidor (`mensaje.startsWith("...")`): se
  añade un campo de estado al JSON.

Después de tocar textos:

```bash
python herramientas/traducciones.py extraer     # actualiza el .pot y el .po
python herramientas/traducciones.py pendientes  # qué falta por traducir
python herramientas/traducciones.py compilar    # .mo y static/i18n/en.js
python herramientas/probar_traducciones.py      # lo mismo que comprueba el workflow
```

El `.po`, el `.mo` y `static/i18n/en.js` se suben al repositorio (el mini PC de campo no compila
nada). Para buscar textos que se hayan quedado sin marcar: `python herramientas/marcar_textos.py
listar fichero...`. Con `idioma_depurar = true` en `[Interfaz]`, lo que falta por traducir sale como
`¿¿texto??` en pantalla y por voz. El vocabulario de la traducción inglesa está en
[traducciones/GLOSARIO.md](traducciones/GLOSARIO.md).

Los ficheros de datos con prosa tienen su versión inglesa al lado, con el mismo formato y el
sufijo `_en`: `frases_humor_en.csv`, `descripciones_objetos_en.csv`, `catalogo_objetos_en.csv`,
`info_objetos/*_en.txt` y `constelaciones/*_en.csv`. Si falta uno, se usa el español.

## Montar el entorno

Hace falta Windows 10 u 11 (la app usa COM, ASCOM y la voz de Windows).

Lo más sencillo es `instalar_scopepower.bat`: instala Python 3.11 si falta, crea un entorno en
`%LOCALAPPDATA%\ScopePower\venv`, instala `requirements.txt`, abre el puerto 5000 y deja ASTAP.

A mano, desde la carpeta del repositorio:

```bash
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.ini config.ini
.venv\Scripts\python ScopePower.py
```

La primera vez la web pide crear el usuario y abre el asistente de configuración.

## Probar sin telescopio ni cámara

ASCOM Platform trae simuladores. En `config.ini`:

```ini
[Dispositivos]
driver_telescopio = ASCOM.Simulator.Telescope
driver_camara = ASCOM.Simulator.Camera
exposicion_captura = 1
```

Con eso se recorre toda la interfaz, se hace GoTo y se apilan fotos sintéticas. El plate solving
necesita fotos reales del cielo, así que el centrado automático fallará con el simulador (es
normal). Para el seguimiento de objetos hay una montura y una cámara falsas en Python:
`[Seguimiento] simulador = true`.

Dos consejos:

- Ejecuta la app desde una **copia** de la carpeta con su propio `config.ini`: escribe resultados
  en `static/`, `capturas/`, `datos/` y `logs/`, y así no pisas tu instalación de uso.
- Pon `anunciar_ip = false` en la copia para que no anuncie la IP por voz.

Las pruebas que no necesitan equipo están en `herramientas/`:

```bash
python herramientas\probar_mosaico.py
python herramientas\probar_seguimiento_simulado.py --segundos 10
```

`probar_cometa.py` necesita ASTAP y un FITS real de la galería; `medir_*.py` y
`comprobar_equipo.py` necesitan la cámara o la montura de verdad.

## Cómo está organizado el código

Flask y todas las rutas están en `ScopePower.py`; cada parte grande tiene su módulo (montura,
cámaras, apilado, procesado, centrado, planetario, modos especiales). El README tiene un mapa.
La configuración se lee de `config.ini` (plantilla en `config.example.ini`) y los secretos de
`secretos.ini`; ninguno de los dos va a git.

`ScopePower.py` tiene unas 6.500 líneas. Partirlo en *blueprints* de Flask es trabajo pendiente;
mientras tanto, lo nuevo va en un módulo propio y en `ScopePower.py` solo quedan las rutas.

Si un cambio añade una clave de configuración, añádela también a `config.example.ini` con un
comentario, y a INSTALACION.md si afecta a la instalación.

## Fallos

Abre una *issue* con la plantilla «Fallo». Lo que hace falta para poder reproducirlo:

- Montura y cómo está conectada (driver ASCOM, SynScan Pro, UDP directo).
- Cámara y driver (ASCOM o SDK nativo, modelo).
- Versión de ScopePower (está en la página Configuración) y de Windows.
- El trozo de `logs/scopepower.log` del momento del fallo. El log lleva la hora; pega desde un
  minuto antes.
- Si el fallo es del apilado o del procesado, una foto FITS con la que pase (por enlace, no en
  la *issue*).

## Pull requests

1. Crea una rama desde `master` con un nombre corto (`arreglo-centrado`, `camara-svbony`).
2. Un PR por tema. Si arreglas dos cosas distintas, dos PR.
3. Antes de abrirlo: `python -m compileall -q .` y `python -m pyflakes <ficheros tocados>` sin
   nombres sin definir, y las pruebas de `herramientas/` que toquen lo que cambias.
4. Cuenta en el PR qué has probado y con qué equipo (o con el simulador). Si cambia algo de la
   interfaz, una captura de pantalla ayuda.
5. Si el cambio merece aparecer en el historial, añade una línea en `CHANGELOG.md` bajo la versión
   en curso.

El workflow de GitHub Actions compila todo, pasa pyflakes y ejecuta las pruebas sin equipo en cada
PR. Lo que necesita montura o cámara se prueba a mano.

## Licencia

Al contribuir aceptas que tu código se distribuya bajo la GPL-3.0 del proyecto. No incluyas
código ni datos con licencias incompatibles; los catálogos de `static/atlas` tienen la suya
(CC-BY-SA) y hay que respetarla.
