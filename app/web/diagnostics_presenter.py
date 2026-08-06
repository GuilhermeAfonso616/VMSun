"""Payloads e view-models da tela de diagnosticos operacionais."""

from collections import deque
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.camera_gateway_client import fetch_gateway_health
from app.services.camera_operational_state import build_camera_operational_state
from app.services.camera_registry import registry
from app.services.runtime_client import get_runtime_health_snapshot

def runtime_tuning_snapshot(*args, **kwargs):
    return {"mode": "vms"}

def engine_status_snapshot(*args, **kwargs):
    return {"status": "disabled"}

class _DummyIdempotencyStore:
    def list_recent(self, *args, **kwargs): return []

event_idempotency_store = _DummyIdempotencyStore()
from app.web.camera_detail_presenter import (
    enrich_camera_for_template,
    enrich_event,
    event_type_label,
    format_dt,
    get_camera_map,
)
from app.web.monitor_presenter import (
    build_monitor_alarm_payload,
    priority_sort_value,
    query_latest_events_by_camera,
    serialize_monitor_camera,
)
from app.web.operational_metrics_presenter import (
    age_seconds,
    build_dashboard_metrics_snapshot,
    diagnose_camera_worker,
    now_utc_naive,
    parse_iso_datetime,
    safe_float,
)


def current_gateway_capture_mode() -> str:
    return (
        "hybrid"
        if bool(settings.camera_gateway_worker_rtsp_fallback_enabled)
        else "gateway_only"
    )


def tail_lines(path: Path, limit: int) -> list[str]:
    buffer = deque(maxlen=max(1, int(limit)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                buffer.append(line.rstrip("\r\n"))
    except Exception:
        return []
    return [line for line in buffer if line.strip()]


def get_gateway_health_snapshot(include_gateway: bool) -> dict | None:
    if not include_gateway:
        return None
    try:
        return fetch_gateway_health()
    except Exception:
        return None


def build_diagnostics_payload(
    db: Session,
    *,
    include_logs: bool = True,
    include_gateway: bool = True,
) -> dict:
    now = now_utc_naive()
    metrics_snapshot = build_dashboard_metrics_snapshot(db)
    health_snapshot = get_runtime_health_snapshot()
    runtime_tuning = health_snapshot.get("runtime_tuning") if isinstance(health_snapshot, dict) else None
    if not isinstance(runtime_tuning, dict):
        runtime_tuning = runtime_tuning_snapshot(
            active_workers=len(registry.list_workers()),
            gpu=health_snapshot.get("gpu") if isinstance(health_snapshot, dict) else None,
        )
    detector_engine = health_snapshot.get("detector_engine") if isinstance(health_snapshot, dict) else None
    if not isinstance(detector_engine, dict):
        detector_engine = engine_status_snapshot()
    inference_pool_summary = health_snapshot.get("inference_pool_summary") if isinstance(health_snapshot, dict) else None
    if not isinstance(inference_pool_summary, dict):
        inference_pool_summary = {
            "enabled": bool(settings.inference_pool_enabled),
            "backend": str(settings.inference_pool_backend or "local"),
            "pool_count": int(settings.inference_pool_count),
            "max_cameras_per_pool": int(settings.inference_pool_max_cameras_per_pool),
            "pools": [],
        }
    log_snapshot = build_diagnostics_log_snapshot() if include_logs else {
        "entries": [],
        "sources": [],
        "logs_dir": str(settings.logs_dir),
        "limit_per_file": 0,
    }
    gateway_health = get_gateway_health_snapshot(include_gateway)

    camera_map = get_camera_map(db)
    latest_by_camera = query_latest_events_by_camera(db)
    for event in latest_by_camera.values():
        enrich_event(event, camera_map)
    alarm_payload = build_monitor_alarm_payload(db)
    open_by_camera = alarm_payload["open_by_camera"]
    camera_metrics_by_id = {item["id"]: item for item in metrics_snapshot.get("camera_metrics", [])}
    health_by_id = {item["camera_id"]: item for item in health_snapshot.get("cameras", [])}

    camera_rows = []
    restart_total = 0
    stalling_cameras = 0
    dedupe_recent_total = 0
    capture_source_gateway_count = 0
    capture_source_python_count = 0
    capture_source_unknown_count = 0
    capture_source_gateway_fps = 0.0
    capture_source_python_fps = 0.0

    for camera in camera_map.values():
        health_item = health_by_id.get(camera.id, {})
        enrich_camera_for_template(camera, camera.id, health_item)
        latest_event = latest_by_camera.get(camera.id)
        camera_open_events = open_by_camera.get(camera.id, [])
        camera.open_events_count = len(camera_open_events)
        camera.new_events_count = sum(1 for event in camera_open_events if event.status_display == "new")
        camera.has_open_alarm = bool(camera_open_events)
        camera.highest_open_severity = None
        if camera_open_events:
            camera.highest_open_severity = min(
                (event.severity_display for event in camera_open_events),
                key=priority_sort_value,
            )
        camera.last_event_at = latest_event.created_at if latest_event else None
        camera.last_event_label = format_dt(latest_event.created_at) if latest_event else "-"
        camera.last_event_type = (
            getattr(latest_event, "event_type_label", None) or event_type_label(latest_event.event_type)
            if latest_event
            else "Sem eventos"
        )
        camera.last_event_severity = latest_event.severity_display if latest_event else None
        camera.last_event_status = latest_event.status_display if latest_event else None
        row = serialize_monitor_camera(camera)
        metrics_item = camera_metrics_by_id.get(row["id"], {})
        operational_state = build_camera_operational_state(camera, metrics=metrics_item, health_entry=health_item)
        worker_diagnosis = operational_state.get("worker_diagnosis") or diagnose_camera_worker(metrics_item, health_item, row.get("status"))
        dedupe_summary = event_idempotency_store.get_recent_summary(row["id"], now=now)

        restart_total += int(row.get("restart_count", 0) or 0)
        if int(row.get("consecutive_stall_checks", 0) or 0) > 0:
            stalling_cameras += 1
        dedupe_recent_total += int(dedupe_summary.get("dedupe_recent_count", 0) or 0)
        capture_source = str(metrics_item.get("capture_source") or "-").strip().lower()
        raw_fps = safe_float(metrics_item.get("raw_fps"), 0.0) or 0.0
        if capture_source == "gateway frames":
            capture_source_gateway_count += 1
            capture_source_gateway_fps += raw_fps
            capture_source_label = "Gateway"
        elif capture_source == "rtsp":
            capture_source_python_count += 1
            capture_source_python_fps += raw_fps
            capture_source_label = "Python RTSP"
        else:
            capture_source_unknown_count += 1
            capture_source_label = "-"

        row.update(
            {
                "status_operational": row.get("health_status_display") or row.get("health_status") or row.get("status"),
                "operator_status": operational_state.get("operator_status"),
                "operational_state": operational_state,
                "operational_health": operational_state.get("operational_health") or row.get("operational_health"),
                "status_legacy": row.get("status"),
                "status_legacy_display": "running" if str(row.get("status") or "").startswith("running") else row.get("status"),
                "last_frame_label": format_dt(parse_iso_datetime(row.get("last_frame_at"))),
                "last_processed_frame_label": format_dt(parse_iso_datetime(row.get("last_processed_frame_at"))),
                "last_successful_inference_label": format_dt(parse_iso_datetime(row.get("last_successful_inference_at"))),
                "last_metrics_label": format_dt(parse_iso_datetime(row.get("last_metrics_at"))),
                "last_restart_label": format_dt(parse_iso_datetime(row.get("last_restart_at"))),
                "last_event_label": format_dt(parse_iso_datetime(row.get("last_event_at"))),
                "last_event_age_seconds": age_seconds(row.get("last_event_at"), now),
                "last_frame_age_seconds": age_seconds(row.get("last_frame_at"), now),
                "last_processed_frame_age_seconds": age_seconds(row.get("last_processed_frame_at"), now),
                "last_successful_inference_age_seconds": age_seconds(row.get("last_successful_inference_at"), now),
                "last_metrics_age_seconds": age_seconds(row.get("last_metrics_at"), now),
                  "fps": metrics_item.get("fps"),
                  "raw_fps": metrics_item.get("raw_fps"),
                  "processed_fps": metrics_item.get("processed_fps"),
                "capture_source": metrics_item.get("capture_source") or "-",
                "capture_source_label": capture_source_label,
                "gateway_fallback_active": bool(metrics_item.get("gateway_fallback_active", False)),
                "gateway_recovery_count": int(metrics_item.get("gateway_recovery_count", 0) or 0),
                "gateway_recovery_last_success_at": metrics_item.get("gateway_recovery_last_success_at"),
                "gateway_fallback_started_at": metrics_item.get("gateway_fallback_started_at"),
                "gateway_recovery_last_success_label": format_dt(parse_iso_datetime(metrics_item.get("gateway_recovery_last_success_at"))),
                "gateway_fallback_started_label": format_dt(parse_iso_datetime(metrics_item.get("gateway_fallback_started_at"))),
                  "read_ms": metrics_item.get("read_ms"),
                "infer_ms": metrics_item.get("infer_ms"),
                "loop_ms": metrics_item.get("loop_ms"),
                "process_cpu_percent": metrics_item.get("process_cpu_percent"),
                "process_rss_mb": metrics_item.get("process_rss_mb"),
                "system_cpu_percent": metrics_item.get("system_cpu_percent"),
                "system_ram_percent": metrics_item.get("system_ram_percent"),
                "metrics_updated_at": metrics_item.get("updated_at"),
                "metrics_health_status": metrics_item.get("health_status"),
                "metrics_health_status_display": metrics_item.get("health_status_display"),
                "metrics_restart_count": metrics_item.get("restart_count"),
                "metrics_last_restart_reason": metrics_item.get("last_restart_reason"),
                "detector_fp16_enabled": bool(metrics_item.get("detector_fp16_enabled", settings.detector_fp16_enabled)),
                "inference_pool_enabled": bool(metrics_item.get("inference_pool_enabled", settings.inference_pool_enabled)),
                "inference_pool_mode": metrics_item.get("inference_pool_mode") or ("pool" if settings.inference_pool_enabled else "direct"),
                "inference_pool_backend": metrics_item.get("inference_pool_backend") or settings.inference_pool_backend,
                "inference_pool_id": metrics_item.get("inference_pool_id"),
                "inference_pool_count": metrics_item.get("inference_pool_count") or settings.inference_pool_count,
                "inference_pool_assigned_cameras": metrics_item.get("inference_pool_assigned_cameras"),
                "inference_pool_total_assigned_cameras": metrics_item.get("inference_pool_total_assigned_cameras"),
                "inference_pool_max_cameras_per_pool": metrics_item.get("inference_pool_max_cameras_per_pool") or settings.inference_pool_max_cameras_per_pool,
                "inference_pool_queue_size": int(metrics_item.get("inference_pool_queue_size", 0) or 0),
                "inference_pool_last_wait_ms": metrics_item.get("inference_pool_last_wait_ms"),
                "inference_pool_timed_out": int(metrics_item.get("inference_pool_timed_out", 0) or 0),
                "inference_pool_rejected": int(metrics_item.get("inference_pool_rejected", 0) or 0),
                "inference_pool_replaced": int(metrics_item.get("inference_pool_replaced", 0) or 0),
                "inference_pool_dropped_oldest": int(metrics_item.get("inference_pool_dropped_oldest", 0) or 0),
                "inference_pool_stale_dropped": int(metrics_item.get("inference_pool_stale_dropped", 0) or 0),
                "inference_pool_last_total_latency_ms": metrics_item.get("inference_pool_last_total_latency_ms"),
                "inference_pool_last_infer_ms": metrics_item.get("inference_pool_last_infer_ms"),
                "inference_pool_central_http_ms": metrics_item.get("inference_pool_central_http_ms"),
                "inference_pool_central_jpeg_quality": metrics_item.get("inference_pool_central_jpeg_quality"),
                "inference_pool_overflow_policy": metrics_item.get("inference_pool_overflow_policy") or settings.inference_pool_overflow_policy,
                "inference_pool_max_job_age_seconds": metrics_item.get("inference_pool_max_job_age_seconds"),
                "health_snapshot_status": health_item.get("health_status"),
                "stability_class": metrics_item.get("stability_class") or worker_diagnosis.get("stability_class", "idle"),
                "stability_label": metrics_item.get("stability_label") or worker_diagnosis.get("stability_label", "-"),
                "diagnosis_label": metrics_item.get("diagnosis_label") or worker_diagnosis.get("diagnosis_label", "-"),
                "diagnosis_reason": metrics_item.get("diagnosis_reason") or worker_diagnosis.get("diagnosis_reason", "-"),
                "reconnect_count": metrics_item.get("reconnect_count", 0),
                "dropped_frames_count": metrics_item.get("dropped_frames_count", 0),
                "capture_queue_dropped_frames": metrics_item.get("capture_queue_dropped_frames", 0),
                "visual_jobs_dropped": metrics_item.get("visual_jobs_dropped", 0),
                "dedupe_recent_count": dedupe_summary.get("dedupe_recent_count", 0),
                "dedupe_recent_at": dedupe_summary.get("dedupe_recent_at"),
                "dedupe_recent_age_seconds": dedupe_summary.get("dedupe_recent_age_seconds"),
                "dedupe_window_seconds": dedupe_summary.get("dedupe_window_seconds"),
            }
        )
        camera_rows.append(row)

    summary = {
        "camera_total": len(camera_rows),
        "running_count": int(health_snapshot.get("running_count", 0) or 0),
        "degraded_count": int(health_snapshot.get("degraded_count", 0) or 0),
        "reconnecting_count": int(health_snapshot.get("reconnecting_count", 0) or 0),
        "offline_count": int(health_snapshot.get("offline_count", 0) or 0),
        "stopped_count": int(health_snapshot.get("stopped_count", 0) or 0),
        "worker_count": int(metrics_snapshot.get("worker_count", 0) or 0),
        "capture_source_gateway_count": capture_source_gateway_count,
        "capture_source_python_count": capture_source_python_count,
        "capture_source_unknown_count": capture_source_unknown_count,
        "capture_source_gateway_fps": round(capture_source_gateway_fps, 2),
        "capture_source_python_fps": round(capture_source_python_fps, 2),
        "restart_total": restart_total,
        "stalling_cameras": stalling_cameras,
        "dedupe_recent_total": dedupe_recent_total,
        "open_events_count": int(alarm_payload.get("open_events_total", 0) or 0),
        "dedupe_window_seconds": event_idempotency_store.window_seconds,
        "last_updated_at": metrics_snapshot.get("last_updated_at"),
        "generated_at": now.isoformat(),
        "gateway_capture_mode": current_gateway_capture_mode(),
        "gateway_capture_mode_label": "Hibrido" if current_gateway_capture_mode() == "hybrid" else "So Gateway",
        "gateway_rtsp_fallback_enabled": bool(settings.camera_gateway_worker_rtsp_fallback_enabled),
        "runtime_tuning": runtime_tuning,
        "detector_engine": detector_engine,
        "inference_pool_summary": inference_pool_summary,
    }

    camera_rows.sort(
        key=lambda item: (
            0 if item.get("has_open_alarm") or int(item.get("open_events_count", 0) or 0) > 0 else 1,
            priority_sort_value(item.get("highest_open_severity") or item.get("camera_priority")),
            0 if item.get("is_running") else 1,
            (item.get("site_name") or ""),
            (item.get("group_name") or ""),
            (item.get("name") or "").lower(),
        )
    )

    return {
        "generated_at": now.isoformat(),
        "summary": summary,
        "cameras": camera_rows,
        "health_snapshot": health_snapshot,
        "metrics_snapshot": metrics_snapshot,
        "logs": log_snapshot,
        "gateway": gateway_health,
        "runtime_tuning": runtime_tuning,
        "detector_engine": detector_engine,
        "inference_pool_summary": inference_pool_summary,
        "monitor_filters": {
            "site_name": "",
            "group_name": "",
            "camera_priority": "",
            "only_running": False,
            "only_alarm": False,
            "grid": 16,
        },
        "latest_alarm_signature": alarm_payload.get("latest_alarm_signature", ""),
        "alarm_should_play": bool(alarm_payload.get("alarm_should_play")),
    }


def build_diagnostics_shell_payload() -> dict:
    now = now_utc_naive()
    health_snapshot = get_runtime_health_snapshot()
    runtime_tuning = health_snapshot.get("runtime_tuning") if isinstance(health_snapshot, dict) else None
    if not isinstance(runtime_tuning, dict):
        runtime_tuning = runtime_tuning_snapshot(active_workers=len(registry.list_workers()))
    detector_engine = health_snapshot.get("detector_engine") if isinstance(health_snapshot, dict) else None
    if not isinstance(detector_engine, dict):
        detector_engine = engine_status_snapshot()
    inference_pool_summary = health_snapshot.get("inference_pool_summary") if isinstance(health_snapshot, dict) else None
    if not isinstance(inference_pool_summary, dict):
        inference_pool_summary = {
            "enabled": bool(settings.inference_pool_enabled),
            "backend": str(settings.inference_pool_backend or "local"),
            "pool_count": int(settings.inference_pool_count),
            "max_cameras_per_pool": int(settings.inference_pool_max_cameras_per_pool),
            "pools": [],
        }
    camera_total = int(health_snapshot.get("camera_total", 0) or 0)
    if camera_total <= 0:
        camera_total = sum(
            int(health_snapshot.get(key, 0) or 0)
            for key in (
                "running_count",
                "degraded_count",
                "reconnecting_count",
                "offline_count",
                "stopped_count",
            )
        )
    summary = {
        "camera_total": camera_total,
        "running_count": int(health_snapshot.get("running_count", 0) or 0),
        "degraded_count": int(health_snapshot.get("degraded_count", 0) or 0),
        "reconnecting_count": int(health_snapshot.get("reconnecting_count", 0) or 0),
        "offline_count": int(health_snapshot.get("offline_count", 0) or 0),
        "stopped_count": int(health_snapshot.get("stopped_count", 0) or 0),
        "worker_count": 0,
        "capture_source_gateway_count": 0,
        "capture_source_python_count": 0,
        "capture_source_unknown_count": 0,
        "capture_source_gateway_fps": 0.0,
        "capture_source_python_fps": 0.0,
        "restart_total": 0,
        "stalling_cameras": 0,
        "dedupe_recent_total": 0,
        "open_events_count": 0,
        "dedupe_window_seconds": event_idempotency_store.window_seconds,
        "last_updated_at": None,
        "generated_at": now.isoformat(),
        "gateway_capture_mode": current_gateway_capture_mode(),
        "gateway_capture_mode_label": "Hibrido" if current_gateway_capture_mode() == "hybrid" else "So Gateway",
        "gateway_rtsp_fallback_enabled": bool(settings.camera_gateway_worker_rtsp_fallback_enabled),
        "runtime_tuning": runtime_tuning,
        "detector_engine": detector_engine,
        "inference_pool_summary": inference_pool_summary,
    }
    return {
        "generated_at": now.isoformat(),
        "summary": summary,
        "cameras": [],
        "health_snapshot": health_snapshot,
        "metrics_snapshot": {},
        "logs": {
            "entries": [],
            "sources": [],
            "logs_dir": str(settings.logs_dir),
            "limit_per_file": 0,
        },
        "gateway": None,
        "runtime_tuning": runtime_tuning,
        "detector_engine": detector_engine,
        "inference_pool_summary": inference_pool_summary,
    }


def build_diagnostics_log_snapshot(limit_per_file: int = 80) -> dict[str, Any]:
    logs_dir = Path(settings.logs_dir)
    sources = [
        ("app.log", "app"),
        ("error.log", "error"),
        ("inference_pool.log", "pool"),
        ("event_rules_debug.log", "regras"),
        ("gateway.log", "gateway"),
    ]

    entries: list[dict[str, str]] = []
    files_seen: list[str] = []

    for filename, source_label in sources:
        path = logs_dir / filename
        if not path.exists():
            continue
        files_seen.append(filename)
        for line in tail_lines(path, limit_per_file):
            entries.append({"source": source_label, "line": line})

    return {
        "entries": entries[-max(1, limit_per_file * len(sources)) :],
        "sources": files_seen,
        "logs_dir": str(logs_dir),
        "limit_per_file": int(limit_per_file),
    }
