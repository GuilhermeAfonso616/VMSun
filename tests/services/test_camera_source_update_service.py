from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.camera.onvif_client import RTSPDiscoveryResult, RTSPProfile
from app.db.base import Base
from app.db.models import Camera
from app.services import camera_source_update_service
from app.services.camera_source_update_service import (
    PendingCameraSourceUpdate,
    confirm_camera_source_update,
    update_camera_source,
    update_camera_rtsp_source,
)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
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
    return engine, db, camera.id


def test_source_update_preserves_password_and_rtsp_when_not_submitted():
    engine, db, camera_id = _session()
    try:
        result = update_camera_source(
            db,
            camera_id=camera_id,
            name="Renomeada",
            ip="10.0.0.11",
            manufacturer="Dahua",
            model="IPC-HFW",
            onvif_port="8899",
            username="new-user",
            password="",
            rtsp_url="",
            rediscover_rtsp=False,
        )

        assert result.pending is None
        assert result.camera.name == "Renomeada"
        assert result.camera.password == "old-secret"
        assert result.camera.rtsp_url == "rtsp://10.0.0.10/original"
        assert result.camera.onvif_port == 8899
    finally:
        db.close()
        engine.dispose()


def test_multi_profile_rediscovery_defers_all_changes_until_confirmation(monkeypatch):
    engine, db, camera_id = _session()
    try:
        monkeypatch.setattr(
            camera_source_update_service,
            "discover_rtsp",
            lambda *_args, **_kwargs: RTSPDiscoveryResult(
                rtsp_url="rtsp://10.0.0.20/main",
                onvif_port=8000,
                profiles=[
                    RTSPProfile(token="main", name="Main", rtsp_url="rtsp://10.0.0.20/main"),
                    RTSPProfile(token="sub", name="Sub", rtsp_url="rtsp://10.0.0.20/sub"),
                ],
            ),
        )

        result = update_camera_source(
            db,
            camera_id=camera_id,
            name="Nova camera",
            ip="10.0.0.20",
            manufacturer="Hikvision",
            model="DS-2DE",
            onvif_port="8000",
            username="new-user",
            password="new-secret",
            rtsp_url="",
            rediscover_rtsp=True,
        )

        assert result.pending is not None
        assert result.pending.name == "Nova camera"
        db.expire_all()
        unchanged = db.query(Camera).filter(Camera.id == camera_id).one()
        assert unchanged.name == "Original"
        assert unchanged.ip == "10.0.0.10"

        updated = confirm_camera_source_update(
            db,
            pending=result.pending,
            rtsp_url="rtsp://10.0.0.20/sub",
        )
        assert updated.name == "Nova camera"
        assert updated.ip == "10.0.0.20"
        assert updated.username == "new-user"
        assert updated.password == "new-secret"
        assert updated.rtsp_url.endswith("/sub")
    finally:
        db.close()
        engine.dispose()


def test_direct_rtsp_update_validates_empty_url():
    engine, db, camera_id = _session()
    try:
        try:
            update_camera_rtsp_source(db, camera_id=camera_id, rtsp_url="  ")
        except ValueError as exc:
            assert "RTSP inválido" in str(exc)
        else:
            raise AssertionError("empty RTSP should fail")

        assert db.query(Camera).filter(Camera.id == camera_id).one().rtsp_url.endswith("/original")
    finally:
        db.close()
        engine.dispose()
