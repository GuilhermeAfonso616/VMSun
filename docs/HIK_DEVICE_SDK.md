# Integração de dispositivos Hikvision

## Runtime homologado no laboratório

- pacote: `EN-HCNetSDKV6.1.9.48_build20230410_linux64.zip`;
- plataforma: Linux x86_64;
- SHA-256: `8DE553FB2E8DBB0AC441EE1BD73C5ECB73D720C1359396750479A6E169ABF93F`;
- versão informada pelo runtime: `0x06010930` (6.1.9.48).

O pacote é proprietário e permanece em `vendor-local/`, ignorado pelo Git e
pelo contexto de build Docker. A obtenção, o uso e uma eventual redistribuição
devem respeitar a licença aceita no portal do fabricante.

## Estrutura local esperada

```text
vendor-local/hikvision/
  EN-HCNetSDKV6.1.9.48_build20230410_linux64.zip
  extracted/EN-HCNetSDKV6.1.9.48_build20230410_linux64/
    incEn/
    lib/
      libhcnetsdk.so
      libPlayCtrl.so
      HCNetSDKCom/
```

Não copie apenas `libhcnetsdk.so`: os componentes, o PlayCtrl e as bibliotecas
fornecidas no diretório `lib/` formam um conjunto compatível.

## Smoke test sem dispositivo

O teste carrega a biblioteca, executa `NET_DVR_Init`, consulta a versão e chama
`NET_DVR_Cleanup`. Ele não lê credenciais e não conecta ao NVR.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_hik_sdk_smoke.ps1
```

Saída esperada:

```text
OK hcnet_sdk_initialized build_hex=0x06010930
```

## Tela de teste administrativa

Com o SDK montado no serviço `analitico`, acesse **Teste SDK** no menu do usuário
administrador ou abra `/admin/teste-sdk`. Por padrão, a página aceita apenas IPs
privados. Para testar um equipamento externo, defina
`HIK_SDK_ALLOW_PUBLIC_IPS=true`; loopback, multicast e endereços inválidos continuam
bloqueados. As sessões são temporárias em memória (15 minutos por padrão). Usuário
e senha não são gravados no banco nem enviados em argumentos de processo.

A visualização usa capturas JPEG solicitadas diretamente por
`NET_DVR_CaptureJPEGPicture_NEW`, com atualização opcional a cada dois segundos.
Ela valida login, canal e imagem sem depender de RTSP. O PTZ usa comandos nativos
com duração máxima de 800 ms e parada automática. Streaming contínuo por callback
`NET_DVR_RealPlay_V40` permanece como evolução posterior do laboratório.

O SDK é executado em subprocesso isolado a cada operação. Assim, uma falha nativa
do fabricante não derruba o processo principal do servidor web.

## Dahua NetSDK

A mesma tela suporta Dahua com o pacote oficial
`General_NetSDK_Eng_Linux64_IS_V3.060.0000003.0.R.251127.tar.gz`, validado localmente
com SHA256 `7CE6669A944E30C26D7FD919D703997561DD9687F211B4FEB50C762C66768E81`.
O diretório `Bin/` é montado somente no container web em `/opt/dahua/lib`.

Ao selecionar Dahua, a porta padrão muda para `37777`. O canal informado na tela é
humano (começa em 1) e o worker converte para a numeração zero-based exigida pelo
NetSDK. Login, snapshot e PTZ também executam em subprocesso isolado.

## Intelbras

Intelbras é OEM Dahua e fala o mesmo protocolo NetSDK. A tela de teste sempre aceita
"Intelbras" como fabricante para a API HTTP CGI (identificação via `getSerialNo`,
sem depender de biblioteca nenhuma). Quando um pacote NetSDK estiver disponível —
seja um enviado especificamente como "Intelbras" na tela de instalação, seja o
pacote Dahua já instalado — snapshot e PTZ passam a usar o SDK nativo (mesmo worker
`dahua_sdk_worker.py`) em vez da API HTTP. Sem SDK nativo disponível, ambos caem de
volta para a API HTTP CGI (`snapshot.cgi`/`ptz.cgi`), como antes.
