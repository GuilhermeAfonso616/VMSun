from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.analytics_v2.events.models import AlarmEvent, EventEvidence
from app.runtime.events import EventPipeline


def _event(*, seconds: int = 0) -> AlarmEvent:
    now = datetime(2026, 5, 8, 12, 0, 0) + timedelta(seconds=seconds)
    return AlarmEvent(
        event_id=f"evt-{seconds}",
        camera_id=3,
        rule_id="intrusion_default",
        track_id=22,
        timestamp_start=now,
        timestamp_end=now,
        event_score=0.81,
        priority="high",
        event_type="person_entered_roi",
        evidence=EventEvidence(bbox=[10.0, 20.0, 80.0, 180.0], zone_id="roi_1"),
        explanation="test",
    )


class VisualRevalidationGateTest(unittest.TestCase):
    def test_records_box_only_after_non_suppressed_revalidation(self):
        pipeline = EventPipeline()
        event = _event()

        with patch("app.runtime.events.settings.visual_revalidation_gate_enabled", True):
            pipeline._record_revalidated_visual_track(
                event,
                {"decision": "ACCEPT", "notification_decision": "LOW_PRIORITY", "ia2_person_score": 0.64},
            )

        tracks = pipeline.visual_tracks(now=event.timestamp_end)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["bbox"], [10.0, 20.0, 80.0, 180.0])
        self.assertEqual(tracks[0]["confidence"], 0.64)
        self.assertEqual(tracks[0]["visual_person_score"], 0.64)
        self.assertEqual(tracks[0]["visual_status"], "revalidated")

    def test_revalidation_below_min_person_score_is_not_displayed(self):
        pipeline = EventPipeline()
        event = _event()

        with patch("app.runtime.events.settings.visual_revalidation_gate_enabled", True):
            with patch("app.runtime.events.settings.visual_revalidation_gate_min_person_score", 0.45):
                pipeline._record_revalidated_visual_track(
                    event,
                    {"decision": "ACCEPT", "notification_decision": "LOW_PRIORITY", "ia2_person_score": 0.33},
                )

        self.assertEqual(pipeline.visual_tracks(now=event.timestamp_end), [])

    def test_suppressed_revalidation_is_not_displayed(self):
        pipeline = EventPipeline()
        event = _event()

        with patch("app.runtime.events.settings.visual_revalidation_gate_enabled", True):
            pipeline._record_revalidated_visual_track(
                event,
                {"decision": "SUPPRESS", "notification_decision": "SUPPRESS"},
            )

        self.assertEqual(pipeline.visual_tracks(now=event.timestamp_end), [])

    def test_revalidated_box_expires(self):
        pipeline = EventPipeline()
        event = _event()

        with patch("app.runtime.events.settings.visual_revalidation_gate_enabled", True):
            with patch("app.runtime.events.settings.visual_revalidation_gate_ttl_seconds", 1.0):
                pipeline._record_revalidated_visual_track(
                    event,
                    {"decision": "ACCEPT", "notification_decision": "NOTIFY", "ia2_person_score": 0.91},
                )

        self.assertEqual(len(pipeline.visual_tracks(now=event.timestamp_end)), 1)
        self.assertEqual(pipeline.visual_tracks(now=event.timestamp_end + timedelta(seconds=2)), [])


if __name__ == "__main__":
    unittest.main()
