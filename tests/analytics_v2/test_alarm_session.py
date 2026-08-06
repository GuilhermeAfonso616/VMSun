from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.analytics_v2.events.alarm_session import AlarmSessionPolicy
from app.analytics_v2.events.models import AlarmEvent, EventEvidence


def _event(*, track_id: int = 1, seconds: int = 0, score: float = 0.80) -> AlarmEvent:
    now = datetime(2026, 5, 8, 12, 0, 0) + timedelta(seconds=seconds)
    return AlarmEvent(
        event_id=f"evt-{track_id}-{seconds}",
        camera_id=7,
        rule_id="intrusion_default",
        track_id=track_id,
        timestamp_start=now,
        timestamp_end=now,
        event_score=score,
        priority="high",
        event_type="person_entered_roi",
        evidence=EventEvidence(zone_id="roi_1", bbox=[10.0, 10.0, 80.0, 160.0]),
        explanation="test",
    )


class AlarmSessionPolicyTest(unittest.TestCase):
    def test_first_event_opens_session_and_notifies(self):
        policy = AlarmSessionPolicy(same_scope_cooldown_seconds=60.0)

        result = policy.evaluate(_event())

        self.assertEqual(result["decision"], "NOTIFY")
        self.assertTrue(result["is_alarm_active"])
        self.assertEqual(result["lifecycle_action"], "open")

    def test_repeated_event_in_same_camera_rule_zone_is_update(self):
        policy = AlarmSessionPolicy(
            same_scope_cooldown_seconds=60.0,
            new_track_renotify_enabled=False,
        )
        first = policy.evaluate(_event(track_id=1, seconds=0))
        second = policy.evaluate(_event(track_id=2, seconds=10))

        self.assertEqual(second["decision"], "UPDATE")
        self.assertFalse(second["is_alarm_active"])
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["track_count"], 2)

    def test_new_track_in_same_camera_rule_zone_renotifies_after_short_cooldown(self):
        policy = AlarmSessionPolicy(
            same_scope_cooldown_seconds=60.0,
            new_track_cooldown_seconds=10.0,
        )
        first = policy.evaluate(_event(track_id=1, seconds=0))
        second = policy.evaluate(_event(track_id=2, seconds=11))

        self.assertEqual(second["decision"], "RENOTIFY")
        self.assertTrue(second["is_alarm_active"])
        self.assertEqual(second["lifecycle_action"], "new_track")
        self.assertEqual(second["reason"], "new_track_same_scope")
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["track_count"], 2)

    def test_new_track_before_short_cooldown_is_update(self):
        policy = AlarmSessionPolicy(
            same_scope_cooldown_seconds=60.0,
            new_track_cooldown_seconds=10.0,
        )
        first = policy.evaluate(_event(track_id=1, seconds=0))
        second = policy.evaluate(_event(track_id=2, seconds=5))

        self.assertEqual(second["decision"], "UPDATE")
        self.assertFalse(second["is_alarm_active"])
        self.assertEqual(second["session_id"], first["session_id"])

    def test_session_rearms_after_clear_and_cooldown(self):
        policy = AlarmSessionPolicy(
            same_scope_cooldown_seconds=60.0,
            active_extend_seconds=30.0,
            rearm_clear_seconds=15.0,
        )
        first = policy.evaluate(_event(track_id=1, seconds=0))
        second = policy.evaluate(_event(track_id=2, seconds=90))

        self.assertEqual(second["decision"], "NOTIFY")
        self.assertEqual(second["reason"], "session_rearmed_after_clear")
        self.assertNotEqual(second["session_id"], first["session_id"])

    def test_reminder_renotifies_long_running_session(self):
        policy = AlarmSessionPolicy(
            same_scope_cooldown_seconds=60.0,
            active_extend_seconds=300.0,
            rearm_clear_seconds=30.0,
            reminder_interval_seconds=120.0,
        )
        first = policy.evaluate(_event(track_id=1, seconds=0))
        reminder = policy.evaluate(_event(track_id=1, seconds=121))

        self.assertEqual(reminder["decision"], "RENOTIFY")
        self.assertEqual(reminder["lifecycle_action"], "reminder")
        self.assertEqual(reminder["session_id"], first["session_id"])


if __name__ == "__main__":
    unittest.main()
