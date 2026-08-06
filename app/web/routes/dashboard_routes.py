"""Paginas e endpoints de dados do dashboard operacional."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.db.models import User
from app.services.alert_center import compute_alerts
from app.services.dashboard_service import (
    get_dashboard_camera_counts,
    get_operational_history_payload,
    get_resource_history_payload,
)
from app.services.revalidator_policy_store import load_revalidator_policy
from app.services.storage_usage import compute_storage_report
from app.web.infrastructure import get_scoped_db, require_web_auth, templates
from app.web.monitor_presenter import build_dashboard_events_payload
from app.web.operational_metrics_presenter import build_dashboard_metrics_snapshot


router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator", "viewer"])
    ),
):
    db = get_scoped_db()
    try:
        counts = get_dashboard_camera_counts(db)
        return templates.TemplateResponse(
            request=request,
            name="dashboard_resources.html",
            context={
                "request": request,
                **counts,
                "total_events": 0,
                "open_events_count": 0,
                "new_events_count": 0,
                "critical_open_count": 0,
                "recent_events": [],
                "open_events": [],
                "metrics_snapshot": None,
                "latest_alarm_signature": "",
                "alarm_should_play": False,
            },
        )
    finally:
        db.close()


@router.get("/saude")
@router.get("/health")
def dashboard_health(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    revalidator_policy = load_revalidator_policy()
    return templates.TemplateResponse(
        request=request,
        name="dashboard_health.html",
        context={
            "request": request,
            "metrics_snapshot": None,
            "person_revalidator_cancel_enabled": (
                revalidator_policy.get("mode") == "block"
            ),
        },
    )


@router.get("/audit")
@router.get("/auditoria")
def dashboard_audit(
    request: Request,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator", "viewer", "dev"])
    ),
):
    return templates.TemplateResponse(
        request=request,
        name="dashboard_audit.html",
        context={"request": request},
    )


@router.get("/recursos/historico")
@router.get("/resources/history")
def dashboard_resource_history_page(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    return templates.TemplateResponse(
        request=request,
        name="dashboard_resource_history.html",
        context={"request": request},
    )


@router.get("/relatorios/disponibilidade")
@router.get("/reports/availability")
def report_availability_page(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    return templates.TemplateResponse(
        request=request,
        name="report_availability.html",
        context={"request": request},
    )


@router.get("/armazenamento")
@router.get("/storage")
def storage_monitor_page(
    request: Request,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    return templates.TemplateResponse(
        request=request,
        name="storage_monitor.html",
        context={"request": request},
    )


@router.get("/dashboard/storage")
def dashboard_storage():
    return JSONResponse(compute_storage_report())


@router.get("/alertas")
@router.get("/alerts")
def alerts_page(
    request: Request,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator", "viewer"])
    ),
):
    return templates.TemplateResponse(
        request=request,
        name="alerts.html",
        context={"request": request},
    )


@router.get("/dashboard/alerts")
def dashboard_alerts():
    return JSONResponse(compute_alerts())


@router.get("/dashboard/metrics")
def dashboard_metrics():
    db = get_scoped_db()
    try:
        return JSONResponse(build_dashboard_metrics_snapshot(db))
    finally:
        db.close()


@router.get("/dashboard/operational-history")
def dashboard_operational_history(
    hours: int = Query(24, ge=1, le=168),
    bucket_minutes: int = Query(5, ge=1, le=60),
    camera_id: int | None = Query(None, ge=1),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    payload, status_code = get_operational_history_payload(
        hours=hours,
        bucket_minutes=bucket_minutes,
        camera_id=camera_id,
        start=start,
        end=end,
    )
    return JSONResponse(payload, status_code=status_code)


@router.get("/dashboard/resource-history")
def dashboard_resource_history(
    hours: int = Query(24, ge=1, le=168),
    bucket_minutes: int = Query(5, ge=1, le=60),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    payload, status_code = get_resource_history_payload(
        hours=hours,
        bucket_minutes=bucket_minutes,
        start=start,
        end=end,
    )
    return JSONResponse(payload, status_code=status_code)


@router.get("/dashboard/events")
def dashboard_events_data():
    db = get_scoped_db()
    try:
        response = JSONResponse(build_dashboard_events_payload(db))
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    finally:
        db.close()
