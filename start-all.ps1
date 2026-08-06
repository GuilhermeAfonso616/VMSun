param(
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Get-PrimaryLanIPv4 {
    $targets = @("1.1.1.1", "8.8.8.8")
    foreach ($target in $targets) {
        $probe = [System.Net.Sockets.UdpClient]::new()
        try {
            $probe.Connect($target, 80)
            $address = ($probe.Client.LocalEndPoint).Address
            if ($address -and -not [System.Net.IPAddress]::IsLoopback($address)) {
                return $address.IPAddressToString
            }
        } catch {
        } finally {
            $probe.Dispose()
        }
    }
    return ""
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [string]$Name,
        [int]$TimeoutSeconds = 180,
        [int]$PollSeconds = 2
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Host "$Name pronto: $Url"
                return
            }
        } catch {
        }

        Start-Sleep -Seconds $PollSeconds
    }

    throw "$Name não respondeu em até $TimeoutSeconds segundos: $Url"
}

Write-Host "Subindo gateway + analítico..."

$configuredWebrtcHosts = [string]$env:MTX_WEBRTCADDITIONALHOSTS
if ([string]::IsNullOrWhiteSpace($configuredWebrtcHosts)) {
    $envFile = Join-Path $PSScriptRoot ".env.docker"
    if (Test-Path -LiteralPath $envFile) {
        $configuredLine = Get-Content -LiteralPath $envFile | Where-Object {
            $_ -match '^\s*MTX_WEBRTCADDITIONALHOSTS\s*='
        } | Select-Object -First 1
        if ($configuredLine) {
            $configuredWebrtcHosts = ($configuredLine -split '=', 2)[1].Trim().Trim('"').Trim("'")
            if (-not [string]::IsNullOrWhiteSpace($configuredWebrtcHosts)) {
                $env:MTX_WEBRTCADDITIONALHOSTS = $configuredWebrtcHosts
            }
        }
    }
}
if ([string]::IsNullOrWhiteSpace($configuredWebrtcHosts)) {
    $detectedWebrtcHost = Get-PrimaryLanIPv4
    if ([string]::IsNullOrWhiteSpace($detectedWebrtcHost)) {
        throw "Nao foi possivel detectar o IPv4 LAN para publicar o WebRTC."
    }
    $env:MTX_WEBRTCADDITIONALHOSTS = $detectedWebrtcHost
}
Write-Host "WebRTC LAN detectado: $env:MTX_WEBRTCADDITIONALHOSTS"

$composeArgs = @("compose", "--env-file", ".env.docker", "up", "-d", "--force-recreate")
if (-not $NoBuild) {
    $composeArgs += "--build"
}

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Wait-HttpOk -Url "http://localhost:8090/healthz" -Name "camera-gateway" -TimeoutSeconds 180
Wait-HttpOk -Url "http://localhost:8000/monitor" -Name "analitico" -TimeoutSeconds 240

Write-Host ""
Write-Host "Tudo pronto."
Write-Host "Monitor: http://localhost:8000/monitor"
Write-Host "Gateway: http://localhost:8090/healthz"
