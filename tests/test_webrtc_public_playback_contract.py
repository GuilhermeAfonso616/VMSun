from pathlib import Path


MONITOR_JS = Path("app/static/js/monitor_vms.js")


def test_https_monitor_never_fabricates_panel_host_port_8889():
    source = MONITOR_JS.read_text(encoding="utf-8")

    assert 'if (window.location.protocol === "http:")' in source
    assert 'return "http://" + window.location.hostname + ":8889";' in source
    assert 'parsed.port === "8889"' in source
    assert 'resolved = webrtcPublicBaseUrl + "/" + encodeURIComponent(path)' in source


def test_spotlight_and_mosaic_share_player_resolution_and_log_real_context():
    source = MONITOR_JS.read_text(encoding="utf-8")

    assert 'resolveWebrtcPlayerUrl(camera, "mosaico")' in source
    assert 'resolveWebrtcPlayerUrl(camera, "spotlight")' in source
    assert 'probeWebrtcPlayer(camera, resolved, "spotlight")' in source
    assert '" status=" + response.status' in source
    assert '" path=" + (cameraWebrtcPath(camera) || "unknown")' in source
    assert 'sanitizedPlayerUrl(spotlightUrl)' in source
    assert 'console.warn("Falha ao abrir camera do spotlight no mosaico", {' not in source


def test_local_helper_failure_is_optional_and_ptz_stop_is_best_effort():
    source = MONITOR_JS.read_text(encoding="utf-8")

    health_probe = source.split("function detectVideoHelper()", 1)[1].split(
        "window.sunorusDiagVideo", 1
    )[0]
    assert ".catch(function ()" in health_probe
    assert "videoHelperAvailable = false" in health_probe
    assert 'return { ok: false, error:' in source
    assert 'resolveWebrtcPlayerUrl(camera, "mosaico")' in source


def test_mediamtx_uses_only_explicit_ice_hosts_and_whep_ports():
    config = Path("webrtc-gateway/mediamtx.yml").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "webrtcAddress: :8889" in config
    assert "webrtcLocalUDPAddress: :8189" in config
    assert "webrtcLocalTCPAddress: :8189" in config
    assert "webrtcIPsFromInterfaces: no" in config
    assert "webrtcAdditionalHosts: []" in config
    assert '"${WEBRTC_GATEWAY_BIND_ADDRESS:-0.0.0.0}:8189:8189/udp"' in compose
    assert '"${WEBRTC_GATEWAY_BIND_ADDRESS:-0.0.0.0}:8189:8189/tcp"' in compose


def test_video_helper_cors_is_narrow_and_supports_public_sunorus_https():
    source = Path("operator-client/src/SunOrus.Video.Helper/Program.cs").read_text(
        encoding="utf-8"
    )

    assert 'uri.Scheme == Uri.UriSchemeHttps' in source
    assert 'uri.Host.Equals("sunorus.com.br"' in source
    assert 'uri.Host.EndsWith(".sunorus.com.br"' in source
    assert 'SUNORUS_VIDEO_HELPER_ALLOWED_ORIGINS' in source
    assert 'AccessControlAllowOrigin = "*"' not in source
