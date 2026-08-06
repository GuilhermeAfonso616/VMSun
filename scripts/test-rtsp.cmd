@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0\.."

where docker >nul 2>&1
if errorlevel 1 (
    echo ERRO: Docker nao foi encontrado no PATH.
    exit /b 1
)

docker compose ps --status running --services | findstr /x /c:"analitico" >nul
if errorlevel 1 (
    echo ERRO: O container analitico nao esta em execucao.
    echo Inicie o servidor com: docker compose up -d --build
    exit /b 1
)

set "RTSP_URL="
if not "%~1"=="" goto use_argument

set /p "RTSP_URL=Cole a URL RTSP completa e pressione Enter: "
goto validate_url

:use_argument
set "RTSP_URL=%~1"

:validate_url
if not defined RTSP_URL (
    echo ERRO: Nenhuma URL RTSP foi informada.
    exit /b 1
)

echo.
echo Testando abertura e leitura de um frame...

(<nul set /p "=%RTSP_URL%") | docker compose exec -T analitico python -c "import json, sys; from app.camera.rtsp_discovery import probe_rtsp_url_details; result = probe_rtsp_url_details(sys.stdin.read().strip()); print(json.dumps(result, ensure_ascii=False, indent=2)); sys.exit(0 if result.get('ok') else 2)"
set "TEST_EXIT=%ERRORLEVEL%"

echo.
if "%TEST_EXIT%"=="0" (
    echo RESULTADO: RTSP OK - o servidor abriu o stream e leu um frame.
) else (
    echo RESULTADO: FALHOU - consulte o erro exibido acima.
)

exit /b %TEST_EXIT%
