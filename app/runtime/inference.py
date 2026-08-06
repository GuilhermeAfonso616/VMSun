"""Fachada de compatibilidade e lifecycle da inferencia do runtime."""

from __future__ import annotations

from threading import Lock, Thread
from typing import Any

import numpy as np

from app.analytics.detector import PersonDetector
from app.core.config import settings
from app.core.logging import get_inference_pool_logger
from app.runtime.inference_detection import (
    DetectionService,
    InferenceBackpressureError,
    _disable_corrupt_detector_engine,
    is_detector_engine_failure,
)
from app.runtime.inference_pool import (
    InferencePool,
    InferencePoolGroup,
    get_inference_pool,
    get_inference_pool_group,
    release_inference_camera,
    reset_inference_pool_services,
)
from app.runtime.inference_scheduling import (
    InferenceDecision,
    MotionAwareInferenceScheduler,
    MotionGate,
    NormalInferenceScheduler,
)
from app.runtime.readiness import mark_degraded, mark_ready, mark_starting, snapshot as readiness_snapshot


pool_logger = get_inference_pool_logger()


_recovery_lock = Lock()
_recovery_thread: Thread | None = None


def _reset_runtime_inference_services() -> None:
    """Release predictors that may retain a failed CUDA/TensorRT context."""
    reset_inference_pool_services()
    PersonDetector.reset_shared_model()


def warmup_runtime_detector() -> dict[str, Any]:
    """Load the detector and execute a real probe before workers are released."""
    mark_starting("detector_probe")
    _reset_runtime_inference_services()
    # A small valid BGR frame exercises model loading, predictor creation and
    # the CUDA/TensorRT execution path without depending on a camera stream.
    probe = np.zeros((360, 640, 3), dtype=np.uint8)
    try:
        service = DetectionService(camera_id=0, use_pool=False)
        service._infer_direct(probe)
    except BaseException as exc:
        if not is_detector_engine_failure(exc):
            raise
        _disable_corrupt_detector_engine()
        service = DetectionService(camera_id=0, use_pool=False, force_pytorch=True)
        service._infer_direct(probe)
    mark_ready("detector_probe_ok")
    return readiness_snapshot()


def _runtime_recovery_loop() -> None:
    import time

    delay = 2.0
    while True:
        time.sleep(delay)
        try:
            warmup_runtime_detector()
        except BaseException as exc:
            mark_degraded("detector_recovery_failed", exc)
            pool_logger.error(
                "event=runtime_recovery_failed error_type=%s error=%s retry_seconds=%.1f",
                type(exc).__name__,
                exc,
                delay,
                extra={
                    "action": "runtime_recovery",
                    "status": "degraded",
                    "reason": "detector_recovery_failed",
                },
            )
            delay = min(30.0, delay * 2.0)
            continue
        pool_logger.info(
            "event=runtime_recovered retry_seconds=%.1f",
            delay,
            extra={
                "action": "runtime_recovery",
                "status": "ready",
                "reason": "detector_probe_ok",
            },
        )
        return


def mark_runtime_degraded(reason: str, error: BaseException | str | None = None) -> None:
    """Publish degraded readiness and schedule one bounded recovery loop."""
    global _recovery_thread
    mark_degraded(reason, error)
    _reset_runtime_inference_services()
    with _recovery_lock:
        if _recovery_thread is not None and _recovery_thread.is_alive():
            return
        _recovery_thread = Thread(
            target=_runtime_recovery_loop,
            name="runtime-detector-recovery",
            daemon=True,
        )
        _recovery_thread.start()
