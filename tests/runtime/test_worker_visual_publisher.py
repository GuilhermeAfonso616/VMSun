from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.runtime import worker_visual_publisher
from app.runtime.output import VisualPublishScheduler
from app.runtime.worker_visual_publisher import WorkerVisualPublisher


def _publisher(*, store, logger=None, normalize=None, raw_interval=0.1, processed_interval=0.1):
    scheduler = VisualPublishScheduler(
        raw_publish_interval_seconds=raw_interval,
        processed_publish_interval_seconds=processed_interval,
    )
    return WorkerVisualPublisher(
        camera_id=7,
        process_pid=4321,
        logger=logger or Mock(),
        scheduler=scheduler,
        frame_store_backend=store,
        normalize_frame=normalize or (lambda frame: frame),
    )


def test_visual_publisher_uses_normalized_frame_and_accumulates_costs(monkeypatch):
    normalized = np.ones((8, 8, 3), dtype=np.uint8)
    store = SimpleNamespace(
        set_raw_frame=Mock(return_value={"ok": True, "encode_ms": 1.5}),
        set_processed_frame=Mock(return_value={"ok": True, "encode_ms": 2.5}),
    )
    normalize = Mock(return_value=normalized)
    render = Mock()
    publisher = _publisher(store=store, normalize=normalize)
    monkeypatch.setattr(worker_visual_publisher.time, "monotonic", lambda: 100.0)
    perf_values = iter((10.0, 10.002))
    monkeypatch.setattr(
        worker_visual_publisher.time,
        "perf_counter",
        lambda: next(perf_values),
    )
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    result = publisher.publish(
        frame=frame,
        geometry=SimpleNamespace(),
        infer_ran=True,
        decision=SimpleNamespace(),
        roi_crop_meta=None,
        tracks=[],
        visual_tracks=[],
        render_frame=render,
    )

    store.set_raw_frame.assert_called_once_with(7, frame)
    assert store.set_processed_frame.call_args.args == (7, normalized)
    render.assert_called_once()
    assert result["annotated_frame"] is not frame
    assert result["overlay_ms"] == pytest.approx(2.0)
    assert result["jpeg_ms"] == 4.0
    assert result["visual_stats"]["raw_frames_published"] == 1
    assert result["visual_stats"]["processed_frames_published"] == 1


def test_visual_publisher_counts_failed_raw_and_processed_jobs(monkeypatch):
    store = SimpleNamespace(
        set_raw_frame=Mock(return_value={"ok": False}),
        set_processed_frame=Mock(return_value=None),
    )
    logger = Mock()
    publisher = _publisher(store=store, logger=logger)
    monkeypatch.setattr(worker_visual_publisher.time, "monotonic", lambda: 200.0)
    monkeypatch.setattr(worker_visual_publisher.time, "perf_counter", Mock(side_effect=[1.0, 1.001]))

    result = publisher.publish(
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
        geometry=SimpleNamespace(),
        infer_ran=False,
        decision=SimpleNamespace(),
        roi_crop_meta=None,
        tracks=[],
        visual_tracks=None,
        render_frame=Mock(),
    )

    assert publisher.scheduler.raw_jobs_dropped == 1
    assert publisher.scheduler.processed_jobs_dropped == 1
    assert logger.warning.call_count == 2
    assert result["raw_publish"] == {"ok": False}
    assert result["processed_publish"] is None


def test_visual_publisher_with_disabled_channels_does_not_copy_or_render(monkeypatch):
    store = SimpleNamespace(set_raw_frame=Mock(), set_processed_frame=Mock())
    render = Mock()
    publisher = _publisher(
        store=store,
        raw_interval=0.0,
        processed_interval=0.0,
    )
    monkeypatch.setattr(worker_visual_publisher.time, "monotonic", lambda: 300.0)

    result = publisher.publish(
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
        geometry=SimpleNamespace(),
        infer_ran=False,
        decision=SimpleNamespace(),
        roi_crop_meta=None,
        tracks=[],
        visual_tracks=None,
        render_frame=render,
    )

    store.set_raw_frame.assert_not_called()
    store.set_processed_frame.assert_not_called()
    render.assert_not_called()
    assert result["annotated_frame"] is None
