"""Rotas HTTP de start e stop do processamento de cameras."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.camera_runtime_service import (
    CameraRuntimeError,
    start_camera_processing,
    stop_camera_processing,
)


router = APIRouter(prefix="/cameras/{camera_id}", tags=["camera-runtime"])


def _translate_error(exc: CameraRuntimeError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/start")
def start_camera(
    camera_id: int,
    request: Request,
    use_motion_test: bool = Query(True),
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    try:
        operation = start_camera_processing(db, camera_id, use_motion_test=use_motion_test)
    except CameraRuntimeError as exc:
        raise _translate_error(exc) from exc

    if operation.audit_details:
        log_audit(
            db,
            "camera_start",
            current_user,
            operation.audit_details,
            ip_address=request.client.host if request.client else None,
        )
    return operation.payload


@router.post("/stop")
def stop_camera(
    camera_id: int,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    try:
        operation = stop_camera_processing(db, camera_id)
    except CameraRuntimeError as exc:
        raise _translate_error(exc) from exc

    if operation.audit_details:
        log_audit(
            db,
            "camera_stop",
            current_user,
            operation.audit_details,
            ip_address=request.client.host if request.client else None,
        )
    return operation.payload
