import json
from datetime import timedelta
from urllib import error as urllib_error

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.credential_crypto import PREFIX
from app.db.base import Base
from app.db.models import Camera, Event, NotificationChannel
from app.services import notification_service
from app.core.timezone import utc_now_naive


@pytest.fixture
def notification_db():
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


def _seed(notification_db, *, max_attempts=2):
    camera = Camera(name="Portaria", ip="10.0.0.2", username="device", password="secret")
    channel = NotificationChannel(
        name="Operacao",
        kind="webhook",
        target="https://hooks.example.test/alarms?token=private",
        signing_secret="signing-private",
        enabled=True,
        min_severity="high",
        event_types_json=json.dumps(["intrusion"]),
        max_attempts=max_attempts,
        timeout_seconds=1,
    )
    notification_db.add_all([camera, channel])
    notification_db.commit()
    event = Event(
        camera_id=camera.id,
        event_type="intrusion",
        severity="critical",
        status="persisted",
        lifecycle_action="open",
        alarm_eligible=True,
        is_alarm_active=True,
    )
    notification_db.add(event)
    notification_db.commit()
    return camera, channel, event


def test_enqueue_is_filtered_idempotent_and_keeps_channel_secrets_encrypted(notification_db):
    _camera, channel, event = _seed(notification_db)

    first = notification_service.enqueue_event_notifications(event, notification_db)
    second = notification_service.enqueue_event_notifications(event, notification_db)

    assert len(first) == 1
    assert second == []
    stored_target, stored_secret = notification_db.execute(
        text("SELECT target, signing_secret FROM notification_channels WHERE id = :id"),
        {"id": channel.id},
    ).one()
    assert stored_target.startswith(PREFIX)
    assert stored_secret.startswith(PREFIX)


def test_webhook_delivery_signs_payload_and_marks_success(notification_db, monkeypatch):
    _camera, channel, event = _seed(notification_db)
    delivery = notification_service.enqueue_event_notifications(event, notification_db)[0]
    observed = {}

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"accepted":true}'

        def getcode(self):
            return self.status

    def fake_urlopen(request, timeout):
        observed["request"] = request
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(notification_service.urllib_request, "urlopen", fake_urlopen)
    result = notification_service.deliver_notification(delivery.id, notification_db)

    assert result.status == "sent"
    assert result.attempt_count == 1
    assert result.http_status == 202
    assert observed["request"].headers["X-analitico-signature"]
    assert observed["request"].headers["X-analitico-delivery"] == delivery.idempotency_key
    assert json.loads(observed["request"].data)["event"]["camera_name"] == "Portaria"


def test_delivery_retries_then_moves_to_dead_letter(notification_db, monkeypatch):
    _camera, _channel, event = _seed(notification_db, max_attempts=2)
    delivery = notification_service.enqueue_event_notifications(event, notification_db)[0]

    monkeypatch.setattr(
        notification_service.urllib_request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib_error.URLError("offline")),
    )
    first = notification_service.deliver_notification(delivery.id, notification_db)
    second = notification_service.deliver_notification(delivery.id, notification_db, force=True)

    assert first.status in {"retry", "dead"}
    assert second.status == "dead"
    assert second.attempt_count == 2
    assert second.next_attempt_at is None


def test_dispatcher_recovers_delivery_left_processing_by_restart(notification_db, monkeypatch):
    _camera, _channel, event = _seed(notification_db)
    delivery = notification_service.enqueue_event_notifications(event, notification_db)[0]
    delivery.status = "processing"
    delivery.last_attempt_at = utc_now_naive() - timedelta(minutes=10)
    notification_db.commit()

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b""

        def getcode(self):
            return self.status

    factory = sessionmaker(bind=notification_db.get_bind())
    monkeypatch.setattr(notification_service, "SessionLocal", factory)
    monkeypatch.setattr(notification_service.urllib_request, "urlopen", lambda *_args, **_kwargs: Response())

    assert notification_service.dispatch_due_notifications() == 1
    notification_db.expire_all()
    assert notification_db.get(type(delivery), delivery.id).status == "sent"
