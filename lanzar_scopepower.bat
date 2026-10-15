@echo off
setlocal
chcp 65001 >nul
title ScopePower

REM ============================================================================
REM  lanzar_scopepower.bat  -  arranca el servidor de ScopePower
REM
REM  Usa el entorno creado por instalar_scopepower.bat (%LOCALAPPDATA%\ScopePower\venv).
REM  Si no existe, prueba con Python 3.11 del sistema y, en su defecto, con anaconda3.
REM  La ventana se queda abierta: es la consola del servidor (el log completo esta
REM  en logs\scopepower.log). Para cerrar la app usa "Cerrar Aplicacion" en la web
REM  o cierra esta ventana. "Reiniciar ScopePower" en la web sale con codigo 3
REM  y este bat la vuelve a arrancar sin cerrar la ventana.
REM  Si ya habia otro ScopePower en marcha, la app sale con codigo 4 y esta
REM  ventana se cierra sola a los 10 s.
REM  "Actualizar desde GitHub" (Configuracion) sale con codigo 5: este bat ejecuta
REM  actualizar_desde_github.bat con la app parada y la vuelve a arrancar.
REM
REM  lanzar_scopepower.bat /test  -> solo muestra que Python usaria y sale
REM ============================================================================

cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PY="

if exist "%LOCALAPPDATA%\ScopePower\venv\Scripts\python.exe" set "PY=%LOCALAPPDATA%\ScopePower\venv\Scripts\python.exe"
if not defined PY (
    py -3.11 -c "import cv2, astropy, flask" >nul 2>&1 && set "PY=py -3.11"
)
if not defined PY (
    if exist "%USERPROFILE%\anaconda3\python.exe" (
        "%USERPROFILE%\anaconda3\python.exe" -c "import cv2, astropy, flask" >nul 2>&1 && set "PY=%USERPROFILE%\anaconda3\python.exe"
    )
)
if not defined PY (
    echo No se encuentra un Python con las dependencias de ScopePower.
    echo Ejecuta primero instalar_scopepower.bat
    pause
    exit /b 1
)

if /I "%~1"=="/test" (
    echo Python que se usaria: %PY%
    exit /b 0
)

if not exist "config.ini" (
    copy "config.example.ini" "config.ini" >nul
    echo config.ini creado a partir de config.example.ini: al entrar en la web se abrira el asistente de configuracion.
)

REM Con esta variable la app sabe que puede pedir un reinicio saliendo con codigo 3
set "SCOPEPOWER_LANZADOR=1"

:arrancar
echo Arrancando ScopePower... (esta ventana es la consola del servidor)
echo.
%PY% ScopePower.py
set "CODIGO=%ERRORLEVEL%"
if "%CODIGO%"=="3" (
    echo.
    echo Reiniciando ScopePower...
    ping -n 3 127.0.0.1 >nul
    goto arrancar
)
if "%CODIGO%"=="5" (
    echo.
    echo Actualizando ScopePower desde GitHub...
    call "%~dp0actualizar_desde_github.bat" /lanzador
    echo.
    echo Arrancando ScopePower de nuevo...
    ping -n 3 127.0.0.1 >nul
    goto arrancar
)
if "%CODIGO%"=="4" (
    echo.
    echo ScopePower ya estaba en marcha en este equipo. Esta ventana se cierra en 10 s.
    ping -n 11 127.0.0.1 >nul
    exit /b 0
)
echo.
echo ScopePower ha terminado (codigo %CODIGO%). Revisa logs\scopepower.log si ha sido un error.
pause
