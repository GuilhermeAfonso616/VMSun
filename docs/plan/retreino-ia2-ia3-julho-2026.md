# Retreino IA2/IA3 com dados mesclados (maio + julho 2026)

Documento de referência da rodada de retreino feita em 2026-07-21, derivada
do diagnóstico em `docs/plan/reduzir-falsos-positivos-intrusion-default.md`.
Cobre: descoberta do dataset de treino existente em `D:\IA2`, mesclagem com
os 507 eventos rotulados de julho, retreino do IA2 (revalidador de crop) e
do IA3 (revalidador "far", pessoa pequena/distante), e os testes de
combinação dos dois.

## Contexto: o que já existia em D:\IA2

`D:\IA2\revalidator` é o projeto que treinou os modelos hoje em produção
(`person_crop_revalidator_yolo11n_v5.pt`, `_v8b_ultra_conservative.pt`,
`_v8c_curated_safe.pt`, `person_far_revalidator_yolo11n_v1.pt`/`v2.pt` -
paths batem com `app/core/config.py`).

Achado importante: todo o dataset de maio (`datasets/raw/revalidator_training_dataset_20260511_122824`
e afins, ~1055 pessoa / ~1884 não-pessoa) vem de **câmeras de teste**
(`camera_id` 7-25, nomeadas "Teste".."Teste8"), não das câmeras reais do
site de produção atual (40-70). Confirmado por inspeção visual dos
snapshots e pelo CSV do export - zero sobreposição de `camera_id` entre as
duas fontes, exceto a câmera **11** (existe fisicamente nos dois lados,
tratada via coluna `source_dataset` pra não confundir).

## Passo 1 - Mesclar dataset de treino do IA2

Scripts novos em `D:\Analitico\scripts`:
- `collect_revalidator_training_dataset_from_export.py` - gera crops
  (`crops_ia2`, `crops_ia3_far`, `context`, `metadata`) direto dos JSONs
  rotulados em `D:\IA_Rebuild\Analitico VMS Clips` (sem depender do
  backfill pro banco, que ainda não rodou em produção). Resultado: **507
  eventos, 130 pessoa / 369 não-pessoa / 8 inconclusivo, 499 crops
  utilizáveis**.
- `merge_revalidator_datasets.py` - mescla N pacotes no mesmo formato.
  Mesclou maio + julho em
  `D:\IA2\revalidator\datasets\raw\merged_2026-07_may_test_plus_july_production`
  (**1236 pessoa / 2287 não-pessoa / 81 inconclusivo**, zero colisão de
  arquivo).

## Passo 2 - Retreinar o IA2 (revalidador de crop)

Script: `D:\IA2\revalidator\scripts\build_merged_dataset.py` (split por
`(source_dataset, camera_id)` - evita confundir a câmera 11 de maio com a
de julho).

Split de teste escolhido de propósito: `may:14,8,12` (convenção já usada
pro v8c) + **`july:42`** (pior câmera do diagnóstico, 140 eventos, 94% FP
histórico) + `july:67` (melhor câmera, controle).

Treino: `person_crop_revalidator_yolo11n_v8c_curated_safe.pt` (produção)
como base, fine-tune curto (16 épocas, parou na 5, LR baixo) →
`D:\IA2\revalidator\exports\candidates_merged_2026-07\person_crop_revalidator_yolo11n_v8c_curated_safe.pt`.

### Resultado (test set, threshold 0,15)

| | falso positivo | pessoa perdida |
|---|---:|---:|
| v5 produção (thr 0,50) | 119 | 20 |
| v8c atual (thr 0,15) | 62 | 16 |
| **candidato mesclado** | **36** | 20 (vs v5) / 20 (vs v8c) |

**Câmera 42 (o alvo, fora do treino)**: v5 errava 81 de 132 ruídos como
pessoa (61%); v8c original errava 14; **candidato mesclado errou 0**.

Regressão real: 4 pessoas a mais perdidas que o v8c original (concentradas
na câmera 14 de maio + 1 na própria câmera 42), todos os 4 são crops
minúsculos/borrados (fronteira, não erro óbvio) - revisados visualmente em
`D:\IA2\revalidator\reports\merged_2026-07_eval_vs_original_v8c\review_cases\regression_fn\`.

Relatórios completos: `D:\IA2\revalidator\reports\merged_2026-07_eval_vs_v5\`
e `...\merged_2026-07_eval_vs_original_v8c\`.

**Recomendação**: candidato forte pra shadow/audit, não troca direta de
produção sem rodar um tempo e reconferir a regressão.

## Passo 3 - Como o IA3 funciona hoje (levantamento de código)

`app/analytics_v2/revalidation/far_person_revalidator.py` +
`app/core/config.py`:

- **Só é acionado** (`_should_run()`) se: crop largura<80px OU altura<96px
  OU `bbox_height_ratio`<0,08 OU IA2 principal extremamente confiante que
  não é pessoa (`person_score<=0,02` e `not_person_score>=0,98`, com
  qualidade ok e longe da borda).
- Modelo real em produção: `person_far_revalidator_yolo11n_v1.pt`,
  threshold 0,48, imgsz 160.
- O próprio componente rotula sua saída como `"operational_decision":
  "audit_only"` (nunca cancela evento sozinho), mas o **score** entra na
  árvore de decisão do `strategy3_v2` (modo `block`, aplicado de verdade):
  confirma ACCEPT, resgata IA2-rejeitado, ou contribui pra SUPPRESS/AUDIT
  em zona cinza. Thresholds de aceite/rejeição por bucket de tamanho:
  large 0,70/0,25, medium 0,65/0,25, small 0,60/0,20.
- Existe uma segunda camada (`ia3_v2_protection`, modelo v2, threshold
  0,94) **100% sombra hoje** (`ia3_v2_protection_mode="audit"`) - não
  afeta nenhuma decisão, só loga (`"recommended_action": "NO_RUNTIME_CHANGE"`).

## Passo 4 - Teste IA2(novo)+IA3: fusão de score não funciona sozinha

Testado em 3 formas, todas no test set do IA2 mesclado:

1. **OR livre** (aceita se IA2 OU IA3 disser pessoa): péssimo - FP salta de
   36 pra 105+ pra ganhar poucas pessoas.
2. **Resgate só em crop pequeno** (gate por tamanho, sem os gatilhos reais):
   melhor caso resgata 1 pessoa com 0 FP novo (gate ≤60px, IA3≥0,80); gates
   mais soltos custam mais FP do que valem.
3. **Gatilhos e thresholds reais de produção** (replicando `_should_run()`
   e os thresholds por bucket): IA3 aciona em 55,5% dos casos de teste (79%
   do bucket medium!) - usá-lo assim piora tudo: FP 92→137 pra ganhar só 1
   pessoa. Conferido nos 4 casos de regressão específicos: IA3 sozinho só
   acerta 1 dos 4 com confiança (0,98); os outros 3 ele erra ou fica
   incerto (0,08 / 0,25 / 0,41).

**Conclusão**: IA3 sozinho (sem tracking/temporal/qualidade, que ele tem em
produção) não é preciso o suficiente pra servir de resgate direto de
score. Ele precisa do contexto adicional que só existe no pipeline
completo.

## Passo 5 - Retreinar o IA3

### Separação de candidatos de treino

Aplicando os critérios reais de disparo (`_should_run()`) sobre os 3438
crops mesclados: **1943 candidatos (56,5%)**, 297 pessoa / 1646
não-pessoa. Lista completa: `D:\IA2\revalidator\reports\ia3_training_candidates_2026-07-21.csv`.

Achado: câmeras reais de julho contribuem só **13 pessoa / 185
não-pessoa** entre os candidatos (câmera 42: 7/68, 43: 1/30, 45: 0/42,
outras residuais) - sinal positivo real de produção é fino, o grosso do
volume positivo ainda vem de maio.

### Scripts e builds

- `D:\IA2\revalidator\scripts\build_merged_ia3_dataset.py` - monta
  train/val/test a partir da lista de candidatos, split por
  `(source_dataset, camera_id)`: teste = `may:14,8,12` + **`july:43,45`**
  (hard negatives reais de produção, custo baixo de recall - 45 não tem
  nenhuma pessoa candidata, 43 só 1); val = `may:10,11`; treino = resto,
  incluindo `july:42` de propósito (7 pessoa / 68 não-pessoa, o caso mais
  valioso).
- Reaproveita `padded_letterbox`/`apply_transform` de
  `D:\IA2\revalidator\src\build_far_dataset.py` (mesmo letterbox cinza-114
  que `FarPersonRevalidator._letterbox()` usa em produção, e as
  augmentations desenhadas pra "far": compressão JPEG, blur, baixa luz,
  ruído, jitter de escala). **Rotação/shear/perspective/flip-vertical
  propositalmente não usados** - pessoa em CFTV sempre aparece de pé,
  rotacionar geraria exemplo irreal (mesma decisão já tomada no pipeline
  do IA2).
- Configs de treino: `configs/ia3_far_merged_2026-07_noaug.yaml` (controle,
  só letterbox) e `configs/ia3_far_merged_2026-07_aug.yaml` (com
  augmentation).

### Resultado - comparação justa (mesmo preprocess letterbox pros 3, cada um no seu melhor threshold)

| modelo | melhor threshold | FP | FN | **erro total** |
|---|---:|---:|---:|---:|
| v1 original (produção) | 0,73 | 48 | 8 | 56 |
| **sem augmentation** | 0,83 | 6 | 22 | **28** ✅ |
| sem augmentation, mais épocas/paciência (80 ép./pac.20) | 0,80 | 10 | 22 | 32 |
| com augmentation "far" | 0,96 | 2 | 37 | 39 |
| com augmentation, mais épocas/paciência | 0,94 | 6 | 35 | 41 |

**Achados**:
- A augmentation específica de "far" (que fazia sentido em teoria - imita
  degradação real de distância/baixa luz/compressão) **piorou** o
  resultado em vez de ajudar, mesmo com mais épocas/paciência/LR pra dar
  chance de absorver o dado extra.
- Mais épocas/paciência não ajudaram nenhuma variante - ambos os treinos
  convergem de verdade na época 2-3 mesmo com orçamento de 80 épocas/
  paciência 20; treinar mais só piora ligeiramente (fine-tune curto e de
  LR baixo já era a escolha certa).
- **Vencedor**: modelo sem augmentation, treino curto -
  `D:\IA2\revalidator\runs\classify_merged_ia3_2026-07_noaug\person_far_revalidator_yolo11n_merged_2026_07_noaug\weights\best.pt`
  - corta o erro total pela metade (56→28) vs o v1 atual em produção, no
  novo threshold recomendado de **0,83** (bem diferente do 0,48 atual -
  precisa mudar os dois juntos).

## Pendências / próximos passos

1. Rodar `scripts/backfill_labels_from_export.py` contra o banco de
   produção (ainda não rodou - bloqueava só a coleta via SQL, não bloqueou
   nada deste retreino porque tudo leu direto dos JSONs).
2. Promover os candidatos (IA2 mesclado + IA3 sem augmentation) pra
   shadow/audit em produção, acompanhar por um tempo antes de considerar
   substituir v5/v1 de vez.
3. Recalibrar o threshold operacional do IA3 pra ~0,83 se o candidato for
   adotado (junto com o modelo, não separado).
4. Continuar coletando e rotulando eventos das câmeras reais de julho -
   sinal positivo real de produção ainda é fino (13 pessoa pro IA3, 130
   pro IA2).
5. Revisar visualmente os 4 casos de regressão do IA2 mesclado antes de
   qualquer promoção (`review_cases/regression_fn/`).

## Arquivos-chave desta rodada

```
D:\Analitico\scripts\
  collect_revalidator_training_dataset_from_export.py
  merge_revalidator_datasets.py

D:\IA2\revalidator\scripts\
  build_merged_dataset.py                  (split IA2)
  build_merged_ia3_dataset.py              (split + letterbox + aug IA3)

D:\IA2\revalidator\configs\
  ia3_far_merged_2026-07_noaug.yaml
  ia3_far_merged_2026-07_aug.yaml

D:\IA2\revalidator\datasets\raw\
  merged_2026-07_may_test_plus_july_production\

D:\IA2\revalidator\datasets\processed\
  merged_2026-07\                          (IA2)
  merged_ia3_2026-07_noaug\                (IA3 vencedor)
  merged_ia3_2026-07_aug\                  (IA3 descartado)

D:\IA2\revalidator\exports\candidates_merged_2026-07\
  person_crop_revalidator_yolo11n_v8c_curated_safe.pt   (IA2 candidato)

D:\IA2\revalidator\runs\classify_merged_ia3_2026-07_noaug\
  person_far_revalidator_yolo11n_merged_2026_07_noaug\weights\best.pt  (IA3 candidato)

D:\IA2\revalidator\reports\
  ia3_training_candidates_2026-07-21.csv
  merged_2026-07_eval_vs_v5\
  merged_2026-07_eval_vs_original_v8c\
```
