# Etapa 3 — implementação da inferência auxiliar centralizada

## 1. Estado do repositório no início

- branch: `codex/split-web-runtime`
- último commit: `11932ac` — *checkpoint: valida backbone e acelera recuperacao do canario*
- a Etapa 2 (2A e 2B) estava **no working tree, não commitada**: 43 arquivos
  modificados/novos, incluindo `gateway/frame_transport.go`,
  `app/camera/shared_frame_reader.py` e `app/runtime/inference_transport.py`;
- nenhuma alteração existente foi descartada; nenhum commit foi criado.

## 2. Inventário do fluxo anterior

Levantado no código, não presumido.

| Modelo | Onde é instanciado | Escopo real |
|---|---|---|
| IA2 principal | `person_crop_revalidator.py:490`, singleton `_INSTANCE` | por processo |
| IA3 principal | `far_person_revalidator.py:481`, singleton `_INSTANCE` | por processo |
| IA2 shadow v8b | `event_revalidation.py:74` | por coordenador |
| IA2 shadow v8c | `event_revalidation.py:87` | por coordenador |
| IA3 v2 protection | `event_revalidation.py:102` | por coordenador |

O `EventRevalidationCoordinator` vive dentro do `EventPipeline`
(`worker_base.py:75`), que roda no processo do worker da câmera
(`worker_process.py:130`, `mp.Process`).

**As cinco flags estão ligadas por padrão**, então com N câmeras existem até
**5 × N instâncias** em N processos distintos. O carregamento é lazy
(`_load_model`), portanto a memória só é ocupada após a primeira inferência
daquele tipo naquele worker — a duplicação é proporcional ao uso, não ao número
de workers iniciados.

Sobre o item 19 da especificação ("já existe abstração de pool reutilizável"):
existe `RevalidatorPool` (`app/services/revalidator_pool.py`), mas ele **não é
uma pool central** — é um limitador de concorrência `ThreadPoolExecutor` dentro
do próprio worker, desligado por padrão. Não compartilha modelo entre processos.
A referência arquitetural real é `InferencePool`
(`app/runtime/inference_pool.py`), usada pela IA1.

Backend: Ultralytics YOLO de classificação, device por
`revalidator_pool_device` (padrão `auto`). A decisão ACCEPT/REJECT/UNCERTAIN
não pertence ao revalidador: está em `strategy3_v2.py`, `alarm_decision.py` e
`event_alarm_policy.py`, dentro do worker — fronteira preservada.

## 3. O que foi implementado (Etapa 3A)

### Arquivos novos

- `app/analytics_v2/revalidation/aux_inference_types.py` — tipos versionados de
  request/result com identidade de job, deadline monotônico, prioridades e
  códigos de erro;
- `app/analytics_v2/revalidation/aux_inference_client.py` — interfaces, clientes
  local/central/fallback, seleção canário, factory, métricas e política segura;
- `tests/analytics_v2/test_aux_inference_client.py` — 29 testes;
- `docs/auxiliary_inference_stage3.md` — fluxo anterior, topologia, protocolo,
  modos, rollback.

### Arquivos alterados

- `app/core/config.py` — modos, listas canário e parâmetros de pool para IA2,
  IA3 e shadow, mais transporte auxiliar. Todos com padrão inativo;
- `app/runtime/event_revalidation.py` — as duas chamadas diretas
  (`revalidator.validate` e `far_revalidator.validate`) passaram a ir por
  `_run_ia2` / `_run_ia3`, que montam a requisição tipada e delegam à factory.
  O resto do método não mudou: `revalidation` e `far_revalidation` continuam
  sendo os mesmos objetos nativos de antes;
- `app/runtime/worker_metrics_publisher.py` — estado de execução por câmera;
- `.env.example` e `.env.docker.example` — variáveis documentadas.

### Modos disponíveis

`local` (padrão), `central_prefer`, `central_strict` — independentes para IA2,
IA3 e shadow, com listas canário próprias e `*` suportado.

### Garantias implementadas e testadas

- falha, timeout ou pool ausente **nunca viram REJECT**: produzem resultado
  equivalente a "revalidador não aplicado" (`applied=False`, `passed=None`);
- `central_strict` **não executa o modelo local** — verificado por teste que
  conta chamadas ao revalidador;
- resposta com identidade divergente é descartada e conta como stale;
- `central_prefer` registra fallback com log limitado, contador e
  `fallback_used=True` no resultado.

## 4. Equivalência

`test_equivalencia_local_versus_central` e
`test_equivalencia_ia3_local_versus_central` submetem o mesmo resultado nativo
pelos dois caminhos e comparam score, `passed`, `threshold` e `applied` com
tolerância de `1e-6`, confirmando que apenas o campo `backend` difere.

Ressalva honesta: como a 3A não move modelo, essa é uma equivalência **de
contrato**, não de execução. A equivalência numérica real entre modelo local e
modelo em pool só pode ser medida na 3B/3C, com o mesmo conjunto de crops.

## 5. Testes executados

| Comando | Resultado |
|---|---|
| `pytest tests/analytics_v2/test_aux_inference_client.py` | 29 aprovados |
| `pytest tests/runtime/ tests/analytics_v2/` | 282 aprovados |
| `pytest` (suíte completa) | **791 aprovados, 4 falharam** |
| `python -m compileall -q app tests` | aprovado |
| `go test ./...` | aprovado |
| `docker compose ... config --quiet` | aprovado |

### Sobre as 4 falhas

Nenhuma pertence a esta etapa. Verificado por horário de modificação dos
arquivos exercitados:

| Teste | Arquivo | Modificado em |
|---|---|---|
| `test_close_requires_assignee_and_classification` | `incident_service.py` | 15:03 |
| `test_native_login_without_channel_ptz_evidence_is_not_controllable` | `monitor_ptz_service.py` | 15:38 |
| `test_intelbras_http_without_channel_capability_does_not_report_no_ptz` | `monitor_ptz_service.py` | 15:38 |
| `test_nvr_protocol_and_motorized_focus_do_not_mark_fixed_channel_as_ptz` | `dahua_sdk_worker.py` | 15:51 |

Os arquivos desta etapa foram criados entre **16:01 e 16:06**. As três falhas de
PTZ/SDK vêm do trabalho paralelo em curso nesses módulos; a falha de
`incident_service` já constava como preexistente no relatório da Etapa 2.

## 6. Canário

**Não executado, e não é aplicável nesta etapa.** A 3A não muda onde nada roda:
o padrão continua `local` e não existe pool para exercitar. O canário real
pertence à 3B (IA2) e à 3C (IA3), com `IA2_POOL_ENABLED=true` e uma câmera não
crítica.

Por isso `reports/auxiliary_inference_stage3_canary_test.md` **não foi criado**:
não há medição para registrar, e um relatório vazio de canário seria enganoso.

## 7. Comparações não realizadas

RAM, VRAM, latência e tempo de startup **não foram comparados**, porque nenhum
modelo mudou de lugar. Qualquer número aqui seria ruído de medição. Essas
comparações são o critério de aprovação da 3B/3C e devem ser feitas lá, com o
mesmo conjunto de crops e carga controlada.

## 8. Riscos remanescentes

- a duplicação de modelos por processo **continua existindo**: a 3A prepara a
  saída, não a executa;
- `IA3Request` carrega `base_quality` e os scores da IA2 porque o gate da IA3
  depende deles; se a 3C não respeitar isso, o gate muda de comportamento
  silenciosamente;
- o crop ainda trafega em `metadata` como referência ao frame; na 3B isso
  precisa virar payload binário serializado, sem objeto Python;
- as pools ainda não existem, então `central_prefer` hoje sempre cai para local
  e `central_strict` sempre degrada — comportamento correto e testado, mas que
  só tem utilidade real após a 3B.

## 9. Etapa 3B — pool central da IA2

Commit inicial: `6e234a2` (checkpoint isolado da 3A). Trabalho da 3B no working
tree.

### Decisão de arquitetura

**Opção A — pool como componente do processo principal do runtime.** Motivos:

- é exatamente o padrão já usado pela IA1: `InferencePool` roda no runtime e os
  workers, que são processos filhos, falam com ela por socket Unix
  (`InferenceSocketServer`, Etapa 2B);
- não exige container novo nem alteração de volume: o socket vive dentro do
  container do runtime, e os workers são filhos do mesmo processo;
- health check e ciclo de vida ficam junto do runtime, que já é supervisionado.

Risco aceito: uma falha da IA2 poderia afetar o runtime. Mitigações: fila e
threads próprias (sem head-of-line blocking com a IA1), cada job isolado em
try/except, estados explícitos de degradação, backoff de 30 s em OOM e health
que reporta a IA2 degradada sem derrubar a API.

### Componentes

- `app/runtime/ia2_pool.py` — `IA2Pool`: fila `PriorityQueue` limitada, N threads,
  timeout por job, descarte de job com deadline vencido antes de ocupar a GPU,
  `generation_id`, estados `loading/ready/degraded/failed`, tratamento de OOM com
  backoff, health e métricas;
- `app/runtime/ia2_transport.py` — canal binário próprio em
  `/run/sunorus/ia2.sock`, **separado do socket da IA1** para que as duas não
  compartilhem fila. Cabeçalho de 84 bytes little-endian com identidade completa,
  resposta de 40 bytes + corpo JSON pequeno de metadados.

### Payload: BGR cru, não JPEG

Diferença deliberada em relação à Etapa 2B. Recomprimir o recorte em JPEG
introduziria perda e **quebraria a equivalência** entre local e central, que é
requisito duro desta etapa. Recortes de pessoa são pequenos, então o custo de
banda é aceitável. Há teste garantindo round-trip sem perda.

### Preprocessamento único

`PersonCropRevalidator` ganhou dois métodos públicos:

- `crop_with_quality(frame, bbox)` — a **única** fonte do recorte, usada pelo
  caminho local e pelo cliente central;
- `infer_prepared_crop(crop, quality)` — o corpo que `_validate_direct` já
  executava, agora também é o ponto de entrada da pool.

Assim o preprocessamento não existe em dois lugares e não pode divergir. Nada de
threshold, `imgsz`, device ou interpretação de resultado foi alterado.

O cliente central resolve **localmente** os casos que não tocam o modelo (IA2
desabilitada, frame ausente, bbox inválido), para não ocupar a pool com trabalho
que o caminho local já resolvia sem inferência.

### Testes da 3B

| Arquivo | Testes |
|---|---|
| `tests/runtime/test_ia2_pool.py` | 13 — ciclo de vida, fila cheia, prioridade, timeout, deadline, OOM, geração, health |
| `tests/runtime/test_ia2_central_equivalence.py` | 13 — equivalência em 4 tipos de recorte, identidade divergente, política segura |
| `tests/runtime/test_ia2_transport.py` | 10 + 1 skip — layout, endianness, identidade, round-trip BGR |

Suíte completa: **830 aprovados, 4 falhas preexistentes** (as mesmas de PTZ/SDK/
incidentes já registradas na seção 5).

### Canário

Executado na câmera 37. Resultados em
`reports/auxiliary_inference_stage3b_canary_test.md`. Resumo: pool pronta em
1,235 s, jobs reais atendidos pela pool, zero fallback/timeout/stale, modo
estrito funcionando, rollback confirmado.

**A economia de memória não foi demonstrada** — o worker manteve 740 MB nos dois
modos porque, no baseline, a IA2 lazy nunca chegou a ser carregada. O custo da
pool ficou medido: +76 MB no runtime.

## 10. Classificação

**Etapa 3A: aprovada.** Contrato fechado, comportamento local inalterado, 29
testes próprios e 282 testes de runtime/analytics aprovados, padrão inativo.

**Etapa 3B — IA2: aprovada para canário.** Pool funcional, equivalência coberta
por teste, política segura validada, padrão inativo. Não aprovada para rollout
parcial: faltam teste de falha em ambiente real, quatro câmeras, soak e uma
medição de carga que comprove a economia de memória.

**Etapa 3C — IA3: não implementada.**

**Etapa 3D — shadow: não implementada.**
