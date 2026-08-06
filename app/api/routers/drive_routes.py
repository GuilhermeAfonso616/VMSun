"""Endpoints do operador para integracao com OneDrive."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.schemas.operator_schemas import OneDriveTokenUpdate, OneDriveUploadToggle
from app.services.onedrive_client import onedrive_client
from app.services.onedrive_reviewed_backfill_service import upload_reviewed_events_pending_onedrive


router = APIRouter(prefix="/operator", tags=["operator-drive"])


@router.get("/drive-token/status")
def operator_drive_token_status():
    return onedrive_client.status(refresh_if_needed=True)


@router.post("/drive-token")
def operator_drive_token(payload: OneDriveTokenUpdate):
    try:
        onedrive_client.save_token_text(payload.token)
        return {"ok": True, "onedrive": onedrive_client.status(refresh_if_needed=True)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "Falha ao salvar token do Drive") from exc


@router.post("/drive-upload")
def operator_drive_upload_toggle(payload: OneDriveUploadToggle):
    try:
        return {"ok": True, "onedrive": onedrive_client.set_archive_enabled(bool(payload.enabled))}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "Falha ao alterar upload do Drive") from exc


@router.post("/drive-reviewed-events/upload")
def operator_drive_reviewed_events_upload(limit: int | None = 200, db: Session = Depends(get_db)):
    try:
        return {"ok": True, "onedrive": upload_reviewed_events_pending_onedrive(db, limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "Falha ao enviar eventos avaliados para o Drive") from exc
