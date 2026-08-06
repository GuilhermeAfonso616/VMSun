from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import BASE_DIR
from app.core.timezone import utc_now_naive
from app.db.models import Event, EventFeedback
from app.services.local_clip_retention_service import local_clip_video_path
from app.services.onedrive_client import onedrive_client


UPLOADED_STATUSES = {"uploaded"}
CLIP_DONE_STATUSES = {"uploaded", "deleted_after_review"}


def _json_or_none(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def _resolve_local_path(value: str | None) -> Path | None:
    if not value:
        return None
    raw = str(value).strip().replace("\\", "/")
    if not raw:
        return None

    path = Path(raw)
    if path.is_absolute() and path.exists():
        return path

    if raw.startswith("/data/"):
        candidate = BASE_DIR / "data" / raw.removeprefix("/data/")
    elif path.is_absolute():
        candidate = path
    else:
        candidate = BASE_DIR / path

    try:
        return candidate.resolve()
    except Exception:
        return candidate


def _latest_feedback_by_event(db: Session, event_ids: list[int]) -> dict[int, EventFeedback]:
    if not event_ids:
        return {}
    rows = (
        db.query(EventFeedback)
        .filter(EventFeedback.event_id.in_(event_ids))
        .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
        .all()
    )
    feedback_by_event: dict[int, EventFeedback] = {}
    for feedback in rows:
        feedback_by_event.setdefault(int(feedback.event_id), feedback)
    return feedback_by_event


def _event_payload(event: Event, feedback: EventFeedback | None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event": {
            "id": event.id,
            "camera_id": event.camera_id,
            "event_type": event.event_type,
            "rule_id": event.rule_id,
            "track_id": event.track_id,
            "severity": event.severity,
            "status": event.status,
            "lifecycle_action": event.lifecycle_action,
            "alarm_category": event.alarm_category,
            "alarm_eligible": event.alarm_eligible,
            "is_alarm_active": event.is_alarm_active,
            "started_at": event.started_at,
            "ended_at": event.ended_at,
            "created_at": event.created_at,
            "confidence": event.confidence,
            "event_score": event.event_score,
            "detector_score": event.detector_score,
            "details": event.details,
            "correlation_key": event.correlation_key,
        },
        "feedback": {
            "id": feedback.id,
            "label": feedback.label,
            "probable_cause": feedback.probable_cause,
            "operator_note": feedback.operator_note,
            "reviewed_by": feedback.reviewed_by,
            "reviewed_at": feedback.reviewed_at,
        }
        if feedback
        else None,
        "evidence": {
            "bbox": _json_or_none(event.bbox_json),
            "zone_id": event.zone_id,
            "roi_id": event.roi_id,
            "scene_profile": event.scene_profile,
            "camera_family": event.camera_family,
        },
        "artifacts": {
            "snapshot_path": event.snapshot_path,
            "clip_path": event.clip_path,
            "clip_remote_web_url": event.clip_remote_web_url,
            "snapshot_remote_web_url": event.snapshot_remote_web_url,
        },
        "profile_snapshot": _json_or_none(event.active_profile_snapshot),
        "threshold_snapshot": _json_or_none(event.threshold_snapshot),
        "nuisance_profile_snapshot": _json_or_none(event.nuisance_profile_snapshot),
    }


def _status_uploaded(status: str | None) -> bool:
    return str(status or "").strip().lower() in UPLOADED_STATUSES


def _clip_done(status: str | None) -> bool:
    return str(status or "").strip().lower() in CLIP_DONE_STATUSES


def _reviewed_event_query(db: Session):
    feedback_event_ids = db.query(EventFeedback.event_id).distinct().subquery()
    return (
        db.query(Event)
        .join(feedback_event_ids, Event.id == feedback_event_ids.c.event_id)
        .order_by(Event.id.asc())
    )


def _event_has_pending_artifacts(event: Event) -> bool:
    if not _status_uploaded(event.event_remote_status):
        return True
    if event.snapshot_path and not _status_uploaded(event.snapshot_remote_status):
        return True
    if event.clip_path and not _clip_done(event.clip_remote_status) and local_clip_video_path(event) is not None:
        return True
    return False


def count_reviewed_events_pending_onedrive(db: Session) -> int:
    return sum(1 for event in _reviewed_event_query(db).all() if _event_has_pending_artifacts(event))


def upload_reviewed_events_pending_onedrive(db: Session, *, limit: int | None = None) -> dict[str, Any]:
    if not onedrive_client.enabled():
        raise RuntimeError("onedrive_disabled")

    selected: list[Event] = []
    for event in _reviewed_event_query(db).all():
        if _event_has_pending_artifacts(event):
            selected.append(event)
        if limit is not None and len(selected) >= max(1, int(limit)):
            break

    feedback_by_event = _latest_feedback_by_event(db, [int(event.id) for event in selected])
    result: dict[str, Any] = {
        "reviewed_pending": len(selected),
        "events_processed": 0,
        "event_json_uploaded": 0,
        "snapshot_uploaded": 0,
        "clip_uploaded": 0,
        "missing_snapshot": 0,
        "missing_clip": 0,
        "failed": 0,
        "errors": [],
    }

    for event in selected:
        event_id = int(event.id)
        changed = False
        result["events_processed"] += 1

        if event.snapshot_path and not _status_uploaded(event.snapshot_remote_status):
            snapshot_file = _resolve_local_path(event.snapshot_path)
            if snapshot_file and snapshot_file.exists():
                try:
                    remote = onedrive_client.upload_audit_snapshot(event_id=event_id, snapshot_file=snapshot_file)
                    event.snapshot_remote_item_id = remote.get("item_id")
                    event.snapshot_remote_web_url = remote.get("web_url")
                    event.snapshot_remote_status = "uploaded"
                    event.snapshot_remote_uploaded_at = utc_now_naive()
                    result["snapshot_uploaded"] += 1
                    changed = True
                except Exception as exc:
                    event.snapshot_remote_status = "failed"
                    result["failed"] += 1
                    result["errors"].append({"event_id": event_id, "artifact": "snapshot", "error": str(exc)})
                    changed = True
            else:
                result["missing_snapshot"] += 1

        if event.clip_path and not _clip_done(event.clip_remote_status):
            clip_file = local_clip_video_path(event)
            if clip_file is not None:
                try:
                    remote = onedrive_client.upload_audit_clip(event_id=event_id, clip_file=clip_file)
                    event.clip_remote_item_id = remote.get("item_id")
                    event.clip_remote_web_url = remote.get("web_url")
                    event.clip_remote_status = "uploaded"
                    event.clip_remote_uploaded_at = utc_now_naive()
                    result["clip_uploaded"] += 1
                    changed = True
                except Exception as exc:
                    event.clip_remote_status = "failed"
                    result["failed"] += 1
                    result["errors"].append({"event_id": event_id, "artifact": "clip", "error": str(exc)})
                    changed = True
            else:
                result["missing_clip"] += 1

        if not _status_uploaded(event.event_remote_status):
            try:
                remote = onedrive_client.upload_audit_event(
                    event_id=event_id,
                    event_payload=_event_payload(event, feedback_by_event.get(event_id)),
                )
                event.event_remote_item_id = remote.get("item_id")
                event.event_remote_web_url = remote.get("web_url")
                event.event_remote_status = "uploaded"
                event.event_remote_uploaded_at = utc_now_naive()
                result["event_json_uploaded"] += 1
                changed = True
            except Exception as exc:
                event.event_remote_status = "failed"
                result["failed"] += 1
                result["errors"].append({"event_id": event_id, "artifact": "event_json", "error": str(exc)})
                changed = True

        if changed:
            db.commit()

    result["reviewed_pending_after"] = count_reviewed_events_pending_onedrive(db)
    return result
