"""Regras transacionais para atualizar a origem de uma camera existente."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.camera.onvif_client import RTSPProfile, discover_rtsp
from app.db.models import Camera
from app.services.media_backbone_service import ensure_camera_media_path
from app.services.webrtc_gateway_client import invalidate_webrtc_camera_path_cache


class CameraSourceNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class PendingCameraSourceUpdate:
    camera_id: int
    name: str
    ip: str
    manufacturer: str
    model: str | None
    onvif_port: int
    username: str
    password: str
    profiles: list[RTSPProfile]


@dataclass(frozen=True, slots=True)
class CameraSourceUpdateResult:
    camera: Camera
    pending: PendingCameraSourceUpdate | None = None


def _camera_or_raise(db: Session, camera_id: int) -> Camera:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        raise CameraSourceNotFoundError("Câmera não encontrada")
    return camera


def _optional_port(value: str | int | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ValueError("Porta ONVIF inválida")
    return port


def _apply_source_values(
    camera: Camera,
    *,
    name: str,
    ip: str,
    manufacturer: str,
    model: str | None,
    onvif_port: int | None,
    username: str,
    password: str,
    rtsp_url: str | None,
) -> None:
    normalized_manufacturer = str(manufacturer or "").strip()
    if not normalized_manufacturer:
        raise ValueError("A marca da camera e obrigatoria")
    camera.name = name.strip()
    camera.ip = ip.strip()
    camera.manufacturer = normalized_manufacturer[:120]
    camera.model = str(model or "").strip()[:120] or None
    camera.username = username.strip()
    if password.strip():
        camera.password = password
    if onvif_port is not None:
        camera.onvif_port = onvif_port
    if rtsp_url is not None:
        camera.rtsp_url = rtsp_url.strip()


def update_camera_source(
    db: Session,
    *,
    camera_id: int,
    name: str,
    ip: str,
    manufacturer: str,
    model: str | None,
    onvif_port: str | int | None,
    username: str,
    password: str,
    rtsp_url: str | None,
    rediscover_rtsp: bool,
) -> CameraSourceUpdateResult:
    camera = _camera_or_raise(db, camera_id)
    normalized_port = _optional_port(onvif_port)
    manual_rtsp_url = str(rtsp_url or "").strip() or None
    effective_password = password if password.strip() else camera.password

    try:
        if manual_rtsp_url is not None:
            _apply_source_values(
                camera,
                name=name,
                ip=ip,
                manufacturer=manufacturer,
                model=model,
                onvif_port=normalized_port,
                username=username,
                password=password,
                rtsp_url=manual_rtsp_url,
            )
        elif rediscover_rtsp:
            discovery = discover_rtsp(
                ip.strip(),
                normalized_port,
                username.strip(),
                effective_password,
            )
            if len(discovery.profiles) > 1:
                db.rollback()
                return CameraSourceUpdateResult(
                    camera=camera,
                    pending=PendingCameraSourceUpdate(
                        camera_id=camera.id,
                        name=name.strip(),
                        ip=ip.strip(),
                        manufacturer=str(manufacturer or "").strip(),
                        model=str(model or "").strip() or None,
                        onvif_port=discovery.onvif_port,
                        username=username.strip(),
                        password=effective_password,
                        profiles=list(discovery.profiles),
                    ),
                )
            _apply_source_values(
                camera,
                name=name,
                ip=ip,
                manufacturer=manufacturer,
                model=model,
                onvif_port=discovery.onvif_port,
                username=username,
                password=password,
                rtsp_url=discovery.rtsp_url,
            )
        else:
            _apply_source_values(
                camera,
                name=name,
                ip=ip,
                manufacturer=manufacturer,
                model=model,
                onvif_port=normalized_port,
                username=username,
                password=password,
                rtsp_url=None,
            )
        db.commit()
        db.refresh(camera)
        from app.services.device_session_service import invalidate_session
        from app.services.monitor_ptz_service import invalidate_camera_ptz_session

        invalidate_session(camera.id)
        invalidate_camera_ptz_session(camera)
        invalidate_webrtc_camera_path_cache(camera.id)
        ensure_camera_media_path(camera.id, camera.rtsp_url)
        return CameraSourceUpdateResult(camera=camera)
    except Exception:
        db.rollback()
        raise


def confirm_camera_source_update(
    db: Session,
    *,
    pending: PendingCameraSourceUpdate,
    rtsp_url: str,
) -> Camera:
    camera = _camera_or_raise(db, pending.camera_id)
    selected_url = str(rtsp_url or "").strip()
    if not selected_url:
        raise ValueError("Selecione um canal para atualizar a camera.")
    try:
        _apply_source_values(
            camera,
            name=pending.name,
            ip=pending.ip,
            manufacturer=pending.manufacturer,
            model=pending.model,
            onvif_port=pending.onvif_port,
            username=pending.username,
            password=pending.password,
            rtsp_url=selected_url,
        )
        db.commit()
        db.refresh(camera)
        from app.services.device_session_service import invalidate_session
        from app.services.monitor_ptz_service import invalidate_camera_ptz_session

        invalidate_session(camera.id)
        invalidate_camera_ptz_session(camera)
        invalidate_webrtc_camera_path_cache(camera.id)
        ensure_camera_media_path(camera.id, camera.rtsp_url)
        return camera
    except Exception:
        db.rollback()
        raise


def update_camera_rtsp_source(db: Session, *, camera_id: int, rtsp_url: str) -> Camera:
    camera = _camera_or_raise(db, camera_id)
    selected_url = str(rtsp_url or "").strip()
    if not selected_url:
        raise ValueError("RTSP inválido")
    try:
        camera.rtsp_url = selected_url
        db.commit()
        db.refresh(camera)
        invalidate_webrtc_camera_path_cache(camera.id)
        ensure_camera_media_path(camera.id, camera.rtsp_url)
        return camera
    except Exception:
        db.rollback()
        raise
