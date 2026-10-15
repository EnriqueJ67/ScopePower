# ScopePower

Control de un telescopio y apilado de fotos en tiempo real desde el móvil. Un mini PC junto
a la montura ejecuta un servidor web (Flask) y habla con la montura y la cámara por ASCOM o por el
SDK nativo del fabricante; desde el móvil o la tablet, en la misma wifi, se apunta, se enfoca, se
apila y se revela sin tocar el PC.

> **English summary.** ScopePower turns a small Windows PC next to the mount into a web server
> you control from your phone: voice GoTo with automatic plate-solve centring (ASTAP), live
> stacking with darks and flats, an offline planetarium, a night planner, mosaics, comet stacking,
> solar/lunar guiding and object tracking. It targets Sky-Watcher SynScan mounts (ASCOM or direct
> UDP) and Player One, ZWO, QHY, ToupTek-family and webcam cameras (ASCOM or native SDK). Windows
> only, Spanish UI. Licensed under the GPL-3.0; see [Licencia](#licencia) for third-party data.

Proyecto personal, probado en el campo con una montura Sky-Watcher AZ GoTo, un Evoguide 50 y una
cámara Player One Saturn-C. Toda la interfaz, el código y los comentarios están en español.

![Nebulosa de la Laguna: 30 fotos de 20 s apiladas en vivo y reveladas en ScopePower](docs/capturas/resultado-lagoon.webp)

| Apilado en tiempo real | Plan de la noche |
|---|---|
| ![Apilado en vivo](docs/capturas/apilado-en-vivo.webp) | ![Plan de la noche](docs/capturas/plan-de-la-noche.webp) |
| **Editor de la galería** | **Enfoque con máscara de Bahtinov** |
| ![Editor](docs/capturas/editor-galeria.webp) | ![Enfoque](docs/capturas/camara-enfoque-bahtinov.webp) |
| **Cámara de día: el Sol** | **Estadísticas y opciones del apilado** |
| ![Modo día](docs/capturas/camara-modo-dia-sol.webp) | ![Estadísticas](docs/capturas/apilado-estadisticas.webp) |

Más capturas en [docs/capturas](docs/capturas).

## Qué hace

- **GoTo por nombre o por voz** («ir a M31»), con catálogo local (OpenNGC, HYG, alias propios) y
  centrado automático por plate solving con ASTAP al terminar cada GoTo.
- **Apilado en vivo**: cada foto se alinea y se suma mientras se captura, con rechazo de las malas,
  darks y flats de biblioteca, detección de rastros de aviones y satélites y estirado multiescala.
- **Galería y editor**: revelado, recorte, giro y espejo, deconvolución, calibración de color con las
  estrellas, anotaciones de objetos NGC/IC y composición de cometas.
- **Planetario sin Internet** (motor Celestia Atlas) con la posición del telescopio y el encuadre de
  la cámara; «Esta noche» propone qué observar y el **plan de la noche** encadena objetos solo.
- **Modos especiales**: mosaico por teselas, apilado por el núcleo del cometa, seguimiento de
  objetos en movimiento con vídeo, foto y guiado de día por el disco del Sol o la Luna, timelapse.
- **Cámara**: foto de prueba, modo en vivo, enfoque asistido (Bahtinov y de día), darks y flats,
  biblioteca de calibración por exposición, ganancia y binning.
- **Montura**: pad de velocidades, alineación por puntos, límites de altura, aparcado, vigilante
  del seguimiento sidéreo, acceso directo por UDP al SynScan Wi-Fi además de ASCOM.
- **Avisos por voz** en el PC (con un nivel de humor ajustable), anuncio de la IP al arrancar,
  aviso de espacio en disco y punto de acceso wifi propio para el campo.

## Requisitos

- Windows 10 u 11 (usa COM, ASCOM y la voz de Windows; no funciona en Linux ni macOS).
- [ASCOM Platform 7](https://ascom-standards.org) y el driver ASCOM de la montura y de la cámara.
  Para la montura SynScan hace falta también la app SynScan Pro de Sky-Watcher.
- [ASTAP](https://www.hnsky.org/astap.htm) con una base de datos de estrellas (D05, D20 o D50).
  El instalador lo descarga si falta.
- Python 3.11. El instalador lo pone si no está.
- Un móvil o tablet en la misma red que el PC. Nada que instalar en el móvil: es una web.

Cámaras probadas: Player One Saturn-C por ASCOM y por SDK nativo. Están preparadas, pero sin
probar con hardware real, las ZWO ASI, QHYCCD, la familia ToupTek (Altair, Ogma, RisingCam,
SVBony, Omegon, MallinCam) y las webcams por DirectShow. Si tienes una y la pruebas, cuéntalo en
un *issue*.

## Instalación en tres pasos

1. Descarga o clona el repositorio en el PC que irá junto al telescopio.
2. Ejecuta `instalar_scopepower.bat` (pide permisos de administrador): instala Python, crea un
   entorno propio en `%LOCALAPPDATA%\ScopePower`, abre el puerto 5000 en el cortafuegos, instala
   ASTAP si falta y crea el acceso directo del escritorio.
3. Arranca ScopePower desde el acceso directo. La primera vez abre el navegador del PC para crear
   el usuario y lanza el asistente de configuración (focal, montura, cámara, ASTAP, ubicación).
   Después, desde el móvil, entra en la dirección que el PC dicta por el altavoz.

Los detalles (drivers, voz en español, arranque automático al encender el mini PC, actualización
del PC de campo) están en [INSTALACION.md](INSTALACION.md).

## Probarlo sin telescopio ni cámara

ASCOM Platform trae simuladores. En `config.ini` pon:

```ini
[Dispositivos]
driver_telescopio = ASCOM.Simulator.Telescope
driver_camara = ASCOM.Simulator.Camera
exposicion_captura = 1
```

Con eso se puede recorrer toda la interfaz, hacer GoTo, capturar y apilar fotos sintéticas. El
plate solving necesita fotos reales del cielo, así que el centrado automático fallará con el
simulador de cámara (es normal). Para el seguimiento de objetos hay además una montura y una cámara
falsas en Python (`[Seguimiento] simulador = true`).

Para desarrollar sin tocar la instalación de uso, conviene ejecutar la app desde una copia de la
carpeta con su propio `config.ini`: la app escribe resultados en `static/`, `capturas/` y `datos/`.

## Cómo está hecho

| Parte | Dónde |
|---|---|
| Servidor web y rutas | `ScopePower.py` (Flask) |
| Montura (ASCOM y UDP directo) | `hardware.py`, `montura_ejes.py`, `aparcado.py`, `alineacion.py` |
| Cámaras por SDK nativo | `camara_sdk.py`, `camara_nativa.py`, `camara_zwo.py`, `camara_qhy.py`, `camara_toupcam.py`, `camara_webcam.py` |
| Apilado y procesado | `apilado.py`, `apilado_incremental.py`, `procesado.py`, `darks.py`, `flats.py`, `rastros.py` |
| Plate solving y centrado | `centrado.py`, `orientacion_camara.py`, `rotacion_camara.py` |
| Planetario y catálogos | `static/atlas/`, `catalogo_offline.py`, `esta_noche.py`, `plan_sesion.py` |
| Modos especiales | `mosaico.py`, `cometa.py`, `seguimiento.py`, `disco.py`, `foto_dia.py`, `vivo_dia.py` |
| Interfaz | `templates/`, `static/css/`, `static/js/` |
| Herramientas de prueba y medida | `herramientas/` |

La configuración vive en `config.ini` (plantilla en `config.example.ini`) y los secretos
(contraseña FTP, clave de Astrometry.net, clave de sesión) en `secretos.ini`; ninguno de los dos va
a git. El log está en `logs/scopepower.log`.

## Seguridad

ScopePower está pensado para una red local (la wifi del campo o la de casa). Escucha en todas las
interfaces del PC con usuario y contraseña, pero sin HTTPS: **no lo expongas a Internet** ni lo
pongas detrás de un puerto abierto en el router.

## Colaborar

Las *issues* y los *pull requests* son bienvenidos: cómo montar el entorno, probar sin equipo y
qué adjuntar en un fallo está en [CONTRIBUTING.md](CONTRIBUTING.md). El código y los comentarios
se escriben en español. Los cambios de cada versión están en [CHANGELOG.md](CHANGELOG.md).

## Licencia

ScopePower se distribuye bajo la licencia **GNU GPL v3** (ver [LICENSE](LICENSE)).

Incluye componentes de terceros con sus propias licencias:

- `client.py`: cliente de Astrometry.net, © Dustin Lang, licencia BSD de 3 cláusulas.
- `static/atlas/`: motor de planetario Celestia Atlas (MIT), Astronomy Engine (MIT), catálogo de
  estrellas HYG v4.1 (CC-BY-SA 4.0), OpenNGC (CC-BY-SA 4.0), elementos de cometas del Minor Planet
  Center y líneas de constelaciones de Stellarium. Detalle en `static/atlas/ORIGEN.md` y
  `static/atlas/THIRD_PARTY_NOTICES.md`.
- `catalogo_objetos.csv`: coordenadas y magnitudes derivadas de OpenNGC (Mattia Verga,
  CC-BY-SA 4.0); los nombres y descripciones son propios.

Los datos de catálogo conservan su licencia CC-BY-SA aunque el código sea GPL.
