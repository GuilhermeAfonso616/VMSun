"""Consultas compartilhadas pelo dashboard web."""

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.query_helpers import get_active_cameras_query
from app.services.operational_history_store import operational_history_store
from app.services.resource_history_store import resource_history_store
from app.services.runtime_client import (
    RuntimeClientError,
    get_runtime_health_snapshot,
    remote_runtime_enabled,
    runtime_get,
)


def get_dashboard_camera_counts(db: Session) -> dict[str, int]:
    health_snapshot = get_runtime_health_snapshot()
    return {
        "total_cameras": int(get_active_cameras_query(db).count() or 0),
        "running_cameras": int(health_snapshot.get("running_count", 0) or 0),
    }


def get_operational_history_payload(
    *,
    hours: int,
    bucket_minutes: int,
    camera_id: int | None,
    start: str | None,
    end: str | None,
) -> tuple[dict[str, Any], int]:
    params = {
        "hours": hours,
        "bucket_minutes": bucket_minutes,
        "camera_id": camera_id,
        "start": start,
        "end": end,
    }
    if remote_runtime_enabled():
        try:
            return (
                runtime_get(
                    "/internal/health/operational-history",
                    params=params,
                    timeout=max(2.0, settings.runtime_api_timeout_seconds),
                ),
                200,
            )
        except RuntimeClientError as exc:
            return (
                {
                    "status": "error",
                    "detail": str(exc),
                    "range": {
                        "hours": hours,
                        "bucket_minutes": bucket_minutes,
                    },
                    "cameras": [],
                    "buckets": [],
                },
                503,
            )
    return (
        operational_history_store.query(
            hours=hours,
            bucket_minutes=bucket_minutes,
            camera_id=camera_id,
            start_iso=start,
            end_iso=end,
        ),
        200,
    )


def get_resource_history_payload(
    *,
    hours: int,
    bucket_minutes: int,
    start: str | None,
    end: str | None,
) -> tuple[dict[str, Any], int]:
    params = {
        "hours": hours,
        "bucket_minutes": bucket_minutes,
        "start": start,
        "end": end,
    }
    if remote_runtime_enabled():
        try:
            return (
                runtime_get(
                    "/internal/health/resource-history",
                    params=params,
                    timeout=max(2.0, settings.runtime_api_timeout_seconds),
                ),
                200,
            )
        except RuntimeClientError as exc:
            return (
                {
                    "status": "error",
                    "detail": str(exc),
                    "range": {
                        "hours": hours,
                        "bucket_minutes": bucket_minutes,
                    },
                    "buckets": [],
                    "summary": {"samples": 0, "metrics": {}},
                },
                503,
            )
    return (
        resource_history_store.query(
            hours=hours,
            bucket_minutes=bucket_minutes,
            start_iso=start,
            end_iso=end,
        ),
        200,
    )
