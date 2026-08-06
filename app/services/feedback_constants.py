"""Contratos e limites compartilhados do aprendizado por feedback."""

LEARNING_MODES = {
    "manual_only",
    "assisted_policy_tuning",
    "bounded_auto_tuning",
    "offline_active_learning_for_detector",
}

FEEDBACK_LABELS = [
    "true_positive",
    "false_positive",
    "expected_event",
    "inconclusive",
]

PROBABLE_CAUSES = [
    "vegetation_wind",
    "shadow",
    "glass_reflection",
    "headlights",
    "rain",
    "insects_ir",
    "camera_vibration",
    "small_target",
    "poor_roi",
    "threshold_too_low",
    "confirmation_too_short",
    "wrong_direction_rule",
    "normal_human_flow",
]

SHIFT_BUCKETS = {
    "morning": range(5, 12),
    "afternoon": range(12, 18),
    "night": range(18, 24),
    "overnight": range(0, 5),
}

PARAMETER_LIMITS = {
    "person_confidence_min": (0.20, 0.85, 0.03),
    "min_box_area_pct": (0.0, 0.08, 0.002),
    "min_box_height_pct": (0.0, 0.18, 0.01),
    "track_persistence_frames": (1, 20, 1),
    "alarm_confirmation_seconds": (0.0, 8.0, 0.4),
    "dwell_seconds": (0.0, 30.0, 1.0),
    "cooldown_seconds": (0.0, 120.0, 5.0),
}

AUTO_TUNABLE_PARAMETERS = {
    "track_persistence_frames",
    "alarm_confirmation_seconds",
    "dwell_seconds",
    "cooldown_seconds",
    "min_box_area_pct",
    "min_box_height_pct",
    "person_confidence_min",
}

FLOAT_TUNING_PARAMETERS = {
    "alarm_confirmation_seconds",
    "dwell_seconds",
    "cooldown_seconds",
    "min_box_area_pct",
    "min_box_height_pct",
    "person_confidence_min",
}

INT_TUNING_PARAMETERS = {"track_persistence_frames"}
