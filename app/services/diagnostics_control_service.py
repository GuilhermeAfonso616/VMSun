"""Comandos administrativos acionados pela tela de diagnosticos."""

import os
import secrets
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import Camera
from app.services.analytic_runtime_guard import update_runtime_tuning
from app.services.camera_registry import registry
from app.services.runtime_client import (
    RuntimeClientError,
    remote_runtime_enabled,
    restart_active_runtime_workers,
    set_runtime_gateway_capture_mode,
    update_runtime_tuning_controls,
)
from app.services.worker_lifecycle import (
    WorkerLifecycleError,
    WorkerStartBlocked,
    worker_lifecycle_manager,
)


logger = get_logger("app.services.diagnostics_control")


def current_gateway_capture_mode() -> str:
    return (
        "hybrid"
        if bool(settings.camera_gateway_worker_rtsp_fallback_enabled)
        else "gateway_only"
    )


def set_gateway_capture_mode(mode: str) -> str:
    del mode
    if remote_runtime_enabled():
        set_runtime_gateway_capture_mode("gateway_only")

    os.environ["CAMERA_GATEWAY_WORKER_RTSP_FALLBACK_ENABLED"] = "false"
    settings.camera_gateway_worker_rtsp_fallback_enabled = False
    return "gateway_only"


def _set_camera_legacy_status(db, camera_id: int, status: str) -> None:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        return
    camera.status = status
    db.commit()


def restart_active_camera_workers() -> int:
    if remote_runtime_enabled():
        try:
            response = restart_active_runtime_workers()
            return int(response.get("restarted_workers", 0) or 0)
        except RuntimeClientError:
            logger.exception(
                "Failed to restart remote active workers",
                extra={"action": "remote_active_workers_restart_failed"},
            )
            return 0

    workers = registry.list_workers()
    if not workers:
        return 0

    db = SessionLocal()
    try:
        camera_ids = [camera_id for camera_id, _ in workers]
        cameras = {
            camera.id: camera
            for camera in db.query(Camera)
            .filter(Camera.id.in_(camera_ids), Camera.is_deleted == False)
            .all()
        }
    finally:
        db.close()

    restarted_count = 0
    for camera_id, _worker in workers:
        camera = cameras.get(camera_id)
        rtsp_url = getattr(camera, "rtsp_url", None) if camera else None
        if not rtsp_url:
            continue
        try:
            worker_lifecycle_manager.start(
                camera,
                restart_existing=True,
                reason="web_restart_active",
            )
            restarted_count += 1
            db = SessionLocal()
            try:
                _set_camera_legacy_status(db, camera_id, "running_motion_test")
            finally:
                db.close()
        except (WorkerLifecycleError, WorkerStartBlocked):
            logger.exception(
                "Failed to restart active local worker",
                extra={
                    "action": "camera_worker_restart_failed",
                    "camera_id": camera_id,
                },
            )

    return restarted_count


def update_runtime_tuning_configuration(payload: dict[str, Any]) -> tuple[dict, dict | None]:
    snapshot = update_runtime_tuning(**payload)
    runtime_snapshot = None
    if remote_runtime_enabled():
        response = update_runtime_tuning_controls(payload)
        runtime_snapshot = response.get("runtime_tuning") if isinstance(response, dict) else None
    return snapshot, runtime_snapshot


def diagnostics_docker_control_is_enabled() -> bool:
    return bool(
        settings.docker_stack_control_enabled
        and str(settings.docker_stack_control_password or "").strip()
    )


def validate_docker_stack_request(
    action: str,
    password: str,
    confirmation: str,
) -> tuple[str, str | None]:
    normalized_action = str(action or "").strip().lower()
    if not diagnostics_docker_control_is_enabled():
        return normalized_action, "docker_control_disabled"

    expected_confirmation = {
        "stop": "PARAR DOCKER",
        "restart": "REINICIAR DOCKER",
    }.get(normalized_action)
    if not expected_confirmation:
        return normalized_action, "docker_bad_action"

    expected_password = str(settings.docker_stack_control_password or "").strip()
    if not secrets.compare_digest(str(password or "").strip(), expected_password):
        return normalized_action, "docker_bad_password"

    if str(confirmation or "").strip().upper() != expected_confirmation:
        return normalized_action, "docker_bad_confirmation"
    return normalized_action, None


def _current_compose_project_containers():
    import docker

    client = docker.from_env()
    current_container_id = str(os.environ.get("HOSTNAME") or "").strip()
    if not current_container_id:
        raise RuntimeError("container_id_unavailable")

    current = client.containers.get(current_container_id)
    project_name = str(
        current.labels.get("com.docker.compose.project") or ""
    ).strip()
    if not project_name:
        raise RuntimeError("compose_project_unavailable")

    containers = client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.project={project_name}"},
    )
    if not containers:
        raise RuntimeError("compose_project_empty")

    ordered = [container for container in containers if container.id != current.id]
    ordered.append(current)
    return client, current, project_name, ordered


def run_docker_stack_action(action: str) -> None:
    try:
        time.sleep(1.5)
        client, _current, project_name, containers = _current_compose_project_containers()
        for container in containers:
            container.reload()
            if action == "stop":
                if container.status == "running":
                    container.stop(timeout=20)
                continue
            if action == "restart":
                if container.status == "running":
                    container.restart(timeout=20)
                else:
                    container.start()
                continue
            raise RuntimeError(f"unsupported_action:{action}")

        logger.warning(
            "Docker compose project action completed action=%s project=%s",
            action,
            project_name,
            extra={
                "action": "diagnostics_docker_stack_action_completed",
                "status": "ok",
                "reason": action,
                "camera_id": "-",
                "event_id": "-",
            },
        )
        client.close()
    except Exception:
        logger.exception(
            "Docker compose project action failed action=%s",
            action,
            extra={
                "action": "diagnostics_docker_stack_action_failed",
                "status": "error",
                "reason": action,
                "camera_id": "-",
                "event_id": "-",
            },
        )


def resolve_backup_paths() -> tuple[Path, Path]:
    db_url = settings.database_url
    if db_url.startswith("sqlite:///"):
        db_path = Path(db_url.replace("sqlite:///", ""))
    else:
        db_path = Path(settings.app_base_dir) / "data" / "analytics.db"
    return db_path, Path(settings.app_base_dir) / ".env"
