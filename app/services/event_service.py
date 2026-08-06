"""Casos de uso compartilhados para eventos e revisao operacional."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.timezone import format_brazil_datetime, utc_now_naive
from app.db.models import Camera, Event
from app.services.feedback_review_service import record_feedback
from app.services.feedback_tuning_service import (
    generate_policy_suggestions,
    maybe_apply_bounded_auto_tuning,
)


EVENT_STATUS_CHOICES = frozenset({"new", "acknowledged", "closed"})


@dataclass(frozen=True)
class EventServiceError(Exception):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class EventFeedbackResult:
    event: Event
    feedback_id: int
    suggestions_created: int



def normalize_event_status(status: str | None) -> str:
    if status == "canceled":
        return "closed"
    return str(status) if status in EVENT_STATUS_CHOICES else "new"


def event_alarm_eligible(event: Event) -> bool:
    explicit = getattr(event, "alarm_eligible", None)
    if explicit is not None:
        return bool(explicit)
    return getattr(event, "status_display", normalize_event_status(event.status)) != "closed"


def get_event(db: Session, event_id: int) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise EventServiceError(404, "Evento não encontrado")
    return event


def serialize_event(event: Event) -> dict[str, Any]:
    from app.services.incident_service import incident_sla_state

    return {
        "id": event.id,
        "camera_id": event.camera_id,
        "event_type": event.event_type,
        "track_id": event.track_id,
        "confidence": event.confidence,
        "detector_score": event.detector_score,
        "event_score": event.event_score,
        "details": event.details,
        "snapshot_path": event.snapshot_path,
        "clip_path": event.clip_path,
        "started_at": format_brazil_datetime(event.started_at) if event.started_at else None,
        "ended_at": format_brazil_datetime(event.ended_at) if event.ended_at else None,
        "scene_profile": event.scene_profile,
        "camera_family": event.camera_family,
        "rule_id": event.rule_id,
        "zone_id": event.zone_id,
        "roi_id": event.roi_id,
        "severity": event.severity,
        "status": event.status,
        "alarm_eligible": event.alarm_eligible,
        "lifecycle_action": event.lifecycle_action,
        "alarm_category": event.alarm_category,
        "correlation_key": event.correlation_key,
        "related_event_id": event.related_event_id,
        "resolved_by_event_id": event.resolved_by_event_id,
        "resolved_at": format_brazil_datetime(event.resolved_at) if event.resolved_at else None,
        "is_alarm_active": event.is_alarm_active,
        "operator_note": event.operator_note,
        "assigned_user_id": event.assigned_user_id,
        "assigned_username": event.assigned_username,
        "sla_due_at": format_brazil_datetime(event.sla_due_at) if event.sla_due_at else None,
        "sla_state": incident_sla_state(event),
        "escalated_at": format_brazil_datetime(event.escalated_at) if event.escalated_at else None,
        "resolution_code": event.resolution_code,
        "acknowledged_at": format_brazil_datetime(event.acknowledged_at) if event.acknowledged_at else None,
        "closed_at": format_brazil_datetime(event.closed_at) if event.closed_at else None,
        "active_profile_snapshot": event.active_profile_snapshot,
        "threshold_snapshot": event.threshold_snapshot,
        "nuisance_profile_snapshot": event.nuisance_profile_snapshot,
        "created_at": format_brazil_datetime(event.created_at),
    }


def list_event_payloads(db: Session, *, limit: int = 200) -> list[dict[str, Any]]:
    events = db.query(Event).order_by(Event.id.desc()).limit(limit).all()
    return [serialize_event(event) for event in events]


def list_audit_queue_payloads(db: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    """Retorna eventos mantidos na fila silenciosa de auditoria (audit / low_priority / sem alarme ativo)."""
    events = (
        db.query(Event)
        .filter(
            or_(
                Event.status.in_(["audit", "low_priority", "processing"]),
                Event.is_alarm_active.is_(False),
                Event.details.like("%audit%"),
            )
        )
        .order_by(Event.id.desc())
        .limit(limit)
        .all()
    )
    return [serialize_event(event) for event in events]


def get_tuning_summary_payload(db: Session, camera_id: int | None = None) -> dict[str, Any]:
    """Retorna estatísticas de falsos alarmes por causa provável e sugestões ativas de tuning."""
    from app.db.models import EventFeedback, TuningSuggestion
    from app.services.feedback_constants import PROBABLE_CAUSES

    query_feedback = db.query(EventFeedback)
    query_suggestions = db.query(TuningSuggestion)
    if camera_id is not None:
        query_feedback = query_feedback.filter(EventFeedback.camera_id == camera_id)
        query_suggestions = query_suggestions.filter(TuningSuggestion.camera_id == camera_id)

    feedbacks = query_feedback.order_by(EventFeedback.id.desc()).limit(500).all()
    suggestions = query_suggestions.order_by(TuningSuggestion.id.desc()).limit(100).all()

    cause_counts: dict[str, int] = {}
    label_counts: dict[str, int] = {}
    for fb in feedbacks:
        if fb.probable_cause:
            cause_counts[fb.probable_cause] = cause_counts.get(fb.probable_cause, 0) + 1
        if fb.label:
            label_counts[fb.label] = label_counts.get(fb.label, 0) + 1

    return {
        "total_feedbacks": len(feedbacks),
        "label_counts": label_counts,
        "probable_cause_counts": cause_counts,
        "available_causes": PROBABLE_CAUSES,
        "suggestions": [
            {
                "id": s.id,
                "camera_id": s.camera_id,
                "scope_type": s.scope_type,
                "suggestion_type": s.suggestion_type,
                "parameter_name": s.parameter_name,
            }
            for s in suggestions
        ],
    }


def update_event_record(
    db: Session,
    event_id: int,
    *,
    status: str | None,
    operator_note: str | None,
    now: datetime | None = None,
) -> Event:
    event = get_event(db, event_id)
    if status is not None:
        if status not in EVENT_STATUS_CHOICES:
            raise EventServiceError(400, "Status inválido")
        event.status = status
        transition_time = now or utc_now_naive()
        if status == "acknowledged":
            event.acknowledged_at = transition_time
        elif status == "closed":
            if not event.acknowledged_at:
                event.acknowledged_at = transition_time
            event.closed_at = transition_time
            event.is_alarm_active = False
        elif bool(getattr(event, "alarm_eligible", False)):
            event.closed_at = None
            event.status = "new"
            event.is_alarm_active = True
        else:
            event.status = "closed"

    if operator_note is not None:
        event.operator_note = operator_note.strip() or None
    db.commit()
    return event


def acknowledge_alarm_event(db: Session, event_id: int) -> bool:
    event = get_event(db, event_id)
    if not event_alarm_eligible(event):
        return False
    event.status = "acknowledged"
    event.acknowledged_at = utc_now_naive()
    db.commit()
    return True


def close_alarm_event(db: Session, event_id: int) -> bool:
    event = get_event(db, event_id)
    if not event_alarm_eligible(event):
        return False
    if normalize_event_status(event.status) == "new":
        event.acknowledged_at = utc_now_naive()
    event.status = "closed"
    event.closed_at = utc_now_naive()
    event.is_alarm_active = False
    db.commit()
    return True


def reopen_alarm_event(db: Session, event_id: int) -> bool:
    event = get_event(db, event_id)
    if not event_alarm_eligible(event):
        return False
    event.status = "new"
    event.closed_at = None
    event.is_alarm_active = True
    db.commit()
    return True


def update_event_note(db: Session, event_id: int, operator_note: str | None) -> Event:
    event = get_event(db, event_id)
    event.operator_note = str(operator_note or "").strip() or None
    db.commit()
    return event


def record_event_feedback(
    db: Session,
    event_id: int,
    *,
    label: str,
    probable_cause: str | None,
    operator_note: str | None,
    reviewed_by: str | None,
    auto_suggest: bool,
    commit_feedback_first: bool = True,
) -> EventFeedbackResult:
    event = get_event(db, event_id)
    camera = db.query(Camera).filter(Camera.id == event.camera_id).first()
    try:
        feedback = record_feedback(
            db,
            event=event,
            label=label,
            probable_cause=probable_cause,
            operator_note=operator_note,
            reviewed_by=reviewed_by,
        )
    except ValueError as exc:
        raise EventServiceError(400, str(exc)) from exc
    if commit_feedback_first:
        db.commit()

    suggestions = []
    if auto_suggest and camera and str(getattr(camera, "learning_mode", "assisted_policy_tuning")) != "manual_only":
        suggestions = generate_policy_suggestions(db, camera)
        maybe_apply_bounded_auto_tuning(db, camera)
    db.commit()
    return EventFeedbackResult(
        event=event,
        feedback_id=feedback.id,
        suggestions_created=len(suggestions),
    )
