"""Rotas HTTP de descoberta, cadastro e saude de canais NVR."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.schemas.camera_schemas import NvrCreateChannelsRequest, NvrDiscoverRequest
from app.core.logging import get_logger
from app.services.nvr_channel_service import (
    NvrChannelError,
    NvrChannelSpec,
    create_nvr_channel_sources,
)
from app.services.nvr_discovery_service import discover_nvr_channels
from app.services.nvr_health_service import collect_nvr_channel_health


router = APIRouter(prefix="/video-sources/nvr", tags=["nvr"])
logger = get_logger("app.api.nvr")


@router.post("/discover")
def discover_nvr(payload: NvrDiscoverRequest):
    try:
        result = discover_nvr_channels(
            host=payload.host,
            username=payload.username,
            password=payload.password,
            rtsp_port=payload.rtsp_port,
            brand=payload.brand,
            channel_count=payload.channel_count,
            provider_type=payload.provider_type,
            stream_kinds=tuple(payload.stream_kinds or ["main", "sub"]),
            probe=payload.probe,
        )
        return result.to_dict()
    except Exception as exc:
        logger.warning("Falha na descoberta NVR host=%s brand=%s: %s", payload.host, payload.brand, exc)
        raise HTTPException(status_code=400, detail=str(exc) or "Falha ao descobrir canais do NVR") from exc


@router.post("/channels")
def create_nvr_channels(payload: NvrCreateChannelsRequest, db: Session = Depends(get_db)):
    try:
        result = create_nvr_channel_sources(
            db,
            host=payload.host,
            username=payload.username,
            password=payload.password,
            onvif_port=payload.onvif_port,
            base_name=payload.base_name,
            brand=payload.brand,
            provider_type=payload.provider_type,
            profiles=[
                NvrChannelSpec(
                    channel=selected.channel,
                    stream_kind=selected.stream_kind,
                    rtsp_url=selected.rtsp_url,
                    name=selected.name,
                    provider_type=selected.provider_type,
                    source_brand=selected.source_brand,
                )
                for selected in payload.profiles
            ],
            camera_family=payload.camera_family,
            scene_category=payload.scene_category,
            target_focus=payload.target_focus,
            nuisance_profile=payload.nuisance_profile.model_dump(),
            analytics_profile=payload.analytics_profile,
        )
    except NvrChannelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/health")
def nvr_channel_health(db: Session = Depends(get_db)):
    rows = []
    for entry in collect_nvr_channel_health(db):
        camera = entry.camera
        runtime_health = entry.runtime_health
        rows.append(
            {
                "camera_id": camera.id,
                "name": camera.name,
                "host": camera.ip,
                "source_type": camera.source_type,
                "source_channel": camera.source_channel,
                "source_stream_kind": camera.source_stream_kind or "main",
                "source_brand": camera.source_brand,
                "source_provider": camera.source_provider,
                "health_status": entry.health_status,
                "worker_registered": entry.worker_registered,
                "last_frame_at": runtime_health.get("last_frame_at"),
                "last_metrics_at": runtime_health.get("last_metrics_at"),
                "gateway_state": runtime_health.get("gateway_state"),
            }
        )
    return {"count": len(rows), "channels": rows}
