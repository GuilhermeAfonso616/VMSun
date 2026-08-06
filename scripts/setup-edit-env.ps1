param(
    [string]$Python = "py -3.11",
    [switch]$SkipRuntimeDeps
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv")) {
    Write-Host "Criando ambiente virtual em .venv"
    Invoke-Expression "$Python -m venv .venv"
}

$venvPython = Join-Path $PWD ".venv\Scripts\python.exe"

& $venvPython -m pip install --upgrade pip

if ($SkipRuntimeDeps) {
    & $venvPython -m pip install pytest
} else {
    & $venvPython -m pip install -r requirements-dev.txt
}

Write-Host ""
Write-Host "Ambiente pronto."
Write-Host "Ativar: .\.venv\Scripts\Activate.ps1"
Write-Host "Validar: .\scripts\check-project.ps1"
