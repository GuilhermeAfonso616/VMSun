from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.web import hik_source_presenter


def test_channel_health_is_provider_scoped_sorted_and_masks_credentials(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add_all(
            [
                Camera(
                    name="Central B",
                    ip="10.0.0.20",
                    username="app-key",
                    password="secret-value",
                    rtsp_url="rtsp://app-key:secret-value@10.0.0.20/main",
                    source_provider="hikcentral",
                    source_channel=2,
                    is_deleted=False,
                ),
                Camera(
                    name="Connect",
                    ip="SERIAL",
                    username="admin",
                    password="device-secret",
                    source_provider="hikconnect",
                    source_channel=1,
                    is_deleted=False,
                ),
                Camera(
                    name="Central A",
                    ip="10.0.0.10",
                    username="app-key",
                    password="secret-value",
                    source_provider="hikcentral",
                    source_channel=1,
                    is_deleted=False,
                ),
            ]
        )
        db.commit()
        cameras = db.query(Camera).order_by(Camera.ip.asc()).all()
        ids = {camera.name: camera.id for camera in cameras}

        monkeypatch.setattr(
            hik_source_presenter,
            "get_runtime_health_snapshot",
            lambda: {
                "cameras": [
                    {
                        "camera_id": ids["Central A"],
                        "health_status": "running",
                    }
                ]
            },
        )

        rows = hik_source_presenter.build_hikcentral_channel_health(db)

    engine.dispose()
    assert [row["name"] for row in rows] == ["Central A", "Central B"]
    assert all(row["source_provider"] == "hikcentral" for row in rows)
    assert rows[0]["health_status"] == "running"
    assert "secret-value" not in str(rows)
    assert rows[1]["masked_rtsp_url"] == "rtsp://app-key:***@10.0.0.20/main"


def test_form_defaults_and_password_sanitization_are_independent():
    central = hik_source_presenter.hikcentral_form_defaults()
    connect = hik_source_presenter.hikconnect_form_defaults()
    safe = hik_source_presenter.without_password(
        {"host": "10.0.0.1", "password": "secret"}
    )

    assert central["base_name"] == "HikCentral"
    assert connect["base_name"] == "Hik-Connect"
    assert connect["username"] == "admin"
    assert safe == {"host": "10.0.0.1", "password": ""}


def test_hikconnect_verification_code_is_masked_in_health_urls():
    assert hik_source_presenter.mask_hik_source_url(
        "hcp2p://SERIAL?verify=PRIVATE-CODE&channel=2"
    ) == "hcp2p://SERIAL?verify=***&channel=2"
