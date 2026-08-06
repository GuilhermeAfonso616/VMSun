"""Buffer temporal de evidencias e despacho da persistencia de eventos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import cv2
import numpy as np

from app.core.config import settings
from app.services.event_persistence import EventPersistenceQueue, PendingEventWrite


@dataclass(slots=True)
class ClipFrame:
    captured_at: datetime
    jpeg_bytes: bytes


@dataclass(slots=True)
class DelayedEventWrite:
    due_at: datetime
    payload: PendingEventWrite


class EventClipPersistenceBuffer:
    def __init__(self, persistence_queue: EventPersistenceQueue | None, logger: Any):
        self.persistence_queue = persistence_queue
        self.logger = logger
        self.history: dict[int, list[ClipFrame]] = {}
        self.delayed_writes: list[DelayedEventWrite] = []

    def record_frame(self, camera_id: int, frame, *, captured_at: datetime) -> None:
        if frame is None or not hasattr(frame, "shape"):
            return
        history = self.history.setdefault(int(camera_id), [])
        sample_interval = max(
            0.05,
            float(settings.event_clip_history_sample_interval_seconds or 0.5),
        )
        if history and (captured_at - history[-1].captured_at).total_seconds() < sample_interval:
            return
        quality = max(30, min(95, int(settings.event_clip_history_jpeg_quality or 75)))
        try:
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), quality],
            )
        except (cv2.error, TypeError, ValueError):
            return
        if not ok:
            return
        history.append(ClipFrame(captured_at=captured_at, jpeg_bytes=encoded.tobytes()))
        before_seconds = max(0.0, float(settings.event_clip_before_seconds or 0.0))
        after_seconds = max(0.0, float(settings.event_clip_after_seconds or 0.0))
        history_seconds = max(
            float(settings.event_clip_history_seconds or 0.0),
            before_seconds + after_seconds + 2.0,
        )
        cutoff = captured_at - timedelta(seconds=history_seconds)
        while history and history[0].captured_at < cutoff:
            history.pop(0)

    def select_before_frame(self, camera_id: int, *, event_at: datetime, fallback_frame):
        before_seconds = max(0.0, float(settings.event_clip_before_seconds or 0.0))
        target_at = event_at - timedelta(seconds=before_seconds)
        history = self.history.get(int(camera_id), [])
        selected = None
        for item in history:
            if item.captured_at <= target_at:
                selected = item
            else:
                break
        if selected is None and history:
            selected = history[0]
        if selected is None:
            return fallback_frame, event_at
        decoded = cv2.imdecode(
            np.frombuffer(selected.jpeg_bytes, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if decoded is None:
            return fallback_frame, event_at
        return decoded, selected.captured_at

    def select_video_clip(
        self,
        camera_id: int,
        *,
        event_at: datetime,
    ) -> tuple[list[bytes], list[float]]:
        """Frames do clipe e o instante real de cada um, em segundos a partir
        do primeiro.

        O ring nao entrega frames equidistantes: o worker roda com jitter e
        pula amostras quando o loop atrasa. Sem esses offsets o clipe e' escrito
        com espacamento uniforme e acaba reproduzido fora da velocidade real."""
        before_seconds = max(0.0, float(settings.event_clip_before_seconds or 0.0))
        after_seconds = max(0.0, float(settings.event_clip_after_seconds or 0.0))
        start_at = event_at - timedelta(seconds=before_seconds)
        end_at = event_at + timedelta(seconds=after_seconds)
        history = self.history.get(int(camera_id), [])
        selected = [item for item in history if start_at <= item.captured_at <= end_at]
        if not selected:
            return [], []
        origin = selected[0].captured_at
        frames = [item.jpeg_bytes for item in selected]
        offsets = [
            round((item.captured_at - origin).total_seconds(), 3)
            for item in selected
        ]
        return frames, offsets

    def select_video_frames(self, camera_id: int, *, event_at: datetime) -> list[bytes]:
        frames, _ = self.select_video_clip(camera_id, event_at=event_at)
        return frames

    def submit(self, payload: PendingEventWrite) -> bool:
        if self.persistence_queue is None:
            self.logger.warning(
                "Persistence queue not configured; event will not be enqueued",
                extra={
                    "camera_id": payload.camera_id,
                    "event_id": getattr(payload.event, "event_id", "-"),
                    "action": "event_persistence_missing_queue",
                    "status": "degraded",
                    "reason": "queue_not_configured",
                },
            )
            return False

        accepted = self.persistence_queue.submit(payload)
        if not accepted:
            self.logger.warning(
                "Event persistence queue full; executing synchronous fallback",
                extra={
                    "camera_id": payload.camera_id,
                    "event_id": getattr(payload.event, "event_id", "-"),
                    "action": "event_persistence_queue_full",
                    "status": "degraded",
                    "reason": "queue_full_fallback",
                },
            )
            accepted = self.persistence_queue.persist_inline(payload)
        return bool(accepted)

    def defer(self, payload: PendingEventWrite, *, due_at: datetime) -> None:
        self.delayed_writes.append(DelayedEventWrite(due_at=due_at, payload=payload))

    def flush_due_writes(self, camera_id: int, frame, *, captured_at: datetime) -> int:
        if not self.delayed_writes:
            return 0
        persisted_count = 0
        remaining: list[DelayedEventWrite] = []
        for item in self.delayed_writes:
            if item.payload.camera_id != camera_id or item.due_at > captured_at:
                remaining.append(item)
                continue
            payload = item.payload
            if frame is not None and hasattr(frame, "copy"):
                payload.clip_after_frame = frame.copy()
                payload.clip_after_captured_at = captured_at
                (
                    payload.clip_video_frames,
                    payload.clip_video_frame_offsets,
                ) = self.select_video_clip(
                    camera_id,
                    event_at=payload.snapshot_captured_at or captured_at,
                )
                clip_context = dict(
                    (getattr(payload.event, "metadata", {}) or {}).get("clip_context")
                    or {}
                )
                clip_context["after_captured_at"] = captured_at.isoformat()
                clip_context["after_offset_seconds"] = round(
                    (
                        captured_at
                        - (payload.snapshot_captured_at or captured_at)
                    ).total_seconds(),
                    3,
                )
                payload.event.metadata["clip_context"] = clip_context
            if self.submit(payload):
                persisted_count += 1
        self.delayed_writes = remaining
        return persisted_count
