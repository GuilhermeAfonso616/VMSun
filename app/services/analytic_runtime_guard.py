from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.gpu_snapshot import read_gpu_snapshot


def _env_bool(value: bool) -> str:
    return "true" if bool(value) else "false"


def _clamp_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, parsed)


def _clamp_float(value: Any, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(minimum, parsed)


@dataclass(frozen=True)
class StartGuardDecision:
    allowed: bool
    reason: str
    message: str
    active_workers: int
    max_active_workers: int
    gpu_memory_used_mb: float | None
    gpu_memory_total_mb: float | None
    max_gpu_memory_mb: int
    gpu_available: bool
    guard_enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "message": self.message,
            "active_workers": self.active_workers,
            "max_active_workers": self.max_active_workers,
            "gpu_memory_used_mb": self.gpu_memory_used_mb,
            "gpu_memory_total_mb": self.gpu_memory_total_mb,
            "max_gpu_memory_mb": self.max_gpu_memory_mb,
            "gpu_available": self.gpu_available,
            "guard_enabled": self.guard_enabled,
        }


def update_runtime_tuning(
    *,
    gpu_guard_enabled: bool | None = None,
    max_gpu_memory_mb: int | None = None,
    max_active_workers: int | None = None,
    detector_fp16_enabled: bool | None = None,
    inference_pool_enabled: bool | None = None,
    inference_pool_max_queue_size: int | None = None,
    inference_pool_job_timeout_seconds: float | None = None,
    inference_pool_max_job_age_seconds: float | None = None,
    inference_pool_overflow_policy: str | None = None,
    inference_pool_backend: str | None = None,
    inference_pool_count: int | None = None,
    inference_pool_max_cameras_per_pool: int | None = None,
    inference_pool_central_url: str | None = None,
    inference_pool_central_jpeg_quality: int | None = None,
    inference_pool_central_fallback_direct: bool | None = None,
) -> dict[str, Any]:
    if gpu_guard_enabled is not None:
        settings.analytic_gpu_guard_enabled = bool(gpu_guard_enabled)
        os.environ["ANALYTIC_GPU_GUARD_ENABLED"] = _env_bool(settings.analytic_gpu_guard_enabled)
    if max_gpu_memory_mb is not None:
        settings.analytic_gpu_max_memory_mb = _clamp_int(max_gpu_memory_mb, settings.analytic_gpu_max_memory_mb)
        os.environ["ANALYTIC_GPU_MAX_MEMORY_MB"] = str(settings.analytic_gpu_max_memory_mb)
    if max_active_workers is not None:
        settings.analytic_gpu_max_active_workers = _clamp_int(max_active_workers, settings.analytic_gpu_max_active_workers)
        os.environ["ANALYTIC_GPU_MAX_ACTIVE_WORKERS"] = str(settings.analytic_gpu_max_active_workers)
    if detector_fp16_enabled is not None:
        settings.detector_fp16_enabled = bool(detector_fp16_enabled)
        os.environ["DETECTOR_FP16_ENABLED"] = _env_bool(settings.detector_fp16_enabled)
    if inference_pool_enabled is not None:
        settings.inference_pool_enabled = bool(inference_pool_enabled)
        os.environ["INFERENCE_POOL_ENABLED"] = _env_bool(settings.inference_pool_enabled)
    if inference_pool_max_queue_size is not None:
        settings.inference_pool_max_queue_size = _clamp_int(
            inference_pool_max_queue_size,
            settings.inference_pool_max_queue_size,
            minimum=1,
        )
        os.environ["INFERENCE_POOL_MAX_QUEUE_SIZE"] = str(settings.inference_pool_max_queue_size)
    if inference_pool_job_timeout_seconds is not None:
        settings.inference_pool_job_timeout_seconds = _clamp_float(
            inference_pool_job_timeout_seconds,
            settings.inference_pool_job_timeout_seconds,
            minimum=0.1,
        )
        os.environ["INFERENCE_POOL_JOB_TIMEOUT_SECONDS"] = str(settings.inference_pool_job_timeout_seconds)
    if inference_pool_max_job_age_seconds is not None:
        settings.inference_pool_max_job_age_seconds = _clamp_float(
            inference_pool_max_job_age_seconds,
            settings.inference_pool_max_job_age_seconds,
            minimum=0.0,
        )
        os.environ["INFERENCE_POOL_MAX_JOB_AGE_SECONDS"] = str(settings.inference_pool_max_job_age_seconds)
    if inference_pool_overflow_policy is not None:
        policy = str(inference_pool_overflow_policy or "").strip().lower()
        if policy in {"reject", "reject_new", "block_new"}:
            policy = "reject_new"
        elif policy in {"drop_oldest", "latest", "latest_only"}:
            policy = "drop_oldest"
        else:
            policy = "drop_oldest"
        settings.inference_pool_overflow_policy = policy
        os.environ["INFERENCE_POOL_OVERFLOW_POLICY"] = policy
    if inference_pool_backend is not None:
        backend = str(inference_pool_backend or "").strip().lower()
        if backend in {"central", "remote", "runtime", "runtime_api"}:
            backend = "central"
        else:
            backend = "local"
        settings.inference_pool_backend = backend
        os.environ["INFERENCE_POOL_BACKEND"] = backend
    if inference_pool_count is not None:
        settings.inference_pool_count = _clamp_int(
            inference_pool_count,
            settings.inference_pool_count,
            minimum=1,
        )
        os.environ["INFERENCE_POOL_COUNT"] = str(settings.inference_pool_count)
    if inference_pool_max_cameras_per_pool is not None:
        settings.inference_pool_max_cameras_per_pool = _clamp_int(
            inference_pool_max_cameras_per_pool,
            settings.inference_pool_max_cameras_per_pool,
            minimum=1,
        )
        os.environ["INFERENCE_POOL_MAX_CAMERAS_PER_POOL"] = str(settings.inference_pool_max_cameras_per_pool)
    if inference_pool_central_url is not None:
        settings.inference_pool_central_url = str(inference_pool_central_url or "").strip()
        os.environ["INFERENCE_POOL_CENTRAL_URL"] = settings.inference_pool_central_url
    if inference_pool_central_jpeg_quality is not None:
        settings.inference_pool_central_jpeg_quality = min(
            95,
            _clamp_int(
                inference_pool_central_jpeg_quality,
                settings.inference_pool_central_jpeg_quality,
                minimum=40,
            ),
        )
        os.environ["INFERENCE_POOL_CENTRAL_JPEG_QUALITY"] = str(settings.inference_pool_central_jpeg_quality)
    if inference_pool_central_fallback_direct is not None:
        settings.inference_pool_central_fallback_direct = bool(inference_pool_central_fallback_direct)
        os.environ["INFERENCE_POOL_CENTRAL_FALLBACK_DIRECT"] = _env_bool(settings.inference_pool_central_fallback_direct)

    return runtime_tuning_snapshot()


def runtime_tuning_snapshot(*, active_workers: int | None = None, gpu: dict[str, Any] | None = None) -> dict[str, Any]:
    gpu_snapshot = gpu if isinstance(gpu, dict) else read_gpu_snapshot()
    memory_used = gpu_snapshot.get("memory_used_mb") if gpu_snapshot else None
    memory_total = gpu_snapshot.get("memory_total_mb") if gpu_snapshot else None
    max_memory = int(settings.analytic_gpu_max_memory_mb)
    memory_margin = None
    if memory_used is not None:
        try:
            memory_margin = round(float(max_memory) - float(memory_used), 2)
        except (TypeError, ValueError):
            memory_margin = None

    return {
        "gpu_guard_enabled": bool(settings.analytic_gpu_guard_enabled),
        "max_gpu_memory_mb": max_memory,
        "max_active_workers": int(settings.analytic_gpu_max_active_workers),
        "active_workers": int(active_workers) if active_workers is not None else None,
        "gpu_available": bool((gpu_snapshot or {}).get("available", False)),
        "gpu_memory_used_mb": memory_used,
        "gpu_memory_total_mb": memory_total,
        "gpu_memory_margin_mb": memory_margin,
        "detector_fp16_enabled": bool(settings.detector_fp16_enabled),
        "inference_pool_enabled": bool(settings.inference_pool_enabled),
        "inference_pool_max_queue_size": int(settings.inference_pool_max_queue_size),
        "inference_pool_job_timeout_seconds": float(settings.inference_pool_job_timeout_seconds),
        "inference_pool_max_job_age_seconds": float(settings.inference_pool_max_job_age_seconds),
        "inference_pool_overflow_policy": str(settings.inference_pool_overflow_policy or "drop_oldest"),
        "inference_pool_backend": str(settings.inference_pool_backend or "local"),
        "inference_pool_count": int(settings.inference_pool_count),
        "inference_pool_max_cameras_per_pool": int(settings.inference_pool_max_cameras_per_pool),
        "inference_pool_central_url": str(settings.inference_pool_central_url or ""),
        "inference_pool_central_jpeg_quality": int(settings.inference_pool_central_jpeg_quality),
        "inference_pool_central_fallback_direct": bool(settings.inference_pool_central_fallback_direct),
    }


def evaluate_worker_start_guard(
    *,
    active_workers: int,
    starting_existing_worker: bool = False,
    gpu: dict[str, Any] | None = None,
) -> StartGuardDecision:
    guard_enabled = bool(settings.analytic_gpu_guard_enabled)
    max_active = int(settings.analytic_gpu_max_active_workers)
    max_memory = int(settings.analytic_gpu_max_memory_mb)
    gpu_snapshot = gpu if isinstance(gpu, dict) else read_gpu_snapshot()
    gpu_available = bool((gpu_snapshot or {}).get("available", False))
    memory_used_raw = (gpu_snapshot or {}).get("memory_used_mb")
    memory_total_raw = (gpu_snapshot or {}).get("memory_total_mb")

    memory_used = None
    memory_total = None
    try:
        if memory_used_raw is not None:
            memory_used = float(memory_used_raw)
    except (TypeError, ValueError):
        memory_used = None
    try:
        if memory_total_raw is not None:
            memory_total = float(memory_total_raw)
    except (TypeError, ValueError):
        memory_total = None

    if not guard_enabled:
        return StartGuardDecision(
            True,
            "disabled",
            "Protecao de GPU desabilitada.",
            int(active_workers),
            max_active,
            memory_used,
            memory_total,
            max_memory,
            gpu_available,
            guard_enabled,
        )

    if not starting_existing_worker and max_active > 0 and int(active_workers) >= max_active:
        return StartGuardDecision(
            False,
            "max_active_workers",
            f"Limite de workers ativos atingido ({active_workers}/{max_active}).",
            int(active_workers),
            max_active,
            memory_used,
            memory_total,
            max_memory,
            gpu_available,
            guard_enabled,
        )

    if memory_used is not None and max_memory > 0 and memory_used >= float(max_memory):
        return StartGuardDecision(
            False,
            "max_gpu_memory",
            f"VRAM acima do limite configurado ({memory_used:.0f}/{max_memory} MB).",
            int(active_workers),
            max_active,
            memory_used,
            memory_total,
            max_memory,
            gpu_available,
            guard_enabled,
        )

    return StartGuardDecision(
        True,
        "ok",
        "Inicio permitido pela protecao de GPU.",
        int(active_workers),
        max_active,
        memory_used,
        memory_total,
        max_memory,
        gpu_available,
        guard_enabled,
    )
