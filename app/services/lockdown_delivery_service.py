"""Casos de uso do histórico de entregas para a integração Lockdown."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Event, LockdownDelivery
from app.services import lockdown_ingest_service


LOCKDOWN_STATUS_CHOICES = frozenset({"pending", "sent", "error"})


@dataclass(frozen=True, slots=True)
class LockdownDeliveryListing:
    deliveries: list[LockdownDelivery]
    event_types: list[str]
    total: int
    sent: int
    error: int
    pending: int


class LockdownDeliveryNotFound(LookupError):
    pass


def normalize_lockdown_status(status: str | None) -> str | None:
    return status if status in LOCKDOWN_STATUS_CHOICES else None


def list_lockdown_deliveries(
    db: Session,
    *,
    camera_id: int | None = None,
    status: str | None = None,
    event_type: str | None = None,
    event_id: int | None = None,
    limit: int = 200,
) -> LockdownDeliveryListing:
    query = db.query(LockdownDelivery)
    if camera_id:
        query = query.filter(LockdownDelivery.camera_id == camera_id)
    normalized_status = normalize_lockdown_status(status)
    if normalized_status:
        query = query.filter(LockdownDelivery.status == normalized_status)
    if event_type:
        query = query.filter(LockdownDelivery.event_type == event_type)
    if event_id:
        query = query.filter(LockdownDelivery.event_id == event_id)

    deliveries = query.order_by(LockdownDelivery.id.desc()).limit(limit).all()
    event_types = [
        row[0]
        for row in db.query(Event.event_type)
        .distinct()
        .order_by(Event.event_type.asc())
        .all()
        if row[0]
    ]
    return LockdownDeliveryListing(
        deliveries=deliveries,
        event_types=event_types,
        total=query.count(),
        sent=query.filter(LockdownDelivery.status == "sent").count(),
        error=query.filter(LockdownDelivery.status == "error").count(),
        pending=query.filter(LockdownDelivery.status == "pending").count(),
    )


def resend_delivery(db: Session, delivery_id: int) -> LockdownDelivery:
    delivery = (
        db.query(LockdownDelivery)
        .filter(LockdownDelivery.id == delivery_id)
        .first()
    )
    if delivery is None:
        raise LockdownDeliveryNotFound(f"Entrega {delivery_id} não encontrada")
    lockdown_ingest_service.send_lockdown_delivery(delivery.id, db)
    return delivery
