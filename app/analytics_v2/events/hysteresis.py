from __future__ import annotations

from dataclasses import dataclass, field

from .debug import event_rule_debug


@dataclass(slots=True)
class HysteresisLatch:
    enter_threshold: float = 0.75
    exit_threshold: float = 0.55
    active: bool = False
    required_hits: int = 1
    required_misses: int = 2
    state_by_signature: dict[str, bool] = field(default_factory=dict)
    hit_counts: dict[str, int] = field(default_factory=dict)
    miss_counts: dict[str, int] = field(default_factory=dict)

    def evaluate(
        self,
        signature: str,
        score: float,
        *,
        camera_id: int | None = None,
        track_id: int | None = None,
        rule_id: str | None = None,
        event_type: str | None = None,
    ) -> bool:
        active = self.state_by_signature.get(signature, self.active)

        if not active:
            if score >= self.enter_threshold:
                hits = self.hit_counts.get(signature, 0) + 1
                self.hit_counts[signature] = hits
                self.miss_counts[signature] = 0

                event_rule_debug.emit(
                    "hysteresis_hit",
                    camera_id=camera_id,
                    track_id=track_id,
                    reason="hysteresis_enter_hit",
                    rate_key=f"hysteresis_enter_hit:{signature}",
                    rule_id=rule_id,
                    event_type=event_type,
                    signature=signature,
                    score=score,
                    enter_threshold=self.enter_threshold,
                    exit_threshold=self.exit_threshold,
                    hits=hits,
                    required_hits=self.required_hits,
                    active=False,
                )

                if hits >= self.required_hits:
                    self.state_by_signature[signature] = True
                    self.hit_counts[signature] = 0

                    event_rule_debug.emit(
                        "hysteresis_entered",
                        camera_id=camera_id,
                        track_id=track_id,
                        reason="hysteresis_entered",
                        rate_key=f"hysteresis_entered:{signature}",
                        rule_id=rule_id,
                        event_type=event_type,
                        signature=signature,
                        score=score,
                        enter_threshold=self.enter_threshold,
                        exit_threshold=self.exit_threshold,
                        previous_state=False,
                        next_state=True,
                    )

                    return True
            else:
                self.hit_counts[signature] = 0

                event_rule_debug.emit(
                    "event_suppressed",
                    camera_id=camera_id,
                    track_id=track_id,
                    reason="suppressed_by_hysteresis_enter_threshold",
                    rate_key=f"hysteresis_enter_threshold:{signature}",
                    rule_id=rule_id,
                    event_type=event_type,
                    signature=signature,
                    score=score,
                    enter_threshold=self.enter_threshold,
                    exit_threshold=self.exit_threshold,
                    previous_state=False,
                    next_state=False,
                )

            return False

        if score <= self.exit_threshold:
            misses = self.miss_counts.get(signature, 0) + 1
            self.miss_counts[signature] = misses
            self.hit_counts[signature] = 0

            event_rule_debug.emit(
                "hysteresis_miss",
                camera_id=camera_id,
                track_id=track_id,
                reason="hysteresis_exit_miss",
                rate_key=f"hysteresis_exit_miss:{signature}",
                rule_id=rule_id,
                event_type=event_type,
                signature=signature,
                score=score,
                enter_threshold=self.enter_threshold,
                exit_threshold=self.exit_threshold,
                misses=misses,
                required_misses=self.required_misses,
                active=True,
            )

            if misses >= self.required_misses:
                self.state_by_signature[signature] = False
                self.miss_counts[signature] = 0

                event_rule_debug.emit(
                    "hysteresis_exited",
                    camera_id=camera_id,
                    track_id=track_id,
                    reason="hysteresis_exited",
                    rate_key=f"hysteresis_exited:{signature}",
                    rule_id=rule_id,
                    event_type=event_type,
                    signature=signature,
                    score=score,
                    enter_threshold=self.enter_threshold,
                    exit_threshold=self.exit_threshold,
                    previous_state=True,
                    next_state=False,
                )
        else:
            self.miss_counts[signature] = 0

            event_rule_debug.emit(
                "event_suppressed",
                camera_id=camera_id,
                track_id=track_id,
                reason="suppressed_by_hysteresis_already_active",
                rate_key=f"hysteresis_active:{signature}",
                rule_id=rule_id,
                event_type=event_type,
                signature=signature,
                score=score,
                enter_threshold=self.enter_threshold,
                exit_threshold=self.exit_threshold,
                previous_state=True,
                next_state=True,
            )

        return False