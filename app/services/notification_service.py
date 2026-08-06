"""Fila persistente e entrega confiavel de notificacoes de eventos."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import timedelta
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timezone import utc_now_naive
from app.db.base import SessionLocal
from app.db.models import Camera, Event, NotificationChannel, NotificationDelivery


logger = get_logger("app.services.notifications")
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
DELIVERABLE_STATUSES = frozenset({"pending", "retry"})
_LAST_PRUNE_AT = 0.0


class NotificationError(ValueError):
    pass


def validate_channel_target(kind: str, target: str) -> str:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind != "webhook":
        raise NotificationError("Somente canais webhook sao suportados nesta versao.")
    normalized = str(target or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise NotificationError("O webhook deve usar uma URL http:// ou https:// valida.")
    try:
        parsed.port
    except ValueError as exc:
        raise NotificationError("A porta do webhook e invalida.") from exc
    if not parsed.hostname:
        raise NotificationError("O webhook deve informar um host valido.")
    if parsed.username or parsed.password:
        raise NotificationError("Nao inclua usuario ou senha na URL do webhook.")
    return normalized


def _event_is_notifiable(event: Event) -> bool:
    return bool(
        event.alarm_eligible is not False
        and event.is_alarm_active is not False
        and str(event.lifecycle_action or "open").lower() == "open"
        and str(event.status or "").lower() in {"new", "processing", "persisted"}
    )


def _channel_matches(channel: NotificationChannel, event: Event) -> bool:
    event_severity = SEVERITY_ORDER.get(str(event.severity or "medium").lower(), 1)
    minimum = SEVERITY_ORDER.get(str(channel.min_severity or "medium").lower(), 1)
    if event_severity < minimum:
        return False
    if not channel.event_types_json:
        return True
    try:
        allowed = {str(value) for value in json.loads(channel.event_types_json)}
    except (TypeError, ValueError):
        return False
    return not allowed or event.event_type in allowed


def build_event_payload(
    event: Event,
    camera: Camera | None,
    *,
    notification_type: str = "alarm",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": notification_type,
        "event": {
            "id": event.id,
            "camera_id": event.camera_id,
            "camera_name": camera.name if camera else f"Camera {event.camera_id}",
            "site_name": camera.site_name if camera else None,
            "group_name": camera.group_name if camera else None,
            "camera_priority": camera.camera_priority if camera else None,
            "event_type": event.event_type,
            "severity": event.severity or "medium",
            "status": event.status,
            "lifecycle_action": event.lifecycle_action,
            "alarm_category": event.alarm_category,
            "confidence": event.confidence,
            "event_score": event.event_score,
            "details": event.details,
            "assigned_username": event.assigned_username,
            "sla_due_at": event.sla_due_at,
            "escalated_at": event.escalated_at,
            "started_at": event.started_at,
            "created_at": event.created_at,
        },
        "artifacts": {
            "snapshot_url": event.snapshot_remote_web_url,
            "clip_url": event.clip_remote_web_url,
        },
    }


def enqueue_event_notifications(
    event: Event,
    db: Session,
    *,
    notification_type: str = "alarm",
) -> list[NotificationDelivery]:
    if not settings.notification_dispatch_enabled or not _event_is_notifiable(event):
        return []
    camera = db.get(Camera, event.camera_id)
    payload_json = json.dumps(
        build_event_payload(event, camera, notification_type=notification_type),
        ensure_ascii=False,
        default=str,
    )
    created: list[NotificationDelivery] = []
    channels = db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True)).all()
    for channel in channels:
        if not _channel_matches(channel, event):
            continue
        idempotency_key = f"event:{event.id}:channel:{channel.id}:type:{notification_type}"
        exists = db.query(NotificationDelivery.id).filter(
            NotificationDelivery.idempotency_key == idempotency_key
        ).first()
        if exists:
            continue
        delivery = NotificationDelivery(
            event_id=event.id,
            channel_id=channel.id,
            idempotency_key=idempotency_key,
            status="pending",
            attempt_count=0,
            payload_json=payload_json,
            next_attempt_at=utc_now_naive(),
        )
        db.add(delivery)
        created.append(delivery)
    if created:
        try:
            db.commit()
        except IntegrityError:
            # Outro produtor venceu a corrida da chave idempotente.
            db.rollback()
            return []
        for delivery in created:
            db.refresh(delivery)
    return created


def enqueue_test_notification(channel: NotificationChannel, db: Session) -> NotificationDelivery:
    payload = {
        "schema_version": 1,
        "type": "test",
        "message": "Notificacao de teste do SunOrus VMS",
        "channel": {"id": channel.id, "name": channel.name},
        "created_at": utc_now_naive().isoformat(),
    }
    delivery = NotificationDelivery(
        channel_id=channel.id,
        idempotency_key=f"test:{uuid4().hex}",
        status="pending",
        attempt_count=0,
        payload_json=json.dumps(payload, ensure_ascii=False),
        next_attempt_at=utc_now_naive(),
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


def _truncate(value: Any) -> str | None:
    if value is None:
        return None
    limit = max(256, int(settings.notification_response_max_chars))
    return str(value)[:limit]


def _schedule_failure(
    db: Session,
    delivery: NotificationDelivery,
    channel: NotificationChannel,
    message: str,
    *,
    http_status: int | None = None,
    response_body: str | None = None,
) -> None:
    delivery.http_status = http_status
    delivery.response_body = _truncate(response_body)
    delivery.error_message = _truncate(message)
    if delivery.attempt_count >= max(1, int(channel.max_attempts or 1)):
        delivery.status = "dead"
        delivery.next_attempt_at = None
    else:
        delivery.status = "retry"
        delay = max(1.0, float(settings.notification_retry_base_seconds)) * (
            2 ** max(0, delivery.attempt_count - 1)
        )
        delivery.next_attempt_at = utc_now_naive() + timedelta(seconds=delay)
    db.commit()


def deliver_notification(delivery_id: int, db: Session, *, force: bool = False) -> NotificationDelivery:
    delivery = db.get(NotificationDelivery, delivery_id)
    if delivery is None:
        raise NotificationError("Entrega de notificacao nao encontrada.")
    channel = db.get(NotificationChannel, delivery.channel_id)
    if channel is None:
        raise NotificationError("Canal da notificacao nao encontrado.")
    if delivery.status == "sent" and not force:
        return delivery
    if not channel.enabled and not force:
        delivery.status = "paused"
        delivery.next_attempt_at = None
        delivery.error_message = "Canal desativado."
        db.commit()
        return delivery

    target = validate_channel_target(channel.kind, channel.target)
    body = delivery.payload_json.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Analitico-Delivery": delivery.idempotency_key,
    }
    if channel.signing_secret:
        headers["X-Analitico-Signature"] = hmac.new(
            channel.signing_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

    claim = db.query(NotificationDelivery).filter(NotificationDelivery.id == delivery.id)
    if not force:
        claim = claim.filter(NotificationDelivery.status.in_(DELIVERABLE_STATUSES))
    claimed = claim.update(
        {
            NotificationDelivery.status: "processing",
            NotificationDelivery.attempt_count: NotificationDelivery.attempt_count + 1,
            NotificationDelivery.last_attempt_at: utc_now_naive(),
            NotificationDelivery.error_message: None,
        },
        synchronize_session=False,
    )
    db.commit()
    if not claimed:
        db.refresh(delivery)
        return delivery
    db.refresh(delivery)

    request = urllib_request.Request(target, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(request, timeout=max(0.1, float(channel.timeout_seconds))) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            status = int(getattr(response, "status", None) or response.getcode() or 200)
            if 200 <= status < 300:
                delivery.status = "sent"
                delivery.http_status = status
                delivery.response_body = _truncate(response_body)
                delivery.error_message = None
                delivery.sent_at = utc_now_naive()
                delivery.next_attempt_at = None
                db.commit()
                return delivery
            _schedule_failure(db, delivery, channel, f"Webhook retornou HTTP {status}.", http_status=status, response_body=response_body)
    except urllib_error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        _schedule_failure(db, delivery, channel, f"Webhook retornou HTTP {exc.code}.", http_status=int(exc.code), response_body=response_body)
    except (urllib_error.URLError, socket.timeout, TimeoutError) as exc:
        _schedule_failure(db, delivery, channel, f"Falha de conexao com o webhook: {exc}")
    except Exception as exc:
        _schedule_failure(db, delivery, channel, f"Falha inesperada no webhook: {exc}")
        logger.exception("Notification delivery failed delivery_id=%s", delivery.id)
    db.refresh(delivery)
    return delivery


def dispatch_due_notifications(*, limit: int = 25) -> int:
    global _LAST_PRUNE_AT
    db = SessionLocal()
    dispatched = 0
    try:
        now = utc_now_naive()
        stale_before = now - timedelta(
            seconds=max(30.0, float(settings.notification_processing_timeout_seconds))
        )
        db.query(NotificationDelivery).filter(
            NotificationDelivery.status == "processing",
            NotificationDelivery.last_attempt_at <= stale_before,
        ).update(
            {NotificationDelivery.status: "retry", NotificationDelivery.next_attempt_at: now},
            synchronize_session=False,
        )
        db.commit()
        retention_days = int(settings.notification_delivery_retention_days)
        monotonic_now = time.monotonic()
        if retention_days > 0 and monotonic_now - _LAST_PRUNE_AT >= 3600:
            cutoff = now - timedelta(days=retention_days)
            db.query(NotificationDelivery).filter(
                NotificationDelivery.status.in_(("sent", "dead")),
                NotificationDelivery.created_at < cutoff,
            ).delete(synchronize_session=False)
            db.commit()
            _LAST_PRUNE_AT = monotonic_now
        deliveries = (
            db.query(NotificationDelivery)
            .filter(NotificationDelivery.status.in_(DELIVERABLE_STATUSES))
            .filter(or_(NotificationDelivery.next_attempt_at.is_(None), NotificationDelivery.next_attempt_at <= now))
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
            .limit(max(1, limit))
            .all()
        )
        for delivery in deliveries:
            try:
                deliver_notification(delivery.id, db)
                dispatched += 1
            except Exception:
                db.rollback()
                logger.exception("Notification dispatcher failed delivery_id=%s", delivery.id)
        return dispatched
    finally:
        db.close()


class NotificationDispatcher:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="notification-dispatcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                dispatch_due_notifications()
            except Exception:
                logger.exception("Notification dispatcher cycle failed")
            self._stop.wait(max(0.5, float(settings.notification_poll_seconds)))


notification_dispatcher = NotificationDispatcher()
