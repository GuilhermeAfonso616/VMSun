import json
import time

import pytest

from app.services import hik_sdk_lab_service as service


@pytest.fixture(autouse=True)
def clear_sessions():
    service._sessions.clear()
    service._orphan_cleanup_done = False
    yield
    service._sessions.clear()
    service._orphan_cleanup_done = False


def test_connect_keeps_secret_private_and_scopes_session_to_owner(monkeypatch):
    monkeypatch.setattr(service, "_run_worker", lambda action, payload, **kwargs: {
        "ok": True, "device": {"serial_number": "ABC", "digital_channels": 8}
    })
    public = service.connect_device(
        owner_id=10, label="NVR Teste", device_type="nvr", host="192.168.1.20",
        port=8000, username="admin", password="segredo", channel=2,
    )

    assert public["device"]["serial_number"] == "ABC"
    assert "password" not in public
    assert "username" not in public
    assert service.get_session(public["token"], 10).password == "segredo"
    with pytest.raises(service.HikSdkLabError, match="inexistente ou expirada"):
        service.get_session(public["token"], 11)


def test_connect_dispatches_to_dahua_worker_and_exposes_only_brand(monkeypatch):
    captured = {}

    def worker(action, payload, **kwargs):
        captured.update(action=action, manufacturer=kwargs.get("manufacturer"), payload=payload)
        return {"ok": True, "device": {"serial_number": "DH-01", "digital_channels": 16}}

    monkeypatch.setattr(service, "_run_worker", worker)
    public = service.connect_device(
        owner_id=12, label="Dahua", device_type="nvr", manufacturer="dahua",
        host="192.168.1.40", port=37777, username="admin", password="secret", channel=1,
    )

    assert captured["action"] == "inspect"
    assert captured["manufacturer"] == "dahua"
    assert public["manufacturer"] == "dahua"
    assert "password" not in public


def test_connect_dispatches_intelbras_to_http_api_client(monkeypatch):
    captured = {}

    def fake_get_serial_number(self):
        captured.update(host=self._base_url)
        return "INT-01"

    monkeypatch.setattr(service, "_run_worker", lambda *a, **k: pytest.fail("nao deveria chamar o worker nativo"))
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_serial_number",
        fake_get_serial_number,
    )
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_presets",
        lambda self, *, channel: [],
    )

    public = service.connect_device(
        owner_id=13, label="Intelbras", device_type="nvr", manufacturer="intelbras",
        host="192.168.1.50", port=80, username="admin", password="secret", channel=1,
    )

    assert captured["host"] == "http://192.168.1.50:80"
    assert public["manufacturer"] == "intelbras"
    assert public["device"]["serial_number"] == "INT-01"
    assert public["device"]["ptz_capable"] is True
    assert "password" not in public


def test_intelbras_http_client_uses_one_based_preset_channels(monkeypatch):
    captured = {}
    expected_client = object()

    def fake_client(**kwargs):
        captured.update(kwargs)
        return expected_client

    monkeypatch.setattr(service, "DahuaHttpApiClient", fake_client)

    client = service._intelbras_client(
        host="192.168.1.50",
        port=80,
        username="admin",
        password="secret",
    )

    assert client is expected_client
    assert captured["preset_channel_one_based"] is True


def test_connect_intelbras_wraps_http_errors(monkeypatch):
    def raise_error(self):
        raise service.DahuaHttpApiError("401")

    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_serial_number",
        raise_error,
    )

    with pytest.raises(service.HikSdkLabError, match="API HTTP Intelbras"):
        service.connect_device(
            owner_id=13, label="Intelbras", device_type="nvr", manufacturer="intelbras",
            host="192.168.1.50", port=80, username="admin", password="wrong", channel=1,
        )


def test_intelbras_move_ptz_uses_http_client_with_bounded_values(monkeypatch):
    now = time.time()
    service._sessions["intelbras-ptz"] = service.HikSdkLabSession(
        token="intelbras-ptz", owner_id=21, label="cam", device_type="camera", host="10.0.0.6",
        port=80, username="admin", password="secret", channel=2,
        created_at=now, last_used_at=now, device={}, manufacturer="intelbras",
    )
    captured = {}

    def fake_ptz_move(self, *, channel, **kwargs):
        captured.update(channel=channel, **kwargs)

    monkeypatch.setattr(service, "_run_worker", lambda *a, **k: pytest.fail("nao deveria chamar o worker nativo"))
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.ptz_move",
        fake_ptz_move,
    )

    service.move_ptz("intelbras-ptz", 21, pan=9, tilt=-9, zoom=0, speed=99, duration_ms=9999)

    assert captured == {"channel": 2, "pan": 1, "tilt": -1, "zoom": 0, "speed": 7, "duration_ms": 800}


def test_intelbras_ptz_uses_native_worker_when_dahua_sdk_installed(monkeypatch, tmp_path):
    install_root = tmp_path / "sdk-packages"
    library = install_root / "dahua" / "current" / "lib" / "libdhnetsdk.so"
    library.parent.mkdir(parents=True)
    library.touch()
    monkeypatch.setenv("SDK_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("DAHUA_SDK_ENABLED", "true")

    now = time.time()
    service._sessions["intelbras-native-ptz"] = service.HikSdkLabSession(
        token="intelbras-native-ptz", owner_id=21, label="cam", device_type="camera", host="10.0.0.6",
        port=37777, username="admin", password="secret", channel=2,
        created_at=now, last_used_at=now, device={}, manufacturer="intelbras",
    )
    captured = {}
    monkeypatch.setattr(service, "_run_worker", lambda action, payload, **kwargs: captured.update(
        action=action, manufacturer=kwargs.get("manufacturer"), payload=payload
    ) or {"ok": True})
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.ptz_move",
        lambda self, **kwargs: pytest.fail("nao deveria usar a API HTTP quando o SDK nativo esta pronto"),
    )

    service.move_ptz("intelbras-native-ptz", 21, pan=1, tilt=0, zoom=0, speed=4, duration_ms=300)

    assert captured["action"] == "ptz"
    assert captured["manufacturer"] == "intelbras"


def test_intelbras_snapshot_falls_back_to_http_without_native_sdk(monkeypatch):
    now = time.time()
    service._sessions["intelbras-snap"] = service.HikSdkLabSession(
        token="intelbras-snap", owner_id=21, label="cam", device_type="camera", host="10.0.0.6",
        port=80, username="admin", password="secret", channel=2,
        created_at=now, last_used_at=now, device={}, manufacturer="intelbras",
    )
    monkeypatch.setattr(service, "_run_worker", lambda *a, **k: pytest.fail("nao deveria chamar o worker nativo"))
    monkeypatch.setattr(
        "app.video_sources.providers.dahua_http_api.DahuaHttpApiClient.get_snapshot",
        lambda self, *, channel: b"\xff\xd8fake",
    )

    frame = service.capture_snapshot("intelbras-snap", 21)

    assert frame == b"\xff\xd8fake"


def test_intelbras_rtsp_uses_dahua_style_url():
    now = time.time()
    session = service.HikSdkLabSession(
        token="t", owner_id=1, label="cam", device_type="camera", host="10.0.0.7",
        port=80, username="admin", password="secret", channel=4,
        created_at=now, last_used_at=now, device={}, manufacturer="intelbras", stream_kind="main",
    )
    assert service._rtsp_url(session) == "rtsp://admin:secret@10.0.0.7:554/cam/realmonitor?channel=4&subtype=0"


def test_sdk_availability_is_independent_per_manufacturer(monkeypatch, tmp_path):
    hik_dir = tmp_path / "hik"
    dahua_dir = tmp_path / "dahua"
    hik_dir.mkdir()
    dahua_dir.mkdir()
    (hik_dir / "libhcnetsdk.so").touch()
    (dahua_dir / "libdhnetsdk.so").touch()
    monkeypatch.setenv("HIK_SDK_ENABLED", "false")
    monkeypatch.setenv("DAHUA_SDK_ENABLED", "true")
    monkeypatch.setenv("HIK_SDK_LIB_DIR", str(hik_dir))
    monkeypatch.setenv("DAHUA_SDK_LIB_DIR", str(dahua_dir))

    assert service.sdk_availability() == {"hikvision": False, "dahua": True, "intelbras": True}
    assert service.sdk_available() is True


def test_uploaded_package_is_available_without_manual_enable_flag(monkeypatch, tmp_path):
    install_root = tmp_path / "sdk-packages"
    library = install_root / "dahua" / "current" / "lib" / "libdhnetsdk.so"
    library.parent.mkdir(parents=True)
    library.touch()
    monkeypatch.setenv("SDK_INSTALL_ROOT", str(install_root))
    monkeypatch.delenv("DAHUA_SDK_ENABLED", raising=False)

    assert service.sdk_available("dahua") is True


def test_expired_session_is_removed(monkeypatch):
    monkeypatch.setenv("HIK_SDK_SESSION_TTL_SECONDS", "60")
    now = time.time()
    session = service.HikSdkLabSession(
        token="expired", owner_id=1, label="x", device_type="camera", host="10.0.0.2",
        port=8000, username="u", password="p", channel=1,
        created_at=now - 90, last_used_at=now - 90, device={},
    )
    service._sessions[session.token] = session
    assert service.list_sessions(1) == []


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "8.8.8.8", "camera.local"])
def test_host_validation_rejects_unsafe_non_literal_or_public_by_default(host):
    with pytest.raises(service.HikSdkLabError):
        service.validate_host(host)


def test_host_validation_accepts_public_ip_only_when_opted_in(monkeypatch):
    monkeypatch.setenv("HIK_SDK_ALLOW_PUBLIC_IPS", "true")
    assert service.validate_host("8.8.8.8") == "8.8.8.8"
    for blocked in ("127.0.0.1", "0.0.0.0", "224.0.0.1"):
        with pytest.raises(service.HikSdkLabError):
            service.validate_host(blocked)


def test_ptz_is_bounded_before_worker(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "_run_worker", lambda action, payload, **kwargs: captured.update(action=action, **payload) or {"ok": True})
    now = time.time()
    service._sessions["token"] = service.HikSdkLabSession(
        token="token", owner_id=3, label="cam", device_type="camera", host="10.0.0.3",
        port=8000, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={},
    )
    service.move_ptz("token", 3, pan=9, tilt=-5, zoom=0, speed=99, duration_ms=9999)
    assert captured["action"] == "ptz"
    assert (captured["pan"], captured["tilt"], captured["speed"], captured["duration_ms"]) == (1, -1, 7, 800)


def test_ptz_uses_translated_hikvision_sdk_channel(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "_run_worker", lambda action, payload, **kwargs: captured.update(payload) or {"ok": True})
    now = time.time()
    service._sessions["mapped"] = service.HikSdkLabSession(
        token="mapped", owner_id=3, label="nvr", device_type="nvr", host="10.0.0.4",
        port=8000, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={"sdk_channel": 33},
    )

    service.move_ptz("mapped", 3, pan=1, tilt=0, zoom=0, speed=4, duration_ms=300)

    assert captured["channel"] == 33


def test_dahua_stream_registers_substream_without_returning_credentials(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "_run_worker", lambda *args, **kwargs: {
        "ok": True, "device": {"serial_number": "DH-01"},
    })

    def register(path, source):
        captured.update(path=path, source=source)
        return {"ok": True, "path": path, "masked_source": "rtsp://***:***@192.168.1.40"}

    monkeypatch.setattr(service, "register_webrtc_path", register)
    monkeypatch.setattr(service, "build_webrtc_player_url", lambda path: f"/__webrtc_public__/{path}")
    public = service.connect_device(
        owner_id=12, label="Dahua", device_type="nvr", manufacturer="dahua",
        host="192.168.1.40", port=37777, rtsp_port=554,
        username="admin@example", password="p@ss:/word", channel=3, stream_kind="sub",
    )

    stream = service.start_stream(public["token"], 12)

    assert captured["path"].startswith("sdk_lab_")
    assert captured["source"] == (
        "rtsp://admin%40example:p%40ss%3A%2Fword@192.168.1.40:554/"
        "cam/realmonitor?channel=3&subtype=1"
    )
    assert stream == {
        "path": captured["path"],
        "player_url": f"/__webrtc_public__/{captured['path']}",
        "stream_kind": "sub",
    }
    assert "username" not in public
    assert "password" not in public


def test_disconnect_unregisters_temporary_stream(monkeypatch):
    removed = []
    now = time.time()
    service._sessions["token"] = service.HikSdkLabSession(
        token="token", owner_id=3, label="cam", device_type="camera", host="10.0.0.3",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua",
        stream_path="sdk_lab_token",
    )
    monkeypatch.setattr(service, "unregister_webrtc_path", lambda path: removed.append(path) or {"ok": True})

    service.disconnect("token", 3)

    assert removed == ["sdk_lab_token"]
    assert "token" not in service._sessions


@pytest.mark.parametrize(
    ("manufacturer", "port", "channel", "device", "expected_channel"),
    [
        ("dahua", 37777, 3, {}, 3),
        ("hikvision", 8000, 1, {"sdk_channel": 33}, 33),
    ],
)
def test_goto_existing_preset_uses_native_sdk_without_saving(
    monkeypatch,
    manufacturer,
    port,
    channel,
    device,
    expected_channel,
):
    calls = []
    monkeypatch.setattr(
        service,
        "_run_worker",
        lambda action, payload, **kwargs: calls.append((action, payload, kwargs)) or {"ok": True},
    )
    now = time.time()
    service._sessions["preset-session"] = service.HikSdkLabSession(
        token="preset-session",
        owner_id=3,
        label="cam",
        device_type="camera",
        host="10.0.0.3",
        port=port,
        username="u",
        password="p",
        channel=channel,
        created_at=now,
        last_used_at=now,
        device=device,
        manufacturer=manufacturer,
        connection_mode="sdk",
    )

    service.goto_preset("preset-session", 3, "7")

    assert len(calls) == 1
    action, payload, kwargs = calls[0]
    assert action == "goto_preset"
    assert payload["preset_token"] == "7"
    assert payload["channel"] == expected_channel
    assert kwargs["manufacturer"] == manufacturer


def test_dahua_list_presets_stays_on_native_sdk(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service,
        "_run_worker",
        lambda action, payload, **kwargs: calls.append((action, payload, kwargs)) or {
            "ok": True,
            "presets": [{"token": "2", "name": "Portao"}],
        },
    )
    now = time.time()
    service._sessions["dahua-presets"] = service.HikSdkLabSession(
        token="dahua-presets",
        owner_id=3,
        label="nvr",
        device_type="nvr",
        host="10.0.0.3",
        port=37777,
        username="u",
        password="p",
        channel=3,
        created_at=now,
        last_used_at=now,
        device={},
        manufacturer="dahua",
        connection_mode="sdk",
    )
    monkeypatch.setattr(
        service,
        "_onvif_ptz_for_session",
        lambda session: pytest.fail("Nao deve tentar ONVIF para Dahua SDK."),
    )

    presets = service.list_presets("dahua-presets", 3)

    assert calls[0][0] == "list_presets"
    assert calls[0][1]["channel"] == 3
    assert calls[0][2]["manufacturer"] == "dahua"


def test_hikvision_list_presets_stays_on_native_sdk(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service,
        "_run_worker",
        lambda action, payload, **kwargs: calls.append((action, payload, kwargs)) or {
            "ok": True,
            "presets": [{"token": "4", "name": "Preset 4"}],
        },
    )
    now = time.time()
    service._sessions["hik-presets"] = service.HikSdkLabSession(
        token="hik-presets",
        owner_id=3,
        label="nvr",
        device_type="nvr",
        host="192.168.89.101",
        port=8000,
        username="admin",
        password="p",
        channel=4,
        created_at=now,
        last_used_at=now,
        device={},
        manufacturer="hikvision",
        connection_mode="sdk",
    )
    monkeypatch.setattr(
        service,
        "_onvif_ptz_for_session",
        lambda session: pytest.fail("Nao deve tentar ONVIF para Hikvision SDK."),
    )

    presets = service.list_presets("hik-presets", 3)

    assert presets == [{"token": "4", "name": "Preset 4"}]
    assert calls[0][0] == "list_presets"
    assert calls[0][1]["channel"] == 4
    assert calls[0][2]["manufacturer"] == "hikvision"


@pytest.mark.parametrize("manufacturer", ["dahua", "hikvision", "intelbras"])
def test_position_ptz_3d_uses_native_worker(monkeypatch, manufacturer):
    calls = []
    monkeypatch.setattr(
        service,
        "_run_worker",
        lambda action, payload, **kwargs: calls.append(
            (action, payload, kwargs)
        ) or {"ok": True},
    )
    monkeypatch.setattr(service, "_terminate_session_ptz", lambda session: None)
    monkeypatch.setattr(service, "intelbras_native_ready", lambda: True)
    now = time.time()
    service._sessions["position-3d"] = service.HikSdkLabSession(
        token="position-3d",
        owner_id=3,
        label="cam",
        device_type="camera",
        host="10.0.0.3",
        port=37777 if manufacturer != "hikvision" else 8000,
        username="u",
        password="p",
        channel=3,
        created_at=now,
        last_used_at=now,
        device={},
        manufacturer=manufacturer,
        connection_mode="sdk",
    )

    service.position_ptz_3d(
        "position-3d",
        3,
        x_start=10,
        y_start=20,
        x_end=230,
        y_end=240,
    )

    assert calls[0][0] == "ptz_3d"
    assert calls[0][1]["x_start"] == 10
    assert calls[0][1]["x_end"] == 230
    assert calls[0][1]["port"] == (
        37777 if manufacturer == "intelbras"
        else (8000 if manufacturer == "hikvision" else 37777)
    )
    assert calls[0][2]["manufacturer"] == manufacturer


def test_ptz_hold_start_updates_live_hold_instead_of_restarting(monkeypatch, tmp_path):
    now = time.time()
    result_path = tmp_path / "dahua-ptz-hold.json"

    class FakeLiveProcess:
        def poll(self):
            return None  # ainda em andamento

    session = service.HikSdkLabSession(
        token="dahua-hold", owner_id=7, label="cam", device_type="camera", host="10.0.0.9",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua",
    )
    session.active_ptz_process = FakeLiveProcess()
    session.active_ptz_result_path = result_path
    service._sessions[session.token] = session

    monkeypatch.setattr(
        service.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("nao deve abrir um novo processo com hold vivo"),
    )

    started = service.ptz_hold_start("dahua-hold", 7, pan=-9, tilt=0, zoom=1, speed=99)

    assert started is True
    command_path = service._command_signal_path(result_path)
    assert json.loads(command_path.read_text(encoding="utf-8")) == {
        "pan": -1, "tilt": 0, "zoom": 1, "speed": 7,
    }


def test_ptz_hold_start_restarts_when_previous_hold_already_exited(monkeypatch, tmp_path):
    now = time.time()

    class DeadProcess:
        def poll(self):
            return 0  # ja saiu (teto de seguranca/crash)

    session = service.HikSdkLabSession(
        token="dahua-dead", owner_id=7, label="cam", device_type="camera", host="10.0.0.9",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua",
    )
    session.active_ptz_process = DeadProcess()
    session.active_ptz_result_path = tmp_path / "dahua-dead.json"
    service._sessions[session.token] = session

    terminated = {}
    monkeypatch.setattr(service, "_terminate_session_ptz", lambda s: terminated.update(called=True))
    monkeypatch.setattr(service, "sdk_available", lambda manufacturer: False)

    started = service.ptz_hold_start("dahua-dead", 7, pan=1, tilt=0, zoom=0, speed=4)

    # Nao reaproveita um hold morto: limpa e tenta reiniciar (aqui o SDK esta
    # indisponivel, entao cai no fallback por pulso devolvendo False).
    assert terminated.get("called") is True
    assert started is False


def test_stream_dimensions_prefers_main_stream_for_3d_geometry(monkeypatch):
    now = time.time()
    service._sessions["dims"] = service.HikSdkLabSession(
        token="dims", owner_id=7, label="cam", device_type="camera", host="10.0.0.8",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua", stream_kind="sub",
    )
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = b'{"streams":[{"width":1920,"height":1080}]}'
        stderr = b""

    def fake_run(command, **kwargs):
        captured["command"] = command
        return FakeCompleted()

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    assert service.stream_dimensions("dims", 7) == (1920, 1080)
    assert service._rtsp_url(service._sessions["dims"], stream_kind="main") in captured["command"]
    assert "v:0" in captured["command"]


def test_stream_dimensions_uses_codec_display_aspect_ratio(monkeypatch):
    now = time.time()
    service._sessions["dims-dar"] = service.HikSdkLabSession(
        token="dims-dar", owner_id=7, label="cam", device_type="camera", host="10.0.0.8",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua", stream_kind="sub",
    )

    class FakeCompleted:
        returncode = 0
        stdout = b'{"streams":[{"width":704,"height":576,"display_aspect_ratio":"16:9"}]}'
        stderr = b""

    monkeypatch.setattr(service.subprocess, "run", lambda command, **kwargs: FakeCompleted())

    assert service.stream_dimensions("dims-dar", 7) == (1024, 576)


def test_stream_dimensions_falls_back_to_displayed_substream(monkeypatch):
    now = time.time()
    service._sessions["dims-fallback"] = service.HikSdkLabSession(
        token="dims-fallback", owner_id=7, label="cam", device_type="camera", host="10.0.0.8",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua", stream_kind="sub",
    )
    commands = []

    class Failed:
        returncode = 1
        stdout = b""
        stderr = b"main unavailable"

    class Substream:
        returncode = 0
        stdout = b'{"streams":[{"width":704,"height":576}]}'
        stderr = b""

    def fake_run(command, **kwargs):
        commands.append(command)
        return Failed() if "subtype=0" in command[-1] else Substream()

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    assert service.stream_dimensions("dims-fallback", 7) == (704, 576)
    assert len(commands) == 2


def test_stream_dimensions_reuses_the_measurement_of_the_session(monkeypatch):
    now = time.time()
    service._sessions["dims-cache"] = service.HikSdkLabSession(
        token="dims-cache", owner_id=7, label="cam", device_type="camera", host="10.0.0.8",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua", stream_kind="sub",
    )
    runs = []

    class FakeCompleted:
        returncode = 0
        stdout = b'{"streams":[{"width":1920,"height":1080}]}'
        stderr = b""

    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda command, **kwargs: runs.append(command) or FakeCompleted(),
    )

    assert service.stream_dimensions("dims-cache", 7) == (1920, 1080)
    assert service.stream_dimensions("dims-cache", 7) == (1920, 1080)
    # Cada medicao abre uma conexao RTSP extra na camera: a segunda consulta do
    # overlay 3D tem de sair do cache da sessao.
    assert len(runs) == 1


def test_stream_dimensions_raises_when_probe_fails(monkeypatch):
    now = time.time()
    service._sessions["dims-fail"] = service.HikSdkLabSession(
        token="dims-fail", owner_id=7, label="cam", device_type="camera", host="10.0.0.8",
        port=37777, username="u", password="p", channel=1,
        created_at=now, last_used_at=now, device={}, manufacturer="dahua", stream_kind="sub",
    )

    class FakeCompleted:
        returncode = 1
        stdout = b""
        stderr = b"Connection refused"

    monkeypatch.setattr(service.subprocess, "run", lambda command, **kwargs: FakeCompleted())

    with pytest.raises(service.HikSdkLabError):
        service.stream_dimensions("dims-fail", 7)


def test_expired_stream_is_unregistered(monkeypatch):
    monkeypatch.setenv("HIK_SDK_SESSION_TTL_SECONDS", "60")
    removed = []
    now = time.time()
    service._sessions["expired"] = service.HikSdkLabSession(
        token="expired", owner_id=1, label="x", device_type="camera", host="10.0.0.2",
        port=8000, username="u", password="p", channel=1,
        created_at=now - 90, last_used_at=now - 90, device={},
        stream_path="sdk_lab_expired",
    )
    monkeypatch.setattr(service, "unregister_webrtc_path", lambda path: removed.append(path) or {"ok": True})

    assert service.list_sessions(1) == []
    assert removed == ["sdk_lab_expired"]
