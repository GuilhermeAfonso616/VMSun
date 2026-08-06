from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timezone import utc_now_naive as _shared_utc_now_naive
from app.db.models import Camera
from app.services.camera_health_monitor import camera_health_monitor
from app.services.metrics_store import metrics_store


def now_utc_naive() -> datetime:
    return _shared_utc_now_naive()


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_iso_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except Exception:
        return None


def age_seconds(value: Any, now: datetime | None = None) -> float | None:
    dt_value = parse_iso_datetime(value)
    if dt_value is None:
        return None
    base = now or now_utc_naive()
    try:
        return max(0.0, (base - dt_value).total_seconds())
    except Exception:
        return None


def status_summary(status: str, label: str, detail: str = "", ok: bool = False) -> dict[str, Any]:
    return {
        "status": status,
        "label": label,
        "detail": detail,
        "ok": bool(ok),
    }


def diagnose_camera_worker(
    metrics: dict[str, Any] | None,
    health_entry: dict[str, Any] | None,
    camera_status: str | None = None,
) -> dict[str, str]:
    metrics = metrics or {}
    health_entry = health_entry or {}
    status = str(health_entry.get("health_status") or metrics.get("health_status") or camera_status or "idle").lower()
    restart_reason = str(health_entry.get("last_restart_reason") or metrics.get("last_restart_reason") or "").lower()
    reconnect_count = int(metrics.get("reconnect_count", 0) or 0)
    dropped_frames = int(metrics.get("dropped_frames_count", 0) or 0)
    queue_dropped = int(metrics.get("capture_queue_dropped_frames", 0) or 0)
    visual_dropped = int(metrics.get("visual_jobs_dropped", 0) or 0)
    stall_checks = int(health_entry.get("consecutive_stall_checks") or metrics.get("consecutive_stall_checks") or 0)
    read_ms = safe_float(metrics.get("read_ms"), 0.0) or 0.0
    infer_ms = safe_float(metrics.get("infer_ms"), 0.0) or 0.0
    loop_ms = safe_float(metrics.get("loop_ms"), 0.0) or 0.0
    cpu_percent = safe_float(metrics.get("process_cpu_percent"), 0.0) or 0.0
    raw_fps = safe_float(metrics.get("raw_fps"), 0.0) or 0.0
    processed_fps = safe_float(metrics.get("processed_fps"), 0.0) or 0.0
    metrics_age_seconds = age_seconds(metrics.get("updated_at"))
    stale_metrics_after = max(10.0, float(settings.camera_health_degraded_after_seconds) * 2.0)
    has_current_pressure = bool(metrics.get("capture_inference_pressure", False))

    if not metrics:
        return {
            "stability_class": "idle",
            "stability_label": "sem metricas",
            "diagnosis_label": "sem worker",
            "diagnosis_reason": "Nenhuma metrica foi publicada ainda para esta camera.",
        }

    worker_failure_tokens = ("fatal", "exception", "exitcode", "worker_process_exit", "worker_missing")
    connection_tokens = ("connect", "read", "stream", "stalled", "timeout", "reconnect", "gateway")

    if any(token in restart_reason for token in worker_failure_tokens):
        return {
            "stability_class": "offline",
            "stability_label": "worker caiu",
            "diagnosis_label": "processo worker",
            "diagnosis_reason": f"O worker reiniciou ou saiu com motivo: {restart_reason or 'desconhecido'}.",
        }

    if metrics_age_seconds is not None and metrics_age_seconds > stale_metrics_after:
        return {
            "stability_class": "degraded",
            "stability_label": "metricas paradas",
            "diagnosis_label": "worker sem pulso",
            "diagnosis_reason": f"A ultima metrica do worker tem {metrics_age_seconds:.1f}s; o processo pode estar travado ou sem publicar.",
        }

    if status in {"offline", "reconnecting", "degraded"} and (
        reconnect_count > 0
        or dropped_frames > 0
        or stall_checks > 0
        or any(token in restart_reason for token in connection_tokens)
    ):
        return {
            "stability_class": "reconnecting" if status == "reconnecting" else "degraded",
            "stability_label": "conexao instavel",
            "diagnosis_label": "camera/rede",
            "diagnosis_reason": (
                f"Ha sinais de perda de stream: reconnects={reconnect_count}, "
                f"dropped={dropped_frames}, stall={stall_checks}, read={read_ms:.1f}ms."
            ),
        }

    if has_current_pressure or queue_dropped >= 50 or visual_dropped >= 10 or infer_ms >= 250 or loop_ms >= 1200 or cpu_percent >= 85:
        return {
            "stability_class": "degraded",
            "stability_label": "sob carga",
            "diagnosis_label": "processamento",
            "diagnosis_reason": (
                f"Pipeline pesado: infer={infer_ms:.1f}ms, loop={loop_ms:.1f}ms, "
                f"cpu={cpu_percent:.1f}%, filas_drop={queue_dropped + visual_dropped}, "
                f"pressao={'sim' if has_current_pressure else 'nao'}."
            ),
        }

    if raw_fps > 0 and processed_fps <= 0:
        return {
            "stability_class": "degraded",
            "stability_label": "sem processamento",
            "diagnosis_label": "pipeline",
            "diagnosis_reason": "A captura recebe frames, mas o processamento ainda nao publicou FPS.",
        }

    if status in {"running", "running_motion_test", "warming_up"}:
        return {
            "stability_class": "running",
            "stability_label": "estavel",
            "diagnosis_label": "normal",
            "diagnosis_reason": "Worker ativo, metricas recentes e sem sinais fortes de queda ou gargalo.",
        }

    return {
        "stability_class": status or "idle",
        "stability_label": status or "indefinido",
        "diagnosis_label": "observacao",
        "diagnosis_reason": f"Estado atual: {status or 'indefinido'}.",
    }


def build_camera_operational_health(
    camera: Camera,
    metrics: dict[str, Any] | None,
    health_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics = metrics or {}
    health_entry = health_entry or {}
    now = now_utc_naive()

    raw_fps = safe_float(metrics.get("raw_fps"), 0.0) or 0.0
    processed_fps = safe_float(metrics.get("processed_fps"), 0.0) or 0.0
    fps = safe_float(metrics.get("fps"), 0.0) or 0.0
    gateway_state = str(health_entry.get("gateway_state") or "").strip().lower()
    health_status = str(health_entry.get("health_status") or metrics.get("health_status") or camera.status or "idle").strip().lower()
    worker_mode = str(metrics.get("worker_mode") or health_entry.get("worker_mode") or "stopped").strip().lower()
    metrics_age = age_seconds(metrics.get("updated_at") or health_entry.get("last_metrics_at"), now)
    raw_frame_age = age_seconds(health_entry.get("last_frame_at") or metrics.get("last_frame_at"), now)
    gateway_frame_age = safe_float(health_entry.get("gateway_last_frame_age_seconds"))
    processed_frame_age = age_seconds(health_entry.get("last_processed_frame_at") or metrics.get("last_processed_frame_at"), now)
    inference_age = age_seconds(health_entry.get("last_successful_inference_at") or metrics.get("last_successful_inference_at"), now)

    fresh_frame_age = None
    for frame_age in (raw_frame_age, gateway_frame_age):
        if frame_age is not None and (fresh_frame_age is None or frame_age < fresh_frame_age):
            fresh_frame_age = frame_age

    image_ok_after = max(8.0, float(settings.camera_health_degraded_after_seconds))
    image_stale_after = max(image_ok_after * 2.0, float(settings.camera_health_offline_after_seconds))
    metrics_stale_after = max(12.0, image_ok_after * 2.0)
    inference_ok_after = max(20.0, image_ok_after * 3.0)
    metrics_fresh = metrics_age is not None and metrics_age <= metrics_stale_after

    has_gateway_frame = gateway_state in {"running", "live", "snapshot", "warming_up"} and gateway_frame_age is not None
    has_python_frame = (metrics_fresh and (raw_fps > 0 or fps > 0)) or raw_frame_age is not None
    metrics_confirm_image_ok = (
        metrics_fresh
        and raw_fps > 0
        and health_status not in {"degraded", "reconnecting", "offline", "stopped"}
    )

    if metrics_confirm_image_ok and (fresh_frame_age is None or fresh_frame_age > image_ok_after):
        image = status_summary("ok", "Imagem OK", f"metricas recentes, {raw_fps:.2f} FPS bruto", True)
    elif fresh_frame_age is not None and fresh_frame_age <= image_ok_after:
        image = status_summary("ok", "Imagem OK", f"ultimo frame ha {fresh_frame_age:.1f}s", True)
    elif (metrics_fresh and raw_fps > 0) or (has_gateway_frame and gateway_frame_age is not None and gateway_frame_age <= image_stale_after):
        age_label = fresh_frame_age if fresh_frame_age is not None else gateway_frame_age
        image = status_summary("stale", "Imagem atrasada", f"ultimo frame ha {age_label:.1f}s" if age_label is not None else "stream lento", False)
    elif health_status in {"starting", "warming_up", "reconnecting"}:
        image = status_summary("warming", "Aguardando imagem", health_status, False)
    elif not getattr(camera, "rtsp_url", None):
        image = status_summary("offline", "Sem RTSP", "camera sem URL de stream", False)
    elif has_python_frame or has_gateway_frame:
        image = status_summary("stale", "Imagem sem pulso", "stream sem frame recente", False)
    else:
        image = status_summary("offline", "Sem imagem", "nenhum frame recente", False)

    worker_alive = health_status in {"running", "running_motion_test", "warming_up", "starting", "degraded", "reconnecting"}
    processed_fresh = processed_frame_age is not None and processed_frame_age <= image_stale_after
    inference_fresh = inference_age is not None and inference_age <= inference_ok_after
    motion_mode = worker_mode == "motion_test" or health_status == "running_motion_test"

    if inference_fresh:
        analysis = status_summary("ok", "IA OK", f"inferencia ha {inference_age:.1f}s", True)
    elif motion_mode and worker_alive and metrics_fresh and (processed_fps > 0 or processed_fresh or image["ok"]):
        detail = "Python ativo; IA roda quando ha movimento"
        if inference_age is not None:
            detail = f"ultima inferencia ha {inference_age:.1f}s; IA sob demanda"
        analysis = status_summary("standby", "IA em espera", detail, True)
    elif worker_alive and metrics_fresh and (processed_fps > 0 or processed_fresh):
        analysis = status_summary("processing", "Python OK", "processando frames, sem inferencia recente", False)
    elif worker_alive and health_status in {"starting", "warming_up", "reconnecting"}:
        analysis = status_summary("warming", "IA iniciando", health_status, False)
    elif metrics and not metrics_fresh:
        detail = f"metricas paradas ha {metrics_age:.1f}s" if metrics_age is not None else "metricas antigas"
        analysis = status_summary("stale", "Python sem pulso", detail, False)
    elif image["ok"]:
        analysis = status_summary("offline", "IA parada", "ha imagem, mas sem worker/metricas", False)
    else:
        analysis = status_summary("offline", "Sem analise", "sem imagem e sem IA ativa", False)

    if image["status"] == "ok" and analysis["status"] in {"ok", "standby"}:
        overall = status_summary("ok", "Operando", "imagem e analise saudaveis", True)
    elif image["status"] == "ok" and analysis["status"] in {"processing", "warming"}:
        overall = status_summary("attention", "Atencao", "imagem OK, IA ainda nao confirmada", False)
    elif image["status"] in {"warming", "stale"} or analysis["status"] in {"warming", "stale", "processing"}:
        overall = status_summary("attention", "Atencao", "camera parcialmente funcional", False)
    else:
        overall = status_summary("down", "Falha", "sem imagem operacional ou sem analise", False)

    return {
        "overall": overall,
        "image": image,
        "analysis": analysis,
        "player": status_summary("unknown", "Player pendente", "aguardando diagnostico do monitor", False),
        "raw_fps": round(float(raw_fps if metrics_fresh else 0.0), 2),
        "processed_fps": round(float(processed_fps if metrics_fresh else 0.0), 2),
        "metrics_age_seconds": metrics_age,
        "last_frame_age_seconds": fresh_frame_age,
        "last_inference_age_seconds": inference_age,
        "worker_mode": worker_mode,
        "gateway_state": gateway_state or None,
    }


def build_camera_operational_state(
    camera: Camera,
    *,
    metrics: dict[str, Any] | None = None,
    health_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = metrics_store.get_metrics(camera.id) if metrics is None else metrics
    health_entry = health_entry or {}
    operational_health = build_camera_operational_health(camera, metrics, health_entry)
    worker_diagnosis = diagnose_camera_worker(metrics or {}, health_entry, str(camera.status or "idle"))
    health_status = str(health_entry.get("health_status") or (metrics or {}).get("health_status") or camera.status or "idle")
    return {
        "camera_id": camera.id,
        "status": health_status,
        "operator_status": (operational_health.get("overall") or {}).get("status", "unknown"),
        "operational_health": operational_health,
        "worker_diagnosis": worker_diagnosis,
        "raw": {
            "camera_status": str(camera.status or "idle"),
            "health": health_entry,
            "metrics": metrics or {},
        },
    }


def build_operational_snapshot(db: Session) -> dict[str, Any]:
    health_snapshot = camera_health_monitor.get_snapshot()
    health_by_camera_id = {item["camera_id"]: item for item in health_snapshot.get("cameras", [])}
    cameras = db.query(Camera).filter(Camera.is_deleted == False).order_by(Camera.id.asc()).all()
    states = [
        build_camera_operational_state(
            camera,
            metrics=metrics_store.get_metrics(camera.id) or {},
            health_entry=health_by_camera_id.get(camera.id, {}),
        )
        for camera in cameras
    ]
    return {
        "generated_at": now_utc_naive().isoformat(),
        "camera_count": len(states),
        "operational_ok": sum(1 for item in states if item["operator_status"] == "ok"),
        "image_ok": sum(1 for item in states if (item["operational_health"].get("image") or {}).get("status") == "ok"),
        "analysis_ok": sum(
            1
            for item in states
            if (item["operational_health"].get("analysis") or {}).get("status") in {"ok", "standby"}
        ),
        "states": states,
    }
