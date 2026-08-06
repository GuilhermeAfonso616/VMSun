"""Cruza os rotulos humanos (feedback.label) gravados pelo review_event_clips*
com os campos de decisao do pipeline atual, para os eventos exportados em
D:\\IA_Rebuild\\Analitico VMS Clips.

Os scripts replay_reviewed_events_current_rules.py e
validate_strategy3_refined_v2.py esperam formatos de entrada diferentes
(CSV vindo do banco / export com crops separados). Este script le
diretamente os `_event.json` ja rotulados e extrai os campos relevantes do
`event.details` (formato mais estavel entre os eventos) e de
`evidence`/`profile_snapshot`.

Exemplo:

    python -B scripts/analyze_labeled_intrusion_events.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SOURCE_DIR = Path(r"D:\IA_Rebuild\Analitico VMS Clips")
DEFAULT_OUTPUT_DIR = Path("reports/labeled_intrusion_analysis")

DETAIL_FIELDS = [
    "revalidator_skipped",
    "far_revalidator_skipped",
    "revalidator_person",
    "far_revalidator_person",
    "maturity_score",
    "maturity_level",
    "maturity_decision",
    "strategy3_v2_decision",
    "strategy3_v2_notify",
    "alarm_decision_action",
    "alarm_decision_reason",
    "alarm_decision_mode",
    "final_notification_level",
    "final_status",
    "final_is_alarm_active",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analisa eventos rotulados vs decisao atual do pipeline.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def extract_detail_fields(details: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in DETAIL_FIELDS:
        match = re.search(rf"\b{re.escape(key)}=(\S+)", details or "")
        out[key] = match.group(1) if match else ""
    return out


def load_row(event_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    feedback = payload.get("feedback") if isinstance(payload.get("feedback"), dict) else None
    if not feedback or not feedback.get("label"):
        return None

    event = payload.get("event") or {}
    evidence = payload.get("evidence") or {}
    profile_snapshot = payload.get("profile_snapshot") or {}
    nuisance = profile_snapshot.get("nuisance_profile") if isinstance(profile_snapshot.get("nuisance_profile"), dict) else {}
    active_nuisance = ",".join(sorted(k for k, v in nuisance.items() if v)) if nuisance else ""

    row: dict[str, Any] = {
        "event_id": event.get("id"),
        "camera_id": event.get("camera_id"),
        "label": feedback.get("label"),
        "status": event.get("status"),
        "severity": event.get("severity"),
        "is_alarm_active": event.get("is_alarm_active"),
        "confidence": event.get("confidence"),
        "event_score": event.get("event_score"),
        "detector_score": event.get("detector_score"),
        "scene_profile": evidence.get("scene_profile") or profile_snapshot.get("scene_profile"),
        "camera_family": evidence.get("camera_family") or profile_snapshot.get("camera_family"),
        "active_nuisance": active_nuisance,
    }
    row.update(extract_detail_fields(event.get("details") or ""))
    return row


def load_rows(source_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_path in sorted(source_dir.glob("audit_pending_event_*_event.json")):
        row = load_row(event_path)
        if row is not None:
            rows.append(row)
    return rows


def pct(part: int, total: int) -> str:
    return f"{(100.0 * part / total):.1f}%" if total else "0.0%"


def print_breakdown(title: str, rows: list[dict[str, Any]], key: str, top_n: int = 15) -> None:
    print(f"\n{title}")
    counter: Counter[tuple[str, str]] = Counter((str(r.get(key) or "-"), r["label"]) for r in rows)
    totals: Counter[str] = Counter(str(r.get(key) or "-") for r in rows)
    for group, total in totals.most_common(top_n):
        fp = counter[(group, "false_positive")]
        tp = counter[(group, "true_positive")]
        unc = counter[(group, "inconclusive")]
        print(f"  {group}: total={total} fp={fp} ({pct(fp, total)}) tp={tp} inconclusivo={unc}")


def main() -> int:
    args = parse_args()
    rows = load_rows(args.source_dir)
    print(f"Eventos rotulados analisados: {len(rows)}")
    if not rows:
        return 0

    label_counts = Counter(r["label"] for r in rows)
    for label, count in label_counts.most_common():
        print(f"  {label}: {count} ({pct(count, len(rows))})")

    # Falsos positivos que ainda viraram alarme de verdade hoje (o que mais importa corrigir).
    fp_rows = [r for r in rows if r["label"] == "false_positive"]
    tp_rows = [r for r in rows if r["label"] == "true_positive"]

    fp_still_alarming = [r for r in fp_rows if str(r.get("is_alarm_active")).lower() == "true" or r.get("status") == "alarm"]
    tp_suppressed = [r for r in tp_rows if r.get("status") in {"suppressed", "closed", "low_priority"}]

    print(f"\nFalsos positivos que ainda geraram alarme hoje: {len(fp_still_alarming)} de {len(fp_rows)} ({pct(len(fp_still_alarming), len(fp_rows))})")
    print(f"Verdadeiros positivos suprimidos/perdidos hoje: {len(tp_suppressed)} de {len(tp_rows)} ({pct(len(tp_suppressed), len(tp_rows))})")

    revalidator_skipped_fp = sum(1 for r in fp_rows if r.get("revalidator_skipped"))
    print(f"\nFalsos positivos onde o revalidador (IA2) foi pulado por erro (revalidator_skipped): {revalidator_skipped_fp} de {len(fp_rows)} ({pct(revalidator_skipped_fp, len(fp_rows))})")

    print_breakdown("Taxa de FP por camera_id", rows, "camera_id")
    print_breakdown("Taxa de FP por status atual", rows, "status")
    print_breakdown("Taxa de FP por maturity_decision", rows, "maturity_decision")
    print_breakdown("Taxa de FP por strategy3_v2_decision", rows, "strategy3_v2_decision")
    print_breakdown("Taxa de FP por scene_profile", rows, "scene_profile")
    print_breakdown("Taxa de FP por nuisance ativo", rows, "active_nuisance")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"labeled_intrusion_analysis_{stamp}.csv"
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nCSV completo: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
