from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Event, User
from app.web.infrastructure import get_web_user
from app.web.routes import camera_detail_routes


@pytest.fixture
def camera_detail_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        camera = Camera(
            name="Portaria",
            ip="10.0.0.10",
            onvif_port=80,
            username="operator",
            password="private-value",
            rtsp_url="rtsp://operator:private-value@10.0.0.10/main",
            is_deleted=False,
        )
        db.add(camera)
        db.flush()
        event = Event(
            camera_id=camera.id,
            event_type="person_entered",
            track_id=41,
            confidence=0.91,
            severity="high",
            status="new",
            snapshot_path="snapshot.jpg",
            created_at=datetime(2026, 7, 17, 12, 30),
        )
        db.add(event)
        db.commit()
        camera_id = camera.id
        event_id = event.id

    application = FastAPI()
    application.include_router(camera_detail_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=21,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(camera_detail_routes, "get_scoped_db", session_factory)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                client=client,
                camera_id=camera_id,
                event_id=event_id,
            )
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def test_detail_page_renders_camera_without_exposing_credentials(camera_detail_context):
    response = camera_detail_context.client.get(f"/cameras/{camera_detail_context.camera_id}")

    assert response.status_code == 200
    assert "Portaria" in response.text
    assert "private-value" not in response.text
    assert "rtsp://operator:***@10.0.0.10/main" in response.text


def test_events_data_preserves_contract_and_disables_cache(camera_detail_context):
    response = camera_detail_context.client.get(
        f"/cameras/{camera_detail_context.camera_id}/events-data"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    payload = response.json()
    assert payload["camera_id"] == camera_detail_context.camera_id
    assert payload["events"] == [
        {
            "id": camera_detail_context.event_id,
            "event_type": "person_entered",
            "event_type_label": "Pessoa detectada",
            "severity": "high",
            "severity_label": "Alta",
            "status": "new",
            "status_label": "Novo",
            "track_id": 41,
            "confidence": 0.91,
            "snapshot_url": f"/events/{camera_detail_context.event_id}/snapshot",
            "created_at_label": "17/07/2026 09:30:00",
            "lifecycle_action": "open",
            "alarm_category": None,
            "alarm_eligible": True,
            "is_alarm_active": True,
            "resolved_at": None,
            "can_ack": True,
            "can_close": True,
            "can_reopen": False,
        }
    ]


def test_rtsp_probe_masks_credentials_before_rendering(camera_detail_context, monkeypatch):
    monkeypatch.setattr(
        camera_detail_routes,
        "probe_rtsp_candidates",
        lambda _candidates: [
            {
                "url": "rtsp://operator:private-value@10.0.0.10/alternative",
                "masked_url": "",
                "ok": True,
                "error": "",
            }
        ],
    )

    response = camera_detail_context.client.post(
        f"/cameras/{camera_detail_context.camera_id}/rtsp-test"
    )

    assert response.status_code == 200
    assert "private-value" not in response.text
    assert "rtsp://operator:***@10.0.0.10/alternative" in response.text


def test_events_data_returns_404_for_unknown_camera(camera_detail_context):
    response = camera_detail_context.client.get("/cameras/99999/events-data")

    assert response.status_code == 404
    assert response.json()["detail"] == "Câmera não encontrada"
