from __future__ import annotations

import threading
import time

import numpy as np

from app.core.config import settings
from app.runtime import inference_detection
from app.runtime.inference import DetectionService, InferencePool, InferencePoolGroup


class BlockingDetectionService:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def _infer_direct(self, frame, offset_x=0, offset_y=0, scale_x=1.0, scale_y=1.0):
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=2.0)
        return [], 7.5


def _run_in_thread(target):
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def test_pool_drops_oldest_queued_job_when_full(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 1)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 5.0)
    monkeypatch.setattr(settings, "inference_pool_overflow_policy", "drop_oldest")

    service = BlockingDetectionService()
    pool = InferencePool(service_factory=lambda: service)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    results: dict[str, object] = {}

    def active_job():
        results["active"] = pool.infer(camera_id=1, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)

    active_thread = _run_in_thread(active_job)
    assert service.started.wait(timeout=1.0)

    def dropped_job():
        try:
            pool.infer(camera_id=2, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)
        except TimeoutError as exc:
            results["dropped"] = str(exc)

    dropped_thread = _run_in_thread(dropped_job)
    time.sleep(0.05)

    def accepted_job():
        results["accepted"] = pool.infer(camera_id=3, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)

    accepted_thread = _run_in_thread(accepted_job)
    time.sleep(0.05)
    service.release.set()

    active_thread.join(timeout=2.0)
    dropped_thread.join(timeout=2.0)
    accepted_thread.join(timeout=2.0)

    assert "removido" in str(results.get("dropped"))
    assert results.get("accepted") == ([], 7.5)
    stats = pool.stats()
    assert stats["dropped_oldest"] == 1
    assert stats["completed"] == 2


def test_pool_drops_stale_job_before_inference(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 2)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 0.05)
    monkeypatch.setattr(settings, "inference_pool_overflow_policy", "drop_oldest")

    service = BlockingDetectionService()
    pool = InferencePool(service_factory=lambda: service)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    results: dict[str, object] = {}

    def active_job():
        results["active"] = pool.infer(camera_id=1, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)

    active_thread = _run_in_thread(active_job)
    assert service.started.wait(timeout=1.0)

    def stale_job():
        try:
            pool.infer(camera_id=2, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)
        except TimeoutError as exc:
            results["stale"] = str(exc)

    stale_thread = _run_in_thread(stale_job)
    time.sleep(0.1)
    service.release.set()

    active_thread.join(timeout=2.0)
    stale_thread.join(timeout=2.0)

    assert "vencido" in str(results.get("stale"))
    stats = pool.stats()
    assert stats["stale_dropped"] == 1
    assert stats["completed"] == 1


def test_pool_uses_isolated_detection_service_per_camera(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 4)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_overflow_policy", "drop_oldest")

    created_services = []

    class TrackingDetectionService:
        def __init__(self):
            self.calls = 0
            created_services.append(self)

        def _infer_direct(self, frame, offset_x=0, offset_y=0, scale_x=1.0, scale_y=1.0):
            self.calls += 1
            return [{"track_id": len(created_services), "bbox": [0, 0, 1, 1], "confidence": 0.9}], 1.0

    pool = InferencePool(service_factory=TrackingDetectionService)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    pool.infer(camera_id=67, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)
    pool.infer(camera_id=68, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)
    pool.infer(camera_id=67, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)

    assert len(created_services) == 2
    assert created_services[0].calls == 2
    assert created_services[1].calls == 1
    assert pool.stats()["service_count"] == 2


def test_pool_group_probe_does_not_consume_camera_assignment(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 4)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 1.0)

    class ProbeDetectionService:
        def _infer_direct(self, frame, offset_x=0, offset_y=0, scale_x=1.0, scale_y=1.0):
            return [], 2.5

    group = InferencePoolGroup(
        pool_count=2,
        max_cameras_per_pool=4,
        service_factory=ProbeDetectionService,
    )
    try:
        tracks, infer_ms, runtime = group.probe(np.zeros((8, 8, 3), dtype=np.uint8))
        assert tracks == []
        assert infer_ms == 2.5
        assert runtime["probe"] is True
        assert runtime["total_assigned_cameras"] == 0
    finally:
        group.stop()


def test_pool_group_release_camera_removes_assignment_and_cached_service(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 4)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 1.0)

    class DetectionServiceStub:
        def _infer_direct(self, frame, offset_x=0, offset_y=0, scale_x=1.0, scale_y=1.0):
            return [], 1.0

    group = InferencePoolGroup(pool_count=2, max_cameras_per_pool=4, service_factory=DetectionServiceStub)
    try:
        group.infer(
            camera_id=67,
            infer_frame=np.zeros((8, 8, 3), dtype=np.uint8),
            offset_x=0,
            offset_y=0,
            scale_x=1,
            scale_y=1,
        )
        assert group.stats()["total_assigned_cameras"] == 1
        assert sum(pool["service_count"] for pool in group.stats()["pools"]) == 1

        result = group.release_camera(67)

        assert result["assignment_removed"] is True
        assert group.stats()["total_assigned_cameras"] == 0
        assert sum(pool["service_count"] for pool in group.stats()["pools"]) == 0
    finally:
        group.stop()


def test_pool_release_waits_for_active_inference_before_dropping_service(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 4)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 2.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 2.0)

    service = BlockingDetectionService()
    pool = InferencePool(service_factory=lambda: service)
    result: dict[str, object] = {}

    thread = _run_in_thread(
        lambda: result.update(
            inference=pool.infer(
                camera_id=67,
                infer_frame=np.zeros((8, 8, 3), dtype=np.uint8),
                offset_x=0,
                offset_y=0,
                scale_x=1,
                scale_y=1,
            )
        )
    )
    assert service.started.wait(timeout=1.0)

    release = pool.release_camera(67)
    assert release["active_job_pending"] is True
    assert pool.stats()["release_pending_count"] == 1

    service.release.set()
    thread.join(timeout=2.0)

    assert result["inference"] == ([], 7.5)
    assert pool.stats()["release_pending_count"] == 0
    assert pool.stats()["service_count"] == 0


def test_pool_recreates_service_after_tensorrt_engine_context_failure(monkeypatch):
    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_max_queue_size", 4)
    monkeypatch.setattr(settings, "inference_pool_job_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_max_job_age_seconds", 1.0)
    monkeypatch.setattr(settings, "inference_pool_overflow_policy", "drop_oldest")

    created_services = []

    class EngineFailOnceService:
        def __init__(self):
            self.calls = 0
            self.should_fail = len(created_services) == 0
            created_services.append(self)

        def _infer_direct(self, frame, offset_x=0, offset_y=0, scale_x=1.0, scale_y=1.0):
            self.calls += 1
            if self.should_fail:
                raise AttributeError("'NoneType' object has no attribute 'create_execution_context'")
            return [{"track_id": 1, "bbox": [0, 0, 1, 1], "confidence": 0.9}], 3.0

    pool = InferencePool(service_factory=EngineFailOnceService)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    try:
        pool.infer(camera_id=67, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)
    except AttributeError as exc:
        assert "create_execution_context" in str(exc)
    else:
        raise AssertionError("expected TensorRT context failure")

    result = pool.infer(camera_id=67, infer_frame=frame, offset_x=0, offset_y=0, scale_x=1, scale_y=1)

    assert result == ([{"track_id": 1, "bbox": [0, 0, 1, 1], "confidence": 0.9}], 3.0)
    assert len(created_services) == 2
    assert created_services[0].calls == 1
    assert created_services[1].calls == 1
    stats = pool.stats()
    assert stats["failed"] == 1
    assert stats["completed"] == 1
    assert stats["detector_recoveries"] == 1
    assert stats["consecutive_detector_failures"] == 0


def test_detection_service_falls_back_to_pytorch_after_engine_failure(monkeypatch):
    monkeypatch.setattr(settings, "detector_engine_auto_build_required", False)
    monkeypatch.setattr(settings, "detector_engine_runtime_fallback_enabled", True)

    created_detectors = []

    class FakePersonDetector:
        reset_count = 0

        def __init__(self, *, force_pytorch=False):
            self.force_pytorch = bool(force_pytorch)
            created_detectors.append(self)

        @classmethod
        def reset_shared_model(cls):
            cls.reset_count += 1

        def track(self, frame):
            if not self.force_pytorch:
                raise AttributeError("'NoneType' object has no attribute 'create_execution_context'")
            return ["fallback-result"]

    monkeypatch.setattr(inference_detection, "PersonDetector", FakePersonDetector)

    service = DetectionService(camera_id=67, use_pool=True)
    monkeypatch.setattr(
        service,
        "_extract_tracks_with_offset",
        lambda results, offset_x=0, offset_y=0, scale_x=1.0, scale_y=1.0: [{"source": results[0]}],
    )

    tracks, infer_ms = service._infer_direct(np.zeros((8, 8, 3), dtype=np.uint8))

    assert tracks == [{"source": "fallback-result"}]
    assert infer_ms >= 0
    assert [det.force_pytorch for det in created_detectors] == [False, True]
    assert FakePersonDetector.reset_count == 1
