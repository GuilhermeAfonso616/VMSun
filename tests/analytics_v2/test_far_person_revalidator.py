from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

from app.analytics_v2.revalidation.far_person_revalidator import FarPersonRevalidator


class FarPersonRevalidatorTest(unittest.TestCase):
    def test_normal_scale_crop_is_skipped(self):
        frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        revalidator = FarPersonRevalidator(enabled=True, model_path="missing_far_model.pt")

        result = revalidator.validate(
            frame,
            [100, 80, 230, 290],
            base_quality={
                "crop_width": 156,
                "crop_height": 252,
                "quality_reason": "ok",
            },
        )

        self.assertTrue(result.enabled)
        self.assertFalse(result.triggered)
        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "not_far_candidate")
        self.assertEqual(result.trigger_reason, "normal_scale")

    def test_small_crop_triggers_audit_without_blocking(self):
        frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        revalidator = FarPersonRevalidator(enabled=True, model_path="missing_far_model.pt")

        result = revalidator.validate(
            frame,
            [100, 80, 140, 145],
            base_quality={
                "crop_width": 48,
                "crop_height": 78,
                "quality_reason": "bbox_height_too_small",
            },
        )

        self.assertTrue(result.enabled)
        self.assertTrue(result.triggered)
        self.assertFalse(result.applied)
        self.assertIn("model_not_found", result.reason or "")
        self.assertIn("crop_width_small", result.trigger_reason or "")
        self.assertEqual(result.to_metadata()["operational_decision"], "audit_only")

    def test_ia2_strong_not_person_triggers_even_on_normal_scale_crop(self):
        frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
        revalidator = FarPersonRevalidator(enabled=True, model_path="missing_far_model.pt")

        result = revalidator.validate(
            frame,
            [1667, 504, 1746, 649],
            base_quality={
                "crop_width": 110,
                "crop_height": 202,
                "quality_gate_passed": True,
                "quality_reason": "ok",
                "near_border": False,
            },
            ia2_result=SimpleNamespace(
                applied=True,
                person_score=0.012,
                not_person_score=0.987,
            ),
        )

        self.assertTrue(result.triggered)
        self.assertFalse(result.applied)
        self.assertIn("ia2_strong_not_person", result.trigger_reason or "")

    def test_extract_scores_accepts_far_class_names(self):
        class FakeProbs:
            data = [0.93, 0.07]

        class FakeResult:
            probs = FakeProbs()
            names = {0: "not_person_far", 1: "person_far"}

        revalidator = FarPersonRevalidator(enabled=True)

        person_score, not_person_score = revalidator._extract_scores([FakeResult()])

        self.assertEqual(person_score, 0.07)
        self.assertEqual(not_person_score, 0.93)


if __name__ == "__main__":
    unittest.main()
