from __future__ import annotations

from dataclasses import dataclass, field


HEALTH_RUNNING = "running"
HEALTH_STARTING = "starting"
HEALTH_WARMING_UP = "warming_up"
HEALTH_DEGRADED = "degraded"
HEALTH_RECONNECTING = "reconnecting"
HEALTH_OFFLINE = "offline"
HEALTH_STOPPED = "stopped"

REASON_CONNECT_TIMEOUT = "connect_timeout"
REASON_READ_TIMEOUT = "read_timeout"
REASON_DECODE_ERROR = "decode_error"
REASON_STREAM_STALLED = "stream_stalled"
REASON_INFERENCE_TIMEOUT = "inference_timeout"
REASON_DB_ERROR = "db_error"
REASON_MANUAL_RESTART = "manual_restart"
REASON_RECONNECT_FAILED = "reconnect_failed"
REASON_UNKNOWN_ERROR = "unknown_error"
REASON_STOPPED = "stopped"

RUNNING_STATES = {HEALTH_RUNNING, "running_motion_test"}
OPERATIONAL_STATES = {
    HEALTH_RUNNING,
    HEALTH_STARTING,
    HEALTH_WARMING_UP,
    HEALTH_DEGRADED,
    HEALTH_RECONNECTING,
    HEALTH_OFFLINE,
    HEALTH_STOPPED,
    "running_motion_test",
}


def normalize_reason(reason: str | None, fallback: str = REASON_UNKNOWN_ERROR) -> str:
    if not reason:
        return fallback

    value = str(reason).strip().lower().replace(" ", "_")
    if value in {
        REASON_CONNECT_TIMEOUT,
        REASON_READ_TIMEOUT,
        REASON_DECODE_ERROR,
        REASON_STREAM_STALLED,
        REASON_INFERENCE_TIMEOUT,
        REASON_DB_ERROR,
        REASON_MANUAL_RESTART,
        REASON_RECONNECT_FAILED,
        REASON_UNKNOWN_ERROR,
        REASON_STOPPED,
    }:
        return value
    return fallback


@dataclass(slots=True)
class ReconnectAttempt:
    attempt_number: int
    delay_seconds: float
    reason: str
    status: str
    exhausted: bool = False
    succeeded: bool = False


@dataclass(slots=True)
class ReconnectPolicy:
    initial_delay_seconds: float
    backoff_multiplier: float
    max_backoff_seconds: float
    max_attempts: int
    attempt_count: int = 0
    last_delay_seconds: float = 0.0
    last_reason: str = REASON_UNKNOWN_ERROR

    def reset(self):
        self.attempt_count = 0
        self.last_delay_seconds = 0.0
        self.last_reason = REASON_UNKNOWN_ERROR

    def _delay_for_attempt(self, attempt_number: int) -> float:
        if attempt_number <= 1:
            return 0.0

        base = max(0.1, float(self.initial_delay_seconds))
        multiplier = max(1.0, float(self.backoff_multiplier))
        delay = base * (multiplier ** (attempt_number - 2))
        return min(max(delay, 0.0), max(0.1, float(self.max_backoff_seconds)))

    def next_attempt(self, reason: str) -> ReconnectAttempt:
        self.attempt_count += 1
        self.last_reason = normalize_reason(reason)
        delay_seconds = self._delay_for_attempt(self.attempt_count)
        self.last_delay_seconds = delay_seconds
        return ReconnectAttempt(
            attempt_number=self.attempt_count,
            delay_seconds=delay_seconds,
            reason=self.last_reason,
            status=HEALTH_RECONNECTING,
            exhausted=self.attempt_count >= max(1, int(self.max_attempts)),
        )

    def can_retry(self) -> bool:
        return self.attempt_count < max(1, int(self.max_attempts))


def connect_reason_for_failure(attempt_number: int, *, previous_reason: str | None = None) -> str:
    if attempt_number <= 1:
        return REASON_CONNECT_TIMEOUT

    normalized_previous = normalize_reason(previous_reason, REASON_STREAM_STALLED)
    if normalized_previous in {REASON_CONNECT_TIMEOUT, REASON_READ_TIMEOUT}:
        return normalized_previous
    return REASON_RECONNECT_FAILED
