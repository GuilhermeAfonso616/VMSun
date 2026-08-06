# Prompt para retreino IA2 v6 e IA3 v2

Voce vai treinar e validar dois modelos de revalidacao de pessoa para um sistema VMS/CCTV generico.

Dataset supervisionado principal:

`D:\Analitico\revalidator_training_dataset_20260511_122824.zip`

Resumo do dataset:

- Total revisado: 3097 eventos
- Person: 1106
- Not person: 1918
- Uncertain: 73
- Trainable IA2 crops: 2939
- Trainable IA3 far crops: 2943
- Status: 3016 crop_saved, 79 snapshot_missing, 2 context_saved
- Estrutura:
  - `person/crops_ia2`
  - `person/crops_ia3_far`
  - `not_person/crops_ia2`
  - `not_person/crops_ia3_far`
  - `uncertain/*`
  - `events.csv`
  - `manifest.json`
  - `missing_or_partial.csv`

Objetivo:

Treinar uma IA2 v6 generalista e uma IA3 v2 especialista em pessoas pequenas/distantes. Nao treine modelos separados por resolucao de camera. A robustez a resolucao deve vir de normalizacao de crop, diversidade real do dataset e augmentations.

IA2 v6:

- Entrada: crops de `crops_ia2`
- Classes: `person` e `not_person`
- Excluir `uncertain` do treino principal
- Papel: validar pessoa em crop normal, medio ou grande
- Deve ser robusta a camera interna/externa, compressao, blur, brilho, baixa resolucao e objetos verticais estaticos
- Saida esperada: score `person_score` e `not_person_score`

IA3 v2:

- Entrada: crops de `crops_ia3_far`
- Classes: `person` e `not_person`
- Excluir `uncertain` do treino principal
- Papel: validar pessoa pequena, distante, parcial ou com pouca informacao visual
- Deve ser mais especialista em baixa resolucao, pessoa pequena, compressao JPEG, blur e objetos pequenos/verticais falsos
- Saida esperada: score `person_far_score` e `not_person_far_score`

Split obrigatorio:

- Fazer split por camera, nao apenas aleatorio por imagem
- Garantir que algumas cameras fiquem fora do treino para validacao
- Objetivo: medir generalizacao para cameras/ambientes nao vistos
- Usar `events.csv` para camera_id e camera_name

Classes:

- `person`: true_positive
- `not_person`: false_positive
- `uncertain`: inconclusive, nao usar no treino binario principal

Balanceamento:

- O dataset tem mais `not_person` do que `person`
- Usar balanceamento por classe no treinamento
- Evitar que o modelo vire conservador demais e rejeite pessoas reais
- Nao duplicar uma unica camera a ponto de o modelo decorar ambiente

Augmentations recomendadas:

- Resize/scale jitter
- Downscale seguido de upscale
- JPEG compression
- Blur leve e moderado
- Brightness/contrast
- Noise leve
- Pequena translacao/crop jitter
- Nao usar augmentations irreais que destruam a forma humana

Validacao minima:

Medir separadamente para IA2 e IA3:

- Accuracy
- Precision person
- Recall person
- Precision not_person
- Recall not_person
- False positive rate
- False negative rate
- Matriz de confusao
- Curva por threshold
- Metricas por camera
- Metricas por tamanho de crop/bbox quando metadata estiver disponivel

Criterios de aprovacao:

- IA2 v6 deve reduzir falso positivo sem aumentar muito pessoa rejeitada
- IA3 v2 deve proteger pessoa pequena real e rejeitar objeto pequeno/vertical falso
- Nao aprovar se o ganho vier de rejeitar muitas pessoas reais
- Nao aprovar usando apenas split aleatorio; precisa existir resultado por camera holdout

Comparativo obrigatorio:

Comparar contra os modelos atuais:

- IA2 atual: `person_crop_revalidator_yolo11n_v5.pt`
- IA3 atual: `person_far_revalidator_yolo11n_v1.pt`

Rodar replay nos eventos revisados e reportar:

- person -> notify/keep
- person -> suppress/reject
- not_person -> notify/keep
- not_person -> suppress/reject
- Casos com IA2 forte e IA3 discordando
- Casos com IA3 forte protegendo pessoa pequena
- Top falsos positivos restantes
- Top falsos negativos novos

Entrega esperada:

- Modelo IA2 v6 exportado em `.pt`
- Modelo IA3 v2 exportado em `.pt`
- Relatorio Markdown com metricas
- CSV de predicoes por evento
- Threshold sugerido para IA2
- Threshold sugerido para IA3
- Lista de exemplos de erro para revisao visual

Politica de decisao:

Nao transformar o treinamento em bloqueio automatico direto. Primeiro rodar em audit/shadow, comparar contra IA2 v5 e IA3 v1, e so depois calibrar Strategy 3 v2 + Anti-FP.
