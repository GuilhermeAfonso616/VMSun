from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TrackingConfig:
    det_threshold_candidate: float = 0.25
    det_threshold_confirm: float = 0.45
    min_frames_to_confirm: int = 3
    max_shadow_age_frames: int = 10
    iou_match_threshold: float = 0.35
    reid_similarity_threshold: float = 0.75
    probation_max_lost_frames: int = 2
    weak_det_threshold: float = 0.18
    max_track_history: int = 60
    max_match_distance_px: float = 120.0
    max_shadow_recovery_distance_px: float = 180.0
    track_quality_min_confirm: float = 0.55
    track_quality_min_event: float = 0.60
    track_quality_smoothing: float = 0.70
    min_motion_px_for_confirm: float = 6.0
    min_motion_px_for_event: float = 4.0
    min_bbox_aspect_ratio: float = 0.22
    max_bbox_aspect_ratio: float = 1.25
    border_margin_ratio: float = 0.06
    border_penalty_strength: float = 0.45


@dataclass(slots=True)
class PerspectiveBand:
    y_min: float = 0.0
    y_max: float = 1.0
    min_bbox_height: float | None = None
    max_bbox_height: float | None = None
    min_bbox_area: float | None = None
    max_bbox_area: float | None = None


@dataclass(slots=True)
class SceneZone:
    zone_id: str
    name: str
    polygon: list[tuple[float, float]]
    zone_type: str = "restricted"
    enabled: bool = True


@dataclass(slots=True)
class DirectionalLine:
    line_id: str
    name: str
    start: tuple[float, float]
    end: tuple[float, float]
    direction: str = "any"
    enabled: bool = True


@dataclass(slots=True)
class SceneConfig:
    exclusion_zones: list[SceneZone] = field(default_factory=list)
    restricted_zones: list[SceneZone] = field(default_factory=list)
    buffer_zones: list[SceneZone] = field(default_factory=list)
    directional_lines: list[DirectionalLine] = field(default_factory=list)
    perspective_profile: list[PerspectiveBand] = field(default_factory=list)
    min_object_size_by_region: dict[str, dict[str, float]] = field(default_factory=dict)
    max_object_size_by_region: dict[str, dict[str, float]] = field(default_factory=dict)
    border_margin_ratio: float = 0.06
    min_bbox_aspect_ratio: float = 0.22
    max_bbox_aspect_ratio: float = 1.25


@dataclass(slots=True)
class RuleConfig:
    rule_id: str
    enabled: bool = True
    rule_type: str = "intrusion_zone"
    target_class: str = "person"
    require_confirmed_track: bool = True
    min_track_age_frames: int = 6
    min_visible_frames: int = 6
    min_dwell_ms: int = 500
    allowed_direction: str | None = None
    prohibited_direction: str | None = None
    min_event_score: float = 0.70
    cooldown_seconds: float = 10.0
    exclusion_zones: list[str] = field(default_factory=list)
    roi_required: bool = False
    ignore_zones_required: bool = False
    full_frame_forbidden: bool = False
    schedule: dict[str, Any] | None = None
    hysteresis_enter: float = 0.75
    hysteresis_exit: float = 0.55
    min_track_quality: float = 0.55
    min_class_consistency: float = 0.55
    min_motion_plausibility: float = 0.25
    min_zone_persistence_frames: int = 2
    min_motion_distance_px: float = 4.0
    min_geometry_confidence: float = 0.45
    block_near_border: bool = True
    max_border_penalty: float = 0.45
    min_aspect_ratio: float = 0.22
    max_aspect_ratio: float = 1.25


@dataclass(slots=True)
class ScoringConfig:
    class_consistency: float = 0.18
    track_stability: float = 0.22
    temporal_persistence: float = 0.18
    size_plausibility: float = 0.16
    motion_plausibility: float = 0.14
    zone_confidence: float = 0.08
    direction_confidence: float = 0.04


@dataclass(slots=True)
class AnalyticsConfig:
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    scene: SceneConfig = field(default_factory=SceneConfig)
    rules: dict[str, RuleConfig] = field(default_factory=dict)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _parse_zone(item: dict[str, Any]) -> SceneZone:
    polygon: list[tuple[float, float]] = []
    for point in item.get("polygon", []) or []:
        try:
            polygon.append((float(point[0]), float(point[1])))
        except Exception:
            continue

    return SceneZone(
        zone_id=str(item.get("zone_id") or item.get("id") or item.get("name") or "zone"),
        name=str(item.get("name") or item.get("zone_id") or "Zone"),
        polygon=polygon,
        zone_type=str(item.get("zone_type") or "restricted"),
        enabled=bool(item.get("enabled", True)),
    )


def _parse_line(item: dict[str, Any]) -> DirectionalLine:
    start = item.get("start") or [item.get("x1"), item.get("y1")]
    end = item.get("end") or [item.get("x2"), item.get("y2")]
    start_point = (float(start[0]), float(start[1])) if start and len(start) >= 2 else (0.0, 0.0)
    end_point = (float(end[0]), float(end[1])) if end and len(end) >= 2 else (1.0, 1.0)
    return DirectionalLine(
        line_id=str(item.get("line_id") or item.get("id") or item.get("name") or "line"),
        name=str(item.get("name") or item.get("line_id") or "Line"),
        start=start_point,
        end=end_point,
        direction=str(item.get("direction") or "any"),
        enabled=bool(item.get("enabled", True)),
    )


def _parse_perspective_band(item: dict[str, Any]) -> PerspectiveBand:
    return PerspectiveBand(
        y_min=float(item.get("y_min", 0.0)),
        y_max=float(item.get("y_max", 1.0)),
        min_bbox_height=_as_float(item.get("min_bbox_height")),
        max_bbox_height=_as_float(item.get("max_bbox_height")),
        min_bbox_area=_as_float(item.get("min_bbox_area")),
        max_bbox_area=_as_float(item.get("max_bbox_area")),
    )


def config_from_mapping(data: dict[str, Any] | None) -> AnalyticsConfig:
    data = data or {}

    tracking_raw = data.get("tracking") or {}
    scene_raw = data.get("scene") or {}
    rules_raw = data.get("rules") or {}
    scoring_raw = data.get("scoring") or {}

    tracking = TrackingConfig(
        det_threshold_candidate=float(tracking_raw.get("det_threshold_candidate", 0.25)),
        det_threshold_confirm=float(tracking_raw.get("det_threshold_confirm", 0.45)),
        min_frames_to_confirm=int(tracking_raw.get("min_frames_to_confirm", 3)),
        max_shadow_age_frames=int(tracking_raw.get("max_shadow_age_frames", 10)),
        iou_match_threshold=float(tracking_raw.get("iou_match_threshold", 0.35)),
        reid_similarity_threshold=float(tracking_raw.get("reid_similarity_threshold", 0.75)),
        probation_max_lost_frames=int(tracking_raw.get("probation_max_lost_frames", 2)),
        weak_det_threshold=float(tracking_raw.get("weak_det_threshold", 0.18)),
        max_track_history=int(tracking_raw.get("max_track_history", 60)),
        max_match_distance_px=float(tracking_raw.get("max_match_distance_px", 120.0)),
        max_shadow_recovery_distance_px=float(tracking_raw.get("max_shadow_recovery_distance_px", 180.0)),
        track_quality_min_confirm=float(tracking_raw.get("track_quality_min_confirm", 0.55)),
        track_quality_min_event=float(tracking_raw.get("track_quality_min_event", 0.60)),
        track_quality_smoothing=float(tracking_raw.get("track_quality_smoothing", 0.70)),
        min_motion_px_for_confirm=float(tracking_raw.get("min_motion_px_for_confirm", 6.0)),
        min_motion_px_for_event=float(tracking_raw.get("min_motion_px_for_event", 4.0)),
        min_bbox_aspect_ratio=float(tracking_raw.get("min_bbox_aspect_ratio", 0.22)),
        max_bbox_aspect_ratio=float(tracking_raw.get("max_bbox_aspect_ratio", 1.25)),
        border_margin_ratio=float(tracking_raw.get("border_margin_ratio", 0.06)),
        border_penalty_strength=float(tracking_raw.get("border_penalty_strength", 0.45)),
    )

    scene = SceneConfig(
        exclusion_zones=[_parse_zone(item) for item in scene_raw.get("exclusion_zones", []) or []],
        restricted_zones=[_parse_zone(item) for item in scene_raw.get("restricted_zones", []) or []],
        buffer_zones=[_parse_zone(item) for item in scene_raw.get("buffer_zones", []) or []],
        directional_lines=[_parse_line(item) for item in scene_raw.get("directional_lines", []) or []],
        perspective_profile=[_parse_perspective_band(item) for item in scene_raw.get("perspective_profile", []) or []],
        min_object_size_by_region=dict(scene_raw.get("min_object_size_by_region", {}) or {}),
        max_object_size_by_region=dict(scene_raw.get("max_object_size_by_region", {}) or {}),
        border_margin_ratio=float(scene_raw.get("border_margin_ratio", 0.06)),
        min_bbox_aspect_ratio=float(scene_raw.get("min_bbox_aspect_ratio", 0.22)),
        max_bbox_aspect_ratio=float(scene_raw.get("max_bbox_aspect_ratio", 1.25)),
    )

    scoring = ScoringConfig(
        class_consistency=float(scoring_raw.get("class_consistency", 0.18)),
        track_stability=float(scoring_raw.get("track_stability", 0.22)),
        temporal_persistence=float(scoring_raw.get("temporal_persistence", 0.18)),
        size_plausibility=float(scoring_raw.get("size_plausibility", 0.16)),
        motion_plausibility=float(scoring_raw.get("motion_plausibility", 0.14)),
        zone_confidence=float(scoring_raw.get("zone_confidence", 0.08)),
        direction_confidence=float(scoring_raw.get("direction_confidence", 0.04)),
    )

    parsed_rules: dict[str, RuleConfig] = {}
    for rule_id, rule_raw in rules_raw.items():
        parsed_rules[str(rule_id)] = RuleConfig(
            rule_id=str(rule_id),
            enabled=bool(rule_raw.get("enabled", True)),
            rule_type=str(rule_raw.get("rule_type") or "intrusion_zone"),
            target_class=str(rule_raw.get("target_class") or "person"),
            require_confirmed_track=bool(rule_raw.get("require_confirmed_track", True)),
            min_track_age_frames=int(rule_raw.get("min_track_age_frames", 6)),
            min_visible_frames=int(rule_raw.get("min_visible_frames", 6)),
            min_dwell_ms=int(rule_raw.get("min_dwell_ms", 500)),
            allowed_direction=rule_raw.get("allowed_direction"),
            prohibited_direction=rule_raw.get("prohibited_direction"),
            min_event_score=float(rule_raw.get("min_event_score", 0.70)),
            cooldown_seconds=float(rule_raw.get("cooldown_seconds", 10.0)),
            exclusion_zones=list(rule_raw.get("exclusion_zones", []) or []),
            roi_required=bool(rule_raw.get("roi_required", False)),
            ignore_zones_required=bool(rule_raw.get("ignore_zones_required", False)),
            full_frame_forbidden=bool(rule_raw.get("full_frame_forbidden", False)),
            schedule=rule_raw.get("schedule"),
            hysteresis_enter=float(rule_raw.get("hysteresis_enter", 0.75)),
            hysteresis_exit=float(rule_raw.get("hysteresis_exit", 0.55)),
            min_track_quality=float(rule_raw.get("min_track_quality", 0.55)),
            min_class_consistency=float(rule_raw.get("min_class_consistency", 0.55)),
            min_motion_plausibility=float(rule_raw.get("min_motion_plausibility", 0.25)),
            min_zone_persistence_frames=int(rule_raw.get("min_zone_persistence_frames", 2)),
            min_motion_distance_px=float(rule_raw.get("min_motion_distance_px", 4.0)),
            min_geometry_confidence=float(rule_raw.get("min_geometry_confidence", 0.45)),
            block_near_border=bool(rule_raw.get("block_near_border", True)),
            max_border_penalty=float(rule_raw.get("max_border_penalty", 0.45)),
            min_aspect_ratio=float(rule_raw.get("min_aspect_ratio", 0.22)),
            max_aspect_ratio=float(rule_raw.get("max_aspect_ratio", 1.25)),
        )

    return AnalyticsConfig(tracking=tracking, scene=scene, rules=parsed_rules, scoring=scoring)
