from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.track_store import TrackStore


class TrackStoreVisualGateTest(unittest.TestCase):
    def test_track_store_preserves_revalidation_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.services.track_store.settings.runtime_state_dir", tmp):
                store = TrackStore()
                store.set_tracks(
                    3,
                    [
                        {
                            "bbox": [10.0, 20.0, 80.0, 180.0],
                            "track_id": 7,
                            "confidence": 0.81,
                            "visual_person_score": 0.81,
                            "label": "person",
                            "visual_status": "revalidated",
                            "notification_decision": "LOW_PRIORITY",
                            "strategy3_v2_decision": "ACCEPT",
                        }
                    ],
                    frame_width=320,
                    frame_height=240,
                )

                payload = store.get_tracks(3, max_age_seconds=5.0)

        self.assertIsNotNone(payload)
        tracks = payload["tracks"]
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["visual_status"], "revalidated")
        self.assertEqual(tracks[0]["visual_person_score"], 0.81)
        self.assertEqual(tracks[0]["notification_decision"], "LOW_PRIORITY")
        self.assertEqual(tracks[0]["strategy3_v2_decision"], "ACCEPT")


if __name__ == "__main__":
    unittest.main()
