import argparse
import csv
import shutil
import sys
from pathlib import Path
from tkinter import BOTH, BOTTOM, LEFT, RIGHT, TOP, Button, Frame, Label, StringVar, Tk

from PIL import Image, ImageDraw, ImageOps, ImageTk


DEFAULT_DISCREPANCIES = Path("D:/Analitico/reports/ia1_finetune_vms_hardneg_v1/visual_review_discrepancies.csv")
DEFAULT_OUTPUT = Path("D:/Analitico/reports/ia1_finetune_vms_hardneg_v1/visual_review_discrepancy_decisions.csv")


def parse_args():
    parser = argparse.ArgumentParser(description="Interface para revisar discrepancias do dataset IA1.")
    parser.add_argument("--discrepancies", default=str(DEFAULT_DISCREPANCIES), help="CSV de discrepancias.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="CSV de decisoes finais.")
    parser.add_argument("--include-reviewed", action="store_true", help="Mostra itens ja decididos neste CSV.")
    parser.add_argument(
        "--copy-reviewed",
        default="",
        help="Opcional: copia imagens revisadas para esta pasta, separando por decisao final.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Conta discrepancias e nao abre a interface.")
    return parser.parse_args()


def read_csv(path):
    with Path(path).open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def output_fields():
    return [
        "path",
        "label_path",
        "expected_label",
        "initial_reviewed_as",
        "final_label",
        "final_action",
        "discrepancy_type",
        "split",
        "source",
        "camera_id",
        "event_id",
        "track_id",
        "sha1",
        "note",
    ]


def ensure_output(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields())
        writer.writeheader()


def reviewed_paths(output_path):
    if not output_path.exists():
        return set()
    with output_path.open("r", newline="", encoding="utf-8-sig") as f:
        return {row.get("path", "") for row in csv.DictReader(f)}


def append_decision(output_path, row, final_label, final_action, note=""):
    record = {
        "path": row.get("path", ""),
        "label_path": row.get("label_path", ""),
        "expected_label": row.get("expected_label", ""),
        "initial_reviewed_as": row.get("reviewed_as", ""),
        "final_label": final_label,
        "final_action": final_action,
        "discrepancy_type": row.get("discrepancy_type", ""),
        "split": row.get("split", ""),
        "source": row.get("source", ""),
        "camera_id": row.get("camera_id", ""),
        "event_id": row.get("event_id", ""),
        "track_id": row.get("track_id", ""),
        "sha1": row.get("sha1", ""),
        "note": note,
    }
    ensure_output(output_path)
    with output_path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields())
        writer.writerow(record)


def read_yolo_boxes(label_path, image_width, image_height):
    path = Path(label_path) if label_path else None
    if not path or not path.exists():
        return []
    boxes = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        try:
            cls = parts[0]
            cx, cy, w, h = [float(v) for v in parts[1:5]]
        except ValueError:
            continue
        x1 = (cx - w / 2) * image_width
        y1 = (cy - h / 2) * image_height
        x2 = (cx + w / 2) * image_width
        y2 = (cy + h / 2) * image_height
        boxes.append((cls, x1, y1, x2, y2))
    return boxes


def draw_boxes(image, boxes):
    draw = ImageDraw.Draw(image)
    for cls, x1, y1, x2, y2 in boxes:
        draw.rectangle((x1, y1, x2, y2), outline=(255, 40, 40), width=4)
        draw.text((x1 + 4, max(0, y1 - 18)), f"class {cls}", fill=(255, 40, 40))
    return image


def copy_reviewed(copy_root, row, action):
    if not copy_root:
        return
    src = Path(row.get("path", ""))
    if not src.exists():
        return
    dst_dir = Path(copy_root) / action
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        dst = dst_dir / f"{src.stem}_{row.get('sha1', '')[:8]}{src.suffix}"
    shutil.copy2(src, dst)


class DiscrepancyReviewApp:
    def __init__(self, rows, output_path, copy_root):
        self.rows = rows
        self.output_path = Path(output_path)
        self.copy_root = copy_root
        self.index = 0
        self.tk_image = None

        self.root = Tk()
        self.root.title("Revisar discrepancias IA1")
        self.root.geometry("1140x840")

        self.status = StringVar()
        self.meta = StringVar()
        self.image_label = Label(self.root, bg="#202020")
        self.image_label.pack(side=TOP, fill=BOTH, expand=True)
        Label(self.root, textvariable=self.status, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.meta, anchor="w").pack(side=TOP, fill="x")

        buttons = Frame(self.root)
        buttons.pack(side=BOTTOM, fill="x")
        Button(buttons, text="Confirmar Pessoa (P)", command=lambda: self.save("person", "keep_as_person")).pack(
            side=LEFT, padx=4, pady=6
        )
        Button(buttons, text="Confirmar Nao Pessoa (N)", command=lambda: self.save("not_person", "change_to_not_person")).pack(
            side=LEFT, padx=4, pady=6
        )
        Button(buttons, text="BBox Errada (W)", command=lambda: self.save("person", "fix_bbox")).pack(
            side=LEFT, padx=4, pady=6
        )
        Button(buttons, text="Incerto (U)", command=lambda: self.save("uncertain", "needs_manual_review")).pack(
            side=LEFT, padx=4, pady=6
        )
        Button(buttons, text="Pular (S)", command=self.next_image).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Voltar (B)", command=self.previous_image).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Sair (Q)", command=self.root.destroy).pack(side=RIGHT, padx=4, pady=6)

        self.root.bind("p", lambda _event: self.save("person", "keep_as_person"))
        self.root.bind("P", lambda _event: self.save("person", "keep_as_person"))
        self.root.bind("n", lambda _event: self.save("not_person", "change_to_not_person"))
        self.root.bind("N", lambda _event: self.save("not_person", "change_to_not_person"))
        self.root.bind("w", lambda _event: self.save("person", "fix_bbox"))
        self.root.bind("W", lambda _event: self.save("person", "fix_bbox"))
        self.root.bind("u", lambda _event: self.save("uncertain", "needs_manual_review"))
        self.root.bind("U", lambda _event: self.save("uncertain", "needs_manual_review"))
        self.root.bind("s", lambda _event: self.next_image())
        self.root.bind("S", lambda _event: self.next_image())
        self.root.bind("b", lambda _event: self.previous_image())
        self.root.bind("B", lambda _event: self.previous_image())
        self.root.bind("q", lambda _event: self.root.destroy())
        self.root.bind("Q", lambda _event: self.root.destroy())
        self.root.bind("<Left>", lambda _event: self.previous_image())
        self.root.bind("<Right>", lambda _event: self.next_image())

        self.show_current()

    def current_row(self):
        if not self.rows or self.index >= len(self.rows):
            return None
        return self.rows[self.index]

    def show_current(self):
        row = self.current_row()
        if row is None:
            self.status.set(f"Fim. Decisoes salvas em: {self.output_path}")
            self.meta.set("")
            self.image_label.configure(image="", text="Fim da revisao")
            return

        image_path = Path(row.get("path", ""))
        boxes = []
        try:
            image = Image.open(image_path).convert("RGB")
            boxes = read_yolo_boxes(row.get("label_path", ""), image.width, image.height)
            image = draw_boxes(image, boxes)
            image = ImageOps.contain(image, (1100, 650))
            self.tk_image = ImageTk.PhotoImage(image)
            self.image_label.configure(image=self.tk_image, text="")
        except Exception as exc:
            self.tk_image = None
            self.image_label.configure(image="", text=f"Erro ao abrir:\n{image_path}\n{exc}")

        self.status.set(
            f"{self.index + 1}/{len(self.rows)} | dataset: {row.get('expected_label', '')} | "
            f"revisao atual: {row.get('reviewed_as', '')} | tipo: {row.get('discrepancy_type', '')} | boxes={len(boxes)}"
        )
        self.meta.set(
            f"split={row.get('split', '')} | camera={row.get('camera_id', '')} | "
            f"event={row.get('event_id', '')} | track={row.get('track_id', '')} | {image_path}"
        )

    def save(self, final_label, final_action):
        row = self.current_row()
        if row is None:
            return
        append_decision(self.output_path, row, final_label, final_action)
        copy_reviewed(self.copy_root, row, final_action)
        self.next_image()

    def next_image(self):
        if self.index < len(self.rows):
            self.index += 1
        self.show_current()

    def previous_image(self):
        if self.index > 0:
            self.index -= 1
        self.show_current()

    def run(self):
        self.root.mainloop()


def main():
    args = parse_args()
    discrepancy_path = Path(args.discrepancies)
    output_path = Path(args.output)
    if not discrepancy_path.exists():
        raise FileNotFoundError(f"CSV de discrepancias nao encontrado: {discrepancy_path}")

    rows = read_csv(discrepancy_path)
    if not args.include_reviewed:
        done = reviewed_paths(output_path)
        rows = [row for row in rows if row.get("path", "") not in done]

    ensure_output(output_path)
    print(f"Discrepancias para revisar: {len(rows)}")
    print(f"CSV de decisoes: {output_path}")
    if args.dry_run:
        return 0
    if not rows:
        return 0
    DiscrepancyReviewApp(rows, output_path, args.copy_reviewed).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
