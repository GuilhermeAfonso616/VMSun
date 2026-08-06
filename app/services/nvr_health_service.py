"""Coleta compartilhada do estado operacional de canais NVR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Camera
from app.db.query_helpers import get_active_cameras_query
from app.services.camera_registry import registry
from app.services.runtime_client import get_runtime_health_snapshot, remote_runtime_enabled


@dataclass(frozen=True, slots=True)
class NvrChannelHealthEntry:
    camera: Camera
    runtime_health: dict[str, Any]
    health_status: str
    worker_registered: bool
    worker_mode: str


def collect_nvr_channel_health(db: Session) -> list[NvrChannelHealthEntry]:
    cameras = (
        get_active_cameras_query(db)
        .filter(Camera.source_type == "nvr_channel")
        .order_by(Camera.ip.asc(), Camera.source_channel.asc(), Camera.source_stream_kind.asc(), Camera.id.asc())
        .all()
    )
    snapshot = get_runtime_health_snapshot()
    health_by_id = {item["camera_id"]: item for item in snapshot.get("cameras", [])}
    is_remote_runtime = remote_runtime_enabled()

    entries: list[NvrChannelHealthEntry] = []
    for camera in cameras:
        runtime_health = health_by_id.get(camera.id, {})
        status = str(runtime_health.get("health_status") or camera.status or "idle")
        worker = None if is_remote_runtime else registry.get_worker(camera.id)
        entries.append(
            NvrChannelHealthEntry(
                camera=camera,
                runtime_health=runtime_health,
                health_status="running" if status == "running_motion_test" else status,
                worker_registered=(
                    bool(runtime_health.get("is_running"))
                    if is_remote_runtime
                    else worker is not None
                ),
                worker_mode=(
                    str(runtime_health.get("worker_mode") or "").strip()
                    or (str(getattr(worker, "mode_name", "normal")) if worker else "stopped")
                ),
            )
        )
    return entries
