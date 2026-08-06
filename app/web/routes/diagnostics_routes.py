"""Rotas da central de diagnosticos e comandos administrativos."""

from datetime import datetime
from pathlib import Path
from threading import Thread

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import User
from app.services.backup_service import BackupError, BackupService
from app.services.diagnostics_control_service import (
    diagnostics_docker_control_is_enabled,
    resolve_backup_paths,
    restart_active_camera_workers,
    run_docker_stack_action,
    set_gateway_capture_mode,
    update_runtime_tuning_configuration,
    validate_docker_stack_request,
)
from app.services.onedrive_client import onedrive_client
from app.services.onedrive_reviewed_backfill_service import (
    count_reviewed_events_pending_onedrive,
    upload_reviewed_events_pending_onedrive,
)
from app.services.runtime_client import RuntimeClientError
from app.web.diagnostics_presenter import (
    build_diagnostics_payload,
    build_diagnostics_shell_payload,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates
from app.web.monitor_presenter import _cached_monitor_json_response


router = APIRouter()
logger = get_logger("app.web.diagnostics")


def _diagnostics_log_extra(action: str, status: str, reason: str, **extra):
    return {
        "action": action,
        "status": status,
        "reason": reason,
        "camera_id": "-",
        "event_id": "-",
        **extra,
    }


@router.get("/diagnostics")
@router.get("/monitor/diagnostics")
def diagnostics_page(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    payload = build_diagnostics_shell_payload()
    pending_reviewed_events = 0
    try:
        db = SessionLocal()
        try:
            pending_reviewed_events = count_reviewed_events_pending_onedrive(db)
        finally:
            db.close()
    except Exception:
        pending_reviewed_events = 0

    logger.info(
        "Diagnostics page accessed",
        extra=_diagnostics_log_extra("diagnostics_view", "ok", "page_load"),
    )
    return templates.TemplateResponse(
        request=request,
        name="diagnostics.html",
        context={
            "request": request,
            "diagnostics": payload,
            "summary": payload["summary"],
            "cameras": payload["cameras"],
            "runtime_tuning": payload.get("runtime_tuning", {}),
            "detector_engine": payload.get("detector_engine", {}),
            "docker_stack_control_enabled": diagnostics_docker_control_is_enabled(),
            "onedrive_status": onedrive_client.status(refresh_if_needed=True),
            "onedrive_pending_reviewed_events": pending_reviewed_events,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/diagnostics/onedrive-token")
@router.post("/monitor/diagnostics/onedrive-token")
def diagnostics_onedrive_token(onedrive_token: str = Form("")):
    try:
        onedrive_client.save_token_text(onedrive_token)
        onedrive_client.status(refresh_if_needed=True)
    except Exception:
        logger.exception(
            "OneDrive token save failed",
            extra=_diagnostics_log_extra(
                "diagnostics_onedrive_token",
                "error",
                "save_failed",
            ),
        )
        return RedirectResponse(
            url="/diagnostics?error=onedrive_token_failed",
            status_code=303,
        )

    logger.info(
        "OneDrive token saved from diagnostics",
        extra=_diagnostics_log_extra(
            "diagnostics_onedrive_token",
            "ok",
            "saved",
        ),
    )
    return RedirectResponse(
        url="/diagnostics?message=onedrive_token_saved",
        status_code=303,
    )


@router.post("/diagnostics/onedrive-upload-toggle")
@router.post("/monitor/diagnostics/onedrive-upload-toggle")
def diagnostics_onedrive_upload_toggle(upload_enabled: str = Form("")):
    enabled = str(upload_enabled or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "ativo",
        "ativar",
    }
    try:
        onedrive_client.set_archive_enabled(enabled)
    except Exception:
        logger.exception(
            "OneDrive upload toggle failed",
            extra=_diagnostics_log_extra(
                "diagnostics_onedrive_upload_toggle",
                "error",
                "toggle_failed",
            ),
        )
        return RedirectResponse(
            url="/diagnostics?error=onedrive_upload_toggle_failed",
            status_code=303,
        )

    logger.info(
        "OneDrive upload toggle changed",
        extra=_diagnostics_log_extra(
            "diagnostics_onedrive_upload_toggle",
            "ok",
            "enabled" if enabled else "disabled",
        ),
    )
    message = "onedrive_upload_enabled" if enabled else "onedrive_upload_disabled"
    return RedirectResponse(url=f"/diagnostics?message={message}", status_code=303)


@router.post("/diagnostics/onedrive-token-file")
@router.post("/monitor/diagnostics/onedrive-token-file")
async def diagnostics_onedrive_token_file(
    onedrive_token_file: UploadFile = File(...),
):
    try:
        raw = await onedrive_token_file.read()
        token_text = raw.decode("utf-8-sig").strip()
        onedrive_client.save_token_text(token_text)
        onedrive_client.status(refresh_if_needed=True)
    except Exception:
        logger.exception(
            "OneDrive token file save failed",
            extra=_diagnostics_log_extra(
                "diagnostics_onedrive_token_file",
                "error",
                "save_failed",
            ),
        )
        return RedirectResponse(
            url="/diagnostics?error=onedrive_token_file_failed",
            status_code=303,
        )

    logger.info(
        "OneDrive token saved from uploaded file",
        extra=_diagnostics_log_extra(
            "diagnostics_onedrive_token_file",
            "ok",
            "saved",
        ),
    )
    return RedirectResponse(
        url="/diagnostics?message=onedrive_token_saved",
        status_code=303,
    )


@router.post("/diagnostics/onedrive-reviewed-events/upload")
@router.post("/monitor/diagnostics/onedrive-reviewed-events/upload")
def diagnostics_onedrive_reviewed_events_upload(limit: int = Form(200)):
    db = SessionLocal()
    try:
        result = upload_reviewed_events_pending_onedrive(db, limit=limit)
    except Exception:
        logger.exception(
            "OneDrive reviewed events upload failed",
            extra=_diagnostics_log_extra(
                "diagnostics_onedrive_reviewed_events_upload",
                "error",
                "upload_failed",
            ),
        )
        return RedirectResponse(
            url="/diagnostics?error=onedrive_reviewed_upload_failed",
            status_code=303,
        )
    finally:
        db.close()

    logger.info(
        "OneDrive reviewed events upload completed",
        extra=_diagnostics_log_extra(
            "diagnostics_onedrive_reviewed_events_upload",
            "ok",
            "upload_completed",
            events_processed=result.get("events_processed"),
            failed=result.get("failed"),
        ),
    )
    message = (
        "onedrive_reviewed_upload_partial"
        if int(result.get("failed") or 0)
        else "onedrive_reviewed_upload_done"
    )
    return RedirectResponse(url=f"/diagnostics?message={message}", status_code=303)


@router.get("/diagnostics/data")
@router.get("/monitor/diagnostics/data")
def diagnostics_data(include_logs: bool = False, include_gateway: bool = False):
    def build_response_payload():
        db = get_scoped_db()
        try:
            return build_diagnostics_payload(
                db,
                include_logs=include_logs,
                include_gateway=include_gateway,
            )
        finally:
            db.close()

    payload_key = ("diagnostics_data", bool(include_logs), bool(include_gateway))

    def build_logged_payload():
        payload = build_response_payload()
        logger.info(
            "Diagnostics data requested",
            extra=_diagnostics_log_extra(
                "diagnostics_data",
                "ok",
                "json_load",
            ),
        )
        return payload

    return _cached_monitor_json_response(
        payload_key,
        build_logged_payload,
        ttl_seconds=settings.monitor_diagnostics_cache_ttl_seconds,
    )


@router.post("/diagnostics/gateway-mode")
@router.post("/monitor/diagnostics/gateway-mode")
def diagnostics_gateway_mode(mode: str = Form(...)):
    selected = set_gateway_capture_mode(mode)
    restarted_count = restart_active_camera_workers()
    logger.warning(
        "Gateway capture mode changed mode=%s restarted_workers=%s",
        selected,
        restarted_count,
        extra=_diagnostics_log_extra(
            "diagnostics_gateway_mode",
            "ok",
            selected,
        ),
    )
    return JSONResponse(
        {
            "ok": True,
            "mode": selected,
            "label": "Hibrido" if selected == "hybrid" else "So Gateway",
            "rtsp_fallback_enabled": bool(
                settings.camera_gateway_worker_rtsp_fallback_enabled
            ),
            "restarted_workers": restarted_count,
        }
    )


@router.post("/diagnostics/runtime-tuning")
@router.post("/monitor/diagnostics/runtime-tuning")
def diagnostics_runtime_tuning(
    gpu_guard_enabled: str | None = Form(None),
    max_gpu_memory_mb: int = Form(5000),
    max_active_workers: int = Form(12),
    detector_fp16_enabled: str | None = Form(None),
    inference_pool_enabled: str | None = Form(None),
    inference_pool_max_queue_size: int = Form(16),
    inference_pool_job_timeout_seconds: float = Form(2.0),
    inference_pool_max_job_age_seconds: float = Form(1.0),
    inference_pool_overflow_policy: str = Form("drop_oldest"),
    inference_pool_backend: str = Form("local"),
    inference_pool_count: int = Form(1),
    inference_pool_max_cameras_per_pool: int = Form(8),
    inference_pool_central_jpeg_quality: int = Form(80),
    inference_pool_central_fallback_direct: str | None = Form(None),
):
    payload = {
        "gpu_guard_enabled": gpu_guard_enabled is not None,
        "max_gpu_memory_mb": max_gpu_memory_mb,
        "max_active_workers": max_active_workers,
        "detector_fp16_enabled": detector_fp16_enabled is not None,
        "inference_pool_enabled": inference_pool_enabled is not None,
        "inference_pool_max_queue_size": inference_pool_max_queue_size,
        "inference_pool_job_timeout_seconds": inference_pool_job_timeout_seconds,
        "inference_pool_max_job_age_seconds": inference_pool_max_job_age_seconds,
        "inference_pool_overflow_policy": inference_pool_overflow_policy,
        "inference_pool_backend": inference_pool_backend,
        "inference_pool_count": inference_pool_count,
        "inference_pool_max_cameras_per_pool": inference_pool_max_cameras_per_pool,
        "inference_pool_central_jpeg_quality": inference_pool_central_jpeg_quality,
        "inference_pool_central_fallback_direct": (
            inference_pool_central_fallback_direct is not None
        ),
    }
    try:
        snapshot, runtime_snapshot = update_runtime_tuning_configuration(payload)
    except RuntimeClientError:
        logger.exception(
            "Runtime tuning update failed",
            extra=_diagnostics_log_extra(
                "diagnostics_runtime_tuning",
                "error",
                "runtime_update_failed",
            ),
        )
        return RedirectResponse(
            url="/diagnostics?error=runtime_tuning_failed",
            status_code=303,
        )

    logger.warning(
        "Runtime tuning updated",
        extra=_diagnostics_log_extra(
            "diagnostics_runtime_tuning",
            "ok",
            "updated",
            runtime_tuning=runtime_snapshot or snapshot,
        ),
    )
    return RedirectResponse(
        url="/diagnostics?message=runtime_tuning_saved",
        status_code=303,
    )


@router.post("/diagnostics/docker-stack")
@router.post("/monitor/diagnostics/docker-stack")
def diagnostics_docker_stack_action(
    action: str = Form(""),
    docker_control_password: str = Form(""),
    confirm_text: str = Form(""),
):
    normalized_action, error = validate_docker_stack_request(
        action,
        docker_control_password,
        confirm_text,
    )
    if error:
        reason = error.removeprefix("docker_")
        logger.warning(
            "Docker stack action rejected reason=%s",
            reason,
            extra=_diagnostics_log_extra(
                "diagnostics_docker_stack_action_rejected",
                "rejected",
                reason,
            ),
        )
        return RedirectResponse(
            url=f"/diagnostics?error={error}",
            status_code=303,
        )

    logger.critical(
        "Docker stack action accepted from diagnostics page action=%s",
        normalized_action,
        extra=_diagnostics_log_extra(
            "diagnostics_docker_stack_action_requested",
            "accepted",
            normalized_action,
        ),
    )
    Thread(
        target=run_docker_stack_action,
        args=(normalized_action,),
        name=f"diagnostics-docker-{normalized_action}",
        daemon=True,
    ).start()
    return RedirectResponse(
        url=f"/diagnostics?message=docker_{normalized_action}_requested",
        status_code=303,
    )


@router.post("/diagnostics/backup/export")
@router.post("/monitor/diagnostics/backup/export")
def web_export_backup(
    password: str = Form(""),
    current_user: User = Depends(require_web_auth(["admin"])),
):
    try:
        db_path, env_path = resolve_backup_paths()
        credential_key_path = Path(
            settings.credential_encryption_key_file
            or Path(settings.runtime_state_dir) / "credential_encryption_key"
        )
        backup_bytes = BackupService.create_backup(
            db_path, env_path, password, credential_key_path
        )
        return Response(
            content=backup_bytes,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": (
                    "attachment; filename="
                    f"vms_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.enc"
                )
            },
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/diagnostics?error=backup_export_failed&msg={exc}",
            status_code=303,
        )


@router.post("/diagnostics/backup/import")
@router.post("/monitor/diagnostics/backup/import")
def web_import_backup(
    file: UploadFile = File(...),
    password: str = Form(...),
    current_user: User = Depends(require_web_auth(["admin"])),
):
    try:
        db_path, env_path = resolve_backup_paths()
        BackupService.restore_backup(
            file.file.read(),
            db_path,
            env_path,
            password,
            Path(
                settings.credential_encryption_key_file
                or Path(settings.runtime_state_dir) / "credential_encryption_key"
            ),
        )
        return RedirectResponse(
            url="/diagnostics?message=backup_restored",
            status_code=303,
        )
    except BackupError as exc:
        return RedirectResponse(
            url=f"/diagnostics?error=backup_restore_failed&msg={exc}",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/diagnostics?error=backup_restore_unexpected&msg={exc}",
            status_code=303,
        )
