r"""Gera JSON IA1/IA2/IA3 para feedbacks ja avaliados.

Exemplos:

    py -3 -B scripts\backfill_review_revalidator_audit.py --limit 1000

No Linux/Docker:

    python -B scripts/backfill_review_revalidator_audit.py --limit 1000
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera auditoria IA1/IA2/IA3 para eventos ja avaliados.")
    parser.add_argument("--limit", type=int, default=1000, help="Quantidade maxima de eventos avaliados.")
    parser.add_argument("--camera-id", type=int, help="Filtra por camera.")
    parser.add_argument("--label", help="Filtra por label do operador.")
    parser.add_argument("--database", help="Caminho do analytics.db. Padrao: settings.database_url.")
    parser.add_argument("--output-dir", default=settings.revalidator_review_audit_dir, help="Diretorio de saida dos JSONs.")
    parser.add_argument("--overwrite", action="store_true", help="Regrava JSONs que ja existem.")
    parser.add_argument("--summary-file", help="Caminho do resumo JSON. Padrao: output-dir/backfill_summary_<timestamp>.json")
    return parser.parse_args()


def sqlite_url_from_args(database: str | None) -> str:
    if database:
        return sqlite_url_for(Path(database))
    if settings.database_url.startswith("sqlite:///"):
        return settings.database_url
    raise SystemExit("Use --database para informar o analytics.db SQLite.")


def output_root_from_arg(output_dir: str) -> Path:
    root = Path(output_dir)
    if not root.is_absolute():
        root = Path(settings.app_base_dir) / root
    return root


def latest_feedback_rows(session, *, limit: int, camera_id: int | None, label: str | None):
    query = (
        session.query(EventFeedback, Event, Camera)
        .join(Event, Event.id == EventFeedback.event_id)
        .outerjoin(Camera, Camera.id == Event.camera_id)
        .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
    )
    if camera_id is not None:
        query = query.filter(EventFeedback.camera_id == camera_id)
    if label:
        query = query.filter(EventFeedback.label == label)

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


def expected_output_path(output_root: Path, event: Event, feedback: EventFeedback) -> Path:
    return output_root / f"camera_{event.camera_id}" / f"event_{event.id}_feedback_{feedback.id}.json"


def main() -> int:
    args = parse_args()
    database_url = sqlite_url_from_args(args.database)
    output_root = output_root_from_arg(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    connect_args = {}
    engine_kwargs = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30.0}
        engine_kwargs["poolclass"] = NullPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    counters = Counter()
    label_counts = Counter()
    camera_counts = Counter()
    ia2_raw_counts = Counter()
    ia3_raw_counts = Counter()
    consensus_counts = Counter()
    maturity_counts = Counter()
    alarm_decision_counts = Counter()
    layered_counts = Counter()
    region_counts = Counter()
    skipped_reasons = Counter()
    written_paths: list[str] = []

    with Session() as session:
        rows = latest_feedback_rows(session, limit=args.limit, camera_id=args.camera_id, label=args.label)
        history_rows = region_history_rows(session, camera_id=args.camera_id)
        counters["selected"] = len(rows)
        for feedback, event, _camera in rows:
            output_path = expected_output_path(output_root, event, feedback)
            if output_path.exists() and not args.overwrite:
                counters["already_exists"] += 1
                continue
            try:
                payload = build_review_audit_payload(event, feedback, history_rows=history_rows)
                write_review_audit_payload_json(payload, output_root=output_root)
            except Exception as exc:
                counters["errors"] += 1
                skipped_reasons[f"exception:{exc.__class__.__name__}"] += 1
                continue

            counters["written"] += 1
            label_counts[str(feedback.label or "unknown")] += 1
            camera_counts[str(event.camera_id)] += 1
            skipped_reasons[str(payload.get("reason") or "unknown")] += 1
            ia2_raw_counts[str((payload.get("ia2") or {}).get("raw_model_interpretation") or "unknown")] += 1
            ia3_raw_counts[str((payload.get("ia3") or {}).get("raw_model_interpretation") or "unknown")] += 1
            consensus = payload.get("consensus_revalidator") or {}
            consensus_counts["block_candidate" if consensus.get("block_candidate") else "not_candidate"] += 1
            maturity = payload.get("event_maturity") or {}
            maturity_counts[str(maturity.get("decision") or "unknown")] += 1
            alarm_decision = payload.get("alarm_decision") or {}
            alarm_decision_counts[str(alarm_decision.get("action") or "unknown")] += 1
            layered = payload.get("layered_decision") or {}
            layered_counts[str(layered.get("decision") or "unknown")] += 1
            region = payload.get("region_memory") or {}
            region_counts[str(region.get("risk_level") or "UNKNOWN")] += 1
            written_paths.append(str(output_path))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = Path(args.summary_file) if args.summary_file else output_root / f"backfill_summary_{timestamp}.json"
    if not summary_path.is_absolute():
        summary_path = Path(settings.app_base_dir) / summary_path
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_url": database_url,
        "output_root": str(output_root),
        "requested_limit": args.limit,
        "camera_id": args.camera_id,
        "label": args.label,
        "overwrite": bool(args.overwrite),
        "counts": dict(counters),
        "label_counts": dict(label_counts),
        "camera_counts": dict(camera_counts),
        "reason_counts": dict(skipped_reasons),
        "ia2_raw_counts": dict(ia2_raw_counts),
        "ia3_raw_counts": dict(ia3_raw_counts),
        "consensus_counts": dict(consensus_counts),
        "maturity_counts": dict(maturity_counts),
        "alarm_decision_counts": dict(alarm_decision_counts),
        "layered_counts": dict(layered_counts),
        "region_risk_counts": dict(region_counts),
        "written_paths_sample": written_paths[:20],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Selecionados: {counters['selected']}")
    print(f"Gravados: {counters['written']}")
    print(f"Ja existiam: {counters['already_exists']}")
    print(f"Erros: {counters['errors']}")
    print(f"Consensus block candidates: {consensus_counts['block_candidate']}")
    print(f"Maturity suppress candidates: {maturity_counts['suppress_candidate_audit']}")
    print(f"Alarm decision LOW_CONFIDENCE: {alarm_decision_counts['LOW_CONFIDENCE_EVENT'] + alarm_decision_counts['LOW_CONFIDENCE_ALARM']}")
    print(f"Alarm decision ALARM: {alarm_decision_counts['ALARM']}")
    print(f"Suppress candidates: {layered_counts['suppress_candidate']}")
    print(f"Saida: {output_root}")
    print(f"Resumo: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
