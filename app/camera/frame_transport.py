"""Selecao e compatibilidade do plano de dados Gateway -> worker."""

from __future__ import annotations

import time

import cv2
import numpy as np

from app.camera.gateway_frames_capture import GatewayFramesCapture
from app.camera.shared_frame_reader import (
    SharedFrameCorrupt,
    SharedFrameError,
    SharedFrameReader,
    SharedFrameUnavailable,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.services.camera_gateway_client import register_camera_source


VALID_MODES = {"http", "shared_memory_prefer", "shared_memory_strict"}


class FrameTransportStrictError(RuntimeError):
    pass


def frame_transport_mode() -> str:
    mode = str(settings.frame_transport_mode or "http").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            "FRAME_TRANSPORT_MODE deve ser http, shared_memory_prefer "
            "ou shared_memory_strict"
        )
    return mode


def frame_transport_selected(camera_id: int) -> bool:
    if frame_transport_mode() == "http":
        return False
    raw = str(settings.frame_transport_camera_ids or "").strip()
    if raw == "*":
        return True
    selected: set[int] = set()
    for item in raw.split(","):
        try:
            value = int(item.strip())
        except ValueError:
            continue
        if value > 0:
            selected.add(value)
    return int(camera_id) in selected


class SharedMemoryGatewayCapture(GatewayFramesCapture):
    def __init__(self, camera_id: int, source_url: str):
        super().__init__(camera_id, source_url)
        self.reader = SharedFrameReader(camera_id)
        self.mode = frame_transport_mode()
        self._http_delegate: GatewayFramesCapture | None = None
        self.http_fallback_total = 0
        self.transport_error_total = 0
        self.transport_mode_active = "shared_memory"
        self._logger = get_logger("app.shared_frame_capture")
        self._last_error_log_at = 0.0
        self._last_shared_probe_at = 0.0

    def _remember_packet(self, packet) -> None:
        self.last_frame_metadata = {
            "camera_id": int(packet.camera_id),
            "frame_id": int(packet.frame_id),
            "generation_id": int(packet.generation_id),
            "gateway_received_at_ns": int(packet.captured_at_wall_ns or 0) or None,
            "gateway_published_at_monotonic_ns": int(
                packet.published_at_monotonic_ns or 0
            )
            or None,
            "gateway_frame_age_ms": round(float(packet.frame_age_ms), 3),
            "source_frame_captured_at_ns": None,
            "source_pts": None,
            "capture_clock": "gateway_receive_wall_clock",
        }

    def _activate_http_fallback(self, reason: str) -> None:
        if self.mode != "shared_memory_prefer" or not bool(
            settings.frame_transport_fallback_enabled
        ):
            raise FrameTransportStrictError(
                f"shared memory transport unavailable: {reason}"
            )
        if self._http_delegate is None:
            self._http_delegate = GatewayFramesCapture(
                self.camera_id, self.source_url
            )
        self._http_delegate.open()
        self.cap = self._http_delegate.cap
        self._opened = True
        self.http_fallback_total += 1
        self.transport_mode_active = "http_fallback"
        self._logger.warning(
            "Shared frame buffer unavailable; explicit HTTP fallback activated "
            "camera_id=%s reason=%s fallback_total=%s",
            self.camera_id,
            reason,
            self.http_fallback_total,
            extra={
                "camera_id": self.camera_id,
                "action": "frame_transport_fallback",
                "status": "degraded",
                "reason": reason,
            },
        )

    def open(self):
        self.release()
        registered = register_camera_source(
            self.camera_id,
            self.source_url,
            timeout_seconds=max(
                1.0, float(settings.camera_gateway_register_timeout_seconds)
            ),
        )
        if not registered:
            self.transport_error_total += 1
            self._activate_http_fallback("gateway_registration_failed")
            return

        timeout = max(
            0.1, float(settings.camera_gateway_first_frame_timeout_seconds)
        )
        deadline = time.monotonic() + timeout
        last_error = "buffer_not_ready"
        while time.monotonic() < deadline:
            try:
                packet = self.reader.read_latest(
                    timeout=min(
                        0.25,
                        max(0.001, deadline - time.monotonic()),
                    )
                )
                if packet is not None:
                    self._remember_packet(packet)
                    frame = cv2.imdecode(
                        np.frombuffer(packet.payload, dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    if frame is None:
                        raise SharedFrameCorrupt("JPEG compartilhado invalido")
                    self._last_seq = packet.frame_id - 1
                    self._opened = True
                    self.cap = object()
                    self.transport_mode_active = "shared_memory"
                    self.consecutive_read_failures = 0
                    self.last_read_reason = None
                    self._frame_queue.clear()
                    self._logger.info(
                        "Worker connected to shared frame buffer camera_id=%s "
                        "generation=%s frame_id=%s",
                        self.camera_id,
                        packet.generation_id,
                        packet.frame_id,
                        extra={
                            "camera_id": self.camera_id,
                            "action": "frame_transport_connected",
                            "status": "running",
                            "reason": "shared_buffer_ready",
                        },
                    )
                    return
            except SharedFrameError as exc:
                last_error = exc.code
                time.sleep(max(0.001, settings.frame_transport_poll_interval_ms / 1000.0))
        self.transport_error_total += 1
        self._activate_http_fallback(last_error)

    def read_latest(self, drop_frames: int = 2):
        if self._http_delegate is not None:
            recovered = self._try_restore_shared()
            if recovered is not None:
                return recovered
            result = self._http_delegate.read_latest(drop_frames=drop_frames)
            self._sync_delegate_state()
            return result
        if self.cap is None or not self._opened:
            raise RuntimeError("Shared memory capture ainda nao foi aberto")
        self.last_read_soft_wait = False
        self.last_read_reason = None
        try:
            packet = self.reader.read_latest(
                timeout=max(
                    0.01, float(settings.frame_transport_read_timeout_ms) / 1000.0
                )
            )
        except SharedFrameUnavailable as exc:
            self.transport_error_total += 1
            if self.mode == "shared_memory_prefer" and bool(
                settings.frame_transport_fallback_enabled
            ):
                self._activate_http_fallback(exc.code)
                result = self._http_delegate.read_latest(drop_frames=drop_frames)
                self._sync_delegate_state()
                return result
            self.consecutive_read_failures += 1
            self.last_read_reason = "shared_buffer_unavailable"
            return False, None
        except SharedFrameError as exc:
            self.transport_error_total += 1
            self.consecutive_read_failures += 1
            self.last_read_reason = exc.code
            now = time.monotonic()
            if now - self._last_error_log_at >= 5.0:
                self._last_error_log_at = now
                self._logger.warning(
                    "Shared frame rejected camera_id=%s reason=%s",
                    self.camera_id,
                    exc.code,
                    extra={
                        "camera_id": self.camera_id,
                        "action": "frame_transport_read",
                        "status": "degraded",
                        "reason": exc.code,
                    },
                )
            return False, None
        if packet is None:
            self.last_read_soft_wait = True
            self.last_read_reason = "no_frame_ready"
            return False, None
        self._remember_packet(packet)
        frame = cv2.imdecode(
            np.frombuffer(packet.payload, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            self.reader.corrupt_frames_total += 1
            self.consecutive_read_failures += 1
            self.last_read_reason = "shared_frame_decode_failed"
            return False, None
        self._last_seq = packet.frame_id
        self.consecutive_read_failures = 0
        return True, frame

    def _try_restore_shared(self):
        now = time.monotonic()
        if now - self._last_shared_probe_at < 2.0:
            return None
        self._last_shared_probe_at = now
        try:
            packet = self.reader.read_latest(timeout=0.0)
        except SharedFrameError:
            return None
        if packet is None:
            return None
        self._remember_packet(packet)
        frame = cv2.imdecode(
            np.frombuffer(packet.payload, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            self.reader.corrupt_frames_total += 1
            return None
        self._http_delegate.release()
        self._http_delegate = None
        self.cap = object()
        self._opened = True
        self._last_seq = packet.frame_id
        self.transport_mode_active = "shared_memory"
        self.consecutive_read_failures = 0
        self.last_read_reason = None
        self._logger.info(
            "Shared frame transport recovered camera_id=%s generation=%s",
            self.camera_id,
            packet.generation_id,
            extra={
                "camera_id": self.camera_id,
                "action": "frame_transport_recovered",
                "status": "running",
                "reason": "shared_buffer_restored",
            },
        )
        return True, frame

    def _sync_delegate_state(self) -> None:
        if self._http_delegate is None:
            return
        for name in (
            "consecutive_read_failures",
            "last_read_soft_wait",
            "last_read_reason",
            "circuit_state",
            "circuit_reason",
            "circuit_open_until",
            "circuit_retry_after_ms",
            "gateway_instance_id",
            "failure_epoch",
        ):
            setattr(self, name, getattr(self._http_delegate, name))

    def open_from_fresh_status(
        self, timeout_seconds: float | None = None, *, strict: bool = False
    ) -> bool:
        if self._http_delegate is not None:
            result = self._http_delegate.open_from_fresh_status(
                timeout_seconds=timeout_seconds, strict=strict
            )
            self._sync_delegate_state()
            return result
        try:
            packet = self.reader.read_latest(timeout=max(0.0, timeout_seconds or 0.0))
        except SharedFrameError:
            return False
        if packet is None:
            return False
        self._last_seq = packet.frame_id - 1
        self._opened = True
        self.cap = object()
        return True

    def transport_metrics(self) -> dict:
        metrics = self.reader.metrics()
        metrics.update(
            {
                "frame_transport_mode": self.transport_mode_active,
                "frame_transport_http_fallback_total": self.http_fallback_total,
                "frame_transport_errors_total": self.transport_error_total,
            }
        )
        return metrics

    def release(self):
        if self._http_delegate is not None:
            self._http_delegate.release()
        self._http_delegate = None
        self.reader.close()
        super().release()


def build_gateway_frame_capture(
    camera_id: int, source_url: str
) -> GatewayFramesCapture:
    if frame_transport_selected(camera_id):
        return SharedMemoryGatewayCapture(camera_id, source_url)
    return GatewayFramesCapture(camera_id, source_url)
