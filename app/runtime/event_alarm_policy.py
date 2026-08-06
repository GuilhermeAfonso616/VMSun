"""Politicas de maturidade, consenso e qualidade para decisoes de alarme."""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics_v2.revalidation import decide_alarm_action, evaluate_event_maturity
from app.core.config import settings


@dataclass(slots=True)
class EventAlarmAssessment:
    event_maturity: dict
    maturity_safety: dict
    alarm_decision: dict


@dataclass(slots=True)
class ConsensusBlockOutcome:
    applied: bool
    reason: str | None = None
    source: str | None = None
    profile: str | None = None


def assess_event_alarm(
    event,
    *,
    source_track,
    revalidation,
    far_revalidation,
    consensus_revalidation: dict,
    strategy3_v2_review: dict,
    frame_width: int,
    frame_height: int,
    camera_family: str | None,
) -> EventAlarmAssessment:
    event_maturity = evaluate_event_maturity(
        track=source_track,
        event=event,
        ia2_result=revalidation,
        ia3_result=far_revalidation,
        frame_width=frame_width,
        frame_height=frame_height,
        camera_family=camera_family,
    )
    event.metadata["event_maturity"] = event_maturity
    maturity_safety = dict(event_maturity.get("safety") or {})
    alarm_decision = decide_alarm_action(
        event_maturity=event_maturity,
        ia2_result=revalidation,
        ia3_result=far_revalidation,
        consensus_result=consensus_revalidation,
        strategy3_v2_result=strategy3_v2_review,
        anti_fp_post_filter_result=strategy3_v2_review.get("anti_fp_post_filter"),
    )

    motion_features = event_maturity.get("features", {})
    motion_passed_str = str(
        bool(motion_features.get("motion_confirm_passed", True))
    ).lower()
    motion_blobs = motion_features.get("motion_blobs_median", 0.0)
    motion_area = motion_features.get("motion_area_pct_median", 0.0)
    motion_mode = alarm_decision.get("inputs", {}).get("motion_confirm_mode", "audit")

    event.metadata["motion_confirmation"] = {
        "passed": motion_features.get("motion_confirm_passed", True),
        "blobs_median": motion_blobs,
        "area_pct_median": motion_area,
        "displacement_norm": motion_features.get("center_displacement_norm", 0.0),
        "mode": motion_mode,
        "has_mask": motion_features.get("motion_confirm_has_mask", False),
        "passed_blobs": motion_features.get("motion_confirm_passed_blobs", False),
        "passed_area": motion_features.get("motion_confirm_passed_area", False),
        "passed_displacement": motion_features.get(
            "motion_confirm_passed_displacement",
            False,
        ),
        "boost": motion_features.get("motion_confirm_boost", False),
        "signal": motion_features.get("motion_confirm_signal", "neutral"),
    }

    event.explanation = (
        f"{event.explanation} | maturity_score={event_maturity.get('score', 0.0)} "
        f"maturity_level={event_maturity.get('level', 'UNKNOWN')} "
        f"maturity_decision={event_maturity.get('decision', 'uncertain')} "
        f"maturity_reason={event_maturity.get('reason', 'unknown')} "
        f"strategy3_v2_decision={strategy3_v2_review.get('decision', 'UNKNOWN')} "
        f"strategy3_v2_notify={strategy3_v2_review.get('notification_decision', 'SUPPRESS')} "
        f"maturity_fast_motion={str(bool(maturity_safety.get('fast_motion_protected'))).lower()} "
        f"maturity_camera_motion={str(bool(maturity_safety.get('camera_motion_possible'))).lower()} "
        f"alarm_decision_action={alarm_decision.get('action', 'AUDIT')} "
        f"alarm_decision_status={alarm_decision.get('suggested_status', 'audit')} "
        f"alarm_decision_reason={alarm_decision.get('reason', 'unknown')} "
        f"alarm_decision_mode={alarm_decision.get('mode', 'audit')} | "
        f"motion_confirm_passed={motion_passed_str} "
        f"motion_blobs_median={motion_blobs:.2f} "
        f"motion_area_pct_median={motion_area:.4f} "
        f"motion_confirm_mode={motion_mode} | "
        f"motion_confirm_has_mask={str(bool(motion_features.get('motion_confirm_has_mask', False))).lower()} "
        f"motion_confirm_displacement_norm={float(motion_features.get('center_displacement_norm', 0.0)):.6f} "
        f"motion_confirm_passed_blobs={str(bool(motion_features.get('motion_confirm_passed_blobs', False))).lower()} "
        f"motion_confirm_passed_area={str(bool(motion_features.get('motion_confirm_passed_area', False))).lower()} "
        f"motion_confirm_passed_displacement={str(bool(motion_features.get('motion_confirm_passed_displacement', False))).lower()} "
        f"motion_confirm_boost={str(bool(motion_features.get('motion_confirm_boost', False))).lower()} "
        f"motion_confirm_signal={motion_features.get('motion_confirm_signal', 'neutral')}"
    )
    return EventAlarmAssessment(
        event_maturity=event_maturity,
        maturity_safety=maturity_safety,
        alarm_decision=alarm_decision,
    )


def apply_consensus_block_policy(
    event,
    *,
    revalidator_mode: str,
    consensus_revalidation: dict,
    maturity_safety: dict,
    ia3_v2_block_veto: bool,
    alarm_decision: dict,
) -> ConsensusBlockOutcome:
    mode_allows_block = revalidator_mode == "block"
    consensus_should_block = bool(
        settings.consensus_revalidator_block_enabled
        and mode_allows_block
        and consensus_revalidation.get("block_candidate")
        and not ia3_v2_block_veto
    )
    motion_safety_allows_block = bool(
        not bool(maturity_safety.get("fast_motion_protected"))
        and not bool(maturity_safety.get("camera_motion_possible"))
        and not bool(maturity_safety.get("camera_motion_uncertain"))
    )
    balanced_should_block = bool(
        settings.consensus_revalidator_balanced_block_enabled
        and mode_allows_block
        and consensus_revalidation.get("balanced_block_candidate")
        and motion_safety_allows_block
        and not ia3_v2_block_veto
    )
    ia3_confirmed_should_block = bool(
        settings.consensus_revalidator_ia3_confirmed_block_enabled
        and mode_allows_block
        and consensus_revalidation.get("ia3_confirmed_dynamic_candidate")
        and motion_safety_allows_block
        and not ia3_v2_block_veto
    )
    ia2_dominant_should_block = bool(
        settings.consensus_revalidator_ia2_dominant_block_enabled
        and mode_allows_block
        and consensus_revalidation.get("ia2_dominant_ia3_non_person_candidate")
        and motion_safety_allows_block
        and not ia3_v2_block_veto
    )
    ia2_only_should_block = bool(
        settings.consensus_revalidator_ia2_only_block_enabled
        and mode_allows_block
        and consensus_revalidation.get("ia2_only_balanced_candidate")
        and motion_safety_allows_block
        and not ia3_v2_block_veto
    )
    if not any(
        (
            consensus_should_block,
            balanced_should_block,
            ia3_confirmed_should_block,
            ia2_dominant_should_block,
            ia2_only_should_block,
        )
    ):
        return ConsensusBlockOutcome(applied=False)

    reason = consensus_revalidation.get("reason") or (
        "balanced_ia2_ia3_consensus_not_person"
        if balanced_should_block
        else "ia3_confirmed_dynamic_not_person"
        if ia3_confirmed_should_block
        else "ia2_dominant_ia3_non_person"
        if ia2_dominant_should_block
        else "ia2_only_balanced_not_person"
        if ia2_only_should_block
        else "ia2_ia3_consensus_not_person"
    )
    profile = (
        "ia2_only_balanced"
        if ia2_only_should_block
        else "ia2_dominant_ia3_non_person"
        if ia2_dominant_should_block
        else "ia3_confirmed_dynamic"
        if ia3_confirmed_should_block
        else "balanced"
        if balanced_should_block
        else "strict"
    )
    source = (
        "ia2_only_balanced"
        if ia2_only_should_block
        else "ia2_dominant_ia3_non_person"
        if ia2_dominant_should_block
        else "ia3_confirmed_dynamic"
        if ia3_confirmed_should_block
        else "balanced_consensus_ia2_ia3"
        if balanced_should_block
        else "consensus_ia2_ia3"
    )
    consensus_revalidation.update(
        {
            "mode": "block",
            "operational_decision": "blocked",
            "block_applied": True,
            "block_profile": profile,
        }
    )
    alarm_decision.update(
        {
            "mode": "block",
            "action": "BLOCK_AUTO",
            "suggested_status": "canceled",
            "suggested_alarm_eligible": False,
            "suggested_is_alarm_active": False,
            "reason": reason,
            "applied": True,
            "current_behavior_preserved": False,
        }
    )
    event.metadata["consensus_revalidator_canceled"] = True
    return ConsensusBlockOutcome(
        applied=True,
        reason=str(reason),
        source=source,
        profile=profile,
    )


def apply_visual_quality_alarm_gate(event, alarm_decision: dict, visual_quality) -> str | None:
    if (
        bool(event.metadata.get("revalidator_canceled"))
        or bool(event.metadata.get("consensus_revalidator_canceled"))
        or not visual_quality.has_artifact
    ):
        return None
    artifact_reason = visual_quality.artifact_reason
    alarm_decision.update(
        {
            "mode": "block",
            "action": "LOG_ONLY",
            "suggested_status": "audit",
            "suggested_alarm_eligible": False,
            "suggested_is_alarm_active": False,
            "reason": f"visual_quality_{artifact_reason}",
            "applied": True,
            "current_behavior_preserved": False,
        }
    )
    event.metadata["visual_quality_alarm_gate"] = {
        "applied": True,
        "reason": artifact_reason,
        "decision": "log_only",
    }
    event.explanation = (
        f"{event.explanation} | visual_quality_alarm_gate=log_only "
        f"reason={artifact_reason}"
    )
    return artifact_reason
