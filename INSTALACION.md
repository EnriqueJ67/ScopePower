# Instalar ScopePower en un PC nuevo

## 1. Copiar la carpeta

Copia la carpeta `ScopePower` completa al PC (por OneDrive o a mano). No hace
falta Python instalado: el instalador lo pone.

Ficheros que NO están en git y conviene copiar del PC antiguo:

- `config.ini` (si no existe, la app lo crea desde `config.example.ini` y abre el asistente
  de configuración; ver el punto 4)
- `secretos.ini` (contraseña FTP, clave de Astrometry, secreto de activación)
- `calibraciones\` (darks y flat maestros, si valen para la misma cámara)

## 2. Ejecutar el instalador

Doble clic en `instalar_scopepower.bat` (pide permisos de administrador). Hace,
con internet:

1. Instala Python 3.11 si no está (winget o descarga de python.org).
2. Crea un entorno propio en `%LOCALAPPDATA%\ScopePower\venv` (fuera de OneDrive).
3. Instala las librerías de `requirements.txt` (opencv, astropy, photutils, ...).
4. Abre el puerto 5000 en el firewall para que el móvil pueda conectar, y concede a los
   usuarios el permiso "Cambiar la hora del sistema" (para poner el PC en hora desde el
   móvil; hace efecto al volver a iniciar sesión en Windows).
5. Crea el acceso directo **ScopePower** en el escritorio.
6. Instala ASTAP y su catálogo de estrellas si faltan: pregunta cuál (D05 100 MB, D20 400 MB o
   D50 900 MB) y lo baja de SourceForge a `%LOCALAPPDATA%\ScopePowerstap`. La opción
   **A) Ayúdame a elegir** pregunta la focal y el sensor de la cámara, calcula el campo y recomienda
   el catálogo (la focal queda apuntada en `config.ini`).
   Conviene hacerlo aquí, en casa y con internet: en el campo el PC suele estar en la wifi del
   SynScan, sin salida a internet. Se puede repetir con `herramientas\instalar_astap_consola.py`.
7. Comprueba ASCOM, drivers, ASTAP, Stellarium, SynScan Pro y la voz en español.
8. Ofrece arrancar ScopePower: la app se abre como usuario normal (no como administrador) y, como
   falta configurarla, abre el navegador del PC con la bienvenida y el asistente de configuración.

`instalar_scopepower.bat /comprobar` repite solo la comprobación.

## 3. Instalar a mano lo que la comprobación marque como FALTA

| Programa | Para qué | Dónde |
|---|---|---|
| ASCOM Platform 7 | Comunicación con montura y cámara | https://ascom-standards.org |
| Driver ASCOM SynScan App (1.4+) | Montura SynScan (y `ClearSyncTo`) | https://skywatcher.com/download/software/ascom-driver/ |
| App SynScan Pro (Windows) | La montura se controla a través de la app | https://skywatcher.com/download/software/synscan-app/ (en `C:\SynScanPro`) |
| Driver ASCOM PlayerOne | Cámara | https://player-one-astronomy.com/service/software/ |
| Cámara sin ASCOM (opcional) | En Configuración aparecen las Player One, ZWO ASI, QHYCCD, ToupTek (y su familia: Altair, Ogma, Risingcam, SVBony, Omegon, MallinCam) y webcams conectadas como «por SDK nativo (sin ASCOM)»: más rápidas que por ASCOM y necesarias para el modo en vivo y el seguimiento. La DLL de cada marca no viene con ScopePower: se usa la del driver ASCOM de la marca, N.I.N.A., SharpCap o ASIStudio (otra ruta: `[CamaraSDK] dll_zwo`, `dll_qhy` o `dll_touptek` en config.ini; las QHY necesitan además el driver USB «All-in-One» de QHY). Webcams: por DirectShow, sin instalar nada; tamaño de píxel en `[CamaraSDK] pixel_webcam_um` (3,0 por defecto) | — |
| ASTAP + base de datos D50 | Plate solving y alineado del apilado | Lo instalan `instalar_scopepower.bat` o el asistente de configuración de la web (en `%LOCALAPPDATA%\ScopePowerstap`); a mano: https://www.hnsky.org/astap.htm (en `C:\Program Files\Astap`) |
| Stellarium (ya no hace falta) | El GoTo resuelve los nombres con el catálogo local (`static/atlas` + `alias_objetos.csv`). Solo si `lanzar_stellarium_al_arrancar = true` | https://stellarium.org |
| Voz en español | Avisos hablados y, según el nivel de humor (botón «Humor» de la barra), frases de `frases_humor.csv` | Configuración de Windows > Hora e idioma > Voz > Añadir voces > Español. Las voces añadidas así (Pablo, Laura, Sabina...) no las ve SAPI hasta ejecutar `herramientasctivar_voces_windows.bat` como administrador |

## 4. Arrancar

Acceso directo **ScopePower** del escritorio (o `lanzar_scopepower.bat`). La
ventana que se abre es la consola del servidor; el log está en `logs\scopepower.log`.
Al arrancar, el PC dicta por el altavoz la dirección a escribir en el móvil.

**Primera vez (config.ini recién creado):** la app abre sola el navegador del PC; la web pide crear el usuario y la contraseña y abre
el **asistente de configuración**, que guía paso a paso: focal del telescopio, driver de la montura
y de la cámara (con una prueba de conexión real), exposición y ganancia, ASTAP con su catálogo de
estrellas (si falta, lo descarga de SourceForge y lo instala sin permisos de administrador en
`%LOCALAPPDATA%\ScopePowerstap`) y la ubicación del observatorio. Se puede volver a pasar cuando
se quiera con el botón **Asistente de configuración** de la página Configuración.

## 5. Activación

La app tiene 30 días de prueba en cada PC nuevo. Para activarla: en el PC nuevo
abre `/activar`, copia el identificador, y en el PC de desarrollo ejecuta

    python herramientas\generar_clave_activacion.py

que usa el `activacion_secreto` de `secretos.ini`. Pega la clave en `/activar`.

## Actualizar el PC de campo

Con la carpeta del PC de campo compartida como `\\PC_MINIBESTIA\ScopePower`, basta con ejecutar
`copiar_a_campo.bat` desde la carpeta de desarrollo cada vez que haya una versión nueva. Copia el
código, plantillas y recursos, y respeta lo que es propio del PC de campo: `config.ini`,
`secretos.ini`, `calibraciones\`, `capturas\`, `logs\`, la galería (`static\fotos\`) y el plan de la
noche (`datos\plan.json`: la lista de objetos y lo que ya está hecho; se puede exportar desde Apilado).
Para probar la copia en otra carpeta: `copiar_a_campo.bat D:\prueba`.
Si `requirements.txt` ha cambiado, ejecutar después `instalar_scopepower.bat` en el PC de campo.

## Arrancar ScopePower al encender el mini PC

En el PC de campo, ejecutar una vez `poner_en_inicio.bat` (doble clic). Crea un acceso directo a
`lanzar_scopepower.bat` en la carpeta de Inicio del usuario, así que al iniciar sesión arranca el
servidor y el altavoz dicta la IP hasta que alguien se conecta. `poner_en_inicio.bat quitar` lo deshace.

Para que el mini PC arranque sin teclado ni pantalla hace falta que Windows inicie sesión solo:
ejecutar `netplwiz`, desmarcar "Los usuarios deben escribir su nombre y contraseña para usar el
equipo" y escribir la contraseña del usuario. Con eso, encender el mini PC basta para tener la app
lista, y el botón "Reiniciar el equipo" de la app vuelve a dejarla arrancada.
