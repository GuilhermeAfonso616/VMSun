from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    Camera,
    ConfigVersionHistory,
    Event,
    EventFeedback,
    LockdownDelivery,
    TuningSuggestion,
)
from app.services import camera_operation_service


@pytest.fixture
def operation_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    cameras = [
        Camera(
            name=f"Camera {index}",
            ip=f"10.0.0.{index}",
            username="operator",
            password="secret",
            rtsp_url=f"rtsp://10.0.0.{index}/main",
            is_deleted=False,
        )
        for index in range(1, 4)
    ]
    db.add_all(cameras)
    db.commit()
    for camera in cameras:
        db.refresh(camera)

    effects = SimpleNamespace(stopped=[], unregistered=[], frames=[], metrics=[])
    monkeypatch.setattr(
        camera_operation_service,
        "stop_camera_runtime",
        lambda camera_id: effects.stopped.append(camera_id),
    )
    monkeypatch.setattr(
        camera_operation_service,
        "unregister_webrtc_camera_path",
        lambda camera_id: effects.unregistered.append(camera_id),
    )
    monkeypatch.setattr(
        camera_operation_service.frame_store,
        "remove_frame",
        lambda camera_id: effects.frames.append(camera_id),
    )
    monkeypatch.setattr(
        camera_operation_service.metrics_store,
        "remove_metrics",
        lambda camera_id: effects.metrics.append(camera_id),
    )
    try:
        yield SimpleNamespace(
            db=db,
            cameras=cameras,
            session_factory=session_factory,
            effects=effects,
        )
    finally:
        db.close()
        engine.dispose()


def test_soft_delete_is_transactional_and_cleans_runtime_state(operation_db):
    camera = operation_db.cameras[0]

    result = camera_operation_service.soft_delete_camera(operation_db.db, camera.id)

    assert result.is_deleted is True
    assert result.status == "disabled"
    assert result.deleted_at is not None
    assert operation_db.effects.stopped == [camera.id]
    assert operation_db.effects.unregistered == [camera.id]
    assert operation_db.effects.frames == [camera.id]
    assert operation_db.effects.metrics == [camera.id]


def test_selected_action_preserves_submitted_order_and_ignores_deleted_camera(
    operation_db,
    monkeypatch,
):
    first, second, deleted = operation_db.cameras
    deleted.is_deleted = True
    operation_db.db.commit()
    started = []

    def fake_start(camera, **_kwargs):
        started.append(camera.id)
        return camera.id == second.id

    monkeypatch.setattr(camera_operation_service, "start_camera_worker", fake_start)
    result = camera_operation_service.apply_selected_camera_action(
        operation_db.db,
        [second.id, first.id, deleted.id],
        "start",
    )

    assert result.camera_ids == [second.id, first.id]
    assert started == [second.id, first.id]
    assert result.processed_count == 1


def test_start_and_stop_actions_preserve_web_noop_semantics(operation_db, monkeypatch):
    camera = operation_db.cameras[0]
    monkeypatch.setattr(camera_operation_service, "start_camera_worker", lambda *_args, **_kwargs: True)

    missing = camera_operation_service.start_camera_action(operation_db.db, 999)
    started = camera_operation_service.start_camera_action(operation_db.db, camera.id)
    stopped_name = camera_operation_service.stop_camera_action(operation_db.db, camera.id)

    operation_db.db.refresh(camera)
    assert missing.reason == "not_found"
    assert started.started is True
    assert stopped_name == camera.name
    assert camera.status == "stopped_manual"


def test_purge_deletes_dependent_records_in_referential_order(operation_db):
    db = operation_db.db
    camera = operation_db.cameras[0]
    event = Event(
        camera_id=camera.id,
        event_type="person_entered",
        confidence=0.9,
        status="new",
    )
    db.add(event)
    db.flush()
    db.add_all(
        [
            EventFeedback(event_id=event.id, camera_id=camera.id, label="false_positive"),
            TuningSuggestion(
                camera_id=camera.id,
                scope_type="camera",
                scope_id=str(camera.id),
                suggestion_type="threshold",
                parameter_name="confidence",
            ),
            ConfigVersionHistory(
                camera_id=camera.id,
                config_before="{}",
                config_after="{}",
            ),
            LockdownDelivery(
                event_id=event.id,
                camera_id=camera.id,
                event_type=event.event_type,
                target_url="http://localhost/hook",
            ),
        ]
    )
    db.commit()

    camera_name = camera_operation_service.purge_camera(db, camera.id)

    assert camera_name == "Camera 1"
    assert db.query(Camera).filter(Camera.id == camera.id).count() == 0
    assert db.query(Event).filter(Event.camera_id == camera.id).count() == 0
    assert db.query(EventFeedback).filter(EventFeedback.camera_id == camera.id).count() == 0
    assert db.query(TuningSuggestion).filter(TuningSuggestion.camera_id == camera.id).count() == 0
    assert db.query(ConfigVersionHistory).filter(ConfigVersionHistory.camera_id == camera.id).count() == 0
    assert db.query(LockdownDelivery).filter(LockdownDelivery.camera_id == camera.id).count() == 0
