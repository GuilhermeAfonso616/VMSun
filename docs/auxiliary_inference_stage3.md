# Inferência auxiliar — Etapa 3

Centralização da execução de IA2, IA3 e modelos shadow, retirando dos workers de
câmera a responsabilidade de carregar e executar esses modelos.

Estado: **Etapas 3A e 3B implementadas**. 3C (IA3) e 3D (shadow) ainda não.

---

## Fluxo atual (medido no repositório, não presumido)

### Onde os modelos são instanciados

| Modelo | Instanciação | Escopo |
|---|---|---|
| IA2 principal | `person_crop_revalidator.py:490` — singleton `_INSTANCE` | por processo |
| IA3 principal | `far_person_revalidator.py:481` — singleton `_INSTANCE` | por processo |
| IA2 shadow v8b | `event_revalidation.py:74` — construção direta | por coordenador |
| IA2 shadow v8c | `event_revalidation.py:87` — construção direta | por coordenador |
| IA3 v2 protection | `event_revalidation.py:102` — construção direta | por coordenador |

O `EventRevalidationCoordinator` é criado dentro de `EventPipeline`
(`worker_base.py:75`), que roda **dentro do processo do worker da câmera**. Os
workers são processos filhos (`worker_process.py:130`, `mp.Process`).

### Quantas cópias existem hoje

Todas as cinco flags estão **ligadas por padrão** (`config.py`):

```
person_revalidator_enabled      = True
far_person_revalidator_enabled  = True
ia2_v8b_shadow_enabled          = True
ia2_v8c_shadow_enabled          = True
ia3_v2_protection_enabled       = True
```

Portanto, com N câmeras ativas há **até 5 × N instâncias de modelo**, em N
processos distintos — memória não compartilhada. Os singletons `_INSTANCE` só
deduplicam *dentro* de um processo; entre processos não têm efeito.

Atenuante importante: o carregamento é **lazy** (`_load_model`, `self._model =
None` no `__init__`). Um modelo só ocupa memória depois da primeira inferência
daquele tipo naquele worker. A duplicação é real, mas proporcional ao uso, não
ao número de workers iniciados.

### O que já existia de pool

`app/services/revalidator_pool.py` (`RevalidatorPool`) **não é uma pool
central**: é um limitador de concorrência `ThreadPoolExecutor` **dentro do
próprio processo do worker**, desligado por padrão
(`revalidator_pool_enabled = False`). Ele limita quantas revalidações rodam em
paralelo naquele worker; não compartilha modelo entre processos.

`app/runtime/inference_pool.py` (`InferencePool`) é a pool da IA1, baseada em
`Thread`, e é o modelo arquitetural a seguir para as pools de IA2/IA3.

### Backend e preprocessamento

- Ultralytics YOLO (`YOLO(str(path))`), classificação;
- device resolvido por `_resolve_revalidator_device` a partir de
  `revalidator_pool_device` (padrão `auto`);
- entrada: frame BGR completo + bbox; o recorte com margem é feito dentro do
  revalidador;
- IA3 tem gate próprio: só roda quando `bbox_height_ratio <= 0.08` ou quando a
  IA2 é "suspeita" (`far_person_revalidator_suspicious_ia2_*`).

### Como o resultado é consumido

`CropRevalidationResult` e `FarPersonRevalidationResult` são dataclasses com
`to_metadata()`. Quem decide ACCEPT/REJECT/UNCERTAIN **não é o revalidador**: é
`strategy3_v2.py` + `alarm_decision.py` + `event_alarm_policy.py`, dentro do
worker. Essa fronteira é exatamente a que a Etapa 3 preserva.

---

## Topologia alvo

```text
Workers das câmeras          Pools centrais
─────────────────────        ──────────────
estado da câmera             IA1  (já central)
tracking                     IA2  (3B)
ROI / linhas                 IA3  (3C)
maturidade                   shadow (3D)
Strategy3 / decisão
        │                            ▲
        └──── request tipado ────────┘
              result tipado
```

O worker mantém contexto e decisão. A pool executa modelo e preprocessamento
ligado ao modelo, nada mais.

---

## Etapa 3A — o que foi implementado

### Tipos versionados

`app/analytics_v2/revalidation/aux_inference_types.py`

- `IA2Request` / `IA2Result`, `IA3Request` / `IA3Result`;
- identidade obrigatória: `job_id`, `camera_id`, `model_type`, `frame_id`,
  `generation_id`, `track_id`, `event_candidate_id`;
- tempo monotônico: `captured_at_monotonic_ns`, `deadline_monotonic_ns`,
  `expired()`, `remaining_seconds()`;
- prioridades: IA1 `0`, IA2 `10`, IA3 `20`, shadow `30`, offline `40`;
- códigos de erro tipados (`timeout`, `pool_unavailable`, `stale_job`, …);
- `IA3Request` carrega `base_quality` e os scores da IA2, porque o gate atual da
  IA3 depende deles — sem isso a execução central mudaria o comportamento do
  gate;
- `native` transporta o resultado original, para que o pipeline continue lendo
  exatamente os mesmos campos.

### Interfaces e clientes

`app/analytics_v2/revalidation/aux_inference_client.py`

```
IA2InferenceClient (ABC)          IA3InferenceClient (ABC)
├── LocalIA2InferenceClient       ├── LocalIA3InferenceClient
├── CentralIA2InferenceClient     ├── CentralIA3InferenceClient
└── FallbackIA2InferenceClient    └── FallbackIA3InferenceClient
```

`build_ia2_client(camera_id)` e `build_ia3_client(camera_id)` são a única porta
de entrada. O pipeline não contém `if mode == ...`.

### Modos e canário

```env
IA2_EXECUTION_MODE=local          # local | central_prefer | central_strict
IA2_CENTRAL_CAMERA_IDS=           # "36,37" ou "*"
IA3_EXECUTION_MODE=local
IA3_CENTRAL_CAMERA_IDS=
SHADOW_EXECUTION_MODE=local
```

- `local`: comportamento atual; a lista é ignorada;
- `central_prefer`: tenta a pool, cai para local com log limitado, contador
  `fallback_local_total` e `fallback_used=True` no resultado;
- `central_strict`: pool obrigatória; sem fallback, com erro tipado.

Seleção centralizada em `central_selected()`; nenhum ID no código. IA2 e IA3 têm
listas independentes.

### Política segura

Falha, timeout ou pool ausente produzem um resultado **equivalente a
"revalidador não aplicado"** (`applied=False`, `passed=None`), que é como o
pipeline já trata a ausência de IA2/IA3. Nunca REJECT, nunca "ausência de
pessoa". Há teste dedicado para isso em ambos os modos.

### Identidade e respostas stale

`AuxInferenceResult.matches(request)` compara `job_id`, `camera_id`,
`model_type`, `frame_id`, `generation_id` e `track_id`. Uma resposta que não
casa é descartada, conta em `jobs_stale_total` e — em `central_prefer` — cai
para local. Isso impede resultado de uma câmera aplicado em outra e resposta de
geração antiga.

### Observabilidade

`camera_execution_state(camera_id)` entra no payload de métricas do worker
(`worker_metrics_publisher._aux_inference_state`) com `ia2_execution_mode`,
`ia2_pool_ready`, `ia2_fallback_active`, `ia2_last_latency_ms`,
`ia2_timeouts_total` e equivalentes de IA3. Não expõe crop nem payload; falha na
leitura não derruba a publicação de métricas.

Contadores em `aux_metrics`, sem labels de alta cardinalidade: `jobs_submitted`,
`jobs_completed`, `jobs_failed`, `jobs_timed_out`, `jobs_dropped`, `jobs_stale`,
`fallback_local`, `payload_bytes`, latência.

---

## O que a 3A deliberadamente não faz

- não carrega nenhum modelo em lugar novo;
- não cria pool, fila, processo ou socket;
- não altera preprocessamento, threshold, modelo ou decisão;
- não muda o padrão: tudo continua `local`.

`CentralIA2InferenceClient` e `CentralIA3InferenceClient` existem com o contrato
fechado e reportam `pool_unavailable` enquanto não houver pool. Isso permite
exercitar modos, canário, fallback, identidade e política segura **antes** de
mover qualquer modelo — que é o objetivo declarado da 3A.

---

## Próximas etapas

**3B — pool central IA2**: implementada. Ver seção "Etapa 3B" abaixo.

**3C — pool central IA3**: fila própria e independente da IA2, validação
específica de pessoas pequenas e distantes, rollout separado.

**3D — shadow**: prioridade baixa, primeiro a ser descartado sob carga, nunca
bloqueando IA1/IA2/IA3, desativado por padrão.

Fila da IA1 permanece separada: a IA1 não pode sofrer head-of-line blocking por
causa de IA2/IA3.

---

## Etapa 3B — pool central da IA2

### Topologia

```text
Worker câmera A ─┐
Worker câmera B ─┼── socket /run/sunorus/ia2.sock ──► IA2Pool ──► modelo IA2 (1x)
Worker câmera C ─┘        (canal próprio, separado da IA1)
```

A pool vive no processo principal do runtime (Opção A), com fila e threads
próprias. Os workers continuam sendo processos filhos e, nas câmeras migradas,
não carregam o modelo.

### Divisão de responsabilidade

O worker prepara o recorte com `crop_with_quality` — a mesma função do caminho
local — e envia apenas o recorte. A pool executa `infer_prepared_crop` e devolve
o resultado. Threshold, ACCEPT/REJECT/UNCERTAIN, tracking, ROI, maturidade e
cooldown continuam no worker.

### Protocolo

Cabeçalho de requisição de 84 bytes (magic `SUNIA201`), resposta de 40 bytes
(`SUNIA2RS`) + corpo JSON pequeno de metadados. Little-endian, offsets
explícitos, CRC32 no payload e no corpo.

**Payload é BGR cru, não JPEG**: recomprimir introduziria perda e quebraria a
equivalência com o caminho local.

Identidade validada na resposta: `camera_id`, `frame_id`, `generation_id`,
`track_id` e `job_id`. Divergência é rejeitada, conta métrica e — em
`central_prefer` — cai para local.

### Fila e prioridade

`PriorityQueue` limitada por `IA2_POOL_MAX_QUEUE_SIZE` (padrão 64). Job com
deadline vencido é descartado **antes** de ocupar o modelo. Fila cheia rejeita
imediatamente em vez de crescer. Prioridade menor é atendida primeiro; a IA1 tem
fila e socket separados, então não há head-of-line blocking entre as duas.

### Estados e recuperação

`loading` → `ready` → `degraded` / `failed`. `generation_id` muda a cada
start/restart. OOM marca degradação, limpa cache quando possível e aplica backoff
de 30 s, sem loop de reinício. Health em `/internal/health/cameras` no campo
`ia2_pool`; a IA2 degradada não derruba a API nem a IA1.

### Configuração

```env
IA2_EXECUTION_MODE=local        # local | central_prefer | central_strict
IA2_CENTRAL_CAMERA_IDS=         # "36,37" ou "*"
IA2_POOL_ENABLED=false
IA2_POOL_WORKER_COUNT=1
IA2_POOL_MAX_QUEUE_SIZE=64
IA2_POOL_TIMEOUT_MS=1500
IA2_POOL_MAX_CONCURRENCY=1
IA2_TRANSPORT_MODE=http         # http | binary_prefer | binary_strict
IA2_TRANSPORT_SOCKET_PATH=/run/sunorus/ia2.sock
IA2_TRANSPORT_TIMEOUT_MS=1500
```

Os valores de fila e timeout são iniciais e devem ser reajustados com medição de
carga real; não são definitivos.

## Rollback

```env
IA2_EXECUTION_MODE=local
IA2_CENTRAL_CAMERA_IDS=
IA2_POOL_ENABLED=false
IA3_EXECUTION_MODE=local
IA3_CENTRAL_CAMERA_IDS=
IA3_POOL_ENABLED=false
SHADOW_EXECUTION_MODE=local
SHADOW_POOL_ENABLED=false
```

Somente configuração. Sem alteração de banco, de modelo ou de evento.
