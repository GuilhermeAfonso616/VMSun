"""Carrega a configuracao de uma camera para o runtime do worker.

O objetivo aqui e traduzir o que esta salvo no banco para estruturas prontas
para o worker, sem espalhar logica de parse pela aplicacao.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.analytics.camera_profile_models import (
    CameraAnalyticProfile,
    profile_from_camera,
)
from app.analytics.camera_policy_builder import DerivedCameraPolicy, build_profile_preview
from app.core.config import settings
from app.core.logging import log_ignored_exception
from app.db.models import Camera


def _override_ignorado(chave: str, overrides: dict[str, Any]) -> None:
    """Um override manual invalido cai para o default do sistema.

    Sem registro, o operador nao tem como saber que o valor que ele configurou
    foi descartado — o comportamento simplesmente nao muda.
    """
    log_ignored_exception(
        f"camera_config.override.{chave}",
        level=logging.WARNING,
        reason=f"valor invalido: {overrides.get(chave)!r}",
    )

DEFAULT_MOTION_CONFIG = {
    "motion_idle_interval": 1.0,
    "motion_active_interval": 0.12,
    "motion_hold_seconds": 2.0,
    "motion_detection_hold_seconds": 3.5,
    "motion_min_motion_frames": 2,
    "motion_downscale_width": 384,
    "motion_min_contour_area": 700,
    "motion_ratio_threshold": 0.0025,
    "motion_global_change_ratio_limit": 0.40,
    "motion_background_alpha": 0.025,
    "motion_warmup_frames": 20,
}


@dataclass(slots=True)
class AnalyticsConfig:
    roi_name: Optional[str] = None
    roi_points: list[dict] = field(default_factory=list)
    line: Optional[dict] = None
    line_direction: str = "any"
    coordinate_space: str = "display"
    human_event_modes: Optional[list[str]] = None
    human_loitering_seconds: Optional[float] = None
    human_detection_sensitivity: Optional[str] = None


@dataclass(slots=True)
class MotionConfig:
    idle_interval: float = DEFAULT_MOTION_CONFIG["motion_idle_interval"]
    active_interval: float = DEFAULT_MOTION_CONFIG["motion_active_interval"]
    motion_hold_seconds: float = DEFAULT_MOTION_CONFIG["motion_hold_seconds"]
    detection_hold_seconds: float = DEFAULT_MOTION_CONFIG["motion_detection_hold_seconds"]
    min_motion_frames: int = DEFAULT_MOTION_CONFIG["motion_min_motion_frames"]
    downscale_width: int = DEFAULT_MOTION_CONFIG["motion_downscale_width"]
    min_contour_area: int = DEFAULT_MOTION_CONFIG["motion_min_contour_area"]
    motion_ratio_threshold: float = DEFAULT_MOTION_CONFIG["motion_ratio_threshold"]
    global_change_ratio_limit: float = DEFAULT_MOTION_CONFIG["motion_global_change_ratio_limit"]
    background_alpha: float = DEFAULT_MOTION_CONFIG["motion_background_alpha"]
    warmup_frames: int = DEFAULT_MOTION_CONFIG["motion_warmup_frames"]


@dataclass(slots=True)
class CameraRuntimeConfig:
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    analytic_profile: CameraAnalyticProfile = field(default_factory=CameraAnalyticProfile)
    analytic_policy_preview: dict = field(default_factory=dict)
    derived_policy: DerivedCameraPolicy | None = None
    motion: MotionConfig = field(default_factory=MotionConfig)
    processing_max_width: int = int(settings.processing_max_width)
    processing_max_height: int = int(settings.processing_max_height)
    processing_upscale_small_frames: bool = bool(settings.processing_upscale_small_frames)
    normal_inference_interval_seconds: float = float(settings.normal_inference_interval_seconds)
    capture_drop_frames: int = int(settings.capture_drop_frames)
    prefer_motion_test: bool = False
    visual_raw_publish_enabled: bool = bool(settings.visual_raw_publish_enabled)
    visual_processed_publish_enabled: bool = bool(settings.visual_processed_publish_enabled)
    visual_raw_publish_interval_seconds: float = float(settings.visual_raw_publish_interval_seconds)
    visual_processed_publish_interval_seconds: float = float(settings.visual_processed_publish_interval_seconds)


def _parse_roi_points(camera: Optional[Camera]) -> list[dict]:
    # ROI sempre entra como lista de pontos normalizados do espaco fonte.
    roi_points: list[dict] = []
    if not camera or not camera.roi_polygon_json:
        return roi_points

    try:
        parsed = json.loads(camera.roi_polygon_json)
        if isinstance(parsed, list):
            for point in parsed:
                if not isinstance(point, dict) or "x" not in point or "y" not in point:
                    continue
                x = float(point["x"])
                y = float(point["y"])
                if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                    roi_points.append({"x": x, "y": y})
    except Exception:
        return []

    return roi_points


def _parse_line(camera: Optional[Camera]) -> Optional[dict]:
    if not camera:
        return None

    if None in (camera.line_start_x, camera.line_start_y, camera.line_end_x, camera.line_end_y):
        return None

    try:
        return {
            "x1": float(camera.line_start_x),
            "y1": float(camera.line_start_y),
            "x2": float(camera.line_end_x),
            "y2": float(camera.line_end_y),
        }
    except Exception:
        return None


def _parse_human_event_modes(camera: Optional[Camera]) -> Optional[list[str]]:
    if not camera or not getattr(camera, "human_event_modes_json", None):
        return None

    try:
        parsed = json.loads(camera.human_event_modes_json)
        if not isinstance(parsed, list):
            return None

        modes: list[str] = []
        for item in parsed:
            value = str(item).strip()
            if value and value not in modes:
                modes.append(value)

        return modes
    except Exception:
        return None


def _parse_human_loitering_seconds(camera: Optional[Camera]) -> Optional[float]:
    if not camera or camera.human_loitering_seconds is None:
        return None

    try:
        value = float(camera.human_loitering_seconds)
        return value if value > 0 else None
    except Exception:
        return None


def _parse_human_detection_sensitivity(camera: Optional[Camera]) -> Optional[str]:
    if not camera:
        return None

    value = str(getattr(camera, "human_detection_sensitivity", "") or "").strip().lower()
    if value in {"very_low", "low", "medium", "high"}:
        return value
    return None


def load_camera_runtime_config(camera: Optional[Camera]) -> CameraRuntimeConfig:
    # Cada worker recebe uma copia imutavel da configuracao carregada do banco.
    coordinate_space = "display"
    if camera and getattr(camera, "analytics_coordinate_space", None):
        coordinate_space = str(camera.analytics_coordinate_space).strip().lower() or "display"

    roi_points = _parse_roi_points(camera)
    human_event_modes = _parse_human_event_modes(camera)
    analytic_profile = profile_from_camera(camera)
    derived_policy_preview = build_profile_preview(
        analytic_profile,
        frame_width=1920,
        frame_height=1080,
    )

    analytics = AnalyticsConfig(
        roi_name=camera.roi_name if camera else None,
        roi_points=roi_points,
        line=_parse_line(camera),
        line_direction=camera.line_direction if camera and camera.line_direction else "any",
        coordinate_space=coordinate_space,
        human_event_modes=human_event_modes,
        human_loitering_seconds=_parse_human_loitering_seconds(camera),
        human_detection_sensitivity=_parse_human_detection_sensitivity(camera),
    )

    motion = MotionConfig(
        idle_interval=float(camera.motion_idle_interval) if camera and camera.motion_idle_interval is not None else DEFAULT_MOTION_CONFIG["motion_idle_interval"],
        active_interval=float(camera.motion_active_interval) if camera and camera.motion_active_interval is not None else DEFAULT_MOTION_CONFIG["motion_active_interval"],
        motion_hold_seconds=float(camera.motion_hold_seconds) if camera and camera.motion_hold_seconds is not None else DEFAULT_MOTION_CONFIG["motion_hold_seconds"],
        detection_hold_seconds=float(camera.motion_detection_hold_seconds) if camera and camera.motion_detection_hold_seconds is not None else DEFAULT_MOTION_CONFIG["motion_detection_hold_seconds"],
        min_motion_frames=int(camera.motion_min_motion_frames) if camera and camera.motion_min_motion_frames is not None else DEFAULT_MOTION_CONFIG["motion_min_motion_frames"],
        downscale_width=int(camera.motion_downscale_width) if camera and camera.motion_downscale_width is not None else DEFAULT_MOTION_CONFIG["motion_downscale_width"],
        min_contour_area=int(camera.motion_min_contour_area) if camera and camera.motion_min_contour_area is not None else DEFAULT_MOTION_CONFIG["motion_min_contour_area"],
        motion_ratio_threshold=float(camera.motion_ratio_threshold) if camera and camera.motion_ratio_threshold is not None else DEFAULT_MOTION_CONFIG["motion_ratio_threshold"],
        global_change_ratio_limit=float(camera.motion_global_change_ratio_limit) if camera and camera.motion_global_change_ratio_limit is not None else DEFAULT_MOTION_CONFIG["motion_global_change_ratio_limit"],
        background_alpha=float(camera.motion_background_alpha) if camera and camera.motion_background_alpha is not None else DEFAULT_MOTION_CONFIG["motion_background_alpha"],
        warmup_frames=int(camera.motion_warmup_frames) if camera and camera.motion_warmup_frames is not None else DEFAULT_MOTION_CONFIG["motion_warmup_frames"],
    )

    visual_raw_publish_interval_seconds = float(settings.visual_raw_publish_interval_seconds)
    visual_processed_publish_interval_seconds = float(settings.visual_processed_publish_interval_seconds)
    visual_raw_publish_enabled = bool(settings.visual_raw_publish_enabled)
    visual_processed_publish_enabled = bool(settings.visual_processed_publish_enabled)
    processing_max_width = int(settings.processing_max_width)
    processing_max_height = int(settings.processing_max_height)
    processing_upscale_small_frames = bool(settings.processing_upscale_small_frames)
    normal_inference_interval_seconds = float(settings.normal_inference_interval_seconds)
    capture_drop_frames = int(settings.capture_drop_frames)
    prefer_motion_test = False
    if getattr(analytic_profile, "manual_overrides", None):
        overrides = dict(analytic_profile.manual_overrides or {})
        try:
            if overrides.get("visual_raw_publish_enabled") is not None:
                visual_raw_publish_enabled = bool(overrides["visual_raw_publish_enabled"])
        except Exception:
            _override_ignorado("visual_raw_publish_enabled", overrides)
        try:
            if overrides.get("visual_processed_publish_enabled") is not None:
                visual_processed_publish_enabled = bool(overrides["visual_processed_publish_enabled"])
        except Exception:
            _override_ignorado("visual_processed_publish_enabled", overrides)
        try:
            if overrides.get("visual_raw_publish_interval_seconds") is not None:
                visual_raw_publish_interval_seconds = max(0.0, float(overrides["visual_raw_publish_interval_seconds"]))
        except Exception:
            _override_ignorado("visual_raw_publish_interval_seconds", overrides)
        try:
            if overrides.get("visual_processed_publish_interval_seconds") is not None:
                visual_processed_publish_interval_seconds = max(0.0, float(overrides["visual_processed_publish_interval_seconds"]))
        except Exception:
            _override_ignorado("visual_processed_publish_interval_seconds", overrides)
        try:
            if overrides.get("visual_publish_interval_seconds") is not None:
                visual_processed_publish_interval_seconds = max(0.0, float(overrides["visual_publish_interval_seconds"]))
        except Exception:
            _override_ignorado("visual_publish_interval_seconds", overrides)
        try:
            if overrides.get("processing_max_width") is not None:
                processing_max_width = max(1, int(overrides["processing_max_width"]))
        except Exception:
            _override_ignorado("processing_max_width", overrides)
        try:
            if overrides.get("processing_max_height") is not None:
                processing_max_height = max(1, int(overrides["processing_max_height"]))
        except Exception:
            _override_ignorado("processing_max_height", overrides)
        try:
            if overrides.get("processing_upscale_small_frames") is not None:
                processing_upscale_small_frames = bool(overrides["processing_upscale_small_frames"])
        except Exception:
            _override_ignorado("processing_upscale_small_frames", overrides)
        try:
            if overrides.get("normal_inference_interval_seconds") is not None:
                normal_inference_interval_seconds = max(0.0, float(overrides["normal_inference_interval_seconds"]))
        except Exception:
            _override_ignorado("normal_inference_interval_seconds", overrides)
        try:
            if overrides.get("capture_drop_frames") is not None:
                capture_drop_frames = max(0, int(overrides["capture_drop_frames"]))
        except Exception:
            _override_ignorado("capture_drop_frames", overrides)
        try:
            prefer_motion_test = bool(overrides.get("prefer_motion_test", False))
        except Exception:
            prefer_motion_test = False

    return CameraRuntimeConfig(
        analytics=analytics,
        analytic_profile=analytic_profile,
        analytic_policy_preview=derived_policy_preview,
        motion=motion,
        processing_max_width=processing_max_width,
        processing_max_height=processing_max_height,
        processing_upscale_small_frames=processing_upscale_small_frames,
        normal_inference_interval_seconds=normal_inference_interval_seconds,
        capture_drop_frames=capture_drop_frames,
        prefer_motion_test=prefer_motion_test,
        visual_raw_publish_enabled=visual_raw_publish_enabled,
        visual_processed_publish_enabled=visual_processed_publish_enabled,
        visual_raw_publish_interval_seconds=visual_raw_publish_interval_seconds,
        visual_processed_publish_interval_seconds=visual_processed_publish_interval_seconds,
    )
