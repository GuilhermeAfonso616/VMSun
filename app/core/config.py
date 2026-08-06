"""Centraliza as configuracoes de runtime lidas do .env e os caminhos do projeto.

Este modulo e o ponto unico para parametros do VMSun: cameras, NVRs, MediaMTX,
diretorios de logs e ajustes de runtime.
"""

import os
import secrets
import sys
import time
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def get_runtime_base_dir() -> Path:
    """Retorna a pasta base do VMSun."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


BASE_DIR = get_runtime_base_dir()


def sqlite_url_for(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _auth_secret_needs_generation(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {
        "",
        "auto",
        "generate",
        "troque_este_secret_em_producao",
        "vmsun_default_secret_key_change_me_in_production",
    }


def _load_or_create_secret_file(key_path: Path) -> str:
    """Cria a chave uma unica vez de forma concorrente-safe."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        if key_path.exists():
            existing = key_path.read_text(encoding="utf-8").strip()
            if not _auth_secret_needs_generation(existing):
                try:
                    key_path.chmod(0o600)
                except OSError:
                    pass
                return existing
            time.sleep(0.05)
            continue

        generated = secrets.token_urlsafe(64)
        try:
            descriptor = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            time.sleep(0.05)
            continue
        try:
            os.write(descriptor, (generated + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return generated
    raise RuntimeError(f"Arquivo de chave invalido ou indisponivel: {key_path}")


class Settings(BaseSettings):
    app_name: str = "VMSun"
    product_mode: str = "vms"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_role: str = "all"

    app_base_dir: str = str(BASE_DIR)
    database_url: str = sqlite_url_for(BASE_DIR / "data" / "vmsun.db")
    camera_bulk_delete_password: str = "EXCLUIR_TODAS"
    docker_stack_control_enabled: bool = False
    docker_stack_control_password: str = ""

    # RTSP & Conectividade
    rtsp_transport: str = "tcp"
    rtsp_open_timeout_ms: int = 8000
    rtsp_read_timeout_ms: int = 8000
    rtsp_max_delay_us: int = 500000
    rtsp_enable_low_latency: bool = True
    rtsp_ffmpeg_log_level: str = "error"

    frame_store_prefer_shm: bool = True
    frame_store_shm_buffer_size_mb: int = 8

    reconnect_seconds: int = 8
    reconnect_initial_delay_seconds: float = 0.5
    reconnect_backoff_multiplier: float = 1.8
    reconnect_max_backoff_seconds: float = 8.0
    reconnect_max_attempts: int = 5

    # Camera Gateway (Go)
    camera_gateway_base_url: str = "http://vms-camera-gateway:8090"
    camera_gateway_public_base_url: str = ""
    camera_gateway_enabled: bool = True
    camera_gateway_recovery_enabled: bool = True
    camera_gateway_circuit_park_after_seconds: float = 120.0

    # Gateways de Fabricantes
    dahua_sdk_gateway_base_url: str = ""
    intelbras_sdk_gateway_base_url: str = ""
    hikvision_sdk_gateway_base_url: str = ""

    # MediaMTX & WebRTC Backbone
    webrtc_gateway_enabled: bool = True
    webrtc_gateway_api_base_url: str = "http://vms-mediamtx:9997"
    webrtc_gateway_public_base_url: str = ""
    webrtc_gateway_rtsp_base_url: str = "rtsp://vms-mediamtx:8554"
    webrtc_gateway_rtsp_public_base_url: str = ""
    webrtc_gateway_monitor_enabled: bool = True
    media_backbone_reconcile_enabled: bool = True
    media_backbone_reconcile_interval_seconds: float = 30.0
    media_backbone_remove_orphan_paths: bool = True

    # Health Check & Disponibilidade
    camera_health_check_interval_seconds: int = 15
    watchdog_poll_interval_seconds: int = 15
    camera_health_offline_after_seconds: int = 45
    camera_health_degraded_after_seconds: float = 15.0
    camera_health_startup_grace_seconds: int = 75
    camera_health_probe_interval_seconds: int = 30
    camera_health_probe_timeout_seconds: float = 2.5

    # Autenticacao & Seguranca
    auth_secret_key: str = "auto"
    auth_secret_key_file: str = ""
    credential_encryption_key: str = "auto"
    credential_encryption_key_file: str = ""
    session_ttl_seconds: int = 604800
    session_cookie_secure: bool | None = None
    password_min_length: int = 12
    api_auth_required: bool = True

    # Historicos e Logs
    operational_history_retention_days: int = 365
    resource_history_retention_days: int = 365
    runtime_state_dir: str = str(BASE_DIR / "runtime_state")
    storage_monitor_disk_path: str = ""
    logs_dir: str = str(BASE_DIR / "logs")
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5

    runtime_api_base_url: str = ""
    runtime_api_timeout_seconds: float = 3.0

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:  # noqa: ANN001
        self.auth_secret_key = self._resolved_auth_secret_key()
        self.credential_encryption_key = self._resolved_credential_encryption_key()

    def _resolved_auth_secret_key(self) -> str:
        configured = str(self.auth_secret_key or "").strip()
        if not _auth_secret_needs_generation(configured):
            return configured

        key_path = Path(self.auth_secret_key_file or Path(self.runtime_state_dir) / "auth_secret_key")
        return _load_or_create_secret_file(key_path)

    def _resolved_credential_encryption_key(self) -> str:
        configured = str(self.credential_encryption_key or "").strip()
        if not _auth_secret_needs_generation(configured):
            return configured

        key_path = Path(
            self.credential_encryption_key_file
            or Path(self.runtime_state_dir) / "credential_encryption_key"
        )
        self.credential_encryption_key_file = str(key_path)
        return _load_or_create_secret_file(key_path)

    def ensure_runtime_dirs(self) -> None:
        dirs = [
            Path(self.app_base_dir),
            Path(self.runtime_state_dir),
            Path(self.auth_secret_key_file).parent if self.auth_secret_key_file else Path(self.runtime_state_dir),
            Path(self.credential_encryption_key_file).parent,
            Path(self.logs_dir),
        ]

        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)

        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
