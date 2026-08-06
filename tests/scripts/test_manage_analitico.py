from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "manage_analitico.py"
SPEC = importlib.util.spec_from_file_location("manage_analitico", MODULE_PATH)
assert SPEC and SPEC.loader
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


def test_compose_environment_preserves_explicit_override(monkeypatch):
    monkeypatch.setenv("MTX_WEBRTCADDITIONALHOSTS", "camera.example.local")
    monkeypatch.setattr(
        manager,
        "detect_primary_lan_ipv4",
        lambda: (_ for _ in ()).throw(AssertionError("nao deve detectar")),
    )

    environment = manager.compose_environment()

    assert environment["MTX_WEBRTCADDITIONALHOSTS"] == "camera.example.local"


def test_compose_environment_detects_lan_ip(monkeypatch):
    monkeypatch.delenv("MTX_WEBRTCADDITIONALHOSTS", raising=False)
    monkeypatch.setattr(manager, "detect_primary_lan_ipv4", lambda: "192.168.50.12")

    environment = manager.compose_environment()

    assert environment["MTX_WEBRTCADDITIONALHOSTS"] == "192.168.50.12"


def test_compose_environment_loads_public_and_ice_values_from_env_docker(monkeypatch, tmp_path):
    (tmp_path / ".env.docker").write_text(
        "WEBRTC_GATEWAY_PUBLIC_BASE_URL=https://video.sunorus.com.br\n"
        "MTX_WEBRTCADDITIONALHOSTS=video.sunorus.com.br,186.250.202.114,192.168.2.62\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(manager, "PROJECT_DIR", tmp_path)
    monkeypatch.delenv("WEBRTC_GATEWAY_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("MTX_WEBRTCADDITIONALHOSTS", raising=False)

    environment = manager.compose_environment()

    assert environment["WEBRTC_GATEWAY_PUBLIC_BASE_URL"] == "https://video.sunorus.com.br"
    assert environment["MTX_WEBRTCADDITIONALHOSTS"].split(",") == [
        "video.sunorus.com.br",
        "186.250.202.114",
        "192.168.2.62",
    ]


def test_compose_command_adds_gpu_overlay_only_for_nvidia():
    cpu = manager.compose_command("cpu", "ps")
    nvidia = manager.compose_command("nvidia", "ps")

    assert not any(value.endswith("docker-compose.gpu.yml") for value in cpu)
    assert any(value.endswith("docker-compose.gpu.yml") for value in nvidia)
    assert "--env-file" in cpu
    assert any(value.endswith(".env.docker") for value in cpu)
    assert cpu[-1] == "ps"
    assert nvidia[-1] == "ps"


def test_select_accelerator_auto_prefers_available_nvidia(monkeypatch):
    monkeypatch.setattr(manager, "_host_has_nvidia", lambda: True)
    monkeypatch.setattr(manager, "_docker_has_nvidia", lambda: True)

    profile, _reason = manager.select_accelerator("auto")

    assert profile == "nvidia"
