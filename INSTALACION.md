# Instalar ScopePower en un PC nuevo

## 1. Copiar la carpeta

Copia la carpeta `ScopePower` completa al PC (por OneDrive o a mano). No hace
falta Python instalado: el instalador lo pone.

Ficheros que NO están en git y conviene copiar del PC antiguo:

- `config.ini` (si no existe, el instalador lo crea desde `config.example.ini`)
- `secretos.ini` (contraseña FTP, clave de Astrometry, secreto de activación)
- `calibraciones\` (darks y flat maestros, si valen para la misma cámara)

## 2. Ejecutar el instalador

Doble clic en `instalar_scopepower.bat` (pide permisos de administrador). Hace,
con internet:

1. Instala Python 3.11 si no está (winget o descarga de python.org).
2. Crea un entorno propio en `%LOCALAPPDATA%\ScopePower\venv` (fuera de OneDrive).
3. Instala las librerías de `requirements.txt` (opencv, astropy, photutils, ...).
4. Abre el puerto 5000 en el firewall para que el móvil pueda conectar.
5. Crea el acceso directo **ScopePower** en el escritorio.
6. Comprueba ASCOM, drivers, ASTAP, Stellarium, SynScan Pro y la voz en español.

`instalar_scopepower.bat /comprobar` repite solo la comprobación.

## 3. Instalar a mano lo que la comprobación marque como FALTA

| Programa | Para qué | Dónde |
|---|---|---|
| ASCOM Platform 7 | Comunicación con montura y cámara | https://ascom-standards.org |
| Driver ASCOM SynScan App (1.4+) | Montura SynScan (y `ClearSyncTo`) | https://skywatcher.com/download/software/ascom-driver/ |
| App SynScan Pro (Windows) | La montura se controla a través de la app | https://skywatcher.com/download/software/synscan-app/ (en `C:\SynScanPro`) |
| Driver ASCOM PlayerOne | Cámara | https://player-one-astronomy.com/service/software/ |
| ASTAP + base de datos D50 | Plate solving y alineado del apilado | https://www.hnsky.org/astap.htm (en `C:\Program Files\Astap`) |
| Stellarium (opcional) | Coordenadas de objetos sin internet | https://stellarium.org |
| Voz en español | Avisos hablados | Configuración de Windows > Hora e idioma > Voz > Añadir voces > Español |

## 4. Arrancar

Acceso directo **ScopePower** del escritorio (o `lanzar_scopepower.bat`). La
ventana que se abre es la consola del servidor; el log está en `logs\scopepower.log`.
Al arrancar, el PC dicta por el altavoz la dirección a escribir en el móvil.

## 5. Activación

La app tiene 30 días de prueba en cada PC nuevo. Para activarla: en el PC nuevo
abre `/activar`, copia el identificador, y en el PC de desarrollo ejecuta

    python herramientas\generar_clave_activacion.py

que usa el `activacion_secreto` de `secretos.ini`. Pega la clave en `/activar`.

## Actualizar el PC de campo

Con la carpeta del PC de campo compartida como `\\PC_MINIBESTIA\ScopePower`, basta con ejecutar
`copiar_a_campo.bat` desde la carpeta de desarrollo cada vez que haya una versión nueva. Copia el
código, plantillas y recursos, y respeta lo que es propio del PC de campo: `config.ini`,
`secretos.ini`, `calibraciones\`, `capturas\`, `logs\` y la galería (`static\fotos\`).
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
