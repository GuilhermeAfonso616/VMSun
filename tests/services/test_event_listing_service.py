from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Event
from app.services.event_listing_service import (
    list_events,
    parse_optional_int_filter,
)


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _camera(name: str, ip: str) -> Camera:
    return Camera(name=name, ip=ip, username="operator", password="secret")


def test_parse_optional_int_filter_accepts_clean_integer_and_rejects_noise():
    assert parse_optional_int_filter(" 42 ") == 42
    assert parse_optional_int_filter(7) == 7
    assert parse_optional_int_filter("") is None
    assert parse_optional_int_filter("camera-7") is None


def test_list_events_applies_persisted_filters_and_keeps_global_type_choices():
    engine, factory = _session_factory()
    with factory() as db:
        camera_a = _camera("A", "10.0.0.1")
        camera_b = _camera("B", "10.0.0.2")
        db.add_all([camera_a, camera_b])
        db.flush()
        db.add_all(
            [
                Event(
                    camera_id=camera_a.id,
                    event_type="intrusion",
                    status="new",
                    created_at=datetime(2026, 7, 17, 12, 0),
                ),
                Event(
                    camera_id=camera_a.id,
                    event_type="loitering",
                    status="closed",
                    created_at=datetime(2026, 7, 17, 13, 0),
                ),
                Event(
                    camera_id=camera_b.id,
                    event_type="intrusion",
                    status="new",
                    created_at=datetime(2026, 7, 17, 14, 0),
                ),
            ]
        )
        db.commit()

        result = list_events(
            db,
            camera_id=str(camera_a.id),
            status="new",
            event_type="intrusion",
        )

    engine.dispose()
    assert len(result.events) == 1
    assert result.events[0].camera_id == camera_a.id
    assert result.event_types == ["intrusion", "loitering"]


def test_list_events_ignores_invalid_optional_date_and_respects_limit():
    engine, factory = _session_factory()
    with factory() as db:
        camera = _camera("A", "10.0.0.1")
        db.add(camera)
        db.flush()
        db.add_all(
            [
                Event(camera_id=camera.id, event_type="event-a", status="new"),
                Event(camera_id=camera.id, event_type="event-b", status="new"),
            ]
        )
        db.commit()

        result = list_events(db, date="not-a-date", limit=1)

    engine.dispose()
    assert len(result.events) == 1
    assert result.events[0].event_type == "event-b"
