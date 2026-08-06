param(
    [string]$Runtime = "win-x64",
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Project = Join-Path $Root "src\Analitico.Operator.DragTest\Analitico.Operator.DragTest.csproj"
$PublishDir = Join-Path $Root "publish-dragtest\$Runtime"
$DistDir = Join-Path $Root "dist"
$ZipPath = Join-Path $DistDir "AnaliticoOperatorDragTest-$Runtime.zip"

if (Test-Path $PublishDir) {
    Remove-Item -LiteralPath $PublishDir -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $PublishDir, $DistDir | Out-Null

dotnet publish $Project `
    -c $Configuration `
    -r $Runtime `
    --self-contained false `
    -o $PublishDir

if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

Compress-Archive -Path (Join-Path $PublishDir "*") -DestinationPath $ZipPath -Force

Write-Host "Pacote de teste gerado: $ZipPath"
