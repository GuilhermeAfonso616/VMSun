from __future__ import annotations

import base64
import json
from urllib.error import HTTPError

import cv2
import numpy as np

from app.core.config import settings
from app.internal.routes import internal_inference_track
from app.runtime.inference import DetectionService, InferenceBackpressureError, InferencePoolGroup


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _FakePool:
    def infer(self, **kwargs):
        assert kwargs["camera_id"] == 7
        assert kwargs["infer_frame"].shape == (8, 8, 3)
        assert kwargs["offset_x"] == 3
        assert kwargs["offset_y"] == 4
        assert kwargs["scale_x"] == 1.5
        assert kwargs["scale_y"] == 2.0
        return (
            [{"track_id": 12, "bbox": [1, 2, 3, 4], "confidence": 0.8}],
            9.5,
            {"enabled": True, "mode": "pool", "backend": "central", "pool_id": 0, "queue_size": 0},
        )


def test_internal_inference_track_decodes_frame_and_uses_pool(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr("app.internal.routes.get_inference_pool_group", lambda: _FakePool())
    monkeypatch.setattr(
        "app.internal.routes.readiness_snapshot",
        lambda: {"status": "ready", "ready": True, "reason": "test", "last_error": None},
    )

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok

    response = internal_inference_track({
        "camera_id": 7,
        "image_b64": base64.b64encode(encoded.tobytes()).decode("ascii"),
        "offset_x": 3,
        "offset_y": 4,
        "scale_x": 1.5,
        "scale_y": 2.0,
    })

    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["runtime"]["backend"] == "central"
    assert payload["runtime"]["pool_id"] == 0
    assert payload["tracks"][0]["track_id"] == 12


def test_detection_service_calls_central_backend(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_backend", "central")
    monkeypatch.setattr(settings, "inference_pool_central_url", "http://runtime/internal/inference/track")
    monkeypatch.setattr(settings, "inference_pool_central_jpeg_quality", 70)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({
            "ok": True,
            "tracks": [{"track_id": 5, "bbox": [1, 1, 4, 4], "confidence": 0.9}],
            "infer_ms": 4.25,
            "runtime": {"queue_size": 0, "completed": 1},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    service = DetectionService(camera_id=42)
    tracks, infer_ms = service.infer(np.zeros((8, 8, 3), dtype=np.uint8), offset_x=1, offset_y=2, scale_x=1.25, scale_y=1.5)

    assert infer_ms == 4.25
    assert tracks[0]["track_id"] == 5
    assert captured["url"] == "http://runtime/internal/inference/track"
    assert captured["timeout"] == 1.5
    assert captured["payload"]["camera_id"] == 42
    assert captured["payload"]["offset_x"] == 1
    assert captured["payload"]["scale_y"] == 1.5
    assert service.runtime_stats()["backend"] == "central"
    assert service.runtime_stats()["central_jpeg_quality"] == 70


def test_detection_service_treats_central_429_as_backpressure(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_backend", "central")
    monkeypatch.setattr(settings, "inference_pool_central_url", "http://runtime/internal/inference/track")
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)

    def fake_urlopen(_request, timeout=None):
        raise HTTPError(
            "http://runtime/internal/inference/track",
            429,
            "Too Many Requests",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    service = DetectionService(camera_id=67)
    try:
        service.infer(np.zeros((8, 8, 3), dtype=np.uint8))
    except InferenceBackpressureError as exc:
        assert "HTTP 429" in str(exc)
    else:
        raise AssertionError("Expected central 429 to raise InferenceBackpressureError")

    stats = service.runtime_stats()
    assert stats["backend"] == "central"
    assert stats["backpressure"] is True
    assert stats["http_status"] == 429


def test_inference_pool_group_assigns_cameras_across_pools(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 2)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_overflow_policy", "drop_oldest")

    class FastService:
        def _infer_direct(self, frame, offset_x=0, offset_y=0, scale_x=1.0, scale_y=1.0):
            return [], 1.0

    group = InferencePoolGroup(pool_count=4, max_cameras_per_pool=8, service_factory=FastService)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    pool_ids = []

    for camera_id in range(1, 11):
        _tracks, _infer_ms, runtime = group.infer(
            camera_id=camera_id,
            infer_frame=frame,
            offset_x=0,
            offset_y=0,
            scale_x=1.0,
            scale_y=1.0,
        )
        pool_ids.append(runtime["pool_id"])

    assert pool_ids[:4] == [0, 1, 2, 3]
    assert max(pool_ids) == 3
    stats = group.stats()
    assert stats["pool_count"] == 4
    assert stats["max_cameras_per_pool"] == 8
    assert stats["total_assigned_cameras"] == 10
    assert sorted(item["assigned_cameras"] for item in stats["pools"]) == [2, 2, 3, 3]
    assigned_ids = sorted(camera_id for item in stats["pools"] for camera_id in item["assigned_camera_ids"])
    assert assigned_ids == list(range(1, 11))
    group.stop()
