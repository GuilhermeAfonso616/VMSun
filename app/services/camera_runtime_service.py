"""Orquestracao testavel do lifecycle local e remoto de cameras."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Camera
from app.services.camera_gateway_client import stop_camera_source
from app.services.camera_registry import registry
from app.services.preview_stream import preview_stream_manager
from app.services.runtime_client import (
    RuntimeClientError,
    remote_runtime_enabled,
    start_runtime_camera,
    stop_runtime_camera,
)
from app.services.webrtc_gateway_client import (
    webrtc_gateway_is_enabled,
)
from app.services.media_backbone_service import (
    camera_gateway_source_mode,
    ensure_camera_media_path,
    media_backbone_selected_for_camera,
)
from app.services.worker_lifecycle import (
    WorkerLifecycleError,
    WorkerStartBlocked,
    worker_lifecycle_manager,
)


logger = get_logger("app.services.camera_runtime")


@dataclass(frozen=True)
class CameraRuntimeError(Exception):
    status_code: int
    detail: Any

    def __str__(self) -> str:
        return str(self.detail)


@dataclass(frozen=True, slots=True)
class CameraRuntimeOperation:
    payload: dict[str, Any]
    audit_details: str | None


def _register_monitor_path(camera: Camera) -> None:
    if settings.webrtc_gateway_monitor_enabled and webrtc_gateway_is_enabled():
        ensure_camera_media_path(camera.id, camera.rtsp_url)


def _require_backbone_before_start(camera: Camera) -> None:
    if camera_gateway_source_mode() != "mediamtx_strict" or not media_backbone_selected_for_camera(camera.id):
        return
    result = ensure_camera_media_path(camera.id, camera.rtsp_url)
    if not result.ok:
        raise CameraRuntimeError(503, {"error": result.error_code or "media_backbone_unavailable"})


def start_camera_worker(
    camera: Camera,
    *,
    use_motion_test: bool = True,
    restart_existing: bool = True,
) -> bool:
    """Adaptador tolerante usado pelas ações web individuais e em lote."""
    del use_motion_test
    if not camera or bool(getattr(camera, "is_deleted", False)) or not camera.rtsp_url:
        return False
    try:
        _require_backbone_before_start(camera)
    except CameraRuntimeError:
        return False

    if remote_runtime_enabled():
        try:
            start_runtime_camera(
                camera.id,
                use_motion_test=True,
                restart_existing=restart_existing,
            )
            camera.status = "running_motion_test"
            return True
        except RuntimeClientError:
            logger.exception(
                "Failed to start remote camera worker",
                extra={"action": "remote_camera_worker_start_failed", "camera_id": camera.id},
            )
            return False

    preview_stream_manager.stop(camera.id)
    try:
        worker_lifecycle_manager.start(
            camera,
            restart_existing=restart_existing,
            reason="web_start",
        )
    except (WorkerLifecycleError, WorkerStartBlocked):
        logger.exception(
            "Failed to start local camera worker",
            extra={"action": "camera_worker_start_failed", "camera_id": camera.id},
        )
        return False
    _register_monitor_path(camera)
    camera.status = "running_motion_test"
    return True


def stop_camera_runtime(camera_id: int, *, timeout: float = 5.0) -> None:
    """Interrompe runtime, preview e fonte do gateway sem falhar a ação web."""
    if remote_runtime_enabled():
        try:
            stop_runtime_camera(camera_id, timeout_seconds=timeout)
        except RuntimeClientError:
            logger.exception(
                "Failed to stop remote camera worker",
                extra={"action": "remote_camera_worker_stop_failed", "camera_id": camera_id},
            )
        return

    try:
        worker_lifecycle_manager.stop(camera_id, timeout=timeout, reason="web_stop")
    except WorkerLifecycleError:
        logger.exception(
            "Failed to stop camera worker",
            extra={"action": "camera_worker_stop_failed", "camera_id": camera_id},
        )
    preview_stream_manager.stop(camera_id)
    try:
        stop_camera_source(camera_id)
    except Exception:
        logger.exception(
            "Failed to stop camera gateway source",
            extra={"action": "camera_gateway_stop_failed", "camera_id": camera_id},
        )


def start_camera_processing(
    db: Session,
    camera_id: int,
    *,
    use_motion_test: bool = True,
) -> CameraRuntimeOperation:
    if remote_runtime_enabled():
        try:
            payload = start_runtime_camera(
                camera_id,
                use_motion_test=use_motion_test,
                restart_existing=False,
            )
        except RuntimeClientError as exc:
            raise CameraRuntimeError(502, f"Runtime indisponivel: {exc}") from exc
        return CameraRuntimeOperation(
            payload=payload,
            audit_details=f"Iniciou a câmera remota via API: id={camera_id}",
        )

    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        raise CameraRuntimeError(404, "Câmera não encontrada")
    if bool(getattr(camera, "is_deleted", False)):
        raise CameraRuntimeError(409, "Câmera desativada")

    worker = registry.get_worker(camera_id)
    if worker:
        _register_monitor_path(camera)
        if camera.status != "running_motion_test":
            camera.status = "running_motion_test"
            db.commit()
        return CameraRuntimeOperation(
            payload={
                "message": "Worker já está rodando",
                "worker_mode": getattr(worker, "mode_name", "normal"),
            },
            audit_details=None,
        )
    if not camera.rtsp_url:
        raise CameraRuntimeError(400, "Câmera sem RTSP")

    _require_backbone_before_start(camera)
    try:
        result = worker_lifecycle_manager.start(
            camera,
            restart_existing=False,
            reason="api_start",
        )
    except WorkerStartBlocked as exc:
        raise CameraRuntimeError(429, exc.detail) from exc
    except WorkerLifecycleError as exc:
        raise CameraRuntimeError(409, str(exc)) from exc

    _register_monitor_path(camera)
    camera.status = "running_motion_test"
    db.commit()
    return CameraRuntimeOperation(
        payload={
            "message": "Worker ja esta rodando" if result.action == "already_running" else "Processamento iniciado",
            "camera_id": camera_id,
            "worker_mode": "motion_test",
            "lifecycle": result.as_dict(),
        },
        audit_details=f"Iniciou a câmera via API: {camera.name} (id={camera.id})",
    )


def stop_camera_processing(db: Session, camera_id: int) -> CameraRuntimeOperation:
    if remote_runtime_enabled():
        try:
            payload = stop_runtime_camera(camera_id)
        except RuntimeClientError as exc:
            raise CameraRuntimeError(502, f"Runtime indisponivel: {exc}") from exc
        return CameraRuntimeOperation(
            payload=payload,
            audit_details=f"Parou a câmera remota via API: id={camera_id}",
        )

    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        raise CameraRuntimeError(404, "Camera nao encontrada")

    try:
        worker_lifecycle_manager.stop(camera_id, reason="api_stop")
    except WorkerLifecycleError as exc:
        raise CameraRuntimeError(409, str(exc)) from exc
    stop_camera_source(camera_id)
    camera.status = "stopped_manual"
    db.commit()
    return CameraRuntimeOperation(
        payload={"message": "Processamento parado", "camera_id": camera_id},
        audit_details=f"Parou a câmera via API: {camera.name} (id={camera.id})",
    )
