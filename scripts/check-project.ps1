param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$SkipTests,
    [switch]$SkipGo,
    [switch]$SkipDotnet,
    [switch]$SkipDatabaseCheck,
    [switch]$Coverage
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path $Python)) {
    $Python = "py"
}

function Invoke-NativeStep {
    param(
        [string]$Label,
        [scriptblock]$Command
    )

    Write-Host ""
    Write-Host "==> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label falhou com codigo $LASTEXITCODE."
    }
}

$runId = [Guid]::NewGuid().ToString("N")
$qualityTemp = Join-Path (Get-Location) ".tmp_quality_gate\$runId"
$pythonTemp = Join-Path $qualityTemp "temp"
$pytestTemp = Join-Path $qualityTemp "pytest"
$goCache = Join-Path $qualityTemp "gocache"
New-Item -ItemType Directory -Force -Path $pythonTemp, $goCache | Out-Null

$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousGoCache = $env:GOCACHE
$env:TEMP = $pythonTemp
$env:TMP = $pythonTemp
$env:GOCACHE = $goCache
$env:AVALONIA_TELEMETRY_OPTOUT = "1"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"

try {
Write-Host "Compilando arquivos Python..."
Invoke-NativeStep "Compilacao Python" {
    & $Python -m compileall -q app tests scripts main.py
}

if (-not $SkipTests -and -not $SkipDatabaseCheck) {
    Invoke-NativeStep "Compatibilidade de schema SQLite legado" {
        & $Python scripts\verify_database_compatibility.py --mode legacy
    }
}

if (-not $SkipTests) {
    $pytestArguments = @("-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--basetemp", $pytestTemp)
    if ($Coverage) {
        $pytestArguments += @("--cov=app", "--cov-report=term", "--cov-report=xml:coverage.xml")
    }
    Invoke-NativeStep "Testes Python" {
        & $Python @pytestArguments
    }
}

if (-not $SkipTests -and -not $SkipGo) {
    if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
        throw "Go nao encontrado. Instale Go 1.23 ou use -SkipGo."
    }
    Invoke-NativeStep "Testes do gateway Go" {
        Push-Location gateway
        try {
            go test ./...
        }
        finally {
            Pop-Location
        }
    }
}

if (-not $SkipDotnet) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw ".NET SDK nao encontrado. Instale .NET 8 ou use -SkipDotnet."
    }
    Invoke-NativeStep "Build do cliente operador" {
        dotnet build operator-client\src\Analitico.Operator.App\Analitico.Operator.App.csproj -c Release
    }
}

Write-Host ""
Write-Host "Checagem concluida com sucesso."
}
finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:GOCACHE = $previousGoCache
}
