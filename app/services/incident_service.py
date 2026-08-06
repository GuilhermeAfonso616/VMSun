"""Workflow operacional de incidentes construído sobre alarmes persistidos."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timezone import utc_now_naive
from app.db.base import SessionLocal
from app.db.models import Camera, Event, IncidentTimeline, User
from app.services.event_service import EventServiceError, event_alarm_eligible, get_event


logger = get_logger("app.services.incidents")
ASSIGNABLE_ROLES = frozenset({"admin", "supervisor", "operator", "dev"})
RESOLUTION_CODES = frozenset(
    {
        "verified_threat",
        "false_alarm",
        "authorized_activity",
        "system_test",
        "automatic_clear",
        "other",
    }
)
INCIDENT_PRIORITIES = frozenset({"low", "medium", "high", "critical"})
DEFAULT_CHECKLIST = (
    ("verify_scene", "Verificar imagem, clipe e contexto da cena"),
    ("contact_site", "Confirmar a situacao com a equipe do local"),
    ("record_actions", "Registrar as acoes executadas"),
    ("preserve_evidence", "Preservar as evidencias relevantes"),
)


def _new_checklist() -> list[dict]:
    return [
        {"id": item_id, "label": label, "completed": False, "completed_by": None, "completed_at": None}
        for item_id, label in DEFAULT_CHECKLIST
    ]


def incident_checklist(event: Event) -> list[dict]:
    if event.incident_checklist:
        try:
            value = json.loads(event.incident_checklist)
            if isinstance(value, list):
                return value
        except (TypeError, ValueError):
            logger.warning("Invalid incident checklist event_id=%s", event.id)
    return _new_checklist()


def _comparable(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def sla_minutes_for(severity: str | None) -> int:
    normalized = str(severity or "medium").strip().lower()
    return max(
        1,
        int(
            {
                "critical": settings.incident_sla_critical_minutes,
                "high": settings.incident_sla_high_minutes,
                "medium": settings.incident_sla_medium_minutes,
                "low": settings.incident_sla_low_minutes,
            }.get(normalized, settings.incident_sla_medium_minutes)
        ),
    )


def calculate_sla_due(severity: str | None, opened_at: datetime | None = None) -> datetime:
    base = _comparable(opened_at) or utc_now_naive()
    return base + timedelta(minutes=sla_minutes_for(severity))


def incident_sla_state(event: Event, now: datetime | None = None) -> str:
    if str(event.status or "new") in {"closed", "canceled"}:
        return "resolved"
    due = _comparable(event.sla_due_at)
    if due is None:
        return "untracked"
    current = _comparable(now) or utc_now_naive()
    if due <= current:
        return "overdue"
    remaining = (due - current).total_seconds()
    priority = event.incident_priority or event.severity
    return "at_risk" if remaining <= max(300, sla_minutes_for(priority) * 60 * 0.25) else "on_time"


def record_timeline(
    db: Session,
    event: Event,
    action: str,
    *,
    actor: User | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    comment: str | None = None,
) -> IncidentTimeline:
    entry = IncidentTimeline(
        event_id=event.id,
        actor_user_id=actor.id if actor and actor.id else None,
        actor_username=actor.username if actor else "system",
        action=action,
        from_status=from_status,
        to_status=to_status,
        comment=(str(comment).strip()[:4000] or None) if comment is not None else None,
    )
    db.add(entry)
    return entry


def initialize_incident(event: Event, db: Session) -> bool:
    if not event_alarm_eligible(event) or not bool(event.is_alarm_active):
        return False
    changed = False
    if event.sla_due_at is None:
        event.sla_due_at = calculate_sla_due(event.incident_priority or event.severity, event.created_at or event.started_at)
        changed = True
    if not event.incident_priority:
        event.incident_priority = str(event.severity or "medium").lower()
        changed = True
    if not event.incident_checklist:
        event.incident_checklist = json.dumps(_new_checklist(), ensure_ascii=False)
        changed = True
    if not event.incident_origin:
        event.incident_origin = "automatic"
        changed = True
    exists = db.query(IncidentTimeline.id).filter(IncidentTimeline.event_id == event.id).first()
    if not exists:
        record_timeline(db, event, "created", to_status=str(event.status or "new"))
        changed = True
    if changed:
        db.commit()
        db.refresh(event)
    return changed


def assign_incident(db: Session, event_id: int, assignee_user_id: int | None, actor: User) -> Event:
    event = get_event(db, event_id)
    previous = event.assigned_username
    if assignee_user_id is None:
        event.assigned_user_id = None
        event.assigned_username = None
        action = "unassigned"
        comment = f"Responsavel anterior: {previous}" if previous else None
    else:
        assignee = db.get(User, assignee_user_id)
        if not assignee or not assignee.is_active or assignee.role not in ASSIGNABLE_ROLES:
            raise EventServiceError(400, "Responsavel invalido ou inativo.")
        if event.assigned_user_id == assignee.id:
            return event
        event.assigned_user_id = assignee.id
        event.assigned_username = assignee.username
        action = "assigned"
        comment = f"Responsavel: {assignee.username}"
    initialize_incident(event, db)
    record_timeline(db, event, action, actor=actor, comment=comment)
    db.commit()
    db.refresh(event)
    return event


def acknowledge_incident(db: Session, event_id: int, actor: User) -> Event:
    event = get_event(db, event_id)
    if not event_alarm_eligible(event):
        raise EventServiceError(400, "Este evento nao representa um incidente operacional.")
    previous = str(event.status or "new")
    if previous == "closed":
        raise EventServiceError(409, "Incidente fechado nao pode ser reconhecido.")
    initialize_incident(event, db)
    event.status = "acknowledged"
    event.acknowledged_at = event.acknowledged_at or utc_now_naive()
    if not event.assigned_user_id and actor.id:
        event.assigned_user_id = actor.id
        event.assigned_username = actor.username
    if previous != "acknowledged":
        record_timeline(db, event, "acknowledged", actor=actor, from_status=previous, to_status="acknowledged")
    db.commit()
    db.refresh(event)
    return event


def close_incident(
    db: Session,
    event_id: int,
    actor: User,
    *,
    resolution_code: str,
    comment: str | None = None,
) -> Event:
    event = get_event(db, event_id)
    if not event_alarm_eligible(event):
        raise EventServiceError(400, "Este evento nao representa um incidente operacional.")
    resolution = str(resolution_code or "").strip().lower()
    if resolution not in RESOLUTION_CODES:
        raise EventServiceError(400, "A classificacao final do incidente e obrigatoria.")
    previous = str(event.status or "new")
    if previous == "closed":
        return event
    initialize_incident(event, db)
    if not event.assigned_user_id:
        if not actor.id:
            raise EventServiceError(409, "Nao foi possivel identificar o usuario responsavel.")
        event.assigned_user_id = actor.id
        event.assigned_username = actor.username
        record_timeline(
            db,
            event,
            "assigned",
            actor=actor,
            comment="Responsavel atribuido automaticamente ao encerrar o incidente.",
        )
    now = utc_now_naive()
    event.status = "closed"
    event.acknowledged_at = event.acknowledged_at or now
    event.closed_at = now
    event.is_alarm_active = False
    event.resolution_code = resolution
    if comment is not None:
        event.operator_note = str(comment).strip()[:4000] or event.operator_note
    record_timeline(
        db,
        event,
        "closed",
        actor=actor,
        from_status=previous,
        to_status="closed",
        comment=f"Resolucao: {resolution}" + (f" — {comment.strip()}" if comment and comment.strip() else ""),
    )
    db.commit()
    db.refresh(event)
    return event


def create_manual_incident(
    db: Session,
    actor: User,
    *,
    camera_id: int,
    title: str,
    description: str | None = None,
    priority: str = "medium",
    team: str | None = None,
    assignee_user_id: int | None = None,
) -> Event:
    camera = db.get(Camera, camera_id)
    if not camera or bool(getattr(camera, "is_deleted", False)):
        raise EventServiceError(400, "Camera invalida para o incidente.")
    normalized_title = str(title or "").strip()
    if not normalized_title:
        raise EventServiceError(400, "Informe um titulo para o incidente.")
    normalized_priority = str(priority or "medium").strip().lower()
    if normalized_priority not in INCIDENT_PRIORITIES:
        raise EventServiceError(400, "Prioridade do incidente invalida.")
    if assignee_user_id is not None:
        assignee = db.get(User, assignee_user_id)
        if not assignee or not assignee.is_active or assignee.role not in ASSIGNABLE_ROLES:
            raise EventServiceError(400, "Responsavel invalido ou inativo.")
    event = Event(
        camera_id=camera.id,
        event_type="manual_incident",
        details=(normalized_title + (f" - {description.strip()}" if description and description.strip() else ""))[:4000],
        severity=normalized_priority,
        incident_priority=normalized_priority,
        incident_team=str(team or "").strip()[:160] or None,
        incident_origin="manual",
        status="new",
        lifecycle_action="open",
        alarm_eligible=True,
        is_alarm_active=True,
        started_at=utc_now_naive(),
    )
    db.add(event)
    db.flush()
    event.correlation_key = f"incident:{event.id}"
    event.incident_parent_id = event.id
    initialize_incident(event, db)
    if assignee_user_id is not None:
        assign_incident(db, event.id, assignee_user_id, actor)
    record_timeline(db, event, "manual_created", actor=actor, comment=normalized_title)
    db.commit()
    db.refresh(event)
    return event


def update_incident_details(
    db: Session,
    event_id: int,
    actor: User,
    *,
    priority: str | None = None,
    team: str | None = None,
) -> Event:
    event = get_event(db, event_id)
    initialize_incident(event, db)
    changes: list[str] = []
    if priority is not None:
        normalized_priority = str(priority).strip().lower()
        if normalized_priority not in INCIDENT_PRIORITIES:
            raise EventServiceError(400, "Prioridade do incidente invalida.")
        if event.incident_priority != normalized_priority:
            event.incident_priority = normalized_priority
            event.sla_due_at = calculate_sla_due(normalized_priority, event.created_at or event.started_at)
            changes.append(f"prioridade={normalized_priority}")
    if team is not None:
        normalized_team = str(team).strip()[:160] or None
        if event.incident_team != normalized_team:
            event.incident_team = normalized_team
            changes.append(f"equipe={normalized_team or 'nenhuma'}")
    if changes:
        record_timeline(db, event, "details_updated", actor=actor, comment=", ".join(changes))
        db.commit()
        db.refresh(event)
    return event


def update_checklist_item(
    db: Session,
    event_id: int,
    item_id: str,
    completed: bool,
    actor: User,
) -> Event:
    event = get_event(db, event_id)
    initialize_incident(event, db)
    items = incident_checklist(event)
    target = next((item for item in items if str(item.get("id")) == str(item_id)), None)
    if target is None:
        raise EventServiceError(404, "Item do checklist nao encontrado.")
    if bool(target.get("completed")) == bool(completed):
        return event
    target["completed"] = bool(completed)
    target["completed_by"] = actor.username if completed else None
    target["completed_at"] = utc_now_naive().isoformat() if completed else None
    event.incident_checklist = json.dumps(items, ensure_ascii=False)
    record_timeline(
        db,
        event,
        "checklist_updated",
        actor=actor,
        comment=f"{target.get('label')}: {'concluido' if completed else 'reaberto'}",
    )
    db.commit()
    db.refresh(event)
    return event


def correlate_incident_events(
    db: Session,
    incident_id: int,
    event_ids: list[int],
    actor: User,
) -> list[Event]:
    root = get_event(db, incident_id)
    if not event_alarm_eligible(root):
        raise EventServiceError(400, "O evento raiz nao representa um incidente operacional.")
    initialize_incident(root, db)
    correlation_key = root.correlation_key or f"incident:{root.id}"
    root.correlation_key = correlation_key
    root.incident_parent_id = root.id
    linked: list[Event] = []
    for candidate_id in dict.fromkeys(int(value) for value in event_ids):
        if candidate_id == root.id:
            continue
        candidate = get_event(db, candidate_id)
        candidate.correlation_key = correlation_key
        candidate.related_event_id = root.id
        candidate.incident_parent_id = root.id
        linked.append(candidate)
        record_timeline(db, root, "event_linked", actor=actor, comment=f"Evento #{candidate.id} vinculado")
    db.commit()
    return linked


def incident_related_events(db: Session, event_id: int) -> list[Event]:
    event = get_event(db, event_id)
    root_id = event.incident_parent_id or event.related_event_id or event.id
    root = get_event(db, root_id)
    key = root.correlation_key
    filters = [Event.id == root_id, Event.related_event_id == root_id, Event.incident_parent_id == root_id]
    if key:
        filters.append(Event.correlation_key == key)
    return db.query(Event).filter(or_(*filters)).order_by(Event.id.asc()).all()


def reopen_incident(db: Session, event_id: int, actor: User, *, comment: str | None = None) -> Event:
    event = get_event(db, event_id)
    if not event_alarm_eligible(event):
        raise EventServiceError(400, "Este evento nao representa um incidente operacional.")
    if actor.role not in {"admin", "supervisor"}:
        raise EventServiceError(403, "Apenas supervisores podem reabrir incidentes.")
    previous = str(event.status or "closed")
    if previous != "closed":
        return event
    event.status = "new"
    event.closed_at = None
    event.resolution_code = None
    event.is_alarm_active = True
    event.escalated_at = None
    event.sla_due_at = calculate_sla_due(event.incident_priority or event.severity)
    record_timeline(db, event, "reopened", actor=actor, from_status=previous, to_status="new", comment=comment)
    db.commit()
    db.refresh(event)
    return event


def add_incident_comment(db: Session, event_id: int, actor: User, comment: str) -> Event:
    event = get_event(db, event_id)
    normalized = str(comment or "").strip()
    if not normalized:
        raise EventServiceError(400, "O comentario nao pode ficar vazio.")
    event.operator_note = normalized[:4000]
    record_timeline(db, event, "commented", actor=actor, comment=normalized)
    db.commit()
    db.refresh(event)
    return event


def incident_timeline(db: Session, event_id: int) -> list[IncidentTimeline]:
    get_event(db, event_id)
    return (
        db.query(IncidentTimeline)
        .filter(IncidentTimeline.event_id == event_id)
        .order_by(IncidentTimeline.id.asc())
        .all()
    )


def incident_summary(db: Session) -> dict:
    now = utc_now_naive()
    open_filter = or_(
        Event.is_alarm_active.is_(True),
        Event.status.is_(None),
        Event.status.in_(("new", "acknowledged", "persisted", "failed")),
    )
    base = db.query(Event).filter(Event.alarm_eligible.is_(True), open_filter)
    closed_events = db.query(Event).filter(
        Event.alarm_eligible.is_(True),
        Event.status == "closed",
        Event.closed_at.is_not(None),
    ).all()
    ack_minutes = [
        max(0.0, (_comparable(item.acknowledged_at) - _comparable(item.created_at or item.started_at)).total_seconds() / 60)
        for item in closed_events
        if item.acknowledged_at and (item.created_at or item.started_at)
    ]
    resolution_minutes = [
        max(0.0, (_comparable(item.closed_at) - _comparable(item.created_at or item.started_at)).total_seconds() / 60)
        for item in closed_events
        if item.closed_at and (item.created_at or item.started_at)
    ]
    return {
        "open": base.count(),
        "unassigned": base.filter(Event.assigned_user_id.is_(None)).count(),
        "overdue": base.filter(Event.sla_due_at.is_not(None), Event.sla_due_at <= now).count(),
        "escalated": base.filter(Event.escalated_at.is_not(None)).count(),
        "closed": len(closed_events),
        "mean_acknowledge_minutes": round(sum(ack_minutes) / len(ack_minutes), 1) if ack_minutes else None,
        "mean_resolution_minutes": round(sum(resolution_minutes) / len(resolution_minutes), 1) if resolution_minutes else None,
    }


def backfill_incident_state(db: Session) -> int:
    events = db.query(Event).filter(
        Event.alarm_eligible.is_(True),
        Event.sla_due_at.is_(None),
        or_(
            Event.is_alarm_active.is_(True),
            Event.status.is_(None),
            Event.status.in_(("new", "acknowledged", "persisted", "failed")),
        ),
    ).all()
    for event in events:
        event.sla_due_at = calculate_sla_due(event.severity, event.created_at or event.started_at)
        if event.is_alarm_active is None:
            event.is_alarm_active = True
    if events:
        db.commit()
    return len(events)


def escalate_overdue_incidents(db: Session) -> list[Event]:
    now = utc_now_naive()
    events = db.query(Event).filter(
        Event.alarm_eligible.is_(True),
        Event.is_alarm_active.is_(True),
        Event.escalated_at.is_(None),
        Event.sla_due_at.is_not(None),
        Event.sla_due_at <= now,
    ).all()
    for event in events:
        event.escalated_at = now
        record_timeline(db, event, "sla_escalated", comment="Prazo de atendimento excedido.")
    if events:
        db.commit()
        from app.services.notification_service import enqueue_event_notifications

        for event in events:
            try:
                enqueue_event_notifications(event, db, notification_type="incident_escalation")
            except Exception:
                db.rollback()
                logger.exception("Incident escalation notification failed event_id=%s", event.id)
    return events


class IncidentSlaMonitor:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="incident-sla-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            db = SessionLocal()
            try:
                backfill_incident_state(db)
                escalate_overdue_incidents(db)
            except Exception:
                db.rollback()
                logger.exception("Incident SLA monitor cycle failed")
            finally:
                db.close()
            self._stop.wait(max(1.0, float(settings.incident_sla_monitor_seconds)))


incident_sla_monitor = IncidentSlaMonitor()
