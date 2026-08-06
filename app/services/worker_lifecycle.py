"""Operacoes atomicas para o ciclo de vida dos workers de camera."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.core.logging import get_logger
from app.services.camera_registry import CameraRegistry, WorkerRecord, registry
from app.services.worker_ownership_store import WorkerOwnershipStore, worker_ownership_store
from app.services.worker_process import CameraProcessController

class _DummyGuard:
    allowed = True
    def as_dict(self): return {"allowed": True}

def evaluate_worker_start_guard(*args, **kwargs):
    return _DummyGuard()

def _release_inference_camera(camera_id: int) -> None:
    pass


class WorkerLifecycleError(RuntimeError):
    pass


class WorkerStartBlocked(WorkerLifecycleError):
    def __init__(self, detail: dict[str, Any]):
        super().__init__(str(detail.get("reason") or "worker_start_blocked"))
        self.detail = detail


@dataclass(slots=True)
class LifecycleResult:
    action: str
    camera_id: int
    record: WorkerRecord | None
    replaced_generation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "camera_id": self.camera_id,
            "replaced_generation": self.replaced_generation,
        }
        if self.record is not None:
            payload["worker"] = self.record.as_dict()
        else:
            payload["worker"] = None
        return payload


class WorkerLifecycleManager:
    """Unico escritor autorizado do registry em tempo de execucao."""

    def __init__(
        self,
        *,
        worker_registry: CameraRegistry = registry,
        controller_factory: Callable[..., Any] = CameraProcessController,
        ownership_store: WorkerOwnershipStore = worker_ownership_store,
    ):
        self.registry = worker_registry
        self.controller_factory = controller_factory
        self.ownership_store = ownership_store
        self.logger = get_logger("app.worker_lifecycle")

    def start(
        self,
        camera,
        *,
        restart_existing: bool = True,
        stop_timeout: float = 5.0,
        reason: str = "requested_start",
    ) -> LifecycleResult:
        camera_id = int(camera.id)
        rtsp_url = str(getattr(camera, "rtsp_url", "") or "")
        if not rtsp_url:
            raise WorkerLifecycleError("camera_without_rtsp")

        with self.registry.camera_guard(camera_id):
            current = self.registry.get_record(camera_id)
            if current is not None and current.alive and not restart_existing:
                return LifecycleResult("already_running", camera_id, current)

            guard = evaluate_worker_start_guard(
                active_workers=len(self.registry.list_workers()),
                starting_existing_worker=current is not None,
            )
            if not guard.allowed:
                raise WorkerStartBlocked(guard.as_dict())

            replaced_generation = current.generation if current is not None else None
            if current is not None:
                self._stop_record(current, timeout=stop_timeout, reason=reason)
                if current.alive:
                    raise WorkerLifecycleError(
                        f"previous_worker_still_alive camera_id={camera_id} "
                        f"generation={current.generation} pid={current.pid}"
                    )
                self.registry.remove_worker(
                    camera_id,
                    expected_generation=current.generation,
                )
                _release_inference_camera(camera_id)
                self.ownership_store.release(
                    camera_id,
                    expected_generation=current.generation,
                    expected_pid=current.pid,
                )

            controller = self.controller_factory(
                camera_id=camera_id,
                rtsp_url=rtsp_url,
                use_motion_test=True,
            )
            generation = str(getattr(controller, "generation", "") or "")
            if not generation:
                raise WorkerLifecycleError("worker_generation_missing")
            self.ownership_store.claim(camera_id, generation=generation, pid=None)
            try:
                controller.start()
                self.ownership_store.claim(camera_id, generation=generation, pid=getattr(controller, "pid", None))
            except Exception as exc:
                try:
                    controller.stop(timeout=1.0)
                except Exception:
                    pass
                _release_inference_camera(camera_id)
                self.ownership_store.release(camera_id, expected_generation=generation)
                self.logger.exception(
                    "Worker generation start failed camera_id=%s reason=%s",
                    camera_id,
                    reason,
                    extra={
                        "camera_id": camera_id,
                        "worker_generation": getattr(controller, "generation", "-"),
                        "action": "worker_generation_start_failed",
                        "status": "error",
                        "reason": reason,
                    },
                )
                raise WorkerLifecycleError(f"worker_start_failed: {exc}") from exc
            record = self.registry.set_worker(camera_id, controller)
            self.logger.info(
                "Worker generation activated camera_id=%s generation=%s pid=%s reason=%s",
                camera_id,
                record.generation,
                record.pid,
                reason,
                extra={
                    "camera_id": camera_id,
                    "worker_pid": record.pid or "-",
                    "worker_generation": record.generation,
                    "action": "worker_generation_activated",
                    "status": "running",
                    "reason": reason,
                },
            )
            return LifecycleResult("started", camera_id, record, replaced_generation)

    def stop(
        self,
        camera_id: int,
        *,
        timeout: float = 5.0,
        reason: str = "requested_stop",
    ) -> LifecycleResult:
        camera_id = int(camera_id)
        with self.registry.camera_guard(camera_id):
            current = self.registry.get_record(camera_id)
            if current is None:
                _release_inference_camera(camera_id)
                return LifecycleResult("already_stopped", camera_id, None)
            self._stop_record(current, timeout=timeout, reason=reason)
            if current.alive:
                raise WorkerLifecycleError(
                    f"worker_stop_failed camera_id={camera_id} "
                    f"generation={current.generation} pid={current.pid}"
                )
            self.registry.remove_worker(camera_id, expected_generation=current.generation)
            _release_inference_camera(camera_id)
            self.ownership_store.release(
                camera_id,
                expected_generation=current.generation,
                expected_pid=current.pid,
            )
            return LifecycleResult("stopped", camera_id, None, current.generation)

    def reconcile(self, camera, *, recover: bool, stop_timeout: float = 5.0) -> LifecycleResult:
        camera_id = int(camera.id)
        desired_running = self.desired_running(camera)
        current = self.registry.get_record(camera_id)

        if not desired_running:
            if current is None:
                _release_inference_camera(camera_id)
                return LifecycleResult("desired_stopped", camera_id, None)
            if not recover:
                return LifecycleResult("would_stop", camera_id, current)
            return self.stop(camera_id, timeout=stop_timeout, reason="supervisor_reconcile")

        if current is not None and current.alive:
            return LifecycleResult("healthy", camera_id, current)
        if not recover:
            return LifecycleResult("would_start", camera_id, current)
        return self.start(
            camera,
            restart_existing=True,
            stop_timeout=stop_timeout,
            reason="supervisor_reconcile",
        )

    @staticmethod
    def desired_running(camera) -> bool:
        status = str(getattr(camera, "status", "") or "").strip().lower()
        if bool(getattr(camera, "is_deleted", False)):
            return False
        if status in {"disabled", "inactive", "deleted", "archived", "stopped_manual"}:
            return False
        return bool(getattr(camera, "auto_start_enabled", False)) or status.startswith(("running", "error:")) or status in {
            "starting",
            "warming_up",
            "reconnecting",
            "degraded",
            "offline",
        }

    def _stop_record(self, record: WorkerRecord, *, timeout: float, reason: str) -> None:
        try:
            record.worker.stop(timeout=timeout)
        except TypeError:
            record.worker.stop()
        except Exception as exc:
            self.logger.exception(
                "Worker stop raised camera_id=%s generation=%s",
                record.camera_id,
                record.generation,
                extra={
                    "camera_id": record.camera_id,
                    "worker_pid": record.pid or "-",
                    "worker_generation": record.generation,
                    "action": "worker_generation_stop_failed",
                    "status": "error",
                    "reason": reason,
                },
            )
            raise WorkerLifecycleError(str(exc)) from exc


worker_lifecycle_manager = WorkerLifecycleManager()
