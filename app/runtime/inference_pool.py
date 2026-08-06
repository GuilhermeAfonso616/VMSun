"""Fila serializada, distribuicao e ciclo de vida dos pools de inferencia."""

from __future__ import annotations

from threading import Condition, Event, Lock, Thread
from typing import Any, Callable

from app.core.config import settings
from app.core.logging import get_inference_pool_logger
from app.runtime.inference_detection import (
    DetectionService,
    _disable_corrupt_detector_engine,
    is_detector_engine_failure,
)


pool_logger = get_inference_pool_logger()


class _InferenceJob:
    def __init__(
        self,
        *,
        camera_id: int,
        infer_frame,
        offset_x: int,
        offset_y: int,
        scale_x: float,
        scale_y: float,
    ):
        import time

        self.camera_id = int(camera_id)
        self.infer_frame = infer_frame
        self.offset_x = int(offset_x)
        self.offset_y = int(offset_y)
        self.scale_x = float(scale_x)
        self.scale_y = float(scale_y)
        self.enqueued_at = time.perf_counter()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.done = Event()
        self.result: tuple[list[dict], float] | None = None
        self.error: BaseException | None = None

    def finish(self, result: tuple[list[dict], float]) -> None:
        import time

        self.result = result
        self.finished_at = time.perf_counter()
        self.done.set()

    def fail(self, error: BaseException) -> None:
        import time

        self.error = error
        self.finished_at = time.perf_counter()
        self.done.set()

    def mark_started(self) -> None:
        import time

        self.started_at = time.perf_counter()

    def queue_wait_ms(self) -> float:
        end = self.started_at if self.started_at is not None else self.finished_at
        if end is None:
            return 0.0
        return max(0.0, (end - self.enqueued_at) * 1000.0)

    def total_latency_ms(self) -> float:
        if self.finished_at is None:
            return 0.0
        return max(0.0, (self.finished_at - self.enqueued_at) * 1000.0)


class InferencePool:
    def __init__(self, service_factory: Callable[[], Any] | None = None, *, pool_id: int = 0):
        self.pool_id = int(pool_id)
        self._condition = Condition()
        self._jobs_by_camera: dict[int, _InferenceJob] = {}
        self._order: list[int] = []
        self._worker: Thread | None = None
        self._stop_requested = False
        self._services_by_camera: dict[int, DetectionService] = {}
        self._service_lock = Lock()
        self._service_factory = service_factory
        self._stats_lock = Lock()
        self._active_job_camera_id: int | None = None
        self._active_job_started_at: float | None = None
        self._release_after_active: set[int] = set()
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._timed_out = 0
        self._replaced = 0
        self._rejected = 0
        self._dropped_oldest = 0
        self._stale_dropped = 0
        self._last_queue_wait_ms = 0.0
        self._last_total_latency_ms = 0.0
        self._last_infer_ms = 0.0
        self._detector_recoveries = 0
        self._consecutive_detector_failures = 0
        self._last_detector_recovery_reason: str | None = None

    def _counter_snapshot(self) -> dict[str, int | float]:
        with self._stats_lock:
            return {
                "submitted": self._submitted,
                "completed": self._completed,
                "failed": self._failed,
                "timed_out": self._timed_out,
                "replaced": self._replaced,
                "rejected": self._rejected,
                "dropped_oldest": self._dropped_oldest,
                "stale_dropped": self._stale_dropped,
                "last_queue_wait_ms": self._last_queue_wait_ms,
                "last_total_latency_ms": self._last_total_latency_ms,
                "last_infer_ms": self._last_infer_ms,
                "detector_recoveries": self._detector_recoveries,
                "consecutive_detector_failures": self._consecutive_detector_failures,
            }

    def _log_event(
        self,
        event: str,
        *,
        status: str,
        reason: str,
        camera_id: int | None = None,
        level: str = "info",
        **fields,
    ) -> None:
        counters = self._counter_snapshot()
        with self._condition:
            queue_size = len(self._jobs_by_camera)
            active_camera_id = self._active_job_camera_id
        payload = {
            "event": event,
            "pool_id": self.pool_id,
            "camera_id": camera_id if camera_id is not None else "-",
            "queue_size": queue_size,
            "active_camera_id": active_camera_id if active_camera_id is not None else "-",
            **fields,
            **counters,
        }
        message = " ".join(f"{key}={value}" for key, value in payload.items())
        log_fn = getattr(pool_logger, level, pool_logger.info)
        log_fn(
            message,
            extra={
                "camera_id": camera_id if camera_id is not None else "-",
                "action": f"inference_pool_{event}",
                "status": status,
                "reason": reason,
            },
        )

    @staticmethod
    def _overflow_policy() -> str:
        policy = str(getattr(settings, "inference_pool_overflow_policy", "drop_oldest") or "").strip().lower()
        if policy in {"reject", "reject_new", "block_new"}:
            return "reject_new"
        if policy in {"drop_oldest", "latest", "latest_only"}:
            return "drop_oldest"
        return "drop_oldest"

    @staticmethod
    def _max_job_age_seconds() -> float:
        try:
            return max(0.0, float(getattr(settings, "inference_pool_max_job_age_seconds", 1.0)))
        except (TypeError, ValueError):
            return 1.0

    def _record_replaced(self) -> None:
        with self._stats_lock:
            self._replaced += 1

    def _record_rejected(self) -> None:
        with self._stats_lock:
            self._rejected += 1

    def _record_dropped_oldest(self) -> None:
        with self._stats_lock:
            self._dropped_oldest += 1

    def _record_stale_dropped(self) -> None:
        with self._stats_lock:
            self._stale_dropped += 1

    def _record_timeout(self) -> None:
        with self._stats_lock:
            self._timed_out += 1

    def _record_completed(self, job: _InferenceJob, infer_ms: float) -> None:
        with self._stats_lock:
            self._completed += 1
            self._consecutive_detector_failures = 0
            self._last_queue_wait_ms = round(job.queue_wait_ms(), 2)
            self._last_total_latency_ms = round(job.total_latency_ms(), 2)
            self._last_infer_ms = round(float(infer_ms), 2)

    def _record_failed(self, job: _InferenceJob | None = None) -> None:
        with self._stats_lock:
            self._failed += 1
            if job is not None:
                self._last_queue_wait_ms = round(job.queue_wait_ms(), 2)
                self._last_total_latency_ms = round(job.total_latency_ms(), 2)

    @staticmethod
    def _is_detector_engine_failure(error: BaseException) -> bool:
        return is_detector_engine_failure(error)

    def _recover_detector_after_failure(self, *, camera_id: int, error: BaseException) -> None:
        reason = f"{type(error).__name__}:{str(error)[:160]}"
        with self._service_lock:
            removed = self._services_by_camera.pop(int(camera_id), None) is not None
            with self._stats_lock:
                self._detector_recoveries += 1
                self._consecutive_detector_failures += 1
                consecutive = self._consecutive_detector_failures
                self._last_detector_recovery_reason = reason

            # If the engine/predictor is poisoned globally, a single per-camera
            # reset is not enough. Periodically flush the whole pool so the next
            # job rebuilds detector state from scratch instead of reusing a bad
            # TensorRT/Ultralytics context forever.
            flushed_all = False
            if consecutive >= 3:
                self._services_by_camera.clear()
                flushed_all = True

        self._log_event(
            "detector_recover",
            status="recovering",
            reason="detector_engine_failure",
            camera_id=camera_id,
            removed_service=removed,
            flushed_all=flushed_all,
            consecutive_detector_failures=consecutive,
            error=reason,
            level="warning",
        )

    def _ensure_started(self) -> None:
        if self._stop_requested:
            raise RuntimeError("pool de inferencia parada")
        if self._worker and self._worker.is_alive():
            return
        with self._condition:
            if self._stop_requested:
                raise RuntimeError("pool de inferencia parada")
            if self._worker and self._worker.is_alive():
                return
            self._worker = Thread(target=self._run, name=f"inference-pool-{self.pool_id}", daemon=True)
            self._worker.start()

    def stop(self, *, timeout: float = 1.0) -> None:
        with self._condition:
            self._stop_requested = True
            for job in list(self._jobs_by_camera.values()):
                job.fail(RuntimeError("pool de inferencia reiniciada"))
            self._jobs_by_camera.clear()
            self._order.clear()
            self._condition.notify_all()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=timeout)

    def _drop_oldest_locked(self) -> bool:
        while self._order:
            oldest_camera_id = self._order.pop(0)
            oldest = self._jobs_by_camera.pop(oldest_camera_id, None)
            if oldest is None:
                continue
            oldest.fail(TimeoutError("inference job removido para liberar fila para frame mais recente"))
            self._record_dropped_oldest()
            self._log_event(
                "drop_oldest",
                status="dropped",
                reason="queue_full",
                camera_id=oldest.camera_id,
                queue_wait_ms=round(oldest.queue_wait_ms(), 2),
            )
            return True
        return False

    def infer(
        self,
        *,
        camera_id: int,
        infer_frame,
        offset_x: int,
        offset_y: int,
        scale_x: float,
        scale_y: float,
    ) -> tuple[list[dict], float]:
        self._ensure_started()
        timeout = max(0.1, float(settings.inference_pool_job_timeout_seconds))
        max_queue = max(1, int(settings.inference_pool_max_queue_size))
        job = _InferenceJob(
            camera_id=camera_id,
            infer_frame=infer_frame,
            offset_x=offset_x,
            offset_y=offset_y,
            scale_x=scale_x,
            scale_y=scale_y,
        )

        with self._condition:
            old = self._jobs_by_camera.get(job.camera_id)
            if old is not None:
                old.fail(TimeoutError("inference job substituido por frame mais recente da mesma camera"))
                self._record_replaced()
                self._log_event(
                    "replace",
                    status="dropped",
                    reason="newer_frame_same_camera",
                    camera_id=old.camera_id,
                    queue_wait_ms=round(old.queue_wait_ms(), 2),
                )
            elif len(self._jobs_by_camera) >= max_queue:
                if self._overflow_policy() == "drop_oldest" and self._drop_oldest_locked():
                    self._order.append(job.camera_id)
                else:
                    self._record_rejected()
                    self._log_event(
                        "reject",
                        status="rejected",
                        reason="queue_full",
                        camera_id=job.camera_id,
                        max_queue_size=max_queue,
                        overflow_policy=self._overflow_policy(),
                    )
                    raise TimeoutError(f"fila de inferencia cheia ({len(self._jobs_by_camera)}/{max_queue})")
            else:
                self._order.append(job.camera_id)
            self._jobs_by_camera[job.camera_id] = job
            with self._stats_lock:
                self._submitted += 1
            self._log_event(
                "enqueue",
                status="queued",
                reason="job_submitted",
                camera_id=job.camera_id,
                max_queue_size=max_queue,
                timeout_seconds=timeout,
                overflow_policy=self._overflow_policy(),
            )
            self._condition.notify()

        if not job.done.wait(timeout=timeout):
            self._record_timeout()
            with self._condition:
                pending = self._jobs_by_camera.get(job.camera_id)
                if pending is job:
                    self._jobs_by_camera.pop(job.camera_id, None)
                    self._order = [item for item in self._order if item != job.camera_id]
            self._log_event(
                "timeout",
                status="timeout",
                reason="wait_timeout",
                camera_id=job.camera_id,
                timeout_seconds=timeout,
                waited_ms=round(timeout * 1000.0, 2),
            )
            raise TimeoutError(f"inferencia expirou apos {timeout:.2f}s na pool")

        if job.error is not None:
            raise job.error
        return job.result or ([], 0.0)

    def _get_service(self, camera_id: int):
        camera_id = int(camera_id)
        with self._service_lock:
            service = self._services_by_camera.get(camera_id)
            if service is None:
                try:
                    if self._service_factory is not None:
                        service = self._service_factory()
                    else:
                        service = DetectionService(camera_id=camera_id, use_pool=False)
                except BaseException as exc:
                    if not self._is_detector_engine_failure(exc):
                        raise
                    _disable_corrupt_detector_engine()
                    service = DetectionService(camera_id=camera_id, use_pool=False, force_pytorch=True)
                self._services_by_camera[camera_id] = service
                service_count = len(self._services_by_camera)
            else:
                service_count = None
        if service_count is not None:
            self._log_event(
                "service_ready",
                status="ready",
                reason="per_camera_detector",
                camera_id=camera_id,
                service_count=service_count,
            )
        return service

    def release_camera(self, camera_id: int) -> dict[str, Any]:
        """Remove fila e cache de uma camera sem interromper inferencia em execucao."""
        camera_id = int(camera_id)
        pending = None
        active = False
        with self._condition:
            pending = self._jobs_by_camera.pop(camera_id, None)
            self._order = [item for item in self._order if item != camera_id]
            active = self._active_job_camera_id == camera_id
            if active:
                self._release_after_active.add(camera_id)
            else:
                self._release_after_active.discard(camera_id)
            self._condition.notify_all()

        if pending is not None:
            pending.fail(RuntimeError("camera removida da pool de inferencia"))

        service_removed = False
        if not active:
            with self._service_lock:
                service_removed = self._services_by_camera.pop(camera_id, None) is not None

        self._log_event(
            "release_camera",
            status="released" if not active else "release_pending",
            reason="camera_parked_or_stopped",
            camera_id=camera_id,
            pending_job_removed=pending is not None,
            active_job_pending=active,
            service_removed=service_removed,
        )
        return {
            "camera_id": camera_id,
            "pending_job_removed": pending is not None,
            "active_job_pending": active,
            "service_removed": service_removed,
        }

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._order and not self._stop_requested:
                    self._condition.wait()
                if self._stop_requested and not self._order:
                    return
                camera_id = self._order.pop(0)
                job = self._jobs_by_camera.pop(camera_id, None)
                if job is None:
                    continue
                self._active_job_camera_id = camera_id
                job.mark_started()
                self._active_job_started_at = job.started_at

            max_age = self._max_job_age_seconds()
            if max_age > 0 and (job.started_at - job.enqueued_at) > max_age:
                job.fail(TimeoutError(f"inference job vencido na fila apos {max_age:.2f}s"))
                self._record_stale_dropped()
                self._log_event(
                    "stale_drop",
                    status="dropped",
                    reason="max_job_age",
                    camera_id=job.camera_id,
                    max_age_seconds=max_age,
                    queue_wait_ms=round(job.queue_wait_ms(), 2),
                )
                with self._condition:
                    self._active_job_camera_id = None
                    self._active_job_started_at = None
                    self._release_after_active.discard(job.camera_id)
                continue

            try:
                service = self._get_service(job.camera_id)
                tracks, infer_ms = service._infer_direct(
                    job.infer_frame,
                    offset_x=job.offset_x,
                    offset_y=job.offset_y,
                    scale_x=job.scale_x,
                    scale_y=job.scale_y,
                )
                job.finish((tracks, infer_ms))
                self._record_completed(job, infer_ms)
                self._log_event(
                    "complete",
                    status="completed",
                    reason="inference_ok",
                    camera_id=job.camera_id,
                    tracks_count=len(tracks or []),
                    queue_wait_ms=round(job.queue_wait_ms(), 2),
                    total_latency_ms=round(job.total_latency_ms(), 2),
                    infer_ms=round(float(infer_ms), 2),
                )
            except BaseException as exc:
                job.fail(exc)
                self._record_failed(job)
                if self._is_detector_engine_failure(exc):
                    self._recover_detector_after_failure(camera_id=job.camera_id, error=exc)
                self._log_event(
                    "error",
                    status="error",
                    reason=type(exc).__name__,
                    camera_id=job.camera_id,
                    queue_wait_ms=round(job.queue_wait_ms(), 2),
                    total_latency_ms=round(job.total_latency_ms(), 2),
                    error=str(exc),
                    level="error",
                )
            finally:
                release_service = False
                with self._condition:
                    self._active_job_camera_id = None
                    self._active_job_started_at = None
                    if job.camera_id in self._release_after_active:
                        self._release_after_active.discard(job.camera_id)
                        release_service = True
                if release_service:
                    with self._service_lock:
                        self._services_by_camera.pop(job.camera_id, None)
                    self._log_event(
                        "release_camera_complete",
                        status="released",
                        reason="active_job_finished",
                        camera_id=job.camera_id,
                    )

    def stats(self) -> dict[str, Any]:
        import time

        with self._condition:
            queue_size = len(self._jobs_by_camera)
            active = self._active_job_camera_id
            release_pending_count = len(self._release_after_active)
            active_age_ms = None
            if self._active_job_started_at is not None:
                active_age_ms = round((time.perf_counter() - self._active_job_started_at) * 1000.0, 2)
        service_count = self._service_count()
        with self._stats_lock:
            return {
                "enabled": bool(settings.inference_pool_enabled),
                "mode": "pool" if bool(settings.inference_pool_enabled) else "direct",
                "backend": "local",
                "pool_id": self.pool_id,
                "queue_size": queue_size,
                "active_camera_id": active,
                "active_age_ms": active_age_ms,
                "submitted": self._submitted,
                "completed": self._completed,
                "failed": self._failed,
                "timed_out": self._timed_out,
                "replaced": self._replaced,
                "rejected": self._rejected,
                "dropped_oldest": self._dropped_oldest,
                "stale_dropped": self._stale_dropped,
                "last_wait_ms": self._last_queue_wait_ms,
                "last_queue_wait_ms": self._last_queue_wait_ms,
                "last_total_latency_ms": self._last_total_latency_ms,
                "last_infer_ms": self._last_infer_ms,
                "detector_recoveries": self._detector_recoveries,
                "consecutive_detector_failures": self._consecutive_detector_failures,
                "last_detector_recovery_reason": self._last_detector_recovery_reason,
                "max_queue_size": int(settings.inference_pool_max_queue_size),
                "job_timeout_seconds": float(settings.inference_pool_job_timeout_seconds),
                "max_job_age_seconds": self._max_job_age_seconds(),
                "overflow_policy": self._overflow_policy(),
                "service_count": service_count,
                "release_pending_count": release_pending_count,
            }

    def _service_count(self) -> int:
        with self._service_lock:
            return len(self._services_by_camera)

    def reset_services(self) -> None:
        """Drop per-camera predictors after a global detector failure."""
        with self._service_lock:
            self._services_by_camera.clear()


class InferencePoolGroup:
    def __init__(
        self,
        *,
        pool_count: int | None = None,
        max_cameras_per_pool: int | None = None,
        service_factory: Callable[[], Any] | None = None,
    ):
        self.pool_count = self._pool_count(pool_count)
        self.max_cameras_per_pool = self._max_cameras_per_pool(max_cameras_per_pool)
        self._pools = [
            InferencePool(service_factory=service_factory, pool_id=pool_id)
            for pool_id in range(self.pool_count)
        ]
        self._assignments: dict[int, int] = {}
        self._lock = Lock()

    @staticmethod
    def _pool_count(value: int | None = None) -> int:
        raw = settings.inference_pool_count if value is None else value
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _max_cameras_per_pool(value: int | None = None) -> int:
        raw = settings.inference_pool_max_cameras_per_pool if value is None else value
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 8

    def signature(self) -> tuple[int, int]:
        return self.pool_count, self.max_cameras_per_pool

    def stop(self) -> None:
        for pool in self._pools:
            pool.stop()

    def reset_services(self) -> None:
        for pool in self._pools:
            pool.reset_services()

    def release_camera(self, camera_id: int) -> dict[str, Any]:
        camera_id = int(camera_id)
        with self._lock:
            assigned_pool_id = self._assignments.pop(camera_id, None)

        if assigned_pool_id is not None and 0 <= assigned_pool_id < self.pool_count:
            pool_results = [self._pools[assigned_pool_id].release_camera(camera_id)]
        else:
            pool_results = [pool.release_camera(camera_id) for pool in self._pools]

        return {
            "camera_id": camera_id,
            "assignment_removed": assigned_pool_id is not None,
            "assigned_pool_id": assigned_pool_id,
            "pool_results": pool_results,
        }

    def _assignment_counts_locked(self) -> dict[int, int]:
        counts = {pool_id: 0 for pool_id in range(self.pool_count)}
        for pool_id in self._assignments.values():
            counts[pool_id] = counts.get(pool_id, 0) + 1
        return counts

    def _select_pool_id_locked(self, camera_id: int) -> int:
        camera_id = int(camera_id)
        existing = self._assignments.get(camera_id)
        if existing is not None and 0 <= existing < self.pool_count:
            return existing

        counts = self._assignment_counts_locked()
        eligible = [
            pool_id
            for pool_id in range(self.pool_count)
            if counts.get(pool_id, 0) < self.max_cameras_per_pool
        ]
        candidates = eligible or list(range(self.pool_count))
        selected = min(candidates, key=lambda pool_id: (counts.get(pool_id, 0), pool_id))
        self._assignments[camera_id] = selected
        pool_logger.info(
            "event=assign camera_id=%s pool_id=%s assigned_cameras=%s total_assigned_cameras=%s pool_count=%s max_cameras_per_pool=%s",
            camera_id,
            selected,
            counts.get(selected, 0) + 1,
            len(self._assignments),
            self.pool_count,
            self.max_cameras_per_pool,
            extra={
                "camera_id": camera_id,
                "action": "inference_pool_assign",
                "status": "assigned",
                "reason": "new_camera_assignment",
            },
        )
        return selected

    def _pool_runtime_stats(self, pool_id: int, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            counts = self._assignment_counts_locked()
            assigned = counts.get(pool_id, 0)
            total_assigned = len(self._assignments)
            assigned_camera_ids = sorted(
                camera_id
                for camera_id, assigned_pool_id in self._assignments.items()
                if assigned_pool_id == pool_id
            )
        payload = dict(runtime or self._pools[pool_id].stats())
        payload.update({
            "backend": "central",
            "pool_id": pool_id,
            "pool_count": self.pool_count,
            "assigned_cameras": assigned,
            "assigned_camera_ids": assigned_camera_ids,
            "total_assigned_cameras": total_assigned,
            "max_cameras_per_pool": self.max_cameras_per_pool,
        })
        return payload

    def infer(
        self,
        *,
        camera_id: int,
        infer_frame,
        offset_x: int,
        offset_y: int,
        scale_x: float,
        scale_y: float,
    ) -> tuple[list[dict], float, dict[str, Any]]:
        with self._lock:
            pool_id = self._select_pool_id_locked(camera_id)
        pool = self._pools[pool_id]
        tracks, infer_ms = pool.infer(
            camera_id=camera_id,
            infer_frame=infer_frame,
            offset_x=offset_x,
            offset_y=offset_y,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        return tracks, infer_ms, self._pool_runtime_stats(pool_id, pool.stats())

    def probe(self, infer_frame) -> tuple[list[dict], float, dict[str, Any]]:
        """Run a synthetic probe through the serialized pool without assigning a camera."""
        pool_id = 0
        pool = self._pools[pool_id]
        tracks, infer_ms = pool.infer(
            camera_id=0,
            infer_frame=infer_frame,
            offset_x=0,
            offset_y=0,
            scale_x=1.0,
            scale_y=1.0,
        )
        runtime = self._pool_runtime_stats(pool_id, pool.stats())
        runtime["probe"] = True
        return tracks, infer_ms, runtime

    def stats(self) -> dict[str, Any]:
        pool_stats = [self._pool_runtime_stats(pool.pool_id, pool.stats()) for pool in self._pools]
        return {
            "enabled": bool(settings.inference_pool_enabled),
            "mode": "pool" if bool(settings.inference_pool_enabled) else "direct",
            "backend": "central",
            "pool_count": self.pool_count,
            "max_cameras_per_pool": self.max_cameras_per_pool,
            "total_assigned_cameras": len(self._assignments),
            "queue_size": sum(int(item.get("queue_size") or 0) for item in pool_stats),
            "submitted": sum(int(item.get("submitted") or 0) for item in pool_stats),
            "completed": sum(int(item.get("completed") or 0) for item in pool_stats),
            "failed": sum(int(item.get("failed") or 0) for item in pool_stats),
            "timed_out": sum(int(item.get("timed_out") or 0) for item in pool_stats),
            "rejected": sum(int(item.get("rejected") or 0) for item in pool_stats),
            "replaced": sum(int(item.get("replaced") or 0) for item in pool_stats),
            "dropped_oldest": sum(int(item.get("dropped_oldest") or 0) for item in pool_stats),
            "stale_dropped": sum(int(item.get("stale_dropped") or 0) for item in pool_stats),
            "pools": pool_stats,
        }


_POOL: InferencePool | None = None
_POOL_LOCK = Lock()
_POOL_GROUP: InferencePoolGroup | None = None
_POOL_GROUP_LOCK = Lock()


def get_inference_pool() -> InferencePool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = InferencePool()
        return _POOL


def get_inference_pool_group() -> InferencePoolGroup:
    global _POOL_GROUP
    signature = (
        InferencePoolGroup._pool_count(),
        InferencePoolGroup._max_cameras_per_pool(),
    )
    with _POOL_GROUP_LOCK:
        if _POOL_GROUP is None or _POOL_GROUP.signature() != signature:
            old_group = _POOL_GROUP
            _POOL_GROUP = InferencePoolGroup(
                pool_count=signature[0],
                max_cameras_per_pool=signature[1],
            )
            if old_group is not None:
                old_group.stop()
        return _POOL_GROUP


def reset_inference_pool_services() -> None:
    """Descarta preditores existentes sem criar novos pools durante o reset."""
    with _POOL_GROUP_LOCK:
        group = _POOL_GROUP
    if group is not None:
        group.reset_services()
    with _POOL_LOCK:
        pool = _POOL
    if pool is not None:
        pool.reset_services()


def release_inference_camera(camera_id: int) -> dict[str, Any]:
    """Libera uma camera dos pools ja existentes sem criar pools novos."""
    camera_id = int(camera_id)
    results: dict[str, Any] = {"camera_id": camera_id, "group": None, "local": None}
    with _POOL_GROUP_LOCK:
        group = _POOL_GROUP
    if group is not None:
        results["group"] = group.release_camera(camera_id)
    with _POOL_LOCK:
        pool = _POOL
    if pool is not None:
        results["local"] = pool.release_camera(camera_id)
    return results
