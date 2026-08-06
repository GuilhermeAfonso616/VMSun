import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from PIL import Image
from tqdm import tqdm
from ultralytics import YOLO


def load_config(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def latest_candidate(config):
    requested = config["evaluation"].get("candidate_model", "auto")
    if requested and requested != "auto":
        return Path(requested)
    configured = Path(config["paths"]["ia1_candidate_model"])
    if configured.exists():
        return configured
    runs = Path(config["paths"]["runs"])
    run_name = config.get("training", {}).get("run_name") or config.get("name", "IA1_candidate_vms_hardneg_v1")
    candidates = sorted(runs.glob(f"{run_name}*/weights/best.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else configured


def load_manifest(config):
    manifest = Path(config["paths"]["output_dataset"]) / "manifest.csv"
    with manifest.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_yolo_label(label_path, width, height):
    boxes = []
    path = Path(label_path)
    if not path.exists():
        return boxes
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) != 5 or parts[0] != "0":
            continue
        cx, cy, bw, bh = [float(v) for v in parts[1:]]
        x1 = (cx - bw / 2) * width
        y1 = (cy - bh / 2) * height
        x2 = (cx + bw / 2) * width
        y2 = (cy + bh / 2) * height
        boxes.append([x1, y1, x2, y2])
    return boxes


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = area_a + area_b - inter
    return inter / denom if denom else 0.0


def predict_person_boxes(model, image_path, conf, imgsz):
    result = model.predict(str(image_path), conf=conf, imgsz=imgsz, classes=[0], verbose=False)[0]
    boxes = []
    if result.boxes is None:
        return boxes
    for box in result.boxes:
        xyxy = box.xyxy.detach().cpu().numpy().tolist()[0]
        score = float(box.conf.detach().cpu().item())
        boxes.append([*xyxy, score])
    return boxes


def evaluate_model(model_path, rows, config):
    model = YOLO(str(model_path))
    conf = float(config["evaluation"].get("conf", 0.25))
    imgsz = int(config["training"].get("imgsz", 640))
    match_iou = float(config["evaluation"].get("iou_match_threshold", 0.50))
    out = []
    for row in tqdm(rows, desc=f"eval {Path(model_path).name}"):
        image = Path(row["path"])
        if not image.exists():
            continue
        with Image.open(image) as im:
            width, height = im.size
        gt = parse_yolo_label(row["label_path"], width, height)
        preds = predict_person_boxes(model, image, conf, imgsz)
        matched = 0
        for gt_box in gt:
            if any(iou(gt_box, pred[:4]) >= match_iou for pred in preds):
                matched += 1
        false_negative = max(0, len(gt) - matched)
        false_positive = max(0, len(preds) - matched)
        out.append({**row, "gt_count": len(gt), "pred_count": len(preds), "matched": matched, "false_negative": false_negative, "false_positive": false_positive})
    return out


def summarize(rows):
    gt = sum(int(r["gt_count"]) for r in rows)
    matched = sum(int(r["matched"]) for r in rows)
    fp = sum(int(r["false_positive"]) for r in rows)
    fn = sum(int(r["false_negative"]) for r in rows)
    recall = matched / gt if gt else 0.0
    precision = matched / (matched + fp) if matched + fp else 0.0
    return {"images": len(rows), "gt": gt, "matched": matched, "false_positive": fp, "false_negative": fn, "recall": recall, "precision": precision}


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(key) or "unknown"].append(row)
    return {name: summarize(items) for name, items in groups.items()}


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def evaluate(config):
    report_dir = Path(config["paths"]["reports"])
    report_dir.mkdir(parents=True, exist_ok=True)
    rows = [row for row in load_manifest(config) if row.get("source") == "reviewed_vms_event" or row.get("split") in ("val", "test")]
    current_model = Path(config["paths"]["ia1_current_model"])
    candidate_model = latest_candidate(config)
    if not candidate_model.exists():
        raise FileNotFoundError(f"Candidate model not found: {candidate_model}. Train it first.")

    current = evaluate_model(current_model, rows, config)
    candidate = evaluate_model(candidate_model, rows, config)
    write_rows(report_dir / "audit_current_predictions.csv", current)
    write_rows(report_dir / "audit_candidate_predictions.csv", candidate)

    current_summary = summarize(current)
    candidate_summary = summarize(candidate)
    current_by_camera = grouped(current, "camera_id")
    candidate_by_camera = grouped(candidate, "camera_id")
    lost = [row for row in candidate if int(row["false_negative"]) > 0 and int(row["gt_count"]) > 0]
    write_rows(report_dir / "candidate_person_losses.csv", lost)

    report = {
        "current_model": str(current_model),
        "candidate_model": str(candidate_model),
        "current": current_summary,
        "candidate": candidate_summary,
        "current_by_camera": current_by_camera,
        "candidate_by_camera": candidate_by_camera,
        "candidate_person_losses": len(lost),
    }
    (report_dir / "audit_metrics.json").write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")

    lines = [
        "# IA1 candidate audit",
        "",
        f"Current model: `{current_model}`",
        f"Candidate model: `{candidate_model}`",
        "",
        "## Summary",
        f"- current: recall={current_summary['recall']:.4f}, precision={current_summary['precision']:.4f}, FN={current_summary['false_negative']}, FP={current_summary['false_positive']}",
        f"- candidate: recall={candidate_summary['recall']:.4f}, precision={candidate_summary['precision']:.4f}, FN={candidate_summary['false_negative']}, FP={candidate_summary['false_positive']}",
        "",
        "## Safety decision",
    ]
    if lost:
        lines.append(f"- Do not recommend production swap: candidate loses {len(lost)} reviewed/person validation images.")
    else:
        lines.append("- No reviewed/person validation loss detected in this audit set. Still keep audit-only until a larger reviewed holdout passes.")
    (report_dir / "candidate_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("report:", report_dir / "candidate_audit.md")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/ia1_finetune_vms_hardneg_v1.yaml")
    args = parser.parse_args()
    evaluate(load_config(args.config))


if __name__ == "__main__":
    main()
