r"""Audita os ultimos eventos avaliados com a IA3 Far Revalidator.

Exemplos:

    py -3 -B scripts\audit_latest_reviewed_events_with_far_revalidator.py --limit 400

No Linux/Docker:

    python -B scripts/audit_latest_reviewed_events_with_far_revalidator.py --limit 400
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

from app.analytics_v2.revalidation.far_person_revalidator import FarPersonRevalidator  # noqa: E402
from app.core.config import settings, sqlite_url_for  # noqa: E402
from scripts.audit_latest_reviewed_events_with_revalidator import (  # noqa: E402
    LABEL_TO_CLASS,
    UNCERTAIN_LABELS,
    as_iso,
    latest_reviewed_rows,
    load_bbox,
    resolve_path,
)


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
    "p_person_far",
    "p_not_person_far",
    "threshold",
    "triggered",
    "applied",
    "reason",
    "trigger_reason",
    "inference_ms",
    "crop_width",
    "crop_height",
    "bbox_width",
    "bbox_height",
    "bbox_height_ratio",
    "quality_reason",
    "snapshot_path",
    "bbox",
    "details",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Roda a IA3 Far Revalidator nos ultimos eventos avaliados e compara com o rotulo humano."
    )
    parser.add_argument("--limit", type=int, default=400, help="Quantidade de eventos avaliados mais recentes.")
    parser.add_argument("--database", help="Caminho do analytics.db. Padrao: settings.database_url.")
    parser.add_argument("--base-dir", default=settings.app_base_dir, help="Base para resolver snapshots relativos.")
    parser.add_argument("--output-dir", default="reports/far_revalidator_audit", help="Diretorio dos CSV/JSON gerados.")
    parser.add_argument("--model", default=settings.far_person_revalidator_model_path, help="Modelo .pt/.onnx da IA3.")
    parser.add_argument("--threshold", type=float, default=settings.far_person_revalidator_threshold)
    parser.add_argument("--imgsz", type=int, default=settings.far_person_revalidator_imgsz)
    parser.add_argument("--margin-pct", type=float, default=settings.far_person_revalidator_margin_pct)
    parser.add_argument("--include-uncertain", action="store_true", help="Inclui inconclusive/uncertain sem contar acerto.")
    return parser.parse_args()


def sqlite_url_from_args(database: str | None) -> str:
    if database:
        return sqlite_url_for(Path(database))
    if settings.database_url.startswith("sqlite:///"):
        return settings.database_url
    raise SystemExit("Use --database para informar o analytics.db SQLite.")


def prediction_from_scores(person_far_score: float | None, threshold: float) -> str | None:
    if person_far_score is None:
        return None
    return "person" if float(person_far_score) >= float(threshold) else "not_person"


def audit_one(
    *,
    validator: FarPersonRevalidator,
    base_dir: Path,
    feedback,
    event,
    camera,
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
    pred_class = prediction_from_scores(result.person_far_score, validator.threshold) if result.triggered else None
    is_match = pred_class == truth_class if truth_class in {"person", "not_person"} and pred_class else None
    quality = result.quality or {}

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
        "p_person_far": result.person_far_score,
        "p_not_person_far": result.not_person_far_score,
        "threshold": validator.threshold,
        "triggered": result.triggered,
        "applied": result.applied,
        "reason": result.reason,
        "trigger_reason": result.trigger_reason,
        "inference_ms": round(float(result.inference_ms or 0.0), 2),
        "crop_width": quality.get("crop_width") or quality.get("far_crop_width"),
        "crop_height": quality.get("crop_height") or quality.get("far_crop_height"),
        "bbox_width": quality.get("bbox_width"),
        "bbox_height": quality.get("bbox_height"),
        "bbox_height_ratio": quality.get("bbox_height_ratio"),
        "quality_reason": quality.get("quality_reason"),
        "snapshot_path": str(snapshot_path) if snapshot_path else event.snapshot_path,
        "bbox": json.dumps(bbox) if bbox else "",
        "details": event.details,
    }


def build_summary(rows: list[dict[str, Any]], *, limit: int, model_path: str, threshold: float, imgsz: int) -> dict[str, Any]:
    triggered = [row for row in rows if row.get("triggered")]
    applied = [row for row in rows if row.get("applied")]
    comparable = [row for row in triggered if row["truth_class"] in {"person", "not_person"} and row["pred_class"]]
    truth_counts = Counter(row["truth_class"] for row in comparable)
    pred_counts = Counter(row["pred_class"] for row in comparable)
    reason_counts = Counter(str(row["reason"]) for row in rows)
    trigger_counts = Counter(str(row["trigger_reason"]) for row in triggered)
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
        "triggered_rows": len(triggered),
        "applied_rows": len(applied),
        "comparable_rows": len(comparable),
        "model_path": model_path,
        "threshold": threshold,
        "imgsz": imgsz,
        "accuracy": correct / len(comparable) if comparable else 0.0,
        "person_far_recall": (person_total - person_to_not) / person_total if person_total else None,
        "not_person_far_recall": (not_person_total - not_to_person) / not_person_total if not_person_total else None,
        "person_far_to_not_person_far": person_to_not,
        "not_person_far_to_person_far": not_to_person,
        "truth_counts": dict(truth_counts),
        "pred_counts": dict(pred_counts),
        "reason_counts": dict(reason_counts),
        "trigger_counts": dict(trigger_counts),
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

    validator = FarPersonRevalidator(
        model_path=args.model,
        threshold=args.threshold,
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
    report_csv = output_dir / f"far_revalidator_audit_latest_{args.limit}_{timestamp}.csv"
    mismatches_csv = output_dir / f"far_revalidator_audit_mismatches_{args.limit}_{timestamp}.csv"
    summary_json = output_dir / f"far_revalidator_audit_summary_{args.limit}_{timestamp}.json"
    write_csv(report_csv, rows)
    write_csv(mismatches_csv, [row for row in rows if row.get("match") is False])
    summary = build_summary(rows, limit=args.limit, model_path=validator.model_path, threshold=args.threshold, imgsz=args.imgsz)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Eventos avaliados no relatorio: {len(rows)}")
    print(f"Candidatos pequenos/distantes: {summary['triggered_rows']}")
    print(f"IA3 aplicada: {summary['applied_rows']}")
    print(f"Comparaveis person/not_person: {summary['comparable_rows']}")
    print(f"Accuracy IA3 far: {summary['accuracy']:.3f}")
    person_recall = summary["person_far_recall"]
    print(f"Person far recall: {person_recall:.3f}" if person_recall is not None else "Person far recall: n/a")
    print(f"person_far -> not_person_far: {summary['person_far_to_not_person_far']}")
    print(f"not_person_far -> person_far: {summary['not_person_far_to_person_far']}")
    print(f"CSV: {report_csv}")
    print(f"Erros: {mismatches_csv}")
    print(f"Resumo: {summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
