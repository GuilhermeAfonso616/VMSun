"""Rotas HTTP de cadastro e consulta basica de cameras."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.api.schemas.camera_schemas import CameraCreate
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.camera_source_service import create_camera_source, list_camera_payloads


router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.post("")
def create_camera(
    payload: CameraCreate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    camera = create_camera_source(
        db,
        name=payload.name,
        ip=payload.ip,
        manufacturer=payload.manufacturer,
        model=payload.model,
        onvif_port=payload.onvif_port,
        username=payload.username,
        password=payload.password,
        rtsp_url=payload.rtsp_url,
        camera_family=payload.camera_family,
        scene_category=payload.scene_category,
        target_focus=payload.target_focus,
        nuisance_profile=payload.nuisance_profile.model_dump(),
        analytics_profile=payload.analytics_profile,
    )
    log_audit(
        db,
        "camera_create",
        current_user,
        f"Criou câmera via API: {camera.name} (IP: {camera.ip})",
        ip_address=request.client.host if request.client else None,
    )
    return {"id": camera.id, "name": camera.name, "status": camera.status}


@router.get("")
def list_cameras(db: Session = Depends(get_db)):
    return list_camera_payloads(db)
