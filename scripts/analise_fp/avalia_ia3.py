#!/usr/bin/env python3
"""Avalia todos os candidatos de IA3 no MESMO split de teste e compara com a v2 em producao."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from ultralytics import YOLO

TEST = Path(r"D:\IA2\revalidator\datasets\processed\merged_ia3_2026-07\test")
IMGSZ = 160

MODELOS = {
    "v1 (producao ate 05/05)": r"D:\Analitico\models\revalidator_far\person_far_revalidator_yolo11n_v1.pt",
    "v2 (PRODUCAO ATUAL)": r"D:\Analitico\models\revalidator_far\person_far_revalidator_yolo11n_v2.pt",
    "jul merged": r"D:\IA2\revalidator\runs\classify_merged_ia3_2026-07\person_far_revalidator_yolo11n_merged_2026_07\weights\best.pt",
    "jul aug": r"D:\IA2\revalidator\runs\classify_merged_ia3_2026-07_aug\person_far_revalidator_yolo11n_merged_2026_07_aug\weights\best.pt",
    "jul aug v2": r"D:\IA2\revalidator\runs\classify_merged_ia3_2026-07_aug\person_far_revalidator_yolo11n_merged_2026_07_aug_v2\weights\best.pt",
    "jul noaug": r"D:\IA2\revalidator\runs\classify_merged_ia3_2026-07_noaug\person_far_revalidator_yolo11n_merged_2026_07_noaug\weights\best.pt",
    "jul noaug v2": r"D:\IA2\revalidator\runs\classify_merged_ia3_2026-07_noaug\person_far_revalidator_yolo11n_merged_2026_07_noaug_v2\weights\best.pt",
}

imgs, labels = [], []
for cls, y in (("person_far", 1), ("not_person_far", 0)):
    for p in sorted((TEST / cls).glob("*")):
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}:
            imgs.append(str(p))
            labels.append(y)
labels = np.array(labels)
print(f"conjunto de teste: {len(imgs)} imagens | person_far={int(labels.sum())} not_person_far={int((1-labels).sum())}\n")

CACHE = Path("ia3_scores.json")
cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}


def scores_for(nome: str, path: str) -> np.ndarray:
    if nome in cache:
        return np.array(cache[nome])
    if not Path(path).exists():
        print(f"  !! nao encontrado: {path}")
        return None
    m = YOLO(path)
    names = m.names if isinstance(m.names, dict) else {i: n for i, n in enumerate(m.names)}
    idx_person = None
    for i, n in names.items():
        if str(n).lower() in {"person_far", "person"}:
            idx_person = int(i)
    if idx_person is None:
        idx_person = 1
    t0 = time.time()
    out = []
    B = 64
    for i in range(0, len(imgs), B):
        res = m.predict(imgs[i:i + B], imgsz=IMGSZ, verbose=False, device=0)
        for r in res:
            out.append(float(r.probs.data[idx_person].item()))
    print(f"  {nome}: {len(out)} inferencias em {time.time()-t0:.1f}s (classe person='{names[idx_person]}')")
    cache[nome] = out
    CACHE.write_text(json.dumps(cache))
    return np.array(out)


def auc(y, s):
    order = np.argsort(-s)
    y = y[order]
    pos, neg = y.sum(), (1 - y).sum()
    if pos == 0 or neg == 0:
        return float("nan")
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    return float(np.trapezoid(tp / pos, fp / neg))


print("Rodando inferencia:")
RES = {}
for nome, path in MODELOS.items():
    s = scores_for(nome, path)
    if s is not None:
        RES[nome] = s
print()

print("=== 1. QUALIDADE GERAL (AUC — maior e melhor) ===")
print(f"  {'modelo':26s} {'AUC':>8} {'acc@0.48':>10} {'acc@0.50':>10}")
for nome, s in RES.items():
    a = auc(labels, s)
    acc48 = float(((s >= 0.48).astype(int) == labels).mean())
    acc50 = float(((s >= 0.50).astype(int) == labels).mean())
    print(f"  {nome:26s} {a:8.4f} {acc48:10.4f} {acc50:10.4f}")
print()

print("=== 2. USO ANTI-FP: quanto ruido rejeita mantendo o recall de pessoa ===")
print("   (rejeitar evento quando score < corte; recall = pessoas distantes preservadas)")
for alvo in (1.00, 0.99, 0.95):
    print(f"\n  -- mantendo recall >= {alvo:.0%} das pessoas --")
    print(f"  {'modelo':26s} {'corte':>8} {'ruido rejeitado':>17} {'pessoas perdidas':>18}")
    for nome, s in RES.items():
        pos = s[labels == 1]
        neg = s[labels == 0]
        melhor = None
        for t in np.unique(np.round(np.concatenate([s, [0.0, 1.0]]), 4)):
            recall = float((pos >= t).mean())
            if recall < alvo:
                continue
            rej = float((neg < t).mean())
            if melhor is None or rej > melhor[1]:
                melhor = (t, rej, recall)
        if melhor:
            t, rej, recall = melhor
            print(f"  {nome:26s} {t:8.4f} {rej:16.1%} {1-recall:17.1%}")
        else:
            print(f"  {nome:26s} {'-':>8} {'nenhum corte viavel':>17}")
print()

print("=== 3. USO DE RESGATE: aceitar evento quando score >= corte (precisao alta) ===")
print(f"  {'modelo':26s} {'corte':>8} {'precisao':>10} {'cobertura pessoas':>18}")
for nome, s in RES.items():
    pos = s[labels == 1]
    neg = s[labels == 0]
    melhor = None
    for t in np.unique(np.round(s, 4)):
        tp = int((pos >= t).sum())
        fp = int((neg >= t).sum())
        if tp == 0:
            continue
        prec = tp / (tp + fp)
        if prec >= 0.95 and (melhor is None or tp > melhor[1]):
            melhor = (t, tp, prec)
    if melhor:
        t, tp, prec = melhor
        print(f"  {nome:26s} {t:8.4f} {prec:10.1%} {tp/len(pos):17.1%}")
    else:
        print(f"  {nome:26s} {'-':>8} {'nao atinge 95% de precisao':>10}")
print()

print("=== 4. CURVA COMPARATIVA (ruido rejeitado por corte) ===")
cortes = [0.05, 0.10, 0.20, 0.30, 0.48, 0.60, 0.80]
print(f"  {'modelo':26s}" + "".join(f"{c:>9.2f}" for c in cortes))
print(f"  {'':26s}" + "".join(f"{'rej/perda':>9}" for c in cortes))
for nome, s in RES.items():
    pos, neg = s[labels == 1], s[labels == 0]
    linha = f"  {nome:26s}"
    for c in cortes:
        rej = float((neg < c).mean())
        perda = float((pos < c).mean())
        linha += f"{rej*100:4.0f}/{perda*100:<4.0f}"
    print(linha)
print("\n  leitura: 'rej/perda' = % de ruido rejeitado / % de pessoas perdidas naquele corte")
