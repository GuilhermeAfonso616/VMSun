from types import SimpleNamespace

import pytest

from app.services import device_session_service
from app.services.device_session_service import (
    DeviceSession,
    ptz_continuous_move,
    ptz_goto_preset,
    ptz_stop,
)


class FakePtz:
    def __init__(self):
        self.moves = []
        self.stops = []
        self.goto_presets = []

    def create_type(self, name):
        assert name == "ContinuousMove"
        return SimpleNamespace(ProfileToken=None, Velocity=None)

    def ContinuousMove(self, request):
        self.moves.append(request)

    def Stop(self, payload):
        self.stops.append(payload)

    def GotoPreset(self, payload):
        self.goto_presets.append(payload)



def _session(ptz):
    return DeviceSession(
        camera_id=7,
        host="10.0.0.7",
        port=80,
        onvif=object(),
        media=object(),
        ptz=ptz,
        profile_token="profile-ptz",
        ptz_capable=True,
        device_info={},
        capabilities={"ptz": True},
        profiles=[],
        connected_at=0.0,
    )


def test_ptz_move_clamps_velocity_and_stop_targets_all_axes(monkeypatch):
    ptz = FakePtz()
    session = _session(ptz)
    monkeypatch.setattr(device_session_service, "_get_session", lambda _camera: session)
    camera = SimpleNamespace(id=7)

    ptz_continuous_move(camera, pan=4, tilt=-3, zoom=0.5)
    ptz_stop(camera)

    assert ptz.moves[0].ProfileToken == "profile-ptz"
    assert ptz.moves[0].Velocity == {
        "PanTilt": {"x": 1.0, "y": -1.0},
        "Zoom": {"x": 0.5},
    }
    assert ptz.stops == [
        {"ProfileToken": "profile-ptz", "PanTilt": True, "Zoom": True}
    ]


def test_ptz_goto_preset_targets_the_active_profile(monkeypatch):
    ptz = FakePtz()
    session = _session(ptz)
    monkeypatch.setattr(device_session_service, "_get_session", lambda _camera: session)

    ptz_goto_preset(SimpleNamespace(id=7), "preset-3")

    assert ptz.goto_presets == [
        {"ProfileToken": "profile-ptz", "PresetToken": "preset-3"}
    ]


def test_ptz_payload_rejects_out_of_range_velocity():
    from pydantic import BaseModel, ConfigDict, Field, ValidationError

    class PtzMovePayload(BaseModel):
        model_config = ConfigDict(allow_inf_nan=False)

        pan: float = Field(default=0.0, ge=-1.0, le=1.0)
        tilt: float = Field(default=0.0, ge=-1.0, le=1.0)
        zoom: float = Field(default=0.0, ge=-1.0, le=1.0)

    with pytest.raises(ValidationError):
        PtzMovePayload(pan=1.1)
