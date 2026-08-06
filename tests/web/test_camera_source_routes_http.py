import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.camera.onvif_client import RTSPDiscoveryResult, RTSPProfile
from app.db.base import Base
from app.db.models import Camera, User
from app.services import camera_source_update_service
from app.web import web_routes
from app.web.infrastructure import get_web_user
from app.web.routes import camera_source_routes


@pytest.fixture
def camera_source_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        camera = Camera(
            name="Original",
            ip="10.0.0.10",
            onvif_port=80,
            username="old-user",
            password="old-secret",
            rtsp_url="rtsp://10.0.0.10/original",
            is_deleted=False,
        )
        db.add(camera)
        db.commit()
        camera_id = camera.id

    application = FastAPI()
    application.include_router(web_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=12,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(camera_source_routes, "get_scoped_db", session_factory)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                client=client,
                session_factory=session_factory,
                camera_id=camera_id,
            )
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def test_direct_rtsp_endpoint_updates_camera(camera_source_context):
    response = camera_source_context.client.post(
        f"/cameras/{camera_source_context.camera_id}/rtsp-url",
        data={"rtsp_url": "rtsp://10.0.0.10/new"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with camera_source_context.session_factory() as db:
        camera = db.query(Camera).filter(Camera.id == camera_source_context.camera_id).one()
        assert camera.rtsp_url.endswith("/new")


def test_manufacturer_detection_uses_saved_password_and_maps_supported_brand(
    camera_source_context,
    monkeypatch,
):
    captured = {}

    def fake_detect(**kwargs):
        captured.update(kwargs)
        return {
            "open_ports": [80, 37777],
            "recommendation": {
                "brand": "dahua",
                "confidence": "high",
                "model": "SD6CE",
                "onvif_port": 80,
                "reason": "Identificado via ONVIF.",
            },
        }

    monkeypatch.setattr(camera_source_routes, "detect_common_video_device", fake_detect)
    monkeypatch.setattr(
        camera_source_routes,
        "_probe_sdk_manufacturers",
        lambda **_kwargs: [
            {"brand": "dahua", "port": 37777, "status": "confirmed", "detail": "ok"}
        ],
    )

    response = camera_source_context.client.post(
        "/cameras/manufacturer/detect",
        data={
            "camera_id": str(camera_source_context.camera_id),
            "ip": "10.0.0.10",
            "username": "old-user",
            "password": "",
            "onvif_port": "80",
        },
    )

    assert response.status_code == 200
    assert response.json()["manufacturer"] == "Dahua"
    assert response.json()["model"] == "SD6CE"
    assert captured["password"] == "old-secret"


def test_manufacturer_detection_maps_unsupported_brand_to_generic(
    camera_source_context,
    monkeypatch,
):
    monkeypatch.setattr(
        camera_source_routes,
        "detect_common_video_device",
        lambda **_kwargs: {
            "open_ports": [80],
            "recommendation": {
                "brand": "uniview",
                "confidence": "high",
                "reason": "Fabricante fora dos drivers nativos suportados.",
            },
        },
    )
    monkeypatch.setattr(
        camera_source_routes,
        "_probe_sdk_manufacturers",
        lambda **_kwargs: [],
    )

    response = camera_source_context.client.post(
        "/cameras/manufacturer/detect",
        data={"ip": "10.0.0.11", "username": "admin", "password": "secret"},
    )

    assert response.status_code == 200
    assert response.json()["manufacturer"] == "Generico"


def test_sdk_detection_confirms_login_and_closes_test_session(monkeypatch):
    disconnected = []
    monkeypatch.setattr(camera_source_routes.sdk_lab, "sdk_available", lambda _brand: True)
    monkeypatch.setattr(
        camera_source_routes.sdk_lab,
        "connect_device",
        lambda **kwargs: {
            "token": f"token-{kwargs['manufacturer']}",
            "device": {"serial_number": "ABC"},
        },
    )
    monkeypatch.setattr(
        camera_source_routes.sdk_lab,
        "disconnect",
        lambda token, owner_id: disconnected.append((token, owner_id)),
    )

    attempts = camera_source_routes._probe_sdk_manufacturers(
        owner_id=12,
        host="10.0.0.12",
        username="admin",
        password="secret",
        open_ports=[80, 8000],
        onvif_port=80,
    )

    assert [item["status"] for item in attempts] == [
        "confirmed",
        "port_closed",
        "confirmed",
    ]
    assert disconnected == [("token-hikvision", 12), ("token-intelbras", 12)]


def test_sdk_detection_marks_shared_protocol_as_ambiguous():
    result = camera_source_routes._merge_sdk_detection(
        {
            "recommendation": {
                "brand": "generic",
                "confidence": "low",
                "reason": "Sem identidade.",
            }
        },
        [
            {"brand": "dahua", "status": "confirmed"},
            {"brand": "intelbras", "status": "confirmed"},
        ],
    )

    assert result["recommendation"]["brand"] == "generic"
    assert result["recommendation"]["confidence"] == "low"
    assert "Mais de um backend autenticou" in result["recommendation"]["reason"]


def test_edit_rediscovery_keeps_secrets_server_side_and_confirms_pending_fields(
    camera_source_context,
    monkeypatch,
):
    secret = "new-private-value"
    monkeypatch.setattr(
        camera_source_update_service,
        "discover_rtsp",
        lambda *_args, **_kwargs: RTSPDiscoveryResult(
            rtsp_url=f"rtsp://new-user:{secret}@10.0.0.20/main",
            onvif_port=8000,
            profiles=[
                RTSPProfile(
                    token="main",
                    name="Principal",
                    rtsp_url=f"rtsp://new-user:{secret}@10.0.0.20/main",
                ),
                RTSPProfile(
                    token="sub",
                    name="Secundaria",
                    rtsp_url=f"rtsp://new-user:{secret}@10.0.0.20/sub",
                ),
            ],
        ),
    )

    discovered = camera_source_context.client.post(
        f"/cameras/{camera_source_context.camera_id}/edit",
        data={
            "name": "Atualizada",
            "ip": "10.0.0.20",
            "manufacturer": "Dahua",
            "model": "SD6CE",
            "onvif_port": "8000",
            "username": "new-user",
            "password": secret,
            "rtsp_url": "",
            "rediscover_rtsp": "true",
        },
    )

    assert discovered.status_code == 200
    assert secret not in discovered.text
    assert 'name="profile_rtsp_url_0"' not in discovered.text
    token = re.search(r'name="credential_token" value="([^"]+)"', discovered.text).group(1)

    confirmed = camera_source_context.client.post(
        f"/cameras/{camera_source_context.camera_id}/edit/confirm",
        data={"credential_token": token, "selected_profile": "1"},
        follow_redirects=False,
    )

    assert confirmed.status_code == 303
    with camera_source_context.session_factory() as db:
        camera = db.query(Camera).filter(Camera.id == camera_source_context.camera_id).one()
        assert camera.name == "Atualizada"
        assert camera.ip == "10.0.0.20"
        assert camera.username == "new-user"
        assert camera.password == secret
        assert camera.rtsp_url.endswith("/sub")


def test_edit_confirmation_rejects_token_for_another_camera(camera_source_context, monkeypatch):
    monkeypatch.setattr(
        camera_source_routes,
        "get_camera_discovery",
        lambda _token: {"camera_id": camera_source_context.camera_id + 1, "profiles": []},
    )

    response = camera_source_context.client.post(
        f"/cameras/{camera_source_context.camera_id}/edit/confirm",
        data={"credential_token": "other-camera", "selected_profile": "0"},
    )

    assert response.status_code == 400
    assert "descoberta expirou" in response.json()["detail"]


def test_edit_confirmation_validation_does_not_echo_cached_secret(camera_source_context, monkeypatch):
    monkeypatch.setattr(
        camera_source_routes,
        "get_camera_discovery",
        lambda _token: {
            "camera_id": camera_source_context.camera_id,
            "base_name": "Camera",
            "ip": "10.0.0.10",
            "username": "operator",
            "password": "cached-private-value",
            "onvif_port": 80,
            "profiles": [
                {
                    "index": 0,
                    "profile_name": "Main",
                    "rtsp_url": "rtsp://operator:cached-private-value@10.0.0.10/main",
                    "masked_rtsp_url": "rtsp://operator:***@10.0.0.10/main",
                    "encoding": "H264",
                    "resolution_label": "1920x1080",
                    "suggested_name": "Camera main",
                    "selected": False,
                }
            ],
        },
    )

    response = camera_source_context.client.post(
        f"/cameras/{camera_source_context.camera_id}/edit/confirm",
        data={"credential_token": "valid-token", "selected_profile": ""},
    )

    assert response.status_code == 400
    assert "Selecione um canal" in response.text
    assert "cached-private-value" not in response.text
    assert 'name="credential_token" value="valid-token"' in response.text
