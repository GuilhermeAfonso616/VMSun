"""Interactive reviewer for exported event clips.

The script opens each clip in the default video player and records the review
inside the matching ``audit_pending_event_<id>_event.json`` file. The replay
manifest builder already reads ``feedback.label``, so reviewed clips become
usable by the quality-impact and replay tools immediately.

Examples:

    python -B scripts/review_event_clips.py

    python -B scripts/review_event_clips.py --start-id 579

    python -B scripts/review_event_clips.py --include-reviewed --limit 20
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_DIR = Path(r"D:\IA_Rebuild\Analitico VMS Clips")
DEFAULT_REPORT_DIR = Path("reports/clip_review")
REVIEWED_LABELS = {"true_positive", "false_positive", "inconclusive"}


@dataclass
class ReviewItem:
    event_id: int
    event_path: Path
    clip_path: Path
    snapshot_path: Path
    label: str
    status: str
    severity: str
    camera_id: str
    detector_score: str
    event_score: str
    created_at: str


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Falha ao ler JSON {path}: {exc}") from exc


def event_id_from_path(path: Path) -> int | None:
    match = re.search(r"audit_pending_event_(\d+)_event\.json$", path.name)
    if not match:
        return None
    return int(match.group(1))


def current_label(payload: dict[str, Any]) -> str:
    feedback = payload.get("feedback") if isinstance(payload.get("feedback"), dict) else {}
    return str(feedback.get("label") or "unreviewed").strip().lower() or "unreviewed"


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def build_item(event_path: Path) -> ReviewItem | None:
    event_id = event_id_from_path(event_path)
    if event_id is None:
        return None
    payload = load_json(event_path)
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    base_dir = event_path.parent
    clip_path = base_dir / f"audit_pending_event_{event_id}_clip.mp4"
    snapshot_path = base_dir / f"audit_pending_event_{event_id}_snapshot.jpg"
    if not clip_path.exists():
        return None
    return ReviewItem(
        event_id=event_id,
        event_path=event_path,
        clip_path=clip_path,
        snapshot_path=snapshot_path,
        label=current_label(payload),
        status=safe_str(event.get("status")),
        severity=safe_str(event.get("severity")),
        camera_id=safe_str(event.get("camera_id")),
        detector_score=safe_str(event.get("detector_score")),
        event_score=safe_str(event.get("event_score")),
        created_at=safe_str(event.get("created_at") or event.get("started_at")),
    )


def discover_items(args: argparse.Namespace) -> list[ReviewItem]:
    source_dir = Path(args.source_dir)
    items: list[ReviewItem] = []
    for event_path in sorted(source_dir.glob("audit_pending_event_*_event.json"), key=lambda p: event_id_from_path(p) or -1):
        item = build_item(event_path)
        if item is None:
            continue
        if args.start_id is not None and item.event_id < args.start_id:
            continue
        if args.end_id is not None and item.event_id > args.end_id:
            continue
        if args.camera and item.camera_id not in set(str(value) for value in args.camera):
            continue
        if not args.include_reviewed and item.label in REVIEWED_LABELS:
            continue
        if args.label and item.label not in set(args.label):
            continue
        items.append(item)

    if args.reverse:
        items.reverse()
    if args.limit and args.limit > 0:
        items = items[: args.limit]
    return items


def open_file(path: Path) -> None:
    if not path.exists():
        print(f"[aviso] arquivo nao encontrado: {path}")
        return
    system = platform.system().lower()
    try:
        if system == "windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif system == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        print(f"[aviso] nao consegui abrir {path}: {exc}")


def ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "reviewed_at",
                "event_id",
                "label",
                "previous_label",
                "reviewer",
                "note",
                "event_json",
                "clip_path",
            ],
        )
        writer.writeheader()


def append_csv(path: Path, row: dict[str, Any]) -> None:
    ensure_csv(path)
    with path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writerow(row)


def backup_json(event_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / event_path.name
    if not dst.exists():
        shutil.copy2(event_path, dst)
    return dst


def save_review(
    item: ReviewItem,
    *,
    label: str,
    reviewer: str,
    note: str,
    report_csv: Path,
    backup_dir: Path,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = load_json(item.event_path)
    previous_label = current_label(payload)
    backup_json(item.event_path, backup_dir)

    feedback = payload.get("feedback") if isinstance(payload.get("feedback"), dict) else {}
    feedback.update(
        {
            "label": label,
            "reviewed_at": now,
            "reviewer": reviewer,
            "note": note,
            "source": "review_event_clips.py",
        }
    )
    payload["feedback"] = feedback
    item.event_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    item.label = label

    append_csv(
        report_csv,
        {
            "reviewed_at": now,
            "event_id": item.event_id,
            "label": label,
            "previous_label": previous_label,
            "reviewer": reviewer,
            "note": note,
            "event_json": str(item.event_path),
            "clip_path": str(item.clip_path),
        },
    )


def decision_to_label(decision: str) -> str | None:
    mapping = {
        "v": "true_positive",
        "t": "true_positive",
        "1": "true_positive",
        "f": "false_positive",
        "0": "false_positive",
        "i": "inconclusive",
        "u": "inconclusive",
        "2": "inconclusive",
    }
    return mapping.get(decision.strip().lower())


def print_item(item: ReviewItem, index: int, total: int) -> None:
    print("")
    print("=" * 88)
    print(f"[{index + 1}/{total}] Evento {item.event_id} | atual={item.label}")
    print(
        "camera={camera} status={status} severidade={severity} "
        "detector={detector} evento={event_score}".format(
            camera=item.camera_id or "-",
            status=item.status or "-",
            severity=item.severity or "-",
            detector=item.detector_score or "-",
            event_score=item.event_score or "-",
        )
    )
    print(f"data={item.created_at or '-'}")
    print(f"clip={item.clip_path}")
    if item.snapshot_path.exists():
        print(f"snapshot={item.snapshot_path}")
    print("")
    print("Comandos: [v] verdadeiro | [f] falso | [i] inconclusivo | [s] pular | [o] abrir de novo | [c] abrir video | [p] abrir snapshot | [b] voltar | [q] sair")


def interactive_review(args: argparse.Namespace, items: list[ReviewItem]) -> None:
    if not items:
        print("Nenhum evento para revisar.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(args.report_dir)
    report_csv = report_dir / f"event_clip_review_{stamp}.csv"
    backup_dir = report_dir / f"json_backup_{stamp}"

    auto_open_path = (lambda item: item.snapshot_path) if args.open_target == "snapshot" else (lambda item: item.clip_path)

    index = 0
    total = len(items)
    while 0 <= index < total:
        item = items[index]
        print_item(item, index, total)
        if args.open_clip:
            open_file(auto_open_path(item))

        raw = input("> ").strip()
        if not raw:
            raw = "s"
        command = raw.lower()
        if command == "q":
            break
        if command == "b":
            index = max(0, index - 1)
            continue
        if command == "o":
            open_file(auto_open_path(item))
            continue
        if command == "c":
            open_file(item.clip_path)
            continue
        if command == "p":
            open_file(item.snapshot_path)
            continue
        if command == "s":
            index += 1
            continue

        label = decision_to_label(command)
        if label is None:
            print("Comando invalido.")
            continue

        note = ""
        if args.ask_note:
            note = input("Observacao opcional: ").strip()
        save_review(
            item,
            label=label,
            reviewer=args.reviewer,
            note=note,
            report_csv=report_csv,
            backup_dir=backup_dir,
        )
        print(f"[ok] Evento {item.event_id} marcado como {label}")
        index += 1

    print("")
    print(f"Revisao salva em: {report_csv}")
    print(f"Backups JSON em: {backup_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Revisao interativa dos clips de eventos exportados.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--reviewer", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operador")
    parser.add_argument("--start-id", type=int, default=None)
    parser.add_argument("--end-id", type=int, default=None)
    parser.add_argument("--camera", action="append", help="Filtrar camera_id. Pode repetir.")
    parser.add_argument("--label", action="append", help="Filtrar label atual: unreviewed, false_positive, true_positive, inconclusive.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-reviewed", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--no-open", dest="open_clip", action="store_false", help="Nao abre nada automaticamente.")
    parser.add_argument(
        "--open-target",
        choices=["clip", "snapshot"],
        default="snapshot",
        help="O que abrir automaticamente a cada evento (padrao: snapshot, mais rapido que o video).",
    )
    parser.add_argument("--ask-note", action="store_true", help="Pergunta observacao a cada decisao.")
    parser.add_argument("--dry-run", action="store_true", help="Mostra a fila e sai sem revisar.")
    parser.set_defaults(open_clip=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    items = discover_items(args)
    print(f"Eventos na fila: {len(items)}")
    if args.dry_run:
        for item in items:
            print(
                f"{item.event_id}\tlabel={item.label}\tcamera={item.camera_id or '-'}\t"
                f"score={item.detector_score or '-'}\tclip={item.clip_path.name}"
            )
        return 0
    interactive_review(args, items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
