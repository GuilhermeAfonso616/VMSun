import numpy as np

from app.analytics.motion_gate import MotionGate
from app.analytics.visual_quality import analyze_frame_quality, invalid_frame_reason


def _color_bar_frame(width=320, height=180):
    colors = [
        (255, 255, 255),
        (0, 255, 255),
        (255, 255, 0),
        (0, 255, 0),
        (255, 0, 255),
        (0, 0, 255),
        (255, 0, 0),
    ]
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    bar_width = width // len(colors)
    for index, color in enumerate(colors):
        start = index * bar_width
        end = width if index == len(colors) - 1 else (index + 1) * bar_width
        frame[:, start:end, :] = color
    return frame


def test_color_bar_frame_is_invalid():
    frame = _color_bar_frame()

    assert invalid_frame_reason(frame) == "color_bar_test_pattern"
    quality = analyze_frame_quality(frame)
    assert quality.invalid_reason == "color_bar_test_pattern"


def test_motion_gate_blocks_invalid_frame_before_inference():
    gate = MotionGate(threshold=0.015, min_interval_seconds=2.0)

    decision = gate.evaluate(_color_bar_frame())

    assert decision.should_infer is False
    assert decision.has_motion is False
    assert decision.invalid_reason == "color_bar_test_pattern"
    assert decision.as_dict()["visual_quality"]["is_invalid"] is True


def test_motion_gate_keeps_first_valid_frame_as_keepalive():
    gate = MotionGate(threshold=0.015, min_interval_seconds=2.0)
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    frame[:, :, :] = (40, 60, 80)

    decision = gate.evaluate(frame)

    assert decision.should_infer is True
    assert decision.invalid_reason == ""
    assert decision.forced_by_interval is True


def test_quality_flags_codec_artifacts_without_marking_stream_invalid():
    gray_artifact = np.full((180, 320, 3), 128, dtype=np.uint8)
    noisy_pixels = np.random.default_rng(3).integers(110, 145, size=(180, 320, 1), dtype=np.uint8)
    gray_artifact[:, :, :] = noisy_pixels

    quality = analyze_frame_quality(gray_artifact)

    assert quality.invalid_reason == ""
    assert quality.artifact_reason == "gray_decoder_artifact"
