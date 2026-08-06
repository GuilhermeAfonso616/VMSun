# Workflow de Auditoria e Tuning por Feedback (Fase B)

Esta documentação descreve o funcionamento do workflow de auditoria silenciosa, registro de causas prováveis de falsos alarmes e geração de sugestões de tuning de sensibilidade de IA no VMS.

---

## 1. Fila Silenciosa de Auditoria (Audit Queue)

### Conceito
Eventos classificados pelos modelos de revalidação de IA ([alarm_decision.py](file:///d:/Analitico/app/analytics_v2/revalidation/alarm_decision.py)) como `AUDIT` ou `LOW_PRIORITY` continuam sendo gravados e analisados pelo sistema, porém:
- **Não disparam som de alerta nem pop-ups invasivos** no painel do operador de linha de frente.
- São roteados para a **Fila Silenciosa de Auditoria** para análise e revisão por supervisores.

### API de Consulta
- Endpoint: `GET /events/audit-queue`
- Implementação: `list_audit_queue_payloads(db)` em `app/services/event_service.py` ([event_service.py](file:///d:/Analitico/app/services/event_service.py)).
- Retorna os eventos em modo auditoria (`status IN ('audit', 'low_priority', 'processing')` ou `is_alarm_active = False`).

---

## 2. Micro-Feedback com Causas Prováveis

### Causas Padronizadas
Ao encerrar ou classificar um alarme como Falso Positivo (`false_positive`), o operador/supervisor pode selecionar uma das causas padronizadas (`PROBABLE_CAUSES` em `feedback_constants.py`):

| Código | Descrição |
| :--- | :--- |
| `vegetation_wind` | Vento em árvores ou vegetação |
| `shadow` | Sombras em movimento ou variação de iluminação |
| `glass_reflection` | Reflexos de luz em vidros ou superfícies molhadas |
| `headlights` | Faróis de veículos ao fundo |
| `rain` | Chuva forte ou respingos |
| `insects_ir` | Insetos atraídos pelo LED infravermelho |
| `camera_vibration` | Balanço de estrutura / vibração de câmera |
| `small_target` | Alvo pequeno abaixo do limiar operacional |
| `threshold_too_low` | Sensibilidade de detecção muito alta |

### API de Registro de Feedback
- Endpoint: `POST /events/{event_id}/feedback`
- Payload:
  ```json
  {
    "label": "false_positive",
    "probable_cause": "vegetation_wind",
    "operator_note": "Vento forte na árvore à esquerda",
    "auto_suggest": true
  }
  ```

---

## 3. Resumo de Tuning & Sugestões de Regras

### API de Estatísticas e Sugestões
- Endpoint: `GET /events/tuning-summary?camera_id=1`
- Implementação: `get_tuning_summary_payload(db, camera_id)` em [event_service.py](file:///d:/Analitico/app/services/event_service.py).
- Retorna a contagem de falsos alarmes acumulados por causa provável e as sugestões ativas de alteração de parâmetro (`TuningSuggestion`), permitindo ao supervisor aprovar ou rejeitar o ajuste de sensibilidade de IA da câmera.
