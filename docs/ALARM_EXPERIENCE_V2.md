# Experiência de Alerta V2 - Transmissão SSE e Tratativa Rica

Esta documentação descreve a arquitetura e as funcionalidades da nova experiência de alertas do VMS (Fase A), projetada para notificação em tempo real de baixa latência e tratativa operacional eficiente.

---

## 1. Barramento em Tempo Real (SSE - Server Sent Events)

### Arquitetura Backend
- **Módulo**: `app/services/event_broadcaster.py` ([EventBroadcaster](file:///d:/Analitico/app/services/event_broadcaster.py))
- **Endpoint HTTP**: `GET /events/stream` em `app/api/routers/event_routes.py` ([event_routes.py](file:///d:/Analitico/app/api/routers/event_routes.py))
- **Mecanismo**:
  - Sempre que um evento ativo é gravado no banco de dados através do `EventPersistenceService` ([event_persistence.py](file:///d:/Analitico/app/services/event_persistence.py)), o `EventBroadcaster` publica o payload JSON formatado via SSE para a rota `/events/stream`.
  - A conexão suporta envio de *ping* de keep-alive a cada 15 segundos para manter a conexão ativa em proxies e firewalls.

### Integração Frontend
- O painel do VMS ([monitor_vms.js](file:///d:/Analitico/app/static/js/monitor_vms.js)) conecta-se automaticamente via `EventSource('/events/stream')`.
- O tempo de entrega do alerta após detecção pela IA é de **< 50ms**.
- Caso a conexão caia, o navegador gerencia a reconexão automática e o polling HTTP tradicional atua como *fallback*.

---

## 2. Modal de Alerta Enriquecido (Rich Evidence Modal)

### Componentes da Interface (`monitor_vms_new.html`)
1. **Crop de Recorte da IA (HTML5 Canvas)**:
   - O modal lê o campo `bbox_json` do evento e renderiza em um Canvas (`#sunorusLiveAlarmCropCanvas`) o recorte aproximado do intruso (com margem de 15% para contexto).
2. **Indicador de SLA e Severidade**:
   - Exibe o estado do SLA (`ON_TIME`, `AT_RISK`, `OVERDUE`) e severidade visual em destaque no card lateral.
3. **Deduplicação de Sessão por Objeto**:
   - Se chegarem múltiplos alertas da mesma pessoa (`track_id` ou `correlation_key`), o modal ativo atualiza os dados da sessão corrente sem abrir pop-ups empilhados. A tag **"Sessão Ativa"** é exibida no cabeçalho do modal.
4. **Botoeira de Tratativa Rápida (1-Click)**:
   - `[✓ Confirmar Ameaça]`: Altera o status para `acknowledged` / `confirmed`.
   - `[⚡ Atividade Autorizada]`: Finaliza o alarme marcando resolução como atividade autorizada.
   - `[✗ Falso Alarme]`: Encerra o evento (`closed`) com registro para auditoria de falsos positivos.

---

## 3. Guia de Operação para Operadores

1. Ao surgir uma invasão, a sirene é acionada e o modal **Sessão Ativa** surge instantaneamente na tela.
2. O operador verifica o **Crop da Pessoa** e a transmissão **Ao Vivo** da câmera lado a lado.
3. Com apenas 1 clique nos botões inferiores, o operador pode **Confirmar Ameaça**, marcar como **Autorizado** ou registrar **Falso Alarme**, liberando a fila para o próximo atendimento.
