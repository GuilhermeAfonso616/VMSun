"""Publicacao cadenciada dos frames raw e processado de um worker."""

from __future__ import annotations

import time
from typing import Callable

from app.runtime.visual_publish_scheduler import VisualPublishScheduler


class WorkerVisualPublisher:
    def __init__(
        self,
        *,
        camera_id: int,
        process_pid: int,
        logger,
        scheduler: VisualPublishScheduler,
        frame_store_backend,
        normalize_frame: Callable,
    ):
        self.camera_id = int(camera_id)
        self.process_pid = int(process_pid)
        self.logger = logger
        self.scheduler = scheduler
        self.frame_store = frame_store_backend
        self.normalize_frame = normalize_frame

    def publish(
        self,
        *,
        frame,
        geometry,
        infer_ran: bool,
        decision,
        roi_crop_meta,
        tracks: list[dict],
        visual_tracks: list[dict] | None,
        render_frame: Callable,
    ) -> dict:
        now = time.monotonic()
        raw_due = self.scheduler.should_publish_raw(now)
        processed_due = self.scheduler.should_publish_processed(now)
        raw_publish = None
        processed_publish = None
        overlay_ms = 0.0
        jpeg_ms = 0.0
        annotated_frame = None

        if raw_due:
            raw_publish = self.frame_store.set_raw_frame(self.camera_id, frame)
            if raw_publish and raw_publish.get("ok"):
                self.scheduler.record_raw_published(now)
                raw_encode_ms = float(raw_publish.get("encode_ms", 0.0))
                jpeg_ms += raw_encode_ms
                self.scheduler.record_jpeg_encode()
                self.scheduler.add_jpeg_encode_time(raw_encode_ms)
            else:
                self.scheduler.raw_jobs_dropped += 1
                self.logger.warning(
                    "Raw frame publication failed",
                    extra={
                        "action": "visual_publish_raw",
                        "status": "degraded",
                        "reason": "raw_publish_failed",
                        "worker_pid": self.process_pid,
                        "camera_id": self.camera_id,
                    },
                )
        else:
            self.scheduler.record_raw_skipped()

        if processed_due:
            overlay_started = time.perf_counter()
            annotated_frame = frame.copy()
            render_frame(
                annotated_frame,
                geometry,
                infer_ran,
                decision,
                roi_crop_meta,
                tracks,
                visual_tracks,
            )
            overlay_ms = (time.perf_counter() - overlay_started) * 1000.0
            self.scheduler.record_overlay_render()
            self.scheduler.add_overlay_render_time(overlay_ms)

            display_frame = self.normalize_frame(annotated_frame)
            processed_publish = self.frame_store.set_processed_frame(
                self.camera_id,
                display_frame if display_frame is not None else annotated_frame,
            )
            if processed_publish and processed_publish.get("ok"):
                self.scheduler.record_processed_published(now)
                processed_encode_ms = float(processed_publish.get("encode_ms", 0.0))
                jpeg_ms += processed_encode_ms
                self.scheduler.record_jpeg_encode()
                self.scheduler.add_jpeg_encode_time(processed_encode_ms)
            else:
                self.scheduler.processed_jobs_dropped += 1
                self.logger.warning(
                    "Processed frame publication failed",
                    extra={
                        "action": "visual_publish_processed",
                        "status": "degraded",
                        "reason": "processed_publish_failed",
                        "worker_pid": self.process_pid,
                        "camera_id": self.camera_id,
                    },
                )
        else:
            self.scheduler.record_processed_skipped()

        return {
            "annotated_frame": annotated_frame,
            "raw_publish": raw_publish,
            "processed_publish": processed_publish,
            "overlay_ms": overlay_ms,
            "jpeg_ms": jpeg_ms,
            "raw_due": raw_due,
            "processed_due": processed_due,
            "visual_stats": self.scheduler.stats(now),
        }
