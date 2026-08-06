"""Revisao operacional, metricas, fila ativa e drift de feedback."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import (
    build_camera_analytic_profile,
    profile_from_camera,
)
from app.analytics.camera_policy_builder import build_profile_preview
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Camera, Event, EventFeedback, TuningSuggestion
from app.services.feedback_constants import (
    AUTO_TUNABLE_PARAMETERS,
    FEEDBACK_LABELS,
    LEARNING_MODES,
    PROBABLE_CAUSES,
    SHIFT_BUCKETS,
)
from app.services.local_clip_retention_service import (
    local_clip_video_path,
    prune_local_review_clips,
)
from app.services.onedrive_client import onedrive_client
from app.services.operational_diagnostics_service import humanize_revalidator_reason
from app.services.revalidator_dataset_collector import (
    collect_false_positive_revalidator_sample,
    collect_person_revalidator_sample,
    collect_uncertain_revalidator_sample,
)
from app.services.revalidator_review_audit_service import write_review_audit_json


logger = get_logger("app.feedback_review")


def _review_audit_history_rows(db: Session, camera_id: int) -> list[tuple[EventFeedback, Event]]:
    return (
        db.query(EventFeedback, Event)
        .join(Event, Event.id == EventFeedback.event_id)
        .filter(EventFeedback.camera_id == camera_id)
        .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
        .limit(max(1, int(settings.region_memory_history_limit or 5000)))
        .all()
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _extract_revalidator_score(details: Any) -> float | None:
    text = str(details or "")
    marker = "revalidator_person="
    if marker not in text:
        return None
    raw = text.split(marker, 1)[1].split()[0].strip()
    try:
        return float(raw)
    except Exception:
        return None


def _detail_segments(details: Any) -> list[str]:
    return [segment.strip() for segment in str(details or "").split("|") if segment.strip()]


def _find_detail_segment(details: Any, marker: str) -> str | None:
    for segment in _detail_segments(details):
        if segment.startswith(marker):
            return segment
    return None


def _extract_segment_value(segment: str | None, marker: str) -> str | None:
    if not segment or marker not in segment:
        return None
    raw = segment.split(marker, 1)[1].split()[0].strip()
    return raw or None


def _extract_segment_float(segment: str | None, marker: str) -> float | None:
    raw = _extract_segment_value(segment, marker)
    try:
        return float(raw) if raw is not None else None
    except Exception:
        return None


def _build_ai_validation_summary(event: Event) -> dict[str, Any]:
    details = getattr(event, "details", None)
    details_text = str(details or "")
    detector_score = getattr(event, "detector_score", None)
    ia2_segment = _find_detail_segment(details, "revalidator_person=")
    ia2_skipped_segment = _find_detail_segment(details, "revalidator_skipped=")
    ia3_segment = _find_detail_segment(details, "far_revalidator_person=")
    ia3_skipped_segment = _find_detail_segment(details, "far_revalidator_skipped=")
    ia2_score = _extract_segment_float(ia2_segment, "revalidator_person=")
    ia2_threshold = _extract_segment_float(ia2_segment, "threshold=")
    ia2_skipped = _extract_segment_value(ia2_skipped_segment, "revalidator_skipped=")
    ia2_canceled = "revalidator_canceled=true" in details_text
    ia3_score = _extract_segment_float(ia3_segment, "far_revalidator_person=")
    ia3_threshold = _extract_segment_float(ia3_segment, "threshold=")
    ia3_skipped = _extract_segment_value(ia3_skipped_segment, "far_revalidator_skipped=")

    ia1_status = "confirmou pessoa"
    ia1_text = "IA1 confirmou: pessoa"
    if detector_score is not None:
        ia1_text = f"IA1 confirmou: pessoa ({float(detector_score):.3f})"

    if ia2_skipped:
        reason = humanize_revalidator_reason(ia2_skipped)
        ia2_status = "indisponivel"
        ia2_text = f"IA2 indisponivel: {reason['title']} - {reason['detail']}"
    elif ia2_score is None:
        ia2_status = "sem dados"
        ia2_text = "IA2 sem dados neste evento"
    else:
        threshold = ia2_threshold if ia2_threshold is not None else 0.0
        passed_safety_threshold = ia2_score >= threshold
        raw_confirmed_person = ia2_score >= 0.5
        if raw_confirmed_person:
            ia2_status = "confirmou pessoa"
            ia2_text = f"IA2 confirmou: pessoa ({ia2_score:.3f})"
        elif passed_safety_threshold:
            ia2_status = "recusou visualmente pessoa"
            ia2_text = (
                f"IA2 recusou visualmente: pessoa ({ia2_score:.3f}), "
                "mas não bloqueou pela política conservadora"
            )
        else:
            ia2_status = "recusou pessoa"
            ia2_text = f"IA2 recusou: pessoa ({ia2_score:.3f})"
        if ia2_canceled:
            ia2_text = f"{ia2_text} - cancelou pelo block conservador"

    if ia3_score is not None:
        threshold = ia3_threshold if ia3_threshold is not None else 0.0
        if ia3_score >= threshold:
            ia3_status = "protegeu pessoa"
            ia3_text = f"IA3 protegeu: score pessoa acima do limiar ({ia3_score:.3f})"
        else:
            ia3_status = "disse nao pessoa"
            ia3_text = f"IA3 foi solicitada e disse que: nao e uma pessoa ({ia3_score:.3f})"
    elif ia3_skipped:
        reason = humanize_revalidator_reason(ia3_skipped)
        ia3_status = "solicitada sem resultado"
        ia3_text = f"IA3 solicitada sem resultado: {reason['title']} - {reason['detail']}"
    else:
        ia3_status = "não solicitada"
        ia3_text = "IA3 não foi solicitada"

    consensus_markers = {
        "consensus_block_candidate=true": "strict",
        "balanced_block_candidate=true": "balanced",
        "ia3_confirmed_dynamic_candidate=true": "ia3_confirmed_dynamic",
        "ia2_dominant_ia3_non_person_candidate=true": "ia2_dominant_ia3_non_person",
        "ia2_only_balanced_candidate=true": "ia2_only_balanced",
    }
    consensus_profile = next(
        (profile for marker, profile in consensus_markers.items() if marker in details_text),
        None,
    )
    consensus_block_applied = "consensus_revalidator_canceled=true" in details_text
    production_block_candidate = bool(consensus_profile or consensus_block_applied)
    if consensus_block_applied:
        consensus_status = "bloqueou em producao"
        consensus_text = "BLOQUEADO por consenso no fluxo de producao"
    elif consensus_profile:
        consensus_status = "bloquearia em producao"
        consensus_text = f"BLOQUEARIA EM PROD por consenso ({consensus_profile})"
    else:
        consensus_status = "audit"
        consensus_text = "Sem bloqueio de producao indicado pelo consenso"

    return {
        "ia1_status": ia1_status,
        "ia1_text": ia1_text,
        "ia1_score": detector_score,
        "ia2_status": ia2_status,
        "ia2_text": ia2_text,
        "ia2_score": ia2_score,
        "ia2_threshold": ia2_threshold,
        "ia3_status": ia3_status,
        "ia3_text": ia3_text,
        "ia3_score": ia3_score,
        "ia3_threshold": ia3_threshold,
        "consensus_status": consensus_status,
        "consensus_text": consensus_text,
        "consensus_profile": consensus_profile,
        "production_block_candidate": production_block_candidate,
        "consensus_block_applied": consensus_block_applied,
    }


def _shift_for(dt: datetime | None) -> str:
    hour = int((dt or _now()).hour)
    for shift_name, hour_range in SHIFT_BUCKETS.items():
        if hour in hour_range:
            return shift_name
    return "night"


def _camera_profile(camera: Camera):
    return profile_from_camera(camera)


def _query_active_cameras(db: Session):
    query = db.query(Camera)
    is_deleted_column = getattr(Camera, "is_deleted", None)
    if is_deleted_column is not None:
        query = query.filter(is_deleted_column == False)  # noqa: E712
    return query


def _ensure_learning_mode(camera: Camera) -> str:
    mode = str(getattr(camera, "learning_mode", None) or "assisted_policy_tuning").strip().lower()
    return mode if mode in LEARNING_MODES else "assisted_policy_tuning"


def build_event_review_item(event: Event, camera: Camera | None, feedback: EventFeedback | None = None) -> dict[str, Any]:
    profile = _camera_profile(camera) if camera else build_camera_analytic_profile(
        preset_name="legacy_default",
        camera_family="dome",
        scene_profile="indoor_discreet",
        analytic_goal="intrusion",
    )
    preview = build_profile_preview(profile, frame_width=1920, frame_height=1080)
    active_profile_snapshot = _parse_json(getattr(event, "active_profile_snapshot", None)) or profile.to_dict()
    threshold_snapshot = _parse_json(getattr(event, "threshold_snapshot", None)) or preview.get("thresholds", {})
    nuisance_snapshot = _parse_json(getattr(event, "nuisance_profile_snapshot", None)) or profile.nuisance_profile.enabled_flags()
    clip_metadata = _load_clip_metadata(getattr(event, "clip_path", None))
    clip_video_available = (
        local_clip_video_path(event) is not None
        or (
            bool(getattr(event, "clip_remote_item_id", None))
            and str(getattr(event, "clip_remote_status", "") or "").lower() == "uploaded"
        )
    )

    return {
        "id": event.id,
        "camera_id": event.camera_id,
        "camera_name": camera.name if camera else f"Câmera {event.camera_id}",
        "camera_family": getattr(event, "camera_family", None) or profile.camera_family,
        "scene_profile": getattr(event, "scene_profile", None) or profile.scene_profile,
        "event_type": event.event_type,
        "rule_id": getattr(event, "rule_id", None) or getattr(event, "alarm_category", None),
        "roi_id": getattr(event, "roi_id", None),
        "zone_id": getattr(event, "zone_id", None),
        "status": getattr(event, "status", "new"),
        "created_at": event.created_at,
        "started_at": getattr(event, "started_at", None) or event.created_at,
        "ended_at": getattr(event, "ended_at", None) or event.created_at,
        "detector_score": getattr(event, "detector_score", None),
        "event_score": getattr(event, "event_score", None) or event.confidence,
        "revalidator_score": _extract_revalidator_score(getattr(event, "details", None)),
        "ai_validation_summary": _build_ai_validation_summary(event),
        "ai_validation_label": getattr(event, "ai_validation_label", None),
        "ai_validation_reason": getattr(event, "ai_validation_reason", None),
        "ai_validation_at": getattr(event, "ai_validation_at", None),
        "snapshot_path": event.snapshot_path,
        "clip_path": getattr(event, "clip_path", None),
        "clip_remote_web_url": getattr(event, "clip_remote_web_url", None),
        "clip_remote_status": getattr(event, "clip_remote_status", None),
        "clip_metadata": clip_metadata,
        "clip_video_available": clip_video_available,
        "active_profile_snapshot": active_profile_snapshot,
        "threshold_snapshot": threshold_snapshot,
        "nuisance_profile_snapshot": nuisance_snapshot,
        "feedback": {
            "label": feedback.label if feedback else None,
            "probable_cause": feedback.probable_cause if feedback else None,
            "operator_note": feedback.operator_note if feedback else None,
            "reviewed_by": feedback.reviewed_by if feedback else None,
            "reviewed_at": feedback.reviewed_at if feedback else None,
        },
        "preview": preview,
    }


def _format_clip_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M:%S")
    except Exception:
        return str(value)


def _load_clip_metadata(clip_path: str | None) -> dict[str, Any]:
    if not clip_path:
        return {}
    try:
        metadata_path = Path(clip_path) / "metadata.json"
        if not metadata_path.exists():
            return {}
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for key in ("before_captured_at", "event_captured_at", "after_captured_at"):
        metadata[f"{key}_label"] = _format_clip_time(metadata.get(key))
    return metadata


def record_feedback(
    db: Session,
    *,
    event: Event,
    label: str,
    probable_cause: str | None,
    operator_note: str | None,
    reviewed_by: str | None,
) -> EventFeedback:
    normalized_label = str(label or "").strip().lower()
    if normalized_label not in FEEDBACK_LABELS:
        raise ValueError(f"label inválido: {label}")
    normalized_cause = str(probable_cause or "").strip().lower() or None
    if normalized_cause is not None and normalized_cause not in PROBABLE_CAUSES:
        raise ValueError(f"probable_cause inválido: {probable_cause}")

    feedback = EventFeedback(
        event_id=event.id,
        camera_id=event.camera_id,
        label=normalized_label,
        probable_cause=normalized_cause,
        operator_note=operator_note,
        reviewed_by=reviewed_by or "operator",
        reviewed_at=_now(),
    )
    db.add(feedback)

    if normalized_label == "false_positive":
        event.status = "closed"
        event.is_alarm_active = False
        event.closed_at = _now()
        try:
            collect_false_positive_revalidator_sample(
                event=event,
                feedback=feedback,
                probable_cause=normalized_cause,
                operator_note=operator_note,
            )
        except Exception:
            # A curadoria do dataset nao pode impedir o registro da avaliacao.
            logger.exception(
                "Failed to collect false-positive revalidator sample",
                extra={
                    "camera_id": event.camera_id,
                    "event_id": event.id,
                    "action": "revalidator_dataset_collect",
                    "status": "error",
                    "reason": "collector_exception",
                },
            )
    elif normalized_label == "true_positive":
        if event.status == "new":
            event.status = "acknowledged"
        event.acknowledged_at = event.acknowledged_at or _now()
        try:
            collect_person_revalidator_sample(
                event=event,
                feedback=feedback,
                decision_source="operator_confirmed",
                operator_note=operator_note,
            )
        except Exception:
            # A curadoria do dataset nao pode impedir o registro da avaliacao.
            logger.exception(
                "Failed to collect person revalidator sample",
                extra={
                    "camera_id": event.camera_id,
                    "event_id": event.id,
                    "action": "revalidator_dataset_collect",
                    "status": "error",
                    "reason": "collector_exception",
                },
            )
    elif normalized_label == "expected_event":
        event.status = "acknowledged"
        event.acknowledged_at = event.acknowledged_at or _now()
        try:
            collect_person_revalidator_sample(
                event=event,
                feedback=feedback,
                decision_source="reviewed_event",
                operator_note=operator_note,
            )
        except Exception:
            # A curadoria do dataset nao pode impedir o registro da avaliacao.
            logger.exception(
                "Failed to collect person revalidator sample",
                extra={
                    "camera_id": event.camera_id,
                    "event_id": event.id,
                    "action": "revalidator_dataset_collect",
                    "status": "error",
                    "reason": "collector_exception",
                },
            )
    else:
        event.status = event.status or "new"
        try:
            collect_uncertain_revalidator_sample(
                event=event,
                feedback=feedback,
                probable_cause=normalized_cause,
                operator_note=operator_note,
            )
        except Exception:
            # A curadoria do dataset nao pode impedir o registro da avaliacao.
            logger.exception(
                "Failed to collect uncertain revalidator sample",
                extra={
                    "camera_id": event.camera_id,
                    "event_id": event.id,
                    "action": "revalidator_dataset_collect",
                    "status": "error",
                    "reason": "collector_exception",
                },
            )

    if operator_note is not None:
        event.operator_note = operator_note.strip() or None

    if getattr(event, "clip_remote_item_id", None) and getattr(event, "clip_remote_status", None) == "uploaded":
        try:
            if onedrive_client.delete_item(str(event.clip_remote_item_id)):
                event.clip_remote_status = "deleted_after_review"
        except Exception:
            # A limpeza remota nao pode impedir o registro da avaliacao.
            logger.exception(
                "Failed to delete reviewed OneDrive clip",
                extra={
                    "camera_id": event.camera_id,
                    "event_id": event.id,
                    "action": "onedrive_clip_delete_after_review",
                    "status": "degraded",
                    "reason": "delete_failed",
                },
            )

    db.flush()
    prune_local_review_clips(db, now=_now())

    db.flush()
    try:
        write_review_audit_json(
            event,
            feedback,
            history_rows=_review_audit_history_rows(db, event.camera_id),
        )
    except Exception:
        # A auditoria das IAs nao pode impedir a avaliacao do operador.
        logger.exception(
            "Failed to write review revalidator audit JSON",
            extra={
                "camera_id": event.camera_id,
                "event_id": event.id,
                "action": "review_revalidator_audit",
                "status": "error",
                "reason": "audit_exception",
            },
        )
    return feedback


def _recent_feedback_query(db: Session, camera_id: int | None = None):
    query = db.query(EventFeedback, Event).join(Event, Event.id == EventFeedback.event_id)
    if camera_id is not None:
        query = query.filter(EventFeedback.camera_id == camera_id)
    return query.order_by(EventFeedback.reviewed_at.desc())


def build_feedback_metrics(db: Session, camera_id: int | None = None, days: int = 30) -> dict[str, Any]:
    since = _now() - timedelta(days=max(1, int(days)))
    rows = (
        _recent_feedback_query(db, camera_id)
        .filter(EventFeedback.reviewed_at >= since)
        .all()
    )
    canceled_query = db.query(Event).filter(Event.created_at >= since, Event.status == "canceled")
    if camera_id is not None:
        canceled_query = canceled_query.filter(Event.camera_id == camera_id)
    canceled_events = canceled_query.all()
    canceled_event_ids = [event.id for event in canceled_events]
    canceled_feedbacks = []
    if canceled_event_ids:
        canceled_feedbacks = (
            db.query(EventFeedback)
            .filter(EventFeedback.event_id.in_(canceled_event_ids))
            .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
            .all()
        )
    canceled_latest_feedback: dict[int, EventFeedback] = {}
    for feedback in canceled_feedbacks:
        if feedback.event_id not in canceled_latest_feedback:
            canceled_latest_feedback[feedback.event_id] = feedback

    total = len(rows)
    label_counts = Counter()
    cause_counts = Counter()
    camera_counts = Counter()
    scene_counts = Counter()
    profile_counts = Counter()
    event_counts = Counter()
    false_positive = 0
    true_positive = 0
    expected_event = 0
    inconclusive = 0
    detector_scores: list[float] = []
    event_scores: list[float] = []

    for feedback, event in rows:
        label = str(feedback.label or "").strip().lower()
        cause = str(feedback.probable_cause or "").strip().lower()
        label_counts[label] += 1
        if cause:
            cause_counts[cause] += 1
        camera_counts[event.camera_id] += 1
        scene_counts[str(getattr(event, "scene_profile", None) or "unknown")] += 1
        profile_counts[str(getattr(event, "camera_family", None) or "unknown")] += 1
        event_counts[str(event.event_type or "unknown")] += 1
        if label == "false_positive":
            false_positive += 1
        elif label == "true_positive":
            true_positive += 1
        elif label == "expected_event":
            expected_event += 1
        else:
            inconclusive += 1
        if getattr(event, "detector_score", None) is not None:
            detector_scores.append(float(event.detector_score))
        if getattr(event, "event_score", None) is not None:
            event_scores.append(float(event.event_score))

    reviewed_events = total
    precision_den = true_positive + false_positive
    operational_precision = (true_positive / precision_den) if precision_den else 0.0
    false_positive_rate = (false_positive / total) if total else 0.0
    true_positive_rate = (true_positive / total) if total else 0.0
    expected_event_rate = (expected_event / total) if total else 0.0
    inconclusive_rate = (inconclusive / total) if total else 0.0
    canceled_reviewed = len(canceled_latest_feedback)
    canceled_label_counts = Counter(
        str(feedback.label or "").strip().lower()
        for feedback in canceled_latest_feedback.values()
    )
    canceled_false_positive = int(canceled_label_counts.get("false_positive", 0))
    canceled_true_positive = int(canceled_label_counts.get("true_positive", 0))
    canceled_expected_event = int(canceled_label_counts.get("expected_event", 0))
    canceled_inconclusive = max(0, canceled_reviewed - canceled_false_positive - canceled_true_positive - canceled_expected_event)
    canceled_precision_den = canceled_false_positive + canceled_true_positive
    canceled_efficiency = (canceled_false_positive / canceled_precision_den) if canceled_precision_den else 0.0
    canceled_person_miss_rate = (canceled_true_positive / canceled_precision_den) if canceled_precision_den else 0.0

    return {
        "reviewed_events": reviewed_events,
        "false_positive": false_positive,
        "true_positive": true_positive,
        "expected_event": expected_event,
        "inconclusive": inconclusive,
        "false_positive_rate": round(false_positive_rate, 4),
        "true_positive_rate": round(true_positive_rate, 4),
        "expected_event_rate": round(expected_event_rate, 4),
        "inconclusive_rate": round(inconclusive_rate, 4),
        "operational_precision": round(operational_precision, 4),
        "canceled_events": len(canceled_events),
        "canceled_reviewed": canceled_reviewed,
        "canceled_false_positive": canceled_false_positive,
        "canceled_true_positive": canceled_true_positive,
        "canceled_expected_event": canceled_expected_event,
        "canceled_inconclusive": canceled_inconclusive,
        "canceled_efficiency": round(canceled_efficiency, 4),
        "canceled_person_miss_rate": round(canceled_person_miss_rate, 4),
        "canceled_label_counts": dict(canceled_label_counts),
        "alert_volume_per_day": round(reviewed_events / max(1, days), 2),
        "top_false_positive_causes": [item[0] for item in cause_counts.most_common(5)],
        "top_cameras": [item[0] for item in camera_counts.most_common(5)],
        "top_profiles": [item[0] for item in profile_counts.most_common(5)],
        "top_event_types": [item[0] for item in event_counts.most_common(5)],
        "mean_detector_score": round(mean(detector_scores), 4) if detector_scores else None,
        "mean_event_score": round(mean(event_scores), 4) if event_scores else None,
        "label_counts": dict(label_counts),
        "cause_counts": dict(cause_counts),
        "camera_counts": dict(camera_counts),
        "scene_counts": dict(scene_counts),
        "profile_counts": dict(profile_counts),
        "event_counts": dict(event_counts),
    }


def build_active_learning_queue(db: Session, camera_id: int | None = None, days: int = 30) -> list[dict[str, Any]]:
    since = _now() - timedelta(days=max(1, int(days)))
    query = _recent_feedback_query(db, camera_id).filter(EventFeedback.reviewed_at >= since)
    queue: list[dict[str, Any]] = []
    for feedback, event in query.all():
        label = str(feedback.label or "").lower()
        cause = str(feedback.probable_cause or "").lower()
        if label in {"false_positive", "inconclusive"} or (label == "true_positive" and (_safe_float(getattr(event, "detector_score", None), 0.0) < 0.55)):
            queue.append(
                {
                    "event_id": event.id,
                    "camera_id": event.camera_id,
                    "event_type": event.event_type,
                    "label": feedback.label,
                    "probable_cause": cause,
                    "snapshot_path": event.snapshot_path,
                    "clip_path": getattr(event, "clip_path", None),
                    "camera_family": getattr(event, "camera_family", None),
                    "scene_profile": getattr(event, "scene_profile", None),
                    "reviewed_at": feedback.reviewed_at,
                }
            )
    return queue


def evaluate_drift(db: Session, camera_id: int, short_days: int = 7, long_days: int = 30) -> dict[str, Any]:
    short_metrics = build_feedback_metrics(db, camera_id=camera_id, days=short_days)
    long_metrics = build_feedback_metrics(db, camera_id=camera_id, days=long_days)

    precision_drop = float(long_metrics["operational_precision"]) - float(short_metrics["operational_precision"])
    fp_rise = float(short_metrics["false_positive_rate"]) - float(long_metrics["false_positive_rate"])
    detector_shift = (short_metrics.get("mean_detector_score") or 0.0) - (long_metrics.get("mean_detector_score") or 0.0)
    event_shift = (short_metrics.get("mean_event_score") or 0.0) - (long_metrics.get("mean_event_score") or 0.0)

    drift_detected = precision_drop < -0.10 or fp_rise > 0.12 or abs(detector_shift) > 0.08 or abs(event_shift) > 0.08
    return {
        "drift_detected": drift_detected,
        "short_window": short_metrics,
        "long_window": long_metrics,
        "precision_drop": round(precision_drop, 4),
        "false_positive_rise": round(fp_rise, 4),
        "detector_score_shift": round(detector_shift, 4),
        "event_score_shift": round(event_shift, 4),
    }


def build_event_review_payload(
    db: Session,
    *,
    camera_id: int | None = None,
    label: str | None = None,
    probable_cause: str | None = None,
    profile: str | None = None,
    turn: str | None = None,
    status: str | None = None,
    days: int = 30,
    limit: int = 80,
    include_ai_validated: bool = False,
) -> dict[str, Any]:
    since = _now() - timedelta(days=max(1, int(days)))
    query = (
        db.query(Event)
        .filter(Event.created_at >= since)
        .order_by(Event.id.desc())
    )
    if camera_id is not None:
        query = query.filter(Event.camera_id == camera_id)
    if status:
        query = query.filter(Event.status == status)
    else:
        query = query.filter(or_(Event.status.is_(None), Event.status != "canceled"))

    # Evento ja concluido por consenso IA2+IA3 nao ocupa a fila do operador, mas
    # continua acessivel para conferencia quando pedido explicitamente.
    ai_validated_query = query.filter(Event.ai_validation_label.isnot(None))
    ai_validated_count = ai_validated_query.count()
    ai_validated_by_label = Counter(
        str(row.ai_validation_label)
        for row in ai_validated_query.with_entities(Event.ai_validation_label).all()
    )
    if not include_ai_validated:
        query = query.filter(Event.ai_validation_label.is_(None))

    event_limit = max(20, min(400, int(limit or 80)))
    events = query.limit(event_limit).all()
    camera_ids = {event.camera_id for event in events}
    cameras = {
        camera.id: camera
        for camera in _query_active_cameras(db).filter(Camera.id.in_(camera_ids or {0})).all()
    }
    feedback_map: dict[int, EventFeedback] = {}
    for feedback in (
        db.query(EventFeedback)
        .filter(EventFeedback.event_id.in_([event.id for event in events] or [0]))
        .order_by(EventFeedback.reviewed_at.desc())
        .all()
    ):
        if feedback.event_id not in feedback_map:
            feedback_map[feedback.event_id] = feedback

    rows = []
    profile_options = set()
    for event in events:
        camera = cameras.get(event.camera_id)
        feedback = feedback_map.get(event.id)
        item = build_event_review_item(event, camera, feedback)
        item["shift"] = _shift_for(event.created_at)
        item["label"] = feedback.label if feedback else None
        item["probable_cause"] = feedback.probable_cause if feedback else None
        item["reviewed_by"] = feedback.reviewed_by if feedback else None
        item["reviewed_at"] = feedback.reviewed_at if feedback else None
        if label and item["label"] != label:
            continue
        if probable_cause and item["probable_cause"] != probable_cause:
            continue
        if profile and profile not in {item["scene_profile"], item["camera_family"]}:
            continue
        if turn and item["shift"] != turn:
            continue
        profile_options.add(item["scene_profile"])
        profile_options.add(item["camera_family"])
        rows.append(item)

    metrics = build_feedback_metrics(db, camera_id=camera_id, days=days)
    suggestions_query = (
        db.query(TuningSuggestion)
        .join(Camera, Camera.id == TuningSuggestion.camera_id)
        .filter(
            TuningSuggestion.status == "pending",
            TuningSuggestion.parameter_name.in_(AUTO_TUNABLE_PARAMETERS),
        )
    )
    is_deleted_column = getattr(Camera, "is_deleted", None)
    if is_deleted_column is not None:
        suggestions_query = suggestions_query.filter(is_deleted_column == False)  # noqa: E712
    suggestions = suggestions_query.order_by(TuningSuggestion.id.desc()).limit(100).all()
    if camera_id is not None:
        suggestions = [suggestion for suggestion in suggestions if suggestion.camera_id == camera_id]

    learning_mode_counts = Counter(
        _ensure_learning_mode(camera)
        for camera in cameras.values()
    )

    return {
        "events": rows,
        "metrics": metrics,
        "cameras": sorted(cameras.values(), key=lambda item: (item.name or "").lower()),
        "learning_mode_counts": dict(learning_mode_counts),
        "labels": FEEDBACK_LABELS,
        "probable_causes": PROBABLE_CAUSES,
        "profile_options": sorted({str(option) for option in profile_options if option}),
        "turn_options": ["morning", "afternoon", "night", "overnight"],
        "suggestions": [
            {
                "id": suggestion.id,
                "camera_id": suggestion.camera_id,
                "scope_type": suggestion.scope_type,
                "scope_id": suggestion.scope_id,
                "suggestion_type": suggestion.suggestion_type,
                "parameter_name": suggestion.parameter_name,
                "old_value": suggestion.old_value,
                "suggested_value": suggestion.suggested_value,
                "reason_summary": suggestion.reason_summary,
                "evidence_count": suggestion.evidence_count,
                "confidence_score": suggestion.confidence_score,
                "status": suggestion.status,
                "is_applicable": suggestion.parameter_name in AUTO_TUNABLE_PARAMETERS,
                "created_at": suggestion.created_at,
                "applied_at": suggestion.applied_at,
            }
            for suggestion in suggestions
        ],
        "active_learning_queue": build_active_learning_queue(db, camera_id=camera_id, days=days),
        "drift": evaluate_drift(db, camera_id) if camera_id is not None else None,
        "loaded_event_limit": event_limit,
        "ai_validated_count": ai_validated_count,
        "ai_validated_by_label": dict(ai_validated_by_label),
        "include_ai_validated": bool(include_ai_validated),
    }
