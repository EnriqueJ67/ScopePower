# Colaborar en ScopePower

Gracias por querer echar una mano. ScopePower es un proyecto personal que se usa de verdad en el
campo, así que lo que más ayuda es: probarlo con tu equipo y contar qué pasa, arreglar fallos y
mejorar lo que ya hay antes que añadir cosas nuevas.

## Idioma

El código, los comentarios, los mensajes de la interfaz, los commits y la documentación están en
**español** y así siguen. Los nombres de módulos y funciones también (`apilado.py`,
`centrado.py`...). Las *issues* y los *pull requests* pueden escribirse en español o en inglés.

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
- Pon `subir_ftp = false` y `anunciar_ip = false` en la copia para que no suba previews ni hable.

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
