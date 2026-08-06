from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from app.core.config import settings
from app.runtime.event_clip_buffer import EventClipPersistenceBuffer
from app.runtime.events import EventPipeline
from app.services.event_persistence import PendingEventWrite


def _payload(camera_id: int, captured_at: datetime) -> PendingEventWrite:
    return PendingEventWrite(
        camera_id=camera_id,
        event=SimpleNamespace(event_id=f"event-{camera_id}", metadata={"clip_context": {}}),
        snapshot_frame=None,
        clip_before_frame=None,
        clip_after_frame=None,
        snapshot_captured_at=captured_at,
        created_at=captured_at,
    )


def test_event_pipeline_keeps_legacy_clip_accessors_backed_by_new_buffer():
    queue = Mock()
    pipeline = EventPipeline(persistence_queue=queue)

    assert pipeline.persistence_queue is queue
    assert pipeline._clip_history is pipeline._clip_persistence.history
    assert pipeline._delayed_event_writes is pipeline._clip_persistence.delayed_writes


def test_clip_buffer_samples_frames_and_keeps_immutable_jpeg_history(monkeypatch):
    monkeypatch.setattr(settings, "event_clip_history_sample_interval_seconds", 0.5)
    monkeypatch.setattr(settings, "event_clip_before_seconds", 2.0)
    monkeypatch.setattr(settings, "event_clip_after_seconds", 2.0)
    monkeypatch.setattr(settings, "event_clip_history_seconds", 10.0)
    buffer = EventClipPersistenceBuffer(None, Mock())
    started_at = datetime(2026, 1, 1, 12, 0, 0)
    frame = np.zeros((24, 32, 3), dtype=np.uint8)

    buffer.record_frame(7, frame, captured_at=started_at)
    buffer.record_frame(7, frame, captured_at=started_at + timedelta(seconds=0.1))
    buffer.record_frame(7, frame, captured_at=started_at + timedelta(seconds=1.0))

    assert len(buffer.history[7]) == 2
    assert all(isinstance(item.jpeg_bytes, bytes) for item in buffer.history[7])
    selected = buffer.select_video_frames(7, event_at=started_at + timedelta(seconds=1.0))
    assert selected == [item.jpeg_bytes for item in buffer.history[7]]


def test_clip_buffer_reports_real_offsets_of_each_sampled_frame(monkeypatch):
    monkeypatch.setattr(settings, "event_clip_history_sample_interval_seconds", 0.5)
    monkeypatch.setattr(settings, "event_clip_before_seconds", 5.0)
    monkeypatch.setattr(settings, "event_clip_after_seconds", 5.0)
    monkeypatch.setattr(settings, "event_clip_history_seconds", 20.0)
    buffer = EventClipPersistenceBuffer(None, Mock())
    started_at = datetime(2026, 1, 1, 12, 0, 0)
    frame = np.zeros((24, 32, 3), dtype=np.uint8)

    # O worker roda com jitter, entao o espacamento real nao e' uniforme.
    for seconds in (0.0, 0.6, 1.9, 2.4):
        buffer.record_frame(7, frame, captured_at=started_at + timedelta(seconds=seconds))

    frames, offsets = buffer.select_video_clip(7, event_at=started_at + timedelta(seconds=2.4))

    assert len(frames) == 4
    assert offsets == [0.0, 0.6, 1.9, 2.4]


def test_clip_buffer_falls_back_inline_when_async_queue_is_full():
    queue = Mock()
    queue.submit.return_value = False
    queue.persist_inline.return_value = True
    buffer = EventClipPersistenceBuffer(queue, Mock())
    payload = _payload(7, datetime(2026, 1, 1, 12, 0, 0))

    assert buffer.submit(payload) is True
    queue.submit.assert_called_once_with(payload)
    queue.persist_inline.assert_called_once_with(payload)


def test_delayed_flush_preserves_future_and_other_camera_writes():
    queue = Mock()
    queue.submit.return_value = True
    buffer = EventClipPersistenceBuffer(queue, Mock())
    event_at = datetime(2026, 1, 1, 12, 0, 0)
    camera_one = _payload(1, event_at)
    camera_two = _payload(2, event_at)
    buffer.defer(camera_one, due_at=event_at + timedelta(seconds=2))
    buffer.defer(camera_two, due_at=event_at + timedelta(seconds=1))
    frame = np.zeros((16, 16, 3), dtype=np.uint8)

    assert (
        buffer.flush_due_writes(
            1,
            frame,
            captured_at=event_at + timedelta(seconds=1),
        )
        == 0
    )
    assert buffer.delayed_writes[0].payload is camera_one
    assert buffer.delayed_writes[1].payload is camera_two

    assert (
        buffer.flush_due_writes(
            1,
            frame,
            captured_at=event_at + timedelta(seconds=2),
        )
        == 1
    )
    assert [item.payload for item in buffer.delayed_writes] == [camera_two]
    assert camera_one.clip_after_captured_at == event_at + timedelta(seconds=2)
    assert camera_one.event.metadata["clip_context"]["after_offset_seconds"] == 2.0
    queue.submit.assert_called_once_with(camera_one)
