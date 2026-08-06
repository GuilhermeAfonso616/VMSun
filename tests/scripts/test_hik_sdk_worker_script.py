import ctypes
import json

from scripts import hik_sdk_worker as worker
from scripts.hik_sdk_worker import NET_DVR_POINT_FRAME, _ptz_3d, _read_command_vector


def test_ptz_3d_passes_normalized_frame_and_sdk_channel():
    observed = {}

    class FakePosition:
        argtypes = None
        restype = None

        def __call__(self, user_id, channel, frame_pointer):
            frame = ctypes.cast(
                frame_pointer,
                ctypes.POINTER(NET_DVR_POINT_FRAME),
            ).contents
            observed["values"] = (
                user_id,
                channel,
                frame.xTop,
                frame.yTop,
                frame.xBottom,
                frame.yBottom,
                frame.bCounter,
            )
            return 1

    library = type("Library", (), {"NET_DVR_PTZSelZoomIn_EX": FakePosition()})()

    _ptz_3d(
        library,
        42,
        {
            "channel": 33,
            "x_start": 30,
            "y_start": 40,
            "x_end": 210,
            "y_end": 220,
        },
    )

    assert observed["values"] == (42, 33, 30, 40, 210, 220, 0)


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

    def fake_start(_library, _user_id, payload):
        calls.append(("start", payload["pan"], payload["tilt"], payload["speed"]))
        # Ao aplicar a nova direcao, sinaliza stop para encerrar o loop no teste.
        worker._stop_signal_path(result_path).write_text("stop", encoding="utf-8")
        return [payload["pan"]]

    def fake_stop(_library, _user_id, payload):
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

    assert ("stop", [3]) in calls
    assert ("start", -1, 0, 5) in calls
    assert calls[-1] == ("stop", [-1])
