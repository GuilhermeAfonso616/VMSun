"""Cache temporario de credenciais e perfis descobertos em NVRs."""

from __future__ import annotations

import secrets
import time
from threading import Lock
from typing import Any, Callable


class NvrDiscoveryCache:
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
        expired = [
            token
            for token, payload in self._entries.items()
            if float(payload.get("expires_at", 0.0) or 0.0) < now
        ]
        for token in expired:
            self._entries.pop(token, None)

    def store(
        self,
        *,
        host: str,
        username: str,
        password: str,
        profiles: list[dict[str, Any]],
        token: str | None = None,
    ) -> str:
        cache_token = str(token or "").strip() or secrets.token_urlsafe(24)
        now = self._clock()
        payload = {
            "expires_at": now + self._ttl_seconds,
            "host": host,
            "username": username,
            "password": password,
            "profiles": {int(item["index"]): dict(item) for item in profiles},
        }
        with self._lock:
            self._cleanup_locked(now)
            self._entries[cache_token] = payload
        return cache_token

    def get(self, token: str | None) -> dict[str, Any] | None:
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            payload = self._entries.get(str(token or ""))
            if payload is None:
                return None
            return {
                **payload,
                "profiles": {
                    int(index): dict(profile)
                    for index, profile in payload.get("profiles", {}).items()
                },
            }

    def cleanup(self) -> None:
        with self._lock:
            self._cleanup_locked(self._clock())


nvr_discovery_cache = NvrDiscoveryCache()


def cleanup_nvr_discovery_cache() -> None:
    nvr_discovery_cache.cleanup()


def store_nvr_discovery_cache(**kwargs) -> str:
    return nvr_discovery_cache.store(**kwargs)


def get_nvr_discovery_cache(token: str | None) -> dict[str, Any] | None:
    return nvr_discovery_cache.get(token)
