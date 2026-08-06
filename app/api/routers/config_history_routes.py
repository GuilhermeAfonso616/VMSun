"""Rotas HTTP de historico e rollback de configuracao."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.feedback_workflow_service import (
    FeedbackWorkflowError,
    list_config_history_payloads,
    rollback_config_history,
)


router = APIRouter(prefix="/config/history", tags=["configuration-history"])


@router.get("")
def config_history(camera_id: int | None = None, db: Session = Depends(get_db)):
    return {"items": list_config_history_payloads(db, camera_id)}


@router.post("/{history_id}/rollback")
def config_rollback(
    history_id: int,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    try:
        camera = rollback_config_history(db, history_id)
    except FeedbackWorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    log_audit(
        db,
        "config_rollback",
        current_user,
        (
            f"Realizou rollback de configuração para a câmera: {camera.name} "
            f"(id={camera.id}), para a versão de histórico id={history_id}"
        ),
        ip_address=request.client.host if request.client else None,
    )
    return {"message": "Rollback aplicado", "camera_id": camera.id, "history_id": history_id}
