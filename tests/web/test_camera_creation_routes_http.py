import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.camera.onvif_client import RTSPProfile
from app.db.base import Base
from app.db.models import Camera, User
from app.services.camera_discovery_service import CameraDiscovery
from app.web.infrastructure import get_web_user
from app.web.routes import camera_creation_routes


@pytest.fixture
def camera_creation_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(camera_creation_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=11,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(camera_creation_routes, "get_scoped_db", session_factory)
    monkeypatch.setattr(camera_creation_routes, "log_audit", lambda *_args, **_kwargs: None)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, session_factory=session_factory)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def test_manual_rtsp_camera_creation_preserves_web_defaults(camera_creation_context):
    response = camera_creation_context.client.post(
        "/cameras/new",
        data={
            "name": "Portaria",
            "ip": "10.0.0.10",
            "manufacturer": "Intelbras",
            "model": "VIP 1230",
            "onvif_port": "80",
            "username": "operator",
            "password": "secret",
            "rtsp_url": "rtsp://operator:secret@10.0.0.10/main",
            "camera_family": "bullet",
            "scene_category": "perimetral",
            "target_focus": "pessoa",
            "rain": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/cameras"
    with camera_creation_context.session_factory() as db:
        camera = db.query(Camera).one()
        assert camera.name == "Portaria"
        assert camera.manufacturer == "Intelbras"
        assert camera.model == "VIP 1230"
        assert camera.source_type == "camera"
        assert camera.analytics_coordinate_space == "display"


def test_manual_camera_creation_rejects_blank_manufacturer(camera_creation_context):
    response = camera_creation_context.client.post(
        "/cameras/new",
        data={
            "name": "Sem marca",
            "ip": "10.0.0.11",
            "manufacturer": "   ",
            "username": "operator",
            "password": "secret",
            "rtsp_url": "rtsp://operator:secret@10.0.0.11/main",
        },
    )

    assert response.status_code == 400
    assert "marca" in response.text.lower()
    with camera_creation_context.session_factory() as db:
        assert db.query(Camera).count() == 0


def test_multiple_profile_discovery_uses_server_side_secret_cache(camera_creation_context, monkeypatch):
    secret = "never-render-this"
    monkeypatch.setattr(
        camera_creation_routes,
        "discover_camera_streams",
        lambda **_kwargs: CameraDiscovery(
            rtsp_url=f"rtsp://operator:{secret}@10.0.0.20/main",
            onvif_port=80,
            profiles=[
                RTSPProfile(
                    token="main",
                    name="Principal",
                    rtsp_url=f"rtsp://operator:{secret}@10.0.0.20/main",
                    width=1920,
                    height=1080,
                ),
                RTSPProfile(
                    token="sub",
                    name="Secundaria",
                    rtsp_url=f"rtsp://operator:{secret}@10.0.0.20/sub",
                    width=640,
                    height=360,
                ),
            ],
            method="onvif",
        ),
    )

    discovered = camera_creation_context.client.post(
        "/cameras/new",
        data={
            "name": "Patio",
            "ip": "10.0.0.20",
            "manufacturer": "Hikvision",
            "model": "DS-2DE",
            "username": "operator",
            "password": secret,
            "rtsp_url": "",
        },
    )

    assert discovered.status_code == 200
    assert secret not in discovered.text
    assert 'name="credential_token"' in discovered.text
    assert 'name="profile_rtsp_url_0"' not in discovered.text
    token_match = re.search(r'name="credential_token" value="([^"]+)"', discovered.text)
    assert token_match

    confirmed = camera_creation_context.client.post(
        "/cameras/new/confirm",
        data={
            "credential_token": token_match.group(1),
            "profile_enabled_0": "true",
            "profile_name_0": "Patio principal",
            "profile_name_1": "Patio secundaria",
        },
        follow_redirects=False,
    )

    assert confirmed.status_code == 303
    with camera_creation_context.session_factory() as db:
        camera = db.query(Camera).one()
        assert camera.name == "Patio principal"
        assert camera.password == secret
        assert camera.rtsp_url.endswith("/main")


def test_expired_discovery_token_returns_safe_validation_error(camera_creation_context, monkeypatch):
    monkeypatch.setattr(camera_creation_routes, "get_camera_discovery", lambda _token: None)

    response = camera_creation_context.client.post(
        "/cameras/new/confirm",
        data={"credential_token": "expired-token", "profile_enabled_0": "true"},
    )

    assert response.status_code == 400
    assert "A descoberta expirou" in response.text


def test_rtsp_probe_masks_credentials_in_rendered_results(camera_creation_context, monkeypatch):
    monkeypatch.setattr(
        camera_creation_routes,
        "probe_rtsp_candidates",
        lambda _candidates: [
            {
                "url": "rtsp://operator:private-value@10.0.0.30/main",
                "masked_url": "",
                "ok": True,
                "error": "",
            }
        ],
    )

    response = camera_creation_context.client.post(
        "/cameras/rtsp-test",
        data={
            "ip": "10.0.0.30",
            "username": "operator",
            "password": "private-value",
        },
    )

    assert response.status_code == 200
    assert "private-value" not in response.text
    assert "rtsp://operator:***@10.0.0.30/main" in response.text
