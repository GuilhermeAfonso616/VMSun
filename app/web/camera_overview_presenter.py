"""Composicao da listagem operacional e recomendacoes leves de cameras."""

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Camera
from app.services.camera_operational_state import (
    build_camera_operational_health as central_build_camera_operational_health,
    build_camera_operational_state,
)
from app.services.metrics_store import metrics_store
from app.services.runtime_client import get_runtime_health_snapshot
from app.web.camera_detail_presenter import enrich_camera_for_template
from app.web.operational_metrics_presenter import (
    enrich_camera_metrics_payload,
    safe_float,
)


def _camera_list_badge(status: str, label: str, detail: str = "") -> dict[str, Any]:
    return {"status": status, "label": label, "detail": detail}


def _format_camera_metric(value: Any, suffix: str, precision: int = 0) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return "-"
    return f"{numeric:.{precision}f}{suffix}"


def build_camera_list_status(
    camera: Camera,
    metrics: dict | None,
    health_entry: dict | None,
) -> dict[str, Any]:
    metrics = metrics or {}
    health_entry = health_entry or {}
    db_status = str(getattr(camera, "status", "") or "idle").strip().lower()
    health_status = str(
        health_entry.get("health_status")
        or getattr(camera, "health_status", "")
        or metrics.get("health_status")
        or db_status
        or "idle"
    ).strip().lower()
    worker_mode = str(
        health_entry.get("worker_mode")
        or metrics.get("worker_mode")
        or getattr(camera, "worker_mode", "")
        or "stopped"
    ).strip().lower()
    raw_fps = safe_float(metrics.get("raw_fps"), 0.0) or 0.0
    processed_fps = safe_float(metrics.get("processed_fps"), 0.0) or 0.0
    infer_ms = safe_float(metrics.get("infer_ms"))
    loop_ms = safe_float(metrics.get("loop_ms"))
    pool_id = metrics.get("inference_pool_id")
    pool_queue = int(safe_float(metrics.get("inference_pool_queue_size"), 0.0) or 0)
    capture_source = str(metrics.get("capture_source") or "").strip()
    gateway_state = str(health_entry.get("gateway_state") or "").strip()

    if db_status in {"running", "running_motion_test"}:
        config = _camera_list_badge("running", "Ativa", db_status)
    elif db_status == "stopped":
        config = _camera_list_badge("stopped", "Parada", "parada manual")
    elif db_status in {"disabled", "deleted"}:
        config = _camera_list_badge("offline", "Desativada", db_status)
    else:
        config = _camera_list_badge("idle", "Cadastro", db_status or "idle")

    if health_status in {"running", "running_motion_test"}:
        runtime = _camera_list_badge("running", "Rodando", worker_mode or health_status)
    elif health_status in {"starting", "warming_up"}:
        runtime = _camera_list_badge("reconnecting", "Iniciando", health_status)
    elif health_status == "reconnecting":
        runtime = _camera_list_badge("reconnecting", "Reconectando", "tentando recuperar")
    elif health_status == "degraded":
        runtime = _camera_list_badge("degraded", "Degradado", "worker com alerta")
    elif health_status == "offline":
        runtime = _camera_list_badge("offline", "Offline", "sem stream/worker")
    elif health_status == "stopped":
        runtime = _camera_list_badge("stopped", "Sem worker", "runtime parado")
    elif db_status in {"running", "running_motion_test"}:
        runtime = _camera_list_badge("degraded", "Sem worker", health_status or "sem snapshot")
    else:
        runtime = _camera_list_badge("idle", "Sem worker", health_status or "idle")

    operational_state = build_camera_operational_state(
        camera,
        metrics=metrics,
        health_entry=health_entry,
    )
    operational_health = operational_state.get("operational_health") or {}
    analysis = operational_health.get("analysis") or {}
    worker_diagnosis = operational_state.get("worker_diagnosis") or {}
    analysis_status = str(analysis.get("status") or "").strip().lower()
    analysis_label = str(analysis.get("label") or "").strip()
    analysis_detail = str(analysis.get("detail") or "").strip()
    diagnosis_label = str(worker_diagnosis.get("stability_label") or "").strip()

    if runtime["status"] in {"stopped", "offline", "idle"}:
        pipeline = _camera_list_badge("stopped", "IA parada", runtime["detail"])
    elif (
        diagnosis_label == "sob carga"
        or pool_queue > 0
        or (loop_ms is not None and loop_ms >= 1200)
    ):
        detail_parts = []
        if pool_id is not None:
            detail_parts.append(f"pool {pool_id}")
        if pool_queue > 0:
            detail_parts.append(f"fila {pool_queue}")
        if infer_ms is not None:
            detail_parts.append(f"infer {_format_camera_metric(infer_ms, 'ms')}")
        pipeline = _camera_list_badge(
            "degraded",
            "Sob carga",
            " | ".join(detail_parts) or diagnosis_label,
        )
    elif analysis_status == "ok":
        detail = (
            f"infer {_format_camera_metric(infer_ms, 'ms')}"
            if infer_ms is not None
            else analysis_detail
        )
        pipeline = _camera_list_badge("running", analysis_label or "IA OK", detail)
    elif analysis_status == "standby":
        pipeline = _camera_list_badge(
            "reconnecting",
            analysis_label or "IA em espera",
            analysis_detail or "aguardando movimento",
        )
    elif analysis_status in {"processing", "warming"}:
        pipeline = _camera_list_badge(
            "degraded",
            analysis_label or "Processando",
            analysis_detail,
        )
    elif analysis_label:
        pipeline = _camera_list_badge("degraded", analysis_label, analysis_detail)
    else:
        pipeline = _camera_list_badge(
            "idle",
            "Sem metricas",
            diagnosis_label or "sem pulso",
        )

    if capture_source:
        capture_label = (
            "Gateway" if "gateway" in capture_source.lower() else "RTSP"
        )
        capture_detail = f"raw {raw_fps:.2f} fps | proc {processed_fps:.2f} fps"
        if gateway_state:
            capture_detail = f"{gateway_state} | {capture_detail}"
        capture = _camera_list_badge(
            "running" if raw_fps > 0 or processed_fps > 0 else "degraded",
            capture_label,
            capture_detail,
        )
    elif raw_fps > 0 or processed_fps > 0:
        capture = _camera_list_badge(
            "running",
            "Frames",
            f"raw {raw_fps:.2f} fps | proc {processed_fps:.2f} fps",
        )
    elif gateway_state:
        capture = _camera_list_badge("degraded", "Gateway", gateway_state)
    elif not getattr(camera, "rtsp_url", None):
        capture = _camera_list_badge("offline", "Sem RTSP", "URL nao configurada")
    else:
        capture = _camera_list_badge("idle", "Sem frame", "sem metricas recentes")

    return {
        "config": config,
        "runtime": runtime,
        "pipeline": pipeline,
        "capture": capture,
    }


def build_light_profile_recommendation(
    camera: Camera | None,
    metrics: dict | None,
) -> dict[str, Any]:
    payload = dict(metrics or {})
    raw_fps = safe_float(payload.get("raw_fps"), 0.0) or 0.0
    processed_fps = safe_float(payload.get("processed_fps"), 0.0) or 0.0
    pressure = bool(payload.get("capture_inference_pressure", False))
    has_roi = bool(payload.get("roi_enabled", False))
    motion_mode = str(payload.get("worker_mode") or "").lower() == "motion_test"
    recommendations: list[str] = []
    overrides: dict[str, Any] = {}

    if not pressure:
        return {
            "enabled": False,
            "title": "Perfil leve não necessário",
            "summary": "A câmera não está sob pressão captável no momento.",
            "recommendations": recommendations,
            "overrides": overrides,
        }

    if not has_roi:
        recommendations.append("Ativar ROI para reduzir área processada.")
        overrides["processing_max_width"] = 800
        overrides["processing_max_height"] = 450

    if raw_fps > 0 and processed_fps > 0 and raw_fps > processed_fps * 1.25:
        recommendations.append("Aumentar o intervalo de inferência desta câmera.")
        overrides["normal_inference_interval_seconds"] = 0.5
        overrides["capture_drop_frames"] = max(
            2,
            int(payload.get("capture_queue_dropped_frames", 0) or 0) + 1,
        )

    if not motion_mode:
        recommendations.append("Preferir modo com movimento nesta câmera.")
        overrides["prefer_motion_test"] = True
    if not recommendations:
        recommendations.append("Manter o perfil atual e apenas reduzir o fluxo visual.")

    return {
        "enabled": True,
        "title": "Perfil leve recomendado",
        "summary": (
            "A câmera está sob pressão; estes ajustes reduzem custo "
            "sem mexer no detector."
        ),
        "recommendations": recommendations,
        "overrides": overrides,
    }


def build_camera_operational_health(
    camera: Camera,
    metrics: dict[str, Any] | None,
    health_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    return central_build_camera_operational_health(camera, metrics, health_entry)


def build_camera_overview_context(
    db: Session,
    *,
    message: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    cameras = (
        db.query(Camera)
        .filter(Camera.is_deleted == False)
        .order_by(Camera.id.desc())
        .all()
    )
    health_snapshot = get_runtime_health_snapshot()
    health_by_camera_id = {
        int(item["camera_id"]): item
        for item in health_snapshot.get("cameras", [])
        if item.get("camera_id") is not None
    }
    for camera in cameras:
        health_entry = health_by_camera_id.get(camera.id, {})
        metrics = enrich_camera_metrics_payload(
            metrics_store.get_metrics(camera.id) or {}
        )
        enrich_camera_for_template(camera, camera.id, health_entry)
        camera.list_status = build_camera_list_status(camera, metrics, health_entry)

    return {
        "cameras": cameras,
        "site_options": sorted(
            {camera.site_name for camera in cameras if camera.site_name},
            key=str.casefold,
        ),
        "group_options": sorted(
            {camera.group_name for camera in cameras if camera.group_name},
            key=str.casefold,
        ),
        "message": message,
        "error": error,
        "bulk_delete_enabled": bool(
            str(settings.camera_bulk_delete_password or "").strip()
        ),
    }
