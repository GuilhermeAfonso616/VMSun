from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.revalidator_region_memory_service import build_region_memory


class RegionMemoryTest(unittest.TestCase):
    def test_green_region_requires_false_positive_history_without_person(self):
        event = SimpleNamespace(id=10, camera_id=3, bbox_json="[80, 80, 120, 160]")
        feedback = SimpleNamespace(label="false_positive")
        history_rows = [
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=1, camera_id=3, bbox_json="[82, 82, 121, 161]")),
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=2, camera_id=3, bbox_json="[84, 83, 122, 162]")),
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=3, camera_id=3, bbox_json="[81, 81, 119, 159]")),
        ]

        result = build_region_memory(
            event=event,
            feedback=feedback,
            history_rows=history_rows,
            frame_width=320,
            frame_height=240,
        )

        self.assertEqual(result["risk_level"], "GREEN")
        self.assertEqual(result["false_positive_count"], 3)
        self.assertEqual(result["true_positive_count"], 0)

    def test_person_history_marks_region_red(self):
        event = SimpleNamespace(id=10, camera_id=3, bbox_json="[80, 80, 120, 160]")
        history_rows = [
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=1, camera_id=3, bbox_json="[82, 82, 121, 161]")),
            (SimpleNamespace(label="true_positive"), SimpleNamespace(id=2, camera_id=3, bbox_json="[84, 83, 122, 162]")),
        ]

        result = build_region_memory(
            event=event,
            feedback=None,
            history_rows=history_rows,
            frame_width=320,
            frame_height=240,
        )

        self.assertEqual(result["risk_level"], "RED")
        self.assertEqual(result["true_positive_count"], 1)

    def test_mixed_region_stays_green_when_false_positive_rate_is_high(self):
        event = SimpleNamespace(id=10, camera_id=3, bbox_json="[80, 80, 120, 160]")
        history_rows = [
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=1, camera_id=3, bbox_json="[82, 82, 121, 161]")),
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=2, camera_id=3, bbox_json="[84, 83, 122, 162]")),
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=3, camera_id=3, bbox_json="[81, 81, 119, 159]")),
            (SimpleNamespace(label="true_positive"), SimpleNamespace(id=4, camera_id=3, bbox_json="[85, 82, 124, 160]")),
        ]

        result = build_region_memory(
            event=event,
            feedback=None,
            history_rows=history_rows,
            frame_width=320,
            frame_height=240,
        )

        self.assertEqual(result["risk_level"], "GREEN")
        self.assertEqual(result["decision_hint"], "recurrent_false_positive_region")
        self.assertEqual(result["false_positive_count"], 3)
        self.assertEqual(result["true_positive_count"], 1)
        self.assertAlmostEqual(result["false_positive_rate"], 0.75)

    def test_person_dominant_region_marks_person_support(self):
        event = SimpleNamespace(id=10, camera_id=3, bbox_json="[80, 80, 120, 160]")
        history_rows = [
            (SimpleNamespace(label="true_positive"), SimpleNamespace(id=1, camera_id=3, bbox_json="[82, 82, 121, 161]")),
            (SimpleNamespace(label="true_positive"), SimpleNamespace(id=2, camera_id=3, bbox_json="[84, 83, 122, 162]")),
            (SimpleNamespace(label="false_positive"), SimpleNamespace(id=3, camera_id=3, bbox_json="[81, 81, 119, 159]")),
        ]

        result = build_region_memory(
            event=event,
            feedback=None,
            history_rows=history_rows,
            frame_width=320,
            frame_height=240,
        )

        self.assertEqual(result["risk_level"], "RED")
        self.assertEqual(result["decision_hint"], "person_seen_in_region")
        self.assertAlmostEqual(result["true_positive_rate"], 2 / 3, places=6)

    def test_current_feedback_does_not_count_as_history(self):
        event = SimpleNamespace(id=10, camera_id=3, bbox_json="[80, 80, 120, 160]")
        feedback = SimpleNamespace(id=99, label="false_positive")
        history_rows = [
            (feedback, event),
            (SimpleNamespace(id=1, label="false_positive"), SimpleNamespace(id=1, camera_id=3, bbox_json="[82, 82, 121, 161]")),
            (SimpleNamespace(id=2, label="false_positive"), SimpleNamespace(id=2, camera_id=3, bbox_json="[84, 83, 122, 162]")),
        ]

        result = build_region_memory(
            event=event,
            feedback=feedback,
            history_rows=history_rows,
            frame_width=320,
            frame_height=240,
        )

        self.assertEqual(result["risk_level"], "YELLOW")
        self.assertEqual(result["false_positive_count"], 2)

    def test_history_uses_latest_feedback_once_per_event(self):
        event = SimpleNamespace(id=10, camera_id=3, bbox_json="[80, 80, 120, 160]")
        history_rows = [
            (SimpleNamespace(id=2, label="true_positive"), SimpleNamespace(id=1, camera_id=3, bbox_json="[82, 82, 121, 161]")),
            (SimpleNamespace(id=1, label="false_positive"), SimpleNamespace(id=1, camera_id=3, bbox_json="[82, 82, 121, 161]")),
            (SimpleNamespace(id=3, label="false_positive"), SimpleNamespace(id=2, camera_id=3, bbox_json="[84, 83, 122, 162]")),
        ]

        result = build_region_memory(
            event=event,
            feedback=None,
            history_rows=history_rows,
            frame_width=320,
            frame_height=240,
        )

        self.assertEqual(result["total_reviewed_count"], 2)
        self.assertEqual(result["true_positive_count"], 1)
        self.assertEqual(result["false_positive_count"], 1)


if __name__ == "__main__":
    unittest.main()
