from app.services import webrtc_gateway_client as client
from app.services.webrtc_gateway_client import webrtc_display_source_url


def test_webrtc_display_source_prefers_hikvision_style_substream():
    source, reason = webrtc_display_source_url(
        "rtsp://admin:secret@192.168.1.20:554/Streaming/Channels/101"
    )

    assert source == "rtsp://admin:secret@192.168.1.20:554/Streaming/Channels/102"
    assert reason == "hikvision_style_main_to_substream"


def test_webrtc_display_source_preserves_query_string():
    source, reason = webrtc_display_source_url(
        "rtsp://admin:secret@192.168.1.20:554/Streaming/Channels/201?transport=tcp"
    )

    assert source == "rtsp://admin:secret@192.168.1.20:554/Streaming/Channels/202?transport=tcp"
    assert reason == "hikvision_style_main_to_substream"


def test_webrtc_display_source_keeps_substream_and_unknown_urls():
    sub_source, sub_reason = webrtc_display_source_url(
        "rtsp://admin:secret@192.168.1.20:554/Streaming/Channels/102"
    )
    custom_source, custom_reason = webrtc_display_source_url("rtsp://camera/live")

    assert sub_source == "rtsp://admin:secret@192.168.1.20:554/Streaming/Channels/102"
    assert sub_reason is None
    assert custom_source == "rtsp://camera/live"
    assert custom_reason is None


def test_player_url_uses_configured_https_public_base_without_8889(monkeypatch):
    monkeypatch.setattr(
        client.settings,
        "webrtc_gateway_public_base_url",
        "https://video.sunorus.com.br/",
    )

    assert client.build_webrtc_player_url("cam_36") == "https://video.sunorus.com.br/cam_36"
    assert ":8889" not in client.build_webrtc_player_url("cam_36")


def test_player_url_falls_back_to_public_marker_when_base_is_missing(monkeypatch):
    monkeypatch.setattr(client.settings, "webrtc_gateway_public_base_url", "")

    assert client.build_webrtc_player_url("cam_36") == "/__webrtc_public__/cam_36"


def test_camera_webrtc_path_remains_canonical():
    assert client.camera_webrtc_path_name(36) == "cam_36"


def test_register_generic_path_masks_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "webrtc_gateway_is_enabled", lambda: True)
    monkeypatch.setattr(client, "_json_request", lambda method, url, payload=None, timeout_seconds=None: (
        calls.append((method, url, payload)) or {
            "ok": True, "source": "rtsp://admin:secret@192.168.1.20:554/live",
        }
    ))

    result = client.register_webrtc_path(
        "sdk lab/abc", "rtsp://admin:secret@192.168.1.20:554/live"
    )

    assert result["ok"] is True
    assert result["path"] == "sdk_lab_abc"
    assert "secret" not in str(result)
    assert calls[0][0] == "POST"
    assert calls[0][2]["source"] == "rtsp://admin:secret@192.168.1.20:554/live"
    assert calls[0][2]["sourceOnDemand"] is True
    assert calls[0][2]["rtspTransport"] == "tcp"


def test_cleanup_generic_paths_only_removes_matching_prefix(monkeypatch):
    removed = []
    monkeypatch.setattr(client, "webrtc_gateway_is_enabled", lambda: True)
    monkeypatch.setattr(client, "_json_request", lambda *args, **kwargs: {
        "items": [{"name": "sdk_lab_one"}, {"name": "cam_4"}, {"name": "sdk_lab_two"}]
    })
    monkeypatch.setattr(client, "unregister_webrtc_path", lambda path: removed.append(path) or {"ok": True})

    count = client.cleanup_webrtc_paths("sdk_lab_")

    assert count == 2
    assert removed == ["sdk_lab_one", "sdk_lab_two"]
