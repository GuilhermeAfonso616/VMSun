# Gestão operacional de incidentes

Alarmes elegíveis agora seguem um fluxo operacional rastreável na central de eventos. Cada incidente pode ter responsável, prazo de atendimento, comentários, resolução e histórico imutável de ações.

## Fluxo

1. Um alarme ativo nasce com status `new` e recebe SLA conforme a prioridade; o operador também pode criar um incidente manual.
2. Admin, supervisor ou operador pode atribuir o incidente e reconhecê-lo.
3. Comentários são adicionados à linha do tempo com usuário e horário.
4. O fechamento exige responsável e classificação final (`verified_threat`, `false_alarm`, `authorized_activity`, `system_test` ou `other`).
5. Apenas admin ou supervisor pode reabrir; um novo prazo é calculado.
6. Ao vencer o SLA, o monitor marca o incidente como escalado e cria uma notificação do tipo `incident_escalation` para cada canal ativo.

Fechamentos correlacionados pelo pipeline são registrados como `auto_closed`, com resolução `automatic_clear`.

## Contexto, checklist e correlação

Cada incidente possui equipe, prioridade própria e checklist de resposta. Alterações nesses dados entram na linha do tempo. Eventos relacionados podem ser vinculados ao incidente sem alterar snapshot, clipe ou metadados originais; o vínculo usa uma chave de correlação e identifica o evento raiz.

A central apresenta incidentes abertos, sem responsável, vencidos e escalados, além dos tempos médios de reconhecimento e resolução. Incidentes manuais usam uma câmera como contexto operacional e ficam identificados com origem `manual`.

## Prazos padrão

| Severidade | Prazo |
|---|---:|
| Crítica | 5 minutos |
| Alta | 15 minutos |
| Média | 60 minutos |
| Baixa | 240 minutos |

Os valores são configuráveis por `INCIDENT_SLA_CRITICAL_MINUTES`, `INCIDENT_SLA_HIGH_MINUTES`, `INCIDENT_SLA_MEDIUM_MINUTES` e `INCIDENT_SLA_LOW_MINUTES`. O ciclo do monitor usa `INCIDENT_SLA_MONITOR_SECONDS`.

## Permissões

- Admin, supervisor e operador: consultar, atribuir, reconhecer, comentar e fechar.
- Admin e supervisor: todas as ações anteriores e reabertura.
- Viewer: sem acesso à central operacional.

## API

- `GET /api/events/incidents/summary`
- `GET /api/events/incidents/assignees`
- `POST /api/events/incidents`
- `GET /api/events/{id}/incident`
- `GET /api/events/{id}/timeline`
- `POST /api/events/{id}/assign`
- `POST /api/events/{id}/acknowledge`
- `POST /api/events/{id}/comments`
- `POST /api/events/{id}/close`
- `POST /api/events/{id}/reopen`
- `PATCH /api/events/{id}/details`
- `PATCH /api/events/{id}/checklist/{item_id}`
- `POST /api/events/{id}/correlate`

As ações de escrita também geram registros no log de auditoria existente.
