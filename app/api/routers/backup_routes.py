"""Exportacao e restauracao administrativa de backups."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app.api.dependencies import require_role
from app.core.config import settings
from app.db.models import User
from app.services.backup_service import BackupError, BackupService


router = APIRouter(prefix="/backup", tags=["backup"])


class BackupRequest(BaseModel):
    password: str


def _backup_paths() -> tuple[Path, Path]:
    database_url = settings.database_url
    if database_url.startswith("sqlite:///"):
        database_path = Path(database_url.removeprefix("sqlite:///"))
    else:
        database_path = Path(settings.app_base_dir) / "data" / "analytics.db"
    return database_path, Path(settings.app_base_dir) / ".env"


def _credential_key_path() -> Path:
    return Path(
        settings.credential_encryption_key_file
        or Path(settings.runtime_state_dir) / "credential_encryption_key"
    )


@router.post("/export")
def export_backup(
    payload: BackupRequest,
    _current_user: User = Depends(require_role(["admin"])),
):
    try:
        database_path, environment_path = _backup_paths()
        backup_bytes = BackupService.create_backup(
            database_path, environment_path, payload.password, _credential_key_path()
        )
        return Response(
            content=backup_bytes,
            media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=vms_backup.enc"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/import")
def import_backup(
    file: UploadFile = File(...),
    password: str = Form(...),
    _current_user: User = Depends(require_role(["admin"])),
):
    try:
        database_path, environment_path = _backup_paths()
        BackupService.restore_backup(
            file.file.read(), database_path, environment_path, password, _credential_key_path()
        )
        return {
            "status": "success",
            "message": "Backup restaurado com sucesso! Reinicie o servidor para aplicar todas as mudancas.",
        }
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
