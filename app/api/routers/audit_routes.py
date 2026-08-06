"""Rotas HTTP de consulta e registro de auditoria."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.db.models import AuditLog, User
from app.services.audit_service import log_audit


router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    user_id: int | None
    username: str | None
    action: str
    details: str | None
    ip_address: str | None


class ClientAuditLog(BaseModel):
    action: str
    details: str | None = None


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    username: str | None = Query(None),
    action: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_role(["admin", "supervisor", "operator", "viewer"])),
    db: Session = Depends(get_db),
):
    query = db.query(AuditLog)

    if current_user.role not in ("admin", "dev"):
        query = query.join(User, AuditLog.user_id == User.id, isouter=True)
        if current_user.role == "supervisor":
            query = query.filter(
                or_(AuditLog.user_id == current_user.id, User.role.in_(["operator", "viewer"]))
            )
        elif current_user.role == "operator":
            query = query.filter(or_(AuditLog.user_id == current_user.id, User.role.in_(["viewer"])))
        else:
            query = query.filter(AuditLog.user_id == current_user.id)

    if username:
        query = query.filter(AuditLog.username.ilike(f"%{username.strip()}%"))
    if action:
        query = query.filter(AuditLog.action == action.strip())
    if isinstance(start_date, str) and start_date:
        try:
            query = query.filter(AuditLog.timestamp >= datetime.fromisoformat(start_date.replace("Z", "")))
        except ValueError:
            pass
    if isinstance(end_date, str) and end_date:
        try:
            query = query.filter(AuditLog.timestamp <= datetime.fromisoformat(end_date.replace("Z", "")))
        except ValueError:
            pass

    return query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()


@router.post("")
def create_client_audit_log(
    payload: ClientAuditLog,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator", "viewer"])),
    db: Session = Depends(get_db),
):
    log_audit(
        db,
        payload.action,
        current_user,
        payload.details,
        ip_address=request.client.host if request.client else None,
    )
    return {"status": "success"}
