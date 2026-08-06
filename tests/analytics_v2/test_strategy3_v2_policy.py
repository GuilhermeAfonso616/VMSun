from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics_v2.revalidation.alarm_decision import decide_alarm_action
from app.analytics_v2.revalidation.strategy3_v2 import anti_fp_post_filter, evaluate_strategy3_v2


def _track(*, age_frames: int = 0, visible_frames: int = 0, history_len: int = 0, recent_motion: float = 0.0):
    history = [SimpleNamespace(footpoint=(float(index), float(index))) for index in range(history_len)]

    def _recent_motion_distance(window: int = 3):
        return recent_motion

    return SimpleNamespace(
        age_frames=age_frames,
        visible_frames=visible_frames,
        bbox_history=history,
        recent_motion_distance=_recent_motion_distance,
        first_seen=None,
        last_seen=None,
    )


class Strategy3V2PolicyTest(unittest.TestCase):
    def test_ia2_unavailable_with_strong_detector_does_not_suppress(self):
        ia2 = SimpleNamespace(person_score=None, not_person_score=None, applied=False, reason="load_failed:AttributeError")
        ia3 = SimpleNamespace(applied=False, triggered=True, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.72,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=None,
            timestamp=None,
        )

        self.assertEqual(result["initial_decision"], "VISUAL_REVALIDATOR_UNAVAILABLE")
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertIn("fail_open", result["reason"])
        self.assertTrue(result["detector_used_for_accept"])

        anti_fp = anti_fp_post_filter(strategy_result=result, event_context={})
        self.assertNotEqual(anti_fp["decision"], "SUPPRESS")

    def test_detector_high_without_ia3_never_accepts_or_notifies(self):
        ia2 = SimpleNamespace(person_score=0.04, not_person_score=0.96)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.95,
            bbox=[0.0, 0.0, 60.0, 320.0],
            frame_width=1920,
            frame_height=1080,
            camera_id=22,
            track=None,
            timestamp=None,
        )

        self.assertIn(result["decision"], {"AUDIT", "LOW_PRIORITY", "SUPPRESS"})
        self.assertNotEqual(result["decision"], "ACCEPT")

        anti_fp = anti_fp_post_filter(strategy_result=result, event_context={})
        self.assertNotEqual(anti_fp["decision"], "NOTIFY")

    def test_ia3_strong_confirms_gray_zone_accept(self):
        ia2 = SimpleNamespace(person_score=0.06, not_person_score=0.94)
        ia3 = SimpleNamespace(applied=True, triggered=True, person_far_score=0.98, not_person_far_score=0.02)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 720.0, 900.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=3, visible_frames=3, history_len=3, recent_motion=4.0),
            timestamp=None,
        )

        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["independent_confirmation"], "ia3")

        anti_fp = anti_fp_post_filter(strategy_result=result, event_context={})
        self.assertEqual(anti_fp["decision"], "NOTIFY")

        alarm = decide_alarm_action(
            event_maturity={"level": "ALARM_READY", "decision": "alarm_candidate", "safety": {}},
            ia2_result=ia2,
            ia3_result=ia3,
            consensus_result={"block_candidate": False},
            strategy3_v2_result=result,
            anti_fp_post_filter_result=anti_fp,
        )
        self.assertEqual(alarm["suggested_status"], "alarm")
        self.assertTrue(alarm["suggested_is_alarm_active"])

    def test_weak_ia3_in_gray_zone_suppresses(self):
        ia2 = SimpleNamespace(person_score=0.07, not_person_score=0.93)
        ia3 = SimpleNamespace(applied=True, triggered=True, person_far_score=0.01, not_person_far_score=0.99)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.30,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=None,
            timestamp=None,
        )

        self.assertEqual(result["decision"], "SUPPRESS")

        anti_fp = anti_fp_post_filter(strategy_result=result, event_context={})
        self.assertEqual(anti_fp["decision"], "SUPPRESS")

    def test_ia3_below_size_accept_threshold_does_not_confirm(self):
        ia2 = SimpleNamespace(person_score=0.06, not_person_score=0.94)
        ia3 = SimpleNamespace(applied=True, triggered=True, person_far_score=0.30, not_person_far_score=0.70)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.30,
            bbox=[0.0, 0.0, 720.0, 900.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=None,
            timestamp=None,
        )

        self.assertNotEqual(result["independent_confirmation"], "ia3")
        self.assertFalse(result["signals"]["ia3_confirmed"])
        self.assertNotEqual(result["decision"], "ACCEPT")

    def test_temporal_persistence_alone_is_low_priority(self):
        ia2 = SimpleNamespace(person_score=0.06, not_person_score=0.94)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.30,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=3, visible_frames=3, history_len=3, recent_motion=0.0),
            timestamp=None,
        )

        self.assertTrue(result["temporal_persistence"])
        self.assertFalse(result["tracking_confirmed"])
        self.assertEqual(result["independent_confirmation"], "temporal")
        self.assertEqual(result["decision"], "LOW_PRIORITY")
        self.assertTrue(result["signals"]["static_track"])

    def test_static_confident_person_is_not_downgraded_by_anti_fp(self):
        # Pessoa PARADA mas confirmada pela IA2 (loitering / parado num portao)
        # e evento legitimo: as penalidades de baixa-movimentacao sao bypassadas
        # quando person_score >= anti_fp_post_filter_still_penalty_ia2_bypass.
        # Antes esse caso era indevidamente rebaixado para LOW_PRIORITY.
        ia2 = SimpleNamespace(person_score=0.99, not_person_score=0.01)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.80,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=4, visible_frames=4, history_len=4, recent_motion=0.0),
            timestamp=None,
        )

        self.assertEqual(result["decision"], "ACCEPT")
        self.assertTrue(result["signals"]["static_track"])

        anti_fp = anti_fp_post_filter(strategy_result=result, event_context={})
        self.assertEqual(anti_fp["decision"], "NOTIFY")
        self.assertIn("still_penalty_bypassed_ia2_confident", anti_fp["reason"])
        self.assertNotIn("static_track", anti_fp["reason"])

    def test_static_track_with_ia3_confirmation_can_still_notify(self):
        ia2 = SimpleNamespace(person_score=0.99, not_person_score=0.01)
        ia3 = SimpleNamespace(applied=True, triggered=True, person_far_score=0.98, not_person_far_score=0.02)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.80,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=4, visible_frames=4, history_len=4, recent_motion=0.0),
            timestamp=None,
        )

        self.assertEqual(result["decision"], "ACCEPT")
        self.assertTrue(result["signals"]["static_track"])
        self.assertEqual(result["independent_confirmation"], "ia3")

        anti_fp = anti_fp_post_filter(strategy_result=result, event_context={})
        self.assertEqual(anti_fp["decision"], "NOTIFY")

    def test_fast_motion_protects_gray_zone_from_suppress(self):
        ia2 = SimpleNamespace(person_score=0.06, not_person_score=0.94)
        ia3 = SimpleNamespace(applied=True, triggered=True, person_far_score=0.01, not_person_far_score=0.99)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=3, visible_frames=3, history_len=3, recent_motion=20.0),
            timestamp=None,
        )

        self.assertEqual(result["decision"], "LOW_PRIORITY")
        self.assertTrue(result["signals"]["fast_motion_protected"])
        self.assertGreater(result["signals"]["human_motion_score"], 0.0)
        self.assertIn("fast_motion_protected", result["reason"])

        anti_fp = anti_fp_post_filter(strategy_result=result, event_context={})
        self.assertEqual(anti_fp["decision"], "LOW_PRIORITY")

    def test_accept_with_blacklisted_region_is_downgraded(self):
        strategy_result = {
            "decision": "ACCEPT",
            "signals": {
                "temporal_persistence": False,
                "tracking_available": True,
                "tracking_confirmed": False,
                "region_fp_risk": "HIGH",
                "pattern_blacklist_match": True,
                "pattern_whitelist_match": False,
                "ia3_available": False,
                "ia3_confirmed": False,
            },
            "region_fp_risk": "HIGH",
            "independent_confirmation": "none",
        }

        anti_fp = anti_fp_post_filter(strategy_result=strategy_result, event_context={})
        self.assertIn(anti_fp["decision"], {"LOW_PRIORITY", "AUDIT", "SUPPRESS"})
        self.assertNotEqual(anti_fp["decision"], "NOTIFY")

    def test_strategy_audit_remains_audit_in_anti_fp(self):
        strategy_result = {
            "decision": "AUDIT",
            "signals": {
                "temporal_persistence": False,
                "tracking_available": False,
                "tracking_confirmed": False,
                "region_fp_risk": "UNKNOWN",
                "pattern_blacklist_match": False,
                "pattern_whitelist_match": False,
                "ia3_available": False,
                "ia3_confirmed": False,
            },
        }

        anti_fp = anti_fp_post_filter(strategy_result=strategy_result, event_context={})
        self.assertEqual(anti_fp["decision"], "AUDIT")
        self.assertEqual(anti_fp["final_notification_level"], "audit_only")

    def test_region_memory_false_positive_history_maps_to_high_fp_risk(self):
        ia2 = SimpleNamespace(person_score=0.20, not_person_score=0.80)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.50,
            bbox=[80.0, 80.0, 120.0, 160.0],
            frame_width=320,
            frame_height=240,
            camera_id=3,
            track=None,
            timestamp=None,
            region_memory={
                "enabled": True,
                "risk_level": "GREEN",
                "decision_hint": "recurrent_false_positive_region",
                "false_positive_count": 3,
                "true_positive_count": 0,
            },
        )

        self.assertEqual(result["region"]["raw_region_risk_level"], "GREEN")
        self.assertEqual(result["region_fp_risk"], "HIGH")
        self.assertEqual(result["decision"], "AUDIT")

    def test_anti_fp_patterns_blacklist_is_used_by_strategy(self):
        ia2 = SimpleNamespace(person_score=0.20, not_person_score=0.80)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.50,
            bbox=[80.0, 80.0, 120.0, 160.0],
            frame_width=320,
            frame_height=240,
            camera_id=3,
            track=None,
            timestamp=None,
            anti_fp_patterns={
                "3": {
                    "blacklist_regions": [
                        {"name": "door_reflection", "x1": 0.20, "y1": 0.20, "x2": 0.40, "y2": 0.80}
                    ]
                }
            },
        )

        self.assertTrue(result["pattern_blacklist_match"])
        self.assertEqual(result["region_fp_risk"], "HIGH")
        self.assertEqual(result["decision"], "AUDIT")

    def test_hard_fail_without_ia3_is_suppressed(self):
        ia2 = SimpleNamespace(person_score=0.001, not_person_score=0.999)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.99,
            bbox=[0.0, 0.0, 32.0, 64.0],
            frame_width=1920,
            frame_height=1080,
            camera_id=22,
            track=None,
            timestamp=None,
        )

        self.assertEqual(result["decision"], "SUPPRESS")
        self.assertEqual(result["initial_decision"], "REJECT_OR_SUPPRESS_CANDIDATE")

    def test_shadow_discordance_with_weak_motion_suppresses(self):
        # medium bucket, ia2 aceita e tracking_temporal confirmaria normalmente,
        # mas os dois revalidadores-sombra discordam e o motion e fraco: nenhum
        # TP na base rotulada tinha essa combinacao (discordancia dupla + motion
        # fraco), entao vai direto para SUPPRESS.
        ia2 = SimpleNamespace(person_score=0.5, not_person_score=0.5)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=3, visible_frames=3, history_len=3, recent_motion=3.5),
            timestamp=None,
            shadow_discordance_count=2,
        )

        self.assertEqual(result["decision"], "SUPPRESS")
        self.assertIn("shadow_discordant_no_motion", result["reason"])

    def test_shadow_discordance_with_motion_downgrades_to_audit(self):
        # Mesmo cenario, mas com motion real presente: o unico TP da base
        # rotulada com discordancia dupla tinha human_motion_score=0.66, entao
        # o desempate manda para AUDIT em vez de SUPPRESS direto.
        ia2 = SimpleNamespace(person_score=0.5, not_person_score=0.5)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=30, visible_frames=30, history_len=30, recent_motion=35.0),
            timestamp=None,
            shadow_discordance_count=2,
        )

        self.assertEqual(result["decision"], "AUDIT")
        self.assertIn("shadow_discordant_weak_signal", result["reason"])

    def test_single_shadow_disagreement_does_not_downgrade(self):
        # So um dos dois modelos-sombra (v8b OU v8c) discordando nao e sinal
        # forte o suficiente (99,4% de precisao exige os DOIS discordando) -
        # nao deve alterar o comportamento atual.
        ia2 = SimpleNamespace(person_score=0.5, not_person_score=0.5)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=30, visible_frames=30, history_len=30, recent_motion=35.0),
            timestamp=None,
            shadow_discordance_count=1,
        )

        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["independent_confirmation"], "tracking_temporal")

    def test_weak_motion_downgrades_medium_tracking_temporal_to_low_priority(self):
        # Bucket medium: subir o threshold de aceite do IA2 nao ajuda (FP e TP
        # tem ia2_person_score igualmente altos na base rotulada) - o sinal que
        # de fato separa e o human_motion_score. Motion fraco (~0.25) fica
        # abaixo do piso de 0.45 do bucket medium.
        ia2 = SimpleNamespace(person_score=0.5, not_person_score=0.5)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 120.0, 180.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=3, visible_frames=3, history_len=3, recent_motion=3.5),
            timestamp=None,
        )

        self.assertEqual(result["decision"], "LOW_PRIORITY")
        self.assertIn("weak_motion", result["reason"])

    def test_weak_motion_rescued_by_very_high_ia2_score(self):
        # Bucket large, motion fraco (~0.25, abaixo do piso 0.30) mas
        # ia2_person_score extremo (0.97 >= 0.95): na base rotulada, o unico TP
        # com motion fraco nesse bucket tinha ia2_person_score=0.96 e nenhum FP
        # do mesmo grupo chegava perto disso - entao mantem ACCEPT.
        ia2 = SimpleNamespace(person_score=0.97, not_person_score=0.03)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 120.0, 300.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=3, visible_frames=3, history_len=3, recent_motion=3.5),
            timestamp=None,
        )

        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["size_bucket"], "large")

    def test_weak_motion_without_rescue_downgrades_large_bucket(self):
        # Mesmo cenario de motion fraco no bucket large, mas sem o
        # ia2_person_score extremo que resgataria - deve ser rebaixado.
        ia2 = SimpleNamespace(person_score=0.5, not_person_score=0.5)
        ia3 = SimpleNamespace(applied=False, triggered=False, person_far_score=None, not_person_far_score=None)

        result = evaluate_strategy3_v2(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=0.55,
            bbox=[0.0, 0.0, 120.0, 300.0],
            frame_width=1280,
            frame_height=1080,
            camera_id=22,
            track=_track(age_frames=3, visible_frames=3, history_len=3, recent_motion=3.5),
            timestamp=None,
        )

        self.assertEqual(result["decision"], "LOW_PRIORITY")
        self.assertIn("weak_motion", result["reason"])


if __name__ == "__main__":
    unittest.main()
