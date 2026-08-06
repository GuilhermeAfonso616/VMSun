"""Status de banco e cleanup ordenado do ciclo de vida do worker de camera."""

from __future__ import annotations

from typing import Callable, Optional

from app.db.models import Camera


class WorkerRuntimeStatus:
    """Escreve `camera.status` e coordena o cleanup ao encerrar o worker.

    Nao guarda a sessao de banco como estado: cada metodo recebe `db`
    explicitamente, na mesma linha dos demais colaboradores do worker
    (ex.: `WorkerFrameProcessor.process(..., db=db)`).
    """

    def __init__(
        self,
        *,
        camera_id: int,
        logger,
        worker_pid: int,
        worker_generation: str,
        owns_runtime_state: Callable[[], bool],
        stop_capture_stage: Callable[[], None],
        release_capture_stage: Callable[[], None],
        stop_event_persistence: Callable[[], None],
        clear_published_state: Callable[[], None],
        camera_model=Camera,
    ):
        self.camera_id = int(camera_id)
        self.logger = logger
        self.worker_pid = int(worker_pid)
        self.worker_generation = worker_generation
        self.owns_runtime_state = owns_runtime_state
        self.stop_capture_stage = stop_capture_stage
        self.release_capture_stage = release_capture_stage
        self.stop_event_persistence = stop_event_persistence
        self.clear_published_state = clear_published_state
        self.camera_model = camera_model

    def fetch_camera(self, db) -> Optional[Camera]:
        return db.query(self.camera_model).filter(self.camera_model.id == self.camera_id).first()

    def mark_starting(self, db, camera: Optional[Camera]) -> None:
        if not camera:
            return
        camera.status = "starting"
        db.commit()
        self.logger.info(
            "Camera status updated to %s",
            camera.status,
            extra={
                "action": "camera_status_update",
                "status": camera.status,
                "reason": "worker_start",
                "worker_pid": self.worker_pid,
            },
        )

    def mark_warming_up(self, db, camera: Optional[Camera]) -> None:
        if not camera:
            return
        camera.status = "warming_up"
        db.commit()

    def mark_running_on_first_frame(self, db, camera: Optional[Camera]) -> Optional[Camera]:
        if camera and camera.status != "running":
            camera.status = "running"
            db.commit()
            self.logger.info(
                "Camera status updated to %s",
                camera.status,
                extra={
                    "action": "camera_status_update",
                    "status": camera.status,
                    "reason": "first_frame_captured",
                    "worker_pid": self.worker_pid,
                },
            )
        return camera

    def mark_fatal_error(self, db) -> None:
        camera = self.fetch_camera(db)
        owns_runtime_state = self.owns_runtime_state()
        if camera and owns_runtime_state:
            camera.status = "error: worker fatal"
            db.commit()
        elif camera:
            self.logger.warning(
                "Ignored fatal status from stale worker generation",
                extra={
                    "action": "stale_worker_fatal_ignored",
                    "status": "ignored",
                    "reason": "newer_worker_active",
                    "worker_pid": self.worker_pid,
                    "worker_generation": self.worker_generation,
                },
            )

    def cleanup(self, db) -> None:
        self.logger.info(
            "Worker cleanup started",
            extra={
                "action": "cleanup_worker",
                "status": "stopped",
                "reason": "shutdown",
                "worker_pid": self.worker_pid,
            },
        )
        self.stop_capture_stage()
        self.stop_event_persistence()
        self.release_capture_stage()

        owns_runtime_state = self.owns_runtime_state()

        if owns_runtime_state:
            self.clear_published_state()

        camera = self.fetch_camera(db)
        if camera and owns_runtime_state and not str(camera.status).startswith("error:"):
            camera.status = "stopped"
            db.commit()
            self.logger.info(
                "Camera final status updated to stopped",
                extra={
                    "action": "camera_status_update",
                    "status": "stopped",
                    "reason": "worker_shutdown",
                    "worker_pid": self.worker_pid,
                },
            )
        elif camera and not owns_runtime_state:
            self.logger.info(
                "Skipped stale worker cleanup status update camera_id=%s",
                self.camera_id,
                extra={
                    "action": "cleanup_worker",
                    "status": "stopped",
                    "reason": "newer_worker_active",
                    "worker_pid": self.worker_pid,
                },
            )
