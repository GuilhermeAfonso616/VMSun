from __future__ import annotations

from unittest.mock import Mock

import numpy as np

from app.analytics_v2.pipeline.event_pipeline import AnalyticsBatchResult
from app.runtime.events import EventPipeline
from app.services.event_persistence import PendingEventWrite
from app.services.event_snapshot_store import EventSnapshotStore


def _empty_batch() -> AnalyticsBatchResult:
    return AnalyticsBatchResult(tracks=[], events=[], suppressed=[])


def test_skipped_inference_keeps_clip_history_without_reusing_tracks():
    pipeline = EventPipeline()
    pipeline.pipeline = Mock()
    pipeline._flush_due_event_writes = Mock(return_value=2)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    result = pipeline.process(
        camera_id=7,
        tracks=[{"track_id": 11, "confidence": 0.91, "bbox": [4, 5, 30, 42]}],
        db=Mock(),
        frame=frame,
        detections_fresh=False,
    )

    pipeline.pipeline.process.assert_not_called()
    assert result.generated_events == []
    assert result.persisted_count == 2
    pipeline._flush_due_event_writes.assert_called_once()
    assert len(pipeline._clip_history[7]) == 1
    stored = pipeline._clip_history[7][0]
    assert isinstance(stored.jpeg_bytes, bytes)
    assert len(stored.jpeg_bytes) < frame.nbytes
    selected = pipeline._select_clip_video_frames(7, event_at=stored.captured_at)
    assert selected == [stored.jpeg_bytes]
    decoded = EventSnapshotStore._decode_video_frame(selected[0])
    assert decoded is not None
    assert decoded.shape == frame.shape


def test_persistence_payload_reuses_immutable_jpeg_bytes():
    jpeg = b"compressed-frame"
    payload = PendingEventWrite(
        camera_id=7,
        event=Mock(),
        snapshot_frame=None,
        clip_before_frame=None,
        clip_after_frame=None,
        clip_video_frames=[jpeg],
    )

    copied = payload.copy()

    assert copied.clip_video_frames == [jpeg]
    assert copied.clip_video_frames[0] is jpeg


def test_fresh_empty_detection_advances_tracker_as_empty_scene():
    pipeline = EventPipeline()
    pipeline.pipeline = Mock()
    pipeline.pipeline.process.return_value = _empty_batch()
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    pipeline.process(
        camera_id=7,
        tracks=[],
        db=Mock(),
        frame=frame,
        detections_fresh=True,
    )

    pipeline.pipeline.process.assert_called_once()
    assert pipeline.pipeline.process.call_args.kwargs["tracks"] == []
