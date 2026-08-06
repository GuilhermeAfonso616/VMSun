"""Composicao e publicacao das metricas produzidas pelo worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.core.timezone import utc_now_naive
from app.services.display_resize import DISPLAY_FRAME_HEIGHT, DISPLAY_FRAME_WIDTH


@dataclass(frozen=True, slots=True)
class MetricsTimings:
    read_ms: float
    infer_ms: float
    plot_ms: float
    jpeg_ms: float
    loop_ms: float
    current_fps: float


@dataclass(frozen=True, slots=True)
class MetricsFrameContext:
    frame: object
    frame_width: int
    frame_height: int
    infer_input_width: int
    infer_input_height: int
    tracks: list[dict]


@dataclass(frozen=True, slots=True)
class MetricsAnalyticsContext:
    roi_polygon: object
    roi_name: str | None
    roi_crop_active: bool
    roi_crop_meta: object
    line_pixels: object
    line_direction: str
    motion_info: dict | None
    inference_result_age_ms: float | None
    visual_tracks_stale: bool


@dataclass(frozen=True, slots=True)
class WorkerMetricsState:
    last_successful_inference_at: datetime | None
    last_frame_at: datetime | None
    last_processed_frame_at: datetime | None
    health_status: str
    consecutive_stall_checks: int
    worker_mode: str
    worker_generation: str
    raw_fps: float
    processed_fps: float


@dataclass(frozen=True, slots=True)
class MetricsPublicationResult:
    last_metrics_at: datetime
    persistence_stats: dict
    payload: dict | None


class WorkerMetricsReporter:
    """Traduz snapshots tipados para o contrato legado do metrics store."""

    def __init__(
        self,
        *,
        camera_id: int,
        process_pid: int,
        logger,
        publisher,
        capture_service,
        frame_mailbox,
        persistence_queue,
        inference_runtime: Callable[[], dict],
        utcnow: Callable[[], datetime] = utc_now_naive,
    ):
        self.camera_id = int(camera_id)
        self.process_pid = int(process_pid)
        self.logger = logger
        self.publisher = publisher
        self.capture_service = capture_service
        self.frame_mailbox = frame_mailbox
        self.persistence_queue = persistence_queue
        self.inference_runtime = inference_runtime
        self.utcnow = utcnow

    def publish(
        self,
        *,
        timings: MetricsTimings,
        frame: MetricsFrameContext,
        analytics: MetricsAnalyticsContext,
        state: WorkerMetricsState,
        visual_stats: dict,
    ) -> MetricsPublicationResult:
        attempted_at = self.utcnow()
        persistence_stats = self.persistence_queue.stats()
        payload = None

        try:
            gateway_recovery_last_success_at = (
                datetime.fromtimestamp(
                    self.capture_service.gateway_recovery_last_success_wall_at,
                    timezone.utc,
                ).replace(tzinfo=None)
                if self.capture_service.gateway_recovery_last_success_wall_at is not None
                else None
            )
            gateway_fallback_started_at = (
                datetime.fromtimestamp(
                    self.capture_service.gateway_fallback_started_wall_at,
                    timezone.utc,
                ).replace(tzinfo=None)
                if self.capture_service.gateway_fallback_started_wall_at is not None
                else None
            )
            payload = self.publisher.publish(
                self.camera_id,
                read_ms=timings.read_ms,
                infer_ms=timings.infer_ms,
                plot_ms=timings.plot_ms,
                jpeg_ms=timings.jpeg_ms,
                loop_ms=timings.loop_ms,
                current_fps=timings.current_fps,
                frame=frame.frame,
                infer_input_width=frame.infer_input_width,
                infer_input_height=frame.infer_input_height,
                tracks_count=len(frame.tracks),
                tracks=frame.tracks,
                reconnect_count=self.capture_service.reconnect_count,
                dropped_frames_count=self.capture_service.dropped_frames_count,
                last_successful_inference_at=state.last_successful_inference_at,
                last_frame_at=state.last_frame_at,
                last_processed_frame_at=state.last_processed_frame_at,
                last_metrics_at=attempted_at,
                health_status=state.health_status,
                consecutive_stall_checks=state.consecutive_stall_checks,
                roi_polygon=analytics.roi_polygon,
                roi_name=analytics.roi_name,
                roi_crop_active=analytics.roi_crop_active,
                roi_crop_meta=analytics.roi_crop_meta,
                line_pixels=analytics.line_pixels,
                line_direction=analytics.line_direction,
                worker_mode=state.worker_mode,
                worker_generation=state.worker_generation,
                capture_source=self.capture_service.capture_source,
                gateway_fallback_active=(
                    self.capture_service.gateway_fallback_started_at is not None
                ),
                gateway_recovery_count=self.capture_service.gateway_recovery_count,
                gateway_recovery_last_success_at=gateway_recovery_last_success_at,
                gateway_fallback_started_at=gateway_fallback_started_at,
                frame_transport_metrics=(
                    self.capture_service.frame_transport_snapshot()
                    if callable(
                        getattr(
                            self.capture_service,
                            "frame_transport_snapshot",
                            None,
                        )
                    )
                    else {}
                ),
                motion_info=analytics.motion_info,
                frame_width=frame.frame_width,
                frame_height=frame.frame_height,
                source_frame_width=frame.frame_width,
                source_frame_height=frame.frame_height,
                display_frame_width=DISPLAY_FRAME_WIDTH,
                display_frame_height=DISPLAY_FRAME_HEIGHT,
                capture_queue_dropped_frames=self.frame_mailbox.dropped_count,
                raw_fps=state.raw_fps,
                processed_fps=state.processed_fps,
                event_persistence_queue_size=persistence_stats.get("queue_size", 0),
                event_persistence_events_queued=persistence_stats.get(
                    "events_queued", 0
                ),
                event_persistence_events_persisted=persistence_stats.get(
                    "events_persisted", 0
                ),
                event_persistence_events_failed=persistence_stats.get(
                    "events_failed", 0
                ),
                event_persistence_dropped_or_rejected_jobs=persistence_stats.get(
                    "dropped_or_rejected_jobs", 0
                ),
                event_persistence_latency_ms=persistence_stats.get(
                    "persist_latency_ms", 0.0
                ),
                event_persistence_last_latency_ms=persistence_stats.get(
                    "last_persist_latency_ms", 0.0
                ),
                raw_frames_published=visual_stats.get("raw_frames_published", 0),
                processed_frames_published=visual_stats.get(
                    "processed_frames_published", 0
                ),
                raw_frames_skipped_by_throttle=visual_stats.get(
                    "raw_frames_skipped_by_throttle", 0
                ),
                processed_frames_skipped_by_throttle=visual_stats.get(
                    "processed_frames_skipped_by_throttle", 0
                ),
                jpeg_encode_count=visual_stats.get("jpeg_encode_count", 0),
                jpeg_encode_time_ms=visual_stats.get("jpeg_encode_time_ms", 0.0),
                overlay_render_count=visual_stats.get("overlay_render_count", 0),
                overlay_render_time_ms=visual_stats.get(
                    "overlay_render_time_ms", 0.0
                ),
                effective_raw_publish_fps=visual_stats.get(
                    "effective_raw_publish_fps", 0.0
                ),
                effective_processed_publish_fps=visual_stats.get(
                    "effective_processed_publish_fps", 0.0
                ),
                visual_queue_size=0,
                visual_jobs_dropped=(
                    visual_stats.get("raw_jobs_dropped", 0)
                    + visual_stats.get("processed_jobs_dropped", 0)
                ),
                inference_result_age_ms=analytics.inference_result_age_ms,
                visual_tracks_stale=analytics.visual_tracks_stale,
                inference_runtime=self.inference_runtime(),
            )
            if isinstance(payload, dict) and payload.get("updated_at"):
                attempted_at = datetime.fromisoformat(str(payload["updated_at"]))
        except Exception:
            self.logger.exception(
                "Failed to publish metrics",
                extra={
                    "action": "publish_metrics",
                    "status": "degraded",
                    "reason": "metrics_publish_failed",
                    "worker_pid": self.process_pid,
                },
            )

        return MetricsPublicationResult(
            last_metrics_at=attempted_at,
            persistence_stats=persistence_stats,
            payload=payload,
        )
