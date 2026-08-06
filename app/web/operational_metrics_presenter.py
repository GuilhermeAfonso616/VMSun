"""Apresentacao compartilhada de metricas e saude operacional."""

import os
import subprocess
from datetime import datetime
from typing import Any

import psutil
from sqlalchemy.orm import Session

from app.core.timezone import utc_now_naive as _shared_utc_now_naive
from app.services.camera_operational_state import (
    diagnose_camera_worker as central_diagnose_camera_worker,
)
from app.services.metrics_store import metrics_store
from app.services.operational_diagnostics_service import build_ai_operational_diagnostics
from app.services.runtime_client import get_runtime_health_snapshot
from app.web.camera_detail_presenter import get_camera_map


_WEB_PROCESS = psutil.Process(os.getpid())


def now_utc_naive() -> datetime:
    return _shared_utc_now_naive()


def parse_iso_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def age_seconds(value, now: datetime | None = None) -> float | None:
    dt_value = parse_iso_datetime(value)
    if dt_value is None:
        return None
    current = now or now_utc_naive()
    try:
        return round((current - dt_value).total_seconds(), 2)
    except Exception:
        return None


def safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def enrich_camera_metrics_payload(metrics: dict | None) -> dict:
    payload = dict(metrics or {})
    raw_fps = safe_float(payload.get("raw_fps"), 0.0) or 0.0
    processed_fps = safe_float(payload.get("processed_fps"), 0.0) or 0.0
    dropped_frames = int(payload.get("capture_queue_dropped_frames", 0) or 0)

    pressure_ratio = None
    if processed_fps > 0:
        pressure_ratio = round(raw_fps / processed_fps, 2)

    pipeline_pressure = bool(
        processed_fps > 0
        and raw_fps > 0
        and raw_fps > processed_fps * 1.25
    ) or dropped_frames >= 50

    pipeline_mode = "ajuste normal"
    pipeline_hint = "Captação e inferência estão equilibradas."
    if pipeline_pressure:
        pipeline_mode = "frame mais recente"
        pipeline_hint = (
            "A captura está mais rápida que a inferência; o pipeline mantém "
            "só o frame mais recente para preservar frescor."
        )

    payload.update(
        {
            "capture_inference_pressure": pipeline_pressure,
            "capture_inference_pressure_ratio": pressure_ratio,
            "capture_inference_pressure_label": "em pressão" if pipeline_pressure else "estável",
            "capture_inference_pipeline_mode": pipeline_mode,
            "capture_inference_pipeline_hint": pipeline_hint,
        }
    )
    return payload


def _read_gpu_snapshot() -> dict:
    gpu = {
        "available": False,
        "name": None,
        "utilization_percent": None,
        "memory_used_mb": None,
        "memory_total_mb": None,
        "memory_allocated_mb": None,
        "memory_reserved_mb": None,
        "temperature_c": None,
        "device_count": 0,
    }

    try:
        import torch

        if torch.cuda.is_available():
            gpu["available"] = True
            gpu["device_count"] = int(torch.cuda.device_count())
            gpu["name"] = torch.cuda.get_device_name(0)

            props = torch.cuda.get_device_properties(0)
            gpu["memory_total_mb"] = round(float(props.total_memory) / (1024 * 1024), 2)
            gpu["memory_allocated_mb"] = round(
                float(torch.cuda.memory_allocated(0)) / (1024 * 1024),
                2,
            )
            gpu["memory_reserved_mb"] = round(
                float(torch.cuda.memory_reserved(0)) / (1024 * 1024),
                2,
            )
    except Exception:
        pass

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            first_line = result.stdout.strip().splitlines()[0]
            parts = [part.strip() for part in first_line.split(",")]
            if len(parts) >= 5:
                gpu["available"] = True
                gpu["utilization_percent"] = safe_float(
                    parts[0],
                    gpu["utilization_percent"],
                )
                gpu["memory_used_mb"] = safe_float(parts[1], gpu["memory_used_mb"])
                gpu["memory_total_mb"] = safe_float(parts[2], gpu["memory_total_mb"])
                gpu["temperature_c"] = safe_float(parts[3], gpu["temperature_c"])
                gpu["name"] = parts[4] or gpu["name"]
    except Exception:
        pass

    return gpu


def diagnose_camera_worker(
    metrics: dict[str, Any],
    health_entry: dict[str, Any],
    camera_status: str | None = None,
) -> dict[str, str]:
    return central_diagnose_camera_worker(metrics, health_entry, camera_status)


def build_dashboard_metrics_snapshot(db: Session) -> dict:
    camera_map = get_camera_map(db)
    camera_metrics = []
    worker_metrics = []
    newest_update = None
    health_snapshot = get_runtime_health_snapshot()
    health_by_camera_id = {item["camera_id"]: item for item in health_snapshot.get("cameras", [])}

    try:
        web_cpu_percent = float(_WEB_PROCESS.cpu_percent(interval=None))
    except Exception:
        web_cpu_percent = None
    try:
        web_rss_mb = round(float(_WEB_PROCESS.memory_info().rss) / (1024 * 1024), 2)
    except Exception:
        web_rss_mb = None

    host_cpu_percent = None
    host_ram_percent = None
    try:
        host_cpu_percent = float(psutil.cpu_percent(interval=None))
    except Exception:
        pass
    try:
        host_ram_percent = float(psutil.virtual_memory().percent)
    except Exception:
        pass

    for camera in camera_map.values():
        if not str(getattr(camera, "status", "") or "").startswith("running"):
            try:
                metrics_store.remove_metrics(camera.id)
            except Exception:
                pass
            continue

        metrics = metrics_store.get_metrics(camera.id)
        if not metrics:
            continue

        metrics = enrich_camera_metrics_payload(metrics)

        health_entry = health_by_camera_id.get(camera.id, {})
        worker_diagnosis = diagnose_camera_worker(metrics, health_entry, camera.status)

        worker_metrics.append(metrics)
        updated_at = metrics.get("updated_at")
        if updated_at and (newest_update is None or str(updated_at) > str(newest_update)):
            newest_update = updated_at

        camera_metrics.append(
            {
                "id": camera.id,
                "name": camera.name,
                "status": str(camera.status or "idle"),
                "worker_mode": metrics.get("worker_mode") or getattr(camera, "worker_mode", "stopped"),
                "capture_source": metrics.get("capture_source") or "-",
                "gateway_fallback_active": bool(metrics.get("gateway_fallback_active", False)),
                "gateway_recovery_count": int(metrics.get("gateway_recovery_count", 0) or 0),
                "gateway_recovery_last_success_at": metrics.get("gateway_recovery_last_success_at"),
                "gateway_fallback_started_at": metrics.get("gateway_fallback_started_at"),
                  "tracks_count": int(metrics.get("tracks_count", 0) or 0),
                  "fps": safe_float(metrics.get("fps")),
                  "raw_fps": safe_float(metrics.get("raw_fps")),
                  "processed_fps": safe_float(metrics.get("processed_fps")),
                  "capture_inference_pressure": bool(metrics.get("capture_inference_pressure", False)),
                  "capture_inference_pressure_ratio": metrics.get("capture_inference_pressure_ratio"),
                  "capture_inference_pressure_label": metrics.get("capture_inference_pressure_label"),
                  "capture_inference_pipeline_mode": metrics.get("capture_inference_pipeline_mode"),
                  "read_ms": safe_float(metrics.get("read_ms")),
                "infer_ms": safe_float(metrics.get("infer_ms")),
                "plot_ms": safe_float(metrics.get("plot_ms")),
                "jpeg_ms": safe_float(metrics.get("jpeg_ms")),
                "loop_ms": safe_float(metrics.get("loop_ms")),
                "process_cpu_percent": safe_float(metrics.get("process_cpu_percent")),
                "system_cpu_percent": safe_float(metrics.get("system_cpu_percent")),
                "process_rss_mb": safe_float(metrics.get("process_rss_mb")),
                "system_ram_percent": safe_float(metrics.get("system_ram_percent")),
                "last_successful_inference_at": metrics.get("last_successful_inference_at"),
                "updated_at": updated_at,
                "roi_name": metrics.get("roi_name"),
                "line_direction": metrics.get("line_direction"),
                "roi_crop_active": bool(metrics.get("roi_crop_active", False)),
                "health_status": health_entry.get("health_status") or metrics.get("health_status") or str(camera.status or "idle"),
                "health_status_display": "running"
                if (health_entry.get("health_status") or metrics.get("health_status") or str(camera.status or "idle")) == "running_motion_test"
                else (health_entry.get("health_status") or metrics.get("health_status") or str(camera.status or "idle")),
                "last_restart_reason": health_entry.get("last_restart_reason"),
                "restart_count": int(health_entry.get("restart_count", 0) or 0),
                "reconnect_count": int(metrics.get("reconnect_count", 0) or 0),
                "dropped_frames_count": int(metrics.get("dropped_frames_count", 0) or 0),
                "capture_queue_dropped_frames": int(metrics.get("capture_queue_dropped_frames", 0) or 0),
                "visual_jobs_dropped": int(metrics.get("visual_jobs_dropped", 0) or 0),
                **worker_diagnosis,
            }
        )

    worker_cpu_total = sum(safe_float(item.get("process_cpu_percent"), 0.0) or 0.0 for item in worker_metrics)
    worker_rss_total = sum(safe_float(item.get("process_rss_mb"), 0.0) or 0.0 for item in worker_metrics)
    worker_raw_fps_total = sum(safe_float(item.get("raw_fps"), 0.0) or 0.0 for item in worker_metrics)
    worker_processed_fps_total = sum(safe_float(item.get("processed_fps"), 0.0) or 0.0 for item in worker_metrics)
    worker_fps_total = worker_processed_fps_total
    worker_infer_avg = (
        sum(safe_float(item.get("infer_ms"), 0.0) or 0.0 for item in worker_metrics) / len(worker_metrics)
        if worker_metrics
        else None
    )
    worker_loop_avg = (
        sum(safe_float(item.get("loop_ms"), 0.0) or 0.0 for item in worker_metrics) / len(worker_metrics)
        if worker_metrics
        else None
    )

    runtime_gpu = health_snapshot.get("gpu") if isinstance(health_snapshot, dict) else None
    gpu = runtime_gpu if isinstance(runtime_gpu, dict) else _read_gpu_snapshot()
    running_cameras = int(health_snapshot.get("running_count", 0) or 0)

    return {
        "generated_at": now_utc_naive().isoformat(),
        "camera_total": len(camera_map),
        "running_cameras": running_cameras,
        "camera_health": {
            "running": int(health_snapshot.get("running_count", 0) or 0),
            "degraded": int(health_snapshot.get("degraded_count", 0) or 0),
            "reconnecting": int(health_snapshot.get("reconnecting_count", 0) or 0),
            "offline": int(health_snapshot.get("offline_count", 0) or 0),
            "stopped": int(health_snapshot.get("stopped_count", 0) or 0),
        },
        "worker_count": len(worker_metrics),
        "worker_cpu_total_percent": round(worker_cpu_total, 2),
        "worker_rss_total_mb": round(worker_rss_total, 2),
        "worker_fps_total": round(worker_fps_total, 2),
        "worker_raw_fps_total": round(worker_raw_fps_total, 2),
        "worker_processed_fps_total": round(worker_processed_fps_total, 2),
        "worker_infer_avg_ms": round(worker_infer_avg, 2) if worker_infer_avg is not None else None,
        "worker_loop_avg_ms": round(worker_loop_avg, 2) if worker_loop_avg is not None else None,
        "web_process_cpu_percent": round(web_cpu_percent, 2) if web_cpu_percent is not None else None,
        "web_process_rss_mb": web_rss_mb,
        "host_cpu_percent": round(host_cpu_percent, 2) if host_cpu_percent is not None else None,
        "host_ram_percent": round(host_ram_percent, 2) if host_ram_percent is not None else None,
        "gpu": gpu,
        "ai_diagnostics": build_ai_operational_diagnostics(db),
        "camera_metrics": sorted(
            camera_metrics,
            key=lambda item: (
                0 if str(item["status"]).startswith("running") else 1,
                -(item["process_cpu_percent"] or 0.0),
                item["name"].lower(),
            ),
        ),
        "last_updated_at": newest_update,
    }
