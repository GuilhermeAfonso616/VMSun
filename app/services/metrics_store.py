from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

from app.core.config import settings
from app.core.timezone import utc_now_naive


class MetricsStore:
    def __init__(self):
        self._lock = Lock()
        self._base_dir = Path(settings.runtime_state_dir) / "metrics"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: dict[int, tuple[float, dict]] = {}

    def _legacy_metrics_path(self, camera_id: int) -> Path:
        return self._base_dir / f"camera_{camera_id}.json"

    def _metrics_final_path(self, camera_id: int, stamp_ns: int) -> Path:
        return self._base_dir / f"camera_{camera_id}_{stamp_ns}.json"

    def _metrics_temp_path(self, camera_id: int, stamp_ns: int) -> Path:
        return self._base_dir / f"camera_{camera_id}_{stamp_ns}.tmp.json"

    def _list_candidate_files(self, camera_id: int) -> list[Path]:
        candidates: list[Path] = []

        try:
            for path in self._base_dir.glob(f"camera_{camera_id}_*.json"):
                if path.name.endswith(".tmp.json"):
                    continue
                candidates.append(path)
        except Exception:
            pass

        legacy_path = self._legacy_metrics_path(camera_id)
        if legacy_path.exists():
            candidates.append(legacy_path)

        def _safe_mtime_ns(path: Path) -> int:
            try:
                return path.stat().st_mtime_ns if path.exists() else 0
            except FileNotFoundError:
                return 0
            except Exception:
                return 0

        candidates.sort(key=_safe_mtime_ns, reverse=True)
        return candidates

    def _cleanup_old_versions(self, camera_id: int, keep: int = 3) -> None:
        try:
            files = []
            for path in self._base_dir.glob(f"camera_{camera_id}_*.json"):
                if path.name.endswith(".tmp.json"):
                    continue
                files.append(path)

            def _safe_mtime_ns(path: Path) -> int:
                try:
                    return path.stat().st_mtime_ns if path.exists() else 0
                except FileNotFoundError:
                    return 0
                except Exception:
                    return 0

            files.sort(key=_safe_mtime_ns, reverse=True)

            for old_path in files[keep:]:
                try:
                    old_path.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def set_metrics(self, camera_id: int, data: dict):
        payload = {
            **data,
            "updated_at": utc_now_naive().isoformat(),
        }

        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        stamp_ns = time.time_ns()

        final_path = self._metrics_final_path(camera_id, stamp_ns)
        temp_path = self._metrics_temp_path(camera_id, stamp_ns)

        with self._lock:
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(serialized)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(temp_path, final_path)
                self._memory_cache[int(camera_id)] = (time.monotonic(), payload)
                self._cleanup_old_versions(camera_id, keep=3)
            finally:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except Exception:
                    pass
        return payload

    def get_metrics(self, camera_id: int):
        cache_ttl = max(0.0, float(getattr(settings, "metrics_store_memory_cache_ttl_seconds", 0.0) or 0.0))
        camera_key = int(camera_id)
        if cache_ttl > 0:
            with self._lock:
                cached = self._memory_cache.get(camera_key)
                if cached and time.monotonic() - cached[0] <= cache_ttl:
                    return dict(cached[1])

        candidates = self._list_candidate_files(camera_id)

        for path in candidates[:5]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if cache_ttl > 0:
                    with self._lock:
                        self._memory_cache[camera_key] = (time.monotonic(), payload)
                return payload
            except Exception:
                continue

        return None

    def remove_metrics(self, camera_id: int):
        with self._lock:
            try:
                legacy_path = self._legacy_metrics_path(camera_id)
                if legacy_path.exists():
                    legacy_path.unlink()
            except Exception:
                pass

            try:
                for path in self._base_dir.glob(f"camera_{camera_id}_*.json"):
                    try:
                        path.unlink()
                    except Exception:
                        pass
            except Exception:
                pass

            self._memory_cache.pop(int(camera_id), None)


metrics_store = MetricsStore()
