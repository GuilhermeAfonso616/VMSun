#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics_v2.revalidation.alarm_decision import decide_alarm_action
from app.analytics_v2.revalidation.far_person_revalidator import FarPersonRevalidator
from app.analytics_v2.revalidation.person_crop_revalidator import PersonCropRevalidator
from app.analytics_v2.revalidation.strategy3_v2 import build_strategy3_v2_review_payload, load_anti_fp_patterns


DEFAULT_EXPORT_DIR = Path("D:/IA2/reviewed_events_export_20260504_134833")
DEFAULT_OUTPUT_DIR = Path("D:/Analitico/reports/strategy3_v2_anti_fp_validation")
LEGACY_SUMMARY = Path("D:/Analitico/reports/strategy3_refined_validation/strategy3_refined_v2_summary_20260508_120554.json")


@dataclass
class AuditRow:
    event_id: int
    camera_id: str
    truth_class: str
    probable_cause: str
    detector_score: float
    frame_source: str
    frame_path: str
    frame_width: int
    frame_height: int
    bbox: str
    bbox_height_ratio: float
    ia2_person_score: float | None
    ia2_not_person_score: float | None
    ia2_quality_reason: str
    ia3_available: bool
    ia3_person_score: float | None
    ia3_not_person_score: float | None
    strategy_decision: str
    strategy_reason: str
    strategy_initial_decision: str
    size_bucket: str
    independent_confirmation: str
    tracking_confirmed: bool
    temporal_persistence: bool
    region_fp_risk: str
    pattern_blacklist_match: bool
    pattern_whitelist_match: bool
    anti_fp_decision: str
    anti_fp_reason: str
    anti_fp_risk_score: float
    final_notification_level: str
    alarm_action: str
    alarm_status: str
    alarm_applied: bool


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _event_truth(label: str) -> str:
    label = str(label or "").strip().lower()
    if label in {"true_positive", "expected_event", "person"}:
        return "person"
    if label in {"false_positive", "not_person"}:
        return "not_person"
    return "uncertain"


def _resolve_path(export_dir: Path, rel: str | None) -> Path | None:
    raw = str(rel or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() and path.exists():
        return path
    candidate = export_dir / raw.replace("/", "\\")
    return candidate if candidate.exists() else None


def load_events(export_dir: Path) -> list[dict[str, Any]]:
    events_csv = export_dir / "events.csv"
    if not events_csv.exists():
        raise FileNotFoundError(f"events.csv not found: {events_csv}")

    rows: list[dict[str, Any]] = []
    with events_csv.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("export_status") != "crop_saved":
                continue
            truth = _event_truth(row.get("label", ""))
            if truth == "uncertain":
                continue
            metadata_path = _resolve_path(export_dir, row.get("metadata_path"))
            metadata = _load_json(metadata_path) if metadata_path else {}
            context_path = _resolve_path(export_dir, row.get("context_path") or metadata.get("context_path"))
            crop_path = _resolve_path(export_dir, row.get("crop_path") or metadata.get("crop_path"))
            frame_path = context_path or crop_path
            if frame_path is None:
                continue
            bbox = metadata.get("bbox")
            rows.append(
                {
                    "event_id": int(row["event_id"]),
                    "camera_id": row.get("camera_id") or metadata.get("camera_id") or "",
                    "truth_class": truth,
                    "probable_cause": row.get("probable_cause") or metadata.get("probable_cause") or "",
                    "detector_score": _float(row.get("detector_score") or metadata.get("detector_score")),
                    "frame_path": frame_path,
                    "frame_source": "context" if context_path else "crop",
                    "bbox": bbox,
                }
            )
    return rows


def _bbox_for_frame(row: dict[str, Any], width: int, height: int) -> list[float]:
    bbox = row.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4 and row.get("frame_source") == "context":
        try:
            x1, y1, x2, y2 = [float(v) for v in bbox]
            if x2 > x1 and y2 > y1 and x1 < width and y1 < height:
                return [max(0.0, x1), max(0.0, y1), min(float(width), x2), min(float(height), y2)]
        except Exception:
            pass
    return [0.0, 0.0, float(width), float(height)]


def _legacy_metrics() -> dict[str, Any]:
    if not LEGACY_SUMMARY.exists():
        return {}
    try:
        return json.loads(LEGACY_SUMMARY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pct(numer: int | float, denom: int | float) -> float:
    return float(numer) / float(denom) if denom else 0.0


def summarize(rows: list[AuditRow]) -> dict[str, Any]:
    by_truth: dict[str, Any] = {}
    for truth in ["person", "not_person"]:
        subset = [r for r in rows if r.truth_class == truth]
        strategy_counts = Counter(r.strategy_decision for r in subset)
        notify_counts = Counter(r.anti_fp_decision for r in subset)
        alarm_counts = Counter(r.alarm_action for r in subset)
        by_truth[truth] = {
            "total": len(subset),
            "strategy_decision": dict(strategy_counts),
            "notification_decision": dict(notify_counts),
            "alarm_action": dict(alarm_counts),
        }

    person_total = by_truth["person"]["total"]
    not_person_total = by_truth["not_person"]["total"]
    person_notify = by_truth["person"]["notification_decision"].get("NOTIFY", 0)
    person_low = by_truth["person"]["notification_decision"].get("LOW_PRIORITY", 0)
    person_audit = by_truth["person"]["notification_decision"].get("AUDIT", 0)
    not_person_notify = by_truth["not_person"]["notification_decision"].get("NOTIFY", 0)
    not_person_low = by_truth["not_person"]["notification_decision"].get("LOW_PRIORITY", 0)
    not_person_audit = by_truth["not_person"]["notification_decision"].get("AUDIT", 0)
    not_person_suppress = by_truth["not_person"]["notification_decision"].get("SUPPRESS", 0)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_events_processed": len(rows),
        "by_truth": by_truth,
        "key_metrics": {
            "person_notify": person_notify,
            "person_notify_rate": _pct(person_notify, person_total),
            "person_not_suppressed_or_rejected": person_notify + person_low + person_audit,
            "person_not_suppressed_or_rejected_rate": _pct(person_notify + person_low + person_audit, person_total),
            "not_person_notify": not_person_notify,
            "not_person_notify_rate": _pct(not_person_notify, not_person_total),
            "not_person_low_priority": not_person_low,
            "not_person_audit": not_person_audit,
            "not_person_suppress": not_person_suppress,
            "not_person_non_notify": not_person_low + not_person_audit + not_person_suppress,
            "not_person_non_notify_rate": _pct(not_person_low + not_person_audit + not_person_suppress, not_person_total),
        },
    }


def write_review_csv(path: Path, rows: list[AuditRow]) -> None:
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(AuditRow.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_markdown(path: Path, summary: dict[str, Any], legacy: dict[str, Any], csv_path: Path, json_path: Path) -> None:
    metrics = summary["key_metrics"]
    person = summary["by_truth"]["person"]
    not_person = summary["by_truth"]["not_person"]
    old_fp = int((legacy.get("strategy3_refined_v2") or legacy.get("strategy3_refined_v1") or {}).get("not_person_fp_count") or 0)
    old_person_accept = int((legacy.get("strategy3_refined_v2") or {}).get("person_accept") or 0)
    old_person_total = int((legacy.get("strategy3_refined_v2") or {}).get("person_total") or 0)
    reduction = old_fp - int(metrics["not_person_notify"]) if old_fp else None
    reduction_pct = _pct(reduction, old_fp) if reduction is not None and old_fp else None

    lines = [
        "# Strategy 3 v2 + Anti-FP Validation",
        "",
        f"Generated at: `{summary['generated_at']}`",
        f"Events processed: `{summary['total_events_processed']}`",
        "",
        "## Main Result",
        "",
        "| metric | old Strategy 3 refined | Strategy 3 v2 + Anti-FP | change |",
        "|---|---:|---:|---:|",
    ]
    if old_fp:
        lines.append(
            f"| not_person strong notifications / FP | {old_fp} | {metrics['not_person_notify']} | "
            f"-{reduction} ({reduction_pct:.1%}) |"
        )
    if old_person_total:
        lines.append(
            f"| person accepted/notified proxy | {old_person_accept}/{old_person_total} | "
            f"{metrics['person_notify']}/{person['total']} NOTIFY | "
            f"{metrics['person_notify_rate']:.1%} notify rate |"
        )
    lines.append(
        f"| person not suppressed/rejected | n/a | {metrics['person_not_suppressed_or_rejected']}/{person['total']} | "
        f"{metrics['person_not_suppressed_or_rejected_rate']:.1%} |"
    )
    lines.append(
        f"| not_person not notified | n/a | {metrics['not_person_non_notify']}/{not_person['total']} | "
        f"{metrics['not_person_non_notify_rate']:.1%} |"
    )

    lines.extend(
        [
            "",
            "## Notification Decision By Truth",
            "",
            "| truth | NOTIFY | LOW_PRIORITY | AUDIT | SUPPRESS | total |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for truth, data in [("person", person), ("not_person", not_person)]:
        counts = data["notification_decision"]
        lines.append(
            f"| {truth} | {counts.get('NOTIFY', 0)} | {counts.get('LOW_PRIORITY', 0)} | "
            f"{counts.get('AUDIT', 0)} | {counts.get('SUPPRESS', 0)} | {data['total']} |"
        )

    lines.extend(
        [
            "",
            "## Strategy Classification By Truth",
            "",
            "| truth | ACCEPT | LOW_PRIORITY | AUDIT | SUPPRESS | REJECT | total |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for truth, data in [("person", person), ("not_person", not_person)]:
        counts = data["strategy_decision"]
        lines.append(
            f"| {truth} | {counts.get('ACCEPT', 0)} | {counts.get('LOW_PRIORITY', 0)} | "
            f"{counts.get('AUDIT', 0)} | {counts.get('SUPPRESS', 0)} | {counts.get('REJECT', 0)} | {data['total']} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This audit uses exported reviewed events and saved context/crop images.",
            "- Track and temporal history are not reconstructed from runtime, so temporal/tracking support is conservative in this offline replay.",
            "- Region memory is not reconstructed from the production database here; configured anti-FP patterns are loaded if available.",
            "- Anti-FP mode remains audit/shadow-safe when the runtime setting is `audit`.",
            "",
            "## Files",
            "",
            f"- CSV: `{csv_path}`",
            f"- JSON: `{json_path}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate current Strategy 3 v2 + Anti-FP on reviewed event export.")
    parser.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    events = load_events(args.export_dir)
    if args.limit:
        events = events[: args.limit]

    print(f"events: {len(events)}")
    print("loading IA2/IA3...")
    ia2 = PersonCropRevalidator()
    ia3 = FarPersonRevalidator()
    anti_fp_patterns = load_anti_fp_patterns()
    print(f"anti_fp_patterns: {'loaded' if anti_fp_patterns else 'none'}")

    rows: list[AuditRow] = []
    started = time.time()
    for index, event in enumerate(events, start=1):
        if index == 1 or index % max(1, len(events) // 10) == 0:
            print(f"processing {index}/{len(events)}")

        frame = cv2.imread(str(event["frame_path"]))
        if frame is None:
            continue
        frame_height, frame_width = frame.shape[:2]
        bbox = _bbox_for_frame(event, frame_width, frame_height)
        ia2_result = ia2.validate(frame, bbox)
        ia3_result = ia3.validate(frame, bbox)
        camera_id_int = _int_or_none(event["camera_id"])

        payload = build_strategy3_v2_review_payload(
            ia2_result=ia2_result,
            ia3_result=ia3_result,
            detector_score=event["detector_score"],
            bbox=bbox,
            frame_width=frame_width,
            frame_height=frame_height,
            camera_id=camera_id_int,
            track=None,
            timestamp=None,
            region_memory=None,
            anti_fp_patterns=anti_fp_patterns,
        )
        anti_fp = payload.get("anti_fp_post_filter") or {}
        alarm = decide_alarm_action(
            event_maturity={"level": "ALARM_READY", "decision": "alarm_candidate", "safety": {}},
            ia2_result=ia2_result,
            ia3_result=ia3_result,
            consensus_result={},
            strategy3_v2_result=payload,
            anti_fp_post_filter_result=anti_fp,
        )
        quality = ia2_result.quality or {}
        rows.append(
            AuditRow(
                event_id=event["event_id"],
                camera_id=str(event["camera_id"]),
                truth_class=event["truth_class"],
                probable_cause=event["probable_cause"],
                detector_score=round(float(event["detector_score"]), 6),
                frame_source=event["frame_source"],
                frame_path=str(event["frame_path"]),
                frame_width=frame_width,
                frame_height=frame_height,
                bbox=json.dumps([round(float(v), 2) for v in bbox]),
                bbox_height_ratio=float(payload.get("bbox_height_ratio") or 0.0),
                ia2_person_score=ia2_result.person_score,
                ia2_not_person_score=ia2_result.not_person_score,
                ia2_quality_reason=str(quality.get("quality_reason") or ""),
                ia3_available=bool(payload.get("ia3_available")),
                ia3_person_score=getattr(ia3_result, "person_far_score", None),
                ia3_not_person_score=getattr(ia3_result, "not_person_far_score", None),
                strategy_decision=str(payload.get("decision") or ""),
                strategy_reason=str(payload.get("reason") or ""),
                strategy_initial_decision=str(payload.get("initial_decision") or ""),
                size_bucket=str(payload.get("size_bucket") or ""),
                independent_confirmation=str(payload.get("independent_confirmation") or ""),
                tracking_confirmed=bool(payload.get("tracking_confirmed")),
                temporal_persistence=bool(payload.get("temporal_persistence")),
                region_fp_risk=str(payload.get("region_fp_risk") or ""),
                pattern_blacklist_match=bool(payload.get("pattern_blacklist_match")),
                pattern_whitelist_match=bool(payload.get("pattern_whitelist_match")),
                anti_fp_decision=str(anti_fp.get("decision") or ""),
                anti_fp_reason=str(anti_fp.get("reason") or ""),
                anti_fp_risk_score=float(anti_fp.get("risk_score") or 0.0),
                final_notification_level=str(payload.get("final_notification_level") or ""),
                alarm_action=str(alarm.get("action") or ""),
                alarm_status=str(alarm.get("suggested_status") or ""),
                alarm_applied=bool(alarm.get("applied")),
            )
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = args.output_dir / f"strategy3_v2_anti_fp_{stamp}.csv"
    json_path = args.output_dir / f"strategy3_v2_anti_fp_summary_{stamp}.json"
    md_path = args.output_dir / f"strategy3_v2_anti_fp_summary_{stamp}.md"
    person_attention_path = args.output_dir / f"strategy3_v2_person_attention_{stamp}.csv"
    not_person_notify_path = args.output_dir / f"strategy3_v2_not_person_notify_{stamp}.csv"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()) if rows else ["empty"])
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    summary = summarize(rows)
    summary["runtime_seconds"] = round(time.time() - started, 2)
    summary["export_dir"] = str(args.export_dir)
    summary["legacy_summary"] = str(LEGACY_SUMMARY)
    person_attention_rows = [
        row for row in rows if row.truth_class == "person" and row.anti_fp_decision in {"AUDIT", "SUPPRESS"}
    ]
    not_person_notify_rows = [
        row for row in rows if row.truth_class == "not_person" and row.anti_fp_decision == "NOTIFY"
    ]
    write_review_csv(person_attention_path, person_attention_rows)
    write_review_csv(not_person_notify_path, not_person_notify_rows)
    summary["review_files"] = {
        "person_attention": str(person_attention_path),
        "not_person_notify": str(not_person_notify_path),
    }
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(md_path, summary, _legacy_metrics(), csv_path, json_path)

    print(f"processed: {len(rows)} in {summary['runtime_seconds']}s")
    print(f"csv: {csv_path}")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    print(f"person_attention_csv: {person_attention_path}")
    print(f"not_person_notify_csv: {not_person_notify_path}")
    print(json.dumps(summary["key_metrics"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
