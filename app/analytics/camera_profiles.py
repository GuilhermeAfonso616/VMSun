"""Fachada compatível para perfis analíticos e políticas derivadas.

Novos consumidores devem usar ``camera_profile_models`` para modelagem e
persistência, ou ``camera_policy_builder`` para configuração de runtime.
"""

from app.analytics.camera_profile_models import (
    ANALYTIC_GOALS,
    CAMERA_FAMILIES,
    RISK_PROFILES,
    SCENE_CATEGORIES,
    SCENE_PROFILES,
    TARGET_FOCUSES,
    THRESHOLD_DEFAULTS,
    CameraAnalyticProfile,
    NuisanceProfile,
    ThresholdProfile,
    build_camera_analytic_profile,
    profile_from_camera,
    profile_from_legacy_camera,
    profile_from_mapping,
    serialize_profile,
    serialize_roi_polygon,
)
from app.analytics.camera_policy_builder import (
    DerivedCameraPolicy,
    build_analytics_config_from_profile,
    build_profile_preview,
    profile_to_legacy_fields,
)


__all__ = [
    "ANALYTIC_GOALS",
    "CAMERA_FAMILIES",
    "RISK_PROFILES",
    "SCENE_CATEGORIES",
    "SCENE_PROFILES",
    "TARGET_FOCUSES",
    "THRESHOLD_DEFAULTS",
    "CameraAnalyticProfile",
    "DerivedCameraPolicy",
    "NuisanceProfile",
    "ThresholdProfile",
    "build_analytics_config_from_profile",
    "build_camera_analytic_profile",
    "build_profile_preview",
    "profile_from_camera",
    "profile_from_legacy_camera",
    "profile_from_mapping",
    "profile_to_legacy_fields",
    "serialize_profile",
    "serialize_roi_polygon",
]
