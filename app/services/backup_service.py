import io
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

SIGNATURE = b"VMSB"

class BackupError(Exception):
    pass

class BackupService:
    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        return kdf.derive(password.encode("utf-8"))

    @classmethod
    def create_backup(
        cls,
        db_path: Path,
        env_path: Path,
        password: str,
        credential_key_path: Path | None = None,
    ) -> bytes:
        """
        Creates a consistent backup of the SQLite database and environment file,
        compressing them into a ZIP archive, and encrypting it using AES-256-GCM.
        """
        if not db_path.exists():
            raise BackupError(f"Banco de dados não encontrado em {db_path}")

        # 1. Create a safe SQLite snapshot
        temp_dir = tempfile.mkdtemp()
        temp_db_path = Path(temp_dir) / "analytics_snapshot.db"
        try:
            src_conn = sqlite3.connect(str(db_path))
            dest_conn = sqlite3.connect(str(temp_db_path))
            with dest_conn:
                src_conn.backup(dest_conn)
            dest_conn.close()
            src_conn.close()
        except Exception as exc:
            if os.path.exists(temp_dir):
                shutil = __import__("shutil")
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise BackupError(f"Falha ao criar snapshot do banco de dados: {exc}")

        # 2. Compress files into a ZIP archive in memory
        zip_buffer = io.BytesIO()
        try:
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                # Add database snapshot as 'analytics.db'
                zip_file.write(temp_db_path, "analytics.db")
                # Add environment file if it exists
                if env_path.exists():
                    zip_file.write(env_path, ".env")
                if credential_key_path and credential_key_path.exists():
                    zip_file.write(credential_key_path, "runtime_secrets/credential_encryption_key")
        except Exception as exc:
            raise BackupError(f"Falha ao gerar arquivo ZIP de backup: {exc}")
        finally:
            if os.path.exists(temp_dir):
                shutil = __import__("shutil")
                shutil.rmtree(temp_dir, ignore_errors=True)

        zip_payload = zip_buffer.getvalue()

        # 3. Encrypt payload using AES-256-GCM
        try:
            salt = os.urandom(16)
            key = cls._derive_key(password, salt)
            
            aesgcm = AESGCM(key)
            iv = os.urandom(12)
            encrypted_payload = aesgcm.encrypt(iv, zip_payload, SIGNATURE)
        except Exception as exc:
            raise BackupError(f"Erro ao criptografar o backup: {exc}")

        # Format: SIGNATURE (4b) + SALT (16b) + IV (12b) + Encrypted Payload (includes GCM tag at the end)
        return SIGNATURE + salt + iv + encrypted_payload

    @classmethod
    def restore_backup(
        cls,
        backup_bytes: bytes,
        db_path: Path,
        env_path: Path,
        password: str,
        credential_key_path: Path | None = None,
    ) -> None:
        """
        Decrypts, verifies, and restores the database and env files from an encrypted backup.
        """
        if len(backup_bytes) < 48:  # 4 (sig) + 16 (salt) + 12 (iv) + min payload
            raise BackupError("Arquivo de backup inválido ou truncado.")

        # 1. Parse backup file components
        signature = backup_bytes[:4]
        if signature != SIGNATURE:
            raise BackupError("Assinatura de backup inválida. O arquivo está corrompido ou não é um backup válido.")

        salt = backup_bytes[4:20]
        iv = backup_bytes[20:32]
        encrypted_payload = backup_bytes[32:]

        # 2. Decrypt payload
        try:
            key = cls._derive_key(password, salt)
            aesgcm = AESGCM(key)
            zip_payload = aesgcm.decrypt(iv, encrypted_payload, SIGNATURE)
        except Exception:
            raise BackupError("Senha incorreta ou arquivo de backup corrompido (falha na autenticação).")

        # 3. Extract and overwrite target files
        try:
            zip_buffer = io.BytesIO(zip_payload)
            with zipfile.ZipFile(zip_buffer, "r") as zip_file:
                # Validate files are present in the zip
                names = zip_file.namelist()
                if "analytics.db" not in names:
                    raise BackupError("Arquivo de banco de dados 'analytics.db' ausente no backup.")

                # Extract to a temp directory first to ensure atomic/safe overwriting
                temp_dir = tempfile.mkdtemp()
                temp_db_path = Path(temp_dir) / "analytics.db"
                temp_env_path = Path(temp_dir) / ".env"

                zip_file.extract("analytics.db", temp_dir)
                if ".env" in names:
                    zip_file.extract(".env", temp_dir)
                key_archive_name = "runtime_secrets/credential_encryption_key"
                if key_archive_name in names:
                    zip_file.extract(key_archive_name, temp_dir)

                # Ensure destination directories exist
                db_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.parent.mkdir(parents=True, exist_ok=True)

                # Copy over database
                wal_path = Path(str(db_path) + "-wal")
                shm_path = Path(str(db_path) + "-shm")
                if wal_path.exists():
                    try:
                        wal_path.unlink()
                    except Exception:
                        pass
                if shm_path.exists():
                    try:
                        shm_path.unlink()
                    except Exception:
                        pass

                # Safe overwrite
                import shutil
                shutil.copy2(temp_db_path, db_path)

                if ".env" in names:
                    shutil.copy2(temp_env_path, env_path)
                if credential_key_path and key_archive_name in names:
                    credential_key_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(Path(temp_dir) / key_archive_name, credential_key_path)

                shutil.rmtree(temp_dir, ignore_errors=True)

        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(f"Falha ao extrair e restaurar os arquivos: {exc}")
