@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Instalacion de ScopePower

REM ============================================================================
REM  instalar_scopepower.bat  -  prepara un PC Windows limpio para ScopePower
REM
REM   1. Instala Python 3.11 si no esta (winget o descarga de python.org)
REM   2. Crea un entorno virtual FUERA de OneDrive (%LOCALAPPDATA%\ScopePower\venv)
REM   3. Instala las dependencias de requirements.txt
REM   4. Abre el puerto 5000 en el firewall para el movil
REM   5. Crea config.ini si no existe y el acceso directo del escritorio
REM   6. Comprueba ASCOM, drivers, ASTAP, Stellarium, SynScan Pro y la voz
REM
REM  Uso:  instalar_scopepower.bat            (instalacion completa; pide admin)
REM        instalar_scopepower.bat /comprobar (solo la comprobacion final)
REM ============================================================================

set "RAIZ=%~dp0"
set "RAIZ=%RAIZ:~0,-1%"
set "VENV=%LOCALAPPDATA%\ScopePower\venv"
set "PYVER=3.11"
set "PY_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"

if /I "%~1"=="/comprobar" goto :comprobar

REM ---- Pedir permisos de administrador (instalar Python y abrir el firewall) ----
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Se necesitan permisos de administrador. Se abrira una ventana nueva...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ======================================
echo   Instalacion de ScopePower
echo   Carpeta: %RAIZ%
echo ======================================
echo.

REM ---- 1) Python 3.11 ----
py -%PYVER% -c "import sys; print('Python', sys.version.split()[0])" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Python %PYVER% ya esta instalado.
    goto :venv
)
echo Python %PYVER% no esta instalado. Intentando con winget...
where winget >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements --scope machine
)
py -%PYVER% -c "print(1)" >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :venv
echo winget no disponible o fallo. Descargando el instalador de python.org...
set "PY_EXE=%TEMP%\python-3.11-instalador.exe"
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_EXE%'"
if not exist "%PY_EXE%" (
    echo ERROR: no se pudo descargar Python. Comprueba la conexion a internet e instala Python 3.11 a mano desde https://www.python.org
    goto :fin_error
)
echo Instalando Python %PYVER% (silencioso, para todos los usuarios)...
"%PY_EXE%" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0
del "%PY_EXE%" >nul 2>&1
py -%PYVER% -c "print(1)" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python %PYVER% sigue sin estar disponible. Cierra esta ventana, abre otra y vuelve a ejecutar el instalador.
    goto :fin_error
)
echo [OK] Python %PYVER% instalado.

REM ---- 2) Entorno virtual fuera de OneDrive ----
:venv
if not exist "%VENV%\Scripts\python.exe" (
    echo Creando el entorno en %VENV% ...
    py -%PYVER% -m venv "%VENV%"
    if not exist "%VENV%\Scripts\python.exe" (
        echo ERROR: no se pudo crear el entorno virtual.
        goto :fin_error
    )
)
echo [OK] Entorno: %VENV%

REM ---- 3) Dependencias ----
echo Instalando dependencias (puede tardar unos minutos la primera vez)...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
"%VENV%\Scripts\python.exe" -m pip install -r "%RAIZ%\requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: fallo la instalacion de alguna dependencia. Revisa los mensajes de arriba.
    goto :fin_error
)
REM pywin32 necesita un paso extra para registrar sus DLL (COM/ASCOM)
"%VENV%\Scripts\python.exe" "%VENV%\Scripts\pywin32_postinstall.py" -install >nul 2>&1
echo [OK] Dependencias instaladas.

REM ---- 4) Firewall: el movil tiene que llegar al puerto 5000 ----
netsh advfirewall firewall show rule name="ScopePower 5000" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    netsh advfirewall firewall add rule name="ScopePower 5000" dir=in action=allow protocol=TCP localport=5000 >nul
    echo [OK] Regla de firewall creada para el puerto 5000.
) else (
    echo [OK] Regla de firewall ya existente.
)

REM ---- 5) config.ini, carpetas y acceso directo ----
if not exist "%RAIZ%\config.ini" (
    copy "%RAIZ%\config.example.ini" "%RAIZ%\config.ini" >nul
    echo [AVISO] config.ini creado a partir de config.example.ini: revisa drivers, focal y observatorio en Configuracion.
)
for %%D in (capturas calibraciones logs static\fotos) do if not exist "%RAIZ%\%%D" mkdir "%RAIZ%\%%D"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\ScopePower.lnk'); $s.TargetPath='%RAIZ%\lanzar_scopepower.bat'; $s.WorkingDirectory='%RAIZ%'; $s.IconLocation='%RAIZ%\scopepower.ico'; $s.Description='Arrancar ScopePower'; $s.Save()"
echo [OK] Acceso directo 'ScopePower' creado en el escritorio.

REM ---- 6) Comprobacion final ----
:comprobar
echo.
echo ======================================
echo   Comprobacion del equipo
echo ======================================
if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" "%RAIZ%\herramientas\comprobar_equipo.py"
) else (
    py -%PYVER% "%RAIZ%\herramientas\comprobar_equipo.py"
)
echo.
echo Lo que este script NO instala (hazlo a mano si aparece como FALTA arriba):
echo   - ASCOM Platform 7:        https://ascom-standards.org
echo   - Driver ASCOM SynScan App: https://skywatcher.com/download/software/ascom-driver/
echo   - App SynScan Pro:          https://skywatcher.com/download/software/synscan-app/
echo   - Driver ASCOM PlayerOne:   https://player-one-astronomy.com/service/software/
echo   - ASTAP + base D50:         https://www.hnsky.org/astap.htm
echo   - Stellarium (opcional):    https://stellarium.org
echo   - Voz en espanol: Configuracion de Windows ^> Hora e idioma ^> Voz ^> Anadir voces
echo.
echo Arranca la app con el acceso directo 'ScopePower' del escritorio o con lanzar_scopepower.bat
echo.
pause
exit /b 0

:fin_error
echo.
pause
exit /b 1
