from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services import camera_runtime_service as runtime_service
from app.services.camera_runtime_service import (
    CameraRuntimeError,
    start_camera_processing,
    stop_camera_processing,
)
from app.services.runtime_client import RuntimeClientError
from app.services.worker_lifecycle import WorkerLifecycleError, WorkerStartBlocked


@pytest.fixture
def runtime_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    camera = Camera(
        name="Entrada",
        ip="10.0.0.10",
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
        status="idle",
        is_deleted=False,
    )
    db.add(camera)
    db.commit()
    db.refresh(camera)
    try:
        yield db, camera
    finally:
        db.close()
        engine.dispose()


def test_remote_runtime_start_and_error_mapping(runtime_db, monkeypatch):
    db, camera = runtime_db
    calls = []
    monkeypatch.setattr(runtime_service, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_service,
        "start_runtime_camera",
        lambda camera_id, **kwargs: calls.append((camera_id, kwargs)) or {"status": "started"},
    )

    operation = start_camera_processing(db, camera.id, use_motion_test=False)

    assert operation.payload == {"status": "started"}
    assert "remota" in operation.audit_details
    assert calls == [(camera.id, {"use_motion_test": False, "restart_existing": False})]

    monkeypatch.setattr(
        runtime_service,
        "start_runtime_camera",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeClientError("offline")),
    )
    with pytest.raises(CameraRuntimeError) as error:
        start_camera_processing(db, camera.id)
    assert error.value.status_code == 502
    assert error.value.detail == "Runtime indisponivel: offline"


def test_local_start_and_stop_update_persisted_status(runtime_db, monkeypatch):
    db, camera = runtime_db
    lifecycle = SimpleNamespace(
        action="started",
        as_dict=lambda: {"action": "started", "camera_id": camera.id},
    )
    stopped = []
    monkeypatch.setattr(runtime_service, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(runtime_service.registry, "get_worker", lambda _camera_id: None)
    monkeypatch.setattr(runtime_service.worker_lifecycle_manager, "start", lambda *_args, **_kwargs: lifecycle)
    monkeypatch.setattr(
        runtime_service.worker_lifecycle_manager,
        "stop",
        lambda camera_id, **kwargs: stopped.append((camera_id, kwargs)),
    )
    monkeypatch.setattr(runtime_service, "webrtc_gateway_is_enabled", lambda: False)
    monkeypatch.setattr(runtime_service, "stop_camera_source", lambda camera_id: stopped.append((camera_id, "gateway")))

    started = start_camera_processing(db, camera.id)
    db.refresh(camera)
    assert started.payload["message"] == "Processamento iniciado"
    assert camera.status == "running_motion_test"

    finished = stop_camera_processing(db, camera.id)
    db.refresh(camera)
    assert finished.payload == {"message": "Processamento parado", "camera_id": camera.id}
    assert camera.status == "stopped_manual"
    assert stopped == [(camera.id, {"reason": "api_stop"}), (camera.id, "gateway")]


def test_local_start_translates_resource_guard(runtime_db, monkeypatch):
    db, camera = runtime_db
    monkeypatch.setattr(runtime_service, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(runtime_service.registry, "get_worker", lambda _camera_id: None)
    monkeypatch.setattr(
        runtime_service.worker_lifecycle_manager,
        "start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            WorkerStartBlocked({"reason": "worker_limit", "allowed": False})
        ),
    )

    with pytest.raises(CameraRuntimeError) as error:
        start_camera_processing(db, camera.id)

    assert error.value.status_code == 429
    assert error.value.detail["reason"] == "worker_limit"


def test_web_worker_adapter_restarts_local_worker_and_updates_legacy_status(runtime_db, monkeypatch):
    _db, camera = runtime_db
    calls = []
    monkeypatch.setattr(runtime_service, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(
        runtime_service.preview_stream_manager,
        "stop",
        lambda camera_id: calls.append(("preview", camera_id)),
    )
    monkeypatch.setattr(
        runtime_service.worker_lifecycle_manager,
        "start",
        lambda current_camera, **kwargs: calls.append(("start", current_camera.id, kwargs)),
    )
    monkeypatch.setattr(runtime_service, "webrtc_gateway_is_enabled", lambda: False)

    started = runtime_service.start_camera_worker(camera, restart_existing=True)

    assert started is True
    assert camera.status == "running_motion_test"
    assert calls == [
        ("preview", camera.id),
        ("start", camera.id, {"restart_existing": True, "reason": "web_start"}),
    ]


def test_web_stop_adapter_continues_cleanup_when_worker_stop_fails(runtime_db, monkeypatch):
    _db, camera = runtime_db
    calls = []
    monkeypatch.setattr(runtime_service, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(
        runtime_service.worker_lifecycle_manager,
        "stop",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(WorkerLifecycleError("failed")),
    )
    monkeypatch.setattr(
        runtime_service.preview_stream_manager,
        "stop",
        lambda camera_id: calls.append(("preview", camera_id)),
    )
    monkeypatch.setattr(
        runtime_service,
        "stop_camera_source",
        lambda camera_id: calls.append(("gateway", camera_id)),
    )

    runtime_service.stop_camera_runtime(camera.id)

    assert calls == [("preview", camera.id), ("gateway", camera.id)]
