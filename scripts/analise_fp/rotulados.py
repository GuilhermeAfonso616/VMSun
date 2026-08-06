#!/usr/bin/env python3
"""Cruza event_feedback (rotulo humano) com os sinais gravados em events.details.

Objetivo: medir o poder discriminante de cada sinal -> quanto FP some, quanto TP se perde.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(r"D:\Onedrive\OneDrive - Office365(a)\Aplicativos\Analitico VMS Clips")
DB = BASE / "server_backup_20260706_114352__backups__analytics_backup_20260610_153404__analytics.db"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

rows = con.execute(
    """
    SELECT e.*, f.label, f.probable_cause, f.reviewed_at, f.operator_note
    FROM events e
    JOIN event_feedback f ON f.event_id = e.id
    WHERE f.label IN ('false_positive','true_positive')
    """
).fetchall()
print(f"eventos rotulados (FP/TP): {len(rows)}")

TOKEN = re.compile(r"(\w+)=([^\s|]+)")


def parse_details(details: str) -> dict:
    out: dict[str, str] = {}
    for k, v in TOKEN.findall(str(details or "")):
        # 'threshold' aparece 2x (ia2 e ia3); qualifica pelo contexto
        if k in out:
            k = k + "_2"
        out[k] = v
    return out


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


DATA = []
for r in rows:
    d = parse_details(r["details"])
    bbox = None
    try:
        bbox = json.loads(r["bbox_json"]) if r["bbox_json"] else None
    except Exception:
        bbox = None
    bw = bh = None
    if bbox and len(bbox) == 4:
        bw = float(bbox[2]) - float(bbox[0])
        bh = float(bbox[3]) - float(bbox[1])
    hour = None
    try:
        hour = int(str(r["created_at"])[11:13])
    except Exception:
        pass
    dur = None
    try:
        from datetime import datetime
        def p(s):
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(str(s), fmt)
                except Exception:
                    continue
            return None
        a, b_ = p(r["started_at"]), p(r["ended_at"])
        if a and b_:
            dur = (b_ - a).total_seconds()
    except Exception:
        pass

    DATA.append({
        "event_id": r["id"],
        "camera_id": r["camera_id"],
        "event_type": r["event_type"],
        "label": r["label"],
        "is_fp": r["label"] == "false_positive",
        "probable_cause": r["probable_cause"],
        "severity": r["severity"],
        "status": r["status"],
        "created_at": r["created_at"],
        "hour": hour,
        "duration_s": dur,
        "detector_score": fnum(r["detector_score"]),
        "event_score": fnum(r["event_score"]),
        "confidence": fnum(r["confidence"]),
        "camera_family": r["camera_family"],
        "scene_profile": r["scene_profile"],
        "bbox_w": bw,
        "bbox_h": bh,
        "bbox_area": (bw * bh) if bw and bh else None,
        "aspect": (bw / bh) if bw and bh else None,
        "ia2_person": fnum(d.get("revalidator_person")),
        "ia3_person_far": fnum(d.get("far_revalidator_person")),
        "maturity_score": fnum(d.get("maturity_score")),
        "maturity_level": d.get("maturity_level"),
        "maturity_decision": d.get("maturity_decision"),
        "maturity_reason": d.get("maturity_reason"),
        "s3_decision": d.get("strategy3_v2_decision"),
        "s3_notify": d.get("strategy3_v2_notify"),
        "fast_motion": d.get("maturity_fast_motion"),
        "camera_motion": d.get("maturity_camera_motion"),
        "alarm_action": d.get("alarm_decision_action"),
        "alarm_reason": d.get("alarm_decision_reason"),
        "final_level": d.get("final_notification_level"),
        "session_decision": d.get("alarm_session_decision"),
        "motion_passed": d.get("motion_confirm_passed"),
        "motion_blobs": fnum(d.get("motion_blobs_median")),
        "motion_area": fnum(d.get("motion_area_pct_median")),
        "motion_signal": d.get("motion_confirm_signal"),
        "motion_disp": fnum(d.get("motion_confirm_displacement_norm")),
        "explanation_head": str(r["details"] or "").split("|")[0].strip(),
    })

out = Path("rotulados.csv")
cols = list(DATA[0].keys())
with out.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(DATA)
print(f"CSV -> {out.resolve()}")

FP = [d for d in DATA if d["is_fp"]]
TP = [d for d in DATA if not d["is_fp"]]
print(f"\nFP={len(FP)}  TP={len(TP)}  taxa_FP={100*len(FP)/len(DATA):.1f}%\n")

# cobertura dos campos
print("=== COBERTURA DOS SINAIS ===")
for k in ("ia2_person", "ia3_person_far", "maturity_score", "s3_decision", "motion_blobs", "bbox_h", "duration_s"):
    n = sum(1 for d in DATA if d[k] is not None)
    print(f"  {k:20s} {n:5d}/{len(DATA)}  ({100*n/len(DATA):.0f}%)")
print()


def dist_by_label(key, top=12):
    print(f"--- {key}: distribuicao FP vs TP ---")
    keys = Counter(str(d[key]) for d in DATA)
    print(f"  {'valor':38s} {'n':>6} {'FP':>6} {'TP':>6} {'%FP':>7}")
    for k, n in keys.most_common(top):
        sub = [d for d in DATA if str(d[key]) == k]
        nfp = sum(1 for d in sub if d["is_fp"])
        print(f"  {k[:38]:38s} {n:6d} {nfp:6d} {n-nfp:6d} {100*nfp/n:6.1f}%")
    print()


for k in ("maturity_level", "s3_decision", "alarm_action", "final_level", "maturity_reason",
          "fast_motion", "camera_motion", "motion_signal", "severity", "camera_family", "scene_profile"):
    dist_by_label(k)


def threshold_sweep(key, points, direction="below"):
    """Se suprimirmos tudo com key <threshold (ou >), quanto FP corta e quanto TP perde."""
    vals = [(d[key], d["is_fp"]) for d in DATA if d[key] is not None]
    if not vals:
        print(f"  {key}: sem dados\n")
        return
    nfp = sum(1 for _, fp in vals if fp)
    ntp = len(vals) - nfp
    print(f"--- corte por {key} ({direction}) | base: {len(vals)} eventos, {nfp} FP, {ntp} TP ---")
    print(f"  {'corte':>10} {'FP cortados':>12} {'%FP':>7} {'TP perdidos':>12} {'%TP':>7}  ganho")
    for t in points:
        if direction == "below":
            hit = [(v, fp) for v, fp in vals if v < t]
        else:
            hit = [(v, fp) for v, fp in vals if v > t]
        hfp = sum(1 for _, fp in hit if fp)
        htp = len(hit) - hfp
        ratio = (hfp / htp) if htp else float("inf")
        print(f"  {t:10.3f} {hfp:12d} {100*hfp/nfp:6.1f}% {htp:12d} {100*htp/ntp:6.1f}%  {ratio:6.1f} FP/TP")
    print()


print("=== VARREDURA DE CORTES ===")
threshold_sweep("ia2_person", [0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99])
threshold_sweep("ia3_person_far", [0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7])
threshold_sweep("maturity_score", [0.4, 0.5, 0.55, 0.6, 0.65, 0.7])
threshold_sweep("detector_score", [0.5, 0.6, 0.65, 0.7, 0.75, 0.8])
threshold_sweep("bbox_h", [10, 20, 30, 40, 60, 80, 120])
threshold_sweep("duration_s", [1, 2, 3, 5, 8, 12])
threshold_sweep("motion_blobs", [1, 2, 3, 5])
threshold_sweep("bbox_area", [500, 1000, 2000, 5000, 10000])
threshold_sweep("aspect", [1.0, 1.5, 2.0], direction="above")

print("=== POR HORA DO DIA ===")
print(f"  {'hora':>5} {'n':>6} {'FP':>6} {'%FP':>7}")
for h in range(24):
    sub = [d for d in DATA if d["hour"] == h]
    if not sub:
        continue
    nfp = sum(1 for d in sub if d["is_fp"])
    print(f"  {h:5d} {len(sub):6d} {nfp:6d} {100*nfp/len(sub):6.1f}%")
print()

print("=== POR CAMERA (>=20 eventos rotulados) ===")
print(f"  {'cam':>5} {'n':>6} {'FP':>6} {'%FP':>7} {'ia2_med_FP':>11} {'ia2_med_TP':>11} {'bboxh_med_FP':>13} {'bboxh_med_TP':>13}")
cams = Counter(d["camera_id"] for d in DATA)
def med(vals):
    vals = sorted(v for v in vals if v is not None)
    return vals[len(vals)//2] if vals else float("nan")
for cam, n in cams.most_common():
    if n < 20:
        continue
    sub = [d for d in DATA if d["camera_id"] == cam]
    fp = [d for d in sub if d["is_fp"]]
    tp = [d for d in sub if not d["is_fp"]]
    print(f"  {cam:5d} {n:6d} {len(fp):6d} {100*len(fp)/n:6.1f}% "
          f"{med(d['ia2_person'] for d in fp):11.3f} {med(d['ia2_person'] for d in tp):11.3f} "
          f"{med(d['bbox_h'] for d in fp):13.1f} {med(d['bbox_h'] for d in tp):13.1f}")
print()

print("=== CAUSAS DECLARADAS DE FP ===")
for k, n in Counter(d["probable_cause"] for d in FP if d["probable_cause"]).most_common():
    print(f"  {n:5d}  {k}")
print()

print("=== NOTAS DE OPERADOR (amostra FP) ===")
notes = [d for d in DATA if d["is_fp"]]
seen = 0
for r in rows:
    if r["label"] == "false_positive" and r["operator_note"]:
        print(f"  #{r['id']}: {str(r['operator_note'])[:160]}")
        seen += 1
        if seen >= 15:
            break
