from __future__ import annotations

import argparse

from app.db.base import SessionLocal
from app.db.models import Event, EventFeedback
from app.services.revalidator_dataset_collector import collect_person_revalidator_sample


PERSON_LABELS = {"true_positive": "operator_confirmed", "expected_event": "reviewed_event"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta crops person a partir de eventos ja avaliados.")
    parser.add_argument("--limit", type=int, default=500, help="Quantidade maxima de feedbacks avaliados a processar.")
    parser.add_argument("--camera-id", type=int, default=None, help="Filtra por camera.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas conta candidatos sem gravar arquivos.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = (
            db.query(EventFeedback, Event)
            .join(Event, Event.id == EventFeedback.event_id)
            .filter(EventFeedback.label.in_(sorted(PERSON_LABELS)))
            .order_by(EventFeedback.reviewed_at.desc())
        )
        if args.camera_id is not None:
            query = query.filter(EventFeedback.camera_id == args.camera_id)

        rows = query.limit(max(1, int(args.limit))).all()
        exported = 0
        candidates = 0
        for feedback, event in rows:
            candidates += 1
            if args.dry_run:
                continue
            result = collect_person_revalidator_sample(
                event=event,
                feedback=feedback,
                decision_source=PERSON_LABELS.get(str(feedback.label), "reviewed_event"),
                operator_note=feedback.operator_note,
            )
            if str(result.get("status") or "").startswith("crop"):
                exported += 1

        print(f"candidates={candidates} exported_crops={exported} dry_run={bool(args.dry_run)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

