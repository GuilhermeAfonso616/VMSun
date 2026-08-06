"""Casos de uso de cadastro e consulta basica de cameras."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import profile_from_camera
from app.db.models import Camera
from app.db.query_helpers import get_active_cameras_query
from app.services.camera_discovery_service import discover_camera_streams
from app.services.camera_factory import build_camera_model


@dataclass(frozen=True, slots=True)
class CameraSourceSpec:
    name: str
    rtsp_url: str


def create_camera_sources(
    db: Session,
    *,
    ip: str,
    manufacturer: str,
    model: str | None,
    onvif_port: int | None,
    username: str,
    password: str,
    sources: list[CameraSourceSpec],
    camera_family: str,
    scene_category: str,
    target_focus: str,
    nuisance_profile: dict[str, Any],
    analytics_profile: Any | None,
    coordinate_space_override: str | None = None,
) -> list[Camera]:
    if not sources:
        raise ValueError("Nenhuma fonte de camera selecionada")
    normalized_manufacturer = str(manufacturer or "").strip()
    if not normalized_manufacturer:
        raise ValueError("A marca da camera e obrigatoria")

    cameras: list[Camera] = []
    for source in sources:
        rtsp_url = str(source.rtsp_url or "").strip()
        if not rtsp_url:
            raise ValueError(f"Fonte {source.name} sem URL RTSP")
        camera = build_camera_model(
            name=str(source.name or "Camera").strip() or "Camera",
            ip=ip,
            manufacturer=normalized_manufacturer[:120],
            model=str(model or "").strip()[:120] or None,
            onvif_port=onvif_port if onvif_port is not None else 80,
            username=username,
            password=password,
            rtsp_url=rtsp_url,
            camera_family=camera_family,
            scene_category=scene_category,
            target_focus=target_focus,
            nuisance_profile=nuisance_profile,
            analytics_profile=analytics_profile,
            coordinate_space_override=coordinate_space_override,
        )
        camera.source_type = "camera"
        db.add(camera)
        cameras.append(camera)

    db.commit()
    for camera in cameras:
        db.refresh(camera)
    return cameras


def create_camera_source(
    db: Session,
    *,
    name: str,
    ip: str,
    manufacturer: str,
    model: str | None,
    onvif_port: int | None,
    username: str,
    password: str,
    rtsp_url: str | None,
    camera_family: str,
    scene_category: str,
    target_focus: str,
    nuisance_profile: dict[str, Any],
    analytics_profile: Any | None,
) -> Camera:
    resolved_rtsp_url = rtsp_url.strip() if rtsp_url and rtsp_url.strip() else None
    resolved_onvif_port = onvif_port
    if not resolved_rtsp_url:
        discovery = discover_camera_streams(
            ip=ip,
            onvif_port=onvif_port,
            username=username,
            password=password,
        )
        resolved_rtsp_url = discovery.rtsp_url
        resolved_onvif_port = discovery.onvif_port

    cameras = create_camera_sources(
        db,
        ip=ip,
        manufacturer=manufacturer,
        model=model,
        onvif_port=resolved_onvif_port if resolved_onvif_port is not None else 80,
        username=username,
        password=password,
        sources=[CameraSourceSpec(name=name, rtsp_url=resolved_rtsp_url)],
        camera_family=camera_family,
        scene_category=scene_category,
        target_focus=target_focus,
        nuisance_profile=nuisance_profile,
        analytics_profile=analytics_profile,
    )
    return cameras[0]


def serialize_camera(camera: Camera) -> dict[str, Any]:
    return {
        "id": camera.id,
        "name": camera.name,
        "ip": camera.ip,
        "manufacturer": camera.manufacturer,
        "model": camera.model,
        "status": camera.status,
        "source_type": camera.source_type,
        "source_parent_id": camera.source_parent_id,
        "source_channel": camera.source_channel,
        "source_stream_kind": camera.source_stream_kind,
        "source_brand": camera.source_brand,
        "source_provider": camera.source_provider,
        "roi_name": camera.roi_name,
        "roi_polygon_json": camera.roi_polygon_json,
        "line_start_x": camera.line_start_x,
        "line_start_y": camera.line_start_y,
        "line_end_x": camera.line_end_x,
        "line_end_y": camera.line_end_y,
        "line_direction": camera.line_direction,
        "site_name": camera.site_name,
        "group_name": camera.group_name,
        "camera_priority": camera.camera_priority,
        "auto_start_enabled": bool(getattr(camera, "auto_start_enabled", False)),
        "alarm_sound_enabled": camera.alarm_sound_enabled,
        "alarm_popup_enabled": camera.alarm_popup_enabled,
        "learning_mode": camera.learning_mode,
        "auto_tuning_enabled": camera.auto_tuning_enabled,
        "critical_lock": camera.critical_lock,
        "max_daily_auto_changes": camera.max_daily_auto_changes,
        "min_reviewed_events_for_suggestion": camera.min_reviewed_events_for_suggestion,
        "min_reviewed_events_for_auto_tuning": camera.min_reviewed_events_for_auto_tuning,
        "rollback_window_hours": camera.rollback_window_hours,
        "analytics_profile_json": camera.analytics_profile_json,
        "analytics_profile_preview": profile_from_camera(camera).to_dict(),
    }


def list_camera_payloads(db: Session) -> list[dict[str, Any]]:
    return [serialize_camera(camera) for camera in get_active_cameras_query(db).all()]
