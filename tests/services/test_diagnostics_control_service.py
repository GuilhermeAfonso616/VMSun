from pathlib import Path
from types import SimpleNamespace

from app.services import diagnostics_control_service


def test_docker_request_validation_covers_disabled_and_all_credentials(monkeypatch):
    monkeypatch.setattr(
        diagnostics_control_service.settings,
        "docker_stack_control_enabled",
        False,
    )
    monkeypatch.setattr(
        diagnostics_control_service.settings,
        "docker_stack_control_password",
        "secret",
    )
    assert diagnostics_control_service.validate_docker_stack_request(
        "restart",
        "secret",
        "REINICIAR DOCKER",
    ) == ("restart", "docker_control_disabled")

    monkeypatch.setattr(
        diagnostics_control_service.settings,
        "docker_stack_control_enabled",
        True,
    )
    assert diagnostics_control_service.validate_docker_stack_request(
        "invalid",
        "secret",
        "REINICIAR DOCKER",
    ) == ("invalid", "docker_bad_action")
    assert diagnostics_control_service.validate_docker_stack_request(
        "restart",
        "wrong",
        "REINICIAR DOCKER",
    ) == ("restart", "docker_bad_password")
    assert diagnostics_control_service.validate_docker_stack_request(
        "restart",
        "secret",
        "wrong",
    ) == ("restart", "docker_bad_confirmation")
    assert diagnostics_control_service.validate_docker_stack_request(
        "restart",
        "secret",
        "reiniciar docker",
    ) == ("restart", None)


def test_local_gateway_mode_updates_runtime_setting_and_environment(monkeypatch):
    monkeypatch.setattr(
        diagnostics_control_service,
        "remote_runtime_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        diagnostics_control_service.settings,
        "camera_gateway_worker_rtsp_fallback_enabled",
        True,
    )

    selected = diagnostics_control_service.set_gateway_capture_mode("gateway-only")

    assert selected == "gateway_only"
    assert diagnostics_control_service.settings.camera_gateway_worker_rtsp_fallback_enabled is False
    assert diagnostics_control_service.os.environ[
        "CAMERA_GATEWAY_WORKER_RTSP_FALLBACK_ENABLED"
    ] == "false"


def test_runtime_tuning_configuration_updates_local_and_remote(monkeypatch):
    calls = []
    monkeypatch.setattr(
        diagnostics_control_service,
        "update_runtime_tuning",
        lambda **payload: calls.append(("local", payload)) or {"source": "local"},
    )
    monkeypatch.setattr(
        diagnostics_control_service,
        "remote_runtime_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        diagnostics_control_service,
        "update_runtime_tuning_controls",
        lambda payload: calls.append(("remote", payload))
        or {"runtime_tuning": {"source": "remote"}},
    )
    payload = {"max_active_workers": 6, "gpu_guard_enabled": True}

    local, remote = diagnostics_control_service.update_runtime_tuning_configuration(payload)

    assert local == {"source": "local"}
    assert remote == {"source": "remote"}
    assert calls == [("local", payload), ("remote", payload)]


def test_docker_stack_action_controls_each_container_and_closes_client(monkeypatch):
    actions = []

    class Container:
        def __init__(self, name, status):
            self.name = name
            self.status = status

        def reload(self):
            actions.append((self.name, "reload"))

        def restart(self, timeout):
            actions.append((self.name, "restart", timeout))

        def start(self):
            actions.append((self.name, "start"))

    client = SimpleNamespace(close=lambda: actions.append(("client", "close")))
    containers = [Container("running", "running"), Container("stopped", "exited")]
    monkeypatch.setattr(diagnostics_control_service.time, "sleep", lambda _value: None)
    monkeypatch.setattr(
        diagnostics_control_service,
        "_current_compose_project_containers",
        lambda: (client, containers[-1], "analitico", containers),
    )

    diagnostics_control_service.run_docker_stack_action("restart")

    assert actions == [
        ("running", "reload"),
        ("running", "restart", 20),
        ("stopped", "reload"),
        ("stopped", "start"),
        ("client", "close"),
    ]


def test_backup_paths_follow_sqlite_url_and_application_base(monkeypatch, tmp_path):
    database = tmp_path / "analytics.db"
    monkeypatch.setattr(
        diagnostics_control_service.settings,
        "database_url",
        f"sqlite:///{database}",
    )
    monkeypatch.setattr(
        diagnostics_control_service.settings,
        "app_base_dir",
        str(tmp_path),
    )

    db_path, env_path = diagnostics_control_service.resolve_backup_paths()

    assert db_path == Path(database)
    assert env_path == tmp_path / ".env"
