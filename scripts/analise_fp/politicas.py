#!/usr/bin/env python3
"""Testa politicas candidatas de supressao sobre o dataset rotulado."""
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


def i(r, k):
    try:
        return int(float(r[k]))
    except Exception:
        return None


for r in rows:
    r["_fp"] = r["is_fp"] == "True"
    r["_h"] = i(r, "hour")
    r["_ia2"] = f(r, "ia2_person")
    r["_ia3"] = f(r, "ia3_person_far")
    r["_mat"] = f(r, "maturity_score")
    r["_det"] = f(r, "detector_score")
    r["_bh"] = f(r, "bbox_h")
    r["_bw"] = f(r, "bbox_w")
    r["_dur"] = f(r, "duration_s")
    r["_cam"] = r["camera_id"]

TOT = len(rows)
FP = sum(1 for r in rows if r["_fp"])
TP = TOT - FP
print(f"base: {TOT} eventos | FP={FP} ({100*FP/TOT:.1f}%) | TP={TP}\n")

NIGHT = set(range(0, 9))  # 00h-08h


def is_night(r):
    return r["_h"] in NIGHT


# ---------------------------------------------------------------- noite x camera
print("=== O EFEITO NOITE E REAL OU E UMA CAMERA RUIM? ===")
print(f"  {'cam':>5} {'noite_n':>8} {'noite_FP%':>10} {'dia_n':>7} {'dia_FP%':>9}")
cams = Counter(r["_cam"] for r in rows)
for cam, n in cams.most_common():
    if n < 20:
        continue
    night = [r for r in rows if r["_cam"] == cam and is_night(r)]
    day = [r for r in rows if r["_cam"] == cam and not is_night(r)]
    nf = 100 * sum(1 for r in night if r["_fp"]) / len(night) if night else float("nan")
    df = 100 * sum(1 for r in day if r["_fp"]) / len(day) if day else float("nan")
    print(f"  {cam:>5} {len(night):8d} {nf:9.1f}% {len(day):7d} {df:8.1f}%")
print()

night = [r for r in rows if is_night(r)]
day = [r for r in rows if not is_night(r)]
print(f"  GLOBAL noite(00-08h): {len(night):5d} eventos, {sum(1 for r in night if r['_fp'])} FP "
      f"({100*sum(1 for r in night if r['_fp'])/len(night):.1f}%), {sum(1 for r in night if not r['_fp'])} TP")
print(f"  GLOBAL dia  (09-23h): {len(day):5d} eventos, {sum(1 for r in day if r['_fp'])} FP "
      f"({100*sum(1 for r in day if r['_fp'])/len(day):.1f}%), {sum(1 for r in day if not r['_fp'])} TP")
print()

print("  TPs noturnos (o que perderiamos) por camera e hora:")
tp_night = [r for r in night if not r["_fp"]]
c = Counter((r["_cam"], r["_h"]) for r in tp_night)
for (cam, h), n in sorted(c.items(), key=lambda x: -x[1])[:15]:
    print(f"    cam {cam:>3} {h:02d}h: {n} TP")
print()

# ---------------------------------------------------------------- politicas
def evaluate(name, predicate, base=None):
    """predicate(r) -> True se o evento seria SUPRIMIDO."""
    data = base if base is not None else rows
    nfp = sum(1 for r in data if r["_fp"])
    ntp = len(data) - nfp
    hit = [r for r in data if predicate(r)]
    hfp = sum(1 for r in hit if r["_fp"])
    htp = len(hit) - hfp
    ratio = (hfp / htp) if htp else float("inf")
    resto_fp = nfp - hfp
    resto_tp = ntp - htp
    prec_depois = 100 * resto_tp / (resto_tp + resto_fp) if (resto_tp + resto_fp) else 0.0
    print(f"  {name:52s} corta {len(hit):5d} | FP -{hfp:5d} ({100*hfp/nfp:5.1f}%) | "
          f"TP -{htp:4d} ({100*htp/ntp:5.1f}%) | {ratio:6.1f} FP/TP | precisao final {prec_depois:5.1f}%")
    return hit


print("=== POLITICAS CANDIDATAS (isoladas) — precisao atual: {:.1f}% ===".format(100 * TP / TOT))

evaluate("P1  noite 00-08h: suprimir tudo", lambda r: is_night(r))
evaluate("P2  noite + ia3<0.20 (ou sem ia3)",
         lambda r: is_night(r) and (r["_ia3"] is None or r["_ia3"] < 0.20))
evaluate("P3  noite + ia3<0.20 + bbox_h<120",
         lambda r: is_night(r) and (r["_ia3"] is None or r["_ia3"] < 0.20) and (r["_bh"] or 0) < 120)
evaluate("P4  ia3 presente e <0.20", lambda r: r["_ia3"] is not None and r["_ia3"] < 0.20)
evaluate("P5  ia3 presente e <0.10", lambda r: r["_ia3"] is not None and r["_ia3"] < 0.10)
evaluate("P6  ia2<0.05 E ia3<0.20", lambda r: (r["_ia2"] is not None and r["_ia2"] < 0.05) and (r["_ia3"] is None or r["_ia3"] < 0.20))
evaluate("P7  maturity LOW_CONFIDENCE", lambda r: r["maturity_level"] == "LOW_CONFIDENCE")
evaluate("P8  maturity LOW_CONFIDENCE + noite", lambda r: r["maturity_level"] == "LOW_CONFIDENCE" and is_night(r))
evaluate("P9  bullet + noite", lambda r: r["camera_family"] == "bullet" and is_night(r))
evaluate("P10 bullet + ia3<0.20", lambda r: r["camera_family"] == "bullet" and (r["_ia3"] is None or r["_ia3"] < 0.20))
evaluate("P11 bbox_h<60", lambda r: (r["_bh"] or 999) < 60)
evaluate("P12 noite + bbox_h<80", lambda r: is_night(r) and (r["_bh"] or 999) < 80)
evaluate("P13 ia2<0.05 (sozinho)", lambda r: r["_ia2"] is not None and r["_ia2"] < 0.05)
print()

print("=== POLITICAS COMPOSTAS (uniao) ===")
def union(name, preds):
    return evaluate(name, lambda r: any(p(r) for p in preds))

union("C1  P2 (noite+ia3 fraco) U P4 (ia3<0.20)", [
    lambda r: is_night(r) and (r["_ia3"] is None or r["_ia3"] < 0.20),
    lambda r: r["_ia3"] is not None and r["_ia3"] < 0.20,
])
union("C2  C1 U maturity LOW_CONFIDENCE", [
    lambda r: is_night(r) and (r["_ia3"] is None or r["_ia3"] < 0.20),
    lambda r: r["_ia3"] is not None and r["_ia3"] < 0.20,
    lambda r: r["maturity_level"] == "LOW_CONFIDENCE",
])
union("C3  noite estrita (ia3<0.5 ou ausente) U ia3<0.10", [
    lambda r: is_night(r) and (r["_ia3"] is None or r["_ia3"] < 0.50),
    lambda r: r["_ia3"] is not None and r["_ia3"] < 0.10,
])
print()

# ---------------------------------------------------------------- por camera
print("=== POLITICA POR CAMERA: qual sinal discrimina em cada uma? ===")
print(f"  {'cam':>5} {'n':>5} {'FP%':>6} | {'ia2 AUC':>8} {'ia3 AUC':>8} {'bbox_h AUC':>11} {'hora AUC':>9} | melhor")


def auc(data, key):
    """AUC simples: P(sinal_TP > sinal_FP). 0.5 = inutil, >0.5 sinal alto indica TP."""
    fps = [r[key] for r in data if r["_fp"] and r[key] is not None]
    tps = [r[key] for r in data if not r["_fp"] and r[key] is not None]
    if not fps or not tps:
        return None
    wins = ties = 0
    # amostragem para nao explodir
    import random
    random.seed(0)
    pairs = 20000
    for _ in range(pairs):
        a = random.choice(tps)
        b = random.choice(fps)
        if a > b:
            wins += 1
        elif a == b:
            ties += 1
    return (wins + 0.5 * ties) / pairs


for cam, n in cams.most_common():
    if n < 20:
        continue
    sub = [r for r in rows if r["_cam"] == cam]
    nfp = sum(1 for r in sub if r["_fp"])
    a2 = auc(sub, "_ia2")
    a3 = auc(sub, "_ia3")
    ab = auc(sub, "_bh")
    ah = auc(sub, "_h")
    def s(v):
        return f"{v:8.3f}" if v is not None else "     n/a"
    best = max(
        [(abs((a2 or .5) - .5), "ia2"), (abs((a3 or .5) - .5), "ia3"),
         (abs((ab or .5) - .5), "bbox_h"), (abs((ah or .5) - .5), "hora")],
        key=lambda x: x[0])
    print(f"  {cam:>5} {n:5d} {100*nfp/n:5.1f}% | {s(a2)} {s(a3)} {s(ab):>11} {s(ah):>9} | {best[1]} ({best[0]+0.5:.2f})")
print()

# ---------------------------------------------------------------- objetos estaticos recorrentes
print("=== FP RECORRENTES NA MESMA REGIAO (candidato a blacklist automatica) ===")
grid = defaultdict(lambda: [0, 0])  # (cam, cx, cy) -> [fp, tp]
for r in rows:
    bw, bh = r["_bw"], r["_bh"]
    if not bw or not bh:
        continue
    try:
        x1 = float(r["bbox_w"]) and None
    except Exception:
        pass
for r in rows:
    # centro aproximado a partir do CSV original nao temos x1/y1; usa bbox_w/h como proxy de celula
    pass

# recarrega bbox real do CSV de eventos rotulados nao tem x1/y1 -> usa agrupamento por (cam, faixa de tamanho)
size_grid = defaultdict(lambda: [0, 0])
for r in rows:
    bh = r["_bh"]
    if not bh:
        continue
    bucket = int(bh // 25) * 25
    key = (r["_cam"], bucket)
    size_grid[key][0 if r["_fp"] else 1] += 1
worst = sorted(size_grid.items(), key=lambda kv: -kv[1][0])[:12]
print(f"  {'cam':>5} {'faixa bbox_h':>14} {'FP':>6} {'TP':>6} {'%FP':>7}")
for (cam, bucket), (nfp, ntp) in worst:
    tot = nfp + ntp
    print(f"  {cam:>5} {bucket:>7}-{bucket+25:<6} {nfp:6d} {ntp:6d} {100*nfp/tot:6.1f}%")
