from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import time
from typing import Any

import cv2
import numpy as np

from app.analytics_v2.revalidation.person_crop_revalidator import (
    _candidate_model_paths,
    _compact_exception_reason,
    _resolve_revalidator_device,
)
from app.core.config import settings
from app.core.logging import get_logger


FAR_QUALITY_REASONS = {
    "bbox_width_too_small",
    "bbox_height_too_small",
    "bbox_area_too_small",
    "bbox_near_border",
}


@dataclass(slots=True)
class FarPersonRevalidationResult:
    enabled: bool
    triggered: bool
    applied: bool
    person_far_score: float | None = None
    not_person_far_score: float | None = None
    passed: bool | None = None
    threshold: float | None = None
    inference_ms: float = 0.0
    model_path: str | None = None
    reason: str | None = None
    trigger_reason: str | None = None
    quality: dict[str, Any] | None = None
    device: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "triggered": self.triggered,
            "applied": self.applied,
            "person_far_score": self.person_far_score,
            "not_person_far_score": self.not_person_far_score,
            "passed": self.passed,
            "threshold": self.threshold,
            "inference_ms": round(float(self.inference_ms or 0.0), 2),
            "model_path": self.model_path,
            "reason": self.reason,
            "trigger_reason": self.trigger_reason,
            "quality": self.quality or {},
            "device": self.device,
            "operational_decision": "audit_only",
        }


class FarPersonRevalidator:
    """Audita detecções pequenas/distantes sem cancelar eventos."""

    def __init__(
        self,
        *,
        model_path: str | None = None,
        threshold: float | None = None,
        margin_pct: float | None = None,
        imgsz: int | None = None,
        enabled: bool | None = None,
        device: str | None = None,
    ):
        self.model_path = str(model_path or settings.far_person_revalidator_model_path)
        self.threshold = float(threshold if threshold is not None else settings.far_person_revalidator_threshold)
        self.margin_pct = float(margin_pct if margin_pct is not None else settings.far_person_revalidator_margin_pct)
        self.imgsz = int(imgsz if imgsz is not None else settings.far_person_revalidator_imgsz)
        self.enabled = bool(settings.far_person_revalidator_enabled if enabled is None else enabled)
        self.device = _resolve_revalidator_device(device)
        self.logger = get_logger("app.analytics.revalidation")
        self._model = None
        self._load_error: str | None = None
        self._last_load_attempt_ts: float = 0.0
        self._load_retry_seconds: float = 30.0
        self._lock = Lock()

    def validate(
        self,
        frame: Any,
        bbox: list[float] | tuple[float, ...] | None,
        *,
        base_quality: dict[str, Any] | None = None,
        ia2_result: Any | None = None,
        _direct: bool = False,
    ) -> FarPersonRevalidationResult:
        if bool(settings.revalidator_pool_enabled) and not _direct and self.enabled:
            from app.services.revalidator_pool import get_revalidator_pool

            return get_revalidator_pool().run(
                "ia3",
                lambda: self.validate(
                    frame,
                    bbox,
                    base_quality=base_quality,
                    ia2_result=ia2_result,
                    _direct=True,
                ),
                self._pool_fallback,
            )
        return self._validate_direct(frame, bbox, base_quality=base_quality, ia2_result=ia2_result)

    def _pool_fallback(self, reason: str) -> FarPersonRevalidationResult:
        return FarPersonRevalidationResult(
            enabled=True,
            triggered=True,
            applied=False,
            threshold=self.threshold,
            model_path=self.model_path,
            reason=reason,
            trigger_reason="revalidator_pool",
            device=self.device,
        )

    def _validate_direct(
        self,
        frame: Any,
        bbox: list[float] | tuple[float, ...] | None,
        *,
        base_quality: dict[str, Any] | None = None,
        ia2_result: Any | None = None,
    ) -> FarPersonRevalidationResult:
        if not self.enabled:
            return FarPersonRevalidationResult(
                enabled=False,
                triggered=False,
                applied=False,
                reason="disabled",
                device=self.device,
            )

        if frame is None or not hasattr(frame, "shape"):
            return FarPersonRevalidationResult(
                enabled=True,
                triggered=False,
                applied=False,
                reason="missing_frame",
                device=self.device,
            )

        crop, quality = self._crop_with_quality(frame, bbox, base_quality=base_quality)
        if crop is None:
            return FarPersonRevalidationResult(
                enabled=True,
                triggered=False,
                applied=False,
                reason=str(quality.get("quality_reason") or "invalid_bbox"),
                quality=quality,
                device=self.device,
            )

        should_run, trigger_reason = self._should_run(quality, ia2_result=ia2_result)
        if not should_run:
            return FarPersonRevalidationResult(
                enabled=True,
                triggered=False,
                applied=False,
                reason="not_far_candidate",
                trigger_reason=trigger_reason,
                quality=quality,
                device=self.device,
            )

        model = self._load_model()
        if model is None:
            return FarPersonRevalidationResult(
                enabled=True,
                triggered=True,
                applied=False,
                threshold=self.threshold,
                model_path=self.model_path,
                reason=self._load_error or "model_unavailable",
                trigger_reason=trigger_reason,
                quality=quality,
                device=self.device,
            )

        started = time.perf_counter()
        try:
            predict_kwargs: dict[str, Any] = {"imgsz": self.imgsz, "verbose": False}
            if self.device:
                predict_kwargs["device"] = self.device
            results = model.predict(crop, **predict_kwargs)
            inference_ms = (time.perf_counter() - started) * 1000.0
            person_far_score, not_person_far_score = self._extract_scores(results)
            passed = person_far_score is not None and person_far_score >= self.threshold
            return FarPersonRevalidationResult(
                enabled=True,
                triggered=True,
                applied=True,
                person_far_score=person_far_score,
                not_person_far_score=not_person_far_score,
                passed=bool(passed),
                threshold=self.threshold,
                inference_ms=inference_ms,
                model_path=self.model_path,
                reason="ok",
                trigger_reason=trigger_reason,
                quality=quality,
                device=self.device,
            )
        except Exception as exc:
            self.logger.exception(
                "Far person revalidation failed",
                extra={
                    "action": "far_person_revalidation_failed",
                    "status": "degraded",
                    "reason": "inference_failed",
                },
            )
            return FarPersonRevalidationResult(
                enabled=True,
                triggered=True,
                applied=False,
                threshold=self.threshold,
                model_path=self.model_path,
                reason=f"inference_failed:{exc.__class__.__name__}",
                trigger_reason=trigger_reason,
                quality=quality,
                device=self.device,
            )

    def _load_model(self):
        if self._model is not None:
            return self._model
        now = time.perf_counter()
        if self._load_error and (now - self._last_load_attempt_ts) < self._load_retry_seconds:
            return None

        with self._lock:
            if self._model is not None:
                return self._model
            now = time.perf_counter()
            if self._load_error and (now - self._last_load_attempt_ts) < self._load_retry_seconds:
                return None
            self._last_load_attempt_ts = now
            try:
                candidates = _candidate_model_paths(self.model_path)
                existing_candidates = [candidate for candidate in candidates if candidate.exists()]
                if not existing_candidates:
                    attempted = "|".join(str(candidate) for candidate in candidates)
                    self._load_error = f"model_not_found:{attempted}"
                    self.logger.warning(
                        "Far person revalidator model not found. attempted=%s",
                        attempted,
                        extra={
                            "action": "far_person_revalidator_load",
                            "status": "degraded",
                            "reason": "model_not_found",
                        },
                    )
                    return None

                from ultralytics import YOLO

                last_error: Exception | None = None
                for path in existing_candidates:
                    try:
                        self._model = YOLO(str(path))
                        self.model_path = str(path)
                        self._load_error = None
                        self.logger.info(
                            "Far person revalidator loaded model=%s",
                            str(path),
                            extra={
                                "action": "far_person_revalidator_load",
                                "status": "running",
                                "reason": "model_loaded",
                            },
                        )
                        return self._model
                    except Exception as exc:
                        last_error = exc
                        self.logger.exception(
                            "Far person revalidator model candidate load failed path=%s",
                            str(path),
                            extra={
                                "action": "far_person_revalidator_load",
                                "status": "degraded",
                                "reason": "candidate_load_failed",
                            },
                        )

                if last_error is not None:
                    self._load_error = _compact_exception_reason(last_error)
                    return None
                self._load_error = "model_unavailable"
                return None
            except Exception as exc:
                self._load_error = _compact_exception_reason(exc)
                self.logger.exception(
                    "Far person revalidator model load failed",
                    extra={
                        "action": "far_person_revalidator_load",
                        "status": "degraded",
                        "reason": "load_failed",
                    },
                )
                return None

    def _crop_with_quality(
        self,
        frame: Any,
        bbox: list[float] | tuple[float, ...] | None,
        *,
        base_quality: dict[str, Any] | None,
    ):
        quality = dict(base_quality or {})
        quality.setdefault("far_policy", "audit_only")
        if not bbox or len(bbox) != 4:
            quality["quality_reason"] = quality.get("quality_reason") or "missing_bbox"
            return None, quality

        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            quality["quality_reason"] = quality.get("quality_reason") or "invalid_frame_size"
            return None, quality

        try:
            x1, y1, x2, y2 = [float(value) for value in bbox]
        except Exception:
            quality["quality_reason"] = quality.get("quality_reason") or "invalid_bbox"
            return None, quality

        box_w = max(1.0, x2 - x1)
        box_h = max(1.0, y2 - y1)
        margin_x = box_w * max(0.0, self.margin_pct)
        margin_y = box_h * max(0.0, self.margin_pct)
        left = max(0, int(round(x1 - margin_x)))
        top = max(0, int(round(y1 - margin_y)))
        right = min(width, int(round(x2 + margin_x)))
        bottom = min(height, int(round(y2 + margin_y)))

        if right <= left or bottom <= top:
            quality["quality_reason"] = quality.get("quality_reason") or "empty_crop_bounds"
            return None, quality

        raw_crop = frame[top:bottom, left:right]
        if raw_crop is None or raw_crop.size <= 0:
            quality["quality_reason"] = quality.get("quality_reason") or "empty_crop"
            return None, quality

        bbox_height_ratio = box_h / max(1.0, float(height))
        quality.update(
            {
                "frame_width": int(width),
                "frame_height": int(height),
                "bbox_width": round(float(box_w), 2),
                "bbox_height": round(float(box_h), 2),
                "bbox_height_ratio": round(float(bbox_height_ratio), 6),
                "far_crop_width": int(right - left),
                "far_crop_height": int(bottom - top),
            }
        )
        return self._letterbox(raw_crop, self.imgsz), quality

    def _should_run(self, quality: dict[str, Any], *, ia2_result: Any | None = None) -> tuple[bool, str]:
        reasons: list[str] = []
        crop_width = float(quality.get("crop_width") or quality.get("far_crop_width") or 0)
        crop_height = float(quality.get("crop_height") or quality.get("far_crop_height") or 0)
        bbox_height_ratio = float(quality.get("bbox_height_ratio") or 0)
        quality_reason = str(quality.get("quality_reason") or "")
        reason_parts = {part.strip() for part in quality_reason.split(",") if part.strip()}

        if crop_width and crop_width < float(settings.far_person_revalidator_max_crop_width_px):
            reasons.append("crop_width_small")
        if crop_height and crop_height < float(settings.far_person_revalidator_max_crop_height_px):
            reasons.append("crop_height_small")
        if bbox_height_ratio and bbox_height_ratio < float(settings.far_person_revalidator_max_bbox_height_ratio):
            reasons.append("bbox_height_ratio_small")
        if reason_parts.intersection(FAR_QUALITY_REASONS):
            reasons.append("quality_reason_far")
        if self._ia2_strong_not_person_should_run(quality, ia2_result=ia2_result):
            reasons.append("ia2_strong_not_person")

        if reasons:
            return True, ",".join(reasons)
        return False, "normal_scale"

    def _ia2_strong_not_person_should_run(
        self,
        quality: dict[str, Any],
        *,
        ia2_result: Any | None,
    ) -> bool:
        if not bool(settings.far_person_revalidator_suspicious_ia2_enabled):
            return False
        if ia2_result is None or not bool(getattr(ia2_result, "applied", False)):
            return False
        try:
            person_score = float(getattr(ia2_result, "person_score", None))
            not_person_score = float(getattr(ia2_result, "not_person_score", None))
        except Exception:
            return False
        if person_score > float(settings.far_person_revalidator_suspicious_ia2_max_person_score):
            return False
        if not_person_score < float(settings.far_person_revalidator_suspicious_ia2_min_not_person_score):
            return False
        if bool(settings.far_person_revalidator_suspicious_ia2_require_quality_gate) and not bool(
            quality.get("quality_gate_passed")
        ):
            return False
        if bool(settings.far_person_revalidator_suspicious_ia2_require_not_near_border) and bool(
            quality.get("near_border")
        ):
            return False
        return True

    def _letterbox(self, image: Any, size: int):
        height, width = image.shape[:2]
        if width <= 0 or height <= 0:
            return image
        scale = min(size / float(width), size / float(height))
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
        if len(resized.shape) == 2:
            canvas = np.full((size, size), 114, dtype=resized.dtype)
            y = (size - new_height) // 2
            x = (size - new_width) // 2
            canvas[y : y + new_height, x : x + new_width] = resized
            return canvas
        canvas = np.full((size, size, resized.shape[2]), 114, dtype=resized.dtype)
        y = (size - new_height) // 2
        x = (size - new_width) // 2
        canvas[y : y + new_height, x : x + new_width] = resized
        return canvas

    def _extract_scores(self, results) -> tuple[float | None, float | None]:
        if not results:
            return None, None
        result = results[0]
        probs = getattr(result, "probs", None)
        if probs is None:
            return None, None

        data = getattr(probs, "data", None)
        if data is None:
            return None, None

        values = data.detach().cpu().tolist() if hasattr(data, "detach") else list(data)
        names = getattr(result, "names", None) or getattr(getattr(self._model, "model", None), "names", None) or {}

        person_far_score = None
        not_person_far_score = None
        for index, score in enumerate(values):
            name = str(names.get(index, index)).lower()
            if name in {"person_far", "person", "human_far"}:
                person_far_score = float(score)
            elif name in {"not_person_far", "not_person", "non_person", "not-person", "nonhuman_far"}:
                not_person_far_score = float(score)

        if not_person_far_score is None and len(values) >= 1:
            not_person_far_score = float(values[0])
        if person_far_score is None and len(values) >= 2:
            person_far_score = float(values[1])
        return person_far_score, not_person_far_score


_INSTANCE: FarPersonRevalidator | None = None
_INSTANCE_LOCK = Lock()


def get_far_person_revalidator() -> FarPersonRevalidator:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = FarPersonRevalidator()
    return _INSTANCE


def reset_far_person_revalidator() -> None:
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
