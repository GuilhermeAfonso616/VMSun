from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.web import camera_overview_presenter


def _camera(**overrides):
    values = {
        "name": "Portaria",
        "ip": "10.0.0.10",
        "username": "operator",
        "password": "secret",
        "rtsp_url": "rtsp://operator:secret@10.0.0.10/main",
        "status": "running",
        "is_deleted": False,
    }
    values.update(overrides)
    return Camera(**values)


def _operational_state(analysis_status="ok", stability_label="estável"):
    return {
        "operational_health": {
            "analysis": {
                "status": analysis_status,
                "label": "IA OK" if analysis_status == "ok" else "IA em espera",
                "detail": "processando",
            }
        },
        "worker_diagnosis": {"stability_label": stability_label},
    }


def test_camera_list_status_separates_config_runtime_pipeline_and_capture(monkeypatch):
    camera = _camera()
    monkeypatch.setattr(
        camera_overview_presenter,
        "build_camera_operational_state",
        lambda *_args, **_kwargs: _operational_state(),
    )

    status = camera_overview_presenter.build_camera_list_status(
        camera,
        {
            "raw_fps": 15,
            "processed_fps": 12,
            "infer_ms": 24,
            "capture_source": "gateway frames",
            "worker_mode": "continuous",
        },
        {"health_status": "running", "gateway_state": "live"},
    )

    assert status["config"] == {
        "status": "running",
        "label": "Ativa",
        "detail": "running",
    }
    assert status["runtime"]["label"] == "Rodando"
    assert status["pipeline"] == {
        "status": "running",
        "label": "IA OK",
        "detail": "infer 24ms",
    }
    assert status["capture"]["label"] == "Gateway"
    assert status["capture"]["detail"] == "live | raw 15.00 fps | proc 12.00 fps"


def test_camera_list_status_prioritizes_pipeline_queue_pressure(monkeypatch):
    camera = _camera()
    monkeypatch.setattr(
        camera_overview_presenter,
        "build_camera_operational_state",
        lambda *_args, **_kwargs: _operational_state(stability_label="sob carga"),
    )

    status = camera_overview_presenter.build_camera_list_status(
        camera,
        {
            "inference_pool_id": 2,
            "inference_pool_queue_size": 4,
            "infer_ms": 80,
        },
        {"health_status": "running"},
    )

    assert status["pipeline"]["status"] == "degraded"
    assert status["pipeline"]["label"] == "Sob carga"
    assert status["pipeline"]["detail"] == "pool 2 | fila 4 | infer 80ms"


def test_camera_list_status_reports_missing_stream_without_metrics(monkeypatch):
    camera = _camera(status="idle", rtsp_url=None)
    monkeypatch.setattr(
        camera_overview_presenter,
        "build_camera_operational_state",
        lambda *_args, **_kwargs: _operational_state(analysis_status="offline"),
    )

    status = camera_overview_presenter.build_camera_list_status(camera, {}, {})

    assert status["runtime"]["status"] == "idle"
    assert status["pipeline"]["label"] == "IA parada"
    assert status["capture"] == {
        "status": "offline",
        "label": "Sem RTSP",
        "detail": "URL nao configurada",
    }


def test_light_profile_recommendation_is_disabled_without_pressure():
    payload = camera_overview_presenter.build_light_profile_recommendation(
        None,
        {"capture_inference_pressure": False},
    )

    assert payload["enabled"] is False
    assert payload["recommendations"] == []
    assert payload["overrides"] == {}


def test_light_profile_recommendation_combines_roi_interval_and_motion():
    payload = camera_overview_presenter.build_light_profile_recommendation(
        None,
        {
            "capture_inference_pressure": True,
            "raw_fps": 30,
            "processed_fps": 10,
            "roi_enabled": False,
            "worker_mode": "continuous",
            "capture_queue_dropped_frames": 4,
        },
    )

    assert payload["enabled"] is True
    assert len(payload["recommendations"]) == 3
    assert payload["overrides"] == {
        "processing_max_width": 800,
        "processing_max_height": 450,
        "normal_inference_interval_seconds": 0.5,
        "capture_drop_frames": 5,
        "prefer_motion_test": True,
    }


def test_camera_overview_context_filters_deleted_and_builds_sorted_options(monkeypatch):
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
                _camera(name="B", site_name="Sul", group_name="Externo"),
                _camera(name="A", ip="10.0.0.11", site_name="Matriz", group_name="Interno"),
                _camera(name="Deleted", ip="10.0.0.12", is_deleted=True),
            ]
        )
        db.commit()
        monkeypatch.setattr(
            camera_overview_presenter,
            "get_runtime_health_snapshot",
            lambda: {"cameras": []},
        )
        monkeypatch.setattr(
            camera_overview_presenter.metrics_store,
            "get_metrics",
            lambda _camera_id: {},
        )
        monkeypatch.setattr(
            camera_overview_presenter,
            "enrich_camera_for_template",
            lambda camera, *_args, **_kwargs: camera,
        )
        monkeypatch.setattr(
            camera_overview_presenter,
            "build_camera_list_status",
            lambda *_args: {"runtime": {"status": "idle"}},
        )
        monkeypatch.setattr(
            camera_overview_presenter.settings,
            "camera_bulk_delete_password",
            "configured",
        )

        context = camera_overview_presenter.build_camera_overview_context(
            db,
            message="saved",
        )

    engine.dispose()
    assert [camera.name for camera in context["cameras"]] == ["A", "B"]
    assert context["site_options"] == ["Matriz", "Sul"]
    assert context["group_options"] == ["Externo", "Interno"]
    assert context["message"] == "saved"
    assert context["bulk_delete_enabled"] is True
