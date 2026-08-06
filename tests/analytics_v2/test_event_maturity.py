from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.analytics_v2.revalidation.event_maturity import evaluate_event_maturity


def _point(seconds: int, bbox, score: float = 0.8):
    return SimpleNamespace(
        timestamp=datetime(2026, 5, 5, 12, 0, 0) + timedelta(seconds=seconds),
        bbox=list(bbox),
        footpoint=((bbox[0] + bbox[2]) / 2.0, bbox[3]),
        score=score,
        class_name="person",
    )


class FakeTrack:
    def __init__(self, points):
        self.bbox_history = list(points)
        self.visible_frames = len(points)
        self.age_frames = len(points)
        self.first_seen = self.bbox_history[0].timestamp
        self.last_seen = self.bbox_history[-1].timestamp
        self.last_detection_score = self.bbox_history[-1].score
        self.score_avg = sum(point.score for point in self.bbox_history) / max(1, len(self.bbox_history))
        self.track_quality = 0.8
        self.geometry_confidence = 0.8

    def recent_motion_distance(self, window=10):
        points = [point.footpoint for point in self.bbox_history[-window:]]
        total = 0.0
        for idx in range(1, len(points)):
            total += ((points[idx][0] - points[idx - 1][0]) ** 2 + (points[idx][1] - points[idx - 1][1]) ** 2) ** 0.5
        return total

    def effective_quality(self):
        return self.track_quality

    def class_consistency(self):
        return 1.0


class EventMaturityTest(unittest.TestCase):
    def test_moving_persistent_track_becomes_alarm_candidate(self):
        track = FakeTrack(
            [
                _point(0, [20, 30, 60, 130], 0.88),
                _point(1, [35, 32, 77, 136], 0.90),
                _point(2, [55, 34, 100, 145], 0.91),
                _point(3, [80, 36, 128, 155], 0.92),
                _point(4, [110, 40, 162, 168], 0.93),
                _point(5, [145, 43, 200, 180], 0.94),
                _point(6, [180, 46, 238, 194], 0.94),
                _point(7, [220, 50, 282, 210], 0.95),
            ]
        )

        result = evaluate_event_maturity(
            track=track,
            ia2_result=SimpleNamespace(person_score=0.55),
            ia3_result=SimpleNamespace(person_far_score=0.12),
            frame_width=640,
            frame_height=360,
        )

        self.assertEqual(result["decision"], "alarm_candidate")
        self.assertGreaterEqual(result["score"], 0.65)
        self.assertTrue(result["safety"]["best_frame_protects_from_suppression"])

    def test_static_track_is_only_suppress_candidate_audit(self):
        track = FakeTrack([_point(idx, [100, 100, 140, 200], 0.55) for idx in range(10)])
        track.track_quality = 0.35
        track.geometry_confidence = 0.35

        result = evaluate_event_maturity(
            track=track,
            ia2_result=SimpleNamespace(person_score=0.02),
            ia3_result=SimpleNamespace(person_far_score=0.001),
            frame_width=640,
            frame_height=360,
        )

        self.assertEqual(result["decision"], "suppress_candidate_audit")
        self.assertTrue(result["safety"]["static_track"])
        self.assertFalse(result["safety"]["suppress_allowed"])

    def test_static_track_with_strong_visual_person_is_alarm_candidate(self):
        track = FakeTrack([_point(idx, [100, 100, 140, 200], 0.55) for idx in range(10)])
        track.track_quality = 0.35
        track.geometry_confidence = 0.35

        result = evaluate_event_maturity(
            track=track,
            ia2_result=SimpleNamespace(person_score=0.72),
            ia3_result=SimpleNamespace(person_far_score=0.01),
            frame_width=640,
            frame_height=360,
        )

        self.assertEqual(result["decision"], "alarm_candidate")
        self.assertEqual(result["reason"], "visual_person_confirmed_with_temporal_evidence")
        self.assertTrue(result["features"]["visual_person_confirmed"])

    def test_short_fast_track_is_protected(self):
        track = FakeTrack(
            [
                _point(0, [20, 80, 60, 180], 0.76),
                _point(1, [75, 82, 118, 184], 0.78),
                _point(2, [140, 85, 186, 192], 0.80),
            ]
        )

        result = evaluate_event_maturity(
            track=track,
            ia2_result=SimpleNamespace(person_score=0.08),
            ia3_result=SimpleNamespace(person_far_score=0.02),
            frame_width=640,
            frame_height=360,
        )

        self.assertEqual(result["level"], "FAST_MOTION_PROTECTED")
        self.assertEqual(result["decision"], "low_confidence_alarm")
        self.assertTrue(result["safety"]["fast_motion_protected"])
        self.assertTrue(result["safety"]["best_frame_protects_from_suppression"])

    def test_dome_motion_is_uncertain_when_visual_evidence_is_weak(self):
        track = FakeTrack(
            [
                _point(0, [20, 80, 60, 180], 0.52),
                _point(1, [45, 80, 85, 180], 0.55),
                _point(2, [70, 80, 110, 180], 0.56),
                _point(3, [95, 80, 135, 180], 0.56),
                _point(4, [120, 80, 160, 180], 0.57),
                _point(5, [145, 80, 185, 180], 0.57),
                _point(6, [170, 80, 210, 180], 0.58),
                _point(7, [195, 80, 235, 180], 0.58),
            ]
        )

        result = evaluate_event_maturity(
            track=track,
            ia2_result=SimpleNamespace(person_score=0.03),
            ia3_result=SimpleNamespace(person_far_score=0.002),
            frame_width=640,
            frame_height=360,
            camera_family="dome",
        )

        self.assertEqual(result["level"], "CAMERA_MOTION_UNCERTAIN")
        self.assertEqual(result["decision"], "uncertain")
        self.assertTrue(result["safety"]["camera_motion_possible"])
        self.assertTrue(result["safety"]["camera_motion_uncertain"])


if __name__ == "__main__":
    unittest.main()
