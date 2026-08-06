import unittest
from datetime import datetime

from app.analytics_v2.detection.types import DetectionCandidate
from app.analytics_v2.tracking.enums import TrackState
from app.analytics_v2.tracking.tracker import StatefulTracker
from app.core.timezone import utc_now_naive


class TrackerStateTests(unittest.TestCase):
    def test_track_progresses_through_states(self):
        tracker = StatefulTracker()
        det = DetectionCandidate(bbox=[10, 10, 30, 60], score=0.9, class_name="person")
        now = utc_now_naive()

        tracks = tracker.update([det], timestamp=now)
        self.assertEqual(tracks[0].state, TrackState.NEW_CANDIDATE)

        tracks = tracker.update([det], timestamp=now)
        self.assertEqual(tracks[0].state, TrackState.PROBATION)

        tracks = tracker.update([det], timestamp=now)
        self.assertEqual(tracks[0].state, TrackState.CONFIRMED)

    def test_new_track_is_not_immediately_treated_as_unmatched(self):
        tracker = StatefulTracker()
        det = DetectionCandidate(bbox=[10, 10, 30, 60], score=0.9, class_name="person")
        now = utc_now_naive()

        tracks = tracker.update([det], timestamp=now)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].lost_frames, 0)
        self.assertGreater(tracks[0].track_quality, 0.0)


if __name__ == "__main__":
    unittest.main()
