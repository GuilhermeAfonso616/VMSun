import pytest

from app.camera.rtsp_capture import RTSPCapture
from app.camera.rtsp_discovery import probe_rtsp_candidates, probe_rtsp_url_details
from app.core.config import settings
from app.core.url_safety import mask_url_credentials, sanitize_url_for_log


def _enable_gateway_only_mode(monkeypatch):
    monkeypatch.setattr(settings, "camera_gateway_enabled", True)
    monkeypatch.setattr(settings, "camera_gateway_worker_capture_enabled", True)
    monkeypatch.setattr(settings, "camera_gateway_worker_rtsp_fallback_enabled", False)


def test_runtime_capture_remains_blocked_in_gateway_only_mode(monkeypatch):
    _enable_gateway_only_mode(monkeypatch)

    with pytest.raises(RuntimeError, match="Direct RTSP capture is disabled"):
        RTSPCapture("rtsp://camera.example/stream").open()


def test_internal_media_backbone_capture_is_allowed_in_gateway_only_mode(monkeypatch):
    _enable_gateway_only_mode(monkeypatch)
    monkeypatch.setattr(
        settings,
        "webrtc_gateway_rtsp_base_url",
        "rtsp://webrtc-gateway:8554",
    )
    opened = []

    def fake_open_once(self, transport):
        opened.append((self.rtsp_url, transport))
        return True

    monkeypatch.setattr(RTSPCapture, "_open_once", fake_open_once)

    capture = RTSPCapture("rtsp://webrtc-gateway:8554/cam_37")
    capture.open()

    assert opened == [("rtsp://webrtc-gateway:8554/cam_37", "tcp")]


def test_explicit_probe_can_open_in_gateway_only_mode(monkeypatch):
    _enable_gateway_only_mode(monkeypatch)
    opened = []

    def fake_open_once(self, transport):
        opened.append((self.rtsp_url, transport))
        return True

    monkeypatch.setattr(RTSPCapture, "_open_once", fake_open_once)

    capture = RTSPCapture(
        "rtsp://camera.example/stream",
        allow_gateway_exclusive_probe=True,
    )
    capture.open()

    assert opened == [("rtsp://camera.example/stream", "tcp")]


def test_probe_can_disable_transport_fallback(monkeypatch):
    _enable_gateway_only_mode(monkeypatch)
    opened = []

    def fake_open_once(self, transport):
        opened.append(transport)
        return False

    monkeypatch.setattr(RTSPCapture, "_open_once", fake_open_once)

    capture = RTSPCapture(
        "rtsp://camera.example/stream",
        allow_gateway_exclusive_probe=True,
        allow_transport_fallback=False,
    )
    with pytest.raises(RuntimeError, match="transporte=tcp"):
        capture.open()

    assert opened == ["tcp"]


def test_discovery_marks_probe_as_gateway_exclusive_safe(monkeypatch):
    observed = {}

    class FakeCapture:
        def __init__(self, rtsp_url, *, allow_gateway_exclusive_probe=False):
            observed["rtsp_url"] = rtsp_url
            observed["allow_gateway_exclusive_probe"] = allow_gateway_exclusive_probe
            self.cap = None

        def open(self):
            return None

        def read_latest(self, drop_frames=0):
            observed["drop_frames"] = drop_frames
            return True, type("Frame", (), {"shape": (720, 1280, 3)})()

        def release(self):
            return None

    monkeypatch.setattr("app.camera.rtsp_discovery.RTSPCapture", FakeCapture)

    result = probe_rtsp_url_details("rtsp://camera.example/stream")

    assert result["ok"] is True
    assert result["width"] == 1280
    assert result["height"] == 720
    assert observed == {
        "rtsp_url": "rtsp://camera.example/stream",
        "allow_gateway_exclusive_probe": True,
        "drop_frames": 0,
    }


def test_parallel_candidate_probe_preserves_input_order(monkeypatch):
    def fake_probe(url, *, allow_transport_fallback=True):
        return {
            "ok": url.endswith("2"),
            "error": "" if url.endswith("2") else "fail",
            "width": None,
            "height": None,
            "fps": None,
        }

    monkeypatch.setattr("app.camera.rtsp_discovery.probe_rtsp_url_details", fake_probe)

    results = probe_rtsp_candidates(
        ["rtsp://camera/1", "rtsp://camera/2", "rtsp://camera/3"],
        max_workers=3,
        allow_transport_fallback=False,
    )

    assert [item["url"] for item in results] == [
        "rtsp://camera/1",
        "rtsp://camera/2",
        "rtsp://camera/3",
    ]
    assert [item["ok"] for item in results] == [False, True, False]


def test_rtsp_mask_does_not_invent_a_password():
    assert mask_url_credentials("rtsp://admin@camera.example/stream") == (
        "rtsp://admin@camera.example/stream"
    )
    assert mask_url_credentials("rtsp://admin:secret@camera.example/stream") == (
        "rtsp://admin:***@camera.example/stream"
    )


def test_rtsp_log_sanitization_removes_all_userinfo():
    sanitized = sanitize_url_for_log(
        "rtsp://operator:private@camera.example:8554/live?password=query-secret"
    )

    assert sanitized == "rtsp://camera.example:8554/live?password=***"
    assert "operator" not in sanitized
    assert "private" not in sanitized
    assert "query-secret" not in sanitized
