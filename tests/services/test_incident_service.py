from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.timezone import utc_now_naive
from app.db.base import Base
from app.db.models import Camera, Event, IncidentTimeline, User
from app.services.alarm_lifecycle import AlarmLifecycleService
from app.services import incident_service


@pytest.fixture
def incident_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _seed(incident_db):
    supervisor = User(username="supervisor", password_hash="hash", role="supervisor", is_active=True)
    operator = User(username="operator", password_hash="hash", role="operator", is_active=True)
    event = Event(
        camera_id=1,
        event_type="intrusion",
        severity="critical",
        status="new",
        lifecycle_action="open",
        alarm_eligible=True,
        is_alarm_active=True,
    )
    incident_db.add_all([supervisor, operator, event])
    incident_db.commit()
    return supervisor, operator, event


def test_incident_assignment_ack_resolution_reopen_and_timeline(incident_db):
    supervisor, operator, event = _seed(incident_db)

    incident_service.initialize_incident(event, incident_db)
    incident_service.assign_incident(incident_db, event.id, operator.id, supervisor)
    incident_service.acknowledge_incident(incident_db, event.id, operator)
    incident_service.add_incident_comment(incident_db, event.id, operator, "Equipe enviada")
    incident_service.close_incident(
        incident_db,
        event.id,
        supervisor,
        resolution_code="verified_threat",
        comment="Area protegida",
    )

    incident_db.refresh(event)
    assert event.status == "closed"
    assert event.assigned_username == "operator"
    assert event.resolution_code == "verified_threat"
    assert event.sla_due_at is not None
    assert incident_service.incident_sla_state(event) == "resolved"
    actions = [item.action for item in incident_service.incident_timeline(incident_db, event.id)]
    assert actions == ["created", "assigned", "acknowledged", "commented", "closed"]

    incident_service.reopen_incident(incident_db, event.id, supervisor, comment="Nova evidencia")
    incident_db.refresh(event)
    assert event.status == "new"
    assert event.resolution_code is None
    assert event.sla_due_at > utc_now_naive()


def test_overdue_incident_is_escalated_once_and_notified(incident_db, monkeypatch):
    _supervisor, _operator, event = _seed(incident_db)
    event.sla_due_at = utc_now_naive() - timedelta(minutes=1)
    incident_db.commit()
    notified = []
    monkeypatch.setattr(
        "app.services.notification_service.enqueue_event_notifications",
        lambda item, _db, **kwargs: notified.append((item.id, kwargs["notification_type"])),
    )

    first = incident_service.escalate_overdue_incidents(incident_db)
    second = incident_service.escalate_overdue_incidents(incident_db)

    assert [item.id for item in first] == [event.id]
    assert second == []
    assert notified == [(event.id, "incident_escalation")]
    assert incident_db.query(IncidentTimeline).filter(
        IncidentTimeline.event_id == event.id,
        IncidentTimeline.action == "sla_escalated",
    ).count() == 1


def test_persisted_pipeline_status_remains_an_open_incident(incident_db):
    _supervisor, _operator, event = _seed(incident_db)
    event.status = "persisted"
    event.is_alarm_active = True
    incident_db.commit()

    incident_service.initialize_incident(event, incident_db)
    summary = incident_service.incident_summary(incident_db)

    assert incident_service.incident_sla_state(event) in {"on_time", "at_risk"}
    assert summary["open"] == 1
    assert summary["unassigned"] == 1


def test_repeated_transitions_are_idempotent_and_operator_cannot_reopen(incident_db):
    supervisor, operator, event = _seed(incident_db)

    incident_service.assign_incident(incident_db, event.id, operator.id, supervisor)
    incident_service.assign_incident(incident_db, event.id, operator.id, supervisor)
    incident_service.close_incident(incident_db, event.id, supervisor, resolution_code="verified_threat")
    incident_service.close_incident(incident_db, event.id, supervisor, resolution_code="verified_threat")

    actions = [item.action for item in incident_service.incident_timeline(incident_db, event.id)]
    assert actions.count("assigned") == 1
    assert actions.count("closed") == 1
    with pytest.raises(incident_service.EventServiceError) as exc_info:
        incident_service.reopen_incident(incident_db, event.id, operator)
    assert exc_info.value.status_code == 403


def test_correlated_close_records_automatic_resolution(incident_db):
    _supervisor, _operator, event = _seed(incident_db)
    resolver = Event(camera_id=1, event_type="left_roi", status="closed")
    incident_db.add(resolver)
    incident_db.commit()

    closed = AlarmLifecycleService().finalize_related_alarm(
        incident_db,
        related_event_id=event.id,
        resolver_event_id=resolver.id,
    )
    incident_db.commit()

    assert closed.resolution_code == "automatic_clear"
    assert closed.status == "closed"
    entry = incident_db.query(IncidentTimeline).filter_by(event_id=event.id).one()
    assert entry.action == "auto_closed"


def test_close_assigns_logged_actor_automatically_and_requires_classification(incident_db):
    supervisor, _operator, event = _seed(incident_db)

    incident_service.close_incident(
        incident_db, event.id, supervisor, resolution_code="false_alarm"
    )
    incident_db.refresh(event)

    assert event.assigned_user_id == supervisor.id
    assert event.assigned_username == supervisor.username
    assert [
        item.action for item in incident_service.incident_timeline(incident_db, event.id)
    ] == ["created", "assigned", "closed"]

    another_event = Event(
        camera_id=1,
        event_type="intrusion",
        severity="critical",
        status="new",
        lifecycle_action="open",
        alarm_eligible=True,
        is_alarm_active=True,
    )
    incident_db.add(another_event)
    incident_db.commit()
    with pytest.raises(incident_service.EventServiceError) as missing_classification:
        incident_service.close_incident(
            incident_db, another_event.id, supervisor, resolution_code="resolved"
        )
    assert missing_classification.value.status_code == 400


def test_manual_incident_details_checklist_and_correlation(incident_db):
    supervisor, operator, related = _seed(incident_db)
    camera = Camera(name="Patio", ip="10.0.0.30", username="device", password="secret")
    incident_db.add(camera)
    incident_db.commit()

    manual = incident_service.create_manual_incident(
        incident_db,
        supervisor,
        camera_id=camera.id,
        title="Movimento suspeito",
        description="Acionado pela central",
        priority="high",
        team="Ronda A",
        assignee_user_id=operator.id,
    )
    incident_service.update_incident_details(
        incident_db, manual.id, supervisor, priority="critical", team="Ronda B"
    )
    incident_service.update_checklist_item(
        incident_db, manual.id, "verify_scene", True, operator
    )
    linked = incident_service.correlate_incident_events(
        incident_db, manual.id, [related.id], supervisor
    )

    incident_db.refresh(manual)
    incident_db.refresh(related)
    assert manual.incident_origin == "manual"
    assert manual.incident_priority == "critical"
    assert manual.incident_team == "Ronda B"
    assert incident_service.incident_checklist(manual)[0]["completed"] is True
    assert [item.id for item in linked] == [related.id]
    assert related.incident_parent_id == manual.id
    assert [item.id for item in incident_service.incident_related_events(incident_db, manual.id)] == sorted(
        [manual.id, related.id]
    )
