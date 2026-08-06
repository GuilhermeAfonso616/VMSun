"""Páginas e endpoint JSON de consulta e revisão de eventos."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.db.models import User
from app.services.feedback_review_service import build_event_review_payload
from app.services.revalidator_dataset_collector import (
    build_revalidator_dataset_summary,
)
from app.services.revalidator_policy_store import load_revalidator_policy
from app.web.event_listing_presenter import (
    as_bool,
    build_events_payload,
    parse_optional_int_filter,
    serialize_event_for_table,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()


@router.get("/events/data")
def events_data(
    camera_id: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    assigned_user_id: str | None = None,
    sla_state: str | None = None,
    only_open: str | None = None,
    only_audit: str | None = None,
    date: str | None = None,
    _current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator"])
    ),
):
    db = get_scoped_db()
    try:
        payload = build_events_payload(
            db,
            camera_id=camera_id,
            severity=severity,
            status=status,
            event_type=event_type,
            assigned_user_id=assigned_user_id,
            sla_state=sla_state,
            only_open=only_open,
            only_audit=only_audit,
            date=date,
        )
        latest_popup_alarm = payload.get("latest_popup_alarm")
        return JSONResponse(
            {
                "events": [
                    serialize_event_for_table(event)
                    for event in payload["events"]
                ],
                "latest_alarm_signature": payload["latest_alarm_signature"],
                "alarm_should_play": payload.get("alarm_should_play", False),
                "latest_popup_alarm": (
                    serialize_event_for_table(latest_popup_alarm)
                    if latest_popup_alarm
                    else None
                ),
            }
        )
    finally:
        db.close()


@router.get("/events")
def events_page(
    request: Request,
    camera_id: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    assigned_user_id: str | None = None,
    sla_state: str | None = None,
    only_open: str | None = None,
    only_audit: str | None = None,
    date: str | None = None,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator"])
    ),
):
    db = get_scoped_db()
    try:
        selected_camera_id = parse_optional_int_filter(camera_id)
        payload = build_events_payload(
            db,
            camera_id=selected_camera_id,
            severity=severity,
            status=status,
            event_type=event_type,
            assigned_user_id=assigned_user_id,
            sla_state=sla_state,
            only_open=only_open,
            only_audit=only_audit,
            date=date,
        )
        revalidator_policy = load_revalidator_policy()
        latest_popup_alarm = payload["latest_popup_alarm"]
        return templates.TemplateResponse(
            request=request,
            name="events.html",
            context={
                "request": request,
                "events": payload["events"],
                "event_rows_payload": [
                    serialize_event_for_table(event)
                    for event in payload["events"]
                ],
                "cameras": list(payload["camera_map"].values()),
                "selected_camera_id": selected_camera_id,
                "selected_severity": severity or "",
                "selected_status": status or "",
                "selected_event_type": event_type or "",
                "selected_assigned_user_id": assigned_user_id or "",
                "selected_sla_state": sla_state or "",
                "only_open": as_bool(only_open),
                "selected_date": date or "",
                "event_types": payload["distinct_event_types"],
                "latest_alarm_signature": payload["latest_alarm_signature"],
                "alarm_should_play": payload["alarm_should_play"],
                "latest_popup_alarm": (
                    serialize_event_for_table(latest_popup_alarm)
                    if latest_popup_alarm
                    else None
                ),
                "person_revalidator_mode": revalidator_policy["mode"],
                "person_revalidator_cancel_enabled": (
                    revalidator_policy["mode"] == "block"
                ),
                "incident_users": [
                    user
                    for user in db.query(User)
                    .filter(User.is_active.is_(True))
                    .order_by(User.name, User.username)
                    .all()
                    if user.role in {"admin", "supervisor", "operator", "dev"}
                ],
            },
        )
    finally:
        db.close()


@router.get("/events/review")
def events_review_page(
    request: Request,
    camera_id: str | None = None,
    label: str | None = None,
    probable_cause: str | None = None,
    profile: str | None = None,
    turn: str | None = None,
    status: str | None = None,
    days: int = 30,
    limit: int = 80,
    include_ai_validated: bool = False,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator"])
    ),
):
    db = get_scoped_db()
    try:
        selected_camera_id = parse_optional_int_filter(camera_id)
        payload = build_event_review_payload(
            db,
            camera_id=selected_camera_id,
            label=label,
            probable_cause=probable_cause,
            profile=profile,
            turn=turn,
            status=status,
            days=days,
            limit=limit,
            include_ai_validated=include_ai_validated,
        )
        return templates.TemplateResponse(
            request=request,
            name="event_validation.html",
            context={
                "request": request,
                "events": payload["events"],
                "metrics": payload["metrics"],
                "cameras": payload["cameras"],
                "labels": payload["labels"],
                "probable_causes": payload["probable_causes"],
                "profile_options": payload["profile_options"],
                "turn_options": payload["turn_options"],
                "learning_mode_counts": payload["learning_mode_counts"],
                "suggestions": payload["suggestions"],
                "active_learning_queue": payload["active_learning_queue"],
                "revalidator_dataset": build_revalidator_dataset_summary(),
                "drift": payload["drift"],
                "selected_camera_id": selected_camera_id,
                "selected_label": label or "",
                "selected_probable_cause": probable_cause or "",
                "selected_profile": profile or "",
                "selected_turn": turn or "",
                "selected_status": status or "",
                "selected_days": days,
                "selected_limit": payload["loaded_event_limit"],
                "ai_validated_count": payload["ai_validated_count"],
                "ai_validated_by_label": payload["ai_validated_by_label"],
                "include_ai_validated": payload["include_ai_validated"],
            },
        )
    finally:
        db.close()
