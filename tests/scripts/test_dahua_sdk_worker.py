import ctypes
import json

from scripts import dahua_sdk_worker as worker
from scripts.dahua_sdk_worker import (
    DH_EXTPTZ_FASTGOTO,
    DH_DEVSTATE_PTZ_PRESET_LIST,
    NET_PTZ_PRESET_LIST,
    PTZ_PRESET_RECORD_SIZE,
    _derive_ptz_capability,
    _list_presets,
    _ptz_3d,
    _read_command_vector,
)


def test_nvr_protocol_and_motorized_focus_do_not_mark_fixed_channel_as_ptz():
    result = _derive_ptz_capability(
        {
            "Name": "DH-SD",
            "Pan": True,
            "Tile": True,
            "Zoom": True,
            "MoveRelatively": True,
        },
        {
            "ElectricFocus": False,
            "AutofocusPeak": False,
            "SyncFocus": False,
        },
    )

    assert result["ptz_capability_verified"] is True
    assert result["ptz_protocol_motion"] is True
    assert result["physical_ptz"] is False
    assert result["ptz_capable"] is False


def test_explicit_physical_ptz_with_ptz_protocol_is_marked_controllable():
    result = _derive_ptz_capability(
        {"Name": "DH-SD", "Pan": True, "Zoom": True, "PtzDevice": True},
        {"ElectricFocus": True},
    )

    assert result["ptz_capable"] is True
    assert result["ptz_protocol_motion"] is True
    assert result["ptz_pan"] is True
    assert result["ptz_zoom"] is True


def test_inspect_exposes_channel_capability_payloads_for_diagnostics(monkeypatch):
    responses = {
        "ptz.getCurrentProtocolCaps": {"Name": "DH-SD", "Pan": True, "Zoom": True},
        "devVideoInput.getCaps": {"ElectricFocus": True, "CustomFlag": "present"},
    }
    monkeypatch.setattr(
        worker,
        "_query_system_info",
        lambda _library, _login_id, command, _channel: responses[command],
    )
    monkeypatch.setattr(worker, "_sdk_version", lambda _library: "test-sdk")

    result = worker._inspect(None, 99, bytes(80), requested_channel=3)

    assert result["ptz_protocol_caps"] == responses["ptz.getCurrentProtocolCaps"]
    assert result["video_input_caps"] == responses["devVideoInput.getCaps"]


def test_native_preset_list_uses_remote_channel_and_parses_current_sdk_layout():
    observed = {}

    class FakeQuery:
        argtypes = None
        restype = None

        def __call__(self, login_id, query_type, channel, info_pointer, *_args):
            info = ctypes.cast(
                info_pointer,
                ctypes.POINTER(NET_PTZ_PRESET_LIST),
            ).contents
            observed.update(
                login_id=login_id,
                query_type=query_type,
                channel=channel,
            )
            for position, (index, name) in enumerate(((1, b"Entrada"), (7, b"Portao"))):
                offset = position * PTZ_PRESET_RECORD_SIZE
                ctypes.memmove(info.pstuPtzPorsetList + offset, ctypes.byref(ctypes.c_int32(index)), 4)
                ctypes.memmove(info.pstuPtzPorsetList + offset + 4, name, len(name))
            info.dwRetPresetNum = 2
            return 1

    library = type("Library", (), {"CLIENT_QueryRemotDevState": FakeQuery()})()

    presets = _list_presets(library, 99, {"channel": 3})

    assert observed == {
        "login_id": 99,
        "query_type": DH_DEVSTATE_PTZ_PRESET_LIST,
        "channel": 2,
    }
    assert presets == [
        {"token": "1", "name": "Entrada"},
        {"token": "7", "name": "Portao"},
    ]


def test_ptz_3d_maps_selection_to_fastgoto_and_zoom_direction():
    calls = []

    class FakeControl:
        argtypes = None
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 1

    library = type("Library", (), {"CLIENT_DHPTZControlEx2": FakeControl()})()

    _ptz_3d(
        library,
        99,
        {
            "channel": 3,
            "x_start": 64,
            "y_start": 64,
            "x_end": 192,
            "y_end": 192,
        },
    )
    _ptz_3d(
        library,
        99,
        {
            "channel": 3,
            "x_start": 192,
            "y_start": 192,
            "x_end": 64,
            "y_end": 64,
        },
    )

    assert calls[0][0:3] == (99, 2, DH_EXTPTZ_FASTGOTO)
    assert abs(calls[0][3]) <= 33
    assert abs(calls[0][4]) <= 33
    assert calls[0][5] > 0
    assert calls[1][5] < 0


def test_ptz_3d_click_centers_without_changing_zoom():
    calls = []

    class FakeControl:
        argtypes = None
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 1

    library = type("Library", (), {"CLIENT_DHPTZControlEx2": FakeControl()})()

    _ptz_3d(
        library,
        7,
        {
            "channel": 1,
            "x_start": 200,
            "y_start": 80,
            "x_end": 200,
            "y_end": 80,
        },
    )

    assert calls[0][5] == 0


def test_read_command_vector_parses_and_bounds(tmp_path):
    command_path = tmp_path / "hold.json.cmd"
    command_path.write_text(
        json.dumps({"pan": 5, "tilt": -3, "zoom": 0, "speed": 99}), encoding="utf-8"
    )

    assert _read_command_vector(command_path) == (1, -1, 0, 7)


def test_read_command_vector_tolerates_missing_or_garbage(tmp_path):
    assert _read_command_vector(tmp_path / "missing.cmd") is None
    bad = tmp_path / "bad.cmd"
    bad.write_text("nao e json", encoding="utf-8")
    assert _read_command_vector(bad) is None


def test_hold_ptz_applies_joystick_direction_change_on_same_login(tmp_path, monkeypatch):
    result_path = tmp_path / "hold.json"
    calls = []

    def fake_start(_library, _login_id, payload):
        calls.append(("start", payload["pan"], payload["tilt"], payload["speed"]))
        # Ao aplicar a nova direcao, sinaliza stop para encerrar o loop no teste.
        worker._stop_signal_path(result_path).write_text("stop", encoding="utf-8")
        return [payload["pan"]]

    def fake_stop(_library, _login_id, payload):
        calls.append(("stop", payload.get("commands")))

    monkeypatch.setattr(worker, "_ptz_start", fake_start)
    monkeypatch.setattr(worker, "_ptz_stop", fake_stop)

    # O pai (arraste do joystick) publica uma direcao diferente da inicial.
    worker._command_signal_path(result_path).write_text(
        json.dumps({"pan": -1, "tilt": 0, "zoom": 0, "speed": 5}), encoding="utf-8"
    )

    worker._hold_ptz(
        None,
        1,
        {"pan": 1, "tilt": 0, "zoom": 0, "speed": 4},
        [3],
        result_path,
        5.0,
    )

    # Parou a direcao inicial, iniciou a nova (mesma sessao) e o finally parou a nova.
    assert ("stop", [3]) in calls
    assert ("start", -1, 0, 5) in calls
    assert calls[-1] == ("stop", [-1])
