# Etapa 4A — implementação Gateway Low-Latency

Data: 2026-07-29

Branch: `checkpoint/ptz-3d-monitor-20260727`

Commit analisado: `d417e41e2a159d3f047c1bcac28a1ef8c45c47fe`

## Estado inicial

O worktree já continha a Etapa 3/fast path e alterações paralelas de PTZ, SDK,
incidentes e frontend. Nenhuma delas foi descartada. Os arquivos do Gateway já
estavam modificados antes desta etapa; por isso o diff completo não representa
autoria exclusiva da Etapa 4A. Nenhum commit foi criado.

`git diff --check` manteve somente whitespace preexistente em:

- `app/services/monitor_ptz_service.py`;
- `app/web/infrastructure.py`.

## Fluxo encontrado

```text
MediaMTX RTSP/TCP
  -> FFmpeg em gateway/capture.go
  -> image2pipe/MJPEG stdout
  -> parser JPEG
  -> LatestFrameMailbox (capacidade 1)
  -> recordFrameObserved
  -> latestJPEG/ring HTTP ou shared transport
  -> GatewayFramesCapture Python
  -> worker/IA1/fast path/SSE
```

O comando anterior já limitava a saída a 2 FPS, escalava para 960x540 e gerava
MJPEG. O timestamp `CapturedAt` era `time.Now()` ao receber o JPEG, não captura
original.

## Alterações implementadas

- perfis `compatibility`, `balanced` e `low_latency`;
- overrides canário separados para `balanced` e `low_latency`;
- fallback explícito e modo strict;
- mailbox thread-safe de último frame, capacidade um;
- contadores de recebido/publicado/substituído/stale;
- leitura de PTS relativo pelo progresso FFmpeg;
- estimador de drift com rebase em discontinuity;
- limite de idade estimada opt-in;
- watchdog com hold, cooldown e teto por hora;
- reinício isolado e nova geração de stream;
- identidade da geração transportada ao cliente Python;
- correção de RFC3339Nano no consumidor Python;
- estado operacional sanitizado por câmera;
- probe OSD com decoder de referência aquecido, alinhamento temporal e rejeição
  de frame stale;
- configuração Compose e exemplos de ambiente.

## Limitações deliberadas

- PTS relativo não é timestamp da câmera;
- a estimativa não detecta atraso constante existente antes da base;
- o descarte por idade permaneceu desligado no canário;
- watchdog permaneceu desligado ao final;
- não foi adotado UDP, NVDEC, BGR/NV12 nem novo container;
- nenhuma lógica de IA, threshold ou evento foi alterada.

## Testes

- `go test ./...`: passou;
- `go vet ./...`: passou;
- `go test -race ./...`: indisponível no host; CGO desativado e, com
  `CGO_ENABLED=1`, não há `gcc`;
- teste Python de contrato Gateway: 2 passaram;
- testes Python direcionados: 34 passaram;
- suíte Python completa: 871 passaram, 1 ignorado e 4 falharam;
- consumidor lento: teste entrega o frame 40, substitui pendentes e não reproduz
  toda a fila;
- H.264 sintético RTSP: abriu em `low_latency`, sem fallback/restart;
- H.265 NVR câmera 37: abriu nos três perfis.

As quatro falhas da suíte completa são preexistentes/paralelas:

1. `tests/scripts/test_dahua_sdk_worker.py` — classificação PTZ por canal;
2. `tests/services/test_incident_service.py` — fechamento sem responsável;
3. `tests/services/test_monitor_ptz_service.py` — texto/capacidade Dahua;
4. `tests/services/test_monitor_ptz_service.py` — texto/capacidade Intelbras.

Detalhes do A/B estão em
[`gateway_low_latency_canary.md`](gateway_low_latency_canary.md).
