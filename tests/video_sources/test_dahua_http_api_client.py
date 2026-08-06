import pytest

from app.video_sources.providers.dahua_http_api import (
    DahuaHttpApiClient,
    DahuaHttpApiError,
    _iso_to_device_time,
    _parse_event_line,
)


class FakeResponse:
    def __init__(self, *, status_code=200, text="", content=b"", lines=None):
        self.status_code = status_code
        self.text = text
        self.content = content
        self._lines = lines or []
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        self.closed = True


def test_iso_to_device_time_converts_zulu_and_naive_formats():
    assert _iso_to_device_time("2024-07-22T13:00:00Z") == "2024-07-22 13:00:00"
    assert _iso_to_device_time("2024-07-22T13:00:00") == "2024-07-22 13:00:00"


def test_iso_to_device_time_rejects_empty_or_invalid():
    with pytest.raises(DahuaHttpApiError):
        _iso_to_device_time("")
    with pytest.raises(DahuaHttpApiError):
        _iso_to_device_time("not-a-date")


def test_parse_event_line_extracts_fields_and_json_data():
    line = 'Code=FaceDetection;action=Start;index=0;data={"Faces": [{"Sex": "Man"}]}'
    event = _parse_event_line(line)
    assert event["code"] == "FaceDetection"
    assert event["action"] == "Start"
    assert event["index"] == "0"
    assert event["data"] == {"Faces": [{"Sex": "Man"}]}


def test_parse_event_line_keeps_raw_data_when_not_json():
    event = _parse_event_line("Code=Heartbeat;action=pulse;index=0;data=notjson")
    assert event["data"] == "notjson"


def test_get_serial_number_parses_key_value_body(monkeypatch):
    captured = {}

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        captured.update(url=url, params=params, timeout=timeout, stream=stream)
        return FakeResponse(text="sn=YZC0GZ05100020")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108", port=80, username="admin", password="secret")
    assert client.get_serial_number() == "YZC0GZ05100020"
    assert captured["url"] == "http://192.168.1.108:80/cgi-bin/magicBox.cgi"
    assert captured["params"] == {"action": "getSerialNo"}


def test_get_snapshot_returns_binary_body(monkeypatch):
    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        assert params == {"channel": 2, "type": 0}
        return FakeResponse(content=b"\xff\xd8fakejpeg")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108")
    assert client.get_snapshot(channel=2) == b"\xff\xd8fakejpeg"


def test_401_response_raises_dahua_http_api_error(monkeypatch):
    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        return FakeResponse(status_code=401)

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108", username="admin", password="wrong")
    with pytest.raises(DahuaHttpApiError, match="401"):
        client.get_serial_number()


def test_download_recording_sends_converted_times_and_dav_type(monkeypatch):
    captured = {}

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        captured.update(url=url, params=params)
        return FakeResponse(content=b"davdata")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108")
    result = client.download_recording(
        channel=1,
        start_iso="2024-07-22T10:00:00Z",
        end_iso="2024-07-22T10:05:00Z",
    )

    assert result == b"davdata"
    assert captured["params"] == {
        "action": "startLoad",
        "channel": 1,
        "startTime": "2024-07-22 10:00:00",
        "endTime": "2024-07-22 10:05:00",
        "subtype": 0,
        "Types": "dav",
    }


def test_ptz_move_sends_start_then_stop_with_direction_code(monkeypatch):
    calls = []

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        calls.append(dict(params))
        return FakeResponse(text="OK")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)
    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.time.sleep", lambda seconds: None)

    client = DahuaHttpApiClient(host="192.168.1.108")
    client.ptz_move(channel=1, pan=1, tilt=0, speed=5, duration_ms=200)

    assert len(calls) == 2
    assert calls[0] == {"action": "start", "channel": 1, "code": "Right", "arg1": 0, "arg2": 5, "arg3": 0}
    assert calls[1] == {"action": "stop", "channel": 1, "code": "Right", "arg1": 0, "arg2": 0, "arg3": 0}


def test_ptz_move_diagonal_uses_both_speed_args(monkeypatch):
    calls = []

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        calls.append(dict(params))
        return FakeResponse(text="OK")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)
    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.time.sleep", lambda seconds: None)

    client = DahuaHttpApiClient(host="192.168.1.108")
    client.ptz_move(channel=1, pan=-1, tilt=1, speed=6, duration_ms=100)

    assert calls[0] == {"action": "start", "channel": 1, "code": "LeftUp", "arg1": 6, "arg2": 6, "arg3": 0}


def test_ptz_move_zoom_takes_priority_over_pan_tilt(monkeypatch):
    calls = []

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        calls.append(dict(params))
        return FakeResponse(text="OK")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)
    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.time.sleep", lambda seconds: None)

    client = DahuaHttpApiClient(host="192.168.1.108")
    client.ptz_move(channel=1, pan=1, tilt=1, zoom=1, duration_ms=100)

    assert calls[0]["code"] == "ZoomTele"


def test_ptz_move_rejects_invalid_direction(monkeypatch):
    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        return FakeResponse(text="OK")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108")
    with pytest.raises(DahuaHttpApiError, match="invalida"):
        client.ptz_move(channel=1, pan=2, tilt=0)


def test_goto_preset_sends_preset_number_in_arg2(monkeypatch):
    calls = []

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        calls.append(dict(params))
        return FakeResponse(text="OK")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108")
    client.goto_preset(channel=2, preset_token="7")

    assert calls == [
        {
            "action": "start",
            "channel": 1,
            "code": "GotoPreset",
            "arg1": 0,
            "arg2": 7,
            "arg3": 0,
        }
    ]


def test_intelbras_get_presets_keeps_one_based_channel(monkeypatch):
    calls = []

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        calls.append(dict(params))
        return FakeResponse(
            text="\n".join(
                [
                    "presets[0].Index=1",
                    "presets[0].Name=Entrada",
                    "presets[1].Index=7",
                    "presets[1].Name=Patio",
                ]
            )
        )

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(
        host="192.168.1.108",
        preset_channel_one_based=True,
    )
    presets = client.get_presets(channel=6)

    assert calls == [{"action": "getPresets", "channel": 6}]
    assert presets == [
        {"token": "1", "name": "Entrada"},
        {"token": "7", "name": "Patio"},
    ]


def test_intelbras_goto_preset_keeps_one_based_channel(monkeypatch):
    calls = []

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        calls.append(dict(params))
        return FakeResponse(text="OK")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(
        host="192.168.1.108",
        preset_channel_one_based=True,
    )
    client.goto_preset(channel=6, preset_token="7")

    assert calls[0]["channel"] == 6
    assert calls[0]["code"] == "GotoPreset"
    assert calls[0]["arg2"] == 7


def test_goto_preset_rejects_non_numeric_token(monkeypatch):
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.requests.get",
        lambda *args, **kwargs: pytest.fail("Nao deve enviar comando invalido."),
    )

    client = DahuaHttpApiClient(host="192.168.1.108")

    with pytest.raises(DahuaHttpApiError, match="Numero do preset invalido"):
        client.goto_preset(channel=1, preset_token="entrada")


def test_ptz_action_raises_when_device_does_not_reply_ok(monkeypatch):
    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        return FakeResponse(text="Error")

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108")
    with pytest.raises(DahuaHttpApiError, match="PTZ start falhou"):
        client.ptz_move(channel=1, pan=0, tilt=1)


def test_subscribe_events_parses_multipart_lines(monkeypatch):
    lines = [
        "Code=VideoMotion;action=Start;index=0",
        "",
        'Code=FaceDetection;action=Start;index=0;data={"Faces": []}',
        "Heartbeat",
    ]

    def fake_get(url, params=None, auth=None, timeout=None, stream=False):
        assert stream is True
        assert params["action"] == "attach"
        assert params["codes"] == "[All]"
        return FakeResponse(lines=lines)

    monkeypatch.setattr("app.video_sources.providers.dahua_http_api.requests.get", fake_get)

    client = DahuaHttpApiClient(host="192.168.1.108")
    events = client.subscribe_events(listen_seconds=1.0)

    assert [event["code"] for event in events] == ["VideoMotion", "FaceDetection"]
    assert events[1]["data"] == {"Faces": []}
