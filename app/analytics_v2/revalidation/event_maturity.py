from __future__ import annotations

from math import hypot
from statistics import mean
from typing import Any

from app.core.config import settings


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _score(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _bbox_area(bbox: list[float] | tuple[float, ...] | None) -> float:
    if not bbox or len(bbox) != 4:
        return 0.0
    x1, y1, x2, y2 = [float(value) for value in bbox]
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_center(bbox: list[float] | tuple[float, ...] | None) -> tuple[float, float] | None:
    if not bbox or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = [float(value) for value in bbox]
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _history(track: Any) -> list[Any]:
    try:
        return list(getattr(track, "bbox_history", []) or [])
    except Exception:
        return []


def _history_bbox(point: Any) -> list[float] | None:
    bbox = getattr(point, "bbox", None)
    if bbox and len(bbox) == 4:
        return [float(value) for value in bbox]
    return None


def _duration_seconds(track: Any) -> float:
    first_seen = getattr(track, "first_seen", None)
    last_seen = getattr(track, "last_seen", None)
    try:
        if first_seen and last_seen:
            return max(0.0, (last_seen - first_seen).total_seconds())
    except Exception:
        pass
    return 0.0


def _area_change_ratio(areas: list[float]) -> float:
    positive = [area for area in areas if area > 0.0]
    if len(positive) < 2:
        return 0.0
    return (max(positive) - min(positive)) / max(1.0, mean(positive))


def _center_displacement(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    x1, y1 = points[0]
    x2, y2 = points[-1]
    return hypot(x2 - x1, y2 - y1)


def _frame_diag(frame_width: float | None, frame_height: float | None) -> float:
    width = max(1.0, _score(frame_width, 1.0))
    height = max(1.0, _score(frame_height, 1.0))
    return hypot(width, height)


def _camera_motion_possible(camera_family: str | None, camera_motion_active: bool | None) -> bool:
    if camera_motion_active:
        return True
    family = str(camera_family or "").strip().lower()
    if not family:
        return False
    configured = {
        item.strip().lower()
        for item in str(settings.event_maturity_camera_motion_families or "").split(",")
        if item.strip()
    }
    return family in configured


def evaluate_event_maturity(
    *,
    track: Any | None,
    event: Any | None = None,
    ia2_result: Any | None = None,
    ia3_result: Any | None = None,
    frame_width: int | float | None = None,
    frame_height: int | float | None = None,
    camera_family: str | None = None,
    camera_motion_active: bool | None = None,
) -> dict[str, Any]:
    """Calcula maturidade do evento em modo audit.

    Esta politica nao bloqueia nem suprime; ela mede se o evento tem evidencias
    temporais/movimento suficientes para virar alarme confiavel.
    """

    if not settings.event_maturity_enabled:
        return {"enabled": False, "mode": "audit"}

    if track is None:
        return {
            "enabled": True,
            "mode": "audit",
            "score": 0.0,
            "level": "UNKNOWN",
            "decision": "uncertain",
            "reason": "missing_track",
            "auto_action_enabled": False,
        }

    history = _history(track)
    bboxes = [_history_bbox(point) for point in history]
    bboxes = [bbox for bbox in bboxes if bbox is not None]
    centers = [_bbox_center(bbox) for bbox in bboxes]
    centers = [point for point in centers if point is not None]
    areas = [_bbox_area(bbox) for bbox in bboxes]
    scores = [_score(getattr(point, "score", None), 0.0) for point in history]

    visible_frames = int(getattr(track, "visible_frames", 0) or 0)
    age_frames = int(getattr(track, "age_frames", 0) or visible_frames or 0)
    duration_seconds = _duration_seconds(track)
    displacement_px = _center_displacement(centers)
    displacement_norm = displacement_px / _frame_diag(frame_width, frame_height)
    motion_distance_px = _score(
        track.recent_motion_distance(window=min(10, max(2, len(history)))) if hasattr(track, "recent_motion_distance") else None,
        0.0,
    )
    area_change = _area_change_ratio(areas)
    best_detector_score = max(scores) if scores else _score(getattr(track, "last_detection_score", None), 0.0)
    avg_detector_score = mean(scores) if scores else _score(getattr(track, "score_avg", None), 0.0)
    track_quality = _score(track.effective_quality() if hasattr(track, "effective_quality") else getattr(track, "track_quality", None), 0.0)
    class_consistency = _score(track.class_consistency() if hasattr(track, "class_consistency") else None, 0.0)
    geometry_confidence = _score(getattr(track, "geometry_confidence", None), 0.0)
    ia2_person = _score(getattr(ia2_result, "person_score", None), 0.0)
    ia3_person = _score(getattr(ia3_result, "person_far_score", None), 0.0)
    camera_motion_possible = _camera_motion_possible(camera_family, camera_motion_active)

    frame_score = _clamp01(visible_frames / float(max(1, settings.event_maturity_min_track_frames)))
    duration_score = _clamp01(duration_seconds / float(max(0.01, settings.event_maturity_min_track_seconds)))
    motion_score = _clamp01(displacement_norm / 0.06)
    motion_score_for_maturity = min(motion_score, 0.35) if camera_motion_possible else motion_score
    area_score = _clamp01(area_change / 0.50)
    visual_revalidator_score = max(ia2_person, ia3_person)
    visual_person_score = max(visual_revalidator_score, best_detector_score)

    score = _clamp01(
        0.17 * frame_score
        + 0.14 * duration_score
        + 0.06 * motion_score_for_maturity
        + 0.05 * area_score
        + 0.14 * _clamp01(avg_detector_score)
        + 0.09 * track_quality
        + 0.06 * geometry_confidence
        + 0.26 * visual_revalidator_score
    )

    fast_motion_protected = bool(
        visible_frames < int(settings.event_maturity_min_track_frames)
        and displacement_norm >= float(settings.event_maturity_fast_motion_displacement_norm)
        and (
            best_detector_score >= float(settings.event_maturity_fast_motion_min_detector_score)
            or ia2_person >= 0.20
            or ia3_person >= 0.10
        )
    )
    static_track = bool(
        not camera_motion_possible
        and displacement_norm <= float(settings.event_maturity_static_displacement_norm)
        and area_change <= float(settings.event_maturity_static_area_change_ratio)
        and visible_frames >= max(3, int(settings.event_maturity_min_track_frames))
    )
    enough_temporal_evidence = bool(
        visible_frames >= int(settings.event_maturity_min_track_frames)
        or duration_seconds >= float(settings.event_maturity_min_track_seconds)
    )
    visual_person_confirmed = bool(
        ia2_person >= float(settings.event_maturity_visual_person_threshold)
        or ia3_person >= float(settings.event_maturity_visual_far_person_threshold)
    )
    best_frame_protects_from_suppression = bool(
        ia2_person >= 0.20
        or ia3_person >= 0.10
        or best_detector_score >= 0.85
        or (displacement_norm > 0.04 and not camera_motion_possible)
        or fast_motion_protected
    )
    camera_motion_uncertain = bool(
        camera_motion_possible
        and displacement_norm > float(settings.event_maturity_static_displacement_norm)
        and visual_person_score < 0.85
        and ia2_person < 0.20
        and ia3_person < 0.10
    )

    # Consolidate motion history features
    motion_history = []
    if track is not None and hasattr(track, "metadata") and isinstance(track.metadata, dict):
        motion_history = track.metadata.get("motion_history", []) or []

    motion_areas = [h["motion_area_pct"] for h in motion_history if h.get("has_motion_mask")]
    motion_blobs = [h["motion_blobs"] for h in motion_history if h.get("has_motion_mask")]
    has_mask_list = [h.get("has_motion_mask", False) for h in motion_history]

    motion_confirm_samples = len(motion_history)
    motion_confirm_has_mask = any(has_mask_list) if has_mask_list else False

    from statistics import median
    motion_area_pct_median = float(median(motion_areas)) if motion_areas else 0.0
    motion_blobs_median = float(median(motion_blobs)) if motion_blobs else 0.0

    min_blobs = 2
    min_area_pct = 0.05
    min_displacement = 0.03
    try:
        if settings is not None:
            min_blobs = getattr(settings, "motion_confirm_min_blobs", 2)
            min_area_pct = getattr(settings, "motion_confirm_min_area_pct", 0.05)
            min_displacement = getattr(settings, "motion_confirm_min_displacement", 0.03)
    except Exception:
        pass

    passed_blobs = bool(motion_blobs_median >= min_blobs)
    passed_area = bool(motion_area_pct_median >= min_area_pct)
    passed_disp = bool(displacement_norm >= min_displacement)

    if motion_confirm_has_mask:
        motion_confirm_passed = bool(passed_blobs and passed_area and passed_disp)
    else:
        motion_confirm_passed = True

    boost_min_blobs = int(getattr(settings, "motion_confirm_boost_min_blobs", 5))
    boost_min_area_pct = float(getattr(settings, "motion_confirm_boost_min_area_pct", 0.50))
    motion_confirm_boost = bool(
        motion_confirm_has_mask
        and (
            motion_blobs_median >= boost_min_blobs
            or motion_area_pct_median >= boost_min_area_pct
        )
    )
    motion_confirm_signal = (
        "confirmed" if motion_confirm_boost else "neutral" if motion_confirm_has_mask else "unavailable"
    )
    score = _clamp01(score + (0.03 if motion_confirm_boost else 0.0))

    if fast_motion_protected:
        level = "FAST_MOTION_PROTECTED"
        decision = "low_confidence_alarm"
        reason = "fast_motion_short_track_protected"
    elif camera_motion_uncertain:
        level = "CAMERA_MOTION_UNCERTAIN"
        decision = "uncertain"
        reason = "camera_motion_reduces_motion_confidence"
    elif visual_person_confirmed and enough_temporal_evidence:
        level = "ALARM_READY"
        decision = "alarm_candidate"
        reason = "visual_person_confirmed_with_temporal_evidence"
    elif score >= float(settings.event_maturity_alarm_score):
        level = "ALARM_READY"
        decision = "alarm_candidate"
        reason = "mature_event_evidence"
    elif static_track and not best_frame_protects_from_suppression:
        level = "LOW_MOTION"
        decision = "suppress_candidate_audit"
        reason = "static_track_without_person_protection"
    elif score >= float(settings.event_maturity_low_confidence_score):
        level = "LOW_CONFIDENCE"
        decision = "low_confidence_alarm"
        reason = "partial_event_evidence"
    else:
        level = "EVENT_CANDIDATE"
        decision = "uncertain"
        reason = "immature_event_evidence"

    return {
        "enabled": True,
        "mode": "audit",
        "score": round(score, 4),
        "level": level,
        "decision": decision,
        "reason": reason,
        "auto_action_enabled": False,
        "features": {
            "visible_frames": visible_frames,
            "age_frames": age_frames,
            "duration_seconds": round(duration_seconds, 3),
            "center_displacement_px": round(displacement_px, 3),
            "center_displacement_norm": round(displacement_norm, 6),
            "recent_motion_distance_px": round(motion_distance_px, 3),
            "bbox_area_change_ratio": round(area_change, 6),
            "avg_detector_score": round(float(avg_detector_score), 4),
            "best_detector_score": round(float(best_detector_score), 4),
            "track_quality": round(track_quality, 4),
            "class_consistency": round(class_consistency, 4),
            "geometry_confidence": round(geometry_confidence, 4),
            "ia2_person_score": round(ia2_person, 4),
            "ia3_person_far_score": round(ia3_person, 4),
            "visual_revalidator_score": round(visual_revalidator_score, 4),
            "visual_person_confirmed": visual_person_confirmed,
            "camera_family": camera_family,
            "motion_score_raw": round(motion_score, 4),
            "motion_score_used": round(motion_score_for_maturity, 4),
            "motion_area_pct_median": round(motion_area_pct_median, 4),
            "motion_blobs_median": round(motion_blobs_median, 2),
            "motion_confirm_samples": motion_confirm_samples,
            "motion_confirm_has_mask": motion_confirm_has_mask,
            "motion_confirm_passed": motion_confirm_passed,
            "motion_confirm_passed_blobs": passed_blobs,
            "motion_confirm_passed_area": passed_area,
            "motion_confirm_passed_displacement": passed_disp,
            "motion_confirm_boost": motion_confirm_boost,
            "motion_confirm_signal": motion_confirm_signal,
        },
        "safety": {
            "static_track": static_track,
            "fast_motion_protected": fast_motion_protected,
            "camera_motion_possible": camera_motion_possible,
            "camera_motion_uncertain": camera_motion_uncertain,
            "enough_temporal_evidence": enough_temporal_evidence,
            "best_frame_protects_from_suppression": best_frame_protects_from_suppression,
            "block_allowed": False,
            "suppress_allowed": False,
        },
    }
