from __future__ import annotations

import shutil
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.timezone import utc_now_naive
from app.db.base import Base
from app.db.models import Camera, Event, EventFeedback
from app.services.onedrive_reviewed_backfill_service import (
    count_reviewed_events_pending_onedrive,
    upload_reviewed_events_pending_onedrive,
)


class OneDriveReviewedBackfillServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        tmp_root = Path(settings.app_base_dir) / ".tmp_tests"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = tmp_root / f"onedrive_backfill_{uuid4().hex}"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.camera = Camera(
            name="Cam 1",
            ip="10.0.0.1",
            username="admin",
            password="secret",
            rtsp_url="rtsp://example",
            status="idle",
        )
        self.db.add(self.camera)
        self.db.commit()
        self.db.refresh(self.camera)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _event(self, *, reviewed: bool, uploaded: bool = False, with_clip: bool = True) -> Event:
        snapshot = self.tmp_dir / f"snapshot_{uuid4().hex}.jpg"
        snapshot.write_bytes(b"jpg")
        clip_dir = self.tmp_dir / f"clip_{uuid4().hex}"
        clip_dir.mkdir()
        (clip_dir / "metadata.json").write_text('{"video_file": "clip.mp4"}', encoding="utf-8")
        if with_clip:
            (clip_dir / "clip.mp4").write_bytes(b"video")

        event = Event(
            camera_id=self.camera.id,
            event_type="person_entered_roi",
            status="closed" if reviewed else "persisted",
            snapshot_path=str(snapshot),
            clip_path=str(clip_dir),
            snapshot_remote_status="uploaded" if uploaded else None,
            clip_remote_status="uploaded" if uploaded else None,
            event_remote_status="uploaded" if uploaded else None,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        if reviewed:
            self.db.add(
                EventFeedback(
                    event_id=event.id,
                    camera_id=self.camera.id,
                    label="true_positive",
                    probable_cause="normal_human_flow",
                    reviewed_by="operador",
                    reviewed_at=utc_now_naive(),
                )
            )
            self.db.commit()
        return event

    def test_uploads_only_reviewed_pending_events(self):
        pending = self._event(reviewed=True)
        self._event(reviewed=False)
        self._event(reviewed=True, uploaded=True)

        self.assertEqual(count_reviewed_events_pending_onedrive(self.db), 1)

        with (
            patch("app.services.onedrive_reviewed_backfill_service.onedrive_client.enabled", return_value=True),
            patch(
                "app.services.onedrive_reviewed_backfill_service.onedrive_client.upload_audit_snapshot",
                return_value={"item_id": "snapshot-1", "web_url": "https://example.test/snapshot"},
            ) as upload_snapshot,
            patch(
                "app.services.onedrive_reviewed_backfill_service.onedrive_client.upload_audit_clip",
                return_value={"item_id": "clip-1", "web_url": "https://example.test/clip"},
            ) as upload_clip,
            patch(
                "app.services.onedrive_reviewed_backfill_service.onedrive_client.upload_audit_event",
                return_value={"item_id": "event-1", "web_url": "https://example.test/event"},
            ) as upload_event,
        ):
            result = upload_reviewed_events_pending_onedrive(self.db)

        self.assertEqual(result["events_processed"], 1)
        self.assertEqual(result["snapshot_uploaded"], 1)
        self.assertEqual(result["clip_uploaded"], 1)
        self.assertEqual(result["event_json_uploaded"], 1)
        self.assertEqual(result["reviewed_pending_after"], 0)
        upload_snapshot.assert_called_once()
        upload_clip.assert_called_once()
        upload_event.assert_called_once()

        self.db.refresh(pending)
        self.assertEqual(pending.snapshot_remote_status, "uploaded")
        self.assertEqual(pending.clip_remote_status, "uploaded")
        self.assertEqual(pending.event_remote_status, "uploaded")

    def test_missing_clip_does_not_block_event_json_upload(self):
        pending = self._event(reviewed=True, with_clip=False)

        with (
            patch("app.services.onedrive_reviewed_backfill_service.onedrive_client.enabled", return_value=True),
            patch("app.services.onedrive_reviewed_backfill_service.onedrive_client.upload_audit_snapshot") as upload_snapshot,
            patch("app.services.onedrive_reviewed_backfill_service.onedrive_client.upload_audit_clip") as upload_clip,
            patch(
                "app.services.onedrive_reviewed_backfill_service.onedrive_client.upload_audit_event",
                return_value={"item_id": "event-1", "web_url": "https://example.test/event"},
            ) as upload_event,
        ):
            upload_snapshot.return_value = {"item_id": "snapshot-1", "web_url": "https://example.test/snapshot"}
            result = upload_reviewed_events_pending_onedrive(self.db)

        self.assertEqual(result["events_processed"], 1)
        self.assertEqual(result["missing_clip"], 1)
        self.assertEqual(result["event_json_uploaded"], 1)
        upload_clip.assert_not_called()
        upload_event.assert_called_once()
        self.db.refresh(pending)
        self.assertEqual(pending.event_remote_status, "uploaded")


if __name__ == "__main__":
    unittest.main()
