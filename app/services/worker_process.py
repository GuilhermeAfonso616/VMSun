"""Gerencia o processo separado de cada camera.

O controller encapsula start/stop do worker e isola a logica de spawn do resto
da aplicacao, permitindo reinicios seguros pela camada web/health monitor.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _worker_entry(camera_id: int, rtsp_url: str, use_motion_test: bool, stop_event, generation: str):
    try:
        use_motion_test = True
        os.environ["SERVER_ANALITICO_WORKER_GENERATION"] = generation
        # Definido antes de qualquer import da app: garante que o primeiro setup_logging
        # deste processo ja abra arquivos proprios, sem disputar o rollover com o pai.
        os.environ["SERVER_ANALITICO_LOG_SUFFIX"] = f"worker-{camera_id}"
        # Cada processo trabalha com seu proprio cwd/runtime para evitar dependencia do processo pai.
        if getattr(sys, "frozen", False):
            os.chdir(os.path.dirname(sys.executable))
            logs_dir = Path(os.getcwd()) / "logs"
        else:
            from app.core.config import settings

            base_dir = Path(settings.app_base_dir).resolve()
            base_dir.mkdir(parents=True, exist_ok=True)
            os.chdir(str(base_dir))
            settings.ensure_runtime_dirs()
            logs_dir = Path(settings.logs_dir)

        from app.core.logging import get_logger, setup_logging
        from app.workers.video_worker_motion_test import VideoWorkerMotionTest

        setup_logging()
        logger = get_logger("app.worker_process")
        logger.info(
            "Worker process entry started camera_id=%s mode=%s pid=%s",
            camera_id,
            "motion_test",
            os.getpid(),
            extra={
                "camera_id": camera_id,
                "worker_mode": "motion_test",
                "worker_pid": os.getpid(),
                "worker_generation": generation,
                "action": "worker_process_entry",
                "status": "starting",
                "reason": "process_started",
            },
        )

        worker_cls = VideoWorkerMotionTest

        try:
            worker = worker_cls(camera_id=camera_id, rtsp_url=rtsp_url, stop_event=stop_event)
        except TypeError as e:
            if "stop_event" not in str(e):
                raise
            worker = worker_cls(camera_id=camera_id, rtsp_url=rtsp_url)

        worker.run()
    except BaseException:
        tb = traceback.format_exc()
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            fatal_path = logs_dir / f"worker_{camera_id}_fatal.log"
            fatal_path.write_text(tb, encoding="utf-8")
        except Exception:
            pass

        try:
            from app.core.logging import get_logger, setup_logging

            setup_logging()
            logger = get_logger("app.worker_process")
            logger.error(
                "Worker process fatal failure camera_id=%s pid=%s\n%s",
                camera_id,
                os.getpid(),
                tb,
                extra={
                    "camera_id": camera_id,
                    "worker_mode": "motion_test",
                    "worker_pid": os.getpid(),
                    "worker_generation": generation,
                    "action": "worker_process_entry",
                    "status": "error",
                    "reason": "fatal_exception",
                },
            )
        except Exception:
            print(tb, file=sys.stderr, flush=True)
        raise


@dataclass
class CameraProcessController:
    # Interface minima para iniciar/parar o worker sem expor detalhes do multiprocessing.
    camera_id: int
    rtsp_url: str
    use_motion_test: bool = True
    generation: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self):
        self.use_motion_test = True
        self.mode_name = "motion_test"
        self._ctx = mp.get_context("spawn")
        self._stop_event: Optional[mp.Event] = None
        self._process: Optional[mp.Process] = None
        self.started_at: Optional[float] = None
        self.stop_requested_at: Optional[float] = None

    def start(self, delay_seconds: float = 0.0):
        if self.is_alive():
            return

        delay_seconds = max(0.0, float(delay_seconds))
        if delay_seconds > 0:
            time.sleep(delay_seconds)

        self._stop_event = self._ctx.Event()
        previous_worker_child = os.environ.get("SERVER_ANALITICO_WORKER_CHILD")
        os.environ["SERVER_ANALITICO_WORKER_CHILD"] = "1"
        self._process = self._ctx.Process(
            target=_worker_entry,
            args=(self.camera_id, self.rtsp_url, self.use_motion_test, self._stop_event, self.generation),
            daemon=True,
            name=f"camera-worker-{self.camera_id}",
        )
        try:
            self._process.start()
        finally:
            if previous_worker_child is None:
                os.environ.pop("SERVER_ANALITICO_WORKER_CHILD", None)
            else:
                os.environ["SERVER_ANALITICO_WORKER_CHILD"] = previous_worker_child
        self.started_at = time.time()
        self.stop_requested_at = None

    def stop(self, timeout: float = 5.0):
        self.stop_requested_at = time.time()

        if self._stop_event is not None:
            try:
                self._stop_event.set()
            except Exception:
                pass

        if self._process is not None and self._process.is_alive():
            self._process.join(timeout=timeout)

            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=2.0)

            if self._process.is_alive():
                kill = getattr(self._process, "kill", None)
                if callable(kill):
                    kill()
                    self._process.join(timeout=2.0)

        return not self.is_alive()

    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    @property
    def is_stop_requested(self) -> bool:
        if self.stop_requested_at is not None:
            return True

        if self._stop_event is None:
            return False

        try:
            return bool(self._stop_event.is_set())
        except Exception:
            return False

    @property
    def pid(self):
        return self._process.pid if self._process is not None else None

    @property
    def exitcode(self):
        return self._process.exitcode if self._process is not None else None

    def snapshot(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "generation": self.generation,
            "pid": self.pid,
            "alive": self.is_alive(),
            "exitcode": self.exitcode,
            "mode": self.mode_name,
            "started_at": self.started_at,
            "stop_requested_at": self.stop_requested_at,
        }
