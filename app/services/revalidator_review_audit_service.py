from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from app.analytics_v2.revalidation.far_person_revalidator import get_far_person_revalidator
from app.analytics_v2.revalidation.consensus_policy import (
    evaluate_consensus_block_candidate,
    evaluate_layered_operational_decision,
)
from app.analytics_v2.revalidation.person_crop_revalidator import get_person_crop_revalidator
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Event, EventFeedback
from app.services.revalidator_region_memory_service import build_region_memory


logger = get_logger("app.services.revalidator_review_audit")

_DETAIL_FIELD_RE = re.compile(r"(?P<key>[a-zA-Z0-9_]+)=(?P<value>[^|]+)")


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _resolve_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        path = Path(settings.app_base_dir) / path
    return path


def _load_bbox(event: Event) -> list[float] | None:
    if not getattr(event, "bbox_json", None):
        return None
    try:
        parsed = json.loads(str(event.bbox_json))
        if isinstance(parsed, list) and len(parsed) == 4:
            return [float(value) for value in parsed]
    except Exception:
        return None
    return None


def _ia1_payload(event: Event) -> dict[str, Any]:
    score = getattr(event, "detector_score", None)
    return {
        "name": "IA1",
        "role": "detector_original",
        "applied": score is not None,
        "person_score": score,
        "interpretation": "CONFIRMOU_PESSOA" if score is not None else "SEM_SCORE",
        "summary": (
            f"IA1 confirmou: pessoa ({float(score):.3f})"
            if score is not None
            else "IA1 sem score salvo no evento"
        ),
    }


def _ia2_payload(result) -> dict[str, Any]:
    person_score = float(result.person_score or 0.0)
    not_person_score = float(result.not_person_score or 0.0)
    threshold = float(result.threshold or 0.0)
    raw_person = bool(result.applied and person_score >= not_person_score and person_score >= 0.5)
    passed = bool(result.applied and result.person_score is not None and person_score >= threshold)
    return {
        "name": "IA2",
        "role": "person_crop_revalidator_v5",
        "raw_model_interpretation": "PARECE_PESSOA" if raw_person else "NAO_PARECE_PESSOA",
        "operational_result": "PASSOU_REVALIDADOR" if passed else "REPROVOU_REVALIDADOR",
        "raw_model_margin": person_score - not_person_score,
        **result.to_metadata(),
    }


def _ia3_payload(result) -> dict[str, Any]:
    if result.applied:
        person_score = float(result.person_far_score or 0.0)
        not_person_score = float(result.not_person_far_score or 0.0)
        raw = "PARECE_PESSOA" if person_score >= not_person_score else "NAO_PARECE_PESSOA"
    elif result.triggered:
        raw = "SOLICITADA_SEM_RESULTADO"
    else:
        raw = "NAO_SOLICITADA"
    return {
        "name": "IA3",
        "role": "far_person_revalidator_v1",
        "raw_model_interpretation": raw,
        **result.to_metadata(),
    }


def _details_fields(details: str | None) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _DETAIL_FIELD_RE.finditer(str(details or "")):
        fields[match.group("key")] = match.group("value").strip()
    return fields


def _float_field(fields: dict[str, str], name: str) -> float | None:
    try:
        return float(fields[name])
    except Exception:
        return None


def _event_maturity_payload(event: Event) -> dict[str, Any]:
    fields = _details_fields(getattr(event, "details", None))
    if "maturity_score" not in fields and "maturity_level" not in fields:
        return {
            "enabled": True,
            "mode": "audit",
            "score": None,
            "level": "UNKNOWN",
            "decision": "uncertain",
            "reason": "not_recorded",
            "source": "event_details",
        }
    return {
        "enabled": True,
        "mode": "audit",
        "score": _float_field(fields, "maturity_score"),
        "level": fields.get("maturity_level") or "UNKNOWN",
        "decision": fields.get("maturity_decision") or "uncertain",
        "reason": fields.get("maturity_reason") or "unknown",
        "source": "event_details",
        "safety": {
            "fast_motion_protected": str(fields.get("maturity_fast_motion") or "").lower() == "true",
            "camera_motion_possible": str(fields.get("maturity_camera_motion") or "").lower() == "true",
            "block_allowed": False,
            "suppress_allowed": False,
        },
    }


def _alarm_decision_payload(event: Event) -> dict[str, Any]:
    fields = _details_fields(getattr(event, "details", None))
    if "alarm_decision_action" not in fields:
        return {
            "enabled": True,
            "mode": "audit",
            "action": "UNKNOWN",
            "suggested_status": None,
            "reason": "not_recorded",
            "applied": False,
            "current_behavior_preserved": True,
            "source": "event_details",
        }
    return {
        "enabled": True,
        "mode": fields.get("alarm_decision_mode") or "audit",
        "action": fields.get("alarm_decision_action") or "UNKNOWN",
        "suggested_status": fields.get("alarm_decision_status") or "audit",
        "reason": fields.get("alarm_decision_reason") or "unknown",
        "applied": False,
        "current_behavior_preserved": True,
        "source": "event_details",
    }


def build_review_audit_payload(
    event: Event,
    feedback: EventFeedback,
    *,
    history_rows: list[tuple[EventFeedback, Event]] | None = None,
) -> dict[str, Any]:
    snapshot_path = _resolve_path(getattr(event, "snapshot_path", None))
    bbox = _load_bbox(event)
    frame = cv2.imread(str(snapshot_path)) if snapshot_path and snapshot_path.exists() else None

    ia2_result = None
    ia3_result = None
    status = "ok"
    reason = "review_audit_completed"

    if frame is None:
        status = "skipped"
        reason = "missing_snapshot"
    elif not bbox:
        status = "skipped"
        reason = "missing_bbox"
    else:
        ia2 = get_person_crop_revalidator()
        ia2_result = ia2.validate(frame, bbox)
        ia3 = get_far_person_revalidator()
        ia3_result = ia3.validate(frame, bbox, base_quality=ia2_result.quality, ia2_result=ia2_result)

    quality = dict(getattr(ia2_result, "quality", None) or {})
    frame_width = quality.get("frame_width") or (frame.shape[1] if frame is not None and hasattr(frame, "shape") else None)
    frame_height = quality.get("frame_height") or (frame.shape[0] if frame is not None and hasattr(frame, "shape") else None)
    region_memory = build_region_memory(
        event=event,
        feedback=feedback,
        history_rows=history_rows or [],
        frame_width=frame_width,
        frame_height=frame_height,
    )
    consensus_revalidator = (
        evaluate_consensus_block_candidate(ia2_result, ia3_result)
        if ia2_result is not None and ia3_result is not None
        else {"enabled": True, "mode": "audit", "block_candidate": False, "reason": reason}
    )
    layered_decision = (
        evaluate_layered_operational_decision(
            ia2_result=ia2_result,
            ia3_result=ia3_result,
            consensus_result=consensus_revalidator,
            region_memory=region_memory,
        )
        if ia2_result is not None and ia3_result is not None
        else {"mode": "audit", "decision": "uncertain", "reason": reason}
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reason": reason,
        "event": {
            "id": event.id,
            "camera_id": event.camera_id,
            "event_type": event.event_type,
            "track_id": event.track_id,
            "detector_score": event.detector_score,
            "event_score": event.event_score or event.confidence,
            "snapshot_path": str(snapshot_path) if snapshot_path else getattr(event, "snapshot_path", None),
            "bbox": bbox,
            "details": event.details,
            "created_at": getattr(event, "created_at", None),
        },
        "feedback": {
            "id": feedback.id,
            "label": feedback.label,
            "probable_cause": feedback.probable_cause,
            "operator_note": feedback.operator_note,
            "reviewed_by": feedback.reviewed_by,
            "reviewed_at": feedback.reviewed_at,
        },
        "ia1": _ia1_payload(event),
        "ia2": _ia2_payload(ia2_result) if ia2_result is not None else {"name": "IA2", "applied": False, "reason": reason},
        "ia3": _ia3_payload(ia3_result) if ia3_result is not None else {"name": "IA3", "triggered": False, "applied": False, "reason": reason},
        "event_maturity": _event_maturity_payload(event),
        "alarm_decision": _alarm_decision_payload(event),
        "region_memory": region_memory,
        "consensus_revalidator": consensus_revalidator,
        "layered_decision": layered_decision,
    }
    return payload


def write_review_audit_json(
    event: Event,
    feedback: EventFeedback,
    *,
    history_rows: list[tuple[EventFeedback, Event]] | None = None,
) -> Path:
    payload = build_review_audit_payload(event, feedback, history_rows=history_rows)
    return write_review_audit_payload_json(payload)


def write_review_audit_payload_json(payload: dict[str, Any], *, output_root: Path | None = None) -> Path:
    event_payload = payload.get("event") or {}
    feedback_payload = payload.get("feedback") or {}
    camera_id = event_payload.get("camera_id")
    event_id = event_payload.get("id")
    feedback_id = feedback_payload.get("id") or "pending"
    output_root = output_root or Path(settings.revalidator_review_audit_dir)
    if not output_root.is_absolute():
        output_root = Path(settings.app_base_dir) / output_root
    output_dir = output_root / f"camera_{camera_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"event_{event_id}_feedback_{feedback_id}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    logger.info(
        "Review revalidator audit JSON saved path=%s",
        str(output_path),
        extra={
            "camera_id": camera_id,
            "event_id": event_id,
            "action": "review_revalidator_audit_saved",
            "status": "running" if payload.get("status") == "ok" else "degraded",
            "reason": payload.get("reason") or "review_audit",
        },
    )
    return output_path
