from app.video_sources.models import VideoSourceConfig
from app.video_sources.providers.dahua_http_api import DahuaHttpApiError
from app.video_sources.providers.sdk import IntelbrasProvider
from app.video_sources.registry import build_video_source_provider


def _provider(**overrides):
    config = VideoSourceConfig(
        provider_type="intelbras_sdk",
        host="192.168.10.20",
        username="admin",
        password="secret",
        brand="intelbras",
        channel_count=4,
        http_port=8080,
        **overrides,
    )
    return build_video_source_provider(config)


def test_registry_builds_intelbras_http_provider():
    provider = _provider()
    assert isinstance(provider, IntelbrasProvider)
    assert provider.capabilities.supports_snapshot is True
    assert provider.capabilities.supports_recording_clip is True
    assert provider.capabilities.supports_native_events is True
    assert provider.capabilities.supports_ptz is True
    assert provider.capabilities.requires_sdk is False


def test_client_uses_configured_http_port():
    provider = _provider()
    client = provider._client()
    assert client._base_url == "http://192.168.10.20:8080"


def test_get_health_reports_ok_with_serial_number(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_serial_number",
        lambda self: "YZC0GZ05100020",
    )

    health = provider.get_health()

    assert health.ok is True
    assert health.metadata["serial_number"] == "YZC0GZ05100020"


def test_get_health_reports_error_on_bad_credentials(monkeypatch):
    provider = _provider()

    def raise_error(self):
        raise DahuaHttpApiError("Credenciais rejeitadas pelo dispositivo (401).")

    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_serial_number",
        raise_error,
    )

    health = provider.get_health()

    assert health.ok is False
    assert health.status == "http_api_error"
    assert "401" in health.reason


def test_get_snapshot_returns_none_on_failure(monkeypatch):
    provider = _provider()

    def raise_error(self, *, channel):
        raise DahuaHttpApiError("boom")

    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_snapshot",
        raise_error,
    )

    assert provider.get_snapshot(channel=1) is None


def test_get_snapshot_returns_bytes_on_success(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_snapshot",
        lambda self, *, channel: b"jpeg-bytes",
    )

    assert provider.get_snapshot(channel=2) == b"jpeg-bytes"


def test_get_native_events_returns_parsed_list(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.subscribe_events",
        lambda self, *, channel=None: [{"code": "VideoMotion", "action": "Start"}],
    )

    events = provider.get_native_events(channel=1)

    assert events == [{"code": "VideoMotion", "action": "Start"}]


def test_move_ptz_returns_true_and_clamps_out_of_range_values(monkeypatch):
    provider = _provider()
    captured = {}

    def fake_ptz_move(self, *, channel, pan, tilt, zoom, speed, duration_ms):
        captured.update(channel=channel, pan=pan, tilt=tilt, zoom=zoom, speed=speed, duration_ms=duration_ms)

    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.ptz_move",
        fake_ptz_move,
    )

    result = provider.move_ptz(channel=2, pan=9, tilt=-9, zoom=0, speed=99, duration_ms=9999)

    assert result is True
    assert captured == {"channel": 2, "pan": 1, "tilt": -1, "zoom": 0, "speed": 8, "duration_ms": 800}


def test_move_ptz_returns_false_on_device_error(monkeypatch):
    provider = _provider()

    def raise_error(self, *, channel, pan, tilt, zoom, speed, duration_ms):
        raise DahuaHttpApiError("PTZ start falhou")

    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.ptz_move",
        raise_error,
    )

    assert provider.move_ptz(channel=1, pan=1, tilt=0) is False
