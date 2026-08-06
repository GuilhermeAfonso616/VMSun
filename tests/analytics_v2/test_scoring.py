import unittest
from datetime import datetime, timedelta

from app.analytics_v2.config.schema import AnalyticsConfig, DirectionalLine, RuleConfig
from app.analytics_v2.events.dedupe import DeduplicationState
from app.core.timezone import utc_now_naive
from app.analytics_v2.rules.pipeline import IntrusionRuleEngine
from app.analytics_v2.scene.model import AnalyticsScene
from app.analytics_v2.scoring.components import motion_plausibility
from app.analytics_v2.config.schema import SceneZone
from app.analytics_v2.tracking.enums import TrackState
from app.analytics_v2.tracking.types import Track, TrackHistoryPoint


class ScoringAndRulesTests(unittest.TestCase):
    def test_rule_engine_emits_event_for_confirmed_track(self):
        config = AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_dwell_ms=0,
                    min_event_score=0.0,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(
            width=100,
            height=100,
            restricted_zones=[SceneZone(zone_id="roi_1", name="ROI", polygon=[(0, 0), (100, 0), (100, 100), (0, 100)], zone_type="roi", enabled=True)],
        )
        now = utc_now_naive()
        track = Track(
            track_id=1,
            state=TrackState.CONFIRMED,
            bbox_current=[10, 10, 35, 90],
            footpoint_current=(22.5, 90),
            first_seen=now - timedelta(seconds=1),
            last_seen=now,
            visible_frames=3,
            age_frames=3,
            class_votes={"person": 3},
        )
        track.zone_history.extend(["roi_1", "roi_1"])
        track.bbox_history.append(
            TrackHistoryPoint(timestamp=now, bbox=[10, 10, 35, 90], footpoint=(22.5, 90), score=0.9, class_name="person")
        )
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(len(suppressed), 0)
        self.assertIn(events[0].event_type, {"person_entered_roi", "person_entered"})

    def test_scene_observation_records_zone_history(self):
        scene = AnalyticsScene(
            width=100,
            height=100,
            restricted_zones=[SceneZone(zone_id="roi_1", name="ROI", polygon=[(0, 0), (100, 0), (100, 100), (0, 100)], zone_type="roi", enabled=True)],
        )
        now = utc_now_naive()
        track = Track(
            track_id=1,
            state=TrackState.CONFIRMED,
            bbox_current=[10, 10, 20, 90],
            footpoint_current=(15, 90),
            first_seen=now,
            last_seen=now,
            visible_frames=3,
            age_frames=3,
            class_votes={"person": 3},
        )
        track.bbox_history.append(
            TrackHistoryPoint(timestamp=now, bbox=[10, 10, 20, 90], footpoint=(15, 90), score=0.9, class_name="person")
        )
        observation = scene.observe_track(track)
        self.assertTrue(observation.in_restricted_area)
        self.assertEqual(track.zone_history[-1], "roi_1")

    def test_exclusion_zone_blocks_intrusion_event(self):
        config = AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_dwell_ms=0,
                    min_event_score=0.0,
                    block_near_border=True,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(
            width=100,
            height=100,
            exclusion_zones=[SceneZone(zone_id="edge_left", name="Edge Left", polygon=[(0, 0), (10, 0), (10, 100), (0, 100)], zone_type="exclusion", enabled=True)],
            restricted_zones=[SceneZone(zone_id="roi_1", name="ROI", polygon=[(0, 0), (100, 0), (100, 100), (0, 100)], zone_type="roi", enabled=True)],
        )
        now = utc_now_naive()
        track = Track(
            track_id=2,
            state=TrackState.CONFIRMED,
            bbox_current=[0, 10, 8, 80],
            footpoint_current=(4, 80),
            first_seen=now - timedelta(seconds=1),
            last_seen=now,
            visible_frames=4,
            age_frames=4,
            class_votes={"person": 4},
            geometry_confidence=0.1,
            border_confidence=0.1,
            size_confidence=0.1,
        )
        track.bbox_history.append(
            TrackHistoryPoint(timestamp=now, bbox=[0, 10, 8, 80], footpoint=(4, 80), score=0.9, class_name="person")
        )
        scene.observe_track(track)
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertEqual(len(events), 0)
        self.assertGreaterEqual(len(suppressed), 0)
        self.assertIn("exclusion:edge_left", track.zone_history[-1])

    def test_border_false_positive_is_suppressed(self):
        config = AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_dwell_ms=0,
                    min_event_score=0.0,
                    block_near_border=True,
                    min_motion_distance_px=4.0,
                    min_geometry_confidence=0.35,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(
            width=200,
            height=120,
            restricted_zones=[SceneZone(zone_id="roi_1", name="ROI", polygon=[(0, 0), (200, 0), (200, 120), (0, 120)], zone_type="roi", enabled=True)],
        )
        now = utc_now_naive()
        track = Track(
            track_id=3,
            state=TrackState.CONFIRMED,
            bbox_current=[0, 20, 18, 90],
            footpoint_current=(9, 90),
            first_seen=now - timedelta(seconds=2),
            last_seen=now,
            visible_frames=4,
            age_frames=4,
            class_votes={"person": 4},
        )
        track.bbox_history.extend(
            [
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=900), bbox=[1, 20, 19, 90], footpoint=(10, 90), score=0.6, class_name="person"),
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=400), bbox=[0, 20, 18, 90], footpoint=(9, 90), score=0.6, class_name="person"),
            ]
        )
        scene.observe_track(track)
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertEqual(len(events), 0)
        self.assertGreaterEqual(len(suppressed), 0)

    def test_full_frame_side_border_false_positive_is_suppressed(self):
        config = AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_dwell_ms=0,
                    min_event_score=0.0,
                    min_track_quality=0.1,
                    block_near_border=True,
                    min_motion_distance_px=4.0,
                    min_geometry_confidence=0.35,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(width=200, height=120)
        now = utc_now_naive()
        track = Track(
            track_id=30,
            state=TrackState.CONFIRMED,
            bbox_current=[188, 20, 198, 88],
            footpoint_current=(193, 88),
            first_seen=now - timedelta(seconds=2),
            last_seen=now,
            visible_frames=4,
            age_frames=4,
            class_votes={"person": 4},
            score_avg=0.95,
            track_quality=0.8,
        )
        track.bbox_history.extend(
            [
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=900), bbox=[187, 20, 197, 88], footpoint=(192, 88), score=0.95, class_name="person"),
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=400), bbox=[188, 20, 198, 88], footpoint=(193, 88), score=0.95, class_name="person"),
            ]
        )
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertEqual(len(events), 0)
        self.assertGreaterEqual(len(suppressed), 0)

    def test_static_high_detector_full_frame_is_allowed_for_revalidator_audit(self):
        config = AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_dwell_ms=0,
                    min_event_score=0.0,
                    min_track_quality=0.1,
                    min_motion_distance_px=5.0,
                    min_motion_plausibility=0.35,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(width=200, height=120)
        now = utc_now_naive()
        track = Track(
            track_id=32,
            state=TrackState.CONFIRMED,
            bbox_current=[70, 20, 100, 90],
            footpoint_current=(85, 90),
            first_seen=now - timedelta(seconds=2),
            last_seen=now,
            visible_frames=4,
            age_frames=4,
            class_votes={"person": 4},
            score_avg=0.98,
            track_quality=0.9,
            geometry_confidence=0.9,
        )
        track.bbox_history.extend(
            [
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=900), bbox=[70, 20, 100, 90], footpoint=(85, 90), score=0.98, class_name="person"),
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=400), bbox=[70, 20, 100, 90], footpoint=(85, 90), score=0.98, class_name="person"),
            ]
        )
        self.assertLess(motion_plausibility(track), 0.35)
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertEqual(len(events), 1)
        self.assertGreaterEqual(len(suppressed), 0)

    def test_full_frame_policy_hint_does_not_suppress_intrusion_without_roi(self):
        config = AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_dwell_ms=0,
                    min_event_score=0.0,
                    min_track_quality=0.1,
                    roi_required=True,
                    full_frame_forbidden=True,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(width=200, height=120)
        now = utc_now_naive()
        track = Track(
            track_id=33,
            state=TrackState.CONFIRMED,
            bbox_current=[70, 20, 100, 90],
            footpoint_current=(85, 90),
            first_seen=now - timedelta(seconds=2),
            last_seen=now,
            visible_frames=4,
            age_frames=4,
            class_votes={"person": 4},
            score_avg=0.98,
            track_quality=0.9,
            geometry_confidence=0.9,
        )
        track.bbox_history.extend(
            [
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=900), bbox=[64, 20, 94, 90], footpoint=(79, 90), score=0.98, class_name="person"),
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=400), bbox=[70, 20, 100, 90], footpoint=(85, 90), score=0.98, class_name="person"),
            ]
        )
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(suppressed), 0)

    def test_full_frame_bottom_cut_person_is_not_rejected_by_border_only(self):
        config = AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_dwell_ms=0,
                    min_event_score=0.0,
                    min_track_quality=0.1,
                    block_near_border=True,
                    min_motion_distance_px=4.0,
                    min_geometry_confidence=0.35,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(width=200, height=120)
        now = utc_now_naive()
        track = Track(
            track_id=31,
            state=TrackState.CONFIRMED,
            bbox_current=[76, 40, 104, 120],
            footpoint_current=(90, 120),
            first_seen=now - timedelta(seconds=2),
            last_seen=now,
            visible_frames=4,
            age_frames=4,
            class_votes={"person": 4},
            score_avg=0.9,
            track_quality=0.8,
        )
        track.bbox_history.extend(
            [
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=900), bbox=[70, 40, 98, 120], footpoint=(84, 120), score=0.9, class_name="person"),
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=400), bbox=[76, 40, 104, 120], footpoint=(90, 120), score=0.9, class_name="person"),
            ]
        )
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(len(suppressed), 0)

    def test_line_crossing_requires_crossing_drawn_segment(self):
        config = AnalyticsConfig(
            rules={
                "line_crossing_default": RuleConfig(
                    rule_id="line_crossing_default",
                    rule_type="line_crossing",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_event_score=0.0,
                    min_track_quality=0.1,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(
            width=200,
            height=120,
            directional_lines=[
                DirectionalLine(line_id="line_1", name="Line 1", start=(0, 50), end=(100, 50), direction="any")
            ],
        )
        now = utc_now_naive()
        track = Track(
            track_id=40,
            state=TrackState.CONFIRMED,
            bbox_current=[140, 50, 160, 90],
            footpoint_current=(150, 90),
            first_seen=now - timedelta(seconds=1),
            last_seen=now,
            visible_frames=3,
            age_frames=3,
            class_votes={"person": 3},
            score_avg=0.9,
            track_quality=0.8,
        )
        track.bbox_history.extend(
            [
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=500), bbox=[140, 0, 160, 40], footpoint=(150, 40), score=0.9, class_name="person"),
                TrackHistoryPoint(timestamp=now, bbox=[140, 50, 160, 90], footpoint=(150, 90), score=0.9, class_name="person"),
            ]
        )
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertEqual(len(events), 0)
        self.assertEqual(len(suppressed), 0)

    def test_line_crossing_direction_matches_a_to_b(self):
        config = AnalyticsConfig(
            rules={
                "line_crossing_default": RuleConfig(
                    rule_id="line_crossing_default",
                    rule_type="line_crossing",
                    enabled=True,
                    min_track_age_frames=1,
                    min_visible_frames=1,
                    min_event_score=0.0,
                    min_track_quality=0.1,
                )
            }
        )
        engine = IntrusionRuleEngine(config)
        scene = AnalyticsScene(
            width=200,
            height=120,
            directional_lines=[
                DirectionalLine(line_id="line_1", name="Line 1", start=(0, 50), end=(100, 50), direction="a_to_b")
            ],
        )
        now = utc_now_naive()
        track = Track(
            track_id=41,
            state=TrackState.CONFIRMED,
            bbox_current=[40, 0, 60, 40],
            footpoint_current=(50, 40),
            first_seen=now - timedelta(seconds=1),
            last_seen=now,
            visible_frames=3,
            age_frames=3,
            class_votes={"person": 3},
            score_avg=0.9,
            track_quality=0.8,
        )
        track.bbox_history.extend(
            [
                TrackHistoryPoint(timestamp=now - timedelta(milliseconds=500), bbox=[40, 50, 60, 90], footpoint=(50, 90), score=0.9, class_name="person"),
                TrackHistoryPoint(timestamp=now, bbox=[40, 0, 60, 40], footpoint=(50, 40), score=0.9, class_name="person"),
            ]
        )
        events, suppressed = engine.evaluate(camera_id=1, tracks=[track], scene=scene, tracker_metrics=engine.metrics, now=now)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "line_crossing")

    def test_dedupe_blocks_duplicate_signature(self):
        dedupe = DeduplicationState(cooldown_seconds=10.0, dedupe_window_seconds=5.0)
        now = utc_now_naive()
        self.assertTrue(dedupe.should_emit("a:b:c", now, 0.8))
        self.assertFalse(dedupe.should_emit("a:b:c", now, 0.9))

    def test_dedupe_blocks_same_track_burst(self):
        dedupe = DeduplicationState(cooldown_seconds=10.0, dedupe_window_seconds=2.0)
        now = utc_now_naive()
        self.assertTrue(dedupe.should_emit("1:rule:track:event", now, 0.9, track_id=1, camera_id=1, rule_id="rule"))
        self.assertFalse(dedupe.should_emit("1:rule:track:event2", now, 0.88, track_id=1, camera_id=1, rule_id="rule"))


if __name__ == "__main__":
    unittest.main()
