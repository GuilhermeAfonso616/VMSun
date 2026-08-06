#!/usr/bin/env python3
"""1) IA3 vale a pena fora do gate atual?  2) validacao temporal das politicas."""
from __future__ import annotations

import csv
import random
from collections import Counter, defaultdict
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
    r["_bw"] = f(r, "bbox_w")
    try:
        r["_h"] = int(float(r["hour"]))
    except Exception:
        r["_h"] = None
    r["_date"] = str(r["created_at"])[:10]

NIGHT = set(range(0, 9))

print("=== 1. ONDE A IA3 RODA HOJE (gate: bbox_height_ratio <= 0.08 ou ia2 suspeita) ===")
com = [r for r in rows if r["_ia3"] is not None]
sem = [r for r in rows if r["_ia3"] is None]
print(f"  com IA3: {len(com)} ({100*len(com)/len(rows):.0f}%)  |  sem IA3: {len(sem)}")
print()
print(f"  {'faixa bbox_h':>16} {'total':>7} {'com IA3':>9} {'cobert.':>8} {'FP%':>7}")
faixas = [(0, 40), (40, 60), (60, 80), (80, 120), (120, 200), (200, 400), (400, 9999)]
for lo, hi in faixas:
    sub = [r for r in rows if r["_bh"] is not None and lo <= r["_bh"] < hi]
    if not sub:
        continue
    ci = [r for r in sub if r["_ia3"] is not None]
    nfp = sum(1 for r in sub if r["_fp"])
    print(f"  {lo:>6}-{hi:<9} {len(sub):7d} {len(ci):9d} {100*len(ci)/len(sub):7.0f}% {100*nfp/len(sub):6.1f}%")
print()


def auc(data, key, samples=30000):
    fps = [r[key] for r in data if r["_fp"] and r[key] is not None]
    tps = [r[key] for r in data if not r["_fp"] and r[key] is not None]
    if len(fps) < 5 or len(tps) < 5:
        return None, len(fps), len(tps)
    random.seed(0)
    w = t = 0
    for _ in range(samples):
        a, b = random.choice(tps), random.choice(fps)
        if a > b:
            w += 1
        elif a == b:
            t += 1
    return (w + 0.5 * t) / samples, len(fps), len(tps)


print("  Poder discriminante da IA3 NAS FAIXAS EM QUE ELA RODOU:")
print(f"  {'faixa bbox_h':>16} {'n_com_ia3':>10} {'AUC ia3':>9} {'AUC ia2':>9}")
for lo, hi in faixas:
    sub = [r for r in rows if r["_bh"] is not None and lo <= r["_bh"] < hi and r["_ia3"] is not None]
    if len(sub) < 20:
        continue
    a3, _, _ = auc(sub, "_ia3")
    a2, _, _ = auc(sub, "_ia2")
    def s(v):
        return f"{v:9.3f}" if v is not None else "      n/a"
    print(f"  {lo:>6}-{hi:<9} {len(sub):10d} {s(a3)} {s(a2)}")
print()

print("=== 2. AUC GLOBAL DOS SINAIS (quanto maior o desvio de 0.5, melhor) ===")
for key, nome in (("_ia3", "ia3_person_far"), ("_ia2", "ia2_person"), ("_bh", "bbox_h"), ("_h", "hora")):
    a, nfp, ntp = auc(rows, key)
    print(f"  {nome:16s} AUC={a:.3f}  (n_FP={nfp}, n_TP={ntp})" if a else f"  {nome}: n/a")
print()

# ------------------------------------------------------- validacao temporal
print("=== 3. VALIDACAO TEMPORAL (treino ate 2026-05-20 | teste depois) ===")
CUT = "2026-05-20"
treino = [r for r in rows if r["_date"] < CUT]
teste = [r for r in rows if r["_date"] >= CUT]
print(f"  treino: {len(treino)} eventos ({sum(1 for r in treino if r['_fp'])} FP)")
print(f"  teste : {len(teste)} eventos ({sum(1 for r in teste if r['_fp'])} FP)")
print()


def is_night(r):
    return r["_h"] in NIGHT


POLITICAS = {
    "P12 noite + bbox_h<80": lambda r: is_night(r) and (r["_bh"] or 999) < 80,
    "P3  noite + ia3 fraco + bbox_h<120": lambda r: is_night(r) and (r["_ia3"] is None or r["_ia3"] < 0.20) and (r["_bh"] or 999) < 120,
    "P4  ia3<0.20 (quando rodou)": lambda r: r["_ia3"] is not None and r["_ia3"] < 0.20,
    "P8  LOW_CONFIDENCE + noite": lambda r: r["maturity_level"] == "LOW_CONFIDENCE" and is_night(r),
    "C1  P3 U P4": lambda r: (is_night(r) and (r["_ia3"] is None or r["_ia3"] < 0.20) and (r["_bh"] or 999) < 120) or (r["_ia3"] is not None and r["_ia3"] < 0.20),
    "R   RECOMENDADA (P12 U P4 U P8)": lambda r: (
        (is_night(r) and (r["_bh"] or 999) < 80)
        or (r["_ia3"] is not None and r["_ia3"] < 0.20)
        or (r["maturity_level"] == "LOW_CONFIDENCE" and is_night(r))
    ),
}


def eval_on(data, pred, nome, tag):
    nfp = sum(1 for r in data if r["_fp"])
    ntp = len(data) - nfp
    hit = [r for r in data if pred(r)]
    hfp = sum(1 for r in hit if r["_fp"])
    htp = len(hit) - hfp
    ratio = (hfp / htp) if htp else float("inf")
    rfp, rtp = nfp - hfp, ntp - htp
    prec = 100 * rtp / (rtp + rfp) if (rtp + rfp) else 0
    print(f"    {tag:7s} FP -{hfp:5d} ({100*hfp/nfp:5.1f}%) | TP -{htp:4d} ({100*htp/ntp:5.1f}%) | "
          f"{ratio:7.1f} FP/TP | precisao {100*ntp/len(data):.1f}% -> {prec:.1f}%")


for nome, pred in POLITICAS.items():
    print(f"  {nome}")
    eval_on(treino, pred, nome, "treino")
    eval_on(teste, pred, nome, "teste")
    print()

# ------------------------------------------------------- quais TP se perderiam
print("=== 4. OS TP QUE A POLITICA RECOMENDADA PERDERIA ===")
pred = POLITICAS["R   RECOMENDADA (P12 U P4 U P8)"]
perdidos = [r for r in rows if pred(r) and not r["_fp"]]
print(f"  total: {len(perdidos)} TP de {sum(1 for r in rows if not r['_fp'])}")
print(f"  {'cam':>5} {'hora':>5} {'bbox_h':>8} {'ia2':>7} {'ia3':>7} {'maturity':>18}")
for r in sorted(perdidos, key=lambda x: (x["camera_id"], x["_h"] or 0))[:25]:
    def s(v):
        return f"{v:7.3f}" if v is not None else "    n/a"
    print(f"  {r['camera_id']:>5} {str(r['_h']):>5} {(r['_bh'] or 0):8.1f} {s(r['_ia2'])} {s(r['_ia3'])} {str(r['maturity_level'])[:18]:>18}")
print()
print("  por camera:", dict(Counter(r["camera_id"] for r in perdidos).most_common()))
print("  por hora  :", dict(sorted(Counter(r["_h"] for r in perdidos).items())))
