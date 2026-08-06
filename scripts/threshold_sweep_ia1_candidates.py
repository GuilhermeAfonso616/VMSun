import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import yaml
from PIL import Image
from tqdm import tqdm
from ultralytics import YOLO


DEFAULT_CONFIG = Path("D:/Analitico/configs/ia1_finetune_vms_hardneg_v3_2_1024.yaml")
DEFAULT_REPORT = Path("D:/Analitico/reports/ia1_threshold_sweep_v1_to_v3_2")


def parse_args():
    parser = argparse.ArgumentParser(description="Threshold sweep IA1 candidates on the same audit set.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT))
    parser.add_argument("--min-conf", type=float, default=0.01)
    parser.add_argument(
        "--thresholds",
        default="0.01,0.03,0.05,0.075,0.10,0.15,0.20,0.25,0.30,0.35,0.40,0.50,0.60",
    )
    return parser.parse_args()


def load_config(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_manifest(config):
    manifest = Path(config["paths"]["output_dataset"]) / "manifest.csv"
    with manifest.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [row for row in rows if row.get("source") == "reviewed_vms_event" or row.get("split") in {"val", "test"}]


def parse_label(path, width, height):
    boxes = []
    label_path = Path(path)
    if not label_path.exists():
        return boxes
    for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split()
        if len(parts) != 5 or parts[0] != "0":
            continue
        cx, cy, bw, bh = [float(v) for v in parts[1:]]
        boxes.append([(cx - bw / 2) * width, (cy - bh / 2) * height, (cx + bw / 2) * width, (cy + bh / 2) * height])
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


def audit_group(row):
    source = row.get("source", "")
    kind = row.get("review_kind", "")
    if source == "external_person_dataset":
        return "external_positive"
    if source in {"reviewed_vms_export", "reviewed_vms_event"} and kind == "positive":
        return "vms_positive"
    if source in {"reviewed_vms_export", "reviewed_vms_event"} and kind == "negative":
        return "vms_negative"
    if source.startswith("trusted_") and kind == "positive":
        return "trusted_pc_positive"
    if source.startswith("trusted_") and kind == "negative":
        return "trusted_pc_negative"
    return "other"


def model_specs():
    return [
        {
            "name": "v1",
            "path": Path("D:/Analitico/models/ia1_candidate/ia1_candidate_vms_hardneg_v1.pt"),
            "imgsz": 640,
        },
        {
            "name": "v2",
            "path": Path("D:/Analitico/models/ia1_candidate/ia1_candidate_vms_hardneg_v2.pt"),
            "imgsz": 640,
        },
        {
            "name": "v3",
            "path": Path("D:/Analitico/models/ia1_candidate/ia1_candidate_vms_hardneg_v3.pt"),
            "imgsz": 640,
        },
        {
            "name": "v3_2_1024",
            "path": Path("D:/Analitico/models/ia1_candidate/ia1_candidate_vms_hardneg_v3_2_1024.pt"),
            "imgsz": 1024,
        },
    ]


def predict_rows(model_spec, rows, min_conf, match_iou):
    model = YOLO(str(model_spec["path"]))
    predictions = []
    for idx, row in enumerate(tqdm(rows, desc=f"predict {model_spec['name']}")):
        image = Path(row["path"])
        if not image.exists():
            continue
        with Image.open(image) as im:
            width, height = im.size
        gt = parse_label(row["label_path"], width, height)
        result = model.predict(str(image), conf=min_conf, imgsz=model_spec["imgsz"], classes=[0], verbose=False)[0]
        preds = []
        if result.boxes is not None:
            for box in result.boxes:
                xyxy = box.xyxy.detach().cpu().numpy().tolist()[0]
                score = float(box.conf.detach().cpu().item())
                preds.append([*xyxy, score])
        predictions.append({"idx": idx, "row": row, "gt": gt, "preds": preds, "match_iou": match_iou})
    return predictions


def metrics_for_threshold(predictions, threshold):
    rows_out = []
    for item in predictions:
        gt = item["gt"]
        preds = [p for p in item["preds"] if p[4] >= threshold]
        matched_pred = set()
        matched = 0
        for gt_box in gt:
            best_i = None
            best_iou = 0.0
            for pred_i, pred in enumerate(preds):
                if pred_i in matched_pred:
                    continue
                value = iou(gt_box, pred[:4])
                if value > best_iou:
                    best_iou = value
                    best_i = pred_i
            if best_i is not None and best_iou >= item["match_iou"]:
                matched += 1
                matched_pred.add(best_i)
        row = item["row"]
        rows_out.append(
            {
                "group": audit_group(row),
                "camera_id": row.get("camera_id", "") or "unknown",
                "gt": len(gt),
                "matched": matched,
                "fp": max(0, len(preds) - matched),
                "fn": max(0, len(gt) - matched),
                "path": row.get("path", ""),
                "source": row.get("source", ""),
                "review_kind": row.get("review_kind", ""),
            }
        )
    return rows_out


def summarize(rows):
    gt = sum(r["gt"] for r in rows)
    matched = sum(r["matched"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    return {
        "images": len(rows),
        "gt": gt,
        "matched": matched,
        "fp": fp,
        "fn": fn,
        "recall": matched / gt if gt else 0.0,
        "precision": matched / (matched + fp) if matched + fp else 0.0,
    }


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in rows for k in row.keys()}) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def choose_threshold(rows):
    # Safety-first: minimize person false negatives on VMS + trusted PC positives.
    candidates = []
    for row in rows:
        critical_fn = int(row["vms_positive_fn"]) + int(row["trusted_pc_positive_fn"])
        critical_fp = int(row["vms_negative_fp"]) + int(row["trusted_pc_negative_fp"])
        candidates.append((critical_fn, critical_fp, -float(row["global_precision"]), float(row["threshold"]), row))
    return sorted(candidates)[0][-1] if candidates else None


def main():
    args = parse_args()
    config = load_config(args.config)
    rows = load_manifest(config)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    thresholds = [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
    match_iou = float(config["evaluation"].get("iou_match_threshold", 0.50))

    all_summary = []
    recommendations = []
    group_names = ["global", "external_positive", "vms_positive", "vms_negative", "trusted_pc_positive", "trusted_pc_negative"]

    for spec in model_specs():
        if not spec["path"].exists():
            continue
        preds = predict_rows(spec, rows, args.min_conf, match_iou)
        model_rows = []
        error_rows = []
        for threshold in thresholds:
            evaluated = metrics_for_threshold(preds, threshold)
            groups = defaultdict(list)
            groups["global"] = evaluated
            for row in evaluated:
                groups[row["group"]].append(row)
                if threshold == 0.25 and row["fn"] > 0 and row["gt"] > 0:
                    error_rows.append({"model": spec["name"], "threshold": threshold, **row})
            flat = {"model": spec["name"], "threshold": threshold}
            for group in group_names:
                summary = summarize(groups[group])
                for key, value in summary.items():
                    flat[f"{group}_{key}"] = value
            model_rows.append(flat)
            all_summary.append(flat)
        rec = choose_threshold(model_rows)
        if rec:
            recommendations.append(rec)
        write_csv(report_dir / f"{spec['name']}_person_fn_at_025.csv", error_rows)

    write_csv(report_dir / "threshold_sweep.csv", all_summary)
    write_csv(report_dir / "recommended_by_model.csv", recommendations)

    lines = ["# IA1 Threshold Sweep V1 to V3.2", "", f"Audit images: `{len(rows)}`", ""]
    lines.append("## Recommended Safety-First Threshold By Model")
    lines.append("")
    lines.append("| model | threshold | critical FN | critical FP | global recall | global precision | VMS+PC pos recall |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in recommendations:
        critical_fn = int(row["vms_positive_fn"]) + int(row["trusted_pc_positive_fn"])
        critical_fp = int(row["vms_negative_fp"]) + int(row["trusted_pc_negative_fp"])
        pos_gt = int(row["vms_positive_gt"]) + int(row["trusted_pc_positive_gt"])
        pos_matched = int(row["vms_positive_matched"]) + int(row["trusted_pc_positive_matched"])
        pos_recall = pos_matched / pos_gt if pos_gt else 0
        lines.append(
            f"| {row['model']} | {float(row['threshold']):.3f} | {critical_fn} | {critical_fp} | "
            f"{float(row['global_recall']):.4f} | {float(row['global_precision']):.4f} | {pos_recall:.4f} |"
        )

    def row_at(model, threshold):
        for row in all_summary:
            if row["model"] == model and abs(float(row["threshold"]) - threshold) < 1e-9:
                return row
        return None

    lines.extend(["", "## Fixed Threshold 0.25 Comparison", ""])
    lines.append("| model | global recall | global precision | global FP | global FN | VMS pos FN | VMS neg FP | PC pos FN | PC neg FP |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for model in ["v1", "v2", "v3", "v3_2_1024"]:
        row = row_at(model, 0.25)
        if not row:
            continue
        lines.append(
            f"| {model} | {float(row['global_recall']):.4f} | {float(row['global_precision']):.4f} | "
            f"{int(row['global_fp'])} | {int(row['global_fn'])} | {int(row['vms_positive_fn'])} | "
            f"{int(row['vms_negative_fp'])} | {int(row['trusted_pc_positive_fn'])} | {int(row['trusted_pc_negative_fp'])} |"
        )

    lines.extend(["", "## Files", "", "- `threshold_sweep.csv`", "- `recommended_by_model.csv`", "- `*_person_fn_at_025.csv`"])
    (report_dir / "threshold_sweep_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("report:", report_dir / "threshold_sweep_summary.md")


if __name__ == "__main__":
    main()
