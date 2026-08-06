from __future__ import annotations

from dataclasses import dataclass

from ..config.schema import RuleConfig
from ..events.models import EventEvidence
from ..tracking.enums import TrackState
from ..tracking.types import Track
from .base import RuleContext, RuleResult


@dataclass(slots=True)
class LineCrossingRule:
    config: RuleConfig
    line_id: str | None = None

    def evaluate(self, track: Track, context: RuleContext) -> RuleResult:
        if not self.config.enabled:
            return RuleResult(False, self.config.rule_id, "line_crossing", "disabled")
        if self.config.require_confirmed_track and track.state != TrackState.CONFIRMED:
            return RuleResult(False, self.config.rule_id, "line_crossing", "track_not_confirmed")
        if track.age_frames < self.config.min_track_age_frames:
            return RuleResult(False, self.config.rule_id, "line_crossing", "track_too_young")
        effective_quality = track.effective_quality()
        if effective_quality < self.config.min_track_quality:
            return RuleResult(False, self.config.rule_id, "line_crossing", "track_quality_too_low", score=effective_quality)
        if not context.scene.directional_lines:
            return RuleResult(False, self.config.rule_id, "line_crossing", "no_line_defined")

        observation = self._scene_observation(track, context)
        if len(track.bbox_history) < 2:
            return RuleResult(False, self.config.rule_id, "line_crossing", "not_enough_history")

        for crossing in observation.line_crossings:
            line = next((item for item in context.scene.directional_lines if item.line_id == crossing.line_id and item.enabled), None)
            if line is None:
                continue

            score = context.scoring.compose(track=track, context=context, zone=None, line=line, observation=observation)
            if score < self.config.min_event_score:
                return RuleResult(False, self.config.rule_id, "line_crossing", "event_score_below_threshold", score=score)

            direction_ok = self._direction_allowed(line.direction, crossing.previous_side, crossing.current_side)
            if not direction_ok:
                return RuleResult(False, self.config.rule_id, "line_crossing", "direction_not_allowed", score=score)

            return RuleResult(
                True,
                self.config.rule_id,
                "line_crossing",
                "confirmed_line_crossing",
                score=score,
                priority=self._priority(score),
                evidence=EventEvidence(
                    bbox=list(track.bbox_current or []),
                    trajectory=[point.footpoint for point in track.bbox_history[-10:]],
                    line_id=line.line_id,
                    footpoint=observation.footpoint,
                    reason="footpoint_crossed_directional_line",
                ),
                metadata={
                    "line_id": line.line_id,
                    "line_name": line.name,
                    "previous_side": crossing.previous_side,
                    "current_side": crossing.current_side,
                },
            )

        return RuleResult(False, self.config.rule_id, "line_crossing", "no_cross_detected")

    def _scene_observation(self, track: Track, context: RuleContext):
        observation = track.metadata.get("scene_observation")
        if observation is not None:
            return observation
        return context.scene.observe_track(track)

    def _direction_allowed(self, line_direction: str, previous_side: float, current_side: float) -> bool:
        if line_direction in {None, "", "any"}:
            return True
        if line_direction == "a_to_b":
            return previous_side < 0 and current_side > 0
        if line_direction == "b_to_a":
            return previous_side > 0 and current_side < 0
        return True

    def _priority(self, score: float) -> str:
        if score >= 0.85:
            return "critical"
        if score >= 0.70:
            return "high"
        if score >= 0.55:
            return "medium"
        return "low"
