"""Fachada compatível para os serviços de aprendizado por feedback.

Novos consumidores devem importar diretamente de ``feedback_review_service``,
``feedback_tuning_service`` ou ``feedback_constants`` conforme a responsabilidade.
"""

from app.services.feedback_constants import (
    AUTO_TUNABLE_PARAMETERS,
    FEEDBACK_LABELS,
    FLOAT_TUNING_PARAMETERS,
    INT_TUNING_PARAMETERS,
    LEARNING_MODES,
    PARAMETER_LIMITS,
    PROBABLE_CAUSES,
    SHIFT_BUCKETS,
)
from app.services.feedback_review_service import (
    build_active_learning_queue,
    build_event_review_item,
    build_event_review_payload,
    build_feedback_metrics,
    evaluate_drift,
    onedrive_client,
    record_feedback,
)
from app.services.feedback_tuning_service import (
    apply_tuning_suggestion,
    generate_policy_suggestions,
    maybe_apply_bounded_auto_tuning,
    rollback_camera_config,
)


__all__ = [
    "AUTO_TUNABLE_PARAMETERS",
    "FEEDBACK_LABELS",
    "FLOAT_TUNING_PARAMETERS",
    "INT_TUNING_PARAMETERS",
    "LEARNING_MODES",
    "PARAMETER_LIMITS",
    "PROBABLE_CAUSES",
    "SHIFT_BUCKETS",
    "apply_tuning_suggestion",
    "build_active_learning_queue",
    "build_event_review_item",
    "build_event_review_payload",
    "build_feedback_metrics",
    "evaluate_drift",
    "generate_policy_suggestions",
    "maybe_apply_bounded_auto_tuning",
    "onedrive_client",
    "record_feedback",
    "rollback_camera_config",
]
