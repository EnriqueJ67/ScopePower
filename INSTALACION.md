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
