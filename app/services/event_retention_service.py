from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil
import threading
import time
from typing import Iterable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timezone import utc_now_naive
from app.db.models import Event, EventFeedback, LockdownDelivery


logger = get_logger("app.event_retention")

_last_prune_ts = 0.0
_prune_lock = threading.Lock()


def _retention_base_dir() -> Path:
    return Path(settings.event_snapshots_dir).resolve()


def _safe_resolve_event_path(value: str | None, *, base_dir: Path) -> Path | None:
    if not value:
        return None
    try:
        path = Path(value).resolve()
        path.relative_to(base_dir)
        return path
    except Exception:
        return None


def _delete_path(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
            return True
        if path.exists():
            path.unlink()
            return True
    except Exception:
        logger.warning(
            "Falha ao apagar evidencia de evento expirada",
            extra={"action": "event_retention_file_delete", "status": "error", "reason": "delete_failed"},
        )
    return False


def _cleanup_empty_parents(paths: Iterable[Path], *, base_dir: Path) -> None:
    for path in paths:
        parent = path.parent
        while parent != base_dir and parent.is_relative_to(base_dir):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def prune_expired_events(
    db: Session,
    *,
    now: datetime | None = None,
    retention_days: int | None = None,
    batch_size: int | None = None,
) -> dict[str, int]:
    if not bool(settings.event_retention_enabled):
        return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}

    days = int(retention_days if retention_days is not None else settings.event_retention_days or 7)
    if days <= 0:
        return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}

    limit = max(1, int(batch_size if batch_size is not None else settings.event_retention_delete_batch_size or 1000))
    current_time = now or utc_now_naive()
    cutoff = current_time - timedelta(days=days)
    base_dir = _retention_base_dir()

    expired_events = (
        db.query(Event)
        .filter(
            or_(
                Event.created_at <= cutoff,
                and_(Event.created_at.is_(None), Event.ended_at <= cutoff),
                and_(Event.created_at.is_(None), Event.ended_at.is_(None), Event.started_at <= cutoff),
            )
        )
        .order_by(Event.created_at.asc(), Event.id.asc())
        .limit(limit)
        .all()
    )
    if not expired_events:
        return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}

    event_ids = [int(event.id) for event in expired_events]
    paths_to_delete: list[Path] = []
    for event in expired_events:
        snapshot_path = _safe_resolve_event_path(getattr(event, "snapshot_path", None), base_dir=base_dir)
        clip_path = _safe_resolve_event_path(getattr(event, "clip_path", None), base_dir=base_dir)
        if snapshot_path is not None:
            paths_to_delete.append(snapshot_path)
        if clip_path is not None:
            paths_to_delete.append(clip_path)

    lockdown_deleted = (
        db.query(LockdownDelivery)
        .filter(LockdownDelivery.event_id.in_(event_ids))
        .delete(synchronize_session=False)
    )
    feedback_deleted = (
        db.query(EventFeedback)
        .filter(EventFeedback.event_id.in_(event_ids))
        .delete(synchronize_session=False)
    )
    events_deleted = db.query(Event).filter(Event.id.in_(event_ids)).delete(synchronize_session=False)
    db.commit()

    files_deleted = 0
    for path in sorted(set(paths_to_delete), key=lambda item: len(str(item)), reverse=True):
        if _delete_path(path):
            files_deleted += 1
    _cleanup_empty_parents(paths_to_delete, base_dir=base_dir)

    logger.info(
        "Eventos expirados removidos retention_days=%s events=%s files=%s",
        days,
        events_deleted,
        files_deleted,
        extra={
            "action": "event_retention_prune",
            "status": "completed",
            "reason": "retention_window_expired",
        },
    )
    return {
        "events_deleted": int(events_deleted or 0),
        "feedback_deleted": int(feedback_deleted or 0),
        "lockdown_deleted": int(lockdown_deleted or 0),
        "files_deleted": int(files_deleted or 0),
    }


def maybe_prune_expired_events(db: Session, *, force: bool = False, now: datetime | None = None) -> dict[str, int]:
    global _last_prune_ts

    if not bool(settings.event_retention_enabled):
        return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}

    interval = max(60.0, float(settings.event_retention_check_interval_seconds or 3600.0))
    current_ts = time.monotonic()
    if not force and current_ts - _last_prune_ts < interval:
        return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}

    if not _prune_lock.acquire(blocking=False):
        return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}
    try:
        if not force and current_ts - _last_prune_ts < interval:
            return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}
        result = prune_expired_events(db, now=now)
        _last_prune_ts = current_ts
        return result
    except Exception:
        db.rollback()
        logger.exception(
            "Falha ao executar retencao de eventos",
            extra={"action": "event_retention_prune", "status": "error", "reason": "unexpected_failure"},
        )
        return {"events_deleted": 0, "feedback_deleted": 0, "lockdown_deleted": 0, "files_deleted": 0}
    finally:
        _prune_lock.release()


__all__ = ["maybe_prune_expired_events", "prune_expired_events"]
