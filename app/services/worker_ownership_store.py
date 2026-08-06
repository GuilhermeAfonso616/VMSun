"""Fencing persistente entre o processo controlador e workers filhos."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any

from app.core.config import settings


class WorkerOwnershipStore:
    def __init__(self, base_dir: str | Path | None = None):
        root = Path(base_dir) if base_dir is not None else Path(settings.runtime_state_dir) / "worker_ownership"
        self.base_dir = root
        self._lock = RLock()

    def _path(self, camera_id: int) -> Path:
        return self.base_dir / f"camera_{int(camera_id)}.json"

    def claim(self, camera_id: int, *, generation: str, pid: int | None) -> dict[str, Any]:
        payload = {
            "camera_id": int(camera_id),
            "generation": str(generation),
            "pid": int(pid) if pid is not None else None,
            "claimed_at": time.time(),
        }
        path = self._path(camera_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".tmp.{os.getpid()}")
            temp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            os.replace(temp, path)
        return payload

    def get(self, camera_id: int) -> dict[str, Any] | None:
        path = self._path(camera_id)
        with self._lock:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, ValueError, TypeError):
                return None
        return payload if isinstance(payload, dict) else None

    def is_owner(self, camera_id: int, *, generation: str, pid: int | None = None) -> bool:
        payload = self.get(camera_id)
        if payload is None or str(payload.get("generation") or "") != str(generation):
            return False
        claimed_pid = payload.get("pid")
        if pid is not None and claimed_pid is not None and int(claimed_pid) != int(pid):
            return False
        return True

    def release(
        self,
        camera_id: int,
        *,
        expected_generation: str,
        expected_pid: int | None = None,
    ) -> bool:
        path = self._path(camera_id)
        with self._lock:
            payload = self.get(camera_id)
            if payload is None:
                return False
            if str(payload.get("generation") or "") != str(expected_generation):
                return False
            claimed_pid = payload.get("pid")
            if expected_pid is not None and claimed_pid is not None and int(claimed_pid) != int(expected_pid):
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True


worker_ownership_store = WorkerOwnershipStore()
