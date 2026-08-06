param(
    [string]$OutputDir = "reports/stability_campaign",
    [string]$WebUrl = "http://127.0.0.1:8000",
    [string]$RuntimeUrl = "http://127.0.0.1:8001",
    [string]$GatewayUrl = "http://127.0.0.1:8090",
    [double]$IntervalSeconds = 15,
    [double]$CanaryIntervalSeconds = 60,
    [double]$DetailIntervalSeconds = 60,
    [string]$PythonCommand = "py",
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$collectorPath = Join-Path $PSScriptRoot "run_stability_campaign.py"

if (-not (Test-Path -LiteralPath $collectorPath)) {
    throw "Coletor nao encontrado: $collectorPath"
}

function Test-Endpoint {
    param(
        [string]$Url,
        [string]$Name
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
            throw "HTTP $($response.StatusCode)"
        }
        Write-Host "[OK] $Name - $Url"
    } catch {
        throw "Preflight falhou em $Name ($Url): $($_.Exception.Message)"
    }
}

if (-not $SkipPreflight) {
    Write-Host "Validando servicos antes da coleta de 7 dias..."
    Test-Endpoint -Url "$WebUrl/api/health" -Name "API web"
    Test-Endpoint -Url "$RuntimeUrl/internal/health/ready" -Name "Runtime"
    Test-Endpoint -Url "$GatewayUrl/healthz" -Name "Camera Gateway"
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$runDir = Join-Path $OutputDir "stability_7d_$stamp"
$arguments = @(
    "-B",
    $collectorPath,
    "observe",
    "--project-root", $projectRoot,
    "--run-dir", $runDir,
    "--stage-name", "stability_7d",
    "--duration-hours", "168",
    "--web-url", $WebUrl,
    "--runtime-url", $RuntimeUrl,
    "--gateway-url", $GatewayUrl,
    "--interval-seconds", ([string]$IntervalSeconds),
    "--canary-interval-seconds", ([string]$CanaryIntervalSeconds),
    "--detail-interval-seconds", ([string]$DetailIntervalSeconds)
)

Write-Host ""
Write-Host "Iniciando observacao de 7 dias. Nenhuma camera sera iniciada ou parada."
Write-Host "As cameras serao descobertas automaticamente pelo supervisor/runtime."
Write-Host "Saida: $runDir"
Write-Host "Use Ctrl+C para interromper; um relatorio parcial sera preservado."
Write-Host ""

Push-Location $projectRoot
try {
    & $PythonCommand @arguments
    $collectorExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($null -eq $collectorExitCode) {
    $collectorExitCode = 1
}

Write-Host ""
Write-Host "Coleta encerrada com codigo $collectorExitCode."
Write-Host "Relatorio: $(Join-Path $runDir 'campaign_summary.md')"
exit $collectorExitCode
