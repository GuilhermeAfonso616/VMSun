from app.analytics.camera_profiles import profile_from_camera
from app.services.camera_factory import build_camera_model


def test_build_camera_model_applies_operational_defaults_and_profile():
    camera = build_camera_model(
        name="Entrada",
        ip="10.0.0.10",
        onvif_port=80,
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
        camera_family="bullet",
        scene_category="externo_geral",
        target_focus="pessoa",
        nuisance_profile={"rain": True},
    )

    profile = profile_from_camera(camera)
    assert camera.status == "idle"
    assert camera.camera_priority == "medium"
    assert camera.analytics_profile_json
    assert profile.camera_family == "bullet"
    assert profile.scene_category == "externo_geral"
    assert profile.nuisance_profile.rain is True


def test_build_camera_model_preserves_web_coordinate_override():
    camera = build_camera_model(
        name="Patio",
        ip="10.0.0.11",
        onvif_port=80,
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.11/main",
        coordinate_space_override="display",
    )

    assert camera.analytics_coordinate_space == "display"
