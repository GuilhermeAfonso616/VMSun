"""Geracao, aplicacao automatica e rollback de tuning por feedback."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import (
    build_camera_analytic_profile,
    profile_from_camera,
    profile_from_mapping,
    serialize_profile,
)
from app.analytics.camera_policy_builder import profile_to_legacy_fields
from app.db.models import Camera, ConfigVersionHistory, EventFeedback, TuningSuggestion
from app.services.feedback_constants import (
    AUTO_TUNABLE_PARAMETERS,
    FLOAT_TUNING_PARAMETERS,
    INT_TUNING_PARAMETERS,
    PARAMETER_LIMITS,
)
from app.services.feedback_review_service import (
    _ensure_learning_mode,
    _now,
    _parse_json,
    _recent_feedback_query,
    build_feedback_metrics,
)


def _aggregate_camera_feedback(db: Session, camera_id: int, days: int = 30) -> dict[str, Any]:
    metrics = build_feedback_metrics(db, camera_id=camera_id, days=days)
    feedback_rows = (
        _recent_feedback_query(db, camera_id)
        .filter(EventFeedback.reviewed_at >= (_now() - timedelta(days=max(1, int(days)))))
        .all()
    )

    cause_by_label: dict[str, Counter] = defaultdict(Counter)
    by_event_type: dict[str, Counter] = defaultdict(Counter)
    detector_scores: list[float] = []
    event_scores: list[float] = []

    for feedback, event in feedback_rows:
        label = str(feedback.label or "").lower()
        cause = str(feedback.probable_cause or "").lower()
        event_type = str(event.event_type or "unknown")
        if cause:
            cause_by_label[label][cause] += 1
        by_event_type[label][event_type] += 1
        if getattr(event, "detector_score", None) is not None:
            detector_scores.append(float(event.detector_score))
        if getattr(event, "event_score", None) is not None:
            event_scores.append(float(event.event_score))

    return {
        "metrics": metrics,
        "rows": feedback_rows,
        "cause_by_label": {key: dict(counter) for key, counter in cause_by_label.items()},
        "by_event_type": {key: dict(counter) for key, counter in by_event_type.items()},
        "mean_detector_score": round(mean(detector_scores), 4) if detector_scores else None,
        "mean_event_score": round(mean(event_scores), 4) if event_scores else None,
    }


def _camera_version_snapshot(camera: Camera) -> dict[str, Any]:
    return {
        "analytics_profile_json": camera.analytics_profile_json,
        "learning_mode": camera.learning_mode,
        "auto_tuning_enabled": camera.auto_tuning_enabled,
        "critical_lock": camera.critical_lock,
        "max_daily_auto_changes": camera.max_daily_auto_changes,
        "min_reviewed_events_for_suggestion": camera.min_reviewed_events_for_suggestion,
        "min_reviewed_events_for_auto_tuning": camera.min_reviewed_events_for_auto_tuning,
        "rollback_window_hours": camera.rollback_window_hours,
        "roi_name": camera.roi_name,
        "roi_polygon_json": camera.roi_polygon_json,
        "line_start_x": camera.line_start_x,
        "line_start_y": camera.line_start_y,
        "line_end_x": camera.line_end_x,
        "line_end_y": camera.line_end_y,
        "line_direction": camera.line_direction,
        "human_event_modes_json": camera.human_event_modes_json,
        "human_loitering_seconds": camera.human_loitering_seconds,
        "human_detection_sensitivity": camera.human_detection_sensitivity,
    }


def _coerce_tuning_value(parameter_name: str, value: Any) -> Any:
    parsed = _parse_json(value)
    if parameter_name in INT_TUNING_PARAMETERS:
        return int(round(float(parsed)))
    if parameter_name in FLOAT_TUNING_PARAMETERS:
        return float(parsed)
    return parsed


def _profile_points_to_payload(points: Any) -> list[dict[str, float]]:
    payload: list[dict[str, float]] = []
    for point in points or []:
        try:
            if isinstance(point, dict):
                payload.append({"x": float(point["x"]), "y": float(point["y"])})
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                payload.append({"x": float(point[0]), "y": float(point[1])})
        except Exception:
            continue
    return payload


def _set_profile_parameter(profile, parameter_name: str, value: Any) -> bool:
    threshold = profile.threshold_profile
    if hasattr(threshold, parameter_name):
        setattr(threshold, parameter_name, value)
        return True
    if hasattr(profile, parameter_name):
        setattr(profile, parameter_name, value)
        return True
    return False


def _clamp_parameter(parameter_name: str, value: Any) -> Any:
    limits = PARAMETER_LIMITS.get(parameter_name)
    if limits is None:
        return value
    min_value, max_value, step = limits
    if isinstance(value, float):
        value = max(min_value, min(max_value, value))
        if step > 0:
            value = round(value / step) * step
        return round(value, 4)
    if isinstance(value, int):
        return max(int(min_value), min(int(max_value), value))
    return value


def _build_suggestion_record(
    *,
    camera: Camera,
    scope_type: str,
    scope_id: str,
    suggestion_type: str,
    parameter_name: str,
    old_value: Any,
    suggested_value: Any,
    reason_summary: str,
    evidence_count: int,
    confidence_score: float,
) -> TuningSuggestion:
    return TuningSuggestion(
        camera_id=camera.id,
        scope_type=scope_type,
        scope_id=scope_id,
        suggestion_type=suggestion_type,
        parameter_name=parameter_name,
        old_value=json.dumps(old_value, ensure_ascii=False, default=str) if not isinstance(old_value, str) else old_value,
        suggested_value=json.dumps(suggested_value, ensure_ascii=False, default=str) if not isinstance(suggested_value, str) else suggested_value,
        reason_summary=reason_summary,
        evidence_count=evidence_count,
        confidence_score=confidence_score,
        status="pending",
    )


def generate_policy_suggestions(db: Session, camera: Camera, days: int = 30) -> list[TuningSuggestion]:
    mode = _ensure_learning_mode(camera)
    if mode == "manual_only":
        return []

    summary = _aggregate_camera_feedback(db, camera.id, days=days)
    rows = summary["rows"]
    minimum_reviews = max(1, int(camera.min_reviewed_events_for_suggestion or 12))
    if len(rows) < minimum_reviews:
        return []

    camera_profile = profile_from_camera(camera)
    thresholds = camera_profile.threshold_profile
    metrics = summary["metrics"]

    cause_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    event_type_counts: Counter[str] = Counter()

    for feedback, event in rows:
        label = str(feedback.label or "").strip().lower()
        cause = str(feedback.probable_cause or "").strip().lower()
        event_type = str(getattr(event, "event_type", None) or "unknown")
        label_counts[label] += 1
        event_type_counts[event_type] += 1
        if cause:
            cause_counts[cause] += 1

    pending_existing = {
        (
            suggestion.scope_type,
            str(suggestion.scope_id),
            suggestion.parameter_name,
            str(suggestion.suggested_value),
        )
        for suggestion in db.query(TuningSuggestion)
        .filter(
            TuningSuggestion.camera_id == camera.id,
            TuningSuggestion.status == "pending",
        )
        .all()
    }

    def _already_pending(scope_type: str, scope_id: str, parameter_name: str, suggested_value: Any) -> bool:
        normalized_value = (
            json.dumps(suggested_value, ensure_ascii=False, default=str)
            if not isinstance(suggested_value, str)
            else suggested_value
        )
        return (scope_type, scope_id, parameter_name, normalized_value) in pending_existing

    suggestions: list[TuningSuggestion] = []

    def _append_if_new(
        *,
        scope_type: str,
        scope_id: str,
        suggestion_type: str,
        parameter_name: str,
        old_value: Any,
        suggested_value: Any,
        reason_summary: str,
        evidence_count: int,
        confidence_score: float,
    ) -> None:
        if _already_pending(scope_type, scope_id, parameter_name, suggested_value):
            return
        suggestions.append(
            _build_suggestion_record(
                camera=camera,
                scope_type=scope_type,
                scope_id=scope_id,
                suggestion_type=suggestion_type,
                parameter_name=parameter_name,
                old_value=old_value,
                suggested_value=suggested_value,
                reason_summary=reason_summary,
                evidence_count=evidence_count,
                confidence_score=confidence_score,
            )
        )

    false_positive_total = int(metrics["false_positive"])
    true_positive_total = int(metrics["true_positive"])
    operational_precision = float(metrics["operational_precision"])
    mean_detector_score = metrics.get("mean_detector_score")

    vegetation_count = cause_counts.get("vegetation_wind", 0)
    if vegetation_count >= 3 or (false_positive_total >= 5 and vegetation_count >= 2):
        target_frames = _clamp_parameter(
            "track_persistence_frames",
            int(getattr(thresholds, "track_persistence_frames", 5)) + 2,
        )
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="track_persistence_frames",
            old_value=getattr(thresholds, "track_persistence_frames", None),
            suggested_value=target_frames,
            reason_summary="Vegetacao oscilante esta gerando falso positivo recorrente.",
            evidence_count=vegetation_count,
            confidence_score=min(0.95, 0.55 + vegetation_count * 0.05),
        )

        target_confirmation = _clamp_parameter(
            "alarm_confirmation_seconds",
            float(getattr(thresholds, "alarm_confirmation_seconds", 1.0)) + 0.6,
        )
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="alarm_confirmation_seconds",
            old_value=getattr(thresholds, "alarm_confirmation_seconds", None),
            suggested_value=target_confirmation,
            reason_summary="A confirmacao precisa ser mais longa para filtrar oscilacao de vegetacao.",
            evidence_count=vegetation_count,
            confidence_score=min(0.95, 0.55 + vegetation_count * 0.05),
        )

        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="full_frame_forbidden",
            old_value=bool(getattr(thresholds, "full_frame_forbidden", False)),
            suggested_value=True,
            reason_summary="Vegetacao exige ROI mais restrita e bloqueio de full frame.",
            evidence_count=vegetation_count,
            confidence_score=0.82,
        )

        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="ignore_zones_required",
            old_value=bool(getattr(thresholds, "ignore_zones_required", False)),
            suggested_value=True,
            reason_summary="Vegetacao pede ignore zones obrigatorias para bloquear oscilacao nas bordas.",
            evidence_count=vegetation_count,
            confidence_score=0.85,
        )

    small_target_count = cause_counts.get("small_target", 0)
    if small_target_count >= 3 or (false_positive_total >= 5 and label_counts["false_positive"] >= 5):
        target_area = _clamp_parameter(
            "min_box_area_pct",
            float(getattr(thresholds, "min_box_area_pct", 0.005)) + 0.002,
        )
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="min_box_area_pct",
            old_value=getattr(thresholds, "min_box_area_pct", None),
            suggested_value=target_area,
            reason_summary="Eventos pequenos recorrentes indicam alvo minimo abaixo do ideal.",
            evidence_count=max(small_target_count, label_counts["false_positive"]),
            confidence_score=min(0.92, 0.5 + label_counts["false_positive"] * 0.04),
        )

        target_height = _clamp_parameter(
            "min_box_height_pct",
            float(getattr(thresholds, "min_box_height_pct", 0.04)) + 0.01,
        )
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="min_box_height_pct",
            old_value=getattr(thresholds, "min_box_height_pct", None),
            suggested_value=target_height,
            reason_summary="Alvos muito pequenos tambem pedem altura minima mais conservadora.",
            evidence_count=max(small_target_count, label_counts["false_positive"]),
            confidence_score=0.76,
        )

    if cause_counts.get("shadow", 0) >= 2 or cause_counts.get("glass_reflection", 0) >= 2:
        reflection_count = max(cause_counts.get("shadow", 0), cause_counts.get("glass_reflection", 0))
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="ignore_zones_required",
            old_value=bool(getattr(thresholds, "ignore_zones_required", False)),
            suggested_value=True,
            reason_summary="Sombras ou reflexos sugerem mascara ou ignore zones dedicadas.",
            evidence_count=reflection_count,
            confidence_score=0.78,
        )
        target_confirmation = _clamp_parameter(
            "alarm_confirmation_seconds",
            float(getattr(thresholds, "alarm_confirmation_seconds", 1.0)) + 0.4,
        )
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="alarm_confirmation_seconds",
            old_value=getattr(thresholds, "alarm_confirmation_seconds", None),
            suggested_value=target_confirmation,
            reason_summary="Sombras e reflexos pedem confirmacao temporal mais forte.",
            evidence_count=reflection_count,
            confidence_score=0.74,
        )

    if cause_counts.get("normal_human_flow", 0) >= 3:
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="schedule_required",
            old_value=bool(getattr(thresholds, "schedule_required", False)),
            suggested_value=True,
            reason_summary="Fluxo humano normal recorrente sugere contexto de agenda.",
            evidence_count=cause_counts.get("normal_human_flow", 0),
            confidence_score=0.74,
        )

    if event_type_counts.get("zone_presence", 0) >= 4 and false_positive_total >= 3:
        _append_if_new(
            scope_type="profile",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="analytic_goal",
            old_value=camera_profile.analytic_goal,
            suggested_value="line_crossing",
            reason_summary="Zone presence esta gerando falso positivo; line_cross com zone_confirm tende a ser mais robusto.",
            evidence_count=event_type_counts.get("zone_presence", 0),
            confidence_score=0.70,
        )

    if (
        true_positive_total >= 5
        and mean_detector_score is not None
        and operational_precision >= 0.65
        and float(mean_detector_score) < float(getattr(thresholds, "person_confidence_min", 0.5))
    ):
        target_conf = _clamp_parameter(
            "person_confidence_min",
            max(0.20, float(getattr(thresholds, "person_confidence_min", 0.5)) - 0.03),
        )
        _append_if_new(
            scope_type="camera",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="person_confidence_min",
            old_value=getattr(thresholds, "person_confidence_min", None),
            suggested_value=target_conf,
            reason_summary="Ha muitos verdadeiros positivos com score do detector abaixo do threshold vigente.",
            evidence_count=true_positive_total,
            confidence_score=0.62,
        )

    if false_positive_total >= 5 and event_type_counts.get("zone_presence", 0) >= 3:
        _append_if_new(
            scope_type="profile",
            scope_id=str(camera.id),
            suggestion_type="policy_tuning",
            parameter_name="analytic_goal",
            old_value=camera_profile.analytic_goal,
            suggested_value="zone_entry",
            reason_summary="Zona ampla com falso positivo recorrente pode se beneficiar de zone_entry ou line_crossing.",
            evidence_count=false_positive_total,
            confidence_score=0.66,
        )

    if suggestions:
        db.add_all(suggestions)
        db.flush()
    return suggestions


def apply_tuning_suggestion(db: Session, suggestion: TuningSuggestion, change_source: str = "suggestion") -> Camera | None:
    camera = db.query(Camera).filter(Camera.id == suggestion.camera_id).first()
    if not camera:
        return None

    profile_before = profile_from_camera(camera)
    before_payload = profile_before.to_dict()
    current_profile = build_camera_analytic_profile(
        preset_name=profile_before.preset_name,
        camera_family=profile_before.camera_family,
        scene_profile=profile_before.scene_profile,
        analytic_goal=profile_before.analytic_goal,
        nuisance_profile=asdict(profile_before.nuisance_profile),
        threshold_profile=asdict(profile_before.threshold_profile),
        roi_polygon=_profile_points_to_payload(profile_before.roi_polygon),
        ignore_zones=list(profile_before.ignore_zones or []),
        subzones=list(profile_before.subzones or []),
        directional_lines=list(profile_before.directional_lines or []),
        schedule=profile_before.schedule,
        manual_overrides=dict(profile_before.manual_overrides or {}),
        notes=list(profile_before.notes or []),
    )
    parameter_name = suggestion.parameter_name
    parsed_old = _parse_json(suggestion.old_value)
    parsed_new = _clamp_parameter(parameter_name, _coerce_tuning_value(parameter_name, suggestion.suggested_value))

    if parameter_name in AUTO_TUNABLE_PARAMETERS:
        if not _set_profile_parameter(current_profile, parameter_name, parsed_new):
            raise ValueError(f"Parametro desconhecido no perfil: {parameter_name}")
    else:
        return None

    camera.analytics_profile_json = serialize_profile(current_profile)
    legacy_values = profile_to_legacy_fields(current_profile)
    camera.roi_name = legacy_values.get("roi_name")
    camera.roi_polygon_json = legacy_values.get("roi_polygon_json")
    camera.line_start_x = legacy_values.get("line_start_x")
    camera.line_start_y = legacy_values.get("line_start_y")
    camera.line_end_x = legacy_values.get("line_end_x")
    camera.line_end_y = legacy_values.get("line_end_y")
    camera.line_direction = legacy_values.get("line_direction")
    camera.analytics_coordinate_space = legacy_values.get("analytics_coordinate_space", "source")
    camera.human_event_modes_json = legacy_values.get("human_event_modes_json")
    camera.human_loitering_seconds = legacy_values.get("human_loitering_seconds")
    camera.human_detection_sensitivity = legacy_values.get("human_detection_sensitivity")

    after_payload = current_profile.to_dict()
    db.add(
        ConfigVersionHistory(
            camera_id=camera.id,
            config_before=json.dumps(before_payload, ensure_ascii=False, default=str),
            config_after=json.dumps(after_payload, ensure_ascii=False, default=str),
            change_source=change_source,
            reason=suggestion.reason_summary,
            rollback_available=True,
        )
    )
    suggestion.status = "applied"
    suggestion.applied_at = _now()
    db.flush()
    return camera


def maybe_apply_bounded_auto_tuning(db: Session, camera: Camera) -> list[TuningSuggestion]:
    if _ensure_learning_mode(camera) != "bounded_auto_tuning":
        return []
    if not bool(getattr(camera, "auto_tuning_enabled", False)):
        return []
    if bool(getattr(camera, "critical_lock", False)):
        return []

    today_start = _now().date()
    auto_changes_today = (
        db.query(ConfigVersionHistory)
        .filter(
            ConfigVersionHistory.camera_id == camera.id,
            ConfigVersionHistory.change_source == "auto_tuning",
            ConfigVersionHistory.created_at >= datetime.combine(today_start, datetime.min.time()),
        )
        .count()
    )
    if auto_changes_today >= int(camera.max_daily_auto_changes or 1):
        return []

    threshold = int(camera.min_reviewed_events_for_auto_tuning or 24)
    suggestions = (
        db.query(TuningSuggestion)
        .filter(
            TuningSuggestion.camera_id == camera.id,
            TuningSuggestion.status == "pending",
        )
        .order_by(TuningSuggestion.confidence_score.desc(), TuningSuggestion.evidence_count.desc(), TuningSuggestion.id.asc())
        .all()
    )
    applied: list[TuningSuggestion] = []
    for suggestion in suggestions:
        if suggestion.parameter_name not in AUTO_TUNABLE_PARAMETERS:
            continue
        if suggestion.evidence_count < threshold:
            continue
        if float(suggestion.confidence_score or 0.0) < 0.70:
            continue
        result = apply_tuning_suggestion(db, suggestion, change_source="auto_tuning")
        if result is not None:
            applied.append(suggestion)
            break

    return applied


def rollback_camera_config(db: Session, history: ConfigVersionHistory) -> Camera | None:
    camera = db.query(Camera).filter(Camera.id == history.camera_id).first()
    if not camera:
        return None

    previous = _parse_json(history.config_before) or {}
    profile = profile_from_mapping(previous)
    camera.analytics_profile_json = serialize_profile(profile)
    legacy_values = profile_to_legacy_fields(profile)
    camera.roi_name = legacy_values.get("roi_name")
    camera.roi_polygon_json = legacy_values.get("roi_polygon_json")
    camera.line_start_x = legacy_values.get("line_start_x")
    camera.line_start_y = legacy_values.get("line_start_y")
    camera.line_end_x = legacy_values.get("line_end_x")
    camera.line_end_y = legacy_values.get("line_end_y")
    camera.line_direction = legacy_values.get("line_direction")
    camera.analytics_coordinate_space = legacy_values.get("analytics_coordinate_space", "source")
    camera.human_event_modes_json = legacy_values.get("human_event_modes_json")
    camera.human_loitering_seconds = legacy_values.get("human_loitering_seconds")
    camera.human_detection_sensitivity = legacy_values.get("human_detection_sensitivity")

    db.add(
        ConfigVersionHistory(
            camera_id=camera.id,
            config_before=history.config_after,
            config_after=history.config_before,
            change_source="reverted",
            reason=history.reason or "rollback",
            rollback_available=False,
        )
    )
    history.rollback_available = False
    db.flush()
    return camera
