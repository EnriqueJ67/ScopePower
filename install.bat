@echo off
setlocal

echo ======================================
echo   Instalación de dependencias ScopePower
echo ======================================

REM --- 1) Detectar lanzador de Python ---
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "LAUNCHER=py -3"
) else (
    set "LAUNCHER=python"
)

echo Usando: %LAUNCHER%
echo.

REM --- 2) Comprobar que realmente tenemos Python ---
%LAUNCHER% --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: No se encontró un Python válido con "%LAUNCHER%".
    echo Asegúrate de tener instalado Python (no solo el alias de Microsoft Store),
    echo o desactiva el alias de Python en:
    echo   Configuración > Aplicaciones > Alias de ejecución de aplicaciones.
    goto FIN
)

REM --- 3) Actualizar pip ---
echo Actualizando pip...
%LAUNCHER% -m pip install --upgrade pip
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Falló la actualizaci n de pip.
    goto FIN
)

REM --- 4) Instalar todas las librerías necesarias ---
echo.
echo Instalando dependencias globales...
%LAUNCHER% -m pip install --upgrade ^
    numpy ^
    astropy ^
    astroquery ^
    opencv-python ^
    flask ^
    unidecode ^
    pywin32 ^
    matplotlib ^
    wikipedia ^
    bcrypt ^
    requests ^
    watchdog
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Falló la instalación de alguna dependencia.
    goto FIN
)

echo.
echo ======================================
echo   ¡Instalación completada con éxito!
echo ======================================
echo Ahora puedes ejecutar tu aplicación con:
echo   %LAUNCHER% scopepower.py

:FIN
echo.
echo Pulsa cualquier tecla para cerrar…
pause>nul
endlocal
