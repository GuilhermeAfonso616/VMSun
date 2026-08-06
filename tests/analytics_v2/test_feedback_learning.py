from __future__ import annotations

import shutil
import unittest
from uuid import uuid4
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analytics.camera_profiles import build_camera_analytic_profile, profile_from_camera, serialize_profile
from app.core.config import settings
from app.core.timezone import utc_now_naive
from app.db.base import Base
from app.db.models import Camera, Event, EventFeedback, LockdownDelivery, TuningSuggestion
from app.services.event_retention_service import prune_expired_events
from app.services.feedback_learning_service import (
    apply_tuning_suggestion,
    build_event_review_payload,
    generate_policy_suggestions,
    record_feedback,
)
from app.services.local_clip_retention_service import prune_local_review_clips


class FeedbackLearningServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self._original_feedback_dataset_dir = settings.revalidator_feedback_dataset_dir
        tmp_root = Path(settings.app_base_dir) / ".tmp_tests"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self._feedback_dataset_tmp = tmp_root / f"feedback_{uuid4().hex}"
        self._feedback_dataset_tmp.mkdir(parents=True, exist_ok=True)
        settings.revalidator_feedback_dataset_dir = str(self._feedback_dataset_tmp)

        profile = build_camera_analytic_profile(
            preset_name="perimeter_bullet",
            camera_family="bullet",
            scene_profile="perimeter_outdoor",
            analytic_goal="intrusion",
            nuisance_profile={"vegetation_wind": True},
            roi_polygon=[
                {"x": 0.1, "y": 0.1},
                {"x": 0.8, "y": 0.1},
                {"x": 0.8, "y": 0.8},
                {"x": 0.1, "y": 0.8},
            ],
        )
        self.camera = Camera(
            name="Cam 1",
            ip="10.0.0.1",
            username="admin",
            password="secret",
            rtsp_url="rtsp://example",
            status="idle",
            analytics_profile_json=serialize_profile(profile),
            learning_mode="assisted_policy_tuning",
            min_reviewed_events_for_suggestion=1,
            min_reviewed_events_for_auto_tuning=1,
        )
        self.db.add(self.camera)
        self.db.commit()
        self.db.refresh(self.camera)
        self._original_review_audit_dir = settings.revalidator_review_audit_dir
        self._review_audit_tmp = tmp_root / f"audit_{uuid4().hex}"
        self._review_audit_tmp.mkdir(parents=True, exist_ok=True)
        settings.revalidator_review_audit_dir = str(self._review_audit_tmp)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        settings.revalidator_feedback_dataset_dir = self._original_feedback_dataset_dir
        settings.revalidator_review_audit_dir = self._original_review_audit_dir
        shutil.rmtree(self._feedback_dataset_tmp, ignore_errors=True)
        shutil.rmtree(self._review_audit_tmp, ignore_errors=True)

    def _make_event(self, event_type="person_entered_roi", score=0.62, detector_score=0.41):
        event = Event(
            camera_id=self.camera.id,
            event_type=event_type,
            status="new",
            confidence=score,
            event_score=score,
            detector_score=detector_score,
            severity="high",
            rule_id="intrusion_default",
            scene_profile="perimeter_outdoor",
            camera_family="bullet",
            threshold_snapshot='{"alarm_confirmation_seconds": 1.2}',
            active_profile_snapshot='{"preset_name": "perimeter_bullet"}',
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def test_record_feedback_closes_false_positive(self):
        event = self._make_event()
        feedback = record_feedback(
            self.db,
            event=event,
            label="false_positive",
            probable_cause="vegetation_wind",
            operator_note="folhagem",
            reviewed_by="operador",
        )
        self.db.commit()

        self.assertEqual(feedback.label, "false_positive")
        self.assertEqual(event.status, "closed")
        self.assertFalse(bool(event.is_alarm_active))
        persisted = self.db.query(EventFeedback).count()
        self.assertEqual(persisted, 1)
        metadata_files = list(Path(settings.revalidator_feedback_dataset_dir).glob("not_person/metadata/*.json"))
        self.assertEqual(len(metadata_files), 1)

    def test_record_feedback_deletes_uploaded_onedrive_clip_after_review(self):
        event = self._make_event()
        event.clip_remote_item_id = "remote-1"
        event.clip_remote_status = "uploaded"
        self.db.commit()

        with patch("app.services.feedback_learning_service.onedrive_client.delete_item", return_value=True) as delete_item:
            record_feedback(
                self.db,
                event=event,
                label="true_positive",
                probable_cause="normal_human_flow",
                operator_note="ok",
                reviewed_by="operador",
            )
            self.db.commit()

        delete_item.assert_called_once_with("remote-1")
        self.assertEqual(event.clip_remote_status, "deleted_after_review")

    def test_review_payload_marks_uploaded_onedrive_clip_as_video_available(self):
        event = self._make_event()
        event.clip_remote_item_id = "remote-1"
        event.clip_remote_status = "uploaded"
        event.clip_remote_web_url = "https://example.test/clip"
        self.db.commit()

        payload = build_event_review_payload(self.db, camera_id=self.camera.id, days=30)

        self.assertTrue(payload["events"][0]["clip_video_available"])
        self.assertEqual(payload["events"][0]["clip_remote_web_url"], "https://example.test/clip")

    def test_record_feedback_deletes_local_video_after_review(self):
        clip_dir = self._review_audit_tmp / "clip_local"
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "metadata.json").write_text('{"video_file": "clip.mp4"}', encoding="utf-8")
        clip_file = clip_dir / "clip.mp4"
        clip_file.write_bytes(b"video")
        event = self._make_event()
        event.clip_path = str(clip_dir)
        self.db.commit()

        record_feedback(
            self.db,
            event=event,
            label="true_positive",
            probable_cause="normal_human_flow",
            operator_note="ok",
            reviewed_by="operador",
        )
        self.db.commit()

        self.assertFalse(clip_file.exists())
        self.assertIsNotNone(event.clip_local_deleted_at)

    def test_local_clip_retention_keeps_only_newest_false_positives_and_total_limit(self):
        original_total = settings.local_clip_retention_max_total
        original_false_positive = settings.local_clip_retention_max_false_positive
        settings.local_clip_retention_max_total = 4
        settings.local_clip_retention_max_false_positive = 2
        try:
            events: list[Event] = []
            for idx in range(6):
                clip_dir = self._review_audit_tmp / f"clip_{idx}"
                clip_dir.mkdir(parents=True, exist_ok=True)
                (clip_dir / "metadata.json").write_text('{"video_file": "clip.mp4"}', encoding="utf-8")
                (clip_dir / "clip.mp4").write_bytes(b"video")
                event = self._make_event()
                event.clip_path = str(clip_dir)
                events.append(event)
                if idx < 3:
                    self.db.add(
                        EventFeedback(
                            event_id=event.id,
                            camera_id=self.camera.id,
                            label="false_positive",
                            reviewed_by="operador",
                            reviewed_at=utc_now_naive(),
                        )
                    )
                elif idx == 3:
                    self.db.add(
                        EventFeedback(
                            event_id=event.id,
                            camera_id=self.camera.id,
                            label="true_positive",
                            reviewed_by="operador",
                            reviewed_at=utc_now_naive(),
                        )
                    )
                self.db.commit()

            result = prune_local_review_clips(self.db)
            self.db.commit()

            self.assertEqual(result["deleted_non_false_positive"], 1)
            self.assertEqual(result["deleted_false_positive_overflow"], 1)
            existing = [event for event in events if (Path(event.clip_path) / "clip.mp4").exists()]
            self.assertEqual(len(existing), 4)
            false_positive_existing = [
                event for event in events[:3] if (Path(event.clip_path) / "clip.mp4").exists()
            ]
            self.assertEqual([event.id for event in false_positive_existing], [events[1].id, events[2].id])
        finally:
            settings.local_clip_retention_max_total = original_total
            settings.local_clip_retention_max_false_positive = original_false_positive

    def test_event_retention_deletes_old_event_data_and_files(self):
        original_snapshots_dir = settings.event_snapshots_dir
        original_enabled = settings.event_retention_enabled
        original_days = settings.event_retention_days
        retention_root = self._review_audit_tmp / "event_snapshots"
        retention_root.mkdir(parents=True, exist_ok=True)
        settings.event_snapshots_dir = str(retention_root)
        settings.event_retention_enabled = True
        settings.event_retention_days = 7
        try:
            old_snapshot = retention_root / "camera_1" / "old.jpg"
            old_snapshot.parent.mkdir(parents=True, exist_ok=True)
            old_snapshot.write_bytes(b"jpg")
            old_clip = retention_root / "camera_1" / "clip_old"
            old_clip.mkdir(parents=True, exist_ok=True)
            (old_clip / "clip.mp4").write_bytes(b"video")
            recent_snapshot = retention_root / "camera_1" / "recent.jpg"
            recent_snapshot.write_bytes(b"jpg")

            old_event = self._make_event()
            old_event.created_at = utc_now_naive() - timedelta(days=8)
            old_event.snapshot_path = str(old_snapshot)
            old_event.clip_path = str(old_clip)
            recent_event = self._make_event()
            recent_event.created_at = utc_now_naive()
            recent_event.snapshot_path = str(recent_snapshot)
            self.db.commit()

            self.db.add(
                EventFeedback(
                    event_id=old_event.id,
                    camera_id=self.camera.id,
                    label="false_positive",
                    reviewed_by="operador",
                    reviewed_at=utc_now_naive(),
                )
            )
            self.db.add(
                LockdownDelivery(
                    event_id=old_event.id,
                    camera_id=self.camera.id,
                    event_type=old_event.event_type,
                    target_url="http://lockdown.test/event",
                )
            )
            self.db.commit()
            old_event_id = int(old_event.id)
            recent_event_id = int(recent_event.id)

            result = prune_expired_events(self.db, now=utc_now_naive(), retention_days=7)

            self.assertEqual(result["events_deleted"], 1)
            self.assertEqual(result["feedback_deleted"], 1)
            self.assertEqual(result["lockdown_deleted"], 1)
            self.assertFalse(old_snapshot.exists())
            self.assertFalse(old_clip.exists())
            self.assertIsNone(self.db.query(Event).filter(Event.id == old_event_id).first())
            self.assertIsNotNone(self.db.query(Event).filter(Event.id == recent_event_id).first())
            self.assertTrue(recent_snapshot.exists())
        finally:
            settings.event_snapshots_dir = original_snapshots_dir
            settings.event_retention_enabled = original_enabled
            settings.event_retention_days = original_days

    def test_generate_policy_suggestions_for_vegetation(self):
        for _ in range(4):
            event = self._make_event()
            record_feedback(
                self.db,
                event=event,
                label="false_positive",
                probable_cause="vegetation_wind",
                operator_note="vento",
                reviewed_by="operador",
            )
        self.db.commit()

        suggestions = generate_policy_suggestions(self.db, self.camera, days=30)
        self.db.commit()

        parameter_names = {s.parameter_name for s in suggestions}
        self.assertIn("track_persistence_frames", parameter_names)
        self.assertIn("alarm_confirmation_seconds", parameter_names)
        self.assertGreaterEqual(self.db.query(TuningSuggestion).count(), 2)

    def test_apply_tuning_suggestion_preserves_dict_roi_points(self):
        suggestion = TuningSuggestion(
            camera_id=self.camera.id,
            scope_type="camera",
            scope_id=str(self.camera.id),
            suggestion_type="policy_tuning",
            parameter_name="min_box_area_pct",
            old_value="0.0",
            suggested_value="0.002",
            reason_summary="Ajuste de area minima.",
            evidence_count=3,
            confidence_score=0.86,
            status="pending",
        )
        self.db.add(suggestion)
        self.db.commit()

        apply_tuning_suggestion(self.db, suggestion)
        self.db.commit()
        self.db.refresh(self.camera)

        profile = profile_from_camera(self.camera)
        self.assertEqual(len(profile.roi_polygon), 4)
        self.assertAlmostEqual(profile.threshold_profile.min_box_area_pct, 0.002)
        self.assertEqual(suggestion.status, "applied")

    def test_review_payload_contains_metrics_and_events(self):
        event = self._make_event()
        event.details = (
            "Pessoa detectada | revalidator_person=0.001 threshold=0.01 mode=block "
            "| far_revalidator_person=0.720 threshold=0.005 mode=audit"
        )
        record_feedback(
            self.db,
            event=event,
            label="true_positive",
            probable_cause="normal_human_flow",
            operator_note="ok",
            reviewed_by="operador",
        )
        self.db.commit()

        payload = build_event_review_payload(self.db, camera_id=self.camera.id, days=30)
        self.assertEqual(len(payload["events"]), 1)
        self.assertIn("metrics", payload)
        self.assertIn("active_learning_queue", payload)
        self.assertEqual(payload["events"][0]["feedback"]["label"], "true_positive")
        ai_summary = payload["events"][0]["ai_validation_summary"]
        self.assertIn("IA1 confirmou: pessoa", ai_summary["ia1_text"])
        self.assertIn("IA2 recusou: pessoa", ai_summary["ia2_text"])
        self.assertIn("IA3 protegeu: score pessoa acima do limiar", ai_summary["ia3_text"])
        metadata_files = list(Path(settings.revalidator_feedback_dataset_dir).glob("person/metadata/*.json"))
        self.assertEqual(len(metadata_files), 1)
        audit_files = list(Path(settings.revalidator_review_audit_dir).glob("camera_*/event_*_feedback_*.json"))
        self.assertEqual(len(audit_files), 1)

    def test_review_payload_explains_low_ia2_score_that_passed_safety_threshold(self):
        event = self._make_event()
        event.details = "Pessoa detectada | revalidator_person=0.027 threshold=0.01 mode=block"
        self.db.commit()

        payload = build_event_review_payload(self.db, camera_id=self.camera.id, days=30)

        ai_summary = payload["events"][0]["ai_validation_summary"]
        self.assertEqual(ai_summary["ia2_status"], "recusou visualmente pessoa")
        self.assertIn("não bloqueou pela política conservadora", ai_summary["ia2_text"])

    def test_review_payload_humanizes_ia2_inference_failure(self):
        event = self._make_event()
        event.details = "Pessoa detectada | revalidator_skipped=inference_failed:RuntimeError mode=block"
        self.db.commit()

        payload = build_event_review_payload(self.db, camera_id=self.camera.id, days=30)

        ai_summary = payload["events"][0]["ai_validation_summary"]
        self.assertEqual(ai_summary["ia2_status"], "indisponivel")
        self.assertIn("IA2 indisponivel: Erro de inferencia", ai_summary["ia2_text"])
        self.assertIn("predict", ai_summary["ia2_text"])

    def test_review_payload_marks_consensus_candidate_as_production_block_feedback(self):
        event = self._make_event()
        event.details = (
            "Pessoa detectada | revalidator_person=0.090 threshold=0.01 mode=block "
            "| far_revalidator_person=0.002 threshold=0.005 mode=audit "
            "| balanced_block_candidate=true reason=balanced_ia2_ia3_consensus_not_person"
        )
        self.db.commit()

        payload = build_event_review_payload(self.db, camera_id=self.camera.id, days=30)

        ai_summary = payload["events"][0]["ai_validation_summary"]
        self.assertTrue(ai_summary["production_block_candidate"])
        self.assertEqual(ai_summary["consensus_status"], "bloquearia em producao")
        self.assertIn("BLOQUEARIA EM PROD", ai_summary["consensus_text"])

    def test_review_payload_can_filter_canceled_events_and_measure_efficiency(self):
        canceled_fp = self._make_event()
        canceled_fp.status = "canceled"
        canceled_tp = self._make_event()
        canceled_tp.status = "canceled"
        active = self._make_event()
        self.db.add_all(
            [
                EventFeedback(
                    event_id=canceled_fp.id,
                    camera_id=self.camera.id,
                    label="false_positive",
                    probable_cause="small_target",
                    operator_note="cancelou corretamente",
                    reviewed_by="operador",
                    reviewed_at=utc_now_naive(),
                ),
                EventFeedback(
                    event_id=canceled_tp.id,
                    camera_id=self.camera.id,
                    label="true_positive",
                    probable_cause="normal_human_flow",
                    operator_note="perdeu pessoa",
                    reviewed_by="operador",
                    reviewed_at=utc_now_naive(),
                ),
            ]
        )
        self.db.commit()

        payload = build_event_review_payload(self.db, camera_id=self.camera.id, status="canceled", days=30)

        self.assertEqual({event["id"] for event in payload["events"]}, {canceled_fp.id, canceled_tp.id})
        self.assertNotIn(active.id, {event["id"] for event in payload["events"]})
        self.assertEqual(payload["metrics"]["canceled_events"], 2)
        self.assertEqual(payload["metrics"]["canceled_reviewed"], 2)
        self.assertEqual(payload["metrics"]["canceled_false_positive"], 1)
        self.assertEqual(payload["metrics"]["canceled_true_positive"], 1)
        self.assertEqual(payload["metrics"]["canceled_efficiency"], 0.5)


if __name__ == "__main__":
    unittest.main()
