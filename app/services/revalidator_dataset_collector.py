from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import cv2

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timezone import now_brazil_naive


logger = get_logger("app.revalidator_dataset")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MIN_CROP_SIZE_PX = 48
MAX_PERSON_SAMPLES_PER_TRACK = 3


def _project_path(path_value: str) -> Path:
    path = Path(str(path_value or ""))
    if path.is_absolute():
        return path
    return Path(settings.app_base_dir) / path


def _safe_slug(value: Any, default: str = "unknown") -> str:
    text = str(value or default).strip().lower()
    allowed = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char in {" ", "/", "\\", "."}:
            allowed.append("_")
    cleaned = "".join(allowed).strip("_")
    return cleaned or default


def _load_bbox(event: Any) -> list[float] | None:
    raw = getattr(event, "bbox_json", None)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) == 4:
            return [float(value) for value in parsed]
    except Exception:
        return None
    return None


def _resolve_snapshot_path(snapshot_path: str | None) -> Path | None:
    if not snapshot_path:
        return None
    path = Path(str(snapshot_path))
    if not path.is_absolute():
        path = Path(settings.app_base_dir) / path
    return path if path.exists() else None


def _crop_with_margin(frame, bbox: list[float], margin_pct: float = 0.20):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [float(value) for value in bbox]
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    margin_x = box_w * margin_pct
    margin_y = box_h * margin_pct
    left = max(0, int(round(x1 - margin_x)))
    top = max(0, int(round(y1 - margin_y)))
    right = min(width, int(round(x2 + margin_x)))
    bottom = min(height, int(round(y2 + margin_y)))
    if right <= left or bottom <= top:
        return None
    crop = frame[top:bottom, left:right]
    return crop if crop is not None and crop.size > 0 else None


def _draw_context_with_bbox(frame, bbox: list[float] | None):
    if frame is None:
        return None
    output = frame.copy()
    if bbox and len(bbox) == 4:
        try:
            height, width = output.shape[:2]
            x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
            x1 = max(0, min(width - 1, x1))
            y1 = max(0, min(height - 1, y1))
            x2 = max(0, min(width - 1, x2))
            y2 = max(0, min(height - 1, y2))
            if x2 > x1 and y2 > y1:
                cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 0), 2)
        except Exception:
            return output
    return output


def _crop_is_useful(crop) -> bool:
    if crop is None or crop.size <= 0:
        return False
    height, width = crop.shape[:2]
    return height >= MIN_CROP_SIZE_PX and width >= MIN_CROP_SIZE_PX


def _metadata_value_matches(value: Any, expected: Any) -> bool:
    if expected is None:
        return value is None
    return str(value) == str(expected)


def _person_track_sample_count(metadata_dir: Path, camera_id: Any, track_id: Any) -> int:
    if track_id is None or not metadata_dir.exists():
        return 0
    count = 0
    for path in metadata_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _metadata_value_matches(payload.get("camera_id"), camera_id) and _metadata_value_matches(payload.get("track_id"), track_id):
            count += 1
    return count


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


def _build_sample_stem(*, camera_id: Any, event_id: Any, track_id: Any, label: str, reason: str) -> str:
    track_label = track_id if track_id is not None else "na"
    return f"event{event_id}_latest_{_safe_slug(label)}_cam{camera_id}_track{track_label}"


def _remove_stable_event_samples(event_id: Any, *, keep_paths: set[Path] | None = None) -> None:
    base_dir = _project_path(settings.revalidator_feedback_dataset_dir)
    keep_resolved = {path.resolve() for path in (keep_paths or set())}
    for class_name in ("person", "not_person", "uncertain"):
        class_dir = base_dir / class_name
        for subdir in ("crops", "context", "metadata"):
            directory = class_dir / subdir
            if not directory.exists():
                continue
            for path in directory.glob(f"event{event_id}_latest_*"):
                if path.resolve() in keep_resolved:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def _committed_sample_paths(*paths: Path | None) -> set[Path]:
    return {path for path in paths if path is not None and path.exists()}


def _sample_has_collected_artifact(*, status: str, crop_target: Path | None, context_target: Path | None) -> bool:
    if status == "metadata_only":
        return False
    return crop_target is not None and crop_target.exists()


def _write_sample_metadata_safely(
    metadata_target: Path,
    metadata: dict[str, Any],
    *,
    has_collected_artifact: bool,
) -> bool:
    if not has_collected_artifact and metadata_target.exists():
        return False
    metadata_target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return True


def _class_dirs(class_name: str) -> tuple[Path, Path, Path]:
    base_dir = _project_path(settings.revalidator_feedback_dataset_dir)
    class_dir = base_dir / class_name
    crops_dir = class_dir / "crops"
    context_dir = class_dir / "context"
    metadata_dir = class_dir / "metadata"
    for directory in (crops_dir, context_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return crops_dir, context_dir, metadata_dir


def collect_false_positive_revalidator_sample(
    *,
    event: Any,
    feedback: Any,
    probable_cause: str | None,
    operator_note: str | None,
) -> dict[str, Any]:
    """Exporta amostra real de falso positivo para treinar o revalidador.

    A estrutura gerada segue a classe `not_person`, pois a avaliacao humana
    marcou que o evento nao era uma pessoa valida para alarme.
    """

    event_id = getattr(event, "id", "unknown")
    camera_id = getattr(event, "camera_id", "unknown")
    track_id = getattr(event, "track_id", None)
    cause = _safe_slug(probable_cause or "false_positive")
    stem = _build_sample_stem(camera_id=camera_id, event_id=event_id, track_id=track_id, label="not_person", reason=cause)
    crops_dir, context_dir, metadata_dir = _class_dirs("not_person")

    snapshot_path = _resolve_snapshot_path(getattr(event, "snapshot_path", None))
    bbox = _load_bbox(event)
    context_target = None
    crop_target = None
    status = "metadata_only"

    if snapshot_path is not None:
        context_target = context_dir / f"{stem}_context.jpg"
        try:
            shutil.copy2(snapshot_path, context_target)
            status = "context_saved"
        except Exception:
            logger.exception(
                "Failed to copy false-positive context image",
                extra={
                    "action": "revalidator_dataset_collect",
                    "status": "degraded",
                    "reason": "context_copy_failed",
                },
            )
            context_target = None

        if bbox:
            frame = cv2.imread(str(snapshot_path))
            if frame is not None:
                crop = _crop_with_margin(frame, bbox)
                if _crop_is_useful(crop):
                    crop_target = crops_dir / f"{stem}_crop.jpg"
                    if cv2.imwrite(str(crop_target), crop):
                        status = "crop_and_context_saved" if context_target else "crop_saved"
                    else:
                        crop_target = None

    metadata = {
        "class": "not_person",
        "source": "operator_false_positive_feedback",
        "status": status,
        "event_id": event_id,
        "camera_id": camera_id,
        "track_id": track_id,
        "event_type": getattr(event, "event_type", None),
        "rule_id": getattr(event, "rule_id", None),
        "probable_cause": probable_cause,
        "operator_note": operator_note,
        "reviewed_by": getattr(feedback, "reviewed_by", None),
        "reviewed_at": getattr(feedback, "reviewed_at", None).isoformat() if getattr(feedback, "reviewed_at", None) else None,
        "detector_score": getattr(event, "detector_score", None),
        "event_score": getattr(event, "event_score", None),
        "bbox": bbox,
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "crop_path": str(crop_target) if crop_target else None,
        "context_path": str(context_target) if context_target else None,
    }

    metadata_target = metadata_dir / f"{stem}.json"
    has_collected_artifact = _sample_has_collected_artifact(
        status=status,
        crop_target=crop_target,
        context_target=context_target,
    )
    metadata_written = _write_sample_metadata_safely(
        metadata_target,
        metadata,
        has_collected_artifact=has_collected_artifact,
    )
    if has_collected_artifact:
        _remove_stable_event_samples(
            event_id,
            keep_paths=_committed_sample_paths(crop_target, context_target, metadata_target if metadata_written else None),
        )

    logger.info(
        "False-positive revalidator sample collected event_id=%s status=%s",
        event_id,
        status,
        extra={
            "camera_id": camera_id,
            "event_id": event_id,
            "action": "revalidator_dataset_collect",
            "status": "running",
            "reason": status,
        },
    )
    return metadata


def collect_person_revalidator_sample(
    *,
    event: Any,
    feedback: Any,
    decision_source: str,
    operator_note: str | None,
    max_samples_per_track: int = MAX_PERSON_SAMPLES_PER_TRACK,
) -> dict[str, Any]:
    """Exporta amostra real de pessoa confirmada para treinar o revalidador."""

    event_id = getattr(event, "id", "unknown")
    camera_id = getattr(event, "camera_id", "unknown")
    track_id = getattr(event, "track_id", None)
    crops_dir, context_dir, metadata_dir = _class_dirs("person")

    stem = _build_sample_stem(
        camera_id=camera_id,
        event_id=event_id,
        track_id=track_id,
        label="person",
        reason=decision_source,
    )
    snapshot_path = _resolve_snapshot_path(getattr(event, "snapshot_path", None))
    bbox = _load_bbox(event)
    context_target = None
    crop_target = None
    status = "metadata_only"

    if snapshot_path is not None:
        frame = cv2.imread(str(snapshot_path))
        if frame is not None:
            context = _draw_context_with_bbox(frame, bbox)
            context_target = context_dir / f"{stem}_context.jpg"
            if cv2.imwrite(str(context_target), context):
                status = "context_saved"
            else:
                context_target = None

            if bbox:
                crop = _crop_with_margin(frame, bbox)
                if _crop_is_useful(crop):
                    crop_target = crops_dir / f"{stem}_crop.jpg"
                    if cv2.imwrite(str(crop_target), crop):
                        status = "crop_and_context_saved" if context_target else "crop_saved"
                    else:
                        crop_target = None
                else:
                    status = "skipped_low_quality_crop" if context_target is None else "context_saved_low_quality_crop"

    metadata = {
        "label": "person",
        "class": "person",
        "source": "server_feedback",
        "status": status,
        "camera_id": camera_id,
        "event_id": event_id,
        "track_id": track_id,
        "timestamp": now_brazil_naive().isoformat(),
        "event_type": getattr(event, "event_type", None),
        "rule_id": getattr(event, "rule_id", None),
        "bbox_xyxy": bbox,
        "confidence_detector": getattr(event, "detector_score", None),
        "confidence_revalidator": _extract_revalidator_score(getattr(event, "details", None)),
        "confidence_event": getattr(event, "event_score", None),
        "decision_source": decision_source,
        "reviewed_by": getattr(feedback, "reviewed_by", None),
        "reviewed_at": getattr(feedback, "reviewed_at", None).isoformat() if getattr(feedback, "reviewed_at", None) else None,
        "frame_path": str(snapshot_path) if snapshot_path else None,
        "crop_path": str(crop_target) if crop_target else None,
        "context_path": str(context_target) if context_target else None,
        "notes": operator_note or "",
    }

    metadata_target = metadata_dir / f"{stem}.json"
    has_collected_artifact = _sample_has_collected_artifact(
        status=status,
        crop_target=crop_target,
        context_target=context_target,
    )
    metadata_written = _write_sample_metadata_safely(
        metadata_target,
        metadata,
        has_collected_artifact=has_collected_artifact,
    )
    if has_collected_artifact:
        _remove_stable_event_samples(
            event_id,
            keep_paths=_committed_sample_paths(crop_target, context_target, metadata_target if metadata_written else None),
        )

    logger.info(
        "Person revalidator sample collected event_id=%s status=%s",
        event_id,
        status,
        extra={
            "camera_id": camera_id,
            "event_id": event_id,
            "action": "revalidator_dataset_collect",
            "status": "running",
            "reason": status,
        },
    )
    return metadata


def collect_uncertain_revalidator_sample(
    *,
    event: Any,
    feedback: Any,
    probable_cause: str | None,
    operator_note: str | None,
) -> dict[str, Any]:
    """Exporta amostra inconclusiva para triagem manual antes de treino."""

    event_id = getattr(event, "id", "unknown")
    camera_id = getattr(event, "camera_id", "unknown")
    track_id = getattr(event, "track_id", None)
    reason = _safe_slug(probable_cause or "inconclusive")
    stem = _build_sample_stem(camera_id=camera_id, event_id=event_id, track_id=track_id, label="uncertain", reason=reason)
    crops_dir, context_dir, metadata_dir = _class_dirs("uncertain")

    snapshot_path = _resolve_snapshot_path(getattr(event, "snapshot_path", None))
    bbox = _load_bbox(event)
    context_target = None
    crop_target = None
    status = "metadata_only"

    if snapshot_path is not None:
        frame = cv2.imread(str(snapshot_path))
        if frame is not None:
            context = _draw_context_with_bbox(frame, bbox)
            context_target = context_dir / f"{stem}_context.jpg"
            if cv2.imwrite(str(context_target), context):
                status = "context_saved"
            else:
                context_target = None

            if bbox:
                crop = _crop_with_margin(frame, bbox)
                if _crop_is_useful(crop):
                    crop_target = crops_dir / f"{stem}_crop.jpg"
                    if cv2.imwrite(str(crop_target), crop):
                        status = "crop_and_context_saved" if context_target else "crop_saved"
                    else:
                        crop_target = None
                else:
                    status = "skipped_low_quality_crop" if context_target is None else "context_saved_low_quality_crop"

    metadata = {
        "label": "uncertain",
        "class": "uncertain",
        "source": "operator_inconclusive_feedback",
        "status": status,
        "camera_id": camera_id,
        "event_id": event_id,
        "track_id": track_id,
        "timestamp": now_brazil_naive().isoformat(),
        "event_type": getattr(event, "event_type", None),
        "rule_id": getattr(event, "rule_id", None),
        "probable_cause": probable_cause,
        "bbox_xyxy": bbox,
        "confidence_detector": getattr(event, "detector_score", None),
        "confidence_revalidator": _extract_revalidator_score(getattr(event, "details", None)),
        "confidence_event": getattr(event, "event_score", None),
        "reviewed_by": getattr(feedback, "reviewed_by", None),
        "reviewed_at": getattr(feedback, "reviewed_at", None).isoformat() if getattr(feedback, "reviewed_at", None) else None,
        "frame_path": str(snapshot_path) if snapshot_path else None,
        "crop_path": str(crop_target) if crop_target else None,
        "context_path": str(context_target) if context_target else None,
        "notes": operator_note or "",
    }

    metadata_target = metadata_dir / f"{stem}.json"
    has_collected_artifact = _sample_has_collected_artifact(
        status=status,
        crop_target=crop_target,
        context_target=context_target,
    )
    metadata_written = _write_sample_metadata_safely(
        metadata_target,
        metadata,
        has_collected_artifact=has_collected_artifact,
    )
    if has_collected_artifact:
        _remove_stable_event_samples(
            event_id,
            keep_paths=_committed_sample_paths(crop_target, context_target, metadata_target if metadata_written else None),
        )

    logger.info(
        "Uncertain revalidator sample collected event_id=%s status=%s",
        event_id,
        status,
        extra={
            "camera_id": camera_id,
            "event_id": event_id,
            "action": "revalidator_dataset_collect",
            "status": "running",
            "reason": status,
        },
    )
    return metadata


def build_revalidator_dataset_summary() -> dict[str, Any]:
    base_dir = _project_path(settings.revalidator_feedback_dataset_dir)
    classes: dict[str, dict[str, Any]] = {}

    for class_name in ("person", "not_person", "uncertain"):
        class_dir = base_dir / class_name
        crops_dir = class_dir / "crops"
        metadata_dir = class_dir / "metadata"
        context_dir = class_dir / "context"

        crop_count = 0
        if crops_dir.exists():
            crop_count = sum(
                1
                for path in crops_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            )

        metadata_count = 0
        if metadata_dir.exists():
            metadata_count = sum(1 for path in metadata_dir.rglob("*.json") if path.is_file())

        context_count = 0
        if context_dir.exists():
            context_count = sum(
                1
                for path in context_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            )

        classes[class_name] = {
            "crops": crop_count,
            "metadata": metadata_count,
            "context": context_count,
        }

    person_crops = int(classes["person"]["crops"])
    not_person_crops = int(classes["not_person"]["crops"])
    trainable_crops = person_crops + not_person_crops
    balanced_pairs = min(person_crops, not_person_crops)

    if trainable_crops < 50:
        readiness = "coletando"
        recommendation = "Ainda e pouco para retreino; continue avaliando falsos positivos e verdadeiros positivos."
    elif balanced_pairs < 50:
        readiness = "desbalanceado"
        recommendation = "Ja ha imagens, mas falta equilibrar person e not_person antes de retreinar."
    elif balanced_pairs < 250:
        readiness = "piloto"
        recommendation = "Volume suficiente para um fine-tune piloto e avaliacao manual dos erros."
    elif balanced_pairs < 1000:
        readiness = "bom"
        recommendation = "Bom volume para retreino inicial do revalidador."
    else:
        readiness = "forte"
        recommendation = "Volume forte para retreino e validacao por camera/cenario."

    return {
        "base_dir": str(base_dir),
        "classes": classes,
        "trainable_crops": trainable_crops,
        "balanced_pairs": balanced_pairs,
        "readiness": readiness,
        "recommendation": recommendation,
        "targets": {
            "pilot_balanced_pairs": 50,
            "initial_retrain_balanced_pairs": 250,
            "strong_retrain_balanced_pairs": 1000,
        },
    }
