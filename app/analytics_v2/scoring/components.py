from __future__ import annotations

from dataclasses import dataclass

from ..scene.geometry import bbox_area, bbox_height, size_plausibility_from_profile
from ..tracking.types import Track


@dataclass(slots=True)
class ScoreBreakdown:
    class_consistency: float
    track_stability: float
    temporal_persistence: float
    size_plausibility: float
    motion_plausibility: float
    zone_confidence: float
    direction_confidence: float


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def class_consistency(track: Track) -> float:
    total = sum(track.class_votes.values()) or 1
    dominant = max(track.class_votes.values()) if track.class_votes else 0
    ratio = dominant / float(total)
    unique = max(1, len(track.class_votes))
    confidence = (0.85 * ratio) + (0.15 * (1.0 / float(unique)))
    return clamp01(confidence)


def track_stability(track: Track, shadow_age: int) -> float:
    visible_ratio = track.age_visible_ratio
    lost_penalty = min(1.0, track.lost_frames / float(max(1, shadow_age)))
    association_bonus = clamp01(track.last_association_score)
    quality_bonus = clamp01(track.track_quality or 0.0)
    geometry_bonus = clamp01(getattr(track, "geometry_confidence", 0.0))
    border_bonus = clamp01(getattr(track, "border_confidence", 1.0))
    return clamp01(
        0.40 * visible_ratio
        + 0.20 * association_bonus
        + 0.15 * quality_bonus
        + 0.13 * geometry_bonus
        + 0.05 * border_bonus
        + 0.07 * (1.0 - lost_penalty)
    )


def temporal_persistence(track: Track, min_visible_frames: int) -> float:
    return clamp01(track.visible_frames / float(max(1, min_visible_frames)))


def size_plausibility(
    track: Track,
    fp_y_ratio: float,
    min_size_by_region: dict[str, dict[str, float]] | None = None,
    max_size_by_region: dict[str, dict[str, float]] | None = None,
    perspective_profile: list | None = None,
    *,
    point: tuple[float, float] | None = None,
    frame_width: float | None = None,
    frame_height: float | None = None,
    min_aspect_ratio: float = 0.22,
    max_aspect_ratio: float = 1.25,
    border_margin_ratio: float = 0.06,
) -> float:
    bbox = track.bbox_current
    if not bbox:
        return 0.0
    score = size_plausibility_from_profile(
        bbox,
        fp_y_ratio,
        perspective_profile,
        min_size_by_region=min_size_by_region,
        max_size_by_region=max_size_by_region,
        point=point,
        frame_width=frame_width,
        frame_height=frame_height,
        min_aspect_ratio=min_aspect_ratio,
        max_aspect_ratio=max_aspect_ratio,
        border_margin_ratio=border_margin_ratio,
    )
    if track.size_confidence > 0.0:
        score = (score * 0.8) + (track.size_confidence * 0.2)
    return clamp01(score)


def motion_plausibility(track: Track) -> float:
    if len(track.bbox_history) < 2:
        return 0.45
    recent = [point.footpoint for point in track.bbox_history[-4:]]
    deltas = []
    for idx in range(1, len(recent)):
        ax, ay = recent[idx - 1]
        bx, by = recent[idx]
        deltas.append(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
    if not deltas:
        return 0.45
    step = deltas[-1]
    avg_step = sum(deltas) / float(len(deltas))
    recent_motion = track.recent_motion_distance(window=min(4, len(track.bbox_history)))
    if step <= 1.5 and avg_step <= 2.0:
        return 0.20 if recent_motion < 4.0 else 0.62
    if step <= 12.0 and avg_step <= 15.0:
        return 0.95
    if step <= 30.0 and avg_step <= 35.0:
        return 0.74
    return 0.28


def zone_confidence(track: Track, inside_zone: bool) -> float:
    if any(zone.startswith("exclusion:") for zone in track.zone_history[-3:]):
        return 0.0
    if bool(track.metadata.get("scene_near_border")) and not inside_zone:
        return 0.25
    if inside_zone:
        streak = max((track.recent_zone_streak(zone_id) for zone_id in track.zone_history if zone_id not in {"outside"}), default=1)
        if streak >= 3:
            return 0.98
        if streak >= 2:
            return 0.93
        return 0.88
    if track.zone_history:
        return 0.65
    return 0.4


def direction_confidence(track: Track, allowed_direction: str | None, prohibited_direction: str | None) -> float:
    if not track.direction_estimate:
        return 0.45
    if prohibited_direction and track.direction_estimate == prohibited_direction:
        return 0.1
    if allowed_direction and track.direction_estimate == allowed_direction:
        return 0.95
    if track.recent_line_crossings():
        return 0.75
    return 0.65


def track_quality_score(track: Track, config) -> float:
    class_score = class_consistency(track)
    visible_ratio = track.age_visible_ratio
    score_avg = clamp01(track.score_avg)
    association = clamp01(track.last_association_score)
    motion = clamp01(track.motion_confidence or 0.0)
    if motion <= 0.0:
        motion = motion_plausibility(track)
    geometry = clamp01(getattr(track, "geometry_confidence", 0.0))
    base = (
        0.28 * class_score
        + 0.24 * visible_ratio
        + 0.12 * score_avg
        + 0.18 * association
        + 0.13 * motion
        + 0.15 * geometry
    )
    smoothing = getattr(config, "track_quality_smoothing", 0.70)
    current = track.track_quality if track.track_quality > 0.0 else base
    return clamp01((smoothing * current) + ((1.0 - smoothing) * base))
