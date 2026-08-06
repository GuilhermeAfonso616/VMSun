from pathlib import Path


def test_compose_auto_script_detects_gpu_and_keeps_application_device_auto():
    script = Path("scripts/compose-auto.sh").read_text(encoding="utf-8")

    assert "nvidia-smi -L" in script
    assert "docker info --format" in script
    assert "docker-compose.gpu.yml" in script
    assert "ANALITICO_ACCELERATOR" in script
    assert "detect_device=auto" in script


def test_smart_update_uses_automatic_compose_profile():
    script = Path("scripts/smart_docker_update.sh").read_text(encoding="utf-8")

    assert 'COMPOSE=("$PWD/scripts/compose-auto.sh")' in script
    assert "docker-compose.gpu.yml" not in script


def test_environment_examples_default_to_automatic_device_selection():
    assert "DETECT_DEVICE=auto" in Path(".env.example").read_text(encoding="utf-8")
    assert "DETECT_DEVICE=auto" in Path(".env.docker.example").read_text(encoding="utf-8")
