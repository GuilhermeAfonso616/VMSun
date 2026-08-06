# Governanca de feature flags

As flags de runtime sao campos booleanos de `app/core/config.py` e podem ser
sobrescritas por variaveis de ambiente. Elas permitem rollout gradual, mas nao
substituem versionamento de banco nem testes.

## Classes

### Infraestrutura

- `CAMERA_GATEWAY_ENABLED`
- `CAMERA_GATEWAY_WORKER_CAPTURE_ENABLED`
- `CAMERA_GATEWAY_WORKER_RTSP_FALLBACK_ENABLED`
- `WEBRTC_GATEWAY_ENABLED`
- `INFERENCE_POOL_ENABLED`
- `REVALIDATOR_POOL_ENABLED`

### Evidencia e armazenamento

- `EVENT_CLIP_VIDEO_ENABLED`
- `EVENT_RETENTION_ENABLED`
- `ONEDRIVE_CLIP_ARCHIVE_ENABLED`
- `NOTIFICATION_DISPATCH_ENABLED`

### Decisao analitica

- `PERSON_REVALIDATOR_ENABLED`
- `FAR_PERSON_REVALIDATOR_ENABLED`
- `STRATEGY3_V2_ENABLED`
- `ANTI_FP_POST_FILTER_ENABLED`
- `VISUAL_REVALIDATION_GATE_ENABLED`
- `EVENT_MATURITY_ENABLED`
- `MOTION_CONFIRM_ENABLED`

### Visualizacao e diagnostico

- `VISUAL_RAW_PUBLISH_ENABLED`
- `VISUAL_PROCESSED_PUBLISH_ENABLED`
- `VISUAL_IA_BOXES_ENABLED`
- `EVENT_RULE_DEBUG_ENABLED`
- `ALERT_IA_INACTIVE_ENABLED`

## Processo de rollout

1. registrar valor anterior, novo valor, cameras afetadas e responsavel;
2. validar primeiro em homologacao;
3. habilitar em modo shadow/audit quando a funcionalidade oferecer esse modo;
4. liberar para uma camera canario;
5. observar alarmes, falsos positivos, latencia e recursos;
6. ampliar por grupo/local;
7. manter criterio objetivo para desligamento imediato.

Flags que alteram a decisao de alarme nao devem ser habilitadas globalmente no
mesmo deploy que introduz seu codigo. Defaults novos devem ser conservadores e
ter teste cobrindo ambiente ausente, `true` e `false`.

## Registro

Para cada release, mantenha junto ao relatorio de homologacao:

- snapshot sanitizado das flags;
- diferenca em relacao a producao;
- horario de cada alteracao;
- usuario responsavel;
- resultado e eventual rollback.
