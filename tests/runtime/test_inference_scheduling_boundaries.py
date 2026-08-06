from unittest.mock import patch

import numpy as np

from app.runtime import inference, inference_scheduling
from app.runtime.camera_config import MotionConfig


def test_inference_module_reexports_scheduler_contracts_for_compatibility():
    assert inference.InferenceDecision is inference_scheduling.InferenceDecision
    assert inference.NormalInferenceScheduler is inference_scheduling.NormalInferenceScheduler
    assert (
        inference.MotionAwareInferenceScheduler
        is inference_scheduling.MotionAwareInferenceScheduler
    )
    assert inference.MotionGate is inference_scheduling.MotionGate


def test_normal_scheduler_enforces_interval_after_completed_inference():
    scheduler = inference_scheduling.NormalInferenceScheduler(inference_interval=2.0)

    with patch("time.perf_counter", side_effect=[10.0, 10.0, 11.0, 12.0]):
        assert scheduler.evaluate().should_infer is True
        scheduler.on_inference_done([])
        assert scheduler.evaluate().should_infer is False
        assert scheduler.evaluate().should_infer is True


def test_motion_scheduler_exposes_motion_decision_without_detection_service():
    scheduler = inference_scheduling.MotionAwareInferenceScheduler(
        MotionConfig(
            idle_interval=0.0,
            active_interval=0.0,
            min_motion_frames=1,
            downscale_width=64,
            min_contour_area=10,
            motion_ratio_threshold=0.001,
            global_change_ratio_limit=1.0,
            background_alpha=0.0,
            warmup_frames=0,
        )
    )
    still_frame = np.zeros((64, 64, 3), dtype=np.uint8)
    changed_frame = still_frame.copy()
    changed_frame[16:48, 16:48] = 255

    initial = scheduler.evaluate(still_frame)
    changed = scheduler.evaluate(changed_frame)

    assert initial.should_infer is False
    assert changed.should_infer is True
    assert changed.motion_info["motion_detected"] is True
    assert changed.motion_info["moving_boxes"]
