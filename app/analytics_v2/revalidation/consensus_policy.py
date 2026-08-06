from __future__ import annotations

from typing import Any

from app.core.config import settings


def _score(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def ia3_v2_protection_blocks_auto_cancel(protection_result: dict[str, Any] | None) -> bool:
    """Retorna True quando a IA3 v2 protegeu uma pessoa contra block automatico."""

    if not protection_result:
        return False
    return bool(protection_result.get("protects_when_primary_rejects"))


def evaluate_consensus_block_candidate(ia2_result: Any, ia3_result: Any) -> dict[str, Any]:
    """Marca candidatos a block quando IA2 e IA3 concordam forte em nao-pessoa.

    Esta politica ainda e audit-only. Ela nao cancela evento; apenas registra
    que o evento seria um bom candidato para uma proxima validacao de block.
    """

    ia2_person = _score(getattr(ia2_result, "person_score", None))
    ia2_not_person = _score(getattr(ia2_result, "not_person_score", None))
    ia3_person = _score(getattr(ia3_result, "person_far_score", None))
    ia3_not_person = _score(getattr(ia3_result, "not_person_far_score", None))
    quality = dict(getattr(ia2_result, "quality", None) or {})

    thresholds = {
        "ia2_max_person_score": settings.consensus_revalidator_ia2_max_person_score,
        "ia2_min_not_person_score": settings.consensus_revalidator_ia2_min_not_person_score,
        "ia3_max_person_score": settings.consensus_revalidator_ia3_max_person_score,
        "ia3_min_not_person_score": settings.consensus_revalidator_ia3_min_not_person_score,
        "require_quality_gate": settings.consensus_revalidator_require_quality_gate,
        "require_not_near_border": settings.consensus_revalidator_require_not_near_border,
    }
    checks = {
        "enabled": bool(settings.consensus_revalidator_candidate_enabled),
        "ia2_applied": bool(getattr(ia2_result, "applied", False)),
        "ia3_triggered": bool(getattr(ia3_result, "triggered", False)),
        "ia3_applied": bool(getattr(ia3_result, "applied", False)),
        "ia2_person_extreme_low": ia2_person is not None and ia2_person <= thresholds["ia2_max_person_score"],
        "ia2_not_person_high": ia2_not_person is not None and ia2_not_person >= thresholds["ia2_min_not_person_score"],
        "ia3_person_extreme_low": ia3_person is not None and ia3_person <= thresholds["ia3_max_person_score"],
        "ia3_not_person_high": ia3_not_person is not None and ia3_not_person >= thresholds["ia3_min_not_person_score"],
        "quality_gate_passed": (not thresholds["require_quality_gate"]) or bool(quality.get("quality_gate_passed")),
        "not_near_border": (not thresholds["require_not_near_border"]) or not bool(quality.get("near_border")),
    }
    candidate = all(checks.values())
    balanced_thresholds = {
        "ia2_max_person_score": settings.consensus_revalidator_balanced_ia2_max_person_score,
        "ia2_min_not_person_score": settings.consensus_revalidator_balanced_ia2_min_not_person_score,
        "ia3_max_person_score": settings.consensus_revalidator_balanced_ia3_max_person_score,
        "ia3_min_not_person_score": settings.consensus_revalidator_balanced_ia3_min_not_person_score,
        "require_quality_gate": settings.consensus_revalidator_balanced_require_quality_gate,
        "require_not_near_border": settings.consensus_revalidator_balanced_require_not_near_border,
    }
    balanced_checks = {
        "enabled": bool(settings.consensus_revalidator_candidate_enabled)
        and bool(settings.consensus_revalidator_balanced_candidate_enabled),
        "ia2_applied": bool(getattr(ia2_result, "applied", False)),
        "ia3_triggered": bool(getattr(ia3_result, "triggered", False)),
        "ia3_applied": bool(getattr(ia3_result, "applied", False)),
        "ia2_person_low": ia2_person is not None and ia2_person <= balanced_thresholds["ia2_max_person_score"],
        "ia2_not_person_high": ia2_not_person is not None and ia2_not_person >= balanced_thresholds["ia2_min_not_person_score"],
        "ia3_person_low": ia3_person is not None and ia3_person <= balanced_thresholds["ia3_max_person_score"],
        "ia3_not_person_high": ia3_not_person is not None and ia3_not_person >= balanced_thresholds["ia3_min_not_person_score"],
        "quality_gate_passed": (not balanced_thresholds["require_quality_gate"])
        or bool(quality.get("quality_gate_passed")),
        "not_near_border": (not balanced_thresholds["require_not_near_border"])
        or not bool(quality.get("near_border")),
    }
    balanced_candidate = all(balanced_checks.values())
    ia3_confirmed_thresholds = {
        "ia2_max_person_score": settings.consensus_revalidator_ia3_confirmed_ia2_max_person_score,
        "ia2_min_not_person_score": settings.consensus_revalidator_ia3_confirmed_ia2_min_not_person_score,
        "ia3_max_person_score": settings.consensus_revalidator_ia3_confirmed_ia3_max_person_score,
        "ia3_min_not_person_score": settings.consensus_revalidator_ia3_confirmed_ia3_min_not_person_score,
        "require_quality_gate": settings.consensus_revalidator_ia3_confirmed_require_quality_gate,
        "require_not_near_border": settings.consensus_revalidator_ia3_confirmed_require_not_near_border,
    }
    ia3_confirmed_checks = {
        "enabled": bool(settings.consensus_revalidator_candidate_enabled)
        and bool(settings.consensus_revalidator_ia3_confirmed_candidate_enabled),
        "ia2_applied": bool(getattr(ia2_result, "applied", False)),
        "ia3_triggered": bool(getattr(ia3_result, "triggered", False)),
        "ia3_applied": bool(getattr(ia3_result, "applied", False)),
        "ia2_person_low": ia2_person is not None and ia2_person <= ia3_confirmed_thresholds["ia2_max_person_score"],
        "ia2_not_person_high": ia2_not_person is not None
        and ia2_not_person >= ia3_confirmed_thresholds["ia2_min_not_person_score"],
        "ia3_person_strong_not_person": ia3_person is not None
        and ia3_person <= ia3_confirmed_thresholds["ia3_max_person_score"],
        "ia3_not_person_strong": ia3_not_person is not None
        and ia3_not_person >= ia3_confirmed_thresholds["ia3_min_not_person_score"],
        "quality_gate_passed": (not ia3_confirmed_thresholds["require_quality_gate"])
        or bool(quality.get("quality_gate_passed")),
        "not_near_border": (not ia3_confirmed_thresholds["require_not_near_border"])
        or not bool(quality.get("near_border")),
    }
    ia3_confirmed_candidate = all(ia3_confirmed_checks.values())
    ia2_dominant_thresholds = {
        "ia2_max_person_score": settings.consensus_revalidator_ia2_dominant_ia2_max_person_score,
        "ia2_min_not_person_score": settings.consensus_revalidator_ia2_dominant_ia2_min_not_person_score,
        "ia3_max_person_score": settings.consensus_revalidator_ia2_dominant_ia3_max_person_score,
        "ia3_min_not_person_score": settings.consensus_revalidator_ia2_dominant_ia3_min_not_person_score,
        "require_quality_gate": settings.consensus_revalidator_ia2_dominant_require_quality_gate,
        "require_not_near_border": settings.consensus_revalidator_ia2_dominant_require_not_near_border,
    }
    ia2_dominant_checks = {
        "enabled": bool(settings.consensus_revalidator_candidate_enabled)
        and bool(settings.consensus_revalidator_ia2_dominant_candidate_enabled),
        "ia2_applied": bool(getattr(ia2_result, "applied", False)),
        "ia3_triggered": bool(getattr(ia3_result, "triggered", False)),
        "ia3_applied": bool(getattr(ia3_result, "applied", False)),
        "ia2_person_very_low": ia2_person is not None and ia2_person <= ia2_dominant_thresholds["ia2_max_person_score"],
        "ia2_not_person_very_high": ia2_not_person is not None
        and ia2_not_person >= ia2_dominant_thresholds["ia2_min_not_person_score"],
        "ia3_does_not_confirm_person": ia3_person is not None
        and ia3_person <= ia2_dominant_thresholds["ia3_max_person_score"],
        "ia3_favors_not_person": ia3_not_person is not None
        and ia3_not_person >= ia2_dominant_thresholds["ia3_min_not_person_score"],
        "quality_gate_passed": (not ia2_dominant_thresholds["require_quality_gate"])
        or bool(quality.get("quality_gate_passed")),
        "not_near_border": (not ia2_dominant_thresholds["require_not_near_border"])
        or not bool(quality.get("near_border")),
    }
    ia2_dominant_candidate = all(ia2_dominant_checks.values())
    ia2_only_thresholds = {
        "ia2_max_person_score": settings.consensus_revalidator_ia2_only_max_person_score,
        "ia2_min_not_person_score": settings.consensus_revalidator_ia2_only_min_not_person_score,
        "require_quality_gate": settings.consensus_revalidator_ia2_only_require_quality_gate,
        "require_not_near_border": settings.consensus_revalidator_ia2_only_require_not_near_border,
    }
    ia2_only_checks = {
        "enabled": bool(settings.consensus_revalidator_candidate_enabled)
        and bool(settings.consensus_revalidator_ia2_only_candidate_enabled),
        "ia2_applied": bool(getattr(ia2_result, "applied", False)),
        "ia3_not_triggered": not bool(getattr(ia3_result, "triggered", False)),
        "ia3_not_applied": not bool(getattr(ia3_result, "applied", False)),
        "ia2_person_low": ia2_person is not None and ia2_person <= ia2_only_thresholds["ia2_max_person_score"],
        "ia2_not_person_high": ia2_not_person is not None
        and ia2_not_person >= ia2_only_thresholds["ia2_min_not_person_score"],
        "quality_gate_passed": (not ia2_only_thresholds["require_quality_gate"])
        or bool(quality.get("quality_gate_passed")),
        "not_near_border": (not ia2_only_thresholds["require_not_near_border"])
        or not bool(quality.get("near_border")),
    }
    ia2_only_candidate = all(ia2_only_checks.values())
    visual_checks_without_quality = [
        "enabled",
        "ia2_applied",
        "ia3_triggered",
        "ia3_applied",
        "ia2_person_extreme_low",
        "ia2_not_person_high",
        "ia3_person_extreme_low",
        "ia3_not_person_high",
        "not_near_border",
    ]
    visual_checks_without_quality_or_border = [
        "enabled",
        "ia2_applied",
        "ia3_triggered",
        "ia3_applied",
        "ia2_person_extreme_low",
        "ia2_not_person_high",
        "ia3_person_extreme_low",
        "ia3_not_person_high",
    ]
    quality_reason = str(quality.get("quality_reason") or "").strip().lower()
    small_quality_reasons = {
        "bbox_width_too_small",
        "bbox_height_too_small",
        "bbox_area_too_small",
        "crop_width_too_small",
        "crop_height_too_small",
    }
    quality_gate_blocked = bool(
        all(bool(checks.get(name)) for name in visual_checks_without_quality)
        and not bool(checks.get("quality_gate_passed"))
    )
    border_blocked = bool(
        all(bool(checks.get(name)) for name in visual_checks_without_quality_or_border)
        and not bool(checks.get("not_near_border"))
    )
    ia2_strong_not_person_without_ia3 = bool(
        bool(settings.far_person_revalidator_suspicious_ia2_enabled)
        and bool(checks.get("enabled"))
        and bool(checks.get("ia2_applied"))
        and ia2_person is not None
        and ia2_person <= float(settings.far_person_revalidator_suspicious_ia2_max_person_score)
        and ia2_not_person is not None
        and ia2_not_person >= float(settings.far_person_revalidator_suspicious_ia2_min_not_person_score)
        and not bool(checks.get("ia3_triggered"))
        and (
            (not bool(settings.far_person_revalidator_suspicious_ia2_require_quality_gate))
            or bool(quality.get("quality_gate_passed"))
        )
        and (
            (not bool(settings.far_person_revalidator_suspicious_ia2_require_not_near_border))
            or not bool(quality.get("near_border"))
        )
    )
    ia2_only_balanced_candidate = bool(
        ia2_only_candidate
        and not candidate
        and not balanced_candidate
        and not ia3_confirmed_candidate
        and not ia2_dominant_candidate
        and not ia2_strong_not_person_without_ia3
    )
    small_bbox_consensus_candidate = bool(
        quality_gate_blocked
        and (
            quality_reason in small_quality_reasons
            or ("too_small" in quality_reason and ("bbox" in quality_reason or "crop" in quality_reason))
        )
    )
    border_consensus_candidate = bool(
        border_blocked
        and (
            "near_border" in quality_reason
            or "border" in quality_reason
            or bool(quality.get("near_border"))
        )
    )
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "enabled": bool(settings.consensus_revalidator_candidate_enabled),
        "mode": "audit",
        "block_candidate": bool(candidate),
        "balanced_block_candidate": bool(
            balanced_candidate
            and not candidate
            and not ia3_confirmed_candidate
            and not ia2_dominant_candidate
        ),
        "ia3_confirmed_dynamic_candidate": bool(ia3_confirmed_candidate and not candidate),
        "ia2_dominant_ia3_non_person_candidate": bool(
            ia2_dominant_candidate
            and not candidate
            and not ia3_confirmed_candidate
        ),
        "ia2_only_balanced_candidate": ia2_only_balanced_candidate,
        "small_bbox_consensus_candidate": small_bbox_consensus_candidate,
        "border_consensus_candidate": border_consensus_candidate,
        "block_blocked_by_quality_gate": quality_gate_blocked,
        "block_blocked_by_border": border_blocked,
        "ia2_strong_not_person_without_ia3": ia2_strong_not_person_without_ia3,
        "operational_decision": (
            "block_candidate_audit"
            if candidate
            else "ia3_confirmed_dynamic_candidate_audit"
            if ia3_confirmed_candidate
            else "ia2_dominant_ia3_non_person_candidate_audit"
            if ia2_dominant_candidate
            else "balanced_block_candidate_audit"
            if balanced_candidate
            else "small_bbox_consensus_audit"
            if small_bbox_consensus_candidate
            else "border_consensus_audit"
            if border_consensus_candidate
            else "ia2_strong_not_person_without_ia3_audit"
            if ia2_strong_not_person_without_ia3
            else "ia2_only_balanced_candidate_audit"
            if ia2_only_balanced_candidate
            else "keep_or_review"
        ),
        "reason": (
            "ia2_ia3_consensus_not_person"
            if candidate
            else "ia3_confirmed_dynamic_not_person"
            if ia3_confirmed_candidate
            else "ia2_dominant_ia3_non_person"
            if ia2_dominant_candidate
            else "balanced_ia2_ia3_consensus_not_person"
            if balanced_candidate
            else "ia2_strong_not_person_without_ia3"
            if ia2_strong_not_person_without_ia3
            else "ia2_only_balanced_not_person"
            if ia2_only_balanced_candidate
            else failed[0]
            if failed
            else "not_candidate"
        ),
        "quality_reason": quality.get("quality_reason"),
        "failed_checks": failed,
        "checks": checks,
        "thresholds": thresholds,
        "balanced_failed_checks": [name for name, passed in balanced_checks.items() if not passed],
        "balanced_checks": balanced_checks,
        "balanced_thresholds": balanced_thresholds,
        "ia3_confirmed_failed_checks": [name for name, passed in ia3_confirmed_checks.items() if not passed],
        "ia3_confirmed_checks": ia3_confirmed_checks,
        "ia3_confirmed_thresholds": ia3_confirmed_thresholds,
        "ia2_dominant_failed_checks": [name for name, passed in ia2_dominant_checks.items() if not passed],
        "ia2_dominant_checks": ia2_dominant_checks,
        "ia2_dominant_thresholds": ia2_dominant_thresholds,
        "ia2_only_failed_checks": [name for name, passed in ia2_only_checks.items() if not passed],
        "ia2_only_checks": ia2_only_checks,
        "ia2_only_thresholds": ia2_only_thresholds,
        "scores": {
            "ia2_person_score": ia2_person,
            "ia2_not_person_score": ia2_not_person,
            "ia3_person_far_score": ia3_person,
            "ia3_not_person_far_score": ia3_not_person,
        },
    }


def evaluate_layered_operational_decision(
    *,
    ia2_result: Any,
    ia3_result: Any,
    consensus_result: dict[str, Any],
    region_memory: dict[str, Any] | None,
) -> dict[str, Any]:
    ia2_person = _score(getattr(ia2_result, "person_score", None))
    ia2_not_person = _score(getattr(ia2_result, "not_person_score", None))
    ia3_person = _score(getattr(ia3_result, "person_far_score", None))
    ia3_not_person = _score(getattr(ia3_result, "not_person_far_score", None))
    region = region_memory or {}
    risk_level = str(region.get("risk_level") or "UNKNOWN")

    strong_person_evidence = bool(
        (ia2_person is not None and ia2_person >= 0.50)
        or (ia3_person is not None and ia3_person >= 0.10)
    )
    visual_not_person_evidence = bool(
        (ia2_not_person is not None and ia2_not_person >= 0.70 and (ia2_person or 0.0) < 0.50)
        or (ia3_not_person is not None and ia3_not_person >= 0.80 and (ia3_person or 0.0) < 0.10)
    )
    block_candidate = bool((consensus_result or {}).get("block_candidate"))
    balanced_block_candidate = bool((consensus_result or {}).get("balanced_block_candidate"))
    ia3_confirmed_dynamic_candidate = bool((consensus_result or {}).get("ia3_confirmed_dynamic_candidate"))
    ia2_dominant_ia3_non_person_candidate = bool(
        (consensus_result or {}).get("ia2_dominant_ia3_non_person_candidate")
    )
    ia2_only_balanced_candidate = bool((consensus_result or {}).get("ia2_only_balanced_candidate"))
    any_visual_candidate = bool(
        block_candidate
        or balanced_block_candidate
        or ia3_confirmed_dynamic_candidate
        or ia2_dominant_ia3_non_person_candidate
        or ia2_only_balanced_candidate
    )
    suppress_candidate = bool(
        not strong_person_evidence
        and visual_not_person_evidence
        and risk_level == "GREEN"
    )

    if strong_person_evidence:
        level = "LEVEL_0_KEEP"
        decision = "keep"
        reason = "strong_person_evidence"
    elif any_visual_candidate and risk_level == "GREEN":
        level = "LEVEL_2_SUPPRESS"
        decision = "suppress_candidate"
        reason = "visual_consensus_in_green_region"
    elif suppress_candidate:
        level = "LEVEL_2_SUPPRESS"
        decision = "suppress_candidate"
        reason = "recurrent_region_with_visual_not_person_evidence"
    elif any_visual_candidate:
        level = "LEVEL_1_UNCERTAIN"
        decision = "block_candidate_audit"
        reason = "visual_consensus_without_green_region"
    else:
        level = "LEVEL_1_UNCERTAIN"
        decision = "uncertain"
        reason = "insufficient_operational_evidence"

    return {
        "mode": "audit",
        "level": level,
        "decision": decision,
        "suppress_candidate": suppress_candidate,
        "block_candidate": any_visual_candidate and risk_level == "GREEN",
        "reason": reason,
        "safety": {
            "strong_person_evidence": strong_person_evidence,
            "visual_not_person_evidence": visual_not_person_evidence,
            "region_risk_level": risk_level,
            "auto_block_enabled": False,
        },
    }
