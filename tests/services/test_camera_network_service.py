import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services import camera_network_service
from app.services.camera_network_service import (
    import_discovered_network_cameras,
    normalize_camera_network_profile,
)
from app.services.onvif_network_discovery import OnvifNetworkDevice


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_camera_network_profile_normalizes_unknown_choices():
    profile = normalize_camera_network_profile("unknown", "invalid", "anything")

    assert profile.camera_family == "dome"
    assert profile.scene_category == "interno"
    assert profile.target_focus == "pessoa"


def test_network_import_creates_valid_devices_and_skips_existing(monkeypatch):
    engine, db = _session()
    try:
        db.add(
            Camera(
                name="Existente",
                ip="192.168.1.10",
                username="existing",
                password="existing",
                is_deleted=False,
            )
        )
        db.commit()
        captured = []

        def fake_discovery(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                onvif_port=kwargs["onvif_port"],
                rtsp_url=f"rtsp://{kwargs['username']}:{kwargs['password']}@{kwargs['ip']}/stream",
            )

        monkeypatch.setattr(camera_network_service, "discover_camera_streams", fake_discovery)
        profile = normalize_camera_network_profile("bullet", "perimetral", "veiculo")
        results = asyncio.run(
            import_discovered_network_cameras(
                db,
                devices=[
                    OnvifNetworkDevice(ip="192.168.1.10", port=80, name="Duplicada"),
                    OnvifNetworkDevice(ip="192.168.1.11", port=8899, name="Portaria"),
                ],
                username="operator",
                password="secret",
                profile=profile,
            )
        )

        assert [item["status"] for item in results] == ["ignored", "created"]
        assert len(captured) == 1
        assert captured[0]["allow_rtsp_fallback"] is False
        created = db.query(Camera).filter(Camera.ip == "192.168.1.11").one()
        assert created.name == "Portaria"
        assert created.onvif_port == 8899
        assert created.analytics_coordinate_space == "display"
    finally:
        db.close()
        engine.dispose()


def test_network_import_isolates_discovery_failure_per_device(monkeypatch):
    engine, db = _session()
    try:
        def fail_discovery(**_kwargs):
            raise RuntimeError("credenciais recusadas")

        monkeypatch.setattr(camera_network_service, "discover_camera_streams", fail_discovery)
        results = asyncio.run(
            import_discovered_network_cameras(
                db,
                devices=[OnvifNetworkDevice(ip="10.0.0.20", port=80)],
                username="operator",
                password="wrong",
                profile=normalize_camera_network_profile("dome", "interno", "pessoa"),
            )
        )

        assert results == [
            {
                "ip": "10.0.0.20",
                "ok": False,
                "status": "failed",
                "message": "credenciais recusadas",
            }
        ]
        assert db.query(Camera).count() == 0
    finally:
        db.close()
        engine.dispose()
