"""Politicas de agendamento da inferencia por intervalo e movimento."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.config import settings
from app.runtime.camera_config import MotionConfig


@dataclass(slots=True)
class InferenceDecision:
    should_infer: bool
    motion_info: dict = field(default_factory=dict)


class NormalInferenceScheduler:
    def __init__(self, inference_interval: float | None = None):
        if inference_interval is None:
            inference_interval = settings.normal_inference_interval_seconds
        self.inference_interval = float(inference_interval)
        self.last_inference_at = 0.0

    def evaluate(self, frame_for_motion=None) -> InferenceDecision:
        import time

        now = time.perf_counter()
        should_infer = (now - self.last_inference_at) >= self.inference_interval
        return InferenceDecision(should_infer=should_infer, motion_info={})

    def on_inference_done(self, tracks: list[dict]):
        import time

        self.last_inference_at = time.perf_counter()


class MotionGate:
    def __init__(self, config: MotionConfig):
        self.idle_interval = float(config.idle_interval)
        self.active_interval = float(config.active_interval)
        self.motion_hold_seconds = float(config.motion_hold_seconds)
        self.detection_hold_seconds = float(config.detection_hold_seconds)
        self.min_motion_frames = int(config.min_motion_frames)
        self.downscale_width = int(config.downscale_width)
        self.min_contour_area = int(config.min_contour_area)
        self.motion_ratio_threshold = float(config.motion_ratio_threshold)
        self.global_change_ratio_limit = float(config.global_change_ratio_limit)
        self.background_alpha = float(config.background_alpha)
        self.warmup_frames = int(config.warmup_frames)

        self.background = None
        self.frames_seen = 0
        self.consecutive_motion_frames = 0
        self.last_motion_ts = 0.0
        self.last_detection_ts = 0.0
        self.last_inference_ts = 0.0
        self.last_motion_info = {
            "motion_detected": False,
            "motion_score": 0.0,
            "motion_ratio": 0.0,
            "global_change_ratio": 0.0,
            "moving_boxes": [],
            "triggered": False,
            "state": "idle",
        }

    def notify_detection(self, tracks: list[dict]):
        import time

        if tracks:
            self.last_detection_ts = time.perf_counter()

    def _resize_gray(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w <= 0 or h <= 0:
            return np.zeros((1, 1), dtype=np.uint8)
        if w <= self.downscale_width:
            small = frame
        else:
            scale = self.downscale_width / float(w)
            small = cv2.resize(
                frame,
                (self.downscale_width, max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        return gray

    def _state_name(self, now: float) -> str:
        in_motion_hold = (now - self.last_motion_ts) <= self.motion_hold_seconds
        in_detection_hold = (now - self.last_detection_ts) <= self.detection_hold_seconds
        if in_detection_hold:
            return "detection_hold"
        if in_motion_hold:
            return "motion_hold"
        return "idle"

    def should_infer(self, now: float, motion_detected: bool) -> bool:
        in_motion_hold = (now - self.last_motion_ts) <= self.motion_hold_seconds
        in_detection_hold = (now - self.last_detection_ts) <= self.detection_hold_seconds
        active = bool(in_motion_hold or in_detection_hold or motion_detected)
        interval = self.active_interval if active else self.idle_interval
        return (now - self.last_inference_ts) >= interval

    def analyze(self, frame: np.ndarray) -> dict:
        import time

        now = time.perf_counter()
        gray = self._resize_gray(frame)
        self.frames_seen += 1
        if self.background is None:
            self.background = gray.astype(np.float32)
            self.last_motion_info = {
                "motion_detected": False,
                "motion_score": 0.0,
                "motion_ratio": 0.0,
                "global_change_ratio": 0.0,
                "moving_boxes": [],
                "triggered": False,
                "state": self._state_name(now),
            }
            return self.last_motion_info

        cv2.accumulateWeighted(gray, self.background, self.background_alpha)
        bg = cv2.convertScaleAbs(self.background)
        diff = cv2.absdiff(gray, bg)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_DILATE, kernel, iterations=2)

        changed_pixels = int(cv2.countNonZero(thresh))
        total_pixels = int(thresh.shape[0] * thresh.shape[1]) if thresh.size else 1
        global_change_ratio = changed_pixels / float(max(1, total_pixels))

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        moving_boxes = []
        motion_area_sum = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_contour_area:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            moving_boxes.append((x, y, x + w, y + h))
            motion_area_sum += int(area)

        motion_ratio = motion_area_sum / float(max(1, total_pixels))
        valid_motion = (
            self.frames_seen > self.warmup_frames
            and global_change_ratio <= self.global_change_ratio_limit
            and motion_ratio >= self.motion_ratio_threshold
            and len(moving_boxes) > 0
        )
        if valid_motion:
            self.consecutive_motion_frames += 1
        else:
            self.consecutive_motion_frames = 0
        motion_detected = self.consecutive_motion_frames >= self.min_motion_frames
        if motion_detected:
            self.last_motion_ts = now
        triggered = self.should_infer(now=now, motion_detected=motion_detected)
        self.last_motion_info = {
            "motion_detected": motion_detected,
            "motion_score": float(motion_area_sum),
            "motion_ratio": float(motion_ratio),
            "global_change_ratio": float(global_change_ratio),
            "moving_boxes": moving_boxes,
            "triggered": triggered,
            "state": self._state_name(now),
        }
        return self.last_motion_info

    def mark_inference_done(self):
        import time

        self.last_inference_ts = time.perf_counter()


class MotionAwareInferenceScheduler:
    def __init__(self, config: MotionConfig):
        self.gate = MotionGate(config)

    def evaluate(self, frame_for_motion) -> InferenceDecision:
        motion_info = self.gate.analyze(frame_for_motion)
        return InferenceDecision(
            should_infer=bool(motion_info.get("triggered", False)),
            motion_info=motion_info,
        )

    def on_inference_done(self, tracks: list[dict]):
        self.gate.mark_inference_done()
        self.gate.notify_detection(tracks)
