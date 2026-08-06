# Plano: reduzir falsos positivos da regra `intrusion_default`

## Contexto

Em `D:\IA_Rebuild\Analitico VMS Clips` existem 915 eventos exportados
(`audit_pending_event_<id>_event.json` + `_snapshot.jpg`), todos da regra
`rule_id=intrusion_default` / `event_type=person_entered`. Distribuicao por
`status`: alarm 465, audit 220, suppressed 89, low_priority 66, closed 48,
correlated 27.

Esses arquivos sao o payload bruto enviado ao OneDrive **no momento da
criacao do evento** (`app/services/event_persistence.py:312-355` ->
`app/services/onedrive_client.py:226-233`), antes de qualquer revisao
humana. O prefixo `audit_pending` (`app/core/config.py:255`) e literal:
"pendente de revisao". Por isso o campo aninhado
`raw_metadata.strategy3_v2.region.region_memory.current_feedback_label`
esta nulo em todos os 915 arquivos - ainda nao existe rotulo humano
associado a esses eventos especificos.

O rotulo humano real vive na tabela `event_feedback`
(`app/db/models.py:133-143`), gravado por `record_feedback()`
(`app/services/feedback_review_service.py:333-392`) via
`POST /{event_id}/feedback`. O script que fecha esse ciclo para este
export especifico ja existe: `scripts/review_event_clips.py`, cujo
`--source-dir` padrao **e literalmente esta pasta**
(`scripts/review_event_clips.py:1-17,326-338`) - ele toca cada
clipe/snapshot, pede o rotulo ao operador e grava em
`payload["feedback"]["label"]` dentro do proprio `_event.json`
(linhas 198-208).

A regra em si nao e estatica: e montada por camera em
`app/analytics_v2/pipeline/event_pipeline.py:130-144` como um
`RuleConfig(rule_type="intrusion_zone", min_track_age_frames=4,
min_visible_frames=4, min_dwell_ms=300, min_event_score=0.60,
cooldown_seconds=5.0, min_motion_plausibility=0.20,
min_motion_distance_px=2.0, ...)`, com defaults adicionais em
`app/analytics_v2/config/schema.py:75-104`. A avaliacao acontece em
`app/analytics_v2/rules/intrusion_zone.py` (`IntrusionZoneRule.evaluate`,
linhas 182-489). Ha uma camada de revalidacao separada,
`strategy3_v2` (`app/analytics_v2/revalidation/strategy3_v2.py`), que
calcula `region_fp_risk`/`region_fp_risk_score` (linha 349) e decide
accept/suppress (linha 479); a maturidade do evento/decisao de alarme
fica em `app/analytics_v2/revalidation/alarm_decision.py` e
`app/runtime/event_alarm_policy.py`.

Nao existe hoje documentacao do fluxo de tuning de regras
(`docs/ARCHITECTURE_REFACTORING.md`, `docs/ANALITICO_SUPERVISOR.md` e
`CAMERA_GATEWAY_CONTRACT.md` nao mencionam `strategy3_v2`,
`region_fp_risk` ou `maturity_decision`) - isso tambem entra no escopo.

## Objetivo

Reduzir a taxa de falsos positivos da regra `intrusion_default` sem
aumentar falsos negativos (eventos reais suprimidos), usando os 915
eventos ja exportados como base de avaliacao, e deixar o fluxo de tuning
documentado e repetivel para as proximas rodadas.

Fora do escopo: mudar a arquitetura do pipeline de analitico
(`app/analytics_v2/pipeline`), adicionar novas regras (`rule_type`) ou
alterar o modelo de deteccao (IA1). O foco e thresholds/logica da regra
`intrusion_zone` e da camada `strategy3_v2` para esta regra especifica.

## Step 1 - Fechar o rotulo de verdade dos 915 eventos exportados

**Intent**: Rodar `scripts/review_event_clips.py` sobre
`D:\IA_Rebuild\Analitico VMS Clips` para revisar snapshot + clipe de cada
um dos 915 eventos e gravar `true_positive`/`false_positive` em
`payload["feedback"]["label"]`. Se o script nao suportar retomar em lotes
ou nao exportar um CSV consolidado ao final, estender isso nele.

**Acceptance**:
- Os 915 arquivos `_event.json` tem `feedback.label` preenchido (ou
  motivo explicito de exclusao registrado, ex.: clipe corrompido).
- Existe um CSV/relatorio consolidado com `event_id, camera_id, rule_id,
  status, label` gerado ao final da rotulacao.

## Step 2 - Cruzar rotulos com a decisao atual do pipeline

**Intent**: Usar `scripts/replay_reviewed_events_current_rules.py`
e/ou `scripts/validate_strategy3_refined_v2.py` /
`scripts/validate_ia_strategies_comparison.py` sobre o export rotulado
(Step 1) para comparar `label` humano vs `status`/`alarm_decision`/
`strategy3_v2_decision` atual, gerando taxa de falso-positivo e
falso-negativo por regra/camera/scene_profile.

**Acceptance**:
- Relatorio com taxa de FP/FN atual da `intrusion_default`, quebrado por
  `camera_id`, `scene_profile` e `region_fp_risk`.
- Numero total de falsos positivos confirmados coincide (ou explica a
  diferenca) com a percepcao inicial do problema.

## Step 3 - Diagnosticar os padroes dos falsos positivos

**Intent**: A partir do relatorio do Step 2, segmentar os falsos
positivos confirmados por causa provavel (ex.: `nuisance_profile`
- vegetacao/sombra/reflexo -, `region_fp_risk_score` alto, cameras/scene
especificos, motivo de status `suppressed` vs `alarm` indevido) e
identificar quais campos de `IntrusionZoneRule.evaluate`
(`app/analytics_v2/rules/intrusion_zone.py:182-489`) ou de
`strategy3_v2` (`get_region_fp_risk`, linha 349) mais correlacionam com
os erros.

**Acceptance**:
- Lista priorizada (top 3-5) de causas de falso positivo, cada uma
  amarrada a um campo/threshold especifico do codigo.

**Concluido (2026-07-21)** - analise dos 507 eventos rotulados ate a
data (72,8% false_positive) cruzados com os campos internos de decisao.
Causas priorizadas, cada uma verificada no codigo:

1. **Discordancia dos revalidadores-sombra nao e usada na decisao.**
   Quando `ia2_v8b_shadow` e `ia2_v8c_shadow` discordam do baseline,
   99,4% (179/180) e falso positivo confirmado. O sinal e calculado em
   `event.metadata["person_revalidator_shadow_discordance"]`
   (`app/runtime/event_revalidation.py:395-411`) mas nunca era lido por
   `evaluate_strategy3_v2()`.
2. **`independent_confirmation="tracking_temporal"` aceita com motion
   minimo.** `human_motion_score` (calculado em `check_human_motion()`,
   nunca usado como gate) separa bem FP de TP no bucket `medium`
   (TP min=0.625, FP p75=0.484), mas nao era verificado antes de
   `ACCEPT`. Subir `strategy3_v2_medium_ia2_accept_threshold` **nao
   funcionaria** - testado nos dados, FP e TP tem `ia2_person_score`
   igualmente altos nesse bucket.
3. **Loop de aprendizado por regiao incompleto.** So 197 dos 507 eventos
   tinham `region_memory` ja populado no momento da decisao - os rotulos
   manuais nunca foram levados ao banco via
   `scripts/backfill_labels_from_export.py`.
4. **Cameras externas/perimetrais classificadas como `indoor_discreet`.**
   Confirmado visualmente (snapshots) que as 10 cameras mais
   problematicas (40, 42, 43, 44, 45, 48, 55, 56, 58, 65 - 85-100% FP
   cada) sao todas externas/perimetrais, mas `profile_from_camera()`
   (`app/analytics/camera_profile_models.py:634-639`) cai no fallback
   hardcoded `scene_profile="indoor_discreet"` porque
   `camera.analytics_profile_json` nunca foi populado para elas.
5. Causa secundaria menor: `revalidator_skipped` em 7% dos FPs (bug
   tecnico separado, nao tratado nesta rodada).

## Step 4 (parcial) - Ajustes implementados nesta rodada

Implementados: causas 1, 2 e 4 acima (ver
`app/analytics_v2/revalidation/strategy3_v2.py`,
`app/core/config.py`, `scripts/reclassify_outdoor_camera_profiles.py`).
Simulacao sobre os 507 eventos rotulados: dos 183 falsos positivos que
hoje viram `ACCEPT`, 108 (59%) passam a ser rebaixados (73 SUPPRESS, 20
LOW_PRIORITY, 15 AUDIT); dos 100 verdadeiros positivos em `ACCEPT`,
apenas 1 e afetado (rebaixado para AUDIT, nao suprimido - e o mesmo
evento que motivou o piso de resgate por `human_motion_score` no item 1
e o piso de `ia2_person_score>=0.95` no item 2).

Causa 3 (backfill do banco de producao) e causa 4 (aplicar de fato a
reclassificacao via `scripts/reclassify_outdoor_camera_profiles.py`)
ainda precisam rodar contra o banco de producao - nao executadas nesta
sessao porque o `data/analytics.db` local esta vazio (mesma limitacao
documentada em `scripts/backfill_labels_from_export.py`).

## Step 4 - Implementar ajustes de threshold/regra

**Intent**: Para cada causa priorizada no Step 3, ajustar o
`RuleConfig` relevante (`app/analytics_v2/pipeline/event_pipeline.py:130-144`
ou defaults em `app/analytics_v2/config/schema.py:75-104`) e/ou a logica
de `strategy3_v2` (`app/analytics_v2/revalidation/strategy3_v2.py`),
mantendo mudancas isoladas por causa para permitir validar cada uma
separadamente.

**Acceptance**:
- Cada ajuste tem testes unitarios cobrindo o caso que motivou a mudanca
  (`tests/analytics_v2/...`).
- Nenhum teste existente da suite quebra.

**Out of scope**: mudar `rule_type`/arquitetura do pipeline; mudar o
modelo de deteccao (IA1).

## Step 5 - Validar sem regressao de falso negativo

**Intent**: Re-rodar o replay do Step 2 com os ajustes do Step 4 sobre a
mesma base rotulada, comparando taxa de FP/FN antes/depois.

**Acceptance**:
- Taxa de falso positivo cai em relacao ao baseline do Step 2.
- Taxa de falso negativo nao piora (ou piora justificada e aceita
  explicitamente).

## Step 6 - Documentar o fluxo de tuning de regras

**Intent**: Criar um doc curto (`docs/EVENT_RULE_TUNING.md` ou similar)
descrevendo o ciclo: exportar eventos -> rotular
(`review_event_clips.py`) -> medir baseline (`replay_reviewed_events_
current_rules.py`) -> ajustar `RuleConfig`/`strategy3_v2` -> revalidar,
para que a proxima rodada de tuning nao precise redescobrir isso.

**Acceptance**:
- Doc novo referenciando os scripts e arquivos de config corretos
  (validado contra os caminhos deste plano).

## Step 7 - Deploy gradual e monitoramento

**Intent**: Publicar os ajustes primeiro nas cameras/`scene_profile` mais
afetados (Step 3), acompanhar `event_score`/`status` por alguns dias antes
de generalizar para todas as cameras usando a mesma `intrusion_default`.

**Acceptance**:
- Criterio de rollback definido (ex.: se falso-negativo aparecer em
  producao, reverter o `RuleConfig` especifico).
