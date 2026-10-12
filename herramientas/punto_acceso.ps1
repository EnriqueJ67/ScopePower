# herramientas/punto_acceso.ps1
#
# Zona con cobertura inalambrica movil de Windows (punto de acceso) para que la
# tablet se conecte al mini PC: Windows le da siempre 192.168.137.1, asi que la
# direccion de ScopePower no cambia nunca (el punto de acceso de Android cambia
# de red cada vez que se enciende).
#
# La pantalla de Configuracion solo deja activarlo si hay una conexion con
# Internet; en el campo la unica red es la del modulo wifi del SynScan, que no
# tiene. Por la API de Windows (NetworkOperatorTetheringManager) se puede
# compartir cualquier red conectada, tenga Internet o no.
#
#   powershell -ExecutionPolicy Bypass -File herramientas\punto_acceso.ps1 [estado|encender|apagar]
#
# Nombre, contrasena y banda se cambian en Configuracion > Zona con cobertura
# inalambrica movil > Editar (eso si deja hacerlo sin Internet).

param([ValidateSet('estado', 'encender', 'apagar', 'diagnostico')][string]$Accion = 'estado')
$ErrorActionPreference = 'Stop'

if ($Accion -eq 'diagnostico') {
    # Todo lo que hace falta para saber por que no arranca, en logs\punto_acceso_diagnostico.txt
    $raiz = Split-Path -Parent $PSScriptRoot
    $ruta = Join-Path $raiz 'logs\punto_acceso_diagnostico.txt'
    $ErrorActionPreference = 'Continue'
    & {
        "== {0} ==" -f (Get-Date)
        "`n== Estado (script) =="; & powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath estado 2>&1
        "`n== netsh wlan show drivers =="; netsh wlan show drivers
        "`n== netsh wlan show wirelesscapabilities =="; netsh wlan show wirelesscapabilities
        "`n== netsh wlan show interfaces =="; netsh wlan show interfaces
        "`n== Adaptadores de red (tambien ocultos) =="
        Get-NetAdapter -IncludeHidden | Sort-Object Name | Format-Table -AutoSize Name, InterfaceDescription, Status, MacAddress | Out-String -Width 250
        "`n== Dispositivos Wi-Fi Direct =="
        Get-PnpDevice -Class Net | Where-Object { $_.FriendlyName -match 'Direct|Wi-?Fi|Wireless|802' } |
            Format-Table -AutoSize FriendlyName, Status, Present | Out-String -Width 250
        "`n== Servicios =="
        Get-Service icssvc, WlanSvc, SharedAccess -ErrorAction SilentlyContinue | Format-Table -AutoSize Name, Status, StartType | Out-String
        "`n== Registro icssvc =="
        Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\icssvc\Settings' -ErrorAction SilentlyContinue | Out-String
        "`n== Radios (modo avion) =="
        Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\RadioManagement\SystemRadioState' -ErrorAction SilentlyContinue | Out-String
    } *>&1 | Out-File -FilePath $ruta -Encoding utf8
    Write-Output "Diagnostico guardado en $ruta"
    exit 0
}

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGen = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1

function Esperar($operacion, [Type]$tipo) {
    $tarea = $asTaskGen.MakeGenericMethod($tipo).Invoke($null, @($operacion))
    [void]$tarea.Wait(-1)
    $tarea.Result
}

$NI = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$TM = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$RES = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]

# Red que se "comparte": la de Internet si la hay; si no, cualquier red conectada
# (en el campo, la del modulo del SynScan).
$conectadas = @($NI::GetConnectionProfiles() | Where-Object { $_.GetNetworkConnectivityLevel().ToString() -ne 'None' })
foreach ($p in $conectadas) {
    Write-Output ("Red conectada: {0} (nivel {1})" -f $p.ProfileName, $p.GetNetworkConnectivityLevel())
}
$origen = $NI::GetInternetConnectionProfile()
if (-not $origen) { $origen = $conectadas | Select-Object -First 1 }
if (-not $origen) {
    Write-Output "ERROR: no hay ninguna red conectada que compartir (conecta antes el wifi del modulo)"
    exit 2
}
Write-Output ("Red de origen: {0}" -f $origen.ProfileName)
Write-Output ("Capacidad: {0}" -f $TM::GetTetheringCapabilityFromConnectionProfile($origen))

$gestor = $TM::CreateFromConnectionProfile($origen)
$ap = $gestor.GetCurrentAccessPointConfiguration()
# Banda (Windows 11): con un solo wifi el punto de acceso va por fuerza en la
# banda y el canal de la red a la que ya esta conectado.
try {
    $bandas = @('Auto', 'TwoPointFourGigahertz', 'FiveGigahertz') | Where-Object { $ap.IsBandSupported($_) }
    Write-Output ("Banda configurada: {0} (admitidas: {1})" -f $ap.Band, ($bandas -join ', '))
} catch { }

switch ($Accion) {
    'encender' {
        if ($gestor.TetheringOperationalState.ToString() -eq 'On') {
            Write-Output "Ya estaba encendido"
        } else {
            $r = Esperar ($gestor.StartTetheringAsync()) ($RES)
            Write-Output ("Encender: {0} {1}" -f $r.Status, $r.AdditionalErrorMessage)
            if ($r.Status.ToString() -ne 'Success') { exit 1 }
        }
    }
    'apagar' {
        $r = Esperar ($gestor.StopTetheringAsync()) ($RES)
        Write-Output ("Apagar: {0} {1}" -f $r.Status, $r.AdditionalErrorMessage)
    }
}

Write-Output ("Estado: {0}, red {1}, {2} cliente(s)" -f $gestor.TetheringOperationalState, $ap.Ssid, $gestor.ClientCount)
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq '192.168.137.1' } |
    ForEach-Object { Write-Output ("IP del punto de acceso: {0} en {1}" -f $_.IPAddress, $_.InterfaceAlias) }
exit 0
