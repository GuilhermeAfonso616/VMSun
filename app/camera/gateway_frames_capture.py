from __future__ import annotations

import base64
import json
import time
from collections import deque
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.services.camera_gateway_client import fetch_camera_status, gateway_camera_url, register_camera_source


class GatewayCircuitOpenError(RuntimeError):
    def __init__(self, camera_id: int, retry_after_ms: int = 0, reason: str = ""):
        self.camera_id = int(camera_id)
        self.retry_after_ms = max(0, int(retry_after_ms or 0))
        self.reason = str(reason or "gateway_circuit_open")
        super().__init__(
            f"camera gateway circuit open camera_id={self.camera_id} "
            f"retry_after_ms={self.retry_after_ms} reason={self.reason}"
        )


class GatewayFramesCapture:
    """Capture compatível com RTSPCapture, lendo frames via /frames do gateway Go."""

    def __init__(self, camera_id: int, source_url: str):
        self.camera_id = camera_id
        self.source_url = source_url
        self.frames_url = gateway_camera_url(camera_id, "/frames")
        self.logger = get_logger("app.gateway_frames_capture")

        self.cap = None
        self.consecutive_read_failures = 0
        self.last_read_error_log_ts = 0.0
        self._frame_queue: deque[dict] = deque(maxlen=5)
        self.last_frame_metadata: dict = {}
        self.last_read_soft_wait = False
        self.last_read_reason: str | None = None

        self._last_seq = 0
        self._opened = False
        self.circuit_state = "closed"
        self.circuit_reason = ""
        self.circuit_open_until: str | None = None
        self.circuit_retry_after_ms = 0
        self.gateway_instance_id = ""
        self.stream_generation_id = ""
        self.failure_epoch = 0
        self._circuit_open_since_monotonic: float | None = None

    def open(self):
        self.release()

        if self.open_from_fresh_status(timeout_seconds=max(0.5, float(settings.camera_gateway_register_timeout_seconds)), strict=True):
            return

        if self.circuit_state == "open":
            raise GatewayCircuitOpenError(
                self.camera_id,
                retry_after_ms=self.circuit_retry_after_ms,
                reason=self.circuit_reason,
            )

        first_seq = self.probe_fresh_frame(
            timeout_seconds=max(1.0, float(settings.camera_gateway_first_frame_timeout_seconds))
        )

        if first_seq <= 0:
            if not self.open_from_fresh_status():
                raise RuntimeError(
                    "camera gateway did not deliver first frame within "
                    f"{settings.camera_gateway_first_frame_timeout_seconds}s camera_id={self.camera_id}"
                )
            return

        self._last_seq = first_seq - 1
        self._opened = True
        self.cap = object()
        self.consecutive_read_failures = 0
        self.last_read_error_log_ts = 0.0
        self._frame_queue.clear()
        self.last_read_soft_wait = False
        self.last_read_reason = None

        self.logger.info(
            "Gateway frames capture opened camera_id=%s frames_url=%s",
            self.camera_id,
            self.frames_url,
            extra={
                "camera_id": self.camera_id,
                "action": "gateway_frames_capture_opened",
                "status": "running",
                "reason": "first_frame_ready",
            },
        )

    def open_from_fresh_status(self, timeout_seconds: float | None = None, *, strict: bool = False) -> bool:
        status = self.fetch_status(timeout_seconds=timeout_seconds)
        self._update_circuit_state(status)
        if not self.status_has_fresh_frame(status, strict=strict):
            return False

        self._last_seq = 0
        self._opened = True
        self.cap = object()
        self.consecutive_read_failures = 0
        self.last_read_error_log_ts = 0.0
        self._frame_queue.clear()
        self.last_read_soft_wait = False
        self.last_read_reason = None

        self.logger.info(
            "Gateway frames capture opened from fresh status camera_id=%s state=%s last_frame_age_ms=%s",
            self.camera_id,
            status.get("state") if isinstance(status, dict) else None,
            status.get("last_frame_age_ms") if isinstance(status, dict) else None,
            extra={
                "camera_id": self.camera_id,
                "action": "gateway_frames_capture_opened",
                "status": "running",
                "reason": "fresh_status_verified_strict" if strict else "fresh_status_verified",
            },
        )
        return True

    def fetch_status(self, timeout_seconds: float | None = None) -> dict | None:
        return fetch_camera_status(
            self.camera_id,
            None,
            timeout_seconds=max(0.5, float(timeout_seconds or settings.camera_gateway_register_timeout_seconds)),
        )

    def _update_circuit_state(self, payload: dict | None) -> None:
        if not isinstance(payload, dict):
            return
        state = str(payload.get("circuit_state") or ("open" if payload.get("circuit_open") else "closed")).strip().lower()
        if state not in {"closed", "open", "half_open"}:
            state = "open" if bool(payload.get("circuit_open")) else "closed"
        self.circuit_state = state
        self.circuit_reason = str(payload.get("circuit_reason") or "")
        self.circuit_open_until = str(payload.get("circuit_open_until") or "") or None
        try:
            self.circuit_retry_after_ms = max(0, int(payload.get("retry_after_ms") or 0))
        except (TypeError, ValueError):
            self.circuit_retry_after_ms = 0
        self.gateway_instance_id = str(payload.get("gateway_instance_id") or "")
        self.stream_generation_id = str(payload.get("stream_generation_id") or "")
        try:
            self.failure_epoch = max(0, int(payload.get("failure_epoch") or 0))
        except (TypeError, ValueError):
            self.failure_epoch = 0

        if state == "open":
            if self._circuit_open_since_monotonic is None:
                self._circuit_open_since_monotonic = time.monotonic()
        elif state == "closed":
            self._circuit_open_since_monotonic = None

    def circuit_open_duration_seconds(self) -> float:
        if self._circuit_open_since_monotonic is None:
            return 0.0
        return max(0.0, time.monotonic() - self._circuit_open_since_monotonic)

    def status_has_fresh_frame(self, status: dict | None, *, strict: bool = False) -> bool:
        if not isinstance(status, dict):
            return False

        state = str(status.get("state") or "").strip().lower()
        if self.circuit_state == "open":
            return False
        if state not in {"running", "warming_up"}:
            return False

        if strict and state != "running":
            return False

        try:
            age_ms = float(status.get("last_frame_age_ms"))
        except (TypeError, ValueError):
            return False

        max_age_ms = max(1000.0, float(settings.camera_gateway_recovery_fresh_frame_max_age_seconds) * 1000.0)
        if strict:
            try:
                failure_count = int(status.get("failure_count", 0) or 0)
            except (TypeError, ValueError):
                failure_count = 0
            max_age_ms = min(max_age_ms, 2000.0)
            if failure_count > 0:
                return False
        return 0.0 <= age_ms <= max_age_ms

    def probe_fresh_frame(self, timeout_seconds: float | None = None) -> int:
        registered = register_camera_source(
            self.camera_id,
            self.source_url,
            timeout_seconds=max(1.0, float(settings.camera_gateway_register_timeout_seconds)),
        )
        if not registered:
            return 0

        deadline = time.monotonic() + max(1.0, float(timeout_seconds or settings.camera_gateway_first_frame_timeout_seconds))
        while time.monotonic() < deadline:
            payload = self._fetch_frames(after_seq=0, limit=1, timeout_seconds=timeout_seconds)
            latest_seq = int(payload.get("latest_seq") or 0) if isinstance(payload, dict) else 0
            probe_after_seq = max(0, latest_seq - 2) if latest_seq > 0 else 0

            if probe_after_seq > 0:
                payload = self._fetch_frames(after_seq=probe_after_seq, limit=2, timeout_seconds=timeout_seconds)

            frames = payload.get("frames") if isinstance(payload, dict) else None
            if isinstance(frames, list):
                for frame_item in reversed(frames):
                    seq = int(frame_item.get("seq") or 0)
                    if seq > 0 and self._frame_is_fresh(frame_item):
                        return seq
            time.sleep(0.2)
        return 0

    def _fetch_frames(self, *, after_seq: int, limit: int, timeout_seconds: float | None = None) -> dict | None:
        url = f"{self.frames_url}?after_seq={int(after_seq)}&limit={int(limit)}"
        request = Request(url, headers={"Accept": "application/json"})
        timeout = max(1.0, float(timeout_seconds or settings.camera_gateway_worker_read_timeout_seconds))

        try:
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status >= 400:
                    return None
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            return None

        if not raw:
            return None

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        self._update_circuit_state(payload)
        return payload

    def _decode_frame(self, frame_item: dict) -> tuple[int, np.ndarray | None]:
        seq = int(frame_item.get("seq") or 0)
        encoded = frame_item.get("jpeg_base64")
        if seq <= 0 or not isinstance(encoded, str) or not encoded:
            return 0, None

        try:
            jpeg = base64.b64decode(encoded)
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            frame = None

        return seq, frame

    def _frame_is_fresh(self, frame_item: dict) -> bool:
        raw_value = frame_item.get("captured_at")
        if not raw_value:
            return False
        text = str(raw_value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        # Go serializes RFC3339Nano with up to nine fractional digits while
        # datetime.fromisoformat accepts at most microseconds on older Python.
        fraction_start = text.find(".")
        timezone_start = max(text.rfind("+"), text.rfind("-"))
        if fraction_start >= 0 and timezone_start > fraction_start:
            fraction = text[fraction_start + 1 : timezone_start]
            if len(fraction) > 6:
                text = text[: fraction_start + 1] + fraction[:6] + text[timezone_start:]
        try:
            captured_at = datetime.fromisoformat(text)
        except Exception:
            return False
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - captured_at.astimezone(timezone.utc)).total_seconds()
        max_age = max(1.0, float(settings.camera_gateway_recovery_fresh_frame_max_age_seconds))
        return 0.0 <= age_seconds <= max_age

    def _enqueue_frames(self, frames: list[dict]) -> None:
        if not frames:
            return

        for frame_item in frames:
            self._frame_queue.append(frame_item)

    def _pop_latest_queued_frame(self) -> tuple[bool, np.ndarray | None]:
        while self._frame_queue:
            frame_item = self._frame_queue.pop()
            seq, frame = self._decode_frame(frame_item)
            if frame is None or seq <= 0:
                continue

            self._last_seq = max(self._last_seq, seq)
            self.last_frame_metadata = {
                "camera_id": self.camera_id,
                "frame_id": seq,
                "generation_id": self.stream_generation_id or self.gateway_instance_id or None,
                "gateway_received_at": frame_item.get("captured_at"),
                "source_frame_captured_at_ns": None,
                "source_pts": None,
                "capture_clock": "gateway_receive_wall_clock",
            }
            return True, frame

        return False, None

    def read_latest(self, drop_frames: int = 2):
        if self.cap is None or not self._opened:
            raise RuntimeError("Gateway frames capture ainda nao foi aberto")

        self.last_read_soft_wait = False
        self.last_read_reason = None

        if self._frame_queue:
            ok, frame = self._pop_latest_queued_frame()
            if ok and frame is not None:
                return True, frame

        limit = max(1, min(5, int(drop_frames) + 1))
        payload = self._fetch_frames(after_seq=self._last_seq, limit=limit)
        if payload is None:
            self.consecutive_read_failures += 1
            self.last_read_reason = "gateway_request_failed"
            return False, None

        state = str(payload.get("state") or "").strip().lower()
        if self.circuit_state == "open":
            self.consecutive_read_failures += 1
            self.last_read_reason = "gateway_circuit_open"
            return False, None
        dropped = bool(payload.get("dropped"))
        latest_seq = int(payload.get("latest_seq") or 0)
        frames = payload.get("frames") if isinstance(payload, dict) else None

        if state in {"offline", "stopped_manual", "stopped"}:
            self.consecutive_read_failures += 1
            self.last_read_reason = f"gateway_state_{state or 'unknown'}"
            return False, None

        if dropped:
            self.logger.warning(
                "gateway_frames_context_gap camera_id=%s last_seq=%s latest_seq=%s state=%s",
                self.camera_id,
                self._last_seq,
                latest_seq,
                state or "unknown",
                extra={
                    "camera_id": self.camera_id,
                    "action": "gateway_frames_context_gap",
                    "status": state or "unknown",
                    "reason": "frame_ring_gap",
                },
            )
            self._frame_queue.clear()
            if latest_seq > 0:
                self._last_seq = latest_seq

        if not isinstance(frames, list) or len(frames) == 0:
            if state in {"running", "warming_up", "degraded", "reconnecting", "idle", "starting"}:
                self.last_read_soft_wait = True
                self.last_read_reason = "no_frame_ready"
                time.sleep(0.05)
                return False, None

            self.consecutive_read_failures += 1
            self.last_read_reason = "gateway_frames_missing"
            return False, None

        self._enqueue_frames(frames)
        ok, frame = self._pop_latest_queued_frame()
        if not ok or frame is None:
            if state in {"running", "warming_up", "degraded", "reconnecting", "idle", "starting"}:
                self.last_read_soft_wait = True
                self.last_read_reason = "frame_decode_pending"
                time.sleep(0.05)
                return False, None

            self.consecutive_read_failures += 1
            self.last_read_reason = "gateway_frame_decode_failed"
            return False, None

        if dropped and latest_seq > 0:
            self._last_seq = max(self._last_seq, latest_seq)

        if state in {"running", "warming_up"}:
            self.consecutive_read_failures = 0
            self.last_read_reason = None
        else:
            self.consecutive_read_failures += 1
            self.last_read_reason = f"gateway_state_{state or 'unknown'}"

        if frame is None:
            return False, None

        return True, frame

    def release(self):
        self.cap = None
        self._opened = False
        self._last_seq = 0
        self._frame_queue.clear()
        self.last_frame_metadata = {}

    def reopen_with_retry(self, retry_seconds: int = 5):
        self.release()
        time.sleep(max(0, retry_seconds))
        self.open()
