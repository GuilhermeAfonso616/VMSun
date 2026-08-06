"""Modelos de perfil de camera e utilitarios VMS sem inteligencia artificial."""

from typing import Any, Dict

CAMERA_FAMILIES = {
    "generic": {"name": "Generica / RTSP", "description": "Stream RTSP padrao"},
    "intelbras": {"name": "Intelbras", "description": "NVR / Camera Intelbras"},
    "dahua": {"name": "Dahua", "description": "NVR / Camera Dahua"},
    "hikvision": {"name": "Hikvision", "description": "NVR / Camera Hikvision"},
    "onvif": {"name": "ONVIF", "description": "Dispositivo padrao ONVIF"},
}

SCENE_CATEGORIES = {
    "perimeter": {"name": "Perimetro", "description": "Perimetro externo"},
    "access": {"name": "Acessos", "description": "Portoes e portarias"},
    "indoor": {"name": "Interno", "description": "Ambiente interno"},
}

TARGET_FOCUSES = {
    "general": {"name": "Geral", "description": "Visualizacao geral"},
}


class CameraProfile:
    def __init__(self, family: str = "generic", scene_category: str = "perimeter", target_focus: str = "general"):
        self.family = family
        self.scene_category = scene_category
        self.target_focus = target_focus

    def as_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "scene_category": self.scene_category,
            "target_focus": self.target_focus,
        }


CameraAnalyticProfile = CameraProfile


def build_camera_analytic_profile(*args, **kwargs) -> CameraProfile:
    return CameraProfile()


def profile_from_camera(camera: Any) -> CameraProfile:
    return CameraProfile()


def profile_from_legacy_camera(camera: Any) -> CameraProfile:
    return CameraProfile()


def profile_from_mapping(mapping: Any) -> CameraProfile:
    return CameraProfile()


def serialize_profile(profile: Any) -> Dict[str, Any]:
    return {"family": "generic", "scene_category": "perimeter", "target_focus": "general"}


class DerivedCameraPolicy:
    def __init__(self):
        self.enabled = True


def build_profile_preview(profile: Any) -> Dict[str, Any]:
    return {"policy": "vms_default"}


def profile_to_legacy_fields(profile: Any) -> Dict[str, Any]:
    return {}


def normalized_line_to_pixels(line: Any, width: int, height: int) -> list:
    return []


def normalized_polygon_to_pixels(polygon: Any, width: int, height: int) -> list:
    return []


class MotionGate:
    def __init__(self, *args, **kwargs):
        pass

    def evaluate(self, *args, **kwargs) -> Any:
        class Result:
            has_motion = True
        return Result()
