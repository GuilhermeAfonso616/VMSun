"""Stub de compatibilidade para onedrive_reviewed_backfill_service no VMSun."""

def count_reviewed_events_pending_onedrive(*args, **kwargs) -> int:
    return 0

def upload_reviewed_events_pending_onedrive(*args, **kwargs) -> dict:
    return {"ok": True, "processed": 0}
