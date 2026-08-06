"""Formularios web de configuracao operacional, analitica e de movimento."""

from __future__ import annotations

from dataclasses import fields

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.db.models import User
from app.services.camera_configuration_service import (
    AnalyticsConfigurationInput,
    CameraConfigurationError,
    HUMAN_DETECTION_SENSITIVITY_CHOICES,
    MotionConfigurationInput,
    OperationalConfigurationInput,
    parse_human_event_modes,
    reset_motion_config,
    update_extended_operational_config,
    update_motion_config,
    update_web_analytics_config,
)
from app.web.camera_detail_presenter import (
    build_camera_detail_context,
    build_camera_detail_payload,
)
from app.web.presentation_constants import DEFAULT_HUMAN_LOITERING_SECONDS
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()
_AUTHORIZED_ROLES = ["admin", "supervisor"]


def _form_bool(value: str | None) -> bool:
    return value is not None and str(value).lower() in {"1", "true", "on", "yes", "sim"}


def _raise_http_error(exc: CameraConfigurationError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _render_configuration_error(
    *,
    db,
    request: Request,
    camera_id: int,
    error: str | None = None,
    motion_error: str | None = None,
    ops_error: str | None = None,
    analytics_values: AnalyticsConfigurationInput | None = None,
    motion_values: MotionConfigurationInput | None = None,
):
    camera, events = build_camera_detail_payload(db, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Câmera não encontrada")

    if analytics_values is not None:
        camera.roi_polygon_pretty = analytics_values.roi_polygon_json
        camera.analytics_coordinate_space = "source"
        camera.line_start_x = analytics_values.line_start_x or None
        camera.line_start_y = analytics_values.line_start_y or None
        camera.line_end_x = analytics_values.line_end_x or None
        camera.line_end_y = analytics_values.line_end_y or None
        camera.line_direction = (
            analytics_values.line_direction
            if analytics_values.line_direction in {"any", "a_to_b", "b_to_a"}
            else "any"
        )
        camera.human_event_modes = parse_human_event_modes(analytics_values.human_event_modes)
        try:
            camera.human_loitering_seconds = (
                float(analytics_values.human_loitering_seconds)
                if analytics_values.human_loitering_seconds.strip()
                else DEFAULT_HUMAN_LOITERING_SECONDS
            )
        except (TypeError, ValueError):
            camera.human_loitering_seconds = DEFAULT_HUMAN_LOITERING_SECONDS
        camera.human_detection_sensitivity = (
            analytics_values.human_detection_sensitivity
            if analytics_values.human_detection_sensitivity in HUMAN_DETECTION_SENSITIVITY_CHOICES
            else "medium"
        )

    if motion_values is not None:
        camera.motion_config = {
            item.name: getattr(motion_values, item.name)
            for item in fields(motion_values)
        }

    return templates.TemplateResponse(
        request=request,
        name="camera_detail.html",
        context=build_camera_detail_context(
            request=request,
            camera=camera,
            events=events,
            error=error,
            motion_error=motion_error,
            ops_error=ops_error,
        ),
        status_code=400,
    )


@router.post("/cameras/{camera_id}/ops-config")
def update_camera_ops_config(
    request: Request,
    camera_id: int,
    site_name: str = Form(""),
    group_name: str = Form(""),
    camera_priority: str = Form("medium"),
    camera_family: str = Form("dome"),
    scene_category: str = Form("interno"),
    target_focus: str = Form("pessoa"),
    auto_start_enabled: str | None = Form(None),
    alarm_sound_enabled: str | None = Form(None),
    alarm_popup_enabled: str | None = Form(None),
    learning_mode: str = Form("assisted_policy_tuning"),
    auto_tuning_enabled: str | None = Form(None),
    critical_lock: str | None = Form(None),
    max_daily_auto_changes: str = Form("1"),
    min_reviewed_events_for_suggestion: str = Form("12"),
    min_reviewed_events_for_auto_tuning: str = Form("24"),
    rollback_window_hours: str = Form("48"),
    processing_max_width: str = Form(""),
    processing_max_height: str = Form(""),
    processing_upscale_small_frames: str | None = Form(None),
    normal_inference_interval_seconds: str = Form(""),
    capture_drop_frames: str = Form(""),
    visual_raw_publish_interval_seconds: str = Form(""),
    visual_processed_publish_interval_seconds: str = Form(""),
    prefer_motion_test: str | None = Form(None),
    vegetation_wind: str | None = Form(None),
    rain: str | None = Form(None),
    headlights: str | None = Form(None),
    insects_ir: str | None = Form(None),
    strong_shadows: str | None = Form(None),
    glass_reflection: str | None = Form(None),
    camera_vibration: str | None = Form(None),
    low_texture_scene: str | None = Form(None),
    crowd_occlusion: str | None = Form(None),
    fog_or_haze: str | None = Form(None),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user, prefer_motion_test
    values = OperationalConfigurationInput(
        site_name=site_name,
        group_name=group_name,
        camera_priority=camera_priority,
        camera_family=camera_family,
        scene_category=scene_category,
        target_focus=target_focus,
        auto_start_enabled=_form_bool(auto_start_enabled),
        alarm_sound_enabled=_form_bool(alarm_sound_enabled),
        alarm_popup_enabled=_form_bool(alarm_popup_enabled),
        learning_mode=learning_mode,
        auto_tuning_enabled=_form_bool(auto_tuning_enabled),
        critical_lock=_form_bool(critical_lock),
        max_daily_auto_changes=max_daily_auto_changes,
        min_reviewed_events_for_suggestion=min_reviewed_events_for_suggestion,
        min_reviewed_events_for_auto_tuning=min_reviewed_events_for_auto_tuning,
        rollback_window_hours=rollback_window_hours,
        manual_overrides={
            "processing_max_width": processing_max_width,
            "processing_max_height": processing_max_height,
            "processing_upscale_small_frames": (
                None
                if processing_upscale_small_frames is None
                else _form_bool(processing_upscale_small_frames)
            ),
            "normal_inference_interval_seconds": normal_inference_interval_seconds,
            "capture_drop_frames": capture_drop_frames,
            "visual_raw_publish_interval_seconds": visual_raw_publish_interval_seconds,
            "visual_processed_publish_interval_seconds": visual_processed_publish_interval_seconds,
        },
        nuisance_profile={
            "vegetation_wind": _form_bool(vegetation_wind),
            "rain": _form_bool(rain),
            "headlights": _form_bool(headlights),
            "insects_ir": _form_bool(insects_ir),
            "strong_shadows": _form_bool(strong_shadows),
            "glass_reflection": _form_bool(glass_reflection),
            "camera_vibration": _form_bool(camera_vibration),
            "low_texture_scene": _form_bool(low_texture_scene),
            "crowd_occlusion": _form_bool(crowd_occlusion),
            "fog_or_haze": _form_bool(fog_or_haze),
        },
    )
    db = get_scoped_db()
    try:
        try:
            update_extended_operational_config(db, camera_id, values)
        except CameraConfigurationError as exc:
            if exc.status_code != 400:
                _raise_http_error(exc)
            return _render_configuration_error(
                db=db,
                request=request,
                camera_id=camera_id,
                ops_error=exc.detail,
            )
        return RedirectResponse(url=f"/cameras/{camera_id}", status_code=303)
    finally:
        db.close()


@router.post("/cameras/{camera_id}/analytics-config")
def update_camera_analytics_config(
    request: Request,
    camera_id: int,
    roi_polygon_json: str = Form(""),
    line_start_x: str = Form(""),
    line_start_y: str = Form(""),
    line_end_x: str = Form(""),
    line_end_y: str = Form(""),
    line_direction: str = Form("any"),
    human_event_modes: list[str] = Form([]),
    human_loitering_seconds: str = Form(""),
    human_detection_sensitivity: str = Form("medium"),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user
    values = AnalyticsConfigurationInput(
        roi_polygon_json=roi_polygon_json,
        line_start_x=line_start_x,
        line_start_y=line_start_y,
        line_end_x=line_end_x,
        line_end_y=line_end_y,
        line_direction=line_direction,
        human_event_modes=human_event_modes,
        human_loitering_seconds=human_loitering_seconds,
        human_detection_sensitivity=human_detection_sensitivity,
    )
    db = get_scoped_db()
    try:
        try:
            update_web_analytics_config(db, camera_id, values)
        except CameraConfigurationError as exc:
            if exc.status_code != 400:
                _raise_http_error(exc)
            return _render_configuration_error(
                db=db,
                request=request,
                camera_id=camera_id,
                error=exc.detail,
                analytics_values=values,
            )
        return RedirectResponse(url=f"/cameras/{camera_id}", status_code=303)
    finally:
        db.close()


@router.post("/cameras/{camera_id}/motion-config")
def update_camera_motion_config(
    request: Request,
    camera_id: int,
    motion_idle_interval: str = Form(...),
    motion_active_interval: str = Form(...),
    motion_hold_seconds: str = Form(...),
    motion_detection_hold_seconds: str = Form(...),
    motion_min_motion_frames: str = Form(...),
    motion_downscale_width: str = Form(...),
    motion_min_contour_area: str = Form(...),
    motion_ratio_threshold: str = Form(...),
    motion_global_change_ratio_limit: str = Form(...),
    motion_background_alpha: str = Form(...),
    motion_warmup_frames: str = Form(...),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user
    values = MotionConfigurationInput(
        motion_idle_interval=motion_idle_interval,
        motion_active_interval=motion_active_interval,
        motion_hold_seconds=motion_hold_seconds,
        motion_detection_hold_seconds=motion_detection_hold_seconds,
        motion_min_motion_frames=motion_min_motion_frames,
        motion_downscale_width=motion_downscale_width,
        motion_min_contour_area=motion_min_contour_area,
        motion_ratio_threshold=motion_ratio_threshold,
        motion_global_change_ratio_limit=motion_global_change_ratio_limit,
        motion_background_alpha=motion_background_alpha,
        motion_warmup_frames=motion_warmup_frames,
    )
    db = get_scoped_db()
    try:
        try:
            update_motion_config(db, camera_id, values)
        except CameraConfigurationError as exc:
            if exc.status_code != 400:
                _raise_http_error(exc)
            return _render_configuration_error(
                db=db,
                request=request,
                camera_id=camera_id,
                motion_error=exc.detail,
                motion_values=values,
            )
        return RedirectResponse(url=f"/cameras/{camera_id}", status_code=303)
    finally:
        db.close()


@router.post("/cameras/{camera_id}/motion-config/reset")
def reset_camera_motion_config(
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user
    db = get_scoped_db()
    try:
        try:
            reset_motion_config(db, camera_id)
        except CameraConfigurationError as exc:
            _raise_http_error(exc)
        return RedirectResponse(url=f"/cameras/{camera_id}", status_code=303)
    finally:
        db.close()
