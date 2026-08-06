# Canário do fast path de bounding boxes

Data: 2026-07-29

Projeto: SunOrus / `ServerAnalitivoVMS-main`

Worktree: `D:\Analitico`

## 1. Resultado executivo

O canário da câmera 37 foi restaurado e permaneceu funcional. O fast path,
diagnóstico, SSE web, retenção de 800 ms e limite de idade parcial de 500 ms
foram ativados somente para essa câmera.

Os aproximadamente 10 segundos não foram reproduzidos. A causa completa desse
relato continua aberta porque o transporte não fornece timestamp original da
câmera e o cliente gráfico não pôde ser aberto nesta sessão.

Foram localizadas fontes reais de picos:

- IA2 apresentou cold start de 2.387,645 ms em replay real;
- IA3 apresentou cold start de 136,264 ms em um caso distante real;
- o caminho tradicional esperava IA2/IA3 e regras antes de publicar;
- o polling de 800 ms produziu Gateway → recebimento HTTP com média de
  342,845 ms, p95 de 614,736 ms e máximo de 758,240 ms;
- a retenção web anterior aceitava boxes por cerca de 3 s;
- a IA1 já apresentou jitter de até 1.847,234 ms na janela anterior;
- o parser Python descartava silenciosamente o RFC3339Nano do Gateway,
  deixando `gateway_received_at_ns` desconhecido. O parser foi corrigido e
  coberto por teste.

Com o fast path e SSE:

- IA1 → publicação: média 0,199 ms, p95 0,894 ms, máximo 7,990 ms;
- Gateway recebido → publicação: média 86,827 ms, p95 215,458 ms,
  máximo 473,012 ms;
- publicação → primeiro recebimento SSE: média 37,280 ms, p95 71,126 ms,
  máximo 191,899 ms;
- Gateway recebido → primeiro recebimento SSE: média 125,544 ms,
  p95 225,705 ms, máximo 483,501 ms.

Esses totais são parciais: começam no recebimento do frame pelo Gateway e
terminam no recebimento SSE. Não incluem atraso anterior no RTSP/FFmpeg nem
tempo de desenho no navegador.

Classificação:

```text
Investigação: causa parcial localizada
Fast path: aprovado para canário
Meta de latência: não atingida (fim a fim ainda não mensurável)
```

## 2. Estado inicial do repositório

- Branch: `checkpoint/ptz-3d-monitor-20260727`
- Commit: `d417e41e2a159d3f047c1bcac28a1ef8c45c47fe`
- Assunto: `feat: transporte binario de frames e pool central da IA2`
- O worktree já estava extensamente modificado, com trabalhos paralelos de
  PTZ, SDK, incidentes, permissões, WebRTC e Video Helper.
- Nenhum reset, clean ou descarte de alterações paralelas foi executado.
- Nenhum commit foi criado nesta tarefa.
- `git diff --check` continua apontando somente whitespace preexistente em
  `app/services/monitor_ptz_service.py` e `app/web/infrastructure.py`.
- Snapshots diagnósticos que estavam na raiz foram movidos para a pasta
  temporária do sistema; nenhum snapshot, crop, vídeo ou log foi adicionado ao
  repositório.

## 3. Câmera canário

| Campo | Valor |
| --- | --- |
| Camera ID | 37 |
| Nome | NVR - Canal 6 sub |
| Origem | NVR Dahua, substream |
| Codec | H.265 |
| Resolução MediaMTX | 704 × 576 |
| Cadência observada no RTSP | 15 FPS por PTS relativo |
| Cadência publicada pelo Gateway | aproximadamente 1,99 FPS na janela final |
| Resolução analítica publicada | 960 × 540 |
| Worker | restaurado; `running_motion_test` |
| IA1 | pool central, backend PyTorch/CPU nesta máquina |
| IA2 | local, modelo v5, modo `block` |
| IA3 | local, modelo far v1; proteção IA3 v2 em `audit` |
| Cliente de transporte medido | HTTP polling e SSE web |

O estado fatal anterior era residual de uma geração que recebeu
`PostgreSQL AdminShutdown`; com `auto_start_enabled=false`, a câmera não voltou
automaticamente. A restauração foi feita pelo endpoint interno oficial de
start, sem mudar câmera, modelo ou thresholds.

## 4. Fluxo real e posição do fast path

```text
MediaMTX
  -> Camera Gateway / FFmpeg
  -> ring HTTP latest-frame
  -> capture stage
  -> LatestFrameMailbox (1 item)
  -> pool IA1 (1 pendente por câmera)
  -> detector.track() [detecção e tracking inseparáveis na métrica atual]
  -> seleção dos tracks visuais
     ├─ fast path: TrackStore.set_tracks()
     └─ EventPipeline.process()
          -> IA2
          -> IA3
          -> shadows / consenso / Strategy 3 / eventos
  -> caminho tradicional: TrackStore.set_tracks()
  -> /monitor/tracks ou /monitor/tracks/stream
  -> cliente
```

O fast path usa os mesmos `tracks` produzidos pela IA1 e não muda ACCEPT,
REJECT, UNCERTAIN, cooldown, maturidade, snapshots, clips, alarmes ou
persistência. Quando a publicação rápida funciona, o caminho tradicional não
publica novamente. Se o TrackStore falhar no fast path, o pipeline de eventos
continua e há fallback explícito para a publicação tradicional.

## 5. Identidade, ordem e resultado vazio

A atualização visual transporta:

```text
camera_id
generation_id
frame_id
tracks_published_at_ns
```

No cliente web:

- câmera divergente é rejeitada;
- na mesma geração, `frame_id` menor ou igual ao aplicado é rejeitado;
- nova geração permite reinício do contador;
- várias mensagens antes do próximo `requestAnimationFrame` são coalescidas;
- resultado vazio fresco limpa as boxes;
- stream offline ou idade acima da retenção limpa as boxes;
- o estado diagnóstico expõe contadores, último frame e timestamps de
  recebimento/renderização.

O backend registrou no canário:

```text
visual_fast_path_published_total: 160
visual_fast_path_failed_total: 0
visual_fast_path_fallback_total: 0
visual_updates_out_of_order_total: 0
visual_updates_identity_rejected_total: 0
visual_empty_results_total: 145
```

Os contadores de coalescing/render pertencem ao processo do navegador e não
puderam ser coletados porque nenhum navegador integrado estava disponível.

## 6. Idade real do frame: OSD e PTS

O PTS do RTSP começou relativo à conexão (`0,178`, `0,245`, `0,311`...) e
indicou intervalos próximos de 66,7 ms. Ele serve para ordem e cadência, não
para associar o frame ao relógio da câmera.

O OSD da câmera estava aproximadamente 57 s adiantado em relação ao computador,
mas preservou a passagem do tempo. Em uma comparação controlada:

```text
MediaMTX/live: 09:36:11
Gateway/IA:    09:36:10
```

A diferença visual observada foi de aproximadamente 1 s, com incerteza de
captura sequencial e espera por keyframe H.265. Não houve diferença próxima de
10 s nessa amostra.

Conclusão:

```text
source_frame_timestamp confiável: indisponível
latência total real: desconhecida
idade parcial desde Gateway: disponível
```

## 7. IA1, latest-frame e latest-job

O Gateway mantém o frame mais recente; o consumidor HTTP escolhe o item mais
novo; o mailbox do worker tem capacidade de um item. Na pool IA1:

- existe somente um job pendente por câmera;
- um job novo substitui o pendente da mesma câmera;
- overflow é `drop_oldest`;
- job com mais de 1 s de fila é descartado.

Foi adicionado um segundo limite, desligado por padrão:

```env
VISUAL_INFERENCE_MAX_FRAME_AGE_MS=0
VISUAL_INFERENCE_MAX_FRAME_AGE_CAMERA_IDS=
```

No canário ele usa 500 ms e começa em `gateway_received_at_ns`. Isso impede
processar frames já velhos dentro do caminho conhecido, mas continua sendo
idade parcial; não detecta backlog anterior ao Gateway.

Janela final ON, 300 amostras:

| Métrica | Média | p95 | Máximo |
| --- | ---: | ---: | ---: |
| Gateway → worker | 39,079 ms | 69,992 ms | 74,381 ms |
| Worker → início IA1 | 7,228 ms | 10,647 ms | 74,530 ms |
| Fila IA1 | 3,907 ms | 6,260 ms | 93,330 ms |
| IA1 + tracking | 34,379 ms | 137,552 ms | 156,908 ms |
| IA1 → fast publish | 0,199 ms | 0,894 ms | 7,990 ms |

`tracking_ms` isolado continua desconhecido porque `detector.track()` retorna
somente o tempo agregado de detecção + tracking.

## 8. IA2 e IA3 reais

Foram usados snapshots de eventos existentes da própria câmera 37, somente em
memória e sem gravar novos eventos.

Replay de 15 execuções IA2 e 15 IA3:

| Modelo | Amostras | Média | p95 | Máximo |
| --- | ---: | ---: | ---: | ---: |
| IA2, incluindo cold start | 15 | 171,440 ms | 18,404 ms | 2.387,645 ms |
| IA3, todos os gates | 15 | 10,737 ms | 7,356 ms | 136,264 ms |
| IA3 realmente acionada | 5 | 31,361 ms | 136,264 ms | 136,264 ms |

O p95 da IA2 fica abaixo do máximo porque houve um único cold start muito
alto. Sem a primeira execução, a média IA2 foi aproximadamente 13,1 ms. No
caso pequeno/distante, IA3 foi `triggered=true` e `applied=true`.

Comparação controlada do pipeline completo com IA2 e IA3 reais em processos
novos:

| Modo | IA2 | IA3 | IA1 → publicação | Ordem |
| --- | ---: | ---: | ---: | --- |
| Fast path OFF | 75,449 ms | 36,382 ms | 112,009 ms | eventos → publicação |
| Fast path ON | 91,722 ms | 44,211 ms | 0,132 ms | publicação → eventos |

As duas execuções mantiveram IA2 aplicada e IA3 acionada/aplicada. A variação
dos modelos não alterou a conclusão: no modo ON a publicação ocorreu antes de
ambos.

## 9. Timeouts e indisponibilidade

Timeouts e ausência das pools foram validados em testes automatizados:

- falha local/central não vira REJECT;
- `central_prefer` usa fallback explícito;
- `central_strict` devolve estado degradado sem fallback oculto;
- falha do fast path não derruba o worker e preserva publicação tradicional;
- erro do pipeline faz rollback e não elimina os tracks visuais já publicados.

Não foi provocado timeout destrutivo nas pools do processo canário em produção.
Logo, esta evidência é automatizada, não uma medição live.

## 10. TrackStore

Medição de 100 frames distintos via SSE:

| Métrica | Média | p95 | Máximo |
| --- | ---: | ---: | ---: |
| Escrita TrackStore | 9,517 ms | 20,556 ms | 69,593 ms |
| Leitura TrackStore | 8,175 ms | 15,174 ms | 28,582 ms |
| Idade do arquivo na leitura | 35,339 ms | 69,598 ms | 189,680 ms |
| Publicação → API | 35,794 ms | 70,008 ms | 190,088 ms |

O TrackStore introduz jitter mensurável, mas não explicou segundos nessa
janela. A correção anterior de coerência entre processos foi preservada.

## 11. Polling versus SSE

### Polling de 800 ms

45 frames distintos:

```text
Gateway recebido → cliente HTTP
média 342,845 ms
p95 614,736 ms
máximo 758,240 ms
```

Em 30 frames, somente publicação → recebimento teve média 283,065 ms, p95
551,123 ms e máximo 631,450 ms.

### SSE canário de 25 ms

100 frames distintos:

```text
publicação → primeiro recebimento SSE
média 37,280 ms
p95 71,126 ms
máximo 191,899 ms

servidor SSE send → cliente HTTP
média 0,834 ms
p95 2,035 ms
máximo 3,878 ms
```

60 frames distintos:

```text
Gateway recebido → primeiro recebimento SSE
média 125,544 ms
p95 225,705 ms
máximo 483,501 ms
```

O modo `sse_prefer` tem fallback explícito para polling e contador. O modo
`sse_strict` não mantém polling oculto.

## 12. Retenção e cliente

Para a câmera 37:

```text
fresh: 500 ms
retention: 800 ms
```

O código mede `client_received_at_ns` e agenda
`client_rendered_at_ns` no próximo `requestAnimationFrame`, mantendo janela de
300 amostras. A automação do navegador não estava disponível, portanto
`cliente → desenho` não foi coletado e o comportamento visual não foi
declarado validado.

O desktop preserva a proteção de identidade e compilou sem erro, mas não foi
executado com vídeo nesta sessão.

## 13. Tabela antes/depois

As linhas backend usam janelas live sem IA2/IA3. A linha específica de
publicação com IA2/IA3 vem do replay controlado da seção 8.

| Trecho | OFF média | OFF p95 | ON média | ON p95 |
| --- | ---: | ---: | ---: | ---: |
| Gateway → worker | 38,981 ms | 69,620 ms | 39,079 ms | 69,992 ms |
| Fila IA1 | 2,317 ms | 6,200 ms | 3,907 ms | 6,260 ms |
| IA1 + tracking | 56,312 ms | 214,475 ms | 34,379 ms | 137,552 ms |
| Tracking isolado | desconhecido | desconhecido | desconhecido | desconhecido |
| IA1 → publicação, live sem IA2/IA3 | 1,334 ms | 2,872 ms | 0,199 ms | 0,894 ms |
| IA1 → publicação, replay IA2+IA3 | 112,009 ms | n/a | 0,132 ms | n/a |
| IA2 real | 171,440 ms | 18,404 ms | não bloqueia | não bloqueia |
| IA3 real acionada | 31,361 ms | 136,264 ms | não bloqueia | não bloqueia |
| Pipeline de eventos live | 1,018 ms | 1,900 ms | 1,174 ms | 2,224 ms |
| TrackStore | não agregado | não agregado | 9,517 ms | 20,556 ms |
| Backend → cliente | 283,065 ms | 551,123 ms | 37,280 ms | 71,126 ms |
| Cliente → renderização | não medido | não medido | não medido | não medido |
| Total parcial Gateway → publicação | 108,721 ms | 293,954 ms | 86,827 ms | 215,458 ms |
| Total parcial Gateway → cliente | 342,845 ms | 614,736 ms | 125,544 ms | 225,705 ms |
| Total com origem | desconhecido | desconhecido | desconhecido | desconhecido |

## 14. Matriz executada

| Cenário | OFF | ON |
| --- | --- | --- |
| Somente IA1 | live, 300 amostras | live, 300 amostras |
| IA1 + IA2 | replay real | replay real |
| IA1 + IA3 | replay real acionado | replay real acionado |
| IA1 + IA2 + IA3 | replay real, publicação após modelos | replay real, publicação antes dos modelos |
| Timeout IA2 | teste automatizado | teste automatizado |
| Timeout IA3 | teste automatizado | teste automatizado |
| Pool IA2 indisponível | teste `central_prefer/strict` | teste e fast path independente |
| Pool IA3 indisponível | teste de fallback degradado | teste e fast path independente |
| Web polling | medido | — |
| Web SSE | — | medido |
| Desktop SSE | build e testes de identidade; sem runtime | build e testes de identidade; sem runtime |

## 15. Testes e validações

```text
Testes direcionados: 76 passed
Compileall: sucesso
Suíte completa: 849 passed, 1 skipped, 4 failed
gateway/go test ./...: sucesso
gateway/go vet ./...: sucesso
docker compose config --quiet: sucesso
dotnet build: sucesso, 0 warnings, 0 errors
```

Falhas preexistentes/paralelas da suíte completa:

1. `tests/scripts/test_dahua_sdk_worker.py` — classificação PTZ por canal;
2. `tests/services/test_incident_service.py` — fechamento sem responsável;
3. `tests/services/test_monitor_ptz_service.py` — texto/semântica PTZ Dahua;
4. `tests/services/test_monitor_ptz_service.py` — texto/semântica Intelbras.

Os testes direcionados de latência, fast path, IA2/IA3, TrackStore, SSE e
cliente web passaram.

## 16. Arquivos da implementação

Arquivos diretamente usados ou ampliados por esta investigação:

- `app/runtime/box_latency_diagnostics.py`;
- `app/runtime/worker_frame_processor.py`;
- `app/runtime/event_revalidation.py`;
- `app/services/track_store.py`;
- `app/core/config.py`;
- `app/web/routes/monitor_routes.py`;
- `app/static/js/monitor_vms.js`;
- `templates/monitor_vms_new.html`;
- `gateway/capture.go`;
- `gateway/frame_transport.go`;
- `gateway/gateway.go`;
- `docker-compose.yml`;
- `.env.docker.example`;
- `tests/runtime/test_box_latency_diagnostics.py`;
- `tests/runtime/test_worker_frame_processor.py`;
- `tests/runtime/test_event_revalidation.py`;
- `tests/services/test_track_store_latency.py`;
- `tests/test_monitor_boxes_polling.py`;
- `tests/web/test_monitor_routes_http.py`.

Como o worktree já estava sujo, essa lista não afirma autoria exclusiva de
todo o diff de cada arquivo.

## 17. Configuração do canário

Configuração efetivamente aplicada aos containers, sem editar `.env`:

```env
BOX_LATENCY_DIAGNOSTICS_ENABLED=true
BOX_LATENCY_DIAGNOSTICS_CAMERA_IDS=37
BOX_LATENCY_DIAGNOSTICS_SAMPLE_WINDOW=300

VISUAL_FAST_PATH_ENABLED=true
VISUAL_FAST_PATH_CAMERA_IDS=37

VISUAL_TRACK_FRESH_MS=500
VISUAL_TRACK_RETENTION_MS=800

VISUAL_INFERENCE_MAX_FRAME_AGE_MS=500
VISUAL_INFERENCE_MAX_FRAME_AGE_CAMERA_IDS=37

WEB_TRACK_TRANSPORT_MODE=sse_prefer
WEB_TRACK_SSE_CAMERA_IDS=37
```

Nenhuma seleção usa `*`.

## 18. Rollback

```env
BOX_LATENCY_DIAGNOSTICS_ENABLED=false
BOX_LATENCY_DIAGNOSTICS_CAMERA_IDS=

VISUAL_FAST_PATH_ENABLED=false
VISUAL_FAST_PATH_CAMERA_IDS=

VISUAL_INFERENCE_MAX_FRAME_AGE_MS=0
VISUAL_INFERENCE_MAX_FRAME_AGE_CAMERA_IDS=

WEB_TRACK_TRANSPORT_MODE=polling
WEB_TRACK_SSE_CAMERA_IDS=

VISUAL_TRACK_FRESH_MS=500
VISUAL_TRACK_RETENTION_MS=3000
```

Depois, recriar somente `analitico-runtime` e `analitico`. Não há migração de
banco, alteração de câmera, modelo ou evento para reverter.

## 19. Riscos e limitações

- os 10 s não foram reproduzidos;
- o timestamp original continua indisponível;
- o OSD indicou aproximadamente 1 s, mas a captura sequencial H.265 limita a
  precisão;
- tracking permanece agregado à IA1;
- IA2/IA3 foram acionadas em replay real, não por evento live da janela final;
- timeouts/indisponibilidade foram validados por teste, não por pane live;
- o cliente browser mede renderização, mas a coleta não ocorreu nesta sessão;
- o desktop não foi executado;
- SSE de 25 ms é deliberadamente canário e aumenta leituras do TrackStore;
- o limite de 500 ms usa recebimento no Gateway, não origem da câmera.

Não há evidência suficiente para declarar o problema operacional resolvido.
