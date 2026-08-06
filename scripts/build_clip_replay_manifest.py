"""Build a replay manifest from exported OneDrive event clips.

The manifest is used to replay reviewed clips as fake RTSP cameras through
MediaMTX/ffmpeg. It is read-only against the source clip folder.

Example:

    python -B scripts/build_clip_replay_manifest.py \
        --source-dir "D:\\IA_Rebuild\\Analitico VMS Clips" \
        --output-dir data/test_replay \
        --rtsp-base-url rtsp://localhost:8554
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_DIR = Path(r"D:\IA_Rebuild\Analitico VMS Clips")
DEFAULT_OUTPUT_DIR = Path("data/test_replay")
DEFAULT_RTSP_BASE_URL = "rtsp://localhost:8554"


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_load_error": str(exc)}


def maybe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def ffprobe_duration_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None

    return maybe_float(completed.stdout.strip())


def expectation_from_feedback(label: str, event: dict[str, Any]) -> str:
    normalized = str(label or "").strip().lower()
    if normalized == "true_positive":
        return "should_alarm"
    if normalized == "false_positive":
        return "should_not_alarm"
    if bool(event.get("is_alarm_active")):
        return "unknown_existing_alarm"
    return "unknown"


def event_id_from_json(path: Path, payload: dict[str, Any]) -> int | None:
    event = payload.get("event") if isinstance(payload, dict) else None
    if isinstance(event, dict):
        try:
            return int(event.get("id"))
        except Exception:
            pass

    stem = path.stem
    marker = "audit_pending_event_"
    if stem.startswith(marker) and stem.endswith("_event"):
        raw = stem[len(marker) : -len("_event")]
        try:
            return int(raw)
        except Exception:
            return None
    return None


def build_manifest(
    *,
    source_dir: Path,
    output_dir: Path,
    rtsp_base_url: str,
    include_unreviewed: bool,
    limit: int | None,
    probe_duration: bool,
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rtsp_base_url = rtsp_base_url.rstrip("/")

    items: list[dict[str, Any]] = []
    event_paths = sorted(source_dir.glob("audit_pending_event_*_event.json"), key=lambda p: p.name)
    for event_path in event_paths:
        payload = load_json(event_path)
        event_id = event_id_from_json(event_path, payload)
        if event_id is None:
            continue

        clip_path = source_dir / f"audit_pending_event_{event_id}_clip.mp4"
        if not clip_path.exists():
            continue

        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        feedback = payload.get("feedback") if isinstance(payload.get("feedback"), dict) else {}
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        label = str(feedback.get("label") or "unreviewed").strip().lower()
        if label == "unreviewed" and not include_unreviewed:
            continue

        replay_id = f"replay_event_{event_id}"
        stream_path = replay_id
        snapshot_path = source_dir / f"audit_pending_event_{event_id}_snapshot.jpg"
        item = {
            "replay_id": replay_id,
            "source_event_id": event_id,
            "source_camera_id": event.get("camera_id"),
            "source_event_type": event.get("event_type"),
            "source_rule_id": event.get("rule_id"),
            "source_track_id": event.get("track_id"),
            "severity": event.get("severity"),
            "status": event.get("status"),
            "is_alarm_active": event.get("is_alarm_active"),
            "confidence": event.get("confidence"),
            "event_score": event.get("event_score"),
            "detector_score": event.get("detector_score"),
            "feedback_label": label,
            "expectation": expectation_from_feedback(label, event),
            "reviewed": bool(feedback),
            "scene_profile": evidence.get("scene_profile"),
            "camera_family": evidence.get("camera_family"),
            "bbox": evidence.get("bbox"),
            "clip_path": str(clip_path),
            "snapshot_path": str(snapshot_path) if snapshot_path.exists() else "",
            "event_json_path": str(event_path),
            "duration_seconds": ffprobe_duration_seconds(clip_path) if probe_duration else None,
            "stream_path": stream_path,
            "rtsp_url": f"{rtsp_base_url}/{stream_path}",
        }
        items.append(item)
        if limit is not None and len(items) >= limit:
            break

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "rtsp_base_url": rtsp_base_url,
        "items": items,
        "summary": {
            "total": len(items),
            "true_positive": sum(1 for item in items if item["feedback_label"] == "true_positive"),
            "false_positive": sum(1 for item in items if item["feedback_label"] == "false_positive"),
            "unreviewed": sum(1 for item in items if item["feedback_label"] == "unreviewed"),
            "should_alarm": sum(1 for item in items if item["expectation"] == "should_alarm"),
            "should_not_alarm": sum(1 for item in items if item["expectation"] == "should_not_alarm"),
        },
    }
    return manifest


def write_outputs(manifest: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "clip_replay_manifest.json"
    csv_path = output_dir / "clip_replay_manifest.csv"

    json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "replay_id",
        "source_event_id",
        "source_camera_id",
        "source_event_type",
        "severity",
        "status",
        "is_alarm_active",
        "feedback_label",
        "expectation",
        "duration_seconds",
        "rtsp_url",
        "clip_path",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in manifest["items"]:
            writer.writerow({field: item.get(field) for field in fields})

    return json_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a manifest for replaying exported event clips.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--rtsp-base-url", default=DEFAULT_RTSP_BASE_URL)
    parser.add_argument("--include-unreviewed", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-duration-probe", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_manifest(
        source_dir=args.source_dir,
        output_dir=args.output_dir,
        rtsp_base_url=args.rtsp_base_url,
        include_unreviewed=bool(args.include_unreviewed),
        limit=args.limit,
        probe_duration=not bool(args.skip_duration_probe),
    )
    json_path, csv_path = write_outputs(manifest, args.output_dir)
    summary = manifest["summary"]
    print(f"Manifest written: {json_path}")
    print(f"CSV written: {csv_path}")
    print(
        "Summary: "
        f"total={summary['total']} "
        f"tp={summary['true_positive']} "
        f"fp={summary['false_positive']} "
        f"unreviewed={summary['unreviewed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
