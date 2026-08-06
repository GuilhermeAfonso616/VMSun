from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Iterable

from app.core.config import settings
from app.db.models import Event, EventFeedback


PERSON_LABELS = {"true_positive", "expected_event"}
NOT_PERSON_LABELS = {"false_positive"}


def _load_bbox(event: Event) -> list[float] | None:
    raw = getattr(event, "bbox_json", None)
    if not raw:
        return None
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list) and len(parsed) == 4:
            return [float(value) for value in parsed]
    except Exception:
        return None
    return None


def _truth_from_label(label: str | None) -> str:
    normalized = str(label or "").strip().lower()
    if normalized in PERSON_LABELS:
        return "person"
    if normalized in NOT_PERSON_LABELS:
        return "not_person"
    return "uncertain"


def _region_from_bbox(
    bbox: list[float] | None,
    *,
    camera_id: int | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
) -> dict[str, Any]:
    cols = max(1, int(settings.region_memory_grid_cols or 8))
    rows = max(1, int(settings.region_memory_grid_rows or 6))
    if not bbox or len(bbox) != 4 or not frame_width or not frame_height:
        return {
            "region_cell": None,
            "grid_cols": cols,
            "grid_rows": rows,
            "bbox_center_x_norm": None,
            "bbox_center_y_norm": None,
            "bbox_width_norm": None,
            "bbox_height_norm": None,
            "reason": "missing_bbox_or_frame_size",
        }

    x1, y1, x2, y2 = [float(value) for value in bbox]
    width = max(1.0, float(frame_width))
    height = max(1.0, float(frame_height))
    center_x = max(0.0, min(1.0, ((x1 + x2) / 2.0) / width))
    center_y = max(0.0, min(1.0, ((y1 + y2) / 2.0) / height))
    cell_x = min(cols - 1, max(0, int(center_x * cols)))
    cell_y = min(rows - 1, max(0, int(center_y * rows)))
    return {
        "region_cell": f"{camera_id}:{cell_x:02d}:{cell_y:02d}",
        "grid_cols": cols,
        "grid_rows": rows,
        "cell_x": cell_x,
        "cell_y": cell_y,
        "bbox_center_x_norm": round(center_x, 6),
        "bbox_center_y_norm": round(center_y, 6),
        "bbox_width_norm": round(max(0.0, x2 - x1) / width, 6),
        "bbox_height_norm": round(max(0.0, y2 - y1) / height, 6),
        "reason": "ok",
    }


def _as_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def build_region_memory(
    *,
    event: Event,
    feedback: EventFeedback | None,
    history_rows: Iterable[tuple[EventFeedback, Event]] | None,
    frame_width: int | float | None,
    frame_height: int | float | None,
) -> dict[str, Any]:
    region = _region_from_bbox(
        _load_bbox(event),
        camera_id=getattr(event, "camera_id", None),
        frame_width=frame_width,
        frame_height=frame_height,
    )
    if not settings.region_memory_enabled:
        return {"enabled": False, **region}

    target_cell = region.get("region_cell")
    false_positive_count = 0
    true_positive_count = 0
    uncertain_count = 0
    latest_seen: datetime | None = None
    sample_event_ids: list[int] = []
    seen_history_event_ids: set[int] = set()

    for history_feedback, history_event in history_rows or []:
        history_event_id = int(getattr(history_event, "id", 0) or 0)
        if (
            feedback is not None
            and getattr(history_event, "id", None) == getattr(event, "id", None)
            and getattr(history_feedback, "id", None) == getattr(feedback, "id", None)
        ):
            continue
        if history_event_id and history_event_id in seen_history_event_ids:
            continue
        if history_event_id:
            seen_history_event_ids.add(history_event_id)
        if getattr(history_event, "camera_id", None) != getattr(event, "camera_id", None):
            continue
        history_region = _region_from_bbox(
            _load_bbox(history_event),
            camera_id=getattr(history_event, "camera_id", None),
            frame_width=frame_width,
            frame_height=frame_height,
        )
        if history_region.get("region_cell") != target_cell:
            continue

        label_truth = _truth_from_label(getattr(history_feedback, "label", None))
        if label_truth == "person":
            true_positive_count += 1
        elif label_truth == "not_person":
            false_positive_count += 1
        else:
            uncertain_count += 1

        reviewed_at = _as_datetime(getattr(history_feedback, "reviewed_at", None))
        if reviewed_at and (latest_seen is None or reviewed_at > latest_seen):
            latest_seen = reviewed_at
        if len(sample_event_ids) < 12:
            sample_event_ids.append(history_event_id)

    total_reviewed_count = false_positive_count + true_positive_count + uncertain_count
    false_positive_rate = false_positive_count / total_reviewed_count if total_reviewed_count else 0.0
    true_positive_rate = true_positive_count / total_reviewed_count if total_reviewed_count else 0.0
    green_min = max(1, int(settings.region_memory_green_min_false_positive_count or 3))
    high_fp_rate = max(0.0, min(1.0, float(settings.region_memory_high_fp_rate_threshold or 0.70)))
    person_support_rate = max(0.0, min(1.0, float(settings.region_memory_person_support_rate_threshold or 0.50)))
    if false_positive_count >= green_min and false_positive_rate >= high_fp_rate:
        risk_level = "GREEN"
        decision_hint = "recurrent_false_positive_region"
    elif true_positive_count > 0 and true_positive_rate >= person_support_rate:
        risk_level = "RED"
        decision_hint = "person_seen_in_region"
    elif true_positive_count > false_positive_count:
        risk_level = "RED"
        decision_hint = "person_seen_in_region"
    elif false_positive_count > 0:
        risk_level = "YELLOW"
        decision_hint = "some_false_positive_history"
    else:
        risk_level = "UNKNOWN"
        decision_hint = "no_region_history"

    return {
        "enabled": True,
        **region,
        "false_positive_count": false_positive_count,
        "true_positive_count": true_positive_count,
        "uncertain_count": uncertain_count,
        "total_reviewed_count": total_reviewed_count,
        "false_positive_rate": round(false_positive_rate, 6),
        "true_positive_rate": round(true_positive_rate, 6),
        "risk_level": risk_level,
        "decision_hint": decision_hint,
        "green_min_false_positive_count": green_min,
        "high_fp_rate_threshold": high_fp_rate,
        "person_support_rate_threshold": person_support_rate,
        "last_seen": latest_seen.isoformat() if latest_seen else None,
        "sample_event_ids": sample_event_ids,
        "current_feedback_label": getattr(feedback, "label", None) if feedback is not None else None,
    }
