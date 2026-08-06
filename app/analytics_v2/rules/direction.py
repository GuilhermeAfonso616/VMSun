from __future__ import annotations

from dataclasses import dataclass

from ..config.schema import RuleConfig
from ..events.models import EventEvidence
from ..tracking.enums import TrackState
from ..tracking.types import Track
from .base import RuleContext, RuleResult


@dataclass(slots=True)
class DirectionalViolationRule:
    config: RuleConfig

    def evaluate(self, track: Track, context: RuleContext) -> RuleResult:
        if not self.config.enabled:
            return RuleResult(False, self.config.rule_id, "directional_violation", "disabled")
        if self.config.require_confirmed_track and track.state != TrackState.CONFIRMED:
            return RuleResult(False, self.config.rule_id, "directional_violation", "track_not_confirmed")
        if not track.direction_estimate:
            return RuleResult(False, self.config.rule_id, "directional_violation", "no_direction")
        effective_quality = track.effective_quality()
        if effective_quality < self.config.min_track_quality:
            return RuleResult(False, self.config.rule_id, "directional_violation", "track_quality_too_low", score=effective_quality)

        if self.config.allowed_direction and track.direction_estimate != self.config.allowed_direction:
            observation = self._scene_observation(track, context)
            return RuleResult(True, self.config.rule_id, "directional_violation", "direction_not_allowed", score=0.7, priority="high", evidence=EventEvidence(bbox=list(track.bbox_current or []), footpoint=observation.footpoint, reason="direction_not_allowed"))

        if self.config.prohibited_direction and track.direction_estimate == self.config.prohibited_direction:
            observation = self._scene_observation(track, context)
            return RuleResult(True, self.config.rule_id, "directional_violation", "prohibited_direction", score=0.8, priority="high", evidence=EventEvidence(bbox=list(track.bbox_current or []), footpoint=observation.footpoint, reason="prohibited_direction"))

        return RuleResult(False, self.config.rule_id, "directional_violation", "direction_allowed")

    def _scene_observation(self, track: Track, context: RuleContext):
        observation = track.metadata.get("scene_observation")
        if observation is not None:
            return observation
        return context.scene.observe_track(track)
