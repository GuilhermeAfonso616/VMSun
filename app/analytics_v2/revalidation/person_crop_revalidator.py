from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import re
import time
from typing import Any

import cv2

from app.core.config import settings
from app.core.logging import get_logger
from app.services.revalidator_policy_store import load_revalidator_policy


def _resolve_project_path(path_value: str | None) -> Path:
    candidates = _candidate_model_paths(path_value)
    return candidates[0]


def _candidate_model_paths(path_value: str | None) -> list[Path]:
    path = Path(str(path_value or ""))
    if path.is_absolute():
        candidates = [path]
        if path.suffix.lower() == ".pt":
            candidates.append(path.with_suffix(".onnx"))
        return candidates

    roots = [
        Path(settings.app_base_dir),
        Path.cwd(),
        Path(__file__).resolve().parents[3],
    ]
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = (root / path).resolve()
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    if path.parts and path.parts[0].lower() == "models":
        candidate = (Path("/") / Path(*path.parts)).resolve()
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
        candidate = (Path("/models") / Path(*path.parts[1:])).resolve()
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    if path.suffix.lower() == ".pt":
        for candidate in list(candidates):
            onnx_candidate = candidate.with_suffix(".onnx")
            key = str(onnx_candidate)
            if key not in seen:
                seen.add(key)
                candidates.append(onnx_candidate)
    return candidates


def _compact_exception_reason(exc: Exception) -> str:
    detail = re.sub(r"\s+", "_", str(exc or "").strip())
    detail = re.sub(r"[^A-Za-z0-9_.:/=-]+", "_", detail).strip("_")
    suffix = f":{detail[:180]}" if detail else ""
    return f"load_failed:{exc.__class__.__name__}{suffix}"


def _resolve_revalidator_device(device: str | None = None) -> str | None:
    raw = str(device if device is not None else settings.revalidator_pool_device or "auto").strip()
    if not raw or raw.lower() == "auto":
        return None
    return raw


@dataclass(slots=True)
class CropRevalidationResult:
    enabled: bool
    applied: bool
    person_score: float | None = None
    not_person_score: float | None = None
    passed: bool | None = None
    threshold: float | None = None
    mode: str = "audit"
    inference_ms: float = 0.0
    model_path: str | None = None
    reason: str | None = None
    block_eligible: bool = False
    block_reason: str | None = None
    quality: dict[str, Any] | None = None
    device: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "applied": self.applied,
            "person_score": self.person_score,
            "not_person_score": self.not_person_score,
            "passed": self.passed,
            "threshold": self.threshold,
            "mode": self.mode,
            "inference_ms": round(float(self.inference_ms or 0.0), 2),
            "model_path": self.model_path,
            "reason": self.reason,
            "block_eligible": self.block_eligible,
            "block_reason": self.block_reason,
            "quality": self.quality or {},
            "device": self.device,
        }


class PersonCropRevalidator:
    """Revalida crops de pessoa apenas no pre-alarme/evento.

    O modelo fica lazy-loaded para nao impactar a inicializacao do servidor. Em
    modo audit, ele nunca bloqueia eventos; apenas escreve score no metadata.
    """

    def __init__(
        self,
        *,
        model_path: str | None = None,
        threshold: float | None = None,
        mode: str | None = None,
        margin_pct: float | None = None,
        imgsz: int | None = None,
        enabled: bool | None = None,
        device: str | None = None,
        policy_controlled: bool = True,
    ):
        self.model_path = str(model_path or settings.person_revalidator_model_path)
        self.threshold = float(threshold if threshold is not None else settings.person_revalidator_threshold)
        policy_mode = load_revalidator_policy().get("mode")
        self.policy_controlled = bool(policy_controlled)
        self.mode = str(mode or policy_mode or settings.person_revalidator_mode or "audit").strip().lower()
        self.margin_pct = float(margin_pct if margin_pct is not None else settings.person_revalidator_margin_pct)
        self.imgsz = int(imgsz if imgsz is not None else settings.person_revalidator_imgsz)
        self.enabled = bool(settings.person_revalidator_enabled if enabled is None else enabled)
        self.device = _resolve_revalidator_device(device)
        self.logger = get_logger("app.analytics.revalidation")
        self._model = None
        self._load_error: str | None = None
        self._last_load_attempt_ts: float = 0.0
        self._load_retry_seconds: float = 30.0
        self._lock = Lock()

    def current_mode(self) -> str:
        if self.policy_controlled:
            self.mode = str(load_revalidator_policy().get("mode") or self.mode or "audit").strip().lower()
        return self.mode

    def validate(
        self,
        frame: Any,
        bbox: list[float] | tuple[float, ...] | None,
        *,
        _direct: bool = False,
    ) -> CropRevalidationResult:
        if bool(settings.revalidator_pool_enabled) and not _direct and self.enabled:
            from app.services.revalidator_pool import get_revalidator_pool

            return get_revalidator_pool().run(
                "ia2",
                lambda: self.validate(frame, bbox, _direct=True),
                self._pool_fallback,
            )
        return self._validate_direct(frame, bbox)

    def _pool_fallback(self, reason: str) -> CropRevalidationResult:
        return CropRevalidationResult(
            enabled=True,
            applied=False,
            mode=self.current_mode(),
            threshold=self.threshold,
            model_path=self.model_path,
            reason=reason,
            device=self.device,
        )

    def _validate_direct(self, frame: Any, bbox: list[float] | tuple[float, ...] | None) -> CropRevalidationResult:
        mode = self.current_mode()
        if not self.enabled:
            return CropRevalidationResult(enabled=False, applied=False, mode=mode, reason="disabled", device=self.device)

        if frame is None or not hasattr(frame, "shape"):
            return CropRevalidationResult(enabled=True, applied=False, mode=mode, reason="missing_frame", device=self.device)

        crop, quality = self.crop_with_quality(frame, bbox)
        if crop is None:
            return CropRevalidationResult(
                enabled=True,
                applied=False,
                mode=mode,
                reason="invalid_bbox",
                quality=quality,
                device=self.device,
            )

        return self.infer_prepared_crop(crop, quality)

    def crop_with_quality(self, frame: Any, bbox: list[float] | tuple[float, ...] | None):
        """Preprocessamento publico do recorte.

        Etapa 3B: e a UNICA fonte do crop, usada tanto pela execucao local
        quanto pelo cliente que envia o recorte para a pool central. Manter
        assim evita que os dois caminhos divirjam no preprocessamento.
        """
        return self._crop_with_quality(frame, bbox)

    def infer_prepared_crop(self, crop: Any, quality: dict[str, Any] | None = None) -> CropRevalidationResult:
        """Executa o modelo sobre um recorte JA preprocessado.

        Etapa 3B: e o ponto de entrada da pool central. O corpo abaixo e
        exatamente o que `_validate_direct` executava, sem alteracao de
        threshold, imgsz, device ou interpretacao do resultado.
        """
        mode = self.current_mode()
        quality = quality if quality is not None else {}

        model = self._load_model()
        if model is None:
            return CropRevalidationResult(
                enabled=True,
                applied=False,
                mode=mode,
                threshold=self.threshold,
                model_path=self.model_path,
                reason=self._load_error or "model_unavailable",
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
            person_score, not_person_score = self._extract_scores(results)
            passed = person_score is not None and person_score >= self.threshold
            block_eligible, block_reason = self._block_decision(
                person_score=person_score,
                not_person_score=not_person_score,
                quality=quality,
            )
            return CropRevalidationResult(
                enabled=True,
                applied=True,
                person_score=person_score,
                not_person_score=not_person_score,
                passed=bool(passed),
                threshold=self.threshold,
                mode=mode,
                inference_ms=inference_ms,
                model_path=self.model_path,
                reason="ok",
                block_eligible=block_eligible,
                block_reason=block_reason,
                quality=quality,
                device=self.device,
            )
        except Exception as exc:
            self.logger.exception(
                "Person crop revalidation failed",
                extra={
                    "action": "person_crop_revalidation_failed",
                    "status": "degraded",
                    "reason": "inference_failed",
                },
            )
            return CropRevalidationResult(
                enabled=True,
                applied=False,
                mode=mode,
                threshold=self.threshold,
                model_path=self.model_path,
                reason=f"inference_failed:{exc.__class__.__name__}",
                quality=quality,
                device=self.device,
            )

    def should_block(self, result: CropRevalidationResult) -> bool:
        return self.current_mode() == "block" and result.applied and result.block_eligible

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
                        "Person crop revalidator model not found. attempted=%s",
                        attempted,
                        extra={
                            "action": "person_crop_revalidator_load",
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
                            "Person crop revalidator loaded model=%s",
                            str(path),
                            extra={
                                "action": "person_crop_revalidator_load",
                                "status": "running",
                                "reason": "model_loaded",
                            },
                        )
                        return self._model
                    except Exception as exc:
                        last_error = exc
                        self.logger.exception(
                            "Person crop revalidator model candidate load failed path=%s",
                            str(path),
                            extra={
                                "action": "person_crop_revalidator_load",
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
                    "Person crop revalidator model load failed",
                    extra={
                        "action": "person_crop_revalidator_load",
                        "status": "degraded",
                        "reason": "load_failed",
                    },
                )
                return None

    def _crop_with_quality(self, frame: Any, bbox: list[float] | tuple[float, ...] | None):
        quality: dict[str, Any] = {
            "block_policy": "conservative",
            "quality_gate_passed": False,
        }
        if not bbox or len(bbox) != 4:
            quality["quality_reason"] = "missing_bbox"
            return None, quality

        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            quality["quality_reason"] = "invalid_frame_size"
            return None, quality

        try:
            x1, y1, x2, y2 = [float(value) for value in bbox]
        except Exception:
            quality["quality_reason"] = "invalid_bbox"
            return None, quality

        box_w = max(1.0, x2 - x1)
        box_h = max(1.0, y2 - y1)
        bbox_area_ratio = (box_w * box_h) / max(1.0, float(width * height))
        near_border = (
            x1 <= width * settings.person_revalidator_block_border_margin_ratio
            or y1 <= height * settings.person_revalidator_block_border_margin_ratio
            or x2 >= width * (1.0 - settings.person_revalidator_block_border_margin_ratio)
            or y2 >= height * (1.0 - settings.person_revalidator_block_border_margin_ratio)
        )
        margin_x = box_w * max(0.0, self.margin_pct)
        margin_y = box_h * max(0.0, self.margin_pct)

        left = max(0, int(round(x1 - margin_x)))
        top = max(0, int(round(y1 - margin_y)))
        right = min(width, int(round(x2 + margin_x)))
        bottom = min(height, int(round(y2 + margin_y)))

        if right <= left or bottom <= top:
            quality["quality_reason"] = "empty_crop_bounds"
            return None, quality

        raw_crop = frame[top:bottom, left:right]
        if raw_crop is None or raw_crop.size <= 0:
            quality["quality_reason"] = "empty_crop"
            return None, quality

        gray = cv2.cvtColor(raw_crop, cv2.COLOR_BGR2GRAY) if len(raw_crop.shape) == 3 else raw_crop
        blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        quality.update(
            {
                "frame_width": int(width),
                "frame_height": int(height),
                "bbox_width": round(float(box_w), 2),
                "bbox_height": round(float(box_h), 2),
                "bbox_area_ratio": round(float(bbox_area_ratio), 6),
                "crop_width": int(right - left),
                "crop_height": int(bottom - top),
                "blur_variance": round(blur_variance, 2),
                "brightness": round(brightness, 2),
                "near_border": bool(near_border),
            }
        )

        quality_failures = []
        if box_w < float(settings.person_revalidator_block_min_bbox_width_px):
            quality_failures.append("bbox_width_too_small")
        if box_h < float(settings.person_revalidator_block_min_bbox_height_px):
            quality_failures.append("bbox_height_too_small")
        if bbox_area_ratio < float(settings.person_revalidator_block_min_bbox_area_ratio):
            quality_failures.append("bbox_area_too_small")
        if blur_variance < float(settings.person_revalidator_block_min_blur_variance):
            quality_failures.append("crop_too_blurry")
        if brightness < float(settings.person_revalidator_block_min_brightness):
            quality_failures.append("crop_too_dark")
        if brightness > float(settings.person_revalidator_block_max_brightness):
            quality_failures.append("crop_too_bright")
        if near_border:
            quality_failures.append("bbox_near_border")

        quality["quality_gate_passed"] = not quality_failures
        quality["quality_reason"] = "ok" if not quality_failures else ",".join(quality_failures)
        crop = cv2.resize(raw_crop, (self.imgsz, self.imgsz), interpolation=cv2.INTER_LINEAR)
        return crop, quality

    def _block_decision(
        self,
        *,
        person_score: float | None,
        not_person_score: float | None,
        quality: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        if person_score is None:
            return False, "missing_person_score"
        if not_person_score is None:
            return False, "missing_not_person_score"
        if not bool((quality or {}).get("quality_gate_passed")):
            reason = str((quality or {}).get("quality_reason") or "quality_gate_failed")
            return False, f"uncertain_{reason}"
        if float(person_score) >= float(settings.person_revalidator_block_person_threshold):
            return False, "person_score_not_extreme_low"
        if float(not_person_score) < float(settings.person_revalidator_block_not_person_threshold):
            return False, "not_person_score_not_extreme_high"
        return True, "clear_not_person_high_confidence_quality_passed"

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

        person_score = None
        not_person_score = None
        for index, score in enumerate(values):
            name = str(names.get(index, index)).lower()
            if name == "person":
                person_score = float(score)
            elif name in {"not_person", "non_person", "not-person", "non-human", "nonhuman"}:
                not_person_score = float(score)

        if person_score is None and len(values) >= 2:
            person_score = float(values[1])
        if not_person_score is None and len(values) >= 1:
            not_person_score = float(values[0])
        return person_score, not_person_score


_INSTANCE: PersonCropRevalidator | None = None
_INSTANCE_LOCK = Lock()


def get_person_crop_revalidator() -> PersonCropRevalidator:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = PersonCropRevalidator()
    return _INSTANCE


def reset_person_crop_revalidator() -> None:
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
