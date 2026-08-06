import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.analytics.camera_profiles import profile_from_camera
from app.db.base import Base
from app.db.models import ConfigVersionHistory, TuningSuggestion
from app.services.camera_factory import build_camera_model
from app.services.feedback_workflow_service import (
    FeedbackWorkflowError,
    apply_suggestion,
    list_config_history_payloads,
    list_suggestion_payloads,
    reject_suggestion,
    rollback_config_history,
    update_learning_policy,
)


@pytest.fixture
def workflow_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    camera = build_camera_model(
        name="Entrada",
        ip="10.0.0.10",
        onvif_port=80,
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
    )
    db.add(camera)
    db.commit()
    db.refresh(camera)
    try:
        yield db, camera
    finally:
        db.close()
        engine.dispose()


def _suggestion(camera_id: int, *, parameter: str = "alarm_confirmation_seconds"):
    return TuningSuggestion(
        camera_id=camera_id,
        scope_type="profile",
        scope_id=str(camera_id),
        suggestion_type="policy_tuning",
        parameter_name=parameter,
        old_value="1.0",
        suggested_value="2.0",
        reason_summary="evidencia operacional",
        evidence_count=20,
        confidence_score=0.8,
        status="pending",
    )


def test_apply_reject_and_list_suggestions(workflow_db):
    db, camera = workflow_db
    applicable = _suggestion(camera.id)
    rejected = _suggestion(camera.id, parameter="cooldown_seconds")
    db.add_all([applicable, rejected])
    db.commit()

    updated = apply_suggestion(db, applicable.id)
    reject_suggestion(db, rejected.id)
    payloads = list_suggestion_payloads(db, camera_id=camera.id)

    assert updated.id == camera.id
    assert applicable.status == "applied"
    assert rejected.status == "rejected"
    assert {item["status"] for item in payloads} == {"applied", "rejected"}
    assert db.query(ConfigVersionHistory).count() == 1


def test_learning_policy_validates_mode_before_persisting(workflow_db):
    db, camera = workflow_db

    with pytest.raises(FeedbackWorkflowError) as invalid:
        update_learning_policy(
            db,
            camera.id,
            learning_mode="autonomous_unbounded",
            auto_tuning_enabled=True,
            critical_lock=False,
            max_daily_auto_changes=2,
            min_reviewed_events_for_suggestion=12,
            min_reviewed_events_for_auto_tuning=24,
            rollback_window_hours=48,
        )
    assert invalid.value.status_code == 400

    update_learning_policy(
        db,
        camera.id,
        learning_mode="bounded_auto_tuning",
        auto_tuning_enabled=True,
        critical_lock=False,
        max_daily_auto_changes=2,
        min_reviewed_events_for_suggestion=12,
        min_reviewed_events_for_auto_tuning=24,
        rollback_window_hours=48,
    )
    assert camera.learning_mode == "bounded_auto_tuning"
    assert camera.max_daily_auto_changes == 2


def test_config_history_can_be_listed_and_rolled_back(workflow_db):
    db, camera = workflow_db
    previous_profile = profile_from_camera(camera).to_dict()
    previous_profile["camera_family"] = "bullet"
    history = ConfigVersionHistory(
        camera_id=camera.id,
        config_before=json.dumps(previous_profile),
        config_after=camera.analytics_profile_json,
        change_source="suggestion",
        reason="teste",
        rollback_available=True,
    )
    db.add(history)
    db.commit()

    rolled_back = rollback_config_history(db, history.id)
    payloads = list_config_history_payloads(db, camera.id)

    assert rolled_back.id == camera.id
    assert profile_from_camera(camera).camera_family == "bullet"
    assert history.rollback_available is False
    assert len(payloads) == 2
