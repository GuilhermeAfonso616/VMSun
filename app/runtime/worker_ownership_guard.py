"""Verificacao de posse da geracao publicada de um worker de camera."""

from __future__ import annotations

from app.services.metrics_store import metrics_store as default_metrics_store
from app.services.worker_ownership_store import worker_ownership_store as default_ownership_store


class WorkerOwnershipGuard:
    """Confirma que este processo ainda e a geracao publicada da camera."""

    def __init__(
        self,
        *,
        camera_id: int,
        process_pid: int,
        worker_generation: str,
        ownership_store=default_ownership_store,
        metrics_store_backend=default_metrics_store,
    ):
        self.camera_id = int(camera_id)
        self.process_pid = int(process_pid)
        self.worker_generation = worker_generation
        self.ownership_store = ownership_store
        self.metrics_store_backend = metrics_store_backend

    def is_owner(self) -> bool:
        try:
            ownership = self.ownership_store.get(self.camera_id)
            if ownership is not None:
                return self.ownership_store.is_owner(
                    self.camera_id,
                    generation=self.worker_generation,
                    pid=self.process_pid,
                )
            latest_metrics = self.metrics_store_backend.get_metrics(self.camera_id) or {}
            latest_generation = str(latest_metrics.get("worker_generation") or "").strip()
            if latest_generation and latest_generation != self.worker_generation:
                return False
            latest_worker_pid = latest_metrics.get("worker_pid")
            if latest_worker_pid is not None and int(latest_worker_pid) != int(self.process_pid):
                return False
        except Exception:
            return True
        return True
