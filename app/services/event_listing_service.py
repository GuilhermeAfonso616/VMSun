"""Consultas de eventos usadas pelas telas operacionais."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.timezone import brazil_date_bounds_as_utc_naive
from app.db.models import Event


@dataclass(frozen=True, slots=True)
class EventListingResult:
    events: list[Event]
    event_types: list[str]


def parse_optional_int_filter(value: int | str | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def list_events(
    db: Session,
    *,
    camera_id: int | str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    assigned_user_id: int | str | None = None,
    date: str | None = None,
    limit: int = 300,
) -> EventListingResult:
    """Aplica filtros persistidos e devolve eventos recentes e tipos disponíveis."""

    query = db.query(Event)
    selected_camera_id = parse_optional_int_filter(camera_id)
    if selected_camera_id is not None:
        query = query.filter(Event.camera_id == selected_camera_id)
    if event_type:
        query = query.filter(Event.event_type == event_type)
    selected_assignee = parse_optional_int_filter(assigned_user_id)
    if str(assigned_user_id or "").strip() == "unassigned":
        query = query.filter(Event.assigned_user_id.is_(None))
    elif selected_assignee is not None:
        query = query.filter(Event.assigned_user_id == selected_assignee)

    if status == "new":
        query = query.filter(
            or_(
                Event.status.is_(None),
                Event.status.in_(("new", "persisted", "failed")),
            )
        )
    elif status in {"acknowledged", "closed", "canceled"}:
        query = query.filter(Event.status == status)

    if date:
        try:
            start_utc, end_utc = brazil_date_bounds_as_utc_naive(str(date))
            query = query.filter(Event.created_at >= start_utc, Event.created_at < end_utc)
        except (TypeError, ValueError):
            # Filtros vindos da interface são opcionais; data inválida mantém a lista.
            pass

    events = query.order_by(Event.id.desc()).limit(limit).all()
    event_types = [
        row[0]
        for row in db.query(Event.event_type)
        .distinct()
        .order_by(Event.event_type.asc())
        .all()
        if row[0]
    ]
    return EventListingResult(events=events, event_types=event_types)
