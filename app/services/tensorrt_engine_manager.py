from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger


_LAST_ENGINE_SNAPSHOT: dict[str, Any] = {}


@dataclass(frozen=True)
class TensorRTEngineResult:
    status: str
    reason: str
    engine_path: str | None = None
    model_path: str | None = None
    gpu_name: str | None = None
    device: str | None = None
    backend: str = "pytorch"
    auto_build_enabled: bool = False
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "engine_path": self.engine_path,
            "model_path": self.model_path,
            "gpu_name": self.gpu_name,
            "device": self.device,
            "backend": self.backend,
            "auto_build_enabled": self.auto_build_enabled,
            "required": self.required,
        }


def _record(result: TensorRTEngineResult) -> TensorRTEngineResult:
    global _LAST_ENGINE_SNAPSHOT
    _LAST_ENGINE_SNAPSHOT = result.as_dict()
    return result


def engine_status_snapshot() -> dict[str, Any]:
    if _LAST_ENGINE_SNAPSHOT:
        return dict(_LAST_ENGINE_SNAPSHOT)

    engine_path = str(settings.detector_engine_path or "").strip()
    if engine_path and Path(engine_path).exists():
        suffix = Path(engine_path).suffix.lower()
        backend = "tensorrt" if suffix == ".engine" else "onnx" if suffix == ".onnx" else "engine"
        return TensorRTEngineResult(
            status="ready",
            reason="configured_engine_exists",
            engine_path=engine_path,
            model_path=str(settings.detector_model_path),
            backend=backend,
            auto_build_enabled=bool(settings.detector_engine_auto_build_enabled),
            required=bool(settings.detector_engine_auto_build_required),
        ).as_dict()

    return TensorRTEngineResult(
        status="pytorch",
        reason="no_engine_configured",
        model_path=str(settings.detector_model_path),
        auto_build_enabled=bool(settings.detector_engine_auto_build_enabled),
        required=bool(settings.detector_engine_auto_build_required),
    ).as_dict()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or "gpu"


def _cuda_device_index(device: str) -> int:
    text = str(device or "").strip().lower()
    if text.startswith("cuda:"):
        try:
            return max(0, int(text.split(":", 1)[1]))
        except ValueError:
            return 0
    try:
        return max(0, int(text))
    except ValueError:
        return 0


def _target_engine_path(model_path: Path, *, gpu_name: str, capability: tuple[int, int] | None, cuda_version: str) -> Path:
    configured = str(settings.detector_engine_path or "").strip()
    if configured:
        return Path(configured)

    capability_label = f"sm{capability[0]}{capability[1]}" if capability else "smunknown"
    precision = "fp16" if bool(settings.detector_fp16_enabled) else "fp32"
    cuda_label = _slug(f"cuda{cuda_version or 'unknown'}")
    filename = (
        f"{model_path.stem}_{_slug(gpu_name)}_{capability_label}_{cuda_label}_"
        f"{precision}_b1_{int(settings.detect_imgsz)}.engine"
    )
    return Path(settings.detector_engine_auto_build_dir) / filename


def _set_detector_engine_path(engine_path: Path) -> None:
    resolved = str(engine_path.resolve())
    settings.detector_engine_path = resolved
    os.environ["DETECTOR_ENGINE_PATH"] = resolved


def ensure_detector_tensorrt_engine() -> dict[str, Any]:
    logger = get_logger("app.tensorrt_engine")
    auto_enabled = bool(settings.detector_engine_auto_build_enabled)
    required = bool(settings.detector_engine_auto_build_required)
    model_path = Path(settings.detector_model_path)

    configured_engine = str(settings.detector_engine_path or "").strip()
    if configured_engine and Path(configured_engine).exists():
        _set_detector_engine_path(Path(configured_engine))
        result = TensorRTEngineResult(
            status="ready",
            reason="configured_engine_exists",
            engine_path=str(Path(configured_engine).resolve()),
            model_path=str(model_path),
            backend="tensorrt" if Path(configured_engine).suffix.lower() == ".engine" else "onnx",
            auto_build_enabled=auto_enabled,
            required=required,
        )
        return _record(result).as_dict()

    if not auto_enabled:
        return _record(
            TensorRTEngineResult(
                status="pytorch",
                reason="auto_build_disabled",
                model_path=str(model_path),
                auto_build_enabled=auto_enabled,
                required=required,
            )
        ).as_dict()

    if not model_path.exists():
        message = f"Detector model not found: {model_path}"
        logger.error(message, extra={"action": "tensorrt_engine_prepare", "status": "error", "reason": "missing_model"})
        if required:
            raise FileNotFoundError(message)
        return _record(
            TensorRTEngineResult(
                status="pytorch",
                reason="missing_model",
                model_path=str(model_path),
                auto_build_enabled=auto_enabled,
                required=required,
            )
        ).as_dict()

    try:
        import torch

        if not torch.cuda.is_available():
            result = TensorRTEngineResult(
                status="pytorch",
                reason="cuda_unavailable",
                model_path=str(model_path),
                device=settings.resolved_detect_device(),
                auto_build_enabled=auto_enabled,
                required=required,
            )
            if required:
                raise RuntimeError("CUDA unavailable for required TensorRT engine build")
            return _record(result).as_dict()

        device = settings.resolved_detect_device()
        device_index = _cuda_device_index(device)
        gpu_name = torch.cuda.get_device_name(device_index)
        props = torch.cuda.get_device_properties(device_index)
        capability = (int(props.major), int(props.minor))
        cuda_version = str(torch.version.cuda or "unknown")
    except Exception as exc:
        logger.exception(
            "Failed to inspect CUDA device before TensorRT export",
            extra={"action": "tensorrt_engine_prepare", "status": "error", "reason": "cuda_inspect_failed"},
        )
        if required:
            raise
        return _record(
            TensorRTEngineResult(
                status="pytorch",
                reason=f"cuda_inspect_failed:{exc}",
                model_path=str(model_path),
                auto_build_enabled=auto_enabled,
                required=required,
            )
        ).as_dict()

    target_path = _target_engine_path(model_path, gpu_name=gpu_name, capability=capability, cuda_version=cuda_version)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        _set_detector_engine_path(target_path)
        result = TensorRTEngineResult(
            status="ready",
            reason="cached_engine_exists",
            engine_path=str(target_path.resolve()),
            model_path=str(model_path),
            gpu_name=gpu_name,
            device=device,
            backend="tensorrt",
            auto_build_enabled=auto_enabled,
            required=required,
        )
        logger.info(
            "TensorRT engine cache found: %s",
            target_path,
            extra={"action": "tensorrt_engine_prepare", "status": "ready", "reason": "cached_engine_exists"},
        )
        return _record(result).as_dict()

    try:
        import tensorrt as trt  # noqa: F401
        from ultralytics import YOLO
    except Exception as exc:
        logger.exception(
            "TensorRT export dependencies unavailable",
            extra={"action": "tensorrt_engine_prepare", "status": "error", "reason": "missing_dependency"},
        )
        if required:
            raise
        return _record(
            TensorRTEngineResult(
                status="pytorch",
                reason=f"missing_dependency:{exc}",
                model_path=str(model_path),
                gpu_name=gpu_name,
                device=device,
                auto_build_enabled=auto_enabled,
                required=required,
            )
        ).as_dict()

    build_dir = target_path.parent / ".build" / target_path.stem
    build_dir.mkdir(parents=True, exist_ok=True)
    work_weights = build_dir / model_path.name
    shutil.copy2(model_path, work_weights)

    logger.warning(
        "Building TensorRT engine for current GPU model=%s target=%s gpu=%s",
        model_path,
        target_path,
        gpu_name,
        extra={"action": "tensorrt_engine_prepare", "status": "building", "reason": "cache_miss"},
    )

    try:
        model = YOLO(str(work_weights))
        exported = Path(
            model.export(
                format="engine",
                imgsz=int(settings.detect_imgsz),
                half=bool(settings.detector_fp16_enabled),
                device=str(device_index),
                workspace=int(settings.detector_engine_auto_build_workspace_gb),
                verbose=True,
            )
        )
        if not exported.exists():
            raise RuntimeError(f"Ultralytics did not create expected engine: {exported}")
        if exported.resolve() != target_path.resolve():
            shutil.copy2(exported, target_path)
        _set_detector_engine_path(target_path)
        result = TensorRTEngineResult(
            status="ready",
            reason="built",
            engine_path=str(target_path.resolve()),
            model_path=str(model_path),
            gpu_name=gpu_name,
            device=device,
            backend="tensorrt",
            auto_build_enabled=auto_enabled,
            required=required,
        )
        logger.warning(
            "TensorRT engine ready: %s",
            target_path,
            extra={"action": "tensorrt_engine_prepare", "status": "ready", "reason": "built"},
        )
        return _record(result).as_dict()
    except Exception as exc:
        logger.exception(
            "Failed to build TensorRT engine; falling back to PyTorch",
            extra={"action": "tensorrt_engine_prepare", "status": "error", "reason": "build_failed"},
        )
        if required:
            raise
        return _record(
            TensorRTEngineResult(
                status="pytorch",
                reason=f"build_failed:{exc}",
                model_path=str(model_path),
                gpu_name=gpu_name,
                device=device,
                auto_build_enabled=auto_enabled,
                required=required,
            )
        ).as_dict()
