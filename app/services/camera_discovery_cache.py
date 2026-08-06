"""Armazena temporariamente credenciais e profiles da descoberta de camera."""

from __future__ import annotations

import secrets
import time
from copy import deepcopy
from threading import Lock
from typing import Any, Callable


class CameraDiscoveryCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = 1800,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._clock = clock
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def _cleanup_locked(self, now: float) -> None:
        for token in [
            key
            for key, payload in self._entries.items()
            if float(payload.get("expires_at", 0.0) or 0.0) < now
        ]:
            self._entries.pop(token, None)

    def store(self, payload: dict[str, Any], *, token: str | None = None) -> str:
        cache_token = str(token or "").strip() or secrets.token_urlsafe(24)
        now = self._clock()
        cached_payload = deepcopy(payload)
        cached_payload["expires_at"] = now + self._ttl_seconds
        with self._lock:
            self._cleanup_locked(now)
            self._entries[cache_token] = cached_payload
        return cache_token

    def get(self, token: str | None) -> dict[str, Any] | None:
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            payload = self._entries.get(str(token or ""))
            return deepcopy(payload) if payload is not None else None


camera_discovery_cache = CameraDiscoveryCache()


def store_camera_discovery(payload: dict[str, Any], *, token: str | None = None) -> str:
    return camera_discovery_cache.store(payload, token=token)


def get_camera_discovery(token: str | None) -> dict[str, Any] | None:
    return camera_discovery_cache.get(token)
