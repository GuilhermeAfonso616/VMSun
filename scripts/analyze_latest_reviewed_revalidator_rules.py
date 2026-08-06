r"""Gera auditoria IA1/IA2/IA3 e avalia candidatos de block/suppress.

Exemplos:

    py -3 -B scripts\analyze_latest_reviewed_revalidator_rules.py --limit 1500 --overwrite

No Docker/Linux:

    python -B scripts/analyze_latest_reviewed_revalidator_rules.py \
      --limit 1500 \
      --output-dir /app/datasets/revalidator_review_audit \
      --report-dir /app/reports/revalidator_rule_analysis \
      --overwrite
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings, sqlite_url_for  # noqa: E402
from app.db.models import Camera, Event, EventFeedback  # noqa: E402
from app.services.revalidator_review_audit_service import (  # noqa: E402
    build_review_audit_payload,
    write_review_audit_payload_json,
)


PERSON_LABELS = {"true_positive", "expected_event"}
NOT_PERSON_LABELS = {"false_positive"}
CSV_FIELDS = [
    "event_id",
    "feedback_id",
    "reviewed_at",
    "camera_id",
    "camera_name",
    "event_type",
    "track_id",
    "human_label",
    "truth_class",
    "status",
    "reason",
    "ia1_person_score",
    "ia2_person_score",
    "ia2_not_person_score",
    "ia2_raw",
    "ia2_operational",
    "ia3_triggered",
    "ia3_person_far_score",
    "ia3_not_person_far_score",
    "ia3_raw",
    "ia3_trigger_reason",
    "quality_reason",
    "crop_width",
    "crop_height",
    "bbox_height_ratio",
    "near_border",
    "region_cell",
    "region_risk",
    "region_fp_count",
    "region_tp_count",
    "consensus_block_candidate",
    "small_bbox_consensus_candidate",
    "border_consensus_candidate",
    "block_blocked_by_quality_gate",
    "block_blocked_by_border",
    "ia2_strong_not_person_without_ia3",
    "consensus_reason",
    "consensus_quality_reason",
    "layered_decision",
    "layered_reason",
    "maturity_level",
    "maturity_decision",
    "alarm_action",
    "snapshot_path",
    "json_path",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita os ultimos eventos avaliados e mede seguranca das regras IA2/IA3."
    )
    parser.add_argument("--limit", type=int, default=1500, help="Quantidade de eventos avaliados recentes.")
    parser.add_argument("--camera-id", type=int, help="Filtra por camera.")
    parser.add_argument("--database", help="Caminho do analytics.db. Padrao: settings.database_url.")
    parser.add_argument("--output-dir", default=settings.revalidator_review_audit_dir, help="Diretorio dos JSONs.")
    parser.add_argument("--report-dir", default="reports/revalidator_rule_analysis", help="Diretorio de CSV/summary.")
    parser.add_argument("--overwrite", action="store_true", help="Regrava JSONs existentes.")
    return parser.parse_args()


def sqlite_url_from_args(database: str | None) -> str:
    if database:
        return sqlite_url_for(Path(database))
    if settings.database_url.startswith("sqlite:///"):
        return settings.database_url
    raise SystemExit("Use --database para informar o analytics.db SQLite.")


def resolve_output_root(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path(settings.app_base_dir) / path
    return path


def latest_feedback_rows(session, *, limit: int, camera_id: int | None):
    query = (
        session.query(EventFeedback, Event, Camera)
        .join(Event, Event.id == EventFeedback.event_id)
        .outerjoin(Camera, Camera.id == Event.camera_id)
        .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
    )
    if camera_id is not None:
        query = query.filter(EventFeedback.camera_id == camera_id)

    rows = query.limit(max(1, int(limit) * 4)).all()
    latest_by_event: dict[int, tuple[EventFeedback, Event, Camera | None]] = {}
    for feedback, event, camera in rows:
        if event.id not in latest_by_event:
            latest_by_event[event.id] = (feedback, event, camera)
        if len(latest_by_event) >= limit:
            break
    return list(latest_by_event.values())


def region_history_rows(session, *, camera_id: int | None):
    query = (
        session.query(EventFeedback, Event)
        .join(Event, Event.id == EventFeedback.event_id)
        .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
    )
    if camera_id is not None:
        query = query.filter(EventFeedback.camera_id == camera_id)
    return query.limit(max(1, int(settings.region_memory_history_limit or 5000))).all()


def truth_from_label(label: str | None) -> str:
    normalized = str(label or "").strip().lower()
    if normalized in PERSON_LABELS:
        return "person"
    if normalized in NOT_PERSON_LABELS:
        return "not_person"
    return "uncertain"


def as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def fmt(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def expected_output_path(output_root: Path, payload: dict[str, Any]) -> Path:
    event = payload.get("event") or {}
    feedback = payload.get("feedback") or {}
    return output_root / f"camera_{event.get('camera_id')}" / f"event_{event.get('id')}_feedback_{feedback.get('id')}.json"


def flatten_payload(payload: dict[str, Any], *, camera: Camera | None, json_path: Path) -> dict[str, Any]:
    event = payload.get("event") or {}
    feedback = payload.get("feedback") or {}
    ia1 = payload.get("ia1") or {}
    ia2 = payload.get("ia2") or {}
    ia3 = payload.get("ia3") or {}
    quality = ia2.get("quality") or {}
    region = payload.get("region_memory") or {}
    consensus = payload.get("consensus_revalidator") or {}
    layered = payload.get("layered_decision") or {}
    maturity = payload.get("event_maturity") or {}
    alarm = payload.get("alarm_decision") or {}
    return {
        "event_id": event.get("id"),
        "feedback_id": feedback.get("id"),
        "reviewed_at": fmt(feedback.get("reviewed_at")),
        "camera_id": event.get("camera_id"),
        "camera_name": getattr(camera, "name", None),
        "event_type": event.get("event_type"),
        "track_id": event.get("track_id"),
        "human_label": feedback.get("label"),
        "truth_class": truth_from_label(feedback.get("label")),
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "ia1_person_score": ia1.get("person_score"),
        "ia2_person_score": ia2.get("person_score"),
        "ia2_not_person_score": ia2.get("not_person_score"),
        "ia2_raw": ia2.get("raw_model_interpretation"),
        "ia2_operational": ia2.get("operational_result"),
        "ia3_triggered": ia3.get("triggered"),
        "ia3_person_far_score": ia3.get("person_far_score"),
        "ia3_not_person_far_score": ia3.get("not_person_far_score"),
        "ia3_raw": ia3.get("raw_model_interpretation"),
        "ia3_trigger_reason": ia3.get("trigger_reason"),
        "quality_reason": quality.get("quality_reason"),
        "crop_width": quality.get("crop_width") or quality.get("far_crop_width"),
        "crop_height": quality.get("crop_height") or quality.get("far_crop_height"),
        "bbox_height_ratio": quality.get("bbox_height_ratio"),
        "near_border": quality.get("near_border"),
        "region_cell": region.get("region_cell"),
        "region_risk": region.get("risk_level"),
        "region_fp_count": region.get("false_positive_count"),
        "region_tp_count": region.get("true_positive_count"),
        "consensus_block_candidate": consensus.get("block_candidate"),
        "small_bbox_consensus_candidate": consensus.get("small_bbox_consensus_candidate"),
        "border_consensus_candidate": consensus.get("border_consensus_candidate"),
        "block_blocked_by_quality_gate": consensus.get("block_blocked_by_quality_gate"),
        "block_blocked_by_border": consensus.get("block_blocked_by_border"),
        "ia2_strong_not_person_without_ia3": consensus.get("ia2_strong_not_person_without_ia3"),
        "consensus_reason": consensus.get("reason"),
        "consensus_quality_reason": consensus.get("quality_reason"),
        "layered_decision": layered.get("decision"),
        "layered_reason": layered.get("reason"),
        "maturity_level": maturity.get("level"),
        "maturity_decision": maturity.get("decision"),
        "alarm_action": alarm.get("action"),
        "snapshot_path": event.get("snapshot_path"),
        "json_path": str(json_path),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def count_truth(rows: list[dict[str, Any]], predicate) -> dict[str, Any]:
    selected = [row for row in rows if predicate(row)]
    truth_counts = Counter(row["truth_class"] for row in selected)
    return {
        "total": len(selected),
        "person": truth_counts.get("person", 0),
        "not_person": truth_counts.get("not_person", 0),
        "uncertain": truth_counts.get("uncertain", 0),
    }


def build_summary(rows: list[dict[str, Any]], *, requested_limit: int) -> dict[str, Any]:
    comparable = [row for row in rows if row["truth_class"] in {"person", "not_person"}]
    truth_counts = Counter(row["truth_class"] for row in rows)
    camera_counts = Counter(str(row["camera_id"]) for row in rows)
    consensus = count_truth(rows, lambda row: str(row.get("consensus_block_candidate")).lower() == "true")
    small_bbox_consensus = count_truth(
        rows,
        lambda row: str(row.get("small_bbox_consensus_candidate")).lower() == "true",
    )
    border_consensus = count_truth(
        rows,
        lambda row: str(row.get("border_consensus_candidate")).lower() == "true",
    )
    ia2_without_ia3 = count_truth(
        rows,
        lambda row: str(row.get("ia2_strong_not_person_without_ia3")).lower() == "true",
    )
    layered_block = count_truth(rows, lambda row: row.get("layered_decision") == "block_candidate")
    layered_suppress = count_truth(rows, lambda row: row.get("layered_decision") == "suppress_candidate")
    green_suppress = count_truth(
        rows,
        lambda row: row.get("layered_decision") == "suppress_candidate" and row.get("region_risk") == "GREEN",
    )
    ia2_raw_not = count_truth(rows, lambda row: row.get("ia2_raw") == "NAO_PARECE_PESSOA")
    ia3_raw_not = count_truth(rows, lambda row: row.get("ia3_raw") == "NAO_PARECE_PESSOA")
    strict_visual = count_truth(
        rows,
        lambda row: (
            (as_float(row.get("ia2_person_score")) is not None and as_float(row.get("ia2_person_score")) <= 0.01)
            and (as_float(row.get("ia2_not_person_score")) is not None and as_float(row.get("ia2_not_person_score")) >= 0.99)
            and (as_float(row.get("ia3_person_far_score")) is not None and as_float(row.get("ia3_person_far_score")) <= 0.005)
            and (as_float(row.get("ia3_not_person_far_score")) is not None and as_float(row.get("ia3_not_person_far_score")) >= 0.995)
            and str(row.get("near_border")).lower() != "true"
        ),
    )
    by_camera: dict[str, dict[str, Any]] = {}
    for camera_id in sorted(camera_counts.keys(), key=lambda value: int(value) if value.isdigit() else value):
        camera_rows = [row for row in rows if str(row["camera_id"]) == camera_id]
        by_camera[camera_id] = {
            "total": len(camera_rows),
            "truth_counts": dict(Counter(row["truth_class"] for row in camera_rows)),
            "consensus_block_candidate": count_truth(
                camera_rows,
                lambda row: str(row.get("consensus_block_candidate")).lower() == "true",
            ),
            "small_bbox_consensus_candidate": count_truth(
                camera_rows,
                lambda row: str(row.get("small_bbox_consensus_candidate")).lower() == "true",
            ),
            "border_consensus_candidate": count_truth(
                camera_rows,
                lambda row: str(row.get("border_consensus_candidate")).lower() == "true",
            ),
            "ia2_strong_not_person_without_ia3": count_truth(
                camera_rows,
                lambda row: str(row.get("ia2_strong_not_person_without_ia3")).lower() == "true",
            ),
            "suppress_candidate": count_truth(camera_rows, lambda row: row.get("layered_decision") == "suppress_candidate"),
        }

    unsafe_consensus = consensus["person"]
    unsafe_suppress = layered_suppress["person"]
    recommendation = "KEEP_AUDIT"
    if comparable and consensus["total"] > 0 and unsafe_consensus == 0:
        recommendation = "BLOCK_CAN_BE_PILOTED_FOR_CONSENSUS_ONLY"
    if unsafe_consensus > 0:
        recommendation = "DO_NOT_ENABLE_BLOCK"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_limit": requested_limit,
        "rows": len(rows),
        "comparable_rows": len(comparable),
        "truth_counts": dict(truth_counts),
        "camera_counts": dict(camera_counts),
        "ia2_raw_not_person": ia2_raw_not,
        "ia3_raw_not_person": ia3_raw_not,
        "strict_visual_not_person": strict_visual,
        "consensus_block_candidate": consensus,
        "small_bbox_consensus_candidate": small_bbox_consensus,
        "border_consensus_candidate": border_consensus,
        "ia2_strong_not_person_without_ia3": ia2_without_ia3,
        "layered_block_candidate": layered_block,
        "layered_suppress_candidate": layered_suppress,
        "green_suppress_candidate": green_suppress,
        "by_camera": by_camera,
        "safety": {
            "consensus_person_would_be_blocked": unsafe_consensus,
            "suppress_person_would_be_affected": unsafe_suppress,
            "recommendation": recommendation,
        },
    }


def build_markdown(summary: dict[str, Any]) -> str:
    consensus = summary["consensus_block_candidate"]
    small_bbox = summary["small_bbox_consensus_candidate"]
    border = summary["border_consensus_candidate"]
    ia2_without_ia3 = summary["ia2_strong_not_person_without_ia3"]
    suppress = summary["layered_suppress_candidate"]
    strict = summary["strict_visual_not_person"]
    safety = summary["safety"]
    lines = [
        "# Revalidator Rule Analysis",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Rows: {summary['rows']} / requested {summary['requested_limit']}",
        f"- Truth counts: `{json.dumps(summary['truth_counts'], ensure_ascii=False)}`",
        "",
        "## Main Decision",
        "",
        f"- Recommendation: **{safety['recommendation']}**",
        f"- Consensus person that would be blocked: **{safety['consensus_person_would_be_blocked']}**",
        f"- Suppress person that would be affected: **{safety['suppress_person_would_be_affected']}**",
        "",
        "## Candidate Counts",
        "",
        f"- Strict visual not-person: total={strict['total']} person={strict['person']} not_person={strict['not_person']}",
        f"- Consensus block candidate: total={consensus['total']} person={consensus['person']} not_person={consensus['not_person']}",
        f"- Small bbox consensus candidate: total={small_bbox['total']} person={small_bbox['person']} not_person={small_bbox['not_person']}",
        f"- Border consensus candidate: total={border['total']} person={border['person']} not_person={border['not_person']}",
        f"- IA2 strong not-person without IA3: total={ia2_without_ia3['total']} person={ia2_without_ia3['person']} not_person={ia2_without_ia3['not_person']}",
        f"- Layered suppress candidate: total={suppress['total']} person={suppress['person']} not_person={suppress['not_person']}",
        "",
        "## By Camera",
        "",
        "| Camera | Total | Person | Not person | Consensus total/person/not_person | Small bbox total/person/not_person | Border total/person/not_person | IA2 no IA3 total/person/not_person | Suppress total/person/not_person |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for camera_id, item in summary["by_camera"].items():
        truths = item["truth_counts"]
        c = item["consensus_block_candidate"]
        small = item["small_bbox_consensus_candidate"]
        border_item = item["border_consensus_candidate"]
        no_ia3 = item["ia2_strong_not_person_without_ia3"]
        s = item["suppress_candidate"]
        lines.append(
            f"| {camera_id} | {item['total']} | {truths.get('person', 0)} | {truths.get('not_person', 0)} | "
            f"{c['total']}/{c['person']}/{c['not_person']} | "
            f"{small['total']}/{small['person']}/{small['not_person']} | "
            f"{border_item['total']}/{border_item['person']}/{border_item['not_person']} | "
            f"{no_ia3['total']}/{no_ia3['person']}/{no_ia3['not_person']} | "
            f"{s['total']}/{s['person']}/{s['not_person']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    database_url = sqlite_url_from_args(args.database)
    output_root = resolve_output_root(args.output_dir)
    report_root = resolve_output_root(args.report_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)

    connect_args = {}
    engine_kwargs = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30.0}
        engine_kwargs["poolclass"] = NullPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    rows: list[dict[str, Any]] = []
    counters = Counter()
    with Session() as session:
        selected = latest_feedback_rows(session, limit=args.limit, camera_id=args.camera_id)
        history_rows = region_history_rows(session, camera_id=args.camera_id)
        counters["selected"] = len(selected)
        for feedback, event, camera in selected:
            try:
                payload = build_review_audit_payload(event, feedback, history_rows=history_rows)
                json_path = expected_output_path(output_root, payload)
                if args.overwrite or not json_path.exists():
                    json_path = write_review_audit_payload_json(payload, output_root=output_root)
                    counters["written"] += 1
                else:
                    counters["already_exists"] += 1
            except Exception as exc:
                counters["errors"] += 1
                print(f"ERRO event={getattr(event, 'id', '?')} feedback={getattr(feedback, 'id', '?')}: {exc}")
                continue
            rows.append(flatten_payload(payload, camera=camera, json_path=json_path))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_csv = report_root / f"revalidator_rule_analysis_latest_{args.limit}_{timestamp}.csv"
    consensus_csv = report_root / f"revalidator_rule_consensus_candidates_{args.limit}_{timestamp}.csv"
    small_bbox_csv = report_root / f"revalidator_rule_small_bbox_consensus_candidates_{args.limit}_{timestamp}.csv"
    border_csv = report_root / f"revalidator_rule_border_consensus_candidates_{args.limit}_{timestamp}.csv"
    ia2_without_ia3_csv = report_root / f"revalidator_rule_ia2_strong_without_ia3_{args.limit}_{timestamp}.csv"
    suppress_csv = report_root / f"revalidator_rule_suppress_candidates_{args.limit}_{timestamp}.csv"
    summary_json = report_root / f"revalidator_rule_summary_{args.limit}_{timestamp}.json"
    report_md = report_root / f"revalidator_rule_report_{args.limit}_{timestamp}.md"

    write_csv(all_csv, rows)
    write_csv(consensus_csv, [row for row in rows if str(row.get("consensus_block_candidate")).lower() == "true"])
    write_csv(small_bbox_csv, [row for row in rows if str(row.get("small_bbox_consensus_candidate")).lower() == "true"])
    write_csv(border_csv, [row for row in rows if str(row.get("border_consensus_candidate")).lower() == "true"])
    write_csv(ia2_without_ia3_csv, [row for row in rows if str(row.get("ia2_strong_not_person_without_ia3")).lower() == "true"])
    write_csv(suppress_csv, [row for row in rows if row.get("layered_decision") == "suppress_candidate"])
    summary = build_summary(rows, requested_limit=args.limit)
    summary["run_counts"] = dict(counters)
    summary["outputs"] = {
        "json_dir": str(output_root),
        "all_csv": str(all_csv),
        "consensus_csv": str(consensus_csv),
        "small_bbox_csv": str(small_bbox_csv),
        "border_csv": str(border_csv),
        "ia2_without_ia3_csv": str(ia2_without_ia3_csv),
        "suppress_csv": str(suppress_csv),
        "summary_json": str(summary_json),
        "report_md": str(report_md),
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(build_markdown(summary), encoding="utf-8")

    print(f"Selecionados: {counters['selected']}")
    print(f"JSONs gravados: {counters['written']}")
    print(f"Ja existiam: {counters['already_exists']}")
    print(f"Erros: {counters['errors']}")
    print(f"Consensus candidates: {summary['consensus_block_candidate']}")
    print(f"Small bbox consensus candidates: {summary['small_bbox_consensus_candidate']}")
    print(f"Border consensus candidates: {summary['border_consensus_candidate']}")
    print(f"IA2 strong not-person without IA3: {summary['ia2_strong_not_person_without_ia3']}")
    print(f"Suppress candidates: {summary['layered_suppress_candidate']}")
    print(f"Recomendacao: {summary['safety']['recommendation']}")
    print(f"CSV: {all_csv}")
    print(f"Consensus CSV: {consensus_csv}")
    print(f"Small bbox CSV: {small_bbox_csv}")
    print(f"Border CSV: {border_csv}")
    print(f"IA2 without IA3 CSV: {ia2_without_ia3_csv}")
    print(f"Suppress CSV: {suppress_csv}")
    print(f"Resumo: {summary_json}")
    print(f"Relatorio: {report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
