# herramientas/exportar_eventos_red.ps1
#
# Saca del registro de eventos de Windows lo que hace falta para entender un
# corte de la red (wifi, punto de acceso, controlador, apagados bruscos) y lo
# deja en logs\eventos_red.txt, donde se puede leer desde la carpeta compartida.
#
#   powershell -ExecutionPolicy Bypass -File herramientas\exportar_eventos_red.ps1 [-Horas 48]

param([int]$Horas = 48)
$ErrorActionPreference = 'Continue'

$raiz = Split-Path -Parent $PSScriptRoot
$ruta = Join-Path $raiz 'logs\eventos_red.txt'
$desde = (Get-Date).AddHours(-$Horas)

# Registros "operational" de red que existan en este equipo
$operacionales = Get-WinEvent -ListLog * -ErrorAction SilentlyContinue | Where-Object {
    $_.RecordCount -gt 0 -and $_.LogName -match 'WLAN|Wlan|WiFi|Wi-Fi|Tether|Hotspot|icssvc|NetworkProfile|Dhcp|NCSI|WFD|WiFiDirect|SharedAccess|Ndis'
} | Select-Object -ExpandProperty LogName

# En System, solo los proveedores que tienen que ver con la red, la energia y los servicios
$proveedoresSistema = 'Netwtw|WLAN|Wlan|Ndis|Tcpip|Dhcp|icssvc|SharedAccess|Kernel-Power|Kernel-General|Kernel-Boot|EventLog|Power-Troubleshooter|Service Control Manager|WudfUsbccid|USB|Kernel-PnP|Display'

$eventos = @()
foreach ($log in $operacionales) {
    $eventos += Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = $desde } -ErrorAction SilentlyContinue
}
$eventos += Get-WinEvent -FilterHashtable @{ LogName = 'System'; StartTime = $desde } -ErrorAction SilentlyContinue |
    Where-Object { $_.ProviderName -match $proveedoresSistema }

$lineas = $eventos | Sort-Object TimeCreated | ForEach-Object {
    $msg = ($_.Message -replace '\s+', ' ')
    if ($msg.Length -gt 400) { $msg = $msg.Substring(0, 400) + '...' }
    '{0:yyyy-MM-dd HH:mm:ss} | {1} | {2} | {3} | {4} | {5}' -f $_.TimeCreated, $_.LogName, $_.ProviderName, $_.Id, $_.LevelDisplayName, $msg
}

& {
    "Eventos de red desde {0:yyyy-MM-dd HH:mm} ({1} h) en {2}" -f $desde, $Horas, $env:COMPUTERNAME
    "Registros leidos: System (filtrado) + " + ($operacionales -join ', ')
    ""
    $lineas
} | Out-File -FilePath $ruta -Encoding utf8

Write-Output ("{0} eventos guardados en {1}" -f @($lineas).Count, $ruta)
