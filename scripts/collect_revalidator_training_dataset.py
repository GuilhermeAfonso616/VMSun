from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import cv2
except Exception as exc:  # pragma: no cover - operational fallback.
    cv2 = None
    CV2_IMPORT_ERROR = exc
else:
    CV2_IMPORT_ERROR = None


LABEL_TO_CLASS = {
    "true_positive": "person",
    "expected_event": "person",
    "person": "person",
    "pessoa": "person",
    "false_positive": "not_person",
    "not_person": "not_person",
    "nao_pessoa": "not_person",
    "fp": "not_person",
    "inconclusive": "uncertain",
    "uncertain": "uncertain",
}
CLASS_NAMES = ("person", "not_person", "uncertain", "unlabeled")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Coleta eventos revisados do analytics.db para retreino IA2/IA3, "
            "salvando crops, contexto com bbox e metadata."
        )
    )
    parser.add_argument("--database-url", default="sqlite:///data/analytics.db", help="URL sqlite ou caminho do analytics.db.")
    parser.add_argument("--output-dir", default="reports/revalidator_training", help="Pasta de saida.")
    parser.add_argument("--base-dir", default=".", help="Base para resolver snapshots relativos.")
    parser.add_argument(
        "--snapshot-root",
        action="append",
        default=[],
        help="Raiz extra para resolver snapshot_path. Pode ser usado mais de uma vez.",
    )
    parser.add_argument("--since", default="", help="Filtra eventos por data do evento. Ex: '2026-05-09 00:00:00'.")
    parser.add_argument("--until", default="", help="Filtra eventos por data do evento.")
    parser.add_argument("--camera-id", type=int, default=None, help="Filtra uma camera especifica.")
    parser.add_argument("--limit", type=int, default=None, help="Limite de eventos para smoke test.")
    parser.add_argument("--include-unreviewed", action="store_true", help="Inclui eventos sem feedback em classe unlabeled.")
    parser.add_argument("--include-snapshot-copy", action="store_true", help="Copia tambem o snapshot original.")
    parser.add_argument("--include-feedback-history", action="store_true", help="Inclui todas as revisoes; padrao usa a mais recente por evento.")
    parser.add_argument("--ia2-margin", type=float, default=0.20, help="Margem do crop IA2 em relacao a bbox.")
    parser.add_argument("--ia3-margin", type=float, default=0.55, help="Margem do crop IA3/far em relacao a bbox.")
    parser.add_argument("--min-crop-size", type=int, default=16, help="Tamanho minimo do crop salvo.")
    parser.add_argument("--no-zip", action="store_true", help="Nao cria ZIP.")
    parser.add_argument("--keep-folder", action="store_true", help="Mantem pasta apos criar ZIP.")
    return parser.parse_args()


def sqlite_path_from_url(database_url: str) -> Path:
    value = str(database_url or "").strip()
    if value.startswith("sqlite:///"):
        return Path(value.removeprefix("sqlite:///"))
    if value.startswith("sqlite://"):
        return Path(value.removeprefix("sqlite://"))
    return Path(value)


def parse_datetime(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def normalize_label(label: Any) -> str:
    return str(label or "").strip().lower()


def class_from_label(label: Any) -> str:
    normalized = normalize_label(label)
    return LABEL_TO_CLASS.get(normalized, "unlabeled" if not normalized else "uncertain")


def safe_slug(value: Any, default: str = "unknown") -> str:
    text = str(value or default).strip().lower()
    chars: list[str] = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            chars.append(char)
        elif char in {" ", "/", "\\", ".", ":", "#"}:
            chars.append("_")
    cleaned = "".join(chars).strip("_")
    return cleaned or default


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return None


def parse_bbox(value: Any) -> list[float] | None:
    parsed = json_loads(value)
    if not isinstance(parsed, list) or len(parsed) != 4:
        return None
    try:
        return [float(item) for item in parsed]
    except Exception:
        return None


def bbox_geometry(bbox: list[float] | None, width: int | None, height: int | None) -> dict[str, Any]:
    if not bbox or not width or not height:
        return {
            "bbox_width": None,
            "bbox_height": None,
            "bbox_area_ratio": None,
            "bbox_center_x_norm": None,
            "bbox_center_y_norm": None,
        }
    x1, y1, x2, y2 = bbox
    bw = max(0.0, x2 - x1)
    bh = max(0.0, y2 - y1)
    return {
        "bbox_width": round(bw, 3),
        "bbox_height": round(bh, 3),
        "bbox_area_ratio": round((bw * bh) / max(1.0, float(width * height)), 8),
        "bbox_center_x_norm": round(((x1 + x2) / 2.0) / max(1.0, float(width)), 6),
        "bbox_center_y_norm": round(((y1 + y2) / 2.0) / max(1.0, float(height)), 6),
    }


def resolve_snapshot(path_value: str | None, *, base_dir: Path, snapshot_roots: list[Path]) -> Path | None:
    if not path_value:
        return None
    raw = Path(str(path_value))
    candidates = [raw]
    normalized = str(path_value).replace("\\", "/")
    if not raw.is_absolute():
        candidates.append(base_dir / raw)
    if normalized.startswith("/data/"):
        rel = normalized.removeprefix("/data/")
        candidates.append(base_dir / "data" / rel)
        for root in snapshot_roots:
            candidates.append(root / rel)
    for root in snapshot_roots:
        candidates.append(root / normalized.lstrip("/"))
        candidates.append(root / raw.name)
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def crop_with_margin(frame: Any, bbox: list[float], margin_pct: float):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    margin_x = box_w * max(0.0, margin_pct)
    margin_y = box_h * max(0.0, margin_pct)
    left = max(0, int(round(x1 - margin_x)))
    top = max(0, int(round(y1 - margin_y)))
    right = min(width, int(round(x2 + margin_x)))
    bottom = min(height, int(round(y2 + margin_y)))
    if right <= left or bottom <= top:
        return None
    crop = frame[top:bottom, left:right]
    return crop if crop is not None and crop.size > 0 else None


def draw_context(frame: Any, bbox: list[float] | None):
    output = frame.copy()
    if not bbox:
        return output
    height, width = output.shape[:2]
    x1, y1, x2, y2 = [int(round(value)) for value in bbox]
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(0, min(width - 1, x2))
    y2 = max(0, min(height - 1, y2))
    if x2 > x1 and y2 > y1:
        cv2.rectangle(output, (x1, y1), (x2, y2), (48, 255, 96), 2)
    return output


def is_useful_crop(crop: Any, min_size: int) -> bool:
    if crop is None or crop.size <= 0:
        return False
    height, width = crop.shape[:2]
    return height >= min_size and width >= min_size


def ensure_package_dirs(package_dir: Path) -> None:
    for class_name in CLASS_NAMES:
        for folder in ("crops_ia2", "crops_ia3_far", "context", "snapshots", "metadata"):
            (package_dir / class_name / folder).mkdir(parents=True, exist_ok=True)


def query_events(
    conn: sqlite3.Connection,
    *,
    since: str | None,
    until: str | None,
    camera_id: int | None,
    include_unreviewed: bool,
    include_feedback_history: bool,
    limit: int | None,
) -> list[sqlite3.Row]:
    where = ["1=1"]
    params: list[Any] = []
    if since:
        where.append("COALESCE(e.started_at, e.created_at) >= ?")
        params.append(since)
    if until:
        where.append("COALESCE(e.started_at, e.created_at) <= ?")
        params.append(until)
    if camera_id is not None:
        where.append("e.camera_id = ?")
        params.append(int(camera_id))
    if not include_unreviewed:
        where.append("lf.feedback_id IS NOT NULL")

    latest_filter = "" if include_feedback_history else "WHERE rn = 1"
    limit_sql = " LIMIT ?" if limit is not None else ""
    if limit is not None:
        params.append(max(0, int(limit)))

    sql = f"""
    WITH feedback_ranked AS (
        SELECT
            ef.*,
            ROW_NUMBER() OVER (
                PARTITION BY ef.event_id
                ORDER BY ef.reviewed_at DESC, ef.id DESC
            ) AS rn
        FROM event_feedback ef
    ),
    lf AS (
        SELECT
            id AS feedback_id,
            event_id,
            camera_id AS feedback_camera_id,
            label AS feedback_label,
            probable_cause,
            operator_note,
            reviewed_by,
            reviewed_at
        FROM feedback_ranked
        {latest_filter}
    )
    SELECT
        e.id AS event_id,
        e.camera_id,
        c.name AS camera_name,
        c.ip AS camera_ip,
        e.event_type,
        e.track_id,
        e.detector_score,
        e.confidence,
        e.event_score,
        e.details,
        e.snapshot_path,
        e.clip_path,
        e.bbox_json,
        e.severity,
        e.status AS event_status,
        e.alarm_eligible,
        e.lifecycle_action,
        e.is_alarm_active,
        e.rule_id,
        e.zone_id,
        e.roi_id,
        e.created_at,
        e.started_at,
        e.ended_at,
        lf.feedback_id,
        lf.feedback_label,
        lf.probable_cause,
        lf.operator_note,
        lf.reviewed_by,
        lf.reviewed_at
    FROM events e
    LEFT JOIN cameras c ON c.id = e.camera_id
    LEFT JOIN lf ON lf.event_id = e.id
    WHERE {" AND ".join(where)}
    ORDER BY COALESCE(e.started_at, e.created_at), e.id, lf.reviewed_at
    {limit_sql}
    """
    return conn.execute(sql, params).fetchall()


def event_stem(row: sqlite3.Row, class_name: str) -> str:
    track_id = row["track_id"] if row["track_id"] is not None else "na"
    feedback_id = row["feedback_id"] if row["feedback_id"] is not None else "unreviewed"
    return (
        f"event{row['event_id']}_feedback{feedback_id}_"
        f"{class_name}_cam{row['camera_id']}_track{track_id}"
    )


def write_image(path: Path, image: Any) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(path), image))


def collect_one(
    row: sqlite3.Row,
    *,
    package_dir: Path,
    base_dir: Path,
    snapshot_roots: list[Path],
    ia2_margin: float,
    ia3_margin: float,
    min_crop_size: int,
    include_snapshot_copy: bool,
) -> dict[str, Any]:
    label = normalize_label(row["feedback_label"])
    class_name = class_from_label(label)
    stem = event_stem(row, class_name)
    class_dir = package_dir / class_name
    bbox = parse_bbox(row["bbox_json"])
    snapshot = resolve_snapshot(row["snapshot_path"], base_dir=base_dir, snapshot_roots=snapshot_roots)

    crop_ia2_path = None
    crop_ia3_path = None
    context_path = None
    snapshot_copy_path = None
    status = "metadata_only"
    error = None
    frame_width = None
    frame_height = None
    crop_ia2_shape = None
    crop_ia3_shape = None

    if cv2 is None:
        status = "opencv_unavailable"
        error = str(CV2_IMPORT_ERROR)
    elif snapshot is None:
        status = "snapshot_missing" if row["snapshot_path"] else "snapshot_not_recorded"
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

            if include_snapshot_copy:
                suffix = snapshot.suffix.lower() if snapshot.suffix.lower() in IMAGE_EXTENSIONS else ".jpg"
                snapshot_target = class_dir / "snapshots" / f"{stem}_snapshot{suffix}"
                shutil.copy2(snapshot, snapshot_target)
                snapshot_copy_path = snapshot_target

    def rel(path: Path | None) -> str | None:
        return str(path.relative_to(package_dir)).replace("\\", "/") if path else None

    metadata = {
        "event_id": row["event_id"],
        "feedback_id": row["feedback_id"],
        "camera_id": row["camera_id"],
        "camera_name": row["camera_name"],
        "camera_ip": row["camera_ip"],
        "label": label,
        "class_name": class_name,
        "probable_cause": row["probable_cause"],
        "operator_note": row["operator_note"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "event_type": row["event_type"],
        "track_id": row["track_id"],
        "rule_id": row["rule_id"],
        "zone_id": row["zone_id"],
        "roi_id": row["roi_id"],
        "detector_score": row["detector_score"],
        "confidence": row["confidence"],
        "event_score": row["event_score"],
        "event_status": row["event_status"],
        "alarm_eligible": row["alarm_eligible"],
        "lifecycle_action": row["lifecycle_action"],
        "is_alarm_active": row["is_alarm_active"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "bbox_xyxy": bbox,
        **bbox_geometry(bbox, frame_width, frame_height),
        "frame_width": frame_width,
        "frame_height": frame_height,
        "source_snapshot_path": str(snapshot) if snapshot else row["snapshot_path"],
        "clip_path": row["clip_path"],
        "crop_ia2_path": rel(crop_ia2_path),
        "crop_ia3_far_path": rel(crop_ia3_path),
        "context_path": rel(context_path),
        "snapshot_copy_path": rel(snapshot_copy_path),
        "crop_ia2_shape_hw": crop_ia2_shape,
        "crop_ia3_far_shape_hw": crop_ia3_shape,
        "export_status": status,
        "export_error": error,
        "details": row["details"],
    }

    metadata_path = class_dir / "metadata" / f"{stem}.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    metadata["metadata_path"] = rel(metadata_path)
    metadata["crop_ia2_sha1"] = sha1_file(crop_ia2_path) if crop_ia2_path else None
    metadata["crop_ia3_far_sha1"] = sha1_file(crop_ia3_path) if crop_ia3_path else None
    return metadata


def write_csvs(package_dir: Path, rows: list[dict[str, Any]]) -> None:
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


def write_manifest(
    package_dir: Path,
    rows: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    database_path: Path,
) -> None:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_path": str(database_path),
        "total_events": len(rows),
        "counts_by_class": dict(Counter(row["class_name"] for row in rows)),
        "counts_by_label": dict(Counter(row["label"] or "unlabeled" for row in rows)),
        "counts_by_camera": dict(Counter(str(row["camera_name"] or row["camera_id"]) for row in rows)),
        "counts_by_status": dict(Counter(row["export_status"] for row in rows)),
        "trainable_ia2_crops": sum(1 for row in rows if row.get("crop_ia2_path") and row.get("class_name") in {"person", "not_person"}),
        "trainable_ia3_far_crops": sum(1 for row in rows if row.get("crop_ia3_far_path") and row.get("class_name") in {"person", "not_person"}),
        "class_names": list(CLASS_NAMES),
        "label_to_class": LABEL_TO_CLASS,
        "options": {
            "since": args.since,
            "until": args.until,
            "camera_id": args.camera_id,
            "include_unreviewed": bool(args.include_unreviewed),
            "include_feedback_history": bool(args.include_feedback_history),
            "ia2_margin": args.ia2_margin,
            "ia3_margin": args.ia3_margin,
            "min_crop_size": args.min_crop_size,
            "include_snapshot_copy": bool(args.include_snapshot_copy),
        },
    }
    (package_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    database_path = sqlite_path_from_url(args.database_url)
    if not database_path.exists():
        raise SystemExit(f"Banco nao encontrado: {database_path}")

    base_dir = Path(args.base_dir).resolve()
    snapshot_roots = [Path(item).resolve() for item in args.snapshot_root]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = output_dir / f"revalidator_training_dataset_{stamp}"
    package_dir.mkdir(parents=True, exist_ok=False)
    ensure_package_dirs(package_dir)

    conn = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = query_events(
            conn,
            since=parse_datetime(args.since),
            until=parse_datetime(args.until),
            camera_id=args.camera_id,
            include_unreviewed=bool(args.include_unreviewed),
            include_feedback_history=bool(args.include_feedback_history),
            limit=args.limit,
        )
    finally:
        conn.close()

    exported = [
        collect_one(
            row,
            package_dir=package_dir,
            base_dir=base_dir,
            snapshot_roots=snapshot_roots,
            ia2_margin=float(args.ia2_margin),
            ia3_margin=float(args.ia3_margin),
            min_crop_size=max(1, int(args.min_crop_size)),
            include_snapshot_copy=bool(args.include_snapshot_copy),
        )
        for row in rows
    ]
    write_csvs(package_dir, exported)
    write_manifest(package_dir, exported, args=args, database_path=database_path)

    zip_path = None
    if not args.no_zip:
        zip_path = Path(shutil.make_archive(str(package_dir), "zip", root_dir=package_dir))
        if not args.keep_folder:
            shutil.rmtree(package_dir)

    print(f"Eventos coletados: {len(exported)}")
    print("Classes:", dict(Counter(row["class_name"] for row in exported)))
    print("Status:", dict(Counter(row["export_status"] for row in exported)))
    if zip_path:
        print(f"ZIP: {zip_path}")
    else:
        print(f"Pasta: {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
