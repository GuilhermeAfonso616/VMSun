"""Casos de uso para perfis e configuracao operacional de cameras."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import (
    CAMERA_FAMILIES,
    SCENE_CATEGORIES,
    TARGET_FOCUSES,
    profile_from_camera,
    profile_from_legacy_camera,
    profile_from_mapping,
    serialize_profile,
)
from app.db.models import Camera
from app.services.camera_factory import apply_profile_to_camera


CAMERA_PRIORITY_CHOICES = frozenset({"low", "medium", "high", "critical"})
LEARNING_MODE_CHOICES = frozenset(
    {
        "manual_only",
        "assisted_policy_tuning",
        "bounded_auto_tuning",
        "offline_active_learning_for_detector",
    }
)
DEFAULT_LEARNING_MODE = "assisted_policy_tuning"
DEFAULT_HUMAN_LOITERING_SECONDS = 10.0
HUMAN_EVENT_MODE_CHOICES = [
    "person_entered",
    "person_left",
    "person_entered_roi",
    "person_left_roi",
    "person_loitering",
    "line_crossing",
]
HUMAN_DETECTION_SENSITIVITY_CHOICES = ["very_low", "low", "medium", "high"]
MOTION_CONFIG_FIELDS = (
    "motion_idle_interval",
    "motion_active_interval",
    "motion_hold_seconds",
    "motion_detection_hold_seconds",
    "motion_min_motion_frames",
    "motion_downscale_width",
    "motion_min_contour_area",
    "motion_ratio_threshold",
    "motion_global_change_ratio_limit",
    "motion_background_alpha",
    "motion_warmup_frames",
)


@dataclass(frozen=True)
class CameraConfigurationError(Exception):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class OperationalConfigurationInput:
    site_name: str
    group_name: str
    camera_priority: str
    camera_family: str
    scene_category: str
    target_focus: str
    auto_start_enabled: bool
    alarm_sound_enabled: bool
    alarm_popup_enabled: bool
    learning_mode: str
    auto_tuning_enabled: bool
    critical_lock: bool
    max_daily_auto_changes: str
    min_reviewed_events_for_suggestion: str
    min_reviewed_events_for_auto_tuning: str
    rollback_window_hours: str
    manual_overrides: dict[str, Any]
    nuisance_profile: dict[str, bool]


@dataclass(frozen=True, slots=True)
class AnalyticsConfigurationInput:
    roi_polygon_json: str
    line_start_x: str
    line_start_y: str
    line_end_x: str
    line_end_y: str
    line_direction: str
    human_event_modes: list[str]
    human_loitering_seconds: str
    human_detection_sensitivity: str


@dataclass(frozen=True, slots=True)
class MotionConfigurationInput:
    motion_idle_interval: str
    motion_active_interval: str
    motion_hold_seconds: str
    motion_detection_hold_seconds: str
    motion_min_motion_frames: str
    motion_downscale_width: str
    motion_min_contour_area: str
    motion_ratio_threshold: str
    motion_global_change_ratio_limit: str
    motion_background_alpha: str
    motion_warmup_frames: str


def _get_camera(db: Session, camera_id: int) -> Camera:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera is None:
        raise CameraConfigurationError(404, "Câmera não encontrada")
    return camera


def get_camera_profile(db: Session, camera_id: int):
    return profile_from_camera(_get_camera(db, camera_id))


def update_camera_profile(db: Session, camera_id: int, profile_payload: dict[str, Any]):
    camera = _get_camera(db, camera_id)
    profile = profile_from_mapping(profile_payload)
    profile.camera_id = camera_id
    apply_profile_to_camera(camera, profile)
    db.commit()
    return camera, profile


def update_legacy_analytics_config(
    db: Session,
    camera_id: int,
    *,
    roi_name: str | None,
    roi_polygon_json: str | None,
    line_start_x: float | None,
    line_start_y: float | None,
    line_end_x: float | None,
    line_end_y: float | None,
    line_direction: str | None,
) -> Camera:
    camera = _get_camera(db, camera_id)
    camera.roi_name = roi_name
    camera.roi_polygon_json = roi_polygon_json
    camera.line_start_x = line_start_x
    camera.line_start_y = line_start_y
    camera.line_end_x = line_end_x
    camera.line_end_y = line_end_y
    camera.line_direction = line_direction or "any"
    camera.analytics_profile_json = serialize_profile(profile_from_legacy_camera(camera))
    db.commit()
    return camera


def update_operational_config(
    db: Session,
    camera_id: int,
    *,
    manufacturer: str | None = None,
    model: str | None = None,
    site_name: str | None,
    group_name: str | None,
    camera_priority: str | None,
    auto_start_enabled: bool,
    alarm_sound_enabled: bool,
    alarm_popup_enabled: bool,
) -> Camera:
    camera = _get_camera(db, camera_id)
    normalized_manufacturer = str(manufacturer if manufacturer is not None else camera.manufacturer or "").strip()
    if not normalized_manufacturer:
        raise CameraConfigurationError(400, "A marca da camera e obrigatoria")
    if camera_priority not in CAMERA_PRIORITY_CHOICES:
        raise CameraConfigurationError(400, "Prioridade inválida")

    previous_manufacturer = str(camera.manufacturer or "")
    camera.manufacturer = normalized_manufacturer[:120]
    if model is not None:
        camera.model = str(model).strip()[:120] or None
    camera.site_name = site_name
    camera.group_name = group_name
    camera.camera_priority = camera_priority
    camera.auto_start_enabled = auto_start_enabled
    camera.alarm_sound_enabled = alarm_sound_enabled
    camera.alarm_popup_enabled = alarm_popup_enabled
    db.commit()
    if previous_manufacturer != camera.manufacturer:
        from app.services.monitor_ptz_service import invalidate_camera_ptz_session

        invalidate_camera_ptz_session(camera)
    return camera


def parse_roi_json(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    points = json.loads(text)
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("A ROI precisa ter pelo menos 3 pontos no formato JSON.")
    cleaned: list[dict[str, float]] = []
    for point in points:
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise ValueError("Cada ponto da ROI precisa ter x e y.")
        x = float(point["x"])
        y = float(point["y"])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("Os pontos da ROI devem estar normalizados entre 0 e 1.")
        cleaned.append({"x": x, "y": y})
    return json.dumps(cleaned)


def parse_optional_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("Os pontos da linha devem estar entre 0 e 1.")
    return number


def parse_positive_float(value: str | None, label: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} é obrigatório.")
    number = float(value)
    if number <= 0:
        raise ValueError(f"{label} deve ser maior que zero.")
    return number


def parse_nonnegative_float(value: str | None, label: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} é obrigatório.")
    number = float(value)
    if number < 0:
        raise ValueError(f"{label} não pode ser negativo.")
    return number


def parse_positive_int(value: str | None, label: str) -> int:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} é obrigatório.")
    number = int(float(value))
    if number <= 0:
        raise ValueError(f"{label} deve ser maior que zero.")
    return number


def parse_unit_float(value: str | None, label: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} é obrigatório.")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{label} deve ficar entre 0 e 1.")
    return number


def parse_human_event_modes(values) -> list[str]:
    parsed: list[str] = []
    for value in values or []:
        mode = str(value).strip()
        if mode in HUMAN_EVENT_MODE_CHOICES and mode not in parsed:
            parsed.append(mode)
    return parsed


def update_extended_operational_config(
    db: Session,
    camera_id: int,
    values: OperationalConfigurationInput,
) -> Camera:
    camera = _get_camera(db, camera_id)
    try:
        if values.camera_priority not in CAMERA_PRIORITY_CHOICES:
            raise ValueError("Prioridade inválida.")

        current_profile = profile_from_camera(camera)
        previous_family = getattr(current_profile, "camera_family", "dome")
        previous_scene_category = getattr(current_profile, "scene_category", "interno")
        previous_target_focus = getattr(current_profile, "target_focus", "pessoa")
        previous_nuisance_profile = getattr(current_profile, "nuisance_profile", None)
        previous_overrides = dict(getattr(current_profile, "manual_overrides", {}) or {})

        normalized_family = values.camera_family if values.camera_family in CAMERA_FAMILIES else "dome"
        normalized_scene = values.scene_category if values.scene_category in SCENE_CATEGORIES else "interno"
        normalized_target = values.target_focus if values.target_focus in TARGET_FOCUSES else "pessoa"

        selected_overrides: dict[str, Any] = {}
        raw_overrides = values.manual_overrides
        if str(raw_overrides.get("processing_max_width") or "").strip():
            selected_overrides["processing_max_width"] = parse_positive_int(
                raw_overrides["processing_max_width"], "Largura máxima de processamento"
            )
        if str(raw_overrides.get("processing_max_height") or "").strip():
            selected_overrides["processing_max_height"] = parse_positive_int(
                raw_overrides["processing_max_height"], "Altura máxima de processamento"
            )
        if raw_overrides.get("processing_upscale_small_frames") is not None:
            selected_overrides["processing_upscale_small_frames"] = bool(
                raw_overrides["processing_upscale_small_frames"]
            )
        if str(raw_overrides.get("normal_inference_interval_seconds") or "").strip():
            selected_overrides["normal_inference_interval_seconds"] = parse_positive_float(
                raw_overrides["normal_inference_interval_seconds"], "Intervalo de inferência"
            )
        if str(raw_overrides.get("capture_drop_frames") or "").strip():
            selected_overrides["capture_drop_frames"] = parse_positive_int(
                raw_overrides["capture_drop_frames"], "Frames descartados na captura"
            )
        if str(raw_overrides.get("visual_raw_publish_interval_seconds") or "").strip():
            selected_overrides["visual_raw_publish_interval_seconds"] = parse_nonnegative_float(
                raw_overrides["visual_raw_publish_interval_seconds"], "Intervalo visual raw"
            )
        if str(raw_overrides.get("visual_processed_publish_interval_seconds") or "").strip():
            selected_overrides["visual_processed_publish_interval_seconds"] = parse_nonnegative_float(
                raw_overrides["visual_processed_publish_interval_seconds"], "Intervalo visual processado"
            )
        selected_overrides["prefer_motion_test"] = True

        nuisance_changed = previous_nuisance_profile is None and any(values.nuisance_profile.values())
        if previous_nuisance_profile is not None:
            nuisance_changed = any(
                bool(getattr(previous_nuisance_profile, key, False)) != bool(value)
                for key, value in values.nuisance_profile.items()
            )
        if (
            normalized_family != previous_family
            or normalized_scene != previous_scene_category
            or normalized_target != previous_target_focus
            or nuisance_changed
        ):
            current_profile.camera_family = normalized_family
            current_profile.scene_category = normalized_scene
            current_profile.target_focus = normalized_target
            current_profile.nuisance_profile = type(current_profile.nuisance_profile)(
                **values.nuisance_profile
            )
        current_profile.manual_overrides = {**previous_overrides, **selected_overrides}

        camera.site_name = values.site_name.strip() or None
        camera.group_name = values.group_name.strip() or None
        camera.camera_priority = values.camera_priority
        camera.auto_start_enabled = values.auto_start_enabled
        camera.alarm_sound_enabled = values.alarm_sound_enabled
        camera.alarm_popup_enabled = values.alarm_popup_enabled
        camera.learning_mode = (
            values.learning_mode if values.learning_mode in LEARNING_MODE_CHOICES else DEFAULT_LEARNING_MODE
        )
        camera.auto_tuning_enabled = values.auto_tuning_enabled
        camera.critical_lock = values.critical_lock
        camera.max_daily_auto_changes = parse_positive_int(
            values.max_daily_auto_changes, "Limite diário de auto ajuste"
        )
        camera.min_reviewed_events_for_suggestion = parse_positive_int(
            values.min_reviewed_events_for_suggestion, "Mínimo de eventos revisados"
        )
        camera.min_reviewed_events_for_auto_tuning = parse_positive_int(
            values.min_reviewed_events_for_auto_tuning, "Mínimo para auto tuning"
        )
        camera.rollback_window_hours = parse_positive_int(
            values.rollback_window_hours, "Janela de rollback"
        )
        camera.analytics_profile_json = serialize_profile(current_profile)
        db.commit()
        return camera
    except ValueError as exc:
        db.rollback()
        raise CameraConfigurationError(400, str(exc)) from exc


def update_web_analytics_config(
    db: Session,
    camera_id: int,
    values: AnalyticsConfigurationInput,
) -> tuple[Camera, bool]:
    camera = _get_camera(db, camera_id)
    try:
        changed = False
        roi_polygon_value = values.roi_polygon_json.strip()
        selected_human_modes = parse_human_event_modes(values.human_event_modes)
        if roi_polygon_value:
            new_roi_polygon = parse_roi_json(roi_polygon_value)
            if camera.roi_polygon_json != new_roi_polygon:
                camera.roi_polygon_json = new_roi_polygon
                changed = True
        elif camera.roi_polygon_json:
            camera.roi_polygon_json = None
            changed = True

        line_values = (
            values.line_start_x,
            values.line_start_y,
            values.line_end_x,
            values.line_end_y,
        )
        if any(value.strip() for value in line_values):
            parsed_line = tuple(parse_optional_float(value) for value in line_values)
            if any(value is None for value in parsed_line):
                raise ValueError("Para atualizar a linha, informe os 4 campos (x1, y1, x2, y2).")
            if parsed_line != (
                camera.line_start_x,
                camera.line_start_y,
                camera.line_end_x,
                camera.line_end_y,
            ):
                (
                    camera.line_start_x,
                    camera.line_start_y,
                    camera.line_end_x,
                    camera.line_end_y,
                ) = parsed_line
                changed = True
        elif any(
            value is not None
            for value in (
                camera.line_start_x,
                camera.line_start_y,
                camera.line_end_x,
                camera.line_end_y,
            )
        ):
            camera.line_start_x = None
            camera.line_start_y = None
            camera.line_end_x = None
            camera.line_end_y = None
            changed = True

        normalized_direction = (
            values.line_direction if values.line_direction in {"any", "a_to_b", "b_to_a"} else "any"
        )
        if normalized_direction != (camera.line_direction or "any"):
            camera.line_direction = normalized_direction
            changed = True
        modes_json = json.dumps(selected_human_modes, ensure_ascii=False)
        if camera.human_event_modes_json != modes_json:
            camera.human_event_modes_json = modes_json
            changed = True
        loitering_seconds = (
            parse_positive_float(values.human_loitering_seconds, "Permanência prolongada")
            if values.human_loitering_seconds.strip()
            else DEFAULT_HUMAN_LOITERING_SECONDS
        )
        if camera.human_loitering_seconds != loitering_seconds:
            camera.human_loitering_seconds = loitering_seconds
            changed = True
        sensitivity = (
            values.human_detection_sensitivity
            if values.human_detection_sensitivity in HUMAN_DETECTION_SENSITIVITY_CHOICES
            else "medium"
        )
        if camera.human_detection_sensitivity != sensitivity:
            camera.human_detection_sensitivity = sensitivity
            changed = True

        if changed:
            camera.analytics_coordinate_space = "source"
            try:
                camera.analytics_profile_json = serialize_profile(profile_from_legacy_camera(camera))
            except Exception:
                pass
            db.commit()
        else:
            db.rollback()
        return camera, changed
    except ValueError as exc:
        db.rollback()
        raise CameraConfigurationError(400, str(exc)) from exc


def update_motion_config(
    db: Session,
    camera_id: int,
    values: MotionConfigurationInput,
) -> Camera:
    camera = _get_camera(db, camera_id)
    try:
        camera.motion_idle_interval = parse_positive_float(values.motion_idle_interval, "Intervalo em idle")
        camera.motion_active_interval = parse_positive_float(values.motion_active_interval, "Intervalo ativo")
        camera.motion_hold_seconds = parse_nonnegative_float(
            values.motion_hold_seconds, "Persistência após movimento"
        )
        camera.motion_detection_hold_seconds = parse_nonnegative_float(
            values.motion_detection_hold_seconds, "Persistência após detecção"
        )
        camera.motion_min_motion_frames = parse_positive_int(
            values.motion_min_motion_frames, "Frames consecutivos"
        )
        camera.motion_downscale_width = parse_positive_int(
            values.motion_downscale_width, "Largura de análise"
        )
        camera.motion_min_contour_area = parse_positive_int(
            values.motion_min_contour_area, "Área mínima de contorno"
        )
        camera.motion_ratio_threshold = parse_unit_float(
            values.motion_ratio_threshold, "Limiar de movimento"
        )
        camera.motion_global_change_ratio_limit = parse_unit_float(
            values.motion_global_change_ratio_limit, "Mudança global máxima"
        )
        camera.motion_background_alpha = parse_unit_float(
            values.motion_background_alpha, "Adaptação do fundo"
        )
        camera.motion_warmup_frames = parse_positive_int(values.motion_warmup_frames, "Frames de aquecimento")
        db.commit()
        return camera
    except ValueError as exc:
        db.rollback()
        raise CameraConfigurationError(400, str(exc)) from exc


def reset_motion_config(db: Session, camera_id: int) -> Camera:
    camera = _get_camera(db, camera_id)
    for field in MOTION_CONFIG_FIELDS:
        setattr(camera, field, None)
    db.commit()
    return camera
