import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Camera, Event, EventFeedback, TuningSuggestion
from app.services.event_service import (
    get_tuning_summary_payload,
    list_audit_queue_payloads,
    record_event_feedback,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_list_audit_queue_payloads(db_session):
    cam = Camera(name="Cam Entrada", ip="192.168.1.10", username="admin", password="secretpassword", status="online")
    db_session.add(cam)
    db_session.commit()

    # Evento normal ativo
    e1 = Event(camera_id=cam.id, event_type="person_entered_roi", status="new", is_alarm_active=True)
    # Evento de auditoria silenciosa
    e2 = Event(camera_id=cam.id, event_type="person_loitering", status="audit", is_alarm_active=False)
    # Evento com baixa prioridade
    e3 = Event(camera_id=cam.id, event_type="crossed_line", status="low_priority", is_alarm_active=False)

    db_session.add_all([e1, e2, e3])
    db_session.commit()

    audit_payloads = list_audit_queue_payloads(db_session)
    event_ids = {item["id"] for item in audit_payloads}

    assert e2.id in event_ids
    assert e3.id in event_ids
    assert e1.id not in event_ids


def test_get_tuning_summary_payload(db_session):
    cam = Camera(name="Cam Perimetro", ip="192.168.1.20", username="admin", password="secretpassword", status="online")
    db_session.add(cam)
    db_session.commit()

    e1 = Event(camera_id=cam.id, event_type="person_entered_roi", status="closed")
    db_session.add(e1)
    db_session.commit()

    # Registrar feedback com causa provavel
    fb = EventFeedback(
        event_id=e1.id,
        camera_id=cam.id,
        label="false_positive",
        probable_cause="vegetation_wind",
        operator_note="Vento em arvores ao fundo",
    )
    sug = TuningSuggestion(
        camera_id=cam.id,
        scope_type="camera",
        scope_id=str(cam.id),
        suggestion_type="raise_threshold",
        parameter_name="person_confidence_min",
    )
    db_session.add_all([fb, sug])
    db_session.commit()

    summary = get_tuning_summary_payload(db_session, camera_id=cam.id)

    assert summary["total_feedbacks"] == 1
    assert summary["probable_cause_counts"].get("vegetation_wind") == 1
    assert len(summary["suggestions"]) == 1
    assert summary["suggestions"][0]["parameter_name"] == "person_confidence_min"
