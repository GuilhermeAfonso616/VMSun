from __future__ import annotations

import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.analytics_v2.events.models import AlarmEvent, EventEvidence
from app.core.timezone import utc_now_naive
from app.db.base import Base
from app.db.models import Event
from app.services.event_persistence import EventPersistenceQueue, PendingEventWrite


class EventPersistenceQueueTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        self.patches = [
            patch("app.services.event_persistence.SessionLocal", self.SessionLocal),
            patch("app.services.event_persistence.event_snapshot_store.save", return_value="snapshot.jpg"),
            patch("app.services.event_persistence.event_snapshot_store.save_clip_pair", return_value="clip"),
            patch("app.services.event_persistence.send_event_if_needed", return_value=None),
            patch("app.services.event_persistence.onedrive_client.enabled", return_value=False),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _payload(self) -> PendingEventWrite:
        event = AlarmEvent(
            event_id="evt-1",
            camera_id=1,
            rule_id="intrusion_default",
            track_id=7,
            timestamp_start=utc_now_naive(),
            timestamp_end=utc_now_naive(),
            event_score=0.82,
            priority="high",
            event_type="person_entered_roi",
            evidence=EventEvidence(
                bbox=[10.0, 20.0, 60.0, 100.0],
                zone_id="roi_1",
            ),
            explanation="Evento de teste",
            metadata={
                "profile_snapshot": {"preset_name": "perimeter_bullet"},
                "threshold_snapshot": {"alarm_confirmation_seconds": 1.5},
                "nuisance_profile_snapshot": {"vegetation_wind": True},
                "scene_profile": "perimeter_outdoor",
                "camera_family": "bullet",
                "track_quality": 0.77,
            },
        )
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        return PendingEventWrite(
            camera_id=1,
            event=event,
            snapshot_frame=frame,
            clip_before_frame=frame,
            clip_after_frame=frame,
        )

    def test_persist_inline_keeps_event_and_artifacts(self):
        queue = EventPersistenceQueue(camera_id=1, worker_mode="normal", maxsize=2)
        ok = queue.persist_inline(self._payload())
        self.assertTrue(ok)

        db = self.SessionLocal()
        try:
            rows = db.query(Event).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "persisted")
            self.assertTrue(rows[0].snapshot_path)
            self.assertTrue(rows[0].clip_path)
        finally:
            db.close()

    def test_persisted_active_alarm_is_enqueued_for_notification(self):
        queue = EventPersistenceQueue(camera_id=1, worker_mode="normal", maxsize=2)
        with patch(
            "app.services.event_persistence.enqueue_event_notifications",
            return_value=[],
        ) as enqueue:
            ok = queue.persist_inline(self._payload())

        self.assertTrue(ok)
        enqueue.assert_called_once()
        persisted_event = enqueue.call_args.args[0]
        self.assertEqual(persisted_event.status, "persisted")
        self.assertTrue(persisted_event.is_alarm_active)

    def test_queue_full_rejects_controlled(self):
        queue = EventPersistenceQueue(camera_id=1, worker_mode="normal", maxsize=1)
        first = queue.submit(self._payload())
        second = queue.submit(self._payload())

        self.assertTrue(first)
        self.assertFalse(second)
        stats = queue.stats()
        self.assertEqual(stats["dropped_or_rejected_jobs"], 1)
        queue.stop(drain=False, timeout=0.1)

    def test_background_worker_drains_queue(self):
        queue = EventPersistenceQueue(camera_id=1, worker_mode="normal", maxsize=4)
        queue.start()
        self.assertTrue(queue.submit(self._payload()))

        deadline = time.time() + 5.0
        stats = queue.stats()
        while time.time() < deadline and stats["events_persisted"] < 1:
            time.sleep(0.05)
            stats = queue.stats()

        queue.stop(drain=True, timeout=2.0)
        self.assertGreaterEqual(stats["events_persisted"], 1)
        self.assertEqual(stats["queue_size"], 0)

    def test_persist_inline_uploads_onedrive_artifacts_when_enabled(self):
        clip_dir = Path(".tmp_event_persistence_tests")
        clip_dir.mkdir(exist_ok=True)
        clip_file = clip_dir / "clip.mp4"
        clip_file.write_bytes(b"video")
        queue = EventPersistenceQueue(camera_id=1, worker_mode="normal", maxsize=2)

        with (
            patch("app.services.event_persistence.event_snapshot_store.save_clip_pair", return_value=str(clip_dir)),
            patch("app.services.event_persistence.onedrive_client.enabled", return_value=True),
            patch(
                "app.services.event_persistence.onedrive_client.upload_audit_clip",
                return_value={"item_id": "remote-1", "web_url": "https://example.test/clip"},
            ),
            patch(
                "app.services.event_persistence.onedrive_client.upload_audit_snapshot",
                return_value={"item_id": "snapshot-1", "web_url": "https://example.test/snapshot"},
            ),
            patch(
                "app.services.event_persistence.onedrive_client.upload_audit_event",
                return_value={"item_id": "event-1", "web_url": "https://example.test/event"},
            ) as upload_event,
        ):
            ok = queue.persist_inline(self._payload())

        self.assertTrue(ok)
        upload_event.assert_called_once()
        db = self.SessionLocal()
        try:
            row = db.query(Event).one()
            self.assertEqual(row.clip_remote_item_id, "remote-1")
            self.assertEqual(row.clip_remote_web_url, "https://example.test/clip")
            self.assertEqual(row.clip_remote_status, "uploaded")
            self.assertEqual(row.snapshot_remote_item_id, "snapshot-1")
            self.assertEqual(row.snapshot_remote_web_url, "https://example.test/snapshot")
            self.assertEqual(row.snapshot_remote_status, "uploaded")
            self.assertEqual(row.event_remote_item_id, "event-1")
            self.assertEqual(row.event_remote_web_url, "https://example.test/event")
            self.assertEqual(row.event_remote_status, "uploaded")
            if not clip_file.exists():
                self.assertIsNotNone(row.clip_local_deleted_at)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
