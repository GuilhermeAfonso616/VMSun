from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, User
from app.video_sources.models import StreamProfile
from app.web.infrastructure import get_web_user
from app.web.routes import nvr_routes


@pytest.fixture
def nvr_web_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(nvr_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=7,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(nvr_routes, "get_scoped_db", session_factory)
    monkeypatch.setattr(nvr_routes, "build_nvr_channel_health", lambda _db: [])
    monkeypatch.setattr(nvr_routes, "log_audit", lambda *_args, **_kwargs: None)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, session_factory=session_factory)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def _discovered_profile(password: str = "secret") -> StreamProfile:
    raw_url = f"rtsp://admin:{password}@nvr.local/cam/realmonitor?channel=1&subtype=0"
    return StreamProfile(
        provider_type="generic_nvr",
        source_brand="dahua",
        channel=1,
        stream_kind="main",
        name="Canal 1 main",
        rtsp_url=raw_url,
        masked_rtsp_url="rtsp://admin:***@nvr.local/cam/realmonitor?channel=1&subtype=0",
        ok=True,
    )


def test_web_nvr_discovery_keeps_password_out_of_html(nvr_web_context, monkeypatch):
    monkeypatch.setattr(
        nvr_routes,
        "discover_nvr_channels",
        lambda **_kwargs: SimpleNamespace(profiles=[_discovered_profile("never-render-me")]),
    )
    monkeypatch.setattr(nvr_routes, "store_nvr_discovery_cache", lambda **_kwargs: "safe-token")

    response = nvr_web_context.client.post(
        "/video-sources/nvr/discover",
        data={
            "host": "nvr.local",
            "username": "admin",
            "password": "never-render-me",
            "brand": "dahua",
            "channel_count": "1",
            "stream_kinds": "main",
        },
    )

    assert response.status_code == 200
    assert "safe-token" in response.text
    assert "never-render-me" not in response.text
    assert "rtsp://admin:***@nvr.local" in response.text


def test_web_nvr_discovery_rejects_empty_host_without_echoing_password(nvr_web_context):
    response = nvr_web_context.client.post(
        "/video-sources/nvr/discover",
        data={"host": "", "username": "admin", "password": "private-value"},
    )

    assert response.status_code == 400
    assert "Informe o IP/host do NVR" in response.text
    assert "private-value" not in response.text


def test_web_nvr_creation_uses_server_side_credentials_and_persists_channel(nvr_web_context, monkeypatch):
    profile = _discovered_profile("cached-secret")
    monkeypatch.setattr(
        nvr_routes,
        "get_nvr_discovery_cache",
        lambda token: {
            "host": "nvr.local",
            "username": "admin",
            "password": "cached-secret",
            "profiles": {0: {"rtsp_url": profile.rtsp_url}},
        }
        if token == "valid-token"
        else None,
    )

    response = nvr_web_context.client.post(
        "/video-sources/nvr/create",
        data={
            "host": "ignored.local",
            "username": "ignored",
            "password": "",
            "credential_token": "valid-token",
            "brand": "dahua",
            "provider_type": "generic_nvr",
            "base_name": "Matriz",
            "selected_profile": "0",
            "profile_count": "1",
            "profile_channel_0": "3",
            "profile_stream_kind_0": "main",
            "profile_camera_name_0": "Portaria",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/video-sources/nvr?created=1&skipped=0"
    with nvr_web_context.session_factory() as db:
        camera = db.query(Camera).one()
        assert camera.name == "Portaria"
        assert camera.ip == "nvr.local"
        assert camera.password == "cached-secret"
        assert camera.source_type == "nvr_channel"
        assert camera.source_channel == 3
        assert camera.analytics_coordinate_space == "display"


def test_web_nvr_creation_is_idempotent(nvr_web_context, monkeypatch):
    profile = _discovered_profile()
    cache = {
        "host": "nvr.local",
        "username": "admin",
        "password": "secret",
        "profiles": {0: {"rtsp_url": profile.rtsp_url}},
    }
    monkeypatch.setattr(nvr_routes, "get_nvr_discovery_cache", lambda _token: cache)
    form = {
        "credential_token": "token",
        "brand": "dahua",
        "provider_type": "generic_nvr",
        "base_name": "Matriz",
        "selected_profile": "0",
        "profile_count": "1",
        "profile_channel_0": "1",
        "profile_stream_kind_0": "main",
    }

    first = nvr_web_context.client.post("/video-sources/nvr/create", data=form, follow_redirects=False)
    second = nvr_web_context.client.post("/video-sources/nvr/create", data=form, follow_redirects=False)

    assert first.headers["location"].endswith("created=1&skipped=0")
    assert second.headers["location"].endswith("created=0&skipped=1")
    with nvr_web_context.session_factory() as db:
        assert db.query(Camera).count() == 1


def test_web_nvr_discovery_job_pause_status_snapshot(nvr_web_context, monkeypatch):
    job = nvr_routes.nvr_discovery_job_manager.create(
        owner_user_id=7,
        host="192.168.1.100",
        username="admin",
        password="password",
        form_values={"brand": "dahua", "probe": True},
        profiles=[_discovered_profile()],
        probe_enabled=False,
    )
    job.candidates[0].status = "ok"

    status_resp = nvr_web_context.client.get(f"/video-sources/nvr/discover/{job.token}/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["discovered"][0]["name"] == "Canal 1 main"
    assert data["discovered"][0]["status"] == "ok"

    job.status = "running"
    pause_resp = nvr_web_context.client.post(f"/video-sources/nvr/discover/{job.token}/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

