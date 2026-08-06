"""Acoes web de lifecycle, soft delete, purge e operacoes em lote."""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.logging import get_logger, log_ignored_exception
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.camera_operation_service import (
    CAMERA_BULK_ACTIONS,
    CameraOperationError,
    apply_selected_camera_action,
    normalize_camera_bulk_ids,
    purge_camera,
    soft_delete_all_cameras,
    soft_delete_camera,
    start_all_cameras,
    start_camera_action,
    stop_all_cameras,
    stop_camera_action,
)
from app.web.infrastructure import get_scoped_db, require_web_auth


router = APIRouter()
_AUTHORIZED_ROLES = ["admin", "supervisor"]
_delete_logger = get_logger("app.web.camera_delete")
_bulk_logger = get_logger("app.web.camera_bulk_action")


def _redirect_back(request: Request, fallback: str) -> str:
    return request.headers.get("referer") or fallback


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _safe_audit(db, action: str, user: User, details: str, request: Request) -> None:
    try:
        log_audit(db, action, user, details, ip_address=_client_ip(request))
    except Exception:
        log_ignored_exception(f"audit.{action}", level=logging.WARNING)


def _raise_operation_error(exc: CameraOperationError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/cameras/{camera_id}/delete")
def delete_camera_web(
    request: Request,
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = get_scoped_db()
    try:
        _delete_logger.info(
            "Camera soft delete requested",
            extra={"action": "camera_soft_delete_requested", "camera_id": camera_id},
        )
        try:
            camera = soft_delete_camera(db, camera_id)
        except CameraOperationError as exc:
            _raise_operation_error(exc)
        _safe_audit(
            db,
            "camera_delete",
            current_user,
            f"Excluiu câmera: {camera.name} (id={camera.id})",
            request,
        )
        _delete_logger.info(
            "Camera soft deleted successfully",
            extra={
                "action": "camera_soft_deleted",
                "camera_id": camera_id,
                "deleted_at": camera.deleted_at.isoformat() if camera.deleted_at else None,
            },
        )
        return RedirectResponse(url="/cameras?message=camera_disabled", status_code=303)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        _delete_logger.error(
            f"Camera soft delete failed: {exc}",
            extra={
                "action": "camera_soft_delete_failed",
                "camera_id": camera_id,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail="Erro ao desativar câmera") from exc
    finally:
        db.close()


@router.post("/cameras/delete-all")
def delete_all_cameras_web(
    request: Request,
    bulk_delete_password: str = Form(""),
    confirm_text: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    expected_password = str(settings.camera_bulk_delete_password or "").strip()
    if not expected_password:
        return RedirectResponse(url="/cameras?error=bulk_delete_disabled", status_code=303)
    if not secrets.compare_digest(str(bulk_delete_password or "").strip(), expected_password):
        _delete_logger.warning(
            "Bulk camera delete rejected",
            extra={"action": "camera_bulk_delete_rejected", "reason": "bad_password"},
        )
        return RedirectResponse(url="/cameras?error=bulk_delete_bad_password", status_code=303)
    if str(confirm_text or "").strip().upper() != "EXCLUIR TODAS":
        _delete_logger.warning(
            "Bulk camera delete rejected",
            extra={"action": "camera_bulk_delete_rejected", "reason": "bad_confirmation"},
        )
        return RedirectResponse(url="/cameras?error=bulk_delete_bad_confirmation", status_code=303)

    db = get_scoped_db()
    try:
        result = soft_delete_all_cameras(db)
        _safe_audit(
            db,
            "camera_delete_all",
            current_user,
            f"Excluiu todas as câmeras (quantidade: {result.processed_count})",
            request,
        )
        _delete_logger.warning(
            "All active cameras soft deleted",
            extra={
                "action": "camera_bulk_soft_deleted",
                "deleted_count": result.processed_count,
                "camera_ids": result.camera_ids,
            },
        )
        return RedirectResponse(
            url=f"/cameras?message=all_cameras_deleted&count={result.processed_count}",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        _delete_logger.exception("Bulk camera delete failed", extra={"action": "camera_bulk_delete_failed"})
        raise HTTPException(status_code=500, detail="Erro ao excluir todas as cameras") from exc
    finally:
        db.close()


def _start_all(
    *,
    request: Request,
    current_user: User,
    audit_action: str,
    audit_label: str,
):
    db = get_scoped_db()
    try:
        result = start_all_cameras(db)
        _safe_audit(
            db,
            audit_action,
            current_user,
            f"{audit_label} (quantidade: {result.processed_count})",
            request,
        )
        return RedirectResponse(url=_redirect_back(request, "/cameras"), status_code=303)
    finally:
        db.close()


@router.post("/cameras/start-all")
def start_all_cameras_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    return _start_all(
        request=request,
        current_user=current_user,
        audit_action="camera_start_all",
        audit_label="Iniciou todas as câmeras",
    )


@router.post("/cameras/start-all-motion")
def start_all_cameras_motion_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    return _start_all(
        request=request,
        current_user=current_user,
        audit_action="camera_start_all_motion",
        audit_label="Iniciou todas as câmeras em modo movimento",
    )


@router.post("/cameras/stop-all")
def stop_all_cameras_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = get_scoped_db()
    try:
        result = stop_all_cameras(db)
        _safe_audit(
            db,
            "camera_stop_all",
            current_user,
            f"Parou todas as câmeras (quantidade: {result.processed_count})",
            request,
        )
        return RedirectResponse(
            url=f"/cameras?message=all_cameras_stopped&count={result.processed_count}",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        _bulk_logger.exception("Failed to stop all cameras", extra={"action": "camera_stop_all_failed"})
        raise HTTPException(status_code=500, detail="Erro ao parar todas as cameras") from exc
    finally:
        db.close()


@router.post("/cameras/bulk-action")
def bulk_camera_action_web(
    request: Request,
    action: str = Form(""),
    camera_ids: list[int] = Form([]),
    delete_confirmation: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    normalized_action = str(action or "").strip().lower()
    selected_ids = normalize_camera_bulk_ids(camera_ids)
    if normalized_action not in CAMERA_BULK_ACTIONS:
        return RedirectResponse(url="/cameras?error=bulk_action_invalid", status_code=303)
    if not selected_ids:
        return RedirectResponse(url="/cameras?error=bulk_action_no_selection", status_code=303)
    if (
        normalized_action == "delete"
        and str(delete_confirmation or "").strip().upper() != "EXCLUIR SELECIONADAS"
    ):
        return RedirectResponse(
            url="/cameras?error=bulk_action_delete_confirmation",
            status_code=303,
        )

    db = get_scoped_db()
    try:
        result = apply_selected_camera_action(db, selected_ids, normalized_action)
        action_labels = {"start": "iniciou", "stop": "parou", "delete": "excluiu"}
        _safe_audit(
            db,
            f"camera_bulk_{normalized_action}",
            current_user,
            f"{action_labels[normalized_action].capitalize()} cameras selecionadas "
            f"(quantidade: {result.processed_count}; ids: {result.camera_ids})",
            request,
        )
        _bulk_logger.info(
            "Camera bulk action completed",
            extra={
                "action": f"camera_bulk_{normalized_action}",
                "processed_count": result.processed_count,
                "camera_ids": result.camera_ids,
            },
        )
        message = {
            "start": "selected_cameras_started",
            "stop": "selected_cameras_stopped",
            "delete": "selected_cameras_deleted",
        }[normalized_action]
        return RedirectResponse(
            url=f"/cameras?message={message}&count={result.processed_count}",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        _bulk_logger.exception(
            "Camera bulk action failed",
            extra={"action": f"camera_bulk_{normalized_action}", "camera_ids": selected_ids},
        )
        raise HTTPException(status_code=500, detail="Erro ao executar acao nas cameras selecionadas") from exc
    finally:
        db.close()


@router.post("/cameras/{camera_id}/start")
def start_camera_web(
    request: Request,
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = get_scoped_db()
    try:
        result = start_camera_action(db, camera_id)
        if result.reason == "deleted":
            return RedirectResponse(url=_redirect_back(request, "/cameras"), status_code=303)
        if result.camera is not None:
            _safe_audit(
                db,
                "camera_start",
                current_user,
                f"Iniciou a câmera: {result.camera.name} (id={result.camera.id})",
                request,
            )
        return RedirectResponse(
            url=_redirect_back(request, f"/cameras/{camera_id}"),
            status_code=303,
        )
    finally:
        db.close()


@router.post("/cameras/{camera_id}/start-motion")
def start_camera_motion_web(
    request: Request,
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = get_scoped_db()
    try:
        result = start_camera_action(db, camera_id, require_rtsp=True)
        if result.reason == "deleted":
            return RedirectResponse(url=_redirect_back(request, "/cameras"), status_code=303)
        if result.reason in {"not_found", "missing_rtsp"}:
            return RedirectResponse(
                url=_redirect_back(request, f"/cameras/{camera_id}"),
                status_code=303,
            )
        _safe_audit(
            db,
            "camera_start_motion",
            current_user,
            f"Iniciou a câmera em modo movimento: {result.camera.name} (id={result.camera.id})",
            request,
        )
        return RedirectResponse(
            url=_redirect_back(request, f"/cameras/{camera_id}"),
            status_code=303,
        )
    finally:
        db.close()


@router.post("/cameras/{camera_id}/purge")
def purge_camera_web(
    request: Request,
    camera_id: int,
    confirm: str = Form(""),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    if str(confirm or "").strip().upper() != "PURGE":
        raise HTTPException(status_code=400, detail="Confirmação inválida para exclusão permanente")
    db = get_scoped_db()
    try:
        try:
            camera_name = purge_camera(db, camera_id)
        except CameraOperationError as exc:
            _raise_operation_error(exc)
        _safe_audit(
            db,
            "camera_purge",
            current_user,
            f"Excluiu permanentemente a câmera: {camera_name} (id={camera_id})",
            request,
        )
        _delete_logger.warning(
            "Camera permanently deleted",
            extra={"action": "camera_hard_deleted", "camera_id": camera_id},
        )
        return RedirectResponse(url="/cameras?message=camera_purged", status_code=303)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        _delete_logger.exception(
            "Camera hard delete failed",
            extra={"action": "camera_hard_delete_failed", "camera_id": camera_id},
        )
        raise HTTPException(status_code=500, detail="Erro ao excluir câmera permanentemente") from exc
    finally:
        db.close()


@router.post("/cameras/{camera_id}/stop")
def stop_camera_web(
    request: Request,
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = get_scoped_db()
    try:
        camera_name = stop_camera_action(db, camera_id)
        _safe_audit(
            db,
            "camera_stop",
            current_user,
            f"Parou a câmera: {camera_name}",
            request,
        )
    finally:
        db.close()
    return RedirectResponse(
        url=_redirect_back(request, f"/cameras/{camera_id}"),
        status_code=303,
    )
