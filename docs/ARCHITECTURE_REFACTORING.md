# Reestruturacao arquitetural

Este documento define os limites usados durante a limpeza incremental do projeto.
Cada extracao deve preservar o comportamento externo e terminar com a suite verde.

## Direcao das dependencias

```text
interfaces (api, web, desktop)
             |
             v
casos de uso e servicos de aplicacao
             |
             v
dominio analitico e contratos
             |
             v
adaptadores (banco, RTSP, gateway, arquivos, OneDrive)
```

Uma camada inferior nao deve importar rotas, templates ou view models. Rotas
validam entrada, chamam um caso de uso e traduzem o resultado para HTTP.

## Modulos alvo

- `app/application.py`: fabrica FastAPI e composicao de routers.
- `app/bootstrap.py`: lifecycle e recursos pertencentes ao processo.
- `app/api/routers/`: routers REST separados por dominio.
- `app/web/routes/`: paginas e adaptadores web separados por dominio.
- `app/services/`: casos de uso compartilhados entre API e web.
- `app/runtime/`: captura, inferencia e publicacao, sem dependencia da web.
- `app/analytics_v2/`: regras analiticas puras sempre que possivel.
- `operator-client`: view models pequenos apoiados por servicos testaveis.

## Ordem de migracao

1. Remover regras duplicadas entre API e web.
2. Isolar lifecycle e efeitos colaterais de importacao.
3. Extrair routers web por dominio.
4. Extrair routers da API e seus schemas.
5. Separar persistencia, integracoes e regras nos servicos extensos.
6. Dividir os view models do cliente desktop.
7. Aplicar cobertura, lint, tipagem e CI como gates obrigatorios.

## Criterios para cada etapa

- Nenhuma mudanca silenciosa de contrato HTTP ou de banco.
- Testes novos para o limite extraido.
- Suite Python, Go e build/testes .NET verdes.
- Nenhuma nova captura ampla e silenciosa de excecao.
- Arquivos de interface nao recebem novas regras de negocio.

## Estado atual

Concluido nesta primeira frente:

- fabrica FastAPI em `app/application.py`, sem inicializacao por efeito de import;
- lifecycle de banco, servicos e threads em `app/bootstrap.py`;
- dependencias de autenticacao HTTP em `app/api/dependencies.py`;
- autenticacao, perfis e usuarios em `app/api/routers/auth_user_routes.py`, com
  regras transacionais isoladas em `app/services/user_service.py`;
- consulta e registro de auditoria em `app/api/routers/audit_routes.py`;
- cadastro e listagem de cameras em `app/api/routers/camera_routes.py`;
- configuracao analitica/operacional em
  `app/api/routers/camera_configuration_routes.py`, apoiada por
  `app/services/camera_configuration_service.py`;
- start/stop local e remoto em `app/api/routers/camera_runtime_routes.py`, com
  integracoes isoladas em `app/services/camera_runtime_service.py`;
- descoberta, persistencia e health NVR em `app/api/routers/nvr_routes.py`, com
  regras compartilhadas em `camera_factory.py`, `camera_source_service.py`,
  `nvr_channel_service.py` e `nvr_health_service.py`;
- schemas de cameras e fontes em `app/api/schemas/camera_schemas.py`;
- eventos e revisao em `app/api/routers/event_routes.py`, compartilhando
  `app/services/event_service.py` com as acoes web;
- metricas, sugestoes e politica em `app/api/routers/feedback_routes.py`, com
  transacoes em `app/services/feedback_workflow_service.py`;
- historico e rollback em `app/api/routers/config_history_routes.py`;
- versao e health em `app/api/routers/system_routes.py`;
- integracao Drive em `app/api/routers/drive_routes.py`;
- bootstrap e telemetria do cliente em `app/api/routers/operator_routes.py`, com
  composicao e persistencia em `app/services/operator_service.py`;
- routers de mosaicos/sequencias e backup em `app/api/routers/`;
- proxy do gateway de cameras em `app/web/routes/gateway_routes.py`;
- infraestrutura compartilhada de autenticacao, sessao e templates em
  `app/web/infrastructure.py`;
- paginas, descoberta assincrona e cadastro NVR em
  `app/web/routes/nvr_routes.py`, com view models puros em
  `app/web/nvr_view_models.py` e cache temporario thread-safe em
  `app/services/nvr_discovery_cache.py`;
- descoberta e importacao ONVIF de rede em
  `app/web/routes/camera_network_routes.py`, com apresentacao em
  `app/web/camera_view_models.py` e transacao por dispositivo em
  `app/services/camera_network_service.py`;
- cadastro individual, descoberta de profiles e teste RTSP em
  `app/web/routes/camera_creation_routes.py`, compartilhando persistencia em
  `app/services/camera_source_service.py` e mantendo credenciais temporarias no
  servidor por `app/services/camera_discovery_cache.py`;
- atualizacao, rediscovery e confirmacao da fonte RTSP em
  `app/web/routes/camera_source_routes.py`, com transacao e estado pendente em
  `app/services/camera_source_update_service.py`;
- pagina de detalhes, polling de eventos e diagnostico RTSP em
  `app/web/routes/camera_detail_routes.py`, usando contexto e serializacao
  centralizados em `app/web/camera_detail_presenter.py`; constantes de
  apresentacao compartilhadas vivem em `app/web/presentation_constants.py`;
- formularios operacional, analitico e de movimento em
  `app/web/routes/camera_configuration_routes.py`, com validacao e transacoes
  testaveis em `app/services/camera_configuration_service.py`; erros reutilizam
  o contexto do presenter de detalhes;
- filtros Jinja independentes em `app/web/presentation_filters.py`, registrados
  por `app/web/infrastructure.py` para que routers funcionem sem importar o
  agregador legado;
- start/stop, soft delete, purge e acoes em lote em
  `app/web/routes/camera_operation_routes.py`; transacoes, limpeza de estado e
  ordem de exclusao referencial vivem em
  `app/services/camera_operation_service.py`, enquanto o lifecycle tolerante de
  workers foi consolidado em `app/services/camera_runtime_service.py`;
- snapshots, streams raw/processado/boxed e placeholders MJPEG em
  `app/web/routes/camera_stream_routes.py`, com frame store, runtime remoto,
  preview temporario e liberacao de recursos isolados em
  `app/services/camera_media_service.py`;
- paginas de monitor e mosaicos, polling JSON, diagnostico WebRTC e tracking
  JSON/SSE em `app/web/routes/monitor_routes.py`; consultas, cache, filtros,
  assinaturas de alarme e serializadores ficam em
  `app/web/monitor_presenter.py`, sempre recebendo a sessao de banco de forma
  explicita;
- central de diagnosticos, aliases legados, integracao OneDrive, tuning,
  controles Docker e backup em `app/web/routes/diagnostics_routes.py`; os
  payloads completos e de shell ficam em `app/web/diagnostics_presenter.py`,
  enquanto metricas compartilhadas com o dashboard vivem em
  `app/web/operational_metrics_presenter.py`;
- comandos mutaveis de gateway, workers, tuning, Docker e resolucao de caminhos
  de backup isolados em `app/services/diagnostics_control_service.py`;
- descoberta, importacao e paginas HikCentral/Hik-Connect em
  `app/web/routes/hik_source_routes.py`; URLs de origem, deduplicacao e criacao
  transacional compartilhadas em `app/services/hik_source_service.py`, com
  health, defaults e mascaramento de segredos em
  `app/web/hik_source_presenter.py`;
- paginas e endpoints do dashboard em `app/web/routes/dashboard_routes.py`,
  com selecao local/remota de historicos em `app/services/dashboard_service.py`;
  a listagem de cameras vive em `app/web/routes/camera_overview_routes.py` e
  seus badges, recomendacoes e filtros em
  `app/web/camera_overview_presenter.py`;
- consulta, filtros e revisao de eventos em
  `app/web/routes/event_listing_routes.py`; filtros persistidos ficam em
  `app/services/event_listing_service.py` e enriquecimento/serializacao da
  tabela em `app/web/event_listing_presenter.py`;
- historico, politica e reenvio Lockdown em
  `app/web/routes/lockdown_routes.py`; consultas e reenvio ficam em
  `app/services/lockdown_delivery_service.py`, com formatacao isolada em
  `app/web/lockdown_presenter.py`;
- login, logout, perfil e aliases da administracao de usuarios em
  `app/web/routes/account_routes.py`; a auditoria de logout reutiliza
  `app/services/user_service.py`;
- pagina detalhada de metricas integrada ao router de cameras, com composicao
  em `app/web/camera_metrics_presenter.py` e sondagem local/remota tolerante a
  falhas em `app/services/camera_metrics_service.py`;
- contratos do aprendizado humano em `app/services/feedback_constants.py`,
  revisao, metricas, fila ativa e drift em
  `app/services/feedback_review_service.py`, e sugestoes, aplicacao automatica
  e rollback em `app/services/feedback_tuning_service.py`;
- modelos, presets, conversao legada e serializacao dos perfis de camera em
  `app/analytics/camera_profile_models.py`; a derivacao de configuracao e das
  politicas de runtime fica em `app/analytics/camera_policy_builder.py`;
- decisoes e schedulers de inferencia temporal e orientada a movimento em
  `app/runtime/inference_scheduling.py`; o adaptador local/central e a
  recuperacao do detector ficam em `app/runtime/inference_detection.py`, e a
  fila, distribuicao, backpressure e singletons em
  `app/runtime/inference_pool.py`;
- historico JPEG, selecao temporal pre/pos-evento, escrita atrasada e fallback
  sincrono da persistencia em `app/runtime/event_clip_buffer.py`; o pipeline de
  eventos apenas coordena esse componente e mantem wrappers compativeis;
- snapshots de perfil/politica, qualidade visual, congelamento do bbox e
  metadados temporais da evidencia em `app/runtime/event_evidence.py`, sempre
  executados antes das fases de revalidacao e decisao de alarme;
- maturidade, confirmacao por movimento, precedencia de bloqueios por consenso
  e gate de artefatos visuais em `app/runtime/event_alarm_policy.py`; o pipeline
  conserva logs, cancelamentos, sessoes e persistencia como efeitos externos;
- sequencia IA2, IA3, shadows, protecao IA3 v2, candidatos de consenso, memoria
  regional e Strategy3 em `app/runtime/event_revalidation.py`, retornando um
  resultado tipado para as politicas e efeitos do pipeline;
- throttle, overlay, normalizacao e escrita raw/processada no frame store em
  `app/runtime/worker_visual_publisher.py`; o worker fornece scheduler,
  dependencias e callback de renderizacao explicitamente;
- thread de captura, abertura, leitura, reconexao e entrega no mailbox em
  `app/runtime/worker_capture_stage.py`; callbacks estreitos atualizam saude,
  ultimo frame e metadados de restart sem acoplar o componente ao worker;
- composicao tipada, coleta de estatisticas auxiliares, conversao de timestamps
  e publicacao tolerante a falhas em `app/runtime/worker_metrics_reporter.py`;
  o loop fornece apenas snapshots de timing, frame, analytics e estado;
- ordem por frame entre geometria, preprocessamento, scheduler, motion gate,
  inferencia, eventos e selecao visual em
  `app/runtime/worker_frame_processor.py`; o estado dos ultimos tracks fica no
  componente e diferencia skip, backpressure e falha real do detector;
- desenho de overlay em `app/runtime/overlay_renderer.py`, cadencia e
  contadores visuais em `app/runtime/visual_publish_scheduler.py`, e
  serializacao/persistencia do snapshot em
  `app/runtime/worker_metrics_publisher.py`; `app/runtime/output.py` e apenas
  uma fachada de reexports compativeis;
- verificacao de posse da geracao publicada em
  `app/runtime/worker_ownership_guard.py`; status de banco da camera
  (starting/warming_up/running/error/stopped) e o cleanup ordenado de
  encerramento (parar captura, parar persistencia de eventos, liberar
  captura, limpar frame/metrics store e gravar status final, sempre
  condicionados a ownership) em `app/runtime/worker_runtime_status.py`; o
  `run()` do worker apenas orquestra as chamadas;
- regras compartilhadas de auditoria e canais NVR em `app/services/`;
- contratos HTTP com banco temporario, testes de seguranca de credenciais e
  testes unitarios para os limites extraidos.

`app/api/routes.py` agora e somente um agregador de routers e reexports de
compatibilidade. `app/web/web_routes.py` tambem e um agregador puro, sem
consultas, regras de apresentacao ou casos de uso embutidos.

`app/services/feedback_learning_service.py` permanece apenas como fachada de
compatibilidade; consumidores internos usam diretamente o modulo responsavel.
Da mesma forma, `app/analytics/camera_profiles.py` e apenas a fachada publica
compativel para modelos e politicas; o codigo interno importa o limite concreto.
`app/runtime/inference.py` tambem permanece como fachada compativel e concentra
somente warmup, readiness e recuperacao do processo.

`datetime.utcnow()` foi substituido em toda a base por `app.core.timezone.utc_now_naive()`
(mesmo valor naive, sem aviso de depreciacao), em uma etapa isolada da
reorganizacao estrutural.
