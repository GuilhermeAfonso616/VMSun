"""Operacoes transacionais e em lote sobre cameras existentes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy.orm import Session

from app.db.models import (
    Camera,
    ConfigVersionHistory,
    Event,
    EventFeedback,
    LockdownDelivery,
    TuningSuggestion,
)
from app.core.timezone import utc_now_naive as _shared_utc_now_naive
from app.services.camera_runtime_service import start_camera_worker, stop_camera_runtime
from app.services.camera_gateway_client import remove_camera_frame_buffer
from app.services.frame_store import frame_store
from app.services.metrics_store import metrics_store
from app.services.webrtc_gateway_client import unregister_webrtc_camera_path


CAMERA_BULK_ACTIONS = frozenset({"start", "stop", "delete"})
CAMERA_BULK_ACTION_LIMIT = 500
CameraBulkAction = Literal["start", "stop", "delete"]


@dataclass(frozen=True)
class CameraOperationError(Exception):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class CameraStartResult:
    camera: Camera | None
    started: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CameraBulkResult:
    processed_count: int
    camera_ids: list[int]


def now_utc_naive() -> datetime:
    return _shared_utc_now_naive()


def normalize_camera_bulk_ids(camera_ids: list[int | str]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_id in camera_ids:
        try:
            camera_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if camera_id <= 0 or camera_id in seen:
            continue
        seen.add(camera_id)
        normalized.append(camera_id)
        if len(normalized) >= CAMERA_BULK_ACTION_LIMIT:
            break
    return normalized


def stop_camera_runtime_for_soft_delete(camera: Camera) -> None:
    stop_camera_runtime(int(camera.id))
    try:
        unregister_webrtc_camera_path(int(camera.id))
    except Exception:
        pass
    try:
        frame_store.remove_frame(int(camera.id))
    except Exception:
        pass
    try:
        metrics_store.remove_metrics(int(camera.id))
    except Exception:
        pass
    try:
        remove_camera_frame_buffer(int(camera.id))
    except Exception:
        pass


def soft_delete_camera_record(
    db: Session,
    camera: Camera,
    *,
    deleted_at: datetime | None = None,
) -> None:
    del db
    stop_camera_runtime_for_soft_delete(camera)
    camera.is_deleted = True
    camera.deleted_at = deleted_at or now_utc_naive()
    camera.status = "disabled"


def soft_delete_camera(db: Session, camera_id: int) -> Camera:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        raise CameraOperationError(404, "Câmera não encontrada")
    try:
        soft_delete_camera_record(db, camera)
        db.commit()
        db.refresh(camera)
        return camera
    except Exception:
        db.rollback()
        raise


def soft_delete_all_cameras(db: Session) -> CameraBulkResult:
    cameras = (
        db.query(Camera)
        .filter(Camera.is_deleted.is_(False))
        .order_by(Camera.id.asc())
        .all()
    )
    deleted_at = now_utc_naive()
    try:
        for camera in cameras:
            soft_delete_camera_record(db, camera, deleted_at=deleted_at)
        db.commit()
        return CameraBulkResult(
            processed_count=len(cameras),
            camera_ids=[int(camera.id) for camera in cameras],
        )
    except Exception:
        db.rollback()
        raise


def start_all_cameras(db: Session) -> CameraBulkResult:
    cameras = (
        db.query(Camera)
        .filter(Camera.is_deleted.is_(False))
        .order_by(Camera.id.asc())
        .all()
    )
    try:
        for camera in cameras:
            start_camera_worker(camera, use_motion_test=True, restart_existing=True)
        db.commit()
        return CameraBulkResult(
            processed_count=len(cameras),
            camera_ids=[int(camera.id) for camera in cameras],
        )
    except Exception:
        db.rollback()
        raise


def stop_all_cameras(db: Session) -> CameraBulkResult:
    cameras = (
        db.query(Camera)
        .filter(Camera.is_deleted.is_(False))
        .order_by(Camera.id.asc())
        .all()
    )
    try:
        for camera in cameras:
            stop_camera_runtime(int(camera.id))
            camera.status = "stopped_manual"
        db.commit()
        return CameraBulkResult(
            processed_count=len(cameras),
            camera_ids=[int(camera.id) for camera in cameras],
        )
    except Exception:
        db.rollback()
        raise


def apply_camera_bulk_action(
    db: Session,
    cameras: list[Camera],
    action: CameraBulkAction,
) -> int:
    processed = 0
    deleted_at = now_utc_naive() if action == "delete" else None
    for camera in cameras:
        if action == "start":
            if start_camera_worker(camera, use_motion_test=True, restart_existing=True):
                processed += 1
        elif action == "stop":
            stop_camera_runtime(int(camera.id))
            camera.status = "stopped_manual"
            processed += 1
        elif action == "delete":
            soft_delete_camera_record(db, camera, deleted_at=deleted_at)
            processed += 1
    return processed


def apply_selected_camera_action(
    db: Session,
    camera_ids: list[int],
    action: str,
) -> CameraBulkResult:
    if action not in CAMERA_BULK_ACTIONS:
        raise CameraOperationError(400, "Ação em lote inválida")
    cameras_by_id = {
        int(camera.id): camera
        for camera in db.query(Camera)
        .filter(Camera.is_deleted.is_(False), Camera.id.in_(camera_ids))
        .all()
    }
    cameras = [cameras_by_id[camera_id] for camera_id in camera_ids if camera_id in cameras_by_id]
    try:
        processed = apply_camera_bulk_action(db, cameras, cast(CameraBulkAction, action))
        db.commit()
        return CameraBulkResult(
            processed_count=processed,
            camera_ids=[int(camera.id) for camera in cameras],
        )
    except Exception:
        db.rollback()
        raise


def start_camera_action(
    db: Session,
    camera_id: int,
    *,
    require_rtsp: bool = False,
) -> CameraStartResult:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        return CameraStartResult(camera=None, started=False, reason="not_found")
    if bool(getattr(camera, "is_deleted", False)):
        return CameraStartResult(camera=camera, started=False, reason="deleted")
    if require_rtsp and not camera.rtsp_url:
        return CameraStartResult(camera=camera, started=False, reason="missing_rtsp")
    try:
        started = start_camera_worker(camera, use_motion_test=True, restart_existing=True)
        db.commit()
        return CameraStartResult(camera=camera, started=started)
    except Exception:
        db.rollback()
        raise


def stop_camera_action(db: Session, camera_id: int) -> str:
    stop_camera_runtime(camera_id)
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    camera_name = camera.name if camera else f"id={camera_id}"
    if camera is not None:
        camera.status = "stopped_manual"
        db.commit()
    return camera_name


def purge_camera(db: Session, camera_id: int) -> str:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        raise CameraOperationError(404, "Câmera não encontrada")
    try:
        stop_camera_runtime(camera_id)
        event_ids = [
            event_id
            for (event_id,) in db.query(Event.id).filter(Event.camera_id == camera_id).all()
        ]
        if event_ids:
            db.query(LockdownDelivery).filter(
                LockdownDelivery.event_id.in_(event_ids)
            ).delete(synchronize_session=False)
            db.query(EventFeedback).filter(
                EventFeedback.event_id.in_(event_ids)
            ).delete(synchronize_session=False)
        db.query(EventFeedback).filter(EventFeedback.camera_id == camera_id).delete(
            synchronize_session=False
        )
        db.query(TuningSuggestion).filter(TuningSuggestion.camera_id == camera_id).delete(
            synchronize_session=False
        )
        db.query(ConfigVersionHistory).filter(
            ConfigVersionHistory.camera_id == camera_id
        ).delete(synchronize_session=False)
        db.query(LockdownDelivery).filter(LockdownDelivery.camera_id == camera_id).delete(
            synchronize_session=False
        )
        db.query(Event).filter(Event.camera_id == camera_id).delete(synchronize_session=False)
        camera_name = camera.name
        db.delete(camera)
        db.commit()
        return camera_name
    except Exception:
        db.rollback()
        raise
