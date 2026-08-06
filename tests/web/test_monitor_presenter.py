import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Event
from app.web import monitor_presenter


@pytest.fixture
def monitor_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        yield db
    engine.dispose()


def _camera(name="Portaria", **overrides):
    values = {
        "name": name,
        "ip": "10.0.0.10",
        "onvif_port": 80,
        "username": "operator",
        "password": "private-value",
        "rtsp_url": "rtsp://operator:private-value@10.0.0.10/main",
        "site_name": "Matriz",
        "group_name": "Entradas",
        "camera_priority": "high",
        "status": "idle",
        "is_deleted": False,
    }
    values.update(overrides)
    return Camera(**values)


def test_grid_helpers_preserve_supported_layout_contract():
    assert monitor_presenter.clamp_monitor_grid(1) == 1
    assert monitor_presenter.clamp_monitor_grid(25) == 25
    assert monitor_presenter.clamp_monitor_grid(3) == 4
    assert monitor_presenter.grid_columns_for(1) == 1
    assert monitor_presenter.grid_columns_for(9) == 3
    assert monitor_presenter.grid_columns_for(16) == 4
    assert monitor_presenter.grid_columns_for(25) == 5


def test_monitor_cache_reports_miss_hit_and_disables_client_cache():
    monitor_presenter._MONITOR_RESPONSE_CACHE.clear()
    calls = 0

    def build_payload():
        nonlocal calls
        calls += 1
        return {"sequence": calls}

    first = monitor_presenter._cached_monitor_json_response(
        ("test-monitor-cache",),
        build_payload,
        ttl_seconds=5,
    )
    second = monitor_presenter._cached_monitor_json_response(
        ("test-monitor-cache",),
        build_payload,
        ttl_seconds=5,
    )

    assert json.loads(first.body) == {"sequence": 1}
    assert json.loads(second.body) == {"sequence": 1}
    assert first.headers["X-Server-Cache"] == "MISS"
    assert second.headers["X-Server-Cache"] == "HIT"
    assert second.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert second.headers["Pragma"] == "no-cache"
    assert calls == 1


def test_monitor_cache_can_be_explicitly_bypassed():
    response = monitor_presenter._cached_monitor_json_response(
        ("test-monitor-bypass",),
        lambda: {"fresh": True},
        ttl_seconds=0,
    )

    assert json.loads(response.body) == {"fresh": True}
    assert response.headers["X-Server-Cache"] == "BYPASS"


def test_alarm_payload_keeps_queue_and_popup_signatures_stable(monitor_db):
    camera = _camera()
    monitor_db.add(camera)
    monitor_db.flush()
    active_alarm = Event(
        camera_id=camera.id,
        event_type="person_entered",
        confidence=0.94,
        severity="critical",
        status="new",
        alarm_eligible=True,
        is_alarm_active=True,
        created_at=datetime(2026, 7, 17, 12, 0),
    )
    monitor_db.add(active_alarm)
    monitor_db.commit()

    payload = monitor_presenter.build_monitor_alarm_payload(monitor_db)

    assert [event.id for event in payload["panel_alarms"]] == [active_alarm.id]
    assert payload["open_events_total"] == 1
    assert payload["alarm_queue_signature"] == f"{active_alarm.id}:new:critical"
    assert payload["latest_alarm_signature"] == f"{active_alarm.id}:new:alarm"
    assert payload["alarm_should_play"] is True
    assert payload["popup_should_show"] is True


def test_tracks_payload_normalizes_inputs_and_filters_low_confidence(monitor_db, monkeypatch):
    camera = _camera()
    monitor_db.add(camera)
    monitor_db.commit()
    observed = []

    def get_tracks(camera_id, max_age_seconds):
        observed.append((camera_id, max_age_seconds))
        return {
            "camera_id": camera_id,
            "tracks": [
                {"track_id": 1, "confidence": 0.91},
                {"track_id": 2, "confidence": 0.25},
                "invalid",
            ],
            "stale": False,
        }

    monkeypatch.setattr(monitor_presenter.track_store, "get_tracks", get_tracks)

    payload = monitor_presenter.build_monitor_tracks_payload(
        monitor_db,
        camera_ids=f"invalid,{camera.id},999",
        max_age_seconds=99,
        min_confidence=0.5,
    )

    assert payload["max_age_seconds"] == 5.0
    assert payload["min_confidence"] == 0.5
    assert observed == [(camera.id, 5.0), (999, 5.0)]
    assert payload["cameras"][str(camera.id)]["tracks"] == [
        {"track_id": 1, "confidence": 0.91}
    ]
    assert payload["cameras"]["999"]["tracks"] == [
        {"track_id": 1, "confidence": 0.91}
    ]


def test_monitor_camera_serialization_never_exposes_source_credentials(monkeypatch):
    camera = _camera()
    camera.id = 41
    camera.worker_mode = "stopped"
    camera.is_running = False
    camera.operational_health = {}
    camera.monitor_boxes = []
    monkeypatch.setattr(monitor_presenter, "webrtc_gateway_is_enabled", lambda: False)

    payload = monitor_presenter.serialize_monitor_camera(camera)
    serialized = json.dumps(payload)

    assert payload["id"] == 41
    assert payload["detail_url"] == "/cameras/41"
    assert "private-value" not in serialized
    assert "rtsp://" not in serialized
