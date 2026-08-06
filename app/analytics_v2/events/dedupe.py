from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .debug import event_rule_debug


@dataclass(slots=True)
class DedupeRecord:
    last_emitted_at: datetime
    last_score: float
    last_signature: str


@dataclass(slots=True)
class DeduplicationState:
    cooldown_seconds: float = 5.0  # Reduced from 10.0 to allow more responsive event generation
    dedupe_window_seconds: float = 3.0
    records: dict[str, DedupeRecord] = field(default_factory=dict)
    track_records: dict[str, DedupeRecord] = field(default_factory=dict)

    def should_emit(
        self,
        signature: str,
        now: datetime,
        score: float,
        *,
        track_id: int | None = None,
        camera_id: int | None = None,
        rule_id: str | None = None,
    ) -> bool:
        record = self.records.get(signature)

        if record is None:
            self.records[signature] = DedupeRecord(now, score, signature)
        else:
            elapsed_seconds = (now - record.last_emitted_at).total_seconds()

            if (now - record.last_emitted_at) < timedelta(seconds=self.dedupe_window_seconds):
                event_rule_debug.emit(
                    "event_suppressed",
                    camera_id=camera_id,
                    track_id=track_id,
                    reason="suppressed_by_dedupe_window",
                    rate_key=f"dedupe_window:{signature}",
                    rule_id=rule_id,
                    signature=signature,
                    score=score,
                    last_score=record.last_score,
                    elapsed_seconds=elapsed_seconds,
                    dedupe_window_seconds=self.dedupe_window_seconds,
                    cooldown_seconds=self.cooldown_seconds,
                )
                return False

            if (now - record.last_emitted_at) < timedelta(seconds=self.cooldown_seconds) and score <= record.last_score:
                event_rule_debug.emit(
                    "event_suppressed",
                    camera_id=camera_id,
                    track_id=track_id,
                    reason="suppressed_by_cooldown",
                    rate_key=f"cooldown:{signature}",
                    rule_id=rule_id,
                    signature=signature,
                    score=score,
                    last_score=record.last_score,
                    elapsed_seconds=elapsed_seconds,
                    cooldown_seconds=self.cooldown_seconds,
                    cooldown_remaining_seconds=max(0.0, self.cooldown_seconds - elapsed_seconds),
                )
                return False

        if track_id is not None and camera_id is not None and rule_id is not None:
            track_key = f"{camera_id}:{rule_id}:{track_id}"
            track_record = self.track_records.get(track_key)

            if track_record is not None:
                elapsed_track_seconds = (now - track_record.last_emitted_at).total_seconds()
                half_cooldown = max(1.0, self.cooldown_seconds * 0.5)

                if (
                    (now - track_record.last_emitted_at) < timedelta(seconds=half_cooldown)
                    and score <= track_record.last_score + 0.02
                ):
                    event_rule_debug.emit(
                        "event_suppressed",
                        camera_id=camera_id,
                        track_id=track_id,
                        reason="suppressed_by_track_half_cooldown",
                        rate_key=f"track_half_cooldown:{track_key}",
                        rule_id=rule_id,
                        signature=signature,
                        track_key=track_key,
                        score=score,
                        last_score=track_record.last_score,
                        elapsed_seconds=elapsed_track_seconds,
                        cooldown_seconds=half_cooldown,
                        cooldown_remaining_seconds=max(0.0, half_cooldown - elapsed_track_seconds),
                    )
                    return False

                if (
                    (now - track_record.last_emitted_at) < timedelta(seconds=self.cooldown_seconds)
                    and score <= track_record.last_score
                ):
                    event_rule_debug.emit(
                        "event_suppressed",
                        camera_id=camera_id,
                        track_id=track_id,
                        reason="suppressed_by_track_cooldown",
                        rate_key=f"track_cooldown:{track_key}",
                        rule_id=rule_id,
                        signature=signature,
                        track_key=track_key,
                        score=score,
                        last_score=track_record.last_score,
                        elapsed_seconds=elapsed_track_seconds,
                        cooldown_seconds=self.cooldown_seconds,
                        cooldown_remaining_seconds=max(0.0, self.cooldown_seconds - elapsed_track_seconds),
                    )
                    return False

            self.track_records[track_key] = DedupeRecord(now, score, track_key)

        self.records[signature] = DedupeRecord(now, score, signature)

        event_rule_debug.emit(
            "event_dedupe_passed",
            camera_id=camera_id,
            track_id=track_id,
            reason="dedupe_passed",
            rate_key=f"dedupe_passed:{signature}",
            rule_id=rule_id,
            signature=signature,
            score=score,
            cooldown_seconds=self.cooldown_seconds,
            dedupe_window_seconds=self.dedupe_window_seconds,
        )

        return True