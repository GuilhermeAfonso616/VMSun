from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from app.core.config import settings


def _score(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _bool(value: Any) -> bool:
    return bool(value)


def _normalize_bbox(bbox: list[float] | tuple[float, ...] | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    try:
        return [float(value) for value in bbox]
    except Exception:
        return None


def _bbox_height_ratio(bbox: list[float] | tuple[float, ...] | None, frame_height: int | float | None) -> float:
    normalized = _normalize_bbox(bbox)
    if normalized is None or not frame_height:
        return 0.0
    x1, y1, x2, y2 = normalized
    height = max(1.0, float(frame_height))
    return max(0.0, min(1.0, (max(0.0, y2 - y1)) / height))


def _bbox_center(bbox: list[float] | tuple[float, ...] | None) -> tuple[float, float] | None:
    normalized = _normalize_bbox(bbox)
    if normalized is None:
        return None
    x1, y1, x2, y2 = normalized
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _size_bucket(bbox_height_ratio: float) -> str:
    if bbox_height_ratio >= 0.20:
        return "large"
    if bbox_height_ratio >= 0.08:
        return "medium"
    return "small"


def _thresholds_for_size(size_bucket: str) -> dict[str, float]:
    if size_bucket == "large":
        return {
            "ia2_accept": float(settings.strategy3_v2_large_ia2_accept_threshold),
            "ia2_reject": float(settings.strategy3_v2_large_ia2_reject_threshold),
            "ia3_accept": float(settings.strategy3_v2_large_ia3_accept_threshold),
            "ia3_reject": float(settings.strategy3_v2_large_ia3_reject_threshold),
        }
    if size_bucket == "medium":
        return {
            "ia2_accept": float(settings.strategy3_v2_medium_ia2_accept_threshold),
            "ia2_reject": float(settings.strategy3_v2_medium_ia2_reject_threshold),
            "ia3_accept": float(settings.strategy3_v2_medium_ia3_accept_threshold),
            "ia3_reject": float(settings.strategy3_v2_medium_ia3_reject_threshold),
        }
    return {
        "ia2_accept": float(settings.strategy3_v2_small_ia2_accept_threshold),
        "ia2_reject": float(settings.strategy3_v2_small_ia2_reject_threshold),
        "ia3_accept": float(settings.strategy3_v2_small_ia3_accept_threshold),
        "ia3_reject": float(settings.strategy3_v2_small_ia3_reject_threshold),
    }


def _track_history(track: Any | None) -> list[Any]:
    if track is None:
        return []
    try:
        return list(getattr(track, "bbox_history", []) or [])
    except Exception:
        return []


def _point_from_history(point: Any) -> tuple[float, float] | None:
    footpoint = getattr(point, "footpoint", None)
    if footpoint and len(footpoint) == 2:
        try:
            return (float(footpoint[0]), float(footpoint[1]))
        except Exception:
            return None
    bbox = getattr(point, "bbox", None)
    if bbox and len(bbox) == 4:
        try:
            x1, y1, x2, y2 = [float(value) for value in bbox]
            return ((x1 + x2) / 2.0, y2)
        except Exception:
            return None
    return None


def _frame_diag(frame_width: int | float | None, frame_height: int | float | None) -> float:
    try:
        width = max(1.0, float(frame_width or 1.0))
        height = max(1.0, float(frame_height or 1.0))
    except Exception:
        width = height = 1.0
    return (width * width + height * height) ** 0.5


def check_human_motion(
    *,
    track: Any | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
    recent_motion_distance_px: float | None = None,
) -> dict[str, Any]:
    history = _track_history(track)
    points = [_point_from_history(point) for point in history]
    points = [point for point in points if point is not None]

    total_distance = 0.0
    for idx in range(1, len(points)):
        prev = points[idx - 1]
        curr = points[idx]
        dx = curr[0] - prev[0]
        dy = curr[1] - prev[1]
        total_distance += (dx * dx + dy * dy) ** 0.5

    displacement = 0.0
    if len(points) >= 2:
        dx = points[-1][0] - points[0][0]
        dy = points[-1][1] - points[0][1]
        displacement = (dx * dx + dy * dy) ** 0.5

    try:
        recent_motion = float(recent_motion_distance_px) if recent_motion_distance_px is not None else 0.0
    except Exception:
        recent_motion = 0.0
    diag = _frame_diag(frame_width, frame_height)
    displacement_norm = displacement / diag
    direction_consistency = displacement / max(1.0, total_distance) if total_distance > 0.0 else 0.0
    motion_score = min(
        1.0,
        max(0.0, 0.45 * min(1.0, displacement_norm / 0.06))
        + max(0.0, 0.35 * min(1.0, recent_motion / 30.0))
        + max(0.0, 0.20 * min(1.0, direction_consistency)),
    )
    fast_motion_candidate = bool(
        len(points) >= 2
        and (
            displacement_norm >= float(settings.event_maturity_fast_motion_displacement_norm)
            or recent_motion >= float(settings.strategy3_v2_fast_motion_min_recent_motion_px)
        )
        and direction_consistency >= float(settings.strategy3_v2_fast_motion_min_direction_consistency)
    )
    return {
        "available": bool(track is not None and len(points) >= 2),
        "history_len": len(history),
        "point_count": len(points),
        "total_distance_px": round(total_distance, 3),
        "center_displacement_px": round(displacement, 3),
        "center_displacement_norm": round(displacement_norm, 6),
        "recent_motion_distance_px": round(recent_motion, 3),
        "direction_consistency": round(direction_consistency, 4),
        "human_motion_score": round(float(motion_score), 4),
        "fast_motion_candidate": fast_motion_candidate,
        "fast_motion_min_recent_motion_px": float(settings.strategy3_v2_fast_motion_min_recent_motion_px),
        "fast_motion_min_direction_consistency": float(settings.strategy3_v2_fast_motion_min_direction_consistency),
        "fast_motion_min_displacement_norm": float(settings.event_maturity_fast_motion_displacement_norm),
    }


def check_tracking_confirmation(
    *,
    track: Any | None,
    min_track_age: int | None = None,
    min_motion_consistency: float | None = None,
    min_motion_px: float | None = None,
) -> tuple[bool, dict[str, Any]]:
    min_track_age = int(min_track_age if min_track_age is not None else settings.strategy3_v2_tracking_min_track_age)
    min_motion_consistency = float(
        min_motion_consistency if min_motion_consistency is not None else settings.strategy3_v2_tracking_min_motion_consistency
    )
    min_motion_px = float(min_motion_px if min_motion_px is not None else settings.strategy3_v2_tracking_min_motion_px)
    history = _track_history(track)
    track_age = int(getattr(track, "age_frames", 0) or getattr(track, "visible_frames", 0) or len(history) or 0)
    recent_motion = 0.0
    if track is not None and hasattr(track, "recent_motion_distance") and history:
        try:
            recent_motion = float(track.recent_motion_distance(window=min(4, len(history))))
        except Exception:
            recent_motion = 0.0
    motion_consistency = 1.0 if recent_motion >= min_motion_px else 0.0
    confirmed = bool(track is not None and track_age >= min_track_age and len(history) >= min_track_age and motion_consistency >= min_motion_consistency)
    return confirmed, {
        "tracking_available": track is not None,
        "track_age_frames": track_age,
        "track_history_len": len(history),
        "recent_motion_distance_px": round(recent_motion, 3),
        "motion_consistency": round(motion_consistency, 3),
        "tracking_confirmed": confirmed,
        "min_track_age": min_track_age,
        "min_motion_consistency": min_motion_consistency,
        "min_motion_px": min_motion_px,
    }


def check_temporal_persistence(
    *,
    track: Any | None,
    timestamp: datetime | None = None,
    min_hits: int | None = None,
    window_seconds: float | None = None,
) -> tuple[bool, dict[str, Any]]:
    min_hits = int(min_hits if min_hits is not None else settings.strategy3_v2_temporal_min_hits)
    window_seconds = float(window_seconds if window_seconds is not None else settings.strategy3_v2_temporal_window_seconds)
    history = _track_history(track)
    visible_frames = int(getattr(track, "visible_frames", 0) or 0)
    age_frames = int(getattr(track, "age_frames", 0) or visible_frames or len(history) or 0)
    first_seen = getattr(track, "first_seen", None)
    last_seen = getattr(track, "last_seen", None)
    duration_seconds = 0.0
    try:
        if first_seen and last_seen:
            duration_seconds = max(0.0, (last_seen - first_seen).total_seconds())
    except Exception:
        duration_seconds = 0.0

    if duration_seconds <= 0.0 and timestamp is not None and first_seen is not None:
        try:
            duration_seconds = max(0.0, (timestamp - first_seen).total_seconds())
        except Exception:
            pass

    temporal_persistence = bool(
        (len(history) >= min_hits and duration_seconds <= window_seconds) or visible_frames >= min_hits or age_frames >= min_hits
    )
    return temporal_persistence, {
        "temporal_persistence": temporal_persistence,
        "min_hits": min_hits,
        "window_seconds": window_seconds,
        "visible_frames": visible_frames,
        "track_age_frames": age_frames,
        "duration_seconds": round(duration_seconds, 3),
        "history_len": len(history),
    }


def _patterns_for_camera(anti_fp_patterns: dict[str, Any] | None, camera_id: int | None) -> dict[str, Any]:
    if not anti_fp_patterns:
        return {}
    if camera_id is None:
        return anti_fp_patterns.get("default", {}) if isinstance(anti_fp_patterns, dict) else {}
    camera_key = str(camera_id)
    return anti_fp_patterns.get(camera_key) or anti_fp_patterns.get(f"cam_{camera_key}") or anti_fp_patterns.get("default", {})


def load_anti_fp_patterns(source: str | dict[str, Any] | None = None) -> dict[str, Any] | None:
    raw_source = source if source is not None else settings.anti_fp_patterns_json
    if isinstance(raw_source, dict):
        return raw_source

    raw = str(raw_source or "").strip()
    if not raw:
        return None

    try:
        if raw.startswith("{"):
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None

        path = Path(raw[1:] if raw.startswith("@") else raw)
        if not path.is_absolute():
            path = Path(settings.app_base_dir) / path
        if not path.exists():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _bbox_center_norm(bbox: list[float] | tuple[float, ...] | None, frame_width: int | float | None, frame_height: int | float | None) -> tuple[float, float] | None:
    center = _bbox_center(bbox)
    if center is None or not frame_width or not frame_height:
        return None
    width = max(1.0, float(frame_width))
    height = max(1.0, float(frame_height))
    return (center[0] / width, center[1] / height)


def _match_region_rule(rule: dict[str, Any], center_norm: tuple[float, float] | None) -> bool:
    if center_norm is None:
        return False
    try:
        x = float(center_norm[0])
        y = float(center_norm[1])
        x1 = float(rule.get("x1", 0.0))
        y1 = float(rule.get("y1", 0.0))
        x2 = float(rule.get("x2", 1.0))
        y2 = float(rule.get("y2", 1.0))
    except Exception:
        return False
    return x1 <= x <= x2 and y1 <= y <= y2


def check_pattern_blacklist(
    *,
    camera_id: int | None,
    bbox: list[float] | tuple[float, ...] | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
    anti_fp_patterns: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    camera_patterns = _patterns_for_camera(anti_fp_patterns, camera_id)
    rules = list(camera_patterns.get("blacklist_regions", []) or [])
    center_norm = _bbox_center_norm(bbox, frame_width, frame_height)
    matched = next((rule for rule in rules if isinstance(rule, dict) and _match_region_rule(rule, center_norm)), None)
    return bool(matched), {
        "pattern_blacklist_match": bool(matched),
        "pattern_blacklist_rule": matched.get("name") if isinstance(matched, dict) else None,
        "pattern_blacklist_action": matched.get("action") if isinstance(matched, dict) else None,
    }


def check_pattern_whitelist(
    *,
    camera_id: int | None,
    bbox: list[float] | tuple[float, ...] | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
    anti_fp_patterns: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    camera_patterns = _patterns_for_camera(anti_fp_patterns, camera_id)
    rules = list(camera_patterns.get("whitelist_regions", []) or [])
    center_norm = _bbox_center_norm(bbox, frame_width, frame_height)
    matched = next((rule for rule in rules if isinstance(rule, dict) and _match_region_rule(rule, center_norm)), None)
    return bool(matched), {
        "pattern_whitelist_match": bool(matched),
        "pattern_whitelist_rule": matched.get("name") if isinstance(matched, dict) else None,
        "pattern_whitelist_action": matched.get("action") if isinstance(matched, dict) else None,
    }


def get_region_fp_risk(
    *,
    camera_id: int | None,
    bbox: list[float] | tuple[float, ...] | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
    region_memory: dict[str, Any] | None = None,
    anti_fp_patterns: dict[str, Any] | None = None,
) -> dict[str, Any]:
    region = dict(region_memory or {})
    if not region:
        region = {
            "enabled": bool(settings.region_memory_enabled),
            "risk_level": "UNKNOWN",
            "decision_hint": "no_region_memory",
        }

    center_norm = _bbox_center_norm(bbox, frame_width, frame_height)
    cell = None
    if center_norm is not None:
        cols = max(1, int(settings.strategy3_v2_region_grid_cols or settings.region_memory_grid_cols or 8))
        rows = max(1, int(settings.strategy3_v2_region_grid_rows or settings.region_memory_grid_rows or 8))
        cell_x = min(cols - 1, max(0, int(center_norm[0] * cols)))
        cell_y = min(rows - 1, max(0, int(center_norm[1] * rows)))
        cell = f"{camera_id}:{cell_x:02d}:{cell_y:02d}"

    blacklist_match, blacklist_meta = check_pattern_blacklist(
        camera_id=camera_id,
        bbox=bbox,
        frame_width=frame_width,
        frame_height=frame_height,
        anti_fp_patterns=anti_fp_patterns,
    )
    whitelist_match, whitelist_meta = check_pattern_whitelist(
        camera_id=camera_id,
        bbox=bbox,
        frame_width=frame_width,
        frame_height=frame_height,
        anti_fp_patterns=anti_fp_patterns,
    )

    raw_risk_level = str(region.get("risk_level") or "UNKNOWN").upper()
    decision_hint = str(region.get("decision_hint") or "").strip().lower()
    false_positive_count = int(region.get("false_positive_count") or 0)
    true_positive_count = int(region.get("true_positive_count") or 0)
    total_reviewed_count = int(region.get("total_reviewed_count") or 0)
    try:
        false_positive_rate = float(region.get("false_positive_rate"))
    except Exception:
        false_positive_rate = (
            false_positive_count / total_reviewed_count
            if total_reviewed_count
            else 0.0
        )
    high_fp_rate = max(0.0, min(1.0, float(settings.region_memory_high_fp_rate_threshold or 0.70)))
    high_fp_history = bool(
        false_positive_count >= int(settings.region_memory_green_min_false_positive_count or 3)
        and false_positive_rate >= high_fp_rate
    )

    if whitelist_match:
        risk_level = "LOW"
        person_support = True
    elif blacklist_match:
        risk_level = "HIGH"
        person_support = False
    elif decision_hint == "recurrent_false_positive_region" or raw_risk_level == "GREEN" or high_fp_history:
        risk_level = "HIGH"
        person_support = False
    elif decision_hint == "some_false_positive_history" or (raw_risk_level == "YELLOW" and false_positive_count > 0):
        risk_level = "YELLOW"
        person_support = False
    elif decision_hint == "person_seen_in_region" or (raw_risk_level == "RED" and true_positive_count > 0):
        risk_level = "LOW"
        person_support = True
    else:
        risk_level = raw_risk_level
        person_support = False

    if risk_level == "GREEN":
        risk_level = "LOW"

    region_risk_score = {
        "HIGH": 1.0,
        "RED": 0.9,
        "YELLOW": 0.5,
        "GREEN": 0.1,
        "LOW": 0.1,
        "UNKNOWN": 0.0,
    }.get(risk_level, 0.0)

    return {
        "enabled": bool(region.get("enabled", settings.region_memory_enabled)),
        "camera_id": camera_id,
        "region_cell": region.get("region_cell") or cell,
        "raw_region_risk_level": raw_risk_level,
        "risk_level": risk_level,
        "fp_risk_level": risk_level,
        "risk_score": region_risk_score,
        "person_support": person_support,
        "decision_hint": region.get("decision_hint") or "unknown",
        "region_memory": region,
        "pattern_blacklist_match": blacklist_meta.get("pattern_blacklist_match", False),
        "pattern_blacklist_rule": blacklist_meta.get("pattern_blacklist_rule"),
        "pattern_blacklist_action": blacklist_meta.get("pattern_blacklist_action"),
        "pattern_whitelist_match": whitelist_meta.get("pattern_whitelist_match", False),
        "pattern_whitelist_rule": whitelist_meta.get("pattern_whitelist_rule"),
        "pattern_whitelist_action": whitelist_meta.get("pattern_whitelist_action"),
    }


def _independent_confirmation(
    *,
    ia3_person_score: float | None,
    ia3_accept_threshold: float,
    tracking_confirmed: bool,
    temporal_persistence: bool,
    allow_temporal_alone: bool = False,
) -> str:
    if ia3_person_score is not None and ia3_person_score >= ia3_accept_threshold:
        return "ia3"
    if tracking_confirmed and temporal_persistence:
        return "tracking_temporal"
    if tracking_confirmed:
        return "tracking"
    if temporal_persistence and allow_temporal_alone:
        return "temporal"
    return "none"


def evaluate_strategy3_v2(
    *,
    ia2_result: Any | None,
    ia3_result: Any | None,
    detector_score: float | None,
    bbox: list[float] | tuple[float, ...] | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
    camera_id: int | None = None,
    track: Any | None = None,
    timestamp: datetime | None = None,
    region_memory: dict[str, Any] | None = None,
    anti_fp_patterns: dict[str, Any] | None = None,
    shadow_discordance_count: int = 0,
) -> dict[str, Any]:
    if not bool(settings.strategy3_v2_enabled):
        return {
            "enabled": False,
            "mode": str(settings.strategy3_v2_mode or "audit"),
            "decision": "UNCERTAIN",
            "reason": "strategy3_v2_disabled",
            "initial_decision": "UNCERTAIN",
            "notification_decision": "SUPPRESS",
            "final_notification_level": "suppressed",
            "detector_used_for_accept": False,
        }

    bbox = _normalize_bbox(bbox)
    bbox_height_ratio = _bbox_height_ratio(bbox, frame_height)
    size_bucket = _size_bucket(bbox_height_ratio)
    thresholds = _thresholds_for_size(size_bucket)
    ia2_person = _score(getattr(ia2_result, "person_score", None))
    ia2_not_person = _score(getattr(ia2_result, "not_person_score", None))
    ia3_person = _score(getattr(ia3_result, "person_far_score", None))
    ia3_not_person = _score(getattr(ia3_result, "not_person_far_score", None))
    ia3_available = bool(getattr(ia3_result, "applied", False) or getattr(ia3_result, "triggered", False))
    detector_score = _score(detector_score, 0.0) or 0.0

    tracking_confirmed, tracking_meta = check_tracking_confirmation(track=track)
    temporal_persistence, temporal_meta = check_temporal_persistence(track=track, timestamp=timestamp)
    human_motion = check_human_motion(
        track=track,
        frame_width=frame_width,
        frame_height=frame_height,
        recent_motion_distance_px=tracking_meta.get("recent_motion_distance_px"),
    )
    static_track = bool(
        tracking_meta.get("tracking_available")
        and temporal_persistence
        and not tracking_confirmed
        and float(tracking_meta.get("recent_motion_distance_px") or 0.0) < float(tracking_meta.get("min_motion_px") or settings.strategy3_v2_tracking_min_motion_px)
    )
    region_meta = get_region_fp_risk(
        camera_id=camera_id,
        bbox=bbox,
        frame_width=frame_width,
        frame_height=frame_height,
        region_memory=region_memory,
        anti_fp_patterns=anti_fp_patterns,
    )
    blacklist_match = bool(region_meta.get("pattern_blacklist_match"))
    whitelist_match = bool(region_meta.get("pattern_whitelist_match"))
    independent_confirmation = _independent_confirmation(
        ia3_person_score=ia3_person,
        ia3_accept_threshold=thresholds["ia3_accept"],
        tracking_confirmed=tracking_confirmed,
        temporal_persistence=temporal_persistence,
        allow_temporal_alone=False,
    )
    visual_hard_reject = bool(
        ia2_person is not None
        and ia2_person < thresholds["ia2_reject"]
        and detector_score < float(settings.strategy3_v2_detector_strong_threshold)
        and (
            not ia3_available
            or (ia3_person is not None and ia3_person < thresholds["ia3_reject"])
        )
    )
    fast_motion_protected = bool(human_motion.get("fast_motion_candidate") and not visual_hard_reject)

    initial_decision = "UNCERTAIN"
    decision = "LOW_PRIORITY"
    reason = f"{size_bucket}_gray_zone_no_confirmation"
    detector_used_for_accept = False

    if ia2_person is None:
        initial_decision = "VISUAL_REVALIDATOR_UNAVAILABLE"
        if blacklist_match or region_meta.get("risk_level") in {"HIGH", "RED"}:
            decision = "AUDIT"
            reason = f"{size_bucket}_ia2_unavailable_risky_region_requires_audit"
        elif detector_score >= float(settings.strategy3_v2_detector_strong_threshold):
            decision = "ACCEPT"
            reason = f"{size_bucket}_ia2_unavailable_detector_strong_fail_open"
            detector_used_for_accept = True
        elif tracking_confirmed:
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_ia2_unavailable_tracking_confirmed_low_priority"
            independent_confirmation = "tracking"
        elif temporal_persistence:
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_ia2_unavailable_temporal_only"
            independent_confirmation = "temporal"
        else:
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_ia2_unavailable_unconfirmed_low_priority"
    elif ia2_person >= thresholds["ia2_accept"]:
        initial_decision = "ACCEPT_CANDIDATE"
        if blacklist_match or region_meta.get("risk_level") in {"HIGH", "RED"}:
            decision = "AUDIT"
            reason = f"{size_bucket}_ia2_strong_accept_risky_region"
        elif independent_confirmation == "ia3":
            decision = "ACCEPT"
            reason = f"{size_bucket}_ia2_strong_accept_confirmed_by_ia3"
        elif independent_confirmation == "tracking":
            decision = "ACCEPT"
            reason = f"{size_bucket}_ia2_strong_accept_confirmed_by_tracking"
        elif independent_confirmation == "tracking_temporal":
            decision = "ACCEPT"
            reason = f"{size_bucket}_ia2_strong_accept_confirmed_by_tracking_temporal"
        elif independent_confirmation == "temporal":
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_ia2_strong_accept_temporal_only"
        else:
            decision = "ACCEPT"
            reason = f"{size_bucket}_ia2_strong_accept_unconfirmed"
    elif ia2_person < thresholds["ia2_reject"]:
        initial_decision = "REJECT_OR_SUPPRESS_CANDIDATE"
        if ia3_available and ia3_person is not None and ia3_person >= thresholds["ia3_accept"]:
            decision = "ACCEPT"
            reason = f"{size_bucket}_ia2_failed_but_ia3_rescued"
            independent_confirmation = "ia3"
        elif tracking_confirmed:
            decision = "ACCEPT"
            reason = f"{size_bucket}_ia2_failed_but_tracking_rescued"
            independent_confirmation = "tracking"
        elif temporal_persistence:
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_ia2_failed_temporal_only"
            independent_confirmation = "temporal"
        elif (
            size_bucket in {"large", "medium"}
            and not ia3_available
            and detector_score >= float(settings.strategy3_v2_detector_strong_threshold)
        ):
            decision = "AUDIT"
            reason = f"{size_bucket}_ia2_hard_fail_detector_strong_requires_audit"
        elif ia3_available and ia3_person is not None and ia3_person < thresholds["ia3_reject"]:
            decision = "SUPPRESS"
            reason = f"{size_bucket}_ia2_hard_fail_ia3_reject"
        else:
            decision = "SUPPRESS"
            reason = f"{size_bucket}_ia2_hard_fail_suppressed"
    else:
        initial_decision = "UNCERTAIN"
        if ia3_available and ia3_person is not None and ia3_person >= thresholds["ia3_accept"]:
            decision = "ACCEPT"
            reason = f"{size_bucket}_gray_zone_accepted_by_ia3"
            independent_confirmation = "ia3"
        elif ia3_available and ia3_person is not None and ia3_person < thresholds["ia3_reject"]:
            if size_bucket in {"large", "medium"} and detector_score >= float(settings.strategy3_v2_detector_strong_threshold):
                decision = "AUDIT"
                reason = f"{size_bucket}_gray_zone_weak_ia3_detector_strong_requires_audit"
            else:
                decision = "SUPPRESS"
                reason = f"{size_bucket}_gray_zone_suppressed_by_ia3_weak"
        elif blacklist_match or region_meta.get("risk_level") in {"HIGH", "RED"}:
            decision = "SUPPRESS"
            reason = f"{size_bucket}_gray_zone_suppressed_by_region_memory"
        elif tracking_confirmed and temporal_persistence:
            decision = "ACCEPT"
            reason = f"{size_bucket}_gray_zone_accepted_by_tracking_temporal"
            independent_confirmation = "tracking_temporal"
        elif tracking_confirmed:
            decision = "ACCEPT"
            reason = f"{size_bucket}_gray_zone_accepted_by_tracking"
            independent_confirmation = "tracking"
        elif temporal_persistence:
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_gray_zone_temporal_only"
            independent_confirmation = "temporal"
        elif detector_score >= float(settings.strategy3_v2_detector_strong_threshold):
            decision = "AUDIT"
            reason = f"{size_bucket}_detector_strong_without_independent_confirmation"
        else:
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_gray_zone_no_confirmation"

    if fast_motion_protected and decision == "SUPPRESS":
        decision = "LOW_PRIORITY"
        reason = f"{size_bucket}_fast_motion_protected_from_suppress"

    human_motion_score = float(human_motion.get("human_motion_score") or 0.0)

    if (
        bool(settings.strategy3_v2_shadow_discordance_downgrade_enabled)
        and decision == "ACCEPT"
        and shadow_discordance_count >= 2
    ):
        if human_motion_score >= float(settings.strategy3_v2_shadow_discordant_motion_rescue):
            decision = "AUDIT"
            reason = f"{size_bucket}_ia2_strong_accept_shadow_discordant_weak_signal"
        else:
            decision = "SUPPRESS"
            reason = f"{size_bucket}_ia2_strong_accept_shadow_discordant_no_motion"

    if (
        decision == "ACCEPT"
        and independent_confirmation in {"tracking", "tracking_temporal"}
        and (ia2_person is None or ia2_person < float(settings.strategy3_v2_tracking_weak_motion_ia2_rescue))
    ):
        motion_floor = {
            "large": float(settings.strategy3_v2_large_tracking_min_human_motion),
            "medium": float(settings.strategy3_v2_medium_tracking_min_human_motion),
            "small": float(settings.strategy3_v2_small_tracking_min_human_motion),
        }.get(size_bucket, float(settings.strategy3_v2_medium_tracking_min_human_motion))
        if human_motion_score < motion_floor:
            decision = "LOW_PRIORITY"
            reason = f"{size_bucket}_{independent_confirmation}_weak_motion"

    return {
        "enabled": True,
        "mode": str(settings.strategy3_v2_mode or "audit").strip().lower(),
        "initial_decision": initial_decision,
        "decision": decision,
        "reason": reason,
        "classification_decision": decision,
        "size_bucket": size_bucket,
        "bbox_height_ratio": round(float(bbox_height_ratio), 6),
        "detector_score": round(float(detector_score), 6),
        "ia2_person_score": ia2_person,
        "ia2_not_person_score": ia2_not_person,
        "ia3_available": ia3_available,
        "ia3_person_score": ia3_person,
        "ia3_not_person_score": ia3_not_person,
        "ia2_accept_threshold": thresholds["ia2_accept"],
        "ia2_reject_threshold": thresholds["ia2_reject"],
        "ia3_accept_threshold": thresholds["ia3_accept"],
        "ia3_reject_threshold": thresholds["ia3_reject"],
        "detector_used_for_accept": detector_used_for_accept,
        "independent_confirmation": independent_confirmation,
        "tracking_confirmed": tracking_confirmed,
        "temporal_persistence": temporal_persistence,
        "static_track": static_track,
        "fast_motion_protected": fast_motion_protected,
        "human_motion_score": human_motion.get("human_motion_score", 0.0),
        "region_fp_risk": region_meta.get("risk_level", "UNKNOWN"),
        "region_fp_risk_score": region_meta.get("risk_score", 0.0),
        "pattern_blacklist_match": blacklist_match,
        "pattern_whitelist_match": whitelist_match,
        "tracking": tracking_meta,
        "temporal": temporal_meta,
        "human_motion": human_motion,
        "region": region_meta,
        "signals": {
            "tracking_available": tracking_meta.get("tracking_available", False),
            "tracking_confirmed": tracking_confirmed,
            "static_track": static_track,
            "fast_motion_protected": fast_motion_protected,
            "human_motion_score": human_motion.get("human_motion_score", 0.0),
            "human_motion_direction_consistency": human_motion.get("direction_consistency", 0.0),
            "recent_motion_distance_px": tracking_meta.get("recent_motion_distance_px", 0.0),
            "tracking_min_motion_px": tracking_meta.get("min_motion_px", settings.strategy3_v2_tracking_min_motion_px),
            "temporal_persistence": temporal_persistence,
            "region_fp_risk": region_meta.get("risk_level", "UNKNOWN"),
            "pattern_blacklist_match": blacklist_match,
            "pattern_whitelist_match": whitelist_match,
            "ia3_available": ia3_available,
            "ia3_confirmed": independent_confirmation == "ia3",
            "independent_confirmation": independent_confirmation,
        },
    }


def anti_fp_post_filter(
    *,
    strategy_result: dict[str, Any],
    event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_context = event_context or {}
    if not bool(settings.anti_fp_post_filter_enabled):
        return {
            "enabled": False,
            "mode": str(settings.anti_fp_post_filter_mode or "audit").strip().lower(),
            "decision": "NOTIFY" if strategy_result.get("decision") == "ACCEPT" else "SUPPRESS",
            "reason": "anti_fp_post_filter_disabled",
            "risk_score": 0.0,
            "final_notification_level": "normal" if strategy_result.get("decision") == "ACCEPT" else "suppressed",
            "signals": {},
        }

    strategy_decision = str(strategy_result.get("decision") or "UNCERTAIN").upper()
    if strategy_decision == "AUDIT":
        return {
            "enabled": True,
            "mode": str(settings.anti_fp_post_filter_mode or "audit").strip().lower(),
            "decision": "AUDIT",
            "reason": "strategy_audit",
            "risk_score": 1.0,
            "final_notification_level": "audit_only",
            "signals": dict(strategy_result.get("signals") or {}),
        }
    if strategy_decision == "LOW_PRIORITY":
        return {
            "enabled": True,
            "mode": str(settings.anti_fp_post_filter_mode or "audit").strip().lower(),
            "decision": "LOW_PRIORITY",
            "reason": "strategy_low_priority",
            "risk_score": 1.0,
            "final_notification_level": "low_priority",
            "signals": dict(strategy_result.get("signals") or {}),
        }
    if strategy_decision not in {"ACCEPT", "ACCEPT_CANDIDATE"}:
        return {
            "enabled": True,
            "mode": str(settings.anti_fp_post_filter_mode or "audit").strip().lower(),
            "decision": "SUPPRESS",
            "reason": "strategy_not_accept",
            "risk_score": 1.0,
            "final_notification_level": "suppressed",
            "signals": {},
        }

    signals = dict(strategy_result.get("signals") or {})
    signals.update(event_context.get("signals") or {})
    risk_score = 0.0
    reasons: list[str] = []

    # Deteccao que a IA2 confirma como pessoa nao deve ser penalizada por estar
    # parada: loitering / parado num portao e evento legitimo. As penalidades de
    # baixa-movimentacao so valem quando a aparencia esta incerta. Regiao/ROI
    # (blacklist) continuam valendo sempre, pois sao intencao do operador.
    ia2_person_score = _score(strategy_result.get("ia2_person_score"))
    appearance_confident = (
        ia2_person_score is not None
        and ia2_person_score >= float(getattr(settings, "anti_fp_post_filter_still_penalty_ia2_bypass", 0.5))
    )

    if str(signals.get("region_fp_risk") or strategy_result.get("region_fp_risk") or "UNKNOWN").upper() in {"HIGH", "RED"}:
        risk_score += float(settings.anti_fp_post_filter_high_region_weight)
        reasons.append("high_fp_region")

    if bool(signals.get("pattern_blacklist_match")):
        risk_score += float(settings.anti_fp_post_filter_blacklist_weight)
        reasons.append("blacklist_region_match")

    if not appearance_confident:
        if not bool(signals.get("temporal_persistence")):
            risk_score += float(settings.anti_fp_post_filter_no_temporal_weight)
            reasons.append("no_temporal_persistence")

        if signals.get("tracking_available") and not bool(signals.get("tracking_confirmed")):
            risk_score += float(settings.anti_fp_post_filter_tracking_not_confirmed_weight)
            reasons.append("tracking_not_confirmed")

        if bool(signals.get("static_track")):
            risk_score += float(settings.anti_fp_post_filter_static_track_weight)
            reasons.append("static_track")
    else:
        reasons.append("still_penalty_bypassed_ia2_confident")

    if bool(signals.get("fast_motion_protected")):
        risk_score += float(settings.anti_fp_post_filter_fast_motion_bonus)
        reasons.append("fast_motion_protected")

    if bool(signals.get("ia3_confirmed")):
        risk_score += float(settings.anti_fp_post_filter_ia3_confirmed_bonus)
        reasons.append("ia3_confirmed")

    if bool(signals.get("pattern_whitelist_match")):
        risk_score -= 0.10
        reasons.append("whitelist_support")

    risk_score = max(0.0, min(1.0, risk_score))
    if risk_score >= float(settings.anti_fp_post_filter_suppress_threshold):
        decision = "SUPPRESS"
        final_level = "suppressed"
    elif risk_score >= float(settings.anti_fp_post_filter_audit_threshold):
        decision = "AUDIT"
        final_level = "audit_only"
    elif risk_score >= float(settings.anti_fp_post_filter_low_priority_threshold):
        decision = "LOW_PRIORITY"
        final_level = "low_priority"
    else:
        decision = "NOTIFY"
        final_level = "normal"

    if strategy_result.get("decision") in {"AUDIT", "LOW_PRIORITY", "SUPPRESS", "REJECT", "UNCERTAIN"} and decision == "NOTIFY":
        decision = "LOW_PRIORITY"
        final_level = "low_priority"

    return {
        "enabled": True,
        "mode": str(settings.anti_fp_post_filter_mode or "audit").strip().lower(),
        "decision": decision,
        "reason": ",".join(reasons) if reasons else "no_anti_fp_risk",
        "risk_score": round(float(risk_score), 4),
        "final_notification_level": final_level,
        "signals": {
            "temporal_persistence": bool(signals.get("temporal_persistence")),
            "tracking_available": bool(signals.get("tracking_available")),
            "tracking_confirmed": bool(signals.get("tracking_confirmed")),
            "static_track": bool(signals.get("static_track")),
            "fast_motion_protected": bool(signals.get("fast_motion_protected")),
            "human_motion_score": signals.get("human_motion_score"),
            "human_motion_direction_consistency": signals.get("human_motion_direction_consistency"),
            "recent_motion_distance_px": signals.get("recent_motion_distance_px"),
            "tracking_min_motion_px": signals.get("tracking_min_motion_px"),
            "region_fp_risk": signals.get("region_fp_risk") or strategy_result.get("region_fp_risk") or "UNKNOWN",
            "pattern_blacklist_match": bool(signals.get("pattern_blacklist_match")),
            "pattern_whitelist_match": bool(signals.get("pattern_whitelist_match")),
            "ia3_required": bool(signals.get("ia3_available")),
            "ia3_available": bool(signals.get("ia3_available")),
            "ia3_confirmed": strategy_result.get("independent_confirmation") == "ia3",
        },
    }


def build_strategy3_v2_review_payload(
    *,
    ia2_result: Any | None,
    ia3_result: Any | None,
    detector_score: float | None,
    bbox: list[float] | tuple[float, ...] | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
    camera_id: int | None = None,
    track: Any | None = None,
    timestamp: datetime | None = None,
    event: Any | None = None,
    region_memory: dict[str, Any] | None = None,
    anti_fp_patterns: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shadow_discordance = getattr(event, "metadata", None) if event is not None else None
    shadow_discordance_count = (
        len(shadow_discordance.get("person_revalidator_shadow_discordance") or [])
        if isinstance(shadow_discordance, dict)
        else 0
    )
    strategy_result = evaluate_strategy3_v2(
        ia2_result=ia2_result,
        ia3_result=ia3_result,
        detector_score=detector_score,
        bbox=bbox,
        frame_width=frame_width,
        frame_height=frame_height,
        camera_id=camera_id,
        track=track,
        timestamp=timestamp,
        region_memory=region_memory,
        anti_fp_patterns=anti_fp_patterns,
        shadow_discordance_count=shadow_discordance_count,
    )
    anti_fp_result = anti_fp_post_filter(
        strategy_result=strategy_result,
        event_context={
            "event": event,
            "camera_id": camera_id,
            "signals": strategy_result.get("signals") or {},
        },
    )
    strategy_result["anti_fp_post_filter"] = anti_fp_result
    strategy_result["notification_decision"] = anti_fp_result.get("decision")
    strategy_result["final_notification_level"] = anti_fp_result.get("final_notification_level")
    return strategy_result
