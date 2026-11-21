@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Actualizar ScopePower

REM ============================================================================
REM  actualizar_desde_github.bat  -  pone al dia ScopePower desde GitHub
REM
REM  No hace falta Git ni tener cuenta en GitHub: solo Internet. Baja el ZIP de la
REM  ultima version publicada (release) y copia sus ficheros encima de esta carpeta.
REM  Sirve igual si la carpeta es una copia, un ZIP descomprimido o un clon de git.
REM
REM  Uso:  actualizar_desde_github.bat           -> a la ultima version publicada
REM        actualizar_desde_github.bat /volver   -> deshace la ultima actualizacion
REM        actualizar_desde_github.bat /si       -> sin pedir confirmacion
REM        actualizar_desde_github.bat X.zip     -> con un ZIP ya descargado (sin Internet);
REM                                                 vale arrastrar el ZIP encima de este .bat
REM
REM  - No toca lo que es propio de este PC: config.ini, secretos.ini, calibraciones\,
REM    capturas\, logs\, la galeria (static\fotos\), datos\, videos\... La lista completa
REM    esta en aplicar_actualizacion.py (EXCLUIDOS_*).
REM  - Antes de cambiar nada comprueba que lo descargado es un ScopePower entero y guarda
REM    lo que va a sobrescribir en _backup\<version anterior>\ (solo la ultima copia).
REM    Si algo falla a mitad, restaura esa copia: la version anterior queda como estaba.
REM  - Si requirements.txt cambia, instala las dependencias con el mismo Python que usa
REM    lanzar_scopepower.bat.
REM  - Si ScopePower esta en marcha, pide cerrarlo y lo vuelve a arrancar al terminar.
REM
REM  El trabajo lo hace aplicar_actualizacion.py, desde una copia en %TEMP% para que nada
REM  cambie bajo sus pies. En una instalacion anterior a este actualizador (sin ese
REM  fichero) se baja de la ultima release: basta con poner este .bat en la carpeta.
REM
REM  Modo interno (lo usa la propia app, boton "Actualizar" de Configuracion):
REM        /lanzador  -> sin preguntas ni pausas; hace lo pedido en datos\actualizar_pendiente.txt,
REM                      deja lo que escribe en logs\actualizacion.log y vuelve a arrancar
REM                      ScopePower. Lo llama lanzar_scopepower.bat cuando la app sale con codigo 5.
REM
REM  Para pruebas: SCOPEPOWER_API_GITHUB y SCOPEPOWER_RAW_GITHUB sustituyen las direcciones
REM  de GitHub (ver herramientas\probar_actualizar.py).
REM ============================================================================

REM La carpeta de ScopePower es la del .bat; en la copia temporal (ver abajo) llega por variable
if not defined SCOPEPOWER_RAIZ set "SCOPEPOWER_RAIZ=%~dp0"
cd /d "%SCOPEPOWER_RAIZ%"
set "RAIZ=%CD%"
if "%RAIZ:~-1%"=="\" set "RAIZ=%RAIZ:~0,-1%"
REM La actualizacion sobrescribe este mismo .bat mientras se ejecuta, y cmd lee los .bat
REM por trozos: se sigue desde una copia en %TEMP%. La llamada y el exit van en la misma
REM linea para que cmd no vuelva a leer este fichero despues de la copia.
if not defined ACT_COPIA_TEMP (
    set "ACT_COPIA_TEMP=1"
    copy /Y "%~f0" "%TEMP%\scopepower_actualizar.bat" >nul 2>&1
    if exist "%TEMP%\scopepower_actualizar.bat" call "%TEMP%\scopepower_actualizar.bat" %* & if errorlevel 2 (exit /b 2) else if errorlevel 1 (exit /b 1) else exit /b 0
)

REM ---- Opciones ----
set "LANZADOR=" & set "SIN_PREGUNTAR=" & set "OPCIONES="
for %%A in (%*) do (
    if /I "%%~A"=="/si"       set "OPCIONES=!OPCIONES! --si" & set "SIN_PREGUNTAR=1"
    if /I "%%~A"=="/volver"   set "OPCIONES=!OPCIONES! --volver"
    if /I "%%~A"=="/lanzador" set "LANZADOR=1"
    if /I "%%~xA"==".zip" if exist "%%~fA" set "OPCIONES=!OPCIONES! --zip "%%~fA""
)

REM ---- El Python de ScopePower: el mismo que elige lanzar_scopepower.bat ----
set "PYTHONIOENCODING=utf-8"
set "PY="
set "PY_ARGS="
if exist "%LOCALAPPDATA%\ScopePower\venv\Scripts\python.exe" set "PY=%LOCALAPPDATA%\ScopePower\venv\Scripts\python.exe"
if not defined PY (
    py -3.11 -c "import cv2, astropy, flask" >nul 2>&1 && set "PY=py" && set "PY_ARGS=-3.11"
)
if not defined PY (
    if exist "%USERPROFILE%\anaconda3\python.exe" (
        "%USERPROFILE%\anaconda3\python.exe" -c "import cv2, astropy, flask" >nul 2>&1 && set "PY=%USERPROFILE%\anaconda3\python.exe"
    )
)
if not defined PY (
    set "MOTIVO=No se encuentra el Python de ScopePower. Ejecuta primero instalar_scopepower.bat"
    goto :fin_error
)

REM ---- El actualizador, en una copia fuera de la carpeta ----
set "TRABAJO=%TEMP%\scopepower_actualizar"
if not exist "%TRABAJO%\" mkdir "%TRABAJO%"
set "SCRIPT=%TRABAJO%\aplicar_actualizacion.py"
del "%SCRIPT%" >nul 2>&1
if exist "%RAIZ%\aplicar_actualizacion.py" (
    copy /Y "%RAIZ%\aplicar_actualizacion.py" "%SCRIPT%" >nul
) else (
    call :bajar_actualizador
)
if not exist "%SCRIPT%" (
    set "MOTIVO=No se pudo bajar el actualizador: no hay conexion a Internet, o la ultima version publicada todavia no lo trae."
    goto :fin_error
)

if defined LANZADOR goto :lanzador

REM ---- A mano: pregunta, actualiza y ensena el resultado ----
"%PY%" %PY_ARGS% "%SCRIPT%" interactivo --raiz "%RAIZ%" %OPCIONES%
set "CODIGO=%ERRORLEVEL%"
echo.
if not defined SIN_PREGUNTAR pause
exit /b %CODIGO%

REM ---- Pedido desde la web: todo a logs\actualizacion.log (la web lo ensena despues) ----
:lanzador
if not exist "%RAIZ%\logs\" mkdir "%RAIZ%\logs"
"%PY%" %PY_ARGS% "%SCRIPT%" lanzador --raiz "%RAIZ%" > "%RAIZ%\logs\actualizacion.log" 2>&1
type "%RAIZ%\logs\actualizacion.log"
goto :relanzar

REM ============================================================================
:bajar_actualizador
REM Instalacion anterior a este actualizador: aplicar_actualizacion.py se baja de la ultima
REM release publicada. Las direcciones van por variables de entorno para no pelear con las comillas.
echo  Bajando el actualizador de la ultima version publicada en GitHub...
set "ACT_API=https://api.github.com"
if defined SCOPEPOWER_API_GITHUB set "ACT_API=%SCOPEPOWER_API_GITHUB%"
set "ACT_RAW=https://raw.githubusercontent.com"
if defined SCOPEPOWER_RAW_GITHUB set "ACT_RAW=%SCOPEPOWER_RAW_GITHUB%"
set "ACT_REPO=EnriqueJ67/ScopePower"
set "ACT_SCRIPT=%SCRIPT%"
set "PS_BAJAR=$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = 'Tls12'; $c = @{ 'User-Agent' = 'ScopePower-actualizador' }; $r = Invoke-RestMethod -Uri ($env:ACT_API + '/repos/' + $env:ACT_REPO + '/releases/latest') -Headers $c -TimeoutSec 15; Invoke-WebRequest -Uri ($env:ACT_RAW + '/' + $env:ACT_REPO + '/' + $r.tag_name + '/aplicar_actualizacion.py') -OutFile $env:ACT_SCRIPT -UseBasicParsing -Headers $c -TimeoutSec 60"
powershell -NoProfile -Command "%PS_BAJAR%" >nul 2>&1
exit /b 0

:fin_error
echo.
echo  [ERROR] %MOTIVO%
if defined LANZADOR (
    if not exist "%RAIZ%\logs\" mkdir "%RAIZ%\logs"
    >"%RAIZ%\logs\actualizacion.log" echo  [ERROR] %MOTIVO%
    >>"%RAIZ%\logs\actualizacion.log" echo [RESULTADO] error mantenida
    goto :relanzar
)
echo.
if not defined SIN_PREGUNTAR pause
exit /b 1

:relanzar
REM Este cmd puede ser el de lanzar_scopepower.bat, que estaba esperando a este script y cuyo
REM fichero acaba de cambiar: no se vuelve a el. Se arranca un lanzador nuevo en su propia
REM ventana y "exit" (sin /b) cierra este cmd entero.
echo.
echo  Arrancando ScopePower de nuevo...
start "ScopePower" /D "%RAIZ%" "%RAIZ%\lanzar_scopepower.bat"
exit
