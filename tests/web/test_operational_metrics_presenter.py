from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.web import operational_metrics_presenter


@pytest.fixture
def metrics_db():
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


def _camera(**overrides):
    values = {
        "name": "Portaria",
        "ip": "10.0.0.10",
        "username": "operator",
        "password": "secret",
        "status": "running",
        "is_deleted": False,
    }
    values.update(overrides)
    return Camera(**values)


def test_metrics_enrichment_identifies_capture_pressure():
    payload = operational_metrics_presenter.enrich_camera_metrics_payload(
        {
            "raw_fps": 25,
            "processed_fps": 10,
            "capture_queue_dropped_frames": 2,
        }
    )

    assert payload["capture_inference_pressure"] is True
    assert payload["capture_inference_pressure_ratio"] == 2.5
    assert payload["capture_inference_pressure_label"] == "em pressão"
    assert payload["capture_inference_pipeline_mode"] == "frame mais recente"


def test_gpu_fallback_parses_nvidia_smi_without_hidden_web_dependency(monkeypatch):
    monkeypatch.setattr(
        operational_metrics_presenter.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="42, 1024, 8192, 61, NVIDIA Test\n",
        ),
    )

    payload = operational_metrics_presenter._read_gpu_snapshot()

    assert payload["available"] is True
    assert payload["utilization_percent"] == 42.0
    assert payload["memory_used_mb"] == 1024.0
    assert payload["memory_total_mb"] == 8192.0
    assert payload["temperature_c"] == 61.0
    assert payload["name"] == "NVIDIA Test"


def test_dashboard_metrics_snapshot_aggregates_worker_and_host_health(metrics_db, monkeypatch):
    camera = _camera()
    metrics_db.add(camera)
    metrics_db.commit()
    metrics = {
        "raw_fps": 20.0,
        "processed_fps": 10.0,
        "process_cpu_percent": 12.5,
        "process_rss_mb": 256.0,
        "infer_ms": 15.0,
        "loop_ms": 40.0,
        "updated_at": "2026-07-17T12:00:00",
    }
    monkeypatch.setattr(
        operational_metrics_presenter.metrics_store,
        "get_metrics",
        lambda camera_id: metrics if camera_id == camera.id else None,
    )
    monkeypatch.setattr(
        operational_metrics_presenter,
        "get_runtime_health_snapshot",
        lambda: {
            "cameras": [{"camera_id": camera.id, "health_status": "running"}],
            "running_count": 1,
            "degraded_count": 0,
            "reconnecting_count": 0,
            "offline_count": 0,
            "stopped_count": 0,
            "gpu": {"available": False},
        },
    )
    monkeypatch.setattr(
        operational_metrics_presenter,
        "build_ai_operational_diagnostics",
        lambda _db: {"status": "ok"},
    )
    monkeypatch.setattr(
        operational_metrics_presenter,
        "diagnose_camera_worker",
        lambda *_args: {
            "stability_class": "running",
            "stability_label": "estável",
            "diagnosis_label": "normal",
            "diagnosis_reason": "ok",
        },
    )
    monkeypatch.setattr(
        operational_metrics_presenter,
        "_WEB_PROCESS",
        SimpleNamespace(
            cpu_percent=lambda interval=None: 3.5,
            memory_info=lambda: SimpleNamespace(rss=128 * 1024 * 1024),
        ),
    )
    monkeypatch.setattr(operational_metrics_presenter.psutil, "cpu_percent", lambda interval=None: 22.0)
    monkeypatch.setattr(
        operational_metrics_presenter.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=44.0),
    )

    payload = operational_metrics_presenter.build_dashboard_metrics_snapshot(metrics_db)

    assert payload["camera_total"] == 1
    assert payload["running_cameras"] == 1
    assert payload["worker_count"] == 1
    assert payload["worker_cpu_total_percent"] == 12.5
    assert payload["worker_rss_total_mb"] == 256.0
    assert payload["worker_raw_fps_total"] == 20.0
    assert payload["worker_processed_fps_total"] == 10.0
    assert payload["web_process_rss_mb"] == 128.0
    assert payload["host_cpu_percent"] == 22.0
    assert payload["host_ram_percent"] == 44.0
    assert payload["camera_metrics"][0]["capture_inference_pressure"] is True
