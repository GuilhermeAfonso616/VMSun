import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Event, LockdownDelivery
from app.services import lockdown_delivery_service


@pytest.fixture
def lockdown_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        camera = Camera(
            name="Portaria",
            ip="10.0.0.10",
            username="operator",
            password="secret",
        )
        db.add(camera)
        db.flush()
        events = [
            Event(camera_id=camera.id, event_type="intrusion", status="new"),
            Event(camera_id=camera.id, event_type="loitering", status="new"),
        ]
        db.add_all(events)
        db.flush()
        deliveries = [
            LockdownDelivery(
                event_id=events[0].id,
                camera_id=camera.id,
                event_type="intrusion",
                target_url="https://example.test/events",
                status="sent",
            ),
            LockdownDelivery(
                event_id=events[1].id,
                camera_id=camera.id,
                event_type="loitering",
                target_url="https://example.test/events",
                status="error",
            ),
        ]
        db.add_all(deliveries)
        db.commit()
        yield db, camera, events, deliveries
    engine.dispose()


def test_listing_filters_and_calculates_summary_inside_selected_scope(lockdown_db):
    db, camera, events, _deliveries = lockdown_db

    result = lockdown_delivery_service.list_lockdown_deliveries(
        db,
        camera_id=camera.id,
        status="sent",
        event_id=events[0].id,
    )

    assert len(result.deliveries) == 1
    assert result.total == 1
    assert result.sent == 1
    assert result.error == 0
    assert result.pending == 0
    assert result.event_types == ["intrusion", "loitering"]


def test_invalid_status_does_not_apply_an_implicit_filter(lockdown_db):
    db, _camera, _events, _deliveries = lockdown_db

    result = lockdown_delivery_service.list_lockdown_deliveries(
        db,
        status="unknown",
    )

    assert result.total == 2
    assert result.sent == 1
    assert result.error == 1


def test_resend_delivery_delegates_to_ingest_service(lockdown_db, monkeypatch):
    db, _camera, _events, deliveries = lockdown_db
    observed = []
    monkeypatch.setattr(
        lockdown_delivery_service.lockdown_ingest_service,
        "send_lockdown_delivery",
        lambda delivery_id, received_db: observed.append(
            (delivery_id, received_db)
        ),
    )

    delivery = lockdown_delivery_service.resend_delivery(db, deliveries[0].id)

    assert delivery.id == deliveries[0].id
    assert observed == [(deliveries[0].id, db)]


def test_resend_delivery_reports_missing_record(lockdown_db):
    db, _camera, _events, _deliveries = lockdown_db

    with pytest.raises(lockdown_delivery_service.LockdownDeliveryNotFound):
        lockdown_delivery_service.resend_delivery(db, 99999)
