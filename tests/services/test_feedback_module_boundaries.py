from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, Event, EventFeedback
from app.services import (
    feedback_learning_service,
    feedback_review_service,
    feedback_tuning_service,
)


def test_legacy_facade_reexports_the_extracted_implementations():
    assert (
        feedback_learning_service.build_feedback_metrics
        is feedback_review_service.build_feedback_metrics
    )
    assert (
        feedback_learning_service.record_feedback
        is feedback_review_service.record_feedback
    )
    assert (
        feedback_learning_service.generate_policy_suggestions
        is feedback_tuning_service.generate_policy_suggestions
    )
    assert (
        feedback_learning_service.rollback_camera_config
        is feedback_tuning_service.rollback_camera_config
    )
    assert (
        feedback_learning_service.onedrive_client
        is feedback_review_service.onedrive_client
    )


def test_review_metrics_operate_without_the_legacy_facade():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        camera = Camera(
            name="Portaria",
            ip="10.0.0.8",
            username="operator",
            password="secret",
        )
        db.add(camera)
        db.flush()
        event = Event(
            camera_id=camera.id,
            event_type="intrusion",
            status="new",
            detector_score=0.82,
            event_score=0.91,
            created_at=datetime.now(),
        )
        db.add(event)
        db.flush()
        db.add(
            EventFeedback(
                event_id=event.id,
                camera_id=camera.id,
                label="true_positive",
                reviewed_by="operator",
                reviewed_at=datetime.now(),
            )
        )
        db.commit()

        metrics = feedback_review_service.build_feedback_metrics(
            db,
            camera_id=camera.id,
            days=1,
        )

    engine.dispose()
    assert metrics["reviewed_events"] == 1
    assert metrics["true_positive"] == 1
    assert metrics["operational_precision"] == 1.0


def test_manual_learning_mode_short_circuits_tuning_without_queries():
    camera = Camera(id=9, learning_mode="manual_only")

    suggestions = feedback_tuning_service.generate_policy_suggestions(
        object(),
        camera,
    )

    assert suggestions == []
