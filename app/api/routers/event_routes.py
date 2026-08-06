"""Rotas HTTP de eventos e revisao operacional."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.services.event_broadcaster import event_broadcaster
from app.api.schemas.event_schemas import (
    EventFeedbackCreate,
    EventUpdate,
    IncidentAssign,
    IncidentChecklistUpdate,
    IncidentClose,
    IncidentComment,
    IncidentCorrelate,
    IncidentCreate,
    IncidentDetailsUpdate,
)
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.event_service import (
    EventServiceError,
    get_tuning_summary_payload,
    list_audit_queue_payloads,
    list_event_payloads,
    record_event_feedback,
)
from app.services.feedback_review_service import build_event_review_payload
from app.services.incident_service import (
    ASSIGNABLE_ROLES,
    acknowledge_incident,
    add_incident_comment,
    assign_incident,
    close_incident,
    correlate_incident_events,
    create_manual_incident,
    incident_checklist,
    incident_related_events,
    incident_summary,
    incident_timeline,
    reopen_incident,
    update_checklist_item,
    update_incident_details,
)


router = APIRouter(prefix="/events", tags=["events"])


def _translate_error(exc: EventServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("")
def list_events(
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    return list_event_payloads(db)


@router.get("/stream")
async def stream_events(
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
):
    """Endpoint de Server-Sent Events (SSE) para notificação instantânea de alarmes ao vivo."""
    return StreamingResponse(
        event_broadcaster.stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/audit-queue")
def get_audit_queue(
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    """Retorna os eventos da fila silenciosa de auditoria (eventos shadow/audit sem alarme ativo)."""
    return list_audit_queue_payloads(db)


@router.get("/tuning-summary")
def get_tuning_summary(
    camera_id: int | None = None,
    _current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    """Retorna estatísticas de causa provável de falsos alarmes e sugestões de tuning por câmera."""
    return get_tuning_summary_payload(db, camera_id=camera_id)


@router.put("/{event_id}")
def update_event(
    event_id: int,
    payload: EventUpdate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        if payload.status == "acknowledged":
            event = acknowledge_incident(db, event_id, current_user)
            if payload.operator_note:
                event = add_incident_comment(db, event_id, current_user, payload.operator_note)
        elif payload.status == "closed":
            event = close_incident(
                db,
                event_id,
                current_user,
                resolution_code=payload.resolution_code or "",
                comment=payload.operator_note,
            )
        elif payload.status == "new":
            event = reopen_incident(db, event_id, current_user, comment=payload.operator_note)
        elif payload.status is not None:
            raise EventServiceError(400, "Status inválido")
        elif payload.operator_note is not None:
            event = add_incident_comment(db, event_id, current_user, payload.operator_note)
        else:
            raise EventServiceError(400, "Nenhuma alteracao informada.")
    except EventServiceError as exc:
        raise _translate_error(exc) from exc

    log_audit(
        db,
        "event_update",
        current_user,
        f"Atualizou status do evento id={event_id} para {payload.status or event.status}",
        ip_address=request.client.host if request.client else None,
    )
    return {"message": "Evento atualizado", "event_id": event_id}


@router.get("/review")
def review_events(
    camera_id: int | None = None,
    label: str | None = None,
    probable_cause: str | None = None,
    profile: str | None = None,
    turn: str | None = None,
    status: str | None = None,
    days: int = 30,
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    return build_event_review_payload(
        db=db,
        camera_id=camera_id,
        label=label,
        probable_cause=probable_cause,
        profile=profile,
        turn=turn,
        status=status,
        days=days,
    )


@router.get("/incidents/summary")
def get_incident_summary(
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    return incident_summary(db)


@router.post("/incidents")
def create_incident_route(
    payload: IncidentCreate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        event = create_manual_incident(
            db,
            current_user,
            camera_id=payload.camera_id,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            team=payload.team,
            assignee_user_id=payload.assignee_user_id,
        )
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_create", current_user, f"Criou incidente manual id={event.id}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id, "status": event.status}


@router.get("/incidents/assignees")
def list_incident_assignees(
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.name, User.username).all()
    return [
        {"id": user.id, "username": user.username, "name": user.name, "role": user.role}
        for user in users
        if user.role in ASSIGNABLE_ROLES
    ]


@router.get("/{event_id}/timeline")
def get_incident_timeline(
    event_id: int,
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        entries = incident_timeline(db, event_id)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    return [
        {
            "id": item.id,
            "action": item.action,
            "actor_username": item.actor_username,
            "from_status": item.from_status,
            "to_status": item.to_status,
            "comment": item.comment,
            "created_at": item.created_at,
        }
        for item in entries
    ]


@router.get("/{event_id}/incident")
def get_incident_detail(
    event_id: int,
    _current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        related = incident_related_events(db, event_id)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    requested = next((item for item in related if item.id == event_id), related[0])
    root_id = requested.incident_parent_id or requested.related_event_id or requested.id
    target = next((item for item in related if item.id == root_id), requested)
    return {
        "event_id": target.id,
        "team": target.incident_team,
        "priority": target.incident_priority or target.severity,
        "origin": target.incident_origin or "automatic",
        "checklist": incident_checklist(target),
        "related_events": [
            {"id": item.id, "event_type": item.event_type, "camera_id": item.camera_id, "status": item.status}
            for item in related
        ],
    }


@router.patch("/{event_id}/details")
def update_incident_details_route(
    event_id: int,
    payload: IncidentDetailsUpdate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        event = update_incident_details(db, event_id, current_user, priority=payload.priority, team=payload.team)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_details", current_user, f"Atualizou incidente id={event_id}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id, "priority": event.incident_priority, "team": event.incident_team}


@router.patch("/{event_id}/checklist/{item_id}")
def update_incident_checklist_route(
    event_id: int,
    item_id: str,
    payload: IncidentChecklistUpdate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        event = update_checklist_item(db, event_id, item_id, payload.completed, current_user)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_checklist", current_user, f"Atualizou checklist do incidente id={event_id}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id, "checklist": incident_checklist(event)}


@router.post("/{event_id}/correlate")
def correlate_incident_route(
    event_id: int,
    payload: IncidentCorrelate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        linked = correlate_incident_events(db, event_id, payload.event_ids, current_user)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_correlate", current_user, f"Vinculou {len(linked)} evento(s) ao incidente id={event_id}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event_id, "linked_event_ids": [item.id for item in linked]}


@router.post("/{event_id}/assign")
def assign_incident_route(
    event_id: int,
    payload: IncidentAssign,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        event = assign_incident(db, event_id, payload.assignee_user_id, current_user)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_assign", current_user, f"Atribuiu incidente id={event_id} para {event.assigned_username or 'ninguem'}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id, "assigned_username": event.assigned_username}


@router.post("/{event_id}/acknowledge")
def acknowledge_incident_route(
    event_id: int,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        event = acknowledge_incident(db, event_id, current_user)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_acknowledge", current_user, f"Reconheceu incidente id={event_id}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id, "status": event.status}


@router.post("/{event_id}/close")
def close_incident_route(
    event_id: int,
    request: Request,
    payload: IncidentClose | None = None,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    code = (payload.resolution_code if payload and payload.resolution_code else None) or "other"
    comment = payload.comment if payload else None
    try:
        event = close_incident(db, event_id, current_user, resolution_code=code, comment=comment)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_close", current_user, f"Fechou incidente id={event_id}: {event.resolution_code}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id, "status": event.status, "resolution_code": event.resolution_code}


@router.post("/{event_id}/reopen")
def reopen_incident_route(
    event_id: int,
    payload: IncidentComment,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor"])),
    db: Session = Depends(get_db),
):
    try:
        event = reopen_incident(db, event_id, current_user, comment=payload.comment)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_reopen", current_user, f"Reabriu incidente id={event_id}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id, "status": event.status}


@router.post("/{event_id}/comments")
def add_incident_comment_route(
    event_id: int,
    payload: IncidentComment,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        event = add_incident_comment(db, event_id, current_user, payload.comment)
    except EventServiceError as exc:
        raise _translate_error(exc) from exc
    log_audit(db, "incident_comment", current_user, f"Comentou incidente id={event_id}", ip_address=request.client.host if request.client else None)
    return {"ok": True, "event_id": event.id}


@router.post("/{event_id}/feedback")
def submit_event_feedback(
    event_id: int,
    payload: EventFeedbackCreate,
    request: Request,
    current_user: User = Depends(require_role(["admin", "supervisor", "operator"])),
    db: Session = Depends(get_db),
):
    try:
        result = record_event_feedback(
            db,
            event_id,
            label=payload.label,
            probable_cause=payload.probable_cause,
            operator_note=payload.operator_note,
            reviewed_by=payload.reviewed_by,
            auto_suggest=payload.auto_suggest,
        )
    except EventServiceError as exc:
        raise _translate_error(exc) from exc

    log_audit(
        db,
        "event_feedback",
        current_user,
        f"Registrou feedback para o evento id={event_id}: classificação={payload.label}",
        ip_address=request.client.host if request.client else None,
    )
    return {
        "message": "Feedback registrado",
        "event_id": event_id,
        "feedback_id": result.feedback_id,
        "suggestions_created": result.suggestions_created,
    }
