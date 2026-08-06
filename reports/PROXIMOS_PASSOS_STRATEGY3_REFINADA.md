# 📌 PRÓXIMOS PASSOS: Strategy 3 Refinada Aprovada

**Status:** ✅ FEEDBACK INTEGRADO E APROVADO  
**Data:** 2026-05-08  
**Recomendação:** Implementar Strategy 3 Refinada

---

## 🎯 O Que Fazer Agora

### Step 1: Revisar Documentação (1-2h)

Leia nesta ordem:

1. ✅ [STRATEGY_3_REFINADA_SUMARIO.md](./STRATEGY_3_REFINADA_SUMARIO.md) (5 min)
   - Entender o problema e solução

2. ✅ [STRATEGY_3_REFINED_3STATES.md](./STRATEGY_3_REFINED_3STATES.md) (20 min)
   - Especificação técnica completa

3. ✅ [STRATEGY_3_EVOLUTION.md](./STRATEGY_3_EVOLUTION.md) (10 min)
   - Comparativa antes vs depois

### Step 2: Validação de Conceito (1h)

- [ ] Entendeu a lógica de 3 estados?
- [ ] Concorda que event 950 é tratado corretamente agora?
- [ ] Confia na zona cinza + IA3 logic?
- [ ] Pronto para implementar?

### Step 3: Implementação (5-6h)

**Arquivos a modificar:**
```
app/core/config.py
  ├─ Adicionar thresholds_accept (3 valores)
  └─ Adicionar thresholds_reject (3 valores) ← NOVO

app/analytics_v2/revalidation/person_crop_revalidator.py
  ├─ Adicionar método _get_adaptive_thresholds()
  ├─ Adicionar método _decide_with_gray_zone()
  └─ Modificar validate() para usar 3 estados

app/analytics_v2/revalidation/far_person_revalidator.py
  └─ Integrar consulta em UNCERTAIN

app/runtime/event_processor.py
  └─ Mapear 5 estados para ações
```

**Estimativa:**
- Config changes: 30 min
- Logic implementation: 2.5h
- Integration: 1h
- Testing: 1.5h
- Total: 5-6h

### Step 4: Testes (2-3h)

```bash
# 1. Unit tests (50+ casos)
pytest tests/analytics_v2/test_person_crop_revalidator_gray_zone.py

# 2. Validar event 950 é tratado como ACCEPT
pytest -k "event_950" tests/

# 3. Integration test (1400 eventos reais)
py -3 scripts/validate_ia_strategy3_refined.py \
  --export-dir "D:/IA2/reviewed_events_export_20260504_134833" \
  --limit 1400

# 4. Comparar vs Strategy 3 Original
# Gerar relatório de diferenças
```

### Step 5: Staging Deployment (4h)

```
1. Deploy em ambiente staging
2. A/B test vs Strategy 3 Original
3. Monitor por 2-4 horas
4. Validar métricas (recall, rejection, UNCERTAIN rate)
5. Preparar rollout em produção
```

---

## 📊 Métricas para Monitorar

```
Durante Staging:

Métrica                    Target      Current (Predicted)
─────────────────────────────────────────────────────────────
Person Recall              ≥ 96%       96-97% ✅
False Negative Rate        ≤ 4%        3-4% ✅
False Positive Rejection   ≥ 80%       82-84% ✅
UNCERTAIN Rate             15-20%      ~18% ✅
Latência P95               < 100ms     ~25ms ✅
IA3 Trigger Rate           ↑ (baseline)~15-20% ↑ ✅
```

---

## 🚀 Timeline Recomendado

```
TODAY:
  ✅ Ler documentação (1-2h)
  ✅ Validar conceito
  
WEEK 1 (Mon-Wed):
  🔧 Implementação (5-6h)
  🧪 Unit tests (2-3h)
  
WEEK 1 (Wed-Thu):
  📊 Integration test em 1400 eventos
  🔄 Comparar vs original
  
WEEK 2 (Mon):
  📈 Staging A/B test (4h)
  ✅ Aprovação para produção
  
WEEK 2 (Tue-Wed):
  🚀 Canary deploy (10% cameras)
  📊 Monitor 24/7
  
WEEK 2 (Thu-Fri):
  ⬆️ Gradual rollout (25% → 50% → 100%)
  📈 Monitor principais métricas
```

---

## 🎁 Documentos de Referência

### Para Desenvolvimento
- [STRATEGY_3_REFINED_3STATES.md](./STRATEGY_3_REFINED_3STATES.md) - Especificação técnica
- [STRATEGY_3_EVOLUTION.md](./STRATEGY_3_EVOLUTION.md) - Comparativa e casos de teste
- [../IMPLEMENTATION_PLAN_STRATEGY3.md](../IMPLEMENTATION_PLAN_STRATEGY3.md) - Plano técnico original (ainda válido com ajustes)

### Para Gestão
- [STRATEGY_3_REFINADA_SUMARIO.md](./STRATEGY_3_REFINADA_SUMARIO.md) - Executivo da mudança
- [QUICK_REFERENCE_2PAGES.md](./QUICK_REFERENCE_2PAGES.md) - 2-page cheat sheet
- [00_INDICE_VISUAL_START_HERE.md](./00_INDICE_VISUAL_START_HERE.md) - Navegação geral

### Para Testes
- [STRATEGY_3_REFINED_3STATES.md](./STRATEGY_3_REFINED_3STATES.md) seção "Exemplos"
- [STRATEGY_3_EVOLUTION.md](./STRATEGY_3_EVOLUTION.md) seção "5 Casos de Teste"

---

## ✅ Checklist Pré-Implementação

Antes de começar o código, confirme:

- [ ] Li [STRATEGY_3_REFINED_3STATES.md](./STRATEGY_3_REFINED_3STATES.md)
- [ ] Entendi os 5 estados (ACCEPT/REJECT/UNCERTAIN/SUPPRESS/AUDIT)
- [ ] Entendi a zona cinza e os 2 thresholds (accept + reject)
- [ ] Concordo que event 950 é tratado corretamente (ACCEPT)
- [ ] Tenho claro os 5 thresholds (accept + reject × 3 tamanhos)
- [ ] Pronto para implementar o código

---

## 🔧 Mudanças Principais em Código

### Config (app/core/config.py)

**Antes:**
```python
person_revalidator_threshold: float = 0.01
```

**Depois:**
```python
# Accept thresholds
person_revalidator_threshold_large_accept: float = 0.15
person_revalidator_threshold_medium_accept: float = 0.08
person_revalidator_threshold_small_accept: float = 0.02

# Reject thresholds (NEW)
person_revalidator_threshold_large_reject: float = 0.03
person_revalidator_threshold_medium_reject: float = 0.02
person_revalidator_threshold_small_reject: float = 0.005
```

### Decision Logic (person_crop_revalidator.py)

**Antes:**
```python
passed = ia2_person >= threshold
```

**Depois:**
```python
# 3 estados + zona cinza
if ia2_person >= threshold_accept:
    decision = ValidationDecision.ACCEPT
elif ia2_person < threshold_reject:
    decision = ValidationDecision.REJECT
else:  # GRAY ZONE
    decision = self._resolve_gray_zone(
        ia2_person, ia3_person, detector_score, bbox_ratio
    )
```

---

## 📝 Exemplo: Teste Unitário

```python
def test_strategy3_refined_event_950_gray_zone_ia3_confirms():
    """Event 950: pessoa real com IA2 fraca, mas IA3 confirma."""
    
    # Input
    bbox_ratio = 1.0  # Pessoa GRANDE
    ia2_person = 0.064  # Gray zone: 0.03 ≤ 0.064 < 0.15
    ia3_person = 0.998  # IA3 FORTE!
    detector_score = 0.72
    
    # Process
    decision = revalidator.validate_with_gray_zone(
        bbox_ratio=bbox_ratio,
        ia2_person=ia2_person,
        ia3_person=ia3_person,
        detector_score=detector_score
    )
    
    # Assert
    assert decision.state == ValidationDecision.ACCEPT
    assert decision.reason == "gray_zone_ia3_confirmed"
    assert decision.confidence == "high"
```

---

## 🎯 Recomendações Finais

1. **Use a nova versão 3-estados:** Muito mais segura
2. **Mantenha backward compatibility:** Threshold único como fallback
3. **Log detalhado:** Incluir motivo de cada decisão
4. **Monitor UNCERTAIN:** Rastrear quantos casos caem em zona cinza
5. **Feedback loop:** Revisar casos UNCERTAIN após 1 semana

---

## 🆘 Suporte

**Dúvidas sobre lógica?**
→ [STRATEGY_3_REFINED_3STATES.md](./STRATEGY_3_REFINED_3STATES.md) seção "Matriz de Decisão"

**Dúvidas sobre implementação?**
→ [STRATEGY_3_EVOLUTION.md](./STRATEGY_3_EVOLUTION.md) seção "Exemplo: 5 Casos de Teste"

**Dúvidas sobre timeline?**
→ This document, seção "Timeline Recomendado"

**Precisa de testes adicionais?**
→ [STRATEGY_3_REFINED_3STATES.md](./STRATEGY_3_REFINED_3STATES.md) seção "Impacto Esperado"

---

## 🎬 Comece Agora!

1. Leia [STRATEGY_3_REFINADA_SUMARIO.md](./STRATEGY_3_REFINADA_SUMARIO.md) (5 min)
2. Leia [STRATEGY_3_REFINED_3STATES.md](./STRATEGY_3_REFINED_3STATES.md) (20 min)
3. Aprove o conceito
4. Comece implementação

---

**Próximos Passos Document v1.0 | 2026-05-08 | Ready to Go** ✅

🚀 **Comece a leitura:** [STRATEGY_3_REFINADA_SUMARIO.md](./STRATEGY_3_REFINADA_SUMARIO.md)
