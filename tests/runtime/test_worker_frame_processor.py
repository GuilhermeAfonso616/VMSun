from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime.inference_detection import InferenceBackpressureError
from app.runtime.inference_scheduling import InferenceDecision
from app.runtime.preprocess import InferenceFrame, SceneGeometry
from app.runtime import worker_frame_processor
from app.runtime.worker_frame_processor import WorkerFrameProcessor


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPTURED_AT = datetime(2026, 7, 20, 12, 30, 0)
NOW = CAPTURED_AT + timedelta(milliseconds=500)


class Frame:
    shape = (720, 1280, 3)


class StubLogger:
    def __init__(self):
        self.info_calls = []
        self.debug_calls = []
        self.warning_calls = []
        self.exception_calls = []

    def info(self, *args, **kwargs):
        self.info_calls.append((args, kwargs))

    def debug(self, *args, **kwargs):
        self.debug_calls.append((args, kwargs))

    def warning(self, *args, **kwargs):
        self.warning_calls.append((args, kwargs))

    def exception(self, *args, **kwargs):
        self.exception_calls.append((args, kwargs))


class StubPreprocessor:
    def __init__(self, order):
        self.order = order
        self.geometry = SceneGeometry(
            roi_polygon=[(0, 0), (100, 0), (100, 100)],
            line_pixels=((10, 10), (20, 20)),
            frame_width=1280,
            frame_height=720,
        )
        self.inference_frame = InferenceFrame(
            frame=object(),
            offset_x=3,
            offset_y=4,
            roi_crop_active=False,
            roi_crop_meta=None,
            input_width=640,
            input_height=360,
            source_width=1280,
            source_height=720,
            scale_x=2.0,
            scale_y=2.0,
        )

    def build_geometry(self, analytics, frame_width, frame_height):
        self.order.append("geometry")
        assert frame_width == 1280
        assert frame_height == 720
        return self.geometry

    def build_inference_frame(self, frame, roi_polygon, **kwargs):
        self.order.append("preprocess")
        assert roi_polygon == self.geometry.roi_polygon
        assert kwargs == {
            "max_width": 640,
            "max_height": 360,
            "allow_upscale": False,
        }
        return self.inference_frame


class StubEventPipeline:
    def __init__(self, order, *, process_error=None, visual_tracks=None):
        self.order = order
        self.process_error = process_error
        self.selected_visual_tracks = visual_tracks or []
        self.geometry_calls = []
        self.process_calls = []
        self.visual_calls = []

    def update_geometry(self, *args, **kwargs):
        self.order.append("update_geometry")
        self.geometry_calls.append((args, kwargs))

    def process(self, *args, **kwargs):
        self.order.append("events")
        self.process_calls.append((args, kwargs))
        if self.process_error is not None:
            raise self.process_error

    def visual_tracks(self, *, now):
        self.order.append("visual_tracks")
        self.visual_calls.append(now)
        return self.selected_visual_tracks


class StubDetectionService:
    def __init__(self, order, outcome):
        self.order = order
        self.outcome = outcome
        self.calls = []

    def infer(self, frame, **kwargs):
        self.order.append("infer")
        self.calls.append((frame, kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class StubTrackStore:
    def __init__(self, order, *, failures_remaining=0):
        self.order = order
        self.calls = []
        self.failures_remaining = failures_remaining

    def set_tracks(self, camera_id, tracks, **kwargs):
        self.order.append("track_store")
        self.calls.append((camera_id, tracks, kwargs))
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("track store unavailable")

    def update_latency_diagnostics(self, camera_id, diagnostics, **kwargs):
        self.order.append("track_store_diagnostics")
        self.calls.append(
            (
                camera_id,
                "latency_diagnostics",
                {"diagnostics": diagnostics, **kwargs},
            )
        )
        return True


class StubDb:
    def __init__(self):
        self.rollback_calls = 0

    def rollback(self):
        self.rollback_calls += 1


def runtime_config():
    analytics = SimpleNamespace(
        line_direction="a_to_b",
        human_event_modes=["person_entered"],
        human_loitering_seconds=12.0,
        human_detection_sensitivity="medium",
    )
    return SimpleNamespace(
        analytics=analytics,
        analytic_profile=SimpleNamespace(preset_name="default"),
        processing_max_width=640,
        processing_max_height=360,
        processing_upscale_small_frames=False,
    )


def build_processor(
    *,
    decision=None,
    detection_outcome=None,
    gate=None,
    event_error=None,
    event_visual_tracks=None,
    visual_ia_boxes_enabled=True,
    visual_revalidation_gate_enabled=False,
    visual_max_age=1.0,
    diagnostics_enabled=False,
    fast_path_enabled=False,
    track_store_failures=0,
    visual_frame_age_limit_ms=0,
):
    order = []
    logger = StubLogger()
    preprocessor = StubPreprocessor(order)
    tracks = [{"track_id": 1, "bbox": [1, 2, 30, 40]}]
    detection = StubDetectionService(
        order,
        detection_outcome if detection_outcome is not None else (tracks, 7.5),
    )
    events = StubEventPipeline(
        order,
        process_error=event_error,
        visual_tracks=event_visual_tracks,
    )
    track_store = StubTrackStore(
        order,
        failures_remaining=track_store_failures,
    )
    health = {"status": "starting", "transitions": []}
    after_calls = []

    def set_health(status, reason):
        order.append("health")
        health["status"] = status
        health["transitions"].append((status, reason))

    def before_inference(frame):
        order.append("scheduler")
        assert frame is preprocessor.inference_frame.frame
        return decision or InferenceDecision(should_infer=True, motion_info={})

    processor = WorkerFrameProcessor(
        camera_id=7,
        process_pid=1234,
        logger=logger,
        preprocessor=preprocessor,
        detection_service=detection,
        event_pipeline=events,
        track_store_backend=track_store,
        runtime_config=runtime_config,
        motion_gate=lambda: gate,
        before_inference=before_inference,
        after_inference=lambda inferred: (
            order.append("after_inference"),
            after_calls.append(inferred),
        ),
        get_health_status=lambda: health["status"],
        set_health=set_health,
        runtime_settings=SimpleNamespace(
            visual_max_result_age_seconds=visual_max_age,
            visual_ia_boxes_enabled=visual_ia_boxes_enabled,
            visual_revalidation_gate_enabled=visual_revalidation_gate_enabled,
            box_latency_diagnostics_enabled=diagnostics_enabled,
            box_latency_diagnostics_camera_ids="7",
            box_latency_diagnostics_sample_window=50,
            visual_fast_path_enabled=fast_path_enabled,
            visual_fast_path_camera_ids="7",
            visual_inference_max_frame_age_ms=visual_frame_age_limit_ms,
            visual_inference_max_frame_age_camera_ids="7",
        ),
        perf_counter=lambda: 2.0,
        utcnow=lambda: NOW,
    )
    return SimpleNamespace(
        processor=processor,
        order=order,
        logger=logger,
        preprocessor=preprocessor,
        detection=detection,
        events=events,
        track_store=track_store,
        health=health,
        after_calls=after_calls,
        tracks=tracks,
        db=StubDb(),
    )


def process_frame(context):
    return context.processor.process(
        frame=Frame(),
        captured_at=CAPTURED_AT,
        db=context.db,
        frame_context={
            "camera_id": 7,
            "generation_id": 12,
            "frame_id": 18722,
            "gateway_received_at_ns": 1_000_000_000,
            "worker_received_at_ns": 1_008_000_000,
            "worker_received_monotonic_ns": 2_000_000_000,
            "capture_clock": "gateway_receive_wall_clock",
        },
    )


def test_frame_processor_preserves_success_order_and_state():
    context = build_processor()

    result = process_frame(context)

    assert context.order == [
        "geometry",
        "update_geometry",
        "preprocess",
        "scheduler",
        "infer",
        "after_inference",
        "health",
        "events",
        "track_store",
    ]
    assert result.tracks is context.tracks
    assert result.display_tracks is context.tracks
    assert result.infer_ms == pytest.approx(7.5)
    assert result.infer_ran is True
    assert result.inference_result_age_ms == pytest.approx(500.0)
    assert context.processor.state.last_successful_inference_at == NOW
    assert context.processor.state.last_tracks_captured_at == CAPTURED_AT
    assert context.after_calls == [context.tracks]
    _, infer_kwargs = context.detection.calls[0]
    assert infer_kwargs == {"offset_x": 3, "offset_y": 4, "scale_x": 2.0, "scale_y": 2.0}
    _, event_kwargs = context.events.process_calls[0]
    assert event_kwargs["detections_fresh"] is True
    assert context.health["transitions"] == [("running", "inference_ok")]


def test_scheduler_skip_reuses_last_tracks_without_calling_detector():
    context = build_processor(decision=InferenceDecision(False, {"state": "idle"}))
    previous = [{"track_id": 8, "bbox": [5, 6, 7, 8]}]
    context.processor.state.last_tracks = previous
    context.processor.state.last_tracks_captured_at = CAPTURED_AT

    result = process_frame(context)

    assert result.tracks is previous
    assert result.display_tracks is previous
    assert result.infer_ran is False
    assert context.detection.calls == []
    assert context.after_calls == []
    assert context.events.process_calls[0][1]["detections_fresh"] is False
    assert context.health["transitions"] == []


def test_backpressure_preserves_previous_tracks_and_marks_ai_degraded():
    context = build_processor(
        detection_outcome=InferenceBackpressureError("pool full")
    )
    previous = [{"track_id": 4, "bbox": [1, 1, 2, 2]}]
    context.processor.state.last_tracks = previous
    context.processor.state.last_tracks_captured_at = CAPTURED_AT

    result = process_frame(context)

    assert result.tracks is previous
    assert result.infer_ran is False
    assert context.processor.state.last_tracks is previous
    assert context.track_store.calls[-1][1] is previous
    assert context.health["transitions"] == [("degraded", "pool_backpressure")]
    assert len(context.logger.warning_calls) == 1


def test_detector_failure_clears_tracks_but_keeps_last_success_timestamp():
    context = build_processor(detection_outcome=RuntimeError("model failed"))
    previous_success = CAPTURED_AT - timedelta(seconds=1)
    context.processor.state.last_tracks = [{"track_id": 2}]
    context.processor.state.last_tracks_captured_at = CAPTURED_AT
    context.processor.state.last_successful_inference_at = previous_success

    result = process_frame(context)

    assert result.tracks == []
    assert result.display_tracks == []
    assert result.infer_ran is False
    assert context.processor.state.last_tracks == []
    assert context.processor.state.last_tracks_captured_at is None
    assert context.processor.state.last_successful_inference_at == previous_success
    assert len(context.track_store.calls) == 2
    assert all(call[1] == [] for call in context.track_store.calls)
    assert context.health["transitions"] == [("degraded", "inference_failed")]
    assert len(context.logger.exception_calls) == 1


def test_event_failure_rolls_back_and_still_publishes_visual_tracks():
    context = build_processor(event_error=RuntimeError("db unavailable"))

    result = process_frame(context)

    assert result.display_tracks is context.tracks
    assert context.db.rollback_calls == 1
    assert context.track_store.calls[-1][1] is context.tracks
    assert len(context.logger.exception_calls) == 1


def test_fast_path_publishes_before_event_pipeline():
    context = build_processor(fast_path_enabled=True, diagnostics_enabled=True)

    result = process_frame(context)

    assert context.order.index("track_store") < context.order.index("events")
    publish_call = next(
        call for call in context.track_store.calls if call[1] != "latency_diagnostics"
    )
    diagnostics_call = next(
        call for call in context.track_store.calls if call[1] == "latency_diagnostics"
    )
    assert publish_call[2]["frame_context"]["frame_id"] == 18722
    assert (
        publish_call[2]["frame_context"]["tracks_published_at_ns"]
        == diagnostics_call[2]["diagnostics"]["latest"]["tracks_published_at_ns"]
    )
    assert diagnostics_call[2]["expected_frame_id"] == 18722
    assert result.box_latency["latest"]["event_pipeline_ms"] == pytest.approx(0.0)


def test_fast_path_disabled_preserves_event_before_publish():
    context = build_processor(fast_path_enabled=False)

    process_frame(context)

    assert context.order.index("events") < context.order.index("track_store")


def test_fast_path_failure_falls_back_after_events_without_stopping_worker():
    context = build_processor(
        fast_path_enabled=True,
        diagnostics_enabled=True,
        track_store_failures=1,
    )

    result = process_frame(context)

    assert result.display_tracks is context.tracks
    assert context.order == [
        "geometry",
        "update_geometry",
        "preprocess",
        "scheduler",
        "infer",
        "after_inference",
        "health",
        "track_store",
        "events",
        "track_store",
    ]
    assert result.box_latency["counters"]["visual_fast_path_failed_total"] == 1
    assert result.box_latency["counters"]["visual_fast_path_fallback_total"] == 1
    assert result.box_latency["counters"]["visual_fast_path_published_total"] == 0


def test_gateway_rfc3339_nanoseconds_are_accepted():
    parsed = WorkerFrameProcessor._wall_ns(
        "2026-07-29T12:42:46.895244363Z"
    )

    assert parsed == 1785328966895244000


def test_canary_drops_frame_stale_since_gateway_receive(monkeypatch):
    monkeypatch.setattr(
        worker_frame_processor.time,
        "time_ns",
        lambda: 2_000_000_000,
    )
    context = build_processor(
        diagnostics_enabled=True,
        visual_frame_age_limit_ms=500,
    )
    context.processor.state.last_tracks = context.tracks
    context.processor.state.last_tracks_captured_at = CAPTURED_AT

    result = process_frame(context)

    assert result.decision.should_infer is False
    assert result.decision.motion_info["visual_frame_stale"] is True
    assert result.decision.motion_info["visual_frame_age_ms"] == 1000.0
    assert result.tracks == []
    assert context.detection.calls == []
    assert context.track_store.calls[-1][1] == []


def test_motion_gate_ands_scheduler_and_invalid_frame_clears_previous_tracks():
    gate_decision = SimpleNamespace(
        should_infer=False,
        invalid_reason="blur",
        motion_score_used=0.01,
        threshold=0.10,
        forced_by_interval=False,
        has_motion=False,
        as_dict=lambda: {"should_infer": False, "invalid_reason": "blur"},
    )
    gate = SimpleNamespace(
        evaluate=lambda frame, roi_polygon: gate_decision,
        mark_inference=lambda: pytest.fail("inference must not be marked"),
    )
    context = build_processor(gate=gate)
    context.processor.state.last_tracks = [{"track_id": 3}]

    result = process_frame(context)

    assert result.decision.should_infer is False
    assert result.decision.motion_info["motion_gate"]["invalid_reason"] == "blur"
    assert result.tracks == []
    assert context.detection.calls == []
    assert context.track_store.calls[-1][1] == []
    assert context.logger.info_calls[-1][1]["extra"]["reason"] == "blur"


def test_visual_selection_supports_stale_suppression_and_revalidation_gate():
    stale = build_processor(
        decision=InferenceDecision(False, {}),
        visual_max_age=0.1,
    )
    stale.processor.state.last_tracks = stale.tracks
    stale.processor.state.last_tracks_captured_at = CAPTURED_AT

    stale_result = process_frame(stale)

    assert stale_result.visual_tracks_stale is True
    assert stale_result.display_tracks == []

    revalidated_tracks = [{"track_id": 99, "visual_status": "revalidated"}]
    gated = build_processor(
        decision=InferenceDecision(False, {}),
        event_visual_tracks=revalidated_tracks,
        visual_ia_boxes_enabled=False,
        visual_revalidation_gate_enabled=True,
        visual_max_age=10.0,
    )
    gated.processor.state.last_tracks = gated.tracks
    gated.processor.state.last_tracks_captured_at = CAPTURED_AT

    gated_result = process_frame(gated)

    assert gated_result.visual_tracks_stale is False
    assert gated_result.display_tracks is revalidated_tracks
    assert gated.events.visual_calls == [NOW]


def test_worker_delegates_frame_processing_without_reverse_dependency():
    worker_source = (
        PROJECT_ROOT / "app/runtime/worker_base.py"
    ).read_text(encoding="utf-8")
    assert "detection_service.infer(" not in worker_source
    assert "event_pipeline.process(" not in worker_source
    assert "frame_processor.process(" in worker_source

    processor_tree = ast.parse(
        (PROJECT_ROOT / "app/runtime/worker_frame_processor.py").read_text(
            encoding="utf-8"
        )
    )
    imported_modules = {
        node.module
        for node in ast.walk(processor_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.runtime.worker_base" not in imported_modules
