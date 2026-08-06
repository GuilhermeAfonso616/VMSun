#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.analytics_v2.revalidation import (  # noqa: E402
    build_strategy3_v2_review_payload,
    decide_alarm_action,
    evaluate_consensus_block_candidate,
    load_anti_fp_patterns,
)
from app.core.config import settings  # noqa: E402


PERSON_LABELS = {"true_positive", "expected_event", "person"}
NOT_PERSON_LABELS = {"false_positive", "not_person"}


@dataclass
class ReplayRow:
    event_id: int
    camera_id: int
    camera_name: str
    truth_class: str
    ia1_detector_score: float | None
    ia2_person_score: float | None
    ia2_not_person_score: float | None
    ia3_person_score: float | None
    ia3_not_person_score: float | None
    frame_width: int
    frame_height: int
    bbox_height_ratio: float
    size_bucket: str
    region_risk: str
    strategy_decision: str
    strategy_reason: str
    anti_fp_decision: str
    anti_fp_reason: str
    final_notification_level: str
    consensus_block_candidate: bool
    direct_ia2_block_candidate: bool
    simulated_runtime_action: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay das regras atuais IA2/IA3/Strategy3 em eventos avaliados.")
    parser.add_argument("--reviewed-csv", required=True, type=Path)
    parser.add_argument("--output-dir", default="reports/current_rules_replay", type=Path)
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    try:
        raw = str(value).strip()
        if raw == "":
            return None
        return float(raw)
    except Exception:
        return None


def truth_from_label(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in PERSON_LABELS:
        return "person"
    if normalized in NOT_PERSON_LABELS:
        return "not_person"
    return "uncertain"


def parse_bbox(value: str | None) -> list[float] | None:
    try:
        parsed = json.loads(str(value or ""))
        if isinstance(parsed, list) and len(parsed) == 4:
            x1, y1, x2, y2 = [float(v) for v in parsed]
            if x2 > x1 and y2 > y1:
                return [x1, y1, x2, y2]
    except Exception:
        return None
    return None


def infer_frame_size(bbox: list[float] | None, area_ratio: float | None) -> tuple[int, int]:
    candidates = [(704, 480), (1280, 720), (1920, 1080), (960, 540), (640, 480), (800, 600)]
    if not bbox:
        return 704, 480
    max_x = max(bbox[0], bbox[2])
    max_y = max(bbox[1], bbox[3])
    valid = [(w, h) for w, h in candidates if w >= max_x and h >= max_y]
    if not valid:
        return int(max(1, max_x)), int(max(1, max_y))
    if not area_ratio or area_ratio <= 0:
        return valid[0]
    box_area = max(1.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    expected_area = box_area / area_ratio
    return min(valid, key=lambda item: abs((item[0] * item[1]) - expected_area))


def quality_for_bbox(bbox: list[float] | None, frame_width: int, frame_height: int) -> dict[str, Any]:
    if not bbox:
        return {"quality_gate_passed": False, "quality_reason": "missing_bbox"}
    x1, y1, x2, y2 = bbox
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    area_ratio = (box_w * box_h) / max(1.0, float(frame_width * frame_height))
    margin = float(settings.person_revalidator_block_border_margin_ratio)
    near_border = (
        x1 <= frame_width * margin
        or y1 <= frame_height * margin
        or x2 >= frame_width * (1.0 - margin)
        or y2 >= frame_height * (1.0 - margin)
    )
    failures: list[str] = []
    if box_w < float(settings.person_revalidator_block_min_bbox_width_px):
        failures.append("bbox_width_too_small")
    if box_h < float(settings.person_revalidator_block_min_bbox_height_px):
        failures.append("bbox_height_too_small")
    if area_ratio < float(settings.person_revalidator_block_min_bbox_area_ratio):
        failures.append("bbox_area_too_small")
    if near_border:
        failures.append("bbox_near_border")
    return {
        "quality_gate_passed": not failures,
        "quality_reason": "ok" if not failures else ",".join(failures),
        "frame_width": frame_width,
        "frame_height": frame_height,
        "bbox_width": round(box_w, 2),
        "bbox_height": round(box_h, 2),
        "bbox_area_ratio": round(area_ratio, 6),
        "crop_width": round(box_w * (1 + float(settings.person_revalidator_margin_pct) * 2), 2),
        "crop_height": round(box_h * (1 + float(settings.person_revalidator_margin_pct) * 2), 2),
        "near_border": near_border,
    }


def region_cell(camera_id: int, bbox: list[float] | None, frame_width: int, frame_height: int) -> str | None:
    if not bbox:
        return None
    cols = max(1, int(settings.region_memory_grid_cols or 8))
    rows = max(1, int(settings.region_memory_grid_rows or 6))
    cx = ((bbox[0] + bbox[2]) / 2.0) / max(1.0, float(frame_width))
    cy = ((bbox[1] + bbox[3]) / 2.0) / max(1.0, float(frame_height))
    cell_x = min(cols - 1, max(0, int(cx * cols)))
    cell_y = min(rows - 1, max(0, int(cy * rows)))
    return f"{camera_id}:{cell_x:02d}:{cell_y:02d}"


def build_region_memory_for_row(target: dict[str, Any], all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    target_cell = target.get("_region_cell")
    if not target_cell:
        return {"risk_level": "UNKNOWN", "decision_hint": "missing_region_cell"}
    fp = tp = uncertain = 0
    samples: list[int] = []
    for row in all_rows:
        if row is target or row.get("_region_cell") != target_cell:
            continue
        truth = row.get("_truth_class")
        if truth == "not_person":
            fp += 1
        elif truth == "person":
            tp += 1
        else:
            uncertain += 1
        if len(samples) < 12:
            samples.append(int(row.get("event_id") or 0))
    total = fp + tp + uncertain
    fp_rate = fp / total if total else 0.0
    tp_rate = tp / total if total else 0.0
    if fp >= int(settings.region_memory_green_min_false_positive_count) and fp_rate >= float(settings.region_memory_high_fp_rate_threshold):
        risk_level = "GREEN"
        hint = "recurrent_false_positive_region"
    elif tp > 0 and (tp_rate >= float(settings.region_memory_person_support_rate_threshold) or tp > fp):
        risk_level = "RED"
        hint = "person_seen_in_region"
    elif fp > 0:
        risk_level = "YELLOW"
        hint = "some_false_positive_history"
    else:
        risk_level = "UNKNOWN"
        hint = "no_region_history"
    return {
        "enabled": True,
        "region_cell": target_cell,
        "false_positive_count": fp,
        "true_positive_count": tp,
        "uncertain_count": uncertain,
        "total_reviewed_count": total,
        "false_positive_rate": round(fp_rate, 6),
        "true_positive_rate": round(tp_rate, 6),
        "risk_level": risk_level,
        "decision_hint": hint,
        "sample_event_ids": samples,
    }


def summarize(rows: list[ReplayRow]) -> dict[str, Any]:
    by_truth: dict[str, Any] = {}
    for truth in ["person", "not_person", "uncertain"]:
        subset = [r for r in rows if r.truth_class == truth]
        by_truth[truth] = {
            "total": len(subset),
            "strategy_decision": dict(Counter(r.strategy_decision for r in subset)),
            "anti_fp_decision": dict(Counter(r.anti_fp_decision for r in subset)),
            "runtime_action": dict(Counter(r.simulated_runtime_action for r in subset)),
        }
    person_total = by_truth["person"]["total"]
    not_person_total = by_truth["not_person"]["total"]
    person_suppressed = by_truth["person"]["anti_fp_decision"].get("SUPPRESS", 0)
    not_person_notify = by_truth["not_person"]["anti_fp_decision"].get("NOTIFY", 0)
    not_person_non_notify = not_person_total - not_person_notify
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(rows),
        "by_truth": by_truth,
        "key_metrics": {
            "person_total": person_total,
            "person_suppressed": person_suppressed,
            "person_not_suppressed": person_total - person_suppressed,
            "person_not_suppressed_rate": (person_total - person_suppressed) / person_total if person_total else 0.0,
            "not_person_total": not_person_total,
            "not_person_notify": not_person_notify,
            "not_person_notify_rate": not_person_notify / not_person_total if not_person_total else 0.0,
            "not_person_non_notify": not_person_non_notify,
            "not_person_non_notify_rate": not_person_non_notify / not_person_total if not_person_total else 0.0,
            "consensus_block_person": sum(1 for r in rows if r.truth_class == "person" and r.consensus_block_candidate),
            "consensus_block_not_person": sum(1 for r in rows if r.truth_class == "not_person" and r.consensus_block_candidate),
            "direct_ia2_block_person": sum(1 for r in rows if r.truth_class == "person" and r.direct_ia2_block_candidate),
            "direct_ia2_block_not_person": sum(1 for r in rows if r.truth_class == "not_person" and r.direct_ia2_block_candidate),
        },
    }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.reviewed_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))

    for row in source_rows:
        bbox = parse_bbox(row.get("bbox_json"))
        frame_width, frame_height = infer_frame_size(bbox, as_float(row.get("bbox_area_ratio_proxy")))
        row["_bbox"] = bbox
        row["_frame_width"] = frame_width
        row["_frame_height"] = frame_height
        row["_truth_class"] = truth_from_label(row.get("feedback_label_normalized") or row.get("feedback_label"))
        row["_region_cell"] = region_cell(int(row.get("camera_id") or 0), bbox, frame_width, frame_height)

    patterns = load_anti_fp_patterns()
    replay_rows: list[ReplayRow] = []
    for row in source_rows:
        truth = row["_truth_class"]
        if truth == "uncertain":
            continue
        bbox = row["_bbox"]
        frame_width = int(row["_frame_width"])
        frame_height = int(row["_frame_height"])
        ia2_person = as_float(row.get("ia2_person_score"))
        ia2_not = 1.0 - ia2_person if ia2_person is not None else None
        ia3_person = as_float(row.get("ia3_person_score"))
        ia3_not = 1.0 - ia3_person if ia3_person is not None else None
        quality = quality_for_bbox(bbox, frame_width, frame_height)
        ia2 = SimpleNamespace(
            enabled=True,
            applied=ia2_person is not None,
            person_score=ia2_person,
            not_person_score=ia2_not,
            passed=ia2_person is not None and ia2_person >= float(settings.person_revalidator_threshold),
            threshold=float(settings.person_revalidator_threshold),
            mode=str(settings.person_revalidator_mode),
            block_eligible=bool(
                ia2_person is not None
                and ia2_not is not None
                and quality.get("quality_gate_passed")
                and ia2_person < float(settings.person_revalidator_block_person_threshold)
                and ia2_not >= float(settings.person_revalidator_block_not_person_threshold)
            ),
            quality=quality,
        )
        ia3 = SimpleNamespace(
            enabled=True,
            triggered=ia3_person is not None,
            applied=ia3_person is not None,
            person_far_score=ia3_person,
            not_person_far_score=ia3_not,
            passed=ia3_person is not None and ia3_person >= float(settings.far_person_revalidator_threshold),
            threshold=float(settings.far_person_revalidator_threshold),
            quality=quality,
        )
        region_memory = build_region_memory_for_row(row, source_rows)
        payload = build_strategy3_v2_review_payload(
            ia2_result=ia2,
            ia3_result=ia3,
            detector_score=as_float(row.get("ia1_detector_score")) or as_float(row.get("event_score")) or 0.0,
            bbox=bbox,
            frame_width=frame_width,
            frame_height=frame_height,
            camera_id=int(row.get("camera_id") or 0),
            track=None,
            timestamp=None,
            event=None,
            region_memory=region_memory,
            anti_fp_patterns=patterns,
        )
        anti_fp = payload.get("anti_fp_post_filter") or {}
        consensus = evaluate_consensus_block_candidate(ia2, ia3)
        alarm = decide_alarm_action(
            event_maturity={"level": "ALARM_READY", "decision": "alarm_candidate", "safety": {}},
            ia2_result=ia2,
            ia3_result=ia3,
            consensus_result=consensus,
            strategy3_v2_result=payload,
            anti_fp_post_filter_result=anti_fp,
        )
        runtime_action = str(alarm.get("action") or "")
        if bool(consensus.get("block_candidate")) or bool(getattr(ia2, "block_eligible", False)):
            runtime_action = "BLOCK_AUTO"
        replay_rows.append(
            ReplayRow(
                event_id=int(row.get("event_id") or 0),
                camera_id=int(row.get("camera_id") or 0),
                camera_name=str(row.get("camera_name") or ""),
                truth_class=truth,
                ia1_detector_score=as_float(row.get("ia1_detector_score")),
                ia2_person_score=ia2_person,
                ia2_not_person_score=ia2_not,
                ia3_person_score=ia3_person,
                ia3_not_person_score=ia3_not,
                frame_width=frame_width,
                frame_height=frame_height,
                bbox_height_ratio=float(payload.get("bbox_height_ratio") or 0.0),
                size_bucket=str(payload.get("size_bucket") or ""),
                region_risk=str(payload.get("region_fp_risk") or ""),
                strategy_decision=str(payload.get("decision") or ""),
                strategy_reason=str(payload.get("reason") or ""),
                anti_fp_decision=str(anti_fp.get("decision") or ""),
                anti_fp_reason=str(anti_fp.get("reason") or ""),
                final_notification_level=str(payload.get("final_notification_level") or ""),
                consensus_block_candidate=bool(consensus.get("block_candidate")),
                direct_ia2_block_candidate=bool(getattr(ia2, "block_eligible", False)),
                simulated_runtime_action=runtime_action,
            )
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = args.output_dir / f"current_rules_replay_{stamp}.csv"
    summary_path = args.output_dir / f"current_rules_replay_summary_{stamp}.json"
    md_path = args.output_dir / f"current_rules_replay_summary_{stamp}.md"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ReplayRow.__dataclass_fields__.keys()))
        writer.writeheader()
        for replay_row in replay_rows:
            writer.writerow(asdict(replay_row))

    summary = summarize(replay_rows)
    summary["source_csv"] = str(args.reviewed_csv)
    summary["outputs"] = {"csv": str(csv_path), "summary_json": str(summary_path), "summary_md": str(md_path)}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Replay regras atuais IA2/IA3",
        "",
        f"- Fonte: `{args.reviewed_csv}`",
        f"- Eventos comparaveis: `{summary['total']}`",
        "",
        "## Metricas principais",
        "",
    ]
    for key, value in summary["key_metrics"].items():
        if isinstance(value, float):
            lines.append(f"- {key}: `{value:.2%}`")
        else:
            lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Decisao por classe", ""])
    for truth, data in summary["by_truth"].items():
        lines.append(f"### {truth}")
        lines.append(f"- total: `{data['total']}`")
        lines.append(f"- anti_fp_decision: `{json.dumps(data['anti_fp_decision'], ensure_ascii=False)}`")
        lines.append(f"- runtime_action: `{json.dumps(data['runtime_action'], ensure_ascii=False)}`")
    lines.extend(["", "## Arquivos", f"- CSV: `{csv_path}`", f"- JSON: `{summary_path}`"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary["key_metrics"], ensure_ascii=False, indent=2))
    print(f"CSV: {csv_path}")
    print(f"Resumo: {summary_path}")
    print(f"Relatorio: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
