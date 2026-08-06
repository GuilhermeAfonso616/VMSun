"""Modelos SQLAlchemy do sistema.

Camera guarda a configuracao operacional e analitica.
Event registra o historico de eventos operacionais.
LockdownDelivery acompanha tentativas de envio externo do Lockdown.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, text
from sqlalchemy.sql import func

from app.core.credential_crypto import EncryptedString
from app.db.base import Base


class Camera(Base):
    # Configuracao persistida da camera e dos analiticos associados.
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    onvif_port = Column(Integer, default=80)
    username = Column(String, nullable=False)
    password = Column(EncryptedString, nullable=False)
    manufacturer = Column(String(120), nullable=False, default="Nao informada", server_default="Nao informada")
    model = Column(String(120), nullable=True)
    rtsp_url = Column(EncryptedString, nullable=True)
    status = Column(String, default="idle")
    source_type = Column(String, nullable=True, default="camera")
    source_parent_id = Column(Integer, nullable=True)
    source_channel = Column(Integer, nullable=True)
    source_stream_kind = Column(String, nullable=True)
    source_brand = Column(String, nullable=True)
    source_provider = Column(String, nullable=True)

    roi_name = Column(String, nullable=True)
    roi_polygon_json = Column(Text, nullable=True)
    line_start_x = Column(Float, nullable=True)
    line_start_y = Column(Float, nullable=True)
    line_end_x = Column(Float, nullable=True)
    line_end_y = Column(Float, nullable=True)
    line_direction = Column(String, nullable=True)
    analytics_coordinate_space = Column(String, nullable=True, default="display")
    human_event_modes_json = Column(Text, nullable=True)
    human_loitering_seconds = Column(Float, nullable=True)
    human_detection_sensitivity = Column(String, nullable=True, default="medium")
    analytics_profile_json = Column(Text, nullable=True)
    learning_mode = Column(String, nullable=True, default="assisted_policy_tuning")
    auto_tuning_enabled = Column(Boolean, nullable=True, default=False)
    critical_lock = Column(Boolean, nullable=True, default=False)
    max_daily_auto_changes = Column(Integer, nullable=True, default=1)
    min_reviewed_events_for_suggestion = Column(Integer, nullable=True, default=12)
    min_reviewed_events_for_auto_tuning = Column(Integer, nullable=True, default=24)
    rollback_window_hours = Column(Integer, nullable=True, default=48)

    site_name = Column(String, nullable=True)
    group_name = Column(String, nullable=True)
    camera_priority = Column(String, nullable=True, default="medium")
    auto_start_enabled = Column(Boolean, nullable=True, default=False)
    alarm_sound_enabled = Column(Boolean, nullable=True, default=True)
    alarm_popup_enabled = Column(Boolean, nullable=True, default=True)

    motion_idle_interval = Column(Float, nullable=True)
    motion_active_interval = Column(Float, nullable=True)
    motion_hold_seconds = Column(Float, nullable=True)
    motion_detection_hold_seconds = Column(Float, nullable=True)
    motion_min_motion_frames = Column(Integer, nullable=True)
    motion_downscale_width = Column(Integer, nullable=True)
    motion_min_contour_area = Column(Integer, nullable=True)
    motion_ratio_threshold = Column(Float, nullable=True)
    motion_global_change_ratio_limit = Column(Float, nullable=True)
    motion_background_alpha = Column(Float, nullable=True)
    motion_warmup_frames = Column(Integer, nullable=True)

    # Soft delete fields
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CameraPtzProfile(Base):
    """Diagnostico persistido do melhor caminho de controle PTZ da camera."""

    __tablename__ = "camera_ptz_profiles"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="unknown", server_default="unknown")
    detected_brand = Column(String(32), nullable=True)
    selected_backend = Column(String(32), nullable=True)
    fallback_backend = Column(String(32), nullable=True)
    control_port = Column(Integer, nullable=True)
    channel = Column(Integer, nullable=True)
    profile_token = Column(String(255), nullable=True)
    supports_pan_tilt = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    supports_zoom = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    supports_presets = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    continuous_move = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    configuration_fingerprint = Column(String(64), nullable=True)
    failure_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_error_category = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    diagnostics_json = Column(Text, nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Event(Base):
    # Evento operacional gerado pelo pipeline analitico.
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False)
    event_type = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    track_id = Column(Integer, nullable=True)
    detector_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    event_score = Column(Float, nullable=True)
    details = Column(Text, nullable=True)
    snapshot_path = Column(String, nullable=True)
    clip_path = Column(String, nullable=True)
    clip_remote_item_id = Column(String, nullable=True)
    clip_remote_web_url = Column(String, nullable=True)
    clip_remote_status = Column(String, nullable=True)
    clip_remote_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    clip_local_deleted_at = Column(DateTime(timezone=True), nullable=True)
    snapshot_remote_item_id = Column(String, nullable=True)
    snapshot_remote_web_url = Column(String, nullable=True)
    snapshot_remote_status = Column(String, nullable=True)
    snapshot_remote_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    event_remote_item_id = Column(String, nullable=True)
    event_remote_web_url = Column(String, nullable=True)
    event_remote_status = Column(String, nullable=True)
    event_remote_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    bbox_json = Column(Text, nullable=True)
    active_profile_snapshot = Column(Text, nullable=True)
    threshold_snapshot = Column(Text, nullable=True)
    scene_profile = Column(String, nullable=True)
    camera_family = Column(String, nullable=True)
    nuisance_profile_snapshot = Column(Text, nullable=True)
    roi_id = Column(String, nullable=True)
    zone_id = Column(String, nullable=True)
    rule_id = Column(String, nullable=True)
    severity = Column(String, nullable=True, default="medium")
    status = Column(String, nullable=True, default="new")
    alarm_eligible = Column(Boolean, nullable=True, default=True)
    lifecycle_action = Column(String, nullable=True, default="open")
    alarm_category = Column(String, nullable=True)
    correlation_key = Column(String, nullable=True)
    related_event_id = Column(Integer, nullable=True)
    resolved_by_event_id = Column(Integer, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    is_alarm_active = Column(Boolean, nullable=True, default=True)
    operator_note = Column(Text, nullable=True)
    assigned_user_id = Column(Integer, nullable=True, index=True)
    assigned_username = Column(String, nullable=True, index=True)
    sla_due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    escalated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    resolution_code = Column(String, nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    incident_team = Column(String, nullable=True, index=True)
    incident_priority = Column(String, nullable=True, index=True)
    incident_checklist = Column(Text, nullable=True)
    incident_origin = Column(String, nullable=True, default="automatic", index=True)
    incident_parent_id = Column(Integer, nullable=True, index=True)
    # Conclusao automatica por consenso IA2+IA3. Fica separada de EventFeedback de
    # proposito: nao entra nas metricas de operador nem no auto-tuning de parametros.
    ai_validation_label = Column(String, nullable=True, index=True)
    ai_validation_reason = Column(String, nullable=True)
    ai_validation_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EventFeedback(Base):
    __tablename__ = "event_feedback"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    label = Column(String, nullable=False)
    probable_cause = Column(String, nullable=True)
    operator_note = Column(Text, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IncidentTimeline(Base):
    __tablename__ = "incident_timeline"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(Integer, nullable=True, index=True)
    actor_username = Column(String, nullable=True)
    action = Column(String, nullable=False, index=True)
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class TuningSuggestion(Base):
    __tablename__ = "tuning_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    scope_type = Column(String, nullable=False)
    scope_id = Column(String, nullable=False)
    suggestion_type = Column(String, nullable=False)
    parameter_name = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    suggested_value = Column(Text, nullable=True)
    reason_summary = Column(Text, nullable=True)
    evidence_count = Column(Integer, nullable=False, default=0)
    confidence_score = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    applied_at = Column(DateTime(timezone=True), nullable=True)


class ConfigVersionHistory(Base):
    __tablename__ = "config_version_history"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    config_before = Column(Text, nullable=False)
    config_after = Column(Text, nullable=False)
    change_source = Column(String, nullable=False, default="manual")
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rollback_available = Column(Boolean, nullable=False, default=True)


class LockdownDelivery(Base):
    # Historico de envios HTTP para a integracao externa do Lockdown.
    __tablename__ = "lockdown_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    camera_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    target_url = Column(String, nullable=False)
    request_body = Column(Text, nullable=True)
    request_timestamp = Column(Integer, nullable=True)
    request_signature = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    http_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="operator")  # "admin", "supervisor", "operator", "viewer"
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    login_attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    lockout_until = Column(DateTime(timezone=True), nullable=True)
    max_active_sessions = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InstallationState(Base):
    __tablename__ = "installation_state"

    id = Column(Integer, primary_key=True, default=1)
    setup_completed = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    setup_completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="webhook", server_default="webhook")
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    target = Column(EncryptedString, nullable=False)
    signing_secret = Column(EncryptedString, nullable=True)
    min_severity = Column(String, nullable=False, default="medium", server_default="medium")
    event_types_json = Column(Text, nullable=True)
    timeout_seconds = Column(Float, nullable=False, default=5.0, server_default="5")
    max_attempts = Column(Integer, nullable=False, default=5, server_default=text("5"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_notification_delivery_idempotency"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_id = Column(Integer, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", server_default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    payload_json = Column(Text, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    http_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)


class ViewPreset(Base):
    __tablename__ = "view_presets"

    id = Column(String, primary_key=True, index=True)  # Ex: view_171234567890
    owner_user_id = Column(Integer, nullable=False, index=True)
    name = Column(String, nullable=False)
    grid_size = Column(Integer, nullable=False, default=16)
    camera_ids = Column(String, nullable=False)  # Armazenado como string JSON: '[1, 2, null, 4]'
    hide_offline = Column(Boolean, default=False)
    boxes_enabled = Column(Boolean, default=True)
    view_config_json = Column(Text, nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TemporalSequence(Base):
    __tablename__ = "temporal_sequences"

    id = Column(String, primary_key=True, index=True)  # Ex: seq_171234567890
    owner_user_id = Column(Integer, nullable=False, index=True)
    name = Column(String, nullable=False)
    steps = Column(String, nullable=False)  # Armazenado como string JSON: '[{"viewId": "view_123", "duration": 8}]'
    is_shared = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
