"""View-models compartilhados das fontes HikCentral e Hik-Connect."""

import re
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Camera
from app.services.runtime_client import get_runtime_health_snapshot
from app.web.camera_detail_presenter import enrich_camera_for_template


def mask_hik_source_url(value: str | None) -> str:
    return re.sub(
        r"([?&]verify=)[^&]*",
        r"\1***",
        str(value or ""),
        flags=re.IGNORECASE,
    )


def build_hik_channel_health(
    db: Session,
    provider: str,
) -> list[dict[str, Any]]:
    cameras = (
        db.query(Camera)
        .filter(Camera.is_deleted == False)
        .filter(Camera.source_provider == provider)
        .order_by(Camera.ip.asc(), Camera.id.asc())
        .all()
    )
    health_snapshot = get_runtime_health_snapshot()
    health_by_id = {
        item["camera_id"]: item for item in health_snapshot.get("cameras", [])
    }
    rows: list[dict[str, Any]] = []
    for camera in cameras:
        health_entry = health_by_id.get(camera.id, {})
        enrich_camera_for_template(camera, camera.id, health_entry=health_entry)
        rows.append(
            {
                "id": camera.id,
                "name": camera.name,
                "host": camera.ip,
                "source_brand": camera.source_brand or (
                    "hikcentral" if provider == "hikcentral" else "hikvision"
                ),
                "source_provider": camera.source_provider or provider,
                "source_channel": camera.source_channel,
                "source_stream_kind": camera.source_stream_kind or "main",
                "health_status": camera.health_status_display,
                "worker_mode": camera.worker_mode,
                "last_frame_at": camera.last_frame_at,
                "last_metrics_at": camera.last_metrics_at,
                "masked_rtsp_url": mask_hik_source_url(
                    camera.masked_rtsp_url or camera.rtsp_url
                ),
            }
        )
    return rows


def build_hikcentral_channel_health(db: Session) -> list[dict[str, Any]]:
    return build_hik_channel_health(db, "hikcentral")


def build_hikconnect_channel_health(db: Session) -> list[dict[str, Any]]:
    return build_hik_channel_health(db, "hikconnect")


def hikcentral_form_defaults() -> dict[str, Any]:
    return {
        "base_name": "HikCentral",
        "host": "",
        "username": "",
        "simulate": True,
    }


def hikconnect_form_defaults() -> dict[str, Any]:
    return {
        "base_name": "Hik-Connect",
        "serial_number": "",
        "verification_code": "",
        "channel_no": 1,
        "username": "admin",
        "simulate": True,
    }


def without_password(values: dict[str, Any]) -> dict[str, Any]:
    safe_values = dict(values)
    safe_values["password"] = ""
    return safe_values
