"""Apresentação do histórico de integrações Lockdown."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.lockdown_delivery_service import list_lockdown_deliveries
from app.web.camera_detail_presenter import format_dt, get_camera_map


def truncate_for_display(value: str | None, limit: int = 220) -> str:
    text = value or "-"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def build_lockdown_deliveries_payload(
    db: Session,
    *,
    camera_id: int | None = None,
    status: str | None = None,
    event_type: str | None = None,
    event_id: int | None = None,
) -> dict:
    listing = list_lockdown_deliveries(
        db,
        camera_id=camera_id,
        status=status,
        event_type=event_type,
        event_id=event_id,
    )
    camera_map = get_camera_map(db)
    for delivery in listing.deliveries:
        camera = camera_map.get(delivery.camera_id)
        delivery.camera_name = (
            camera.name if camera else f"Câmera {delivery.camera_id}"
        )
        delivery.created_at_label = (
            format_dt(delivery.created_at)
            if getattr(delivery, "created_at", None)
            else "-"
        )
        delivery.request_body_preview = truncate_for_display(delivery.request_body)
        delivery.response_body_preview = truncate_for_display(delivery.response_body)
        delivery.error_message_preview = truncate_for_display(delivery.error_message)
        delivery.request_signature_preview = truncate_for_display(
            delivery.request_signature,
            32,
        )
        delivery.last_attempt_at_label = (
            format_dt(delivery.last_attempt_at) if delivery.last_attempt_at else "-"
        )
        delivery.sent_at_label = (
            format_dt(delivery.sent_at) if delivery.sent_at else "-"
        )
    return {
        "deliveries": listing.deliveries,
        "camera_map": camera_map,
        "cameras": list(camera_map.values()),
        "event_types": listing.event_types,
        "summary": {
            "total": listing.total,
            "sent": listing.sent,
            "error": listing.error,
            "pending": listing.pending,
        },
    }
