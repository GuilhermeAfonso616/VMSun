# Analitico Operator Client

Cliente desktop para operar mosaicos fora do navegador.

Funcoes principais:

- carregar a lista de cameras pelo endpoint `/api/operator/bootstrap`;
- tocar os caminhos RTSP publicados pelo MediaMTX (`rtsp://SERVIDOR:8554/cam_ID`);
- usar LibVLC para decodificacao nativa, reduzindo dependencia do suporte HEVC/H.265 do navegador;
- manter credenciais do NVR no servidor, sem expor RTSP original ao cliente.
- operar mosaicos 1/2/4/6/8/9/12/16/25;
- filtrar biblioteca por todas, visiveis, IA ligada, offline e alta/critica;
- iniciar/parar IA em modo movimento por camera;
- consultar metricas, eventos abertos e saude operacional em polling leve;
- reconhecer e fechar alarmes diretamente no app.
- sobrepor boxes azuis da IA via `/monitor/tracks` quando a IA estiver ativa.

## Requisitos

Servidor:

- `WEBRTC_GATEWAY_RTSP_PUBLIC_BASE_URL=rtsp://IP_DO_SERVIDOR:8554`
- porta `8554/tcp` publicada no `webrtc-gateway`
- cameras registradas pelo backend no MediaMTX

Windows:

- .NET 8 SDK
- bibliotecas LibVLC empacotadas via NuGet para o primeiro MVP

Linux Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y dotnet-sdk-8.0 vlc libvlc-dev
```

## Rodar

```bash
cd operator-client/src/Analitico.Operator.App
dotnet restore
dotnet run
```

Informe a URL do servidor web, por exemplo:

```text
http://192.168.1.42:8000
```

## Publicar

Windows:

```powershell
cd D:\Analitico\operator-client\src\Analitico.Operator.App
dotnet publish -c Release -r win-x64 --self-contained false
```

Linux:

```bash
cd /caminho/Analitico/operator-client/src/Analitico.Operator.App
dotnet publish -c Release -r linux-x64 --self-contained false
```

No Linux, mantenha `vlc`/`libvlc` instalados no sistema para o LibVLCSharp localizar os binarios nativos.
