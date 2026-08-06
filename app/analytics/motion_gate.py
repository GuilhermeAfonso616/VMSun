from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from app.analytics.visual_quality import analyze_frame_quality

try:
    from app.core.config import settings
except ImportError:
    settings = None


@dataclass(slots=True)
class MotionGateDecision:
    should_infer: bool
    motion_score_global: float
    motion_score_roi: float | None
    motion_score_used: float
    threshold: float
    forced_by_interval: bool
    has_motion: bool
    invalid_reason: str = ""
    visual_artifact_reason: str = ""
    visual_quality: dict | None = None

    def as_dict(self) -> dict:
        return {
            "should_infer": self.should_infer,
            "motion_score_global": self.motion_score_global,
            "motion_score_roi": self.motion_score_roi,
            "motion_score_used": self.motion_score_used,
            "threshold": self.threshold,
            "forced_by_interval": self.forced_by_interval,
            "has_motion": self.has_motion,
            "invalid_reason": self.invalid_reason,
            "visual_artifact_reason": self.visual_artifact_reason,
            "visual_quality": self.visual_quality or {},
        }


class MotionGate:
    def __init__(
        self,
        *,
        threshold: float,
        min_interval_seconds: float,
        downscale_width: int = 320,
    ):
        self.threshold = max(0.0, float(threshold))
        self.min_interval_seconds = max(0.05, float(min_interval_seconds))
        self.downscale_width = max(64, int(downscale_width))

        self._prev_gray: np.ndarray | None = None
        self._last_inference_ts: float = 0.0

        self.last_motion_mask: np.ndarray | None = None
        self.last_motion_shape: tuple[int, int] | None = None
        self.last_source_shape: tuple[int, int] | None = None

    def _resize_gray(self, frame: np.ndarray) -> np.ndarray:
        frame_h, frame_w = frame.shape[:2]
        if frame_w <= self.downscale_width:
            resized = frame
        else:
            scale = self.downscale_width / float(frame_w)
            resized_h = max(1, int(round(frame_h * scale)))
            resized = cv2.resize(frame, (self.downscale_width, resized_h), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (5, 5), 0)

    def _build_roi_mask(self, gray_shape: tuple[int, int], roi_polygon: list[tuple[int, int]] | None) -> np.ndarray | None:
        if not roi_polygon or len(roi_polygon) < 3:
            return None

        h, w = gray_shape
        # ROI points are in source frame pixel coordinates; remap to reduced gray size.
        source_w = max(pt[0] for pt in roi_polygon) + 1
        source_h = max(pt[1] for pt in roi_polygon) + 1
        if source_w <= 1 or source_h <= 1:
            return None

        scale_x = w / float(source_w)
        scale_y = h / float(source_h)

        points = []
        for x, y in roi_polygon:
            px = int(round(float(x) * scale_x))
            py = int(round(float(y) * scale_y))
            points.append([max(0, min(w - 1, px)), max(0, min(h - 1, py))])

        if len(points) < 3:
            return None

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask, [np.array(points, dtype=np.int32)], 255)
        return mask

    def evaluate(self, frame: np.ndarray, roi_polygon: list[tuple[int, int]] | None = None) -> MotionGateDecision:
        now = time.perf_counter()
        quality = analyze_frame_quality(frame)
        if quality.is_invalid:
            self._prev_gray = None
            self.last_motion_mask = None
            self.last_motion_shape = None
            self.last_source_shape = frame.shape[:2]
            return MotionGateDecision(
                should_infer=False,
                motion_score_global=0.0,
                motion_score_roi=0.0 if roi_polygon else None,
                motion_score_used=0.0,
                threshold=self.threshold,
                forced_by_interval=False,
                has_motion=False,
                invalid_reason=quality.invalid_reason,
                visual_artifact_reason=quality.artifact_reason,
                visual_quality=quality.as_dict(),
            )

        gray = self._resize_gray(frame)

        if self._prev_gray is None:
            self._prev_gray = gray
            self.last_motion_mask = np.zeros_like(gray)
            self.last_motion_shape = gray.shape[:2]
            self.last_source_shape = frame.shape[:2]
            return MotionGateDecision(
                should_infer=True,
                motion_score_global=1.0,
                motion_score_roi=1.0 if roi_polygon else None,
                motion_score_used=1.0,
                threshold=self.threshold,
                forced_by_interval=True,
                has_motion=True,
                visual_artifact_reason=quality.artifact_reason,
                visual_quality=quality.as_dict(),
            )

        diff = cv2.absdiff(gray, self._prev_gray)
        self._prev_gray = gray

        # threshold binarization (fixed configuration/default 15)
        thresh_val = 15
        if settings is not None:
            thresh_val = getattr(settings, "motion_confirm_threshold_px", 15)
        _, thresh = cv2.threshold(diff, thresh_val, 255, cv2.THRESH_BINARY)
        self.last_motion_mask = thresh
        self.last_motion_shape = thresh.shape[:2]
        self.last_source_shape = frame.shape[:2]

        global_score = float(np.mean(diff) / 255.0)
        roi_score: float | None = None

        roi_mask = self._build_roi_mask(gray.shape, roi_polygon)
        if roi_mask is not None:
            selected = diff[roi_mask > 0]
            if selected.size > 0:
                roi_score = float(np.mean(selected) / 255.0)

        used_score = max(global_score, roi_score if roi_score is not None else 0.0)
        has_motion = used_score >= self.threshold
        forced_by_interval = (now - self._last_inference_ts) >= self.min_interval_seconds
        should_infer = bool(has_motion or forced_by_interval)

        return MotionGateDecision(
            should_infer=should_infer,
            motion_score_global=global_score,
            motion_score_roi=roi_score,
            motion_score_used=used_score,
            threshold=self.threshold,
            forced_by_interval=forced_by_interval,
            has_motion=has_motion,
            visual_artifact_reason=quality.artifact_reason,
            visual_quality=quality.as_dict(),
        )

    def mark_inference(self) -> None:
        self._last_inference_ts = time.perf_counter()

    def get_local_motion_features(self, bbox: list[float] | None) -> dict:
        default_res = {
            "motion_area_pct": 0.0,
            "motion_blobs": 0,
            "has_motion_mask": False,
        }
        if bbox is None or len(bbox) != 4:
            return default_res

        if self.last_motion_mask is None or self.last_motion_shape is None or self.last_source_shape is None:
            return default_res

        src_h, src_w = self.last_source_shape
        mask_h, mask_w = self.last_motion_shape
        if src_h <= 0 or src_w <= 0 or mask_h <= 0 or mask_w <= 0:
            return default_res

        scale_x = mask_w / float(src_w)
        scale_y = mask_h / float(src_h)

        x1 = int(round(bbox[0] * scale_x))
        y1 = int(round(bbox[1] * scale_y))
        x2 = int(round(bbox[2] * scale_x))
        y2 = int(round(bbox[3] * scale_y))

        x1 = max(0, min(mask_w - 1, x1))
        y1 = max(0, min(mask_h - 1, y1))
        x2 = max(0, min(mask_w, x2))
        y2 = max(0, min(mask_h, y2))

        if (x2 - x1) <= 0 or (y2 - y1) <= 0:
            return default_res

        crop = self.last_motion_mask[y1:y2, x1:x2]
        moving_pixels = int(np.count_nonzero(crop))

        total_scene_pixels = mask_w * mask_h
        motion_area_pct = float(moving_pixels / total_scene_pixels) * 100.0

        min_blob_area = 2
        if settings is not None:
            min_blob_area = getattr(settings, "motion_confirm_blob_min_area_px", 2)

        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(crop)
        blobs_count = 0
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] >= min_blob_area:
                blobs_count += 1

        return {
            "motion_area_pct": motion_area_pct,
            "motion_blobs": blobs_count,
            "has_motion_mask": True,
        }
