from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, User
from app.services import camera_network_service
from app.services.onvif_network_discovery import OnvifNetworkDevice, OnvifNetworkDiscoveryResult
from app.web.infrastructure import get_web_user
from app.web.routes import camera_network_routes


@pytest.fixture
def camera_network_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    application = FastAPI()
    application.include_router(camera_network_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=9,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(camera_network_routes, "get_scoped_db", session_factory)
    monkeypatch.setattr(camera_network_routes, "log_audit", lambda *_args, **_kwargs: None)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, session_factory=session_factory)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def test_network_discovery_page_suggests_network_from_existing_camera(camera_network_context):
    with camera_network_context.session_factory() as db:
        db.add(
            Camera(
                name="Portaria",
                ip="10.20.30.44",
                username="existing",
                password="existing",
                is_deleted=False,
            )
        )
        db.commit()

    response = camera_network_context.client.get("/cameras/network-discovery")

    assert response.status_code == 200
    assert 'value="10.20.30.0/24"' in response.text


def test_network_scan_marks_already_registered_device(camera_network_context, monkeypatch):
    with camera_network_context.session_factory() as db:
        db.add(
            Camera(
                name="Camera existente",
                ip="192.168.5.10",
                username="existing",
                password="existing",
                is_deleted=False,
            )
        )
        db.commit()
    monkeypatch.setattr(
        camera_network_routes,
        "discover_onvif_network",
        lambda *_args, **_kwargs: OnvifNetworkDiscoveryResult(
            network="192.168.5.0/24",
            devices=[
                OnvifNetworkDevice(
                    ip="192.168.5.10",
                    port=80,
                    name="Camera ONVIF",
                    source="ws_discovery",
                )
            ],
            ws_discovery_count=1,
            port_scan_count=0,
            elapsed_seconds=0.25,
        ),
    )

    response = camera_network_context.client.post(
        "/cameras/network-discovery",
        data={"network": "192.168.5.0/24", "include_port_scan": "true"},
    )

    assert response.status_code == 200
    assert "Ja cadastrada: Camera existente" in response.text
    assert "1 dispositivo(s) em 0.25s" in response.text


def test_network_import_persists_camera_without_echoing_password(camera_network_context, monkeypatch):
    monkeypatch.setattr(
        camera_network_service,
        "discover_camera_streams",
        lambda **kwargs: SimpleNamespace(
            onvif_port=kwargs["onvif_port"],
            rtsp_url=f"rtsp://operator:private-value@{kwargs['ip']}/stream",
        ),
    )

    response = camera_network_context.client.post(
        "/cameras/network-discovery/import",
        data={
            "network": "192.168.10.0/24",
            "username": "operator",
            "password": "private-value",
            "camera_family": "bullet",
            "scene_category": "perimetral",
            "target_focus": "pessoa",
            "device_count": "1",
            "selected_device": "0",
            "device_ip_0": "192.168.10.21",
            "device_port_0": "80",
            "device_name_0": "Entrada",
            "device_source_0": "ws_discovery",
        },
    )

    assert response.status_code == 200
    assert "Entrada adicionada" in response.text
    assert "private-value" not in response.text
    with camera_network_context.session_factory() as db:
        camera = db.query(Camera).one()
        assert camera.ip == "192.168.10.21"
        assert camera.password == "private-value"


def test_network_import_rejects_devices_outside_submitted_network(camera_network_context):
    response = camera_network_context.client.post(
        "/cameras/network-discovery/import",
        data={
            "network": "192.168.10.0/24",
            "username": "operator",
            "password": "private-value",
            "device_count": "1",
            "selected_device": "0",
            "device_ip_0": "192.168.11.99",
            "device_port_0": "80",
        },
    )

    assert response.status_code == 400
    assert "Selecione pelo menos uma camera" in response.text
    assert "private-value" not in response.text
    with camera_network_context.session_factory() as db:
        assert db.query(Camera).count() == 0
