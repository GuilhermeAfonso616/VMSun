from app.core.config import settings
from app.services.analytic_runtime_guard import evaluate_worker_start_guard, update_runtime_tuning


def test_gpu_guard_blocks_when_memory_limit_is_reached(monkeypatch):
    monkeypatch.setattr(settings, "analytic_gpu_guard_enabled", True)
    monkeypatch.setattr(settings, "analytic_gpu_max_memory_mb", 5000)
    monkeypatch.setattr(settings, "analytic_gpu_max_active_workers", 12)

    decision = evaluate_worker_start_guard(
        active_workers=3,
        gpu={"available": True, "memory_used_mb": 5200, "memory_total_mb": 6138},
    )

    assert decision.allowed is False
    assert decision.reason == "max_gpu_memory"


def test_gpu_guard_blocks_new_worker_when_active_limit_is_reached(monkeypatch):
    monkeypatch.setattr(settings, "analytic_gpu_guard_enabled", True)
    monkeypatch.setattr(settings, "analytic_gpu_max_memory_mb", 5000)
    monkeypatch.setattr(settings, "analytic_gpu_max_active_workers", 2)

    decision = evaluate_worker_start_guard(
        active_workers=2,
        starting_existing_worker=False,
        gpu={"available": True, "memory_used_mb": 1000, "memory_total_mb": 6138},
    )

    assert decision.allowed is False
    assert decision.reason == "max_active_workers"


def test_gpu_guard_allows_existing_worker_restart_at_active_limit(monkeypatch):
    monkeypatch.setattr(settings, "analytic_gpu_guard_enabled", True)
    monkeypatch.setattr(settings, "analytic_gpu_max_memory_mb", 5000)
    monkeypatch.setattr(settings, "analytic_gpu_max_active_workers", 2)

    decision = evaluate_worker_start_guard(
        active_workers=2,
        starting_existing_worker=True,
        gpu={"available": True, "memory_used_mb": 1000, "memory_total_mb": 6138},
    )

    assert decision.allowed is True


def test_runtime_tuning_updates_settings():
    original = {
        "analytic_gpu_guard_enabled": settings.analytic_gpu_guard_enabled,
        "analytic_gpu_max_memory_mb": settings.analytic_gpu_max_memory_mb,
        "analytic_gpu_max_active_workers": settings.analytic_gpu_max_active_workers,
        "detector_fp16_enabled": settings.detector_fp16_enabled,
        "inference_pool_enabled": settings.inference_pool_enabled,
        "inference_pool_max_queue_size": settings.inference_pool_max_queue_size,
        "inference_pool_job_timeout_seconds": settings.inference_pool_job_timeout_seconds,
        "inference_pool_max_job_age_seconds": settings.inference_pool_max_job_age_seconds,
        "inference_pool_overflow_policy": settings.inference_pool_overflow_policy,
        "inference_pool_backend": settings.inference_pool_backend,
        "inference_pool_count": settings.inference_pool_count,
        "inference_pool_max_cameras_per_pool": settings.inference_pool_max_cameras_per_pool,
        "inference_pool_central_url": settings.inference_pool_central_url,
        "inference_pool_central_jpeg_quality": settings.inference_pool_central_jpeg_quality,
        "inference_pool_central_fallback_direct": settings.inference_pool_central_fallback_direct,
    }
    try:
        snapshot = update_runtime_tuning(
            gpu_guard_enabled=False,
            max_gpu_memory_mb=4096,
            max_active_workers=8,
            detector_fp16_enabled=False,
            inference_pool_enabled=True,
            inference_pool_max_queue_size=4,
            inference_pool_job_timeout_seconds=1.25,
            inference_pool_max_job_age_seconds=0.75,
            inference_pool_overflow_policy="reject_new",
            inference_pool_backend="central",
            inference_pool_count=4,
            inference_pool_max_cameras_per_pool=8,
            inference_pool_central_url="http://runtime:8001/internal/inference/track",
            inference_pool_central_jpeg_quality=72,
            inference_pool_central_fallback_direct=True,
        )

        assert settings.analytic_gpu_guard_enabled is False
        assert settings.analytic_gpu_max_memory_mb == 4096
        assert settings.analytic_gpu_max_active_workers == 8
        assert settings.detector_fp16_enabled is False
        assert settings.inference_pool_enabled is True
        assert settings.inference_pool_max_queue_size == 4
        assert settings.inference_pool_job_timeout_seconds == 1.25
        assert settings.inference_pool_max_job_age_seconds == 0.75
        assert settings.inference_pool_overflow_policy == "reject_new"
        assert settings.inference_pool_backend == "central"
        assert settings.inference_pool_count == 4
        assert settings.inference_pool_max_cameras_per_pool == 8
        assert settings.inference_pool_central_url == "http://runtime:8001/internal/inference/track"
        assert settings.inference_pool_central_jpeg_quality == 72
        assert settings.inference_pool_central_fallback_direct is True
        assert snapshot["inference_pool_backend"] == "central"
        assert snapshot["inference_pool_count"] == 4
    finally:
        for key, value in original.items():
            setattr(settings, key, value)
