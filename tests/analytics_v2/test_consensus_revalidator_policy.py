from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics_v2.revalidation.consensus_policy import (
    evaluate_consensus_block_candidate,
    evaluate_layered_operational_decision,
    ia3_v2_protection_blocks_auto_cancel,
)


class ConsensusRevalidatorPolicyTest(unittest.TestCase):
    def test_marks_candidate_only_when_ia2_and_ia3_agree_strong_not_person(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.032,
            not_person_score=0.968,
            quality={"quality_gate_passed": True, "near_border": False},
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.002,
            not_person_far_score=0.998,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertTrue(result["block_candidate"])
        self.assertEqual(result["operational_decision"], "block_candidate_audit")

    def test_does_not_mark_candidate_when_ia2_still_sees_person(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.762,
            not_person_score=0.238,
            quality={"quality_gate_passed": True, "near_border": False},
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.0004,
            not_person_far_score=0.9995,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertIn("ia2_person_extreme_low", result["failed_checks"])

    def test_marks_balanced_candidate_for_less_extreme_consensus(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.097,
            not_person_score=0.903,
            quality={"quality_gate_passed": True, "near_border": False},
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.006,
            not_person_far_score=0.994,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertTrue(result["balanced_block_candidate"])
        self.assertEqual(result["operational_decision"], "balanced_block_candidate_audit")
        self.assertEqual(result["reason"], "balanced_ia2_ia3_consensus_not_person")

    def test_balanced_candidate_still_requires_quality_and_border_safety(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.097,
            not_person_score=0.903,
            quality={"quality_gate_passed": True, "near_border": True},
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.006,
            not_person_far_score=0.994,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["balanced_block_candidate"])
        self.assertIn("not_near_border", result["balanced_failed_checks"])

    def test_marks_ia3_confirmed_dynamic_candidate_when_ia3_is_strong(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.024,
            not_person_score=0.976,
            quality={"quality_gate_passed": True, "near_border": False},
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.006,
            not_person_far_score=0.994,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertTrue(result["ia3_confirmed_dynamic_candidate"])
        self.assertEqual(result["operational_decision"], "ia3_confirmed_dynamic_candidate_audit")
        self.assertEqual(result["reason"], "ia3_confirmed_dynamic_not_person")

    def test_marks_ia2_dominant_candidate_when_ia3_favors_not_person(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.011216985061764717,
            not_person_score=0.988783061504364,
            quality={"quality_gate_passed": True, "near_border": False},
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.08275045454502106,
            not_person_far_score=0.9172495007514954,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertFalse(result["balanced_block_candidate"])
        self.assertFalse(result["ia3_confirmed_dynamic_candidate"])
        self.assertTrue(result["ia2_dominant_ia3_non_person_candidate"])
        self.assertEqual(result["operational_decision"], "ia2_dominant_ia3_non_person_candidate_audit")
        self.assertEqual(result["reason"], "ia2_dominant_ia3_non_person")

    def test_marks_small_bbox_candidate_when_quality_gate_blocks_strong_consensus(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.014,
            not_person_score=0.985,
            quality={
                "quality_gate_passed": False,
                "quality_reason": "bbox_width_too_small",
                "near_border": False,
            },
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.004,
            not_person_far_score=0.996,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertTrue(result["small_bbox_consensus_candidate"])
        self.assertTrue(result["block_blocked_by_quality_gate"])
        self.assertEqual(result["operational_decision"], "small_bbox_consensus_audit")
        self.assertIn("quality_gate_passed", result["failed_checks"])

    def test_marks_ia2_strong_not_person_without_ia3_audit(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.012,
            not_person_score=0.987,
            quality={
                "quality_gate_passed": True,
                "quality_reason": "ok",
                "near_border": False,
            },
        )
        ia3 = SimpleNamespace(
            triggered=False,
            applied=False,
            person_far_score=None,
            not_person_far_score=None,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertTrue(result["ia2_strong_not_person_without_ia3"])
        self.assertEqual(result["operational_decision"], "ia2_strong_not_person_without_ia3_audit")
        self.assertIn("ia3_triggered", result["failed_checks"])

    def test_marks_ia2_only_balanced_candidate_without_ia3_when_crop_is_good(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.140,
            not_person_score=0.860,
            quality={
                "quality_gate_passed": True,
                "quality_reason": "ok",
                "near_border": False,
            },
        )
        ia3 = SimpleNamespace(
            triggered=False,
            applied=False,
            person_far_score=None,
            not_person_far_score=None,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertFalse(result["balanced_block_candidate"])
        self.assertTrue(result["ia2_only_balanced_candidate"])
        self.assertEqual(result["operational_decision"], "ia2_only_balanced_candidate_audit")
        self.assertEqual(result["reason"], "ia2_only_balanced_not_person")

    def test_ia2_only_balanced_candidate_still_requires_quality_and_border_safety(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.140,
            not_person_score=0.860,
            quality={
                "quality_gate_passed": True,
                "quality_reason": "ok",
                "near_border": True,
            },
        )
        ia3 = SimpleNamespace(
            triggered=False,
            applied=False,
            person_far_score=None,
            not_person_far_score=None,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["ia2_only_balanced_candidate"])
        self.assertIn("not_near_border", result["ia2_only_failed_checks"])

    def test_marks_border_candidate_when_border_blocks_strong_consensus(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.001,
            not_person_score=0.999,
            quality={
                "quality_gate_passed": False,
                "quality_reason": "bbox_near_border",
                "near_border": True,
            },
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.001,
            not_person_far_score=0.999,
        )

        result = evaluate_consensus_block_candidate(ia2, ia3)

        self.assertFalse(result["block_candidate"])
        self.assertTrue(result["border_consensus_candidate"])
        self.assertTrue(result["block_blocked_by_border"])
        self.assertEqual(result["operational_decision"], "border_consensus_audit")
        self.assertIn("not_near_border", result["failed_checks"])

    def test_layered_decision_suppresses_green_region_without_blocking(self):
        ia2 = SimpleNamespace(
            applied=True,
            person_score=0.032,
            not_person_score=0.968,
            quality={"quality_gate_passed": True, "near_border": False},
        )
        ia3 = SimpleNamespace(
            triggered=True,
            applied=True,
            person_far_score=0.002,
            not_person_far_score=0.998,
        )
        consensus = evaluate_consensus_block_candidate(ia2, ia3)

        result = evaluate_layered_operational_decision(
            ia2_result=ia2,
            ia3_result=ia3,
            consensus_result=consensus,
            region_memory={"risk_level": "GREEN"},
        )

        self.assertEqual(result["decision"], "suppress_candidate")
        self.assertFalse(result["safety"]["auto_block_enabled"])

    def test_ia3_v2_protection_vetoes_auto_cancel_only_when_it_protects_person(self):
        self.assertTrue(
            ia3_v2_protection_blocks_auto_cancel(
                {"protects_when_primary_rejects": True, "recommended_action": "UNCERTAIN_AUDIT"}
            )
        )
        self.assertFalse(ia3_v2_protection_blocks_auto_cancel(None))
        self.assertFalse(
            ia3_v2_protection_blocks_auto_cancel(
                {"protects_when_primary_rejects": False, "recommended_action": "NO_RUNTIME_CHANGE"}
            )
        )


if __name__ == "__main__":
    unittest.main()
