"""Página e operações do histórico de entregas Lockdown."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.logging import get_logger
from app.db.models import User
from app.services.event_listing_service import parse_optional_int_filter
from app.services.lockdown_delivery_service import (
    LockdownDeliveryNotFound,
    normalize_lockdown_status,
    resend_delivery,
)
from app.services.lockdown_policy_store import (
    LOCKDOWN_TRIGGER_EVENT_CHOICES,
    LOCKDOWN_TRIGGER_EVENT_LABELS,
    load_lockdown_policy,
    save_lockdown_policy,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates
from app.web.lockdown_presenter import build_lockdown_deliveries_payload


router = APIRouter()
logger = get_logger("app.web.lockdown_deliveries")


def _redirect_back(request: Request, fallback: str) -> str:
    return request.headers.get("referer") or fallback


@router.get("/lockdown-deliveries")
def lockdown_deliveries_page(
    request: Request,
    camera_id: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    event_id: str | None = None,
    current_user: User = Depends(require_web_auth(["admin", "supervisor"])),
):
    db = get_scoped_db()
    try:
        selected_camera_id = parse_optional_int_filter(camera_id)
        parsed_event_id = parse_optional_int_filter(event_id)
        payload = build_lockdown_deliveries_payload(
            db,
            camera_id=selected_camera_id,
            status=status,
            event_type=event_type,
            event_id=parsed_event_id,
        )
        policy = load_lockdown_policy()
        return templates.TemplateResponse(
            request=request,
            name="lockdown_deliveries.html",
            context={
                "request": request,
                "deliveries": payload["deliveries"],
                "cameras": payload["cameras"],
                "event_types": payload["event_types"],
                "summary": payload["summary"],
                "selected_camera_id": selected_camera_id,
                "selected_status": normalize_lockdown_status(status) or "",
                "selected_event_type": event_type or "",
                "selected_event_id": event_id or "",
                "lockdown_trigger_event_choices": LOCKDOWN_TRIGGER_EVENT_CHOICES,
                "lockdown_trigger_event_labels": LOCKDOWN_TRIGGER_EVENT_LABELS,
                "selected_trigger_event_types": (
                    policy.get("allowed_trigger_events") or []
                ),
            },
        )
    finally:
        db.close()


@router.post("/lockdown-deliveries/policy")
def update_lockdown_policy(
    request: Request,
    trigger_event_types: list[str] = Form(default=[]),
):
    normalized = [
        event_type
        for event_type in trigger_event_types
        if event_type in LOCKDOWN_TRIGGER_EVENT_CHOICES
    ]
    save_lockdown_policy(normalized)
    logger.info(
        "Lockdown trigger policy updated allowed_trigger_events=%s",
        normalized,
    )
    return RedirectResponse(
        url=_redirect_back(request, "/lockdown-deliveries"),
        status_code=303,
    )


@router.post("/lockdown-deliveries/{delivery_id}/resend")
def resend_lockdown_delivery(request: Request, delivery_id: int):
    db = get_scoped_db()
    try:
        try:
            delivery = resend_delivery(db, delivery_id)
        except LockdownDeliveryNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail="Envio não encontrado",
            ) from exc
        logger.info(
            "Manual resend requested delivery_id=%s event_id=%s",
            delivery.id,
            delivery.event_id,
        )
        return RedirectResponse(
            url=_redirect_back(request, "/lockdown-deliveries"),
            status_code=303,
        )
    finally:
        db.close()
