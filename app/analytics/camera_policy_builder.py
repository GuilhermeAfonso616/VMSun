"""Derivacao de politicas e configuracoes de runtime a partir de perfis."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from app.analytics.camera_profile_models import (
    CameraAnalyticProfile,
    ThresholdProfile,
    _sensitivity_from_confidence,
    serialize_roi_polygon,
)
from app.analytics_v2.config.schema import (
    AnalyticsConfig,
    DirectionalLine,
    RuleConfig,
    SceneConfig,
    SceneZone,
    ScoringConfig,
    TrackingConfig,
)


@dataclass(slots=True)
class DerivedCameraPolicy:
    profile: CameraAnalyticProfile
    config: AnalyticsConfig
    effective_goal: str
    primary_intrusion_sensor: bool
    roi_required: bool
    ignore_zones_required: bool
    full_frame_forbidden: bool
    schedule_required: bool
    direction_required: bool
    subzones_required: bool
    specialized_pipeline: str | None = None
    disabled_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    rule_plan: list[str] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def _serialize(item: Any) -> Any:
            if is_dataclass(item):
                return asdict(item)
            return deepcopy(item)

        payload = {
            "profile": self.profile.to_dict(),
            "effective_goal": self.effective_goal,
            "primary_intrusion_sensor": self.primary_intrusion_sensor,
            "roi_required": self.roi_required,
            "ignore_zones_required": self.ignore_zones_required,
            "full_frame_forbidden": self.full_frame_forbidden,
            "schedule_required": self.schedule_required,
            "direction_required": self.direction_required,
            "subzones_required": self.subzones_required,
            "specialized_pipeline": self.specialized_pipeline,
            "disabled_reason": self.disabled_reason,
            "notes": list(self.notes),
            "rule_plan": list(self.rule_plan),
            "preview": deepcopy(self.preview),
            "config": {
                "tracking": asdict(self.config.tracking),
                "scene": {
                    "restricted_zones": [asdict(zone) for zone in self.config.scene.restricted_zones],
                    "exclusion_zones": [asdict(zone) for zone in self.config.scene.exclusion_zones],
                    "buffer_zones": [asdict(zone) for zone in self.config.scene.buffer_zones],
                    "directional_lines": [asdict(line) for line in self.config.scene.directional_lines],
                    "perspective_profile": [_serialize(band) for band in self.config.scene.perspective_profile],
                    "border_margin_ratio": self.config.scene.border_margin_ratio,
                    "min_bbox_aspect_ratio": self.config.scene.min_bbox_aspect_ratio,
                    "max_bbox_aspect_ratio": self.config.scene.max_bbox_aspect_ratio,
                },
                "rules": {
                    rule_id: asdict(rule_config)
                    for rule_id, rule_config in self.config.rules.items()
                },
                "scoring": asdict(self.config.scoring),
            },
        }
        payload.update(deepcopy(self.preview))
        return payload


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _polygon_from_points(points: list[dict[str, Any]] | None) -> list[tuple[float, float]]:
    polygon: list[tuple[float, float]] = []
    for point in points or []:
        try:
            polygon.append((float(point["x"]), float(point["y"])))
        except Exception:
            continue
    return polygon


def _normalize_zone_payload(item: dict[str, Any]) -> dict[str, Any]:
    polygon: list[dict[str, float]] = []
    for point in item.get("polygon", []) or []:
        try:
            polygon.append({"x": float(point[0]), "y": float(point[1])})
        except Exception:
            continue
    return {
        "zone_id": str(item.get("zone_id") or item.get("id") or item.get("name") or "zone"),
        "name": str(item.get("name") or item.get("zone_id") or "Zone"),
        "polygon": polygon,
        "zone_type": str(item.get("zone_type") or "restricted"),
        "enabled": bool(item.get("enabled", True)),
    }


def _normalize_line_payload(item: dict[str, Any]) -> dict[str, Any]:
    start = item.get("start") or [item.get("x1"), item.get("y1")]
    end = item.get("end") or [item.get("x2"), item.get("y2")]
    return {
        "line_id": str(item.get("line_id") or item.get("id") or item.get("name") or "line"),
        "name": str(item.get("name") or item.get("line_id") or "Line"),
        "start": [float(start[0]), float(start[1])] if start and len(start) >= 2 else [0.0, 0.0],
        "end": [float(end[0]), float(end[1])] if end and len(end) >= 2 else [1.0, 1.0],
        "direction": str(item.get("direction") or "any"),
        "enabled": bool(item.get("enabled", True)),
    }


def profile_to_legacy_fields(profile: CameraAnalyticProfile | None) -> dict[str, Any]:
    if profile is None:
        return {}

    threshold, _ = _effective_threshold_profile(profile)
    legacy_modes: list[str] = []
    if profile.analytic_goal in {"intrusion", "zone_entry", "zone_presence", "dwell_time"}:
        if profile.roi_polygon or threshold.roi_required:
            legacy_modes.extend(["person_entered_roi", "person_left_roi"])
        else:
            legacy_modes.extend(["person_entered", "person_left"])
    if profile.analytic_goal in {"loitering", "dwell_time", "queue_monitoring"}:
        legacy_modes.append("person_loitering")
    if profile.analytic_goal in {"line_crossing", "access_control"} or threshold.direction_required:
        legacy_modes.append("line_crossing")

    line = profile.directional_lines[0] if profile.directional_lines else None
    return {
        "roi_name": "ROI" if profile.roi_polygon else None,
        "roi_polygon_json": serialize_roi_polygon(profile.roi_polygon),
        "line_start_x": line["start"][0] if line else None,
        "line_start_y": line["start"][1] if line else None,
        "line_end_x": line["end"][0] if line else None,
        "line_end_y": line["end"][1] if line else None,
        "line_direction": (line.get("direction") if line else None) or "any",
        "human_event_modes_json": __import__("json").dumps(sorted(set(legacy_modes)), ensure_ascii=False),
        "human_loitering_seconds": threshold.dwell_seconds or threshold.alarm_confirmation_seconds or 10.0,
        "human_detection_sensitivity": _sensitivity_from_confidence(threshold.person_confidence_min),
        "analytics_coordinate_space": "source",
    }


def _scene_profile_defaults(profile: CameraAnalyticProfile) -> tuple[float, float, float]:
    border_margin_ratio = 0.06
    min_aspect_ratio = 0.22
    max_aspect_ratio = 1.25
    if profile.scene_profile in {"perimeter_outdoor", "gate_access", "parking", "yard", "wide_area"}:
        border_margin_ratio = 0.08
    if profile.scene_profile in {"high_criticality", "harsh_environment"}:
        border_margin_ratio = 0.09
    if profile.nuisance_profile.camera_vibration or profile.nuisance_profile.strong_shadows:
        border_margin_ratio = max(border_margin_ratio, 0.08)
    if profile.camera_family in {"fisheye", "panoramic", "multisensor"}:
        min_aspect_ratio = 0.18
        max_aspect_ratio = 1.40
    return border_margin_ratio, min_aspect_ratio, max_aspect_ratio


def _effective_threshold_profile(profile: CameraAnalyticProfile) -> tuple[ThresholdProfile, list[str]]:
    effective = deepcopy(profile.threshold_profile)
    notes: list[str] = []

    if profile.nuisance_profile.vegetation_wind:
        effective.ignore_zones_required = True
        effective.track_persistence_frames = max(effective.track_persistence_frames, 4)
        effective.alarm_confirmation_seconds = max(effective.alarm_confirmation_seconds, 1.5)
        effective.cooldown_seconds = max(effective.cooldown_seconds, 12.0)
        effective.min_box_area_pct = max(effective.min_box_area_pct, 0.003)
        effective.min_box_height_pct = max(effective.min_box_height_pct, 0.025)
        notes.append("vegetation_wind_prefers_ignore_zones_and_moderate_confirmation")

    if profile.scene_profile == "high_criticality":
        effective.person_confidence_min = max(effective.person_confidence_min, 0.60)
        effective.track_persistence_frames = max(1, effective.track_persistence_frames + 2)
        effective.alarm_confirmation_seconds = max(effective.alarm_confirmation_seconds, 2.0)
        effective.cooldown_seconds = max(effective.cooldown_seconds, 20.0)
        effective.roi_required = True
        effective.ignore_zones_required = True
        effective.full_frame_forbidden = True
        effective.schedule_required = True
        notes.append("high_criticality_raises_confirmation_and_zone_requirements")

    if profile.scene_profile == "indoor_restricted":
        effective.person_confidence_min = max(effective.person_confidence_min, 0.55)
        effective.track_persistence_frames = max(effective.track_persistence_frames, 5)
        effective.alarm_confirmation_seconds = max(effective.alarm_confirmation_seconds, 2.5)
        effective.cooldown_seconds = max(effective.cooldown_seconds, 15.0)
        effective.schedule_required = True
        effective.roi_required = True
        effective.full_frame_forbidden = True
        notes.append("indoor_restricted_is_schedule_gated")

    if profile.scene_profile in {"perimeter_outdoor", "gate_access", "parking", "yard", "wide_area"}:
        effective.person_confidence_min = max(effective.person_confidence_min, 0.45)
        effective.track_persistence_frames = max(effective.track_persistence_frames, 4)
        effective.alarm_confirmation_seconds = max(effective.alarm_confirmation_seconds, 1.2)
        effective.cooldown_seconds = max(effective.cooldown_seconds, 12.0)
        effective.roi_required = True

    if profile.camera_family in {"ptz", "speed_dome"}:
        effective.person_confidence_min = max(effective.person_confidence_min, 0.55)
        effective.track_persistence_frames = max(effective.track_persistence_frames, 4)
        notes.append("ptz_prefers_verification_thresholds")

    if profile.camera_family in {"fisheye", "panoramic", "multisensor"}:
        effective.person_confidence_min = max(effective.person_confidence_min, 0.50)
        effective.track_persistence_frames = max(effective.track_persistence_frames, 5)
        effective.roi_required = True
        effective.full_frame_forbidden = True
        notes.append("multi_view_cameras_require_sectorized_thresholds")

    if profile.camera_family == "thermal":
        effective.person_confidence_min = max(0.30, effective.person_confidence_min)
        effective.alarm_confirmation_seconds = max(effective.alarm_confirmation_seconds, 1.2)

    if profile.camera_family == "lpr" or profile.analytic_goal == "vehicle_plate_read":
        effective.person_confidence_min = 0.0
        effective.track_persistence_frames = 0
        effective.alarm_confirmation_seconds = 0.0
        effective.cooldown_seconds = 0.0
        effective.roi_required = True
        effective.full_frame_forbidden = True

    return effective, notes


def _band_from_thresholds(profile: CameraAnalyticProfile, frame_width: int, frame_height: int) -> list[dict[str, Any]]:
    min_height_pct = max(0.0, float(profile.threshold_profile.min_box_height_pct))
    min_area_pct = max(0.0, float(profile.threshold_profile.min_box_area_pct))
    if min_height_pct <= 0.0 and min_area_pct <= 0.0:
        return []

    frame_area = max(1.0, float(frame_width) * float(frame_height))
    return [
        {
            "y_min": 0.0,
            "y_max": 1.0,
            "min_bbox_height": min_height_pct * float(frame_height),
            "min_bbox_area": min_area_pct * frame_area,
        }
    ]


def _base_tracking_config(profile: CameraAnalyticProfile) -> TrackingConfig:
    threshold, _ = _effective_threshold_profile(profile)
    track_persistence = max(1, int(threshold.track_persistence_frames))
    person_confidence = _clamp01(float(threshold.person_confidence_min))

    max_shadow_age = max(10, track_persistence * 2)
    if profile.nuisance_profile.vegetation_wind or profile.nuisance_profile.crowd_occlusion:
        max_shadow_age = max(max_shadow_age, track_persistence * 3)
    if profile.nuisance_profile.low_texture_scene:
        max_match_distance_px = 140.0
    else:
        max_match_distance_px = 120.0

    return TrackingConfig(
        det_threshold_candidate=max(0.18, person_confidence - 0.18),
        det_threshold_confirm=person_confidence,
        min_frames_to_confirm=track_persistence,
        max_shadow_age_frames=max_shadow_age,
        iou_match_threshold=0.35 if profile.camera_family not in {"fisheye", "panoramic"} else 0.28,
        reid_similarity_threshold=0.75,
        probation_max_lost_frames=max(2, track_persistence // 2),
        weak_det_threshold=max(0.15, person_confidence - 0.20),
        max_track_history=60,
        max_match_distance_px=max_match_distance_px,
        max_shadow_recovery_distance_px=max_match_distance_px + 60.0,
        track_quality_min_confirm=max(0.50, person_confidence + 0.08),
        track_quality_min_event=max(0.55, person_confidence + 0.12),
        track_quality_smoothing=0.70,
        min_motion_px_for_confirm=7.0 if not profile.nuisance_profile.vegetation_wind else 9.0,
        min_motion_px_for_event=5.0 if not profile.nuisance_profile.vegetation_wind else 7.0,
        min_bbox_aspect_ratio=0.18 if profile.camera_family in {"fisheye", "panoramic"} else 0.22,
        max_bbox_aspect_ratio=1.40 if profile.camera_family in {"fisheye", "panoramic"} else 1.25,
        border_margin_ratio=_scene_profile_defaults(profile)[0],
        border_penalty_strength=0.45,
    )


def _base_scoring_config(profile: CameraAnalyticProfile) -> ScoringConfig:
    scoring = ScoringConfig(
        class_consistency=0.18,
        track_stability=0.22,
        temporal_persistence=0.18,
        size_plausibility=0.16,
        motion_plausibility=0.14,
        zone_confidence=0.08,
        direction_confidence=0.04,
    )
    if profile.nuisance_profile.vegetation_wind or profile.nuisance_profile.crowd_occlusion:
        scoring.temporal_persistence = 0.26
        scoring.motion_plausibility = 0.16
        scoring.zone_confidence = 0.10
    if profile.risk_profile in {"sterile_zone", "critical_asset"}:
        scoring.track_stability = 0.25
        scoring.temporal_persistence = max(scoring.temporal_persistence, 0.20)
        scoring.direction_confidence = 0.12
    if profile.camera_family in {"ptz", "speed_dome"}:
        scoring.direction_confidence = max(scoring.direction_confidence, 0.15)
    if profile.camera_family in {"fisheye", "panoramic", "multisensor"}:
        scoring.zone_confidence = max(scoring.zone_confidence, 0.14)
    return scoring


def _goal_to_rules(profile: CameraAnalyticProfile, *, frame_width: int, frame_height: int) -> tuple[list[RuleConfig], list[str], bool, bool, bool, bool, bool, str | None]:
    threshold, extra_notes = _effective_threshold_profile(profile)
    is_legacy_default = profile.preset_name == "legacy_default"
    roi_required = bool(threshold.roi_required)
    ignore_required = bool(threshold.ignore_zones_required)
    full_frame_forbidden = bool(threshold.full_frame_forbidden)
    schedule_required = bool(threshold.schedule_required)
    direction_required = bool(threshold.direction_required)
    subzones_required = profile.camera_family in {"fisheye", "panoramic", "multisensor"}
    primary_intrusion_sensor = True
    specialized_pipeline: str | None = None
    notes: list[str] = []
    rule_plan: list[str] = []
    rules: list[RuleConfig] = []

    if profile.nuisance_profile.vegetation_wind:
        roi_required = True
        ignore_required = True
        full_frame_forbidden = True
        schedule_required = schedule_required or profile.scene_profile == "perimeter_outdoor"
        direction_required = True
        notes.append("vegetation_wind_forces_roi_ignore_zones_and_temporal_confirmation")

    if profile.scene_profile in {"perimeter_outdoor", "gate_access", "parking", "yard", "wide_area"}:
        roi_required = True
        direction_required = True if profile.analytic_goal in {"intrusion", "line_crossing", "access_control"} else direction_required
        ignore_required = True if profile.nuisance_profile.vegetation_wind or profile.scene_profile in {"perimeter_outdoor", "gate_access", "parking"} else ignore_required
        full_frame_forbidden = True if profile.scene_profile != "active_monitoring" else full_frame_forbidden

    if profile.scene_profile in {"indoor_discreet", "reception", "office", "elevator"} and not is_legacy_default:
        roi_required = True
        full_frame_forbidden = True
        notes.append("indoor_scene_treats_presence_as_context_not_intrusion")

    if profile.scene_profile == "indoor_restricted" and not is_legacy_default:
        roi_required = True
        schedule_required = True
        full_frame_forbidden = True
        notes.append("indoor_restricted_requires_schedule_and_zone_context")

    if profile.scene_profile == "high_criticality" and not is_legacy_default:
        roi_required = True
        ignore_required = True
        schedule_required = True
        full_frame_forbidden = True
        notes.append("high_criticality_uses_conservative_confirmation")

    if profile.scene_category == "perimetral":
        roi_required = True if profile.analytic_goal != "people_count" else roi_required
        ignore_required = True if profile.nuisance_profile.vegetation_wind or profile.scene_profile in {"perimeter_outdoor", "gate_access", "parking", "yard", "wide_area"} else ignore_required
        full_frame_forbidden = True
        notes.append("perimetral_scene_prioritizes_roi_and_ignore_zones")

    if profile.scene_category == "interno":
        if profile.analytic_goal in {"zone_presence", "intrusion", "loitering", "dwell_time"}:
            roi_required = True
        notes.append("indoor_scene_relies_on_context_and_subzones")

    if profile.target_focus == "objeto":
        notes.append("target_focus_object_prefers_object_specific_zones")
    elif profile.target_focus == "placa":
        notes.append("target_focus_plate_prefers_specialized_vehicle_context")

    if profile.camera_family in {"ptz", "speed_dome"}:
        primary_intrusion_sensor = False
        notes.append("ptz_family_not_primary_intrusion_sensor_by_default")
        if profile.analytic_goal != "tracking_verification":
            notes.append("ptz_prefers_tracking_verification_or_response_mode")

    if profile.camera_family in {"fisheye", "panoramic"}:
        subzones_required = True
        full_frame_forbidden = True
        roi_required = True
        notes.append("fisheye_panoramic_require_subzones_or_view_areas")

    if profile.camera_family == "multisensor":
        subzones_required = True
        roi_required = True
        full_frame_forbidden = True
        notes.append("multisensor_requires_sectorized_context")

    if profile.camera_family == "lpr" or profile.analytic_goal == "vehicle_plate_read":
        specialized_pipeline = "lpr"
        primary_intrusion_sensor = False
        roi_required = True
        full_frame_forbidden = True
        rules = []
        rule_plan = ["specialized_lpr_pipeline"]
        notes.append("lpr_uses_vehicle_plate_pipeline_not_person_intrusion")
        return rules, rule_plan, primary_intrusion_sensor, roi_required, ignore_required, full_frame_forbidden, schedule_required, specialized_pipeline

    if profile.analytic_goal in {"people_count", "queue_monitoring"}:
        rule_plan.append("analytics_only")
        if profile.analytic_goal == "queue_monitoring":
            rules.append(
                RuleConfig(
                    rule_id="queue_monitoring_default",
                    enabled=True,
                    rule_type="loitering",
                    target_class="person",
                    require_confirmed_track=True,
                    min_track_age_frames=max(3, threshold.track_persistence_frames + 1),
                    min_visible_frames=max(3, threshold.track_persistence_frames + 1),
                    min_dwell_ms=int(max(0.5, threshold.dwell_seconds or threshold.alarm_confirmation_seconds) * 1000.0),
                    min_event_score=0.55,
                    cooldown_seconds=max(5.0, threshold.cooldown_seconds),
                    min_track_quality=0.45,
                    min_class_consistency=0.45,
                    min_zone_persistence_frames=max(1, threshold.track_persistence_frames - 1),
                )
            )
        return rules, rule_plan, primary_intrusion_sensor, roi_required, ignore_required, full_frame_forbidden, schedule_required, specialized_pipeline

    if profile.analytic_goal in {"loitering", "dwell_time"}:
        rule_plan.append("loitering")
        rules.append(
            RuleConfig(
                rule_id="loitering_default",
                enabled=True,
                rule_type="loitering",
                target_class="person",
                require_confirmed_track=True,
                min_track_age_frames=max(3, threshold.track_persistence_frames + 1),
                min_visible_frames=max(3, threshold.track_persistence_frames + 1),
                min_dwell_ms=int(max(threshold.dwell_seconds, threshold.alarm_confirmation_seconds) * 1000.0),
                min_event_score=0.60,
                cooldown_seconds=max(5.0, threshold.cooldown_seconds),
                min_track_quality=max(0.50, threshold.person_confidence_min + 0.08),
                min_class_consistency=0.50,
                min_zone_persistence_frames=max(1, threshold.track_persistence_frames - 1),
            )
        )

    if profile.analytic_goal in {"line_crossing", "access_control"} or threshold.direction_required:
        if profile.directional_lines or profile.analytic_goal in {"line_crossing", "access_control"}:
            rule_plan.append("line_crossing")
            rules.append(
                RuleConfig(
                    rule_id="line_crossing_default",
                    enabled=True,
                    rule_type="line_crossing",
                    target_class="person",
                    require_confirmed_track=True,
                    min_track_age_frames=max(2, threshold.track_persistence_frames - 1),
                    min_visible_frames=max(2, threshold.track_persistence_frames - 1),
                    min_dwell_ms=0,
                    allowed_direction=(profile.directional_lines[0].get("direction") if profile.directional_lines else None),
                    prohibited_direction=None,
                    min_event_score=0.65 if profile.risk_profile != "critical_asset" else 0.70,
                    cooldown_seconds=max(8.0, threshold.cooldown_seconds),
                    min_track_quality=max(0.50, threshold.person_confidence_min + 0.05),
                    min_class_consistency=0.50,
                    min_zone_persistence_frames=max(1, threshold.track_persistence_frames - 2),
                )
            )

    if profile.analytic_goal in {"intrusion", "zone_entry", "zone_presence"}:
        rule_plan.append("intrusion_zone")
        rules.append(
            RuleConfig(
                rule_id="intrusion_default",
                enabled=True,
                rule_type="intrusion_zone",
                target_class="person",
                require_confirmed_track=True,
                min_track_age_frames=max(2, threshold.track_persistence_frames),
                min_visible_frames=max(2, threshold.track_persistence_frames),
                min_dwell_ms=int(max(threshold.dwell_seconds, threshold.alarm_confirmation_seconds) * 1000.0),
                min_event_score=0.70 if profile.risk_profile in {"sterile_zone", "critical_asset"} else 0.60,
                cooldown_seconds=max(10.0, threshold.cooldown_seconds),
                exclusion_zones=[],
                roi_required=roi_required,
                ignore_zones_required=ignore_required,
                full_frame_forbidden=full_frame_forbidden,
                schedule=None,
                hysteresis_enter=0.75,
                hysteresis_exit=0.55,
                min_track_quality=max(0.45, threshold.person_confidence_min + 0.05),
                min_class_consistency=0.45,
                min_motion_plausibility=0.20,
                min_zone_persistence_frames=max(1, threshold.track_persistence_frames),
                min_motion_distance_px=3.0 if not profile.nuisance_profile.vegetation_wind else 4.0,
                min_geometry_confidence=0.35,
                block_near_border=True,
                max_border_penalty=0.45,
                min_aspect_ratio=0.18 if profile.camera_family in {"fisheye", "panoramic"} else 0.22,
                max_aspect_ratio=1.40 if profile.camera_family in {"fisheye", "panoramic"} else 1.25,
            )
        )

    if profile.analytic_goal == "tracking_verification":
        primary_intrusion_sensor = False
        rule_plan.append("tracking_verification")
        if profile.directional_lines:
            rules.append(
                RuleConfig(
                    rule_id="tracking_verification_default",
                    enabled=True,
                    rule_type="directional_violation",
                    target_class="person",
                    require_confirmed_track=True,
                    min_track_age_frames=max(2, threshold.track_persistence_frames),
                    min_visible_frames=max(2, threshold.track_persistence_frames),
                    min_dwell_ms=0,
                    min_event_score=0.55,
                    cooldown_seconds=max(5.0, threshold.cooldown_seconds),
                    min_track_quality=max(0.50, threshold.person_confidence_min + 0.05),
                    min_class_consistency=0.50,
                    min_motion_plausibility=0.20,
                    min_zone_persistence_frames=1,
                )
            )
        else:
            notes.append("tracking_verification_without_line_becomes_observation_only")

    if profile.analytic_goal == "vehicle_plate_read":
        specialized_pipeline = "lpr"
        primary_intrusion_sensor = False
        rule_plan = ["specialized_lpr_pipeline"]

    if not rules and profile.analytic_goal not in {"people_count", "queue_monitoring"}:
        if full_frame_forbidden:
            notes.append("no_full_frame_geometry_available_rule_remains_conservative")
        else:
            rule_plan.append("fallback_intrusion_zone")
            rules.append(
                RuleConfig(
                    rule_id="intrusion_default",
                    enabled=True,
                    rule_type="intrusion_zone",
                    target_class="person",
                    require_confirmed_track=True,
                    min_track_age_frames=max(2, threshold.track_persistence_frames),
                    min_visible_frames=max(2, threshold.track_persistence_frames),
                    min_dwell_ms=int(max(threshold.dwell_seconds, threshold.alarm_confirmation_seconds) * 1000.0),
                    min_event_score=0.60,
                    cooldown_seconds=max(10.0, threshold.cooldown_seconds),
                    roi_required=roi_required,
                    ignore_zones_required=ignore_required,
                    full_frame_forbidden=full_frame_forbidden,
                    min_track_quality=max(0.45, threshold.person_confidence_min + 0.05),
                    min_class_consistency=0.45,
                    min_zone_persistence_frames=max(1, threshold.track_persistence_frames),
                    min_motion_distance_px=3.0,
                    min_geometry_confidence=0.35,
                    block_near_border=True,
                    max_border_penalty=0.45,
                    min_aspect_ratio=0.22,
                    max_aspect_ratio=1.25,
                )
            )

    notes.extend(extra_notes)
    return rules, rule_plan, primary_intrusion_sensor, roi_required, ignore_required, full_frame_forbidden, schedule_required, specialized_pipeline


def build_analytics_config_from_profile(
    profile: CameraAnalyticProfile,
    *,
    frame_width: int,
    frame_height: int,
) -> tuple[AnalyticsConfig, DerivedCameraPolicy]:
    frame_width = max(1, int(frame_width))
    frame_height = max(1, int(frame_height))

    threshold, threshold_notes = _effective_threshold_profile(profile)
    border_margin_ratio, min_aspect_ratio, max_aspect_ratio = _scene_profile_defaults(profile)
    tracking = _base_tracking_config(profile)
    scoring = _base_scoring_config(profile)
    rules, rule_plan, primary_intrusion_sensor, roi_required, ignore_required, full_frame_forbidden, schedule_required, specialized_pipeline = _goal_to_rules(
        profile,
        frame_width=frame_width,
        frame_height=frame_height,
    )

    restricted_zones: list[SceneZone] = []
    if profile.subzones:
        for idx, zone_raw in enumerate(profile.subzones, start=1):
            zone_payload = _normalize_zone_payload(zone_raw)
            polygon = _polygon_from_points(zone_payload["polygon"])
            if len(polygon) >= 3:
                restricted_zones.append(
                    SceneZone(
                        zone_id=str(zone_payload["zone_id"] or f"subzone_{idx}"),
                        name=str(zone_payload["name"] or f"Subzona {idx}"),
                        polygon=polygon,
                        zone_type=str(zone_payload["zone_type"] or "restricted"),
                        enabled=bool(zone_payload.get("enabled", True)),
                    )
                )
    elif profile.roi_polygon and len(profile.roi_polygon) >= 3:
        restricted_zones.append(
            SceneZone(
                zone_id="roi_1",
                name=profile.preset_name or "ROI 1",
                polygon=_polygon_from_points(profile.roi_polygon),
                zone_type="roi",
                enabled=True,
            )
        )
    elif not full_frame_forbidden:
        restricted_zones.append(
            SceneZone(
                zone_id="scene_full",
                name="Cena inteira",
                polygon=[(0.0, 0.0), (float(frame_width), 0.0), (float(frame_width), float(frame_height)), (0.0, float(frame_height))],
                zone_type="scene",
                enabled=True,
            )
        )

    exclusion_zones: list[SceneZone] = []
    for idx, zone_raw in enumerate(profile.ignore_zones, start=1):
        zone_payload = _normalize_zone_payload(zone_raw)
        polygon = _polygon_from_points(zone_payload["polygon"])
        if len(polygon) < 3:
            continue
        exclusion_zones.append(
            SceneZone(
                zone_id=str(zone_payload["zone_id"] or f"ignore_{idx}"),
                name=str(zone_payload["name"] or f"Ignorar {idx}"),
                polygon=polygon,
                zone_type="exclusion",
                enabled=bool(zone_payload.get("enabled", True)),
            )
        )

    if profile.nuisance_profile.vegetation_wind and not exclusion_zones:
        notes = ["vegetation_wind_requires_manual_ignore_zones"]
    else:
        notes = []

    directional_lines: list[DirectionalLine] = []
    for idx, line_raw in enumerate(profile.directional_lines, start=1):
        line_payload = _normalize_line_payload(line_raw)
        start = line_payload["start"]
        end = line_payload["end"]
        directional_lines.append(
            DirectionalLine(
                line_id=str(line_payload["line_id"] or f"line_{idx}"),
                name=str(line_payload["name"] or f"Linha {idx}"),
                start=(float(start[0]), float(start[1])),
                end=(float(end[0]), float(end[1])),
                direction=str(line_payload.get("direction") or "any"),
                enabled=bool(line_payload.get("enabled", True)),
            )
        )

    if not directional_lines and profile.analytic_goal in {"line_crossing", "access_control"} and profile.threshold_profile.direction_required:
        directional_lines.append(
            DirectionalLine(
                line_id="line_1",
                name="Linha 1",
                start=(0.0, float(frame_height) * 0.5),
                end=(float(frame_width), float(frame_height) * 0.5),
                direction="any",
                enabled=True,
            )
        )

    scene = SceneConfig(
        restricted_zones=restricted_zones,
        exclusion_zones=exclusion_zones,
        buffer_zones=[],
        directional_lines=directional_lines,
        perspective_profile=_band_from_thresholds(profile, frame_width, frame_height),
        border_margin_ratio=border_margin_ratio,
        min_bbox_aspect_ratio=min_aspect_ratio,
        max_bbox_aspect_ratio=max_aspect_ratio,
    )

    config = AnalyticsConfig(tracking=tracking, scene=scene, rules={rule.rule_id: rule for rule in rules}, scoring=scoring)

    summary = {
        "camera_family": profile.camera_family,
        "scene_category": profile.scene_category,
        "scene_profile": profile.scene_profile,
        "target_focus": profile.target_focus,
        "analytic_goal": profile.analytic_goal,
        "risk_profile": profile.risk_profile,
        "preset_name": profile.preset_name,
        "primary_intrusion_sensor": primary_intrusion_sensor,
        "roi_required": roi_required,
        "ignore_zones_required": ignore_required,
        "full_frame_forbidden": full_frame_forbidden,
        "schedule_required": schedule_required,
        "direction_required": threshold.direction_required,
        "subzones_required": profile.camera_family in {"fisheye", "panoramic", "multisensor"},
        "specialized_pipeline": specialized_pipeline,
        "notes": list(profile.notes) + notes,
        "nuisance_flags": profile.nuisance_profile.enabled_flags(),
        "thresholds": asdict(threshold),
        "rule_plan": list(rule_plan),
        "scene_counts": {
            "restricted_zones": len(restricted_zones),
            "exclusion_zones": len(exclusion_zones),
            "directional_lines": len(directional_lines),
        },
    }
    summary["notes"].extend(threshold_notes)

    derived = DerivedCameraPolicy(
        profile=profile,
        config=config,
        effective_goal=profile.analytic_goal,
        primary_intrusion_sensor=primary_intrusion_sensor,
        roi_required=roi_required,
        ignore_zones_required=ignore_required,
        full_frame_forbidden=full_frame_forbidden,
        schedule_required=schedule_required,
        direction_required=threshold.direction_required,
        subzones_required=profile.camera_family in {"fisheye", "panoramic", "multisensor"},
        specialized_pipeline=specialized_pipeline,
        disabled_reason=None,
        notes=summary["notes"],
        rule_plan=rule_plan,
        preview=summary,
    )
    return config, derived


def build_profile_preview(profile: CameraAnalyticProfile, *, frame_width: int | None = None, frame_height: int | None = None) -> dict[str, Any]:
    width = max(1, int(frame_width or 1920))
    height = max(1, int(frame_height or 1080))
    config, derived = build_analytics_config_from_profile(profile, frame_width=width, frame_height=height)
    return derived.to_dict()
