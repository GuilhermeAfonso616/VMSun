from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.models import Camera, User
from app.services import camera_operation_service
from app.web.infrastructure import get_web_user
from app.web.routes import camera_operation_routes


@pytest.fixture
def camera_operation_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        cameras = [
            Camera(
                name=f"Camera {index}",
                ip=f"10.0.1.{index}",
                username="operator",
                password="secret",
                rtsp_url=f"rtsp://10.0.1.{index}/main",
                is_deleted=False,
            )
            for index in range(1, 4)
        ]
        db.add_all(cameras)
        db.commit()
        camera_ids = [camera.id for camera in cameras]

    application = FastAPI()
    application.include_router(camera_operation_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=23,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(camera_operation_routes, "get_scoped_db", session_factory)
    monkeypatch.setattr(camera_operation_routes, "log_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(camera_operation_service, "stop_camera_runtime", lambda _camera_id: None)
    monkeypatch.setattr(camera_operation_service, "unregister_webrtc_camera_path", lambda _camera_id: None)
    monkeypatch.setattr(camera_operation_service.frame_store, "remove_frame", lambda _camera_id: None)
    monkeypatch.setattr(camera_operation_service.metrics_store, "remove_metrics", lambda _camera_id: None)
    monkeypatch.setattr(camera_operation_service, "start_camera_worker", lambda camera, **_kwargs: True)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                client=client,
                session_factory=session_factory,
                camera_ids=camera_ids,
            )
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def test_single_soft_delete_disables_camera(camera_operation_context):
    camera_id = camera_operation_context.camera_ids[0]
    response = camera_operation_context.client.post(
        f"/cameras/{camera_id}/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/cameras?message=camera_disabled"
    with camera_operation_context.session_factory() as db:
        camera = db.query(Camera).filter(Camera.id == camera_id).one()
        assert camera.is_deleted is True
        assert camera.status == "disabled"


def test_bulk_action_validates_selection_and_stops_in_submitted_order(camera_operation_context):
    invalid = camera_operation_context.client.post(
        "/cameras/bulk-action",
        data={"action": "stop"},
        follow_redirects=False,
    )
    assert invalid.status_code == 303
    assert invalid.headers["location"] == "/cameras?error=bulk_action_no_selection"

    selected = camera_operation_context.camera_ids[1:]
    response = camera_operation_context.client.post(
        "/cameras/bulk-action",
        data={"action": "stop", "camera_ids": selected},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/cameras?message=selected_cameras_stopped&count=2"
    with camera_operation_context.session_factory() as db:
        statuses = [
            db.query(Camera).filter(Camera.id == camera_id).one().status
            for camera_id in selected
        ]
        assert statuses == ["stopped_manual", "stopped_manual"]


def test_start_and_stop_routes_preserve_referer(camera_operation_context):
    camera_id = camera_operation_context.camera_ids[0]
    headers = {"referer": "http://testserver/cameras"}

    started = camera_operation_context.client.post(
        f"/cameras/{camera_id}/start",
        headers=headers,
        follow_redirects=False,
    )
    stopped = camera_operation_context.client.post(
        f"/cameras/{camera_id}/stop",
        headers=headers,
        follow_redirects=False,
    )

    assert started.status_code == 303
    assert stopped.status_code == 303
    assert started.headers["location"] == "http://testserver/cameras"
    assert stopped.headers["location"] == "http://testserver/cameras"
    with camera_operation_context.session_factory() as db:
        camera = db.query(Camera).filter(Camera.id == camera_id).one()
        assert camera.status == "stopped_manual"


def test_purge_requires_confirmation_and_removes_camera(camera_operation_context):
    camera_id = camera_operation_context.camera_ids[0]
    rejected = camera_operation_context.client.post(
        f"/cameras/{camera_id}/purge",
        data={"confirm": "DELETE"},
    )
    assert rejected.status_code == 400

    purged = camera_operation_context.client.post(
        f"/cameras/{camera_id}/purge",
        data={"confirm": "PURGE"},
        follow_redirects=False,
    )
    assert purged.status_code == 303
    assert purged.headers["location"] == "/cameras?message=camera_purged"
    with camera_operation_context.session_factory() as db:
        assert db.query(Camera).filter(Camera.id == camera_id).count() == 0


def test_delete_all_requires_password_and_confirmation(camera_operation_context, monkeypatch):
    monkeypatch.setattr(settings, "camera_bulk_delete_password", "admin-secret")
    rejected = camera_operation_context.client.post(
        "/cameras/delete-all",
        data={"bulk_delete_password": "wrong", "confirm_text": "EXCLUIR TODAS"},
        follow_redirects=False,
    )
    assert rejected.headers["location"] == "/cameras?error=bulk_delete_bad_password"

    accepted = camera_operation_context.client.post(
        "/cameras/delete-all",
        data={"bulk_delete_password": "admin-secret", "confirm_text": "EXCLUIR TODAS"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    assert "all_cameras_deleted" in accepted.headers["location"]
    with camera_operation_context.session_factory() as db:
        assert db.query(Camera).filter(Camera.is_deleted.is_(False)).count() == 0
