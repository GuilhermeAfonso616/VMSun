# 🚀 Quick Reference: Testes e Logicas - 2 PÁGINAS

---

## PÁGINA 1: Decisão em 5 Minutos

### ❓ O Problema
- ❌ Muitos falsos positivos (não_pessoa aceitas erroneamente)
- ❌ Threshold único (0.01) não funciona para pessoa grande vs pequena
- ❌ IA3 está subutilizado (nunca dispara em consenso)

### ✅ A Solução
**Strategy 3: Adaptive Thresholds**

Usar threshold DIFERENTE conforme o tamanho da pessoa:

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│   GRANDE     │   │    MÉDIO     │   │   PEQUENO    │
│ ≥ 0.20       │   │ 0.08-0.20    │   │ < 0.08       │
│              │   │              │   │              │
│ threshold    │   │ threshold    │   │ threshold    │
│   = 0.15     │   │   = 0.08     │   │   = 0.02     │
│              │   │              │   │              │
│ (RIGOROSO)   │   │ (BALANÇO)    │   │(PERMISSIVO)  │
└──────────────┘   └──────────────┘   └──────────────┘
```

### 📊 Resultados

| Métrica | Valor | vs Baseline |
|---|---|---|
| **Person Recall** | 93.2% | -6.8% (aceitável) |
| **Rejeita Não_Pessoa** | 85.2% | +7.7% 🎉 |
| **Manual Review** | 27 casos | -34% (14 casos menos) |
| **Falsos Positivos** | 14.8% | -7.7 pontos |
| **Latência** | ~20ms | ✅ Rápido |

### 🎯 Recomendação
✅ **USE STRATEGY 3 - IMPLEMENTAR AGORA**

Razões:
1. ✅ Melhor trade-off (mais ganho que perda)
2. ✅ Fácil implementar (3 números em config.py)
3. ✅ Baixo risco (reversível em 5 min)
4. ✅ Validado em 450 eventos reais
5. ✅ Lógica intuitiva (pessoa grande = critério rigoroso)

### 🚀 Primeiros Passos

```bash
1. Approve esta análise ← YOU ARE HERE
2. Add 3 thresholds a config.py (15 min)
3. Refactor PersonCropRevalidator.py (1h)
4. Test em 1400 eventos (30 min)
5. A/B test em produção (staging)
6. Deploy gradual (canary → full)
```

---

## PÁGINA 2: Comparativa Técnica

### 4 ESTRATÉGIAS TESTADAS

#### Strategy 1: Weighted Voting
```
score = 0.3×detector + 0.5×ia2 + 0.2×ia3
if score ≥ 0.30: ACCEPT
```
- ✅ 98.3% recall pessoa
- ❌ 69.2% rejeita não_pessoa (muitos FP)
- ⚠️ Use apenas se false negatives são críticos

#### Strategy 2: Cascading Logic
```
Stage 1: IA2 clear cases
  if ia2_person ≥ 0.20: ACCEPT
  if ia2_person < 0.01 AND ia2_not_person ≥ 0.99: REJECT
Stage 2: Ambiguous cases (tamanho + qualidade)
  if bbox_large AND ia2 ≥ 0.10: ACCEPT
  if bbox_medium AND (ia2≥0.08 OR ia3≥0.15): ACCEPT
  if bbox_small AND ia3≥0.10: ACCEPT
```
- ✅ 94.9% recall pessoa
- ✅ 77.5% rejeita não_pessoa
- ⚠️ Complexo, deixar para Phase 2

#### **Strategy 3: Adaptive Thresholds** ⭐⭐⭐
```
if bbox_ratio ≥ 0.20:      threshold = 0.15  # Pessoa grande
elif 0.08 ≤ bbox_ratio:    threshold = 0.08  # Pessoa média
else:                      threshold = 0.02  # Pessoa pequena
if ia2_person ≥ threshold: ACCEPT
```
- ✅ 93.2% recall pessoa
- ✅ 85.2% rejeita não_pessoa 🏆
- ✅ Fácil implementar
- ✅ **RECOMMENDED**

#### Strategy 4: Hybrid Consensus
```
7 regras + benefit_of_doubt
- Rule 1: ia2_person ≥ 0.20 → ACCEPT
- Rule 2: ia2_person ≥ 0.10 AND detector ≥ 0.35 → ACCEPT
- Rule 3: ia3_person ≥ 0.15 → ACCEPT
- Rule 4: ia2_not_person ≥ 0.90 AND ia2_person ≤ 0.10 → REJECT
- Rule 5: ia3_person ≤ 0.05 AND ia2_person ≤ 0.15 → REJECT
- Rule 6: detector ≤ 0.30 AND ia2_person ≤ 0.05 → REJECT
- Rule 7: DEFAULT → benefit_of_doubt (ACCEPT)
```
- ✅ 98.3% recall pessoa
- ❌ 73.1% rejeita não_pessoa (rule 7 deixa passar)
- ❌ Muito complexo

### 📈 MATRIZ DE DECISÃO

```
┌─────────────────────────────────────────┬────────────────┬──────────────┐
│ SE SUA PRIORIDADE É:                    │ ESCOLHA:       │ SCORE        │
├─────────────────────────────────────────┼────────────────┼──────────────┤
│ Minimizar False Negatives                │ Strategy 1, 4  │ ⭐⭐ (risky)  │
│ (ZERO pessoas perdidas)                  │                │              │
├─────────────────────────────────────────┼────────────────┼──────────────┤
│ Minimizar False Positives                │ Strategy 3     │ ⭐⭐⭐ BEST  │
│ (Menos manual review)                    │                │              │
├─────────────────────────────────────────┼────────────────┼──────────────┤
│ Balanço / MVP (safe choice)              │ Strategy 3     │ ⭐⭐⭐ BEST  │
│                                          │                │              │
├─────────────────────────────────────────┼────────────────┼──────────────┤
│ Production robusta (mais tarde)          │ Strategy 2     │ ⭐⭐⭐ Phase2│
│ (Lógica elegante)                        │                │              │
└─────────────────────────────────────────┴────────────────┴──────────────┘
```

### 🔧 IMPLEMENTAÇÃO RÁPIDA

**File: `app/core/config.py`**
```python
# ADD estas linhas:
person_revalidator_threshold_large: float = 0.15
person_revalidator_threshold_medium: float = 0.08
person_revalidator_threshold_small: float = 0.02
```

**File: `app/analytics_v2/revalidation/person_crop_revalidator.py`**
```python
def _get_adaptive_threshold(self, bbox_ratio: float) -> float:
    if bbox_ratio >= 0.20:
        return self.threshold_large
    elif 0.08 <= bbox_ratio < 0.20:
        return self.threshold_medium
    else:
        return self.threshold_small

def validate(self, frame, bbox):
    # ... existing code ...
    frame_height = frame.shape[0] if hasattr(frame, "shape") else 0
    bbox_ratio = self._calculate_bbox_height_ratio(bbox, frame_height)
    threshold = self._get_adaptive_threshold(bbox_ratio)  # ← NEW
    passed = person_score >= threshold  # ← USE adaptive threshold
    # ... rest of method ...
```

### 📊 DADOS DOS TESTES

| Teste | Dataset | Modelos | Eventos | Tempo |
|---|---|---|---|---|
| **Teste 1** | real | IA2 | 150 | 1s |
| **Teste 2** | real | IA1+IA2+IA3 | 300 | 6.2s |
| **TOTAL** | - | - | **450** | **7.2s** |

### ✅ CRITÉRIOS DE SUCESSO

| Métrica | Target | Atingido | Status |
|---|---|---|---|
| Person Recall | ≥90% | 93.2% | ✅ |
| Not_Person Reject | ≥80% | 85.2% | ✅ |
| Latência P95 | <150ms | ~20ms | ✅ |
| Manual Review ↓ | ≥20% | 34% | ✅✅ |
| Risk Level | Baixo | Baixo | ✅ |

### 🎬 TIMELINE

```
TODAY:        ✅ Read & Approve (30 min)
WEEK 1 (Mon): 🔧 Code implementation (3h)
WEEK 1 (Wed): 🧪 Testing 1400 events (1h)
WEEK 1 (Fri): 📊 A/B test staging (2h)
WEEK 2:       🚀 Canary deploy (10%)
WEEK 2+:      📈 Gradual rollout
```

### 📁 ONDE ENCONTRAR

- **Sumário 1-page:** `SUMARIO_EXECUTIVO_1PAGE.md`
- **Relatório completo:** `RELATORIO_FINAL_TESTES_E_LOGICAS.md`
- **Implementação:** `IMPLEMENTATION_PLAN_STRATEGY3.md`
- **Scripts de teste:** `scripts/validate_ia_strategies_comparison.py`
- **Dados brutos:** `reports/ia_strategies_comparison/strategies_comparison_*.csv`
- **Este documento:** `QUICK_REFERENCE_2PAGES.md`

### 🎓 LESSON LEARNED

> **Um threshold não serve para pessoa de 80% vs 5% da imagem.**
> 
> A lógica simples "person_score ≥ 0.01" falha porque:
> - Pessoa GRANDE: muita informação → precisa ser mais exigente
> - Pessoa PEQUENA: pouca informação → precisa ser mais permissiva + IA3 confirma
>
> **Solução: Thresholds adaptativos por contexto (tamanho da bbox)**

---

## DECISION CHECKPOINTS ✅

- [ ] Li este Quick Reference (5 min)
- [ ] Entendi Strategy 3 (10 min)
- [ ] Conferi números e trade-offs (5 min)
- [ ] **APROVO para implementação** ← CLICK HERE

**Próximo Passo:** `IMPLEMENTATION_PLAN_STRATEGY3.md` → Start coding

---

**Quick Reference v1.0 | 2026-05-08 | Ready for Implementation**
