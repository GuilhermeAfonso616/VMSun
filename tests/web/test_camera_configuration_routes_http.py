import html
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.analytics.camera_profiles import profile_from_camera
from app.db.base import Base
from app.db.models import Camera, User
from app.web.infrastructure import get_web_user
from app.web.routes import camera_configuration_routes


@pytest.fixture
def camera_configuration_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        camera = Camera(
            name="Configuravel",
            ip="10.0.0.40",
            onvif_port=80,
            username="operator",
            password="private-value",
            rtsp_url="rtsp://operator:private-value@10.0.0.40/main",
            is_deleted=False,
        )
        db.add(camera)
        db.commit()
        camera_id = camera.id

    application = FastAPI()
    application.include_router(camera_configuration_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=22,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(camera_configuration_routes, "get_scoped_db", session_factory)
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


def _motion_form(**overrides):
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
    return values


def test_ops_config_persists_operational_profile(camera_configuration_context):
    response = camera_configuration_context.client.post(
        f"/cameras/{camera_configuration_context.camera_id}/ops-config",
        data={
            "site_name": "Matriz",
            "group_name": "Perimetro",
            "camera_priority": "critical",
            "camera_family": "bullet",
            "scene_category": "perimetral",
            "target_focus": "pessoa",
            "auto_start_enabled": "true",
            "alarm_popup_enabled": "true",
            "learning_mode": "bounded_auto_tuning",
            "auto_tuning_enabled": "true",
            "max_daily_auto_changes": "2",
            "min_reviewed_events_for_suggestion": "10",
            "min_reviewed_events_for_auto_tuning": "20",
            "rollback_window_hours": "36",
            "processing_max_width": "1280",
            "rain": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with camera_configuration_context.session_factory() as db:
        camera = db.query(Camera).filter(Camera.id == camera_configuration_context.camera_id).one()
        profile = profile_from_camera(camera)
        assert camera.site_name == "Matriz"
        assert camera.camera_priority == "critical"
        assert profile.camera_family == "bullet"
        assert profile.nuisance_profile.rain is True


def test_ops_validation_renders_shared_detail_context(camera_configuration_context):
    response = camera_configuration_context.client.post(
        f"/cameras/{camera_configuration_context.camera_id}/ops-config",
        data={"camera_priority": "urgent", "max_daily_auto_changes": "1"},
    )

    assert response.status_code == 400
    assert "Prioridade inválida" in response.text
    assert "private-value" not in response.text


def test_analytics_config_persists_roi_line_and_event_modes(camera_configuration_context):
    response = camera_configuration_context.client.post(
        f"/cameras/{camera_configuration_context.camera_id}/analytics-config",
        data={
            "roi_polygon_json": '[{"x":0.1,"y":0.1},{"x":0.9,"y":0.1},{"x":0.5,"y":0.9}]',
            "line_start_x": "0.1",
            "line_start_y": "0.2",
            "line_end_x": "0.8",
            "line_end_y": "0.9",
            "line_direction": "a_to_b",
            "human_event_modes": ["person_entered", "line_crossing"],
            "human_loitering_seconds": "15",
            "human_detection_sensitivity": "high",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with camera_configuration_context.session_factory() as db:
        camera = db.query(Camera).filter(Camera.id == camera_configuration_context.camera_id).one()
        assert camera.analytics_coordinate_space == "source"
        assert camera.line_direction == "a_to_b"
        assert camera.human_event_modes_json == '["person_entered", "line_crossing"]'


def test_analytics_validation_preserves_submitted_values(camera_configuration_context):
    response = camera_configuration_context.client.post(
        f"/cameras/{camera_configuration_context.camera_id}/analytics-config",
        data={
            "roi_polygon_json": '[{"x":2,"y":0}]',
            "line_start_x": "0.1",
            "human_loitering_seconds": "15",
        },
    )

    assert response.status_code == 400
    assert "ROI precisa ter pelo menos 3 pontos" in response.text
    submitted_roi = re.search(
        r'name="roi_polygon_json"[^>]+value=\'([^\']*)\'',
        response.text,
    )
    assert submitted_roi
    assert html.unescape(submitted_roi.group(1)) == '[{"x":2,"y":0}]'


def test_motion_config_validation_and_reset(camera_configuration_context):
    base_url = f"/cameras/{camera_configuration_context.camera_id}/motion-config"
    saved = camera_configuration_context.client.post(
        base_url,
        data=_motion_form(),
        follow_redirects=False,
    )
    assert saved.status_code == 303

    invalid = camera_configuration_context.client.post(
        base_url,
        data=_motion_form(motion_ratio_threshold="2"),
    )
    assert invalid.status_code == 400
    assert "Limiar de movimento deve ficar entre 0 e 1" in invalid.text
    assert 'value="2"' in invalid.text

    reset = camera_configuration_context.client.post(
        f"{base_url}/reset",
        follow_redirects=False,
    )
    assert reset.status_code == 303
    with camera_configuration_context.session_factory() as db:
        camera = db.query(Camera).filter(Camera.id == camera_configuration_context.camera_id).one()
        assert camera.motion_ratio_threshold is None
