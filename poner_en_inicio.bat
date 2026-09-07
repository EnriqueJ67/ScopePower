@echo off
REM ============================================================================
REM  poner_en_inicio.bat - Arrancar ScopePower al iniciar sesion en Windows.
REM
REM  Crea un acceso directo a lanzar_scopepower.bat en la carpeta de Inicio del
REM  usuario actual (shell:startup). Hay que ejecutarlo UNA VEZ en el PC de campo,
REM  con el usuario con el que arranca Windows. A partir de ahi, al encender el
REM  mini PC arranca ScopePower solo y el altavoz dicta la IP hasta que te conectas.
REM
REM  Uso:  poner_en_inicio.bat           -> crea el acceso directo
REM        poner_en_inicio.bat quitar    -> lo elimina
REM
REM  Para que arranque sin teclado ni pantalla, Windows debe iniciar sesion solo:
REM  ejecuta "netplwiz", desmarca "Los usuarios deben escribir su nombre y
REM  contrasena" y escribe la contrasena del usuario una vez.
REM ============================================================================
setlocal
set "CARPETA=%~dp0"
set "CARPETA=%CARPETA:~0,-1%"
set "INICIO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if not "%~2"=="" set "INICIO=%~2"
set "ACCESO=%INICIO%\ScopePower.lnk"

echo.
if /I "%~1"=="quitar" (
    if exist "%ACCESO%" (
        del /Q "%ACCESO%"
        echo  ScopePower ya no arranca con Windows ^(borrado %ACCESO%^)
    ) else (
        echo  ScopePower no estaba en el inicio de Windows.
    )
    goto :fin
)

if not exist "%CARPETA%\lanzar_scopepower.bat" (
    echo  [ERROR] No encuentro lanzar_scopepower.bat junto a este script.
    goto :fin
)
if not exist "%INICIO%\" mkdir "%INICIO%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%ACCESO%');" ^
  "$s.TargetPath = '%CARPETA%\lanzar_scopepower.bat';" ^
  "$s.WorkingDirectory = '%CARPETA%';" ^
  "$s.IconLocation = '%CARPETA%\static\scopepower.ico';" ^
  "$s.Description = 'Arranca el servidor de ScopePower';" ^
  "$s.Save()"

if exist "%ACCESO%" (
    echo  ScopePower arrancara al iniciar sesion en Windows.
    echo  Acceso directo: %ACCESO%
    echo  Arranca:        %CARPETA%\lanzar_scopepower.bat
    echo.
    echo  Para que el mini PC entre solo sin teclado: netplwiz y desmarcar
    echo  "Los usuarios deben escribir su nombre y contrasena".
) else (
    echo  [ERROR] No se pudo crear el acceso directo en %INICIO%
)

:fin
echo.
pause
endlocal
