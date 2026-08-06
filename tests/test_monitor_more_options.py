from pathlib import Path


def test_more_options_closes_on_outside_click_and_escape():
    javascript = Path("app/static/js/monitor_vms.js").read_text(encoding="utf-8")

    assert 'document.querySelector(".vms-more-options")' in javascript
    assert "!moreOptionsEl.contains(event.target)" in javascript
    assert "moreOptionsEl.open = false" in javascript
    assert 'event.key === "Escape" && moreOptionsEl && moreOptionsEl.open' in javascript
