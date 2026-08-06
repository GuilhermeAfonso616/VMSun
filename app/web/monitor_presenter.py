"""View-models, cache e payloads do monitor operacional.

Este modulo nao conhece o router web nem cria sessoes de banco. As consultas
recebem uma Session explicita para manter serializacao e transporte
testaveis de forma independente.
"""

import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import profile_from_camera
from app.analytics.camera_policy_builder import build_profile_preview
from app.core.config import settings
from app.db.models import Camera, Event
from app.db.query_helpers import get_active_cameras_query as _query_active_cameras
from app.services.camera_gateway_client import fetch_gateway_health
from app.services.camera_health_monitor import _monitor_stream_for_status
from app.services.camera_operational_state import (
    build_camera_operational_state,
    status_summary as central_status_summary,
)
from app.services.display_resize import DISPLAY_FRAME_HEIGHT, DISPLAY_FRAME_WIDTH
from app.services.metrics_store import metrics_store
from app.services.preview_stream import preview_stream_manager
from app.services.runtime_client import get_runtime_health_snapshot
from app.services.stream_topology import resolve_stream_url
from app.services.track_store import track_store
from app.services.webrtc_gateway_client import (
    build_webrtc_player_url,
    camera_webrtc_path_name,
    get_webrtc_path_diagnostics,
    register_webrtc_camera_path,
    webrtc_gateway_is_enabled,
)
from app.web.camera_detail_presenter import (
    DEFAULT_MONITOR_BOX_MIN_CONFIDENCE,
    _normalize_monitor_box_min_confidence,
    _track_passes_monitor_box_min_confidence,
    attach_camera_frame_geometry,
    camera_monitor_boxes,
    camera_monitor_overlay,
    enrich_camera_for_template,
    enrich_event,
    event_alarm_active,
    event_alarm_eligible,
    event_raw_status,
    event_type_label,
    format_dt,
    get_camera_map,
    infer_event_severity,
    normalize_event_status,
    severity_label,
    status_label,
)
from app.web.presentation_constants import (
    MONITOR_POPUP_NOTICE_EVENT_TYPES,
    PRIORITY_CHOICES,
)


PRIORITY_SORT_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_MONITOR_RESPONSE_CACHE_LOCK = Lock()
_MONITOR_RESPONSE_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_GATEWAY_HEALTH_CACHE_LOCK = Lock()
_GATEWAY_HEALTH_CACHE: tuple[float, Any] | None = None


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _status_summary(
    status: str,
    label: str,
    detail: str = "",
    ok: bool = False,
) -> dict[str, Any]:
    return central_status_summary(status, label, detail, ok)


def _filter_visual_gate_tracks(payload: dict | None, camera: Camera | None = None) -> dict | None:
    if not payload:
        return payload
    filtered = dict(payload)
    tracks = payload.get("tracks") or []
    if not isinstance(tracks, list):
        filtered["tracks"] = []
        return filtered
    filtered["tracks"] = [
        track
        for track in tracks
        if isinstance(track, dict)
    ]
    return filtered


def _filter_monitor_box_min_confidence(payload: dict | None, minimum_confidence: float) -> dict | None:
    if not payload:
        return payload
    filtered = dict(payload)
    tracks = payload.get("tracks") or []
    if not isinstance(tracks, list):
        filtered["tracks"] = []
        return filtered
    filtered["tracks"] = [
        track
        for track in tracks
        if isinstance(track, dict)
        and _track_passes_monitor_box_min_confidence(track, minimum_confidence)
    ]
    return filtered


def priority_sort_value(value: str | None) -> int:
    return PRIORITY_SORT_ORDER.get((value or "medium").lower(), 2)


def clamp_monitor_grid(grid: int | None) -> int:
    if grid in {1, 2, 4, 6, 8, 9, 12, 16, 25}:
        return int(grid)
    return 4


def grid_columns_for(grid: int) -> int:
    if grid == 1:
        return 1
    if grid == 2:
        return 2
    if grid == 4:
        return 2
    if grid in {6, 9}:
        return 3
    if grid in {8, 12, 16}:
        return 4
    if grid == 25:
        return 5
    return 4


def iso_dt(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _monitor_cache_ttl(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _monitor_cache_filter_key(
    site_name: str | None,
    group_name: str | None,
    camera_priority: str | None,
    only_running: bool,
    only_alarm: bool,
    grid: int,
) -> tuple[Any, ...]:
    return (
        site_name or "",
        group_name or "",
        camera_priority if camera_priority in PRIORITY_CHOICES else "",
        bool(only_running),
        bool(only_alarm),
        clamp_monitor_grid(grid),
    )


def _monitor_json_response(payload: dict[str, Any], cache_status: str | None = None) -> JSONResponse:
    response = JSONResponse(payload)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    if cache_status:
        response.headers["X-Server-Cache"] = cache_status
    return response


def _cached_monitor_json_response(
    cache_key: tuple[Any, ...],
    build_payload,
    ttl_seconds: float | None = None,
) -> JSONResponse:
    ttl = _monitor_cache_ttl(
        settings.monitor_payload_cache_ttl_seconds if ttl_seconds is None else ttl_seconds
    )
    if ttl <= 0:
        return _monitor_json_response(build_payload(), "BYPASS")

    now = time.monotonic()
    with _MONITOR_RESPONSE_CACHE_LOCK:
        cached = _MONITOR_RESPONSE_CACHE.get(cache_key)
        if cached and now - cached[0] <= ttl:
            return _monitor_json_response(cached[1], "HIT")

    payload = build_payload()
    with _MONITOR_RESPONSE_CACHE_LOCK:
        _MONITOR_RESPONSE_CACHE[cache_key] = (time.monotonic(), payload)
        if len(_MONITOR_RESPONSE_CACHE) > 128:
            oldest_key = min(_MONITOR_RESPONSE_CACHE, key=lambda key: _MONITOR_RESPONSE_CACHE[key][0])
            _MONITOR_RESPONSE_CACHE.pop(oldest_key, None)
    return _monitor_json_response(payload, "MISS")


def _get_cached_gateway_health():
    global _GATEWAY_HEALTH_CACHE

    if not settings.camera_gateway_enabled or not settings.camera_gateway_base_url.strip():
        return None

    ttl = _monitor_cache_ttl(settings.monitor_gateway_health_cache_ttl_seconds)
    if ttl <= 0:
        return fetch_gateway_health()

    now = time.monotonic()
    with _GATEWAY_HEALTH_CACHE_LOCK:
        cached = _GATEWAY_HEALTH_CACHE
        if cached and now - cached[0] <= ttl:
            return cached[1]

    try:
        gateway_health = fetch_gateway_health()
    except Exception:
        with _GATEWAY_HEALTH_CACHE_LOCK:
            cached = _GATEWAY_HEALTH_CACHE
            if cached:
                return cached[1]
        return None

    with _GATEWAY_HEALTH_CACHE_LOCK:
        _GATEWAY_HEALTH_CACHE = (time.monotonic(), gateway_health)
    return gateway_health


def build_monitor_live_payload(
    db: Session,
    site_name: str | None = None,
    group_name: str | None = None,
    camera_priority: str | None = None,
    only_running: bool = False,
    only_alarm: bool = False,
    grid: int = 4,
):
    grid = clamp_monitor_grid(grid)
    grid_columns = grid_columns_for(grid)

    health_snapshot = get_runtime_health_snapshot()
    gateway_health = _get_cached_gateway_health()
    health_by_camera_id = {item["camera_id"]: item for item in health_snapshot.get("cameras", [])}
    camera_map = get_camera_map(db)
    cameras = []
    for camera in camera_map.values():
        health_entry = health_by_camera_id.get(camera.id, {})
        cameras.append(enrich_camera_for_template(camera, camera.id, health_entry))

    latest_by_camera = query_latest_events_by_camera(db)
    for event in latest_by_camera.values():
        enrich_event(event, camera_map)

    active_events = _query_active_alarm_events(db)
    for event in active_events:
        enrich_event(event, camera_map)

    open_by_camera: dict[int, list[Event]] = {}
    for event in active_events:
        if event_alarm_active(event):
            open_by_camera.setdefault(event.camera_id, []).append(event)

    filtered_cameras = []
    for camera in cameras:
        health_entry = health_by_camera_id.get(camera.id, {})
        metrics = metrics_store.get_metrics(camera.id) or {}
        camera.operational_state = build_camera_operational_state(camera, metrics=metrics, health_entry=health_entry)
        camera.operational_health = camera.operational_state.get("operational_health")
        attach_camera_frame_geometry(camera, metrics)
        camera.monitor_boxes = camera_monitor_boxes(metrics, camera)
        camera_open_events = open_by_camera.get(camera.id, [])
        latest_event = latest_by_camera.get(camera.id)
        preview_stats = preview_stream_manager.get_stats(camera.id)

        camera.open_events_count = len(camera_open_events)
        camera.new_events_count = sum(1 for event in camera_open_events if event.status_display == "new")
        camera.latest_event = latest_event
        camera.has_open_alarm = bool(camera_open_events)
        camera.highest_open_severity = None
        if camera_open_events:
            camera.highest_open_severity = min(
                (event.severity_display for event in camera_open_events),
                key=priority_sort_value,
            )

        if camera.highest_open_severity == "critical":
            camera.monitor_class = "alarm-critical"
        elif camera.highest_open_severity == "high":
            camera.monitor_class = "alarm-high"
        elif camera.has_open_alarm:
            camera.monitor_class = "alarm-medium"
        elif getattr(camera, "health_status", "idle") == "degraded":
            camera.monitor_class = "monitor-degraded"
        elif getattr(camera, "health_status", "idle") == "reconnecting":
            camera.monitor_class = "monitor-reconnecting"
        elif getattr(camera, "health_status", "idle") == "offline":
            camera.monitor_class = "monitor-offline"
        elif getattr(camera, "health_status", "idle") == "stopped":
            camera.monitor_class = "monitor-stopped"
        elif camera.is_running:
            camera.monitor_class = "monitor-running"
        else:
            camera.monitor_class = "monitor-idle"

        camera.monitor_stream_url = _monitor_stream_for_status(camera.id, getattr(camera, "health_status", "idle"))
        camera.fps = _safe_float(metrics.get("fps"))
        camera.raw_fps = _safe_float(metrics.get("raw_fps"))
        camera.processed_fps = _safe_float(metrics.get("processed_fps"))
        if ((getattr(camera, "operational_health", {}) or {}).get("metrics_age_seconds") or 0) > max(12.0, float(settings.camera_health_degraded_after_seconds) * 2.0):
            camera.fps = 0.0
            camera.raw_fps = 0.0
            camera.processed_fps = 0.0
        camera.preview_fps = _safe_float(preview_stats.get("fps"))
        camera.preview_last_frame_age_seconds = _safe_float(preview_stats.get("last_frame_age_seconds"))
        camera.preview_has_frame = bool(preview_stats.get("has_frame", False))
        camera.preview_last_error = preview_stats.get("last_error")
        display_fps = camera.processed_fps or camera.raw_fps or camera.fps or camera.preview_fps or 0.0
        camera.display_fps = round(float(display_fps), 2)
        camera.last_event_at = latest_event.created_at if latest_event else None
        camera.last_event_label = format_dt(latest_event.created_at) if latest_event else "-"
        if latest_event:
            camera.last_event_type = getattr(latest_event, "event_type_label", None) or event_type_label(latest_event.event_type)
        else:
            camera.last_event_type = "Sem eventos"
        camera.last_event_severity = latest_event.severity_display if latest_event else None
        camera.last_event_status = latest_event.status_display if latest_event else None

        if site_name and camera.site_name != site_name:
            continue
        if group_name and camera.group_name != group_name:
            continue
        if camera_priority and camera.camera_priority != camera_priority:
            continue
        if only_running and not camera.is_running:
            continue
        if only_alarm and not camera.has_open_alarm:
            continue

        filtered_cameras.append(camera)

    filtered_cameras.sort(
        key=lambda camera: (
            0 if camera.has_open_alarm else 1,
            priority_sort_value(camera.highest_open_severity or camera.camera_priority),
            0 if camera.is_running else 1,
            (camera.site_name or ""),
            (camera.group_name or ""),
            camera.name.lower(),
        )
    )

    visible_cameras = filtered_cameras[:grid]

    stats = {
        "displayed": len(visible_cameras),
        "filtered_total": len(filtered_cameras),
        "running": sum(1 for camera in filtered_cameras if camera.is_running),
        "image_ok": sum(1 for camera in filtered_cameras if ((getattr(camera, "operational_health", {}) or {}).get("image") or {}).get("status") == "ok"),
        "analysis_ok": sum(1 for camera in filtered_cameras if ((getattr(camera, "operational_health", {}) or {}).get("analysis") or {}).get("status") in {"ok", "standby"}),
        "operational_ok": sum(1 for camera in filtered_cameras if ((getattr(camera, "operational_health", {}) or {}).get("overall") or {}).get("status") == "ok"),
        "with_alarm": sum(1 for camera in filtered_cameras if camera.has_open_alarm),
        "critical_or_high": sum(1 for camera in filtered_cameras if camera.highest_open_severity in {"high", "critical"}),
        "new_alarms": sum(camera.new_events_count for camera in filtered_cameras),
    }

    return {
        "visible_cameras": visible_cameras,
        "selected_site_name": site_name or "",
        "selected_group_name": group_name or "",
        "selected_camera_priority": camera_priority or "",
        "selected_only_running": bool(only_running),
        "selected_only_alarm": bool(only_alarm),
        "selected_grid": grid,
        "grid_columns": grid_columns,
        "stats": stats,
        "gateway_health": gateway_health,
    }


def query_latest_events_by_camera(db: Session) -> dict[int, Event]:
    latest_event_ids = (
        db.query(func.max(Event.id).label("id"))
        .group_by(Event.camera_id)
        .subquery()
    )
    latest_events = (
        db.query(Event)
        .join(latest_event_ids, Event.id == latest_event_ids.c.id)
        .all()
    )
    return {int(event.camera_id): event for event in latest_events}


def _query_active_alarm_events(db: Session) -> list[Event]:
    return (
        db.query(Event)
        .filter(Event.status != "closed")
        .filter(or_(Event.is_alarm_active.is_(True), Event.is_alarm_active.is_(None)))
        .order_by(Event.id.desc())
        .all()
    )


def _query_monitor_popup_notice_events(db: Session) -> list[Event]:
    return (
        db.query(Event)
        .filter(Event.status != "closed")
        .filter(Event.is_alarm_active.is_(False))
        .filter(or_(Event.alarm_eligible.is_(True), Event.alarm_eligible.is_(None)))
        .order_by(Event.id.desc())
        .limit(50)
        .all()
    )


def event_monitor_popup_notice(event: Event) -> bool:
    if event_alarm_active(event) or not event_alarm_eligible(event):
        return False
    if event_raw_status(event) not in {"low_priority", "low_confidence"}:
        return False
    if not bool(getattr(event, "alarm_popup_enabled", True)):
        return False
    return str(getattr(event, "event_type", "") or "") in MONITOR_POPUP_NOTICE_EVENT_TYPES


def _monitor_camera_passes_filters(
    camera: Camera,
    *,
    site_name: str | None,
    group_name: str | None,
    camera_priority: str | None,
    only_running: bool,
    only_alarm: bool,
    open_by_camera: dict[int, list[Event]],
) -> bool:
    if site_name and camera.site_name != site_name:
        return False
    if group_name and camera.group_name != group_name:
        return False
    if camera_priority and camera.camera_priority != camera_priority:
        return False
    if only_running and not camera.is_running:
        return False
    if only_alarm and not open_by_camera.get(camera.id):
        return False
    return True


def build_monitor_alarm_payload(
    db: Session,
    *,
    site_name: str | None = None,
    group_name: str | None = None,
    camera_priority: str | None = None,
    only_running: bool = False,
    only_alarm: bool = False,
) -> dict[str, Any]:
    camera_map = get_camera_map(db)
    cameras: list[Camera] = list(camera_map.values())
    if only_running:
        health_snapshot = get_runtime_health_snapshot()
        health_by_camera_id = {item["camera_id"]: item for item in health_snapshot.get("cameras", [])}
        cameras = [
            enrich_camera_for_template(camera, camera.id, health_by_camera_id.get(camera.id, {}))
            for camera in cameras
        ]

    active_events = _query_active_alarm_events(db)
    for event in active_events:
        enrich_event(event, camera_map)
    popup_notice_events = _query_monitor_popup_notice_events(db)
    for event in popup_notice_events:
        enrich_event(event, camera_map)

    open_by_camera: dict[int, list[Event]] = {}
    for event in active_events:
        if event_alarm_active(event):
            open_by_camera.setdefault(event.camera_id, []).append(event)

    filtered_ids = {
        camera.id
        for camera in cameras
        if _monitor_camera_passes_filters(
            camera,
            site_name=site_name,
            group_name=group_name,
            camera_priority=camera_priority,
            only_running=only_running,
            only_alarm=only_alarm,
            open_by_camera=open_by_camera,
        )
    }
    panel_alarms = [
        event
        for event in active_events
        if event.camera_id in filtered_ids and event_alarm_active(event)
    ][:20]
    popup_notices = [
        event
        for event in popup_notice_events
        if event.camera_id in filtered_ids and event_monitor_popup_notice(event)
    ]
    alarm_queue_signature = "|".join(
        f"{event.id}:{event.status_display}:{event.severity_display}"
        for event in panel_alarms
    )
    latest_active_alarm = next(
        (event for event in panel_alarms if event.severity_display in {"high", "critical"}),
        None,
    ) or (panel_alarms[0] if panel_alarms else None)
    latest_popup_notice = popup_notices[0] if popup_notices else None
    latest_popup_alarm = latest_active_alarm or latest_popup_notice
    latest_popup_status = event_raw_status(latest_popup_alarm) if latest_popup_alarm else ""
    latest_popup_kind = "alarm" if latest_popup_alarm and event_alarm_active(latest_popup_alarm) else "notice"
    return {
        "panel_alarms": panel_alarms,
        "open_events_total": sum(len(events) for events in open_by_camera.values()),
        "alarm_queue_signature": alarm_queue_signature,
        "latest_alarm_signature": f"{latest_popup_alarm.id}:{latest_popup_status}:{latest_popup_kind}" if latest_popup_alarm else "",
        "alarm_should_play": bool(latest_active_alarm and latest_active_alarm.alarm_sound_enabled),
        "popup_should_show": bool(latest_popup_alarm and getattr(latest_popup_alarm, "alarm_popup_enabled", True)),
        "latest_popup_alarm": latest_popup_alarm,
        "open_by_camera": open_by_camera,
    }


def build_monitor_library_payload(
    db: Session,
    *,
    site_name: str | None = None,
    group_name: str | None = None,
    camera_priority: str | None = None,
    only_running: bool = False,
    only_alarm: bool = False,
) -> dict[str, Any]:
    health_snapshot = get_runtime_health_snapshot()
    gateway_health = _get_cached_gateway_health()
    health_by_camera_id = {item["camera_id"]: item for item in health_snapshot.get("cameras", [])}
    camera_map = get_camera_map(db)
    alarm_payload = build_monitor_alarm_payload(
        db,
        site_name=site_name,
        group_name=group_name,
        camera_priority=camera_priority,
        only_running=only_running,
        only_alarm=only_alarm,
    )
    open_by_camera = alarm_payload["open_by_camera"]

    cameras: list[Camera] = []
    for camera in camera_map.values():
        health_entry = health_by_camera_id.get(camera.id, {})
        camera = enrich_camera_for_template(camera, camera.id, health_entry)
        metrics = metrics_store.get_metrics(camera.id) or {}
        camera_open_events = open_by_camera.get(camera.id, [])
        camera.operational_state = build_camera_operational_state(camera, metrics=metrics, health_entry=health_entry)
        camera.operational_health = camera.operational_state.get("operational_health")
        attach_camera_frame_geometry(camera, metrics)
        camera.open_events_count = len(camera_open_events)
        camera.new_events_count = sum(1 for event in camera_open_events if event.status_display == "new")
        camera.has_open_alarm = bool(camera_open_events)
        camera.highest_open_severity = None
        if camera_open_events:
            camera.highest_open_severity = min(
                (event.severity_display for event in camera_open_events),
                key=priority_sort_value,
            )
        if _monitor_camera_passes_filters(
            camera,
            site_name=site_name,
            group_name=group_name,
            camera_priority=camera_priority,
            only_running=only_running,
            only_alarm=only_alarm,
            open_by_camera=open_by_camera,
        ):
            cameras.append(camera)

    cameras.sort(
        key=lambda camera: (
            0 if camera.has_open_alarm else 1,
            priority_sort_value(camera.highest_open_severity or camera.camera_priority),
            0 if camera.is_running else 1,
            (camera.site_name or ""),
            (camera.group_name or ""),
            camera.name.lower(),
        )
    )
    return {
        "available_cameras": cameras,
        "gateway_health": gateway_health,
    }


def serialize_monitor_camera(
    camera,
    webrtc_registration: dict | None = None,
    ptz_profile: dict | None = None,
) -> dict:
    webrtc_path_name = camera_webrtc_path_name(camera.id)
    webrtc_monitor_enabled = bool(settings.webrtc_gateway_monitor_enabled and webrtc_gateway_is_enabled())
    if webrtc_registration is None and webrtc_monitor_enabled and getattr(camera, "rtsp_url", None):
        webrtc_registration = register_webrtc_camera_path(camera.id, camera.rtsp_url)
    if webrtc_registration:
        webrtc_path_name = str(webrtc_registration.get("path") or webrtc_path_name)
    webrtc_registration_ok = bool(
        webrtc_monitor_enabled
        and getattr(camera, "rtsp_url", None)
        and (webrtc_registration is None or webrtc_registration.get("ok"))
    )
    webrtc_registration_reason = (
        ""
        if webrtc_registration_ok
        else (
            "monitor_webrtc_disabled"
            if not webrtc_monitor_enabled
            else str((webrtc_registration or {}).get("reason") or (webrtc_registration or {}).get("error") or "missing_rtsp_url")
        )
    )
    operational_health = dict(getattr(camera, "operational_health", None) or {})
    if "player" not in operational_health:
        if webrtc_monitor_enabled and webrtc_registration_ok:
            operational_health["player"] = _status_summary(
                "checking",
                "Player verificando",
                "aguardando MediaMTX/WebRTC",
                False,
            )
        elif webrtc_monitor_enabled:
            operational_health["player"] = _status_summary(
                "offline",
                "Player sem rota",
                webrtc_registration_reason or "registro WebRTC indisponivel",
                False,
            )
        else:
            operational_health["player"] = _status_summary(
                "offline",
                "WebRTC off",
                "mosaico principal exige WebRTC",
                False,
            )

    return {
        "id": camera.id,
        "name": camera.name,
        "site_name": camera.site_name or "",
        "group_name": camera.group_name or "",
        "camera_priority": camera.camera_priority or "medium",
        "status": str(getattr(camera, "status", "") or "idle"),
        "health_status": getattr(camera, "health_status", str(getattr(camera, "status", "") or "idle")),
        "health_status_display": getattr(
            camera,
            "health_status_display",
            "running" if getattr(camera, "health_status", "") == "running_motion_test" else getattr(camera, "health_status", str(getattr(camera, "status", "") or "idle")),
        ),
        "worker_mode": camera.worker_mode or "stopped",
        "is_running": bool(getattr(camera, "is_running", False)),
        "fps": round(float(getattr(camera, "fps", 0.0) or 0.0), 2),
        "raw_fps": round(float(getattr(camera, "raw_fps", 0.0) or 0.0), 2),
        "processed_fps": round(float(getattr(camera, "processed_fps", 0.0) or 0.0), 2),
        "preview_fps": round(float(getattr(camera, "preview_fps", 0.0) or 0.0), 2),
        "display_fps": round(float(getattr(camera, "display_fps", 0.0) or 0.0), 2),
        "has_live_metrics": bool(getattr(camera, "has_live_metrics", False)),
        "source_frame_width": int(getattr(camera, "source_frame_width", 0) or 0),
        "source_frame_height": int(getattr(camera, "source_frame_height", 0) or 0),
        "monitor_stream_url": getattr(
            camera,
            "monitor_stream_url",
            resolve_stream_url(f"/cameras/{camera.id}/stream/raw"),
        ),
        "open_events_count": int(getattr(camera, "open_events_count", 0) or 0),
        "new_events_count": int(getattr(camera, "new_events_count", 0) or 0),
        "highest_open_severity": getattr(camera, "highest_open_severity", None),
        "has_open_alarm": bool(getattr(camera, "has_open_alarm", False)),
        "monitor_class": getattr(camera, "monitor_class", "monitor-idle"),
        "consecutive_stall_checks": int(getattr(camera, "consecutive_stall_checks", 0) or 0),
        "restart_count": int(getattr(camera, "restart_count", 0) or 0),
        "last_restart_at": iso_dt(getattr(camera, "last_restart_at", None)),
        "last_restart_reason": getattr(camera, "last_restart_reason", None),
        "last_frame_at": iso_dt(getattr(camera, "last_frame_at", None)),
        "last_processed_frame_at": iso_dt(getattr(camera, "last_processed_frame_at", None)),
        "last_successful_inference_at": iso_dt(getattr(camera, "last_successful_inference_at", None)),
        "last_metrics_at": iso_dt(getattr(camera, "last_metrics_at", None)),
        "latest_activity_at": iso_dt(getattr(camera, "latest_activity_at", None)),
        "latest_activity_age_seconds": getattr(camera, "latest_activity_age_seconds", None),
        "gateway_state": getattr(camera, "gateway_state", None),
        "gateway_source_active": bool(getattr(camera, "gateway_source_url", None)),
        "gateway_failure_count": int(getattr(camera, "gateway_failure_count", 0) or 0),
        "gateway_last_frame_at": iso_dt(getattr(camera, "gateway_last_frame_at", None)),
        "gateway_last_frame_age_seconds": getattr(camera, "gateway_last_frame_age_seconds", None),
        "gateway_last_reconnect_at": iso_dt(getattr(camera, "gateway_last_reconnect_at", None)),
        "gateway_last_reconnect_age_seconds": getattr(camera, "gateway_last_reconnect_age_seconds", None),
        "ptz_profile": ptz_profile or {
            "status": "unknown",
            "status_label": "PTZ nao avaliado",
            "ptz_capable": False,
            "capabilities": {"ptz": False},
        },
        "operational_health": operational_health,
        "operational_state": getattr(camera, "operational_state", None),
        "preview_fps": round(float(getattr(camera, "preview_fps", 0.0) or 0.0), 2),
        "preview_last_frame_age_seconds": getattr(camera, "preview_last_frame_age_seconds", None),
        "preview_has_frame": bool(getattr(camera, "preview_has_frame", False)),
        "preview_last_error": getattr(camera, "preview_last_error", None),
        "last_event_type": getattr(camera, "last_event_type", "Sem eventos"),
        "last_event_label": getattr(camera, "last_event_label", "-"),
        "last_event_at": iso_dt(getattr(camera, "last_event_at", None)),
        "last_event_severity": getattr(camera, "last_event_severity", None),
        "last_event_status": getattr(camera, "last_event_status", None),
        "analytics_profile_preview": build_profile_preview(
            profile_from_camera(camera),
            frame_width=getattr(camera, "source_frame_width", None) or DISPLAY_FRAME_WIDTH,
            frame_height=getattr(camera, "source_frame_height", None) or DISPLAY_FRAME_HEIGHT,
        ),
        "monitor_overlay": camera_monitor_overlay(camera),
        "monitor_boxes": getattr(camera, "monitor_boxes", []),
        "webrtc_enabled": webrtc_monitor_enabled,
        "webrtc_path": webrtc_path_name,
        "webrtc_player_url": build_webrtc_player_url(webrtc_path_name),
        "webrtc_registration_ok": webrtc_registration_ok,
        "webrtc_registration_reason": webrtc_registration_reason,
        "detail_url": f"/cameras/{camera.id}",
        "metrics_url": f"/cameras/{camera.id}/metrics/view",
    }


def build_monitor_shell_context(
    db: Session,
    *,
    site_name: str | None,
    group_name: str | None,
    camera_priority: str | None,
    only_running: bool,
    only_alarm: bool,
    grid: int,
) -> dict[str, Any]:
    selected_grid = clamp_monitor_grid(grid)
    active_cameras = _query_active_cameras(db).all()
    return {
        "site_options": sorted({camera.site_name for camera in active_cameras if camera.site_name}),
        "group_options": sorted({camera.group_name for camera in active_cameras if camera.group_name}),
        "available_cameras": [],
        "visible_cameras": [],
        "panel_alarms": [],
        "selected_site_name": site_name or "",
        "selected_group_name": group_name or "",
        "selected_camera_priority": camera_priority or "",
        "selected_only_running": bool(only_running),
        "selected_only_alarm": bool(only_alarm),
        "selected_grid": selected_grid,
        "grid_columns": grid_columns_for(selected_grid),
        "stats": {
            "displayed": 0,
            "filtered_total": 0,
            "running": 0,
            "image_ok": 0,
            "analysis_ok": 0,
            "operational_ok": 0,
            "with_alarm": 0,
            "critical_or_high": 0,
            "new_alarms": 0,
        },
        "gateway_health": None,
        "alarm_queue_signature": "",
        "latest_alarm_signature": "",
        "alarm_should_play": False,
        "popup_should_show": False,
        "latest_popup_alarm": None,
    }


def event_clip_available(event) -> bool:
    if getattr(event, "clip_path", None):
        return True

    remote_status = str(getattr(event, "clip_remote_status", "") or "").lower()
    remote_item_id = str(getattr(event, "clip_remote_item_id", "") or "").strip()
    return bool(remote_item_id and remote_status == "uploaded")


def event_clip_url(event) -> str:
    return f"/events/{event.id}/clip/video" if event_clip_available(event) else ""


def serialize_monitor_event(event) -> dict:
    status_display = getattr(event, "status_display", normalize_event_status(event.status))
    operational_status = event_raw_status(event)
    payload_status = operational_status if operational_status not in {"", "new"} else status_display
    clip_url = event_clip_url(event)
    return {
        "id": event.id,
        "camera_id": event.camera_id,
        "camera_name": getattr(event, "camera_name", f"Câmera {event.camera_id}"),
        "site_name": getattr(event, "site_name", "") or "",
        "group_name": getattr(event, "group_name", "") or "",
        "event_type": event.event_type,
        "event_type_label": event_type_label(event.event_type),
        "severity": getattr(event, "severity_display", infer_event_severity(event.event_type, event.confidence)),
        "severity_label": severity_label(getattr(event, "severity_display", infer_event_severity(event.event_type, event.confidence))),
        "status": payload_status,
        "status_label": status_label(payload_status),
        "confidence": event.confidence,
        "created_at": iso_dt(event.created_at),
        "created_at_label": format_dt(event.created_at),
        "snapshot_url": f"/events/{event.id}/snapshot" if event.snapshot_path else "",
        "clip_url": clip_url,
        "event_url": f"/events?camera_id={event.camera_id}&status={payload_status}",
        "camera_url": f"/cameras/{event.camera_id}",
        "lifecycle_action": getattr(event, "lifecycle_action", "open"),
        "alarm_category": getattr(event, "alarm_category", None),
        "alarm_eligible": event_alarm_eligible(event),
        "is_alarm_active": event_alarm_active(event),
        "correlation_key": getattr(event, "correlation_key", None),
        "related_event_id": getattr(event, "related_event_id", None),
        "resolved_by_event_id": getattr(event, "resolved_by_event_id", None),
        "resolved_at": iso_dt(getattr(event, "resolved_at", None)),
        "can_ack": status_display == "new" and event_alarm_eligible(event),
        "can_close": event_alarm_eligible(event) and event_alarm_active(event),
        "can_reopen": event_alarm_eligible(event) and status_display == "closed",
        "alarm_sound_enabled": bool(getattr(event, "alarm_sound_enabled", True)),
        "alarm_popup_enabled": bool(getattr(event, "alarm_popup_enabled", True)),
    }


def serialize_dashboard_event(event) -> dict:
    status_display = getattr(event, "status_display", normalize_event_status(event.status))
    severity_display = getattr(event, "severity_display", infer_event_severity(event.event_type, event.confidence))
    clip_url = event_clip_url(event)
    return {
        "id": event.id,
        "camera_id": event.camera_id,
        "camera_name": getattr(event, "camera_name", f"Câmera {event.camera_id}"),
        "event_type": event.event_type,
        "event_type_label": event_type_label(event.event_type),
        "severity": severity_display,
        "severity_label": severity_label(severity_display),
        "status": status_display,
        "status_label": status_label(status_display),
        "confidence": event.confidence,
        "snapshot_url": f"/events/{event.id}/snapshot" if event.snapshot_path else "",
        "clip_url": clip_url,
        "created_at_label": format_dt(event.created_at),
        "lifecycle_action": getattr(event, "lifecycle_action", "open"),
        "alarm_category": getattr(event, "alarm_category", None),
        "alarm_eligible": event_alarm_eligible(event),
        "is_alarm_active": event_alarm_active(event),
        "can_ack": status_display == "new" and event_alarm_eligible(event),
        "can_close": event_alarm_eligible(event) and event_alarm_active(event),
        "can_reopen": event_alarm_eligible(event) and status_display == "closed",
        "alarm_sound_enabled": bool(getattr(event, "alarm_sound_enabled", True)),
    }


def build_dashboard_events_payload(db: Session) -> dict[str, Any]:
    camera_map = get_camera_map(db)
    recent_events = db.query(Event).order_by(Event.id.desc()).limit(12).all()
    open_events_base_query = (
        db.query(Event)
        .filter(Event.status != "closed")
        .filter(or_(Event.is_alarm_active.is_(True), Event.is_alarm_active.is_(None)))
    )
    open_events = (
        open_events_base_query
        .order_by(Event.id.desc())
        .limit(50)
        .all()
    )
    open_events_count = int(open_events_base_query.count() or 0)
    for event in recent_events:
        enrich_event(event, camera_map)
    for event in open_events:
        enrich_event(event, camera_map)
    open_events = [event for event in open_events if event_alarm_active(event)]
    critical_open_events = [event for event in open_events if event.severity_display in {"high", "critical"}]
    latest_alarm = critical_open_events[0] if critical_open_events else None
    return {
        "open_events_count": open_events_count,
        "recent_events": [serialize_dashboard_event(event) for event in recent_events],
        "open_events": [serialize_dashboard_event(event) for event in open_events[:10]],
        "latest_alarm_signature": f"{latest_alarm.id}:{latest_alarm.status_display}" if latest_alarm else "",
        "alarm_should_play": bool(latest_alarm and latest_alarm.alarm_sound_enabled),
    }


def serialize_webrtc_camera(camera) -> dict:
    registration = register_webrtc_camera_path(camera.id, getattr(camera, "rtsp_url", None))
    path_name = str(registration.get("path") or f"cam_{camera.id}")
    path_diagnostics = get_webrtc_path_diagnostics(path_name)
    return {
        "id": camera.id,
        "name": camera.name,
        "site_name": camera.site_name or "Sem local",
        "group_name": camera.group_name or "Sem grupo",
        "status": camera.status,
        "is_running": bool(camera.is_running),
        "gateway_state": getattr(camera, "gateway_state", None),
        "path": path_name,
        "player_url": build_webrtc_player_url(path_name),
        "registration_ok": bool(registration.get("ok")),
        "registration_reason": registration.get("reason") or "",
        "registration_cached": bool(registration.get("cached")),
        "registration_masked_source": registration.get("masked_source") or "",
        "registration_response": registration.get("response") or {},
        "path_diagnostics": path_diagnostics,
    }


def build_monitor_tracks_payload(
    db: Session,
    camera_ids: str = "",
    max_age_seconds: float = 1.5,
    min_confidence: float = DEFAULT_MONITOR_BOX_MIN_CONFIDENCE,
) -> dict[str, Any]:
    ids: list[int] = []
    for raw_id in str(camera_ids or "").split(","):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        try:
            ids.append(int(raw_id))
        except Exception:
            continue

    ids = ids[:32]
    max_age = max(0.4, min(5.0, float(max_age_seconds or 1.5)))
    minimum_confidence = _normalize_monitor_box_min_confidence(min_confidence)
    camera_map = {
        int(camera.id): camera
        for camera in db.query(Camera).filter(Camera.id.in_(ids)).all()
    } if ids else {}

    cameras = {}
    for camera_id in ids:
        item = track_store.get_tracks(camera_id, max_age_seconds=max_age)
        api_read_at_ns = time.time_ns()
        if item:
            item["api_read_at_ns"] = api_read_at_ns
            tracks_published_at_ns = item.get("tracks_published_at_ns")
            if tracks_published_at_ns:
                try:
                    item["track_publish_to_api_ms"] = round(
                        max(
                            0.0,
                            (
                                api_read_at_ns - int(tracks_published_at_ns)
                            )
                            / 1_000_000.0,
                        ),
                        3,
                    )
                except (TypeError, ValueError):
                    item["track_publish_to_api_ms"] = None
        item = _filter_visual_gate_tracks(item, camera_map.get(camera_id))
        item = _filter_monitor_box_min_confidence(item, minimum_confidence)
        cameras[str(camera_id)] = item or {
            "camera_id": camera_id,
            "tracks": [],
            "stale": True,
            "age_seconds": None,
        }

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "max_age_seconds": max_age,
        "min_confidence": minimum_confidence,
        "cameras": cameras,
    }
