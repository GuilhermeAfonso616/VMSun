param(
    [string]$Runtime = "win-x64",
    [string]$Configuration = "Release",
    [string]$Version = "0.6.46"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppProject = Join-Path $Root "src\Analitico.Operator.App\Analitico.Operator.App.csproj"
$InstallerProject = Join-Path $Root "installer\SunOrus.Operator.Installer\SunOrus.Operator.Installer.csproj"
$PublishRoot = Join-Path $Root "publish"
$PayloadDir = Join-Path $PublishRoot "installer-payload\$Runtime"
$InstallerPayloadDir = Join-Path $Root "installer\SunOrus.Operator.Installer\Payload"
$PayloadZip = Join-Path $InstallerPayloadDir "AnaliticoOperatorPayload.zip"
$InstallerOut = Join-Path $Root "dist\installer"
$SetupName = "SunOrus-Operator-Setup-$Version-$Runtime.exe"
$SetupPath = Join-Path $InstallerOut $SetupName

Write-Host "== SunOrus Operator Installer =="
Write-Host "Runtime: $Runtime"
Write-Host "Configuracao: $Configuration"
Write-Host ""

if (Test-Path $PayloadDir) {
    Remove-Item -LiteralPath $PayloadDir -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $PayloadDir, $InstallerPayloadDir, $InstallerOut | Out-Null

Write-Host "1/4 Publicando aplicativo self-contained..."
dotnet publish $AppProject `
    -c $Configuration `
    -r $Runtime `
    --self-contained true `
    -p:PublishSingleFile=false `
    -p:PublishTrimmed=false `
    -p:Version=$Version `
    -p:AssemblyVersion=$Version.0 `
    -p:FileVersion=$Version.0 `
    -o $PayloadDir

$MainExe = Join-Path $PayloadDir "Analitico.Operator.App.exe"
if (!(Test-Path $MainExe)) {
    throw "Executavel principal nao foi gerado: $MainExe"
}

Write-Host "2/4 Gerando payload embutido..."
if (Test-Path $PayloadZip) {
    Remove-Item -LiteralPath $PayloadZip -Force
}
Compress-Archive -Path (Join-Path $PayloadDir "*") -DestinationPath $PayloadZip -Force

Write-Host "3/4 Publicando instalador single-file..."
dotnet publish $InstallerProject `
    -c $Configuration `
    -r $Runtime `
    --self-contained true `
    -p:PublishSingleFile=true `
    -p:EnableCompressionInSingleFile=true `
    -p:DebugType=None `
    -p:DebugSymbols=false `
    -p:Version=$Version `
    -p:AssemblyVersion=$Version.0 `
    -p:FileVersion=$Version.0 `
    -o $InstallerOut

$GeneratedSetup = Join-Path $InstallerOut "SunOrus.Operator.Setup.exe"
if (!(Test-Path $GeneratedSetup)) {
    throw "Instalador nao foi gerado: $GeneratedSetup"
}

Write-Host "4/4 Nomeando instalador final..."
if (Test-Path $SetupPath) {
    Remove-Item -LiteralPath $SetupPath -Force
}
Move-Item -LiteralPath $GeneratedSetup -Destination $SetupPath

$SizeMb = [Math]::Round((Get-Item $SetupPath).Length / 1MB, 2)
Write-Host ""
Write-Host "Instalador gerado com sucesso:"
Write-Host $SetupPath
Write-Host "Tamanho: $SizeMb MB"
Write-Host ""
Write-Host "Opcoes:"
Write-Host "  /silent       instala sem perguntas"
Write-Host "  /launch       abre o app apos instalar"
Write-Host "  /no-desktop   nao cria atalho na area de trabalho"
Write-Host "  /dir=CAMINHO  instala em um caminho especifico"
