# Investigação de latência das bounding boxes

> Continuação da investigação do frame analítico:
> [`reports/gateway_low_latency_canary.md`](gateway_low_latency_canary.md) e
> [`reports/gateway_low_latency_implementation.md`](gateway_low_latency_implementation.md).

> Continuação do canário executado em 2026-07-29:
> [`reports/box_latency_fast_path_canary.md`](box_latency_fast_path_canary.md).

Data da retomada: 2026-07-29

Projeto: SunOrus / `ServerAnalitivoVMS-main`

Worktree analisado: `D:\Analitico`

## 1. Resumo executivo

A janela de diagnóstico disponível **não reproduziu os aproximadamente 10
segundos** relatados. Na câmera canário 37, em 300 amostras coletadas em
2026-07-28, a idade parcial entre o recebimento no Gateway e a publicação no
TrackStore foi:

- média: **444,567 ms**;
- p95: **1.526,340 ms**;
- máximo: **2.609,787 ms**.

A maior parcela medida foi IA1: média de 260,632 ms, p95 de 1.122,066 ms e
máximo de 1.847,234 ms. A espera na fila IA1 foi pequena nessa janela: média
de 9,797 ms e p95 de 29,260 ms.

Foi localizado um problema arquitetural objetivo no código anterior: a
publicação visual no TrackStore ocorria somente depois de
`EventPipeline.process()`. Assim, quando IA2, IA3 ou outra regra síncrona é
acionada, sua duração entra integralmente no caminho visual. Na janela
coletada, porém, não houve revalidação IA2/IA3 e o pipeline de eventos levou
somente 1,750 ms em média. Portanto, o acoplamento foi comprovado no código,
mas **não explica os 10 segundos naquela amostra**.

Também foram confirmados:

- política `latest frame wins` no pipe MJPEG do Gateway, no consumidor HTTP e
  no mailbox do worker;
- substituição do job pendente da mesma câmera, `drop_oldest` e descarte por
  idade na pool IA1;
- polling web de 800 ms e retenção visual de 3 s;
- SSE do cliente desktop com intervalo configurável (limitado a 100–2.000 ms);
- TrackStore baseado em JSON atômico, com risco anterior de cache em memória
  não observar imediatamente uma escrita feita por outro processo;
- ausência de PTS ou timestamp original da câmera no transporte atual. O campo
  existente marca o recebimento no Gateway e não pode ser chamado de captura.

Classificação desta entrega:

```text
Investigação: inconclusiva para a origem dos ~10 s
Correção: implementada para teste, desligada por padrão
```

## 2. Estado inicial do repositório

- Branch: `checkpoint/ptz-3d-monitor-20260727`
- Upstream: `origin/checkpoint/ptz-3d-monitor-20260727`
- Commit analisado: `d417e41` (`feat: transporte binario de frames e pool central da IA2`)
- Histórico imediatamente anterior:
  - `6e234a2 refactor: prepare auxiliary inference centralization`
  - `11932ac checkpoint: valida backbone e acelera recuperacao do canario`
  - `a7db332 Salva checkpoint do PTZ 3D no monitor`
  - `efaac4d @ checkpoint: estado atual antes de HLS + transcode on-demand`

O worktree já estava extensamente modificado: 55 arquivos rastreados alterados
e vários arquivos novos. Há trabalhos paralelos de PTZ, permissões/usuários,
Video Helper, IA3, WebRTC e interface. Nada foi descartado ou resetado.

O `git diff --check` global já apresentava whitespace em:

- `app/services/monitor_ptz_service.py`;
- `app/web/infrastructure.py`.

Esses trechos são paralelos à investigação e não foram alterados.

## 3. Fluxo real das boxes

```text
MediaMTX / RTSP
  -> gateway/capture.go
     Gateway.ffmpegArgs()
     Gateway.runFFmpegOnce()
     readMJPEGFrames() [canal de 1 item, substitui o antigo]
  -> gateway/gateway.go
     Gateway.recordFrame()
     latestJPEG + sequence + ring/shared transport
  -> app/camera/gateway_frames_capture.py
     GatewayFramesCapture.read()
     deque(maxlen=5), escolhe o frame mais recente
     ou
     app/camera/frame_transport.py
     SharedMemoryGatewayCapture.read()
  -> app/runtime/capture.py
     CameraCaptureService.read_latest()
  -> app/runtime/worker_capture_stage.py
     WorkerCaptureStage.run()
     anota worker_received_* e chama put_latest()
  -> app/runtime/frame_pipeline.py
     LatestFrameMailbox [um item, substitui o anterior]
  -> app/runtime/worker_base.py
     BaseCameraWorker.run()
  -> app/runtime/worker_frame_processor.py
     WorkerFrameProcessor.process()
     _run_inference()
  -> app/runtime/inference_detection.py
     DetectionService.infer()
  -> app/runtime/inference_pool.py
     InferencePool.infer()
     um job pendente por câmera, replace/drop_oldest/max_age
  -> app/runtime/events.py
     EventPipeline.process()
  -> app/runtime/event_revalidation.py
     EventRevalidationCoordinator.evaluate() [IA2/IA3]
  -> app/services/track_store.py
     TrackStore.set_tracks() [JSON temporário + os.replace]
  -> app/web/monitor_presenter.py
     build_monitor_tracks_payload()
  -> app/web/routes/monitor_routes.py
     GET /monitor/tracks
     GET /monitor/tracks/stream
     GET /monitor/tracks/diagnostics
  -> app/static/js/monitor_vms.js
     pollTrackBoxes() -> updateCameraBoxes() -> renderCameraBoxes()
     ou
     operator-client/.../AnaliticoApiClient.cs
     StreamMonitorTracksAsync()
  -> operator-client/.../MainWindowViewModel.cs
     ApplyTrackResponseAsync()
  -> operator-client/.../CameraTileViewModel.cs
     ApplyTracks()
```

### Ordem visual encontrada antes do canário

```python
tracks = _run_inference(...)
_process_events(tracks=tracks, ...)
track_store.set_tracks(...)
```

Essa ordem torna IA2/IA3 e regras parte do caminho crítico da box.

### Ordem do canário opcional

Com `VISUAL_FAST_PATH_ENABLED=true` e câmera explicitamente selecionada:

```python
tracks = _run_inference(...)
track_store.set_tracks(...)  # publicação visual
_process_events(tracks=tracks, ...)
```

O padrão permanece desligado e preserva a ordem anterior.

## 4. Identidade e relógios

A identidade propagada quando disponível é:

```text
camera_id
generation_id
frame_id
```

Campos instrumentados:

- `gateway_received_at_ns`;
- `gateway_published_at_monotonic_ns` no transporte compartilhado;
- `worker_received_at_ns`;
- `worker_received_monotonic_ns`;
- `inference_started_at_ns`;
- `inference_completed_at_ns`;
- `event_pipeline_started_at_ns`;
- `event_pipeline_completed_at_ns`;
- `tracks_published_at_ns`;
- `api_read_at_ns`;
- `sse_sent_at_ns`;
- `client_received_at_ns` no desktop e no browser;
- `client_rendered_at_ns` no browser.

Limitações deliberadamente explícitas:

- `source_frame_captured_at_ns` continua `null`;
- `source_pts` continua `null`;
- `capture_clock` é `gateway_receive_wall_clock`;
- `gateway_received_at_ns` é relógio de parede e não timestamp original da
  câmera;
- durações internas no mesmo processo usam relógio monotônico;
- diferenças backend/cliente usam relógio de parede e pressupõem relógios
  sincronizados;
- tracking está dentro de `PersonDetector.track()` e não possui hoje um marco
  separado confiável; `tracking_ms` permanece desconhecido em vez de receber
  um valor inventado.

## 5. Diagnóstico por câmera

Endpoint restrito:

```text
GET /monitor/tracks/diagnostics?camera_ids=37
```

Ele retorna identidade, relógio conhecido, tempos de publicação/leitura,
estado do TrackStore e agregados de latência sem URL RTSP, frames, crops ou
credenciais.

Configuração:

```env
BOX_LATENCY_DIAGNOSTICS_ENABLED=false
BOX_LATENCY_DIAGNOSTICS_CAMERA_IDS=
BOX_LATENCY_DIAGNOSTICS_SAMPLE_WINDOW=300
VISUAL_FAST_PATH_ENABLED=false
VISUAL_FAST_PATH_CAMERA_IDS=
```

Seleção aceita IDs separados por vírgula ou `*`. Nenhuma opção é ligada
globalmente por padrão.

## 6. Medições disponíveis

Origem: payload persistido da câmera 37, janela de 300 amostras de
2026-07-28. Fast path estava desligado. O timestamp original da câmera não
estava disponível.

| Trecho | Média | p95 | Máximo |
| --- | ---: | ---: | ---: |
| Frame → Gateway | desconhecido | desconhecido | desconhecido |
| Gateway → worker | 41,753 ms | 76,151 ms | 91,264 ms |
| Worker → início IA1 | 110,166 ms | 449,957 ms | 1.041,021 ms |
| Fila IA1 | 9,797 ms | 29,260 ms | 63,720 ms |
| IA1 (detector + tracking) | 260,632 ms | 1.122,066 ms | 1.847,234 ms |
| IA1 → publicação | 4,395 ms | 19,698 ms | 113,436 ms |
| Pipeline de eventos | 1,750 ms | 4,649 ms | 8,039 ms |
| TrackStore | sem agregado | sem agregado | sem agregado |
| Backend → cliente | não coletado | não coletado | não coletado |
| Cliente → desenho | não coletado | não coletado | não coletado |
| Total da box | desconhecido | desconhecido | desconhecido |
| Gateway recebido → publicação (idade parcial) | 444,567 ms | 1.526,340 ms | 2.609,787 ms |

Última amostra da janela:

| Métrica | Valor |
| --- | ---: |
| Gateway → worker | 31,225 ms |
| Worker → início IA1 | 36,829 ms |
| Fila IA1 | 19,450 ms |
| IA1 | 620,369 ms |
| Pipeline de eventos | 1,687 ms |
| IA1 → publicação | 1,753 ms |
| Idade parcial na publicação | 843,218 ms |
| Escrita anterior registrada no TrackStore | 16,951 ms |

Não houve eventos revalidados nessa janela; por isso `ia2_ms` e `ia3_ms`
ficaram sem amostras.

## 7. Evidências por hipótese

### 7.1 Frame analítico atrasado

Não comprovado. O transporte não carrega PTS nem captura original, portanto
esta hipótese permanece aberta.

O Gateway observado em 2026-07-29 mantinha `last_frame_age_ms` entre
aproximadamente 143 e 316 ms nas câmeras 32, 34 e 37. Havia cerca de
1.600 descartes por câmera, coerentes com o canal de um item que substitui o
frame anterior. Isso prova descarte no pipe, não a idade do frame antes de o
FFmpeg decodificá-lo.

### 7.2 IA1 processando jobs antigos

Não ocorreu na janela medida: fila média de 9,797 ms e p95 de 29,260 ms.

O código atual mantém um job pendente por câmera, substitui o anterior, usa
`drop_oldest` quando a fila global enche e descarta job que excede
`INFERENCE_POOL_MAX_JOB_AGE_SECONDS` (1 s na configuração observada).

A inferência em si apresentou cauda alta e foi o maior componente medido.

### 7.3 Publicação esperando eventos/IA2/IA3

Comprovado no fluxo de código anterior. Não comprovado como causa dos 10 s no
baseline, pois IA2/IA3 não dispararam e o pipeline ficou abaixo de 9 ms.

O fast path opt-in foi implementado para permitir o experimento controlado.

### 7.4 TrackStore

O caminho usa arquivo JSON com escrita atômica. A escrita observada mais
recente era da ordem de 17 ms, sem histograma suficiente para média/p95.

Foi corrigido um risco real de consistência: um leitor em outro processo podia
manter cache em memória até o TTL mesmo após nova versão do arquivo. O leitor
agora compara `st_mtime_ns` antes de reutilizar o cache. Resultado vazio também
mantém a nova identidade e remove boxes.

### 7.5 SSE, polling e proxy

- Web: polling a cada 800 ms. Só esse intervalo já impede garantir 500 ms no
  pior caso.
- Desktop: SSE `/monitor/tracks/stream`, padrão do backend de 250 ms e limite
  de 100–2.000 ms solicitado pelo cliente.
- SSE envia `Cache-Control: no-store`, `X-Accel-Buffering: no` e eventos sem
  acumulação no servidor.
- Não foi medido o buffering de um proxy externo.

### 7.6 Cliente

- Web retinha boxes por até 3 s.
- Web e desktop agora ignoram `frame_id` igual/mais antigo dentro da mesma
  geração.
- Resultado vazio fresco remove imediatamente as boxes.
- O browser mede recebimento e o próximo `requestAnimationFrame`.
- O desktop mede recebimento, mas ainda não exporta histograma de renderização.

Mesmo combinados, polling de 800 ms e retenção de 3 s não explicam sozinhos
uma box seguindo a pessoa com atraso constante de 10 s; podem prolongar box
antiga ou elevar a latência percebida.

## 8. Teste OSD existente

Foram encontrados dois snapshots temporários:

| Arquivo | Criação no PC (local) | OSD |
| --- | --- | --- |
| `.tmp_cam37_direct.jpg` | 13:22:03 | 13:22:59 |
| `.tmp_cam37_helper.jpg` | 13:22:15 | 13:23:11 |

O intervalo real e o intervalo OSD foram ambos de aproximadamente 12 s. O
relógio da câmera estava cerca de 56 s adiantado em relação ao PC. Os
snapshots mostram enquadramentos PTZ diferentes e não incluem um snapshot
identificado do frame efetivamente usado pela IA. Assim, são evidência de que
o relógio avançava normalmente, mas não concluem o teste visual ponta a ponta.

Os snapshots devem ser removidos após a conclusão do teste controlado; eles
foram preservados nesta retomada por já existirem e serem evidência da sessão
anterior.

## 9. Alterações implementadas

- agregador por câmera com janela limitada, média, p95 e máximo;
- propagação de identidade e timestamps Gateway → captura → worker;
- métricas de recebimento, publicação, descarte, FPS e backlog estimado no
  Gateway;
- timestamp de leitura da API e envio SSE;
- endpoint operacional restrito;
- fast path visual configurável e desligado;
- atualização posterior apenas do diagnóstico, protegida por frame e geração,
  sem renovar `updated_at`;
- correção de coerência do cache interprocesso do TrackStore;
- remoção imediata em resultado vazio fresco;
- descarte de atualização visual mais antiga no browser e desktop;
- medições de recebimento/render no cliente.

## 10. Testes e validações

Executados nesta retomada:

```text
.venv\Scripts\python.exe -m pytest
  tests/runtime/test_box_latency_diagnostics.py
  tests/services/test_track_store_latency.py
  tests/runtime/test_worker_frame_processor.py -q

Resultado: 15 passed

.venv\Scripts\python.exe -m pytest
  [suíte ampliada de diagnóstico, worker, pool IA1, scheduling,
   transporte binário, shared frame reader, TrackStore, presenter e rotas]

Resultado: 61 passed

cd gateway
go test ./...

Resultado: ok gateway

dotnet build
  operator-client/src/Analitico.Operator.App/Analitico.Operator.App.csproj
  -c Release --no-restore

Resultado: compilação bem-sucedida, 0 avisos, 0 erros
```

O primeiro teste com o Python global falhou durante a coleta por ausência do
pacote `cryptography`. A repetição no `.venv` do projeto passou; não foi uma
falha funcional.

Cobertura direta já presente:

- fast path publica antes dos eventos;
- fast path desligado preserva a ordem anterior;
- falha no pipeline de eventos não elimina publicação;
- identidade e resultado vazio são preservados;
- leitor observa nova versão escrita por outro processo;
- diagnóstico tardio não sobrescreve frame/generation divergente;
- SSE preserva cabeçalhos anti-buffering e inclui `sse_sent_at_ns`;
- média, p95 e máximo são calculados;
- seleção de canário é explícita.

Testes preexistentes da pool cobrem substituição, `drop_oldest` e stale job.

## 11. Estado do runtime e bloqueios do novo canário

Em 2026-07-29 os containers estavam ativos, mas os workers das câmeras 32, 34
e 37 entraram em `error: worker fatal` após o reinício. Os arquivos de tracks
permaneceram na geração anterior, de 2026-07-28. O Gateway continuava
recebendo frames numa geração nova.

Esse problema é preexistente e não foi corrigido ou mascarado nesta tarefa. Um
novo canário com fast path não deve ser iniciado até que o worker fatal seja
diagnosticado separadamente.

## 12. Canário e rollback propostos

Somente depois de restaurar um worker saudável:

```env
BOX_LATENCY_DIAGNOSTICS_ENABLED=true
BOX_LATENCY_DIAGNOSTICS_CAMERA_IDS=37
BOX_LATENCY_DIAGNOSTICS_SAMPLE_WINDOW=300

VISUAL_FAST_PATH_ENABLED=true
VISUAL_FAST_PATH_CAMERA_IDS=37
```

Manter todo o restante inalterado. Coletar no mínimo cinco minutos sem IA2/IA3
e repetir com eventos que acionem IA2 e IA3.

Rollback:

```env
VISUAL_FAST_PATH_ENABLED=false
VISUAL_FAST_PATH_CAMERA_IDS=
```

Depois da coleta:

```env
BOX_LATENCY_DIAGNOSTICS_ENABLED=false
BOX_LATENCY_DIAGNOSTICS_CAMERA_IDS=
```

O rollback restaura a ordem anterior sem alterar modelo, threshold, tracking,
regras ou decisão de evento.

## 13. Riscos

- O fast path publica tracks IA1 antes da confirmação decisória. Deve ser
  usado somente onde a UI já trata a box como detecção visual, não como alarme.
- Se `visual_revalidation_gate_enabled` for obrigatório para a semântica das
  cores/boxes, o canário precisa validar explicitamente essa combinação.
- Relógios entre host, container e cliente podem divergir; durações
  intermáquinas exigem sincronização.
- Sem PTS/captura original, a idade anterior ao Gateway continua invisível.
- Polling web e retenção de 3 s continuam incompatíveis com garantia rígida de
  500 ms.
- O estado atual de `worker fatal` impede comparar antes/depois no mesmo
  runtime.

## 14. Próximos experimentos obrigatórios

1. Diagnosticar o `worker fatal` sem alterar a investigação de latência.
2. Capturar simultaneamente OSD do vídeo exibido e snapshot identificado por
   `camera_id/generation_id/frame_id` do frame IA.
3. Coletar cinco minutos de baseline saudável.
4. Repetir com IA2, IA3 e timeout provocado.
5. Ativar fast path somente na câmera 37 e repetir os mesmos cenários.
6. Medir backend → cliente e cliente → desenho com relógios sincronizados.
7. Testar browser com SSE/coalescing antes de reduzir retenção.
8. Somente então decidir se é necessário perfil FFmpeg de baixa latência.

## 15. Conclusão

As evidências disponíveis localizam componentes relevantes, mas não localizam
com segurança a origem dos aproximadamente 10 segundos:

- frame analítico atrasado: **não determinado**, por falta de timestamp de
  origem;
- fila IA1 atrasada: **não** na janela coletada;
- IA2/IA3 bloqueavam a publicação: **sim no desenho do código**, mas não foram
  acionadas na janela;
- TrackStore adicionava atraso dominante: **não demonstrado**; havia um bug de
  cache interprocesso corrigido;
- SSE/polling adicionava atraso: **sim**, polling web de até 800 ms mais rede;
- cliente acumulava mensagens: **não demonstrado**; retenção de até 3 s e
  ausência de guarda por identidade eram riscos;
- latência observada antes: relato operacional de ~10 s;
- latência medida no baseline instrumentado: parcial de 444,567 ms em média,
  p95 1.526,340 ms, máximo 2.609,787 ms;
- latência depois: **ainda não medida**, pois o fast path permanece desligado
  e o worker canário está fatal.

Não há evidência suficiente para declarar o problema resolvido ou aprovar
rollout.
