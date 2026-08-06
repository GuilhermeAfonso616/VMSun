"""Casos de uso para importar cameras encontradas na rede ONVIF."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import CAMERA_FAMILIES, SCENE_CATEGORIES, TARGET_FOCUSES
from app.db.models import Camera
from app.services.camera_discovery_service import discover_camera_streams
from app.services.camera_factory import build_camera_model
from app.services.onvif_network_discovery import OnvifNetworkDevice


@dataclass(frozen=True, slots=True)
class CameraNetworkProfile:
    camera_family: str
    scene_category: str
    target_focus: str


def normalize_camera_network_profile(
    camera_family: str,
    scene_category: str,
    target_focus: str,
) -> CameraNetworkProfile:
    return CameraNetworkProfile(
        camera_family=camera_family if camera_family in CAMERA_FAMILIES else "dome",
        scene_category=scene_category if scene_category in SCENE_CATEGORIES else "interno",
        target_focus=target_focus if target_focus in TARGET_FOCUSES else "pessoa",
    )


async def import_discovered_network_cameras(
    db: Session,
    *,
    devices: list[OnvifNetworkDevice],
    username: str,
    password: str,
    profile: CameraNetworkProfile,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for device in devices:
        existing = (
            db.query(Camera)
            .filter(Camera.is_deleted == False, Camera.ip == device.ip)
            .first()
        )
        if existing is not None:
            results.append(
                {
                    "ip": device.ip,
                    "ok": False,
                    "status": "ignored",
                    "message": f"Ja cadastrada como {existing.name}.",
                }
            )
            continue

        try:
            stream = await asyncio.to_thread(
                discover_camera_streams,
                ip=device.ip,
                onvif_port=device.port,
                username=username,
                password=password,
                allow_rtsp_fallback=False,
            )
            camera_name = device.suggested_name[:120]
            camera = build_camera_model(
                name=camera_name,
                ip=device.ip,
                manufacturer=device.manufacturer or "Nao informada",
                model=device.model or None,
                onvif_port=stream.onvif_port,
                username=username,
                password=password,
                rtsp_url=stream.rtsp_url,
                camera_family=profile.camera_family,
                scene_category=profile.scene_category,
                target_focus=profile.target_focus,
                coordinate_space_override="display",
            )
            db.add(camera)
            results.append(
                {
                    "ip": device.ip,
                    "ok": True,
                    "status": "created",
                    "message": f"{camera_name} adicionada com o primeiro profile ONVIF.",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ip": device.ip,
                    "ok": False,
                    "status": "failed",
                    "message": str(exc) or "Falha ao consultar a camera via ONVIF.",
                }
            )

    if any(bool(item["ok"]) for item in results):
        db.commit()
    else:
        db.rollback()
    return results
