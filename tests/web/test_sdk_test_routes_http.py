from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.db.models import User
from app.web.infrastructure import get_web_user
from app.web.routes import sdk_test_routes


@pytest.fixture
def sdk_http(monkeypatch):
    role = {"value": "admin"}
    app = FastAPI()
    app.include_router(sdk_test_routes.router)
    app.dependency_overrides[get_web_user] = lambda: User(id=51, username="sdk-admin", role=role["value"], is_active=True)
    monkeypatch.setattr(sdk_test_routes.sdk_lab, "sdk_available", lambda: True)
    monkeypatch.setattr(sdk_test_routes.sdk_lab, "sdk_availability", lambda: {"hikvision": True, "dahua": True, "intelbras": True})
    monkeypatch.setattr(sdk_test_routes.sdk_lab, "list_sessions", lambda owner_id: [])
    monkeypatch.setattr(sdk_test_routes.sdk_lab, "cleanup_orphaned_streams", lambda: None)
    monkeypatch.setattr(sdk_test_routes.sdk_packages, "package_status", lambda: {
        "hikvision": {"installed": True}, "dahua": {"installed": True},
    })
    monkeypatch.setattr(sdk_test_routes.templates, "TemplateResponse", lambda **kwargs: PlainTextResponse(kwargs["name"]))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, role=role)
    app.dependency_overrides.clear()


def test_page_is_available_to_admin_and_global_dev_bypass(sdk_http):
    assert sdk_http.client.get("/admin/teste-sdk").status_code == 200
    sdk_http.role["value"] = "dev"
    assert sdk_http.client.get("/admin/teste-sdk").status_code == 200
    sdk_http.role["value"] = "supervisor"
    denied = sdk_http.client.get("/admin/teste-sdk", follow_redirects=False)
    assert denied.status_code == 303


def test_connect_does_not_return_credentials(sdk_http, monkeypatch):
    monkeypatch.setattr(sdk_test_routes.sdk_lab, "connect_device", lambda **kwargs: {
        "token": "abc", "label": kwargs["label"], "host": kwargs["host"], "device": {}
    })
    response = sdk_http.client.post("/admin/teste-sdk/sessoes", json={
        "label": "Portaria", "device_type": "camera", "host": "192.168.1.30",
        "manufacturer": "dahua", "port": 37777, "channel": 1,
        "username": "admin", "password": "private",
    })
    assert response.status_code == 200
    assert response.json()["session"]["token"] == "abc"
    assert "private" not in response.text
    assert "admin" not in response.text


def test_stream_returns_only_public_player_data(sdk_http, monkeypatch):
    monkeypatch.setattr(sdk_test_routes.sdk_lab, "start_stream", lambda token, owner_id: {
        "path": "sdk_lab_abc", "player_url": "/__webrtc_public__/sdk_lab_abc",
        "stream_kind": "sub",
    })

    response = sdk_http.client.post("/admin/teste-sdk/sessoes/abc/stream", json={})

    assert response.status_code == 200
    assert response.json()["stream"]["path"] == "sdk_lab_abc"
    assert "rtsp://" not in response.text
    assert "password" not in response.text


def test_legacy_snapshot_endpoint_is_not_exposed(sdk_http):
    assert sdk_http.client.get("/admin/teste-sdk/sessoes/abc/snapshot").status_code == 404


def test_dimensions_endpoint_returns_only_size_not_image(sdk_http, monkeypatch):
    monkeypatch.setattr(
        sdk_test_routes.sdk_lab,
        "stream_dimensions",
        lambda token, owner_id: (1920, 1080),
    )

    response = sdk_http.client.get("/admin/teste-sdk/sessoes/abc/dimensoes")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"ok": True, "width": 1920, "height": 1080}


def test_list_presets_returns_existing_camera_presets(sdk_http, monkeypatch):
    monkeypatch.setattr(sdk_test_routes.sdk_lab, "list_presets", lambda token, owner_id: [
        {"token": "3", "name": "Portao"},
    ])

    response = sdk_http.client.get("/admin/teste-sdk/sessoes/abc/presets")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "presets": [{"token": "3", "name": "Portao"}]}


def test_goto_preset_sends_token_to_sdk_session(sdk_http, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sdk_test_routes.sdk_lab,
        "goto_preset",
        lambda token, owner_id, preset_token: calls.append((token, owner_id, preset_token)),
    )

    response = sdk_http.client.post(
        "/admin/teste-sdk/sessoes/abc/presets/goto",
        json={"preset_token": "3"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls == [("abc", 51, "3")]


def test_ptz_3d_sends_normalized_selection_to_sdk_session(sdk_http, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sdk_test_routes.sdk_lab,
        "position_ptz_3d",
        lambda token, owner_id, **coordinates: calls.append(
            (token, owner_id, coordinates)
        ),
    )

    response = sdk_http.client.post(
        "/admin/teste-sdk/sessoes/abc/ptz/3d",
        json={"x_start": 20, "y_start": 30, "x_end": 220, "y_end": 230},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls == [(
        "abc",
        51,
        {"x_start": 20, "y_start": 30, "x_end": 220, "y_end": 230},
    )]


def test_ptz_3d_rejects_coordinates_outside_normalized_frame(sdk_http):
    response = sdk_http.client.post(
        "/admin/teste-sdk/sessoes/abc/ptz/3d",
        json={"x_start": -1, "y_start": 0, "x_end": 255, "y_end": 256},
    )

    assert response.status_code == 422


def test_sdk_page_only_exposes_existing_preset_activation():
    source = open("templates/sdk_test.html", encoding="utf-8").read()

    assert 'id="goto-preset"' in source
    assert "presets/goto" in source
    assert "não salva nem altera presets" in source
    assert "presets/save" not in source
    assert "presets/salvar" not in source


def test_sdk_page_exposes_native_3d_positioning_overlay():
    source = open("templates/sdk_test.html", encoding="utf-8").read()

    assert 'id="position-3d"' in source
    assert 'id="ptz-3d-overlay"' in source
    assert "/ptz/3d" in source
    assert "x_start" in source
    assert "x_end" in source
    assert "setPosition3dEnabled" in source


def test_admin_can_upload_sdk_package(sdk_http, monkeypatch):
    monkeypatch.setattr(sdk_test_routes.sdk_packages, "install_archive", lambda **kwargs: {
        "installed": True, "manufacturer": kwargs["manufacturer"], "sha256": "abc",
    })

    response = sdk_http.client.post(
        "/admin/teste-sdk/instalar",
        data={"manufacturer": "dahua"},
        files={"archive": ("netsdk.tar.gz", b"archive", "application/gzip")},
    )

    assert response.status_code == 200
    assert response.json()["package"]["manufacturer"] == "dahua"
