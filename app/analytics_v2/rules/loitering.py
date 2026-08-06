from __future__ import annotations

from dataclasses import dataclass

from ..config.schema import RuleConfig
from ..events.models import EventEvidence
from ..tracking.enums import TrackState
from ..tracking.types import Track
from .base import RuleContext, RuleResult


@dataclass(slots=True)
class LoiteringRule:
    config: RuleConfig
    zone_id: str | None = None

    def evaluate(self, track: Track, context: RuleContext) -> RuleResult:
        if not self.config.enabled:
            return RuleResult(False, self.config.rule_id, "loitering", "disabled")
        if self.config.require_confirmed_track and track.state != TrackState.CONFIRMED:
            return RuleResult(False, self.config.rule_id, "loitering", "track_not_confirmed")
        effective_quality = track.effective_quality()
        if effective_quality < self.config.min_track_quality:
            return RuleResult(False, self.config.rule_id, "loitering", "track_quality_too_low", score=effective_quality)
        if track.class_consistency() < self.config.min_class_consistency:
            return RuleResult(False, self.config.rule_id, "loitering", "class_consistency_too_low")

        dwell_reference = track.confirmed_at or track.first_seen
        dwell_ms = 0.0
        if dwell_reference and track.last_seen:
            dwell_ms = (track.last_seen - dwell_reference).total_seconds() * 1000.0
        if dwell_ms < self.config.min_dwell_ms:
            return RuleResult(False, self.config.rule_id, "loitering", "dwell_too_short")

        observation = self._scene_observation(track, context)
        zone_hit = observation.restricted_zone or observation.buffer_zone
        if zone_hit is None:
            zone_hit = context.scene.first_restricted_zone()
        score = context.scoring.compose(track=track, context=context, zone=zone_hit, line=None, observation=observation)
        if score < self.config.min_event_score:
            return RuleResult(False, self.config.rule_id, "loitering", "event_score_below_threshold", score=score)

        return RuleResult(
            True,
            self.config.rule_id,
            "loitering",
            "confirmed_loitering",
            score=score,
            priority=self._priority(score),
            evidence=EventEvidence(
                bbox=list(track.bbox_current or []),
                trajectory=[point.footpoint for point in track.bbox_history[-10:]],
                zone_id=getattr(zone_hit, "zone_id", None),
                footpoint=observation.footpoint,
                reason="track_loitering_duration_exceeded",
            ),
            metadata={
                "dwell_ms": dwell_ms,
                "zone_id": getattr(zone_hit, "zone_id", None),
                "track_quality": effective_quality,
            },
        )

    def _scene_observation(self, track: Track, context: RuleContext):
        observation = track.metadata.get("scene_observation")
        if observation is not None:
            return observation
        return context.scene.observe_track(track)

    def _priority(self, score: float) -> str:
        if score >= 0.85:
            return "critical"
        if score >= 0.70:
            return "high"
        if score >= 0.55:
            return "medium"
        return "low"
