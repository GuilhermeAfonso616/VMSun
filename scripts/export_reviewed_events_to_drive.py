r"""Exporta eventos avaliados para um pacote de treino no Google Drive.

O script usa a pasta local sincronizada do Google Drive como destino. Exemplo:

    py -3 -B scripts/export_reviewed_events_to_drive.py --output-dir "G:\Meu Drive\VMS Treino"

Ele nao usa a API do Drive: basta apontar `--output-dir` para uma pasta que o
Google Drive for desktop sincronize.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

try:
    import cv2
except Exception as exc:  # pragma: no cover - mensagem operacional
    cv2 = None
    CV2_IMPORT_ERROR = exc
else:
    CV2_IMPORT_ERROR = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings, sqlite_url_for  # noqa: E402
from app.db.models import Camera, Event, EventFeedback  # noqa: E402


LABEL_TO_CLASS = {
    "true_positive": "person",
    "expected_event": "person",
    "false_positive": "not_person",
    "inconclusive": "uncertain",
}

CLASS_NAMES = ("person", "not_person", "uncertain")
MIN_CROP_SIZE_PX = 16


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exporta todos os eventos avaliados para crops/contexto/metadata e "
            "salva um ZIP em uma pasta local, normalmente sincronizada com Google Drive."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Pasta local de destino. Use uma pasta sincronizada pelo Google Drive.",
    )
    parser.add_argument(
        "--database",
        help="Caminho do analytics.db. Padrao: banco configurado em app.core.config.",
    )
    parser.add_argument(
        "--base-dir",
        default=settings.app_base_dir,
        help="Base para resolver caminhos relativos de snapshot. Padrao: raiz do projeto configurada.",
    )
    parser.add_argument(
        "--since",
        help="Exporta somente avaliacoes revisadas a partir desta data ISO, ex: 2026-05-03.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limita a quantidade de eventos exportados. Util para teste rapido.",
    )
    parser.add_argument(
        "--include-feedback-history",
        action="store_true",
        help="Exporta todas as avaliacoes. Por padrao, usa so a avaliacao mais recente por evento.",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Nao cria ZIP; deixa somente a pasta exportada.",
    )
    parser.add_argument(
        "--keep-folder",
        action="store_true",
        help="Mantem a pasta temporaria mesmo apos criar o ZIP.",
    )
    return parser.parse_args()


def sqlite_url_from_args(database: str | None) -> str:
    if database:
        return sqlite_url_for(Path(database))
    if settings.database_url.startswith("sqlite:///"):
        return settings.database_url
    raise SystemExit("Use --database para informar um arquivo SQLite analytics.db.")


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SystemExit(f"Data invalida em --since: {value}") from exc
    return parsed


def safe_slug(value: Any, default: str = "unknown") -> str:
    text = str(value or default).strip().lower()
    allowed = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char in {" ", "/", "\\", ".", ":"}:
            allowed.append("_")
    cleaned = "".join(allowed).strip("_")
    return cleaned or default


def load_bbox(event: Event) -> list[float] | None:
    if not event.bbox_json:
        return None
    try:
        parsed = json.loads(event.bbox_json)
    except Exception:
        return None
    if isinstance(parsed, list) and len(parsed) == 4:
        try:
            return [float(value) for value in parsed]
        except (TypeError, ValueError):
            return None
    return None


def resolve_existing_path(path_value: str | None, base_dir: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        path = base_dir / path
    return path if path.exists() else None


def crop_with_margin(frame, bbox: list[float], margin_pct: float = 0.20):
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


def draw_context_with_bbox(frame, bbox: list[float] | None):
    output = frame.copy()
    if bbox and len(bbox) == 4:
        height, width = output.shape[:2]
        x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width - 1, x2))
        y2 = max(0, min(height - 1, y2))
        if x2 > x1 and y2 > y1:
            cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 0), 2)
    return output


def crop_is_useful(crop) -> bool:
    if crop is None or crop.size <= 0:
        return False
    height, width = crop.shape[:2]
    return height >= MIN_CROP_SIZE_PX and width >= MIN_CROP_SIZE_PX


def as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def feedback_sort_key(feedback: EventFeedback) -> tuple[str, int]:
    return (as_iso(feedback.reviewed_at) or "", feedback.id or 0)


def build_event_stem(event: Event, feedback: EventFeedback, class_name: str, history: bool) -> str:
    track_id = event.track_id if event.track_id is not None else "na"
    base = f"event{event.id}_latest_{class_name}_cam{event.camera_id}_track{track_id}"
    if history:
        reviewed = safe_slug(as_iso(feedback.reviewed_at), "review")
        return f"{base}_feedback{feedback.id}_{reviewed}"
    return base


def latest_feedback_rows(rows: list[tuple[EventFeedback, Event, Camera | None]]) -> list[tuple[EventFeedback, Event, Camera | None]]:
    latest_by_event: dict[int, tuple[EventFeedback, Event, Camera | None]] = {}
    for feedback, event, camera in rows:
        current = latest_by_event.get(event.id)
        if current is None:
            latest_by_event[event.id] = (feedback, event, camera)
            continue
        current_feedback = current[0]
        current_key = feedback_sort_key(current_feedback)
        new_key = feedback_sort_key(feedback)
        if new_key >= current_key:
            latest_by_event[event.id] = (feedback, event, camera)
    return list(latest_by_event.values())


def query_reviewed_events(
    session,
    *,
    since: datetime | None,
    include_history: bool,
    limit: int | None,
) -> list[tuple[EventFeedback, Event, Camera | None]]:
    query = (
        session.query(EventFeedback, Event, Camera)
        .join(Event, Event.id == EventFeedback.event_id)
        .outerjoin(Camera, Camera.id == Event.camera_id)
        .order_by(EventFeedback.event_id.asc(), EventFeedback.reviewed_at.asc(), EventFeedback.id.asc())
    )
    if since is not None:
        query = query.filter(EventFeedback.reviewed_at >= since)
    rows = query.all()
    if not include_history:
        rows = latest_feedback_rows(rows)
        rows.sort(key=lambda row: feedback_sort_key(row[0]))
    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows


def make_dirs(package_dir: Path) -> None:
    for class_name in CLASS_NAMES:
        for subdir in ("crops", "context", "metadata"):
            (package_dir / class_name / subdir).mkdir(parents=True, exist_ok=True)


def export_one(
    *,
    package_dir: Path,
    base_dir: Path,
    feedback: EventFeedback,
    event: Event,
    camera: Camera | None,
    include_history: bool,
) -> dict[str, Any]:
    label = str(feedback.label or "").strip()
    class_name = LABEL_TO_CLASS.get(label, "uncertain")
    stem = build_event_stem(event, feedback, class_name, include_history)
    snapshot_path = resolve_existing_path(event.snapshot_path, base_dir)
    bbox = load_bbox(event)
    crop_path = None
    context_path = None
    status = "metadata_only"
    error = None

    if snapshot_path and cv2 is not None:
        frame = cv2.imread(str(snapshot_path))
        if frame is None:
            status = "snapshot_unreadable"
        else:
            context = draw_context_with_bbox(frame, bbox)
            context_path = package_dir / class_name / "context" / f"{stem}_context.jpg"
            cv2.imwrite(str(context_path), context)
            status = "context_saved"

            if bbox:
                crop = crop_with_margin(frame, bbox)
                if crop_is_useful(crop):
                    crop_path = package_dir / class_name / "crops" / f"{stem}_crop.jpg"
                    cv2.imwrite(str(crop_path), crop)
                    status = "crop_saved"
                else:
                    status = "context_saved_crop_not_useful"
            else:
                status = "context_saved_missing_bbox"
    elif snapshot_path and cv2 is None:
        status = "opencv_unavailable"
        error = str(CV2_IMPORT_ERROR)
    elif event.snapshot_path:
        status = "snapshot_missing"

    metadata = {
        "event_id": event.id,
        "feedback_id": feedback.id,
        "camera_id": event.camera_id,
        "camera_name": getattr(camera, "name", None),
        "camera_ip": getattr(camera, "ip", None),
        "label": label,
        "class_name": class_name,
        "probable_cause": feedback.probable_cause,
        "operator_note": feedback.operator_note,
        "reviewed_by": feedback.reviewed_by,
        "reviewed_at": as_iso(feedback.reviewed_at),
        "event_type": event.event_type,
        "track_id": event.track_id,
        "detector_score": event.detector_score,
        "confidence": event.confidence,
        "event_score": event.event_score,
        "event_status": event.status,
        "event_created_at": as_iso(event.created_at),
        "event_started_at": as_iso(event.started_at),
        "event_ended_at": as_iso(event.ended_at),
        "bbox": bbox,
        "snapshot_path": str(snapshot_path) if snapshot_path else event.snapshot_path,
        "crop_path": str(crop_path.relative_to(package_dir)) if crop_path else None,
        "context_path": str(context_path.relative_to(package_dir)) if context_path else None,
        "export_status": status,
        "export_error": error,
    }

    metadata_path = package_dir / class_name / "metadata" / f"{stem}.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata["metadata_path"] = str(metadata_path.relative_to(package_dir))
    return metadata


def write_csv(package_dir: Path, rows: list[dict[str, Any]]) -> None:
    csv_path = package_dir / "events.csv"
    fieldnames = [
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
        "confidence",
        "event_score",
        "export_status",
        "crop_path",
        "context_path",
        "metadata_path",
        "snapshot_path",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_manifest(
    *,
    package_dir: Path,
    rows: list[dict[str, Any]],
    database_url: str,
    base_dir: Path,
    include_history: bool,
) -> None:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_url": database_url,
        "base_dir": str(base_dir),
        "include_feedback_history": include_history,
        "total_rows": len(rows),
        "counts_by_class": dict(Counter(row["class_name"] for row in rows)),
        "counts_by_label": dict(Counter(row["label"] for row in rows)),
        "counts_by_status": dict(Counter(row["export_status"] for row in rows)),
        "classes": list(CLASS_NAMES),
        "label_to_class": LABEL_TO_CLASS,
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    database_url = sqlite_url_from_args(args.database)
    base_dir = Path(args.base_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = output_dir / f"reviewed_events_export_{timestamp}"
    package_dir.mkdir(parents=True, exist_ok=False)
    make_dirs(package_dir)

    connect_args = {}
    engine_kwargs = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30.0}
        engine_kwargs["poolclass"] = NullPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    since = parse_since(args.since)
    rows: list[dict[str, Any]] = []
    with Session() as session:
        feedback_rows = query_reviewed_events(
            session,
            since=since,
            include_history=args.include_feedback_history,
            limit=args.limit,
        )
        for feedback, event, camera in feedback_rows:
            rows.append(
                export_one(
                    package_dir=package_dir,
                    base_dir=base_dir,
                    feedback=feedback,
                    event=event,
                    camera=camera,
                    include_history=args.include_feedback_history,
                )
            )

    write_csv(package_dir, rows)
    write_manifest(
        package_dir=package_dir,
        rows=rows,
        database_url=database_url,
        base_dir=base_dir,
        include_history=args.include_feedback_history,
    )

    zip_path = None
    if not args.no_zip:
        zip_base = output_dir / package_dir.name
        zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=package_dir))
        if not args.keep_folder:
            shutil.rmtree(package_dir)

    counts = Counter(row["class_name"] for row in rows)
    status_counts = Counter(row["export_status"] for row in rows)
    print(f"Eventos exportados: {len(rows)}")
    print("Classes:", dict(counts))
    print("Status:", dict(status_counts))
    if zip_path:
        print(f"ZIP salvo em: {zip_path}")
    else:
        print(f"Pasta salva em: {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
