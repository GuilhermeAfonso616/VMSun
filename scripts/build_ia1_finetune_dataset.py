import argparse
import csv
import hashlib
import json
import random
import shutil
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from PIL import Image, ImageOps


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLITS = ("train", "val", "test")


def load_config(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def stable_hash(value):
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()


def file_sha1(path):
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=True)


def parse_bbox(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or len(data) != 4:
        return None
    return [float(v) for v in data]


def normalize_label(label):
    return (label or "").strip().lower()


def label_kind(label, config):
    label = normalize_label(label)
    review = config["review_labels"]
    if label in {normalize_label(x) for x in review["positive"]}:
        return "positive"
    if label in {normalize_label(x) for x in review["negative"]}:
        return "negative"
    if label in {normalize_label(x) for x in review["ignore"]}:
        return "ignore"
    return "ignore"


def resolve_snapshot(path, config):
    if not path:
        return None
    raw = Path(path)
    candidates = [raw]
    text = str(path).replace("\\", "/")
    if text.startswith("/data/"):
        rel = text.removeprefix("/data/")
        for root in config["paths"].get("snapshot_roots", []):
            candidates.append(Path(root) / rel)
    for root in config["paths"].get("snapshot_roots", []):
        candidates.append(Path(root) / text.lstrip("/"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def split_for_reviewed(row, config):
    key = f"{row.get('camera_id')}|{str(row.get('created_at', ''))[:10]}|{row.get('event_id')}"
    value = int(stable_hash(key)[:8], 16) / 0xFFFFFFFF
    train_limit = float(config["split"]["train"])
    val_limit = train_limit + float(config["split"]["val"])
    if value < train_limit:
        return "train"
    if value < val_limit:
        return "val"
    return "test"


def yolo_line_from_bbox(bbox, width, height):
    x1, y1, x2, y2 = bbox
    x1 = max(0.0, min(float(width), x1))
    x2 = max(0.0, min(float(width), x2))
    y1 = max(0.0, min(float(height), y1))
    y2 = max(0.0, min(float(height), y2))
    bw = max(0.0, x2 - x1)
    bh = max(0.0, y2 - y1)
    if bw <= 0 or bh <= 0:
        return None
    cx = x1 + bw / 2
    cy = y1 + bh / 2
    return f"0 {cx / width:.6f} {cy / height:.6f} {bw / width:.6f} {bh / height:.6f}"


def bbox_bucket(bbox, width, height):
    if not bbox or width <= 0 or height <= 0:
        return "missing"
    area_pct = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]) / float(width * height)
    if area_pct < 0.01:
        return "small"
    if area_pct < 0.08:
        return "medium"
    return "large"


def yolo_bbox_buckets(label_path):
    buckets = Counter()
    path = Path(label_path)
    if not path.exists():
        return buckets
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) != 5:
            continue
        try:
            area = float(parts[3]) * float(parts[4])
        except ValueError:
            continue
        if area < 0.01:
            buckets["small"] += 1
        elif area < 0.08:
            buckets["medium"] += 1
        else:
            buckets["large"] += 1
    return buckets


def copy_reviewed_positive(src, dst_img, dst_label, bbox):
    dst_img.parent.mkdir(parents=True, exist_ok=True)
    dst_label.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst_img)
    with Image.open(src) as im:
        width, height = im.size
    line = yolo_line_from_bbox(bbox, width, height)
    if not line:
        return None, width, height
    dst_label.write_text(line + "\n", encoding="utf-8")
    return line, width, height


def copy_reviewed_negative_crop(src, dst_img, dst_label, bbox, padding_ratio):
    dst_img.parent.mkdir(parents=True, exist_ok=True)
    dst_label.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        width, height = im.size
        if not bbox:
            crop = im
        else:
            x1, y1, x2, y2 = bbox
            bw = max(1.0, x2 - x1)
            bh = max(1.0, y2 - y1)
            pad_x = bw * padding_ratio
            pad_y = bh * padding_ratio
            box = (
                max(0, int(x1 - pad_x)),
                max(0, int(y1 - pad_y)),
                min(width, int(x2 + pad_x)),
                min(height, int(y2 + pad_y)),
            )
            crop = im.crop(box)
        crop.save(dst_img, quality=95)
    dst_label.write_text("", encoding="utf-8")
    return width, height


def reviewed_rows(config):
    db = Path(config["paths"]["database"])
    if not db.exists():
        return [], [{"reason": "database_missing", "path": str(db)}]
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    query = """
        SELECT
            e.id AS event_id, e.camera_id, e.event_type, e.track_id, e.confidence,
            e.detector_score, e.event_score, e.details, e.created_at, e.snapshot_path,
            e.bbox_json, e.active_profile_snapshot, e.threshold_snapshot,
            f.label AS human_label, f.probable_cause, f.operator_note,
            f.reviewed_by, f.reviewed_at
        FROM event_feedback f
        JOIN events e ON e.id = f.event_id
        ORDER BY f.reviewed_at, f.id
    """
    return [dict(row) for row in con.execute(query).fetchall()], []


def reviewed_export_rows(config):
    records = []
    missing = []
    for export_dir in config.get("reviewed_vms", {}).get("reviewed_exports", []):
        base = Path(export_dir)
        events_csv = base / "events.csv"
        if not events_csv.exists():
            missing.append({"reason": "reviewed_export_missing", "path": str(base)})
            continue
        with events_csv.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                class_name = normalize_label(row.get("class_name"))
                label = normalize_label(row.get("label"))
                if class_name == "uncertain" or label in {normalize_label(x) for x in config["review_labels"]["ignore"]}:
                    continue
                if class_name not in {"person", "not_person"}:
                    continue
                meta_path = base / row.get("metadata_path", "")
                metadata = {}
                if meta_path.exists():
                    try:
                        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        metadata = {}
                crop_rel = row.get("crop_path") or metadata.get("crop_path") or ""
                context_rel = row.get("context_path") or metadata.get("context_path") or ""
                crop = base / crop_rel if crop_rel else None
                context = base / context_rel if context_rel else None
                source_image = crop if crop and crop.exists() else None
                if not source_image:
                    missing.append({"reason": "reviewed_export_image_missing", "event_id": row.get("event_id"), "metadata_path": str(meta_path), "crop_path": crop_rel, "context_path": context_rel})
                    continue
                bbox = metadata.get("bbox") or ""
                if isinstance(bbox, list):
                    bbox_json = json.dumps(bbox)
                else:
                    bbox_json = row.get("bbox_json") or ""
                records.append(
                    {
                        "source_image": str(source_image),
                        "source_kind": "crop",
                        "source": "reviewed_vms_export",
                        "review_kind": "positive" if class_name == "person" else "negative",
                        "human_label": row.get("label") or metadata.get("label") or "",
                        "probable_cause": row.get("probable_cause") or metadata.get("probable_cause") or "",
                        "operator_note": metadata.get("operator_note") or "",
                        "reviewed_by": metadata.get("reviewed_by") or "",
                        "reviewed_at": row.get("reviewed_at") or metadata.get("reviewed_at") or "",
                        "event_id": row.get("event_id") or metadata.get("event_id") or "",
                        "feedback_id": row.get("feedback_id") or metadata.get("feedback_id") or "",
                        "camera_id": row.get("camera_id") or metadata.get("camera_id") or "",
                        "camera_name": row.get("camera_name") or metadata.get("camera_name") or "",
                        "event_type": row.get("event_type") or metadata.get("event_type") or "",
                        "track_id": row.get("track_id") or metadata.get("track_id") or "",
                        "created_at": metadata.get("event_created_at") or "",
                        "bbox_json": bbox_json,
                        "detector_score": row.get("detector_score") or metadata.get("detector_score") or "",
                        "confidence": row.get("confidence") or metadata.get("confidence") or "",
                        "event_score": row.get("event_score") or metadata.get("event_score") or "",
                        "snapshot_path": row.get("snapshot_path") or metadata.get("snapshot_path") or "",
                        "export_status": row.get("export_status") or metadata.get("export_status") or "",
                    }
                )
    return records, missing


def load_review_decisions(config):
    audit_cfg = config.get("review_audit", {})
    decisions_csv = audit_cfg.get("decisions_csv")
    if not decisions_csv:
        return {}
    path = Path(decisions_csv)
    if not path.exists():
        return {}
    decisions = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = decision_key(row)
            if key:
                decisions[key] = row
    return decisions


def decision_key(row):
    camera = str(row.get("camera_id") or "")
    event = str(row.get("event_id") or "")
    track = str(row.get("track_id") or "")
    if not camera or not event:
        return ""
    return f"{camera}|{event}|{track}"


def apply_review_decision(row, decisions, config):
    decision = decisions.get(decision_key(row))
    if not decision:
        return row, None
    action = decision.get("final_action", "")
    final_label = decision.get("final_label", "")
    exclude_actions = set(config.get("review_audit", {}).get("exclude_actions", []))
    if action in exclude_actions or final_label == "uncertain":
        return None, {"reason": "excluded_by_visual_review", "event_id": row.get("event_id"), "camera_id": row.get("camera_id"), "track_id": row.get("track_id"), "final_action": action, "final_label": final_label}
    updated = dict(row)
    if final_label == "person":
        updated["review_kind"] = "positive"
        updated["human_label"] = "visual_review_person"
    elif final_label == "not_person":
        updated["review_kind"] = "negative"
        updated["human_label"] = "visual_review_not_person"
    updated["visual_review_action"] = action
    updated["visual_review_final_label"] = final_label
    return updated, None


def external_records(config):
    base = Path(config["paths"]["external_person_dataset"])
    records = []
    if not base.exists():
        return records
    rng = random.Random(int(config["external_dataset"].get("seed", 43)))
    split_limits = {
        "train": int(config["external_dataset"].get("max_train_images", 0)),
        "val": int(config["external_dataset"].get("max_val_images", 0)),
        "test": int(config["external_dataset"].get("max_test_images", 0)),
    }
    for src_split in ("train", "val"):
        images = sorted((base / "images" / src_split).glob("*"))
        images = [p for p in images if p.suffix.lower() in IMAGE_EXTS and (base / "labels" / src_split / f"{p.stem}.txt").exists()]
        rng.shuffle(images)
        if src_split == "train":
            destinations = [("train", split_limits["train"])]
        else:
            destinations = [("val", split_limits["val"]), ("test", split_limits["test"])]
        offset = 0
        for dst_split, limit in destinations:
            selected = images[offset: offset + limit] if limit > 0 else []
            offset += limit
            for image in selected:
                records.append(
                    {
                        "src_image": str(image),
                        "src_label": str(base / "labels" / src_split / f"{image.stem}.txt"),
                        "split": dst_split,
                        "source": "external_person_dataset",
                        "human_label": "external_person",
                        "event_id": "",
                        "camera_id": "",
                        "created_at": "",
                        "bbox_json": "",
                        "probable_cause": "",
                    }
                )
    return records


def extra_trusted_records(config):
    extra_cfg = config.get("extra_trusted_datasets", {})
    csv_paths = extra_cfg.get("trusted_csvs", [])
    records = []
    for csv_path in csv_paths:
        path = Path(csv_path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("triage_status") != "trusted":
                    continue
                label = normalize_label(row.get("label"))
                if label not in {"person", "not_person"}:
                    continue
                image_path = Path(row.get("image_path") or row.get("crop_path") or "")
                context_path = Path(row.get("context_path") or "")
                if not image_path.exists() and context_path.exists():
                    image_path = context_path
                if not image_path.exists():
                    continue
                row["source"] = f"trusted_{row.get('dataset', 'pc_test')}"
                row["review_kind"] = "positive" if label == "person" else "negative"
                row["human_label"] = "trusted_person" if label == "person" else "trusted_not_person"
                row["source_image"] = str(image_path)
                records.append(row)
    return records


def copy_extra_positive(row, dst_img, dst_label):
    src = Path(row["source_image"])
    dst_img.parent.mkdir(parents=True, exist_ok=True)
    dst_label.parent.mkdir(parents=True, exist_ok=True)

    context = Path(row.get("context_path") or "")
    bbox = parse_bbox(row.get("bbox_xyxy") or "")
    if context.exists() and bbox:
        shutil.copy2(context, dst_img)
        with Image.open(dst_img) as im:
            width, height = im.size
        line = yolo_line_from_bbox(bbox, width, height)
        if line:
            dst_label.write_text(line + "\n", encoding="utf-8")
            return "context_bbox_positive", width, height

    shutil.copy2(src, dst_img)
    with Image.open(dst_img) as im:
        width, height = im.size
    dst_label.write_text("0 0.500000 0.500000 1.000000 1.000000\n", encoding="utf-8")
    return "crop_fullbox_positive", width, height


def copy_extra_negative(row, dst_img, dst_label):
    src = Path(row["source_image"])
    dst_img.parent.mkdir(parents=True, exist_ok=True)
    dst_label.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst_img)
    with Image.open(dst_img) as im:
        width, height = im.size
    dst_label.write_text("", encoding="utf-8")
    return "trusted_negative_empty", width, height


def build(config):
    out = Path(config["paths"]["output_dataset"])
    report_dir = Path(config["paths"]["reports"])
    if out.exists():
        shutil.rmtree(out)
    for split in SPLITS:
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    missing = []
    duplicate_feedback = set()
    review_decisions = load_review_decisions(config)

    for record in external_records(config):
        src_img = Path(record["src_image"])
        src_label = Path(record["src_label"])
        split = record["split"]
        stem = f"external_{stable_hash(src_img)[:16]}{src_img.suffix.lower()}"
        dst_img = out / "images" / split / stem
        dst_label = out / "labels" / split / f"{Path(stem).stem}.txt"
        shutil.copy2(src_img, dst_img)
        shutil.copy2(src_label, dst_label)
        with Image.open(dst_img) as im:
            width, height = im.size
        manifest.append({**record, "path": str(dst_img), "label_path": str(dst_label), "width": width, "height": height, "sha1": file_sha1(dst_img), "bbox_bucket": "external", "review_kind": "positive"})

    rows, initial_missing = reviewed_rows(config)
    missing.extend(initial_missing)
    for row in rows:
        kind = label_kind(row.get("human_label"), config)
        if kind == "ignore":
            continue
        event_key = (row.get("event_id"), row.get("human_label"))
        if event_key in duplicate_feedback:
            continue
        duplicate_feedback.add(event_key)
        snapshot = resolve_snapshot(row.get("snapshot_path"), config)
        bbox = parse_bbox(row.get("bbox_json"))
        if not snapshot:
            missing.append({"reason": "snapshot_missing", "event_id": row.get("event_id"), "path": row.get("snapshot_path")})
            continue
        if not bbox:
            missing.append({"reason": "bbox_missing", "event_id": row.get("event_id"), "path": str(snapshot)})
            continue
        split = split_for_reviewed(row, config)
        suffix = snapshot.suffix.lower() if snapshot.suffix.lower() in IMAGE_EXTS else ".jpg"
        label_name = "person" if kind == "positive" else "negative"
        stem = f"reviewed_event{row['event_id']}_cam{row['camera_id']}_{label_name}{suffix}"
        dst_img = out / "images" / split / stem
        dst_label = out / "labels" / split / f"{Path(stem).stem}.txt"
        if kind == "positive":
            line, width, height = copy_reviewed_positive(snapshot, dst_img, dst_label, bbox)
            if not line:
                missing.append({"reason": "invalid_bbox", "event_id": row.get("event_id"), "path": str(snapshot)})
                continue
        else:
            width, height = copy_reviewed_negative_crop(snapshot, dst_img, dst_label, bbox, float(config["reviewed_vms"].get("false_positive_padding_ratio", 0.35)))
        manifest.append(
            {
                "path": str(dst_img),
                "label_path": str(dst_label),
                "source": "reviewed_vms_event",
                "review_kind": kind,
                "human_label": row.get("human_label") or "",
                "probable_cause": row.get("probable_cause") or "",
                "operator_note": row.get("operator_note") or "",
                "reviewed_by": row.get("reviewed_by") or "",
                "reviewed_at": row.get("reviewed_at") or "",
                "event_id": row.get("event_id"),
                "camera_id": row.get("camera_id"),
                "event_type": row.get("event_type") or "",
                "track_id": row.get("track_id") or "",
                "created_at": row.get("created_at") or "",
                "bbox_json": row.get("bbox_json") or "",
                "detector_score": row.get("detector_score") or row.get("confidence") or "",
                "event_score": row.get("event_score") or "",
                "details": row.get("details") or "",
                "active_profile_snapshot": row.get("active_profile_snapshot") or "",
                "threshold_snapshot": row.get("threshold_snapshot") or "",
                "split": split,
                "width": width,
                "height": height,
                "sha1": file_sha1(dst_img),
                "bbox_bucket": bbox_bucket(bbox, width, height),
            }
        )

    export_rows, export_missing = reviewed_export_rows(config)
    missing.extend(export_missing)
    for row in export_rows:
        row, skipped = apply_review_decision(row, review_decisions, config)
        if skipped:
            missing.append(skipped)
            continue
        src = Path(row["source_image"])
        split = split_for_reviewed({"camera_id": row.get("camera_id"), "created_at": row.get("created_at") or row.get("reviewed_at"), "event_id": row.get("event_id")}, config)
        suffix = src.suffix.lower() if src.suffix.lower() in IMAGE_EXTS else ".jpg"
        label_name = "person" if row["review_kind"] == "positive" else "negative"
        stem = f"reviewed_export_event{row.get('event_id')}_cam{row.get('camera_id')}_{label_name}_{stable_hash(src)[:8]}{suffix}"
        dst_img = out / "images" / split / stem
        dst_label = out / "labels" / split / f"{Path(stem).stem}.txt"
        dst_img.parent.mkdir(parents=True, exist_ok=True)
        dst_label.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst_img)
        with Image.open(dst_img) as im:
            width, height = im.size
        if row["review_kind"] == "positive":
            dst_label.write_text(f"0 0.500000 0.500000 1.000000 1.000000\n", encoding="utf-8")
            bucket = "crop_positive"
        else:
            dst_label.write_text("", encoding="utf-8")
            bucket = "crop_negative"
        manifest.append(
            {
                **row,
                "path": str(dst_img),
                "label_path": str(dst_label),
                "split": split,
                "width": width,
                "height": height,
                "sha1": file_sha1(dst_img),
                "bbox_bucket": bucket,
            }
        )

    extra_total = Counter()
    for row in extra_trusted_records(config):
        src = Path(row["source_image"])
        split = split_for_reviewed(
            {
                "camera_id": row.get("camera_id"),
                "created_at": row.get("timestamp"),
                "event_id": f"{row.get('dataset')}:{row.get('event_id')}:{row.get('track_id')}:{row.get('sha1')}",
            },
            config,
        )
        suffix = src.suffix.lower() if src.suffix.lower() in IMAGE_EXTS else ".jpg"
        label_name = "person" if row["review_kind"] == "positive" else "negative"
        stem = f"{row.get('dataset','pc')}_event{row.get('event_id')}_cam{row.get('camera_id')}_{label_name}_{stable_hash(row.get('sha1') or src)[:10]}{suffix}"
        dst_img = out / "images" / split / stem
        dst_label = out / "labels" / split / f"{Path(stem).stem}.txt"
        try:
            if row["review_kind"] == "positive":
                bucket, width, height = copy_extra_positive(row, dst_img, dst_label)
            else:
                bucket, width, height = copy_extra_negative(row, dst_img, dst_label)
        except Exception as exc:
            missing.append({"reason": "extra_trusted_copy_error", "path": str(src), "error": str(exc)})
            continue
        extra_total[row["review_kind"]] += 1
        manifest.append(
            {
                **row,
                "path": str(dst_img),
                "label_path": str(dst_label),
                "split": split,
                "width": width,
                "height": height,
                "sha1": file_sha1(dst_img),
                "bbox_bucket": bucket,
                "source_image": str(src),
                "triage_status": row.get("triage_status", ""),
                "triage_reason": row.get("triage_reason", ""),
                "size_bucket": row.get("size_bucket", ""),
            }
        )

    with (out / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = sorted({key for row in manifest for key in row.keys()})
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(manifest)
    (out / "data.yaml").write_text(f"path: {out.as_posix()}\ntrain: images/train\nval: images/val\ntest: images/test\n\nnames:\n  0: person\n", encoding="utf-8")

    counts = defaultdict(Counter)
    cameras = defaultdict(Counter)
    bbox_buckets = Counter()
    label_box_buckets = Counter()
    causes = Counter()
    for row in manifest:
        counts[row["split"]][row.get("review_kind", "unknown")] += 1
        if row.get("camera_id") != "":
            cameras[str(row.get("camera_id"))][row.get("review_kind", "unknown")] += 1
        bbox_buckets[row.get("bbox_bucket", "unknown")] += 1
        label_box_buckets.update(yolo_bbox_buckets(row.get("label_path", "")))
        if row.get("probable_cause"):
            causes[row["probable_cause"]] += 1

    write_json(out / "dataset_counts.json", {split: dict(counter) for split, counter in counts.items()})
    write_json(report_dir / "missing_files.json", missing)
    write_json(report_dir / "camera_distribution.json", {cam: dict(counter) for cam, counter in cameras.items()})
    write_json(report_dir / "bbox_buckets.json", dict(bbox_buckets))
    write_json(report_dir / "label_box_buckets.json", dict(label_box_buckets))
    write_json(report_dir / "probable_causes.json", dict(causes))

    reviewed_total = Counter(row.get("review_kind", "unknown") for row in manifest if row.get("source") in {"reviewed_vms_event", "reviewed_vms_export"})
    reviewed_by_source = defaultdict(Counter)
    for row in manifest:
        reviewed_by_source[row.get("source", "unknown")][row.get("review_kind", "unknown")] += 1
    lines = [
        "# IA1 fine-tune dataset report",
        "",
        f"Output dataset: `{out}`",
        f"Current IA1 model: `{config['paths']['ia1_current_model']}`",
        "",
        "## Counts by split",
    ]
    for split in SPLITS:
        lines.append(f"- {split}: " + ", ".join(f"{k}={v}" for k, v in counts[split].items()))
    lines.extend(["", "## Reviewed VMS events"])
    lines.append(f"- positives: {reviewed_total.get('positive', 0)}")
    lines.append(f"- negatives: {reviewed_total.get('negative', 0)}")
    lines.append(f"- missing/skipped files: {len(missing)}")
    lines.extend(["", "## Counts by source"])
    for source, counter in sorted(reviewed_by_source.items()):
        lines.append(f"- {source}: " + ", ".join(f"{k}={v}" for k, v in counter.items()))
    lines.extend(["", "## Safety notes"])
    lines.append("- False positives are exported as padded crops with empty label files, not as full-frame negatives.")
    lines.append("- Inconclusive/canceled labels are ignored.")
    lines.append("- Candidate training must start from current IA1, never from scratch.")
    lines.append("- This dataset is for audit training only; it does not change production.")
    (report_dir / "dataset_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("dataset:", out)
    print("manifest_rows:", len(manifest))
    print("reviewed:", dict(reviewed_total))
    print("missing:", len(missing))
    print("report:", report_dir / "dataset_report.md")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/ia1_finetune_vms_hardneg_v1.yaml")
    args = parser.parse_args()
    build(load_config(args.config))


if __name__ == "__main__":
    main()
