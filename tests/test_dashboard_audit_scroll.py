from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_audit.html"


def test_user_audit_tab_has_its_own_vertical_scroll_region() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "#paneUsers {" in source
    assert "height: calc(100% - 40px);" in source
    assert "min-height: 0;" in source
    assert "overflow-y: auto;" in source


def test_user_audit_tab_returns_to_document_scroll_on_mobile() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    mobile_section = source.split("@media (max-width: 760px)", 1)[1]

    assert "#paneUsers {" in mobile_section
    assert "height: auto;" in mobile_section
    assert "overflow: visible;" in mobile_section
