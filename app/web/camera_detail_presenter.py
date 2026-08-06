"""Presenter compartilhado para cameras, eventos e pagina de detalhes."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import profile_from_camera
from app.analytics.camera_policy_builder import build_profile_preview
from app.core.config import settings
from app.core.timezone import format_brazil_datetime
from app.db.models import Camera, Event, TuningSuggestion
from app.services.camera_health_monitor import _monitor_stream_for_status
from app.services.camera_registry import registry
from app.services.display_resize import DISPLAY_FRAME_HEIGHT, DISPLAY_FRAME_WIDTH
from app.services.feedback_constants import AUTO_TUNABLE_PARAMETERS
from app.services.frame_store import frame_store
from app.services.metrics_store import metrics_store
from app.services.runtime_client import remote_runtime_enabled
from app.web.presentation_constants import (
    DEFAULT_HUMAN_LOITERING_SECONDS,
    EVENT_STATUS_CHOICES,
    HUMAN_DETECTION_SENSITIVITY_CHOICES,
    HUMAN_DETECTION_SENSITIVITY_LABELS,
    HUMAN_DETECTION_SENSITIVITY_THRESHOLDS,
    HUMAN_EVENT_MODE_CHOICES,
    HUMAN_EVENT_MODE_GROUPS,
    HUMAN_EVENT_MODE_LABELS,
    MOTION_DEFAULTS,
)
from app.web.camera_view_models import (
    build_camera_family_options,
    build_manufacturer_options,
    build_nuisance_options,
    build_scene_category_options,
    build_target_focus_options,
)
from app.web.presentation_filters import (
    event_type_label,
    lifecycle_label,
    severity_label,
    status_label,
)

DEFAULT_MONITOR_BOX_MIN_CONFIDENCE = 0.40


def build_camera_detail_context(
    *,
    request,
    camera: Camera | None,
    events: list[Event],
    error: str | None = None,
    camera_error: str | None = None,
    motion_error: str | None = None,
    ops_error: str | None = None,
    rtsp_test_results=None,
    rtsp_test_source=None,
) -> dict[str, Any]:
    profile = getattr(camera, "analytics_profile", None)
    return {
        "request": request,
        "camera": camera,
        "events": events,
        "error": error,
        "camera_error": camera_error,
        "motion_error": motion_error,
        "ops_error": ops_error,
        "motion_defaults": MOTION_DEFAULTS,
        "rtsp_test_results": rtsp_test_results,
        "rtsp_test_source": rtsp_test_source,
        "camera_family_options": build_camera_family_options(getattr(profile, "camera_family", "dome")),
        "scene_category_options": build_scene_category_options(getattr(profile, "scene_category", "interno")),
        "target_focus_options": build_target_focus_options(getattr(profile, "target_focus", "pessoa")),
        "nuisance_options": build_nuisance_options(getattr(profile, "nuisance_profile", None)),
        "manufacturer_options": build_manufacturer_options(),
    }

def camera_performance_overrides(camera: Camera | None) -> dict[str, Any]:
    profile = profile_from_camera(camera) if camera else None
    overrides = dict(getattr(profile, "manual_overrides", {}) or {}) if profile else {}
    def _value(key: str, default: Any) -> Any:
        return overrides[key] if key in overrides and overrides[key] is not None else default
    return {
        "processing_max_width": int(_value("processing_max_width", settings.processing_max_width)),
        "processing_max_height": int(_value("processing_max_height", settings.processing_max_height)),
        "processing_upscale_small_frames": bool(_value("processing_upscale_small_frames", settings.processing_upscale_small_frames)),
        "normal_inference_interval_seconds": float(_value("normal_inference_interval_seconds", settings.normal_inference_interval_seconds)),
        "capture_drop_frames": int(_value("capture_drop_frames", settings.capture_drop_frames)),
        "visual_raw_publish_interval_seconds": float(_value("visual_raw_publish_interval_seconds", settings.visual_raw_publish_interval_seconds)),
        "visual_processed_publish_interval_seconds": float(
            _value(
                "visual_processed_publish_interval_seconds",
                _value("visual_publish_interval_seconds", settings.visual_processed_publish_interval_seconds),
            )
        ),
        "prefer_motion_test": True,
    }

def serialize_tuning_suggestion(suggestion: TuningSuggestion) -> dict[str, Any]:
    is_applicable = suggestion.parameter_name in AUTO_TUNABLE_PARAMETERS
    return {
        "id": suggestion.id,
        "camera_id": suggestion.camera_id,
        "scope_type": suggestion.scope_type,
        "scope_id": suggestion.scope_id,
        "suggestion_type": suggestion.suggestion_type,
        "parameter_name": suggestion.parameter_name,
        "old_value": suggestion.old_value,
        "suggested_value": suggestion.suggested_value,
        "reason_summary": suggestion.reason_summary,
        "evidence_count": suggestion.evidence_count,
        "confidence_score": suggestion.confidence_score,
        "status": suggestion.status,
        "is_applicable": is_applicable,
        "action_label": "Aplicar" if is_applicable else "Ajustar na camera",
    }

def build_camera_detail_payload(db: Session, camera_id: int):
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    camera = enrich_camera_for_template(camera, camera_id)
    if camera is None:
        return None, []

    metrics = metrics_store.get_metrics(camera_id)
    attach_camera_frame_geometry(camera, metrics)
    camera.monitor_overlay = camera_monitor_overlay(camera)
    camera.monitor_boxes = camera_monitor_boxes(metrics, camera)
    camera.log_entries = build_camera_log_entries(camera_id)
    suggestions_query = db.query(TuningSuggestion).filter(
        TuningSuggestion.camera_id == camera_id,
        TuningSuggestion.status == "pending",
    )
    camera.tuning_suggestions = [
        serialize_tuning_suggestion(suggestion)
        for suggestion in (
            suggestions_query
            .order_by(TuningSuggestion.confidence_score.desc(), TuningSuggestion.id.desc())
            .limit(50)
            .all()
        )
    ]

    camera_map = get_camera_map(db)
    events = (
        db.query(Event)
        .filter(Event.camera_id == camera_id)
        .order_by(Event.id.desc())
        .limit(50)
        .all()
    )
    for event in events:
        enrich_event(event, camera_map)

    return camera, events


def serialize_camera_detail_event(event: Event) -> dict[str, Any]:
    """Converte um evento no contrato JSON consumido pela pagina da camera."""
    status_display = getattr(event, "status_display", normalize_event_status(event.status))
    severity_display = getattr(
        event,
        "severity_display",
        infer_event_severity(event.event_type, event.confidence),
    )
    resolved_at = getattr(event, "resolved_at", None)
    return {
        "id": event.id,
        "event_type": event.event_type,
        "event_type_label": event_type_label(event.event_type),
        "severity": severity_display,
        "severity_label": severity_label(severity_display),
        "status": status_display,
        "status_label": status_label(status_display),
        "track_id": event.track_id,
        "confidence": event.confidence,
        "snapshot_url": f"/events/{event.id}/snapshot" if event.snapshot_path else "",
        "created_at_label": format_dt(event.created_at),
        "lifecycle_action": getattr(event, "lifecycle_action", "open"),
        "alarm_category": getattr(event, "alarm_category", None),
        "alarm_eligible": event_alarm_eligible(event),
        "is_alarm_active": event_alarm_active(event),
        "resolved_at": resolved_at.isoformat() if isinstance(resolved_at, datetime) else resolved_at,
        "can_ack": status_display == "new" and event_alarm_eligible(event),
        "can_close": event_alarm_eligible(event) and event_alarm_active(event),
        "can_reopen": event_alarm_eligible(event) and status_display == "closed",
    }

def _tail_lines(path: Path, limit: int) -> list[str]:
    buffer = deque(maxlen=max(1, int(limit)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                buffer.append(line.rstrip("\r\n"))
    except Exception:
        return []
    return [line for line in buffer if line.strip()]

def build_camera_log_entries(camera_id: int, limit: int = 120) -> dict[str, Any]:
    logs_dir = Path(settings.logs_dir)
    files = [
        ("app.log", "app"),
        ("error.log", "error"),
        ("inference_pool.log", "pool"),
        ("event_rules_debug.log", "regras"),
    ]

    camera_key = f"cam={camera_id}"
    camera_key_alt = f"camera_id={camera_id}"
    camera_key_alt2 = f"camera_id: {camera_id}"

    matched_entries: list[dict[str, str]] = []
    fallback_entries: list[dict[str, str]] = []
    sources_seen: list[str] = []

    for filename, source_label in files:
        path = logs_dir / filename
        if not path.exists():
            continue

        sources_seen.append(filename)
        for line in _tail_lines(path, max(limit * 3, limit + 20)):
            entry = {"source": source_label, "line": line}
            fallback_entries.append(entry)
            if camera_key in line or camera_key_alt in line or camera_key_alt2 in line:
                matched_entries.append(entry)

    entries = matched_entries[-limit:] if matched_entries else fallback_entries[-limit:]

    return {
        "camera_id": camera_id,
        "filtered": bool(matched_entries),
        "entries": entries,
        "sources": sources_seen,
        "logs_dir": str(logs_dir),
        "limit": int(limit),
    }

def get_worker_mode(worker) -> str:
    return getattr(worker, "mode_name", "normal")

def is_motion_test_worker(worker) -> bool:
    return get_worker_mode(worker) == "motion_test"

def mask_rtsp(rtsp_url: str | None) -> str | None:
    if not rtsp_url:
        return rtsp_url
    if "@" not in rtsp_url or "://" not in rtsp_url:
        return rtsp_url
    scheme, rest = rtsp_url.split("://", 1)
    creds, host_part = rest.split("@", 1)
    if ":" not in creds:
        return f"{scheme}://***@{host_part}"
    user, _ = creds.split(":", 1)
    return f"{scheme}://{user}:***@{host_part}"

def camera_config_summary(camera: Camera | None) -> dict:
    # Resumo compacto para a UI: a tela trabalha com este dict em vez de acessar
    # os campos crús do ORM em varios lugares.
    roi_points = []
    if camera and camera.roi_polygon_json:
        try:
            roi_points = json.loads(camera.roi_polygon_json)
        except Exception:
            roi_points = []

    line_enabled = camera and None not in (
        camera.line_start_x,
        camera.line_start_y,
        camera.line_end_x,
        camera.line_end_y,
    )

    stored_modes = []
    stored_human_modes_raw = getattr(camera, "human_event_modes_json", None) if camera else None
    if stored_human_modes_raw:
        try:
            parsed_modes = json.loads(stored_human_modes_raw)
            if isinstance(parsed_modes, list):
                for mode in parsed_modes:
                    mode_value = str(mode).strip()
                    if mode_value in HUMAN_EVENT_MODE_CHOICES and mode_value not in stored_modes:
                        stored_modes.append(mode_value)
        except Exception:
            stored_modes = []

    if stored_human_modes_raw is None and not stored_modes:
        if roi_points and len(roi_points) >= 3:
            stored_modes.extend(["person_entered_roi", "person_left_roi"])
        elif camera:
            stored_modes.extend(["person_entered", "person_left"])
        if line_enabled:
            stored_modes.append("line_crossing")

    if getattr(camera, "human_loitering_seconds", None) is not None:
        human_loitering_seconds = float(camera.human_loitering_seconds)
    else:
        human_loitering_seconds = DEFAULT_HUMAN_LOITERING_SECONDS

    human_detection_sensitivity = str(
        getattr(camera, "human_detection_sensitivity", None) or "medium"
    ).strip().lower() or "medium"
    if human_detection_sensitivity not in HUMAN_DETECTION_SENSITIVITY_CHOICES:
        human_detection_sensitivity = "medium"

    active_mode_labels = [
        HUMAN_EVENT_MODE_LABELS.get(mode, mode)
        for mode in stored_modes
        if mode in HUMAN_EVENT_MODE_LABELS
    ]
    active_mode_groups = []
    for mode in stored_modes:
        group = HUMAN_EVENT_MODE_GROUPS.get(mode)
        if group and group not in active_mode_groups:
            active_mode_groups.append(group)

    if stored_human_modes_raw is not None and not active_mode_labels:
        human_event_modes_label = "Nenhum"
    else:
        human_event_modes_label = ", ".join(active_mode_labels) if active_mode_labels else "Padrão"

    analytics_profile_preview = {}
    if camera is not None:
        try:
            analytics_profile_preview = build_profile_preview(
                profile_from_camera(camera),
                frame_width=getattr(camera, "frame_width", None) or 1920,
                frame_height=getattr(camera, "frame_height", None) or 1080,
            )
        except Exception:
            analytics_profile_preview = {}

    return {
        "roi_enabled": len(roi_points) >= 3,
        "roi_name": camera.roi_name or "ROI" if camera else None,
        "roi_points": roi_points,
        "line_enabled": bool(line_enabled),
        "line_direction": camera.line_direction or "any" if camera else "any",
        "human_event_modes": stored_modes,
        "human_event_modes_label": human_event_modes_label,
        "human_event_groups": active_mode_groups,
        "human_loitering_seconds": human_loitering_seconds,
        "human_detection_sensitivity": human_detection_sensitivity,
        "human_detection_sensitivity_label": HUMAN_DETECTION_SENSITIVITY_LABELS.get(
            human_detection_sensitivity, "Padrão"
        ),
        "analytics_profile_preview": analytics_profile_preview,
    }

def camera_motion_config(camera: Camera | None) -> dict:
    if not camera:
        return dict(MOTION_DEFAULTS)

    return {
        key: getattr(camera, key) if getattr(camera, key) is not None else default
        for key, default in MOTION_DEFAULTS.items()
    }

def camera_monitor_overlay(camera: Camera | None) -> dict:
    roi_points = []
    if camera and camera.roi_polygon_json:
        try:
            parsed_roi = json.loads(camera.roi_polygon_json)
            if isinstance(parsed_roi, list):
                roi_points = [
                    {"x": float(point.get("x", 0.0)), "y": float(point.get("y", 0.0))}
                    for point in parsed_roi
                    if isinstance(point, dict)
                ]
        except Exception:
            roi_points = []

    line = None
    if camera and None not in (
        camera.line_start_x,
        camera.line_start_y,
        camera.line_end_x,
        camera.line_end_y,
    ):
        try:
            line = {
                "start": {"x": float(camera.line_start_x), "y": float(camera.line_start_y)},
                "end": {"x": float(camera.line_end_x), "y": float(camera.line_end_y)},
                "direction": camera.line_direction or "any",
            }
        except Exception:
            line = None

    return {
        "enabled": bool(len(roi_points) >= 3 or line),
        "coordinate_space": "normalized",
        "roi_name": camera.roi_name or "ROI" if camera else "ROI",
        "roi_points": roi_points,
        "line": line,
    }

def camera_ia1_visual_threshold(camera: Camera | None) -> float:
    sensitivity = str(getattr(camera, "human_detection_sensitivity", None) or "medium").strip().lower()
    return float(HUMAN_DETECTION_SENSITIVITY_THRESHOLDS.get(sensitivity, HUMAN_DETECTION_SENSITIVITY_THRESHOLDS["medium"]))

def _track_passes_ia1_visual_threshold(track: dict, camera: Camera | None) -> bool:
    try:
        confidence = track.get("confidence")
        return confidence is not None and float(confidence) >= camera_ia1_visual_threshold(camera)
    except Exception:
        return False

def _normalize_monitor_box_min_confidence(value: Any = None) -> float:
    try:
        confidence = float(value)
    except Exception:
        confidence = DEFAULT_MONITOR_BOX_MIN_CONFIDENCE
    return max(0.0, min(1.0, confidence))

def _track_passes_monitor_box_min_confidence(track: dict, minimum_confidence: float) -> bool:
    try:
        confidence = track.get("confidence")
        return confidence is not None and float(confidence) >= minimum_confidence
    except Exception:
        return False

def camera_monitor_boxes(metrics: dict | None, camera: Camera | None = None) -> list[dict]:
    boxes: list[dict] = []
    if (
        str((metrics or {}).get("worker_mode") or "").lower() == "motion_test"
        and not bool(settings.monitor_motion_boxes_enabled)
        and not bool(settings.visual_ia_boxes_enabled)
    ):
        return boxes
    raw_tracks = (metrics or {}).get("latest_tracks") or []
    if not isinstance(raw_tracks, list):
        return boxes

    for track in raw_tracks[:30]:
        if not isinstance(track, dict):
            continue
        bbox = track.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if not _track_passes_monitor_box_min_confidence(track, DEFAULT_MONITOR_BOX_MIN_CONFIDENCE):
            continue
        try:
            item = {
                "bbox": [float(value) for value in bbox],
                "track_id": int(track.get("track_id")) if track.get("track_id") is not None else None,
                "confidence": float(track.get("confidence")) if track.get("confidence") is not None else None,
            }
            if track.get("label"):
                item["label"] = str(track.get("label"))
            if track.get("visual_status"):
                item["visual_status"] = str(track.get("visual_status"))
            if track.get("visual_person_score") is not None:
                item["visual_person_score"] = float(track.get("visual_person_score"))
            if track.get("notification_decision"):
                item["notification_decision"] = str(track.get("notification_decision"))
        except Exception:
            continue
        boxes.append(item)
    return boxes

def infer_event_severity(event_type: str | None, confidence: float | None) -> str:
    label = (event_type or "").lower()
    conf = float(confidence) if confidence is not None else 0.0

    if "crossed_line" in label:
        return "critical" if conf >= 0.85 else "high"
    if "entered_roi" in label:
        return "high" if conf >= 0.80 else "medium"
    if "loitering" in label:
        return "high"
    if label in {"person_entered", "person_left"}:
        return "medium"
    if "left_roi" in label:
        return "low"
    return "medium"

def normalize_event_status(status: str | None) -> str:
    if status == "canceled":
        return "closed"
    if status in EVENT_STATUS_CHOICES:
        return status
    return "new"

def event_raw_status(event: Event) -> str:
    raw = str(getattr(event, "status", "") or "").strip().lower()
    if raw == "canceled":
        return "closed"
    return raw or normalize_event_status(getattr(event, "status", None))

def enrich_event(event: Event, camera_map: dict[int, Camera] | None = None):
    event.status_display = normalize_event_status(event.status)
    event.severity_display = event.severity or infer_event_severity(event.event_type, event.confidence)
    if camera_map:
        camera = camera_map.get(event.camera_id)
        event.camera_name = camera.name if camera else f"Câmera {event.camera_id}"
        event.camera_priority = (camera.camera_priority if camera and camera.camera_priority else "medium")
        event.alarm_sound_enabled = bool(camera.alarm_sound_enabled) if camera and camera.alarm_sound_enabled is not None else True
        event.alarm_popup_enabled = bool(camera.alarm_popup_enabled) if camera and camera.alarm_popup_enabled is not None else True
        event.site_name = camera.site_name if camera else None
        event.group_name = camera.group_name if camera else None
    else:
        event.camera_name = f"Câmera {event.camera_id}"
        event.camera_priority = "medium"
        event.alarm_sound_enabled = True
        event.alarm_popup_enabled = True
        event.site_name = None
        event.group_name = None
    event.created_at_label = format_dt(event.created_at)
    event.event_type_label = event_type_label(event.event_type)
    event.status_label = status_label(event.status_display)
    event.severity_label = severity_label(event.severity_display)
    return event

def event_alarm_active(event: Event) -> bool:
    explicit = getattr(event, "is_alarm_active", None)
    if explicit is not None:
        return bool(explicit)

    alarm_eligible = getattr(event, "alarm_eligible", None)
    if alarm_eligible is not None:
        return bool(alarm_eligible) and getattr(event, "status_display", normalize_event_status(event.status)) != "closed"

    return getattr(event, "status_display", normalize_event_status(event.status)) != "closed"

def event_alarm_eligible(event: Event) -> bool:
    explicit = getattr(event, "alarm_eligible", None)
    if explicit is not None:
        return bool(explicit)

    return getattr(event, "status_display", normalize_event_status(event.status)) != "closed"

def enrich_camera_for_template(camera: Camera | None, camera_id: int, health_entry: dict | None = None):
    worker = None if remote_runtime_enabled() else registry.get_worker(camera_id)

    if not camera:
        return None

    camera.masked_rtsp_url = mask_rtsp(camera.rtsp_url)
    camera.analytics_summary = camera_config_summary(camera)
    camera.analytics_profile = profile_from_camera(camera)
    camera.performance_overrides = camera_performance_overrides(camera)
    camera.motion_config = camera_motion_config(camera)
    camera.roi_polygon_pretty = (
        json.dumps(camera.analytics_summary["roi_points"], ensure_ascii=False)
        if camera.analytics_summary["roi_enabled"]
        else ""
    )
    camera.human_event_modes = camera.analytics_summary["human_event_modes"]
    camera.human_loitering_seconds = camera.analytics_summary["human_loitering_seconds"]
    camera.human_detection_sensitivity = camera.analytics_summary["human_detection_sensitivity"]
    camera.processing_max_width_override = camera.performance_overrides["processing_max_width"]
    camera.processing_max_height_override = camera.performance_overrides["processing_max_height"]
    camera.processing_upscale_small_frames_override = camera.performance_overrides["processing_upscale_small_frames"]
    camera.normal_inference_interval_seconds_override = camera.performance_overrides["normal_inference_interval_seconds"]
    camera.capture_drop_frames_override = camera.performance_overrides["capture_drop_frames"]
    camera.visual_raw_publish_interval_seconds_override = camera.performance_overrides["visual_raw_publish_interval_seconds"]
    camera.visual_processed_publish_interval_seconds_override = camera.performance_overrides["visual_processed_publish_interval_seconds"]
    camera.prefer_motion_test = camera.performance_overrides["prefer_motion_test"]
    health_entry = health_entry or {}
    camera.health_status = str(health_entry.get("health_status") or camera.status or "idle")
    camera.health_status_display = "running" if camera.health_status == "running_motion_test" else camera.health_status
    camera.is_running = camera.health_status in {"running", "running_motion_test"}
    camera.worker_mode = str(health_entry.get("worker_mode") or "").strip() or (get_worker_mode(worker) if worker else "stopped")
    camera.site_name = camera.site_name or ""
    camera.group_name = camera.group_name or ""
    camera.camera_priority = camera.camera_priority or "medium"
    camera.auto_start_enabled = bool(getattr(camera, "auto_start_enabled", False))
    camera.alarm_sound_enabled = True if camera.alarm_sound_enabled is None else bool(camera.alarm_sound_enabled)
    camera.alarm_popup_enabled = True if camera.alarm_popup_enabled is None else bool(camera.alarm_popup_enabled)
    camera.monitor_class = health_entry.get("monitor_class") or (
        "monitor-reconnecting" if camera.health_status in {"starting", "warming_up"} else ("monitor-running" if camera.is_running else "monitor-idle")
    )
    camera.monitor_stream_url = health_entry.get("monitor_stream_url") or _monitor_stream_for_status(camera.id, camera.health_status)
    camera.consecutive_stall_checks = int(health_entry.get("consecutive_stall_checks", 0) or 0)
    camera.restart_count = int(health_entry.get("restart_count", 0) or 0)
    camera.last_restart_at = health_entry.get("last_restart_at")
    camera.last_restart_reason = health_entry.get("last_restart_reason")
    camera.last_frame_at = health_entry.get("last_frame_at")
    camera.last_processed_frame_at = health_entry.get("last_processed_frame_at")
    camera.last_successful_inference_at = health_entry.get("last_successful_inference_at")
    camera.last_metrics_at = health_entry.get("last_metrics_at")
    camera.latest_activity_at = health_entry.get("latest_activity_at")
    camera.latest_activity_age_seconds = health_entry.get("latest_activity_age_seconds")
    camera.gateway_state = health_entry.get("gateway_state")
    camera.gateway_source_url = health_entry.get("gateway_source_url")
    camera.gateway_failure_count = int(health_entry.get("gateway_failure_count", 0) or 0)
    camera.gateway_last_frame_at = health_entry.get("gateway_last_frame_at")
    camera.gateway_last_frame_age_seconds = health_entry.get("gateway_last_frame_age_seconds")
    camera.gateway_last_reconnect_at = health_entry.get("gateway_last_reconnect_at")
    camera.gateway_last_reconnect_age_seconds = health_entry.get("gateway_last_reconnect_age_seconds")
    return camera

def attach_camera_frame_geometry(camera: Camera | None, metrics: dict | None = None) -> Camera | None:
    if not camera:
        return None

    metrics = metrics or {}
    frame_meta = None
    if not metrics.get("frame_width") or not metrics.get("frame_height"):
        try:
            frame_meta = frame_store.get_raw_frame_metadata(camera.id) or frame_store.get_processed_frame_metadata(camera.id)
        except Exception:
            frame_meta = None

    source_width = int(
        metrics.get("frame_width")
        or metrics.get("source_frame_width")
        or metrics.get("input_width")
        or (frame_meta or {}).get("width")
        or DISPLAY_FRAME_WIDTH
    )
    source_height = int(
        metrics.get("frame_height")
        or metrics.get("source_frame_height")
        or metrics.get("input_height")
        or (frame_meta or {}).get("height")
        or DISPLAY_FRAME_HEIGHT
    )

    camera.source_frame_width = max(1, source_width)
    camera.source_frame_height = max(1, source_height)
    camera.analytics_coordinate_space = str(getattr(camera, "analytics_coordinate_space", None) or "display").strip().lower() or "display"
    camera.display_frame_width = DISPLAY_FRAME_WIDTH
    camera.display_frame_height = DISPLAY_FRAME_HEIGHT
    return camera

def get_camera_map(db: Session) -> dict[int, Camera]:
    return {camera.id: camera for camera in db.query(Camera).filter(Camera.is_deleted == False).all()}

def format_dt(value) -> str:
    return format_brazil_datetime(value)
