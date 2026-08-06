"""Contratos HTTP do dominio de cameras e fontes de video."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NuisanceProfileUpdate(BaseModel):
    vegetation_wind: bool = False
    rain: bool = False
    headlights: bool = False
    insects_ir: bool = False
    strong_shadows: bool = False
    glass_reflection: bool = False
    camera_vibration: bool = False
    low_texture_scene: bool = False
    crowd_occlusion: bool = False
    fog_or_haze: bool = False


class ThresholdProfileUpdate(BaseModel):
    person_confidence_min: float = 0.45
    min_box_area_pct: float = 0.0
    min_box_height_pct: float = 0.0
    track_persistence_frames: int = 3
    alarm_confirmation_seconds: float = 1.0
    dwell_seconds: float = 0.0
    cooldown_seconds: float = 10.0
    direction_required: bool = False
    schedule_required: bool = False
    roi_required: bool = False
    ignore_zones_required: bool = False
    full_frame_forbidden: bool = False


class CameraCreate(BaseModel):
    name: str
    ip: str
    manufacturer: str = Field(min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    onvif_port: int | None = None
    username: str
    password: str
    rtsp_url: str | None = None
    camera_family: str = "dome"
    scene_category: str = "interno"
    target_focus: str = "pessoa"
    nuisance_profile: NuisanceProfileUpdate = Field(default_factory=NuisanceProfileUpdate)
    analytics_profile: Any | None = None

    @field_validator("manufacturer")
    @classmethod
    def validate_manufacturer(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A marca da camera e obrigatoria")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class NvrDiscoverRequest(BaseModel):
    host: str
    username: str = ""
    password: str = ""
    rtsp_port: int = 554
    channel_count: int = 16
    brand: str = "generic"
    provider_type: str = "generic_nvr"
    stream_kinds: list[str] = Field(default_factory=lambda: ["main", "sub"])
    probe: bool = True


class NvrChannelSelection(BaseModel):
    channel: int
    stream_kind: str = "main"
    rtsp_url: str
    name: str | None = None
    provider_type: str | None = None
    source_brand: str | None = None


class NvrCreateChannelsRequest(BaseModel):
    host: str
    username: str = ""
    password: str = ""
    rtsp_port: int = 554
    onvif_port: int | None = None
    base_name: str = "NVR"
    brand: str = "generic"
    provider_type: str = "generic_nvr"
    profiles: list[NvrChannelSelection] = Field(default_factory=list)
    camera_family: str = "dome"
    scene_category: str = "interno"
    target_focus: str = "pessoa"
    nuisance_profile: NuisanceProfileUpdate = Field(default_factory=NuisanceProfileUpdate)
    analytics_profile: Any | None = None


class CameraLearningPolicyUpdate(BaseModel):
    learning_mode: str = "assisted_policy_tuning"
    auto_tuning_enabled: bool = False
    critical_lock: bool = False
    max_daily_auto_changes: int = 1
    min_reviewed_events_for_suggestion: int = 12
    min_reviewed_events_for_auto_tuning: int = 24
    rollback_window_hours: int = 48


class CameraAnalyticsProfileUpdate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    preset_name: str | None = None
    camera_family: str = "dome"
    scene_category: str = "interno"
    scene_profile: str = "indoor_discreet"
    target_focus: str = "pessoa"
    analytic_goal: str = "zone_presence"
    nuisance_profile: NuisanceProfileUpdate = Field(default_factory=NuisanceProfileUpdate)
    risk_profile: str = "mixed_traffic"
    threshold_profile: ThresholdProfileUpdate = Field(default_factory=ThresholdProfileUpdate)
    roi_polygon: list[dict[str, float]] = Field(default_factory=list)
    ignore_zones: list[dict[str, Any]] = Field(default_factory=list)
    subzones: list[dict[str, Any]] = Field(default_factory=list)
    directional_lines: list[dict[str, Any]] = Field(default_factory=list)
    schedule: dict[str, Any] | None = None
    manual_overrides: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class CameraAnalyticsConfigUpdate(BaseModel):
    roi_name: str | None = None
    roi_polygon_json: str | None = None
    line_start_x: float | None = None
    line_start_y: float | None = None
    line_end_x: float | None = None
    line_end_y: float | None = None
    line_direction: str | None = "any"


class CameraOpsConfigUpdate(BaseModel):
    manufacturer: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    site_name: str | None = None
    group_name: str | None = None
    camera_priority: str | None = "medium"
    auto_start_enabled: bool = False
    alarm_sound_enabled: bool = True
    alarm_popup_enabled: bool = True

    @field_validator("manufacturer")
    @classmethod
    def validate_manufacturer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("A marca da camera e obrigatoria")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None
