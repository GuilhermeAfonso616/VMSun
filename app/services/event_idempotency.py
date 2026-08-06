"""Controla deduplicacao de eventos por camera e janela temporal.

Esse store evita que o mesmo track gere varios registros identicos em janela
curta quando o tracker oscila ou o worker reinicia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha1
import json
from pathlib import Path
import threading

from app.core.config import settings
from app.core.timezone import utc_now_naive


@dataclass(slots=True)
class EventDedupDecision:
    allowed: bool
    dedupe_key: str
    reason: str
    status: str


class EventIdempotencyStore:
    def __init__(self, window_seconds: float | None = None):
        self.window_seconds = float(window_seconds or settings.event_dedupe_window_seconds)
        self._retention_seconds = max(self.window_seconds * 10.0, 60.0)
        self._base_dir = Path(settings.runtime_state_dir) / "event_dedupe"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._recent_keys: dict[int, dict[str, datetime]] = {}
        self._recent_duplicate_hits: dict[int, list[datetime]] = {}

    def evaluate(
        self,
        camera_id: int,
        event: dict,
        rules,
        pending_keys: set[str] | None = None,
        now: datetime | None = None,
    ) -> EventDedupDecision:
        current_time = now or utc_now_naive()
        # A chave mistura camera, evento, track, bbox e geometria para manter estabilidade.
        dedupe_key = self.build_dedupe_key(camera_id, event, rules)

        if pending_keys is not None and dedupe_key in pending_keys:
            return EventDedupDecision(
                allowed=False,
                dedupe_key=dedupe_key,
                reason="duplicate_in_batch",
                status="deduplicated",
            )

        with self._lock:
            state = self._load_state(camera_id)
            self._prune_state(state, current_time)

            last_seen = state.get(dedupe_key)
            if last_seen is not None:
                age = (current_time - last_seen).total_seconds()
                if age <= self.window_seconds:
                    self._record_duplicate_hit(camera_id, current_time)
                    self._persist_state(camera_id, state)
                    return EventDedupDecision(
                        allowed=False,
                        dedupe_key=dedupe_key,
                        reason="recent_duplicate",
                        status="deduplicated",
                    )

            return EventDedupDecision(
                allowed=True,
                dedupe_key=dedupe_key,
                reason="fresh_event",
                status="new",
            )

    def mark_persisted(self, camera_id: int, dedupe_key: str, now: datetime | None = None) -> None:
        current_time = now or utc_now_naive()
        with self._lock:
            state = self._load_state(camera_id)
            self._prune_state(state, current_time)
            state[dedupe_key] = current_time
            self._persist_state(camera_id, state)

    def mark_persisted_many(self, camera_id: int, dedupe_keys: set[str], now: datetime | None = None) -> None:
        if not dedupe_keys:
            return

        current_time = now or utc_now_naive()
        with self._lock:
            state = self._load_state(camera_id)
            self._prune_state(state, current_time)
            for dedupe_key in dedupe_keys:
                state[dedupe_key] = current_time
            self._persist_state(camera_id, state)

    def get_recent_summary(self, camera_id: int, now: datetime | None = None) -> dict:
        current_time = now or utc_now_naive()
        with self._lock:
            self._load_state(camera_id, force_reload=True)
            hits = self._recent_duplicate_hits.get(camera_id, [])
            pruned_hits = [hit for hit in hits if (current_time - hit).total_seconds() <= self.window_seconds]
            self._recent_duplicate_hits[camera_id] = pruned_hits
            return {
                "dedupe_recent_count": len(pruned_hits),
                "dedupe_recent_at": pruned_hits[-1].isoformat(timespec="microseconds") if pruned_hits else None,
                "dedupe_recent_age_seconds": (current_time - pruned_hits[-1]).total_seconds() if pruned_hits else None,
                "dedupe_window_seconds": self.window_seconds,
            }

    def build_dedupe_key(self, camera_id: int, event: dict, rules) -> str:
        event_type = str(event.get("event_type") or "unknown_event")
        track_token = self._track_token(event.get("track_id"))
        bbox_token = self._bbox_token(event.get("bbox"))
        geometry_token = self._geometry_token(rules)

        raw = "|".join(
            [
                f"camera:{int(camera_id)}",
                f"event:{event_type}",
                track_token,
                bbox_token,
                geometry_token,
            ]
        )
        geometry_hash = sha1(geometry_token.encode("utf-8")).hexdigest()[:10]
        return "|".join(
            [
                f"camera:{int(camera_id)}",
                event_type,
                track_token,
                bbox_token,
                f"geo:{geometry_hash}",
                f"sig:{sha1(raw.encode('utf-8')).hexdigest()[:12]}",
            ]
        )

    def _camera_path(self, camera_id: int) -> Path:
        return self._base_dir / f"camera_{int(camera_id)}.json"

    def _load_state(self, camera_id: int, force_reload: bool = False) -> dict[str, datetime]:
        if camera_id in self._recent_keys and not force_reload:
            return self._recent_keys[camera_id]

        state: dict[str, datetime] = {}
        duplicate_hits: list[datetime] = []
        path = self._camera_path(camera_id)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                entries = payload.get("entries", []) if isinstance(payload, dict) else []
                for entry in entries:
                    key = entry.get("key")
                    seen_at = entry.get("seen_at")
                    if not key or not seen_at:
                        continue
                    state[str(key)] = datetime.fromisoformat(str(seen_at))
                duplicate_entries = payload.get("duplicate_hits", []) if isinstance(payload, dict) else []
                for entry in duplicate_entries:
                    if not entry:
                        continue
                    duplicate_hits.append(datetime.fromisoformat(str(entry)))
            except Exception:
                state = {}
                duplicate_hits = []

        self._recent_keys[camera_id] = state
        self._recent_duplicate_hits[camera_id] = duplicate_hits
        return state

    def _persist_state(self, camera_id: int, state: dict[str, datetime]) -> None:
        path = self._camera_path(camera_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"key": key, "seen_at": seen_at.isoformat(timespec="microseconds")}
            for key, seen_at in sorted(state.items(), key=lambda item: item[1])
        ]
        payload = {
            "camera_id": camera_id,
            "window_seconds": self.window_seconds,
            "updated_at": utc_now_naive().isoformat(timespec="microseconds"),
            "entries": entries,
            "duplicate_hits": [
                hit.isoformat(timespec="microseconds")
                for hit in self._recent_duplicate_hits.get(camera_id, [])
            ],
        }
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)

    def _prune_state(self, state: dict[str, datetime], now: datetime) -> None:
        retention = timedelta(seconds=self._retention_seconds)
        for key, seen_at in list(state.items()):
            if now - seen_at > retention:
                state.pop(key, None)

    def _record_duplicate_hit(self, camera_id: int, now: datetime) -> None:
        hits = self._recent_duplicate_hits.setdefault(camera_id, [])
        hits.append(now)
        cutoff = now - timedelta(seconds=self.window_seconds)
        self._recent_duplicate_hits[camera_id] = [hit for hit in hits if hit >= cutoff]

    @staticmethod
    def _track_token(track_id) -> str:
        if track_id is None:
            return "track:na"
        try:
            return f"track:{int(track_id)}"
        except Exception:
            return f"track:{track_id}"

    @staticmethod
    def _bbox_token(bbox) -> str:
        if not bbox or len(bbox) != 4:
            return "bbox:na"

        grid = 16.0
        try:
            normalized = [str(int(round(float(value) / grid))) for value in bbox]
        except Exception:
            return "bbox:na"
        return "bbox:" + ":".join(normalized)

    @staticmethod
    def _geometry_token(rules) -> str:
        roi = getattr(rules, "roi_polygon", None) or []
        line = getattr(rules, "line", None)
        line_direction = str(getattr(rules, "line_direction", "any") or "any")

        tokens: list[str] = [f"dir:{line_direction}"]

        if roi:
            roi_tokens = []
            for point in roi:
                if not point or len(point) != 2:
                    continue
                try:
                    roi_tokens.append(f"{int(point[0])}:{int(point[1])}")
                except Exception:
                    continue
            tokens.append("roi:" + ";".join(roi_tokens) if roi_tokens else "roi:na")
        else:
            tokens.append("roi:na")

        if line and len(line) == 2:
            try:
                start = line[0]
                end = line[1]
                tokens.append(f"line:{int(start[0])}:{int(start[1])}:{int(end[0])}:{int(end[1])}")
            except Exception:
                tokens.append("line:na")
        else:
            tokens.append("line:na")

        return "|".join(tokens)


event_idempotency_store = EventIdempotencyStore()
