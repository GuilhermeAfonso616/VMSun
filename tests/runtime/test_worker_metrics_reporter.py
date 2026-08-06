from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime.worker_metrics_reporter import (
    MetricsAnalyticsContext,
    MetricsFrameContext,
    MetricsTimings,
    WorkerMetricsReporter,
    WorkerMetricsState,
)
from app.services.display_resize import DISPLAY_FRAME_HEIGHT, DISPLAY_FRAME_WIDTH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ATTEMPTED_AT = datetime(2026, 7, 20, 12, 30, 0)
STORED_AT = datetime(2026, 7, 20, 12, 30, 1)


class StubLogger:
    def __init__(self):
        self.exception_calls = []

    def exception(self, *args, **kwargs):
        self.exception_calls.append((args, kwargs))


class StubPublisher:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def publish(self, camera_id, **kwargs):
        self.calls.append((camera_id, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def build_contexts():
    return {
        "timings": MetricsTimings(
            read_ms=1.1,
            infer_ms=2.2,
            plot_ms=3.3,
            jpeg_ms=4.4,
            loop_ms=5.5,
            current_fps=6.6,
        ),
        "frame": MetricsFrameContext(
            frame=SimpleNamespace(shape=(720, 1280, 3)),
            frame_width=1280,
            frame_height=720,
            infer_input_width=640,
            infer_input_height=384,
            tracks=[{"track_id": 9, "bbox": [1, 2, 3, 4]}],
        ),
        "analytics": MetricsAnalyticsContext(
            roi_polygon=[(0, 0), (10, 0), (10, 10)],
            roi_name="entrada",
            roi_crop_active=True,
            roi_crop_meta={"x": 10, "y": 20, "w": 30, "h": 40},
            line_pixels=((1, 2), (3, 4)),
            line_direction="a_to_b",
            motion_info={"state": "active"},
            inference_result_age_ms=125.5,
            visual_tracks_stale=True,
        ),
        "state": WorkerMetricsState(
            last_successful_inference_at=datetime(2026, 7, 20, 12, 29, 55),
            last_frame_at=datetime(2026, 7, 20, 12, 29, 59),
            last_processed_frame_at=datetime(2026, 7, 20, 12, 29, 58),
            health_status="running",
            consecutive_stall_checks=2,
            worker_mode="normal",
            worker_generation="generation-7",
            raw_fps=7.7,
            processed_fps=8.8,
        ),
        "visual_stats": {
            "raw_frames_published": 11,
            "processed_frames_published": 12,
            "raw_frames_skipped_by_throttle": 13,
            "processed_frames_skipped_by_throttle": 14,
            "jpeg_encode_count": 15,
            "jpeg_encode_time_ms": 16.5,
            "overlay_render_count": 17,
            "overlay_render_time_ms": 18.5,
            "effective_raw_publish_fps": 19.5,
            "effective_processed_publish_fps": 20.5,
            "raw_jobs_dropped": 2,
            "processed_jobs_dropped": 3,
        },
    }


def build_reporter(publisher):
    logger = StubLogger()
    persistence_stats = {
        "queue_size": 21,
        "events_queued": 22,
        "events_persisted": 23,
        "events_failed": 24,
        "dropped_or_rejected_jobs": 25,
        "persist_latency_ms": 26.5,
        "last_persist_latency_ms": 27.5,
    }
    persistence_queue = SimpleNamespace(stats=lambda: persistence_stats)
    capture_service = SimpleNamespace(
        reconnect_count=4,
        dropped_frames_count=5,
        capture_source="gateway frames",
        gateway_fallback_started_at=100.0,
        gateway_recovery_count=6,
        gateway_recovery_last_success_wall_at=1_000.0,
        gateway_fallback_started_wall_at=2_000.0,
    )
    frame_mailbox = SimpleNamespace(dropped_count=7)
    inference_runtime = {"mode": "pool", "pool_id": "pool-a"}
    reporter = WorkerMetricsReporter(
        camera_id=123,
        process_pid=456,
        logger=logger,
        publisher=publisher,
        capture_service=capture_service,
        frame_mailbox=frame_mailbox,
        persistence_queue=persistence_queue,
        inference_runtime=lambda: inference_runtime,
        utcnow=lambda: ATTEMPTED_AT,
    )
    return reporter, logger, persistence_stats, inference_runtime


def test_reporter_translates_every_metrics_source_and_returns_store_timestamp():
    publisher = StubPublisher(result={"updated_at": STORED_AT.isoformat()})
    reporter, logger, persistence_stats, inference_runtime = build_reporter(publisher)

    result = reporter.publish(**build_contexts())

    assert result.last_metrics_at == STORED_AT
    assert result.persistence_stats is persistence_stats
    assert result.payload == {"updated_at": STORED_AT.isoformat()}
    assert logger.exception_calls == []

    camera_id, payload = publisher.calls[0]
    assert camera_id == 123
    assert payload["read_ms"] == pytest.approx(1.1)
    assert payload["infer_ms"] == pytest.approx(2.2)
    assert payload["loop_ms"] == pytest.approx(5.5)
    assert payload["tracks_count"] == 1
    assert payload["tracks"][0]["track_id"] == 9
    assert payload["reconnect_count"] == 4
    assert payload["dropped_frames_count"] == 5
    assert payload["capture_queue_dropped_frames"] == 7
    assert payload["event_persistence_queue_size"] == 21
    assert payload["event_persistence_events_persisted"] == 23
    assert payload["visual_jobs_dropped"] == 5
    assert payload["raw_frames_published"] == 11
    assert payload["processed_frames_published"] == 12
    assert payload["last_metrics_at"] == ATTEMPTED_AT
    assert payload["health_status"] == "running"
    assert payload["worker_generation"] == "generation-7"
    assert payload["roi_name"] == "entrada"
    assert payload["visual_tracks_stale"] is True
    assert payload["inference_runtime"] is inference_runtime
    assert payload["display_frame_width"] == DISPLAY_FRAME_WIDTH
    assert payload["display_frame_height"] == DISPLAY_FRAME_HEIGHT
    assert payload["gateway_recovery_last_success_at"] == datetime(
        1970, 1, 1, 0, 16, 40
    )
    assert payload["gateway_fallback_started_at"] == datetime(
        1970, 1, 1, 0, 33, 20
    )


def test_reporter_preserves_attempt_timestamp_when_publisher_is_throttled():
    publisher = StubPublisher(result=None)
    reporter, logger, persistence_stats, _ = build_reporter(publisher)

    result = reporter.publish(**build_contexts())

    assert result.last_metrics_at == ATTEMPTED_AT
    assert result.persistence_stats is persistence_stats
    assert result.payload is None
    assert logger.exception_calls == []


def test_reporter_contains_publication_failure_and_keeps_operational_stats():
    publisher = StubPublisher(error=RuntimeError("metrics store unavailable"))
    reporter, logger, persistence_stats, _ = build_reporter(publisher)

    result = reporter.publish(**build_contexts())

    assert result.last_metrics_at == ATTEMPTED_AT
    assert result.persistence_stats is persistence_stats
    assert result.payload is None
    assert len(logger.exception_calls) == 1
    _, kwargs = logger.exception_calls[0]
    assert kwargs["extra"] == {
        "action": "publish_metrics",
        "status": "degraded",
        "reason": "metrics_publish_failed",
        "worker_pid": 456,
    }


def test_worker_delegates_metrics_contract_without_reverse_dependency():
    worker_source = (
        PROJECT_ROOT / "app/runtime/worker_base.py"
    ).read_text(encoding="utf-8")
    assert "metrics_publisher.publish(" not in worker_source
    assert "metrics_reporter.publish(" in worker_source

    reporter_tree = ast.parse(
        (PROJECT_ROOT / "app/runtime/worker_metrics_reporter.py").read_text(
            encoding="utf-8"
        )
    )
    imported_modules = {
        node.module
        for node in ast.walk(reporter_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.runtime.worker_base" not in imported_modules
