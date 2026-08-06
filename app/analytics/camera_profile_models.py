"""Modelos, presets, persistencia e compatibilidade legada de perfis."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


CAMERA_FAMILIES = [
    "dome",
    "bullet",
    "turret",
    "ptz",
    "speed_dome",
    "fisheye",
    "panoramic",
    "multisensor",
    "box",
    "lpr",
    "thermal",
    "starlight",
]


SCENE_PROFILES = [
    "indoor_discreet",
    "indoor_corridor",
    "indoor_restricted",
    "reception",
    "elevator",
    "office",
    "perimeter_outdoor",
    "gate_access",
    "parking",
    "warehouse",
    "yard",
    "wide_area",
    "active_monitoring",
    "low_light",
    "harsh_environment",
    "high_criticality",
]


SCENE_CATEGORIES = [
    "interno",
    "perimetral",
    "misto",
    "externo_geral",
    "interno_restrito",
]


TARGET_FOCUSES = [
    "pessoa",
    "objeto",
    "veiculo",
    "placa",
    "zona",
    "linha",
]


ANALYTIC_GOALS = [
    "intrusion",
    "line_crossing",
    "zone_entry",
    "zone_presence",
    "loitering",
    "dwell_time",
    "people_count",
    "queue_monitoring",
    "access_control",
    "vehicle_plate_read",
    "tracking_verification",
]


RISK_PROFILES = [
    "sterile_zone",
    "restricted_zone",
    "mixed_traffic",
    "public_flow",
    "critical_asset",
    "after_hours_only",
]


THRESHOLD_DEFAULTS = {
    "person_confidence_min": 0.45,
    "min_box_area_pct": 0.0,
    "min_box_height_pct": 0.0,
    "track_persistence_frames": 3,
    "alarm_confirmation_seconds": 1.0,
    "dwell_seconds": 0.0,
    "cooldown_seconds": 10.0,
    "direction_required": False,
    "schedule_required": False,
    "roi_required": False,
    "ignore_zones_required": False,
    "full_frame_forbidden": False,
}


@dataclass(slots=True)
class NuisanceProfile:
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

    def enabled_flags(self) -> list[str]:
        return [name for name, enabled in asdict(self).items() if enabled]


@dataclass(slots=True)
class ThresholdProfile:
    person_confidence_min: float = THRESHOLD_DEFAULTS["person_confidence_min"]
    min_box_area_pct: float = THRESHOLD_DEFAULTS["min_box_area_pct"]
    min_box_height_pct: float = THRESHOLD_DEFAULTS["min_box_height_pct"]
    track_persistence_frames: int = THRESHOLD_DEFAULTS["track_persistence_frames"]
    alarm_confirmation_seconds: float = THRESHOLD_DEFAULTS["alarm_confirmation_seconds"]
    dwell_seconds: float = THRESHOLD_DEFAULTS["dwell_seconds"]
    cooldown_seconds: float = THRESHOLD_DEFAULTS["cooldown_seconds"]
    direction_required: bool = THRESHOLD_DEFAULTS["direction_required"]
    schedule_required: bool = THRESHOLD_DEFAULTS["schedule_required"]
    roi_required: bool = THRESHOLD_DEFAULTS["roi_required"]
    ignore_zones_required: bool = THRESHOLD_DEFAULTS["ignore_zones_required"]
    full_frame_forbidden: bool = THRESHOLD_DEFAULTS["full_frame_forbidden"]


@dataclass(slots=True)
class CameraAnalyticProfile:
    camera_id: int | None = None
    preset_name: str | None = None
    camera_family: str = "dome"
    scene_category: str = "interno"
    scene_profile: str = "indoor_discreet"
    target_focus: str = "pessoa"
    analytic_goal: str = "zone_presence"
    nuisance_profile: NuisanceProfile = field(default_factory=NuisanceProfile)
    risk_profile: str = "mixed_traffic"
    threshold_profile: ThresholdProfile = field(default_factory=ThresholdProfile)
    roi_polygon: list[dict[str, float]] = field(default_factory=list)
    ignore_zones: list[dict[str, Any]] = field(default_factory=list)
    subzones: list[dict[str, Any]] = field(default_factory=list)
    directional_lines: list[dict[str, Any]] = field(default_factory=list)
    schedule: dict[str, Any] | None = None
    manual_overrides: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["nuisance_profile"] = asdict(self.nuisance_profile)
        payload["threshold_profile"] = asdict(self.threshold_profile)
        return payload


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _merge_dict(base: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base)
    if not overrides:
        return result
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _normalize_family(value: str | None) -> str:
    value = str(value or "dome").strip().lower()
    return value if value in CAMERA_FAMILIES else "dome"


def _normalize_scene(value: str | None) -> str:
    value = str(value or "indoor_discreet").strip().lower()
    return value if value in SCENE_PROFILES else "indoor_discreet"


def _normalize_scene_category(value: str | None) -> str:
    value = str(value or "interno").strip().lower()
    return value if value in SCENE_CATEGORIES else "interno"


def _normalize_target_focus(value: str | None) -> str:
    value = str(value or "pessoa").strip().lower()
    return value if value in TARGET_FOCUSES else "pessoa"


def _normalize_goal(value: str | None) -> str:
    value = str(value or "zone_presence").strip().lower()
    return value if value in ANALYTIC_GOALS else "zone_presence"


def _normalize_risk(value: str | None) -> str:
    value = str(value or "mixed_traffic").strip().lower()
    return value if value in RISK_PROFILES else "mixed_traffic"


def _build_preset_base(preset_name: str) -> CameraAnalyticProfile:
    preset_name = str(preset_name or "legacy_default").strip().lower()
    if preset_name == "perimeter_bullet":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="bullet",
            scene_category="perimetral",
            scene_profile="perimeter_outdoor",
            target_focus="pessoa",
            analytic_goal="intrusion",
            risk_profile="sterile_zone",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.50,
                min_box_area_pct=0.005,
                min_box_height_pct=0.04,
                track_persistence_frames=5,
                alarm_confirmation_seconds=1.5,
                dwell_seconds=0.0,
                cooldown_seconds=15.0,
                direction_required=True,
                schedule_required=False,
                roi_required=True,
                ignore_zones_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "perimeter_thermal":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="thermal",
            scene_category="perimetral",
            scene_profile="perimeter_outdoor",
            target_focus="pessoa",
            analytic_goal="intrusion",
            risk_profile="sterile_zone",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.35,
                min_box_area_pct=0.004,
                min_box_height_pct=0.03,
                track_persistence_frames=6,
                alarm_confirmation_seconds=1.8,
                cooldown_seconds=20.0,
                direction_required=True,
                roi_required=True,
                ignore_zones_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "indoor_dome_discreet":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="dome",
            scene_category="interno",
            scene_profile="indoor_discreet",
            target_focus="pessoa",
            analytic_goal="zone_presence",
            risk_profile="public_flow",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.55,
                min_box_area_pct=0.004,
                min_box_height_pct=0.03,
                track_persistence_frames=4,
                alarm_confirmation_seconds=2.0,
                dwell_seconds=5.0,
                cooldown_seconds=18.0,
                roi_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "indoor_restricted":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="dome",
            scene_category="interno_restrito",
            scene_profile="indoor_restricted",
            target_focus="pessoa",
            analytic_goal="intrusion",
            risk_profile="restricted_zone",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.60,
                min_box_area_pct=0.005,
                min_box_height_pct=0.04,
                track_persistence_frames=6,
                alarm_confirmation_seconds=2.5,
                dwell_seconds=1.0,
                cooldown_seconds=20.0,
                direction_required=True,
                schedule_required=True,
                roi_required=True,
                ignore_zones_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "gate_access":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="bullet",
            scene_category="perimetral",
            scene_profile="gate_access",
            target_focus="pessoa",
            analytic_goal="access_control",
            risk_profile="restricted_zone",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.50,
                min_box_area_pct=0.004,
                min_box_height_pct=0.035,
                track_persistence_frames=5,
                alarm_confirmation_seconds=1.5,
                dwell_seconds=0.0,
                cooldown_seconds=12.0,
                direction_required=True,
                roi_required=True,
                ignore_zones_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "parking_low_light":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="starlight",
            scene_category="externo_geral",
            scene_profile="parking",
            target_focus="veiculo",
            analytic_goal="zone_presence",
            risk_profile="mixed_traffic",
            nuisance_profile=NuisanceProfile(headlights=True, low_texture_scene=True, strong_shadows=True),
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.40,
                min_box_area_pct=0.003,
                min_box_height_pct=0.025,
                track_persistence_frames=5,
                alarm_confirmation_seconds=1.2,
                dwell_seconds=3.0,
                cooldown_seconds=14.0,
                roi_required=True,
                ignore_zones_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "ptz_tracking_support":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="ptz",
            scene_category="misto",
            scene_profile="active_monitoring",
            target_focus="pessoa",
            analytic_goal="tracking_verification",
            risk_profile="public_flow",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.55,
                min_box_area_pct=0.004,
                min_box_height_pct=0.03,
                track_persistence_frames=4,
                alarm_confirmation_seconds=2.5,
                cooldown_seconds=20.0,
                roi_required=False,
                full_frame_forbidden=False,
            ),
        )
    if preset_name == "fisheye_wide_area":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="fisheye",
            scene_category="externo_geral",
            scene_profile="wide_area",
            target_focus="zona",
            analytic_goal="zone_entry",
            risk_profile="mixed_traffic",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.50,
                min_box_area_pct=0.006,
                min_box_height_pct=0.035,
                track_persistence_frames=5,
                alarm_confirmation_seconds=1.5,
                cooldown_seconds=15.0,
                roi_required=True,
                ignore_zones_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "lpr_access_control":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="lpr",
            scene_category="perimetral",
            scene_profile="gate_access",
            target_focus="placa",
            analytic_goal="vehicle_plate_read",
            risk_profile="restricted_zone",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.0,
                min_box_area_pct=0.0,
                min_box_height_pct=0.0,
                track_persistence_frames=0,
                alarm_confirmation_seconds=0.0,
                cooldown_seconds=0.0,
                roi_required=True,
                ignore_zones_required=False,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "multisensor_large_area":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="multisensor",
            scene_category="externo_geral",
            scene_profile="wide_area",
            target_focus="zona",
            analytic_goal="intrusion",
            risk_profile="critical_asset",
            threshold_profile=ThresholdProfile(
                person_confidence_min=0.50,
                min_box_area_pct=0.004,
                min_box_height_pct=0.03,
                track_persistence_frames=5,
                alarm_confirmation_seconds=1.8,
                cooldown_seconds=12.0,
                roi_required=True,
                ignore_zones_required=True,
                full_frame_forbidden=True,
            ),
        )
    if preset_name == "legacy_default":
        return CameraAnalyticProfile(
            preset_name=preset_name,
            camera_family="dome",
            scene_category="interno",
            scene_profile="indoor_discreet",
            target_focus="pessoa",
            analytic_goal="intrusion",
            risk_profile="mixed_traffic",
        )
    return CameraAnalyticProfile(preset_name=preset_name)


def build_camera_analytic_profile(
    *,
    camera_id: int | None = None,
    preset_name: str | None = None,
    camera_family: str | None = None,
    scene_category: str | None = None,
    scene_profile: str | None = None,
    target_focus: str | None = None,
    analytic_goal: str | None = None,
    nuisance_profile: dict[str, Any] | NuisanceProfile | None = None,
    risk_profile: str | None = None,
    threshold_profile: dict[str, Any] | ThresholdProfile | None = None,
    roi_polygon: list[dict[str, float]] | None = None,
    ignore_zones: list[dict[str, Any]] | None = None,
    subzones: list[dict[str, Any]] | None = None,
    directional_lines: list[dict[str, Any]] | None = None,
    schedule: dict[str, Any] | None = None,
    manual_overrides: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> CameraAnalyticProfile:
    base = _build_preset_base(preset_name or "legacy_default")

    if camera_id is not None:
        base.camera_id = camera_id

    if camera_family is not None:
        base.camera_family = _normalize_family(camera_family)
    else:
        base.camera_family = _normalize_family(base.camera_family)

    if scene_category is not None:
        base.scene_category = _normalize_scene_category(scene_category)
    else:
        base.scene_category = _normalize_scene_category(base.scene_category)

    if scene_profile is not None:
        base.scene_profile = _normalize_scene(scene_profile)
    else:
        base.scene_profile = _normalize_scene(base.scene_profile)

    if target_focus is not None:
        base.target_focus = _normalize_target_focus(target_focus)
    else:
        base.target_focus = _normalize_target_focus(base.target_focus)

    if analytic_goal is not None:
        base.analytic_goal = _normalize_goal(analytic_goal)
    else:
        base.analytic_goal = _normalize_goal(base.analytic_goal)

    if risk_profile is not None:
        base.risk_profile = _normalize_risk(risk_profile)
    else:
        base.risk_profile = _normalize_risk(base.risk_profile)

    if isinstance(nuisance_profile, NuisanceProfile):
        base.nuisance_profile = nuisance_profile
    elif isinstance(nuisance_profile, dict):
        merged = asdict(base.nuisance_profile)
        merged.update({key: bool(value) for key, value in nuisance_profile.items() if key in merged})
        base.nuisance_profile = NuisanceProfile(**merged)

    if isinstance(threshold_profile, ThresholdProfile):
        base.threshold_profile = threshold_profile
    elif isinstance(threshold_profile, dict):
        merged = asdict(base.threshold_profile)
        for key, value in threshold_profile.items():
            if key in merged:
                merged[key] = value
        base.threshold_profile = ThresholdProfile(
            person_confidence_min=_as_float(merged["person_confidence_min"], THRESHOLD_DEFAULTS["person_confidence_min"]),
            min_box_area_pct=_as_float(merged["min_box_area_pct"], THRESHOLD_DEFAULTS["min_box_area_pct"]),
            min_box_height_pct=_as_float(merged["min_box_height_pct"], THRESHOLD_DEFAULTS["min_box_height_pct"]),
            track_persistence_frames=_as_int(merged["track_persistence_frames"], THRESHOLD_DEFAULTS["track_persistence_frames"]),
            alarm_confirmation_seconds=_as_float(merged["alarm_confirmation_seconds"], THRESHOLD_DEFAULTS["alarm_confirmation_seconds"]),
            dwell_seconds=_as_float(merged["dwell_seconds"], THRESHOLD_DEFAULTS["dwell_seconds"]),
            cooldown_seconds=_as_float(merged["cooldown_seconds"], THRESHOLD_DEFAULTS["cooldown_seconds"]),
            direction_required=bool(merged["direction_required"]),
            schedule_required=bool(merged["schedule_required"]),
            roi_required=bool(merged["roi_required"]),
            ignore_zones_required=bool(merged["ignore_zones_required"]),
            full_frame_forbidden=bool(merged["full_frame_forbidden"]),
        )

    base.roi_polygon = [dict(point) for point in roi_polygon or base.roi_polygon]
    base.ignore_zones = [dict(item) for item in ignore_zones or base.ignore_zones]
    base.subzones = [dict(item) for item in subzones or base.subzones]
    base.directional_lines = [dict(item) for item in directional_lines or base.directional_lines]
    base.schedule = deepcopy(schedule if schedule is not None else base.schedule)
    base.manual_overrides = deepcopy(manual_overrides or base.manual_overrides)
    base.notes = list(notes or base.notes)
    return base


def profile_from_mapping(data: dict[str, Any] | None) -> CameraAnalyticProfile:
    data = data or {}
    nuisance_raw = data.get("nuisance_profile") or {}
    threshold_raw = data.get("threshold_profile") or {}
    return build_camera_analytic_profile(
        camera_id=data.get("camera_id"),
        preset_name=data.get("preset_name"),
        camera_family=data.get("camera_family"),
        scene_category=data.get("scene_category"),
        scene_profile=data.get("scene_profile"),
        target_focus=data.get("target_focus"),
        analytic_goal=data.get("analytic_goal"),
        nuisance_profile=nuisance_raw,
        risk_profile=data.get("risk_profile"),
        threshold_profile=threshold_raw,
        roi_polygon=data.get("roi_polygon") or [],
        ignore_zones=data.get("ignore_zones") or [],
        subzones=data.get("subzones") or [],
        directional_lines=data.get("directional_lines") or [],
        schedule=data.get("schedule"),
        manual_overrides=data.get("manual_overrides") or {},
        notes=data.get("notes") or [],
    )


def profile_from_camera(camera) -> CameraAnalyticProfile:
    if camera is None:
        return build_camera_analytic_profile(preset_name="legacy_default")

    raw = getattr(camera, "analytics_profile_json", None)
    if raw:
        try:
            import json

            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                profile = profile_from_mapping(parsed)
                profile.camera_id = getattr(camera, "id", None)
                if not profile.roi_polygon and getattr(camera, "roi_polygon_json", None):
                    try:
                        parsed_roi = json.loads(camera.roi_polygon_json)
                        if isinstance(parsed_roi, list):
                            profile.roi_polygon = [dict(point) for point in parsed_roi if isinstance(point, dict)]
                    except Exception:
                        pass
                return profile
        except Exception:
            pass

    roi_polygon = []
    if getattr(camera, "roi_polygon_json", None):
        try:
            import json

            parsed_roi = json.loads(camera.roi_polygon_json)
            if isinstance(parsed_roi, list):
                roi_polygon = [dict(point) for point in parsed_roi if isinstance(point, dict)]
        except Exception:
            roi_polygon = []

    ignore_zones: list[dict[str, Any]] = []
    manual_overrides: dict[str, Any] = {}
    if getattr(camera, "human_event_modes_json", None):
        try:
            import json

            manual_overrides["human_event_modes"] = json.loads(camera.human_event_modes_json)
        except Exception:
            pass

    threshold_profile = ThresholdProfile(
        person_confidence_min=_confidence_from_sensitivity(getattr(camera, "human_detection_sensitivity", None)),
        track_persistence_frames=3,
        alarm_confirmation_seconds=float(getattr(camera, "human_loitering_seconds", 10.0) or 10.0),
        dwell_seconds=float(getattr(camera, "human_loitering_seconds", 10.0) or 10.0),
        roi_required=bool(roi_polygon),
        full_frame_forbidden=False,
    )
    return build_camera_analytic_profile(
        camera_id=getattr(camera, "id", None),
        preset_name="legacy_default",
        camera_family="dome",
        scene_profile="indoor_discreet",
        analytic_goal="intrusion",
        risk_profile="mixed_traffic",
        nuisance_profile={},
        threshold_profile=threshold_profile,
        roi_polygon=roi_polygon,
        ignore_zones=ignore_zones,
        manual_overrides=manual_overrides,
        notes=["legacy_fallback"],
    )


def profile_from_legacy_camera(camera) -> CameraAnalyticProfile:
    """Constrói um perfil novo a partir dos campos legados da camera."""

    base_profile = profile_from_camera(camera) if getattr(camera, "analytics_profile_json", None) else build_camera_analytic_profile(
        camera_id=getattr(camera, "id", None),
        preset_name="legacy_default",
        camera_family="dome",
        scene_profile="indoor_discreet",
        analytic_goal="intrusion",
    )

    roi_polygon = []
    if getattr(camera, "roi_polygon_json", None):
        try:
            import json

            parsed_roi = json.loads(camera.roi_polygon_json)
            if isinstance(parsed_roi, list):
                roi_polygon = [dict(point) for point in parsed_roi if isinstance(point, dict)]
        except Exception:
            roi_polygon = []

    line_values = None
    if None not in (
        getattr(camera, "line_start_x", None),
        getattr(camera, "line_start_y", None),
        getattr(camera, "line_end_x", None),
        getattr(camera, "line_end_y", None),
    ):
        line_values = {
            "line_id": "line_1",
            "name": "Linha 1",
            "start": [float(camera.line_start_x), float(camera.line_start_y)],
            "end": [float(camera.line_end_x), float(camera.line_end_y)],
            "direction": str(getattr(camera, "line_direction", None) or "any"),
            "enabled": True,
        }

    human_modes = []
    if getattr(camera, "human_event_modes_json", None):
        try:
            import json

            parsed_modes = json.loads(camera.human_event_modes_json)
            if isinstance(parsed_modes, list):
                human_modes = [str(item).strip() for item in parsed_modes if str(item).strip()]
        except Exception:
            human_modes = []

    base_profile.roi_polygon = roi_polygon
    base_profile.directional_lines = [line_values] if line_values else []
    base_profile.manual_overrides = {
        **(base_profile.manual_overrides or {}),
        "human_event_modes": human_modes,
    }
    if getattr(camera, "human_loitering_seconds", None) is not None:
        base_profile.threshold_profile.alarm_confirmation_seconds = float(camera.human_loitering_seconds)
        base_profile.threshold_profile.dwell_seconds = float(camera.human_loitering_seconds)
    if getattr(camera, "human_detection_sensitivity", None):
        base_profile.threshold_profile.person_confidence_min = _confidence_from_sensitivity(camera.human_detection_sensitivity)
    base_profile.threshold_profile.roi_required = bool(roi_polygon)
    base_profile.threshold_profile.direction_required = bool(line_values)
    base_profile.threshold_profile.full_frame_forbidden = False if base_profile.preset_name == "legacy_default" else base_profile.threshold_profile.full_frame_forbidden
    return base_profile


def serialize_profile(profile: CameraAnalyticProfile | None) -> str | None:
    if profile is None:
        return None
    import json

    return json.dumps(profile.to_dict(), ensure_ascii=False)


def serialize_roi_polygon(points: list[dict[str, float]] | None) -> str | None:
    if not points:
        return None
    import json

    cleaned = []
    for point in points:
        try:
            cleaned.append({"x": float(point["x"]), "y": float(point["y"])})
        except Exception:
            continue
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def _confidence_from_sensitivity(value: Any) -> float:
    mapping = {
        "very_low": 0.20,
        "low": 0.30,
        "medium": 0.45,
        "high": 0.60,
    }
    return mapping.get(str(value or "medium").strip().lower(), 0.45)


def _sensitivity_from_confidence(value: float) -> str:
    if value <= 0.20:
        return "very_low"
    if value <= 0.30:
        return "low"
    if value <= 0.45:
        return "medium"
    return "high"
