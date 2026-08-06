from __future__ import annotations

import base64
import os
import secrets
import threading
import time

import cv2
import numpy as np
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timezone import now_brazil_naive
from app.db.base import SessionLocal
from app.db.models import Camera
from app.services.camera_gateway_client import (
    fetch_gateway_cameras,
    fetch_gateway_health,
    stop_camera_source,
)
from app.services.camera_health_monitor import camera_health_monitor
from app.services.camera_operational_state import build_operational_snapshot
from app.services.camera_registry import registry
from app.services.frame_store import frame_store
from app.services.gpu_snapshot import read_gpu_snapshot
from app.services.analytic_runtime_guard import runtime_tuning_snapshot, update_runtime_tuning
from app.services.operational_history_store import operational_history_store
from app.services.resource_history_store import resource_history_store
from app.services.preview_stream import preview_stream_manager
from app.runtime.inference import mark_runtime_degraded
from app.runtime.inference_pool import get_inference_pool_group, release_inference_camera
from app.runtime.readiness import snapshot as readiness_snapshot
from app.services.webrtc_gateway_client import camera_webrtc_path_name, get_webrtc_path_diagnostics, webrtc_gateway_is_enabled
from app.services.media_backbone_service import camera_gateway_source_mode, ensure_camera_media_path, media_backbone_selected_for_camera
from app.services.worker_lifecycle import (
    WorkerLifecycleError,
    WorkerStartBlocked,
    worker_lifecycle_manager,
)
from app.services.worker_ownership_store import worker_ownership_store


router = APIRouter(prefix="/internal")
logger = get_logger("app.internal")
_supervisor_canary_lock = threading.Lock()


def _require_supervisor_token(
    x_analitico_supervisor_token: str | None = Header(default=None),
) -> None:
    configured = str(settings.supervisor_api_token or "").strip()
    if not configured:
        return
    supplied = str(x_analitico_supervisor_token or "")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=401, detail="Supervisor token invalido")


def _start_camera_runtime(camera: Camera, *, use_motion_test: bool = True, restart_existing: bool = True) -> dict:
    if not camera or bool(getattr(camera, "is_deleted", False)):
        raise HTTPException(status_code=404, detail="Camera nao encontrada")
    if not camera.rtsp_url:
        raise HTTPException(status_code=400, detail="Camera sem RTSP")

    preview_stream_manager.stop(camera.id)
    try:
        result = worker_lifecycle_manager.start(
            camera,
            restart_existing=restart_existing,
            reason="internal_api_start",
        )
    except WorkerStartBlocked as exc:
        raise HTTPException(status_code=429, detail=exc.detail) from exc
    except WorkerLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if camera_gateway_source_mode() == "mediamtx_strict" and media_backbone_selected_for_camera(camera.id):
        media_path = ensure_camera_media_path(camera.id, camera.rtsp_url)
        if not media_path.ok:
            raise HTTPException(status_code=503, detail={"error": media_path.error_code or "media_backbone_unavailable"})
    elif settings.webrtc_gateway_monitor_enabled and webrtc_gateway_is_enabled():
        ensure_camera_media_path(camera.id, camera.rtsp_url)
    camera.status = "running_motion_test"
    return {
        "message": "Worker ja estava rodando" if result.action == "already_running" else "Processamento iniciado",
        "camera_id": camera.id,
        "worker_mode": "motion_test",
        "lifecycle": result.as_dict(),
    }


def _stop_camera_runtime(camera_id: int, *, timeout_seconds: float = 5.0) -> None:
    try:
        worker_lifecycle_manager.stop(
            camera_id,
            timeout=timeout_seconds,
            reason="internal_api_stop",
        )
    except WorkerLifecycleError:
        logger.exception("Falha ao parar worker camera_id=%s", camera_id)
        raise

    preview_stream_manager.stop(camera_id)
    try:
        stop_camera_source(camera_id)
    except Exception:
        logger.exception("Falha ao parar fonte no gateway camera_id=%s", camera_id)


@router.get("/health/live")
def internal_health_live():
    return JSONResponse({"status": "ok", "pid": os.getpid(), "timestamp": now_brazil_naive().isoformat()})


@router.get("/health/ready")
def internal_health_ready():
    readiness = readiness_snapshot()
    status_code = 200 if readiness.get("ready") else 503
    return JSONResponse(
        {
            **readiness,
            "pid": os.getpid(),
            "timestamp": now_brazil_naive().isoformat(),
        },
        status_code=status_code,
    )


def _runtime_inference_pool_summary() -> dict:
    if not bool(settings.inference_pool_enabled):
        return {
            "enabled": False,
            "backend": str(settings.inference_pool_backend or "local"),
            "pool_count": int(settings.inference_pool_count),
            "max_cameras_per_pool": int(settings.inference_pool_max_cameras_per_pool),
            "pools": [],
        }
    if str(settings.inference_pool_backend or "local").strip().lower() != "central":
        return {
            "enabled": True,
            "backend": "local",
            "pool_count": int(settings.inference_pool_count),
            "max_cameras_per_pool": int(settings.inference_pool_max_cameras_per_pool),
            "pools": [],
        }
    try:
        return get_inference_pool_group().stats()
    except Exception as exc:
        logger.exception(
            "Falha ao coletar resumo das pools centrais",
            extra={"action": "inference_pool_summary", "status": "error", "reason": "summary_failed"},
        )
        return {
            "enabled": True,
            "backend": "central",
            "pool_count": int(settings.inference_pool_count),
            "max_cameras_per_pool": int(settings.inference_pool_max_cameras_per_pool),
            "error": str(exc),
            "pools": [],
        }


def _ia2_pool_summary() -> dict:
    """Health da pool central da IA2 (Etapa 3B).

    A IA2 degradada nao pode derrubar a saude do runtime nem da IA1: em caso de
    falha aqui, devolvemos o estado degradado da IA2 e seguimos.
    """
    if not bool(settings.ia2_pool_enabled):
        return {"enabled": False, "ready": False, "state": "disabled"}
    try:
        from app.runtime.ia2_pool import get_ia2_pool

        return get_ia2_pool().health()
    except Exception as exc:
        logger.exception(
            "Falha ao coletar health da pool IA2",
            extra={"action": "ia2_pool_summary", "status": "error", "reason": "summary_failed"},
        )
        return {"enabled": True, "ready": False, "state": "failed", "last_error": str(exc)}


@router.get("/health/cameras")
def internal_health_cameras():
    db = SessionLocal()
    try:
        health_snapshot = camera_health_monitor.get_snapshot()
        gpu = read_gpu_snapshot()
        return JSONResponse({
            **health_snapshot,
            "runtime_readiness": readiness_snapshot(),
            "gpu": gpu,
            "runtime_tuning": runtime_tuning_snapshot(active_workers=len(registry.list_workers()), gpu=gpu),
            "inference_pool_summary": _runtime_inference_pool_summary(),
            "ia2_pool": _ia2_pool_summary(),
            "operational": build_operational_snapshot(db),
        })
    finally:
        db.close()


@router.get("/media-backbone/cameras", dependencies=[Depends(_require_supervisor_token)])
def internal_media_backbone_cameras():
    db = SessionLocal()
    try:
        query = db.query(Camera)
        if getattr(Camera, "is_deleted", None) is not None:
            query = query.filter(Camera.is_deleted == False)  # noqa: E712
        cameras = []
        for camera in query.order_by(Camera.id.asc()).all():
            path = get_webrtc_path_diagnostics(camera_webrtc_path_name(camera.id))
            cameras.append({
                "camera_id": int(camera.id),
                "media_path": camera_webrtc_path_name(camera.id),
                "source_mode": camera_gateway_source_mode(),
                "media_backbone": "mediamtx",
                "media_path_configured": bool(path.get("ok")),
                "media_path_ready": bool((path.get("runtime") or {}).get("ready", False)),
                "gateway_direct_source": False,
                "error_code": path.get("reason"),
            })
        return {"ok": True, "cameras": cameras}
    finally:
        db.close()


def _supervisor_snapshot(db) -> dict:
    health = camera_health_monitor.get_snapshot()
    health_by_camera = {
        int(item.get("camera_id")): item
        for item in health.get("cameras", [])
        if item.get("camera_id") is not None
    }
    records = registry.snapshot()
    record_by_camera = {int(item["camera_id"]): item for item in records}
    cameras = []
    query = db.query(Camera)
    if getattr(Camera, "is_deleted", None) is not None:
        query = query.filter(Camera.is_deleted == False)  # noqa: E712
    for camera in query.order_by(Camera.id.asc()).all():
        record = record_by_camera.get(int(camera.id))
        ownership = worker_ownership_store.get(int(camera.id))
        ownership_matches = bool(
            record
            and ownership
            and str(record.get("generation") or "") == str(ownership.get("generation") or "")
            and (
                record.get("pid") is None
                or ownership.get("pid") is None
                or int(record["pid"]) == int(ownership["pid"])
            )
        )
        camera_health = health_by_camera.get(int(camera.id), {})
        cameras.append({
            "camera_id": int(camera.id),
            "camera_name": camera.name,
            "camera_status": camera.status,
            "auto_start_enabled": bool(getattr(camera, "auto_start_enabled", False)),
            "desired_running": worker_lifecycle_manager.desired_running(camera),
            "worker": record,
            "ownership": ownership,
            "ownership_matches": ownership_matches,
            "health_status": camera_health.get("health_status"),
            "latest_activity_age_seconds": camera_health.get("latest_activity_age_seconds"),
            "gateway_state": camera_health.get("gateway_state"),
            "metrics_age_seconds": camera_health.get("metrics_age_seconds"),
        })

    desired_camera_ids = {
        int(item["camera_id"])
        for item in cameras
        if item["desired_running"]
    }
    desired_running = len(desired_camera_ids)
    actual_running = sum(1 for item in records if item.get("alive"))
    unhealthy = sum(
        1
        for item in cameras
        if item["desired_running"]
        and (
            not item.get("worker")
            or not item["worker"].get("alive")
            or not item.get("ownership_matches")
            or item.get("health_status") not in {"running", "running_motion_test"}
        )
    )
    gateway_cameras = fetch_gateway_cameras()
    active_gateway_cameras = [
        item
        for item in gateway_cameras
        if bool(item.get("source_registered"))
        and str(item.get("state") or "").strip().lower() != "stopped_manual"
    ]
    gateway_orphan_ids = sorted(
        int(item.get("camera_id"))
        for item in active_gateway_cameras
        if item.get("camera_id") is not None
        and int(item.get("camera_id")) not in desired_camera_ids
    )
    gpu = read_gpu_snapshot()
    return {
        "generated_at": now_brazil_naive().isoformat(),
        "runtime": readiness_snapshot(),
        "summary": {
            "camera_count": len(cameras),
            "desired_running": desired_running,
            "actual_running": actual_running,
            "unhealthy_desired": unhealthy,
            "registry_records": len(records),
        },
        "workers": records,
        "cameras": cameras,
        "gpu": gpu,
        "runtime_tuning": runtime_tuning_snapshot(
            active_workers=len(registry.list_workers()),
            gpu=gpu,
        ),
        "inference_pool": _runtime_inference_pool_summary(),
        "ia2_pool": _ia2_pool_summary(),
        "gateway": {
            "health": fetch_gateway_health() or {},
            "camera_count": len(gateway_cameras),
            "active_camera_count": len(active_gateway_cameras),
            "orphan_camera_ids": gateway_orphan_ids,
            "cameras": gateway_cameras,
        },
    }


@router.get("/supervisor/snapshot", dependencies=[Depends(_require_supervisor_token)])
def internal_supervisor_snapshot():
    db = SessionLocal()
    try:
        return JSONResponse(_supervisor_snapshot(db))
    finally:
        db.close()


@router.post("/supervisor/cameras/{camera_id}/reconcile", dependencies=[Depends(_require_supervisor_token)])
def internal_supervisor_reconcile_camera(
    camera_id: int,
    recover: bool = Query(False),
    force_restart: bool = Query(False),
):
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera or bool(getattr(camera, "is_deleted", False)):
            raise HTTPException(status_code=404, detail="Camera nao encontrada")
        try:
            if recover and force_restart and worker_lifecycle_manager.desired_running(camera):
                result = worker_lifecycle_manager.start(
                    camera,
                    restart_existing=True,
                    reason="supervisor_forced_reconcile",
                )
            else:
                result = worker_lifecycle_manager.reconcile(camera, recover=bool(recover))
        except WorkerStartBlocked as exc:
            raise HTTPException(status_code=429, detail=exc.detail) from exc
        except WorkerLifecycleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if recover and result.action == "started":
            camera.status = "running_motion_test"
        db.commit()
        return JSONResponse({
            "mode": "recover" if recover else "audit",
            "result": result.as_dict(),
        })
    finally:
        db.close()


@router.post("/supervisor/gateway/reconcile", dependencies=[Depends(_require_supervisor_token)])
def internal_supervisor_reconcile_gateway(recover: bool = Query(False)):
    db = SessionLocal()
    try:
        query = db.query(Camera)
        if getattr(Camera, "is_deleted", None) is not None:
            query = query.filter(Camera.is_deleted == False)  # noqa: E712
        desired_ids = {
            int(camera.id)
            for camera in query.all()
            if worker_lifecycle_manager.desired_running(camera)
        }
    finally:
        db.close()

    active_gateway = [
        item
        for item in fetch_gateway_cameras()
        if bool(item.get("source_registered"))
        and str(item.get("state") or "").strip().lower() != "stopped_manual"
    ]
    orphan_ids = sorted(
        int(item.get("camera_id"))
        for item in active_gateway
        if item.get("camera_id") is not None
        and int(item.get("camera_id")) not in desired_ids
    )
    stopped: list[int] = []
    failed: list[int] = []
    if recover:
        for camera_id in orphan_ids:
            result = stop_camera_source(camera_id)
            if result and bool(result.get("ok", True)):
                stopped.append(camera_id)
            else:
                failed.append(camera_id)

    return JSONResponse({
        "mode": "recover" if recover else "audit",
        "desired_camera_ids": sorted(desired_ids),
        "orphan_camera_ids": orphan_ids,
        "stopped_camera_ids": stopped,
        "failed_camera_ids": failed,
    })


@router.post("/supervisor/canary", dependencies=[Depends(_require_supervisor_token)])
def internal_supervisor_canary():
    readiness = readiness_snapshot()
    if not readiness.get("ready"):
        raise HTTPException(status_code=503, detail={
            "error": "runtime_not_ready",
            "readiness": readiness,
        })
    if not _supervisor_canary_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Canario ja esta em execucao")

    started = time.perf_counter()
    try:
        probe = np.zeros((360, 640, 3), dtype=np.uint8)
        tracks, infer_ms, runtime = get_inference_pool_group().probe(probe)
        return JSONResponse({
            "ok": True,
            "tracks_count": len(tracks or []),
            "infer_ms": round(float(infer_ms), 2),
            "total_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "runtime": runtime,
            "readiness": readiness_snapshot(),
        })
    except BaseException as exc:
        mark_runtime_degraded("supervisor_canary_failed", exc)
        logger.exception(
            "Canario de inferencia falhou",
            extra={
                "action": "supervisor_canary",
                "status": "degraded",
                "reason": "inference_failed",
            },
        )
        return JSONResponse({
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "total_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }, status_code=503)
    finally:
        _supervisor_canary_lock.release()


@router.post("/inference/track")
def internal_inference_track(payload: dict):
    if not bool(settings.inference_pool_enabled):
        raise HTTPException(status_code=409, detail="Pool de inferencia desativada")

    readiness = readiness_snapshot()
    if not readiness.get("ready"):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "runtime de inferencia ainda nao esta pronto",
                "readiness": readiness,
            },
        )

    try:
        camera_id = int(payload.get("camera_id") or 0)
        raw_image = base64.b64decode(str(payload.get("image_b64") or ""), validate=True)
        frame = cv2.imdecode(np.frombuffer(raw_image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("frame invalido")
        offset_x = int(payload.get("offset_x") or 0)
        offset_y = int(payload.get("offset_y") or 0)
        scale_x = float(payload.get("scale_x") or 1.0)
        scale_y = float(payload.get("scale_y") or 1.0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Payload de inferencia invalido: {exc}") from exc

    try:
        pool_group = get_inference_pool_group()
        tracks, infer_ms, runtime = pool_group.infer(
            camera_id=camera_id,
            infer_frame=frame,
            offset_x=offset_x,
            offset_y=offset_y,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        return JSONResponse({
            "ok": True,
            "camera_id": camera_id,
            "tracks": tracks,
            "infer_ms": infer_ms,
            "runtime": runtime,
        })
    except TimeoutError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        mark_runtime_degraded("inference_failed", exc)
        logger.exception(
            "Central inference failed camera_id=%s",
            camera_id,
            extra={"camera_id": camera_id, "action": "central_inference", "status": "error", "reason": "inference_failed"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/inference/cameras/{camera_id}")
def internal_inference_release_camera(camera_id: int):
    if camera_id <= 0:
        raise HTTPException(status_code=400, detail="camera_id invalido")
    result = release_inference_camera(camera_id)
    return JSONResponse({"ok": True, **result})


@router.get("/health/operational-history")
def internal_operational_history(
    hours: int = Query(24, ge=1, le=168),
    bucket_minutes: int = Query(5, ge=1, le=60),
    camera_id: int | None = Query(None, ge=1),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    return JSONResponse(
        operational_history_store.query(
            hours=hours,
            bucket_minutes=bucket_minutes,
            camera_id=camera_id,
            start_iso=start,
            end_iso=end,
        )
    )


@router.get("/health/resource-history")
def internal_resource_history(
    hours: int = Query(24, ge=1, le=168),
    bucket_minutes: int = Query(5, ge=1, le=60),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    return JSONResponse(
        resource_history_store.query(
            hours=hours,
            bucket_minutes=bucket_minutes,
            start_iso=start,
            end_iso=end,
        )
    )


@router.post("/cameras/{camera_id}/start")
def internal_start_camera(
    camera_id: int,
    use_motion_test: bool = Query(True),
    restart_existing: bool = Query(True),
):
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        result = _start_camera_runtime(camera, use_motion_test=use_motion_test, restart_existing=restart_existing)
        db.commit()
        return result
    finally:
        db.close()


@router.post("/cameras/{camera_id}/stop")
def internal_stop_camera(camera_id: int, timeout_seconds: float = Query(5.0)):
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            raise HTTPException(status_code=404, detail="Camera nao encontrada")
        _stop_camera_runtime(camera_id, timeout_seconds=timeout_seconds)
        camera.status = "stopped_manual"
        db.commit()
        return {"message": "Processamento parado", "camera_id": camera_id}
    finally:
        db.close()


@router.post("/cameras/restart-active")
def internal_restart_active_workers():
    workers = registry.list_workers()
    if not workers:
        return {"restarted_workers": 0}

    db = SessionLocal()
    try:
        camera_ids = [camera_id for camera_id, _ in workers]
        cameras = {
            camera.id: camera
            for camera in db.query(Camera).filter(Camera.id.in_(camera_ids), Camera.is_deleted == False).all()
        }
        restarted_count = 0
        for camera_id, _worker in workers:
            camera = cameras.get(camera_id)
            if not camera or not camera.rtsp_url:
                continue
            try:
                worker_lifecycle_manager.start(
                    camera,
                    restart_existing=True,
                    reason="internal_restart_active",
                )
            except (WorkerLifecycleError, WorkerStartBlocked):
                logger.exception("Falha ao reiniciar worker camera_id=%s", camera_id)
                continue
            camera.status = "running_motion_test"
            restarted_count += 1
        db.commit()
        return {"restarted_workers": restarted_count}
    finally:
        db.close()


@router.post("/runtime-tuning")
def internal_runtime_tuning(payload: dict):
    snapshot = update_runtime_tuning(
        gpu_guard_enabled=payload.get("gpu_guard_enabled"),
        max_gpu_memory_mb=payload.get("max_gpu_memory_mb"),
        max_active_workers=payload.get("max_active_workers"),
        detector_fp16_enabled=payload.get("detector_fp16_enabled"),
        inference_pool_enabled=payload.get("inference_pool_enabled"),
        inference_pool_max_queue_size=payload.get("inference_pool_max_queue_size"),
        inference_pool_job_timeout_seconds=payload.get("inference_pool_job_timeout_seconds"),
        inference_pool_max_job_age_seconds=payload.get("inference_pool_max_job_age_seconds"),
        inference_pool_overflow_policy=payload.get("inference_pool_overflow_policy"),
        inference_pool_backend=payload.get("inference_pool_backend"),
        inference_pool_count=payload.get("inference_pool_count"),
        inference_pool_max_cameras_per_pool=payload.get("inference_pool_max_cameras_per_pool"),
        inference_pool_central_url=payload.get("inference_pool_central_url"),
        inference_pool_central_jpeg_quality=payload.get("inference_pool_central_jpeg_quality"),
        inference_pool_central_fallback_direct=payload.get("inference_pool_central_fallback_direct"),
    )
    return {"ok": True, "runtime_tuning": snapshot}


@router.post("/gateway-mode")
def internal_gateway_mode(mode: str = Query(...)):
    del mode
    os.environ["CAMERA_GATEWAY_WORKER_RTSP_FALLBACK_ENABLED"] = "false"
    settings.camera_gateway_worker_rtsp_fallback_enabled = False
    restarted = internal_restart_active_workers().get("restarted_workers", 0)
    return {
        "ok": True,
        "mode": "gateway_only",
        "rtsp_fallback_enabled": False,
        "restarted_workers": restarted,
    }


@router.get("/cameras/{camera_id}/probe")
def internal_probe_camera(camera_id: int):
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            raise HTTPException(status_code=404, detail="Camera nao encontrada")
        return {"camera_id": camera_id, "reachable": bool(camera_health_monitor._probe_camera_reachable(camera))}
    finally:
        db.close()


@router.get("/cameras/{camera_id}/frame/{kind}")
def internal_camera_frame(camera_id: int, kind: str):
    normalized_kind = "raw" if str(kind or "").strip().lower() == "raw" else "processed"
    from app.services.camera_media_service import (
        STREAM_STALE_MAX_AGE_SECONDS,
        frame_metadata_age_seconds,
    )

    metadata = (
        frame_store.get_raw_frame_metadata(camera_id)
        if normalized_kind == "raw"
        else frame_store.get_processed_frame_metadata(camera_id)
    )
    age_seconds = frame_metadata_age_seconds(metadata)
    if age_seconds is not None and age_seconds > STREAM_STALE_MAX_AGE_SECONDS:
        raise HTTPException(status_code=404, detail="Frame expirado")
    jpg_bytes = frame_store.get_raw_jpeg(camera_id) if normalized_kind == "raw" else frame_store.get_processed_jpeg(camera_id)
    if not jpg_bytes:
        raise HTTPException(status_code=404, detail="Frame nao disponivel")
    return Response(content=jpg_bytes, media_type="image/jpeg")
