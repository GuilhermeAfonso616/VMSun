# Inventário de dados para treinar IA2 e IA3

Levantamento de quanto material existe além do que já foi usado nos treinos de 21/07/2026.

---

## 1. O que já foi usado

Dataset `merged_2026-07_may_test_plus_july_production`
(`D:\IA2\revalidator\datasets\raw\`), base dos cinco treinos de IA3 de julho e do IA2 v8c:

| Classe | Amostras |
|---|---|
| `not_person` | 2.287 |
| `person` | 1.236 |
| `uncertain` | 81 |
| **total** | **3.604 eventos** (IDs 5–5403) |

Cada evento gera quatro artefatos: `context`, `crops_ia2`, `crops_ia3_far`, `metadata`.

**Câmeras cobertas: 7–70.**

---

## 2. O que existe e ainda não foi usado

### 2.1 Acervo do OneDrive (julho/2026) — material bruto grande, sem rótulo

| Item | Volume |
|---|---|
| Eventos com metadados completos | **3.636** |
| Snapshots | 3.627 |
| Clipes `.mp4` | 2.869 |
| Pares before/after | 527 |
| Eventos com bbox utilizável | 3.474 |

**O achado principal: 2.281 eventos (62,7%) vêm de 19 câmeras que nunca entraram em nenhum treino** —
61, 62, 63, 66, 71, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122, 124, 126, 128, 130.

E são justamente as de maior volume operacional:

| Câmera | Eventos | Já treinada? |
|---|---|---|
| 130 | 599 | ❌ |
| 124 | 346 | ❌ |
| 114 | 322 | ❌ |
| 112 | 275 | ❌ |
| 128 | 194 | ❌ |
| 108 | 151 | ❌ |

Os modelos atuais nunca viram o cenário dessas câmeras — o que explica a heterogeneidade encontrada
em `ANTI_FP_ANALISE_DADOS.md` §3.3 (o mesmo threshold funcionando de formas opostas em câmeras
diferentes).

**Limitação: esse acervo não tem rótulo humano.** São eventos `audit_pending`, ou seja, pendentes de
auditoria. É matéria-prima, não dado de treino ainda.

### 2.2 Casos difíceis já identificáveis sem rotular tudo

Cruzando as pontuações dos dois modelos nos 2.509 eventos que têm IA2 e IA3:

| Situação | Eventos | Interpretação |
|---|---|---|
| IA2 ≥ 0,50 e IA3 < 0,20 | **1.039** | a IA3 provavelmente está errando |
| IA2 < 0,20 e IA3 ≥ 0,50 | 1 | a IA2 provavelmente está errando |

Esses **1.039 casos de discordância são o material mais valioso que existe no acervo** para a IA3 —
são exatamente os erros que causaram a perda dos 28 TP da câmera 25. Rotular esse subconjunto rende
muito mais que rotular 1.039 eventos aleatórios.

### 2.3 Material específico para IA3

| Faixa de `bbox_height_ratio` | Acervo todo | Só câmeras novas |
|---|---|---|
| ≤ 0,08 (gate atual da IA3) | 279 | 177 |
| ≤ 0,15 (gate ampliado) | 1.346 | 727 |

Se o gate da IA3 for ampliado para 0,15 — recomendação de `IA3_CANDIDATO_JULHO.md` — o material de
treino disponível **quase quintuplica**.

### 2.4 Clipes: multiplicador ainda não explorado

Os 2.869 clipes contêm frames *before*, *event* e *after* do mesmo track. Nenhum treino até hoje usou
frames de vídeo — só o snapshot do instante do evento. Extrair 3–5 frames por clipe multiplicaria o
material por 3–5× **com variação temporal real** (mesma pessoa/ruído em poses e iluminações
ligeiramente diferentes), que é exatamente o tipo de augmentation que funciona — ao contrário da
augmentation sintética, que **piorou** os treinos de julho (AUC caiu de 0,88 para 0,65–0,70).

---

## 3. A fonte que provavelmente é a maior — e não foi verificada

O sistema **já coleta crops de treino automaticamente** a cada rotulagem do operador:
`app/services/revalidator_dataset_collector.py` (`collect_false_positive_revalidator_sample`,
`collect_person_revalidator_sample`, `collect_uncertain_revalidator_sample`), chamado por
`feedback_review_service.record_feedback`.

Destino: `datasets/revalidator_feedback/` (`settings.revalidator_feedback_dataset_dir`), que no
compose está montado como `./datasets:/app/datasets` — ou seja, **persistido no servidor**.

O backup local mais recente do banco (`analytics_backup_20260610`) tem **3.338 rótulos até 10/06**.
De 10/06 até hoje (27/07) há **mais de seis semanas de rotulagem** que não estão em nenhuma cópia
local — e é justamente o período em que as câmeras 104–130 entraram em operação.

**Para verificar, rode no servidor:**

```bash
cd /media/srv-sunshield/ANALITICO_SSD/Analitico_Go_V4

# quanto já foi coletado automaticamente
find datasets/revalidator_feedback -type f -name '*.jpg' | wc -l
for c in person not_person uncertain; do
  echo "$c: $(ls datasets/revalidator_feedback/$c/crops 2>/dev/null | wc -l)"
done

# rótulos no banco de produção
docker compose exec -T postgres psql -U analitico -d analitico -c \
  "select label, count(*), min(reviewed_at)::date, max(reviewed_at)::date
   from event_feedback group by 1 order by 2 desc;"

# rótulos por câmera, só do período novo
docker compose exec -T postgres psql -U analitico -d analitico -c \
  "select camera_id, label, count(*) from event_feedback
   where reviewed_at > '2026-06-10' group by 1,2 order by 1,3 desc;"
```

---

## 4. Resumo e recomendação

| Fonte | Volume | Rotulado? | Pronto para treino? |
|---|---|---|---|
| Dataset atual (`merged_2026-07`) | 3.604 | ✅ | já usado |
| Acervo OneDrive julho | 3.636 eventos / 3.627 snaps | ❌ | precisa rotular |
| → só de câmeras inéditas | 2.281 | ❌ | **maior lacuna de cobertura** |
| → discordância IA2×IA3 | 1.039 | ❌ | **maior valor por rótulo** |
| Frames de clipes | ~2.869 × 3–5 | herda do evento | multiplicador |
| `datasets/revalidator_feedback` no servidor | **não verificado** | ✅ | provavelmente o maior ganho |
| Banco de produção 10/06 → hoje | **não verificado** | ✅ | 6+ semanas de rotulagem |

**A resposta curta: sim, há bastante material — mas rótulo é o gargalo, não imagem.** O acervo tem
3.636 eventos novos e nenhum rótulo; o servidor provavelmente tem milhares de rótulos que nunca
foram baixados.

Ordem recomendada:

1. **Verificar o servidor primeiro** (comandos acima). Se `revalidator_feedback` já tiver alguns
   milhares de amostras das câmeras 104–130, o treino pode sair sem rotular nada novo.
2. **Rotular por prioridade, não por volume**: os 1.039 casos de discordância IA2×IA3 e uma amostra
   estratificada das câmeras inéditas. O projeto já tem `build_active_learning_queue`
   (`feedback_review_service.py:620`) para montar essa fila.
3. **Extrair frames dos clipes** apenas dos eventos já rotulados — ganho barato e sem custo de
   rotulagem.
4. **Não repetir a augmentation sintética** — está medido que piorou (`IA3_CANDIDATO_JULHO.md`).
