# SunOrus Video Helper

Servico local Windows para reproducao de cameras H.265 em navegadores que nao
decodificam HEVC via WebRTC.

- Escuta somente em `127.0.0.1`/`::1`, porta `34020`.
- Recebe apenas um ID numerico de camera e um servidor da rede privada.
- Le `rtsp://SERVIDOR:8554/cam_ID`.
- Decodifica com FFmpeg e entrega MJPEG ao elemento `<img>` do mosaico.
- Nao recebe credenciais de camera e nao se conecta diretamente aos DVRs.
- Aceita CORS somente de localhost, redes privadas, `sunorus.com.br` e seus
  subdominios HTTPS. Outras origens exatas podem ser listadas em
  `SUNORUS_VIDEO_HELPER_ALLOWED_ORIGINS`, separadas por virgula.

## Desenvolvimento

```powershell
$env:SUNORUS_FFMPEG_PATH = "C:\caminho\ffmpeg.exe"
dotnet run --project .\src\SunOrus.Video.Helper
```

Teste:

```text
http://127.0.0.1:34020/health
http://127.0.0.1:34020/stream/34.mjpeg?server=192.168.1.33&width=960&fps=10
```

## Distribuicao

O setup e um bootstrapper de ~52 KB em .NET Framework 4.8 (ja presente no
Windows 10 1903+ e no Windows 11, nada a instalar antes). Ele nao carrega o
helper nem o FFmpeg dentro de si: baixa os dois na instalacao e confere o
SHA-256 de cada um. Atualizacao nao rebaixa o FFmpeg que ja esta no disco.

```powershell
# Setup enxuto: publique tambem o zip gerado ao lado dele em dist\video-helper
.\build_video_helper_installer.ps1 -Version 0.4.0 `
    -PayloadUrl https://SEU-HOST/video-helper/SunOrusVideoHelperPayload-0.4.0-win-x64.zip

# Setup offline, com helper e FFmpeg embutidos (~230 MB), para maquina sem rede
.\build_video_helper_installer.ps1 -Version 0.4.0 -EmbutirPayload -EmbutirFfmpeg
```

O hash do pacote e gravado dentro do instalador no momento do build: se o
arquivo publicado no host nao for exatamente o gerado, a instalacao falha. Por
isso o zip e o `.exe` precisam ser publicados sempre em par.

Instalacao silenciosa: `SunOrus-Video-Helper-Setup-*.exe /silent`.
Remocao: o mesmo executavel com `/uninstall [/silent]`.
