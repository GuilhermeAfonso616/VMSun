# Eventos de Invasão — Levantamento do Pipeline Atual

Foco: como o evento/alarme de invasão é **gerado, exibido, avaliado e finalizado** hoje, com ênfase
em **filtros anti-FP** e **fusão de tracks/eventos**.

Estado do código analisado: branch `checkpoint/ptz-3d-monitor-20260727` (commit `a7db332`).

---

## 1. Fluxo end-to-end

### 1.1 Geração (worker → regras → evento)

| Etapa | Onde | O que faz |
|---|---|---|
| Detecção (IA1) | `app/analytics/detector.py:107` | YOLO `track(persist=True, tracker=bytetrack.yaml)`; devolve `bbox`, `confidence`, `track_id` (ByteTrack) |
| Extração | `app/runtime/inference_detection.py:434` | Converte resultado em `{track_id, confidence, bbox}` |
| Entrada do pipeline | `app/runtime/worker_frame_processor.py:229` → `app/runtime/events.py:354` | `EventPipeline.process(...)`; se `detections_fresh=False` (motion gate pulou IA1) só alimenta o ring de clipe |
| Re-tracking | `app/analytics_v2/pipeline/event_pipeline.py:190-208` + `tracking/tracker.py` | Cada detecção vira `DetectionCandidate`; `StatefulTracker` associa e mantém máquina de estados `NEW_CANDIDATE → PROBATION → CONFIRMED → SHADOW → TERMINATED` |
| Motion features | `event_pipeline.py:212-237` | Anexa `motion_history` (blobs/área do motion gate) por track, janela de 20 amostras |
| Regras | `app/analytics_v2/rules/pipeline.py:104` | Só tracks `CONFIRMED` chegam às regras: `intrusion_zone`, `loitering`, `line_crossing`, `direction` |
| Gates da regra de invasão | `app/analytics_v2/rules/intrusion_zone.py:182-435` | Sequência: track confirmado → idade → frames visíveis → qualidade → consistência de classe → zona de exclusão → aspect ratio → dentro da ROI → borda → geometria → movimento → dwell → persistência na zona → score final |
| Supressão local | `events/hysteresis.py`, `events/dedupe.py` | Latch de entrada/saída + cooldown por assinatura e por track |
| Saída | `rules/pipeline.py:223` | `AlarmEvent` com `event_id = camera:rule:track:event_type:zone` (`rules/base.py:39`) |
| Eventos de saída | `event_pipeline.py:259` | `person_left`/`person_left_roi` quando o track termina, score fixo `0.9` |

### 1.2 Revalidação e decisão (anti-FP)

Coordenado por `app/runtime/event_revalidation.py` e consolidado em `app/runtime/events.py:407-527`:

1. **IA2** `person_crop_revalidator` — classifica o crop (`person_score` / `not_person_score`).
2. **IA3** `far_person_revalidator` — pessoa distante (`person_far_score`).
3. **Shadows** IA2 v8b/v8c e **IA3 v2 protection** (`ia3_v2_protection_*`) — só auditoria/veto de bloqueio.
4. **Region memory** (`revalidator_region_memory_service`) — histórico de FP/TP por célula da grade (8×6), montado por evento a partir das últimas 25 revisões da câmera.
5. **Consenso** `revalidation/consensus_policy.py` — 5 perfis de bloqueio (`strict`, `balanced`, `ia3_confirmed`, `ia2_dominant`, `ia2_only`).
6. **Strategy3 v2** `revalidation/strategy3_v2.py:479` — decisão principal por *size bucket* (`small`/`medium`/`large`), cruzando IA2 × IA3 × confirmação independente (`ia3` / `tracking` / `tracking_temporal` / `temporal`) × região.
7. **Anti-FP post-filter** `strategy3_v2.py:752` — soma de risco (região HIGH `+0.35`, blacklist `+0.35`, sem persistência temporal `+0.20`, tracking não confirmado `+0.20`, track estático `+0.15`; bônus `ia3_confirmed −0.40`, `fast_motion −0.25`, whitelist `−0.10`) → `SUPPRESS ≥0.70` / `AUDIT ≥0.40` / `LOW_PRIORITY ≥0.20` / `NOTIFY`.
8. **Maturidade** `revalidation/event_maturity.py:95` — score ponderado (frames, duração, movimento, área, detector, qualidade, geometria, 26% revalidador visual) → `ALARM_READY` / `LOW_CONFIDENCE` / `LOW_MOTION` / `FAST_MOTION_PROTECTED` / `CAMERA_MOTION_UNCERTAIN`.
9. **Decisão de alarme** `revalidation/alarm_decision.py:15` — traduz para `ALARM` / `LOW_PRIORITY_ALARM` / `AUDIT` / `SUPPRESS`.
10. **Políticas finais** `runtime/event_alarm_policy.py` — bloqueio por consenso, gate de qualidade visual (`LOG_ONLY` em artefato).

Modo operacional atual (`app/core/config.py:410-473`): `strategy3_v2_mode="block"` e
`anti_fp_post_filter_mode="block"` — ou seja, **a decisão anti-FP já altera runtime** (não é mais shadow).

### 1.3 Fusão / sessão

| Camada | Onde | Chave | Escopo |
|---|---|---|---|
| Histerese | `events/hysteresis.py` | assinatura (inclui `track_id`) | por processo |
| Dedupe + cooldown | `events/dedupe.py` | assinatura e `camera:rule:track` | por processo |
| Sessão de alarme | `events/alarm_session.py` | `camera:rule:event_type:zone` | por processo, em memória |
| Ciclo de vida no banco | `services/alarm_lifecycle.py` | `camera:family:track:geo` | **não é chamado por ninguém** |

A `AlarmSessionPolicy` decide `NOTIFY` / `RENOTIFY` / `UPDATE`; em `UPDATE`, `app/runtime/events.py:195`
marca `is_alarm_active=False` e `status="correlated"` — o evento é gravado mas não notifica.

### 1.4 Persistência e exibição

- `services/event_persistence.py:264` monta a linha `Event` a partir do `metadata` do evento; `correlation_key = event.event_id`.
- `:526` status final = `metadata.final_status` (ou `persisted` / `failed`).
- `:542` **só se `status == "persisted"` e `is_alarm_active`**: notificações (`enqueue_event_notifications`), lockdown (`send_event_if_needed`) e broadcast SSE (`event_broadcaster`).
- Front: `EventSource('/events/stream')` em `app/static/js/monitor_vms.js`, modal rico com crop do bbox e botoeira 1-clique (`docs/ALARM_EXPERIENCE_V2.md`); listagens em `web/routes/event_listing_routes.py` (`/events`, `/events/data`, `/events/review`).

### 1.5 Avaliação humana e finalização

- Revisão/rotulagem: `services/feedback_review_service.py:333` (`record_feedback`) grava `EventFeedback` com `true_positive` / `false_positive` / `expected_event` / `inconclusive`; alimenta *region memory*, coleta de dataset e métricas (`build_feedback_metrics:502`, drift em `:645`).
- Ações do operador: `web/routes/event_actions_routes.py` — `/ack`, `/close`, `/reopen`, `/note`, `/assign`, `/feedback`, `/suggestion`.
- Incidentes/SLA: `services/incident_service.py` + `IncidentTimeline`.
- Ajuste de política: `services/feedback_tuning_service.py:171` gera sugestões e `:509` aplica auto-tuning limitado **por câmera** (perfil da câmera), não nos thresholds globais de anti-FP.

---

## 2. Achados (ordenados por impacto)

### A. O ciclo de vida de alarme no banco está morto — não há fusão persistente
`AlarmLifecycleService` (`services/alarm_lifecycle.py`, 412 linhas: `OPEN_TYPES`/`CLOSE_TYPES`,
`correlation_key` por família+geometria, `finalize_related_alarm`) **não é referenciado em nenhum
ponto do runtime** (só existe a definição e a instância no fim do arquivo).

Consequências:
- `correlation_key` gravado é o `event_id` do analytics, que **inclui `track_id`** → cada track novo cria uma correlação nova no banco.
- `person_left` / `person_left_roi` não fecham o alarme correspondente; viram eventos independentes (score fixo `0.9`).
- `resolved_by_event_id` / `resolved_at` só são preenchidos manualmente pelo operador.
- Toda a fusão real depende da `AlarmSessionPolicy` **em memória do worker**: reiniciou o worker, a sessão se perde e o próximo evento reabre alarme.

### B. Dois trackers em série e o ID do ByteTrack é descartado
`event_pipeline.py:200` passa o `track_id` do ByteTrack apenas como `detection_id`/metadata. O
`MultiStageAssociator` (`tracking/association.py:99`) associa **só** por IoU predito, distância e
embedding — nunca consulta `detection_id`. O `StatefulTracker` cria IDs próprios (`_next_track_id`).

Consequência: um ID switch interno gera um track novo → assinatura nova → **fura histerese e dedupe**
(ambos chaveados por `track_id`) e zera `age_frames`/`visible_frames`, que são exatamente os
insumos de `tracking_confirmed` e `temporal_persistence` no anti-FP. Ou seja, ID switch produz
simultaneamente **evento duplicado** e **evento com menos evidência** (mais chance de cair em
`LOW_PRIORITY`/`SUPPRESS` errado).

### C. Re-ID por aparência é código morto
`DetectionCandidate.embedding` nunca é preenchido (nenhum ponto do runtime escreve `"embedding"`),
e `Track.embedding_mean` nunca é atualizado em `_apply_detection` (só `embedding_latest`). O ramo
`method="reid"` de `association.py:144` é inalcançável na prática — a recuperação de track depende
apenas de distância/IoU, o que é frágil em oclusão (justamente onde nascem os ID switches do item B).

### D. Estado compartilhado entre regras no motor
`rules/pipeline.py:129-139` sobrescreve `self.latch.enter_threshold/exit_threshold` e
`self.deduper.cooldown_seconds` a cada regra avaliada, mas `HysteresisLatch` e `DeduplicationState`
são **instâncias únicas do engine**. Com duas regras ativas (ex.: `intrusion` + `loitering`), a
última regra do laço define os limiares usados pelas comparações seguintes. Funciona por acidente
porque as assinaturas diferem, mas a configuração efetiva depende da ordem do dicionário.

### E. `AlarmSessionPolicy` sem expiração
`self.sessions: dict[str, AlarmSessionRecord]` (`events/alarm_session.py:49`) nunca remove chaves.
Em câmera com muitas zonas/regras isso cresce indefinidamente no processo do worker, e sessões
antigas continuam servindo de base para `rearmed`.

### F. Anti-FP post-filter é aditivo, global e não pode elevar prioridade
- Pesos e thresholds são **globais** (`app/core/config.py:455-473`), sem ajuste por câmera — apesar de existir *region memory* por câmera e auto-tuning por câmera para o perfil analítico.
- Os bônus são fortes: `ia3_confirmed −0.40` + `fast_motion −0.25` zeram até `blacklist +0.35 + região HIGH +0.35`, isto é, **a intenção explícita do operador (blacklist) pode ser anulada** por dois sinais automáticos.
- `strategy3_v2.py:865` rebaixa `NOTIFY → LOW_PRIORITY` quando a decisão da estratégia não foi ACCEPT, mas não existe caminho inverso: o post-filtro só desce, nunca sobe. Um evento com whitelist + IA3 confirmado + tracking sólido nunca supera o teto da estratégia.
- `appearance_confident` usa um único limiar (`0.5`) para desligar as três penalidades de "parado", enquanto os limiares de aceitação por tamanho são bem menores (`small` aceita com IA2 ≥ 0.02) — a faixa entre 0.02 e 0.5 recebe penalidade cheia.

### G. Region memory recalculada por evento, com janela curta
`event_revalidation.py:159-200` consulta `EventFeedback ⋈ Event` (limite `25 × 3` linhas) **para cada
evento** no caminho de geração, e monta a memória do zero. É custo de banco no caminho quente e,
ao mesmo tempo, uma janela pequena demais para estatística de região (25 revisões por câmera).

### H. Eventos de saída herdam evidência degradada
`_generate_exit_events` (`event_pipeline.py:259`) usa `track.bbox_current` de um track já
`TERMINATED` e o frame **atual**, com `event_score=0.9` fixo. Esse par (bbox velho, frame novo)
alimenta IA2/IA3 na revalidação — crop potencialmente vazio classificado como "não pessoa",
poluindo tanto a decisão quanto o *region memory* daquela célula.

### I. Observabilidade existe mas não é agregada
`events/debug.py` emite motivos ricos (`rejected_border`, `suppressed_by_cooldown`,
`suppressed_by_hysteresis`, …) para log, e `explanation` acumula ~30 campos por evento. Não há
contador agregado por motivo/câmera/janela exportado para o dashboard — hoje a análise depende de
grep em log ou dos scripts offline.

---

## 3. Base já disponível para medir refinamento

- `scripts/replay_reviewed_events_current_rules.py` — reprocessa eventos já revisados pelas regras atuais (usa `build_strategy3_v2_review_payload`, `decide_alarm_action`, `evaluate_consensus_block_candidate`) e emite CSV/JSON.
- `scripts/validate_strategy3_v2_anti_fp.py` — roda IA2/IA3 de verdade sobre um export de eventos revisados.
- `services/feedback_review_service.py:502` — precisão operacional, FP rate, top causas de FP, drift 7d × 30d.
- Testes existentes: `tests/analytics_v2/test_strategy3_v2_policy.py`, `test_alarm_decision.py`, `tests/runtime/test_event_alarm_policy.py`, `test_event_revalidation.py`.

Qualquer mudança nos itens abaixo deve ser medida com replay antes/depois nos mesmos eventos rotulados.

---

## 4. Proposta de refinamento

### Trilha 1 — Fusão de tracks (ataca a causa raiz do FP duplicado)

1. **Usar o ID do ByteTrack como âncora de associação.** Em `MultiStageAssociator.match`, antes do
   estágio de IoU, casar `track.metadata["detection_id"]` com `det.detection_id` quando ambos
   existirem e a distância for plausível. Ganho: elimina a maior parte dos ID switches internos,
   estabiliza `age_frames`/`visible_frames` e, por consequência, `tracking_confirmed` e
   `temporal_persistence`.
2. **Corrigir `embedding_mean`** (média móvel em `_apply_detection`) ou remover o ramo `reid` da
   associação. Manter código inalcançável mascara o custo real da oclusão.
3. **Dedupe por objeto, não por track.** Trocar a chave `camera:rule:track` de `DeduplicationState`
   por `camera:rule:zone` + proximidade espacial do footpoint, mantendo `track_id` apenas como
   desempate. Assim o ID switch deixa de furar o cooldown.
4. **Instâncias de latch/deduper por regra** em `IntrusionRuleEngine` (remove o estado compartilhado
   do item D).

### Trilha 2 — Fusão de eventos/alarme persistente

5. **Decidir o destino do `AlarmLifecycleService`**: ou reativar (chamando `build_decision` /
   `apply_decision` / `finalize_related_alarm` em `event_persistence._persist_payload`), ou apagar e
   promover a `AlarmSessionPolicy` a serviço persistente. Recomendação: **promover a sessão**, porque
   ela já é a política real em uso; persistir `AlarmSessionRecord` (tabela ou Redis) resolve o
   restart do worker e dá ao `correlation_key` um valor estável sem `track_id`.
6. **Fechar alarme com `person_left`** ligado à sessão, em vez de gerar evento solto — e não
   revalidar visualmente evento de saída (item H).
7. **TTL nas sessões** (`rearm_clear + margem`), com varredura periódica.

### Trilha 3 — Anti-FP calibrado

8. **Thresholds por câmera**: mover `anti_fp_post_filter_*` e os buckets de `strategy3_v2_*` para um
   *store* por câmera com fallback global (o padrão já existe em `revalidator_policy_store.py` /
   `feedback_tuning_service`). Calibrar com o replay sobre eventos rotulados daquela câmera.
9. **Blacklist deixa de ser somável**: região em blacklist vira piso de decisão (`AUDIT` no máximo),
   não uma parcela de risco que bônus podem anular.
10. **Permitir promoção**: deixar o post-filtro subir `LOW_PRIORITY → NOTIFY` quando houver
    confirmação independente forte (IA3 confirmado + tracking_temporal + whitelist), removendo o
    teto rígido de `strategy3_v2.py:865`.
11. **Region memory como cache**: manter agregado incremental por célula (atualizado no
    `record_feedback`) em vez de recomputar por evento; ampliar a janela histórica.

### Trilha 4 — Instrumentação

12. Contadores agregados por `(camera_id, motivo, decisão)` em janela deslizante, expostos no
    dashboard: quantos eventos morreram em cada gate da regra, quantos em dedupe/histerese, quantos
    em cada decisão do anti-FP. Sem isso, qualquer ajuste de threshold é feito às cegas.

### Ordem sugerida

`1 → 3 → 4` (tracking/dedupe, baixo risco, ganho imediato em duplicatas) →
`12` (medir) → `5/6/7` (fusão persistente) → `8/9/10/11` (calibração anti-FP, sempre com replay
antes/depois).
