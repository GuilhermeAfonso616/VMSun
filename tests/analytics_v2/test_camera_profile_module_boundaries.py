import json

from app.analytics import (
    camera_policy_builder,
    camera_profile_models,
    camera_profiles,
)


def test_legacy_facade_reexports_model_and_policy_implementations():
    assert (
        camera_profiles.build_camera_analytic_profile
        is camera_profile_models.build_camera_analytic_profile
    )
    assert camera_profiles.profile_from_mapping is camera_profile_models.profile_from_mapping
    assert (
        camera_profiles.build_profile_preview
        is camera_policy_builder.build_profile_preview
    )
    assert (
        camera_profiles.build_analytics_config_from_profile
        is camera_policy_builder.build_analytics_config_from_profile
    )


def test_profile_models_round_trip_without_runtime_policy_imports():
    profile = camera_profile_models.build_camera_analytic_profile(
        preset_name="perimeter_bullet",
        nuisance_profile={"vegetation_wind": True},
    )

    serialized = camera_profile_models.serialize_profile(profile)
    restored = camera_profile_models.profile_from_mapping(json.loads(serialized))

    assert restored.camera_family == "bullet"
    assert restored.scene_profile == "perimeter_outdoor"
    assert restored.nuisance_profile.vegetation_wind is True


def test_policy_builder_derives_runtime_config_from_model_contract():
    profile = camera_profile_models.build_camera_analytic_profile(
        camera_family="fisheye",
        scene_profile="wide_area",
        analytic_goal="zone_presence",
        subzones=[
            {
                "zone_id": "sector-a",
                "polygon": [
                    [0.1, 0.1],
                    [0.4, 0.1],
                    [0.4, 0.6],
                ],
            }
        ],
    )

    config, derived = camera_policy_builder.build_analytics_config_from_profile(
        profile,
        frame_width=1920,
        frame_height=1080,
    )

    assert derived.subzones_required is True
    assert derived.full_frame_forbidden is True
    assert len(config.scene.restricted_zones) == 1
    assert config.scene.restricted_zones[0].zone_id == "sector-a"
