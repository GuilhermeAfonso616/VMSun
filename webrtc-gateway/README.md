# WebRTC Gateway beta

Este servico experimental usa MediaMTX para testar RTSP -> WebRTC em paralelo ao
gateway MJPEG atual.

Fluxo:

```text
Camera RTSP -> MediaMTX -> WebRTC browser player -> /monitor/webrtc
```

Portas:

- `8889/tcp`: WebRTC HTTP/player
- `8189/udp`: ICE/WebRTC
- `8189/tcp`: ICE/WebRTC fallback quando UDP nao fecha na rede
- `9997/tcp`: API de controle, usada pelo FastAPI dentro da rede Docker

O Compose publica `8889/tcp` e `8189` TCP/UDP nas interfaces do servidor para
permitir o acesso pela LAN confiavel. Para restringir o bind explicitamente:

```env
WEBRTC_GATEWAY_BIND_ADDRESS=127.0.0.1
```

Use isso apenas em rede confiavel durante o beta. Os paths seguem o padrao
`cam_<id>`, entao nao exponha o gateway WebRTC diretamente para redes abertas.

No app, abra:

```text
http://SERVIDOR:8000/monitor/webrtc
```

A tela registra paths `cam_<id>` no MediaMTX via Control API e abre o player
WebRTC embutido do MediaMTX em iframes.

Ao iniciar por `start-all.ps1`, pelo painel Docker ou por
`scripts/compose-auto.sh`, o IPv4 LAN usado pela maquina e detectado
automaticamente e enviado ao MediaMTX. Assim, mover o servidor de
`192.168.1.x` para `192.168.2.x` nao exige editar o projeto.

Se for necessario anunciar um DNS, IP publico ou varios enderecos, sobrescreva
a deteccao antes de iniciar (valores separados por virgula):

```env
WEBRTC_GATEWAY_BIND_ADDRESS=0.0.0.0
MTX_WEBRTCADDITIONALHOSTS=servidor.exemplo.local,IP_ALTERNATIVO_DO_SERVIDOR
```

Esse valor manual e opcional e especifico de cada ambiente.

O `mediamtx.yml` nao fixa um endereco de rede. O valor de
`MTX_WEBRTCADDITIONALHOSTS` e aplicado por ambiente para evitar que uma copia
do projeto anuncie o IP de outro servidor durante a negociacao ICE.

As cameras devem fornecer H.264 para compatibilidade ampla entre navegadores.
H.265/HEVC depende do navegador, do sistema operacional e dos codecs instalados;
por isso, uma camera somente H.265 pode funcionar em um PC e falhar em outro.

## Acesso externo por HTTPS

Defina em `.env.docker`:

```dotenv
WEBRTC_GATEWAY_PUBLIC_BASE_URL=https://video.sunorus.com.br
MTX_WEBRTCADDITIONALHOSTS=video.sunorus.com.br,sunorus.com.br,186.250.202.114,192.168.2.62
```

Use `docker compose --env-file .env.docker ...`: o Compose interpola `${VAR}`
antes de aplicar `env_file`, portanto omitir `--env-file` pode substituir um
valor local por vazio. Alteracao de ambiente exige recriar os containers;
`restart` sozinho nao atualiza variaveis.

O player oficial usa WHEP por HTTP, nao WebSocket. Um virtual host Apache que
termina TLS deve preservar o host, informar o esquema original e encaminhar
todos os metodos HTTP e sessoes longas:

```apache
ProxyPreserveHost On
RequestHeader set X-Forwarded-Proto "https"
ProxyPass        / http://127.0.0.1:8889/ connectiontimeout=5 timeout=3600 nocanon
ProxyPassReverse / http://127.0.0.1:8889/
```

Os modulos necessarios sao `proxy`, `proxy_http`, `headers` e `ssl`. Nao use
regra de upgrade WebSocket. O fluxo WHEP precisa que `OPTIONS`, `POST`, `PATCH`
e `DELETE`, `Content-Type: application/sdp`, `Location`, `ETag` e
`If-Match` atravessem o proxy sem reescrita.
