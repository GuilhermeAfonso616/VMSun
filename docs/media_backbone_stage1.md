# Backbone de midia — etapa 1

Os consumidores continuos usam `rtsp://webrtc-gateway:8554/cam_{id}`. O RTSP salvo da camera e usado somente como origem do path MediaMTX; descoberta ONVIF, teste de credenciais e diagnosticos administrativos continuam podendo acessa-lo diretamente.

Rollout canario:

```env
CAMERA_GATEWAY_SOURCE_MODE=mediamtx_strict
CAMERA_GATEWAY_MEDIAMTX_CAMERA_IDS=67
GATEWAY_SOURCE_POLICY=mediamtx_only
GATEWAY_ALLOWED_RTSP_HOSTS=webrtc-gateway
```

Amplie a lista para H.264, H.265, camera direta e canal NVR, e por fim use `*`. Verifique no MediaMTX um unico upstream por `cam_{id}` e varios readers.

Rollback (reinicie `analitico`, `analitico-runtime` e `camera-gateway`):

```env
CAMERA_GATEWAY_SOURCE_MODE=direct
GATEWAY_SOURCE_POLICY=any
```

Roteiro: IA somente; IA + quatro operadores; operador sem IA; sem consumidores aguardando `sourceOnDemand`; reinicio de `webrtc-gateway`; indisponibilidade MediaMTX em modo estrito; alteracao do RTSP; e H.264/H.265 para cameras e NVRs.
