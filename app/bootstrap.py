"""Inicializacao e encerramento dos recursos de processo do VMSun.

Manter estas operacoes fora da fabrica FastAPI permite construir a aplicacao
em testes sem criar schema, usuarios ou diretorios no host.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger, log_environment_summary, setup_logging
from app.db.base import Base, SessionLocal, engine
from app.services.camera_health_monitor import camera_health_monitor
from app.services.camera_registry import registry
from app.services.db_migrations import ensure_runtime_schema
from app.services.media_backbone_reconciler import media_backbone_reconciler
from app.services.preview_stream import preview_stream_manager


def normalized_app_role() -> str:
    role = str(settings.app_role or "all").strip().lower()
    if role == "runtime":
        role = "control"
    return role if role in {"all", "web", "control"} else "all"


def control_role_enabled() -> bool:
    return normalized_app_role() in {"all", "control"}


def web_role_enabled() -> bool:
    return normalized_app_role() in {"all", "web"}


def setup_runtime_environment() -> None:
    """Prepara caminhos mutaveis somente quando o processo realmente inicia."""
    base_dir = Path(settings.app_base_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(str(base_dir))
    settings.ensure_runtime_dirs()


def _initialize_security_state(logger) -> None:
    """Inicializa o setup e neutraliza credenciais padrao de versoes antigas."""
    from datetime import datetime, timezone

    from app.core.credential_crypto import PREFIX, decrypt_credential, encrypt_credential
    from app.core.security import verify_password
    from app.db.models import InstallationState, User
    from sqlalchemy import text

    db = SessionLocal()
    try:
        users = db.query(User).all()
        state = db.get(InstallationState, 1)
        if state is None:
            state = InstallationState(
                id=1,
                setup_completed=bool(users),
                setup_completed_at=datetime.now(timezone.utc) if users else None,
            )
            db.add(state)

        for user in users:
            if user.username == "admin" and verify_password("admin", user.password_hash):
                user.must_change_password = True
                logger.warning("Conta admin legada exige troca de senha no proximo acesso.")
            if user.username == "dev" and verify_password("dev123", user.password_hash):
                user.is_active = False
                user.must_change_password = True
                logger.warning("Conta dev com senha padrao foi desativada automaticamente.")
        db.commit()

        encrypted = 0
        rows = db.execute(text("SELECT id, password, rtsp_url FROM cameras")).all()
        for camera_id, password, rtsp_url in rows:
            updates = {}
            if password and not str(password).startswith(PREFIX):
                updates["password"] = encrypt_credential(str(password))
            elif password:
                decrypt_credential(str(password))
            if rtsp_url and not str(rtsp_url).startswith(PREFIX):
                updates["rtsp_url"] = encrypt_credential(str(rtsp_url))
            elif rtsp_url:
                decrypt_credential(str(rtsp_url))
            if updates:
                assignments = ", ".join(f"{column} = :{column}" for column in updates)
                db.execute(
                    text(f"UPDATE cameras SET {assignments} WHERE id = :camera_id"),  # noqa: S608
                    {**updates, "camera_id": camera_id},
                )
                encrypted += 1
        db.commit()
        if encrypted:
            logger.info("Segredos de %s camera(s) migrados para armazenamento cifrado.", encrypted)
    except Exception as exc:
        db.rollback()
        logger.exception("Falha ao inicializar estado de seguranca: %s", exc)
        raise
    finally:
        db.close()


def _start_camera_health_monitor(logger) -> None:
    retry_seconds = 2.0
    while True:
        try:
            camera_health_monitor.start()
            media_backbone_reconciler.start()
            return
        except BaseException as exc:
            logger.exception(
                "Monitor de cameras aguardara a recuperacao",
                extra={
                    "action": "health_monitor_start",
                    "status": "degraded",
                },
            )
            time.sleep(retry_seconds)
            retry_seconds = min(30.0, retry_seconds * 2.0)


def startup_application() -> None:
    """Inicializa banco e servicos do VMSun."""
    setup_runtime_environment()
    setup_logging()
    logger = get_logger("app.startup")
    logger.info("Inicializando aplicacao VMSun: %s role=%s", settings.app_name, normalized_app_role())

    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    _initialize_security_state(logger)
    log_environment_summary()

    if control_role_enabled():
        threading.Thread(
            target=_start_camera_health_monitor,
            args=(logger,),
            daemon=True,
            name="vms-camera-health-monitor-startup",
        ).start()

    logger.info("VMSun inicializado com sucesso")


def shutdown_application() -> None:
    """Encerra recursos pertencentes ao processo atual do VMSun."""
    preview_stream_manager.stop_all()
    if control_role_enabled():
        registry.stop_all()
        camera_health_monitor.stop()
        media_backbone_reconciler.stop()
