from pathlib import Path


def test_live_alarm_popup_uses_one_click_workflow_and_distinct_resolutions():
    script = Path("app/static/js/monitor_vms.js").read_text(encoding="utf-8")

    assert 'postLiveAlarmAction("ack")' in script
    assert 'postLiveAlarmAction("close", "authorized_activity")' in script
    assert 'postLiveAlarmAction("close", "false_alarm")' in script
    assert '"application/x-www-form-urlencoded;charset=UTF-8"' in script
    assert "resolution_code: resolutionCode" in script


def test_live_alarm_popup_prevents_duplicate_actions_and_disables_all_choices():
    script = Path("app/static/js/monitor_vms.js").read_text(encoding="utf-8")

    assert "if (liveAlarmActionPending || !liveAlarmCurrent" in script
    assert "setLiveAlarmActionButtonsDisabled(true, alarm)" in script
    assert "liveAlarmAuthorizeBtn.disabled = disabled || !current.can_close" in script
