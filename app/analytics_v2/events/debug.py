from __future__ import annotations

import json
import threading
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_event_rules_debug_logger


class EventRuleDebugEmitter:
    def __init__(self):
        self._logger = get_event_rules_debug_logger()
        self._lock = threading.Lock()
        self._last_log_at: dict[tuple[int, int, str], float] = {}

    def enabled_for(self, camera_id: int | None) -> bool:
        return settings.event_rule_debug_is_enabled_for_camera(camera_id)

    def emit(
        self,
        event_name: str,
        *,
        camera_id: int | None,
        track_id: int | None = None,
        reason: str | None = None,
        rate_key: str | None = None,
        **payload: Any,
    ) -> None:
        if not self.enabled_for(camera_id):
            return

        camera_key = int(camera_id) if camera_id is not None else -1
        track_key = int(track_id) if track_id is not None else -1
        throttle_key = str(rate_key or reason or event_name)
        now = time.monotonic()
        min_interval = max(0.0, float(settings.event_rule_debug_rate_limit_seconds))

        with self._lock:
            key = (camera_key, track_key, throttle_key)
            last = self._last_log_at.get(key, 0.0)
            if min_interval > 0 and (now - last) < min_interval:
                return
            self._last_log_at[key] = now

        message = {
            "kind": "event_rule_debug",
            "event": event_name,
            "camera_id": camera_id,
            "track_id": track_id,
            "reason": reason,
            **payload,
        }
        try:
            self._logger.info(json.dumps(message, ensure_ascii=False, default=str, sort_keys=True))
        except Exception:
            pass


event_rule_debug = EventRuleDebugEmitter()
