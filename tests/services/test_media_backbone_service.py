from app.services import media_backbone_service, webrtc_gateway_client


def test_canonical_registration_preserves_hikvision_main_stream(monkeypatch):
    payloads = []
    monkeypatch.setattr(webrtc_gateway_client, "webrtc_gateway_is_enabled", lambda: True)
    monkeypatch.setattr(webrtc_gateway_client.settings, "webrtc_gateway_register_timeout_seconds", 1)
    monkeypatch.setattr(webrtc_gateway_client, "_json_request", lambda _method, _url, payload=None, *_args: payloads.append(payload) or {"ok": True})
    webrtc_gateway_client.invalidate_webrtc_camera_path_cache(67)
    source = "rtsp://admin:secret@nvr/Streaming/Channels/101"
    result = webrtc_gateway_client.register_webrtc_camera_path(67, source)
    assert result["ok"]
    assert payloads[0]["source"] == source


def test_cached_registration_is_recreated_after_mediamtx_restart(monkeypatch):
    requests = []

    def fake_request(method, url, payload=None, timeout_seconds=None):
        requests.append((method, url, payload))
        if method == "GET":
            return {"ok": False, "status": 404}
        return {"ok": True}

    monkeypatch.setattr(webrtc_gateway_client, "webrtc_gateway_is_enabled", lambda: True)
    monkeypatch.setattr(webrtc_gateway_client.settings, "webrtc_gateway_register_timeout_seconds", 1)
    monkeypatch.setattr(webrtc_gateway_client, "_json_request", fake_request)
    webrtc_gateway_client.invalidate_webrtc_camera_path_cache(36)
    source = "rtsp://origin/sub"

    assert webrtc_gateway_client.register_webrtc_camera_path(36, source)["ok"]
    requests.clear()
    result = webrtc_gateway_client.register_webrtc_camera_path(36, source)

    assert result["ok"] is True
    assert result.get("cached") is not True
    assert [request[0] for request in requests] == ["GET", "POST"]


def test_cached_registration_is_reused_when_path_still_exists(monkeypatch):
    requests = []

    def fake_request(method, url, payload=None, timeout_seconds=None):
        requests.append((method, url, payload))
        if method == "GET":
            return {"name": "cam_36", "source": "redacted"}
        return {"ok": True}

    monkeypatch.setattr(webrtc_gateway_client, "webrtc_gateway_is_enabled", lambda: True)
    monkeypatch.setattr(webrtc_gateway_client.settings, "webrtc_gateway_register_timeout_seconds", 1)
    monkeypatch.setattr(webrtc_gateway_client, "_json_request", fake_request)
    webrtc_gateway_client.invalidate_webrtc_camera_path_cache(36)
    source = "rtsp://origin/sub"

    assert webrtc_gateway_client.register_webrtc_camera_path(36, source)["ok"]
    requests.clear()
    result = webrtc_gateway_client.register_webrtc_camera_path(36, source)

    assert result["ok"] is True
    assert result["cached"] is True
    assert [request[0] for request in requests] == ["GET"]


def test_canary_star_selects_all(monkeypatch):
    monkeypatch.setattr(media_backbone_service.settings, "camera_gateway_mediamtx_camera_ids", "*")
    assert media_backbone_service.media_backbone_selected_for_camera(1)


def test_invalid_mode_is_rejected(monkeypatch):
    monkeypatch.setattr(media_backbone_service.settings, "camera_gateway_source_mode", "unknown")
    try:
        media_backbone_service.camera_gateway_source_mode()
    except ValueError:
        return
    raise AssertionError("invalid mode should fail clearly")
