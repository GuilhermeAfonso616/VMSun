from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.timezone import utc_now_naive

from ..config.schema import AnalyticsConfig, RuleConfig
from ..events.debug import event_rule_debug
from ..events.dedupe import DeduplicationState
from ..events.hysteresis import HysteresisLatch
from ..events.models import AlarmEvent
from ..scoring.composer import EventScoreComposer
from ..tracking.enums import TrackState
from ..tracking.types import Track
from .base import make_event_signature
from .direction import DirectionalViolationRule
from .intrusion_zone import IntrusionZoneRule
from .line_crossing import LineCrossingRule
from .loitering import LoiteringRule


@dataclass(slots=True)
class RuleEngineMetrics:
    events_emitted: int = 0
    events_suppressed: int = 0
    mean_event_score: float = 0.0
    processed_tracks: int = 0


@dataclass(slots=True)
class RuleEngineContext:
    camera_id: int
    now: datetime
    scene: Any
    scoring: EventScoreComposer
    tracker_metrics: Any
    rule_config: RuleConfig
    scene_height: float | None = None


class IntrusionRuleEngine:
    def __init__(self, config: AnalyticsConfig):
        self.config = config
        self.score_composer = EventScoreComposer(config.scoring)
        self.deduper = DeduplicationState()
        self.latch = HysteresisLatch()
        self.metrics = RuleEngineMetrics()
        self.rules = self._build_rules()

    def _build_rules(self):
        rules = []
        for rule_config in self.config.rules.values():
            if not rule_config.enabled:
                continue
            if rule_config.rule_type == "line_crossing":
                rules.append(LineCrossingRule(rule_config))
            elif rule_config.rule_type == "loitering":
                rules.append(LoiteringRule(rule_config))
            elif rule_config.rule_type == "directional_violation":
                rules.append(DirectionalViolationRule(rule_config))
            else:
                rules.append(IntrusionZoneRule(rule_config))
        return rules

    def evaluate(
        self,
        *,
        camera_id: int,
        tracks: list[Track],
        scene,
        tracker_metrics,
        now: datetime | None = None,
    ) -> tuple[list[AlarmEvent], list[dict]]:
        now = now or utc_now_naive()
        events: list[AlarmEvent] = []
        suppressed: list[dict] = []

        event_rule_debug.emit(
            "rule_engine_evaluate_start",
            camera_id=camera_id,
            track_id=None,
            reason="evaluate_start",
            rate_key=f"engine_start:{camera_id}",
            tracks_count=len(tracks or []),
            rules_count=len(self.rules or []),
            rule_ids=[rule.config.rule_id for rule in self.rules],
        )

        rule_contexts = {
            rule.config.rule_id: RuleEngineContext(
                camera_id=camera_id,
                now=now,
                scene=scene,
                scoring=self.score_composer,
                tracker_metrics=tracker_metrics,
                rule_config=rule.config,
                scene_height=getattr(scene, "height", None),
            )
            for rule in self.rules
        }

        for track in tracks:
            track_id = getattr(track, "track_id", None)

            if track.state != TrackState.CONFIRMED:
                event_rule_debug.emit(
                    "rule_rejected",
                    camera_id=camera_id,
                    track_id=track_id,
                    reason="rejected_unconfirmed_track_engine_skip",
                    rate_key="engine_skip_unconfirmed_track",
                    track_state=str(getattr(track, "state", None)),
                    track_age_frames=getattr(track, "age_frames", None),
                    visible_frames=getattr(track, "visible_frames", None),
                )
                continue

            self.metrics.processed_tracks += 1

            for rule in self.rules:
                context = rule_contexts[rule.config.rule_id]
                result = rule.evaluate(track, context)

                if not result.triggered:
                    continue

                self.latch.enter_threshold = rule.config.hysteresis_enter
                self.latch.exit_threshold = rule.config.hysteresis_exit
                self.deduper.cooldown_seconds = rule.config.cooldown_seconds

                enter_threshold = max(
                    float(rule.config.min_event_score),
                    min(float(rule.config.hysteresis_enter), 0.65),
                )
                exit_threshold = min(float(rule.config.hysteresis_exit), max(0.40, enter_threshold - 0.15))
                self.latch.enter_threshold = enter_threshold
                self.latch.exit_threshold = exit_threshold

                signature = make_event_signature(
                    camera_id=camera_id,
                    rule_id=rule.config.rule_id,
                    track_id=track.track_id,
                    event_type=result.event_type,
                    zone_id=result.evidence.zone_id,
                    line_id=result.evidence.line_id,
                )

                event_rule_debug.emit(
                    "rule_triggered_before_suppression",
                    camera_id=camera_id,
                    track_id=track.track_id,
                    reason="rule_triggered",
                    rate_key=f"triggered:{signature}",
                    signature=signature,
                    rule_id=rule.config.rule_id,
                    rule_type=rule.config.rule_type,
                    event_type=result.event_type,
                    score=result.score,
                    priority=result.priority,
                    explanation=result.reason,
                    hysteresis_enter=enter_threshold,
                    hysteresis_exit=exit_threshold,
                    cooldown_seconds=rule.config.cooldown_seconds,
                    zone_id=result.evidence.zone_id,
                    line_id=result.evidence.line_id,
                )

                if not self.latch.evaluate(
                    signature,
                    result.score,
                    camera_id=camera_id,
                    track_id=track.track_id,
                    rule_id=rule.config.rule_id,
                    event_type=result.event_type,
                ):
                    self.metrics.events_suppressed += 1
                    suppressed.append({"signature": signature, "reason": "hysteresis", "score": result.score})

                    event_rule_debug.emit(
                        "event_suppressed",
                        camera_id=camera_id,
                        track_id=track.track_id,
                        reason="suppressed_by_hysteresis",
                        rate_key=f"suppressed_hysteresis:{signature}",
                        signature=signature,
                        rule_id=rule.config.rule_id,
                        rule_type=rule.config.rule_type,
                        event_type=result.event_type,
                        score=result.score,
                        enter_threshold=enter_threshold,
                        exit_threshold=exit_threshold,
                    )
                    continue

                if not self.deduper.should_emit(
                    signature,
                    now,
                    result.score,
                    track_id=track.track_id,
                    camera_id=camera_id,
                    rule_id=rule.config.rule_id,
                ):
                    self.metrics.events_suppressed += 1
                    suppressed.append({"signature": signature, "reason": "dedupe", "score": result.score})

                    event_rule_debug.emit(
                        "event_suppressed",
                        camera_id=camera_id,
                        track_id=track.track_id,
                        reason="suppressed_by_dedupe",
                        rate_key=f"suppressed_dedupe:{signature}",
                        signature=signature,
                        rule_id=rule.config.rule_id,
                        rule_type=rule.config.rule_type,
                        event_type=result.event_type,
                        score=result.score,
                        cooldown_seconds=rule.config.cooldown_seconds,
                    )
                    continue

                event = AlarmEvent(
                    event_id=signature,
                    camera_id=camera_id,
                    rule_id=rule.config.rule_id,
                    track_id=track.track_id,
                    timestamp_start=track.first_seen or now,
                    timestamp_end=now,
                    event_score=result.score,
                    priority=result.priority,
                    event_type=result.event_type,
                    evidence=result.evidence,
                    explanation=result.reason,
                    metadata={"rule_type": rule.config.rule_type, **result.metadata},
                )

                events.append(event)
                self.metrics.events_emitted += 1
                self.metrics.mean_event_score = (
                    (self.metrics.mean_event_score * (self.metrics.events_emitted - 1)) + result.score
                ) / float(self.metrics.events_emitted)

                track.event_emitted = True
                track.metadata["last_event_id"] = signature

                event_rule_debug.emit(
                    "event_enqueued",
                    camera_id=camera_id,
                    track_id=track.track_id,
                    reason="event_enqueued",
                    rate_key=f"event_enqueued:{signature}",
                    signature=signature,
                    event_id=signature,
                    rule_id=rule.config.rule_id,
                    rule_type=rule.config.rule_type,
                    event_type=result.event_type,
                    score=result.score,
                    priority=result.priority,
                    explanation=result.reason,
                    zone_id=result.evidence.zone_id,
                    line_id=result.evidence.line_id,
                )

        event_rule_debug.emit(
            "rule_engine_evaluate_end",
            camera_id=camera_id,
            track_id=None,
            reason="evaluate_end",
            rate_key=f"engine_end:{camera_id}",
            emitted=len(events),
            suppressed=len(suppressed),
            processed_tracks=self.metrics.processed_tracks,
        )

        return events, suppressed