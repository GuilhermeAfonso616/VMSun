from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import User
from app.web.infrastructure import get_web_user
from app.web.routes import event_listing_routes, lockdown_routes


@pytest.fixture
def event_lockdown_http(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    role = {"value": "admin"}
    application = FastAPI()
    application.include_router(event_listing_routes.router)
    application.include_router(lockdown_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=61,
        username="event-user",
        role=role["value"],
        is_active=True,
    )
    monkeypatch.setattr(event_listing_routes, "get_scoped_db", factory)
    monkeypatch.setattr(lockdown_routes, "get_scoped_db", factory)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, role=role)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def _capture_template(monkeypatch, module):
    captured = []

    def render(*, request, name, context, status_code=200):
        captured.append({"name": name, "context": context})
        return PlainTextResponse(name, status_code=status_code)

    monkeypatch.setattr(module.templates, "TemplateResponse", render)
    return captured


def test_events_data_forwards_all_filters_and_preserves_json_contract(
    event_lockdown_http,
    monkeypatch,
):
    observed = {}
    event = SimpleNamespace(id=11)

    def build(_db, **kwargs):
        observed.update(kwargs)
        return {
            "events": [event],
            "latest_alarm_signature": "11:new",
            "alarm_should_play": True,
            "latest_popup_alarm": event,
        }

    monkeypatch.setattr(event_listing_routes, "build_events_payload", build)
    monkeypatch.setattr(
        event_listing_routes,
        "serialize_event_for_table",
        lambda item: {"id": item.id},
    )

    response = event_lockdown_http.client.get(
        "/events/data?camera_id=3&severity=high&status=new"
        "&event_type=intrusion&only_open=1&date=2026-07-17"
    )

    assert response.status_code == 200
    assert response.json() == {
        "events": [{"id": 11}],
        "latest_alarm_signature": "11:new",
        "alarm_should_play": True,
        "latest_popup_alarm": {"id": 11},
    }
    assert observed == {
        "camera_id": "3",
        "severity": "high",
        "status": "new",
        "event_type": "intrusion",
        "assigned_user_id": None,
        "sla_state": None,
        "only_open": "1",
        "only_audit": None,
        "date": "2026-07-17",
    }


def test_events_page_builds_context_and_rejects_viewer(
    event_lockdown_http,
    monkeypatch,
):
    captured = _capture_template(monkeypatch, event_listing_routes)
    monkeypatch.setattr(
        event_listing_routes,
        "build_events_payload",
        lambda *_args, **_kwargs: {
            "events": [],
            "camera_map": {},
            "distinct_event_types": ["intrusion"],
            "latest_alarm_signature": "",
            "alarm_should_play": False,
            "latest_popup_alarm": None,
        },
    )
    monkeypatch.setattr(
        event_listing_routes,
        "load_revalidator_policy",
        lambda: {"mode": "block"},
    )

    response = event_lockdown_http.client.get(
        "/events?camera_id=9&only_open=sim&severity=high"
    )

    assert response.status_code == 200
    assert captured[0]["name"] == "events.html"
    assert captured[0]["context"]["selected_camera_id"] == 9
    assert captured[0]["context"]["only_open"] is True
    assert captured[0]["context"]["person_revalidator_cancel_enabled"] is True

    event_lockdown_http.role["value"] = "viewer"
    denied = event_lockdown_http.client.get("/events", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/"


def test_event_review_forwards_filters_to_learning_service(
    event_lockdown_http,
    monkeypatch,
):
    captured = _capture_template(monkeypatch, event_listing_routes)
    observed = {}

    def build_review(_db, **kwargs):
        observed.update(kwargs)
        return {
            "events": [],
            "metrics": {},
            "cameras": [],
            "labels": [],
            "probable_causes": [],
            "profile_options": [],
            "turn_options": [],
            "learning_mode_counts": {},
            "suggestions": [],
            "active_learning_queue": [],
            "drift": {},
            "loaded_event_limit": 25,
            "ai_validated_count": 0,
            "ai_validated_by_label": {},
            "include_ai_validated": False,
        }

    monkeypatch.setattr(event_listing_routes, "build_event_review_payload", build_review)
    monkeypatch.setattr(
        event_listing_routes,
        "build_revalidator_dataset_summary",
        lambda: {"samples": 4},
    )

    response = event_lockdown_http.client.get(
        "/events/review?camera_id=4&label=false_positive&days=7&limit=25"
    )

    assert response.status_code == 200
    assert captured[0]["name"] == "event_validation.html"
    assert captured[0]["context"]["revalidator_dataset"] == {"samples": 4}
    assert observed["camera_id"] == 4
    assert observed["label"] == "false_positive"
    assert observed["days"] == 7
    assert observed["limit"] == 25


def test_lockdown_page_normalizes_filters_and_builds_context(
    event_lockdown_http,
    monkeypatch,
):
    captured = _capture_template(monkeypatch, lockdown_routes)
    observed = {}

    def build(_db, **kwargs):
        observed.update(kwargs)
        return {
            "deliveries": [],
            "cameras": [],
            "event_types": ["intrusion"],
            "summary": {"total": 0, "sent": 0, "error": 0, "pending": 0},
        }

    monkeypatch.setattr(lockdown_routes, "build_lockdown_deliveries_payload", build)
    monkeypatch.setattr(
        lockdown_routes,
        "load_lockdown_policy",
        lambda: {"allowed_trigger_events": ["intrusion"]},
    )

    response = event_lockdown_http.client.get(
        "/lockdown-deliveries?camera_id=5&status=sent&event_id=17"
    )

    assert response.status_code == 200
    assert observed == {
        "camera_id": 5,
        "status": "sent",
        "event_type": None,
        "event_id": 17,
    }
    assert captured[0]["name"] == "lockdown_deliveries.html"
    assert captured[0]["context"]["selected_status"] == "sent"
    assert captured[0]["context"]["selected_trigger_event_types"] == [
        "intrusion"
    ]


def test_lockdown_policy_filters_unknown_values_and_redirects_to_referer(
    event_lockdown_http,
    monkeypatch,
):
    saved = []
    monkeypatch.setattr(
        lockdown_routes,
        "LOCKDOWN_TRIGGER_EVENT_CHOICES",
        ["intrusion", "loitering"],
    )
    monkeypatch.setattr(
        lockdown_routes,
        "save_lockdown_policy",
        lambda values: saved.append(values),
    )

    response = event_lockdown_http.client.post(
        "/lockdown-deliveries/policy",
        data={"trigger_event_types": ["intrusion", "unknown"]},
        headers={"referer": "/lockdown-deliveries?status=sent"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/lockdown-deliveries?status=sent"
    assert saved == [["intrusion"]]


def test_lockdown_resend_maps_service_result_and_missing_record(
    event_lockdown_http,
    monkeypatch,
):
    monkeypatch.setattr(
        lockdown_routes,
        "resend_delivery",
        lambda _db, delivery_id: SimpleNamespace(id=delivery_id, event_id=22),
    )

    response = event_lockdown_http.client.post(
        "/lockdown-deliveries/8/resend",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/lockdown-deliveries"

    def missing(_db, _delivery_id):
        raise lockdown_routes.LockdownDeliveryNotFound()

    monkeypatch.setattr(lockdown_routes, "resend_delivery", missing)
    not_found = event_lockdown_http.client.post(
        "/lockdown-deliveries/999/resend"
    )
    assert not_found.status_code == 404
    assert not_found.json()["detail"] == "Envio não encontrado"
