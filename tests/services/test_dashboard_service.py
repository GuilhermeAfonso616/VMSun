from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services import dashboard_service
from app.services.runtime_client import RuntimeClientError


def test_dashboard_counts_exclude_deleted_and_use_runtime_health(monkeypatch):
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
                Camera(name="Active", ip="1", username="u", password="p", is_deleted=False),
                Camera(name="Deleted", ip="2", username="u", password="p", is_deleted=True),
            ]
        )
        db.commit()
        monkeypatch.setattr(
            dashboard_service,
            "get_runtime_health_snapshot",
            lambda: {"running_count": 3},
        )

        counts = dashboard_service.get_dashboard_camera_counts(db)

    engine.dispose()
    assert counts == {"total_cameras": 1, "running_cameras": 3}


def test_operational_history_uses_local_store_with_exact_filters(monkeypatch):
    observed = {}
    monkeypatch.setattr(dashboard_service, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(
        dashboard_service.operational_history_store,
        "query",
        lambda **kwargs: observed.update(kwargs) or {"buckets": [1]},
    )

    payload, status = dashboard_service.get_operational_history_payload(
        hours=12,
        bucket_minutes=10,
        camera_id=7,
        start="2026-07-16T00:00:00",
        end="2026-07-17T00:00:00",
    )

    assert status == 200
    assert payload == {"buckets": [1]}
    assert observed == {
        "hours": 12,
        "bucket_minutes": 10,
        "camera_id": 7,
        "start_iso": "2026-07-16T00:00:00",
        "end_iso": "2026-07-17T00:00:00",
    }


def test_remote_operational_history_failure_preserves_503_contract(monkeypatch):
    monkeypatch.setattr(dashboard_service, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        dashboard_service,
        "runtime_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeClientError("offline")),
    )

    payload, status = dashboard_service.get_operational_history_payload(
        hours=24,
        bucket_minutes=5,
        camera_id=None,
        start=None,
        end=None,
    )

    assert status == 503
    assert payload["status"] == "error"
    assert payload["detail"] == "offline"
    assert payload["range"] == {"hours": 24, "bucket_minutes": 5}
    assert payload["cameras"] == []
    assert payload["buckets"] == []


def test_remote_resource_history_forwards_path_and_query(monkeypatch):
    observed = {}
    monkeypatch.setattr(dashboard_service, "remote_runtime_enabled", lambda: True)

    def runtime_get(path, **kwargs):
        observed.update({"path": path, **kwargs})
        return {"summary": {"samples": 2}}

    monkeypatch.setattr(dashboard_service, "runtime_get", runtime_get)

    payload, status = dashboard_service.get_resource_history_payload(
        hours=6,
        bucket_minutes=15,
        start=None,
        end=None,
    )

    assert status == 200
    assert payload == {"summary": {"samples": 2}}
    assert observed["path"] == "/internal/health/resource-history"
    assert observed["params"] == {
        "hours": 6,
        "bucket_minutes": 15,
        "start": None,
        "end": None,
    }
