from __future__ import annotations

from typing import Any

from app.core.config import settings


def _score(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def decide_alarm_action(
    *,
    event_maturity: dict[str, Any] | None,
    ia2_result: Any | None = None,
    ia3_result: Any | None = None,
    consensus_result: dict[str, Any] | None = None,
    strategy3_v2_result: dict[str, Any] | None = None,
    anti_fp_post_filter_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sugere a separacao entre evento e alarme em modo audit.

    Esta camada ainda nao altera status, nao desativa alarmes e nao suprime
    eventos. Ela apenas registra qual seria a acao operacional recomendada.
    """

    maturity = event_maturity or {}
    safety = maturity.get("safety") or {}
    level = str(maturity.get("level") or "UNKNOWN")
    maturity_decision = str(maturity.get("decision") or "uncertain")
    consensus = consensus_result or {}

    ia2_person = _score(getattr(ia2_result, "person_score", None), 0.0)
    ia3_person = _score(getattr(ia3_result, "person_far_score", None), 0.0)
    strategy3_v2 = strategy3_v2_result or {}
    anti_fp = anti_fp_post_filter_result or {}
    strong_person_evidence = bool(
        ia2_person >= 0.50
        or ia3_person >= 0.10
        or bool(safety.get("best_frame_protects_from_suppression"))
    )

    if anti_fp:
        notification_decision = str(anti_fp.get("decision") or "SUPPRESS").upper()
        notification_reason = str(anti_fp.get("reason") or "unknown")
        mode = str((anti_fp.get("mode") or settings.anti_fp_post_filter_mode or "audit")).strip().lower()
        applies_to_runtime = mode not in {"", "audit", "shadow"}
        if notification_decision == "NOTIFY":
            action = "ALARM"
            suggested_status = "alarm"
            reason = notification_reason
            alarm_eligible = True
            is_alarm_active = True
        elif notification_decision == "LOW_PRIORITY":
            action = "LOW_PRIORITY_ALARM"
            suggested_status = "low_priority"
            reason = notification_reason
            alarm_eligible = True
            is_alarm_active = False
        elif notification_decision == "AUDIT":
            action = "AUDIT"
            suggested_status = "audit"
            reason = notification_reason
            alarm_eligible = True
            is_alarm_active = False
        else:
            action = "SUPPRESS"
            suggested_status = "suppressed"
            reason = notification_reason
            alarm_eligible = True
            is_alarm_active = False

        res = {
            "enabled": True,
            "mode": mode,
            "action": action,
            "suggested_status": suggested_status,
            "suggested_alarm_eligible": alarm_eligible,
            "suggested_is_alarm_active": is_alarm_active,
            "reason": reason,
            "applied": applies_to_runtime,
            "current_behavior_preserved": not applies_to_runtime,
            "inputs": {
                "maturity_level": level,
                "maturity_decision": maturity_decision,
                "ia2_person_score": ia2_person,
                "ia3_person_far_score": ia3_person,
                "consensus_block_candidate": bool(consensus.get("block_candidate")),
                "fast_motion_protected": bool(safety.get("fast_motion_protected")),
                "camera_motion_uncertain": bool(safety.get("camera_motion_uncertain")),
                "strong_person_evidence": strong_person_evidence,
                "strategy3_v2_decision": strategy3_v2.get("decision"),
                "strategy3_v2_reason": strategy3_v2.get("reason"),
                "strategy3_v2_initial_decision": strategy3_v2.get("initial_decision"),
                "strategy3_v2_notification_decision": anti_fp.get("decision"),
            },
        }

    else:
        if bool(safety.get("fast_motion_protected")) or level == "FAST_MOTION_PROTECTED":
            action = "LOW_CONFIDENCE_ALARM"
            suggested_status = "low_confidence"
            reason = "fast_motion_protected"
            alarm_eligible = True
            is_alarm_active = False
        elif bool(safety.get("camera_motion_uncertain")) or level == "CAMERA_MOTION_UNCERTAIN":
            action = "LOW_CONFIDENCE_ALARM"
            suggested_status = "low_confidence"
            reason = "camera_motion_uncertain"
            alarm_eligible = True
            is_alarm_active = False
        elif level == "ALARM_READY" or maturity_decision == "alarm_candidate" or strong_person_evidence:
            action = "ALARM"
            suggested_status = "alarm"
            reason = "mature_or_strong_person_evidence"
            alarm_eligible = True
            is_alarm_active = True
        elif maturity_decision == "suppress_candidate_audit" or level == "LOW_MOTION":
            action = "LOW_CONFIDENCE_EVENT"
            suggested_status = "low_confidence"
            reason = "low_motion_requires_review_before_suppress"
            alarm_eligible = True
            is_alarm_active = False
        elif bool(consensus.get("block_candidate")):
            action = "AUDIT"
            suggested_status = "audit"
            reason = "strict_visual_consensus_requires_audit"
            alarm_eligible = True
            is_alarm_active = False
        else:
            action = "LOW_CONFIDENCE_EVENT"
            suggested_status = "low_confidence"
            reason = "immature_or_partial_evidence"
            alarm_eligible = True
            is_alarm_active = False

        res = {
            "enabled": True,
            "mode": "audit",
            "action": action,
            "suggested_status": suggested_status,
            "suggested_alarm_eligible": alarm_eligible,
            "suggested_is_alarm_active": is_alarm_active,
            "reason": reason,
            "applied": False,
            "current_behavior_preserved": True,
            "inputs": {
                "maturity_level": level,
                "maturity_decision": maturity_decision,
                "ia2_person_score": ia2_person,
                "ia3_person_far_score": ia3_person,
                "consensus_block_candidate": bool(consensus.get("block_candidate")),
                "fast_motion_protected": bool(safety.get("fast_motion_protected")),
                "camera_motion_uncertain": bool(safety.get("camera_motion_uncertain")),
                "strong_person_evidence": strong_person_evidence,
                "strategy3_v2_decision": strategy3_v2.get("decision"),
                "strategy3_v2_reason": strategy3_v2.get("reason"),
                "strategy3_v2_initial_decision": strategy3_v2.get("initial_decision"),
            },
        }

    # Integrate motion confirmation inputs
    features = maturity.get("features") or {}
    motion_confirm_passed = bool(features.get("motion_confirm_passed", True))
    motion_blobs_median = float(features.get("motion_blobs_median", 0.0))
    motion_area_pct_median = float(features.get("motion_area_pct_median", 0.0))
    motion_displacement_norm = float(features.get("center_displacement_norm", 0.0))
    motion_confirm_has_mask = bool(features.get("motion_confirm_has_mask", False))
    motion_confirm_boost = bool(features.get("motion_confirm_boost", False))
    motion_confirm_signal = str(features.get("motion_confirm_signal", "neutral"))

    motion_confirm_mode = "audit"
    try:
        from app.core.config import settings
        if settings is not None:
            motion_confirm_mode = getattr(settings, "motion_confirm_mode", "audit")
    except Exception:
        pass

    if "inputs" not in res:
        res["inputs"] = {}
    res["inputs"].update({
        "motion_confirm_passed": motion_confirm_passed,
        "motion_blobs_median": motion_blobs_median,
        "motion_area_pct_median": motion_area_pct_median,
        "motion_displacement_norm": motion_displacement_norm,
        "motion_confirm_mode": motion_confirm_mode,
        "motion_confirm_has_mask": motion_confirm_has_mask,
        "motion_confirm_boost": motion_confirm_boost,
        "motion_confirm_signal": motion_confirm_signal,
        "motion_confirmation_veto_enabled": False,
    })

    return res
