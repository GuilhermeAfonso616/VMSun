"""Listagem operacional de cameras e endpoint simples de metricas."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.db.models import User
from app.services.metrics_store import metrics_store
from app.web.camera_metrics_presenter import build_camera_metrics_context
from app.web.camera_overview_presenter import build_camera_overview_context
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()


@router.get("/cameras/{camera_id}/metrics")
def camera_metrics(camera_id: int):
    data = metrics_store.get_metrics(camera_id)
    if not data:
        raise HTTPException(status_code=404, detail="Sem métricas para esta câmera")
    return data


@router.get("/cameras/{camera_id}/metrics/view")
def camera_metrics_view(request: Request, camera_id: int):
    db = get_scoped_db()
    try:
        context = build_camera_metrics_context(db, camera_id)
        return templates.TemplateResponse(
            request=request,
            name="camera_metrics.html",
            context={"request": request, **context},
        )
    finally:
        db.close()


@router.get("/cameras")
def cameras_page(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    db = get_scoped_db()
    try:
        context = build_camera_overview_context(
            db,
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        )
        return templates.TemplateResponse(
            request=request,
            name="cameras.html",
            context={"request": request, **context},
        )
    finally:
        db.close()
