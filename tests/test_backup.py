import os
import tempfile
import unittest
from pathlib import Path
import sqlite3

from app.services.backup_service import BackupService, BackupError
from app.db.base import Base

class TestBackupAndRestore(unittest.TestCase):
    def setUp(self):
        # Create temp files for tests
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_analytics.db"
        self.env_path = Path(self.temp_dir) / ".env"
        
        # Initialize a test SQLite database
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE cameras (id INTEGER PRIMARY KEY, name TEXT)")
        cursor.execute("INSERT INTO cameras (id, name) VALUES (1, 'Cam 1')")
        conn.commit()
        conn.close()

        # Write dummy env file
        self.env_path.write_text("APP_PORT=8000\nSECRET_KEY=supersecret", encoding="utf-8")

    def tearDown(self):
        # Clean up files
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_backup_and_restore_success(self):
        password = "my_secure_backup_password"
        
        # 1. Create backup
        backup_bytes = BackupService.create_backup(self.db_path, self.env_path, password)
        self.assertGreater(len(backup_bytes), 48)
        self.assertTrue(backup_bytes.startswith(b"VMSB"))

        # 2. Modify database and env file so we can verify restoration
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("INSERT INTO cameras (id, name) VALUES (2, 'Cam 2')")
        conn.commit()
        conn.close()
        self.env_path.write_text("APP_PORT=9000\nSECRET_KEY=changed", encoding="utf-8")

        # 3. Restore backup
        BackupService.restore_backup(backup_bytes, self.db_path, self.env_path, password)

        # 4. Verify DB was restored (should have only 1 row)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM cameras")
        rows = cursor.fetchall()
        conn.close()
        
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[0][1], 'Cam 1')

        # 5. Verify env was restored
        env_content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("APP_PORT=8000", env_content)
        self.assertIn("SECRET_KEY=supersecret", env_content)

    def test_backup_restore_invalid_password(self):
        password = "correct_password"
        backup_bytes = BackupService.create_backup(self.db_path, self.env_path, password)

        with self.assertRaises(BackupError) as ctx:
            BackupService.restore_backup(backup_bytes, self.db_path, self.env_path, "wrong_password")
        
        self.assertIn("Senha incorreta", str(ctx.exception))

    def test_backup_restore_corrupted_payload(self):
        password = "correct_password"
        backup_bytes = bytearray(BackupService.create_backup(self.db_path, self.env_path, password))
        
        # Corrupt one byte in the ciphertext part
        backup_bytes[40] = backup_bytes[40] ^ 0xFF
        
        with self.assertRaises(BackupError) as ctx:
            BackupService.restore_backup(bytes(backup_bytes), self.db_path, self.env_path, password)
        
        self.assertIn("Senha incorreta ou arquivo de backup corrompido", str(ctx.exception))

    def test_backup_preserves_camera_credential_key(self):
        key_path = Path(self.temp_dir) / "runtime_state" / "credential_encryption_key"
        key_path.parent.mkdir(parents=True)
        key_path.write_text("original-key", encoding="utf-8")
        backup_bytes = BackupService.create_backup(
            self.db_path, self.env_path, "backup-password", key_path
        )

        key_path.write_text("changed-key", encoding="utf-8")
        BackupService.restore_backup(
            backup_bytes, self.db_path, self.env_path, "backup-password", key_path
        )

        self.assertEqual(key_path.read_text(encoding="utf-8"), "original-key")

if __name__ == "__main__":
    unittest.main()
