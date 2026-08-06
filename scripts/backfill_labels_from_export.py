r"""Leva os rotulos gravados por review_event_clips*.py (nos JSONs exportados
em D:\IA_Rebuild\Analitico VMS Clips) para a tabela `event_feedback` do banco
real.

Sem isso, o rotulo fica preso no arquivo local e nunca alimenta o
region_fp_risk / region_memory (app/services/revalidator_region_memory_service.py),
que le EventFeedback direto do banco - ou seja, a supressao automatica de
regiao com falso positivo recorrente nunca aprende com essa revisao.

Roda direto na maquina/ambiente onde o analytics.db (ou Postgres) de producao
esta acessivel (settings.database_url), nao nesta maquina de edicao (o
data/analytics.db local esta vazio).

Exemplos:

    python -B scripts/backfill_labels_from_export.py --dry-run

    python -B scripts/backfill_labels_from_export.py

    python -B scripts/backfill_labels_from_export.py --database D:\Analitico\data\analytics.db
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings, sqlite_url_for  # noqa: E402
from app.db.models import Event, EventFeedback  # noqa: E402

DEFAULT_SOURCE_DIR = Path(r"D:\IA_Rebuild\Analitico VMS Clips")
VALID_LABELS = {"true_positive", "false_positive", "inconclusive"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill de feedback.label exportado para a tabela event_feedback.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--database", help="Caminho do analytics.db SQLite. Padrao: settings.database_url.")
    parser.add_argument("--reviewer", default=None, help="Sobrescreve reviewed_by. Padrao: usa o que estiver no JSON.")
    parser.add_argument("--overwrite-existing", action="store_true", help="Atualiza feedback ja existente no banco para o mesmo event_id.")
    parser.add_argument("--dry-run", action="store_true", help="Mostra o que seria feito sem gravar no banco.")
    return parser.parse_args()


def database_url_from_args(database: str | None) -> str:
    if database:
        return sqlite_url_for(Path(database))
    return settings.database_url


def parse_reviewed_at(value: str | None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def load_labeled_rows(source_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for event_path in sorted(source_dir.glob("audit_pending_event_*_event.json")):
        match = re.search(r"audit_pending_event_(\d+)_event\.json$", event_path.name)
        if not match:
            continue
        try:
            payload = json.loads(event_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        feedback = payload.get("feedback") if isinstance(payload.get("feedback"), dict) else None
        if not feedback:
            continue
        label = str(feedback.get("label") or "").strip().lower()
        if label not in VALID_LABELS:
            continue
        rows.append(
            {
                "event_id": int(match.group(1)),
                "label": label,
                "operator_note": feedback.get("note") or feedback.get("operator_note"),
                "reviewed_by": feedback.get("reviewer") or feedback.get("reviewed_by"),
                "reviewed_at": feedback.get("reviewed_at"),
            }
        )
    return rows


def main() -> int:
    args = parse_args()
    rows = load_labeled_rows(args.source_dir)
    print(f"Eventos rotulados no export: {len(rows)}")
    if not rows:
        return 0

    database_url = database_url_from_args(args.database)
    print(f"Banco alvo: {database_url}")

    engine = create_engine(database_url, poolclass=NullPool)
    Session = sessionmaker(bind=engine)
    session = Session()

    inserted = 0
    updated = 0
    skipped_no_event = 0
    skipped_existing = 0

    try:
        for row in rows:
            event = session.query(Event).filter(Event.id == row["event_id"]).one_or_none()
            if event is None:
                skipped_no_event += 1
                continue

            existing = session.query(EventFeedback).filter(EventFeedback.event_id == row["event_id"]).one_or_none()
            reviewed_by = args.reviewer or row["reviewed_by"] or "review_event_clips_backfill"
            reviewed_at = parse_reviewed_at(row["reviewed_at"])

            if existing is not None:
                if not args.overwrite_existing:
                    skipped_existing += 1
                    continue
                if not args.dry_run:
                    existing.label = row["label"]
                    existing.operator_note = row["operator_note"]
                    existing.reviewed_by = reviewed_by
                    existing.reviewed_at = reviewed_at
                updated += 1
                continue

            if not args.dry_run:
                session.add(
                    EventFeedback(
                        event_id=row["event_id"],
                        camera_id=event.camera_id,
                        label=row["label"],
                        operator_note=row["operator_note"],
                        reviewed_by=reviewed_by,
                        reviewed_at=reviewed_at,
                    )
                )
            inserted += 1

        if not args.dry_run:
            session.commit()
    finally:
        session.close()

    print(f"inseridos={inserted} atualizados={updated} sem_evento_no_banco={skipped_no_event} ja_tinham_feedback={skipped_existing} dry_run={bool(args.dry_run)}")
    if skipped_existing and not args.overwrite_existing:
        print("Use --overwrite-existing para sobrescrever feedback que ja existe no banco com o rotulo do export.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
