from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.services.onedrive_client import OneDriveClient


class OneDriveClientTest(unittest.TestCase):
    def setUp(self):
        self.original_token_file = settings.onedrive_token_file
        self.original_archive_enabled = settings.onedrive_clip_archive_enabled
        tmp_root = Path(settings.app_base_dir) / ".tmp_tests"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = tmp_root / f"onedrive_client_{uuid4().hex}"
        settings.onedrive_token_file = str(self.tmp_dir / "onedrive_token.json")

    def tearDown(self):
        settings.onedrive_token_file = self.original_token_file
        settings.onedrive_clip_archive_enabled = self.original_archive_enabled
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_upload_toggle_file_overrides_env_setting(self):
        settings.onedrive_clip_archive_enabled = False
        client = OneDriveClient()

        self.assertFalse(client.archive_enabled())

        status = client.set_archive_enabled(True)

        self.assertTrue(status["archive_enabled"])
        self.assertTrue(client.archive_enabled())
        self.assertTrue(client.upload_toggle_file.exists())

        settings.onedrive_clip_archive_enabled = False
        restored = OneDriveClient()
        self.assertTrue(restored.archive_enabled())


if __name__ == "__main__":
    unittest.main()
