from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.schemas.camera_schemas import CameraAnalyticsProfileUpdate
from app.db.base import Base
from app.db.models import Camera
from app.services.camera_configuration_service import (
    AnalyticsConfigurationInput,
    CameraConfigurationError,
    MotionConfigurationInput,
    OperationalConfigurationInput,
    get_camera_profile,
    reset_motion_config,
    update_camera_profile,
    update_extended_operational_config,
    update_legacy_analytics_config,
    update_motion_config,
    update_operational_config,
    update_web_analytics_config,
)
from app.services.camera_factory import build_camera_model


@pytest.fixture
def camera_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    camera = build_camera_model(
        name="Entrada",
        ip="10.0.0.10",
        onvif_port=80,
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
    )
    db.add(camera)
    db.commit()
    db.refresh(camera)
    try:
        yield db, camera
    finally:
        db.close()
        engine.dispose()


def test_update_camera_profile_persists_canonical_and_legacy_fields(camera_db):
    db, camera = camera_db
    payload = CameraAnalyticsProfileUpdate(
        camera_family="bullet",
        scene_category="perimetral",
        roi_polygon=[{"x": 0.1, "y": 0.2}, {"x": 0.8, "y": 0.9}],
    ).model_dump()

    updated, profile = update_camera_profile(db, camera.id, payload)

    assert updated is camera
    assert profile.camera_id == camera.id
    assert profile.camera_family == "bullet"
    assert camera.analytics_profile_json
    assert camera.roi_polygon_json
    assert get_camera_profile(db, camera.id).camera_family == "bullet"


def test_update_legacy_and_operational_config_commit_expected_values(camera_db):
    db, camera = camera_db

    update_legacy_analytics_config(
        db,
        camera.id,
        roi_name="Portao",
        roi_polygon_json='[[0.1, 0.1], [0.9, 0.9]]',
        line_start_x=0.1,
        line_start_y=0.2,
        line_end_x=0.8,
        line_end_y=0.9,
        line_direction="a_to_b",
    )
    update_operational_config(
        db,
        camera.id,
        site_name="Matriz",
        group_name="Perimetro",
        camera_priority="critical",
        auto_start_enabled=True,
        alarm_sound_enabled=False,
        alarm_popup_enabled=True,
    )

    db.refresh(camera)
    assert camera.roi_name == "Portao"
    assert camera.line_direction == "a_to_b"
    assert camera.site_name == "Matriz"
    assert camera.camera_priority == "critical"
    assert camera.auto_start_enabled is True


def test_configuration_service_returns_typed_errors(camera_db):
    db, camera = camera_db

    with pytest.raises(CameraConfigurationError) as invalid_priority:
        update_operational_config(
            db,
            camera.id,
            site_name=None,
            group_name=None,
            camera_priority="urgent",
            auto_start_enabled=False,
            alarm_sound_enabled=True,
            alarm_popup_enabled=True,
        )
    with pytest.raises(CameraConfigurationError) as missing:
        get_camera_profile(db, 999)

    assert invalid_priority.value.status_code == 400
    assert invalid_priority.value.detail == "Prioridade inválida"
    assert missing.value.status_code == 404


def _operational_values(**overrides):
    values = {
        "site_name": "Matriz",
        "group_name": "Perimetro",
        "camera_priority": "high",
        "camera_family": "bullet",
        "scene_category": "perimetral",
        "target_focus": "pessoa",
        "auto_start_enabled": True,
        "alarm_sound_enabled": False,
        "alarm_popup_enabled": True,
        "learning_mode": "bounded_auto_tuning",
        "auto_tuning_enabled": True,
        "critical_lock": False,
        "max_daily_auto_changes": "2",
        "min_reviewed_events_for_suggestion": "10",
        "min_reviewed_events_for_auto_tuning": "20",
        "rollback_window_hours": "36",
        "manual_overrides": {
            "processing_max_width": "1280",
            "processing_max_height": "720",
            "processing_upscale_small_frames": True,
            "normal_inference_interval_seconds": "0.5",
            "capture_drop_frames": "2",
            "visual_raw_publish_interval_seconds": "0",
            "visual_processed_publish_interval_seconds": "0.2",
        },
        "nuisance_profile": {
            "vegetation_wind": False,
            "rain": True,
            "headlights": False,
            "insects_ir": False,
            "strong_shadows": False,
            "glass_reflection": False,
            "camera_vibration": False,
            "low_texture_scene": False,
            "crowd_occlusion": False,
            "fog_or_haze": False,
        },
    }
    values.update(overrides)
    return OperationalConfigurationInput(**values)


def _motion_values(**overrides):
    values = {
        "motion_idle_interval": "1.5",
        "motion_active_interval": "0.2",
        "motion_hold_seconds": "2",
        "motion_detection_hold_seconds": "3",
        "motion_min_motion_frames": "2",
        "motion_downscale_width": "384",
        "motion_min_contour_area": "700",
        "motion_ratio_threshold": "0.01",
        "motion_global_change_ratio_limit": "0.4",
        "motion_background_alpha": "0.025",
        "motion_warmup_frames": "20",
    }
    values.update(overrides)
    return MotionConfigurationInput(**values)


def test_extended_operational_config_persists_profile_and_tuning_fields(camera_db):
    db, camera = camera_db

    update_extended_operational_config(db, camera.id, _operational_values())

    db.refresh(camera)
    profile = get_camera_profile(db, camera.id)
    assert camera.site_name == "Matriz"
    assert camera.camera_priority == "high"
    assert camera.max_daily_auto_changes == 2
    assert camera.auto_tuning_enabled is True
    assert profile.camera_family == "bullet"
    assert profile.nuisance_profile.rain is True
    assert profile.manual_overrides["processing_max_width"] == 1280
    assert profile.manual_overrides["prefer_motion_test"] is True


def test_extended_operational_validation_rolls_back_partial_changes(camera_db):
    db, camera = camera_db
    camera.site_name = "Original"
    db.commit()

    with pytest.raises(CameraConfigurationError) as error:
        update_extended_operational_config(
            db,
            camera.id,
            _operational_values(max_daily_auto_changes="0"),
        )

    db.refresh(camera)
    assert error.value.status_code == 400
    assert "Limite diário" in error.value.detail
    assert camera.site_name == "Original"


def test_web_analytics_config_updates_canonical_profile_and_rolls_back_invalid_roi(camera_db):
    db, camera = camera_db
    valid = AnalyticsConfigurationInput(
        roi_polygon_json='[{"x":0.1,"y":0.1},{"x":0.9,"y":0.1},{"x":0.5,"y":0.9}]',
        line_start_x="0.1",
        line_start_y="0.2",
        line_end_x="0.8",
        line_end_y="0.9",
        line_direction="a_to_b",
        human_event_modes=["person_entered", "line_crossing", "invalid"],
        human_loitering_seconds="15",
        human_detection_sensitivity="high",
    )

    _, changed = update_web_analytics_config(db, camera.id, valid)
    db.refresh(camera)
    stored_roi = camera.roi_polygon_json
    assert changed is True
    assert camera.analytics_coordinate_space == "source"
    assert camera.line_direction == "a_to_b"
    assert camera.human_event_modes_json == '["person_entered", "line_crossing"]'
    assert get_camera_profile(db, camera.id).roi_polygon

    invalid = replace(valid, roi_polygon_json='[{"x":2,"y":0}]')
    with pytest.raises(CameraConfigurationError):
        update_web_analytics_config(db, camera.id, invalid)
    db.refresh(camera)
    assert camera.roi_polygon_json == stored_roi


def test_motion_config_is_transactional_and_can_be_reset(camera_db):
    db, camera = camera_db

    update_motion_config(db, camera.id, _motion_values())
    db.refresh(camera)
    assert camera.motion_idle_interval == 1.5
    assert camera.motion_ratio_threshold == 0.01

    with pytest.raises(CameraConfigurationError):
        update_motion_config(db, camera.id, _motion_values(motion_ratio_threshold="1.5"))
    db.refresh(camera)
    assert camera.motion_ratio_threshold == 0.01

    reset_motion_config(db, camera.id)
    db.refresh(camera)
    assert camera.motion_idle_interval is None
    assert camera.motion_ratio_threshold is None
