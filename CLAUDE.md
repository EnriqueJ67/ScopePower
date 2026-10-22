# Reglas de trabajo en ScopePower

Guía para quien trabaje en este repositorio con un asistente de código (Claude Code u otro).
Complementa a [CONTRIBUTING.md](CONTRIBUTING.md), que explica el entorno y las pruebas.

## Flujo: nunca directo a master

Todo cambio va en una rama y llega a `master` por pull request, también los del autor.
El workflow «Comprobar» de GitHub Actions debe pasar antes de fusionar.

1. Partir de `master` al día: `git switch master && git pull`.
2. Rama con nombre corto en español que diga qué hace: `arreglo-centrado-goto`,
   `camara-svbony`, `readme-capturas`. Una rama por tema.
3. Commits en español, con un asunto de una línea y un cuerpo que explique el porqué.
4. Antes de abrir el PR: `python -m compileall -q .`, pyflakes sin nombres sin definir y las
   pruebas de `herramientas/` que toquen lo cambiado. Si el cambio se ve en la web, probarlo en
   una copia con los simuladores ASCOM.
5. `git push -u origin <rama>` y abrir el pull request con la plantilla del repo (qué cambia,
   cómo se ha probado). Esperar al workflow.
6. Fusionar solo con el workflow en verde y, si hay revisores, con su visto bueno. Después borrar
   la rama y volver a `master` con `git pull`.

`master` está protegida en GitHub: no admite subidas directas ni forzadas, solo pull requests
con la comprobación `comprobar` en verde, y la regla vale también para el administrador. Además,
un gancho local `pre-commit` rechaza commits en `master` para no descubrirlo al subir; no se salta
con `--no-verify`.

Si el propio workflow se rompe y ningún PR puede pasar (por ejemplo, GitHub cambia algo de
`windows-latest`), el arreglo va igualmente en una rama y por PR. Para fusionarlo, el dueño del
repositorio desactiva un momento «Do not allow bypassing the above settings» en
Settings > Branches > `master`, fusiona el PR saltándose la comprobación, vuelve a activar la
casilla y lo cuenta en la descripción del PR. Nunca se quita la protección para subir directo.

## Qué no cambia

- Código, comentarios, commits y documentación en **español**. La app habla español e inglés: todo
  texto nuevo que vea u oiga el usuario va en español dentro de `_()` (Python, plantillas y JS) y se
  traduce en `traducciones/en/LC_MESSAGES/scopepower.po`; antes del PR, `herramientas/traducciones.py
  extraer` y `compilar`, y `herramientas/probar_traducciones.py` en verde. Reglas en CONTRIBUTING.md.
- `config.ini`, `secretos.ini`, `capturas/`, `calibraciones/`, `datos/` y `logs/` no van a git.
  Una clave de configuración nueva se añade a `config.example.ini` con su comentario.
- Nada de sistemas de activación o licencias de uso: la app es libre (GPL-3.0).
- Nada de nombres de máquinas, rutas de usuario ni datos personales en el código ni en la
  documentación.

## Probar sin equipo

Simuladores ASCOM (`ASCOM.Simulator.Telescope` y `ASCOM.Simulator.Camera`) en una copia de la
carpeta con su propio `config.ini` y `anunciar_ip = false`. Las pruebas
sintéticas: `herramientas/probar_servidor.py` (servidor web y pantallas, obligatoria si cambia
`requirements.txt`), `herramientas/probar_mosaico.py`, `herramientas/probar_seguimiento_simulado.py` y
`herramientas/probar_puesta_estacion.py` (montura ecuatorial falsa con error conocido, sin ASTAP).
