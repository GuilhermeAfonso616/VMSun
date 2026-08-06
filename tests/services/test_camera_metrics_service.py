from types import SimpleNamespace

from app.services import camera_metrics_service


def test_remote_probe_preserves_runtime_reachability(monkeypatch):
    camera = SimpleNamespace(id=7)
    monkeypatch.setattr(camera_metrics_service, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        camera_metrics_service,
        "probe_runtime_camera",
        lambda camera_id: {"camera_id": camera_id, "reachable": False},
    )

    assert camera_metrics_service.probe_camera_reachability(camera) is False


def test_local_probe_normalizes_result_to_boolean(monkeypatch):
    camera = SimpleNamespace(id=8)
    monkeypatch.setattr(camera_metrics_service, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(
        camera_metrics_service.camera_health_monitor,
        "_probe_camera_reachable",
        lambda received: received is camera,
    )

    assert camera_metrics_service.probe_camera_reachability(camera) is True


def test_probe_failure_is_reported_as_unknown(monkeypatch):
    monkeypatch.setattr(camera_metrics_service, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        camera_metrics_service,
        "probe_runtime_camera",
        lambda _camera_id: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    assert camera_metrics_service.probe_camera_reachability(SimpleNamespace(id=9)) is None
