from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install_analitico_supervisor.sh"


def test_installer_restarts_updated_supervisor_units() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "systemctl restart analitico-supervisor.service" in source
    assert "systemctl restart analitico-slo-report.timer" in source
    assert "systemctl enable --now analitico-supervisor.service" not in source
