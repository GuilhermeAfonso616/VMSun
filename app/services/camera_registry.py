"""Registro autoritativo dos processos de camera no runtime.

O registro mantem compatibilidade com a interface antiga, mas acrescenta uma
geracao por worker e exclusao mutua por camera. A geracao funciona como fencing
token: operacoes atrasadas de um worker antigo nao podem remover o atual.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, RLock
from typing import Any, Iterator


@dataclass(slots=True)
class WorkerRecord:
    camera_id: int
    worker: Any
    generation: str
    registered_at: float

    @property
    def pid(self) -> int | None:
        return getattr(self.worker, "pid", None)

    @property
    def alive(self) -> bool:
        is_alive = getattr(self.worker, "is_alive", None)
        if not callable(is_alive):
            return True
        try:
            return bool(is_alive())
        except Exception:
            return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "generation": self.generation,
            "pid": self.pid,
            "alive": self.alive,
            "exitcode": getattr(self.worker, "exitcode", None),
            "mode": getattr(self.worker, "mode_name", "normal"),
            "stop_requested": bool(getattr(self.worker, "is_stop_requested", False)),
            "started_at": getattr(self.worker, "started_at", None),
            "registered_at": self.registered_at,
        }


class CameraRegistry:
    def __init__(self):
        self._records: dict[int, WorkerRecord] = {}
        self._lock = RLock()
        self._camera_locks: dict[int, RLock] = {}
        self._camera_locks_guard = Lock()

    def _lock_for_camera(self, camera_id: int) -> RLock:
        camera_key = int(camera_id)
        with self._camera_locks_guard:
            lock = self._camera_locks.get(camera_key)
            if lock is None:
                lock = RLock()
                self._camera_locks[camera_key] = lock
            return lock

    @contextmanager
    def camera_guard(self, camera_id: int) -> Iterator[None]:
        """Serializa start/stop/restart para uma camera especifica."""

        with self._lock_for_camera(camera_id):
            yield

    @staticmethod
    def _worker_generation(worker: Any) -> str:
        generation = str(getattr(worker, "generation", "") or "").strip()
        if not generation:
            generation = uuid.uuid4().hex
            try:
                setattr(worker, "generation", generation)
            except Exception:
                pass
        return generation

    def set_worker(self, camera_id: int, worker) -> WorkerRecord:
        camera_key = int(camera_id)
        record = WorkerRecord(
            camera_id=camera_key,
            worker=worker,
            generation=self._worker_generation(worker),
            registered_at=time.time(),
        )
        with self._lock:
            self._records[camera_key] = record
        return record

    def get_record(self, camera_id: int) -> WorkerRecord | None:
        with self._lock:
            return self._records.get(int(camera_id))

    def get_worker(self, camera_id: int):
        record = self.get_record(camera_id)
        return record.worker if record is not None else None

    def is_current(
        self,
        camera_id: int,
        *,
        generation: str | None = None,
        pid: int | None = None,
    ) -> bool:
        record = self.get_record(camera_id)
        if record is None:
            return False
        if generation is not None and record.generation != str(generation):
            return False
        if pid is not None and record.pid != int(pid):
            return False
        return True

    def remove_worker(
        self,
        camera_id: int,
        *,
        expected_generation: str | None = None,
        expected_pid: int | None = None,
    ) -> bool:
        camera_key = int(camera_id)
        with self._lock:
            record = self._records.get(camera_key)
            if record is None:
                return False
            if expected_generation is not None and record.generation != str(expected_generation):
                return False
            if expected_pid is not None and record.pid != int(expected_pid):
                return False
            self._records.pop(camera_key, None)
            return True

    def list_records(self, *, include_dead: bool = True) -> list[WorkerRecord]:
        with self._lock:
            records = list(self._records.values())
        if include_dead:
            return records
        return [record for record in records if record.alive]

    def list_workers(self):
        return [(record.camera_id, record.worker) for record in self.list_records(include_dead=False)]

    def snapshot(self) -> list[dict[str, Any]]:
        return [record.as_dict() for record in self.list_records(include_dead=True)]

    def stop_all(self, timeout: float = 5.0):
        records = self.list_records(include_dead=True)
        for record in records:
            stop = getattr(record.worker, "stop", None)
            if callable(stop):
                try:
                    stop(timeout=timeout)
                except TypeError:
                    stop()
                except Exception:
                    pass
            self.remove_worker(record.camera_id, expected_generation=record.generation)


registry = CameraRegistry()
