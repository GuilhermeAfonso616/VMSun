from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.timezone import utc_now_naive


@dataclass(slots=True)
class AlarmSessionRecord:
    session_id: str
    key: str
    camera_id: int
    rule_id: str
    event_type: str
    zone_id: str | None
    opened_at: datetime
    last_seen_at: datetime
    last_notified_at: datetime
    last_track_id: int | None
    track_ids: set[int] = field(default_factory=set)
    event_count: int = 0
    update_count: int = 0
    last_score: float = 0.0


class AlarmSessionPolicy:
    def __init__(
        self,
        *,
        enabled: bool = True,
        same_scope_cooldown_seconds: float = 60.0,
        active_extend_seconds: float = 30.0,
        rearm_clear_seconds: float = 15.0,
        new_track_renotify_enabled: bool = True,
        new_track_cooldown_seconds: float = 10.0,
        reminder_interval_seconds: float = 300.0,
        escalation_score_delta: float = 0.20,
    ):
        self.enabled = bool(enabled)
        self.same_scope_cooldown_seconds = max(0.0, float(same_scope_cooldown_seconds))
        self.active_extend_seconds = max(0.0, float(active_extend_seconds))
        self.rearm_clear_seconds = max(0.0, float(rearm_clear_seconds))
        self.new_track_renotify_enabled = bool(new_track_renotify_enabled)
        self.new_track_cooldown_seconds = max(0.0, float(new_track_cooldown_seconds))
        self.reminder_interval_seconds = max(1.0, float(reminder_interval_seconds))
        self.escalation_score_delta = max(0.0, float(escalation_score_delta))
        self.sessions: dict[str, AlarmSessionRecord] = {}

    @staticmethod
    def key_for_event(event: Any) -> str:
        zone_id = getattr(getattr(event, "evidence", None), "zone_id", None) or "scene"
        return ":".join(
            [
                str(getattr(event, "camera_id", "")),
                str(getattr(event, "rule_id", "")),
                str(getattr(event, "event_type", "")),
                str(zone_id),
            ]
        )

    def evaluate(self, event: Any, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or getattr(event, "timestamp_end", None) or utc_now_naive()
        key = self.key_for_event(event)
        track_id = getattr(event, "track_id", None)
        score = float(getattr(event, "event_score", 0.0) or 0.0)
        if not self.enabled:
            return {
                "enabled": False,
                "key": key,
                "decision": "NOTIFY",
                "reason": "alarm_session_disabled",
                "is_alarm_active": True,
                "lifecycle_action": "open",
                "status": "processing",
            }

        record = self.sessions.get(key)
        if record is None:
            return self._open_session(event, key=key, now=now, track_id=track_id, score=score)

        inactive_seconds = max(0.0, (now - record.last_seen_at).total_seconds())
        notify_elapsed_seconds = max(0.0, (now - record.last_notified_at).total_seconds())
        rearmed = inactive_seconds >= (self.active_extend_seconds + self.rearm_clear_seconds)
        cooldown_elapsed = notify_elapsed_seconds >= self.same_scope_cooldown_seconds
        new_track = track_id is not None and int(track_id) not in record.track_ids
        new_track_cooldown_elapsed = notify_elapsed_seconds >= self.new_track_cooldown_seconds
        reminder_due = notify_elapsed_seconds >= self.reminder_interval_seconds
        score_escalated = score >= record.last_score + self.escalation_score_delta

        if rearmed and cooldown_elapsed:
            return self._open_session(event, key=key, now=now, track_id=track_id, score=score, previous=record)

        record.event_count += 1
        record.last_seen_at = now
        record.last_track_id = track_id
        record.last_score = max(record.last_score, score)
        if track_id is not None:
            record.track_ids.add(int(track_id))

        if self.new_track_renotify_enabled and new_track and new_track_cooldown_elapsed:
            record.last_notified_at = now
            return {
                "enabled": True,
                "key": key,
                "session_id": record.session_id,
                "decision": "RENOTIFY",
                "reason": "new_track_same_scope",
                "is_alarm_active": True,
                "lifecycle_action": "new_track",
                "status": "processing",
                "event_count": record.event_count,
                "update_count": record.update_count,
                "track_count": len(record.track_ids),
                "seconds_since_last_notify": round(notify_elapsed_seconds, 3),
                "cooldown_seconds": self.same_scope_cooldown_seconds,
                "new_track_cooldown_seconds": self.new_track_cooldown_seconds,
            }

        if reminder_due or (cooldown_elapsed and score_escalated):
            record.last_notified_at = now
            reason = "session_reminder_due" if reminder_due else "session_score_escalated"
            return {
                "enabled": True,
                "key": key,
                "session_id": record.session_id,
                "decision": "RENOTIFY",
                "reason": reason,
                "is_alarm_active": True,
                "lifecycle_action": "reminder" if reminder_due else "escalate",
                "status": "processing",
                "event_count": record.event_count,
                "update_count": record.update_count,
                "track_count": len(record.track_ids),
                "seconds_since_last_notify": round(notify_elapsed_seconds, 3),
                "cooldown_seconds": self.same_scope_cooldown_seconds,
            }

        record.update_count += 1
        return {
            "enabled": True,
            "key": key,
            "session_id": record.session_id,
            "decision": "UPDATE",
            "reason": "same_camera_rule_zone_active_session",
            "is_alarm_active": False,
            "lifecycle_action": "update",
            "status": "correlated",
            "event_count": record.event_count,
            "update_count": record.update_count,
            "track_count": len(record.track_ids),
            "seconds_since_last_notify": round(notify_elapsed_seconds, 3),
            "cooldown_seconds": self.same_scope_cooldown_seconds,
        }

    def _open_session(
        self,
        event: Any,
        *,
        key: str,
        now: datetime,
        track_id: int | None,
        score: float,
        previous: AlarmSessionRecord | None = None,
    ) -> dict[str, Any]:
        session_id = f"{key}:{int(now.timestamp())}"
        zone_id = getattr(getattr(event, "evidence", None), "zone_id", None)
        track_ids = {int(track_id)} if track_id is not None else set()
        record = AlarmSessionRecord(
            session_id=session_id,
            key=key,
            camera_id=int(getattr(event, "camera_id", 0) or 0),
            rule_id=str(getattr(event, "rule_id", "") or ""),
            event_type=str(getattr(event, "event_type", "") or ""),
            zone_id=str(zone_id) if zone_id is not None else None,
            opened_at=now,
            last_seen_at=now,
            last_notified_at=now,
            last_track_id=track_id,
            track_ids=track_ids,
            event_count=1,
            update_count=0,
            last_score=score,
        )
        self.sessions[key] = record
        return {
            "enabled": True,
            "key": key,
            "session_id": session_id,
            "decision": "NOTIFY",
            "reason": "new_alarm_session" if previous is None else "session_rearmed_after_clear",
            "is_alarm_active": True,
            "lifecycle_action": "open",
            "status": "processing",
            "event_count": 1,
            "update_count": 0,
            "track_count": len(track_ids),
            "seconds_since_last_notify": None,
            "cooldown_seconds": self.same_scope_cooldown_seconds,
        }
