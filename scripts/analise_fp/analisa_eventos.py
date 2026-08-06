#!/usr/bin/env python3
"""Le os audit_pending_event_*_event.json e consolida em CSV + resumo estatistico."""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(r"D:\Onedrive\OneDrive - Office365(a)\Aplicativos\Analitico VMS Clips")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eventos.csv")
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 0


def g(d, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def parse_details(details: str) -> dict:
    """details e uma string 'k=v k=v | k=v'."""
    out = {}
    for token in re.findall(r"(\w+)=([^\s|]+)", str(details or "")):
        out[token[0]] = token[1]
    return out


ROWS = []
files = sorted(BASE.glob("audit_pending_event_*_event.json"))
if LIMIT:
    files = files[:LIMIT]
print(f"arquivos: {len(files)}", flush=True)

t0 = time.time()
errors = 0
for i, path in enumerate(files, 1):
    if i % 250 == 0:
        print(f"  {i}/{len(files)}  ({time.time()-t0:.0f}s)", flush=True)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        errors += 1
        continue

    ev = d.get("event") or {}
    rm = d.get("raw_metadata") or {}
    s3 = rm.get("strategy3_v2") or {}
    afp = rm.get("anti_fp_post_filter") or {}
    mat = rm.get("event_maturity") or {}
    feat = mat.get("features") or {}
    safety = mat.get("safety") or {}
    dec = rm.get("alarm_decision") or {}
    ia2 = rm.get("person_revalidator") or {}
    ia3 = rm.get("far_person_revalidator") or {}
    cons = rm.get("consensus_revalidator") or {}
    sess = rm.get("alarm_session") or {}
    vq = rm.get("visual_quality") or {}
    bbox = g(d, "evidence", "bbox") or []
    det = parse_details(ev.get("details"))

    x1, y1, x2, y2 = (list(bbox) + [None] * 4)[:4]
    ROWS.append({
        "event_id": ev.get("id"),
        "camera_id": ev.get("camera_id"),
        "event_type": ev.get("event_type"),
        "rule_id": ev.get("rule_id"),
        "track_id": ev.get("track_id"),
        "severity": ev.get("severity"),
        "status": ev.get("status"),
        "final_status": rm.get("final_status"),
        "lifecycle_action": ev.get("lifecycle_action"),
        "alarm_eligible": ev.get("alarm_eligible"),
        "is_alarm_active": ev.get("is_alarm_active"),
        "created_at": ev.get("created_at"),
        "started_at": ev.get("started_at"),
        "ended_at": ev.get("ended_at"),
        "event_score": ev.get("event_score"),
        "detector_score": ev.get("detector_score"),
        "correlation_key": ev.get("correlation_key"),
        # geometria
        "bbox_x1": x1, "bbox_y1": y1, "bbox_x2": x2, "bbox_y2": y2,
        "bbox_w": (x2 - x1) if None not in (x1, x2) else None,
        "bbox_h": (y2 - y1) if None not in (y1, y2) else None,
        "zone_type": rm.get("zone_type"),
        "full_frame_mode": rm.get("full_frame_mode"),
        "near_border": rm.get("near_border"),
        "border_score": rm.get("border_score"),
        "geometry_confidence": rm.get("geometry_confidence"),
        "dwell_ms": rm.get("dwell_ms"),
        "zone_streak": rm.get("zone_streak"),
        "track_quality": rm.get("track_quality"),
        "camera_family": rm.get("camera_family"),
        "scene_profile": rm.get("scene_profile"),
        # IA2 / IA3
        "ia2_person": ia2.get("person_score"),
        "ia2_not_person": ia2.get("not_person_score"),
        "ia2_passed": ia2.get("passed"),
        "ia2_applied": ia2.get("applied"),
        "ia2_mode": ia2.get("mode"),
        "ia2_threshold": ia2.get("threshold"),
        "ia3_person_far": ia3.get("person_far_score"),
        "ia3_not_person_far": ia3.get("not_person_far_score"),
        "ia3_applied": ia3.get("applied"),
        "ia3_passed": ia3.get("passed"),
        # consenso
        "consensus_block_candidate": cons.get("block_candidate"),
        "consensus_balanced_candidate": cons.get("balanced_block_candidate"),
        "consensus_block_applied": cons.get("block_applied"),
        "consensus_profile": cons.get("block_profile"),
        # strategy3 v2
        "s3_decision": s3.get("decision"),
        "s3_initial": s3.get("initial_decision"),
        "s3_reason": s3.get("reason"),
        "s3_size_bucket": s3.get("size_bucket"),
        "s3_bbox_height_ratio": s3.get("bbox_height_ratio"),
        "s3_independent_confirmation": s3.get("independent_confirmation"),
        "s3_tracking_confirmed": s3.get("tracking_confirmed"),
        "s3_temporal_persistence": s3.get("temporal_persistence"),
        "s3_static_track": s3.get("static_track"),
        "s3_fast_motion_protected": s3.get("fast_motion_protected"),
        "s3_human_motion_score": s3.get("human_motion_score"),
        "s3_region_fp_risk": s3.get("region_fp_risk"),
        "s3_blacklist": s3.get("pattern_blacklist_match"),
        "s3_whitelist": s3.get("pattern_whitelist_match"),
        "s3_mode": s3.get("mode"),
        "s3_track_age_frames": g(s3, "tracking", "track_age_frames"),
        "s3_recent_motion_px": g(s3, "tracking", "recent_motion_distance_px"),
        "s3_visible_frames": g(s3, "temporal", "visible_frames"),
        "s3_duration_seconds": g(s3, "temporal", "duration_seconds"),
        "s3_direction_consistency": g(s3, "human_motion", "direction_consistency"),
        "s3_displacement_norm": g(s3, "human_motion", "center_displacement_norm"),
        # anti-fp post filter
        "afp_decision": afp.get("decision"),
        "afp_reason": afp.get("reason"),
        "afp_risk_score": afp.get("risk_score"),
        "afp_level": afp.get("final_notification_level"),
        "afp_mode": afp.get("mode"),
        "final_notification_level": rm.get("final_notification_level"),
        # maturidade
        "mat_score": mat.get("score"),
        "mat_level": mat.get("level"),
        "mat_decision": mat.get("decision"),
        "mat_reason": mat.get("reason"),
        "mat_visible_frames": feat.get("visible_frames"),
        "mat_duration_s": feat.get("duration_seconds"),
        "mat_displacement_norm": feat.get("center_displacement_norm"),
        "mat_area_change": feat.get("bbox_area_change_ratio"),
        "mat_avg_detector": feat.get("avg_detector_score"),
        "mat_best_detector": feat.get("best_detector_score"),
        "mat_class_consistency": feat.get("class_consistency"),
        "mat_motion_blobs_median": feat.get("motion_blobs_median"),
        "mat_motion_area_median": feat.get("motion_area_pct_median"),
        "mat_motion_has_mask": feat.get("motion_confirm_has_mask"),
        "mat_motion_passed": feat.get("motion_confirm_passed"),
        "mat_static_track": safety.get("static_track"),
        "mat_fast_motion_protected": safety.get("fast_motion_protected"),
        "mat_camera_motion_possible": safety.get("camera_motion_possible"),
        "mat_camera_motion_uncertain": safety.get("camera_motion_uncertain"),
        "mat_best_frame_protects": safety.get("best_frame_protects_from_suppression"),
        # decisao final
        "decision_action": dec.get("action"),
        "decision_status": dec.get("suggested_status"),
        "decision_reason": dec.get("reason"),
        "decision_mode": dec.get("mode"),
        "decision_applied": dec.get("applied"),
        # sessao
        "session_decision": rm.get("alarm_session_decision"),
        "session_key": rm.get("alarm_session_key"),
        "session_reason": sess.get("reason"),
        "session_event_count": sess.get("event_count"),
        "session_track_count": sess.get("track_count"),
        # qualidade visual
        "vq_has_artifact": vq.get("has_artifact"),
        "vq_artifact_reason": vq.get("artifact_reason"),
        # revalidator cancelado
        "revalidator_canceled": rm.get("revalidator_canceled"),
        "revalidator_cancel_reason": rm.get("revalidator_cancel_reason"),
        "consensus_revalidator_canceled": rm.get("consensus_revalidator_canceled"),
        "det_revalidator_person": det.get("revalidator_person"),
        "det_far_revalidator_person": det.get("far_revalidator_person"),
        "snapshot_file": f"audit_pending_event_{ev.get('id')}_snapshot.jpg",
        "clip_file": f"audit_pending_event_{ev.get('id')}_clip.mp4",
    })

print(f"lidos={len(ROWS)} erros={errors} tempo={time.time()-t0:.0f}s", flush=True)

if ROWS:
    cols = list(ROWS[0].keys())
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(ROWS)
    print(f"CSV -> {OUT.resolve()}")
