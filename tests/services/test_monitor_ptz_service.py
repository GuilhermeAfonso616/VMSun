import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, CameraPtzProfile
from app.services import monitor_ptz_service as service


@pytest.fixture(autouse=True)
def clear_sdk_sessions():
    service._sdk_sessions.clear()
    service._onvif_connect_failures.clear()
    service._active_movements.clear()
    service._preferred_backend_cache.clear()
    yield
    service._sdk_sessions.clear()
    service._onvif_connect_failures.clear()
    service._active_movements.clear()
    service._preferred_backend_cache.clear()


def _intelbras_camera() -> Camera:
    return Camera(
        id=1, ip="10.0.0.6", username="admin", password="secret",
        manufacturer="intelbras", source_channel=2,
    )


def _dahua_nvr_camera() -> Camera:
    return Camera(
        id=2, name="NVR - Canal 3 main", ip="192.168.1.100", username="admin", password="secret",
        manufacturer="Nao informada", source_provider="dahua_nvr", source_channel=3, onvif_port=80,
    )


def test_dahua_nvr_camera_brand_detection(monkeypatch):
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    cam = _dahua_nvr_camera()
    assert service._camera_brand(cam) == "dahua"
    candidate = service._native_sdk_candidate(cam)
    assert candidate is not None
    assert candidate["brand"] == "dahua"
    assert candidate["port"] == 37777
    assert candidate["channel"] == 3


def test_ptz_declaration_changes_configuration_fingerprint():
    cam = _dahua_nvr_camera()
    before = service._configuration_fingerprint(cam)

    cam.analytics_profile_json = '{"camera_family":"ptz"}'

    assert service._configuration_fingerprint(cam) != before


def test_ptz_profile_payload_returns_persisted_sdk_diagnostics():
    profile = CameraPtzProfile(
        status="controllable",
        selected_backend="dahua_sdk",
        supports_pan_tilt=True,
        supports_zoom=True,
        supports_presets=False,
        continuous_move=True,
        failure_count=0,
        diagnostics_json='{"channel":2,"physical_ptz":false}',
    )

    payload = service.ptz_profile_payload(profile)

    assert payload["diagnostics"] == {"channel": 2, "physical_ptz": False}


def test_dahua_describe_ptz_falls_back_to_native_sdk_when_onvif_incapable(monkeypatch):
    cam = _dahua_nvr_camera()
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    monkeypatch.setattr(service, "_load_valid_profile", lambda camera: None)
    monkeypatch.setattr(service, "describe_onvif_device", lambda camera: {"capabilities": {"ptz": False}})
    monkeypatch.setattr(
        service,
        "_get_or_create_sdk_session",
        lambda *args, **kwargs: type(
            "Session",
            (),
            {
                "device": {
                    "model": "Dahua",
                    "ptz_capability_verified": True,
                    "ptz_capable": True,
                    "ptz_pan": True,
                    "ptz_zoom": True,
                }
            },
        )(),
    )
    monkeypatch.setattr(service, "_save_profile", lambda *args, **kwargs: {})

    result = service.describe_ptz(cam, owner_id=1)
    assert result["backend"] == "native_sdk"
    assert result["ptz_capable"] is True
    assert result["sdk_brand"] == "dahua"
    assert result["sdk_port"] == 37777
    assert result["sdk_channel"] == 3


def test_native_login_without_channel_ptz_evidence_is_not_controllable(monkeypatch):
    cam = _dahua_nvr_camera()
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    monkeypatch.setattr(
        service,
        "describe_onvif_device",
        lambda camera: {"capabilities": {"ptz": False}},
    )
    monkeypatch.setattr(
        service,
        "_get_or_create_sdk_session",
        lambda *args, **kwargs: type(
            "Session",
            (),
            {
                "device": {
                    "ptz_capability_verified": True,
                    "ptz_capable": False,
                    "motorized_input": False,
                }
            },
        )(),
    )

    result = service._describe_ptz_uncached(cam, owner_id=1)

    assert result["backend"] == "native_sdk"
    assert result["ptz_capable"] is False
    assert result["capabilities"]["ptz"] is False
    assert "nao confirmou mecanismo PTZ" in result["reason"]


def test_dahua_declared_ptz_accepts_channel_protocol_when_nvr_omits_physical_flag(monkeypatch):
    cam = _dahua_nvr_camera()
    cam.analytics_profile_json = '{"camera_family":"ptz"}'
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    monkeypatch.setattr(service, "describe_onvif_device", lambda camera: {"capabilities": {"ptz": False}})
    monkeypatch.setattr(
        service,
        "_get_or_create_sdk_session",
        lambda *args, **kwargs: type(
            "Session",
            (),
            {
                "device": {
                    "ptz_capability_verified": True,
                    "ptz_capable": False,
                    "ptz_protocol_motion": True,
                    "physical_ptz": False,
                }
            },
        )(),
    )

    result = service._describe_ptz_uncached(cam, owner_id=1)

    assert result["ptz_capable"] is True
    assert result["capabilities"]["pan_tilt"] is True
    assert result["device"]["ptz_declared_by_camera_profile"] is True
    assert result["diagnostics"]["ptz_protocol_motion"] is True
    assert result["diagnostics"]["physical_ptz"] is False


def test_dahua_move_ptz_uses_native_sdk(monkeypatch):
    cam = _dahua_nvr_camera()
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    monkeypatch.setattr(service, "_load_valid_profile", lambda camera: None)
    monkeypatch.setattr(service, "_save_profile", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service, "onvif_ptz_move",
        lambda *a, **k: (_ for _ in ()).throw(service.DeviceCapabilityError("sem ONVIF PTZ")),
    )

    captured = {}

    class FakeSession:
        token = "dahua-sess-1"

    def fake_get_or_create(camera, owner_id, brand, port, channel):
        captured.update(brand=brand, port=port, channel=channel)
        return FakeSession()

    def fake_hold_start(token, owner_id, **kwargs):
        captured.update(token=token, owner_id=owner_id, **kwargs)
        return True

    monkeypatch.setattr(service, "_get_or_create_sdk_session", fake_get_or_create)
    monkeypatch.setattr(service.sdk_lab, "ptz_hold_start", fake_hold_start)

    result = service.move_ptz(cam, owner_id=5, pan=1.0, tilt=0.0, zoom=0.0)
    assert result["backend"] == "native_sdk"
    assert result["ptz_capable"] is True
    assert captured["brand"] == "dahua"
    assert captured["port"] == 37777
    assert captured["channel"] == 3


def test_intelbras_uses_http_port_by_default(monkeypatch):
    cam = _intelbras_camera()
    cam.onvif_port = 80
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)

    candidate = service._native_sdk_candidate(cam)

    assert candidate is not None
    assert candidate["brand"] == "intelbras"
    assert candidate["port"] == 80


def test_intelbras_http_without_channel_capability_does_not_report_no_ptz(monkeypatch):
    cam = _intelbras_camera()
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    monkeypatch.setattr(
        service,
        "describe_onvif_device",
        lambda camera: (_ for _ in ()).throw(service.DeviceSessionError("credenciais ONVIF recusadas")),
    )
    monkeypatch.setattr(
        service,
        "_get_or_create_sdk_session",
        lambda *args, **kwargs: type("Session", (), {"device": {"serial_number": "ABC"}})(),
    )

    with pytest.raises(service.DeviceCapabilityError, match="nem via SDK nativo"):
        service._describe_ptz_uncached(cam, owner_id=1)


def test_cached_native_backend_skips_onvif_and_stop_uses_same_backend(monkeypatch):
    cam = _dahua_nvr_camera()
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    monkeypatch.setattr(service, "_load_valid_profile", lambda camera: {"backend": "dahua_sdk"})
    monkeypatch.setattr(service, "_save_profile", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "onvif_ptz_move",
        lambda *args, **kwargs: pytest.fail("ONVIF nao deve ser testado quando o backend salvo funciona"),
    )

    class FakeSession:
        token = "dahua-sess"

    monkeypatch.setattr(service, "_get_or_create_sdk_session", lambda *args, **kwargs: FakeSession())
    monkeypatch.setattr(service.sdk_lab, "ptz_hold_start", lambda *args, **kwargs: True)
    monkeypatch.setattr(service, "_peek_sdk_session_token", lambda *args, **kwargs: "dahua-sess")
    stopped = {}
    monkeypatch.setattr(
        service.sdk_lab,
        "ptz_hold_stop",
        lambda token, owner_id: stopped.update(token=token, owner_id=owner_id),
    )

    moved = service.move_ptz(cam, owner_id=7, pan=1.0)
    result = service.stop_ptz(cam, owner_id=7)

    assert moved["driver"] == "dahua_sdk"
    assert moved["movement_id"]
    assert result["movement_id"] == moved["movement_id"]
    assert stopped == {"token": "dahua-sess", "owner_id": 7}


def test_native_presets_reuse_selected_sdk_backend(monkeypatch):
    cam = _dahua_nvr_camera()
    monkeypatch.setattr(service, "_sdk_available", lambda brand: True)
    monkeypatch.setattr(service, "_preferred_backend", lambda camera: "dahua_sdk")

    class FakeSession:
        token = "dahua-preset-session"

    monkeypatch.setattr(service, "_get_or_create_sdk_session", lambda *args, **kwargs: FakeSession())
    monkeypatch.setattr(
        service.sdk_lab,
        "list_presets",
        lambda token, owner_id: [{"token": "2", "name": "Portao"}],
    )
    calls = []
    monkeypatch.setattr(
        service.sdk_lab,
        "goto_preset",
        lambda token, owner_id, preset_token: calls.append((token, owner_id, preset_token)),
    )
    presets = service.list_ptz_presets(cam, owner_id=7)
    result = service.goto_ptz_preset(cam, owner_id=7, preset_token="2")

    assert presets == [{"token": "2", "name": "Portao"}]
    assert calls == [("dahua-preset-session", 7, "2")]
    assert result == {
        "backend": "native_sdk",
        "driver": "dahua_sdk",
        "preset_token": "2",
    }


def test_onvif_presets_use_existing_device_presets_without_saving(monkeypatch):
    cam = _dahua_nvr_camera()
    monkeypatch.setattr(service, "_preferred_backend", lambda camera: "onvif")
    monkeypatch.setattr(
        service,
        "describe_onvif_device",
        lambda camera: {
            "capabilities": {"ptz": True},
            "presets": [{"token": "home", "name": "Entrada"}],
        },
    )
    calls = []
    monkeypatch.setattr(
        service,
        "onvif_goto_preset",
        lambda camera, token: calls.append((camera.id, token)),
    )

    assert service.list_ptz_presets(cam, owner_id=7) == [
        {"token": "home", "name": "Entrada"}
    ]
    result = service.goto_ptz_preset(cam, owner_id=7, preset_token="home")

    assert calls == [(cam.id, "home")]
    assert result["backend"] == "onvif"


def test_first_inspection_is_persisted_and_next_selection_uses_cache(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "SessionLocal", session_factory)
    with session_factory() as db:
        camera = Camera(
            name="Portaria",
            ip="10.0.0.20",
            onvif_port=80,
            username="admin",
            password="secret",
            manufacturer="Dahua",
            is_deleted=False,
        )
        db.add(camera)
        db.commit()
        camera_id = camera.id

    monkeypatch.setattr(
        service,
        "_describe_ptz_uncached",
        lambda camera, owner_id: {
            "backend": "onvif",
            "ptz_capable": True,
            "capabilities": {"ptz": True},
            "profile_token": "profile-1",
        },
    )

    initial, should_probe = service.prepare_ptz_inspection(camera_id)
    discovered = service.discover_and_persist_ptz(camera_id, owner_id=9)
    cached, should_probe_again = service.prepare_ptz_inspection(camera_id)

    assert initial["status"] == "probing"
    assert should_probe is True
    assert discovered["status"] == "controllable"
    assert discovered["backend"] == "onvif"
    assert cached["status_label"] == "PTZ disponivel · ONVIF"
    assert should_probe_again is False
    engine.dispose()
