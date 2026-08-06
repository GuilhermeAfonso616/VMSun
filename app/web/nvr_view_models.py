"""Adaptadores de dados do dominio NVR para o template HTML."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.url_safety import mask_url_credentials
from app.services.nvr_channel_service import find_existing_nvr_channel, normalize_source_stream_kind
from app.services.nvr_health_service import collect_nvr_channel_health


def build_nvr_brand_options(selected_brand: str | None = None) -> list[dict[str, Any]]:
    selected = str(selected_brand or "generic").strip().lower()
    return [
        {"value": "generic", "label": "Generico", "selected": selected == "generic"},
        {"value": "hikvision", "label": "Hikvision", "selected": selected == "hikvision"},
        {"value": "dahua", "label": "Dahua", "selected": selected == "dahua"},
        {"value": "intelbras", "label": "Intelbras", "selected": selected == "intelbras"},
    ]


def build_nvr_profile_rows(db: Session, profiles, *, host: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, profile in enumerate(profiles):
        stream_kind = normalize_source_stream_kind(getattr(profile, "stream_kind", None))
        rtsp_url = str(getattr(profile, "rtsp_url", "") or "").strip()
        existing = find_existing_nvr_channel(
            db,
            host=host,
            channel=int(getattr(profile, "channel", 0) or 0),
            stream_kind=stream_kind,
            rtsp_url=rtsp_url,
        )
        metadata = getattr(profile, "metadata", None)
        rows.append(
            {
                "index": index,
                "channel": int(getattr(profile, "channel", 0) or 0),
                "stream_kind": stream_kind,
                "name": str(getattr(profile, "name", "") or f"Canal {index + 1}"),
                "rtsp_url": rtsp_url,
                "masked_rtsp_url": str(
                    getattr(profile, "masked_rtsp_url", "") or mask_url_credentials(rtsp_url)
                ),
                "ok": bool(getattr(profile, "ok", False)),
                "error": str(getattr(profile, "error", "") or ""),
                "source_brand": str(getattr(profile, "source_brand", "") or "generic"),
                "provider_type": str(getattr(profile, "provider_type", "") or "generic_nvr"),
                "resolution_label": (
                    f"{getattr(profile, 'width', None)}x{getattr(profile, 'height', None)}"
                    if getattr(profile, "width", None) and getattr(profile, "height", None)
                    else ""
                ),
                "fps": metadata.get("fps") if isinstance(metadata, dict) else None,
                "existing_id": getattr(existing, "id", None) if existing else None,
                "existing_name": getattr(existing, "name", None) if existing else None,
                "selected": bool(getattr(profile, "ok", False)) and existing is None,
            }
        )
    return rows


def build_nvr_channel_health(db: Session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in collect_nvr_channel_health(db):
        camera = entry.camera
        runtime_health = entry.runtime_health
        rows.append(
            {
                "id": camera.id,
                "name": camera.name,
                "host": camera.ip,
                "source_brand": camera.source_brand or "generic",
                "source_provider": camera.source_provider or "generic_nvr",
                "source_channel": camera.source_channel,
                "source_stream_kind": camera.source_stream_kind or "main",
                "health_status": entry.health_status,
                "worker_mode": entry.worker_mode,
                "last_frame_at": runtime_health.get("last_frame_at"),
                "last_metrics_at": runtime_health.get("last_metrics_at"),
                "masked_rtsp_url": mask_url_credentials(camera.rtsp_url or ""),
            }
        )
    return rows
