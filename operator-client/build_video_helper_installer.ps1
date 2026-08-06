param(
    [string]$Runtime = "win-x64",
    [string]$Configuration = "Release",
    [string]$Version = "0.1.0",
    # Por padrao nem o helper nem o FFmpeg vao dentro do instalador: juntos eram
    # 22 dos 22,6 MB do setup. O bootstrapper (net48, sem runtime embutido) baixa
    # os dois na instalacao e valida o SHA-256.
    # -PayloadUrl e a URL publica final do zip gerado aqui (dist\video-helper).
    [string]$PayloadUrl = "",
    [switch]$EmbutirPayload,
    # Informe -FfmpegPath ou -EmbutirFfmpeg para voltar a embutir o FFmpeg.
    [string]$FfmpegPath = "",
    [switch]$EmbutirFfmpeg,
    [string]$FfmpegDownloadUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$HelperProject = Join-Path $Root "src\SunOrus.Video.Helper\SunOrus.Video.Helper.csproj"
$InstallerProject = Join-Path $Root "installer\SunOrus.Video.Helper.Installer\SunOrus.Video.Helper.Installer.csproj"
$PublishRoot = Join-Path $Root "publish"
$PayloadDir = Join-Path $PublishRoot "video-helper\$Runtime"
$InstallerPayloadDir = Join-Path $Root "installer\SunOrus.Video.Helper.Installer\Payload"
$EmbeddedPayloadZip = Join-Path $InstallerPayloadDir "SunOrusVideoHelperPayload.zip"
$InstallerOut = Join-Path $Root "dist\video-helper"
$PayloadName = "SunOrusVideoHelperPayload-$Version-$Runtime.zip"
$PublishedPayloadZip = Join-Path $InstallerOut $PayloadName
$StagingZip = Join-Path $PublishRoot "SunOrusVideoHelperPayload-$Version-$Runtime.zip"
$SetupName = "SunOrus-Video-Helper-Setup-$Version-$Runtime.exe"
$SetupPath = Join-Path $InstallerOut $SetupName

$EmbutindoPayload = $EmbutirPayload.IsPresent
$EmbutindoFfmpeg = $EmbutirFfmpeg.IsPresent -or -not [string]::IsNullOrWhiteSpace($FfmpegPath)

if (-not $EmbutindoPayload -and [string]::IsNullOrWhiteSpace($PayloadUrl)) {
    throw @"
Informe onde o pacote do helper ficara hospedado:

    .\build_video_helper_installer.ps1 -PayloadUrl https://seu-host/caminho/$PayloadName

Ou gere um setup offline, com o helper dentro do executavel:

    .\build_video_helper_installer.ps1 -EmbutirPayload -EmbutirFfmpeg
"@
}

if ($EmbutindoFfmpeg) {
    if ([string]::IsNullOrWhiteSpace($FfmpegPath)) {
        if ($env:SUNORUS_FFMPEG_PATH -and (Test-Path -LiteralPath $env:SUNORUS_FFMPEG_PATH)) {
            $FfmpegPath = $env:SUNORUS_FFMPEG_PATH
        } else {
            $command = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
            if ($command) {
                $FfmpegPath = $command.Source
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($FfmpegPath) -or !(Test-Path -LiteralPath $FfmpegPath)) {
        $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
        if (Test-Path -LiteralPath $wingetRoot) {
            $candidate = Get-ChildItem -LiteralPath $wingetRoot -Filter "ffmpeg.exe" -File -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($candidate) {
                $FfmpegPath = $candidate.FullName
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($FfmpegPath) -or !(Test-Path -LiteralPath $FfmpegPath)) {
        throw "ffmpeg.exe nao encontrado. Informe -FfmpegPath C:\caminho\ffmpeg.exe ou remova -EmbutirFfmpeg"
    }
}

if (Test-Path -LiteralPath $PayloadDir) {
    Remove-Item -LiteralPath $PayloadDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PayloadDir, $InstallerOut | Out-Null

Write-Host "1/5 Publicando SunOrus Video Helper..."
dotnet publish $HelperProject `
    -c $Configuration `
    -r $Runtime `
    --self-contained true `
    -p:PublishSingleFile=false `
    -p:PublishTrimmed=true `
    -p:Version=$Version `
    -p:AssemblyVersion=$Version.0 `
    -p:FileVersion=$Version.0 `
    -o $PayloadDir
if ($LASTEXITCODE -ne 0) { throw "Falha ao publicar o helper." }

if ($EmbutindoFfmpeg) {
    Write-Host "2/5 Incluindo FFmpeg no pacote (modo offline)..."
    Copy-Item -LiteralPath $FfmpegPath -Destination (Join-Path $PayloadDir "ffmpeg.exe") -Force
} else {
    Write-Host "2/5 FFmpeg sera baixado na instalacao: $FfmpegDownloadUrl"
}

$origemFfmpeg = if ($EmbutindoFfmpeg) { "Este pacote inclui FFmpeg." } else { "Este instalador baixa o FFmpeg de $FfmpegDownloadUrl durante a instalacao." }
$notices = @"
SunOrus Video Helper - avisos de terceiros

$origemFfmpeg FFmpeg e um projeto independente, distribuido sob
suas proprias licencas. Consulte https://ffmpeg.org/legal.html e a configuracao
do binario utilizado antes de distribuir este instalador fora de sua organizacao.
"@
Set-Content -LiteralPath (Join-Path $PayloadDir "THIRD_PARTY_NOTICES.txt") -Value $notices -Encoding UTF8

Write-Host "3/5 Gerando payload..."
if (Test-Path -LiteralPath $StagingZip) {
    Remove-Item -LiteralPath $StagingZip -Force
}
Compress-Archive -Path (Join-Path $PayloadDir "*") -DestinationPath $StagingZip -Force
$PayloadSha256 = (Get-FileHash -LiteralPath $StagingZip -Algorithm SHA256).Hash.ToLowerInvariant()

# O zip so entra no executavel no modo offline. Fora dele, qualquer copia antiga
# em Payload\ e removida para nao correr o risco de embutir uma versao velha.
if ($EmbutindoPayload) {
    Write-Host "4/5 Embutindo payload no instalador (modo offline)..."
    New-Item -ItemType Directory -Force -Path $InstallerPayloadDir | Out-Null
    Copy-Item -LiteralPath $StagingZip -Destination $EmbeddedPayloadZip -Force
} else {
    Write-Host "4/5 Payload sera baixado de: $PayloadUrl"
    if (Test-Path -LiteralPath $EmbeddedPayloadZip) {
        Remove-Item -LiteralPath $EmbeddedPayloadZip -Force
    }
    Copy-Item -LiteralPath $StagingZip -Destination $PublishedPayloadZip -Force
    Set-Content -LiteralPath "$PublishedPayloadZip.sha256" -Value "$PayloadSha256  $PayloadName" -Encoding ASCII
}

Write-Host "5/5 Publicando instalador..."
$props = @(
    "-p:Version=$Version",
    "-p:AssemblyVersion=$Version.0",
    "-p:FileVersion=$Version.0",
    "-p:FfmpegDownloadUrl=$FfmpegDownloadUrl",
    "-p:EmbutirPayload=$($EmbutindoPayload.ToString().ToLowerInvariant())"
)
if (-not $EmbutindoPayload) {
    $props += "-p:PayloadDownloadUrl=$PayloadUrl"
    $props += "-p:PayloadSha256=$PayloadSha256"
}

dotnet publish $InstallerProject -c $Configuration @props -o $InstallerOut
if ($LASTEXITCODE -ne 0) { throw "Falha ao publicar o instalador." }

$GeneratedSetup = Join-Path $InstallerOut "SunOrus.Video.Helper.Setup.exe"
if (!(Test-Path -LiteralPath $GeneratedSetup)) {
    throw "Instalador nao foi gerado: $GeneratedSetup"
}
if (Test-Path -LiteralPath $SetupPath) {
    Remove-Item -LiteralPath $SetupPath -Force
}
Move-Item -LiteralPath $GeneratedSetup -Destination $SetupPath

$SizeKb = [Math]::Round((Get-Item -LiteralPath $SetupPath).Length / 1KB, 1)
Write-Host ""
Write-Host "Instalador gerado: $SetupPath"
Write-Host "Tamanho: $SizeKb KB"
Write-Host "Uso silencioso: /silent"
if (-not $EmbutindoPayload) {
    $PayloadMb = [Math]::Round((Get-Item -LiteralPath $PublishedPayloadZip).Length / 1MB, 2)
    Write-Host ""
    Write-Host "PUBLIQUE o pacote antes de distribuir o instalador:"
    Write-Host "  arquivo : $PublishedPayloadZip ($PayloadMb MB)"
    Write-Host "  destino : $PayloadUrl"
    Write-Host "  sha256  : $PayloadSha256"
    Write-Host "O hash esta gravado no instalador; se o arquivo publicado for outro, a instalacao falha."
}
Write-Host ""
Write-Host "Requisito na maquina do operador: .NET Framework 4.8 (ja incluso no Windows 10 1903+ e no Windows 11)."
