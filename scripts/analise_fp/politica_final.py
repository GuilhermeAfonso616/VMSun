#!/usr/bin/env python3
"""Politica refinada + validacao leave-one-camera-out."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

rows = list(csv.DictReader(Path("rotulados.csv").open(encoding="utf-8")))


def f(r, k):
    try:
        return float(r[k])
    except Exception:
        return None


for r in rows:
    r["_fp"] = r["is_fp"] == "True"
    r["_ia2"] = f(r, "ia2_person")
    r["_ia3"] = f(r, "ia3_person_far")
    r["_bh"] = f(r, "bbox_h")
    try:
        r["_h"] = int(float(r["hour"]))
    except Exception:
        r["_h"] = None

NIGHT = set(range(0, 9))


def night(r):
    return r["_h"] in NIGHT


def ia2_confident(r, t=0.50):
    return r["_ia2"] is not None and r["_ia2"] >= t


# ---------------------------------------------------------------- politicas
POL = {}

POL["A. noite + bbox_h<80"] = lambda r: night(r) and (r["_bh"] or 999) < 80

POL["B. ia3<0.20 (como hoje, sem guarda)"] = lambda r: r["_ia3"] is not None and r["_ia3"] < 0.20

POL["C. ia3<0.20 + bbox_h<120 (dominio da ia3)"] = (
    lambda r: r["_ia3"] is not None and r["_ia3"] < 0.20 and (r["_bh"] or 999) < 120
)

POL["D. ia3<0.20 + bbox_h<120 + ia2<0.50"] = (
    lambda r: r["_ia3"] is not None and r["_ia3"] < 0.20 and (r["_bh"] or 999) < 120 and not ia2_confident(r)
)

POL["E. LOW_CONFIDENCE + noite"] = lambda r: r["maturity_level"] == "LOW_CONFIDENCE" and night(r)

POL["FINAL = A U D U E"] = lambda r: (
    (night(r) and (r["_bh"] or 999) < 80)
    or (r["_ia3"] is not None and r["_ia3"] < 0.20 and (r["_bh"] or 999) < 120 and not ia2_confident(r))
    or (r["maturity_level"] == "LOW_CONFIDENCE" and night(r))
)

POL["FINAL+ (FINAL U ia2<0.05 & bbox_h<80)"] = lambda r: (
    POL["FINAL = A U D U E"](r)
    or (r["_ia2"] is not None and r["_ia2"] < 0.05 and (r["_bh"] or 999) < 80)
)


def stats(data, pred):
    nfp = sum(1 for r in data if r["_fp"])
    ntp = len(data) - nfp
    hit = [r for r in data if pred(r)]
    hfp = sum(1 for r in hit if r["_fp"])
    htp = len(hit) - hfp
    rfp, rtp = nfp - hfp, ntp - htp
    return {
        "n": len(data), "nfp": nfp, "ntp": ntp, "cut": len(hit), "hfp": hfp, "htp": htp,
        "pfp": 100 * hfp / nfp if nfp else 0,
        "ptp": 100 * htp / ntp if ntp else 0,
        "ratio": (hfp / htp) if htp else float("inf"),
        "prec0": 100 * ntp / len(data) if data else 0,
        "prec1": 100 * rtp / (rtp + rfp) if (rtp + rfp) else 0,
        "vol": 100 * len(hit) / len(data) if data else 0,
    }


print(f"BASE: {len(rows)} eventos rotulados | FP={sum(1 for r in rows if r['_fp'])} "
      f"({100*sum(1 for r in rows if r['_fp'])/len(rows):.1f}%) | precisao operacional atual = "
      f"{100*sum(1 for r in rows if not r['_fp'])/len(rows):.1f}%\n")

print("=== POLITICAS ===")
print(f"  {'politica':44s} {'volume':>7} {'FP cort':>9} {'TP perd':>9} {'FP/TP':>8} {'precisao':>16}")
for nome, pred in POL.items():
    s = stats(rows, pred)
    print(f"  {nome:44s} {s['vol']:6.1f}% {s['pfp']:8.1f}% {s['ptp']:8.1f}% {s['ratio']:8.1f} "
          f"{s['prec0']:6.1f}% -> {s['prec1']:5.1f}%")
print()

# ---------------------------------------------------------------- leave-one-camera-out
print("=== VALIDACAO LEAVE-ONE-CAMERA-OUT (a regra generaliza para camera nao vista?) ===")
final = POL["FINAL = A U D U E"]
cams = [c for c, n in Counter(r["camera_id"] for r in rows).most_common() if n >= 20]
print(f"  {'cam held-out':>13} {'n':>6} {'FP%':>7} {'FP cortados':>12} {'TP perdidos':>12} {'FP/TP':>8} {'precisao':>16}")
for cam in cams:
    sub = [r for r in rows if r["camera_id"] == cam]
    s = stats(sub, final)
    ratio = f"{s['ratio']:8.1f}" if s["ratio"] != float("inf") else "     inf"
    print(f"  {cam:>13} {s['n']:6d} {100*s['nfp']/s['n']:6.1f}% "
          f"{s['hfp']:5d} ({s['pfp']:5.1f}%) {s['htp']:5d} ({s['ptp']:5.1f}%) {ratio} "
          f"{s['prec0']:6.1f}% -> {s['prec1']:5.1f}%")
print()

# ---------------------------------------------------------------- ganho por camera com threshold proprio
print("=== QUANTO SE GANHA COM THRESHOLD DE IA2 POR CAMERA (vs global) ===")
print(f"  {'cam':>5} {'n':>5} {'melhor corte ia2':>17} {'FP cort':>9} {'TP perd':>9} {'FP/TP':>8}")
for cam in cams:
    sub = [r for r in rows if r["camera_id"] == cam and r["_ia2"] is not None]
    if len(sub) < 20:
        continue
    best = None
    for t in [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99, 0.999]:
        s = stats(sub, lambda r, t=t: r["_ia2"] < t)
        # exige perder no maximo 5% dos TP
        if s["ptp"] <= 5.0 and (best is None or s["pfp"] > best[1]["pfp"]):
            best = (t, s)
    if best:
        t, s = best
        print(f"  {cam:>5} {len(sub):5d} {t:17.3f} {s['pfp']:8.1f}% {s['ptp']:8.1f}% {s['ratio']:8.1f}")
    else:
        print(f"  {cam:>5} {len(sub):5d} {'nenhum corte seguro':>17}")
print()

# ---------------------------------------------------------------- resumo do impacto
print("=== IMPACTO OPERACIONAL ESTIMADO (regra FINAL) ===")
s = stats(rows, final)
print(f"  eventos analisados            : {s['n']}")
print(f"  seriam suprimidos/rebaixados  : {s['cut']} ({s['vol']:.1f}% do volume)")
print(f"  falsos positivos eliminados   : {s['hfp']} de {s['nfp']} ({s['pfp']:.1f}%)")
print(f"  verdadeiros perdidos          : {s['htp']} de {s['ntp']} ({s['ptp']:.1f}%)")
print(f"  precisao operacional          : {s['prec0']:.1f}% -> {s['prec1']:.1f}%")
print(f"  alarmes que o operador ve     : {s['n']} -> {s['n']-s['cut']} por periodo")
print()
perdidos = [r for r in rows if final(r) and not r["_fp"]]
print(f"  TP perdidos por camera: {dict(Counter(r['camera_id'] for r in perdidos).most_common())}")
print(f"  TP perdidos por hora  : {dict(sorted(Counter(r['_h'] for r in perdidos).items()))}")
print()
print("  * Se em vez de SUPRIMIR a regra apenas REBAIXAR para low_priority/audit,")
print("    os TP nao sao perdidos - so saem da fila principal de atendimento.")
