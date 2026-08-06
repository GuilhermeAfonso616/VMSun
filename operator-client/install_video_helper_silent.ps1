param(
    [string]$SetupPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SetupPath)) {
    $candidates = @(
        (Get-ChildItem -Path "$env:USERPROFILE\Downloads" -Filter "SunOrus-Video-Helper-Setup-*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1),
        (Get-ChildItem -Path "$PSScriptRoot" -Filter "SunOrus-Video-Helper-Setup-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1)
    )
    $found = $candidates | Where-Object { $_ -ne $null } | Select-Object -First 1
    if ($found) {
        $SetupPath = $found.FullName
    } else {
        throw "Instalador SunOrus-Video-Helper-Setup-*.exe nao encontrado na pasta Downloads nem na pasta atual."
    }
}

Write-Host "[1/2] Desbloqueando arquivo no Windows SmartScreen..." -ForegroundColor Cyan
Unblock-File -LiteralPath $SetupPath -ErrorAction SilentlyContinue

Write-Host "[2/2] Instalando SunOrus Video Helper silenciosamente..." -ForegroundColor Cyan
$process = Start-Process -FilePath $SetupPath -ArgumentList "/silent" -Wait -PassThru

if ($process.ExitCode -eq 0) {
    Write-Host "Sucesso: SunOrus Video Helper instalado e iniciado em segundo plano!" -ForegroundColor Green
} else {
    Write-Host "Atencao: A instalacao retornou o codigo: $($process.ExitCode)" -ForegroundColor Yellow
}
