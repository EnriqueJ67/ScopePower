@echo off
setlocal
chcp 65001 >nul
title ScopePower - abrir el cortafuegos

REM ============================================================================
REM  abrir_cortafuegos.bat  -  deja entrar al movil o la tablet en ScopePower
REM
REM  Lo mismo que hace instalar_scopepower.bat con el cortafuegos, por separado,
REM  para quien arranco la app sin instalarla o le dio a "Cancelar" cuando
REM  Windows pregunto. La consola de ScopePower y Configuracion > Conectar desde
REM  el movil avisan cuando hace falta.
REM    1. Crea (o rehace) la regla "ScopePower 5000": TCP 5000, todas las redes.
REM    2. Borra las reglas de BLOQUEO de entrada de python.exe / pythonw.exe que
REM       Windows crea al pulsar "Cancelar" (pesan mas que cualquier permiso).
REM  Pide permiso de administrador.
REM ============================================================================

net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Pidiendo permiso de administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

echo.
netsh advfirewall firewall delete rule name="ScopePower 5000" >nul 2>&1
netsh advfirewall firewall add rule name="ScopePower 5000" dir=in action=allow protocol=TCP localport=5000 profile=any >nul
if %ERRORLEVEL% EQU 0 (
    echo [OK] Regla "ScopePower 5000" creada para todas las redes.
) else (
    echo [ERROR] No se pudo crear la regla del cortafuegos.
)

powershell -NoProfile -Command "$r = Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -match '\\pythonw?\.exe$' } | Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }; if ($r) { $r | Remove-NetFirewallRule; Write-Host ('[OK] Quitadas ' + @($r).Count + ' reglas que bloqueaban Python.') } else { Write-Host '[OK] Ninguna regla bloquea Python.' }"

echo.
echo Listo. Vuelve a probar desde el movil (no hace falta reiniciar ScopePower).
echo.
pause
exit /b 0
