from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from app.runtime import worker_base as worker_module
from app.runtime.output import VisualPublishScheduler


class VisualPublishThrottleTest(unittest.TestCase):
    def setUp(self):
        self.worker = worker_module.BaseCameraWorker.__new__(worker_module.BaseCameraWorker)
        self.worker.camera_id = 6
        self.worker.process_pid = 4321
        self.worker.logger = MagicMock()
        self.worker.render_frame = MagicMock()
        self.worker.visual_scheduler = VisualPublishScheduler(
            raw_publish_interval_seconds=0.05,
            processed_publish_interval_seconds=10.0,
        )

        self.frame = np.zeros((32, 32, 3), dtype=np.uint8)
        self.geometry = SimpleNamespace(roi_polygon=[], line_pixels=None)
        self.decision = SimpleNamespace(motion_info={})

    def _raw_publish_result(self):
        return {
            "ok": True,
            "source": "shared_memory",
            "encode_ms": 1.5,
            "jpeg_size": 128,
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }

    def _processed_publish_result(self):
        return {
            "ok": True,
            "source": "shared_memory",
            "encode_ms": 2.5,
            "jpeg_size": 256,
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }

    def test_processed_publish_throttle_skips_overlay_and_processed_store(self):
        with patch.object(worker_module.time, "monotonic", side_effect=[100.0, 100.2]):
            with patch.object(worker_module.frame_store, "set_raw_frame", return_value=self._raw_publish_result()) as raw_store:
                with patch.object(worker_module.frame_store, "set_processed_frame", return_value=self._processed_publish_result()) as processed_store:
                    with patch.object(worker_module, "normalize_display_frame", side_effect=lambda frame: frame) as normalize_mock:
                        first = self.worker._publish_visual_frame(
                            frame=self.frame,
                            geometry=self.geometry,
                            infer_ran=True,
                            decision=self.decision,
                            roi_crop_meta=None,
                            tracks=[],
                            frame_width=32,
                            frame_height=32,
                        )
                        second = self.worker._publish_visual_frame(
                            frame=self.frame,
                            geometry=self.geometry,
                            infer_ran=True,
                            decision=self.decision,
                            roi_crop_meta=None,
                            tracks=[],
                            frame_width=32,
                            frame_height=32,
                        )

        self.assertEqual(raw_store.call_count, 2)
        self.assertEqual(processed_store.call_count, 1)
        self.assertEqual(self.worker.render_frame.call_count, 1)
        self.assertEqual(normalize_mock.call_count, 1)
        self.assertIsNotNone(first["annotated_frame"])
        self.assertIsNone(second["annotated_frame"])
        self.assertEqual(second["visual_stats"]["processed_frames_skipped_by_throttle"], 1)
        self.assertEqual(second["visual_stats"]["raw_frames_published"], 2)
        self.assertEqual(second["visual_stats"]["processed_frames_published"], 1)

    def test_raw_publish_throttle_skips_raw_store_but_keeps_processed_publish(self):
        self.worker.visual_scheduler = VisualPublishScheduler(
            raw_publish_interval_seconds=10.0,
            processed_publish_interval_seconds=0.05,
        )

        with patch.object(worker_module.time, "monotonic", side_effect=[200.0, 200.2]):
            with patch.object(worker_module.frame_store, "set_raw_frame", return_value=self._raw_publish_result()) as raw_store:
                with patch.object(worker_module.frame_store, "set_processed_frame", return_value=self._processed_publish_result()) as processed_store:
                    with patch.object(worker_module, "normalize_display_frame", side_effect=lambda frame: frame):
                        self.worker._publish_visual_frame(
                            frame=self.frame,
                            geometry=self.geometry,
                            infer_ran=True,
                            decision=self.decision,
                            roi_crop_meta=None,
                            tracks=[],
                            frame_width=32,
                            frame_height=32,
                        )
                        second = self.worker._publish_visual_frame(
                            frame=self.frame,
                            geometry=self.geometry,
                            infer_ran=True,
                            decision=self.decision,
                            roi_crop_meta=None,
                            tracks=[],
                            frame_width=32,
                            frame_height=32,
                        )

        self.assertEqual(raw_store.call_count, 1)
        self.assertEqual(processed_store.call_count, 2)
        self.assertEqual(self.worker.render_frame.call_count, 2)
        self.assertIsNotNone(second["annotated_frame"])
        self.assertEqual(second["visual_stats"]["raw_frames_skipped_by_throttle"], 1)
        self.assertEqual(second["visual_stats"]["processed_frames_published"], 2)

    def test_zero_interval_disables_publish(self):
        self.worker.visual_scheduler = VisualPublishScheduler(
            raw_publish_interval_seconds=0.0,
            processed_publish_interval_seconds=0.0,
        )

        with patch.object(worker_module.time, "monotonic", return_value=300.0):
            with patch.object(worker_module.frame_store, "set_raw_frame") as raw_store:
                with patch.object(worker_module.frame_store, "set_processed_frame") as processed_store:
                    result = self.worker._publish_visual_frame(
                        frame=self.frame,
                        geometry=self.geometry,
                        infer_ran=True,
                        decision=self.decision,
                        roi_crop_meta=None,
                        tracks=[],
                        frame_width=32,
                        frame_height=32,
                    )

        raw_store.assert_not_called()
        processed_store.assert_not_called()
        self.worker.render_frame.assert_not_called()
        self.assertIsNone(result["annotated_frame"])


if __name__ == "__main__":
    unittest.main()
