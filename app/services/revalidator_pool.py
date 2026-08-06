from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from typing import Callable, TypeVar

from app.core.config import settings
from app.core.logging import get_logger


T = TypeVar("T")


class RevalidatorPoolStaleJob(RuntimeError):
    pass


@dataclass(frozen=True)
class _PoolSignature:
    max_concurrency: int
    max_queue_size: int


class RevalidatorPool:
    """Shared limiter for IA2/IA3 event-time revalidation jobs."""

    def __init__(self) -> None:
        self._logger = get_logger("app.analytics.revalidation")
        self._lock = Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._semaphore: BoundedSemaphore | None = None
        self._signature: _PoolSignature | None = None

    def run(self, kind: str, fn: Callable[[], T], fallback: Callable[[str], T]) -> T:
        if not bool(settings.revalidator_pool_enabled):
            return fn()

        executor, semaphore, signature = self._ensure_executor()
        if not semaphore.acquire(blocking=False):
            self._logger.warning(
                "Revalidator pool full kind=%s queue_size=%s concurrency=%s",
                kind,
                signature.max_queue_size,
                signature.max_concurrency,
                extra={
                    "action": "revalidator_pool_reject",
                    "status": "degraded",
                    "reason": "queue_full",
                },
            )
            return fallback("revalidator_pool_queue_full")

        submitted_at = time.monotonic()
        self._logger.info(
            "Revalidator pool enqueue kind=%s concurrency=%s queue_size=%s",
            kind,
            signature.max_concurrency,
            signature.max_queue_size,
            extra={
                "action": "revalidator_pool_enqueue",
                "status": "running",
                "reason": "queued",
            },
        )

        def wrapped() -> T:
            try:
                max_age = max(0.0, float(settings.revalidator_pool_max_job_age_seconds))
                age = time.monotonic() - submitted_at
                if max_age and age > max_age:
                    raise RevalidatorPoolStaleJob(f"age_seconds={age:.3f}")
                started_at = time.monotonic()
                result = fn()
                self._logger.info(
                    "Revalidator pool complete kind=%s wait_ms=%.2f run_ms=%.2f",
                    kind,
                    (started_at - submitted_at) * 1000.0,
                    (time.monotonic() - started_at) * 1000.0,
                    extra={
                        "action": "revalidator_pool_complete",
                        "status": "running",
                        "reason": "ok",
                    },
                )
                return result
            finally:
                semaphore.release()

        try:
            future = executor.submit(wrapped)
        except Exception as exc:
            semaphore.release()
            self._logger.exception(
                "Revalidator pool submit failed kind=%s",
                kind,
                extra={
                    "action": "revalidator_pool_submit",
                    "status": "degraded",
                    "reason": exc.__class__.__name__,
                },
            )
            return fallback(f"revalidator_pool_submit_failed:{exc.__class__.__name__}")

        timeout = max(0.1, float(settings.revalidator_pool_job_timeout_seconds))
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            self._logger.warning(
                "Revalidator pool timeout kind=%s timeout_seconds=%.3f",
                kind,
                timeout,
                extra={
                    "action": "revalidator_pool_timeout",
                    "status": "degraded",
                    "reason": "timeout",
                },
            )
            return fallback("revalidator_pool_timeout")
        except RevalidatorPoolStaleJob:
            self._logger.warning(
                "Revalidator pool stale job kind=%s",
                kind,
                extra={
                    "action": "revalidator_pool_stale",
                    "status": "degraded",
                    "reason": "stale_job",
                },
            )
            return fallback("revalidator_pool_stale")
        except Exception as exc:
            self._logger.exception(
                "Revalidator pool job failed kind=%s",
                kind,
                extra={
                    "action": "revalidator_pool_failed",
                    "status": "degraded",
                    "reason": exc.__class__.__name__,
                },
            )
            return fallback(f"revalidator_pool_failed:{exc.__class__.__name__}")

    def _ensure_executor(self) -> tuple[ThreadPoolExecutor, BoundedSemaphore, _PoolSignature]:
        signature = _PoolSignature(
            max_concurrency=max(1, int(settings.revalidator_pool_max_concurrency)),
            max_queue_size=max(0, int(settings.revalidator_pool_max_queue_size)),
        )
        with self._lock:
            if self._executor is not None and self._semaphore is not None and self._signature == signature:
                return self._executor, self._semaphore, signature

            old_executor = self._executor
            self._executor = ThreadPoolExecutor(
                max_workers=signature.max_concurrency,
                thread_name_prefix="revalidator-pool",
            )
            self._semaphore = BoundedSemaphore(signature.max_concurrency + signature.max_queue_size)
            self._signature = signature

        if old_executor is not None:
            old_executor.shutdown(wait=False, cancel_futures=True)
        return self._executor, self._semaphore, signature


_POOL: RevalidatorPool | None = None
_POOL_LOCK = Lock()


def get_revalidator_pool() -> RevalidatorPool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = RevalidatorPool()
    return _POOL
