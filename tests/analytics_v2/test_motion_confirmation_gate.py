from __future__ import annotations

import unittest
import numpy as np
from types import SimpleNamespace
from datetime import datetime

from app.analytics.motion_gate import MotionGate
from app.analytics_v2.revalidation.event_maturity import evaluate_event_maturity
from app.analytics_v2.revalidation.alarm_decision import decide_alarm_action
from app.core.config import settings


class MotionConfirmationGateTest(unittest.TestCase):
    def test_motion_gate_features_no_motion(self):
        gate = MotionGate(threshold=0.015, min_interval_seconds=2.0, downscale_width=320)
        
        frame1 = np.zeros((180, 320, 3), dtype=np.uint8)
        frame2 = np.zeros((180, 320, 3), dtype=np.uint8)
        
        gate.evaluate(frame1)
        gate.evaluate(frame2)
        
        bbox = [10.0, 10.0, 50.0, 50.0]
        features = gate.get_local_motion_features(bbox)
        
        self.assertTrue(features["has_motion_mask"])
        self.assertEqual(features["motion_blobs"], 0)
        self.assertEqual(features["motion_area_pct"], 0.0)

    def test_motion_gate_features_with_blobs(self):
        gate = MotionGate(threshold=0.015, min_interval_seconds=2.0, downscale_width=320)
        
        frame1 = np.zeros((180, 320, 3), dtype=np.uint8)
        frame2 = np.zeros((180, 320, 3), dtype=np.uint8)
        
        # Draw two white squares (moving regions)
        frame2[20:30, 20:30, :] = 255
        frame2[40:50, 40:50, :] = 255
        
        gate.evaluate(frame1)
        gate.evaluate(frame2)
        
        bbox = [15.0, 15.0, 55.0, 55.0]
        features = gate.get_local_motion_features(bbox)
        
        self.assertTrue(features["has_motion_mask"])
        self.assertGreaterEqual(features["motion_blobs"], 2)
        self.assertGreater(features["motion_area_pct"], 0.1)

    def test_event_maturity_includes_motion_features(self):
        class FakeTrackWithHistory:
            def __init__(self, motion_history):
                self.metadata = {"motion_history": motion_history}
                self.bbox_history = [SimpleNamespace(footpoint=(100.0, 100.0))]
                self.visible_frames = 10
                self.age_frames = 10
                self.first_seen = datetime(2026, 5, 5, 12, 0, 0)
                self.last_seen = datetime(2026, 5, 5, 12, 0, 10)
                self.last_detection_score = 0.8
                self.score_avg = 0.8
                self.track_quality = 0.8
                self.geometry_confidence = 0.8
            def recent_motion_distance(self, window=10):
                return 10.0
            def effective_quality(self):
                return self.track_quality

        motion_history = [
            {"motion_area_pct": 0.12, "motion_blobs": 3, "has_motion_mask": True},
            {"motion_area_pct": 0.08, "motion_blobs": 2, "has_motion_mask": True},
            {"motion_area_pct": 0.15, "motion_blobs": 2, "has_motion_mask": True},
        ]
        track = FakeTrackWithHistory(motion_history)
        
        result = evaluate_event_maturity(
            track=track,
            ia2_result=SimpleNamespace(person_score=0.4),
            ia3_result=SimpleNamespace(person_far_score=0.05),
            frame_width=640,
            frame_height=360,
        )
        
        features = result["features"]
        self.assertEqual(features["motion_confirm_samples"], 3)
        self.assertTrue(features["motion_confirm_has_mask"])
        self.assertEqual(features["motion_blobs_median"], 2.0)
        self.assertEqual(features["motion_area_pct_median"], 0.12)
        self.assertFalse(features["motion_confirm_passed"])
        self.assertTrue(features["motion_confirm_passed_blobs"])
        self.assertTrue(features["motion_confirm_passed_area"])
        self.assertFalse(features["motion_confirm_passed_displacement"])
        self.assertFalse(features["motion_confirm_boost"])
        self.assertEqual(features["motion_confirm_signal"], "neutral")

        track.metadata["motion_history"] = [
            {"motion_area_pct": 0.10, "motion_blobs": 5, "has_motion_mask": True},
        ]
        boost_result = evaluate_event_maturity(
            track=track,
            ia2_result=SimpleNamespace(person_score=0.4),
            ia3_result=SimpleNamespace(person_far_score=0.05),
            frame_width=640,
            frame_height=360,
        )
        self.assertTrue(boost_result["features"]["motion_confirm_boost"])
        self.assertEqual(boost_result["features"]["motion_confirm_signal"], "confirmed")

    def test_alarm_decision_audit_preserves_behavior(self):
        event_maturity = {
            "level": "ALARM_READY",
            "decision": "alarm_candidate",
            "safety": {},
            "features": {
                "motion_confirm_passed": False,
                "motion_blobs_median": 0.0,
                "motion_area_pct_median": 0.0,
                "center_displacement_norm": 0.0,
            }
        }
        
        settings.motion_confirm_mode = "audit"
        settings.motion_confirm_enabled = True
        
        result = decide_alarm_action(
            event_maturity=event_maturity,
            ia2_result=SimpleNamespace(person_score=0.35),
            ia3_result=SimpleNamespace(person_far_score=0.02),
            consensus_result={"block_candidate": False},
        )
        
        self.assertEqual(result["action"], "ALARM")
        self.assertEqual(result["suggested_status"], "alarm")
        self.assertFalse(result["applied"])
        self.assertTrue(result["current_behavior_preserved"])
        self.assertEqual(result["inputs"]["motion_confirm_mode"], "audit")
        self.assertFalse(result["inputs"]["motion_confirm_passed"])

    def test_alarm_decision_never_suppresses_on_motion_failure(self):
        event_maturity = {
            "level": "ALARM_READY",
            "decision": "alarm_candidate",
            "safety": {},
            "features": {
                "motion_confirm_passed": False,
                "motion_blobs_median": 0.0,
                "motion_area_pct_median": 0.0,
                "center_displacement_norm": 0.0,
            }
        }
        
        settings.motion_confirm_mode = "block"
        settings.motion_confirm_enabled = True
        
        result = decide_alarm_action(
            event_maturity=event_maturity,
            ia2_result=SimpleNamespace(person_score=0.35),
            ia3_result=SimpleNamespace(person_far_score=0.02),
            consensus_result={"block_candidate": False},
        )
        
        self.assertEqual(result["action"], "ALARM")
        self.assertEqual(result["suggested_status"], "alarm")
        self.assertTrue(result["suggested_is_alarm_active"])
        self.assertFalse(result["applied"])
        self.assertTrue(result["current_behavior_preserved"])
        self.assertFalse(result["inputs"]["motion_confirmation_veto_enabled"])

    def test_alarm_decision_block_does_not_suppress_on_strong_person(self):
        event_maturity = {
            "level": "ALARM_READY",
            "decision": "alarm_candidate",
            "safety": {},
            "features": {
                "motion_confirm_passed": False,
                "motion_blobs_median": 0.0,
                "motion_area_pct_median": 0.0,
                "center_displacement_norm": 0.0,
            }
        }
        
        settings.motion_confirm_mode = "block"
        settings.motion_confirm_enabled = True
        
        result = decide_alarm_action(
            event_maturity=event_maturity,
            ia2_result=SimpleNamespace(person_score=0.55),
            ia3_result=SimpleNamespace(person_far_score=0.02),
            consensus_result={"block_candidate": False},
        )
        
        self.assertEqual(result["action"], "ALARM")
        self.assertEqual(result["suggested_status"], "alarm")
        self.assertTrue(result["suggested_is_alarm_active"])
        self.assertFalse(result["applied"])


if __name__ == "__main__":
    unittest.main()
