from datetime import datetime
from types import SimpleNamespace

from app.db.models import Camera, Event
from app.services.event_listing_service import EventListingResult
from app.web import event_listing_presenter


def _event(**overrides) -> Event:
    values = {
        "id": 10,
        "camera_id": 3,
        "event_type": "entered_roi",
        "status": "new",
        "severity": "high",
        "confidence": 0.91,
        "details": "Pessoa detectada",
        "snapshot_path": "snapshot.jpg",
        "operator_note": None,
        "alarm_eligible": True,
        "is_alarm_active": True,
        "created_at": datetime(2026, 7, 17, 12, 0),
    }
    values.update(overrides)
    return Event(**values)


def test_build_events_payload_filters_severity_and_selects_latest_alarm(monkeypatch):
    high = _event(id=20, severity="high")
    low = _event(id=19, severity="low", event_type="left_roi")
    camera = Camera(
        id=3,
        name="Portaria",
        ip="10.0.0.3",
        username="operator",
        password="secret",
        alarm_sound_enabled=True,
    )
    observed = {}

    def list_events(_db, **kwargs):
        observed.update(kwargs)
        return EventListingResult(
            events=[high, low],
            event_types=["entered_roi", "left_roi"],
        )

    monkeypatch.setattr(event_listing_presenter, "list_events", list_events)
    monkeypatch.setattr(
        event_listing_presenter,
        "get_camera_map",
        lambda _db: {3: camera},
    )

    payload = event_listing_presenter.build_events_payload(
        object(),
        camera_id="3",
        severity="high",
        status="new",
        only_open="true",
        date="2026-07-17",
    )

    assert observed == {
        "camera_id": "3",
        "status": "new",
        "event_type": None,
        "assigned_user_id": None,
        "date": "2026-07-17",
    }
    assert payload["events"] == [high]
    assert payload["latest_alarm_signature"] == "20:new"
    assert payload["alarm_should_play"] is True
    assert payload["latest_popup_alarm"] is high


def test_severity_filter_is_ignored_when_value_is_not_supported(monkeypatch):
    event = _event()
    monkeypatch.setattr(
        event_listing_presenter,
        "list_events",
        lambda *_args, **_kwargs: EventListingResult(
            events=[event],
            event_types=["entered_roi"],
        ),
    )
    monkeypatch.setattr(
        event_listing_presenter,
        "get_camera_map",
        lambda _db: {},
    )

    payload = event_listing_presenter.build_events_payload(
        object(),
        severity="urgent",
    )

    assert payload["events"] == [event]


def test_serialize_event_for_table_preserves_actions_and_urls(monkeypatch):
    event = _event(status="closed", is_alarm_active=False, clip_path="clip.mp4")
    event.status_display = "closed"
    event.severity_display = "high"
    event.camera_name = "Portaria"
    event.site_name = "Matriz"
    event.lifecycle_action = "close"
    event.alarm_popup_enabled = False
    monkeypatch.setattr(
        event_listing_presenter,
        "event_clip_url",
        lambda _event: "/events/10/clip/video",
    )

    payload = event_listing_presenter.serialize_event_for_table(event)

    assert payload["camera_name"] == "Portaria"
    assert payload["confidence_label"] == "0.910000"
    assert payload["snapshot_url"] == "/events/10/snapshot"
    assert payload["clip_url"] == "/events/10/clip/video"
    assert payload["can_ack"] is False
    assert payload["can_close"] is False
    assert payload["can_reopen"] is True
    assert payload["alarm_popup_enabled"] is False


def test_boolean_query_parser_is_explicit():
    assert event_listing_presenter.as_bool("sim") is True
    assert event_listing_presenter.as_bool("ON") is True
    assert event_listing_presenter.as_bool("0") is False
    assert event_listing_presenter.as_bool(None) is False
