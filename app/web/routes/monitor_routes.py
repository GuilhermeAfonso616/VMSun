"""Rotas HTTP do monitor operacional, mosaicos, WebRTC e tracking."""

import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.db.models import Camera, User
from app.services.device_session_service import DeviceCapabilityError, DeviceSessionError
from app.services.monitor_ptz_service import (
    discover_and_persist_ptz,
    get_camera_stream_dimensions,
    goto_ptz_preset,
    list_ptz_presets,
    load_ptz_profile_map,
    move_ptz,
    position_ptz_3d,
    prepare_ptz_inspection,
    stop_ptz,
)
from app.services.webrtc_gateway_client import (
    camera_webrtc_path_name,
    get_webrtc_path_diagnostics,
    webrtc_gateway_is_enabled,
    webrtc_public_base_url,
)
from app.web.camera_detail_presenter import DEFAULT_MONITOR_BOX_MIN_CONFIDENCE
from app.web.infrastructure import get_scoped_db, require_web_auth, templates
from app.web.monitor_presenter import (
    _cached_monitor_json_response,
    _monitor_cache_filter_key,
    build_monitor_alarm_payload,
    build_monitor_library_payload,
    build_monitor_live_payload,
    build_monitor_shell_context,
    build_monitor_tracks_payload,
    clamp_monitor_grid,
    serialize_monitor_camera,
    serialize_monitor_event,
    serialize_webrtc_camera,
)
from app.web.presentation_constants import PRIORITY_CHOICES


from app.services.role_permissions_service import is_action_allowed

router = APIRouter()
MONITOR_PTZ_ROLES = ["admin", "supervisor", "operator", "viewer", "dev"]


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class MonitorPtzMovePayload(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    tilt: float = Field(default=0.0, ge=-1.0, le=1.0)
    zoom: float = Field(default=0.0, ge=-1.0, le=1.0)


class MonitorPtzPresetPayload(BaseModel):
    preset_token: str = Field(min_length=1, max_length=200)


class MonitorPtz3dPayload(BaseModel):
    x_start: int = Field(ge=0, le=255)
    y_start: int = Field(ge=0, le=255)
    x_end: int = Field(ge=0, le=255)
    y_end: int = Field(ge=0, le=255)


def _load_monitor_camera(camera_id: int) -> Camera | None:
    db = get_scoped_db()
    try:
        return (
            db.query(Camera)
            .filter(Camera.id == camera_id, Camera.is_deleted == False)  # noqa: E712
            .first()
        )
    finally:
        db.close()


def _monitor_ptz_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, DeviceCapabilityError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if isinstance(exc, DeviceSessionError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    return JSONResponse({"ok": False, "error": f"Erro inesperado: {exc}"}, status_code=500)


def _serialize_monitor_cameras(cameras: list[Camera]) -> list[dict]:
    profile_map = load_ptz_profile_map(cameras)
    return [
        serialize_monitor_camera(camera, ptz_profile=profile_map.get(int(camera.id)))
        for camera in cameras
    ]


def _state_datetime_iso(request: Request, name: str) -> str:
    value = getattr(request.state, name, None)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _comparable_datetime(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event_after_queue_session_start(event, session_started_at) -> bool:
    if not isinstance(session_started_at, datetime):
        return True
    created_at = getattr(event, "created_at", None)
    if not isinstance(created_at, datetime):
        return True
    return _comparable_datetime(created_at) > _comparable_datetime(session_started_at)


def _normalize_filters(
    camera_priority: str | None,
    only_running: str | None,
    only_alarm: str | None,
) -> tuple[str | None, bool, bool]:
    return (
        camera_priority if camera_priority in PRIORITY_CHOICES else None,
        _as_bool(only_running),
        _as_bool(only_alarm),
    )


@router.get("/mosaicos")
def web_mosaicos(
    request: Request,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator", "viewer", "dev"])
    ),
):
    return templates.TemplateResponse(
        request=request,
        name="mosaicos.html",
        context={
            "request": request,
            "can_share_mosaics": current_user.role == "admin",
            "mosaic_storage_user_id": current_user.id,
        },
    )


@router.get("/monitor")
def monitor_page(
    request: Request,
    site_name: str | None = None,
    group_name: str | None = None,
    camera_priority: str | None = None,
    only_running: str | None = None,
    only_alarm: str | None = None,
    grid: int = 4,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator", "viewer", "dev"])
    ),
):
    normalized_priority, normalized_only_running, normalized_only_alarm = _normalize_filters(
        camera_priority,
        only_running,
        only_alarm,
    )
    db = get_scoped_db()
    try:
        context = build_monitor_shell_context(
            db,
            site_name=site_name,
            group_name=group_name,
            camera_priority=normalized_priority,
            only_running=normalized_only_running,
            only_alarm=normalized_only_alarm,
            grid=grid,
        )
        context["available_cameras_payload"] = []
        context["visible_cameras_payload"] = []
        context["panel_alarms_payload"] = []
        context["latest_popup_alarm_payload"] = None
        effective_role = getattr(request.state, "effective_role", current_user.role)
        queue_session_key = str(getattr(request.state, "session_id", "") or "")
        queue_session_started_at = _state_datetime_iso(request, "session_created_at")
        context.update(
            {
                "request": request,
                "webrtc_monitor_enabled": bool(
                    settings.webrtc_gateway_monitor_enabled and webrtc_gateway_is_enabled()
                ),
                "webrtc_public_base_url": webrtc_public_base_url(),
                "monitor_dev_mode": current_user.role == "dev",
                "monitor_can_share_mosaics": effective_role in {"admin", "dev"},
                "monitor_can_control_camera": is_action_allowed(effective_role, "control_ptz"),

                "mosaic_storage_user_id": current_user.id,
                "monitor_queue_session_key": queue_session_key,
                "monitor_queue_session_started_at": queue_session_started_at,
                "web_track_transport_mode": str(
                    settings.web_track_transport_mode or "polling"
                ),
                "web_track_sse_camera_ids": str(
                    settings.web_track_sse_camera_ids or ""
                ),
                "visual_track_fresh_ms": max(
                    100, int(settings.visual_track_fresh_ms or 500)
                ),
                "visual_track_retention_ms": max(
                    100, int(settings.visual_track_retention_ms or 3000)
                ),
            }
        )

        return templates.TemplateResponse(
            request=request,
            name="monitor_vms_new.html",
            context=context,
        )
    finally:
        db.close()


@router.get("/monitor/webrtc")
def monitor_webrtc_page(
    request: Request,
    grid: int = 8,
    current_user: User = Depends(require_web_auth(["dev"])),
):
    selected_grid = clamp_monitor_grid(grid)
    db = get_scoped_db()
    try:
        context = build_monitor_live_payload(db=db, grid=selected_grid)
        cameras = context["visible_cameras"][:selected_grid]
        context.update(
            {
                "request": request,
                "selected_grid": selected_grid,
                "webrtc_enabled": webrtc_gateway_is_enabled(),
                "webrtc_public_base_url": webrtc_public_base_url(),
                "webrtc_cameras_payload": [
                    serialize_webrtc_camera(camera) for camera in cameras
                ],
            }
        )
        return templates.TemplateResponse(
            request=request,
            name="monitor_webrtc.html",
            context=context,
        )
    finally:
        db.close()


@router.get("/monitor/webrtc/diagnostics")
def monitor_webrtc_diagnostics(camera_ids: str = ""):
    ids: set[int] = set()
    for token in str(camera_ids or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ids.add(int(token))
        except ValueError:
            continue

    db = get_scoped_db()
    try:
        query = db.query(Camera).order_by(Camera.id.asc())
        if ids:
            query = query.filter(Camera.id.in_(ids))
        cameras = query.all()
        return JSONResponse(
            {
                "ok": True,
                "enabled": webrtc_gateway_is_enabled(),
                "public_base_url": webrtc_public_base_url(),
                "items": [
                    {
                        "id": camera.id,
                        "name": camera.name,
                        "path": camera_webrtc_path_name(camera.id),
                        "diagnostics": get_webrtc_path_diagnostics(
                            camera_webrtc_path_name(camera.id)
                        ),
                    }
                    for camera in cameras
                ],
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            }
        )
    finally:
        db.close()


@router.get("/monitor/cameras/{camera_id}/ptz/inspect")
def monitor_camera_ptz_inspect(
    camera_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    force: bool = False,
    current_user: User = Depends(require_web_auth(MONITOR_PTZ_ROLES)),
):
    effective_role = getattr(request.state, "effective_role", current_user.role)
    if not is_action_allowed(effective_role, "control_ptz"):
        return JSONResponse({"ok": False, "error": "Acesso negado: seu perfil nao possui permissao para controlar PTZ."}, status_code=403)
    camera = _load_monitor_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        if force:
            payload = discover_and_persist_ptz(camera_id, current_user.id)
            return JSONResponse({"ok": True, **payload}, status_code=200)
        payload, should_probe = prepare_ptz_inspection(camera_id, force=False)
    except Exception as exc:
        return _monitor_ptz_error_response(exc)
    if should_probe:
        background_tasks.add_task(discover_and_persist_ptz, camera_id, current_user.id)
    return JSONResponse({"ok": True, **payload}, status_code=202 if should_probe else 200)


@router.post("/monitor/cameras/{camera_id}/ptz/move")
def monitor_camera_ptz_move(
    camera_id: int,
    request: Request,
    payload: MonitorPtzMovePayload,
    current_user: User = Depends(require_web_auth(MONITOR_PTZ_ROLES)),
):
    effective_role = getattr(request.state, "effective_role", current_user.role)
    if not is_action_allowed(effective_role, "control_ptz"):
        return JSONResponse({"ok": False, "error": "Acesso negado: seu perfil nao possui permissao para controlar PTZ."}, status_code=403)
    camera = _load_monitor_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        result = move_ptz(
            camera,
            owner_id=current_user.id,
            pan=payload.pan,
            tilt=payload.tilt,
            zoom=payload.zoom,
        )
    except Exception as exc:
        return _monitor_ptz_error_response(exc)
    return JSONResponse({"ok": True, **result})


@router.post("/monitor/cameras/{camera_id}/ptz/stop")
def monitor_camera_ptz_stop(
    camera_id: int,
    request: Request,
    current_user: User = Depends(require_web_auth(MONITOR_PTZ_ROLES)),
):
    effective_role = getattr(request.state, "effective_role", current_user.role)
    if not is_action_allowed(effective_role, "control_ptz"):
        return JSONResponse({"ok": False, "error": "Acesso negado: seu perfil nao possui permissao para controlar PTZ."}, status_code=403)
    camera = _load_monitor_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        result = stop_ptz(camera, owner_id=current_user.id)
    except Exception as exc:
        return _monitor_ptz_error_response(exc)
    return JSONResponse({"ok": True, **result})


@router.post("/monitor/cameras/{camera_id}/ptz/3d")
def monitor_camera_ptz_3d(
    camera_id: int,
    request: Request,
    payload: MonitorPtz3dPayload,
    current_user: User = Depends(require_web_auth(MONITOR_PTZ_ROLES)),
):
    effective_role = getattr(request.state, "effective_role", current_user.role)
    if not is_action_allowed(effective_role, "control_ptz"):
        return JSONResponse({"ok": False, "error": "Acesso negado: seu perfil nao possui permissao para controlar PTZ."}, status_code=403)
    camera = _load_monitor_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        result = position_ptz_3d(camera, owner_id=current_user.id, **payload.model_dump())
    except Exception as exc:
        return _monitor_ptz_error_response(exc)
    return JSONResponse({"ok": True, **result})


@router.get("/monitor/cameras/{camera_id}/ptz/3d/dimensoes")
def monitor_camera_ptz_3d_dimensions(
    camera_id: int,
    request: Request,
    current_user: User = Depends(require_web_auth(MONITOR_PTZ_ROLES)),
):
    """Largura/altura reais do fluxo exibido no tile, para o overlay do PTZ 3D
    excluir a area de letterbox conforme a proporcao principal da camera."""
    effective_role = getattr(request.state, "effective_role", current_user.role)
    if not is_action_allowed(effective_role, "control_ptz"):
        return JSONResponse({"ok": False, "error": "Acesso negado: seu perfil nao possui permissao para controlar PTZ."}, status_code=403)
    camera = _load_monitor_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        width, height = get_camera_stream_dimensions(camera, owner_id=current_user.id)
    except Exception as exc:
        return _monitor_ptz_error_response(exc)
    return JSONResponse({"ok": True, "width": width, "height": height})


@router.get("/monitor/cameras/{camera_id}/ptz/presets")
def monitor_camera_ptz_presets(
    camera_id: int,
    request: Request,
    current_user: User = Depends(require_web_auth(MONITOR_PTZ_ROLES)),
):
    effective_role = getattr(request.state, "effective_role", current_user.role)
    if not is_action_allowed(effective_role, "control_ptz"):
        return JSONResponse({"ok": False, "error": "Acesso negado: seu perfil nao possui permissao para controlar PTZ."}, status_code=403)
    camera = _load_monitor_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        presets = list_ptz_presets(camera, owner_id=current_user.id)
    except Exception as exc:
        return _monitor_ptz_error_response(exc)
    return JSONResponse({"ok": True, "presets": presets})


@router.post("/monitor/cameras/{camera_id}/ptz/presets/goto")
def monitor_camera_ptz_goto_preset(
    camera_id: int,
    request: Request,
    payload: MonitorPtzPresetPayload,
    current_user: User = Depends(require_web_auth(MONITOR_PTZ_ROLES)),
):
    effective_role = getattr(request.state, "effective_role", current_user.role)
    if not is_action_allowed(effective_role, "control_ptz"):
        return JSONResponse({"ok": False, "error": "Acesso negado: seu perfil nao possui permissao para controlar PTZ."}, status_code=403)
    camera = _load_monitor_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        result = goto_ptz_preset(
            camera,
            owner_id=current_user.id,
            preset_token=payload.preset_token,
        )
    except Exception as exc:

        return _monitor_ptz_error_response(exc)
    return JSONResponse({"ok": True, **result})


@router.get("/monitor/data")
def monitor_data(
    site_name: str | None = None,
    group_name: str | None = None,
    camera_priority: str | None = None,
    only_running: str | None = None,
    only_alarm: str | None = None,
    grid: int = 4,
):
    normalized_priority, normalized_only_running, normalized_only_alarm = _normalize_filters(
        camera_priority,
        only_running,
        only_alarm,
    )
    normalized_grid = clamp_monitor_grid(grid)
    cache_key = (
        "monitor_data",
        *_monitor_cache_filter_key(
            site_name,
            group_name,
            normalized_priority,
            normalized_only_running,
            normalized_only_alarm,
            normalized_grid,
        ),
    )

    def build_response_payload():
        db = get_scoped_db()
        try:
            payload = build_monitor_live_payload(
                db=db,
                site_name=site_name,
                group_name=group_name,
                camera_priority=normalized_priority,
                only_running=normalized_only_running,
                only_alarm=normalized_only_alarm,
                grid=normalized_grid,
            )
            return {
                "stats": payload["stats"],
                "gateway_health": payload["gateway_health"],
                "cameras": _serialize_monitor_cameras(payload["visible_cameras"]),
            }
        finally:
            db.close()

    return _cached_monitor_json_response(cache_key, build_response_payload)


@router.get("/monitor/library")
def monitor_library_data(
    site_name: str | None = None,
    group_name: str | None = None,
    camera_priority: str | None = None,
    only_running: str | None = None,
    only_alarm: str | None = None,
):
    normalized_priority, normalized_only_running, normalized_only_alarm = _normalize_filters(
        camera_priority,
        only_running,
        only_alarm,
    )
    cache_key = (
        "monitor_library",
        *_monitor_cache_filter_key(
            site_name,
            group_name,
            normalized_priority,
            normalized_only_running,
            normalized_only_alarm,
            4,
        )[:-1],
    )

    def build_response_payload():
        db = get_scoped_db()
        try:
            payload = build_monitor_library_payload(
                db,
                site_name=site_name,
                group_name=group_name,
                camera_priority=normalized_priority,
                only_running=normalized_only_running,
                only_alarm=normalized_only_alarm,
            )
            return {
                "gateway_health": payload["gateway_health"],
                "available_cameras": _serialize_monitor_cameras(payload["available_cameras"]),
            }
        finally:
            db.close()

    return _cached_monitor_json_response(
        cache_key,
        build_response_payload,
        ttl_seconds=settings.monitor_library_cache_ttl_seconds,
    )


@router.get("/monitor/alarms")
def monitor_alarm_queue_data(
    request: Request,
    site_name: str | None = None,
    group_name: str | None = None,
    camera_priority: str | None = None,
    only_running: str | None = None,
    only_alarm: str | None = None,
    grid: int = 4,
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "operator", "viewer", "dev"])
    ),
):
    normalized_priority, normalized_only_running, normalized_only_alarm = _normalize_filters(
        camera_priority,
        only_running,
        only_alarm,
    )
    normalized_grid = clamp_monitor_grid(grid)
    queue_session_key = str(getattr(request.state, "session_id", "") or "")
    queue_session_started_at = _state_datetime_iso(request, "session_created_at")
    cache_key = (
        "monitor_alarms",
        "user",
        int(current_user.id),
        "session",
        queue_session_key,
        *_monitor_cache_filter_key(
            site_name,
            group_name,
            normalized_priority,
            normalized_only_running,
            normalized_only_alarm,
            normalized_grid,
        ),
    )

    def build_response_payload():
        db = get_scoped_db()
        try:
            payload = build_monitor_alarm_payload(
                db,
                site_name=site_name,
                group_name=group_name,
                camera_priority=normalized_priority,
                only_running=normalized_only_running,
                only_alarm=normalized_only_alarm,
            )
            session_started_at = getattr(request.state, "session_created_at", None)
            panel_alarms = [
                event
                for event in payload["panel_alarms"]
                if _event_after_queue_session_start(event, session_started_at)
            ]
            latest_popup_alarm = payload["latest_popup_alarm"]
            if latest_popup_alarm and not _event_after_queue_session_start(
                latest_popup_alarm,
                session_started_at,
            ):
                latest_popup_alarm = None
            return {
                "queue_session_key": queue_session_key,
                "queue_session_started_at": queue_session_started_at,
                "alarm_queue_signature": (
                    payload["alarm_queue_signature"] if panel_alarms else ""
                ),
                "latest_alarm_signature": (
                    payload["latest_alarm_signature"] if latest_popup_alarm else ""
                ),
                "alarm_should_play": bool(latest_popup_alarm and payload["alarm_should_play"]),
                "popup_should_show": bool(latest_popup_alarm and payload["popup_should_show"]),
                "latest_popup_alarm": (
                    serialize_monitor_event(latest_popup_alarm)
                    if latest_popup_alarm
                    else None
                ),
                "alarms": [
                    serialize_monitor_event(event)
                    for event in panel_alarms
                ],
            }
        finally:
            db.close()

    return _cached_monitor_json_response(cache_key, build_response_payload)


@router.get("/monitor/tracks")
def monitor_tracks(
    camera_ids: str = "",
    max_age_seconds: float = 1.5,
    min_confidence: float = DEFAULT_MONITOR_BOX_MIN_CONFIDENCE,
):
    db = get_scoped_db()
    try:
        payload = build_monitor_tracks_payload(
            db,
            camera_ids=camera_ids,
            max_age_seconds=max_age_seconds,
            min_confidence=min_confidence,
        )
    finally:
        db.close()
    response = JSONResponse(payload)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@router.get("/monitor/tracks/stream")
async def monitor_tracks_stream(
    request: Request,
    camera_ids: str = "",
    max_age_seconds: float = 1.5,
    min_confidence: float = DEFAULT_MONITOR_BOX_MIN_CONFIDENCE,
    interval_ms: int = 250,
):
    delay_seconds = max(0.02, min(2.0, float(interval_ms or 250) / 1000.0))

    async def generate():
        while not await request.is_disconnected():
            db = get_scoped_db()
            try:
                payload = build_monitor_tracks_payload(
                    db,
                    camera_ids=camera_ids,
                    max_age_seconds=max_age_seconds,
                    min_confidence=min_confidence,
                )
            finally:
                db.close()
            payload["sse_sent_at_ns"] = time.time_ns()
            for item in (payload.get("cameras") or {}).values():
                if isinstance(item, dict):
                    item["sse_sent_at_ns"] = payload["sse_sent_at_ns"]
            yield f"event: tracks\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            await asyncio.sleep(delay_seconds)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/monitor/tracks/diagnostics")
def monitor_tracks_diagnostics(
    camera_ids: str = "",
    current_user: User = Depends(
        require_web_auth(["admin", "supervisor", "dev"])
    ),
):
    db = get_scoped_db()
    try:
        payload = build_monitor_tracks_payload(
            db,
            camera_ids=camera_ids,
            max_age_seconds=5.0,
        )
    finally:
        db.close()

    cameras = {}
    for camera_id, item in (payload.get("cameras") or {}).items():
        item = item if isinstance(item, dict) else {}
        cameras[camera_id] = {
            "camera_id": item.get("camera_id"),
            "generation_id": item.get("generation_id"),
            "frame_id": item.get("frame_id"),
            "capture_clock": item.get("capture_clock") or "unknown",
            "source_frame_captured_at_ns": item.get(
                "source_frame_captured_at_ns"
            ),
            "gateway_received_at_ns": item.get("gateway_received_at_ns"),
            "tracks_published_at_ns": item.get("tracks_published_at_ns"),
            "api_read_at_ns": item.get("api_read_at_ns"),
            "track_publish_to_api_ms": item.get("track_publish_to_api_ms"),
            "track_store_write_ms": item.get("track_store_write_ms"),
            "track_store_read_ms": item.get("track_store_read_ms"),
            "track_store_file_age_ms": item.get("track_store_file_age_ms"),
            "track_store_cache_hit": item.get("track_store_cache_hit"),
            "latency": item.get("latency_diagnostics"),
        }
    return {
        "ok": True,
        "generated_at": payload.get("generated_at"),
        "diagnostics_enabled": bool(settings.box_latency_diagnostics_enabled),
        "diagnostics_camera_ids": str(
            settings.box_latency_diagnostics_camera_ids or ""
        ),
        "visual_fast_path_enabled": bool(settings.visual_fast_path_enabled),
        "visual_fast_path_camera_ids": str(
            settings.visual_fast_path_camera_ids or ""
        ),
        "source_capture_clock_available": False,
        "cameras": cameras,
    }
