from pathlib import Path

from app.services import device_auto_detection as detection


def test_auto_detection_prefers_confirmed_dahua_rtsp(monkeypatch):
    monkeypatch.setattr(
        detection,
        "probe_common_ports",
        lambda *args, **kwargs: {80: True, 443: False, 554: True, 8000: False, 8899: False, 37777: True},
    )
    monkeypatch.setattr(
        detection,
        "_quick_rtsp_test",
        lambda **kwargs: {
            "tested": True,
            "ok": True,
            "brand": "dahua",
            "error": "",
            "attempts": [{"brand": "dahua", "ok": True}],
        },
    )

    result = detection.detect_common_video_device(
        host="camera.example",
        username="admin",
        password="secret",
    )

    assert result["recommendation"] == {
        "brand": "dahua",
        "provider_type": "generic_nvr",
        "rtsp_port": 554,
        "onvif_port": None,
        "confidence": "high",
        "ready": True,
        "reason": "Frame RTSP aberto com o template dahua.",
    }
    assert result["open_ports"] == [80, 554, 37777]


def test_auto_detection_reports_native_sdk_as_pending(monkeypatch):
    monkeypatch.setattr(
        detection,
        "probe_common_ports",
        lambda *args, **kwargs: {80: False, 443: False, 554: False, 8000: False, 8899: False, 37777: True},
    )
    monkeypatch.setattr(detection, "_native_gateway_ready", lambda provider_type: False)

    result = detection.detect_common_video_device(
        host="camera.example",
        username="admin",
        password="secret",
    )

    recommendation = result["recommendation"]
    assert recommendation["provider_type"] == "dahua_sdk"
    assert recommendation["sdk_port"] == 37777
    assert recommendation["ready"] is False
    assert "ainda nao esta instalado" in recommendation["reason"]
    assert result["rtsp"]["tested"] is False


def test_quick_rtsp_detection_stops_after_authentication_failure(monkeypatch):
    attempts = []

    def fake_probe(url, **kwargs):
        attempts.append(url)
        return {
            "ok": False,
            "error": "NVR recusou o usuario ou a senha (401 Unauthorized)",
            "timed_out": False,
        }

    monkeypatch.setattr(detection, "probe_rtsp_url_details_bounded", fake_probe)

    result = detection._quick_rtsp_test(
        host="camera.example",
        port=554,
        username="admin",
        password="wrong",
        open_ports={37777, 554},
        timeout_seconds=5,
    )

    assert result["ok"] is False
    assert result["authentication_failed"] is True
    assert len(attempts) == 1


def test_auto_detection_prioritizes_authentication_error_over_sdk_hint(monkeypatch):
    monkeypatch.setattr(
        detection,
        "probe_common_ports",
        lambda *args, **kwargs: {80: True, 443: False, 554: True, 8000: False, 8899: False, 37777: True},
    )
    monkeypatch.setattr(
        detection,
        "_quick_rtsp_test",
        lambda **kwargs: {
            "tested": True,
            "ok": False,
            "brand": "dahua",
            "authentication_failed": True,
            "error": "401 Unauthorized",
            "attempts": [{"brand": "dahua", "ok": False}],
        },
    )

    result = detection.detect_common_video_device(
        host="camera.example",
        username="admin",
        password="wrong",
    )

    recommendation = result["recommendation"]
    assert recommendation["brand"] == "dahua"
    assert recommendation["provider_type"] == "generic_nvr"
    assert recommendation["ready"] is False
    assert "nenhuma nova tentativa" in recommendation["reason"]


def test_nvr_template_exposes_automatic_detection_controls():
    template = Path("templates/nvr_sources.html").read_text(encoding="utf-8")

    assert "/video-sources/nvr/detect" in template
    assert 'id="nvrAutoDetectButton"' in template
    assert 'id="nvrAutoDetectionResult"' in template
    assert "Testando portas e um unico stream" in template
    assert "recommendation.ready" in template
