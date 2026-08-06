from types import SimpleNamespace
from unittest.mock import Mock

from app.core.config import settings
from app.runtime import event_alarm_policy
from app.runtime.event_alarm_policy import (
    apply_consensus_block_policy,
    apply_visual_quality_alarm_gate,
    assess_event_alarm,
)


def test_alarm_assessment_records_maturity_and_motion_contract(monkeypatch):
    maturity = {
        "score": 0.82,
        "level": "HIGH",
        "decision": "notify",
        "reason": "stable_track",
        "safety": {"fast_motion_protected": False},
        "features": {
            "motion_confirm_passed": True,
            "motion_blobs_median": 2.0,
            "motion_area_pct_median": 0.04,
            "center_displacement_norm": 0.12,
            "motion_confirm_has_mask": True,
            "motion_confirm_passed_blobs": True,
            "motion_confirm_passed_area": True,
            "motion_confirm_passed_displacement": False,
            "motion_confirm_boost": True,
            "motion_confirm_signal": "positive",
        },
    }
    decision = {
        "action": "NOTIFY",
        "suggested_status": "active",
        "reason": "mature",
        "mode": "audit",
        "inputs": {"motion_confirm_mode": "enforce"},
    }
    maturity_fn = Mock(return_value=maturity)
    decision_fn = Mock(return_value=decision)
    monkeypatch.setattr(event_alarm_policy, "evaluate_event_maturity", maturity_fn)
    monkeypatch.setattr(event_alarm_policy, "decide_alarm_action", decision_fn)
    event = SimpleNamespace(metadata={}, explanation="base")
    revalidation = object()
    far_revalidation = object()
    source_track = object()
    strategy = {
        "decision": "PERSON",
        "notification_decision": "NOTIFY",
        "anti_fp_post_filter": {"blocked": False},
    }

    assessment = assess_event_alarm(
        event,
        source_track=source_track,
        revalidation=revalidation,
        far_revalidation=far_revalidation,
        consensus_revalidation={"block_candidate": False},
        strategy3_v2_review=strategy,
        frame_width=1920,
        frame_height=1080,
        camera_family="bullet",
    )

    assert assessment.event_maturity is maturity
    assert assessment.alarm_decision is decision
    assert event.metadata["event_maturity"] is maturity
    assert event.metadata["motion_confirmation"] == {
        "passed": True,
        "blobs_median": 2.0,
        "area_pct_median": 0.04,
        "displacement_norm": 0.12,
        "mode": "enforce",
        "has_mask": True,
        "passed_blobs": True,
        "passed_area": True,
        "passed_displacement": False,
        "boost": True,
        "signal": "positive",
    }
    assert "maturity_score=0.82" in event.explanation
    assert "motion_confirm_mode=enforce" in event.explanation
    maturity_fn.assert_called_once_with(
        track=source_track,
        event=event,
        ia2_result=revalidation,
        ia3_result=far_revalidation,
        frame_width=1920,
        frame_height=1080,
        camera_family="bullet",
    )


def test_consensus_block_preserves_existing_precedence_when_candidates_overlap(
    monkeypatch,
):
    monkeypatch.setattr(settings, "consensus_revalidator_block_enabled", True)
    monkeypatch.setattr(settings, "consensus_revalidator_balanced_block_enabled", True)
    monkeypatch.setattr(settings, "consensus_revalidator_ia3_confirmed_block_enabled", True)
    monkeypatch.setattr(settings, "consensus_revalidator_ia2_dominant_block_enabled", True)
    monkeypatch.setattr(settings, "consensus_revalidator_ia2_only_block_enabled", True)
    consensus = {
        "block_candidate": True,
        "balanced_block_candidate": True,
        "ia3_confirmed_dynamic_candidate": True,
        "ia2_dominant_ia3_non_person_candidate": True,
        "ia2_only_balanced_candidate": True,
    }
    event = SimpleNamespace(metadata={})
    alarm_decision = {}

    outcome = apply_consensus_block_policy(
        event,
        revalidator_mode="block",
        consensus_revalidation=consensus,
        maturity_safety={},
        ia3_v2_block_veto=False,
        alarm_decision=alarm_decision,
    )

    assert outcome.applied is True
    assert outcome.reason == "balanced_ia2_ia3_consensus_not_person"
    assert outcome.profile == "ia2_only_balanced"
    assert outcome.source == "ia2_only_balanced"
    assert consensus["block_profile"] == "ia2_only_balanced"
    assert alarm_decision["action"] == "BLOCK_AUTO"
    assert event.metadata["consensus_revalidator_canceled"] is True


def test_consensus_block_respects_motion_safety_and_ia3_veto(monkeypatch):
    monkeypatch.setattr(settings, "consensus_revalidator_block_enabled", False)
    monkeypatch.setattr(settings, "consensus_revalidator_balanced_block_enabled", True)
    monkeypatch.setattr(settings, "consensus_revalidator_ia3_confirmed_block_enabled", False)
    monkeypatch.setattr(settings, "consensus_revalidator_ia2_dominant_block_enabled", False)
    monkeypatch.setattr(settings, "consensus_revalidator_ia2_only_block_enabled", False)
    candidate = {"balanced_block_candidate": True}

    protected = apply_consensus_block_policy(
        SimpleNamespace(metadata={}),
        revalidator_mode="block",
        consensus_revalidation=dict(candidate),
        maturity_safety={"fast_motion_protected": True},
        ia3_v2_block_veto=False,
        alarm_decision={},
    )
    vetoed = apply_consensus_block_policy(
        SimpleNamespace(metadata={}),
        revalidator_mode="block",
        consensus_revalidation=dict(candidate),
        maturity_safety={},
        ia3_v2_block_veto=True,
        alarm_decision={},
    )

    assert protected.applied is False
    assert vetoed.applied is False


def test_visual_quality_gate_applies_only_before_other_cancellation():
    quality = SimpleNamespace(has_artifact=True, artifact_reason="compression")
    event = SimpleNamespace(metadata={}, explanation="base")
    alarm_decision = {}

    reason = apply_visual_quality_alarm_gate(event, alarm_decision, quality)

    assert reason == "compression"
    assert alarm_decision["action"] == "LOG_ONLY"
    assert event.metadata["visual_quality_alarm_gate"]["decision"] == "log_only"
    assert "visual_quality_alarm_gate=log_only" in event.explanation

    canceled = SimpleNamespace(
        metadata={"revalidator_canceled": True},
        explanation="base",
    )
    untouched = {}
    assert apply_visual_quality_alarm_gate(canceled, untouched, quality) is None
    assert untouched == {}
