import unittest
from types import SimpleNamespace

from app.analytics.camera_profiles import (
    build_camera_analytic_profile,
    build_profile_preview,
    profile_from_legacy_camera,
)


class CameraProfileTests(unittest.TestCase):
    def test_indoor_dome_discreet_requires_zone_context(self):
        profile = build_camera_analytic_profile(preset_name="indoor_dome_discreet")
        preview = build_profile_preview(profile, frame_width=1920, frame_height=1080)

        self.assertTrue(preview["roi_required"])
        self.assertTrue(preview["full_frame_forbidden"])
        self.assertEqual(preview["scene_counts"]["restricted_zones"], 0)

    def test_perimeter_with_vegetation_forces_ignore_zones_and_confirmation(self):
        profile = build_camera_analytic_profile(
            preset_name="perimeter_bullet",
            nuisance_profile={"vegetation_wind": True},
        )
        preview = build_profile_preview(profile, frame_width=1920, frame_height=1080)

        self.assertTrue(preview["ignore_zones_required"])
        self.assertTrue(preview["full_frame_forbidden"])
        self.assertGreaterEqual(preview["thresholds"]["track_persistence_frames"], 4)
        self.assertGreaterEqual(preview["thresholds"]["alarm_confirmation_seconds"], 1.5)

    def test_ptz_is_not_primary_intrusion_sensor_by_default(self):
        profile = build_camera_analytic_profile(preset_name="ptz_tracking_support")
        preview = build_profile_preview(profile, frame_width=1920, frame_height=1080)

        self.assertFalse(preview["primary_intrusion_sensor"])
        self.assertEqual(preview["effective_goal"], "tracking_verification")

    def test_fisheye_requires_subzones(self):
        profile = build_camera_analytic_profile(preset_name="fisheye_wide_area")
        preview = build_profile_preview(profile, frame_width=1920, frame_height=1080)

        self.assertTrue(preview["subzones_required"])
        self.assertTrue(preview["full_frame_forbidden"])

    def test_lpr_uses_specialized_pipeline(self):
        profile = build_camera_analytic_profile(preset_name="lpr_access_control")
        preview = build_profile_preview(profile, frame_width=1920, frame_height=1080)

        self.assertEqual(preview["specialized_pipeline"], "lpr")
        self.assertEqual(preview["rule_plan"], ["specialized_lpr_pipeline"])

    def test_high_criticality_is_conservative(self):
        profile = build_camera_analytic_profile(
            camera_family="bullet",
            scene_profile="high_criticality",
            analytic_goal="intrusion",
            risk_profile="critical_asset",
        )
        preview = build_profile_preview(profile, frame_width=1920, frame_height=1080)

        self.assertTrue(preview["schedule_required"])
        self.assertTrue(preview["roi_required"])
        self.assertTrue(preview["ignore_zones_required"])
        self.assertGreaterEqual(preview["thresholds"]["person_confidence_min"], 0.60)
        self.assertGreaterEqual(preview["thresholds"]["track_persistence_frames"], 5)
        self.assertGreaterEqual(preview["thresholds"]["alarm_confirmation_seconds"], 2.0)

    def test_legacy_camera_sync_builds_profile_from_legacy_fields(self):
        camera = SimpleNamespace(
            id=7,
            analytics_profile_json=None,
            roi_polygon_json='[{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.9, "y": 0.9}]',
            line_start_x=0.1,
            line_start_y=0.2,
            line_end_x=0.9,
            line_end_y=0.8,
            line_direction="a_to_b",
            human_event_modes_json='["person_entered_roi", "line_crossing"]',
            human_loitering_seconds=8.0,
            human_detection_sensitivity="high",
        )

        profile = profile_from_legacy_camera(camera)

        self.assertEqual(len(profile.roi_polygon), 3)
        self.assertEqual(len(profile.directional_lines), 1)
        self.assertEqual(profile.threshold_profile.person_confidence_min, 0.60)
        self.assertEqual(profile.threshold_profile.dwell_seconds, 8.0)


if __name__ == "__main__":
    unittest.main()
