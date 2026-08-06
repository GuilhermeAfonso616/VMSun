from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timezone import utc_now_naive
from app.db.models import Event, EventFeedback


def _load_clip_metadata(clip_path: str | None) -> dict:
    if not clip_path:
        return {}
    metadata_path = Path(clip_path) / "metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def local_clip_video_path(event: Event) -> Path | None:
    clip_path = getattr(event, "clip_path", None)
    if not clip_path:
        return None
    metadata = _load_clip_metadata(clip_path)
    candidate = Path(clip_path) / str(metadata.get("video_file") or "clip.mp4")
    return candidate if candidate.exists() else None


def _latest_feedback_labels(db: Session, event_ids: list[int]) -> dict[int, str]:
    if not event_ids:
        return {}
    rows = (
        db.query(EventFeedback)
        .filter(EventFeedback.event_id.in_(event_ids))
        .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
        .all()
    )
    labels: dict[int, str] = {}
    for row in rows:
        labels.setdefault(int(row.event_id), str(row.label or "").strip().lower())
    return labels


def _delete_local_video(event: Event, video_path: Path, *, deleted_at: datetime) -> bool:
    try:
        video_path.unlink(missing_ok=True)
        event.clip_local_deleted_at = deleted_at
        return True
    except Exception:
        return False


def prune_local_review_clips(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    current_time = now or utc_now_naive()
    events = (
        db.query(Event)
        .filter(Event.clip_path.isnot(None))
        .order_by(Event.created_at.asc(), Event.id.asc())
        .all()
    )
    candidates: list[tuple[Event, Path]] = []
    for event in events:
        video_path = local_clip_video_path(event)
        if video_path is not None:
            candidates.append((event, video_path))

    labels = _latest_feedback_labels(db, [int(event.id) for event, _ in candidates])
    deleted_non_fp = 0
    kept: list[tuple[Event, Path, str | None]] = []
    for event, video_path in candidates:
        label = labels.get(int(event.id))
        if label and label != "false_positive":
            if _delete_local_video(event, video_path, deleted_at=current_time):
                deleted_non_fp += 1
            continue
        kept.append((event, video_path, label))

    deleted_false_positive = 0
    false_positive_items = [item for item in kept if item[2] == "false_positive"]
    false_positive_limit = max(0, int(settings.local_clip_retention_max_false_positive or 0))
    overflow_false_positive = max(0, len(false_positive_items) - false_positive_limit)
    for event, video_path, _ in false_positive_items[:overflow_false_positive]:
        if _delete_local_video(event, video_path, deleted_at=current_time):
            deleted_false_positive += 1

    kept = [
        item
        for item in kept
        if local_clip_video_path(item[0]) is not None
    ]
    total_limit = max(0, int(settings.local_clip_retention_max_total or 0))
    overflow_total = max(0, len(kept) - total_limit)
    deleted_total_overflow = 0
    for event, video_path, _ in kept[:overflow_total]:
        if _delete_local_video(event, video_path, deleted_at=current_time):
            deleted_total_overflow += 1

    return {
        "deleted_non_false_positive": deleted_non_fp,
        "deleted_false_positive_overflow": deleted_false_positive,
        "deleted_total_overflow": deleted_total_overflow,
    }


__all__ = ["local_clip_video_path", "prune_local_review_clips"]
