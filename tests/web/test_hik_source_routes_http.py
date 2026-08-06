from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, User
from app.web.infrastructure import get_web_user
from app.web.routes import hik_source_routes


@pytest.fixture
def hik_http(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    role = {"value": "admin"}
    application = FastAPI()
    application.include_router(hik_source_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=44,
        username="hik-admin",
        role=role["value"],
        is_active=True,
    )
    monkeypatch.setattr(hik_source_routes, "get_scoped_db", session_factory)
    monkeypatch.setattr(hik_source_routes, "_audit_discovery", lambda *_args: None)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                client=client,
                session_factory=session_factory,
                role=role,
            )
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def _capture_templates(monkeypatch):
    captured = []

    def render(*, request, name, context, status_code=200):
        captured.append(
            {
                "name": name,
                "context": context,
                "status_code": status_code,
            }
        )
        return PlainTextResponse(
            str(
                {
                    "error": context.get("error"),
                    "message": context.get("message"),
                    "form_values": context.get("form_values"),
                    "profiles": context.get("profiles"),
                }
            ),
            status_code=status_code,
        )

    monkeypatch.setattr(hik_source_routes.templates, "TemplateResponse", render)
    monkeypatch.setattr(
        hik_source_routes,
        "build_hikcentral_channel_health",
        lambda _db: [],
    )
    monkeypatch.setattr(
        hik_source_routes,
        "build_hikconnect_channel_health",
        lambda _db: [],
    )
    return captured


def test_source_pages_preserve_admin_supervisor_permissions(hik_http, monkeypatch):
    captured = _capture_templates(monkeypatch)
    response = hik_http.client.get("/video-sources/hikcentral?created=2&skipped=1")

    assert response.status_code == 200
    assert captured[0]["name"] == "hikcentral_sources.html"
    assert captured[0]["context"]["created"] == 2
    assert captured[0]["context"]["skipped"] == 1

    hik_http.role["value"] = "operator"
    denied = hik_http.client.get(
        "/video-sources/hikconnect",
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert denied.headers["location"] == "/"


def test_hikcentral_discovery_caches_secret_but_never_returns_it(
    hik_http,
    monkeypatch,
):
    captured = _capture_templates(monkeypatch)
    cache_args = {}
    monkeypatch.setattr(
        hik_source_routes,
        "discover_hikcentral_cameras",
        lambda **_kwargs: [
            {
                "cameraIndexCode": "cam-01",
                "cameraName": "Portaria",
                "channelNo": 1,
                "status": "online",
            }
        ],
    )
    monkeypatch.setattr(
        hik_source_routes,
        "store_nvr_discovery_cache",
        lambda **kwargs: cache_args.update(kwargs) or "safe-token",
    )

    response = hik_http.client.post(
        "/video-sources/hikcentral/discover",
        data={
            "host": "10.0.0.20",
            "username": "app-key",
            "password": "private-secret",
            "base_name": "Matriz",
            "simulate": "on",
        },
    )

    assert response.status_code == 200
    assert cache_args["password"] == "private-secret"
    assert captured[0]["context"]["credential_token"] == "safe-token"
    assert captured[0]["context"]["form_values"]["password"] == ""
    assert "private-secret" not in response.text


def test_failed_discovery_does_not_echo_password(hik_http, monkeypatch):
    captured = _capture_templates(monkeypatch)
    monkeypatch.setattr(
        hik_source_routes,
        "discover_hikcentral_cameras",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    response = hik_http.client.post(
        "/video-sources/hikcentral/discover",
        data={
            "host": "10.0.0.20",
            "username": "app-key",
            "password": "private-secret",
        },
    )

    assert response.status_code == 400
    assert captured[0]["context"]["form_values"]["password"] == ""
    assert "private-secret" not in response.text


def test_hikconnect_validation_returns_form_error_without_calling_cloud(
    hik_http,
    monkeypatch,
):
    captured = _capture_templates(monkeypatch)
    called = []
    monkeypatch.setattr(
        hik_source_routes,
        "discover_hikconnect_cameras",
        lambda **_kwargs: called.append(True),
    )

    response = hik_http.client.post(
        "/video-sources/hikconnect/discover",
        data={"serial_number": "", "verification_code": ""},
    )

    assert response.status_code == 400
    assert "Número de Série" in captured[0]["context"]["error"]
    assert called == []


def test_hikconnect_discovery_preserves_channel_and_caches_credentials(
    hik_http,
    monkeypatch,
):
    captured = _capture_templates(monkeypatch)
    discovery_args = {}
    cache_args = {}

    def discover(**kwargs):
        discovery_args.update(kwargs)
        return [
            {
                "cameraIndexCode": "SERIAL-01-3",
                "cameraName": "Canal 3",
                "channelNo": 3,
                "status": "online",
            }
        ]

    monkeypatch.setattr(hik_source_routes, "discover_hikconnect_cameras", discover)
    monkeypatch.setattr(
        hik_source_routes,
        "store_nvr_discovery_cache",
        lambda **kwargs: cache_args.update(kwargs) or "connect-token",
    )

    response = hik_http.client.post(
        "/video-sources/hikconnect/discover",
        data={
            "serial_number": "SERIAL-01",
            "verification_code": "VERIFY",
            "channel_no": "3",
            "username": "admin",
            "password": "device-password",
            "simulate": "on",
        },
    )

    assert response.status_code == 200
    assert discovery_args["channel_no"] == 3
    assert discovery_args["verification_code"] == "VERIFY"
    assert cache_args["password"] == "device-password"
    assert cache_args["profiles"][0]["serial_number"] == "SERIAL-01"
    assert captured[0]["context"]["form_values"]["password"] == ""
    assert "device-password" not in response.text


def test_hikconnect_creation_uses_cache_and_persists_selected_channel(
    hik_http,
    monkeypatch,
):
    monkeypatch.setattr(
        hik_source_routes,
        "get_nvr_discovery_cache",
        lambda _token: {
            "password": "device-password",
            "profiles": {
                0: {
                    "cameraIndexCode": "SERIAL-01-2",
                    "cameraName": "Canal 2",
                    "channelNo": 2,
                    "serial_number": "SERIAL-01",
                }
            },
        },
    )

    response = hik_http.client.post(
        "/video-sources/hikconnect/create",
        data={
            "serial_number": "SERIAL-01",
            "verification_code": "VERIFY",
            "username": "admin",
            "credential_token": "token",
            "selected_profile": "0",
            "profile_camera_name_0": "Garagem",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/video-sources/hikconnect?created=1&skipped=0"
    )
    with hik_http.session_factory() as db:
        camera = db.query(Camera).one()
        assert camera.name == "Garagem"
        assert camera.rtsp_url == "hcp2p://SERIAL-01?verify=VERIFY&channel=2"
        assert camera.source_provider == "hikconnect"
