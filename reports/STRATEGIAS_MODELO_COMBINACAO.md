# Estratégias de Combinação IA1 + IA2 + IA3 para Melhorar Validação

## Contexto Atual (Baseline)

**Configuração Padrão:**
- **IA1 (Detector YOLO):** score ≥ 0.25 → pessoa detectada
- **IA2 (PersonCropRevalidator):** threshold = 0.01, apenas auditoria
- **IA3 (FarPersonRevalidator):** threshold = 0.005, disparado em casos "suspeitos"
- **Consensus:** Extremamente restrito (IA2 ≤ 0.05 pessoa E IA3 ≤ 0.005 pessoa)

**Resultado Atual no Dataset Real (150 eventos):**
- Threshold IA2 0.10: 95% recall person (97/102), mas 85% rejeita not_person (7/48 passam)
- IA3 consensus: 0 ativações (política muito restrita)

---

## Problema Diagnosticado

1. **IA2 sozinho não consegue:** não_pessoa passam porque IA2 sozinha é ambígua em muitos casos
2. **IA3 nunca dispara:** triggering logic depende de pessoa pequena (bbox_height_ratio ≤ 0.08) + qualidade
3. **Consensus muito restrito:** requer ambos em concordância forte (IA2 ≤ 0.05, IA3 ≤ 0.005)

---

## Estratégias Propostas

### **Estratégia 1: Ensemble Weighted Voting** 
**Lógica:** Combinar scores dos 3 modelos com pesos

```
score_final = w1 * detector_score + w2 * ia2_person_score + w3 * ia3_person_score

Opções:
A) Simples: w1=0.3, w2=0.5, w3=0.2 (IA2 como decision maker)
B) Detector-first: w1=0.5, w2=0.3, w3=0.2 (respeita detector inicial)
C) IA3-informado: w1=0.2, w2=0.4, w3=0.4 (boosted para person pequenas)
```

**Vantagem:** Melhora recall person (IA3 pessoa_far_score alto = reforça), rejeita not_person melhor
**Desvantagem:** Requer tuning de pesos

---

### **Estratégia 2: Cascading Logic (Sequential Filter)**
**Lógica:** Aplicar modelos em sequência, cada um informa o próximo

```
Stage 1: IA2 com threshold baixo (0.05)
  ├─ Se IA2_person ≥ 0.05: ACEITO (pessoa clara)
  ├─ Se IA2_person < 0.01: REJEITO (não-pessoa clara)
  └─ Se 0.01 ≤ IA2_person < 0.05: passa para Stage 2

Stage 2: IA3 em modo "resolver empate"
  ├─ Se bbox_height_ratio ≤ 0.08: executa IA3
  │  ├─ Se IA3_person ≥ 0.10: ACEITO (IA3 confirmou pessoa)
  │  └─ Senão: REJEITO (não foi confirmada como pessoa)
  └─ Senão: consulta qualidade (blur, brightness, etc)
       └─ Se qualidade OK: ACEITO (benefício da dúvida)
       └─ Senão: REJEITO
```

**Vantagem:** Decisões mais lógicas, aproveita IA3 em casos ambíguos
**Resultado Esperado:** 97-99% recall person, 60-70% rejeita not_person

---

### **Estratégia 3: Adaptive Thresholds baseado em Context**
**Lógica:** Threshold IA2 varia conforme bbox e detector_score

```
bbox_height_ratio = bbox_height / frame_height

Se bbox_height_ratio ≥ 0.20 (pessoa grande):
  IA2_threshold = 0.15 (mais exigente, pessoa clara)
  Dispensa IA3

Se 0.08 ≤ bbox_height_ratio < 0.20 (pessoa médio):
  IA2_threshold = 0.08 (balanço)
  Opcionalmente IA3 para confirmar em caso de dúvida

Se bbox_height_ratio < 0.08 (pessoa pequena):
  IA2_threshold = 0.02 (mais permissivo)
  SEMPRE ativa IA3 (mandatory)
```

**Vantagem:** Reconhece que critérios de "pessoa" variam com tamanho
**Resultado Esperado:** 98% recall person, 75% rejeita not_person

---

### **Estratégia 4: Hybrid Consensus Policy**
**Lógica:** Redefinir quando IA2 + IA3 concordam (menos restritivo que hoje)

```
Para REJEITAR como não_pessoa (block_candidate):
  REGRA 1: IA2_person ≤ 0.10 E IA2_not_person ≥ 0.85
    → REJEITA (IA2 forte em não_pessoa)

  REGRA 2: IA2_person ≤ 0.15 E IA3_triggered E IA3_person ≤ 0.02
    → REJEITA (IA2 fraco + IA3 fraco = consenso não_pessoa)

  REGRA 3: IA2_person ≤ 0.20 E IA2_not_person ≥ 0.75 E IA2_quality_good
    → REJEITA (IA2 forte em não_pessoa + boa qualidade)

  REGRA 4: detector_score ≤ 0.30 E IA2_person ≤ 0.05
    → REJEITA (detector fraco + IA2 fraco = provavelmente artefato)

Para ACEITAR como pessoa:
  REGRA 5: IA2_person ≥ 0.20 
    → ACEITA (IA2 forte em pessoa)

  REGRA 6: IA2_person ≥ 0.10 E detector_score ≥ 0.35
    → ACEITA (IA2 moderada + detector confiante)

  REGRA 7: IA3_triggered E IA3_person ≥ 0.15
    → ACEITA (IA3 confirmou pessoa pequena)
```

**Vantagem:** Mais granular, aproveita múltiplas evidências
**Resultado Esperado:** 96-98% recall person, 70-80% rejeita not_person

---

### **Estratégia 5: Quality Gates + Model Confidence Weighting**
**Lógica:** Usar qualidade da imagem para ponderar confiança dos modelos

```
quality_score = (não_blur * 0.3) + (não_too_bright * 0.3) + (bbox_ratio_ok * 0.4)

Se quality_score < 0.5:
  → Usar threshold mais alto (0.15) - exigir pessoa clara
  
Se 0.5 ≤ quality_score < 0.8:
  → Usar threshold médio (0.08) - balanço normal
  
Se quality_score ≥ 0.8:
  → Usar threshold baixo (0.05) - confiamos em IA2
  
Sempre ativar IA3 se bbox_height_ratio < 0.10 (pessoa muito pequena)
```

**Vantagem:** Adapta rigor conforme confiabilidade dos dados
**Resultado Esperado:** 96% recall person, 75% rejeita not_person

---

## Comparação de Estratégias

| Estratégia | Complexidade | Recall Person | Rejeita Not_Person | Custo Computacional | Fácil Tuning |
|---|---|---|---|---|---|
| **1. Weighted Voting** | Baixa | 97% | 60% | +0% | ⭐⭐ |
| **2. Cascading** | Média | 98% | 70% | -20% (evita IA3 desnecessária) | ⭐⭐⭐ |
| **3. Adaptive Threshold** | Média | 98% | 75% | +0% | ⭐⭐⭐⭐ |
| **4. Hybrid Consensus** | Alta | 96% | 80% | +10% (mais IA3) | ⭐ |
| **5. Quality-Weighted** | Média | 96% | 75% | -15% (usa qualidade existente) | ⭐⭐⭐ |

---

## Recomendação Priorizada

### **Phase 1 (MVP - Fácil ganho):**
✅ **Estratégia 3 (Adaptive Thresholds)**: 
- Implementar em `validate_ia2_export_with_logic_sweep.py`
- Testar com 3 faixas de bbox_height_ratio
- Esperado: 96-98% recall, 75% rejeição
- Effort: ~2 horas
- Risco: Baixo (apenas thresholds, sem nova lógica)

### **Phase 2 (Robusto):**
✅ **Estratégia 2 (Cascading)**:
- Refatorar `consensus_policy.py` para ser sequencial
- Substituir audit-only por decisões reais
- Esperado: 98%+ recall, 70%+ rejeição
- Effort: ~4 horas
- Risco: Médio (mudar paradigma, mas bem testável)

### **Phase 3 (Production):**
✅ **Estratégia 5 (Quality-Weighted)**:
- Integrar quality gates existentes (blur, brightness)
- Reponderar scores com qualidade
- Esperado: 96% recall, 75%+ rejeição, menor overhead
- Effort: ~3 horas
- Risco: Baixo (aproveita código existente)

---

## Próximos Passos

1. **Criar `validate_ia2_adaptive_strategies.py`** - testar Estratégia 3 e 2 em paralelo
2. **Gerar comparativo** - métrica por estratégia no dataset real
3. **Selecionar vencedor** - qual oferece melhor trade-off
4. **Implementar em produção** - integrar ao pipeline principal
5. **Monitoring** - rastrear performance conforme novos dados chegam
