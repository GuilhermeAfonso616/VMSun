from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Event
from app.web import diagnostics_presenter


@pytest.fixture
def diagnostics_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        camera = Camera(
            name="Portaria",
            ip="10.0.0.10",
            username="operator",
            password="secret",
            status="idle",
            site_name="Matriz",
            group_name="Entradas",
            camera_priority="high",
            is_deleted=False,
        )
        db.add(camera)
        db.flush()
        event = Event(
            camera_id=camera.id,
            event_type="person_entered",
            severity="high",
            status="new",
            is_alarm_active=True,
            created_at=datetime(2026, 7, 17, 12, 0),
        )
        db.add(event)
        db.commit()
        yield db, camera, event
    engine.dispose()


def test_log_snapshot_reads_tail_and_ignores_missing_files(tmp_path, monkeypatch):
    (tmp_path / "app.log").write_text("first\nsecond\nthird\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics_presenter.settings, "logs_dir", str(tmp_path))

    payload = diagnostics_presenter.build_diagnostics_log_snapshot(limit_per_file=2)

    assert payload["sources"] == ["app.log"]
    assert payload["entries"] == [
        {"source": "app", "line": "second"},
        {"source": "app", "line": "third"},
    ]
    assert payload["limit_per_file"] == 2


def test_shell_payload_uses_runtime_fallbacks_without_database(monkeypatch):
    monkeypatch.setattr(
        diagnostics_presenter,
        "get_runtime_health_snapshot",
        lambda: {
            "running_count": 2,
            "degraded_count": 1,
            "reconnecting_count": 0,
            "offline_count": 1,
            "stopped_count": 3,
        },
    )
    monkeypatch.setattr(
        diagnostics_presenter,
        "runtime_tuning_snapshot",
        lambda **_kwargs: {"source": "fallback"},
    )
    monkeypatch.setattr(
        diagnostics_presenter,
        "engine_status_snapshot",
        lambda: {"backend": "cpu"},
    )
    monkeypatch.setattr(
        diagnostics_presenter.registry,
        "list_workers",
        lambda: [(1, object()), (2, object())],
    )

    payload = diagnostics_presenter.build_diagnostics_shell_payload()

    assert payload["summary"]["camera_total"] == 7
    assert payload["summary"]["running_count"] == 2
    assert payload["runtime_tuning"] == {"source": "fallback"}
    assert payload["detector_engine"] == {"backend": "cpu"}
    assert payload["cameras"] == []


def test_full_diagnostics_payload_executes_latest_event_path(diagnostics_db, monkeypatch):
    db, camera, event = diagnostics_db
    health_snapshot = {
        "cameras": [{"camera_id": camera.id, "health_status": "idle"}],
        "running_count": 0,
        "degraded_count": 0,
        "reconnecting_count": 0,
        "offline_count": 0,
        "stopped_count": 1,
        "runtime_tuning": {"source": "runtime"},
        "detector_engine": {"backend": "cpu"},
        "inference_pool_summary": {"enabled": False, "pools": []},
    }
    monkeypatch.setattr(
        diagnostics_presenter,
        "get_runtime_health_snapshot",
        lambda: health_snapshot,
    )
    monkeypatch.setattr(
        diagnostics_presenter,
        "build_dashboard_metrics_snapshot",
        lambda _db: {
            "worker_count": 0,
            "camera_metrics": [],
            "last_updated_at": None,
        },
    )
    monkeypatch.setattr(
        diagnostics_presenter,
        "build_monitor_alarm_payload",
        lambda _db: {
            "open_by_camera": {camera.id: [event]},
            "open_events_total": 1,
            "latest_alarm_signature": f"{event.id}:new:alarm",
            "alarm_should_play": True,
        },
    )
    monkeypatch.setattr(
        diagnostics_presenter,
        "serialize_monitor_camera",
        lambda item: {
            "id": item.id,
            "name": item.name,
            "status": item.status,
            "health_status": "idle",
            "health_status_display": "idle",
            "restart_count": 0,
            "consecutive_stall_checks": 0,
            "camera_priority": item.camera_priority,
            "site_name": item.site_name,
            "group_name": item.group_name,
            "is_running": False,
            "last_event_type": item.last_event_type,
        },
    )
    monkeypatch.setattr(
        diagnostics_presenter,
        "build_camera_operational_state",
        lambda *_args, **_kwargs: {
            "operator_status": "stopped",
            "operational_health": {},
            "worker_diagnosis": {
                "stability_class": "idle",
                "stability_label": "parada",
                "diagnosis_label": "normal",
                "diagnosis_reason": "sem worker",
            },
        },
    )
    monkeypatch.setattr(
        diagnostics_presenter.event_idempotency_store,
        "get_recent_summary",
        lambda *_args, **_kwargs: {
            "dedupe_recent_count": 0,
            "dedupe_recent_at": None,
            "dedupe_recent_age_seconds": None,
            "dedupe_window_seconds": 2,
        },
    )

    payload = diagnostics_presenter.build_diagnostics_payload(
        db,
        include_logs=False,
        include_gateway=False,
    )

    assert payload["summary"]["camera_total"] == 1
    assert payload["summary"]["open_events_count"] == 1
    assert payload["cameras"][0]["last_event_type"] == "Pessoa detectada"
    assert payload["latest_alarm_signature"] == f"{event.id}:new:alarm"
    assert payload["alarm_should_play"] is True
    assert payload["logs"]["entries"] == []
    assert payload["gateway"] is None


def test_gateway_failure_is_degraded_to_none(monkeypatch):
    monkeypatch.setattr(
        diagnostics_presenter,
        "fetch_gateway_health",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert diagnostics_presenter.get_gateway_health_snapshot(True) is None
    assert diagnostics_presenter.get_gateway_health_snapshot(False) is None
