import json

from app.analytics.camera_profiles import profile_from_camera
from app.core.config import settings
from app.db.models import AuditLog, ConfigVersionHistory, Event, EventFeedback, TuningSuggestion, User
from app.services.camera_factory import build_camera_model


def _seed_event_context(api_context):
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    camera = build_camera_model(
        name="Entrada",
        ip="10.0.0.10",
        onvif_port=80,
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
    )
    event = Event(
        camera_id=1,
        event_type="person_entered_roi",
        status="new",
        alarm_eligible=True,
        is_alarm_active=True,
        severity="high",
    )
    api_context.db.add_all([admin, camera])
    api_context.db.flush()
    event.camera_id = camera.id
    event.assigned_user_id = admin.id
    event.assigned_username = admin.username
    api_context.db.add(event)
    api_context.db.commit()
    for item in (admin, camera, event):
        api_context.db.refresh(item)
    api_context.selected_user["value"] = admin
    return admin, camera, event


def test_event_list_update_validation_and_audit(api_context):
    admin, _camera, event = _seed_event_context(api_context)

    listed = api_context.client.get("/api/events")
    updated = api_context.client.put(
        f"/api/events/{event.id}",
        json={
            "status": "closed",
            "operator_note": "  verificado  ",
            "resolution_code": "verified_threat",
        },
    )
    invalid = api_context.client.put(f"/api/events/{event.id}", json={"status": "canceled"})

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == event.id
    assert updated.status_code == 200
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "Status inválido"}
    api_context.db.refresh(event)
    assert event.status == "closed"
    assert event.operator_note == "verificado"
    audit = api_context.db.query(AuditLog).filter(AuditLog.action == "event_update").one()
    assert audit.user_id == admin.id


def test_event_feedback_http_persists_review_without_auto_suggestion(
    api_context,
    monkeypatch,
    tmp_path,
):
    _admin, _camera, event = _seed_event_context(api_context)
    monkeypatch.setattr(settings, "revalidator_feedback_dataset_dir", str(tmp_path / "dataset"))
    monkeypatch.setattr(settings, "revalidator_review_audit_dir", str(tmp_path / "audit"))

    response = api_context.client.post(
        f"/api/events/{event.id}/feedback",
        json={
            "label": "true_positive",
            "probable_cause": "normal_human_flow",
            "operator_note": "confirmado",
            "reviewed_by": "operador",
            "auto_suggest": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Feedback registrado"
    assert response.json()["suggestions_created"] == 0
    feedback = api_context.db.query(EventFeedback).filter(EventFeedback.event_id == event.id).one()
    assert feedback.label == "true_positive"
    assert feedback.reviewed_by == "operador"


def test_feedback_policy_suggestions_and_history_contracts(api_context):
    _admin, camera, _event = _seed_event_context(api_context)
    suggestion = TuningSuggestion(
        camera_id=camera.id,
        scope_type="profile",
        scope_id=str(camera.id),
        suggestion_type="policy_tuning",
        parameter_name="manual_parameter",
        old_value="1",
        suggested_value="2",
        reason_summary="manual",
        evidence_count=1,
        confidence_score=0.5,
        status="pending",
    )
    before_profile = profile_from_camera(camera).to_dict()
    before_profile["camera_family"] = "bullet"
    history = ConfigVersionHistory(
        camera_id=camera.id,
        config_before=json.dumps(before_profile),
        config_after=camera.analytics_profile_json,
        change_source="manual",
        reason="teste",
        rollback_available=True,
    )
    api_context.db.add_all([suggestion, history])
    api_context.db.commit()

    listed = api_context.client.get(f"/api/feedback/suggestions?camera_id={camera.id}")
    manual_apply = api_context.client.post(f"/api/feedback/suggestions/{suggestion.id}/apply")
    rejected = api_context.client.post(f"/api/feedback/suggestions/{suggestion.id}/reject")
    invalid_policy = api_context.client.post(
        f"/api/cameras/{camera.id}/learning-policy",
        json={"learning_mode": "unbounded"},
    )
    rolled_back = api_context.client.post(f"/api/config/history/{history.id}/rollback")

    assert listed.status_code == 200
    assert listed.json()["suggestions"][0]["id"] == suggestion.id
    assert manual_apply.status_code == 400
    assert rejected.status_code == 200
    assert invalid_policy.status_code == 400
    assert rolled_back.status_code == 200
    assert profile_from_camera(camera).camera_family == "bullet"
