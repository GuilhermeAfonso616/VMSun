"""Processamento de um frame capturado pelo worker de camera."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from app.core.config import settings
from app.core.timezone import utc_now_naive
from app.runtime.box_latency_diagnostics import (
    BoxLatencyDiagnostics,
    camera_is_selected,
)
from app.runtime.inference_detection import InferenceBackpressureError
from app.runtime.inference_scheduling import InferenceDecision


@dataclass(slots=True)
class FrameProcessingState:
    last_tracks: list[dict] = field(default_factory=list)
    last_tracks_captured_at: datetime | None = None
    last_successful_inference_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FrameProcessingResult:
    frame_width: int
    frame_height: int
    geometry: object
    inference_frame: object
    decision: InferenceDecision
    tracks: list[dict]
    display_tracks: list[dict]
    infer_ms: float
    infer_ran: bool
    inference_result_age_ms: float | None
    visual_tracks_stale: bool
    box_latency: dict | None


class WorkerFrameProcessor:
    """Coordena preprocessamento, inferencia, eventos e tracks visuais."""

    def __init__(
        self,
        *,
        camera_id: int,
        process_pid: int,
        logger,
        preprocessor,
        detection_service,
        event_pipeline,
        track_store_backend,
        runtime_config: Callable[[], object],
        motion_gate: Callable[[], object | None],
        before_inference: Callable[[object], InferenceDecision],
        after_inference: Callable[[list[dict]], None],
        get_health_status: Callable[[], str],
        set_health: Callable[[str, str], None],
        runtime_settings=settings,
        perf_counter: Callable[[], float] = time.perf_counter,
        utcnow: Callable[[], datetime] = utc_now_naive,
    ):
        self.camera_id = int(camera_id)
        self.process_pid = int(process_pid)
        self.logger = logger
        self.preprocessor = preprocessor
        self.detection_service = detection_service
        self.event_pipeline = event_pipeline
        self.track_store = track_store_backend
        self.runtime_config = runtime_config
        self.motion_gate = motion_gate
        self.before_inference = before_inference
        self.after_inference = after_inference
        self.get_health_status = get_health_status
        self.set_health = set_health
        self.settings = runtime_settings
        self.perf_counter = perf_counter
        self.utcnow = utcnow
        self.state = FrameProcessingState()
        self.last_motion_gate_log_ts = 0.0
        self.box_latency_diagnostics = BoxLatencyDiagnostics(
            max_samples=int(
                getattr(runtime_settings, "box_latency_diagnostics_sample_window", 300)
                or 300
            )
        )

    def _diagnostics_enabled(self) -> bool:
        return camera_is_selected(
            self.camera_id,
            enabled=bool(
                getattr(self.settings, "box_latency_diagnostics_enabled", False)
            ),
            camera_ids=str(
                getattr(self.settings, "box_latency_diagnostics_camera_ids", "")
                or ""
            ),
        )

    def _fast_path_enabled(self) -> bool:
        return camera_is_selected(
            self.camera_id,
            enabled=bool(getattr(self.settings, "visual_fast_path_enabled", False)),
            camera_ids=str(
                getattr(self.settings, "visual_fast_path_camera_ids", "") or ""
            ),
        )

    def _visual_frame_age_limit_ms(self) -> int:
        try:
            limit_ms = max(
                0,
                int(
                    getattr(
                        self.settings,
                        "visual_inference_max_frame_age_ms",
                        0,
                    )
                    or 0
                ),
            )
        except (TypeError, ValueError):
            return 0
        if limit_ms <= 0:
            return 0
        if not camera_is_selected(
            self.camera_id,
            enabled=True,
            camera_ids=str(
                getattr(
                    self.settings,
                    "visual_inference_max_frame_age_camera_ids",
                    "",
                )
                or ""
            ),
        ):
            return 0
        return limit_ms

    @staticmethod
    def _wall_ns(value) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        # Go emits RFC3339Nano with up to nine fractional digits, while
        # datetime.fromisoformat accepts microsecond precision.
        text = re.sub(r"(\.\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r"\1", text)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = parsed.astimezone(timezone.utc) - datetime(
            1970, 1, 1, tzinfo=timezone.utc
        )
        return (
            (delta.days * 86_400 + delta.seconds) * 1_000_000_000
            + delta.microseconds * 1_000
        )

    def _frame_context(self, supplied: dict | None) -> dict:
        context = dict(supplied or {})
        if not context.get("gateway_received_at_ns"):
            context["gateway_received_at_ns"] = self._wall_ns(
                context.get("gateway_received_at")
            )
        context.setdefault("camera_id", self.camera_id)
        context.setdefault("source_frame_captured_at_ns", None)
        context.setdefault("source_pts", None)
        context.setdefault("capture_clock", "unknown")
        return context

    def _combine_motion_gate(
        self,
        *,
        frame,
        roi_polygon,
        decision: InferenceDecision,
        gate,
    ) -> InferenceDecision:
        gate_decision = gate.evaluate(frame, roi_polygon=roi_polygon)
        decision = InferenceDecision(
            should_infer=bool(decision.should_infer and gate_decision.should_infer),
            motion_info={
                **(decision.motion_info or {}),
                "motion_gate": gate_decision.as_dict(),
            },
        )
        if gate_decision.invalid_reason:
            self.state.last_tracks = []

        now = self.perf_counter()
        if (now - self.last_motion_gate_log_ts) < 1.0:
            return decision

        if decision.should_infer:
            self.logger.info(
                "motion_gate_trigger score=%.5f threshold=%.5f forced_by_interval=%s",
                gate_decision.motion_score_used,
                gate_decision.threshold,
                gate_decision.forced_by_interval,
                extra={
                    "action": "motion_gate_trigger",
                    "status": self.get_health_status(),
                    "reason": (
                        "motion_detected"
                        if gate_decision.has_motion
                        else "forced_interval"
                    ),
                    "worker_pid": self.process_pid,
                },
            )
        else:
            self.logger.info(
                "motion_gate_skip score=%.5f threshold=%.5f",
                gate_decision.motion_score_used,
                gate_decision.threshold,
                extra={
                    "action": "motion_gate_skip",
                    "status": self.get_health_status(),
                    "reason": gate_decision.invalid_reason or "no_motion",
                    "worker_pid": self.process_pid,
                },
            )
        self.last_motion_gate_log_ts = now
        return decision

    def _run_inference(
        self,
        *,
        decision: InferenceDecision,
        inference_frame,
        captured_at: datetime,
        frame_width: int,
        frame_height: int,
        gate,
    ) -> tuple[list[dict], float, bool]:
        tracks = self.state.last_tracks
        if not decision.should_infer:
            self.logger.debug(
                "Inference skipped by scheduler",
                extra={
                    "action": "inference",
                    "status": "running",
                    "reason": "scheduler_skip",
                    "worker_pid": self.process_pid,
                },
            )
            return tracks, 0.0, False

        if gate is not None:
            gate.mark_inference()

        try:
            tracks, infer_ms = self.detection_service.infer(
                inference_frame.frame,
                offset_x=inference_frame.offset_x,
                offset_y=inference_frame.offset_y,
                scale_x=inference_frame.scale_x,
                scale_y=inference_frame.scale_y,
            )
            self.state.last_tracks = tracks
            self.state.last_tracks_captured_at = captured_at
            self.state.last_successful_inference_at = self.utcnow()
            self.after_inference(tracks)
            self.set_health("running", "inference_ok")
            self.logger.debug(
                "Inference executed infer_ms=%.2f tracks=%s roi_crop_active=%s",
                infer_ms,
                len(tracks),
                inference_frame.roi_crop_active,
                extra={
                    "action": "inference",
                    "status": "running",
                    "reason": "inference_ok",
                    "worker_pid": self.process_pid,
                },
            )
            return tracks, infer_ms, True
        except InferenceBackpressureError as exc:
            self.logger.warning(
                "Inference deferred by pool backpressure roi_crop_active=%s input=%sx%s error=%s",
                inference_frame.roi_crop_active,
                inference_frame.input_width,
                inference_frame.input_height,
                exc,
                extra={
                    "action": "inference",
                    "status": "degraded",
                    "reason": "pool_backpressure",
                    "worker_pid": self.process_pid,
                },
            )
            self.set_health("degraded", "pool_backpressure")
            return self.state.last_tracks, 0.0, False
        except Exception:
            self.logger.exception(
                "Inference failed roi_crop_active=%s input=%sx%s",
                inference_frame.roi_crop_active,
                inference_frame.input_width,
                inference_frame.input_height,
                extra={
                    "action": "inference",
                    "status": "degraded",
                    "reason": "inference_failed",
                    "worker_pid": self.process_pid,
                },
            )
            self.state.last_tracks = []
            self.state.last_tracks_captured_at = None
            self.track_store.set_tracks(
                self.camera_id,
                [],
                frame_width=frame_width,
                frame_height=frame_height,
            )
            self.set_health("degraded", "inference_failed")
            return [], 0.0, False

    def _process_events(self, *, tracks, db, frame, infer_ran: bool, gate) -> None:
        try:
            self.event_pipeline.process(
                self.camera_id,
                tracks,
                db,
                frame,
                raw_frame=frame,
                annotated_frame=None,
                detections_fresh=infer_ran,
                motion_gate=gate,
            )
        except Exception:
            self.logger.exception(
                "Event pipeline failed",
                extra={
                    "action": "event_pipeline",
                    "status": "degraded",
                    "reason": "event_pipeline_failed",
                    "worker_pid": self.process_pid,
                },
            )
            db.rollback()

    def _select_visual_tracks(
        self,
        *,
        tracks: list[dict],
    ) -> tuple[list[dict], float | None, bool]:
        result_age_ms = None
        result_age_seconds = None
        if self.state.last_tracks_captured_at is not None:
            result_age_seconds = max(
                0.0,
                (self.utcnow() - self.state.last_tracks_captured_at).total_seconds(),
            )
            result_age_ms = round(result_age_seconds * 1000.0, 2)

        max_visual_age = max(
            0.0,
            float(
                getattr(self.settings, "visual_max_result_age_seconds", 1.0)
                or 0.0
            ),
        )
        visual_tracks_stale = bool(
            tracks
            and max_visual_age > 0
            and result_age_seconds is not None
            and result_age_seconds > max_visual_age
        )
        if visual_tracks_stale:
            display_tracks = []
            self.logger.info(
                "Visual tracks suppressed because inference result is stale age_ms=%.2f max_age_seconds=%.2f tracks=%s",
                result_age_ms or 0.0,
                max_visual_age,
                len(tracks),
                extra={
                    "action": "visual_tracks",
                    "status": "suppressed",
                    "reason": "stale_result",
                    "worker_pid": self.process_pid,
                },
            )
        elif bool(self.settings.visual_ia_boxes_enabled):
            display_tracks = tracks
        elif bool(self.settings.visual_revalidation_gate_enabled):
            display_tracks = self.event_pipeline.visual_tracks(now=self.utcnow())
        else:
            display_tracks = tracks
        return display_tracks, result_age_ms, visual_tracks_stale

    def process(
        self,
        *,
        frame,
        captured_at: datetime,
        db,
        frame_context: dict | None = None,
    ) -> FrameProcessingResult:
        trace_context = self._frame_context(frame_context)
        diagnostics_enabled = self._diagnostics_enabled()
        runtime_config = self.runtime_config()
        frame_height, frame_width = frame.shape[:2]
        geometry = self.preprocessor.build_geometry(
            runtime_config.analytics,
            frame_width,
            frame_height,
        )
        self.event_pipeline.update_geometry(
            geometry.roi_polygon,
            geometry.line_pixels,
            runtime_config.analytics.line_direction,
            human_event_modes=runtime_config.analytics.human_event_modes,
            human_loitering_seconds=runtime_config.analytics.human_loitering_seconds,
            human_detection_sensitivity=runtime_config.analytics.human_detection_sensitivity,
            camera_profile=runtime_config.analytic_profile,
            frame_width=geometry.frame_width,
            frame_height=geometry.frame_height,
        )

        inference_frame = self.preprocessor.build_inference_frame(
            frame,
            geometry.roi_polygon,
            max_width=runtime_config.processing_max_width,
            max_height=runtime_config.processing_max_height,
            allow_upscale=runtime_config.processing_upscale_small_frames,
        )
        decision = self.before_inference(inference_frame.frame)
        gate = self.motion_gate()
        if gate is not None:
            decision = self._combine_motion_gate(
                frame=frame,
                roi_polygon=geometry.roi_polygon,
                decision=decision,
                gate=gate,
            )

        frame_age_limit_ms = self._visual_frame_age_limit_ms()
        gateway_received_at_ns = trace_context.get("gateway_received_at_ns")
        if frame_age_limit_ms > 0 and gateway_received_at_ns:
            visual_frame_age_ms = max(
                0.0,
                (time.time_ns() - int(gateway_received_at_ns)) / 1_000_000.0,
            )
            if visual_frame_age_ms > frame_age_limit_ms:
                decision = InferenceDecision(
                    should_infer=False,
                    motion_info={
                        **(decision.motion_info or {}),
                        "visual_frame_stale": True,
                        "visual_frame_age_ms": round(visual_frame_age_ms, 3),
                        "visual_frame_age_clock": "gateway_receive_wall_clock",
                    },
                )
                self.state.last_tracks = []
                self.state.last_tracks_captured_at = None
                self.box_latency_diagnostics.increment(
                    "visual_updates_stale_total"
                )

        inference_started_perf = self.perf_counter()
        inference_started_at_ns = time.time_ns()
        tracks, infer_ms, infer_ran = self._run_inference(
            decision=decision,
            inference_frame=inference_frame,
            captured_at=captured_at,
            frame_width=frame_width,
            frame_height=frame_height,
            gate=gate,
        )
        inference_completed_perf = self.perf_counter()
        inference_completed_at_ns = time.time_ns()
        inference_runtime = {}
        runtime_stats = getattr(self.detection_service, "runtime_stats", None)
        if callable(runtime_stats):
            try:
                inference_runtime = dict(runtime_stats())
            except Exception:
                inference_runtime = {}

        sample = {
            "camera_id": self.camera_id,
            "generation_id": trace_context.get("generation_id"),
            "frame_id": trace_context.get("frame_id"),
            "source_pts": trace_context.get("source_pts"),
            "source_frame_captured_at_ns": trace_context.get(
                "source_frame_captured_at_ns"
            ),
            "gateway_received_at_ns": trace_context.get("gateway_received_at_ns"),
            "gateway_published_at_monotonic_ns": trace_context.get(
                "gateway_published_at_monotonic_ns"
            ),
            "worker_received_at_ns": trace_context.get("worker_received_at_ns"),
            "inference_enqueued_at_ns": None,
            "inference_started_at_ns": inference_started_at_ns,
            "inference_completed_at_ns": inference_completed_at_ns,
            "tracking_completed_at_ns": None,
            "gateway_frame_age_ms": trace_context.get("gateway_frame_age_ms"),
            "gateway_to_worker_ms": None,
            "worker_to_inference_ms": None,
            "inference_queue_wait_ms": inference_runtime.get("last_wait_ms"),
            "inference_ms": infer_ms,
            "tracking_ms": None,
            "event_pipeline_ms": None,
            "ia2_ms": None,
            "ia3_ms": None,
            "ia1_to_track_publish_ms": None,
            "ia1_to_fast_publish_ms": None,
            "ia1_to_traditional_publish_ms": None,
            "box_total_age_ms": None,
            "box_partial_age_ms": None,
            "capture_clock": trace_context.get("capture_clock") or "unknown",
        }
        gateway_received_ns = trace_context.get("gateway_received_at_ns")
        worker_received_ns = trace_context.get("worker_received_at_ns")
        worker_received_mono_ns = trace_context.get("worker_received_monotonic_ns")
        if gateway_received_ns and worker_received_ns:
            sample["gateway_to_worker_ms"] = max(
                0.0, (worker_received_ns - gateway_received_ns) / 1_000_000.0
            )
        if worker_received_mono_ns:
            sample["worker_to_inference_ms"] = max(
                0.0,
                (
                    int(inference_started_perf * 1_000_000_000)
                    - int(worker_received_mono_ns)
                )
                / 1_000_000.0,
            )

        display_tracks, result_age_ms, visual_tracks_stale = (
            self._select_visual_tracks(tracks=tracks)
        )
        fast_path_enabled = self._fast_path_enabled()
        if visual_tracks_stale:
            self.box_latency_diagnostics.increment("visual_updates_stale_total")
        if not display_tracks:
            self.box_latency_diagnostics.increment("visual_empty_results_total")

        def publish_tracks(*, path: str, tolerate_failure: bool = False) -> bool:
            tracks_published_at_ns = time.time_ns()
            sample["tracks_published_at_ns"] = tracks_published_at_ns
            sample["ia1_to_track_publish_ms"] = max(
                0.0,
                (self.perf_counter() - inference_completed_perf) * 1000.0,
            )
            if path == "fast":
                sample["visual_fast_path_published_at_ns"] = (
                    tracks_published_at_ns
                )
                sample["ia1_to_fast_publish_ms"] = sample[
                    "ia1_to_track_publish_ms"
                ]
            else:
                sample["traditional_tracks_published_at_ns"] = (
                    tracks_published_at_ns
                )
                sample["ia1_to_traditional_publish_ms"] = sample[
                    "ia1_to_track_publish_ms"
                ]
            if gateway_received_ns:
                sample["box_partial_age_ms"] = max(
                    0.0,
                    (tracks_published_at_ns - gateway_received_ns) / 1_000_000.0,
                )
            source_captured_ns = trace_context.get("source_frame_captured_at_ns")
            if source_captured_ns:
                sample["box_total_age_ms"] = max(
                    0.0,
                    (tracks_published_at_ns - source_captured_ns) / 1_000_000.0,
                )
            if diagnostics_enabled:
                self.box_latency_diagnostics.record(sample)
            publish_context = dict(trace_context)
            publish_context["tracks_published_at_ns"] = tracks_published_at_ns
            try:
                self.track_store.set_tracks(
                    self.camera_id,
                    display_tracks,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    frame_context=publish_context,
                    latency_diagnostics=(
                        self.box_latency_diagnostics.snapshot()
                        if diagnostics_enabled
                        else None
                    ),
                )
            except Exception:
                if not tolerate_failure:
                    raise
                self.box_latency_diagnostics.increment(
                    "visual_fast_path_failed_total"
                )
                self.box_latency_diagnostics.increment(
                    "visual_fast_path_fallback_total"
                )
                self.logger.exception(
                    "Fast path visual failed; traditional publish remains available",
                    extra={
                        "camera_id": self.camera_id,
                        "action": "visual_fast_path_publish",
                        "status": "fallback",
                        "reason": "track_store_write_failed",
                    },
                )
                return False
            if path == "fast":
                self.box_latency_diagnostics.increment(
                    "visual_fast_path_published_total"
                )
            return True

        fast_path_published = False
        if fast_path_enabled:
            fast_path_published = publish_tracks(
                path="fast",
                tolerate_failure=True,
            )

        event_started_at_ns = time.time_ns()
        event_started_perf = self.perf_counter()
        self._process_events(
            tracks=tracks,
            db=db,
            frame=frame,
            infer_ran=infer_ran,
            gate=gate,
        )
        sample["event_pipeline_started_at_ns"] = event_started_at_ns
        sample["event_pipeline_completed_at_ns"] = time.time_ns()
        sample["event_pipeline_ms"] = max(
            0.0, (self.perf_counter() - event_started_perf) * 1000.0
        )
        event_latency_snapshot = getattr(
            self.event_pipeline,
            "latency_snapshot",
            None,
        )
        if callable(event_latency_snapshot):
            try:
                auxiliary_timings = dict(event_latency_snapshot(self.camera_id))
            except Exception:
                auxiliary_timings = {}
            if int(auxiliary_timings.get("ia2_calls") or 0) > 0:
                sample["ia2_ms"] = auxiliary_timings.get("ia2_ms")
            if int(auxiliary_timings.get("ia3_calls") or 0) > 0:
                sample["ia3_ms"] = auxiliary_timings.get("ia3_ms")
            for timing_field in (
                "ia2_started_at_ns",
                "ia2_completed_at_ns",
                "ia3_started_at_ns",
                "ia3_completed_at_ns",
                "ia2_calls",
                "ia3_calls",
                "ia2_errors",
                "ia3_errors",
                "revalidation_events",
            ):
                if timing_field in auxiliary_timings:
                    sample[timing_field] = auxiliary_timings[timing_field]

        if fast_path_enabled and fast_path_published:
            if diagnostics_enabled:
                self.box_latency_diagnostics.record(sample)
                update_diagnostics = getattr(
                    self.track_store,
                    "update_latency_diagnostics",
                    None,
                )
                if callable(update_diagnostics):
                    update_diagnostics(
                        self.camera_id,
                        self.box_latency_diagnostics.snapshot(),
                        expected_frame_id=trace_context.get("frame_id"),
                        expected_generation_id=trace_context.get("generation_id"),
                    )
        else:
            publish_tracks(path="traditional")

        return FrameProcessingResult(
            frame_width=frame_width,
            frame_height=frame_height,
            geometry=geometry,
            inference_frame=inference_frame,
            decision=decision,
            tracks=tracks,
            display_tracks=display_tracks,
            infer_ms=infer_ms,
            infer_ran=infer_ran,
            inference_result_age_ms=result_age_ms,
            visual_tracks_stale=visual_tracks_stale,
            box_latency=(
                self.box_latency_diagnostics.snapshot()
                if diagnostics_enabled
                else None
            ),
        )
