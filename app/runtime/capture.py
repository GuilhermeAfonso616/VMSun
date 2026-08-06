from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from app.camera.gateway_frames_capture import GatewayFramesCapture
from app.camera.frame_transport import build_gateway_frame_capture
from app.camera.rtsp_capture import RTSPCapture
from app.core.config import settings
from app.core.logging import get_camera_logger
from app.core.url_safety import sanitize_url_for_log
from app.services.reconnect_policy import (
    HEALTH_OFFLINE,
    HEALTH_RECONNECTING,
    HEALTH_RUNNING,
    HEALTH_STOPPED,
    REASON_CONNECT_TIMEOUT,
    REASON_READ_TIMEOUT,
    REASON_RECONNECT_FAILED,
    REASON_STOPPED,
    REASON_STREAM_STALLED,
    ReconnectPolicy,
    connect_reason_for_failure,
    normalize_reason,
)


@dataclass(slots=True)
class CaptureReadResult:
    ok: bool
    frame: object | None
    read_ms: float
    reason: str | None = None
    metadata: dict | None = None


@dataclass(slots=True)
class ReconnectResult:
    recovered: bool
    exhausted: bool
    attempts: int
    reason: str
    delay_seconds: float
    status: str


class CameraCaptureService:
    def __init__(
        self,
        rtsp_url: str,
        camera_id: int | None = None,
        worker_mode: str = "-",
        stop_requested: Callable[[], bool] | None = None,
        failures_before_reconnect: int = 12,
        reconnect_sleep_step_seconds: float = 0.2,
    ):
        self.rtsp_url = rtsp_url
        self.camera_id = camera_id
        self.worker_mode = worker_mode
        self.capture_source = "rtsp"
        self.capture = self._build_capture(prefer_gateway=True)
        self.reconnect_count = 0
        self.dropped_frames_count = 0
        self.gateway_recovery_count = 0
        self.gateway_recovery_last_probe_at: float | None = None
        self.gateway_recovery_last_success_at: float | None = None
        self.gateway_fallback_started_at: float | None = None
        self.gateway_recovery_last_success_wall_at: float | None = None
        self.gateway_fallback_started_wall_at: float | None = None
        self.last_successful_read_at: float | None = None
        self.logger = get_camera_logger(
            "app.runtime.capture",
            camera_id=camera_id if camera_id is not None else -1,
            worker_mode=worker_mode,
        )

        self._stop_requested = stop_requested or (lambda: False)
        self.failures_before_reconnect = max(1, int(failures_before_reconnect))
        self.reconnect_sleep_step_seconds = max(0.05, float(reconnect_sleep_step_seconds))
        self._last_soft_failure_log_ts = 0.0
        self.reconnect_policy = ReconnectPolicy(
            initial_delay_seconds=float(settings.reconnect_initial_delay_seconds),
            backoff_multiplier=float(settings.reconnect_backoff_multiplier),
            max_backoff_seconds=float(settings.reconnect_max_backoff_seconds),
            max_attempts=int(settings.reconnect_max_attempts),
        )


    def _gateway_capture_enabled(self) -> bool:
        return bool(
            settings.camera_gateway_enabled
            and settings.camera_gateway_worker_capture_enabled
            and str(settings.camera_gateway_base_url or "").strip()
            and self.camera_id is not None
        )

    def _gateway_worker_exclusive_mode_enabled(self) -> bool:
        return bool(
            settings.camera_gateway_enabled
            and settings.camera_gateway_worker_capture_enabled
            and not settings.camera_gateway_worker_rtsp_fallback_enabled
        )

    def _build_capture(self, *, prefer_gateway: bool):
        if prefer_gateway and settings.camera_gateway_enabled and settings.camera_gateway_worker_capture_enabled:
            self.capture_source = "gateway frames"
            if self.camera_id is None:
                raise RuntimeError("Gateway frames capture requires a valid camera_id")
            return build_gateway_frame_capture(int(self.camera_id), self.rtsp_url)

        if prefer_gateway and self._gateway_worker_exclusive_mode_enabled():
            raise RuntimeError(
                "Gateway worker capture is enabled in exclusive mode and direct RTSP capture is disabled"
            )

        self.capture_source = "rtsp"
        return RTSPCapture(self.rtsp_url)

    def _rtsp_fallback_enabled(self) -> bool:
        return bool(settings.camera_gateway_worker_rtsp_fallback_enabled)

    def gateway_circuit_snapshot(self) -> dict:
        capture = self.capture
        if not isinstance(capture, GatewayFramesCapture):
            return {"state": "closed", "open_duration_seconds": 0.0}
        return {
            "state": capture.circuit_state,
            "reason": capture.circuit_reason,
            "open_until": capture.circuit_open_until,
            "retry_after_ms": capture.circuit_retry_after_ms,
            "gateway_instance_id": capture.gateway_instance_id,
            "failure_epoch": capture.failure_epoch,
            "open_duration_seconds": capture.circuit_open_duration_seconds(),
        }

    def frame_transport_snapshot(self) -> dict:
        snapshot = getattr(self.capture, "transport_metrics", None)
        if callable(snapshot):
            try:
                return dict(snapshot())
            except Exception:
                return {"frame_transport_mode": "shared_memory", "frame_transport_errors_total": 1}
        return {
            "frame_transport_mode": "http",
            "frame_transport_http_fallback_total": 0,
        }

    def inference_pool_release_due(self) -> bool:
        snapshot = self.gateway_circuit_snapshot()
        return bool(
            snapshot.get("state") in {"open", "half_open"}
            and float(snapshot.get("open_duration_seconds") or 0.0)
            >= max(0.0, float(settings.camera_gateway_circuit_park_after_seconds))
        )

    def _keep_gateway_if_fresh(self, reason: str) -> bool:
        if self.capture_source != "gateway frames" or not isinstance(self.capture, GatewayFramesCapture):
            return False

        try:
            if not self.capture.open_from_fresh_status(
                timeout_seconds=max(0.5, float(settings.camera_gateway_register_timeout_seconds))
            ):
                return False
        except Exception:
            return False

        self.logger.info(
            "Gateway capture kept after timeout because status has fresh frames reason=%s",
            reason,
            extra={
                "action": "capture_gateway_status_verified",
                "status": HEALTH_RUNNING,
                "reason": "fresh_gateway_status",
            },
        )
        self.reconnect_policy.reset()
        return True

    def _switch_to_rtsp_fallback(self, reason: str) -> bool:
        if self.capture_source == "rtsp":
            return True

        if not self._rtsp_fallback_enabled():
            self.logger.error(
                "Gateway capture unavailable and direct RTSP fallback is disabled reason=%s",
                reason,
                extra={
                    "action": "capture_gateway_no_fallback",
                    "status": HEALTH_OFFLINE,
                    "reason": REASON_CONNECT_TIMEOUT,
                },
            )
            return False

        self.logger.warning(
            "Gateway capture unavailable; falling back to direct RTSP reason=%s",
            reason,
            extra={"action": "capture_gateway_fallback", "status": HEALTH_RECONNECTING, "reason": reason},
        )
        try:
            self.capture.release()
        except Exception:
            pass
        self.capture = self._build_capture(prefer_gateway=False)
        self.gateway_fallback_started_at = time.monotonic()
        self.gateway_fallback_started_wall_at = time.time()
        return True

    def _gateway_recovery_enabled(self) -> bool:
        return bool(
            settings.camera_gateway_recovery_enabled
            and self._gateway_capture_enabled()
            and self._rtsp_fallback_enabled()
        )

    def _maybe_recover_gateway(self) -> None:
        if self.capture_source != "rtsp" or not self._gateway_recovery_enabled():
            return
        if self.gateway_fallback_started_at is None:
            return

        now = time.monotonic()
        stable_seconds = max(1.0, float(settings.camera_gateway_recovery_stable_seconds))
        if (now - self.gateway_fallback_started_at) < stable_seconds:
            return

        probe_interval = max(5.0, float(settings.camera_gateway_recovery_probe_interval_seconds))
        if self.gateway_recovery_last_probe_at is not None and (now - self.gateway_recovery_last_probe_at) < probe_interval:
            return
        self.gateway_recovery_last_probe_at = now

        candidate = GatewayFramesCapture(int(self.camera_id), self.rtsp_url)
        timeout_seconds = max(1.0, float(settings.camera_gateway_recovery_probe_timeout_seconds))
        seq = 0
        try:
            seq = candidate.probe_fresh_frame(timeout_seconds=timeout_seconds)
        except Exception:
            seq = 0

        if seq <= 0:
            self.logger.info(
                "Gateway recovery probe did not find fresh frames camera_id=%s timeout_seconds=%.1f",
                self.camera_id,
                timeout_seconds,
                extra={
                    "action": "capture_gateway_recovery_probe",
                    "status": "degraded",
                    "reason": "no_fresh_gateway_frame",
                },
            )
            return

        try:
            self.capture.release()
        except Exception:
            pass

        candidate._last_seq = seq - 1
        candidate._opened = True
        candidate.cap = object()
        candidate.consecutive_read_failures = 0
        candidate.last_read_error_log_ts = 0.0
        candidate._frame_queue.clear()
        candidate.last_read_soft_wait = False
        candidate.last_read_reason = None

        self.capture = candidate
        self.capture_source = "gateway frames"
        self.gateway_fallback_started_at = None
        self.gateway_fallback_started_wall_at = None
        self.gateway_recovery_count += 1
        self.gateway_recovery_last_success_at = now
        self.gateway_recovery_last_success_wall_at = time.time()
        self.reconnect_policy.reset()
        self.logger.warning(
            "Gateway recovered and capture switched back camera_id=%s seq=%s",
            self.camera_id,
            seq,
            extra={
                "action": "capture_gateway_recovered",
                "status": HEALTH_RUNNING,
                "reason": "fresh_gateway_frame_verified",
            },
        )

    def _should_stop(self) -> bool:
        try:
            return bool(self._stop_requested())
        except Exception:
            return False

    def _sleep_interruptible(self, total_seconds: float) -> bool:
        remaining = max(0.0, float(total_seconds))
        while remaining > 0:
            if self._should_stop():
                return False
            step = min(self.reconnect_sleep_step_seconds, remaining)
            time.sleep(step)
            remaining -= step
        return not self._should_stop()

    def open(self):
        if self._should_stop():
            self.logger.info(
                "Open canceled before capture start",
                extra={"action": "open_capture", "status": HEALTH_STOPPED, "reason": REASON_STOPPED},
            )
            return

        max_attempts = max(1, int(getattr(settings, "capture_open_retry_attempts", 3)))
        delay_seconds = max(0.1, float(getattr(settings, "capture_open_retry_initial_delay_seconds", 0.75)))
        delay_multiplier = max(1.0, float(getattr(settings, "capture_open_retry_backoff_multiplier", 1.8)))
        max_delay_seconds = max(delay_seconds, float(getattr(settings, "capture_open_retry_max_delay_seconds", 5.0)))

        attempt = 0
        while not self._should_stop():
            attempt += 1
            self.logger.info(
                "Opening %s capture: %s attempt=%s/%s",
                self.capture_source,
                sanitize_url_for_log(self.rtsp_url),
                attempt,
                max_attempts,
                extra={"action": "open_capture", "status": HEALTH_RUNNING, "reason": "initial_open"},
            )
            try:
                self.capture.open()
                break
            except Exception as exc:
                if self.capture_source == "gateway frames":
                    if self._keep_gateway_if_fresh(str(exc)):
                        break
                    if self._switch_to_rtsp_fallback(str(exc)):
                        try:
                            self.capture.open()
                            break
                        except Exception:
                            pass
                    else:
                        self.logger.warning(
                            "Gateway frames capture not ready and RTSP fallback is disabled; will retry reason=%s",
                            exc,
                            extra={
                                "action": "open_capture",
                                "status": HEALTH_RECONNECTING,
                                "reason": REASON_CONNECT_TIMEOUT,
                            },
                        )
                        raise
                if attempt >= max_attempts or self._should_stop():
                    raise
                self.logger.warning(
                    "%s open failed, retrying with backoff attempt=%s delay_seconds=%.2f",
                    self.capture_source.upper(),
                    attempt,
                    delay_seconds,
                    extra={
                        "action": "open_capture_retry",
                        "status": HEALTH_RECONNECTING,
                        "reason": REASON_CONNECT_TIMEOUT,
                    },
                )
                if not self._sleep_interruptible(delay_seconds):
                    return
                delay_seconds = min(delay_seconds * delay_multiplier, max_delay_seconds)

        if self._should_stop():
            self.logger.info(
                "Stop requested right after open; releasing RTSP",
                extra={"action": "open_capture", "status": HEALTH_STOPPED, "reason": REASON_STOPPED},
            )
            self.capture.release()

    def read_latest(self, drop_frames: int = 2) -> CaptureReadResult:
        started = time.perf_counter()

        if self._should_stop():
            read_ms = (time.perf_counter() - started) * 1000.0
            return CaptureReadResult(ok=False, frame=None, read_ms=read_ms, reason=REASON_STOPPED)

        if self.capture.cap is None:
            read_ms = (time.perf_counter() - started) * 1000.0
            self.dropped_frames_count += 1
            return CaptureReadResult(ok=False, frame=None, read_ms=read_ms, reason=REASON_CONNECT_TIMEOUT)

        ok, frame = self.capture.read_latest(drop_frames=drop_frames)
        read_ms = (time.perf_counter() - started) * 1000.0

        if not ok and getattr(self.capture, "last_read_soft_wait", False):
            return CaptureReadResult(ok=False, frame=None, read_ms=read_ms, reason="no_frame_ready")

        if not ok or frame is None:
            self.dropped_frames_count += 1
            capture_reason = str(getattr(self.capture, "last_read_reason", "") or "")
            if capture_reason == "gateway_circuit_open":
                reason = capture_reason
            else:
                reason = REASON_READ_TIMEOUT if self.capture.consecutive_read_failures <= 1 else REASON_STREAM_STALLED
            return CaptureReadResult(ok=ok, frame=frame, read_ms=read_ms, reason=reason)

        self.last_successful_read_at = time.monotonic()
        self._maybe_recover_gateway()
        metadata = getattr(self.capture, "last_frame_metadata", None)
        return CaptureReadResult(
            ok=ok,
            frame=frame,
            read_ms=read_ms,
            reason=None,
            metadata=dict(metadata) if isinstance(metadata, dict) else None,
        )

    def _open_capture(self) -> tuple[bool, str]:
        try:
            self.capture.open()
            self.reconnect_policy.reset()
            return True, HEALTH_RUNNING
        except Exception as exc:
            if self.capture_source == "gateway frames":
                if self._keep_gateway_if_fresh(str(exc)):
                    self.reconnect_policy.reset()
                    return True, HEALTH_RUNNING
                if self._switch_to_rtsp_fallback(str(exc)):
                    try:
                        self.capture.open()
                        self.reconnect_policy.reset()
                        return True, HEALTH_RUNNING
                    except Exception:
                        pass
                else:
                    self.logger.warning(
                        "Gateway capture stream not ready; RTSP fallback disabled reason=%s",
                        exc,
                        extra={
                            "action": "capture_open",
                            "status": HEALTH_RECONNECTING,
                            "reason": REASON_CONNECT_TIMEOUT,
                        },
                    )
                    return False, REASON_CONNECT_TIMEOUT
            self.logger.exception(
                "Failed to open capture stream",
                extra={
                    "action": "capture_open",
                    "status": HEALTH_OFFLINE,
                    "reason": REASON_CONNECT_TIMEOUT,
                },
            )
            return False, REASON_CONNECT_TIMEOUT

    def reconnect_stream(self, reason: str | None = None) -> ReconnectResult:
        if self._should_stop():
            return ReconnectResult(
                recovered=False,
                exhausted=False,
                attempts=0,
                reason=REASON_STOPPED,
                delay_seconds=0.0,
                status=HEALTH_STOPPED,
            )

        normalized_reason = normalize_reason(reason, REASON_STREAM_STALLED)
        attempt_number = 0
        last_delay = 0.0

        while self.reconnect_policy.can_retry() and not self._should_stop():
            attempt = self.reconnect_policy.next_attempt(normalized_reason)
            attempt_number = attempt.attempt_number
            last_delay = attempt.delay_seconds

            self.logger.warning(
                "Reconnect attempt scheduled attempt=%s delay_seconds=%.2f reason=%s",
                attempt.attempt_number,
                attempt.delay_seconds,
                attempt.reason,
                extra={
                    "action": "capture_reconnect_attempt",
                    "status": HEALTH_RECONNECTING,
                    "reason": attempt.reason,
                },
            )

            if attempt.delay_seconds > 0:
                self.logger.info(
                    "Reconnect backoff selected attempt=%s delay_seconds=%.2f reason=%s",
                    attempt.attempt_number,
                    attempt.delay_seconds,
                    attempt.reason,
                    extra={
                        "action": "capture_reconnect_backoff",
                        "status": HEALTH_RECONNECTING,
                        "reason": attempt.reason,
                    },
                )
                if not self._sleep_interruptible(attempt.delay_seconds):
                    return ReconnectResult(
                        recovered=False,
                        exhausted=False,
                        attempts=attempt.attempt_number,
                        reason=REASON_STOPPED,
                        delay_seconds=attempt.delay_seconds,
                        status=HEALTH_STOPPED,
                    )

            try:
                self.capture.release()
            except Exception:
                pass

            opened, open_reason = self._open_capture()
            if opened:
                self.logger.info(
                    "Reconnect succeeded attempt=%s reason=%s",
                    attempt.attempt_number,
                    normalized_reason,
                    extra={
                        "action": "capture_reconnect_success",
                        "status": HEALTH_RUNNING,
                        "reason": normalized_reason,
                    },
                )
                return ReconnectResult(
                    recovered=True,
                    exhausted=False,
                    attempts=attempt.attempt_number,
                    reason=normalized_reason,
                    delay_seconds=attempt.delay_seconds,
                    status=HEALTH_RUNNING,
                )

            normalized_reason = connect_reason_for_failure(attempt.attempt_number, previous_reason=open_reason)
            self.logger.warning(
                "Reconnect failed attempt=%s reason=%s",
                attempt.attempt_number,
                normalized_reason,
                extra={
                    "action": "capture_reconnect_failure",
                    "status": HEALTH_RECONNECTING,
                    "reason": normalized_reason,
                },
            )

        self.reconnect_policy.reset()
        exhausted_reason = REASON_RECONNECT_FAILED if attempt_number else normalized_reason
        self.logger.error(
            "Reconnect exhausted attempts=%s reason=%s",
            attempt_number,
            exhausted_reason,
            extra={
                "action": "capture_reconnect_exhausted",
                "status": HEALTH_OFFLINE,
                "reason": exhausted_reason,
            },
        )
        return ReconnectResult(
            recovered=False,
            exhausted=True,
            attempts=attempt_number,
            reason=exhausted_reason,
            delay_seconds=last_delay,
            status=HEALTH_OFFLINE,
        )

    def handle_capture_failure(self):
        if self._should_stop():
            self.logger.info(
                "Reconnect canceled by stop request",
                extra={"action": "capture_reconnect", "status": HEALTH_STOPPED, "reason": REASON_STOPPED},
            )
            return ReconnectResult(
                recovered=False,
                exhausted=False,
                attempts=0,
                reason=REASON_STOPPED,
                delay_seconds=0.0,
                status=HEALTH_STOPPED,
            )

        failure_count = int(getattr(self.capture, "consecutive_read_failures", 0) or 0)

        if failure_count < self.failures_before_reconnect:
            now = time.perf_counter()
            if (
                failure_count == 1
                or failure_count == self.failures_before_reconnect - 1
                or (now - self._last_soft_failure_log_ts) >= 5.0
            ):
                self.logger.warning(
                    "Capture failed without reconnect yet failure_count=%s threshold=%s dropped_frames_count=%s",
                    failure_count,
                    self.failures_before_reconnect,
                    self.dropped_frames_count,
                    extra={
                        "action": "capture_read",
                        "status": "degraded",
                        "reason": REASON_STREAM_STALLED if failure_count > 1 else REASON_READ_TIMEOUT,
                    },
                )
                self._last_soft_failure_log_ts = now
            return ReconnectResult(
                recovered=False,
                exhausted=False,
                attempts=0,
                reason=REASON_STREAM_STALLED if failure_count > 1 else REASON_READ_TIMEOUT,
                delay_seconds=0.0,
                status="degraded",
            )

        self.reconnect_count += 1
        self.logger.warning(
            "Reconnect requested after consecutive failures failure_count=%s reconnect_count=%s dropped_frames_count=%s",
            failure_count,
            self.reconnect_count,
            self.dropped_frames_count,
            extra={
                "action": "capture_reconnect",
                "status": HEALTH_RECONNECTING,
                "reason": REASON_STREAM_STALLED,
            },
        )

        result = self.reconnect_stream(reason=REASON_STREAM_STALLED)
        return result

    def release(self):
        self.logger.info(
            "Releasing capture service",
            extra={"action": "release_capture", "status": HEALTH_STOPPED, "reason": "worker_shutdown"},
        )
        self.capture.release()
