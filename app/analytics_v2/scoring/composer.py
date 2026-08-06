from __future__ import annotations

from dataclasses import dataclass

from ..config.schema import ScoringConfig
from ..scene.geometry import bbox_footpoint, normalize_footpoint, point_ratio
from ..scene.zones import ZoneHit
from ..scene.lines import LineCrossing
from ..tracking.types import Track
from .components import (
    ScoreBreakdown,
    class_consistency,
    direction_confidence,
    motion_plausibility,
    size_plausibility,
    temporal_persistence,
    track_stability,
    zone_confidence,
    clamp01,
)


@dataclass(slots=True)
class EventScoreComposer:
    config: ScoringConfig

    def compose(self, *, track: Track, context, zone: ZoneHit | None, line: LineCrossing | None, observation=None) -> float:
        breakdown = self.breakdown(track=track, context=context, zone=zone, line=line, observation=observation)
        weights = self.config
        total = (
            weights.class_consistency * breakdown.class_consistency
            + weights.track_stability * breakdown.track_stability
            + weights.temporal_persistence * breakdown.temporal_persistence
            + weights.size_plausibility * breakdown.size_plausibility
            + weights.motion_plausibility * breakdown.motion_plausibility
            + weights.zone_confidence * breakdown.zone_confidence
            + weights.direction_confidence * breakdown.direction_confidence
        )
        return clamp01(total)

    def breakdown(self, *, track: Track, context, zone: ZoneHit | None, line: LineCrossing | None, observation=None) -> ScoreBreakdown:
        fp = normalize_footpoint(track.footpoint_current or bbox_footpoint(track.bbox_current or [0, 0, 0, 0]))
        if getattr(context, "scene_height", None):
            _, fp_y_ratio = point_ratio(fp, getattr(context.scene, "width", None), getattr(context.scene, "height", None))
        else:
            fp_y_ratio = float(fp[1])

        if observation is not None:
            zone = zone or getattr(observation, "restricted_zone", None)
            line = line or (observation.line_crossings[0] if getattr(observation, "line_crossings", None) else None)
            inside_zone = bool(getattr(observation, "in_restricted_area", False))
        else:
            inside_zone = bool(zone is not None)
        return ScoreBreakdown(
            class_consistency=class_consistency(track),
            track_stability=track_stability(track, 10),
            temporal_persistence=temporal_persistence(track, max(1, getattr(context.rule_config, "min_visible_frames", 6))),
            size_plausibility=size_plausibility(
                track,
                fp_y_ratio,
                perspective_profile=getattr(context.scene, "perspective_profile", None),
                point=fp,
                frame_width=getattr(context.scene, "width", None),
                frame_height=getattr(context.scene, "height", None),
                min_aspect_ratio=getattr(context.scene, "min_bbox_aspect_ratio", 0.22),
                max_aspect_ratio=getattr(context.scene, "max_bbox_aspect_ratio", 1.25),
                border_margin_ratio=getattr(context.scene, "border_margin_ratio", 0.06),
            ),
            motion_plausibility=motion_plausibility(track),
            zone_confidence=zone_confidence(track, inside_zone),
            direction_confidence=direction_confidence(
                track,
                getattr(context.rule_config, "allowed_direction", None) if hasattr(context, "rule_config") else None,
                getattr(context.rule_config, "prohibited_direction", None) if hasattr(context, "rule_config") else None,
            ),
        )
