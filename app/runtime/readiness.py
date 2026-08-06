"""Estado de prontidao do runtime de inferencia.

O processo HTTP pode estar vivo enquanto o detector ainda carrega, recompila
ou se recupera de um erro CUDA/TensorRT. Esse estado precisa ser exposto
separadamente para impedir que os workers enviem trabalho para uma IA que ainda
nao esta apta a processar frames.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any


_lock = Lock()
_status = "starting"
_reason = "runtime_starting"
_last_error: str | None = None
_changed_at = datetime.now(timezone.utc)


def _set_state(status: str, reason: str, error: BaseException | str | None = None) -> None:
    global _status, _reason, _last_error, _changed_at
    with _lock:
        _status = status
        _reason = reason
        _last_error = None if error is None else str(error)[:500]
        _changed_at = datetime.now(timezone.utc)


def mark_starting(reason: str = "runtime_starting") -> None:
    _set_state("starting", reason)


def mark_ready(reason: str = "detector_probe_ok") -> None:
    _set_state("ready", reason)


def mark_degraded(reason: str = "detector_unavailable", error: BaseException | str | None = None) -> None:
    _set_state("degraded", reason, error)


def is_ready() -> bool:
    with _lock:
        return _status == "ready"


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "status": _status,
            "ready": _status == "ready",
            "reason": _reason,
            "last_error": _last_error,
            "changed_at": _changed_at.isoformat(),
        }
