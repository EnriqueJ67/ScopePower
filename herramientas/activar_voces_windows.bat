@echo off
REM  activar_voces_windows.bat - Hace visibles para ScopePower las voces que se instalan
REM  desde Configuracion de Windows (Hora e idioma > Voz > Agregar voces).
REM
REM  Windows guarda esas voces "OneCore" (Pablo, Laura, Sabina, Raul...) en una rama del
REM  registro que las aplicaciones clasicas (SAPI 5, la que usa voz.py) no miran. Este
REM  script copia su registro a la rama de SAPI 5, que es lo que hace falta para que
REM  aparezcan en Configuracion > Avisos por voz > Voz. No instala nada ni borra nada.
REM
REM  Con ScopePower en ingles hace falta una voz inglesa: David y Zira ya vienen con Windows;
REM  otras (Mark, Hazel...) se anaden igual y este script las hace visibles.
REM  English: makes the voices added in Windows Settings > Time & language > Speech visible to
REM  ScopePower (SAPI 5). Right click > Run as administrator, then restart ScopePower.
REM
REM  Uso: boton derecho > Ejecutar como administrador (hace falta para escribir en HKLM).
REM       Despues reinicia ScopePower y elige la voz nueva en Configuracion.
REM  Para quitar una voz copiada: borrar su clave en
REM       HKLM\SOFTWARE\Microsoft\Speech\Voices\Tokens  (y en WOW6432Node)

setlocal EnableDelayedExpansion
set "ORIGEN=HKLM\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens"
set "DESTINO=HKLM\SOFTWARE\Microsoft\Speech\Voices\Tokens"
set "DESTINO32=HKLM\SOFTWARE\WOW6432Node\Microsoft\Speech\Voices\Tokens"

echo.
echo  ScopePower - activar las voces de Windows para los avisos por voz
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Hay que ejecutarlo como administrador:
    echo          boton derecho sobre el .bat ^> Ejecutar como administrador
    echo.
    pause
    exit /b 1
)

reg query "%ORIGEN%" >nul 2>&1
if errorlevel 1 (
    echo  No hay voces de Windows instaladas. Instala alguna en espanol desde
    echo  Configuracion ^> Hora e idioma ^> Voz ^> Agregar voces, y vuelve a ejecutar esto.
    echo.
    pause
    exit /b 1
)

set N=0
for /f "tokens=*" %%K in ('reg query "%ORIGEN%"') do (
    for /f "tokens=2,*" %%A in ('reg query "%%K" /ve 2^>nul ^| findstr /i "REG_SZ"') do set "NOMBRE=%%B"
    for %%T in ("%%~nxK") do set "TOKEN=%%~T"
    echo  - !NOMBRE!
    reg copy "%%K" "%DESTINO%\!TOKEN!" /s /f >nul
    if errorlevel 1 (
        echo      [ERROR] no se pudo copiar a %DESTINO%
    ) else (
        reg copy "%%K" "%DESTINO32%\!TOKEN!" /s /f >nul 2>&1
        set /a N+=1
    )
)

echo.
if %N%==0 (
    echo  No se ha copiado ninguna voz.
) else (
    echo  %N% voz^(es^) activadas. Reinicia ScopePower y eligela en Configuracion ^> Avisos por voz.
    echo  Para los avisos en espanol vale cualquier voz "Spanish"; si no eliges ninguna,
    echo  ScopePower usa la primera en espanol que encuentre.
)
echo.
pause
endlocal
