# IA3 — Candidato de julho/2026 encontrado e avaliado

## Onde estava

Não em `D:\IA_Rebuild` (essa pasta tem análise de movimento e PDFs). Os treinos estão em:

```
D:\IA2\revalidator\runs\classify_merged_ia3_2026-07*\
```

Cinco treinos de **21/07/2026**, todos posteriores à v2 em produção (11/05/2026). Objetivo declarado
no `config_snapshot`: *"reduzir aceite de ruído (not_person_far → person_far) nas câmeras reais de
julho, sem piorar recall"*.

Nenhum deles foi avaliado no split de teste, exportado (`exports/candidates_merged_ia3*` não existe)
nem promovido — por isso ficaram para trás.

| Run | Estratégia | acc. val | Situação |
|---|---|---|---|
| `person_far_revalidator_yolo11n_merged_2026_07` | dataset mesclado maio+julho | **0,888** | melhor |
| `..._aug` / `..._aug_v2` | + augmentation "far" | 0,855 / 0,837 | piorou |
| `..._noaug` / `..._noaug_v2` | controle, letterbox sem aug | 0,834 / 0,850 | piorou |

As tentativas de melhorar via augmentation **degradaram** o resultado. O melhor é o primeiro treino,
das 14:56.

Base de treino: `person_far_revalidator_yolo11n_v1.pt`, yolo11n-cls, imgsz 160, 80 épocas
(parou na 15 por early stopping), AdamW, lr0 1e-5.

## Avaliação (feita agora, não existia)

Todos os modelos rodados no **mesmo** conjunto de teste, mais dois conjuntos cruzados.
Scripts: `scripts/analise_fp/avalia_ia3.py` e `avalia_ia3_cruzado.py`.

### Câmeras atuais — `merged_ia3_2026-07/test` (46 pessoas, 475 ruídos)

| Modelo | AUC | Ruído rejeitado com recall 100% | com recall 95% |
|---|---|---|---|
| v2 (produção) | 0,843 | 21,7% (corte 0,143) | 34,9% |
| v1 | 0,780 | 23,6% | 37,9% |
| **jul merged** | **0,884** | **41,5%** (corte 0,056) | **70,3%** |

**O candidato rejeita quase o dobro do ruído sem perder uma única pessoa.** Com tolerância de 5% no
recall, o dobro (70,3% contra 34,9%).

### Teste cruzado — domínio antigo `ia3_v2_20260511/test` (194 pessoas, 553 ruídos)

| Modelo | AUC | Ruído rejeitado com recall 95% |
|---|---|---|
| v2 (produção) | **0,880** | **56,2%** |
| v1 | 0,892 | 67,3% |
| jul merged | 0,835 | 38,5% |

**Aqui o candidato regride.** Ele é melhor nas câmeras de julho e pior no perfil de maio — ganhou
especialização, não capacidade geral.

### Safety `far_block` (28 pessoas, 15 ruídos — amostra pequena)

| Modelo | AUC |
|---|---|
| v1 | 0,955 |
| jul merged | 0,767 |
| v2 (produção) | 0,741 |

O candidato é melhor que a v2 também aqui.

## Veredito

O candidato **vale a troca para o parque atual**, mas não é dominante — por isso a recomendação é
promover com medição, não trocar direto:

1. **Rodar como shadow primeiro.** A infra já existe: `ia3_v2_protection_*` em `app/core/config.py:364`
   carrega um segundo modelo far em modo auditoria (`EventRevalidationCoordinator._build_ia3_v2_protection_revalidator`).
   Apontar esse slot para o candidato e comparar as duas pontuações em produção por alguns dias.
2. **Recalibrar o limiar junto.** O threshold atual é `far_person_revalidator_threshold = 0,48`.
   Para o candidato, o ponto de recall 100% fica em **0,056** e o de recall 95% em **0,272**. Trocar o
   modelo mantendo 0,48 desperdiça o ganho. Os limiares de `strategy3_v2_*_ia3_accept/reject_threshold`
   (hoje 0,60–0,70 / 0,20–0,25) também precisam ser refeitos para o novo modelo.
3. **Ampliar cobertura.** A IA3 hoje só roda em 31% dos eventos (`far_person_revalidator_max_bbox_height_ratio = 0,08`).
   Sendo o melhor sinal disponível (ver `ANTI_FP_ANALISE_DADOS.md` §3.2), ampliar o gate rende mais
   que trocar o modelo — e as duas coisas se somam.
4. **Manter a guarda de domínio.** Mesmo com o modelo novo, `ia3` não deve rejeitar evento com
   `bbox_h ≥ 120 px` ou `ia2 ≥ 0,50` — foi o que causou a perda dos 28 TP da câmera 25.

## Ressalvas

- O conjunto de teste de julho tem **apenas 46 exemplos positivos**. O intervalo de confiança é
  largo; o ganho é consistente, mas a magnitude exata não é precisa.
- O split de teste veio do mesmo dataset em que o candidato foi treinado (split separado, mas mesma
  coleta). A comparação justa entre as cinco variantes de julho é sólida; contra v1/v2 há vantagem
  de domínio a favor do candidato — que é exatamente o domínio de produção hoje.
- Há **3.629 snapshots** no acervo do OneDrive que podem ampliar esse conjunto de teste antes da
  promoção definitiva.

## Arquivos

```
candidato : D:\IA2\revalidator\runs\classify_merged_ia3_2026-07\
            person_far_revalidator_yolo11n_merged_2026_07\weights\best.pt   (3,04 MB, 21/07/2026)
produção  : D:\Analitico\models\revalidator_far\person_far_revalidator_yolo11n_v2.pt
config    : app/core/config.py:336-348 (far_person_revalidator_*)
            app/core/config.py:364-369 (ia3_v2_protection_* — slot de shadow)
```
