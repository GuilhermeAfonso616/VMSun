# Supressão de Falsos Positivos — Análise sobre Dados Reais

Análise do histórico em `D:\Onedrive\OneDrive - Office365(a)\Aplicativos\Analitico VMS Clips`.
Scripts reproduzíveis em `scripts/analise_fp/`.

---

## 1. O que existe no acervo

| Fonte | Volume | Período | Serve para |
|---|---|---|---|
| `audit_pending_event_*_event.json` | **3.636 eventos** com metadados completos (strategy3_v2, anti_fp, maturity, sessão) | jul/2026 | Diagnóstico da política **atual** |
| Snapshots / clipes | 3.629 jpg + 2.873 mp4 + 527 pares before/after | — | Auditoria visual, re-treino |
| `analytics.db` (dentro de `server_backup_…`) | **3.338 rótulos humanos** em `event_feedback` | abr–jun/2026 | **Medir FP vs TP de verdade** |

O banco de backup é a peça-chave: **2.015 false_positive, 1.248 true_positive, 75 inconclusive**.
Taxa de FP de **61,8%** — a cada 10 alarmes atendidos, 6 são ruído.

> Ressalva: os dois conjuntos são de períodos e numerações de câmera diferentes (banco: câmeras 7–25;
> JSONs de julho: 40–130). Os thresholds calibrados abaixo valem como **método**; os valores por
> câmera precisam ser recalculados sobre o parque atual.

---

## 2. Diagnóstico da política atual (3.636 eventos de julho)

```
strategy3_v2 ACCEPT ........ 85,0%      anti_fp NOTIFY .......... 83,7%
maturity ALARM_READY ....... 62,8%      is_alarm_active ......... 74,3%
revalidator_canceled ....... 0          consensus canceled ...... 0
```

**Os filtros anti-FP praticamente não estão filtrando.** Nenhum evento foi cancelado por
revalidador ou consenso, e o `afp_risk_score` tem p75 = 0,000.

Motivo, visível na distribuição: `ia2_person` tem **p05 = 0,547** e mediana 0,963. Os limiares de
aceite são `large 0,15 / medium 0,08 / small 0,02` — ou seja, **95% dos eventos entram com IA2 3 a
27× acima do limiar**. O gate de IA2 nunca é atingido.

Outros sinais estruturais:

- `mat_visible_frames` **mediana = 4**, enquanto `event_maturity_min_track_frames = 8`. Quase todo
  evento nasce com metade da evidência temporal exigida — daí 18,5% caírem em `FAST_MOTION_PROTECTED`.
  Isso é sintoma do re-tracking descrito em `EVENTOS_INTRUSAO_LEVANTAMENTO.md` (§2.B).
- Há tracks patológicos: `max visible_frames = 5.191` e `duration = 13.898 s` (3,8 h).
- **41 chaves de sessão** para 3.636 eventos, mas 90,2% das decisões são `NOTIFY` e apenas 2,1%
  `UPDATE` — a sessão rearma em vez de fundir.
- **3.437 `correlation_key` distintas para 3.636 eventos** e 3.437 pares (câmera, track) — confirma
  que cada track vira uma correlação nova.
- Em 35 rajadas (≥3 eventos em ≤60 s na mesma câmera), **o número de tracks é igual ao número de
  eventos** — assinatura clássica de ID switch.

---

## 3. O que realmente separa FP de TP (3.263 eventos rotulados)

Poder discriminante global (AUC; 0,5 = inútil):

| Sinal | AUC | Cobertura |
|---|---|---|
| `ia3_person_far` | **0,866** | 31% dos eventos |
| `ia2_person` | 0,786 | 81% |
| `bbox_h` | 0,666 | 100% |
| hora do dia | 0,654 | 100% |

### 3.1 O horário é o filtro mais barato que existe

| Faixa | Eventos | FP | %FP |
|---|---|---|---|
| **00h–08h** | 926 | 876 | **94,6%** |
| 09h–23h | 2.337 | 1.139 | 48,7% |

Às 02h e 07h a taxa de FP é 99,2%. E **não é uma câmera ruim puxando a média** — o padrão se repete
em quase todas: cam 13 vai de 3,4% de FP no dia para 98,0% à noite; cam 10, de 34,4% para 99,5%.

Mas atenção: existem **50 TP noturnos**, concentrados na cam 7 (29 deles) — provavelmente ronda.
Suprimir a noite inteira seria inaceitável em segurança patrimonial. A leitura correta é
**exigir mais evidência à noite**, não desligar.

### 3.2 A IA3 é excelente — dentro do domínio dela

Onde a IA3 roda, ela é o melhor sinal (AUC 0,997 na cam 9; 1,000 na cam 12). Mas o gate atual
(`far_person_revalidator_max_bbox_height_ratio = 0,08` ou IA2 suspeita) a limita a 31% dos eventos.

E há uma armadilha: aplicar `ia3 < 0,20` sem guardas **perde 3,0% dos TP**. Investigando os TP
perdidos, **28 de 41 eram da câmera 25 com `ia2 = 1,000` e bbox de ~175 px** — pessoa grande, óbvia,
com IA3 baixa porque o modelo é de *pessoa distante* e estava sendo usado fora do domínio.

### 3.3 Nenhum threshold global serve para todas as câmeras

Corte ótimo de IA2 por câmera (perdendo ≤5% dos TP):

| Câmera | Corte IA2 | FP cortados | TP perdidos |
|---|---|---|---|
| 12 | 0,10 | **98,4%** | 0,0% |
| 25 | 0,99 | 86,4% | 1,3% |
| 8 | 0,20 | 50,8% | 4,1% |
| 19 | 0,90 | 39,4% | 0,0% |
| 10 | 0,001 | 34,8% | 1,4% |
| 14 | 0,010 | 17,2% | 3,5% |
| 9 | 0,001 | 1,8% | 0,0% |

Os cortes ótimos variam em **três ordens de grandeza** (0,001 a 0,99). E o sinal que discrimina muda:
IA3 manda nas câmeras 9, 12 e 14; hora do dia na 13; tamanho do bbox na 7, 11 e 15; IA2 na 8 e 25.
Na câmera 9, IA2 tem AUC 0,557 — **é ruído puro** — enquanto IA3 tem 0,997.

### 3.4 Causas declaradas

Poucos operadores preenchem `probable_cause` (62 `vegetation_wind`, 10 `shadow`), mas as notas livres
são eloquentes: **"Bebedouro + galão de água"** repetido em 10 eventos consecutivos — objeto estático
recorrente, exatamente o caso que *region memory* e blacklist deveriam matar. E
*"apesar de haver uma pessoa na imagem, a IA marcou a sala de controle como disparo"* — ROI errada.

---

## 4. Política proposta e resultado medido

### Camada 1 — regra global (segura, sem calibração)

Suprimir/rebaixar quando **não** houver veto de aparência (`ia2 ≥ 0,90`) **e** valer uma das três:

```
A)  hora ∈ [00h, 09h)  E  bbox_h < 80 px
D)  ia3 < 0,20  E  bbox_h < 120 px  E  ia2 < 0,50        (IA3 só dentro do domínio)
E)  maturity_level = LOW_CONFIDENCE  E  hora ∈ [00h, 09h)
```

Resultado sobre os 3.263 eventos rotulados:

| Guarda de IA2 | FP cortados | TP perdidos | FP por TP |
|---|---|---|---|
| sem guarda | 24,8% | 6 (0,5%) | 83 : 1 |
| `ia2 ≥ 0,99` | 24,8% | 2 (0,2%) | 250 : 1 |
| **`ia2 ≥ 0,90`** | **24,2%** | **0 (0,0%)** | **∞** |

**Um quarto de todos os falsos positivos desaparece sem perder um único verdadeiro positivo.**

Validação *leave-one-camera-out*: a regra não causa dano em nenhuma câmera isolada; pior caso é a
cam 15 (1 TP, 0 FP), que a guarda de IA2 ≥ 0,90 já resolve.

### Camada 2 — calibração por câmera

Sobre o que a camada 1 não pegou, aplicar `ia2 < t_cam` (e `bbox_h < h_cam` quando ajudar), com
`t_cam` calibrado no histórico rotulado da própria câmera, limitando a perda a 3% dos TP.

Validação com holdout (calibra na metade dos eventos, mede na outra metade):

| Política | Volume | FP cortados | TP perdidos | Precisão |
|---|---|---|---|---|
| hoje | — | — | — | 38,7% |
| camada 1 | −15,4% | −24,7% | −0,6% | **45,4%** |
| camada 1 + 2 | −25,0% | **−39,7%** | −1,6% | **50,7%** |

Por câmera, no conjunto de teste: cam 25 vai de 73,2% para **97,6%** de precisão; cam 12 de 29,6%
para 51,2%; cam 13 de 83,9% para 90,8%; cam 9 corta 53,7% dos FP sem perder nenhum TP.

### Regra de ouro: rebaixar, não deletar

Todos os números acima tratam "suprimir" como remover da fila. Se em vez disso o evento for
**rebaixado para `low_priority`/`audit`** (o pipeline já tem esses níveis), nenhum TP é perdido de
fato — ele só sai da fila principal de atendimento e continua auditável. Recomendo começar assim.

---

## 5. Plano de implementação

| # | Ação | Onde | Ganho esperado |
|---|---|---|---|
| 1 | **Guarda de aparência**: `ia2 ≥ 0,90` veta qualquer supressão automática | `anti_fp_post_filter` (`strategy3_v2.py:801`) | elimina 100% da perda de TP das regras novas |
| 2 | **Restringir IA3 ao domínio**: só usar `ia3` para rejeitar quando `bbox_h < 120 px` e `ia2 < 0,50` | `evaluate_strategy3_v2` (`strategy3_v2.py:604-664`) | corrige os 28 TP perdidos da cam 25 |
| 3 | **Fator noturno**: janela horária por câmera elevando os limiares (não suprimindo por hora) | novo peso em `anti_fp_post_filter` | −23% FP |
| 4 | **Thresholds por câmera** com fallback global, calibrados pelo feedback | `revalidator_policy_store` + `feedback_tuning_service` | −15% FP adicional |
| 5 | **Ampliar cobertura da IA3** — hoje só 31%; é o melhor sinal disponível | `far_person_revalidator_max_bbox_height_ratio` | mais eventos com o sinal forte |
| 6 | Reduzir ID switch (ver `EVENTOS_INTRUSAO_LEVANTAMENTO.md` §4 trilha 1) | `tracking/association.py` | menos duplicata e mais evidência temporal |
| 7 | Blacklist automática por objeto estático recorrente ("bebedouro") | *region memory* + `anti_fp_patterns_json` | mata FP repetitivo |

Ordem sugerida: **1 → 2** (correções de segurança, ganho imediato, risco nulo) → **3** → medir →
**4 → 5** → **6 → 7**.

---

## 6. Como reproduzir

```bash
cd scripts/analise_fp
py analisa_eventos.py eventos.csv        # consolida os 3.636 JSONs de julho
py rotulados.py                          # cruza analytics.db + feedback -> rotulados.csv
py politicas.py                          # varredura de cortes e políticas isoladas
py politica_final.py                     # política final + leave-one-camera-out
py calibracao_por_camera.py              # calibração por câmera com holdout
```

O caminho da pasta OneDrive está no topo de `analisa_eventos.py` e `rotulados.py`.

---

## 7. Limitações honestas

- Os rótulos são de abr–jun/2026, de um parque de câmeras com numeração diferente do atual. **Os
  valores por câmera precisam ser recalculados**; o método e as regras globais transferem.
- Só 31% dos eventos rotulados têm IA3 e 44% têm `maturity_score` — os eventos mais antigos foram
  gerados por uma versão anterior do pipeline.
- O acervo `audit_pending` contém quase só eventos **aprovados** (83,7% NOTIFY). Ele mostra o que
  passa, não o que já é bloqueado — não serve para medir TP perdido pela política atual.
- Não há rótulo humano para os eventos de julho. Rotular ~300 eventos recentes do parque atual
  fecharia essa lacuna e permitiria calibrar direto nas câmeras em produção.
