from datetime import datetime
from types import SimpleNamespace

from app.db.models import Camera
from app.web import lockdown_presenter


def test_truncate_for_display_handles_empty_short_and_long_values():
    assert lockdown_presenter.truncate_for_display(None) == "-"
    assert lockdown_presenter.truncate_for_display("short", 10) == "short"
    assert lockdown_presenter.truncate_for_display("abcdefghij", 8) == "abcde..."


def test_payload_enriches_delivery_and_keeps_summary(monkeypatch):
    delivery = SimpleNamespace(
        camera_id=7,
        request_body="x" * 230,
        response_body=None,
        error_message="timeout",
        request_signature="s" * 40,
        created_at=datetime(2026, 7, 17, 12, 0),
        last_attempt_at=None,
        sent_at=None,
    )
    listing = SimpleNamespace(
        deliveries=[delivery],
        event_types=["intrusion"],
        total=3,
        sent=1,
        error=1,
        pending=1,
    )
    camera = Camera(
        id=7,
        name="Portaria",
        ip="10.0.0.7",
        username="operator",
        password="secret",
    )
    monkeypatch.setattr(
        lockdown_presenter,
        "list_lockdown_deliveries",
        lambda *_args, **_kwargs: listing,
    )
    monkeypatch.setattr(
        lockdown_presenter,
        "get_camera_map",
        lambda _db: {7: camera},
    )
    monkeypatch.setattr(
        lockdown_presenter,
        "format_dt",
        lambda value: value.isoformat(),
    )

    payload = lockdown_presenter.build_lockdown_deliveries_payload(object())

    assert delivery.camera_name == "Portaria"
    assert delivery.request_body_preview.endswith("...")
    assert len(delivery.request_signature_preview) == 32
    assert delivery.response_body_preview == "-"
    assert delivery.last_attempt_at_label == "-"
    assert payload["summary"] == {
        "total": 3,
        "sent": 1,
        "error": 1,
        "pending": 1,
    }
