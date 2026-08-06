from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

from app.analytics.camera_profiles import build_camera_analytic_profile, serialize_profile
from app.runtime.camera_config import load_camera_runtime_config
from app.runtime.preprocess import FramePreprocessor


class CameraRuntimeOverridesTest(unittest.TestCase):
    def test_load_camera_runtime_config_uses_manual_overrides(self):
        profile = build_camera_analytic_profile(
            preset_name="teste_leve",
            camera_family="bullet",
            scene_category="perimetral",
            scene_profile="perimeter_outdoor",
            analytic_goal="intrusion",
            manual_overrides={
                "processing_max_width": 640,
                "processing_max_height": 360,
                "processing_upscale_small_frames": False,
                "normal_inference_interval_seconds": 0.5,
                "capture_drop_frames": 4,
                "visual_raw_publish_interval_seconds": 0.1,
                "visual_processed_publish_interval_seconds": 0.1,
                "prefer_motion_test": True,
            },
        )
        camera = SimpleNamespace(
            analytics_profile_json=serialize_profile(profile),
            roi_name=None,
            roi_polygon_json=None,
            line_start_x=None,
            line_start_y=None,
            line_end_x=None,
            line_end_y=None,
            line_direction=None,
            human_event_modes_json=None,
            human_loitering_seconds=None,
            human_detection_sensitivity=None,
            motion_idle_interval=None,
            motion_active_interval=None,
            motion_hold_seconds=None,
            motion_detection_hold_seconds=None,
            motion_min_motion_frames=None,
            motion_downscale_width=None,
            motion_min_contour_area=None,
            motion_ratio_threshold=None,
            motion_global_change_ratio_limit=None,
            motion_background_alpha=None,
            motion_warmup_frames=None,
        )

        runtime_config = load_camera_runtime_config(camera)

        self.assertEqual(runtime_config.processing_max_width, 640)
        self.assertEqual(runtime_config.processing_max_height, 360)
        self.assertFalse(runtime_config.processing_upscale_small_frames)
        self.assertAlmostEqual(runtime_config.normal_inference_interval_seconds, 0.5)
        self.assertEqual(runtime_config.capture_drop_frames, 4)
        self.assertAlmostEqual(runtime_config.visual_raw_publish_interval_seconds, 0.1)
        self.assertAlmostEqual(runtime_config.visual_processed_publish_interval_seconds, 0.1)
        self.assertTrue(runtime_config.prefer_motion_test)

    def test_preprocessor_respects_custom_processing_limits(self):
        preprocessor = FramePreprocessor()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        infer = preprocessor.build_inference_frame(
            frame,
            [],
            max_width=640,
            max_height=360,
            allow_upscale=False,
        )

        self.assertLessEqual(infer.input_width, 640)
        self.assertLessEqual(infer.input_height, 360)
        self.assertEqual(infer.source_width, 1920)
        self.assertEqual(infer.source_height, 1080)

    def test_preprocessor_keeps_full_frame_when_roi_is_configured(self):
        preprocessor = FramePreprocessor()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[:, :960] = (10, 20, 30)
        roi_polygon = [(100, 100), (800, 100), (800, 800), (100, 800)]

        infer = preprocessor.build_inference_frame(
            frame,
            roi_polygon,
            max_width=640,
            max_height=360,
            allow_upscale=False,
        )

        self.assertEqual(infer.frame.shape[:2], (360, 640))
        self.assertEqual(infer.source_width, 1920)
        self.assertEqual(infer.source_height, 1080)
        self.assertEqual(infer.offset_x, 0)
        self.assertEqual(infer.offset_y, 0)
        self.assertFalse(infer.roi_crop_active)
        self.assertIsNone(infer.roi_crop_meta)
        self.assertEqual(tuple(infer.frame[0, 0]), (10, 20, 30))


if __name__ == "__main__":
    unittest.main()
