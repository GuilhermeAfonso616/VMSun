"""Interface grafica para revisar os eventos exportados em D:\\IA_Rebuild\\Analitico VMS Clips.

Mostra a snapshot de cada evento (rapido, sem abrir player de video) com
botoes e atalhos de teclado para marcar o rotulo. Reusa a mesma logica de
descoberta/gravacao do `review_event_clips.py`, entao o resultado e
compativel com os outros scripts (replay, validate_*, etc.).

Exemplos:

    python -B scripts/review_event_clips_gui.py

    python -B scripts/review_event_clips_gui.py --start-id 579

    python -B scripts/review_event_clips_gui.py --camera 11 --limit 50
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, BOTTOM, LEFT, RIGHT, TOP, X, Button, Entry, Frame, Label, StringVar, Tk

from PIL import Image, ImageOps, ImageTk

from review_event_clips import (
    DEFAULT_REPORT_DIR,
    DEFAULT_SOURCE_DIR,
    ReviewItem,
    discover_items,
    open_file,
    save_review,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Revisao grafica (snapshot) dos clips de eventos exportados.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--reviewer", default=None)
    parser.add_argument("--start-id", type=int, default=None)
    parser.add_argument("--end-id", type=int, default=None)
    parser.add_argument("--camera", action="append", help="Filtrar camera_id. Pode repetir.")
    parser.add_argument("--label", action="append", help="Filtrar label atual: unreviewed, false_positive, true_positive, inconclusive.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-reviewed", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    return parser.parse_args()


def resolve_reviewer(value: str | None) -> str:
    import os

    return value or os.environ.get("USERNAME") or os.environ.get("USER") or "operador"


class ReviewApp:
    def __init__(self, items: list[ReviewItem], *, reviewer: str, report_csv: Path, backup_dir: Path) -> None:
        self.items = items
        self.reviewer = reviewer
        self.report_csv = report_csv
        self.backup_dir = backup_dir
        self.index = 0
        self.tk_image: ImageTk.PhotoImage | None = None

        self.root = Tk()
        self.root.title("Revisao de eventos - Analitico VMS")
        self.root.geometry("1180x860")

        self.status = StringVar()
        self.meta = StringVar()
        self.note_var = StringVar()

        self.image_label = Label(self.root, bg="#202020")
        self.image_label.pack(side=TOP, fill=BOTH, expand=True)
        Label(self.root, textvariable=self.status, anchor="w", font=("Segoe UI", 11, "bold")).pack(side=TOP, fill=X, padx=8)
        Label(self.root, textvariable=self.meta, anchor="w", font=("Segoe UI", 9)).pack(side=TOP, fill=X, padx=8, pady=(0, 4))

        note_frame = Frame(self.root)
        note_frame.pack(side=TOP, fill=X, padx=8, pady=(0, 4))
        Label(note_frame, text="Observacao:").pack(side=LEFT)
        Entry(note_frame, textvariable=self.note_var).pack(side=LEFT, fill=X, expand=True, padx=(6, 0))

        buttons = Frame(self.root)
        buttons.pack(side=BOTTOM, fill=X, pady=6)
        Button(buttons, text="Verdadeiro (V)", bg="#1f7a3f", fg="white", command=lambda: self.save("true_positive")).pack(side=LEFT, padx=4)
        Button(buttons, text="Falso (F)", bg="#a12f2f", fg="white", command=lambda: self.save("false_positive")).pack(side=LEFT, padx=4)
        Button(buttons, text="Inconclusivo (I)", command=lambda: self.save("inconclusive")).pack(side=LEFT, padx=4)
        Button(buttons, text="Pular (S)", command=self.next_item).pack(side=LEFT, padx=4)
        Button(buttons, text="Voltar (B)", command=self.previous_item).pack(side=LEFT, padx=4)
        Button(buttons, text="Abrir video (C)", command=self.open_clip).pack(side=LEFT, padx=4)
        Button(buttons, text="Sair (Q)", command=self.root.destroy).pack(side=RIGHT, padx=4)

        for key in ("v", "V"):
            self.root.bind(key, lambda _event: self.save("true_positive"))
        for key in ("f", "F"):
            self.root.bind(key, lambda _event: self.save("false_positive"))
        for key in ("i", "I", "u", "U"):
            self.root.bind(key, lambda _event: self.save("inconclusive"))
        for key in ("s", "S"):
            self.root.bind(key, lambda _event: self.next_item())
        for key in ("b", "B"):
            self.root.bind(key, lambda _event: self.previous_item())
        for key in ("c", "C"):
            self.root.bind(key, lambda _event: self.open_clip())
        for key in ("q", "Q"):
            self.root.bind(key, lambda _event: self.root.destroy())
        self.root.bind("<Left>", lambda _event: self.previous_item())
        self.root.bind("<Right>", lambda _event: self.next_item())

        self.show_current()

    def current_item(self) -> ReviewItem | None:
        if not self.items or self.index >= len(self.items):
            return None
        return self.items[self.index]

    def show_current(self) -> None:
        item = self.current_item()
        if item is None:
            self.status.set(f"Fim da fila. Revisao salva em: {self.report_csv}")
            self.meta.set("")
            self.image_label.configure(image="", text="Fim da revisao")
            return

        self.note_var.set("")
        try:
            image = Image.open(item.snapshot_path).convert("RGB")
            image = ImageOps.contain(image, (1140, 680))
            self.tk_image = ImageTk.PhotoImage(image)
            self.image_label.configure(image=self.tk_image, text="")
        except Exception as exc:
            self.tk_image = None
            self.image_label.configure(image="", text=f"Erro ao abrir snapshot:\n{item.snapshot_path}\n{exc}")

        self.status.set(
            f"[{self.index + 1}/{len(self.items)}] Evento {item.event_id} | atual={item.label} | "
            f"camera={item.camera_id or '-'} | status={item.status or '-'} | severidade={item.severity or '-'}"
        )
        self.meta.set(
            f"detector={item.detector_score or '-'} | evento={item.event_score or '-'} | data={item.created_at or '-'} | "
            f"clip={item.clip_path.name}"
        )

    def save(self, label: str) -> None:
        item = self.current_item()
        if item is None:
            return
        save_review(
            item,
            label=label,
            reviewer=self.reviewer,
            note=self.note_var.get().strip(),
            report_csv=self.report_csv,
            backup_dir=self.backup_dir,
        )
        self.next_item()

    def open_clip(self) -> None:
        item = self.current_item()
        if item is not None:
            open_file(item.clip_path)

    def next_item(self) -> None:
        if self.index < len(self.items):
            self.index += 1
        self.show_current()

    def previous_item(self) -> None:
        if self.index > 0:
            self.index -= 1
        self.show_current()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    args = parse_args()
    items = discover_items(args)
    print(f"Eventos na fila: {len(items)}")
    if not items:
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(args.report_dir)
    report_csv = report_dir / f"event_clip_review_gui_{stamp}.csv"
    backup_dir = report_dir / f"json_backup_{stamp}"

    ReviewApp(items, reviewer=resolve_reviewer(args.reviewer), report_csv=report_csv, backup_dir=backup_dir).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
