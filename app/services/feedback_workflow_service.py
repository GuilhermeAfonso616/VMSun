"""Transacoes de sugestoes, politica de aprendizado e rollback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.timezone import format_brazil_datetime
from app.db.models import Camera, ConfigVersionHistory, TuningSuggestion
from app.services.feedback_constants import (
    AUTO_TUNABLE_PARAMETERS,
    LEARNING_MODES,
)
from app.services.feedback_tuning_service import (
    apply_tuning_suggestion,
    rollback_camera_config,
)


logger = get_logger("app.feedback_workflow")


@dataclass(frozen=True)
class FeedbackWorkflowError(Exception):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def list_suggestion_payloads(
    db: Session,
    *,
    camera_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = db.query(TuningSuggestion)
    if camera_id is not None:
        query = query.filter(TuningSuggestion.camera_id == camera_id)
    if status:
        query = query.filter(TuningSuggestion.status == status)
    suggestions = query.order_by(TuningSuggestion.id.desc()).limit(200).all()
    return [
        {
            "id": suggestion.id,
            "camera_id": suggestion.camera_id,
            "scope_type": suggestion.scope_type,
            "scope_id": suggestion.scope_id,
            "suggestion_type": suggestion.suggestion_type,
            "parameter_name": suggestion.parameter_name,
            "old_value": suggestion.old_value,
            "suggested_value": suggestion.suggested_value,
            "reason_summary": suggestion.reason_summary,
            "evidence_count": suggestion.evidence_count,
            "confidence_score": suggestion.confidence_score,
            "status": suggestion.status,
            "created_at": format_brazil_datetime(suggestion.created_at),
            "applied_at": format_brazil_datetime(suggestion.applied_at) if suggestion.applied_at else None,
        }
        for suggestion in suggestions
    ]


def apply_suggestion(db: Session, suggestion_id: int) -> Camera:
    suggestion = db.query(TuningSuggestion).filter(TuningSuggestion.id == suggestion_id).first()
    if suggestion is None:
        raise FeedbackWorkflowError(404, "Sugestão não encontrada")
    if suggestion.parameter_name not in AUTO_TUNABLE_PARAMETERS:
        raise FeedbackWorkflowError(400, "Sugestão requer ajuste manual na página da câmera")
    camera_record = db.query(Camera).filter(Camera.id == suggestion.camera_id).first()
    if camera_record is None or bool(getattr(camera_record, "is_deleted", False)):
        raise FeedbackWorkflowError(400, "Sugestão vinculada a câmera inexistente ou removida")

    try:
        camera = apply_tuning_suggestion(db, suggestion, change_source="suggestion")
        if camera is None:
            db.rollback()
            raise FeedbackWorkflowError(400, "Sugestão não aplicável ao perfil atual da câmera")
        db.commit()
        return camera
    except FeedbackWorkflowError:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Failed to apply tuning suggestion suggestion_id=%s camera_id=%s parameter=%s",
            suggestion_id,
            suggestion.camera_id,
            suggestion.parameter_name,
            extra={
                "camera_id": suggestion.camera_id,
                "action": "feedback_apply_suggestion",
                "status": "error",
                "reason": "apply_failed",
            },
        )
        raise FeedbackWorkflowError(
            500,
            f"Falha ao aplicar {suggestion.parameter_name}: {exc.__class__.__name__}: {exc}",
        ) from exc


def reject_suggestion(db: Session, suggestion_id: int) -> TuningSuggestion:
    suggestion = db.query(TuningSuggestion).filter(TuningSuggestion.id == suggestion_id).first()
    if suggestion is None:
        raise FeedbackWorkflowError(404, "Sugestão não encontrada")
    suggestion.status = "rejected"
    db.commit()
    return suggestion


def update_learning_policy(
    db: Session,
    camera_id: int,
    *,
    learning_mode: str,
    auto_tuning_enabled: bool,
    critical_lock: bool,
    max_daily_auto_changes: int,
    min_reviewed_events_for_suggestion: int,
    min_reviewed_events_for_auto_tuning: int,
    rollback_window_hours: int,
) -> Camera:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        raise FeedbackWorkflowError(404, "Câmera não encontrada")
    if learning_mode not in LEARNING_MODES:
        raise FeedbackWorkflowError(400, "Modo de aprendizado inválido")
    camera.learning_mode = learning_mode
    camera.auto_tuning_enabled = auto_tuning_enabled
    camera.critical_lock = critical_lock
    camera.max_daily_auto_changes = max_daily_auto_changes
    camera.min_reviewed_events_for_suggestion = min_reviewed_events_for_suggestion
    camera.min_reviewed_events_for_auto_tuning = min_reviewed_events_for_auto_tuning
    camera.rollback_window_hours = rollback_window_hours
    db.commit()
    return camera


def list_config_history_payloads(db: Session, camera_id: int | None = None) -> list[dict[str, Any]]:
    query = db.query(ConfigVersionHistory)
    if camera_id is not None:
        query = query.filter(ConfigVersionHistory.camera_id == camera_id)
    items = query.order_by(ConfigVersionHistory.id.desc()).limit(100).all()
    return [
        {
            "id": item.id,
            "camera_id": item.camera_id,
            "config_before": item.config_before,
            "config_after": item.config_after,
            "change_source": item.change_source,
            "reason": item.reason,
            "created_at": format_brazil_datetime(item.created_at),
            "rollback_available": item.rollback_available,
        }
        for item in items
    ]


def rollback_config_history(db: Session, history_id: int) -> Camera:
    history = db.query(ConfigVersionHistory).filter(ConfigVersionHistory.id == history_id).first()
    if history is None:
        raise FeedbackWorkflowError(404, "Histórico não encontrado")
    camera = rollback_camera_config(db, history)
    if camera is None:
        raise FeedbackWorkflowError(400, "Rollback não aplicável")
    db.commit()
    return camera
