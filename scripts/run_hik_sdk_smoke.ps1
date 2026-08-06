param(
    [string]$SdkRoot = "vendor-local\hikvision\extracted\EN-HCNetSDKV6.1.9.48_build20230410_linux64",
    [string]$Image = "analitico_go_v4-analitico"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sdkPath = (Resolve-Path (Join-Path $projectRoot $SdkRoot)).Path
$sdkLib = Join-Path $sdkPath "lib"
$smokeScript = Join-Path $projectRoot "scripts\hik_sdk_smoke.py"

if (-not (Test-Path -LiteralPath (Join-Path $sdkLib "libhcnetsdk.so") -PathType Leaf)) {
    throw "libhcnetsdk.so nao encontrado em $sdkLib"
}

docker run --rm `
    --mount "type=bind,source=$sdkLib,target=/opt/hikvision/lib,readonly" `
    --mount "type=bind,source=$smokeScript,target=/smoke.py,readonly" `
    --env "HIK_SDK_LIB_DIR=/opt/hikvision/lib" `
    --env "LD_LIBRARY_PATH=/opt/hikvision/lib:/opt/hikvision/lib/HCNetSDKCom" `
    $Image python3 /smoke.py

if ($LASTEXITCODE -ne 0) {
    throw "Smoke test do HCNetSDK falhou com codigo $LASTEXITCODE"
}
