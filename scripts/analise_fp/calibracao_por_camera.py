#!/usr/bin/env python3
"""Politica em 2 camadas: regra global segura + calibracao por camera.
Calibra por camera com validacao holdout (metade dos eventos por camera)."""
from __future__ import annotations

import csv
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
    r["_id"] = int(r["event_id"])
    try:
        r["_h"] = int(float(r["hour"]))
    except Exception:
        r["_h"] = None

NIGHT = set(range(0, 9))
GLOBAL = lambda r: (
    (r["_h"] in NIGHT and (r["_bh"] or 999) < 80)
    or (r["_ia3"] is not None and r["_ia3"] < 0.20 and (r["_bh"] or 999) < 120
        and not (r["_ia2"] is not None and r["_ia2"] >= 0.50))
    or (r["maturity_level"] == "LOW_CONFIDENCE" and r["_h"] in NIGHT)
)

IA2_GRID = [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99, 0.999]
BH_GRID = [0, 40, 50, 60, 70, 80, 100, 120]


def calibrate(train, max_tp_loss_pct=3.0):
    """Escolhe (ia2_cut, bbox_cut) que maximiza FP cortado com perda de TP <= limite."""
    ntp = sum(1 for r in train if not r["_fp"])
    nfp = len(train) - ntp
    if nfp == 0 or ntp == 0:
        return None
    best = (0.0, None)
    for t in IA2_GRID:
        for bh in BH_GRID:
            hit = [r for r in train
                   if not GLOBAL(r) and r["_ia2"] is not None and r["_ia2"] < t and (r["_bh"] or 999) >= bh]
            # bh aqui e piso: so suprime acima do piso? nao - queremos bbox pequeno
            hit = [r for r in train
                   if not GLOBAL(r) and r["_ia2"] is not None and r["_ia2"] < t
                   and (bh == 0 or (r["_bh"] or 999) < bh)]
            htp = sum(1 for r in hit if not r["_fp"])
            hfp = len(hit) - htp
            if ntp and 100 * htp / ntp > max_tp_loss_pct:
                continue
            if hfp > best[0]:
                best = (hfp, (t, bh))
    return best[1]


print(f"BASE: {len(rows)} | FP={sum(1 for r in rows if r['_fp'])} | precisao atual "
      f"{100*sum(1 for r in rows if not r['_fp'])/len(rows):.1f}%\n")

# holdout: eventos com id par = treino, impar = teste (aleatorio o suficiente e reprodutivel)
train = [r for r in rows if r["_id"] % 2 == 0]
test = [r for r in rows if r["_id"] % 2 == 1]
print(f"treino={len(train)}  teste={len(test)}\n")

params = {}
for cam in {r["camera_id"] for r in rows}:
    sub = [r for r in train if r["camera_id"] == cam]
    if len(sub) < 20:
        continue
    p = calibrate(sub)
    if p:
        params[cam] = p

print("=== PARAMETROS CALIBRADOS POR CAMERA (no treino) ===")
print(f"  {'cam':>5} {'ia2 <':>8} {'bbox_h <':>10}")
for cam, (t, bh) in sorted(params.items(), key=lambda x: -len([r for r in rows if r['camera_id'] == x[0]])):
    print(f"  {cam:>5} {t:8.3f} {('sem limite' if bh == 0 else bh):>10}")
print()


def policy(r):
    if GLOBAL(r):
        return True
    p = params.get(r["camera_id"])
    if not p:
        return False
    t, bh = p
    if r["_ia2"] is None:
        return False
    return r["_ia2"] < t and (bh == 0 or (r["_bh"] or 999) < bh)


def report(name, data, pred):
    ntp = sum(1 for r in data if not r["_fp"])
    nfp = len(data) - ntp
    hit = [r for r in data if pred(r)]
    htp = sum(1 for r in hit if not r["_fp"])
    hfp = len(hit) - htp
    rest_tp, rest_fp = ntp - htp, nfp - hfp
    prec0 = 100 * ntp / len(data)
    prec1 = 100 * rest_tp / (rest_tp + rest_fp) if (rest_tp + rest_fp) else 0
    ratio = f"{hfp/htp:.1f}" if htp else "inf"
    print(f"  {name:34s} volume -{100*len(hit)/len(data):5.1f}% | FP -{hfp:5d} ({100*hfp/nfp:5.1f}%) | "
          f"TP -{htp:3d} ({100*htp/ntp:4.1f}%) | {ratio:>6} FP/TP | precisao {prec0:5.1f}% -> {prec1:5.1f}%")


print("=== RESULTADO ===")
print(" [TREINO]")
report("so regra global", train, GLOBAL)
report("global + calibracao por camera", train, policy)
print(" [TESTE - dados nao usados na calibracao]")
report("so regra global", test, GLOBAL)
report("global + calibracao por camera", test, policy)
print()

print("=== TESTE: DETALHE POR CAMERA ===")
print(f"  {'cam':>5} {'n':>5} {'FP%':>6} {'FP cortados':>13} {'TP perdidos':>13} {'precisao':>16}")
for cam, n in Counter(r["camera_id"] for r in test).most_common():
    if n < 15:
        continue
    sub = [r for r in test if r["camera_id"] == cam]
    ntp = sum(1 for r in sub if not r["_fp"])
    nfp = len(sub) - ntp
    hit = [r for r in sub if policy(r)]
    htp = sum(1 for r in hit if not r["_fp"])
    hfp = len(hit) - htp
    rest_tp, rest_fp = ntp - htp, nfp - hfp
    prec0 = 100 * ntp / len(sub)
    prec1 = 100 * rest_tp / (rest_tp + rest_fp) if (rest_tp + rest_fp) else 0
    print(f"  {cam:>5} {len(sub):5d} {100*nfp/len(sub):5.1f}% {hfp:5d} ({100*hfp/nfp if nfp else 0:5.1f}%) "
          f"{htp:5d} ({100*htp/ntp if ntp else 0:5.1f}%) {prec0:6.1f}% -> {prec1:5.1f}%")
print()

perdidos = [r for r in test if policy(r) and not r["_fp"]]
print(f"=== TP PERDIDOS NO TESTE: {len(perdidos)} ===")
for r in perdidos[:20]:
    print(f"  cam {r['camera_id']:>3} {str(r['_h']):>2}h bbox_h={r['_bh'] or 0:6.1f} "
          f"ia2={r['_ia2'] if r['_ia2'] is not None else -1:.3f} "
          f"ia3={r['_ia3'] if r['_ia3'] is not None else -1:.3f} {r['maturity_level']}")
