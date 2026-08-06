r"""Audita os ultimos eventos avaliados com o revalidador IA2 atual.

Exemplos:

    py -3 -B scripts\audit_latest_reviewed_events_with_revalidator.py --limit 400

No Linux/Docker:

    python -B scripts/audit_latest_reviewed_events_with_revalidator.py --limit 400
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.analytics_v2.revalidation.person_crop_revalidator import PersonCropRevalidator  # noqa: E402
from app.core.config import settings, sqlite_url_for  # noqa: E402
from app.db.models import Camera, Event, EventFeedback  # noqa: E402


LABEL_TO_CLASS = {
    "true_positive": "person",
    "expected_event": "person",
    "false_positive": "not_person",
}
UNCERTAIN_LABELS = {"inconclusive", "uncertain"}
FIELDNAMES = [
    "event_id",
    "feedback_id",
    "reviewed_at",
    "camera_id",
    "camera_name",
    "event_type",
    "track_id",
    "human_label",
    "truth_class",
    "pred_class",
    "match",
    "p_person",
    "p_not_person",
    "threshold",
    "applied",
    "reason",
    "inference_ms",
    "snapshot_path",
    "bbox",
    "details",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Roda a IA2/revalidador nos ultimos eventos avaliados e compara com o rotulo humano."
    )
    parser.add_argument("--limit", type=int, default=400, help="Quantidade de eventos avaliados mais recentes.")
    parser.add_argument("--database", help="Caminho do analytics.db. Padrao: settings.database_url.")
    parser.add_argument("--base-dir", default=settings.app_base_dir, help="Base para resolver snapshots relativos.")
    parser.add_argument("--output-dir", default="reports/revalidator_audit", help="Diretorio dos CSV/JSON gerados.")
    parser.add_argument("--model", default=settings.person_revalidator_model_path, help="Modelo .pt/.onnx do revalidador.")
    parser.add_argument("--threshold", type=float, default=settings.person_revalidator_threshold)
    parser.add_argument("--imgsz", type=int, default=settings.person_revalidator_imgsz)
    parser.add_argument("--margin-pct", type=float, default=settings.person_revalidator_margin_pct)
    parser.add_argument("--include-uncertain", action="store_true", help="Inclui inconclusive/uncertain sem contar acerto.")
    return parser.parse_args()


def sqlite_url_from_args(database: str | None) -> str:
    if database:
        return sqlite_url_for(Path(database))
    if settings.database_url.startswith("sqlite:///"):
        return settings.database_url
    raise SystemExit("Use --database para informar o analytics.db SQLite.")


def resolve_path(path_value: str | None, base_dir: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        path = base_dir / path
    return path if path.exists() else None


def load_bbox(event: Event) -> list[float] | None:
    if not event.bbox_json:
        return None
    try:
        parsed = json.loads(event.bbox_json)
        if isinstance(parsed, list) and len(parsed) == 4:
            return [float(value) for value in parsed]
    except Exception:
        return None
    return None


def as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def latest_reviewed_rows(session, limit: int) -> list[tuple[EventFeedback, Event, Camera | None]]:
    rows = (
        session.query(EventFeedback, Event, Camera)
        .join(Event, Event.id == EventFeedback.event_id)
        .outerjoin(Camera, Camera.id == Event.camera_id)
        .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
        .limit(max(1, int(limit) * 4))
        .all()
    )

    latest_by_event: dict[int, tuple[EventFeedback, Event, Camera | None]] = {}
    for feedback, event, camera in rows:
        if event.id not in latest_by_event:
            latest_by_event[event.id] = (feedback, event, camera)
        if len(latest_by_event) >= limit:
            break
    return list(latest_by_event.values())


def prediction_from_scores(person_score: float | None, threshold: float) -> str | None:
    if person_score is None:
        return None
    return "person" if float(person_score) >= float(threshold) else "not_person"


def audit_one(
    *,
    validator: PersonCropRevalidator,
    base_dir: Path,
    feedback: EventFeedback,
    event: Event,
    camera: Camera | None,
    include_uncertain: bool,
) -> dict[str, Any] | None:
    label = str(feedback.label or "").strip()
    truth_class = LABEL_TO_CLASS.get(label)
    if truth_class is None:
        if label in UNCERTAIN_LABELS and include_uncertain:
            truth_class = "uncertain"
        else:
            return None

    snapshot_path = resolve_path(event.snapshot_path, base_dir)
    bbox = load_bbox(event)
    frame = cv2.imread(str(snapshot_path)) if snapshot_path else None
    result = validator.validate(frame, bbox)
    pred_class = prediction_from_scores(result.person_score, validator.threshold)
    is_match = pred_class == truth_class if truth_class in {"person", "not_person"} and pred_class else None

    return {
        "event_id": event.id,
        "feedback_id": feedback.id,
        "reviewed_at": as_iso(feedback.reviewed_at),
        "camera_id": event.camera_id,
        "camera_name": getattr(camera, "name", None),
        "event_type": event.event_type,
        "track_id": event.track_id,
        "human_label": label,
        "truth_class": truth_class,
        "pred_class": pred_class,
        "match": is_match,
        "p_person": result.person_score,
        "p_not_person": result.not_person_score,
        "threshold": validator.threshold,
        "applied": result.applied,
        "reason": result.reason,
        "inference_ms": round(float(result.inference_ms or 0.0), 2),
        "snapshot_path": str(snapshot_path) if snapshot_path else event.snapshot_path,
        "bbox": json.dumps(bbox) if bbox else "",
        "details": event.details,
    }


def build_summary(rows: list[dict[str, Any]], *, limit: int, model_path: str, threshold: float, imgsz: int) -> dict[str, Any]:
    comparable = [row for row in rows if row["truth_class"] in {"person", "not_person"} and row["pred_class"]]
    truth_counts = Counter(row["truth_class"] for row in comparable)
    pred_counts = Counter(row["pred_class"] for row in comparable)
    status_counts = Counter(str(row["reason"]) for row in rows)
    correct = sum(1 for row in comparable if row["match"] is True)
    mismatches = [row for row in comparable if row["match"] is False]
    person_to_not = sum(1 for row in comparable if row["truth_class"] == "person" and row["pred_class"] == "not_person")
    not_to_person = sum(1 for row in comparable if row["truth_class"] == "not_person" and row["pred_class"] == "person")
    person_total = truth_counts["person"]
    not_person_total = truth_counts["not_person"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_limit": limit,
        "rows": len(rows),
        "comparable_rows": len(comparable),
        "model_path": model_path,
        "threshold": threshold,
        "imgsz": imgsz,
        "accuracy": correct / len(comparable) if comparable else 0.0,
        "person_recall": (person_total - person_to_not) / person_total if person_total else None,
        "not_person_recall": (not_person_total - not_to_person) / not_person_total if not_person_total else None,
        "person_to_not_person": person_to_not,
        "not_person_to_person": not_to_person,
        "truth_counts": dict(truth_counts),
        "pred_counts": dict(pred_counts),
        "reason_counts": dict(status_counts),
        "mismatch_count": len(mismatches),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    database_url = sqlite_url_from_args(args.database)
    base_dir = Path(args.base_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    connect_args = {}
    engine_kwargs = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30.0}
        engine_kwargs["poolclass"] = NullPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    validator = PersonCropRevalidator(
        model_path=args.model,
        threshold=args.threshold,
        mode="audit",
        margin_pct=args.margin_pct,
        imgsz=args.imgsz,
        enabled=True,
    )

    rows: list[dict[str, Any]] = []
    with Session() as session:
        for feedback, event, camera in latest_reviewed_rows(session, args.limit):
            row = audit_one(
                validator=validator,
                base_dir=base_dir,
                feedback=feedback,
                event=event,
                camera=camera,
                include_uncertain=args.include_uncertain,
            )
            if row is not None:
                rows.append(row)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_csv = output_dir / f"revalidator_audit_latest_{args.limit}_{timestamp}.csv"
    mismatches_csv = output_dir / f"revalidator_audit_mismatches_{args.limit}_{timestamp}.csv"
    summary_json = output_dir / f"revalidator_audit_summary_{args.limit}_{timestamp}.json"
    write_csv(report_csv, rows)
    write_csv(mismatches_csv, [row for row in rows if row.get("match") is False])
    summary = build_summary(
        rows,
        limit=args.limit,
        model_path=validator.model_path,
        threshold=args.threshold,
        imgsz=args.imgsz,
    )
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Eventos avaliados no relatorio: {len(rows)}")
    print(f"Comparaveis person/not_person: {summary['comparable_rows']}")
    print(f"Accuracy: {summary['accuracy']:.3f}")
    person_recall = summary["person_recall"]
    print(f"Person recall: {person_recall:.3f}" if person_recall is not None else "Person recall: n/a")
    print(f"person -> not_person: {summary['person_to_not_person']}")
    print(f"not_person -> person: {summary['not_person_to_person']}")
    print(f"CSV: {report_csv}")
    print(f"Erros: {mismatches_csv}")
    print(f"Resumo: {summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
