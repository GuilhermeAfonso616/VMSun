"""Composicao do bootstrap e persistencia de telemetria do operador."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.build_info import build_info_payload
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Camera
from app.db.query_helpers import get_active_cameras_query
from app.services.onedrive_client import onedrive_client
from app.services.runtime_client import get_runtime_health_snapshot
from app.services.webrtc_gateway_client import (
    build_webrtc_player_url,
    build_webrtc_rtsp_url,
    camera_webrtc_path_name,
    register_webrtc_camera_path,
    webrtc_gateway_is_enabled,
    webrtc_public_base_url,
    webrtc_rtsp_public_base_url,
)


logger = get_logger("app.operator")


def camera_operator_status(camera: Camera) -> str:
    status = str(camera.status or "idle").strip().lower()
    return "running" if status == "running_motion_test" else status or "idle"


def operator_registration(camera: Camera, *, register_paths: bool) -> dict[str, Any]:
    path_name = camera_webrtc_path_name(camera.id)
    if not webrtc_gateway_is_enabled():
        return {"ok": False, "path": path_name, "reason": "webrtc_gateway_disabled"}
    if not camera.rtsp_url:
        return {"ok": False, "path": path_name, "reason": "missing_rtsp_url"}
    if not register_paths:
        return {"ok": True, "path": path_name, "reason": "not_registered_by_request"}
    return register_webrtc_camera_path(camera.id, camera.rtsp_url)


def build_operator_bootstrap(
    db: Session,
    *,
    request_hostname: str | None,
    register_paths: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    cameras = (
        get_active_cameras_query(db)
        .order_by(Camera.site_name.asc(), Camera.group_name.asc(), Camera.name.asc(), Camera.id.asc())
        .all()
    )
    health_snapshot = get_runtime_health_snapshot()
    health_by_id = {item.get("camera_id"): item for item in health_snapshot.get("cameras", [])}
    rtsp_public_base = webrtc_rtsp_public_base_url()
    if not rtsp_public_base and request_hostname:
        rtsp_public_base = f"rtsp://{request_hostname}:8554"

    payload_cameras = []
    for camera in cameras:
        path_name = camera_webrtc_path_name(camera.id)
        registration = operator_registration(camera, register_paths=register_paths)
        registration_ok = bool(registration.get("ok"))
        media_rtsp_url = ""
        if registration_ok:
            media_rtsp_url = build_webrtc_rtsp_url(path_name)
            if not media_rtsp_url and rtsp_public_base:
                media_rtsp_url = f"{rtsp_public_base.rstrip('/')}/{path_name}"
        health = health_by_id.get(camera.id, {})
        payload_cameras.append(
            {
                "id": camera.id,
                "name": camera.name,
                "site_name": camera.site_name,
                "group_name": camera.group_name,
                "priority": camera.camera_priority or "medium",
                "status": camera_operator_status(camera),
                "source_type": camera.source_type,
                "source_channel": camera.source_channel,
                "source_stream_kind": camera.source_stream_kind or "main",
                "webrtc_path": path_name,
                "webrtc_player_url": build_webrtc_player_url(path_name),
                "media_rtsp_url": media_rtsp_url,
                "processed_stream_url": f"/cameras/{camera.id}/stream/processed",
                "boxed_stream_url": f"/cameras/{camera.id}/stream/boxed",
                "raw_stream_url": f"/cameras/{camera.id}/stream/raw",
                "monitor_stream_url": f"/monitor/gateway/cameras/{camera.id}/stream/live",
                "stream_url_available": bool(media_rtsp_url),
                "registration_ok": registration_ok,
                "registration_reason": registration.get("reason") or registration.get("error"),
                "health_status": health.get("health_status"),
                "is_running": bool(health.get("is_running")),
                "last_frame_at": health.get("last_frame_at"),
                "last_metrics_at": health.get("last_metrics_at"),
                "gateway_state": health.get("gateway_state"),
            }
        )

    current_time = now or datetime.now(timezone.utc)
    return {
        **build_info_payload(),
        "server_time_utc": current_time.isoformat(),
        "webrtc_enabled": webrtc_gateway_is_enabled(),
        "webrtc_public_base_url": webrtc_public_base_url(),
        "rtsp_public_base_url": rtsp_public_base,
        "camera_count": len(payload_cameras),
        "cameras": payload_cameras,
    }


def operator_performance_filename(payload: dict[str, Any], *, fallback_time: datetime | None = None) -> str:
    captured_raw = str(payload.get("captured_at_utc") or "")
    try:
        captured_at = datetime.fromisoformat(captured_raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        captured_at = fallback_time or datetime.now(timezone.utc)

    hardware = payload.get("hardware") if isinstance(payload.get("hardware"), dict) else {}
    machine = str(payload.get("machine_name") or hardware.get("machine_name") or "unknown_pc")
    clean_machine = re.sub(r"[^A-Za-z0-9_.-]+", "_", machine).strip("._")[:48] or "unknown_pc"
    stamp = captured_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"operator_perf_{clean_machine}_{stamp}.json"


def store_operator_performance(
    payload: dict[str, Any],
    *,
    client_host: str | None,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    current_time = received_at or datetime.now(timezone.utc)
    stored_payload = dict(payload)
    stored_payload.setdefault("received_at_utc", current_time.isoformat())
    if client_host is not None:
        stored_payload.setdefault("client_host", client_host)

    filename = operator_performance_filename(stored_payload, fallback_time=current_time)
    output_dir = Path(settings.runtime_state_dir) / "operator_performance"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_text(
        json.dumps(stored_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    remote: dict[str, Any] | None = None
    remote_error: str | None = None
    archive_enabled = onedrive_client.enabled()
    if archive_enabled:
        try:
            remote = onedrive_client.upload_operator_performance_log(
                filename=filename,
                payload=stored_payload,
            )
        except Exception as exc:
            remote_error = str(exc)
            logger.warning("Falha ao enviar log de performance do operador para OneDrive: %s", exc)

    return {
        "ok": True,
        "filename": filename,
        "stored_path": str(output_path),
        "onedrive_enabled": archive_enabled,
        "onedrive": remote,
        "onedrive_error": remote_error,
    }
