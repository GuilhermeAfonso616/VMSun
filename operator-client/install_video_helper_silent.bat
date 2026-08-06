@echo off
title Instalador Silencioso - SunOrus Video Helper
echo ========================================================
echo   Instalando SunOrus Video Helper (Modo Silencioso)
echo ========================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$setup = Get-ChildItem -Path '$env:USERPROFILE\Downloads', '%~dp0' -Filter 'SunOrus-Video-Helper-Setup-*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($setup) { Unblock-File -LiteralPath $setup.FullName; Start-Process -FilePath $setup.FullName -ArgumentList '/silent' -Wait; Write-Host 'Concluido com sucesso!' -ForegroundColor Green } else { Write-Host 'Arquivo SunOrus-Video-Helper-Setup-*.exe nao encontrado na pasta Downloads.' -ForegroundColor Red }"
echo.
pause
