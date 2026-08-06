import argparse
import csv
import random
import shutil
import sys
from pathlib import Path
from tkinter import BOTH, BOTTOM, LEFT, RIGHT, TOP, Button, Frame, Label, StringVar, Tk

from PIL import Image, ImageDraw, ImageOps, ImageTk


DEFAULT_DATASET = Path("D:/IA/datasets/ia1_finetune_vms_hardneg_v1")
DEFAULT_OUTPUT = Path("D:/Analitico/reports/ia1_finetune_vms_hardneg_v1/visual_review_ia1_dataset.csv")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Revisao visual do dataset YOLO da IA1, com bboxes desenhadas e CSV de decisoes."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Pasta do dataset YOLO gerado.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="CSV onde a revisao sera salva.")
    parser.add_argument("--split", action="append", help="Filtrar split: train, val ou test. Pode repetir.")
    parser.add_argument(
        "--review-kind",
        action="append",
        help="Filtrar tipo do manifest: positive ou negative. Pode repetir.",
    )
    parser.add_argument("--source", action="append", help="Filtrar source do manifest. Pode repetir.")
    parser.add_argument("--camera", action="append", help="Filtrar camera_id do manifest. Pode repetir.")
    parser.add_argument("--limit", type=int, default=0, help="Limite de imagens.")
    parser.add_argument("--shuffle", action="store_true", help="Embaralhar a ordem.")
    parser.add_argument("--include-reviewed", action="store_true", help="Incluir imagens ja revisadas no CSV.")
    parser.add_argument("--dry-run", action="store_true", help="Mostra contagens e nao abre a interface.")
    parser.add_argument(
        "--copy-reviewed",
        default="",
        help="Opcional: copia imagens revisadas para esta pasta, separando por decisao.",
    )
    return parser.parse_args()


def read_manifest(dataset_dir):
    manifest_path = dataset_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv nao encontrado em {dataset_dir}")

    rows = []
    with manifest_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            image_path = Path(row.get("path", ""))
            if image_path.exists() and image_path.suffix.lower() in IMAGE_EXTS:
                rows.append(row)
    return rows


def reviewed_paths(output_path):
    if not output_path.exists():
        return set()
    with output_path.open("r", newline="", encoding="utf-8-sig") as f:
        return {row.get("path", "") for row in csv.DictReader(f)}


def row_matches(row, args):
    if args.split and row.get("split") not in set(args.split):
        return False
    if args.review_kind and row.get("review_kind") not in set(args.review_kind):
        return False
    if args.source and row.get("source") not in set(args.source):
        return False
    if args.camera and row.get("camera_id") not in set(args.camera):
        return False
    return True


def prepare_rows(args):
    dataset_dir = Path(args.dataset)
    output_path = Path(args.output)
    rows = [row for row in read_manifest(dataset_dir) if row_matches(row, args)]
    if not args.include_reviewed:
        done = reviewed_paths(output_path)
        rows = [row for row in rows if row.get("path", "") not in done]
    if args.shuffle:
        random.seed(43)
        random.shuffle(rows)
    if args.limit > 0:
        rows = rows[: args.limit]
    return rows


def output_fields():
    return [
        "path",
        "label_path",
        "split",
        "source",
        "review_kind",
        "human_label",
        "camera_id",
        "event_id",
        "track_id",
        "sha1",
        "reviewed_as",
        "is_correction",
        "note",
    ]


def ensure_output(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields())
        writer.writeheader()


def append_decision(output_path, row, reviewed_as, note=""):
    review_kind = row.get("review_kind", "")
    expected = "person" if review_kind == "positive" else "not_person" if review_kind == "negative" else ""
    record = {
        "path": row.get("path", ""),
        "label_path": row.get("label_path", ""),
        "split": row.get("split", ""),
        "source": row.get("source", ""),
        "review_kind": review_kind,
        "human_label": row.get("human_label", ""),
        "camera_id": row.get("camera_id", ""),
        "event_id": row.get("event_id", ""),
        "track_id": row.get("track_id", ""),
        "sha1": row.get("sha1", ""),
        "reviewed_as": reviewed_as,
        "is_correction": str(bool(expected and reviewed_as in {"person", "not_person"} and reviewed_as != expected)).lower(),
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
        draw.text((x1 + 4, max(0, y1 - 16)), f"class {cls}", fill=(255, 40, 40))
    return image


def copy_reviewed(copy_root, row, reviewed_as):
    if not copy_root:
        return
    src = Path(row.get("path", ""))
    if not src.exists():
        return
    dst_dir = Path(copy_root) / reviewed_as
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        dst = dst_dir / f"{src.stem}_{row.get('sha1', '')[:8]}{src.suffix}"
    shutil.copy2(src, dst)


class ReviewApp:
    def __init__(self, rows, output_path, copy_root):
        self.rows = rows
        self.output_path = Path(output_path)
        self.copy_root = copy_root
        self.index = 0
        self.tk_image = None

        self.root = Tk()
        self.root.title("Revisao visual IA1 dataset")
        self.root.geometry("1120x820")

        self.status = StringVar()
        self.meta = StringVar()
        self.image_label = Label(self.root, bg="#202020")
        self.image_label.pack(side=TOP, fill=BOTH, expand=True)
        Label(self.root, textvariable=self.status, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.meta, anchor="w").pack(side=TOP, fill="x")

        buttons = Frame(self.root)
        buttons.pack(side=BOTTOM, fill="x")
        Button(buttons, text="Pessoa (P)", command=lambda: self.save("person")).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Nao pessoa (N)", command=lambda: self.save("not_person")).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="BBox errada (W)", command=lambda: self.save("wrong_bbox")).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Incerto (U)", command=lambda: self.save("uncertain")).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Pular (S)", command=self.next_image).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Voltar (B)", command=self.previous_image).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Sair (Q)", command=self.root.destroy).pack(side=RIGHT, padx=4, pady=6)

        for key, value in {
            "p": "person",
            "P": "person",
            "n": "not_person",
            "N": "not_person",
            "w": "wrong_bbox",
            "W": "wrong_bbox",
            "u": "uncertain",
            "U": "uncertain",
        }.items():
            self.root.bind(key, lambda _event, label=value: self.save(label))
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
            self.status.set(f"Fim. Revisao salva em: {self.output_path}")
            self.meta.set("")
            self.image_label.configure(image="", text="Fim da revisao")
            return

        image_path = Path(row.get("path", ""))
        try:
            image = Image.open(image_path).convert("RGB")
            boxes = read_yolo_boxes(row.get("label_path", ""), image.width, image.height)
            image = draw_boxes(image, boxes)
            image = ImageOps.contain(image, (1080, 650))
            self.tk_image = ImageTk.PhotoImage(image)
            self.image_label.configure(image=self.tk_image, text="")
        except Exception as exc:
            self.tk_image = None
            self.image_label.configure(image="", text=f"Erro ao abrir:\n{image_path}\n{exc}")
            boxes = []

        expected = "person" if row.get("review_kind") == "positive" else "not_person"
        self.status.set(
            f"{self.index + 1}/{len(self.rows)} | esperado: {expected} | "
            f"split={row.get('split', '')} | boxes={len(boxes)}"
        )
        self.meta.set(
            f"source={row.get('source', '')} | camera={row.get('camera_id', '')} | "
            f"event={row.get('event_id', '')} | image={image_path}"
        )

    def save(self, reviewed_as):
        row = self.current_row()
        if row is None:
            return
        append_decision(self.output_path, row, reviewed_as)
        copy_reviewed(self.copy_root, row, reviewed_as)
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


def print_counts(rows):
    counts = {}
    for row in rows:
        key = (row.get("split", ""), row.get("review_kind", ""), row.get("source", ""), row.get("camera_id", ""))
        counts[key] = counts.get(key, 0) + 1
    for (split, kind, source, camera), count in sorted(counts.items()):
        camera_label = camera or "-"
        print(f"{split or '-'} / {kind or '-'} / {source or '-'} / camera={camera_label}: {count}")


def main():
    args = parse_args()
    rows = prepare_rows(args)
    output_path = Path(args.output)
    ensure_output(output_path)
    print(f"Imagens para revisar: {len(rows)}")
    print(f"CSV de saida: {output_path}")
    if args.dry_run:
        print_counts(rows)
        return 0
    if not rows:
        return 0
    ReviewApp(rows, output_path, args.copy_reviewed).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
