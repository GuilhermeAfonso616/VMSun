import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services.hik_source_service import (
    HikSourceError,
    build_hik_discovery_profiles,
    build_hik_source_url,
    create_hik_sources,
)


@pytest.fixture
def hik_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        yield db
    engine.dispose()


def test_hik_source_urls_preserve_provider_schemes():
    assert build_hik_source_url(
        "hikcentral",
        camera_index_code="cam-01",
    ) == "hc://cam-01"
    assert build_hik_source_url(
        "hikconnect",
        serial_number="SN123",
        verification_code="VERIFY",
        channel_no=2,
    ) == "hcp2p://SN123?verify=VERIFY&channel=2"

    with pytest.raises(HikSourceError, match="cameraIndexCode"):
        build_hik_source_url("hikcentral")


def test_discovery_profiles_mark_existing_active_source_and_ignore_deleted(hik_db):
    active = Camera(
        name="Existente",
        ip="10.0.0.1",
        username="user",
        password="secret",
        rtsp_url="hc://cam-01",
        is_deleted=False,
    )
    deleted = Camera(
        name="Excluída",
        ip="10.0.0.2",
        username="user",
        password="secret",
        rtsp_url="hc://cam-02",
        is_deleted=True,
    )
    hik_db.add_all([active, deleted])
    hik_db.commit()

    profiles = build_hik_discovery_profiles(
        hik_db,
        provider="hikcentral",
        discovered_cameras=[
            {
                "cameraIndexCode": "cam-01",
                "cameraName": "Portaria",
                "channelNo": 1,
                "status": "online",
            },
            {
                "cameraIndexCode": "cam-02",
                "cameraName": "Garagem",
                "channelNo": 2,
                "status": "offline",
            },
        ],
    )

    assert profiles[0]["existing_id"] == active.id
    assert profiles[0]["existing_name"] == "Existente"
    assert profiles[1]["existing_id"] is None


def test_create_hikcentral_sources_is_transactional_and_deduplicates(hik_db):
    cached_profiles = {
        0: {
            "cameraIndexCode": "cam-01",
            "cameraName": "Portaria",
            "channelNo": 1,
        }
    }
    first = create_hik_sources(
        hik_db,
        provider="hikcentral",
        host="10.0.0.20",
        username="app-key",
        password="app-secret",
        verification_code="",
        selected_indexes={"0"},
        cached_profiles=cached_profiles,
        camera_names={0: "Entrada principal"},
    )
    repeated = create_hik_sources(
        hik_db,
        provider="hikcentral",
        host="10.0.0.20",
        username="app-key",
        password="app-secret",
        verification_code="",
        selected_indexes={"0"},
        cached_profiles=cached_profiles,
        camera_names={},
    )

    camera = hik_db.query(Camera).one()
    assert first.created_count == 1
    assert first.skipped_count == 0
    assert repeated.created_count == 0
    assert repeated.skipped_count == 1
    assert camera.name == "Entrada principal"
    assert camera.rtsp_url == "hc://cam-01"
    assert camera.source_type == "hikcentral_channel"
    assert camera.source_provider == "hikcentral"
    assert camera.source_brand == "hikvision"
    assert camera.source_channel == 1
    assert camera.password == "app-secret"


def test_create_hikconnect_source_uses_cached_serial_and_submitted_verification(hik_db):
    result = create_hik_sources(
        hik_db,
        provider="hikconnect",
        host="FORM-SERIAL",
        username="admin",
        password="device-password",
        verification_code="VERIFY",
        selected_indexes={"0"},
        cached_profiles={
            0: {
                "cameraIndexCode": "SERIAL-01-1",
                "cameraName": "Canal 1",
                "channelNo": 3,
                "serial_number": "CACHED-SERIAL",
            }
        },
        camera_names={},
    )

    camera = hik_db.query(Camera).one()
    assert result.created_count == 1
    assert camera.ip == "CACHED-SERIAL"
    assert camera.rtsp_url == "hcp2p://CACHED-SERIAL?verify=VERIFY&channel=3"
    assert camera.source_type == "hikconnect_channel"
    assert camera.source_provider == "hikconnect"


def test_invalid_selected_index_rolls_back_entire_creation(hik_db):
    with pytest.raises(ValueError):
        create_hik_sources(
            hik_db,
            provider="hikcentral",
            host="10.0.0.20",
            username="app-key",
            password="app-secret",
            verification_code="",
            selected_indexes={"0", "invalid"},
            cached_profiles={
                0: {
                    "cameraIndexCode": "cam-01",
                    "cameraName": "Portaria",
                    "channelNo": 1,
                }
            },
            camera_names={},
        )

    assert hik_db.query(Camera).count() == 0
