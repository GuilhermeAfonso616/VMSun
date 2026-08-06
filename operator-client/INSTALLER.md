# Instalador do SunOrus Operator

Este fluxo gera um `Setup.exe` unico para Windows. O instalador tem interface grafica em formato de assistente classico: selecao de idioma, acordo de licenca, pasta de destino, tarefas adicionais, progresso e conclusao. Ele embute o app publicado, instala em `Program Files` quando executado como administrador, ou em `%LOCALAPPDATA%\Programs` quando executado sem elevacao.

## Gerar instalador

```powershell
cd D:\Analitico\operator-client
.\build_operator_installer.ps1
```

Saida padrao:

```text
D:\Analitico\operator-client\dist\installer\SunOrus-Operator-Setup-0.6.29-win-x64.exe
```

## Opcoes do instalador

```powershell
.\SunOrus-Operator-Setup-0.6.29-win-x64.exe /silent
.\SunOrus-Operator-Setup-0.6.29-win-x64.exe /launch
.\SunOrus-Operator-Setup-0.6.29-win-x64.exe /no-desktop
.\SunOrus-Operator-Setup-0.6.29-win-x64.exe /dir="D:\Apps\SunOrus Operator"
```

## O que ele faz

- Publica o app em modo self-contained, sem exigir runtime .NET instalado.
- Embute o payload do app dentro do setup.
- Exibe uma interface grafica em etapas, parecida com instaladores Inno/NSIS.
- Cria atalhos no Menu Iniciar e, por padrao, na Area de Trabalho.
- Registra o app em "Aplicativos instalados" do Windows para desinstalacao.
- Copia o proprio setup para a pasta instalada para manter o desinstalador funcional.
