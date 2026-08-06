"""Base do worker de camera.

Concentra captura, inferencia, preprocessamento, eventos e publicacao de
frame/metrica para que as variantes de worker compartilhem o mesmo ciclo.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

from app.analytics.motion_gate import MotionGate
from app.core.logging import get_camera_logger
from app.core.config import settings
from app.core.url_safety import sanitize_url_for_log
from app.db.base import SessionLocal
from app.db.models import Camera
from app.runtime.camera_config import CameraRuntimeConfig, load_camera_runtime_config
from app.runtime.events import EventPipeline
from app.runtime.inference_detection import DetectionService
from app.runtime.inference_scheduling import InferenceDecision, NormalInferenceScheduler
from app.runtime.overlay_renderer import OverlayRenderer
from app.runtime.preprocess import FramePreprocessor
from app.runtime.visual_publish_scheduler import VisualPublishScheduler
from app.runtime.worker_capture_stage import WorkerCaptureStage
from app.runtime.worker_frame_processor import WorkerFrameProcessor
from app.runtime.worker_ownership_guard import WorkerOwnershipGuard
from app.runtime.worker_runtime_status import WorkerRuntimeStatus
from app.core.timezone import utc_now_naive
from app.runtime.worker_metrics_reporter import (
    MetricsAnalyticsContext,
    MetricsFrameContext,
    MetricsTimings,
    WorkerMetricsReporter,
    WorkerMetricsState,
)
from app.runtime.worker_metrics_publisher import WorkerMetricsPublisher
from app.runtime.worker_visual_publisher import WorkerVisualPublisher
from app.services.display_resize import normalize_display_frame
from app.services.event_persistence import EventPersistenceQueue
from app.services.frame_store import frame_store
from app.services.metrics_store import metrics_store
from app.services.track_store import track_store


class BaseCameraWorker:
    mode_name = "normal"

    def __init__(self, camera_id: int, rtsp_url: str, stop_event=None):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.stop_event = stop_event

        self.running = False
        self.thread = None
        self.process_pid = os.getpid()
        self.worker_generation = str(os.environ.get("SERVER_ANALITICO_WORKER_GENERATION") or "legacy")

        self.logger = get_camera_logger(
            "app.worker",
            camera_id=self.camera_id,
            worker_mode=self.mode_name,
        )

        self.preprocessor = FramePreprocessor()
        self.detection_service = DetectionService(camera_id=self.camera_id)
        self._inference_resources_parked = False
        self.event_persistence_queue = EventPersistenceQueue(
            camera_id=self.camera_id,
            worker_mode=self.mode_name,
            maxsize=8,
        )
        self.event_pipeline = EventPipeline(persistence_queue=self.event_persistence_queue)
        self.renderer = OverlayRenderer()
        self.metrics_publisher = WorkerMetricsPublisher()

        self.last_frame_at: datetime | None = None
        self.last_processed_frame_at: datetime | None = None
        self.last_metrics_at: datetime | None = None
        self.health_status: str = "starting"
        self.consecutive_stall_checks: int = 0
        self.restart_count: int = 0
        self.last_restart_at: datetime | None = None
        self.last_restart_reason: str | None = None
        self._runtime_config_signature: tuple | None = None
        self._last_runtime_config_check_ts: float = 0.0
        self._runtime_config_refresh_interval_seconds: float = 2.0

        self.runtime_config = CameraRuntimeConfig()
        self.scheduler = self.build_scheduler(self.runtime_config)
        self.visual_scheduler = VisualPublishScheduler(
            raw_publish_interval_seconds=self.runtime_config.visual_raw_publish_interval_seconds,
            processed_publish_interval_seconds=self.runtime_config.visual_processed_publish_interval_seconds,
            raw_publish_enabled=self.runtime_config.visual_raw_publish_enabled,
            processed_publish_enabled=self.runtime_config.visual_processed_publish_enabled,
        )
        self._visual_publisher = self._build_visual_publisher()
        self.last_health_log_ts = 0.0
        self.capture_stage_started_at: datetime | None = None
        self.raw_fps_current: float = 0.0
        self.processed_fps_current: float = 0.0
        self._raw_fps_counter: int = 0
        self._processed_fps_counter: int = 0
        self._raw_fps_window_start = time.perf_counter()
        self._processed_fps_window_start = time.perf_counter()
        self.motion_gate: MotionGate | None = None
        self.capture_stage = self._build_capture_stage()
        self.capture_service = self.capture_stage.capture_service
        self.frame_mailbox = self.capture_stage.mailbox
        self.capture_thread = self.capture_stage.thread
        self.metrics_reporter = self._build_metrics_reporter()
        self.frame_processor = self._build_frame_processor()
        self._ownership_guard = self._build_ownership_guard()
        self.runtime_status = self._build_runtime_status()

    def build_scheduler(self, runtime_config: CameraRuntimeConfig):
        return NormalInferenceScheduler(inference_interval=runtime_config.normal_inference_interval_seconds)

    def _build_capture_stage(self) -> WorkerCaptureStage:
        return WorkerCaptureStage(
            camera_id=self.camera_id,
            process_pid=self.process_pid,
            rtsp_url=self.rtsp_url,
            worker_mode=self.mode_name,
            logger=self.logger,
            is_running=lambda: self.running,
            stop_requested=self._external_stop_requested,
            capture_drop_frames=lambda: self.runtime_config.capture_drop_frames,
            get_health_status=lambda: self.health_status,
            set_health=self._set_health,
            on_frame_captured=self._on_capture_frame,
            on_restart=self._on_capture_restart,
            failures_before_reconnect=max(
                12,
                settings.camera_health_restart_after_stall_checks * 2,
            ),
        )

    def _on_capture_frame(self, captured_at: datetime) -> None:
        self.last_frame_at = captured_at

    def _on_capture_restart(
        self,
        reconnect_count: int,
        restarted_at: datetime,
        reason: str,
    ) -> None:
        self.restart_count = reconnect_count
        self.last_restart_at = restarted_at
        self.last_restart_reason = reason

    def _build_metrics_reporter(self) -> WorkerMetricsReporter:
        return WorkerMetricsReporter(
            camera_id=self.camera_id,
            process_pid=self.process_pid,
            logger=self.logger,
            publisher=self.metrics_publisher,
            capture_service=self.capture_service,
            frame_mailbox=self.frame_mailbox,
            persistence_queue=self.event_persistence_queue,
            inference_runtime=self.detection_service.runtime_stats,
        )

    def _build_frame_processor(self) -> WorkerFrameProcessor:
        return WorkerFrameProcessor(
            camera_id=self.camera_id,
            process_pid=self.process_pid,
            logger=self.logger,
            preprocessor=self.preprocessor,
            detection_service=self.detection_service,
            event_pipeline=self.event_pipeline,
            track_store_backend=track_store,
            runtime_config=lambda: self.runtime_config,
            motion_gate=lambda: self.motion_gate,
            before_inference=self.before_inference,
            after_inference=self.after_inference,
            get_health_status=lambda: self.health_status,
            set_health=self._set_health,
        )

    def _build_ownership_guard(self) -> WorkerOwnershipGuard:
        return WorkerOwnershipGuard(
            camera_id=self.camera_id,
            process_pid=self.process_pid,
            worker_generation=self.worker_generation,
        )

    def _build_runtime_status(self) -> WorkerRuntimeStatus:
        return WorkerRuntimeStatus(
            camera_id=self.camera_id,
            logger=self.logger,
            worker_pid=self.process_pid,
            worker_generation=self.worker_generation,
            owns_runtime_state=self._ownership_guard.is_owner,
            stop_capture_stage=self._stop_capture_stage,
            release_capture_stage=self.capture_stage.release,
            stop_event_persistence=self._stop_event_persistence,
            clear_published_state=self._clear_published_state,
        )

    def _clear_published_state(self) -> None:
        frame_store.remove_frame(self.camera_id)
        metrics_store.remove_metrics(self.camera_id)

    def _park_inference_resources(self, reason: str) -> None:
        if self._inference_resources_parked:
            return
        result = self.detection_service.release_camera()
        self._inference_resources_parked = bool(result.get("ok"))
        self.logger.warning(
            "Inference resources parked reason=%s result=%s",
            reason,
            result,
            extra={
                "action": "inference_resources_parked",
                "status": "parked" if self._inference_resources_parked else "degraded",
                "reason": reason,
                "worker_pid": self.process_pid,
            },
        )

    @property
    def last_tracks(self) -> list[dict]:
        return self.frame_processor.state.last_tracks

    @last_tracks.setter
    def last_tracks(self, tracks: list[dict]) -> None:
        self.frame_processor.state.last_tracks = tracks

    @property
    def last_tracks_captured_at(self) -> datetime | None:
        return self.frame_processor.state.last_tracks_captured_at

    @last_tracks_captured_at.setter
    def last_tracks_captured_at(self, captured_at: datetime | None) -> None:
        self.frame_processor.state.last_tracks_captured_at = captured_at

    @property
    def last_successful_inference_at(self) -> datetime | None:
        return self.frame_processor.state.last_successful_inference_at

    @last_successful_inference_at.setter
    def last_successful_inference_at(self, inferred_at: datetime | None) -> None:
        self.frame_processor.state.last_successful_inference_at = inferred_at

    def _build_visual_publisher(self) -> WorkerVisualPublisher:
        return WorkerVisualPublisher(
            camera_id=self.camera_id,
            process_pid=self.process_pid,
            logger=self.logger,
            scheduler=self.visual_scheduler,
            frame_store_backend=frame_store,
            normalize_frame=normalize_display_frame,
        )

    def load_runtime_config(self, camera: Optional[Camera]):
        self.runtime_config = load_camera_runtime_config(camera)
        self.scheduler = self.build_scheduler(self.runtime_config)
        self.visual_scheduler = VisualPublishScheduler(
            raw_publish_interval_seconds=self.runtime_config.visual_raw_publish_interval_seconds,
            processed_publish_interval_seconds=self.runtime_config.visual_processed_publish_interval_seconds,
            raw_publish_enabled=self.runtime_config.visual_raw_publish_enabled,
            processed_publish_enabled=self.runtime_config.visual_processed_publish_enabled,
        )
        self._visual_publisher = self._build_visual_publisher()
        self._runtime_config_signature = self._build_runtime_config_signature(camera)
        self._last_runtime_config_check_ts = time.perf_counter()
        self.motion_gate = None
        if bool(settings.motion_gate_enabled):
            self.motion_gate = MotionGate(
                threshold=float(settings.motion_gate_threshold),
                min_interval_seconds=float(settings.motion_gate_min_interval_seconds),
                downscale_width=int(settings.motion_gate_downscale_width),
            )

        self.logger.info(
            "Configuration loaded roi_name=%s line_direction=%s human_modes=%s preset=%s family=%s scene=%s goal=%s motion_enabled=%s",
            self.runtime_config.analytics.roi_name,
            self.runtime_config.analytics.line_direction,
            self.runtime_config.analytics.human_event_modes,
            self.runtime_config.analytic_profile.preset_name,
            self.runtime_config.analytic_profile.camera_family,
            self.runtime_config.analytic_profile.scene_profile,
            self.runtime_config.analytic_profile.analytic_goal,
            self.mode_name == "motion_test",
            extra={
                "action": "load_runtime_config",
                "status": "running",
                "reason": "startup",
                "worker_pid": self.process_pid,
            },
        )

    def _build_runtime_config_signature(self, camera: Optional[Camera]) -> tuple:
        if camera is None:
            return ("camera:none",)

        return (
            str(getattr(camera, "analytics_profile_json", "") or ""),
            str(getattr(camera, "roi_polygon_json", "") or ""),
            getattr(camera, "line_start_x", None),
            getattr(camera, "line_start_y", None),
            getattr(camera, "line_end_x", None),
            getattr(camera, "line_end_y", None),
            str(getattr(camera, "line_direction", "") or ""),
            str(getattr(camera, "analytics_coordinate_space", "") or ""),
            str(getattr(camera, "human_event_modes_json", "") or ""),
            getattr(camera, "human_loitering_seconds", None),
            str(getattr(camera, "human_detection_sensitivity", "") or ""),
            getattr(camera, "motion_idle_interval", None),
            getattr(camera, "motion_active_interval", None),
            getattr(camera, "motion_hold_seconds", None),
            getattr(camera, "motion_detection_hold_seconds", None),
            getattr(camera, "motion_min_motion_frames", None),
            getattr(camera, "motion_downscale_width", None),
            getattr(camera, "motion_min_contour_area", None),
            getattr(camera, "motion_ratio_threshold", None),
            getattr(camera, "motion_global_change_ratio_limit", None),
            getattr(camera, "motion_background_alpha", None),
            getattr(camera, "motion_warmup_frames", None),
        )

    def _refresh_runtime_config_if_needed(self, db) -> bool:
        now = time.perf_counter()
        if (now - self._last_runtime_config_check_ts) < self._runtime_config_refresh_interval_seconds:
            return False

        self._last_runtime_config_check_ts = now
        camera = db.query(Camera).filter(Camera.id == self.camera_id).first()
        if camera is None:
            return False

        signature = self._build_runtime_config_signature(camera)
        if signature == self._runtime_config_signature:
            return False

        self.logger.info(
            "Runtime config changed in DB; reloading without restart",
            extra={
                "action": "runtime_config_reload",
                "status": "running",
                "reason": "config_changed",
                "worker_pid": self.process_pid,
            },
        )
        self.load_runtime_config(camera)
        return True

    def start(self):
        if self.running:
            self.logger.warning(
                "Worker start ignored because it is already running",
                extra={"action": "start_worker", "status": "running", "reason": "already_running", "worker_pid": self.process_pid},
            )
            return

        self.running = True
        self.logger.info(
            "Worker marked as running=True",
            extra={"action": "start_worker", "status": "running", "reason": "manual_start", "worker_pid": self.process_pid},
        )

    def stop(self):
        self.logger.info(
            "Worker stop requested",
            extra={"action": "stop_worker", "status": "stopped", "reason": "manual_stop", "worker_pid": self.process_pid},
        )
        self.running = False
        if self.stop_event is not None:
            try:
                self.stop_event.set()
            except Exception:
                pass

    def _owns_published_runtime_state(self) -> bool:
        """Confirma que este processo ainda e a geracao publicada da camera."""

        return WorkerOwnershipGuard(
            camera_id=self.camera_id,
            process_pid=self.process_pid,
            worker_generation=self.worker_generation,
        ).is_owner()

    def before_inference(self, frame_for_motion) -> InferenceDecision:
        return self.scheduler.evaluate(frame_for_motion=frame_for_motion)

    def after_inference(self, tracks: list[dict]):
        self.scheduler.on_inference_done(tracks)

    def render_frame(self, annotated_frame, geometry, infer_ran: bool, decision: InferenceDecision, roi_crop_meta, tracks, visual_tracks=None):
        if bool(settings.visual_revalidation_gate_enabled):
            display_tracks = visual_tracks or []
        else:
            display_tracks = visual_tracks if visual_tracks is not None else tracks
        self.renderer.draw_tracks(annotated_frame, display_tracks)
        self.renderer.draw_analytics_overlay(
            annotated_frame,
            geometry.roi_polygon,
            geometry.line_pixels,
            self.runtime_config.analytics.roi_name,
            self.runtime_config.analytics.line_direction,
        )

    def compose_status(self, camera: Optional[Camera]) -> str:
        if not self.running:
            return "starting"
        if self.capture_stage_started_at is None:
            return "starting"
        # Receber frames do gateway nao significa que a IA esteja saudavel.
        # Preserve a falha de inferencia ate que uma inferencia realmente
        # conclua, evitando mascarar um detector quebrado como "running".
        if self.health_status in {"degraded", "reconnecting", "offline"}:
            return self.health_status
        if self.last_successful_inference_at is None:
            return "warming_up"
        return "running_motion_test" if self.mode_name == "motion_test" else "running"

    def _set_health(self, status: str, reason: str):
        if self.health_status == status:
            return
        self.health_status = status
        self.logger.info(
            "Worker health state changed to %s",
            status,
            extra={
                "action": "worker_health_change",
                "status": status,
                "reason": reason,
                "worker_pid": self.process_pid,
            },
        )

    def log_worker_health(self, *, fps: float, tracks_count: int, infer_ms: float, loop_ms: float, persistence_stats: dict | None = None):
        now = time.perf_counter()
        if (now - self.last_health_log_ts) < 30.0:
            return

        queue_size = int((persistence_stats or {}).get("queue_size", 0) or 0)
        queued = int((persistence_stats or {}).get("events_queued", 0) or 0)
        persisted = int((persistence_stats or {}).get("events_persisted", 0) or 0)
        failed = int((persistence_stats or {}).get("events_failed", 0) or 0)
        rejected = int((persistence_stats or {}).get("dropped_or_rejected_jobs", 0) or 0)
        persist_latency_ms = float((persistence_stats or {}).get("persist_latency_ms", 0.0) or 0.0)

        self.logger.info(
            "Health worker fps=%.2f raw_fps=%.2f processed_fps=%.2f tracks=%s infer_ms=%.2f loop_ms=%.2f reconnects=%s dropped=%s queue_dropped=%s persistence_queue=%s queued=%s persisted=%s failed=%s rejected=%s persist_latency_ms=%.2f last_inference_at=%s",
            fps,
            self.raw_fps_current,
            self.processed_fps_current,
            tracks_count,
            infer_ms,
            loop_ms,
            self.capture_service.reconnect_count,
            self.capture_service.dropped_frames_count,
            self.frame_mailbox.dropped_count,
            queue_size,
            queued,
            persisted,
            failed,
            rejected,
            persist_latency_ms,
            self.last_successful_inference_at,
            extra={
                "action": "worker_health_snapshot",
                "status": self.health_status,
                "reason": "periodic_snapshot",
                "worker_pid": self.process_pid,
            },
        )
        self.last_health_log_ts = now

    def _external_stop_requested(self) -> bool:
        if self.stop_event is None:
            return False
        try:
            return bool(self.stop_event.is_set())
        except Exception:
            return False

    def _roll_fps_window(self, *, kind: str) -> float:
        now = time.perf_counter()
        if kind == "raw":
            counter = self._raw_fps_counter + 1
            window_start = self._raw_fps_window_start
            current = self.raw_fps_current
            if (now - window_start) >= 1.0:
                current = counter / max(0.001, now - window_start)
                counter = 0
                window_start = now
            self._raw_fps_counter = counter
            self._raw_fps_window_start = window_start
            self.raw_fps_current = current
            return current

        counter = self._processed_fps_counter + 1
        window_start = self._processed_fps_window_start
        current = self.processed_fps_current
        if (now - window_start) >= 1.0:
            current = counter / max(0.001, now - window_start)
            counter = 0
            window_start = now
        self._processed_fps_counter = counter
        self._processed_fps_window_start = window_start
        self.processed_fps_current = current
        return current

    def _publish_visual_frame(
        self,
        *,
        frame,
        geometry,
        infer_ran: bool,
        decision: InferenceDecision,
        roi_crop_meta,
        tracks: list[dict],
        visual_tracks: list[dict] | None = None,
        frame_width: int,
        frame_height: int,
    ):
        publisher = getattr(self, "_visual_publisher", None)
        if (
            publisher is None
            or publisher.scheduler is not self.visual_scheduler
            or publisher.frame_store is not frame_store
            or publisher.normalize_frame is not normalize_display_frame
        ):
            publisher = self._build_visual_publisher()
            self._visual_publisher = publisher
        return publisher.publish(
            frame=frame,
            geometry=geometry,
            infer_ran=infer_ran,
            decision=decision,
            roi_crop_meta=roi_crop_meta,
            tracks=tracks,
            visual_tracks=visual_tracks,
            render_frame=self.render_frame,
        )

    def _start_capture_stage(self) -> None:
        self.capture_stage.start()
        self.capture_thread = self.capture_stage.thread
        self.capture_stage_started_at = self.capture_stage.started_at

    def _stop_capture_stage(self) -> None:
        self.capture_stage.stop(timeout=5.0)
        self.capture_thread = self.capture_stage.thread

    def _start_event_persistence(self) -> None:
        self.event_persistence_queue.start()

    def _stop_event_persistence(self) -> None:
        try:
            self.event_persistence_queue.stop(drain=True, timeout=5.0)
        except Exception:
            self.logger.exception(
                "Event persistence shutdown failed",
                extra={
                    "action": "event_persistence_stop_failed",
                    "status": "degraded",
                    "reason": "shutdown_failure",
                    "worker_pid": self.process_pid,
                },
            )

    def run(self):
        db = SessionLocal()
        self.logger.info(
            "Worker run started rtsp_url=%s",
            sanitize_url_for_log(self.rtsp_url),
            extra={
                "action": "run_worker",
                "status": "starting",
                "reason": "process_entry",
                "worker_pid": self.process_pid,
            },
        )

        try:
            camera = self.runtime_status.fetch_camera(db)
            self.load_runtime_config(camera)
            self.runtime_status.mark_starting(db, camera)

            self.running = True
            self._start_event_persistence()
            self._start_capture_stage()
            self._set_health("warming_up", "capture_stage_started")
            self.runtime_status.mark_warming_up(db, camera)

            if self._external_stop_requested():
                self.logger.info(
                    "Stop detected immediately after capture open",
                    extra={"action": "run_worker", "status": "stopped", "reason": "stop_requested", "worker_pid": self.process_pid},
                )
                return

            fps_counter = 0
            fps_window_start = time.perf_counter()
            current_fps = 0.0

            while self.running and not self._external_stop_requested():
                loop_started = time.perf_counter()

                self._refresh_runtime_config_if_needed(db)

                captured_frame = self.frame_mailbox.get_latest(timeout=0.5)
                if captured_frame is None:
                    if self.capture_service.inference_pool_release_due():
                        self._park_inference_resources("gateway_circuit_open")
                    if not self.capture_thread or not self.capture_thread.is_alive():
                        self._set_health("offline", "capture_stage_stopped")
                        break
                    continue

                frame = captured_frame.frame
                if self._inference_resources_parked:
                    self._inference_resources_parked = False
                    self.logger.info(
                        "Inference resources will be reassigned after stream recovery",
                        extra={
                            "action": "inference_resources_resume",
                            "status": "recovering",
                            "reason": "fresh_frame_after_park",
                            "worker_pid": self.process_pid,
                        },
                    )
                camera = self.runtime_status.mark_running_on_first_frame(db, camera)

                frame_result = self.frame_processor.process(
                    frame=frame,
                    captured_at=captured_frame.captured_at,
                    db=db,
                    frame_context=captured_frame.metadata,
                )
                frame_width = frame_result.frame_width
                frame_height = frame_result.frame_height
                geometry = frame_result.geometry
                infer_frame_meta = frame_result.inference_frame
                decision = frame_result.decision
                tracks = frame_result.tracks
                display_tracks = frame_result.display_tracks
                infer_ms = frame_result.infer_ms
                infer_ran = frame_result.infer_ran
                inference_result_age_ms = frame_result.inference_result_age_ms
                visual_tracks_stale = frame_result.visual_tracks_stale

                visual_publish = self._publish_visual_frame(
                    frame=frame,
                    geometry=geometry,
                    infer_ran=infer_ran,
                    decision=decision,
                    roi_crop_meta=infer_frame_meta.roi_crop_meta,
                    tracks=tracks,
                    visual_tracks=display_tracks,
                    frame_width=frame_width,
                    frame_height=frame_height,
                )
                annotated_frame = visual_publish["annotated_frame"]
                plot_ms = float(visual_publish["overlay_ms"])
                jpeg_ms = float(visual_publish["jpeg_ms"])
                visual_stats = visual_publish["visual_stats"]
                self.raw_fps_current = float(visual_stats.get("effective_raw_publish_fps", 0.0))
                self.processed_fps_current = float(visual_stats.get("effective_processed_publish_fps", 0.0))

                processed_publish = visual_publish["processed_publish"]
                if processed_publish and processed_publish.get("updated_at"):
                    self.last_processed_frame_at = processed_publish.get("updated_at") or utc_now_naive()

                fps_counter += 1
                now = time.perf_counter()
                if now - fps_window_start >= 1.0:
                    current_fps = fps_counter / (now - fps_window_start)
                    fps_counter = 0
                    fps_window_start = now

                loop_ms = (time.perf_counter() - loop_started) * 1000.0
                metrics_result = self.metrics_reporter.publish(
                    timings=MetricsTimings(
                        read_ms=captured_frame.read_ms,
                        infer_ms=infer_ms,
                        plot_ms=plot_ms,
                        jpeg_ms=jpeg_ms,
                        loop_ms=loop_ms,
                        current_fps=current_fps,
                    ),
                    frame=MetricsFrameContext(
                        frame=frame,
                        frame_width=frame_width,
                        frame_height=frame_height,
                        infer_input_width=infer_frame_meta.input_width,
                        infer_input_height=infer_frame_meta.input_height,
                        tracks=display_tracks,
                    ),
                    analytics=MetricsAnalyticsContext(
                        roi_polygon=geometry.roi_polygon,
                        roi_name=self.runtime_config.analytics.roi_name,
                        roi_crop_active=infer_frame_meta.roi_crop_active,
                        roi_crop_meta=infer_frame_meta.roi_crop_meta,
                        line_pixels=geometry.line_pixels,
                        line_direction=self.runtime_config.analytics.line_direction,
                        motion_info=decision.motion_info,
                        inference_result_age_ms=inference_result_age_ms,
                        visual_tracks_stale=visual_tracks_stale,
                    ),
                    state=WorkerMetricsState(
                        last_successful_inference_at=self.last_successful_inference_at,
                        last_frame_at=self.last_frame_at,
                        last_processed_frame_at=self.last_processed_frame_at,
                        health_status=self.health_status,
                        consecutive_stall_checks=self.consecutive_stall_checks,
                        worker_mode=self.mode_name,
                        worker_generation=self.worker_generation,
                        raw_fps=self.raw_fps_current,
                        processed_fps=self.processed_fps_current,
                    ),
                    visual_stats=visual_stats,
                )
                self.last_metrics_at = metrics_result.last_metrics_at
                persistence_stats = metrics_result.persistence_stats

                self.log_worker_health(
                    fps=current_fps,
                    tracks_count=len(tracks),
                    infer_ms=infer_ms,
                    loop_ms=loop_ms,
                    persistence_stats=persistence_stats,
                )

        except Exception:
            self.logger.exception(
                "Fatal worker failure",
                extra={
                    "action": "run_worker",
                    "status": "error",
                    "reason": "fatal_exception",
                    "worker_pid": self.process_pid,
                },
            )
            self.runtime_status.mark_fatal_error(db)
        finally:
            self.runtime_status.cleanup(db)

            if self._owns_published_runtime_state():
                try:
                    self.detection_service.release_camera()
                except Exception:
                    self.logger.exception(
                        "Failed to release inference resources during worker shutdown",
                        extra={
                            "action": "inference_resources_release_failed",
                            "status": "degraded",
                            "reason": "worker_shutdown",
                            "worker_pid": self.process_pid,
                        },
                    )

            db.close()
            self.running = False
            self._set_health("stopped", "worker_shutdown")
            self.logger.info(
                "Worker finished",
                extra={
                    "action": "run_worker",
                    "status": "stopped",
                    "reason": "shutdown",
                    "worker_pid": self.process_pid,
                },
            )
