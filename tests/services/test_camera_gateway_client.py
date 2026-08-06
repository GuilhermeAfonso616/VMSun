import pytest

from app.services import camera_gateway_client
from app.services.media_backbone_service import MediaBackboneUnavailable, MediaPathResult


def _mode(monkeypatch, mode: str, canary: str = ""):
    monkeypatch.setattr(camera_gateway_client.settings, "camera_gateway_source_mode", mode)
    monkeypatch.setattr(camera_gateway_client.settings, "camera_gateway_mediamtx_camera_ids", canary)


def test_webrtc_rtsp_source_url_uses_configured_internal_base(monkeypatch):
    monkeypatch.setattr(camera_gateway_client.settings, "webrtc_gateway_rtsp_base_url", "rtsp://media:8554/")
    assert camera_gateway_client.webrtc_rtsp_source_url(11) == "rtsp://media:8554/cam_11"


def test_direct_returns_original_source(monkeypatch):
    source = "rtsp://admin:pass@10.0.2.15:554/live"
    _mode(monkeypatch, "direct")
    assert camera_gateway_client.resolve_camera_gateway_source_url(12, source) == source


def test_prefer_returns_internal_when_path_is_registered(monkeypatch):
    _mode(monkeypatch, "mediamtx_prefer")
    monkeypatch.setattr(camera_gateway_client, "ensure_camera_media_path", lambda *_: MediaPathResult(True, 12, "cam_12", "rtsp://webrtc-gateway:8554/cam_12"))
    assert camera_gateway_client.resolve_camera_gateway_source_url(12, "rtsp://x/live") == "rtsp://webrtc-gateway:8554/cam_12"


def test_prefer_fallback_is_explicit(monkeypatch, caplog):
    source = "rtsp://admin:pass@10.0.2.15:554/live"
    _mode(monkeypatch, "mediamtx_prefer")
    monkeypatch.setattr(camera_gateway_client, "ensure_camera_media_path", lambda *_: MediaPathResult(False, 12, "cam_12", None, error_code="media_path_registration_failed"))
    assert camera_gateway_client.resolve_camera_gateway_source_url(12, source) == source
    assert "direct rtsp fallback" in caplog.text.lower()
    assert "pass" not in caplog.text


def test_strict_never_returns_direct_source(monkeypatch):
    _mode(monkeypatch, "mediamtx_strict")
    monkeypatch.setattr(camera_gateway_client, "ensure_camera_media_path", lambda *_: MediaPathResult(False, 12, "cam_12", None, error_code="media_backbone_unavailable"))
    with pytest.raises(MediaBackboneUnavailable) as exc:
        camera_gateway_client.resolve_camera_gateway_source_url(12, "rtsp://admin:pass@10.0.2.15/live")
    assert exc.value.code == "media_backbone_unavailable"


def test_canary_keeps_unselected_camera_direct(monkeypatch):
    source = "rtsp://10.0.2.15/live"
    _mode(monkeypatch, "mediamtx_strict", "67,68")
    assert camera_gateway_client.resolve_camera_gateway_source_url(12, source) == source


def test_fetch_gateway_cameras_filters_invalid_entries(monkeypatch):
    monkeypatch.setattr(camera_gateway_client.settings, "camera_gateway_enabled", True)
    monkeypatch.setattr(camera_gateway_client.settings, "camera_gateway_base_url", "http://gateway:8090")
    monkeypatch.setattr(camera_gateway_client, "_fetch_json", lambda *_args, **_kwargs: {"cameras": [{"camera_id": 41, "state": "running"}, "invalid", None]})
    assert camera_gateway_client.fetch_gateway_cameras() == [{"camera_id": 41, "state": "running"}]
