from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass(slots=True)
class FrameQuality:
    invalid_reason: str = ""
    artifact_reason: str = ""
    metrics: dict[str, float | int] = field(default_factory=dict)

    @property
    def is_invalid(self) -> bool:
        return bool(self.invalid_reason)

    @property
    def has_artifact(self) -> bool:
        return bool(self.artifact_reason)

    def as_dict(self) -> dict:
        return {
            "invalid_reason": self.invalid_reason,
            "artifact_reason": self.artifact_reason,
            "is_invalid": self.is_invalid,
            "has_artifact": self.has_artifact,
            "metrics": dict(self.metrics),
        }


def _sample_frame(frame: np.ndarray, *, width: int = 320) -> np.ndarray:
    frame_h, frame_w = frame.shape[:2]
    if frame_w <= width:
        return frame
    scale = width / float(frame_w)
    return cv2.resize(frame, (width, max(1, int(round(frame_h * scale)))), interpolation=cv2.INTER_AREA)


def invalid_frame_reason(frame: np.ndarray | None) -> str:
    if frame is None or getattr(frame, "size", 0) == 0:
        return "empty_frame"

    frame_h, frame_w = frame.shape[:2]
    if frame_h < 16 or frame_w < 16:
        return "frame_too_small"

    sample_w = min(160, frame_w)
    sample_h = max(1, int(round(frame_h * (sample_w / float(frame_w)))))
    sample = cv2.resize(frame, (sample_w, sample_h), interpolation=cv2.INTER_AREA)
    top = sample[: max(1, sample_h // 2), :, :]

    hsv = cv2.cvtColor(top, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32) / 255.0
    value = hsv[:, :, 2].astype(np.float32) / 255.0
    high_sat_ratio = float(np.mean(saturation >= 0.72))
    bright_ratio = float(np.mean(value >= 0.45))
    if high_sat_ratio < 0.50 or bright_ratio < 0.80:
        return ""

    column_medians = np.median(top, axis=0).astype(np.float32)
    quantized = (column_medians // 32).astype(np.int16)
    runs = []
    start = 0
    for idx in range(1, sample_w):
        if np.max(np.abs(quantized[idx] - quantized[idx - 1])) > 1:
            runs.append((start, idx - start))
            start = idx
    runs.append((start, sample_w - start))

    wide_runs = [length for _start, length in runs if length >= max(8, int(round(sample_w * 0.08)))]
    if len(wide_runs) >= 5:
        return "color_bar_test_pattern"

    return ""


def analyze_frame_quality(frame: np.ndarray | None) -> FrameQuality:
    reason = invalid_frame_reason(frame)
    if frame is None or getattr(frame, "size", 0) == 0:
        return FrameQuality(invalid_reason=reason or "empty_frame")

    frame_h, frame_w = frame.shape[:2]
    if frame_h < 16 or frame_w < 16:
        return FrameQuality(invalid_reason=reason or "frame_too_small")

    sample = _sample_frame(frame)
    hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32) / 255.0
    value = hsv[:, :, 2].astype(np.float32) / 255.0

    gray_ratio = float(np.mean(saturation < 0.08))
    mid_gray_ratio = float(np.mean((saturation < 0.08) & (value > 0.25) & (value < 0.80)))
    high_saturation_ratio = float(np.mean(saturation > 0.65))

    saturated_mask = ((saturation > 0.55) & (value > 0.20)).astype(np.uint8) * 255
    contours, _ = cv2.findContours(saturated_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    small_color_area = 0.0
    small_color_components = 0
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if 2.0 <= area <= 80.0:
            small_color_area += area
            small_color_components += 1

    total_pixels = float(max(1, sample.shape[0] * sample.shape[1]))
    small_color_component_ratio = small_color_area / total_pixels
    metrics: dict[str, float | int] = {
        "gray_ratio": round(gray_ratio, 4),
        "mid_gray_ratio": round(mid_gray_ratio, 4),
        "high_saturation_ratio": round(high_saturation_ratio, 4),
        "small_color_component_ratio": round(small_color_component_ratio, 5),
        "small_color_components": int(small_color_components),
    }

    artifact_reason = ""
    if mid_gray_ratio >= 0.95 and high_saturation_ratio <= 0.01:
        artifact_reason = "gray_decoder_artifact"
    elif small_color_component_ratio >= 0.01 and small_color_components >= 30:
        artifact_reason = "color_macroblock_noise"

    return FrameQuality(invalid_reason=reason, artifact_reason=artifact_reason, metrics=metrics)
