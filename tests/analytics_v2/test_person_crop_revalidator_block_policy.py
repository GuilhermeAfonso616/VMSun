from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics_v2.revalidation.person_crop_revalidator import (
    CropRevalidationResult,
    PersonCropRevalidator,
)
from app.core.config import settings
from app.services.revalidator_policy_store import save_revalidator_policy


class PersonCropRevalidatorBlockPolicyTest(unittest.TestCase):
    def setUp(self):
        self._original_mode = settings.person_revalidator_mode
        settings.person_revalidator_mode = "block"
        save_revalidator_policy("block")

    def tearDown(self):
        settings.person_revalidator_mode = self._original_mode
        save_revalidator_policy(self._original_mode)

    def test_block_requires_conservative_quality_gate(self):
        revalidator = PersonCropRevalidator(mode="block")
        result = CropRevalidationResult(
            enabled=True,
            applied=True,
            person_score=0.0005,
            not_person_score=0.9995,
            passed=False,
            mode="block",
            block_eligible=False,
            block_reason="uncertain_bbox_area_too_small",
            quality={"quality_gate_passed": False},
        )

        self.assertFalse(revalidator.should_block(result))

    def test_block_allows_only_extreme_not_person_with_good_quality(self):
        revalidator = PersonCropRevalidator(mode="block")
        result = CropRevalidationResult(
            enabled=True,
            applied=True,
            person_score=0.0005,
            not_person_score=0.9995,
            passed=False,
            mode="block",
            block_eligible=True,
            block_reason="clear_not_person_high_confidence_quality_passed",
            quality={"quality_gate_passed": True},
        )

        self.assertTrue(revalidator.should_block(result))

    def test_nominal_threshold_failure_is_not_enough_to_block(self):
        revalidator = PersonCropRevalidator(mode="block")
        result = CropRevalidationResult(
            enabled=True,
            applied=True,
            person_score=0.006,
            not_person_score=0.994,
            passed=False,
            threshold=0.01,
            mode="block",
            block_eligible=False,
            block_reason="person_score_not_extreme_low",
            quality={"quality_gate_passed": True},
        )

        self.assertFalse(revalidator.should_block(result))

    def test_model_loader_falls_back_to_onnx_sibling(self):
        class FakeYolo:
            def __init__(self, path):
                self.path = str(path)
                if self.path.endswith(".pt"):
                    raise RuntimeError("broken pt checkpoint")

        with tempfile.TemporaryDirectory() as tmpdir:
            pt_path = Path(tmpdir) / "example.pt"
            onnx_path = Path(tmpdir) / "example.onnx"
            pt_path.write_bytes(b"pt")
            onnx_path.write_bytes(b"onnx")
            revalidator = PersonCropRevalidator(model_path=str(pt_path))

            with patch.dict(sys.modules, {"ultralytics": SimpleNamespace(YOLO=FakeYolo)}):
                model = revalidator._load_model()

        self.assertIsNotNone(model)
        self.assertTrue(str(revalidator.model_path).endswith("example.onnx"))


if __name__ == "__main__":
    unittest.main()
