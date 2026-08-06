# Scripts para sincronizar arquivos do Monitor e reiniciar o container web
Write-Host "Iniciando sincronização de arquivos com server-analiticos..." -ForegroundColor Cyan

# Templates
Write-Host "Copiando templates/monitor_vms_new.html..." -ForegroundColor Gray
docker cp templates/monitor_vms_new.html server-analiticos:/app/templates/monitor_vms_new.html

Write-Host "Copiando templates/base.html..." -ForegroundColor Gray
docker cp templates/base.html server-analiticos:/app/templates/base.html

Write-Host "Copiando templates/camera_discovery_confirm.html..." -ForegroundColor Gray
docker cp templates/camera_discovery_confirm.html server-analiticos:/app/templates/camera_discovery_confirm.html
docker cp templates/sdk_test.html server-analiticos:/app/templates/sdk_test.html
docker cp templates/device_lab.html server-analiticos:/app/templates/device_lab.html

# Static JS & CSS
Write-Host "Copiando app/static/js/monitor_vms.js..." -ForegroundColor Gray
docker cp app/static/js/monitor_vms.js server-analiticos:/app/app/static/js/monitor_vms.js

Write-Host "Copiando app/static/css/monitor_vms.css..." -ForegroundColor Gray
docker cp app/static/css/monitor_vms.css server-analiticos:/app/app/static/css/monitor_vms.css

# Application bootstrap
Write-Host "Copiando main.py..." -ForegroundColor Gray
docker cp main.py server-analiticos:/app/main.py

Write-Host "Copiando app/application.py e app/bootstrap.py..." -ForegroundColor Gray
docker cp app/application.py server-analiticos:/app/app/application.py
docker cp app/bootstrap.py server-analiticos:/app/app/bootstrap.py

# Python Routes / Backend files
Write-Host "Copiando app/api/routes.py..." -ForegroundColor Gray
docker cp app/api/routes.py server-analiticos:/app/app/api/routes.py

Write-Host "Copiando app/web/web_routes.py..." -ForegroundColor Gray
docker cp app/web/web_routes.py server-analiticos:/app/app/web/web_routes.py

Write-Host "Copiando contratos e politicas de perfis analiticos..." -ForegroundColor Gray
docker exec server-analiticos mkdir -p /app/app/analytics
docker cp app/analytics/camera_profiles.py server-analiticos:/app/app/analytics/camera_profiles.py
docker cp app/analytics/camera_profile_models.py server-analiticos:/app/app/analytics/camera_profile_models.py
docker cp app/analytics/camera_policy_builder.py server-analiticos:/app/app/analytics/camera_policy_builder.py

Write-Host "Copiando modulos do runtime alterados..." -ForegroundColor Gray
docker exec server-analiticos mkdir -p /app/app/runtime
docker cp app/runtime/__init__.py server-analiticos:/app/app/runtime/__init__.py
docker cp app/runtime/camera_config.py server-analiticos:/app/app/runtime/camera_config.py
docker cp app/runtime/event_alarm_policy.py server-analiticos:/app/app/runtime/event_alarm_policy.py
docker cp app/runtime/event_clip_buffer.py server-analiticos:/app/app/runtime/event_clip_buffer.py
docker cp app/runtime/event_evidence.py server-analiticos:/app/app/runtime/event_evidence.py
docker cp app/runtime/event_revalidation.py server-analiticos:/app/app/runtime/event_revalidation.py
docker cp app/runtime/events.py server-analiticos:/app/app/runtime/events.py
docker cp app/runtime/inference.py server-analiticos:/app/app/runtime/inference.py
docker cp app/runtime/inference_detection.py server-analiticos:/app/app/runtime/inference_detection.py
docker cp app/runtime/inference_pool.py server-analiticos:/app/app/runtime/inference_pool.py
docker cp app/runtime/inference_scheduling.py server-analiticos:/app/app/runtime/inference_scheduling.py
docker cp app/runtime/output.py server-analiticos:/app/app/runtime/output.py
docker cp app/runtime/overlay_renderer.py server-analiticos:/app/app/runtime/overlay_renderer.py
docker cp app/runtime/visual_publish_scheduler.py server-analiticos:/app/app/runtime/visual_publish_scheduler.py
docker cp app/runtime/worker_capture_stage.py server-analiticos:/app/app/runtime/worker_capture_stage.py
docker cp app/runtime/worker_frame_processor.py server-analiticos:/app/app/runtime/worker_frame_processor.py
docker cp app/runtime/worker_metrics_publisher.py server-analiticos:/app/app/runtime/worker_metrics_publisher.py
docker cp app/runtime/worker_metrics_reporter.py server-analiticos:/app/app/runtime/worker_metrics_reporter.py
docker cp app/runtime/worker_visual_publisher.py server-analiticos:/app/app/runtime/worker_visual_publisher.py
docker cp app/runtime/worker_base.py server-analiticos:/app/app/runtime/worker_base.py

Write-Host "Copiando app/services/nvr_channel_service.py..." -ForegroundColor Gray
docker cp app/services/nvr_channel_service.py server-analiticos:/app/app/services/nvr_channel_service.py
docker cp app/services/nvr_discovery_cache.py server-analiticos:/app/app/services/nvr_discovery_cache.py

Write-Host "Copiando dependencias e routers da API..." -ForegroundColor Gray
docker exec server-analiticos mkdir -p /app/app/api/routers
docker exec server-analiticos mkdir -p /app/app/api/schemas
docker cp app/api/dependencies.py server-analiticos:/app/app/api/dependencies.py
docker cp app/api/routers/__init__.py server-analiticos:/app/app/api/routers/__init__.py
docker cp app/api/routers/view_routes.py server-analiticos:/app/app/api/routers/view_routes.py
docker cp app/api/routers/backup_routes.py server-analiticos:/app/app/api/routers/backup_routes.py
docker cp app/api/routers/auth_user_routes.py server-analiticos:/app/app/api/routers/auth_user_routes.py
docker cp app/api/routers/audit_routes.py server-analiticos:/app/app/api/routers/audit_routes.py
docker cp app/api/routers/camera_routes.py server-analiticos:/app/app/api/routers/camera_routes.py
docker cp app/api/routers/camera_configuration_routes.py server-analiticos:/app/app/api/routers/camera_configuration_routes.py
docker cp app/api/routers/camera_runtime_routes.py server-analiticos:/app/app/api/routers/camera_runtime_routes.py
docker cp app/api/routers/nvr_routes.py server-analiticos:/app/app/api/routers/nvr_routes.py
docker cp app/api/routers/event_routes.py server-analiticos:/app/app/api/routers/event_routes.py
docker cp app/api/routers/feedback_routes.py server-analiticos:/app/app/api/routers/feedback_routes.py
docker cp app/api/routers/config_history_routes.py server-analiticos:/app/app/api/routers/config_history_routes.py
docker cp app/api/routers/system_routes.py server-analiticos:/app/app/api/routers/system_routes.py
docker cp app/api/routers/drive_routes.py server-analiticos:/app/app/api/routers/drive_routes.py
docker cp app/api/routers/operator_routes.py server-analiticos:/app/app/api/routers/operator_routes.py
docker cp app/api/schemas/__init__.py server-analiticos:/app/app/api/schemas/__init__.py
docker cp app/api/schemas/camera_schemas.py server-analiticos:/app/app/api/schemas/camera_schemas.py
docker cp app/api/schemas/event_schemas.py server-analiticos:/app/app/api/schemas/event_schemas.py
docker cp app/api/schemas/operator_schemas.py server-analiticos:/app/app/api/schemas/operator_schemas.py
docker cp app/services/audit_service.py server-analiticos:/app/app/services/audit_service.py
docker cp app/video_sources/providers/dahua_http_api.py server-analiticos:/app/app/video_sources/providers/dahua_http_api.py
docker cp app/services/user_service.py server-analiticos:/app/app/services/user_service.py
docker cp app/services/camera_factory.py server-analiticos:/app/app/services/camera_factory.py
docker cp app/services/camera_configuration_service.py server-analiticos:/app/app/services/camera_configuration_service.py
docker cp app/services/camera_runtime_service.py server-analiticos:/app/app/services/camera_runtime_service.py
docker cp app/services/camera_operation_service.py server-analiticos:/app/app/services/camera_operation_service.py
docker cp app/services/camera_media_service.py server-analiticos:/app/app/services/camera_media_service.py
docker cp app/services/diagnostics_control_service.py server-analiticos:/app/app/services/diagnostics_control_service.py
docker cp app/services/hik_source_service.py server-analiticos:/app/app/services/hik_source_service.py
docker cp app/services/dashboard_service.py server-analiticos:/app/app/services/dashboard_service.py
docker cp app/services/camera_metrics_service.py server-analiticos:/app/app/services/camera_metrics_service.py
docker cp app/services/camera_source_service.py server-analiticos:/app/app/services/camera_source_service.py
docker cp app/services/camera_network_service.py server-analiticos:/app/app/services/camera_network_service.py
docker cp app/services/camera_discovery_cache.py server-analiticos:/app/app/services/camera_discovery_cache.py
docker cp app/services/camera_source_update_service.py server-analiticos:/app/app/services/camera_source_update_service.py
docker cp app/services/nvr_health_service.py server-analiticos:/app/app/services/nvr_health_service.py
docker cp app/services/event_service.py server-analiticos:/app/app/services/event_service.py
docker cp app/services/event_listing_service.py server-analiticos:/app/app/services/event_listing_service.py
docker cp app/services/lockdown_delivery_service.py server-analiticos:/app/app/services/lockdown_delivery_service.py
docker cp app/services/feedback_workflow_service.py server-analiticos:/app/app/services/feedback_workflow_service.py
docker cp app/services/feedback_learning_service.py server-analiticos:/app/app/services/feedback_learning_service.py
docker cp app/services/feedback_constants.py server-analiticos:/app/app/services/feedback_constants.py
docker cp app/services/feedback_review_service.py server-analiticos:/app/app/services/feedback_review_service.py
docker cp app/services/feedback_tuning_service.py server-analiticos:/app/app/services/feedback_tuning_service.py
docker cp app/services/operator_service.py server-analiticos:/app/app/services/operator_service.py

Write-Host "Copiando app/web/routes/gateway_routes.py..." -ForegroundColor Gray
docker exec server-analiticos mkdir -p /app/app/web/routes
docker cp app/web/routes/__init__.py server-analiticos:/app/app/web/routes/__init__.py
docker cp app/web/routes/gateway_routes.py server-analiticos:/app/app/web/routes/gateway_routes.py
docker cp app/web/routes/event_actions_routes.py server-analiticos:/app/app/web/routes/event_actions_routes.py
docker cp app/web/routes/nvr_routes.py server-analiticos:/app/app/web/routes/nvr_routes.py
docker cp app/web/routes/camera_network_routes.py server-analiticos:/app/app/web/routes/camera_network_routes.py
docker cp app/web/routes/camera_creation_routes.py server-analiticos:/app/app/web/routes/camera_creation_routes.py
docker cp app/web/routes/camera_source_routes.py server-analiticos:/app/app/web/routes/camera_source_routes.py
docker cp app/web/routes/camera_configuration_routes.py server-analiticos:/app/app/web/routes/camera_configuration_routes.py
docker cp app/web/routes/camera_operation_routes.py server-analiticos:/app/app/web/routes/camera_operation_routes.py
docker cp app/web/routes/camera_stream_routes.py server-analiticos:/app/app/web/routes/camera_stream_routes.py
docker cp app/web/routes/monitor_routes.py server-analiticos:/app/app/web/routes/monitor_routes.py
docker cp app/web/routes/diagnostics_routes.py server-analiticos:/app/app/web/routes/diagnostics_routes.py
docker cp app/web/routes/hik_source_routes.py server-analiticos:/app/app/web/routes/hik_source_routes.py
docker cp app/web/routes/dashboard_routes.py server-analiticos:/app/app/web/routes/dashboard_routes.py
docker cp app/web/routes/camera_overview_routes.py server-analiticos:/app/app/web/routes/camera_overview_routes.py
docker cp app/web/routes/camera_detail_routes.py server-analiticos:/app/app/web/routes/camera_detail_routes.py
docker cp app/web/routes/account_routes.py server-analiticos:/app/app/web/routes/account_routes.py
docker cp app/web/routes/event_listing_routes.py server-analiticos:/app/app/web/routes/event_listing_routes.py
docker cp app/web/routes/lockdown_routes.py server-analiticos:/app/app/web/routes/lockdown_routes.py
docker cp app/web/routes/sdk_test_routes.py server-analiticos:/app/app/web/routes/sdk_test_routes.py
docker cp app/web/routes/device_lab_routes.py server-analiticos:/app/app/web/routes/device_lab_routes.py
docker cp app/services/hik_sdk_lab_service.py server-analiticos:/app/app/services/hik_sdk_lab_service.py
docker cp app/web/infrastructure.py server-analiticos:/app/app/web/infrastructure.py
docker cp app/web/nvr_view_models.py server-analiticos:/app/app/web/nvr_view_models.py
docker cp app/web/camera_view_models.py server-analiticos:/app/app/web/camera_view_models.py
docker cp app/web/camera_detail_presenter.py server-analiticos:/app/app/web/camera_detail_presenter.py
docker cp app/web/monitor_presenter.py server-analiticos:/app/app/web/monitor_presenter.py
docker cp app/web/operational_metrics_presenter.py server-analiticos:/app/app/web/operational_metrics_presenter.py
docker cp app/web/diagnostics_presenter.py server-analiticos:/app/app/web/diagnostics_presenter.py
docker cp app/web/hik_source_presenter.py server-analiticos:/app/app/web/hik_source_presenter.py
docker cp app/web/camera_overview_presenter.py server-analiticos:/app/app/web/camera_overview_presenter.py
docker cp app/web/camera_metrics_presenter.py server-analiticos:/app/app/web/camera_metrics_presenter.py
docker cp app/web/event_listing_presenter.py server-analiticos:/app/app/web/event_listing_presenter.py
docker cp app/web/lockdown_presenter.py server-analiticos:/app/app/web/lockdown_presenter.py
docker cp app/web/presentation_constants.py server-analiticos:/app/app/web/presentation_constants.py
docker cp app/web/presentation_filters.py server-analiticos:/app/app/web/presentation_filters.py

# Restart container
Write-Host "Reiniciando container server-analiticos..." -ForegroundColor Yellow
docker restart server-analiticos

Write-Host "Sincronização concluída com sucesso!" -ForegroundColor Green
