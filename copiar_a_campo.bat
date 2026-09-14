@echo off
REM ============================================================================
REM  copiar_a_campo.bat - Copia ScopePower al PC de campo (carpeta compartida).
REM
REM  Se puede ejecutar cada vez que se actualiza la version. Copia el codigo,
REM  las plantillas y los recursos, y NO toca lo que es propio del PC de campo:
REM    - config.ini y secretos.ini (solo se copian si alli no existen todavia)
REM    - calibraciones\ (darks y flats hechos con la camara de campo)
REM    - capturas\, logs\ y static\fotos\ (fotos y galeria del PC de campo)
REM
REM  Uso:  copiar_a_campo.bat            -> copia a \\PC_MINIBESTIA\ScopePower
REM        copiar_a_campo.bat D:\otra    -> copia a otra carpeta (pruebas)
REM ============================================================================
setlocal
set "ORIGEN=%~dp0"
set "ORIGEN=%ORIGEN:~0,-1%"
set "DESTINO=\\PC_MINIBESTIA\ScopePower"
if not "%~1"=="" set "DESTINO=%~1"

echo.
echo  ScopePower - copia al PC de campo
echo  Origen : %ORIGEN%
echo  Destino: %DESTINO%
echo.

if not exist "%DESTINO%\" (
    echo  [ERROR] No se puede acceder a %DESTINO%
    echo          Comprueba que el PC de campo esta encendido, en la misma red
    echo          y con la carpeta compartida. Si pide usuario y contrasena,
    echo          abre primero la carpeta en el Explorador de Windows.
    echo.
    pause
    exit /b 1
)

REM Opciones comunes de robocopy: sin listar ficheros ni carpetas (solo el
REM resumen de cada paso), 2 reintentos de 3 s si un fichero esta en uso
set "OPC=/NFL /NDL /NJH /NP /R:2 /W:3"
set "ERRORES=0"

echo  [1/7] Codigo y ficheros de la raiz
robocopy "%ORIGEN%" "%DESTINO%" *.py *.bat *.md *.txt *.csv config.example.ini %OPC%
if errorlevel 8 set "ERRORES=1"

echo  [2/7] Plantillas (templates\)
robocopy "%ORIGEN%\templates" "%DESTINO%\templates" /MIR %OPC%
if errorlevel 8 set "ERRORES=1"

echo  [3/7] Recursos fijos (static\ sin fotos ni temporales)
robocopy "%ORIGEN%\static" "%DESTINO%\static" *.png *.ico *.wav *.js manifest.json %OPC% /XF captura*.* resultado_apilado*.* ultima_captura.jpg scopepower_objetos.jpg
if errorlevel 8 set "ERRORES=1"
robocopy "%ORIGEN%\static\js" "%DESTINO%\static\js" /MIR %OPC%
if errorlevel 8 set "ERRORES=1"
robocopy "%ORIGEN%\static\css" "%DESTINO%\static\css" /MIR %OPC%
if errorlevel 8 set "ERRORES=1"
REM Motor del planetario offline (Celestia Atlas): codigo, catalogos y Via Lactea
robocopy "%ORIGEN%\static\atlas" "%DESTINO%\static\atlas" /MIR %OPC%
if errorlevel 8 set "ERRORES=1"

echo  [4/7] Herramientas, constelaciones e informacion de objetos
robocopy "%ORIGEN%\herramientas" "%DESTINO%\herramientas" /MIR %OPC%
if errorlevel 8 set "ERRORES=1"
robocopy "%ORIGEN%\constelaciones" "%DESTINO%\constelaciones" /MIR %OPC%
if errorlevel 8 set "ERRORES=1"
robocopy "%ORIGEN%\info_objetos" "%DESTINO%\info_objetos" /E %OPC%
if errorlevel 8 set "ERRORES=1"
REM Elementos orbitales de cometas y asteroides: por si el PC de campo no tiene Internet
if exist "%ORIGEN%\datos\mpc\" robocopy "%ORIGEN%\datos\mpc" "%DESTINO%\datos\mpc" /E %OPC%

echo  [5/7] Configuracion y secretos (solo si alli no existen)
if not exist "%DESTINO%\config.ini" (
    copy /Y "%ORIGEN%\config.ini" "%DESTINO%\config.ini" >nul
    echo        config.ini copiado por primera vez: revisa drivers, focal y observatorio en el PC de campo
) else (
    echo        config.ini ya existe en el destino: no se toca
)
if not exist "%DESTINO%\secretos.ini" (
    if exist "%ORIGEN%\secretos.ini" copy /Y "%ORIGEN%\secretos.ini" "%DESTINO%\secretos.ini" >nul
    echo        secretos.ini copiado por primera vez
) else (
    echo        secretos.ini ya existe en el destino: no se toca
)

echo  [6/7] Carpetas de datos del PC de campo (se crean vacias si faltan)
for %%D in (capturas calibraciones logs static\fotos static\timelapse static\continuo) do (
    if not exist "%DESTINO%\%%D\" mkdir "%DESTINO%\%%D"
)

echo  [7/7] Limpieza de restos antiguos en el destino
if exist "%DESTINO%\__pycache__\" rmdir /S /Q "%DESTINO%\__pycache__"
if exist "%DESTINO%\favicon.ico" del /Q "%DESTINO%\favicon.ico"
if exist "%DESTINO%\scopepower.ico" del /Q "%DESTINO%\scopepower.ico"

echo.
if "%ERRORES%"=="1" (
    echo  [AVISO] Alguna copia ha fallado. Revisa los mensajes de arriba.
) else (
    echo  Copia terminada. Si hay dependencias nuevas, ejecuta instalar_scopepower.bat
    echo  en el PC de campo ^(pip install -r requirements.txt^) antes de lanzar la app.
)
echo.
pause
endlocal
