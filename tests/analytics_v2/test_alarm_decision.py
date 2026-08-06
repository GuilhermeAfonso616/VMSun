from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics_v2.revalidation.alarm_decision import decide_alarm_action


class AlarmDecisionTest(unittest.TestCase):
    def test_alarm_ready_suggests_alarm_without_applying(self):
        result = decide_alarm_action(
            event_maturity={
                "level": "ALARM_READY",
                "decision": "alarm_candidate",
                "safety": {"best_frame_protects_from_suppression": True},
            },
            ia2_result=SimpleNamespace(person_score=0.62),
            ia3_result=SimpleNamespace(person_far_score=0.0),
            consensus_result={"block_candidate": False},
        )

        self.assertEqual(result["action"], "ALARM")
        self.assertEqual(result["suggested_status"], "alarm")
        self.assertTrue(result["suggested_is_alarm_active"])
        self.assertFalse(result["applied"])

    def test_fast_motion_suggests_low_confidence_alarm(self):
        result = decide_alarm_action(
            event_maturity={
                "level": "FAST_MOTION_PROTECTED",
                "decision": "low_confidence_alarm",
                "safety": {"fast_motion_protected": True},
            },
            ia2_result=SimpleNamespace(person_score=0.08),
            ia3_result=SimpleNamespace(person_far_score=0.01),
            consensus_result={"block_candidate": False},
        )

        self.assertEqual(result["action"], "LOW_CONFIDENCE_ALARM")
        self.assertEqual(result["suggested_status"], "low_confidence")
        self.assertTrue(result["suggested_alarm_eligible"])
        self.assertFalse(result["suggested_is_alarm_active"])

    def test_low_motion_does_not_suppress_yet(self):
        result = decide_alarm_action(
            event_maturity={
                "level": "LOW_MOTION",
                "decision": "suppress_candidate_audit",
                "safety": {"static_track": True},
            },
            ia2_result=SimpleNamespace(person_score=0.02),
            ia3_result=SimpleNamespace(person_far_score=0.001),
            consensus_result={"block_candidate": True},
        )

        self.assertEqual(result["action"], "LOW_CONFIDENCE_EVENT")
        self.assertEqual(result["reason"], "low_motion_requires_review_before_suppress")
        self.assertTrue(result["current_behavior_preserved"])

    def test_anti_fp_audit_mode_preserves_current_behavior(self):
        result = decide_alarm_action(
            event_maturity={"level": "ALARM_READY", "decision": "alarm_candidate", "safety": {}},
            ia2_result=SimpleNamespace(person_score=0.80),
            ia3_result=SimpleNamespace(person_far_score=0.0),
            consensus_result={"block_candidate": False},
            strategy3_v2_result={"decision": "ACCEPT", "reason": "test"},
            anti_fp_post_filter_result={
                "enabled": True,
                "mode": "audit",
                "decision": "SUPPRESS",
                "reason": "blacklist_region_match",
            },
        )

        self.assertEqual(result["action"], "SUPPRESS")
        self.assertEqual(result["suggested_status"], "suppressed")
        self.assertFalse(result["applied"])
        self.assertTrue(result["current_behavior_preserved"])


if __name__ == "__main__":
    unittest.main()
