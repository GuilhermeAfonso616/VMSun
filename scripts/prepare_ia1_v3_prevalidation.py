import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageOps


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
FILENAME_RE = re.compile(r"cam_?(?P<camera>\d+).*?event_?(?:event_)?(?P<event>\d+).*?(?:track_?(?P<track>\d+))?.*?(?:feedback_?(?P<feedback>\d+))?", re.I)


def load_config(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def sha1_file(path):
    digest = hashlib.sha1()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def image_size(path):
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return 0, 0


def parse_name(path):
    match = FILENAME_RE.search(Path(path).stem)
    if not match:
        return {}
    return {k: (v or "") for k, v in match.groupdict().items()}


def normalize_label(label):
    label = (label or "").strip().lower()
    if label in {"person", "true_positive", "operator_confirmed", "expected_event"}:
        return "person"
    if label in {"not_person", "false_positive", "shadow"}:
        return "not_person"
    return "uncertain"


def resolve_pc2_pair(root, label, metadata_path, meta):
    crop_path = meta.get("crop_path") or ""
    context_path = meta.get("context_path") or ""
    crop = None
    context = None
    if crop_path:
        crop = root / label / "crops" / Path(crop_path).name
    if context_path:
        context = root / label / "context" / Path(context_path).name
    if not crop or not crop.exists():
        fallback = root / label / "crops" / (metadata_path.stem + ".jpg")
        crop = fallback if fallback.exists() else None
    if not context or not context.exists():
        fallback = root / label / "context" / (metadata_path.stem + "_context.jpg")
        context = fallback if fallback.exists() else None
    return crop, context


def records_pc1(root):
    rows = []
    for label in ("person", "not_person", "uncertain"):
        folder = root / label
        if not folder.exists():
            continue
        for image in folder.iterdir():
            if not image.is_file() or image.suffix.lower() not in IMAGE_EXTS:
                continue
            parsed = parse_name(image)
            width, height = image_size(image)
            rows.append(
                {
                    "dataset": "pc_teste_1",
                    "label": label,
                    "image_path": str(image),
                    "crop_path": str(image),
                    "context_path": "",
                    "metadata_path": "",
                    "camera_id": parsed.get("camera", ""),
                    "event_id": parsed.get("event", ""),
                    "track_id": parsed.get("track", ""),
                    "feedback_id": parsed.get("feedback", ""),
                    "timestamp": "",
                    "decision_source": "folder_label",
                    "status": "",
                    "bbox_xyxy": "",
                    "confidence_detector": "",
                    "confidence_revalidator": "",
                    "confidence_event": "",
                    "width": width,
                    "height": height,
                    "sha1": sha1_file(image),
                }
            )
    return rows


def records_pc2(root):
    rows = []
    for label in ("person", "not_person", "uncertain"):
        metadata_dir = root / label / "metadata"
        if not metadata_dir.exists():
            continue
        for metadata in metadata_dir.glob("*.json"):
            meta = read_json(metadata)
            normalized = normalize_label(meta.get("label") or meta.get("class") or label)
            crop, context = resolve_pc2_pair(root, label, metadata, meta)
            primary = crop or context
            if not primary:
                width, height = 0, 0
                sha1 = ""
            else:
                width, height = image_size(primary)
                sha1 = sha1_file(primary)
            bbox = meta.get("bbox_xyxy") or []
            rows.append(
                {
                    "dataset": "pc_teste_2",
                    "label": normalized,
                    "image_path": str(primary or ""),
                    "crop_path": str(crop or ""),
                    "context_path": str(context or ""),
                    "metadata_path": str(metadata),
                    "camera_id": str(meta.get("camera_id", "")),
                    "event_id": str(meta.get("event_id", "")),
                    "track_id": str(meta.get("track_id", "")),
                    "feedback_id": "",
                    "timestamp": meta.get("timestamp", ""),
                    "decision_source": meta.get("decision_source", ""),
                    "status": meta.get("status", ""),
                    "bbox_xyxy": json.dumps(bbox) if bbox else "",
                    "confidence_detector": meta.get("confidence_detector", ""),
                    "confidence_revalidator": meta.get("confidence_revalidator", ""),
                    "confidence_event": meta.get("confidence_event", ""),
                    "width": width,
                    "height": height,
                    "sha1": sha1,
                }
            )
    return rows


def size_bucket(row, config):
    width = int(row.get("width") or 0)
    height = int(row.get("height") or 0)
    small_w = int(config["triage"].get("small_crop_width", 80))
    small_h = int(config["triage"].get("small_crop_height", 96))
    if not width or not height:
        return "missing"
    if width < small_w or height < small_h:
        return "small"
    if width < 160 or height < 192:
        return "medium"
    return "large"


def as_float(value):
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def triage_row(row, config):
    reasons = []
    label = row["label"]
    bucket = size_bucket(row, config)
    detector = as_float(row.get("confidence_detector"))
    problem_cameras = {str(c) for c in config["triage"].get("review_problem_cameras", [])}
    low_pos = float(config["triage"].get("review_low_detector_positive_below", 0.35))
    high_neg = float(config["triage"].get("review_high_detector_negative_above", 0.70))

    if not row.get("image_path"):
        return "exclude", "missing_image", bucket
    if label == "uncertain":
        return "exclude", "uncertain_label", bucket
    if row.get("sha1") == "":
        return "exclude", "missing_sha1", bucket
    if label == "person" and bucket == "small" and config["triage"].get("review_small_person", True):
        reasons.append("small_person_safety")
    if label == "person" and detector is not None and detector < low_pos:
        reasons.append("person_low_detector_score")
    if label == "not_person" and detector is not None and detector > high_neg:
        reasons.append("not_person_high_detector_score")
    if row.get("camera_id") in problem_cameras and label == "person" and bucket in {"small", "medium"}:
        reasons.append("problem_camera_person_safety")
    if row.get("status") in {"context_saved_low_quality_crop"}:
        reasons.append("low_quality_crop_status")

    if reasons:
        return "review_required", ";".join(sorted(set(reasons))), bucket
    return "trusted", "label_structurally_consistent", bucket


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in rows for k in row.keys()}) if rows else ["path"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def contact_sheets(rows, out_dir, config):
    sheet_cfg = config["triage"].get("contact_sheet", {})
    if not sheet_cfg.get("enabled", True):
        return []
    max_per = int(sheet_cfg.get("max_images_per_sheet", 100))
    tw = int(sheet_cfg.get("thumb_width", 160))
    th = int(sheet_cfg.get("thumb_height", 120))
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for start in range(0, len(rows), max_per):
        chunk = rows[start : start + max_per]
        cols = 5
        rows_count = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows_count * (th + 32)), (245, 245, 245))
        draw = ImageDraw.Draw(sheet)
        for idx, row in enumerate(chunk):
            path = Path(row.get("image_path", ""))
            x = (idx % cols) * tw
            y = (idx // cols) * (th + 32)
            try:
                im = Image.open(path).convert("RGB")
                im = ImageOps.contain(im, (tw, th))
                sheet.paste(im, (x, y))
            except Exception:
                draw.rectangle((x, y, x + tw - 1, y + th - 1), fill=(220, 220, 220))
            caption = f"{row.get('label')} cam{row.get('camera_id')} e{row.get('event_id')}"
            draw.text((x + 2, y + th + 2), caption[:28], fill=(0, 0, 0))
            draw.text((x + 2, y + th + 16), row.get("triage_reason", "")[:28], fill=(120, 0, 0))
        output = out_dir / f"review_required_{start // max_per + 1:03d}.jpg"
        sheet.save(output, quality=92)
        written.append(str(output))
    return written


def build_report(report_path, rows, trusted, review_required, excluded, sheets):
    by_dataset = defaultdict(Counter)
    by_camera = defaultdict(Counter)
    by_reason = Counter()
    by_bucket = defaultdict(Counter)
    for row in rows:
        by_dataset[row["dataset"]][row["label"]] += 1
        by_camera[row.get("camera_id", "")][row["triage_status"]] += 1
        by_reason[row["triage_reason"]] += 1
        by_bucket[row["size_bucket"]][row["label"]] += 1

    lines = [
        "# IA1 V3 Prevalidation",
        "",
        "## Resumo",
        "",
        f"- total: {len(rows)}",
        f"- trusted: {len(trusted)}",
        f"- review_required: {len(review_required)}",
        f"- excluded: {len(excluded)}",
        "",
        "## Por Dataset E Label",
        "",
    ]
    for dataset, counter in sorted(by_dataset.items()):
        lines.append(f"- {dataset}: " + ", ".join(f"{k}={v}" for k, v in sorted(counter.items())))
    lines.extend(["", "## Buckets De Tamanho", ""])
    for bucket, counter in sorted(by_bucket.items()):
        lines.append(f"- {bucket}: " + ", ".join(f"{k}={v}" for k, v in sorted(counter.items())))
    lines.extend(["", "## Motivos De Triagem", ""])
    for reason, count in by_reason.most_common(30):
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Cameras Com Mais Review Required/Exclude", ""])
    camera_rows = sorted(by_camera.items(), key=lambda item: item[1].get("review_required", 0) + item[1].get("exclude", 0), reverse=True)
    for camera, counter in camera_rows[:30]:
        lines.append(f"- camera {camera or '-'}: " + ", ".join(f"{k}={v}" for k, v in sorted(counter.items())))
    lines.extend(["", "## Contact Sheets", ""])
    if sheets:
        for sheet in sheets:
            lines.append(f"- `{sheet}`")
    else:
        lines.append("- nenhuma gerada")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/ia1_v3_prevalidation.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    report_dir = Path(config["paths"]["reports"])
    report_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    rows.extend(records_pc1(Path(config["paths"]["pc_test_1"])))
    rows.extend(records_pc2(Path(config["paths"]["pc_test_2"])))

    seen = set()
    deduped = []
    for row in rows:
        key = row.get("sha1") or f"{row.get('dataset')}|{row.get('image_path')}"
        if key in seen:
            row["duplicate"] = "true"
        else:
            row["duplicate"] = "false"
            seen.add(key)
        status, reason, bucket = triage_row(row, config)
        row["triage_status"] = status
        row["triage_reason"] = reason
        row["size_bucket"] = bucket
        deduped.append(row)

    trusted = [r for r in deduped if r["triage_status"] == "trusted" and r["duplicate"] == "false"]
    review_required = [r for r in deduped if r["triage_status"] == "review_required" and r["duplicate"] == "false"]
    excluded = [r for r in deduped if r["triage_status"] == "exclude" or r["duplicate"] == "true"]

    write_csv(report_dir / "pc_test_inventory.csv", deduped)
    write_csv(report_dir / "trusted_train_candidates.csv", trusted)
    write_csv(report_dir / "review_required.csv", review_required)
    write_csv(report_dir / "excluded_uncertain_or_invalid.csv", excluded)
    sheets = contact_sheets(review_required, report_dir / "contact_sheets", config)
    build_report(report_dir / "summary.md", deduped, trusted, review_required, excluded, sheets)

    print("total:", len(deduped))
    print("trusted:", len(trusted))
    print("review_required:", len(review_required))
    print("excluded:", len(excluded))
    print("report:", report_dir / "summary.md")


if __name__ == "__main__":
    main()
