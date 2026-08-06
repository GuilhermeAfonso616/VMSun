from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Event
from app.services import event_service
from app.services.event_service import (
    EventServiceError,
    acknowledge_alarm_event,
    close_alarm_event,
    list_event_payloads,
    record_event_feedback,
    reopen_alarm_event,
    update_event_note,
    update_event_record,
)


@pytest.fixture
def event_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    camera = Camera(
        name="Entrada",
        ip="10.0.0.10",
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
        learning_mode="assisted_policy_tuning",
        is_deleted=False,
    )
    db.add(camera)
    db.commit()
    event = Event(
        camera_id=camera.id,
        event_type="person_entered_roi",
        status="new",
        alarm_eligible=True,
        is_alarm_active=True,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    try:
        yield db, camera, event
    finally:
        db.close()
        engine.dispose()


def test_event_status_transitions_and_serialization(event_db):
    db, _camera, event = event_db
    transition_time = datetime(2026, 7, 17, 12, 0, 0)

    update_event_record(
        db,
        event.id,
        status="acknowledged",
        operator_note="  validado  ",
        now=transition_time,
    )
    assert event.status == "acknowledged"
    assert event.acknowledged_at == transition_time
    assert event.operator_note == "validado"

    update_event_record(db, event.id, status="closed", operator_note=None, now=transition_time)
    assert event.status == "closed"
    assert event.is_alarm_active is False
    assert list_event_payloads(db)[0]["id"] == event.id


def test_web_alarm_transitions_share_event_invariants(event_db):
    db, _camera, event = event_db

    assert acknowledge_alarm_event(db, event.id) is True
    assert event.status == "acknowledged"
    assert close_alarm_event(db, event.id) is True
    assert event.status == "closed"
    assert reopen_alarm_event(db, event.id) is True
    assert event.status == "new"
    update_event_note(db, event.id, "  observacao  ")
    assert event.operator_note == "observacao"


def test_event_service_returns_typed_validation_errors(event_db):
    db, _camera, event = event_db

    with pytest.raises(EventServiceError) as invalid:
        update_event_record(db, event.id, status="canceled", operator_note=None)
    with pytest.raises(EventServiceError) as missing:
        update_event_record(db, 999, status="closed", operator_note=None)

    assert invalid.value.status_code == 400
    assert invalid.value.detail == "Status inválido"
    assert missing.value.status_code == 404


def test_record_event_feedback_orchestrates_optional_suggestions(event_db, monkeypatch):
    db, _camera, event = event_db
    calls = []
    monkeypatch.setattr(
        event_service,
        "record_feedback",
        lambda *_args, **_kwargs: SimpleNamespace(id=42),
    )
    monkeypatch.setattr(
        event_service,
        "generate_policy_suggestions",
        lambda *_args, **_kwargs: [SimpleNamespace(id=1), SimpleNamespace(id=2)],
    )
    monkeypatch.setattr(
        event_service,
        "maybe_apply_bounded_auto_tuning",
        lambda *_args, **_kwargs: calls.append("auto"),
    )

    result = record_event_feedback(
        db,
        event.id,
        label="true_positive",
        probable_cause=None,
        operator_note="ok",
        reviewed_by="operator",
        auto_suggest=True,
    )

    assert result.feedback_id == 42
    assert result.suggestions_created == 2
    assert calls == ["auto"]
