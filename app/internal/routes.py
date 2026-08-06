"""Rotas internas de diagnóstico e controle do VMSun."""

from __future__ import annotations

import os
import secrets
import threading

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

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
from app.services.operational_history_store import operational_history_store
from app.services.resource_history_store import resource_history_store
from app.services.preview_stream import preview_stream_manager
from app.services.webrtc_gateway_client import camera_webrtc_path_name, get_webrtc_path_diagnostics, webrtc_gateway_is_enabled
from app.services.media_backbone_service import camera_gateway_source_mode, ensure_camera_media_path, media_backbone_selected_for_camera

router = APIRouter(prefix="/internal")
logger = get_logger("app.internal")


def _require_supervisor_token(
    x_analitico_supervisor_token: str | None = Header(default=None),
) -> None:
    configured = str(settings.supervisor_api_token or "").strip()
    if not configured:
        return
    supplied = str(x_analitico_supervisor_token or "")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=401, detail="Supervisor token invalido")


@router.get("/health/live")
def internal_health_live():
    return JSONResponse({"status": "ok", "app": settings.app_name, "pid": os.getpid(), "timestamp": now_brazil_naive().isoformat()})


@router.get("/health/ready")
def internal_health_ready():
    return JSONResponse(
        {
            "ready": True,
            "status": "ready",
            "app": settings.app_name,
            "pid": os.getpid(),
            "timestamp": now_brazil_naive().isoformat(),
        },
        status_code=200,
    )


@router.get("/health/cameras")
def internal_health_cameras():
    db = SessionLocal()
    try:
        health_snapshot = camera_health_monitor.get_snapshot()
        return JSONResponse({
            **health_snapshot,
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
