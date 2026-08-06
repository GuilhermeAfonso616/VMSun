from __future__ import annotations

import pytest

from app.camera import gateway_frames_capture as gateway_capture_module
from app.camera.gateway_frames_capture import GatewayCircuitOpenError, GatewayFramesCapture
from app.core.config import settings
from app.runtime.capture import CameraCaptureService


def _open_circuit_payload() -> dict:
    return {
        "camera_id": 41,
        "state": "queued",
        "circuit_open": True,
        "circuit_state": "open",
        "circuit_reason": "4_reconnects_in_2m0s",
        "circuit_open_until": "2026-07-20T15:00:00Z",
        "retry_after_ms": 45000,
        "gateway_instance_id": "gateway-test",
        "stream_generation_id": "gateway-test:41:8",
        "failure_epoch": 3,
    }


def test_gateway_capture_respects_open_circuit_before_registering_source(monkeypatch):
    registrations: list[tuple[int, str]] = []
    monkeypatch.setattr(
        gateway_capture_module,
        "fetch_camera_status",
        lambda camera_id, source_url, timeout_seconds=None: _open_circuit_payload(),
    )
    monkeypatch.setattr(
        gateway_capture_module,
        "register_camera_source",
        lambda camera_id, source_url, timeout_seconds=None: registrations.append((camera_id, source_url)),
    )

    capture = GatewayFramesCapture(41, "rtsp://camera/41")
    with pytest.raises(GatewayCircuitOpenError) as error:
        capture.open()

    assert error.value.retry_after_ms == 45000
    assert capture.circuit_state == "open"
    assert capture.gateway_instance_id == "gateway-test"
    assert capture.stream_generation_id == "gateway-test:41:8"
    assert capture.failure_epoch == 3
    assert registrations == []


def test_capture_service_releases_pool_only_after_circuit_grace(monkeypatch):
    monkeypatch.setattr(settings, "camera_gateway_enabled", True)
    monkeypatch.setattr(settings, "camera_gateway_worker_capture_enabled", True)
    monkeypatch.setattr(settings, "camera_gateway_circuit_park_after_seconds", 120.0)

    service = CameraCaptureService("rtsp://camera/41", camera_id=41)
    assert isinstance(service.capture, GatewayFramesCapture)
    service.capture._update_circuit_state(_open_circuit_payload())

    service.capture._circuit_open_since_monotonic -= 119.0
    assert service.inference_pool_release_due() is False

    service.capture._circuit_open_since_monotonic -= 2.0
    assert service.inference_pool_release_due() is True

    service.capture._update_circuit_state({"circuit_state": "half_open", "circuit_open": False})
    assert service.inference_pool_release_due() is True

    service.capture._update_circuit_state({"circuit_state": "closed", "circuit_open": False})
    assert service.inference_pool_release_due() is False
