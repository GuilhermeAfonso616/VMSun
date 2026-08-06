"""Construcao coerente de modelos de camera e aplicacao de perfis analiticos."""

from __future__ import annotations

from typing import Any

from app.analytics.camera_profile_models import (
    build_camera_analytic_profile,
    profile_from_mapping,
    serialize_profile,
)
from app.analytics.camera_policy_builder import profile_to_legacy_fields
from app.db.models import Camera


def resolve_initial_profile(
    analytics_profile: Any | None,
    *,
    camera_family: str,
    scene_category: str,
    target_focus: str,
    nuisance_profile: dict[str, Any] | None,
):
    if isinstance(analytics_profile, dict):
        return profile_from_mapping(analytics_profile)
    if analytics_profile is not None and hasattr(analytics_profile, "model_dump"):
        return profile_from_mapping(analytics_profile.model_dump())
    return build_camera_analytic_profile(
        preset_name="legacy_default",
        camera_family=camera_family,
        scene_category=scene_category,
        scene_profile="indoor_discreet",
        target_focus=target_focus,
        analytic_goal="intrusion",
        nuisance_profile=nuisance_profile,
    )


def apply_profile_to_camera(
    camera: Camera,
    profile,
    *,
    coordinate_space_override: str | None = None,
) -> Camera:
    legacy_values = profile_to_legacy_fields(profile)
    camera.analytics_profile_json = serialize_profile(profile)
    camera.roi_name = legacy_values.get("roi_name")
    camera.roi_polygon_json = legacy_values.get("roi_polygon_json")
    camera.line_start_x = legacy_values.get("line_start_x")
    camera.line_start_y = legacy_values.get("line_start_y")
    camera.line_end_x = legacy_values.get("line_end_x")
    camera.line_end_y = legacy_values.get("line_end_y")
    camera.line_direction = legacy_values.get("line_direction")
    camera.analytics_coordinate_space = coordinate_space_override or legacy_values.get(
        "analytics_coordinate_space", "source"
    )
    camera.human_event_modes_json = legacy_values.get("human_event_modes_json")
    camera.human_loitering_seconds = legacy_values.get("human_loitering_seconds")
    camera.human_detection_sensitivity = legacy_values.get("human_detection_sensitivity")
    return camera


def build_camera_model(
    *,
    name: str,
    ip: str,
    onvif_port: int,
    username: str,
    password: str,
    rtsp_url: str | None,
    manufacturer: str = "Nao informada",
    model: str | None = None,
    camera_family: str = "dome",
    scene_category: str = "interno",
    target_focus: str = "pessoa",
    nuisance_profile: dict[str, Any] | None = None,
    analytics_profile: Any | None = None,
    coordinate_space_override: str | None = None,
) -> Camera:
    profile = resolve_initial_profile(
        analytics_profile,
        camera_family=camera_family,
        scene_category=scene_category,
        target_focus=target_focus,
        nuisance_profile=nuisance_profile,
    )
    camera = Camera(
        name=name,
        ip=ip,
        manufacturer=str(manufacturer or "").strip() or "Nao informada",
        model=str(model or "").strip() or None,
        onvif_port=onvif_port,
        username=username,
        password=password,
        rtsp_url=rtsp_url,
        status="idle",
        camera_priority="medium",
        auto_start_enabled=False,
        alarm_sound_enabled=True,
        alarm_popup_enabled=True,
        learning_mode="assisted_policy_tuning",
        auto_tuning_enabled=False,
        critical_lock=False,
        max_daily_auto_changes=1,
        min_reviewed_events_for_suggestion=12,
        min_reviewed_events_for_auto_tuning=24,
        rollback_window_hours=48,
    )
    return apply_profile_to_camera(
        camera,
        profile,
        coordinate_space_override=coordinate_space_override,
    )
