"""Apresentação da listagem de eventos para HTML e JSON."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.event_listing_service import list_events, parse_optional_int_filter
from app.web.camera_detail_presenter import (
    enrich_event,
    event_alarm_active,
    event_alarm_eligible,
    event_type_label,
    format_dt,
    get_camera_map,
    infer_event_severity,
    normalize_event_status,
    severity_label,
    status_label,
)
from app.web.monitor_presenter import event_clip_url
from app.web.presentation_constants import PRIORITY_CHOICES
from app.services.incident_service import incident_sla_state


def as_bool(value: str | None) -> bool:
    return value is not None and str(value).lower() in {
        "1",
        "true",
        "on",
        "yes",
        "sim",
    }


def serialize_event_for_table(event) -> dict:
    status_display = getattr(
        event,
        "status_display",
        normalize_event_status(event.status),
    )
    severity_display = getattr(
        event,
        "severity_display",
        infer_event_severity(event.event_type, event.confidence),
    )
    return {
        "id": event.id,
        "camera_id": event.camera_id,
        "camera_name": getattr(event, "camera_name", f"Câmera {event.camera_id}"),
        "site_name": getattr(event, "site_name", None) or "-",
        "event_type": event.event_type or "-",
        "event_type_label": event_type_label(event.event_type),
        "details": event.details or "-",
        "severity_display": severity_display,
        "severity_label": severity_label(severity_display),
        "status_display": status_display,
        "status_label": status_label(status_display),
        "confidence": event.confidence,
        "confidence_label": (
            f"{float(event.confidence):.6f}"
            if event.confidence is not None
            else "-"
        ),
        "snapshot_url": f"/events/{event.id}/snapshot" if event.snapshot_path else "",
        "clip_url": event_clip_url(event),
        "event_url": f"/events?camera_id={event.camera_id}&status={status_display}",
        "camera_url": f"/cameras/{event.camera_id}",
        "operator_note": event.operator_note or "",
        "assigned_user_id": getattr(event, "assigned_user_id", None),
        "assigned_username": getattr(event, "assigned_username", None),
        "sla_due_at_label": format_dt(getattr(event, "sla_due_at", None)) if getattr(event, "sla_due_at", None) else "-",
        "sla_state": incident_sla_state(event),
        "escalated_at_label": format_dt(getattr(event, "escalated_at", None)) if getattr(event, "escalated_at", None) else "",
        "resolution_code": getattr(event, "resolution_code", None),
        "incident_team": getattr(event, "incident_team", None),
        "incident_priority": getattr(event, "incident_priority", None) or severity_display,
        "incident_origin": getattr(event, "incident_origin", None) or "automatic",
        "incident_parent_id": getattr(event, "incident_parent_id", None),
        "created_at_label": format_dt(event.created_at),
        "lifecycle_action": getattr(event, "lifecycle_action", "open"),
        "alarm_category": getattr(event, "alarm_category", None),
        "alarm_eligible": event_alarm_eligible(event),
        "is_alarm_active": event_alarm_active(event),
        "alarm_popup_enabled": bool(getattr(event, "alarm_popup_enabled", True)),
        "can_ack": status_display == "new" and event_alarm_eligible(event),
        "can_close": event_alarm_eligible(event) and event_alarm_active(event),
        "can_reopen": event_alarm_eligible(event) and status_display == "closed",
    }


from app.services.feedback_constants import PROBABLE_CAUSES


def build_events_payload(
    db: Session,
    *,
    camera_id: int | str | None = None,
    severity: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    assigned_user_id: int | str | None = None,
    sla_state: str | None = None,
    only_open: str | None = None,
    only_audit: str | None = None,
    date: str | None = None,
) -> dict:
    listing = list_events(
        db,
        camera_id=camera_id,
        status=status,
        event_type=event_type,
        assigned_user_id=assigned_user_id,
        date=date,
    )
    camera_map = get_camera_map(db)
    events = listing.events
    for event in events:
        enrich_event(event, camera_map)
        if event.status == "canceled":
            event.status_display = "canceled"

    if as_bool(only_open):
        events = [event for event in events if event_alarm_active(event)]
    if as_bool(only_audit):
        events = [
            event for event in events
            if event.status_display in {"audit", "low_priority"}
            or not event_alarm_active(event)
            or "audit" in str(getattr(event, "details", "")).lower()
        ]
    if severity in PRIORITY_CHOICES:
        events = [event for event in events if event.severity_display == severity]
    if sla_state in {"on_time", "at_risk", "overdue", "resolved", "untracked"}:
        events = [event for event in events if incident_sla_state(event) == sla_state]

    latest_alarm = next(
        (
            event
            for event in events
            if event_alarm_active(event)
            and event.severity_display in {"high", "critical"}
        ),
        None,
    )
    return {
        "events": events,
        "camera_map": camera_map,
        "distinct_event_types": listing.event_types,
        "probable_causes": PROBABLE_CAUSES,
        "latest_alarm_signature": (
            f"{latest_alarm.id}:{latest_alarm.status_display}"
            if latest_alarm
            else ""
        ),
        "alarm_should_play": bool(
            latest_alarm and latest_alarm.alarm_sound_enabled
        ),
        "latest_popup_alarm": latest_alarm,
    }


__all__ = [
    "as_bool",
    "build_events_payload",
    "parse_optional_int_filter",
    "serialize_event_for_table",
]
