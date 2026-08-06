# Relatório de seleção automática da câmera canário

Data: 2026-07-27
Validação de campo: EXECUTADA

## Câmera selecionada

- ID: 37
- Nome: NVR - Canal 6 sub
- Host sanitizado: 177.137.223.61
- Porta RTSP: 554
- Codec: H.265/HEVC, 704x576, 15 FPS
- Estado antes do teste: Camera Gateway `running`; banco/runtime `stopped`, sem worker ativo
- Último frame: 442 ms no encerramento da janela canário
- Path MediaMTX: `cam_37`, pronto
- Consumidores: um `rtspSession` técnico do Camera Gateway; nenhuma sessão WebRTC no path

## Motivo da seleção

A câmera 37 foi escolhida dinamicamente porque apresentou stream H.265 contínuo,
porta RTSP acessível a partir do servidor, circuito fechado, frame subsegundo,
path MediaMTX pronto e ausência de worker de IA. Ela é um substream de menor
resolução, sem `critical_lock`, e não estava sendo visualizada por operador.

A câmera 36 foi a escolha preliminar, mas foi descartada quando uma sessão
WebRTC apareceu durante a validação. A seleção foi recalculada e movida para a
câmera 37 antes da confirmação final.

## Evidências do canário

- Host e porta obtidos com `urllib.parse.urlparse` a partir do valor cadastrado.
- Conexão TCP com a porta RTSP confirmada a partir do container da aplicação.
- `ffprobe` no path interno confirmou HEVC, 704x576 e 15 FPS.
- Camera Gateway permaneceu `running`, sem circuito aberto.
- MediaMTX recebeu 563.373 bytes durante uma janela de 5 segundos.
- Último frame permaneceu recente (442 ms).
- Nenhuma sessão WebRTC consumia `cam_37`.
- Nenhuma URL RTSP completa ou credencial foi impressa.
- Nenhum worker de IA estava ativo antes ou durante a seleção.

## Configuração temporária aplicada

```text
CAM_ID=37
CAM_HOST=<extraído por parser da URL cadastrada>
CAMERA_GATEWAY_SOURCE_MODE=mediamtx_strict
CAMERA_GATEWAY_MEDIAMTX_CAMERA_IDS=37
MEDIA_BACKBONE_RECONCILE_ENABLED=true
MEDIA_BACKBONE_RECONCILE_INTERVAL_SECONDS=30
MEDIA_BACKBONE_REMOVE_ORPHAN_PATHS=true
GATEWAY_SOURCE_POLICY=any
GATEWAY_ALLOWED_RTSP_HOSTS=webrtc-gateway
```

A configuração foi aplicada somente ao processo de validação. Nenhum ID ou IP
canário foi gravado em Docker Compose, código ou arquivo de exemplo. O source
resolvido para o Camera Gateway foi `webrtc-gateway:8554/cam_37`.

## Outras candidatas avaliadas

| ID | Nome | Codec/FPS | Gateway/path | Worker | Consumo no fim da seleção | Decisão |
|---:|---|---|---|---|---|---|
| 29 | NVR - Canal 6 main | Não confirmado | Sem path pronto e fora do Gateway | Inativo | Nenhum | Rejeitada: parada manualmente e sem stream pronto |
| 32 | NVR - Canal 3 main | H.265, 20 FPS | Running/pronto | Inativo | WebRTC ativo | Rejeitada: em visualização |
| 33 | NVR - Canal 1 main | H.265 + G.711, 20 FPS | Running/pronto | Inativo | WebRTC ativo | Rejeitada: em visualização |
| 34 | NVR - Canal 2 main | H.265 + G.711, 20 FPS | Running/pronto | Inativo | WebRTC ativo | Rejeitada: em visualização |
| 35 | NVR - Canal 4 main | H.265 + G.711, 20 FPS | Running/pronto | Inativo | WebRTC ativo | Rejeitada: em visualização |
| 36 | NVR - Canal 5 sub | H.265, 15 FPS | Running/pronto | Inativo | WebRTC ativo | Descartada após mudança do estado ao vivo |
| 37 | NVR - Canal 6 sub | H.265, 15 FPS | Running/pronto | Inativo | Sem WebRTC | Selecionada |

Todas as câmeras cadastradas estavam com `auto_start_enabled=false`; esse
critério preferencial não diferenciou as candidatas. Os IDs 32 a 37 apontavam
para o mesmo host de origem e tiveram a porta RTSP acessível.

## Testes automatizados

- Python: 10 testes do Media Backbone e Camera Gateway passaram.
- Go: suíte do Camera Gateway passou.
