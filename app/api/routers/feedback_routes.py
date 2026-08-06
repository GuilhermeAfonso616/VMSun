"""Rotas HTTP de metricas, sugestoes e politica de aprendizado."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.schemas.camera_schemas import CameraLearningPolicyUpdate
from app.services.feedback_review_service import (
    build_active_learning_queue,
    build_feedback_metrics,
    evaluate_drift,
)
from app.services.feedback_workflow_service import (
    FeedbackWorkflowError,
    apply_suggestion,
    list_suggestion_payloads,
    reject_suggestion,
    update_learning_policy,
)


router = APIRouter(tags=["feedback"])


def _translate_error(exc: FeedbackWorkflowError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/feedback/metrics")
def feedback_metrics(camera_id: int | None = None, days: int = 30, db: Session = Depends(get_db)):
    return build_feedback_metrics(db, camera_id=camera_id, days=days)


@router.get("/feedback/active-learning")
def feedback_active_learning(camera_id: int | None = None, days: int = 30, db: Session = Depends(get_db)):
    return {"items": build_active_learning_queue(db, camera_id=camera_id, days=days)}


@router.get("/feedback/suggestions")
def feedback_suggestions(
    camera_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    return {"suggestions": list_suggestion_payloads(db, camera_id=camera_id, status=status)}


@router.post("/feedback/suggestions/{suggestion_id}/apply")
def feedback_apply_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    try:
        camera = apply_suggestion(db, suggestion_id)
    except FeedbackWorkflowError as exc:
        raise _translate_error(exc) from exc
    return {"message": "Sugestão aplicada", "camera_id": camera.id, "suggestion_id": suggestion_id}


@router.post("/feedback/suggestions/{suggestion_id}/reject")
def feedback_reject_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    try:
        reject_suggestion(db, suggestion_id)
    except FeedbackWorkflowError as exc:
        raise _translate_error(exc) from exc
    return {"message": "Sugestão rejeitada", "suggestion_id": suggestion_id}


@router.get("/feedback/drift")
def feedback_drift(camera_id: int, db: Session = Depends(get_db)):
    return evaluate_drift(db, camera_id)


@router.post("/cameras/{camera_id}/learning-policy")
def update_camera_learning_policy(
    camera_id: int,
    payload: CameraLearningPolicyUpdate,
    db: Session = Depends(get_db),
):
    try:
        update_learning_policy(
            db,
            camera_id,
            learning_mode=payload.learning_mode,
            auto_tuning_enabled=payload.auto_tuning_enabled,
            critical_lock=payload.critical_lock,
            max_daily_auto_changes=payload.max_daily_auto_changes,
            min_reviewed_events_for_suggestion=payload.min_reviewed_events_for_suggestion,
            min_reviewed_events_for_auto_tuning=payload.min_reviewed_events_for_auto_tuning,
            rollback_window_hours=payload.rollback_window_hours,
        )
    except FeedbackWorkflowError as exc:
        raise _translate_error(exc) from exc
    return {"message": "Política de aprendizado atualizada", "camera_id": camera_id}
