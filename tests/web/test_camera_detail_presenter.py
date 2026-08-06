from datetime import datetime
from types import SimpleNamespace

from app.web.camera_detail_presenter import (
    build_camera_detail_context,
    serialize_camera_detail_event,
)


def test_detail_context_centralizes_template_defaults_and_selected_profile_options():
    request = object()
    camera = SimpleNamespace(
        analytics_profile=SimpleNamespace(
            camera_family="bullet",
            scene_category="perimetral",
            target_focus="veiculo",
            nuisance_profile={"rain": True},
        )
    )

    context = build_camera_detail_context(
        request=request,
        camera=camera,
        events=[],
        camera_error="falha controlada",
    )

    assert context["request"] is request
    assert context["camera"] is camera
    assert context["camera_error"] == "falha controlada"
    assert context["motion_error"] is None
    assert context["rtsp_test_results"] is None
    assert next(item for item in context["camera_family_options"] if item["selected"])["value"] == "bullet"
    assert next(item for item in context["scene_category_options"] if item["selected"])["value"] == "perimetral"
    assert next(item for item in context["target_focus_options"] if item["selected"])["value"] == "veiculo"
    assert next(item for item in context["nuisance_options"] if item["key"] == "rain")["enabled"] is True


def test_event_serializer_normalizes_canceled_status_and_datetime():
    event = SimpleNamespace(
        id=17,
        event_type="person_left",
        status="canceled",
        severity=None,
        confidence=0.72,
        track_id=8,
        snapshot_path=None,
        created_at=datetime(2026, 7, 17, 12, 0),
        lifecycle_action="close",
        alarm_category="operational",
        alarm_eligible=False,
        is_alarm_active=False,
        resolved_at=datetime(2026, 7, 17, 12, 5),
    )

    payload = serialize_camera_detail_event(event)

    assert payload["status"] == "closed"
    assert payload["status_label"] == "Fechado"
    assert payload["severity"] == "medium"
    assert payload["snapshot_url"] == ""
    assert payload["resolved_at"] == "2026-07-17T12:05:00"
    assert payload["can_ack"] is False
    assert payload["can_close"] is False
    assert payload["can_reopen"] is False
