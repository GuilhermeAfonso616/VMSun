"""Cadencia e telemetria da publicacao visual do worker."""

from __future__ import annotations

import time


class VisualPublishScheduler:
    def __init__(
        self,
        *,
        raw_publish_interval_seconds: float,
        processed_publish_interval_seconds: float,
        raw_publish_enabled: bool = True,
        processed_publish_enabled: bool = True,
    ):
        self.raw_publish_enabled = bool(raw_publish_enabled)
        self.processed_publish_enabled = bool(processed_publish_enabled)
        self.raw_publish_interval_seconds = max(
            0.0,
            float(raw_publish_interval_seconds),
        )
        self.processed_publish_interval_seconds = max(
            0.0,
            float(processed_publish_interval_seconds),
        )
        self.started_at = time.monotonic()
        self.last_raw_publish_at = 0.0
        self.last_processed_publish_at = 0.0
        self.raw_frames_published = 0
        self.processed_frames_published = 0
        self.raw_frames_skipped_by_throttle = 0
        self.processed_frames_skipped_by_throttle = 0
        self.overlay_render_count = 0
        self.overlay_render_time_ms = 0.0
        self.jpeg_encode_count = 0
        self.jpeg_encode_time_ms = 0.0
        self.raw_jobs_dropped = 0
        self.processed_jobs_dropped = 0

    def _now(self, now: float | None = None) -> float:
        return float(now if now is not None else time.monotonic())

    def should_publish_raw(self, now: float | None = None) -> bool:
        if not self.raw_publish_enabled:
            return False
        now = self._now(now)
        if self.raw_publish_interval_seconds <= 0:
            return False
        if self.last_raw_publish_at <= 0:
            return True
        return (now - self.last_raw_publish_at) >= self.raw_publish_interval_seconds

    def should_publish_processed(self, now: float | None = None) -> bool:
        if not self.processed_publish_enabled:
            return False
        now = self._now(now)
        if self.processed_publish_interval_seconds <= 0:
            return False
        if self.last_processed_publish_at <= 0:
            return True
        return (
            now - self.last_processed_publish_at
        ) >= self.processed_publish_interval_seconds

    def record_raw_published(self, now: float | None = None) -> None:
        self.last_raw_publish_at = self._now(now)
        self.raw_frames_published += 1

    def record_processed_published(self, now: float | None = None) -> None:
        self.last_processed_publish_at = self._now(now)
        self.processed_frames_published += 1

    def record_raw_skipped(self) -> None:
        self.raw_frames_skipped_by_throttle += 1

    def record_processed_skipped(self) -> None:
        self.processed_frames_skipped_by_throttle += 1

    def record_overlay_render(self) -> None:
        self.overlay_render_count += 1

    def add_overlay_render_time(self, elapsed_ms: float) -> None:
        self.overlay_render_time_ms += max(0.0, float(elapsed_ms))

    def record_jpeg_encode(self) -> None:
        self.jpeg_encode_count += 1

    def add_jpeg_encode_time(self, elapsed_ms: float) -> None:
        self.jpeg_encode_time_ms += max(0.0, float(elapsed_ms))

    def stats(self, now: float | None = None) -> dict[str, float | int]:
        now = self._now(now)
        elapsed = max(0.001, now - self.started_at)
        return {
            "raw_frames_published": self.raw_frames_published,
            "processed_frames_published": self.processed_frames_published,
            "raw_frames_skipped_by_throttle": self.raw_frames_skipped_by_throttle,
            "processed_frames_skipped_by_throttle": (
                self.processed_frames_skipped_by_throttle
            ),
            "jpeg_encode_count": self.jpeg_encode_count,
            "jpeg_encode_time_ms": round(self.jpeg_encode_time_ms, 2),
            "overlay_render_count": self.overlay_render_count,
            "overlay_render_time_ms": round(self.overlay_render_time_ms, 2),
            "raw_jobs_dropped": self.raw_jobs_dropped,
            "processed_jobs_dropped": self.processed_jobs_dropped,
            "effective_raw_publish_fps": round(
                self.raw_frames_published / elapsed,
                2,
            ),
            "effective_processed_publish_fps": round(
                self.processed_frames_published / elapsed,
                2,
            ),
        }
