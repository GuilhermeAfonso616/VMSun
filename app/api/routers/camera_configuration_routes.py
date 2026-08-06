"""Rotas HTTP de configuracao analitica e operacional de cameras."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.api.schemas.camera_schemas import (
    CameraAnalyticsConfigUpdate,
    CameraAnalyticsProfileUpdate,
    CameraOpsConfigUpdate,
)
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.camera_configuration_service import (
    CameraConfigurationError,
    get_camera_profile,
    update_camera_profile,
    update_legacy_analytics_config,
    update_operational_config,
)


router = APIRouter(prefix="/cameras/{camera_id}", tags=["camera-configuration"])


def _translate_error(exc: CameraConfigurationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/analytics-profile")
def get_camera_analytics_profile(camera_id: int, db: Session = Depends(get_db)):
    try:
        return get_camera_profile(db, camera_id).to_dict()
    except CameraConfigurationError as exc:
        raise _translate_error(exc) from exc


@router.put("/analytics-profile")
def update_camera_analytics_profile(
    camera_id: int,
    payload: CameraAnalyticsProfileUpdate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    try:
        camera, profile = update_camera_profile(db, camera_id, payload.model_dump())
    except CameraConfigurationError as exc:
        raise _translate_error(exc) from exc

    log_audit(
        db,
        "camera_analytics_profile_update",
        current_user,
        f"Atualizou perfil de analíticos da câmera: {camera.name} (id={camera_id})",
        ip_address=request.client.host if request.client else None,
    )
    return {
        "message": "Perfil analítico atualizado",
        "camera_id": camera_id,
        "profile": profile.to_dict(),
    }


@router.put("/analytics-config")
def update_analytics_config(
    camera_id: int,
    payload: CameraAnalyticsConfigUpdate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    try:
        camera = update_legacy_analytics_config(
            db,
            camera_id,
            roi_name=payload.roi_name,
            roi_polygon_json=payload.roi_polygon_json,
            line_start_x=payload.line_start_x,
            line_start_y=payload.line_start_y,
            line_end_x=payload.line_end_x,
            line_end_y=payload.line_end_y,
            line_direction=payload.line_direction,
        )
    except CameraConfigurationError as exc:
        raise _translate_error(exc) from exc

    log_audit(
        db,
        "camera_analytics_config_update",
        current_user,
        f"Atualizou config de analíticos da câmera: {camera.name} (id={camera_id})",
        ip_address=request.client.host if request.client else None,
    )
    return {"message": "Configuração analítica atualizada", "camera_id": camera_id}


@router.put("/ops-config")
def update_ops_config(
    camera_id: int,
    payload: CameraOpsConfigUpdate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    try:
        camera = update_operational_config(
            db,
            camera_id,
            manufacturer=payload.manufacturer,
            model=payload.model,
            site_name=payload.site_name,
            group_name=payload.group_name,
            camera_priority=payload.camera_priority,
            auto_start_enabled=payload.auto_start_enabled,
            alarm_sound_enabled=payload.alarm_sound_enabled,
            alarm_popup_enabled=payload.alarm_popup_enabled,
        )
    except CameraConfigurationError as exc:
        raise _translate_error(exc) from exc

    log_audit(
        db,
        "camera_ops_config_update",
        current_user,
        f"Atualizou config operacional da câmera: {camera.name} (id={camera_id})",
        ip_address=request.client.host if request.client else None,
    )
    return {"message": "Perfil operacional atualizado", "camera_id": camera_id}
