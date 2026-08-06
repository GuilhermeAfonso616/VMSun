"""Serviço de envio HTTP para o Lockdown.

Centraliza montagem de payload, assinatura HMAC, persistencia do delivery e
reenvio manual para manter o fluxo previsivel e rastreavel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import time
from datetime import datetime
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timezone import as_brazil_naive, now_brazil_naive, utc_now_naive
from app.db.models import Event, LockdownDelivery
from app.services.lockdown_policy_store import load_lockdown_policy


logger = get_logger("app.services.lockdown_ingest")
MAX_TEXT_LENGTH = 8000


def truncate_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str | None:
    if value is None:
        return None

    text = str(value)
    if len(text) <= limit:
        return text

    return text[: max(0, limit - 3)] + "..."


def build_lockdown_payload(event: Event) -> dict[str, Any]:
    event_created_at = as_brazil_naive(event.created_at) or now_brazil_naive()
    executed_at = event_created_at.strftime("%Y-%m-%d %H:%M:%S")
    date_folder = event_created_at.strftime("%d-%m-%Y")
    filename = f"evento_{event.id}.json"

    return {
        "event_id": event.id,
        "monitor_id": event.camera_id,
        "executed_at": executed_at,
        "frames_analyzed": 15,
        "dirname": f"{date_folder}/ID_{event.camera_id}/{filename}",
        "filename": filename,
    }


def serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_signature(timestamp: int, body_json: str, secret: str) -> str:
    message = f"{timestamp}.{body_json}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _normalize_url(url: str) -> str:
    return url.strip()


def _upsert_delivery_from_event(event: Event, db: Session) -> LockdownDelivery:
    delivery = LockdownDelivery(
        event_id=event.id,
        camera_id=event.camera_id,
        event_type=event.event_type,
        target_url=_normalize_url(settings.lockdown_ingest_url),
        status="pending",
        attempt_count=0,
    )
    db.add(delivery)
    db.flush()
    db.commit()
    db.refresh(delivery)
    return delivery


def _persist_payload_preview(
    db: Session,
    delivery: LockdownDelivery,
    body_json: str,
    request_timestamp: int | None = None,
    request_signature: str | None = None,
) -> None:
    delivery.request_body = body_json
    delivery.request_timestamp = request_timestamp
    delivery.request_signature = request_signature
    db.add(delivery)
    db.commit()


def _store_delivery_error(
    db: Session,
    delivery: LockdownDelivery,
    error_message: str,
    http_status: int | None = None,
    response_body: str | None = None,
) -> None:
    delivery.status = "error"
    delivery.error_message = truncate_text(error_message)
    delivery.http_status = http_status
    delivery.response_body = truncate_text(response_body)
    delivery.sent_at = None
    db.add(delivery)
    db.commit()


def _store_delivery_success(
    db: Session,
    delivery: LockdownDelivery,
    http_status: int | None,
    response_body: str | None,
) -> None:
    delivery.status = "sent"
    delivery.http_status = http_status
    delivery.response_body = truncate_text(response_body)
    delivery.error_message = None
    delivery.sent_at = utc_now_naive()
    db.add(delivery)
    db.commit()


def _attempt_http_send(
    db: Session,
    delivery: LockdownDelivery,
    body_json: str,
    request_timestamp: int,
    request_signature: str,
) -> None:
    timeout_seconds = max(0.1, float(settings.lockdown_ingest_timeout_seconds))
    headers = {
        "X-Timestamp": str(request_timestamp),
        "X-Signature": request_signature,
        "Content-Type": "application/json",
    }
    request_obj = urllib_request.Request(
        delivery.target_url,
        data=body_json.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    logger.info(
        "Lockdown ingest started event_id=%s delivery_id=%s url=%s",
        delivery.event_id,
        delivery.id,
        delivery.target_url,
    )

    _persist_payload_preview(db, delivery, body_json, request_timestamp, request_signature)
    delivery.status = "pending"
    delivery.attempt_count = int(delivery.attempt_count or 0) + 1
    delivery.last_attempt_at = utc_now_naive()
    delivery.error_message = None
    db.add(delivery)
    db.commit()

    try:
        with urllib_request.urlopen(request_obj, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            http_status = getattr(response, "status", None) or response.getcode()
            if http_status is None:
                http_status = 200
            if 200 <= int(http_status) < 300:
                if response_body.strip():
                    try:
                        json.loads(response_body)
                    except Exception as exc:
                        message = f"Resposta invalida do Lockdown (JSON esperado): {exc}"
                        _store_delivery_error(db, delivery, message, http_status=int(http_status), response_body=response_body)
                        logger.warning(
                            "Lockdown ingest invalid JSON response event_id=%s delivery_id=%s http_status=%s",
                            delivery.event_id,
                            delivery.id,
                            http_status,
                        )
                        return
                _store_delivery_success(db, delivery, int(http_status), response_body)
                logger.info(
                    "Lockdown ingest success event_id=%s delivery_id=%s http_status=%s",
                    delivery.event_id,
                    delivery.id,
                    http_status,
                )
                return

            message = f"HTTP {http_status}: {truncate_text(response_body, 2000) or ''}".strip()
            _store_delivery_error(db, delivery, message, http_status=int(http_status), response_body=response_body)
            logger.warning(
                "Lockdown ingest HTTP error event_id=%s delivery_id=%s http_status=%s",
                delivery.event_id,
                delivery.id,
                http_status,
            )
    except urllib_error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        message = f"HTTP {exc.code}: {truncate_text(response_body, 2000) or exc.reason}"
        _store_delivery_error(db, delivery, message, http_status=int(exc.code), response_body=response_body)
        logger.warning(
            "Lockdown ingest HTTP error event_id=%s delivery_id=%s http_status=%s",
            delivery.event_id,
            delivery.id,
            exc.code,
        )
    except (urllib_error.URLError, socket.timeout, TimeoutError) as exc:
        message = f"Lockdown request failed: {exc}"
        _store_delivery_error(db, delivery, message)
        logger.exception(
            "Lockdown ingest exception event_id=%s delivery_id=%s",
            delivery.event_id,
            delivery.id,
        )
    except Exception as exc:
        message = f"Lockdown unexpected error: {exc}"
        _store_delivery_error(db, delivery, message)
        logger.exception(
            "Lockdown ingest unexpected exception event_id=%s delivery_id=%s",
            delivery.event_id,
            delivery.id,
        )


def _prepare_attempt(event: Event) -> tuple[str, int]:
    payload = build_lockdown_payload(event)
    body_json = serialize_payload(payload)
    request_timestamp = int(time.time())
    return body_json, request_timestamp


def _prepare_delivery_record(event: Event, db: Session) -> tuple[LockdownDelivery, str, int]:
    delivery = db.query(LockdownDelivery).filter(LockdownDelivery.event_id == event.id).first()
    if delivery is None:
        delivery = _upsert_delivery_from_event(event, db)

    body_json, request_timestamp = _prepare_attempt(event)
    _persist_payload_preview(db, delivery, body_json, request_timestamp=request_timestamp)
    # Persistimos o body antes de qualquer validacao para manter rastreabilidade do envio.
    logger.info(
        "Lockdown payload persisted event_id=%s delivery_id=%s",
        event.id,
        delivery.id,
    )
    return delivery, body_json, request_timestamp


def _build_disabled_reason() -> str:
    if not settings.lockdown_ingest_enabled:
        return "Lockdown ingest desabilitado no .env"
    if not settings.lockdown_ingest_url.strip():
        return "Lockdown ingest URL nao configurada"
    if not settings.lockdown_ingest_secret.strip():
        return "Lockdown ingest secret nao configurada"
    return "Lockdown ingest indisponivel"


def _event_triggers_lockdown(event: Event) -> bool:
    policy = load_lockdown_policy()
    allowed_trigger_events = set(policy.get("allowed_trigger_events") or [])
    status = str(getattr(event, "status", "") or "").lower()
    lifecycle_action = str(getattr(event, "lifecycle_action", "") or "").lower()
    alarm_eligible = getattr(event, "alarm_eligible", None)
    is_alarm_active = getattr(event, "is_alarm_active", None)

    if alarm_eligible is not None and not bool(alarm_eligible):
        return False
    if is_alarm_active is not None and not bool(is_alarm_active):
        return False

    return (
        event.event_type in allowed_trigger_events
        and lifecycle_action == "open"
        and status in {"new", "processing", "persisted"}
    )


def send_lockdown_delivery(delivery_id: int, db: Session) -> None:
    try:
        delivery = db.query(LockdownDelivery).filter(LockdownDelivery.id == delivery_id).first()
        if not delivery:
            return

        event = db.query(Event).filter(Event.id == delivery.event_id).first()
        if not event:
            _store_delivery_error(db, delivery, "Evento local nao encontrado para reenvio")
            return

        delivery, body_json, request_timestamp = _prepare_delivery_record(event, db)
        if not settings.lockdown_ingest_enabled or not settings.lockdown_ingest_url.strip() or not settings.lockdown_ingest_secret.strip():
            reason = _build_disabled_reason()
            delivery.target_url = _normalize_url(settings.lockdown_ingest_url)
            db.add(delivery)
            db.commit()
            _store_delivery_error(db, delivery, reason)
            logger.info(
                "Lockdown ingest skipped event_id=%s delivery_id=%s reason=%s",
                delivery.event_id,
                delivery.id,
                reason,
            )
            return

        request_signature = build_signature(request_timestamp, body_json, settings.lockdown_ingest_secret)
        _persist_payload_preview(db, delivery, body_json, request_timestamp, request_signature)
        delivery.target_url = settings.lockdown_ingest_url.strip()
        _attempt_http_send(db, delivery, body_json, request_timestamp, request_signature)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Lockdown resend failed delivery_id=%s", delivery_id)


def send_event_if_needed(event: Event, db: Session) -> None:
    try:
        if not _event_triggers_lockdown(event):
            return

        delivery, body_json, request_timestamp = _prepare_delivery_record(event, db)
        if not settings.lockdown_ingest_enabled or not settings.lockdown_ingest_url.strip() or not settings.lockdown_ingest_secret.strip():
            reason = _build_disabled_reason()
            delivery.target_url = _normalize_url(settings.lockdown_ingest_url)
            db.add(delivery)
            db.commit()
            _store_delivery_error(db, delivery, reason)
            logger.info(
                "Lockdown ingest skipped event_id=%s delivery_id=%s reason=%s",
                delivery.event_id,
                delivery.id,
                reason,
            )
            return

        request_signature = build_signature(request_timestamp, body_json, settings.lockdown_ingest_secret)
        _persist_payload_preview(db, delivery, body_json, request_timestamp, request_signature)
        delivery.target_url = settings.lockdown_ingest_url.strip()
        _attempt_http_send(db, delivery, body_json, request_timestamp, request_signature)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Lockdown automatic ingest failed event_id=%s", getattr(event, "id", None))
