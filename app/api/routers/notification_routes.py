"""Configuracao de canais e acompanhamento de entregas de notificacao."""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.core.timezone import utc_now_naive
from app.db.models import NotificationChannel, NotificationDelivery, User
from app.services.audit_service import log_audit
from app.services.notification_service import (
    NotificationError,
    deliver_notification,
    enqueue_test_notification,
    validate_channel_target,
)


router = APIRouter(prefix="/notifications", tags=["notifications"])
VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "webhook"
    target: str
    signing_secret: str | None = None
    enabled: bool = True
    min_severity: str = "medium"
    event_types: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    max_attempts: int = Field(default=5, ge=1, le=20)


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target: str | None = None
    signing_secret: str | None = None
    clear_signing_secret: bool = False
    enabled: bool | None = None
    min_severity: str | None = None
    event_types: list[str] | None = None
    timeout_seconds: float | None = Field(default=None, ge=0.1, le=60.0)
    max_attempts: int | None = Field(default=None, ge=1, le=20)


def _validate_severity(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in VALID_SEVERITIES:
        raise HTTPException(status_code=400, detail="Severidade minima invalida.")
    return normalized


def _mask_target(value: str) -> str:
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = f"{hostname}:{parsed.port}" if parsed.port else hostname
    return urlunsplit((parsed.scheme, authority, "/…" if parsed.path not in {"", "/"} else "", "", ""))


def _channel_out(channel: NotificationChannel) -> dict:
    try:
        event_types = json.loads(channel.event_types_json or "[]")
    except ValueError:
        event_types = []
    return {
        "id": channel.id,
        "name": channel.name,
        "kind": channel.kind,
        "target_masked": _mask_target(channel.target),
        "signing_secret_configured": bool(channel.signing_secret),
        "enabled": channel.enabled,
        "min_severity": channel.min_severity,
        "event_types": event_types,
        "timeout_seconds": channel.timeout_seconds,
        "max_attempts": channel.max_attempts,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


def _delivery_out(delivery: NotificationDelivery, channel_name: str | None = None) -> dict:
    return {
        "id": delivery.id,
        "event_id": delivery.event_id,
        "channel_id": delivery.channel_id,
        "channel_name": channel_name,
        "status": delivery.status,
        "attempt_count": delivery.attempt_count,
        "next_attempt_at": delivery.next_attempt_at,
        "last_attempt_at": delivery.last_attempt_at,
        "sent_at": delivery.sent_at,
        "http_status": delivery.http_status,
        "error_message": delivery.error_message,
        "created_at": delivery.created_at,
    }


@router.get("/channels")
def list_channels(
    _user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    return [_channel_out(item) for item in db.query(NotificationChannel).order_by(NotificationChannel.name).all()]


@router.post("/channels", status_code=201)
def create_channel(
    payload: ChannelCreate,
    request: Request,
    user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    try:
        target = validate_channel_target(payload.kind, payload.target)
    except NotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="O nome do canal e obrigatorio.")
    channel = NotificationChannel(
        name=name,
        kind=payload.kind.strip().lower(),
        target=target,
        signing_secret=(payload.signing_secret or "").strip() or None,
        enabled=payload.enabled,
        min_severity=_validate_severity(payload.min_severity),
        event_types_json=json.dumps(sorted(set(payload.event_types)), ensure_ascii=False),
        timeout_seconds=payload.timeout_seconds,
        max_attempts=payload.max_attempts,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    log_audit(db, "notification_channel_create", user, f"Criou canal: {channel.name}", ip_address=request.client.host if request.client else None)
    return _channel_out(channel)


@router.put("/channels/{channel_id}")
def update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    request: Request,
    user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Canal nao encontrado.")
    if payload.name is not None:
        channel.name = payload.name.strip()
        if not channel.name:
            raise HTTPException(status_code=400, detail="O nome do canal e obrigatorio.")
    if payload.target is not None:
        try:
            channel.target = validate_channel_target(channel.kind, payload.target)
        except NotificationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.signing_secret is not None:
        channel.signing_secret = payload.signing_secret.strip() or None
    if payload.clear_signing_secret:
        channel.signing_secret = None
    if payload.enabled is not None:
        channel.enabled = payload.enabled
        if payload.enabled:
            db.query(NotificationDelivery).filter(
                NotificationDelivery.channel_id == channel.id,
                NotificationDelivery.status == "paused",
            ).update(
                {
                    NotificationDelivery.status: "pending",
                    NotificationDelivery.next_attempt_at: utc_now_naive(),
                },
                synchronize_session=False,
            )
    if payload.min_severity is not None:
        channel.min_severity = _validate_severity(payload.min_severity)
    if payload.event_types is not None:
        channel.event_types_json = json.dumps(sorted(set(payload.event_types)), ensure_ascii=False)
    if payload.timeout_seconds is not None:
        channel.timeout_seconds = payload.timeout_seconds
    if payload.max_attempts is not None:
        channel.max_attempts = payload.max_attempts
    db.commit()
    db.refresh(channel)
    log_audit(db, "notification_channel_update", user, f"Alterou canal: {channel.name}", ip_address=request.client.host if request.client else None)
    return _channel_out(channel)


@router.post("/channels/{channel_id}/test")
def test_channel(
    channel_id: int,
    request: Request,
    user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Canal nao encontrado.")
    delivery = enqueue_test_notification(channel, db)
    delivery = deliver_notification(delivery.id, db, force=True)
    log_audit(db, "notification_channel_test", user, f"Testou canal: {channel.name} ({delivery.status})", ip_address=request.client.host if request.client else None)
    return _delivery_out(delivery, channel.name)


@router.get("/deliveries")
def list_deliveries(
    status: str | None = None,
    limit: int = 100,
    _user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    query = db.query(NotificationDelivery)
    if status:
        query = query.filter(NotificationDelivery.status == status.strip().lower())
    deliveries = query.order_by(NotificationDelivery.id.desc()).limit(min(500, max(1, limit))).all()
    names = {item.id: item.name for item in db.query(NotificationChannel).all()}
    return [_delivery_out(item, names.get(item.channel_id)) for item in deliveries]


@router.post("/deliveries/{delivery_id}/resend")
def resend_delivery(
    delivery_id: int,
    request: Request,
    user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    delivery = db.get(NotificationDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Entrega nao encontrada.")
    delivery.status = "pending"
    delivery.next_attempt_at = utc_now_naive()
    delivery.error_message = None
    db.commit()
    delivered = deliver_notification(delivery.id, db, force=True)
    log_audit(db, "notification_delivery_resend", user, f"Reenviou entrega: {delivery.id} ({delivered.status})", ip_address=request.client.host if request.client else None)
    channel = db.get(NotificationChannel, delivered.channel_id)
    return _delivery_out(delivered, channel.name if channel else None)
