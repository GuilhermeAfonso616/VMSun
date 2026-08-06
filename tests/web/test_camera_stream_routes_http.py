from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services.camera_media_service import mjpeg_chunk
from app.services.media_backbone_service import MediaBackboneUnavailable
from app.web.routes import camera_stream_routes


@pytest.fixture
def camera_stream_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        camera = Camera(
            name="Com RTSP",
            ip="10.0.2.1",
            username="operator",
            password="secret",
            rtsp_url="rtsp://10.0.2.1/main",
            is_deleted=False,
        )
        camera_without_rtsp = Camera(
            name="Sem RTSP",
            ip="10.0.2.2",
            username="operator",
            password="secret",
            rtsp_url=None,
            is_deleted=False,
        )
        db.add_all([camera, camera_without_rtsp])
        db.commit()
        camera_id = camera.id
        no_rtsp_id = camera_without_rtsp.id

    application = FastAPI()
    application.include_router(camera_stream_routes.router)
    monkeypatch.setattr(camera_stream_routes, "get_scoped_db", session_factory)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                client=client,
                camera_id=camera_id,
                no_rtsp_id=no_rtsp_id,
            )
    finally:
        engine.dispose()


def test_snapshot_returns_service_jpeg_and_unknown_camera_is_404(camera_stream_context, monkeypatch):
    monkeypatch.setattr(
        camera_stream_routes,
        "get_camera_snapshot_bytes",
        lambda camera_id, _url: f"jpeg-{camera_id}".encode(),
    )

    response = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.camera_id}/snapshot"
    )
    missing = camera_stream_context.client.get("/cameras/99999/snapshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == f"jpeg-{camera_stream_context.camera_id}".encode()
    assert missing.status_code == 404


def test_snapshot_returns_503_when_strict_backbone_is_unavailable(
    camera_stream_context,
    monkeypatch,
):
    def unavailable(*_args, **_kwargs):
        raise MediaBackboneUnavailable(
            "media_backbone_unavailable",
            "MediaMTX indisponivel",
        )

    monkeypatch.setattr(
        camera_stream_routes,
        "get_camera_snapshot_bytes",
        unavailable,
    )

    response = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.camera_id}/snapshot"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "media_backbone_unavailable"


def test_raw_stream_requires_rtsp_and_uses_mjpeg_contract(camera_stream_context, monkeypatch):
    monkeypatch.setattr(
        camera_stream_routes,
        "generate_camera_raw_mjpeg",
        lambda camera_id, _url: iter([mjpeg_chunk(f"raw-{camera_id}".encode())]),
    )

    response = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.camera_id}/stream/raw"
    )
    no_rtsp = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.no_rtsp_id}/stream/raw"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert f"raw-{camera_stream_context.camera_id}".encode() in response.content
    assert no_rtsp.status_code == 404


def test_processed_and_legacy_stream_routes_share_media_generator(camera_stream_context, monkeypatch):
    calls = []

    def finite_generator(get_bytes_fn, camera_id):
        calls.append((get_bytes_fn, camera_id))
        yield mjpeg_chunk(f"processed-{camera_id}".encode())

    monkeypatch.setattr(camera_stream_routes, "generate_mjpeg_bytes", finite_generator)

    processed = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.camera_id}/stream/processed"
    )
    legacy = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.camera_id}/stream"
    )

    assert processed.status_code == 200
    assert legacy.status_code == 200
    assert len(calls) == 2
    assert all(camera_id == camera_stream_context.camera_id for _, camera_id in calls)


def test_boxed_stream_uses_camera_visual_threshold(camera_stream_context, monkeypatch):
    calls = []
    monkeypatch.setattr(camera_stream_routes, "camera_ia1_visual_threshold", lambda _camera: 0.73)
    monkeypatch.setattr(
        camera_stream_routes,
        "get_boxed_stream_bytes",
        lambda camera_id, threshold: calls.append((camera_id, threshold)) or b"boxed",
    )

    def finite_generator(get_bytes_fn, camera_id):
        yield mjpeg_chunk(get_bytes_fn(camera_id))

    monkeypatch.setattr(camera_stream_routes, "generate_mjpeg_bytes", finite_generator)

    response = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.camera_id}/stream/boxed"
    )

    assert response.status_code == 200
    assert b"boxed" in response.content
    assert calls == [(camera_stream_context.camera_id, 0.73)]


def test_offline_stream_normalizes_state_label(camera_stream_context, monkeypatch):
    calls = []
    monkeypatch.setattr(
        camera_stream_routes,
        "generate_status_mjpeg",
        lambda title, message: calls.append((title, message)) or iter([mjpeg_chunk(b"offline")]),
    )

    response = camera_stream_context.client.get(
        f"/cameras/{camera_stream_context.camera_id}/stream/offline?state=reconnecting_now"
    )

    assert response.status_code == 200
    assert calls == [
        (
            "CAMERA RECONNECTING NOW",
            f"camera_id={camera_stream_context.camera_id}",
        )
    ]
