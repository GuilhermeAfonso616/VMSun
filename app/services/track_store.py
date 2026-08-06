from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from app.core.config import settings


class TrackStore:
    def __init__(self):
        self._base_dir = Path(settings.runtime_state_dir) / "tracks"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._memory_cache: dict[int, tuple[float, int, dict]] = {}
        self._last_write_ms: dict[int, float] = {}

    def _track_path(self, camera_id: int) -> Path:
        return self._base_dir / f"camera_{int(camera_id)}.json"

    def _temp_path(self, camera_id: int) -> Path:
        return self._base_dir / f"camera_{int(camera_id)}.tmp.json"

    def _serialize_tracks(self, tracks: list[dict] | None, limit: int = 30) -> list[dict]:
        items: list[dict] = []
        for track in (tracks or [])[:limit]:
            if not isinstance(track, dict):
                continue
            bbox = track.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            try:
                item = {
                    "bbox": [round(float(value), 2) for value in bbox],
                    "track_id": int(track.get("track_id")) if track.get("track_id") is not None else None,
                    "confidence": round(float(track.get("confidence")), 4) if track.get("confidence") is not None else None,
                }
                if track.get("label"):
                    item["label"] = str(track.get("label"))
                if track.get("visual_status"):
                    item["visual_status"] = str(track.get("visual_status"))
                if track.get("visual_person_score") is not None:
                    item["visual_person_score"] = round(float(track.get("visual_person_score")), 4)
                if track.get("notification_decision"):
                    item["notification_decision"] = str(track.get("notification_decision"))
                if track.get("strategy3_v2_decision"):
                    item["strategy3_v2_decision"] = str(track.get("strategy3_v2_decision"))
            except Exception:
                continue
            items.append(item)
        return items

    def set_tracks(
        self,
        camera_id: int,
        tracks: list[dict] | None,
        *,
        frame_width: int | None = None,
        frame_height: int | None = None,
        frame_context: dict | None = None,
        latency_diagnostics: dict | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        camera_key = int(camera_id)
        context = dict(frame_context or {})
        tracks_published_at_ns = int(
            context.get("tracks_published_at_ns") or time.time_ns()
        )
        payload = {
            "camera_id": camera_key,
            "updated_at": now.isoformat(),
            "updated_monotonic": time.monotonic(),
            "tracks_published_at_ns": tracks_published_at_ns,
            "frame_id": context.get("frame_id"),
            "generation_id": context.get("generation_id"),
            "gateway_received_at_ns": context.get("gateway_received_at_ns"),
            "source_frame_captured_at_ns": context.get(
                "source_frame_captured_at_ns"
            ),
            "source_pts": context.get("source_pts"),
            "capture_clock": context.get("capture_clock") or "unknown",
            "track_store_write_ms": self._last_write_ms.get(camera_key),
            "source_frame_width": int(frame_width or 0),
            "source_frame_height": int(frame_height or 0),
            "tracks": self._serialize_tracks(tracks),
        }
        if latency_diagnostics:
            payload["latency_diagnostics"] = latency_diagnostics

        temp_path = self._temp_path(camera_id)
        final_path = self._track_path(camera_id)
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        write_started = time.perf_counter()
        with self._lock:
            temp_path.write_text(serialized, encoding="utf-8")
            os.replace(temp_path, final_path)
            write_ms = (time.perf_counter() - write_started) * 1000.0
            self._last_write_ms[camera_key] = write_ms
            payload["track_store_current_write_ms"] = round(write_ms, 3)
            try:
                file_version = final_path.stat().st_mtime_ns
            except OSError:
                file_version = 0
            self._memory_cache[camera_key] = (
                time.monotonic(),
                file_version,
                payload,
            )
        return payload

    def update_latency_diagnostics(
        self,
        camera_id: int,
        latency_diagnostics: dict,
        *,
        expected_frame_id=None,
        expected_generation_id=None,
    ) -> bool:
        """Atualiza somente o diagnostico do frame ja publicado.

        O segundo replace atomico ocorre apenas no canario de diagnostico. O
        ``updated_at`` e os tracks permanecem intactos, portanto esta escrita
        nao prolonga a validade visual nem republica uma box antiga.
        """

        camera_key = int(camera_id)
        final_path = self._track_path(camera_key)
        temp_path = self._temp_path(camera_key)
        with self._lock:
            try:
                payload = json.loads(final_path.read_text(encoding="utf-8"))
            except Exception:
                return False

            if (
                expected_frame_id is not None
                and payload.get("frame_id") != expected_frame_id
            ):
                return False
            if (
                expected_generation_id is not None
                and payload.get("generation_id") != expected_generation_id
            ):
                return False

            payload["latency_diagnostics"] = dict(latency_diagnostics or {})
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(temp_path, final_path)
            try:
                file_version = final_path.stat().st_mtime_ns
            except OSError:
                file_version = 0
            self._memory_cache[camera_key] = (
                time.monotonic(),
                file_version,
                payload,
            )
        return True

    def get_tracks(self, camera_id: int, *, max_age_seconds: float = 2.0) -> dict | None:
        read_started = time.perf_counter()
        camera_key = int(camera_id)
        cache_ttl = max(0.0, float(getattr(settings, "track_store_memory_cache_ttl_seconds", 0.0) or 0.0))
        payload = None
        path = self._track_path(camera_id)
        if cache_ttl > 0:
            try:
                current_file_version = path.stat().st_mtime_ns
            except OSError:
                current_file_version = 0
            with self._lock:
                cached = self._memory_cache.get(camera_key)
                if (
                    cached
                    and time.monotonic() - cached[0] <= cache_ttl
                    and cached[1] == current_file_version
                ):
                    payload = dict(cached[2])
                    payload["tracks"] = list((cached[2] or {}).get("tracks") or [])
                    payload["track_store_cache_hit"] = True

        if payload is None:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["track_store_cache_hit"] = False
                if cache_ttl > 0:
                    try:
                        file_version = path.stat().st_mtime_ns
                    except OSError:
                        file_version = 0
                    with self._lock:
                        self._memory_cache[camera_key] = (
                            time.monotonic(),
                            file_version,
                            payload,
                        )
            except Exception:
                return None

        age_seconds = None
        try:
            updated_at = datetime.fromisoformat(str(payload.get("updated_at")))
            age_seconds = max(0.0, (datetime.now(timezone.utc).replace(tzinfo=None) - updated_at).total_seconds())
        except Exception:
            age_seconds = None

        stale = age_seconds is None or age_seconds > float(max_age_seconds)
        payload["age_seconds"] = age_seconds
        payload["stale"] = stale
        payload["track_store_read_ms"] = round(
            (time.perf_counter() - read_started) * 1000.0,
            3,
        )
        payload["track_store_file_age_ms"] = (
            round(age_seconds * 1000.0, 3) if age_seconds is not None else None
        )
        if stale:
            payload["tracks"] = []
        return payload


track_store = TrackStore()
