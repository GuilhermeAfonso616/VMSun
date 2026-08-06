r"""Coleta crops de treino IA2/IA3 direto do export local de eventos
rotulados (`audit_pending_event_<id>_event.json` + `_snapshot.jpg`), sem
depender do `analytics.db`.

`scripts/collect_revalidator_training_dataset.py` faz o mesmo trabalho mas le
via SQL de `event_feedback`/`events` - e os rotulos coletados com
`review_event_clips.py`/`review_event_clips_gui.py` sobre
`D:\IA_Rebuild\Analitico VMS Clips` ficam presos em `payload["feedback"]`
dentro de cada JSON ate o backfill (`scripts/backfill_labels_from_export.py`)
rodar contra o banco de producao. Este script fecha esse gap lendo os JSONs
diretamente, reaproveitando as mesmas funcoes de crop/margem/manifesto de
`collect_revalidator_training_dataset.py` para gerar um pacote no formato
identico (`<class>/{crops_ia2,crops_ia3_far,context,metadata}` +
`events.csv` + `manifest.json`), compativel com os scripts de build de
dataset em `D:\IA2\revalidator\scripts` (ex.: `build_ia2_v8c_dataset.py`).

Exemplo:

    python -B scripts/collect_revalidator_training_dataset_from_export.py
    python -B scripts/collect_revalidator_training_dataset_from_export.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collect_revalidator_training_dataset import (  # noqa: E402
    CLASS_NAMES,
    bbox_geometry,
    class_from_label,
    crop_with_margin,
    draw_context,
    ensure_package_dirs,
    is_useful_crop,
    normalize_label,
    sha1_file,
    write_image,
)

try:
    import cv2
except Exception as exc:  # pragma: no cover - operational fallback.
    cv2 = None
    CV2_IMPORT_ERROR = exc
else:
    CV2_IMPORT_ERROR = None

DEFAULT_SOURCE_DIR = Path(r"D:\IA_Rebuild\Analitico VMS Clips")
DEFAULT_OUTPUT_DIR = Path("reports/revalidator_training")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Coleta crops de treino IA2/IA3 direto dos JSONs rotulados exportados."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="Limite de eventos para smoke test.")
    parser.add_argument("--ia2-margin", type=float, default=0.20)
    parser.add_argument("--ia3-margin", type=float, default=0.55)
    parser.add_argument("--min-crop-size", type=int, default=16)
    return parser.parse_args()


def event_id_from_path(path: Path) -> int | None:
    match = re.search(r"audit_pending_event_(\d+)_event\.json$", path.name)
    return int(match.group(1)) if match else None


def load_labeled_event(event_path: Path) -> dict[str, Any] | None:
    event_id = event_id_from_path(event_path)
    if event_id is None:
        return None
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    feedback = payload.get("feedback") if isinstance(payload.get("feedback"), dict) else None
    if not feedback or not feedback.get("label"):
        return None

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    raw = payload.get("raw_metadata") if isinstance(payload.get("raw_metadata"), dict) else {}
    revalidation_evidence = raw.get("revalidation_evidence") if isinstance(raw.get("revalidation_evidence"), dict) else {}

    bbox = evidence.get("bbox") or revalidation_evidence.get("bbox")
    frame_width = revalidation_evidence.get("width")
    frame_height = revalidation_evidence.get("height")

    snapshot_path = event_path.parent / f"audit_pending_event_{event_id}_snapshot.jpg"

    return {
        "event_id": event_id,
        "event_path": event_path,
        "snapshot_path": snapshot_path,
        "camera_id": event.get("camera_id"),
        "track_id": event.get("track_id"),
        "event_type": event.get("event_type"),
        "rule_id": event.get("rule_id"),
        "zone_id": event.get("zone_id"),
        "roi_id": event.get("roi_id"),
        "detector_score": event.get("detector_score"),
        "confidence": event.get("confidence"),
        "event_score": event.get("event_score"),
        "event_status": event.get("status"),
        "alarm_eligible": event.get("alarm_eligible"),
        "lifecycle_action": event.get("lifecycle_action"),
        "is_alarm_active": event.get("is_alarm_active"),
        "created_at": event.get("created_at"),
        "started_at": event.get("started_at"),
        "ended_at": event.get("ended_at"),
        "details": event.get("details"),
        "label": feedback.get("label"),
        "probable_cause": feedback.get("probable_cause"),
        "operator_note": feedback.get("note") or feedback.get("operator_note"),
        "reviewed_by": feedback.get("reviewer") or feedback.get("reviewed_by"),
        "reviewed_at": feedback.get("reviewed_at"),
        "bbox": bbox,
        "frame_width": frame_width,
        "frame_height": frame_height,
    }


def event_stem(row: dict[str, Any], class_name: str) -> str:
    track_id = row["track_id"] if row["track_id"] is not None else "na"
    return f"event{row['event_id']}_feedback{row['event_id']}_{class_name}_cam{row['camera_id']}_track{track_id}"


def collect_one(
    row: dict[str, Any],
    *,
    package_dir: Path,
    ia2_margin: float,
    ia3_margin: float,
    min_crop_size: int,
) -> dict[str, Any]:
    label = normalize_label(row["label"])
    class_name = class_from_label(label)
    stem = event_stem(row, class_name)
    class_dir = package_dir / class_name

    crop_ia2_path = None
    crop_ia3_path = None
    context_path = None
    status = "metadata_only"
    error = None
    frame_width = row.get("frame_width")
    frame_height = row.get("frame_height")
    crop_ia2_shape = None
    crop_ia3_shape = None
    bbox = row.get("bbox")

    snapshot = row["snapshot_path"]
    if cv2 is None:
        status = "opencv_unavailable"
        error = str(CV2_IMPORT_ERROR)
    elif not snapshot.exists():
        status = "snapshot_missing"
    else:
        frame = cv2.imread(str(snapshot))
        if frame is None:
            status = "snapshot_unreadable"
        else:
            frame_height, frame_width = frame.shape[:2]
            context = draw_context(frame, bbox)
            context_target = class_dir / "context" / f"{stem}_context.jpg"
            if write_image(context_target, context):
                context_path = context_target
                status = "context_saved"

            if bbox:
                crop_ia2 = crop_with_margin(frame, bbox, ia2_margin)
                if is_useful_crop(crop_ia2, min_crop_size):
                    crop_target = class_dir / "crops_ia2" / f"{stem}_ia2_crop.jpg"
                    if write_image(crop_target, crop_ia2):
                        crop_ia2_path = crop_target
                        crop_ia2_shape = list(crop_ia2.shape[:2])
                        status = "crop_saved"

                crop_ia3 = crop_with_margin(frame, bbox, ia3_margin)
                if is_useful_crop(crop_ia3, min_crop_size):
                    crop_target = class_dir / "crops_ia3_far" / f"{stem}_ia3_far_crop.jpg"
                    if write_image(crop_target, crop_ia3):
                        crop_ia3_path = crop_target
                        crop_ia3_shape = list(crop_ia3.shape[:2])
                        status = "crop_saved"
            else:
                status = "context_saved_missing_bbox" if context_path else "missing_bbox"

    def rel(path: Path | None) -> str | None:
        return str(path.relative_to(package_dir)).replace("\\", "/") if path else None

    metadata = {
        "event_id": row["event_id"],
        "feedback_id": row["event_id"],
        "camera_id": row["camera_id"],
        "camera_name": row["camera_id"],
        "label": label,
        "class_name": class_name,
        "probable_cause": row.get("probable_cause"),
        "operator_note": row.get("operator_note"),
        "reviewed_by": row.get("reviewed_by"),
        "reviewed_at": row.get("reviewed_at"),
        "event_type": row.get("event_type"),
        "track_id": row.get("track_id"),
        "rule_id": row.get("rule_id"),
        "zone_id": row.get("zone_id"),
        "roi_id": row.get("roi_id"),
        "detector_score": row.get("detector_score"),
        "confidence": row.get("confidence"),
        "event_score": row.get("event_score"),
        "event_status": row.get("event_status"),
        "alarm_eligible": row.get("alarm_eligible"),
        "lifecycle_action": row.get("lifecycle_action"),
        "is_alarm_active": row.get("is_alarm_active"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "bbox_xyxy": bbox,
        **bbox_geometry(bbox, frame_width, frame_height),
        "frame_width": frame_width,
        "frame_height": frame_height,
        "source_snapshot_path": str(snapshot),
        "clip_path": str(row["event_path"].parent / f"audit_pending_event_{row['event_id']}_clip.mp4"),
        "crop_ia2_path": rel(crop_ia2_path),
        "crop_ia3_far_path": rel(crop_ia3_path),
        "context_path": rel(context_path),
        "snapshot_copy_path": None,
        "crop_ia2_shape_hw": crop_ia2_shape,
        "crop_ia3_far_shape_hw": crop_ia3_shape,
        "export_status": status,
        "export_error": error,
        "details": row.get("details"),
        "source_dataset": "july_2026_production_export",
    }

    metadata_path = class_dir / "metadata" / f"{stem}.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    metadata["metadata_path"] = rel(metadata_path)
    metadata["crop_ia2_sha1"] = sha1_file(crop_ia2_path) if crop_ia2_path else None
    metadata["crop_ia3_far_sha1"] = sha1_file(crop_ia3_path) if crop_ia3_path else None
    return metadata


def write_csvs(package_dir: Path, rows: list[dict[str, Any]]) -> None:
    import csv

    fields = [
        "event_id",
        "feedback_id",
        "camera_id",
        "camera_name",
        "label",
        "class_name",
        "probable_cause",
        "reviewed_at",
        "event_type",
        "track_id",
        "detector_score",
        "event_score",
        "bbox_area_ratio",
        "frame_width",
        "frame_height",
        "export_status",
        "crop_ia2_path",
        "crop_ia3_far_path",
        "context_path",
        "metadata_path",
        "source_snapshot_path",
        "source_dataset",
    ]
    with (package_dir / "events.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    missing_rows = [row for row in rows if row.get("export_status") not in {"crop_saved", "context_saved"}]
    with (package_dir / "missing_or_partial.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(missing_rows)


def write_manifest(package_dir: Path, rows: list[dict[str, Any]], *, args: argparse.Namespace, source_dir: Path) -> None:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "total_events": len(rows),
        "counts_by_class": dict(Counter(row["class_name"] for row in rows)),
        "counts_by_label": dict(Counter(row["label"] or "unlabeled" for row in rows)),
        "counts_by_camera": dict(Counter(str(row["camera_id"]) for row in rows)),
        "counts_by_status": dict(Counter(row["export_status"] for row in rows)),
        "trainable_ia2_crops": sum(1 for row in rows if row.get("crop_ia2_path") and row.get("class_name") in {"person", "not_person"}),
        "trainable_ia3_far_crops": sum(1 for row in rows if row.get("crop_ia3_far_path") and row.get("class_name") in {"person", "not_person"}),
        "class_names": list(CLASS_NAMES),
        "options": {
            "limit": args.limit,
            "ia2_margin": args.ia2_margin,
            "ia3_margin": args.ia3_margin,
            "min_crop_size": args.min_crop_size,
        },
    }
    (package_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        raise SystemExit(f"Pasta de export nao encontrada: {source_dir}")

    rows: list[dict[str, Any]] = []
    for event_path in sorted(source_dir.glob("audit_pending_event_*_event.json"), key=lambda p: event_id_from_path(p) or -1):
        row = load_labeled_event(event_path)
        if row is not None:
            rows.append(row)
    if args.limit:
        rows = rows[: args.limit]

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = output_dir / f"revalidator_training_dataset_from_export_{stamp}"
    package_dir.mkdir(parents=True, exist_ok=False)
    ensure_package_dirs(package_dir)

    exported = [
        collect_one(
            row,
            package_dir=package_dir,
            ia2_margin=float(args.ia2_margin),
            ia3_margin=float(args.ia3_margin),
            min_crop_size=max(1, int(args.min_crop_size)),
        )
        for row in rows
    ]
    write_csvs(package_dir, exported)
    write_manifest(package_dir, exported, args=args, source_dir=source_dir)

    print(f"Eventos rotulados encontrados: {len(rows)}")
    print("Classes:", dict(Counter(row["class_name"] for row in exported)))
    print("Status:", dict(Counter(row["export_status"] for row in exported)))
    print(f"Pasta: {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
