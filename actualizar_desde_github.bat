@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Actualizar ScopePower desde GitHub

REM ============================================================================
REM  actualizar_desde_github.bat  -  pone al dia ScopePower desde GitHub
REM
REM  Pensado para el mini PC de campo, en casa y con Internet. Hace falta tener
REM  instalado Git para Windows (https://git-scm.com/download/win).
REM
REM  Uso:  actualizar_desde_github.bat           -> a la ultima release (etiqueta v* mas alta)
REM        actualizar_desde_github.bat /master   -> a lo ultimo de la rama master (en desarrollo)
REM        actualizar_desde_github.bat /volver   -> a la version que habia antes de actualizar
REM        actualizar_desde_github.bat /si       -> sin pedir confirmacion
REM
REM  - Si la carpeta todavia no es un clon de git (copia hecha a mano o con
REM    copiar_a_campo.bat), ofrece convertirla: git init + origin + reset a la release.
REM  - No toca lo que es propio de este PC: config.ini, secretos.ini, calibraciones\,
REM    capturas\, logs\, static\fotos\ ni datos\. Estan ignorados por git; aqui se
REM    comprueba y se avisa si alguno estuviera seguido.
REM  - Antes de aplicar ensena los commits que van a entrar y pide confirmacion.
REM  - Si requirements.txt cambia, reinstala las dependencias con el pip del entorno
REM    de instalar_scopepower.bat (%LOCALAPPDATA%\ScopePower\venv).
REM  - Guarda la version anterior en datos\version_anterior.txt para poder volver.
REM  - Si ScopePower esta en marcha, ofrece cerrarlo y volver a arrancarlo al terminar.
REM
REM  Modos internos (los usa la propia app, boton "Actualizar desde GitHub" de la web):
REM        /lanzador  -> sin preguntas ni pausas; lee el destino de datos\actualizar_pendiente.txt
REM                      y deja todo lo que escribe en logs\actualizacion.log. Lo llama
REM                      lanzar_scopepower.bat cuando la app sale con codigo 5.
REM        /relanzar  -> al terminar arranca lanzar_scopepower.bat (app lanzada a mano con python)
REM
REM  Para pruebas: la variable de entorno SCOPEPOWER_REMOTO sustituye la URL de GitHub.
REM ============================================================================

REM La carpeta de ScopePower es la del .bat; en la copia temporal (ver abajo) llega por variable
if not defined SCOPEPOWER_RAIZ set "SCOPEPOWER_RAIZ=%~dp0"
cd /d "%SCOPEPOWER_RAIZ%"
set "RAIZ=%CD%"
REM git reset --hard sobrescribe este mismo .bat mientras se ejecuta, y cmd lee los .bat
REM por trozos: se sigue desde una copia en %TEMP%. La llamada y el exit van en la misma
REM linea para que cmd no vuelva a leer este fichero despues de la copia.
if not defined ACT_COPIA_TEMP (
    set "ACT_COPIA_TEMP=1"
    copy /Y "%~f0" "%TEMP%\scopepower_actualizar.bat" >nul 2>&1
    if exist "%TEMP%\scopepower_actualizar.bat" call "%TEMP%\scopepower_actualizar.bat" %* & if errorlevel 2 (exit /b 2) else if errorlevel 1 (exit /b 1) else exit /b 0
)
set "REMOTO=https://github.com/EnriqueJ67/ScopePower.git"
if defined SCOPEPOWER_REMOTO set "REMOTO=%SCOPEPOWER_REMOTO%"
set "VENV=%LOCALAPPDATA%\ScopePower\venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "FICHERO_ANTERIOR=datos\version_anterior.txt"
set "FICHERO_PENDIENTE=datos\actualizar_pendiente.txt"
set "LOG=logs\actualizacion.log"
set "PROTEGIDOS=config.ini secretos.ini calibraciones capturas logs static/fotos datos"

REM ---- Opciones ----
set "A_MASTER=" & set "SIN_PREGUNTAR=" & set "VOLVER=" & set "LANZADOR=" & set "RELANZAR="
for %%A in (%*) do (
    if /I "%%~A"=="/master"   set "A_MASTER=1"
    if /I "%%~A"=="/si"       set "SIN_PREGUNTAR=1"
    if /I "%%~A"=="/volver"   set "VOLVER=1"
    if /I "%%~A"=="/lanzador" set "LANZADOR=1"
    if /I "%%~A"=="/relanzar" set "RELANZAR=1"
)

REM ---- Modo lanzador: todo a logs\actualizacion.log (la web lo ensena despues) ----
if defined LANZADOR (
    set "SIN_PREGUNTAR=1"
    REM Sin ventanas de contrasena: en el mini PC no hay nadie delante
    set "GIT_TERMINAL_PROMPT=0"
    set "GCM_INTERACTIVE=never"
    if not defined ACT_REDIRIGIDO (
        if not exist logs mkdir logs
        set "ACT_REDIRIGIDO=1"
        call "%~f0" %* > "%LOG%" 2>&1
        set "CODIGO=!ERRORLEVEL!"
        type "%LOG%"
        exit /b !CODIGO!
    )
    echo [%DATE% %TIME%] Actualizacion pedida desde la web
    if exist "%FICHERO_PENDIENTE%" (
        set /p DESTINO_PEDIDO=<"%FICHERO_PENDIENTE%"
        del "%FICHERO_PENDIENTE%" >nul 2>&1
        echo Destino pedido: !DESTINO_PEDIDO!
        if /I "!DESTINO_PEDIDO!"=="master" set "A_MASTER=1"
        if /I "!DESTINO_PEDIDO!"=="volver" set "VOLVER=1"
    )
)

echo.
echo  ScopePower - actualizar desde GitHub
echo  Carpeta: %RAIZ%
echo.

REM ---- 1) Git instalado ----
where git >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] No se encuentra git. Instala "Git para Windows" desde
    echo          https://git-scm.com/download/win ^(sirven todas las opciones por defecto^)
    echo          y vuelve a ejecutar este script en una ventana nueva.
    goto :fin_error
)

REM ---- 2) Internet y acceso a GitHub ----
echo  Comprobando el acceso a GitHub...
set "TMP_LS=%TEMP%\scopepower_lsremote.txt"
git ls-remote --exit-code --heads "%REMOTO%" master >nul 2>"%TMP_LS%"
if errorlevel 1 (
    findstr /I /C:"Could not resolve" /C:"unable to access" /C:"Failed to connect" /C:"Timed out" /C:"Connection was reset" "%TMP_LS%" >nul
    if not errorlevel 1 (
        echo  [ERROR] No hay Internet o no se llega a github.com. Conecta el PC a una red con
        echo          salida a Internet ^(en el campo no la hay^) y vuelve a intentarlo.
    ) else (
        echo  [ERROR] GitHub no ha dejado leer el repositorio:
        type "%TMP_LS%"
        echo          Mientras el repositorio sea privado hay que identificarse una vez en este PC:
        echo          ejecuta este script a mano ^(con pantalla^) y Git abrira el navegador para
        echo          entrar en GitHub; la identificacion queda guardada para las siguientes veces.
    )
    del "%TMP_LS%" >nul 2>&1
    goto :fin_error
)
del "%TMP_LS%" >nul 2>&1
echo  [OK] GitHub responde.

REM ---- 3) Es ya un clon de git? Si no, convertir la carpeta ----
set "ES_CLON="
set "TMP_TOP=%TEMP%\scopepower_toplevel.txt"
git rev-parse --show-toplevel >"%TMP_TOP%" 2>nul
if not errorlevel 1 (
    set /p TOPLEVEL=<"%TMP_TOP%"
    set "TOPLEVEL=!TOPLEVEL:/=\!"
    if /I "!TOPLEVEL!"=="%RAIZ%" set "ES_CLON=1"
)
del "%TMP_TOP%" >nul 2>&1

if defined ES_CLON goto :rama

echo.
echo  Esta carpeta es una copia de ScopePower, no un clon de git ^(no tiene historial^).
echo  Para poder actualizarla desde GitHub hay que convertirla en un clon: se anade la
echo  carpeta oculta .git, se baja el historial y los ficheros del programa se dejan
echo  exactamente como estan en la version elegida. Lo tuyo ^(config.ini, secretos.ini,
echo  calibraciones, capturas, logs, galeria y datos^) no se toca.
if not defined SIN_PREGUNTAR (
    choice /C SN /M "Convertir esta carpeta en un clon de GitHub"
    if errorlevel 2 goto :fin_cancelado
)
if exist ".git\" (
    echo  [ERROR] Ya hay una carpeta .git aqui pero git no la reconoce. Revisa a mano.
    goto :fin_error
)
git init -q -b master . || goto :fin_error_git
git remote add origin "%REMOTO%" || goto :fin_error_git
set "RECIEN_CONVERTIDA=1"
goto :fetch

:rama
REM Este script es para el PC de campo, que vive en master. En una carpeta de desarrollo
REM con otra rama activa no se hace nada: un reset --hard moveria esa rama.
set "RAMA="
for /f "delims=" %%B in ('git symbolic-ref -q --short HEAD 2^>nul') do set "RAMA=%%B"
if defined RAMA if /I not "!RAMA!"=="master" (
    echo  [ERROR] Esta carpeta esta en la rama "!RAMA!", no en master: parece una carpeta de
    echo          desarrollo. Cambia a master ^(git switch master^) o actualiza con git a mano.
    goto :fin_error
)

:fetch
REM ---- 4) Bajar lo nuevo (ramas y etiquetas) ----
git remote set-url origin "%REMOTO%" >nul 2>&1
echo  Descargando de GitHub...
git fetch --quiet --force --tags --prune origin
if errorlevel 1 goto :fin_error_git

REM ---- 5) Elegir el destino ----
set "DESTINO=" & set "NOMBRE_DESTINO="
if defined VOLVER (
    if not exist "%FICHERO_ANTERIOR%" (
        echo  [ERROR] No hay version anterior guardada ^(%FICHERO_ANTERIOR%^): todavia no se ha
        echo          actualizado con este script.
        goto :fin_error
    )
    set /p LINEA_ANTERIOR=<"%FICHERO_ANTERIOR%"
    for /f "tokens=1,* delims= " %%A in ("!LINEA_ANTERIOR!") do (set "DESTINO=%%A" & set "NOMBRE_DESTINO=%%B")
    if "!NOMBRE_DESTINO!"=="" set "NOMBRE_DESTINO=!DESTINO!"
    set "NOMBRE_DESTINO=version anterior !NOMBRE_DESTINO!"
    git rev-parse --verify -q "!DESTINO!" >nul 2>&1 || (
        echo  [ERROR] La version anterior guardada ^(!DESTINO!^) no esta en el historial.
        goto :fin_error
    )
) else if defined A_MASTER (
    set "DESTINO=origin/master"
    set "NOMBRE_DESTINO=master (ultimo desarrollo)"
) else (
    for /f "delims=" %%T in ('git tag --list "v*" --sort=-v:refname') do (
        if not defined DESTINO (set "DESTINO=%%T" & set "NOMBRE_DESTINO=release %%T")
    )
    if not defined DESTINO (
        echo  [ERROR] En GitHub no hay ninguna release ^(etiqueta v1.2.3^) todavia.
        echo          Para actualizar a lo ultimo de master: actualizar_desde_github.bat /master
        goto :fin_error
    )
)
for /f "delims=" %%H in ('git rev-parse --short "!DESTINO!"') do set "SHA_DESTINO=%%H"

REM ---- 6) Comprobar que lo propio del PC no esta seguido en la version destino ----
set "AVISO_SEGUIDOS="
for %%P in (%PROTEGIDOS%) do (
    git ls-tree -r --name-only "!DESTINO!" -- "%%P" 2>nul | findstr /R "." >nul
    if not errorlevel 1 (
        echo  [AVISO] %%P esta seguido por git en !NOMBRE_DESTINO!: la actualizacion lo sobrescribiria.
        set "AVISO_SEGUIDOS=1"
    )
)
if defined AVISO_SEGUIDOS (
    echo  [ERROR] Se para aqui para no perder nada. Revisa .gitignore en GitHub.
    goto :fin_error
)

REM ---- 7) Que va a cambiar? ----
set "HAY_HEAD=" & set "HAY_CAMBIOS="
git rev-parse --verify -q HEAD >nul 2>&1 && set "HAY_HEAD=1"
if defined HAY_HEAD (
    for /f "delims=" %%H in ('git rev-parse --short HEAD') do set "SHA_ACTUAL=%%H"
    for /f "delims=" %%D in ('git describe --tags --always HEAD') do set "NOMBRE_ACTUAL=%%D"
    echo.
    echo  Instalado ahora: !NOMBRE_ACTUAL! ^(!SHA_ACTUAL!^)
    echo  Destino:         !NOMBRE_DESTINO! ^(!SHA_DESTINO!^)
    git diff --quiet HEAD "!DESTINO!" 2>nul || set "HAY_CAMBIOS=1"
    set "TMP_ST=%TEMP%\scopepower_status.txt"
    git status --porcelain --untracked-files=no >"!TMP_ST!" 2>nul
    for %%Z in ("!TMP_ST!") do if %%~zZ GTR 0 (
        echo.
        echo  [AVISO] Hay ficheros del programa modificados en este PC; la actualizacion los
        echo          devuelve a como estan en GitHub:
        type "!TMP_ST!"
        set "HAY_CAMBIOS=1"
    )
    del "!TMP_ST!" >nul 2>&1
    if not defined HAY_CAMBIOS (
        echo.
        echo  [OK] Ya esta en !NOMBRE_DESTINO!: no hay nada que actualizar.
        goto :fin_ok
    )
    echo.
    echo  Cambios que van a entrar ^(los mas nuevos arriba^):
    git log --oneline --no-decorate "HEAD..!DESTINO!"
    set "ATRAS="
    for /f "delims=" %%N in ('git rev-list --count "!DESTINO!..HEAD"') do set "ATRAS=%%N"
    if defined ATRAS if not "!ATRAS!"=="0" echo  ^(y se deshacen !ATRAS! commits que estan aqui pero no en el destino^)
) else (
    echo.
    echo  Se van a dejar los ficheros del programa como en !NOMBRE_DESTINO! ^(!SHA_DESTINO!^).
    set "HAY_CAMBIOS=1"
)

REM ---- 8) Cambia requirements.txt? ----
set "REQ_CAMBIA="
if defined HAY_HEAD (
    git diff --quiet HEAD "!DESTINO!" -- requirements.txt 2>nul || set "REQ_CAMBIA=1"
) else (
    set "REQ_CAMBIA=1"
)
if defined REQ_CAMBIA echo  requirements.txt cambia: se reinstalaran las dependencias ^(pip^).

REM ---- 9) Esta ScopePower en marcha? ----
set "PID_APP=" & set "PID_LANZADOR=" & set "REINICIAR="
if not defined LANZADOR call :buscar_app
if defined PID_APP (
    echo.
    echo  ScopePower esta en marcha en este PC ^(proceso !PID_APP!^).
    if defined SIN_PREGUNTAR (
        set "REINICIAR=1"
    ) else (
        choice /C SN /M "Cerrarlo ahora y volver a arrancarlo al terminar (si no, el cambio entra en el proximo arranque)"
        if not errorlevel 2 set "REINICIAR=1"
    )
)

echo.
if not defined SIN_PREGUNTAR (
    choice /C SN /M "Aplicar la actualizacion"
    if errorlevel 2 goto :fin_cancelado
)

REM ---- 10) Guardar la version actual para poder volver ----
if defined HAY_HEAD (
    if not exist datos mkdir datos
    >"%FICHERO_ANTERIOR%" echo !SHA_ACTUAL! !NOMBRE_ACTUAL!
    echo  Version anterior apuntada en %FICHERO_ANTERIOR% ^(para volver: /volver^).
)

REM ---- 11) Cerrar la app si asi se ha pedido ----
if defined REINICIAR (
    echo  Cerrando ScopePower...
    if defined PID_LANZADOR taskkill /PID !PID_LANZADOR! /F >nul 2>&1
    taskkill /PID !PID_APP! /T /F >nul 2>&1
    ping -n 3 127.0.0.1 >nul
)

REM ---- 12) Aplicar: los ficheros del programa quedan como en el destino ----
echo  Aplicando !NOMBRE_DESTINO!...
git reset -q --hard "!DESTINO!"
if errorlevel 1 goto :fin_error_git
REM Lo propio del PC tiene que seguir ignorado: si no, un git status lo listaria
for %%P in (%PROTEGIDOS%) do (
    if exist "%%P" (
        git status --porcelain --untracked-files=all -- "%%P" 2>nul | findstr /B "??" >nul
        if not errorlevel 1 echo  [AVISO] En %%P hay ficheros que git no ignora ^(no se han tocado; conviene anadirlos a .gitignore^).
    )
)
echo  [OK] Ficheros del programa actualizados a !NOMBRE_DESTINO! ^(!SHA_DESTINO!^).

REM ---- 13) Dependencias ----
if defined REQ_CAMBIA (
    if exist "%VENV_PY%" (
        echo  Instalando dependencias ^(pip^), puede tardar unos minutos...
        "%VENV_PY%" -m pip install -q -r requirements.txt
        if errorlevel 1 (
            echo  [AVISO] pip ha fallado. Ejecuta instalar_scopepower.bat o repite la actualizacion.
        ) else (
            "%VENV_PY%" "%VENV%\Scripts\pywin32_postinstall.py" -install >nul 2>&1
            echo  [OK] Dependencias instaladas.
        )
    ) else (
        echo  [AVISO] No existe el entorno %VENV_PY%: ejecuta instalar_scopepower.bat.
    )
)

REM ---- 14) Reiniciar ScopePower ----
if defined LANZADOR (
    echo [RESULTADO] ok !NOMBRE_DESTINO! ^(!SHA_DESTINO!^)
    if defined RELANZAR start "ScopePower" /D "%RAIZ%" "%RAIZ%\lanzar_scopepower.bat"
    exit /b 0
)
if defined REINICIAR (
    echo  Arrancando ScopePower de nuevo...
    start "ScopePower" /D "%RAIZ%" "%RAIZ%\lanzar_scopepower.bat"
) else if defined PID_APP (
    echo  ScopePower sigue con la version antigua hasta que se reinicie ^(Cerrar o apagar ^> Reiniciar ScopePower^).
) else if defined RECIEN_CONVERTIDA (
    echo.
    echo  Carpeta convertida en clon. Arranca ScopePower con el acceso directo del escritorio.
)
goto :fin_ok

REM ============================================================================
:buscar_app
REM Busca el python que ejecuta ScopePower.py desde esta carpeta y la ventana de
REM lanzar_scopepower.bat que lo arranco (para que no se quede abierta en su "pause").
set "PS_BUSCAR=$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'ScopePower\.py' -and $_.CommandLine -match [regex]::Escape('%RAIZ%') } | Select-Object -First 1; if ($p) { $pl = ''; $padre = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId); if ($padre -and $padre.Name -eq 'cmd.exe' -and $padre.CommandLine -match 'lanzar_scopepower') { $pl = $padre.ProcessId }; Write-Output ([string]$p.ProcessId + ' ' + $pl) }"
for /f "tokens=1,2" %%A in ('powershell -NoProfile -Command "!PS_BUSCAR!" 2^>nul') do (
    set "PID_APP=%%A"
    set "PID_LANZADOR=%%B"
)
exit /b 0

:fin_error_git
echo.
echo  [ERROR] git ha fallado. Revisa los mensajes de arriba.
echo          Si dice "dubious ownership": git config --global --add safe.directory "%RAIZ:\=/%"
echo          Si pide usuario y contrasena: ejecuta este script a mano para identificarte en GitHub.
:fin_error
if defined LANZADOR (
    echo [RESULTADO] error
    if defined RELANZAR start "ScopePower" /D "%RAIZ%" "%RAIZ%\lanzar_scopepower.bat"
    exit /b 1
)
echo.
pause
exit /b 1

:fin_cancelado
echo.
echo  No se ha cambiado nada.
pause
exit /b 2

:fin_ok
if defined LANZADOR (
    echo [RESULTADO] ok sin cambios
    if defined RELANZAR start "ScopePower" /D "%RAIZ%" "%RAIZ%\lanzar_scopepower.bat"
    exit /b 0
)
echo.
pause
exit /b 0
