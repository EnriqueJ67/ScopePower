@echo off
setlocal
chcp 65001 >nul
title ScopePower - simulacion de carga

REM ============================================================================
REM  simular_carga.bat  -  simula una sesion de apilado para medir el consumo
REM
REM  Lanza herramientas\simular_carga.py con el mismo Python que usa ScopePower.
REM  Apunta los mAh del cargador antes de empezar y al terminar (1 hora).
REM  Los argumentos se pasan tal cual, p. ej.:  simular_carga.bat --minutos 30
REM ============================================================================

cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PY="

if exist "%LOCALAPPDATA%\ScopePower\venv\Scripts\python.exe" set "PY=%LOCALAPPDATA%\ScopePower\venv\Scripts\python.exe"
if not defined PY (
    py -3.11 -c "import cv2, astropy" >nul 2>&1 && set "PY=py -3.11"
)
if not defined PY (
    if exist "%USERPROFILE%\anaconda3\python.exe" (
        "%USERPROFILE%\anaconda3\python.exe" -c "import cv2, astropy" >nul 2>&1 && set "PY=%USERPROFILE%\anaconda3\python.exe"
    )
)
if not defined PY (
    echo No se encuentra un Python con las dependencias de ScopePower.
    echo Ejecuta primero instalar_scopepower.bat
    pause
    exit /b 1
)

%PY% herramientas\simular_carga.py %*
echo.
pause
