from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services.nvr_channel_service import (
    NvrChannelSpec,
    create_nvr_channel_sources,
    find_existing_nvr_channel,
    normalize_source_stream_kind,
)


def _camera(**overrides) -> Camera:
    values = {
        "name": "Canal 1",
        "ip": "10.0.0.8",
        "username": "operator",
        "password": "secret",
        "rtsp_url": "rtsp://10.0.0.8/channel/1",
        "source_type": "nvr_channel",
        "source_channel": 1,
        "source_stream_kind": "main",
        "is_deleted": False,
    }
    values.update(overrides)
    return Camera(**values)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_normalize_source_stream_kind_uses_persisted_values():
    assert normalize_source_stream_kind(None) == "main"
    assert normalize_source_stream_kind(" MAIN ") == "main"
    assert normalize_source_stream_kind("secondary") == "sub"
    assert normalize_source_stream_kind("sub_stream") == "sub"


def test_find_existing_nvr_channel_prefers_matching_rtsp_url():
    engine, db = _session()
    try:
        camera = _camera(ip="10.0.0.99", source_channel=9)
        db.add(camera)
        db.commit()

        found = find_existing_nvr_channel(
            db,
            host="10.0.0.8",
            channel=1,
            stream_kind="main",
            rtsp_url=camera.rtsp_url,
        )

        assert found is camera
    finally:
        db.close()
        engine.dispose()


def test_find_existing_nvr_channel_falls_back_to_logical_identity():
    engine, db = _session()
    try:
        camera = _camera()
        db.add(camera)
        db.commit()

        found = find_existing_nvr_channel(
            db,
            host="10.0.0.8",
            channel=1,
            stream_kind="main",
            rtsp_url="rtsp://new-address/channel/1",
        )

        assert found is camera
    finally:
        db.close()
        engine.dispose()


def test_find_existing_nvr_channel_ignores_soft_deleted_records():
    engine, db = _session()
    try:
        camera = _camera(is_deleted=True)
        db.add(camera)
        db.commit()

        found = find_existing_nvr_channel(
            db,
            host="10.0.0.8",
            channel=1,
            stream_kind="main",
            rtsp_url=camera.rtsp_url,
        )

        assert found is None
    finally:
        db.close()
        engine.dispose()


def test_create_nvr_channel_sources_returns_created_and_skipped_contracts():
    engine, db = _session()
    try:
        first = create_nvr_channel_sources(
            db,
            host="10.0.0.8",
            username="nvr",
            password="secret",
            onvif_port=None,
            base_name="Matriz",
            brand="intelbras",
            provider_type="generic_nvr",
            profiles=[
                NvrChannelSpec(
                    channel=1,
                    stream_kind="secondary",
                    rtsp_url="rtsp://10.0.0.8/channel/1/sub",
                )
            ],
            camera_family="dome",
            scene_category="interno",
            target_focus="pessoa",
            nuisance_profile={},
            analytics_profile=None,
        )
        repeated = create_nvr_channel_sources(
            db,
            host="10.0.0.8",
            username="nvr",
            password="secret",
            onvif_port=None,
            base_name="Matriz",
            brand="intelbras",
            provider_type="generic_nvr",
            profiles=[
                NvrChannelSpec(
                    channel=1,
                    stream_kind="sub",
                    rtsp_url="rtsp://10.0.0.8/channel/1/changed",
                )
            ],
            camera_family="dome",
            scene_category="interno",
            target_focus="pessoa",
            nuisance_profile={},
            analytics_profile=None,
        )

        assert first.to_dict()["count"] == 1
        assert first.created[0]["source_stream_kind"] == "sub"
        assert repeated.to_dict()["skipped_count"] == 1
        assert db.query(Camera).count() == 1
    finally:
        db.close()
        engine.dispose()
