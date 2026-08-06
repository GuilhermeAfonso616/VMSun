from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.config import settings
from app.services.storage_usage import compute_storage_report


class StorageUsageTest(unittest.TestCase):
    def setUp(self):
        self._original = {
            "app_base_dir": settings.app_base_dir,
            "database_url": settings.database_url,
            "event_snapshots_dir": settings.event_snapshots_dir,
            "runtime_state_dir": settings.runtime_state_dir,
            "logs_dir": settings.logs_dir,
            "debug_frames_dir": settings.debug_frames_dir,
            "revalidator_feedback_dataset_dir": settings.revalidator_feedback_dataset_dir,
            "detector_model_path": settings.detector_model_path,
            "storage_monitor_disk_path": settings.storage_monitor_disk_path,
        }

    def tearDown(self):
        for key, value in self._original.items():
            setattr(settings, key, value)
        compute_storage_report(force=True)

    def test_database_category_counts_sqlite_files_not_whole_data_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "data"
            events_dir = data_dir / "event_snapshots"
            runtime_dir = data_dir / "runtime_state"
            logs_dir = data_dir / "logs"
            debug_dir = data_dir / "debug_frames"
            datasets_dir = root / "datasets" / "revalidator_feedback"
            models_dir = root / "models" / "ia1_candidate"
            for path in [events_dir, runtime_dir, logs_dir, debug_dir, datasets_dir, models_dir]:
                path.mkdir(parents=True, exist_ok=True)

            db_path = data_dir / "analytics.db"
            db_path.write_bytes(b"database")
            (events_dir / "clip.bin").write_bytes(b"x" * 100)

            settings.app_base_dir = str(root)
            settings.database_url = f"sqlite:///{db_path.as_posix()}"
            settings.event_snapshots_dir = str(events_dir)
            settings.runtime_state_dir = str(runtime_dir)
            settings.logs_dir = str(logs_dir)
            settings.debug_frames_dir = str(debug_dir)
            settings.revalidator_feedback_dataset_dir = str(datasets_dir)
            settings.detector_model_path = str(models_dir / "model.pt")
            settings.storage_monitor_disk_path = ""

            report = compute_storage_report(force=True)
            categories = {item["key"]: item for item in report["categories"]}

        self.assertEqual(categories["database"]["size_bytes"], len(b"database"))
        self.assertEqual(categories["event_media"]["size_bytes"], 100)
        self.assertEqual(Path(report["disk"]["path"]), data_dir)

    def test_storage_monitor_disk_path_override_wins(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "data"
            override_dir = root / "other_disk"
            events_dir = data_dir / "event_snapshots"
            runtime_dir = data_dir / "runtime_state"
            logs_dir = data_dir / "logs"
            debug_dir = data_dir / "debug_frames"
            datasets_dir = root / "datasets" / "revalidator_feedback"
            models_dir = root / "models" / "ia1_candidate"
            for path in [override_dir, events_dir, runtime_dir, logs_dir, debug_dir, datasets_dir, models_dir]:
                path.mkdir(parents=True, exist_ok=True)

            db_path = data_dir / "analytics.db"
            db_path.write_bytes(b"database")

            settings.app_base_dir = str(root)
            settings.database_url = f"sqlite:///{db_path.as_posix()}"
            settings.event_snapshots_dir = str(events_dir)
            settings.runtime_state_dir = str(runtime_dir)
            settings.logs_dir = str(logs_dir)
            settings.debug_frames_dir = str(debug_dir)
            settings.revalidator_feedback_dataset_dir = str(datasets_dir)
            settings.detector_model_path = str(models_dir / "model.pt")
            settings.storage_monitor_disk_path = str(override_dir)

            report = compute_storage_report(force=True)

        self.assertEqual(Path(report["disk"]["path"]), override_dir)


if __name__ == "__main__":
    unittest.main()
