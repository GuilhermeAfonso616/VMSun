"""Centraliza as configuracoes de runtime lidas do .env e os caminhos do projeto.

Este modulo e o ponto unico para parametros de camera, eventos, Lockdown,
diretorios de logs e ajustes usados tanto no worker quanto na interface web.
"""

import os
import secrets
import sys
import time
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def get_runtime_base_dir() -> Path:
    """
    Retorna a pasta base correta.

    Em execucao local usamos a raiz do projeto.
    No executavel empacotado usamos a pasta do .exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


BASE_DIR = get_runtime_base_dir()


def sqlite_url_for(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _auth_secret_needs_generation(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {
        "",
        "auto",
        "generate",
        "troque_este_secret_em_producao",
        "sunorus_vms_default_secret_key_change_me_in_production",
    }


def _load_or_create_secret_file(key_path: Path) -> str:
    """Cria a chave uma unica vez, inclusive com processos concorrentes."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        if key_path.exists():
            existing = key_path.read_text(encoding="utf-8").strip()
            if not _auth_secret_needs_generation(existing):
                try:
                    key_path.chmod(0o600)
                except OSError:
                    pass
                return existing
            time.sleep(0.05)
            continue

        generated = secrets.token_urlsafe(64)
        try:
            descriptor = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            time.sleep(0.05)
            continue
        try:
            os.write(descriptor, (generated + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return generated
    raise RuntimeError(f"Arquivo de chave invalido ou indisponivel: {key_path}")


class Settings(BaseSettings):
    app_name: str = "Server Analiticos"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    app_base_dir: str = str(BASE_DIR)
    database_url: str = sqlite_url_for(BASE_DIR / "data" / "analytics.db")
    camera_bulk_delete_password: str = "EXCLUIR_TODAS"
    docker_stack_control_enabled: bool = False
    docker_stack_control_password: str = ""

    detector_model_path: str = str(
        BASE_DIR / "models" / "ia1_candidate" / "ia1_vnext_recall_from_hardneg_v3_2_1024.pt"
    )
    # Engine TensorRT (.engine) ou ONNX (.onnx) opcional. Quando preenchido e o
    # arquivo existir, e carregado no lugar do .pt (mesma resolucao, ~1.5-3x de
    # throughput em GPU NVIDIA). Vazio = usa o .pt. Gere com
    # scripts/export_detector_tensorrt.py NA MESMA GPU de producao.
    detector_engine_path: str = ""
    detector_engine_auto_build_enabled: bool = False
    detector_engine_auto_build_dir: str = str(BASE_DIR / "runtime_state" / "tensorrt_engines")
    detector_engine_auto_build_workspace_gb: int = 4
    detector_engine_auto_build_required: bool = False
    detector_engine_runtime_fallback_enabled: bool = True
    # IA1 deve operar como detector/gerador de candidatos, nao como filtro final.
    detect_conf: float = 0.05
    # Mantido em 1024: o modelo IA1 foi treinado em 1024 e reduzir a resolucao
    # degrada o recall de pessoas pequenas/distantes. A economia de GPU vem do
    # motion_gate (sem custo de acuracia), nao de baixar imgsz.
    detect_imgsz: int = 1024
    detect_device: str = "auto"
    detector_fp16_enabled: bool = True
    tracker_config: str = "bytetrack.yaml"

    analytic_gpu_guard_enabled: bool = True
    analytic_gpu_max_memory_mb: int = 5000
    analytic_gpu_max_active_workers: int = 12

    inference_pool_enabled: bool = False
    inference_pool_max_queue_size: int = 16
    inference_pool_job_timeout_seconds: float = 2.0
    inference_pool_max_job_age_seconds: float = 1.0
    inference_pool_overflow_policy: str = "drop_oldest"
    inference_pool_backend: str = "local"
    inference_pool_count: int = 1
    inference_pool_max_cameras_per_pool: int = 8
    inference_pool_central_url: str = ""
    inference_pool_central_jpeg_quality: int = 80
    inference_pool_central_fallback_direct: bool = False

    # novo: resolucao de trabalho separada da resolucao de exibicao
    processing_max_width: int = 960
    processing_max_height: int = 540
    processing_upscale_small_frames: bool = False

    # novo: scheduler do worker normal
    normal_inference_interval_seconds: float = 0.35

    # filtro barato (diff de frames em CPU) antes da inferencia pesada na GPU.
    # Ligado por padrao: cenas paradas caem para keepalive a cada
    # motion_gate_min_interval_seconds; movimento dispara inferencia (ainda
    # limitada pelo normal_inference_interval_seconds via AND no worker).
    motion_gate_enabled: bool = True
    motion_gate_threshold: float = 0.015
    motion_gate_min_interval_seconds: float = 2.0
    motion_gate_downscale_width: int = 320

    # novo: quantos grab() descartar por leitura
    capture_drop_frames: int = 2

    # RTSP
    rtsp_transport: str = "tcp"
    rtsp_open_timeout_ms: int = 8000
    rtsp_read_timeout_ms: int = 8000
    rtsp_max_delay_us: int = 500000
    rtsp_enable_low_latency: bool = True
    rtsp_ffmpeg_log_level: str = "error"

    reconnect_seconds: int = 8
    reconnect_initial_delay_seconds: float = 0.5
    reconnect_backoff_multiplier: float = 1.8
    reconnect_max_backoff_seconds: float = 8.0
    reconnect_max_attempts: int = 5

    # Tracking / eventos
    track_exit_timeout_seconds: float = 12.0
    track_enter_min_seen_frames: int = 4
    track_enter_min_dwell_seconds: float = 1.0
    track_reacquire_window_seconds: float = 8.0
    track_reacquire_max_distance_px: float = 150.0
    track_exit_min_missing_frames: int = 15
    intrusion_min_inside_frames: int = 2
    intrusion_min_outside_frames: int = 4
    intrusion_lost_track_timeout_seconds: float = 20.0
    intrusion_min_track_age_frames: int = 2
    intrusion_min_confidence: float = 0.25
    intrusion_min_bbox_area: int = 800
    # Tolerância maior para evitar oscilação em câmeras com RTSP instável.
    camera_health_degraded_after_seconds: float = 15.0
    camera_health_restart_after_stall_checks: int = 8
    camera_health_restart_cooldown_seconds: float = 120.0
    camera_health_reconnect_grace_seconds: float = 45.0
    watchdog_max_deferred_stale_seconds: float = 120.0
    watchdog_max_stall_checks_before_force_restart: int = 20
    watchdog_restart_cooldown_seconds: float = 60.0
    camera_gateway_base_url: str = ""
    camera_gateway_public_base_url: str = ""
    camera_gateway_enabled: bool = False
    camera_gateway_worker_capture_enabled: bool = True
    # Quando False, o worker nunca abre RTSP direto se o gateway estiver habilitado.
    # Isso evita duas conexões simultâneas na câmera.
    camera_gateway_worker_rtsp_fallback_enabled: bool = False
    camera_gateway_recovery_enabled: bool = True
    camera_gateway_recovery_stable_seconds: float = 300.0
    camera_gateway_recovery_probe_interval_seconds: float = 60.0
    camera_gateway_recovery_probe_timeout_seconds: float = 5.0
    camera_gateway_recovery_fresh_frame_max_age_seconds: float = 10.0
    camera_gateway_circuit_park_after_seconds: float = 120.0
    camera_gateway_register_timeout_seconds: float = 2.5
    camera_gateway_first_frame_timeout_seconds: float = 20.0
    camera_gateway_stream_timeout_seconds: float = 15.0
    camera_gateway_worker_read_timeout_seconds: float = 2.0
    camera_gateway_reader_reconnect_delay_seconds: float = 1.0
    camera_gateway_reader_chunk_size_bytes: int = 32768
    camera_gateway_reader_max_buffer_bytes: int = 4194304
    # Gateways nativos opcionais. Vazios significam que o SDK proprietario nao
    # esta instalado; a deteccao automatica ainda identifica a porta/familia e
    # informa claramente que o runtime nativo precisa ser provisionado.
    dahua_sdk_gateway_base_url: str = ""
    intelbras_sdk_gateway_base_url: str = ""
    hikvision_sdk_gateway_base_url: str = ""
    # Quando habilitado, o camera-gateway consome o RTSP interno do MediaMTX
    # em vez de abrir uma segunda conexao direta na camera/NVR.
    camera_gateway_via_webrtc_rtsp_enabled: bool = False
    # direct: compatibilidade; mediamtx_prefer: fallback auditavel;
    # mediamtx_strict: nunca entregar RTSP original ao camera-gateway.
    camera_gateway_source_mode: str = ""
    camera_gateway_mediamtx_camera_ids: str = ""

    # Etapa 2: plano de dados local Gateway -> worker. O HTTP permanece como
    # plano de controle e como rollback explícito.
    frame_transport_mode: str = "http"
    frame_transport_camera_ids: str = ""
    frame_transport_protocol_version: int = 1
    frame_transport_root: str = "/run/sunorus/frames"
    frame_transport_slot_count: int = 4
    frame_transport_slot_capacity_bytes: int = 2 * 1024 * 1024
    frame_transport_poll_interval_ms: int = 5
    frame_transport_read_timeout_ms: int = 1000
    frame_transport_remove_on_stop: bool = False
    frame_transport_fallback_enabled: bool = True

    # Etapa 2B permanece em HTTP até a Etapa 2A concluir o canário.
    inference_transport_mode: str = "http"
    inference_transport_camera_ids: str = ""
    inference_transport_socket_path: str = "/run/sunorus/inference.sock"
    inference_transport_timeout_ms: int = 5000

    webrtc_gateway_enabled: bool = False
    webrtc_gateway_api_base_url: str = ""
    webrtc_gateway_public_base_url: str = ""
    webrtc_gateway_rtsp_base_url: str = "rtsp://webrtc-gateway:8554"
    webrtc_gateway_rtsp_public_base_url: str = ""
    webrtc_gateway_register_timeout_seconds: float = 1.5
    media_backbone_reconcile_enabled: bool = True
    media_backbone_reconcile_interval_seconds: float = 30.0
    media_backbone_remove_orphan_paths: bool = True
    # Main mosaic uses WebRTC by default; legacy MJPEG/snapshot paths remain for
    # detail pages and diagnostics, not as monitor fallbacks.
    webrtc_gateway_monitor_enabled: bool = True

    # Health/status
    camera_health_check_interval_seconds: int = 15
    watchdog_poll_interval_seconds: int = 15
    camera_health_offline_after_seconds: int = 45
    camera_health_startup_grace_seconds: int = 75
    camera_health_probe_interval_seconds: int = 30
    camera_health_probe_timeout_seconds: float = 2.5
    camera_health_restore_workers_on_startup: bool = True
    camera_startup_stagger_seconds: float = 1.5
    capture_open_retry_attempts: int = 5
    capture_open_retry_initial_delay_seconds: float = 1.0
    capture_open_retry_backoff_multiplier: float = 2.0
    capture_open_retry_max_delay_seconds: float = 12.0
    inference_timeout_seconds: float = 8.0
    snapshot_timeout_seconds: float = 2.0
    event_persist_timeout_seconds: float = 3.0
    alarm_lifecycle_correlation_window_seconds: float = 20.0
    alarm_session_enabled: bool = True
    alarm_session_same_scope_cooldown_seconds: float = 60.0
    alarm_session_active_extend_seconds: float = 30.0
    alarm_session_rearm_clear_seconds: float = 15.0
    alarm_session_new_track_renotify_enabled: bool = True
    alarm_session_new_track_cooldown_seconds: float = 10.0
    alarm_session_reminder_interval_seconds: float = 300.0
    alarm_session_escalation_score_delta: float = 0.20

    lockdown_ingest_enabled: bool = True
    lockdown_ingest_url: str = ""
    lockdown_ingest_secret: str = ""
    lockdown_ingest_timeout_seconds: float = 5.0
    auth_secret_key: str = "auto"
    auth_secret_key_file: str = ""
    credential_encryption_key: str = "auto"
    credential_encryption_key_file: str = ""
    session_ttl_seconds: int = 604800
    # None = ativa Secure automaticamente quando a request chega por HTTPS.
    session_cookie_secure: bool | None = None
    password_min_length: int = 12
    notification_dispatch_enabled: bool = True
    notification_poll_seconds: float = 5.0
    notification_retry_base_seconds: float = 30.0
    notification_processing_timeout_seconds: float = 120.0
    notification_response_max_chars: int = 4000
    notification_delivery_retention_days: int = 90
    incident_sla_critical_minutes: int = 5
    incident_sla_high_minutes: int = 15
    incident_sla_medium_minutes: int = 60
    incident_sla_low_minutes: int = 240
    incident_sla_monitor_seconds: float = 15.0
    api_auth_required: bool = True
    lockdown_policy_file: str = str(BASE_DIR / "runtime_state" / "lockdown_policy.json")

    runtime_state_dir: str = str(BASE_DIR / "runtime_state")
    # Caminho usado para medir o disco no monitor de armazenamento. Vazio =
    # detecta automaticamente pelo volume operacional (ex.: /data no Docker).
    storage_monitor_disk_path: str = ""

    # Instaladores do SunOrus Video Helper oferecidos ao operador quando o
    # navegador nao decodifica HEVC. Fica no volume de dados de proposito:
    # publicar uma versao nova nao deve exigir rebuild da imagem.
    video_helper_dist_dir: str = str(BASE_DIR / "data" / "downloads" / "video-helper")

    save_debug_frames: bool = False
    debug_frames_dir: str = str(BASE_DIR / "debug_frames")

    weak_detection_threshold: float = 0.05
    save_interval_seconds: int = 3
    motion_area_threshold: int = 4000

    save_event_snapshots: bool = True
    event_snapshots_dir: str = str(BASE_DIR / "event_snapshots")
    event_clip_before_seconds: float = 5.0
    event_clip_after_seconds: float = 10.0
    event_clip_history_seconds: float = 18.0
    event_clip_history_sample_interval_seconds: float = 0.5
    event_clip_history_jpeg_quality: int = 75
    event_clip_video_enabled: bool = True
    # FPS do arquivo gerado, nao da captura: o ring amostra com jitter e cada
    # frame ocupa no clipe o tempo real em que ficou na tela. Precisa ser >= a
    # densidade da amostragem (~2 fps) para nao descartar frames capturados.
    event_clip_video_fps: float = 5.0
    event_retention_days: int = 7
    event_retention_enabled: bool = True
    event_retention_check_interval_seconds: float = 3600.0
    event_retention_delete_batch_size: int = 1000
    local_clip_retention_max_total: int = 100
    local_clip_retention_max_false_positive: int = 50
    onedrive_clip_archive_enabled: bool = False
    onedrive_tenant: str = "organizations"
    onedrive_client_id: str = ""
    onedrive_token_file: str = str(BASE_DIR / "runtime_state" / "onedrive_token.json")
    onedrive_audit_prefix: str = "audit_pending"
    onedrive_upload_timeout_seconds: float = 30.0
    event_dedupe_window_seconds: float = 3.0
    event_rule_debug_enabled: bool = False
    event_rule_debug_camera_ids: str = ""
    event_rule_debug_rate_limit_seconds: float = 2.0

    person_revalidator_enabled: bool = True
    person_revalidator_model_path: str = "models/revalidator/person_crop_revalidator_yolo11n_v5.pt"
    # audit: registra o score sem bloquear. block: rejeita abaixo do limiar.
    person_revalidator_mode: str = "block"
    # Producao segura atual: IA2 v5 @ 0.50.
    person_revalidator_threshold: float = 0.50
    person_revalidator_margin_pct: float = 0.20
    person_revalidator_imgsz: int = 320
    person_revalidator_block_person_threshold: float = 0.001
    person_revalidator_block_not_person_threshold: float = 0.999
    person_revalidator_block_min_bbox_width_px: int = 24
    person_revalidator_block_min_bbox_height_px: int = 40
    person_revalidator_block_min_bbox_area_ratio: float = 0.0015
    person_revalidator_block_min_blur_variance: float = 12.0
    person_revalidator_block_min_brightness: float = 20.0
    person_revalidator_block_max_brightness: float = 235.0
    person_revalidator_block_border_margin_ratio: float = 0.02

    far_person_revalidator_enabled: bool = True
    far_person_revalidator_model_path: str = "models/revalidator_far/person_far_revalidator_yolo11n_v1.pt"
    # IA3 v1 melhora recall em pessoa pequena/distante com 0.48 sem aumentar FP no teste.
    far_person_revalidator_threshold: float = 0.48
    far_person_revalidator_margin_pct: float = 0.25
    far_person_revalidator_imgsz: int = 160
    far_person_revalidator_max_crop_width_px: int = 80
    far_person_revalidator_max_crop_height_px: int = 96
    far_person_revalidator_max_bbox_height_ratio: float = 0.08
    far_person_revalidator_suspicious_ia2_enabled: bool = True
    far_person_revalidator_suspicious_ia2_max_person_score: float = 0.02
    far_person_revalidator_suspicious_ia2_min_not_person_score: float = 0.98
    far_person_revalidator_suspicious_ia2_require_quality_gate: bool = True
    far_person_revalidator_suspicious_ia2_require_not_near_border: bool = True

    revalidator_pool_enabled: bool = False
    revalidator_pool_device: str = "auto"
    revalidator_pool_max_concurrency: int = 1
    revalidator_pool_max_queue_size: int = 32
    revalidator_pool_job_timeout_seconds: float = 3.0
    revalidator_pool_max_job_age_seconds: float = 10.0

    # --- Etapa 3: execucao centralizada das inferencias auxiliares -----------
    # Modos: "local" (atual), "central_prefer" (pool com fallback local
    # explicito) e "central_strict" (pool obrigatoria, sem fallback). As listas
    # aceitam IDs separados por virgula ou "*"; em modo local sao ignoradas.
    # Nada e ativado por padrao: a migracao e por configuracao e por camera.
    ia2_execution_mode: str = "local"
    ia2_central_camera_ids: str = ""
    ia2_pool_enabled: bool = False
    ia2_pool_worker_count: int = 1
    ia2_pool_max_queue_size: int = 64
    ia2_pool_timeout_ms: int = 1500
    ia2_pool_max_concurrency: int = 1
    ia2_pool_priority_enabled: bool = True

    ia3_execution_mode: str = "local"
    ia3_central_camera_ids: str = ""
    ia3_pool_enabled: bool = False
    ia3_pool_worker_count: int = 1
    ia3_pool_max_queue_size: int = 32
    ia3_pool_timeout_ms: int = 2000
    ia3_pool_max_concurrency: int = 1
    ia3_pool_priority_enabled: bool = True

    shadow_execution_mode: str = "local"
    shadow_central_camera_ids: str = ""
    shadow_pool_enabled: bool = False
    shadow_pool_max_queue_size: int = 16

    aux_inference_transport_mode: str = "http"
    aux_inference_socket_path: str = "/run/sunorus/aux-inference.sock"
    aux_inference_timeout_ms: int = 3000

    # Canal proprio da IA2, separado do socket da IA1 (Etapa 2B) para que as
    # duas nao compartilhem fila. Modos: http | binary_prefer | binary_strict.
    ia2_transport_mode: str = "http"
    ia2_transport_socket_path: str = "/run/sunorus/ia2.sock"
    ia2_transport_timeout_ms: int = 1500

    ia2_v8b_shadow_enabled: bool = True
    ia2_v8b_shadow_model_path: str = "models/revalidator/person_crop_revalidator_yolo11n_v8b_ultra_conservative.pt"
    ia2_v8b_shadow_threshold: float = 0.15
    ia2_v8c_shadow_enabled: bool = True
    ia2_v8c_shadow_model_path: str = "models/revalidator/person_crop_revalidator_yolo11n_v8c_curated_safe.pt"
    ia2_v8c_shadow_threshold: float = 0.20
    ia3_v2_protection_enabled: bool = True
    ia3_v2_protection_model_path: str = "models/revalidator_far/person_far_revalidator_yolo11n_v2.pt"
    ia3_v2_protection_threshold: float = 0.94
    # "audit"/"shadow" = calcula e registra o veto, mas NAO age (observa). Qualquer
    # outro valor (ex.: "block") faz o veto de fato impedir o auto-cancel.
    ia3_v2_protection_mode: str = "audit"

    consensus_revalidator_candidate_enabled: bool = True
    consensus_revalidator_ia2_max_person_score: float = 0.05
    consensus_revalidator_ia2_min_not_person_score: float = 0.95
    consensus_revalidator_ia3_max_person_score: float = 0.005
    consensus_revalidator_ia3_min_not_person_score: float = 0.995
    consensus_revalidator_require_quality_gate: bool = True
    consensus_revalidator_require_not_near_border: bool = True
    consensus_revalidator_block_enabled: bool = True
    consensus_revalidator_balanced_candidate_enabled: bool = True
    consensus_revalidator_balanced_block_enabled: bool = True
    consensus_revalidator_balanced_ia2_max_person_score: float = 0.10
    consensus_revalidator_balanced_ia2_min_not_person_score: float = 0.90
    consensus_revalidator_balanced_ia3_max_person_score: float = 0.01
    consensus_revalidator_balanced_ia3_min_not_person_score: float = 0.99
    consensus_revalidator_balanced_require_quality_gate: bool = True
    consensus_revalidator_balanced_require_not_near_border: bool = True
    consensus_revalidator_ia3_confirmed_candidate_enabled: bool = True
    consensus_revalidator_ia3_confirmed_block_enabled: bool = True
    consensus_revalidator_ia3_confirmed_ia2_max_person_score: float = 0.03
    consensus_revalidator_ia3_confirmed_ia2_min_not_person_score: float = 0.97
    consensus_revalidator_ia3_confirmed_ia3_max_person_score: float = 0.01
    consensus_revalidator_ia3_confirmed_ia3_min_not_person_score: float = 0.99
    consensus_revalidator_ia3_confirmed_require_quality_gate: bool = True
    consensus_revalidator_ia3_confirmed_require_not_near_border: bool = True
    consensus_revalidator_ia2_dominant_candidate_enabled: bool = True
    consensus_revalidator_ia2_dominant_block_enabled: bool = True
    consensus_revalidator_ia2_dominant_ia2_max_person_score: float = 0.02
    consensus_revalidator_ia2_dominant_ia2_min_not_person_score: float = 0.98
    consensus_revalidator_ia2_dominant_ia3_max_person_score: float = 0.10
    consensus_revalidator_ia2_dominant_ia3_min_not_person_score: float = 0.90
    consensus_revalidator_ia2_dominant_require_quality_gate: bool = True
    consensus_revalidator_ia2_dominant_require_not_near_border: bool = True
    consensus_revalidator_ia2_only_candidate_enabled: bool = True
    consensus_revalidator_ia2_only_block_enabled: bool = True
    consensus_revalidator_ia2_only_max_person_score: float = 0.15
    consensus_revalidator_ia2_only_min_not_person_score: float = 0.85
    consensus_revalidator_ia2_only_require_quality_gate: bool = True
    consensus_revalidator_ia2_only_require_not_near_border: bool = True

    strategy3_v2_enabled: bool = True
    # "block" = enforca (suprime/rebaixa em runtime). "audit"/"shadow" = so observa.
    strategy3_v2_mode: str = "block"
    strategy3_v2_detector_strong_threshold: float = 0.60
    strategy3_v2_very_high_detector_score: float = 0.85
    strategy3_v2_large_ia2_accept_threshold: float = 0.15
    strategy3_v2_large_ia2_reject_threshold: float = 0.03
    strategy3_v2_large_ia3_accept_threshold: float = 0.70
    strategy3_v2_large_ia3_reject_threshold: float = 0.25
    strategy3_v2_medium_ia2_accept_threshold: float = 0.08
    strategy3_v2_medium_ia2_reject_threshold: float = 0.02
    strategy3_v2_medium_ia3_accept_threshold: float = 0.65
    strategy3_v2_medium_ia3_reject_threshold: float = 0.25
    strategy3_v2_small_ia2_accept_threshold: float = 0.02
    strategy3_v2_small_ia2_reject_threshold: float = 0.005
    strategy3_v2_small_ia3_accept_threshold: float = 0.60
    strategy3_v2_small_ia3_reject_threshold: float = 0.20
    strategy3_v2_tracking_min_track_age: int = 2
    strategy3_v2_tracking_min_motion_consistency: float = 0.30
    strategy3_v2_tracking_min_motion_px: float = 3.0
    strategy3_v2_fast_motion_min_recent_motion_px: float = 12.0
    strategy3_v2_fast_motion_min_direction_consistency: float = 0.35
    strategy3_v2_temporal_min_hits: int = 2
    strategy3_v2_temporal_window_seconds: float = 2.0
    strategy3_v2_region_grid_cols: int = 8
    strategy3_v2_region_grid_rows: int = 8
    strategy3_v2_region_high_fp_min_count: int = 3
    strategy3_v2_region_risk_threshold: float = 0.70

    # Discordancia dos revalidadores-sombra (ia2_v8b_shadow + ia2_v8c_shadow) contra
    # o baseline: 99,4% dos eventos rotulados com discordancia dupla eram falso
    # positivo confirmado. O unico TP nessa amostra tinha human_motion_score=0.66,
    # por isso o piso de resgate abaixo.
    strategy3_v2_shadow_discordance_downgrade_enabled: bool = True
    strategy3_v2_shadow_discordant_motion_rescue: float = 0.50
    # Piso de human_motion_score para aceitar via tracking/tracking_temporal.
    # Calibrado com base nos 507 eventos rotulados de D:\IA_Rebuild\Analitico VMS Clips
    # (ver docs/plan/reduzir-falsos-positivos-intrusion-default.md): 0 TP perdidos no
    # bucket medium ate 0.50; no bucket large, ia2_person_score>=0.95 resgata o unico
    # TP que ficaria abaixo do piso de motion.
    strategy3_v2_large_tracking_min_human_motion: float = 0.30
    strategy3_v2_medium_tracking_min_human_motion: float = 0.45
    strategy3_v2_small_tracking_min_human_motion: float = 0.30
    strategy3_v2_tracking_weak_motion_ia2_rescue: float = 0.95

    anti_fp_post_filter_enabled: bool = True
    # "block" = enforca em runtime. "audit"/"shadow" = so observa.
    anti_fp_post_filter_mode: str = "block"
    anti_fp_post_filter_suppress_threshold: float = 0.70
    anti_fp_post_filter_audit_threshold: float = 0.40
    anti_fp_post_filter_low_priority_threshold: float = 0.20
    anti_fp_post_filter_high_region_weight: float = 0.35
    anti_fp_post_filter_blacklist_weight: float = 0.35
    anti_fp_post_filter_no_temporal_weight: float = 0.20
    anti_fp_post_filter_tracking_not_confirmed_weight: float = 0.20
    anti_fp_post_filter_static_track_weight: float = 0.15
    # Pessoa parada (loitering) e evento legitimo: quando a IA2 confirma a
    # pessoa (person_score >= bypass), as penalidades de baixa-movimentacao
    # (no_temporal / tracking_not_confirmed / static_track) NAO sao aplicadas.
    # Isso evita suprimir intruso parado num portao. 1.0 = desliga o bypass.
    anti_fp_post_filter_still_penalty_ia2_bypass: float = 0.5
    anti_fp_post_filter_fast_motion_bonus: float = -0.25
    anti_fp_post_filter_ia3_confirmed_bonus: float = -0.40
    anti_fp_patterns_json: str = ""

    visual_revalidation_gate_enabled: bool = True
    visual_revalidation_gate_ttl_seconds: float = 3.0
    visual_revalidation_gate_decisions: str = "NOTIFY,LOW_PRIORITY,AUDIT"
    visual_revalidation_gate_min_person_score: float = 0.45
    visual_ia_boxes_enabled: bool = True

    region_memory_enabled: bool = True
    region_memory_grid_cols: int = 8
    region_memory_grid_rows: int = 6
    region_memory_green_min_false_positive_count: int = 3
    region_memory_high_fp_rate_threshold: float = 0.70
    region_memory_person_support_rate_threshold: float = 0.50
    region_memory_history_limit: int = 5000
    region_memory_runtime_training_limit: int = 25

    event_maturity_enabled: bool = True
    event_maturity_alarm_score: float = 0.65
    event_maturity_low_confidence_score: float = 0.40
    event_maturity_static_displacement_norm: float = 0.015
    event_maturity_static_area_change_ratio: float = 0.20
    event_maturity_min_track_frames: int = 8
    event_maturity_min_track_seconds: float = 0.30
    event_maturity_fast_motion_displacement_norm: float = 0.035
    event_maturity_fast_motion_min_detector_score: float = 0.70
    event_maturity_camera_motion_families: str = "dome,ptz,speed_dome"
    event_maturity_visual_person_threshold: float = 0.50
    event_maturity_visual_far_person_threshold: float = 0.10

    # motion_confirm settings
    motion_confirm_enabled: bool = True
    motion_confirm_mode: str = "audit"
    motion_confirm_min_blobs: int = 2
    motion_confirm_min_area_pct: float = 0.05
    motion_confirm_min_displacement: float = 0.03
    # Movimento claro e' evidencia positiva; ausencia de movimento nunca veta pessoa.
    motion_confirm_boost_min_blobs: int = 5
    motion_confirm_boost_min_area_pct: float = 0.50
    motion_confirm_blob_min_area_px: int = 2
    motion_confirm_history_size: int = 20
    motion_confirm_threshold_px: int = 15

    revalidator_feedback_dataset_dir: str = "datasets/revalidator_feedback"
    revalidator_review_audit_dir: str = "datasets/revalidator_review_audit"

    visual_raw_publish_interval_seconds: float = 0.10
    visual_processed_publish_interval_seconds: float = 0.10
    visual_raw_publish_enabled: bool = True
    visual_processed_publish_enabled: bool = True
    visual_max_result_age_seconds: float = 1.0
    box_latency_diagnostics_enabled: bool = False
    box_latency_diagnostics_camera_ids: str = ""
    box_latency_diagnostics_sample_window: int = 300
    visual_fast_path_enabled: bool = False
    visual_fast_path_camera_ids: str = ""
    visual_track_fresh_ms: int = 500
    visual_track_retention_ms: int = 3000
    visual_inference_max_frame_age_ms: int = 0
    visual_inference_max_frame_age_camera_ids: str = ""
    web_track_transport_mode: str = "polling"
    web_track_sse_camera_ids: str = ""
    visual_motion_debug_overlay_enabled: bool = False
    monitor_motion_boxes_enabled: bool = False
    monitor_payload_cache_ttl_seconds: float = 1.0
    monitor_library_cache_ttl_seconds: float = 15.0
    monitor_gateway_health_cache_ttl_seconds: float = 2.0
    monitor_diagnostics_cache_ttl_seconds: float = 3.0
    metrics_store_memory_cache_ttl_seconds: float = 2.0
    track_store_memory_cache_ttl_seconds: float = 1.0
    webrtc_gateway_registration_cache_ttl_seconds: float = 300.0
    operational_history_sample_interval_seconds: float = 60.0
    # Retencao generosa para virar historico de verdade; 0 = ilimitado (nunca poda).
    operational_history_retention_days: int = 365
    operational_history_max_buckets: int = 720
    # Historico de recursos (CPU/RAM/GPU/FPS dos workers). Reaproveita os defaults
    # do historico operacional, mas pode ser ajustado de forma independente.
    resource_history_sample_interval_seconds: float = 60.0
    resource_history_retention_days: int = 365
    resource_history_max_buckets: int = 720
    # Central de alertas: offline/degradada/reconectando saem do proprio
    # health_status. O alerta de "IA sem deteccoes" e opcional pois cenas
    # paradas (com motion gate) nao inferem legitimamente.
    alert_ia_inactive_enabled: bool = True
    alert_ia_inactive_seconds: float = 900.0
    alert_metrics_fresh_seconds: float = 90.0

    app_role: str = "all"
    runtime_api_base_url: str = ""
    runtime_api_timeout_seconds: float = 3.0
    supervisor_api_token: str = ""

    logs_dir: str = str(BASE_DIR / "logs")
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5

    frame_store_prefer_shm: bool = True
    frame_store_shm_buffer_size_mb: int = 8

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:  # noqa: ANN001
        self.auth_secret_key = self._resolved_auth_secret_key()
        self.credential_encryption_key = self._resolved_credential_encryption_key()

    def _resolved_auth_secret_key(self) -> str:
        configured = str(self.auth_secret_key or "").strip()
        if not _auth_secret_needs_generation(configured):
            return configured

        key_path = Path(self.auth_secret_key_file or Path(self.runtime_state_dir) / "auth_secret_key")
        return _load_or_create_secret_file(key_path)

    def _resolved_credential_encryption_key(self) -> str:
        configured = str(self.credential_encryption_key or "").strip()
        if not _auth_secret_needs_generation(configured):
            return configured

        key_path = Path(
            self.credential_encryption_key_file
            or Path(self.runtime_state_dir) / "credential_encryption_key"
        )
        self.credential_encryption_key_file = str(key_path)
        return _load_or_create_secret_file(key_path)

    def ensure_runtime_dirs(self) -> None:
        dirs = [
            Path(self.app_base_dir),
            Path(self.runtime_state_dir),
            Path(self.auth_secret_key_file).parent if self.auth_secret_key_file else Path(self.runtime_state_dir),
            Path(self.credential_encryption_key_file).parent,
            Path(self.debug_frames_dir),
            Path(self.event_snapshots_dir),
            Path(self.logs_dir),
            Path(self.lockdown_policy_file).parent,
            Path(self.detector_engine_auto_build_dir),
        ]

        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)

        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)

    def resolved_detect_device(self) -> str:
        configured = str(self.detect_device or "auto").strip()

        if configured.lower() not in {"", "auto", "default"}:
            return configured

        try:
            import torch

            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            pass

        return "cpu"

    def event_rule_debug_camera_id_set(self) -> set[int]:
        raw = str(self.event_rule_debug_camera_ids or "").strip()
        if not raw:
            return set()

        parsed: set[int] = set()
        for token in raw.split(","):
            value = token.strip()
            if not value:
                continue
            try:
                parsed.add(int(value))
            except Exception:
                continue
        return parsed

    def event_rule_debug_is_enabled_for_camera(self, camera_id: int | None) -> bool:
        if not bool(self.event_rule_debug_enabled):
            return False
        allowed = self.event_rule_debug_camera_id_set()
        if not allowed:
            return True
        if camera_id is None:
            return False
        return int(camera_id) in allowed


settings = Settings()
