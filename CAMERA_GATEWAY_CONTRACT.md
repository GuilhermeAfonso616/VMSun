# Camera Gateway Contract

Objetivo: separar captura RTSP, reconexao e fan-out de frames do worker de IA.

## Responsabilidades

- Gateway Go:
  - recebe ou atualiza a `source_url` da camera
  - abre RTSP
  - reconecta
  - publica frame ao vivo
  - publica snapshot
  - expõe health/status da camera
- Python:
  - inferencia
  - regras analiticas
  - eventos
  - cadastro/admin
  - registro gradual da origem RTSP no gateway quando `CAMERA_GATEWAY_ENABLED=true`

## Endpoints

- `GET /healthz`
- `POST /cameras/{camera_id}/source`
- `GET /cameras/{camera_id}/stream/live`
- `GET /cameras/{camera_id}/snapshot`
- `GET /cameras/{camera_id}/health`
- `GET /cameras/{camera_id}/status`

## `POST /cameras/{camera_id}/source`

Payload:

```json
{
  "source_url": "rtsp://usuario:senha@host:554/caminho"
}
```

Resposta:

```json
{
  "ok": true,
  "restarted": true,
  "camera": {
    "camera_id": 1,
    "state": "warming_up",
    "failure_count": 0,
    "source_url": "rtsp://usuario:senha@host:554/caminho"
  }
}
```

## Forma dos dados

- `stream/live`: MJPEG multipart (`multipart/x-mixed-replace; boundary=frame`)
- `snapshot`: JPEG unico com o ultimo frame recebido
- `health/status`: JSON com:
  - `camera_id`
  - `state`
  - `last_frame_at`
  - `last_reconnect_at`
  - `failure_count`
  - `source_url`

## Estados

- `idle`: camera conhecida, mas sem origem registrada
- `starting`: origem registrada e loop iniciando
- `warming_up`: processo de captura iniciado, aguardando frames
- `running`: ultimo frame recebido com sucesso
- `degraded`: ultimo frame ficou velho alem do limite configurado
- `reconnecting`: falha de captura e tentativa de reconexao em andamento
- `offline`: falha ao iniciar captura
- `stopped_manual`: loop encerrado por troca de origem ou shutdown

## Regras

- `preview_status` nao deve derrubar `analytics_status`.
- `warming_up` nao deve virar `offline` antes da janela de tolerancia.
- O frontend pode alternar entre origem local e gateway por config.
- O gateway nao executa IA, regras analiticas nem persistencia de eventos.
- Se o gateway nao estiver ligado, o Python continua usando o caminho legado.

## Etapa de migracao

1. Gateway publica snapshot e health.
2. Python consome snapshot/health via helper unico.
3. Gateway passa a publicar live para o VMS.
4. Python para de tocar RTSP direto.

## Consumo pelo worker Python

Além do uso pela UI/monitoramento, o endpoint `GET /cameras/{id}/stream/live` também pode ser consumido pelo worker Python quando `CAMERA_GATEWAY_WORKER_CAPTURE_ENABLED=true`. O stream deve permanecer como `multipart/x-mixed-replace` contendo frames JPEG completos. O Python descarta frames antigos e processa apenas frames novos, preservando o comportamento de baixa fila do worker.

O endpoint `POST /cameras/{id}/source` deve ser idempotente: chamadas repetidas com a mesma `source_url` não devem reiniciar a captura se ela já estiver ativa.
