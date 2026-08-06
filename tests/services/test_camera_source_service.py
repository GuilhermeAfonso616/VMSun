from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services.camera_source_service import CameraSourceSpec, create_camera_sources


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_create_camera_sources_persists_selected_profiles_in_one_transaction():
    engine, db = _session()
    try:
        cameras = create_camera_sources(
            db,
            ip="10.0.0.8",
            manufacturer="Hikvision",
            model="DS-2CD",
            onvif_port=80,
            username="operator",
            password="secret",
            sources=[
                CameraSourceSpec(name="Principal", rtsp_url="rtsp://10.0.0.8/main"),
                CameraSourceSpec(name="Secundaria", rtsp_url="rtsp://10.0.0.8/sub"),
            ],
            camera_family="bullet",
            scene_category="perimetral",
            target_focus="pessoa",
            nuisance_profile={"rain": True},
            analytics_profile=None,
            coordinate_space_override="display",
        )

        assert [camera.name for camera in cameras] == ["Principal", "Secundaria"]
        assert all(camera.id for camera in cameras)
        assert all(camera.source_type == "camera" for camera in cameras)
        assert all(camera.analytics_coordinate_space == "display" for camera in cameras)
        assert db.query(Camera).count() == 2
    finally:
        db.close()
        engine.dispose()


def test_create_camera_sources_rejects_empty_selection_before_writing():
    engine, db = _session()
    try:
        try:
            create_camera_sources(
                db,
                ip="10.0.0.8",
                manufacturer="Hikvision",
                model=None,
                onvif_port=80,
                username="operator",
                password="secret",
                sources=[],
                camera_family="dome",
                scene_category="interno",
                target_focus="pessoa",
                nuisance_profile={},
                analytics_profile=None,
            )
        except ValueError as exc:
            assert "Nenhuma fonte" in str(exc)
        else:
            raise AssertionError("empty selection should fail")

        assert db.query(Camera).count() == 0
    finally:
        db.close()
        engine.dispose()
