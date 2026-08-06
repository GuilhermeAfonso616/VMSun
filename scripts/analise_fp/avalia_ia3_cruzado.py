#!/usr/bin/env python3
"""Teste cruzado: o candidato de julho regride no dominio antigo (onde a v2 foi treinada)?"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from ultralytics import YOLO

CONJUNTOS = {
    "TESTE JULHO (cameras atuais)": (
        Path(r"D:\IA2\revalidator\datasets\processed\merged_ia3_2026-07\test"),
        ("person_far", "not_person_far"),
    ),
    "TESTE MAIO (dominio da v2)": (
        Path(r"D:\IA2\revalidator\datasets\processed\ia3_v2_20260511\test"),
        ("person", "not_person"),
    ),
    "SAFETY far_block (v1)": (
        Path(r"D:\IA2\revalidator\datasets\processed\ia3_far_v1\far_block_safety_test"),
        ("person_far", "not_person_far"),
    ),
}

MODELOS = {
    "v2 (PRODUCAO ATUAL)": r"D:\Analitico\models\revalidator_far\person_far_revalidator_yolo11n_v2.pt",
    "v1": r"D:\Analitico\models\revalidator_far\person_far_revalidator_yolo11n_v1.pt",
    "jul merged (CANDIDATO)": r"D:\IA2\revalidator\runs\classify_merged_ia3_2026-07\person_far_revalidator_yolo11n_merged_2026_07\weights\best.pt",
}

CACHE = Path("ia3_cross.json")
cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}


def load(conj):
    base, (cp, cn) = conj
    imgs, y = [], []
    for cls, lab in ((cp, 1), (cn, 0)):
        for p in sorted((base / cls).glob("*")):
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}:
                imgs.append(str(p))
                y.append(lab)
    return imgs, np.array(y)


def infer(nome, path, imgs, tag):
    key = f"{tag}|{nome}"
    if key in cache:
        return np.array(cache[key])
    m = YOLO(path)
    names = m.names if isinstance(m.names, dict) else {i: n for i, n in enumerate(m.names)}
    idx = next((int(i) for i, n in names.items() if str(n).lower() in {"person_far", "person"}), 1)
    out = []
    for i in range(0, len(imgs), 64):
        for r in m.predict(imgs[i:i + 64], imgsz=160, verbose=False, device=0):
            out.append(float(r.probs.data[idx].item()))
    cache[key] = out
    CACHE.write_text(json.dumps(cache))
    return np.array(out)


def auc(y, s):
    o = np.argsort(-s)
    y = y[o]
    pos, neg = y.sum(), (1 - y).sum()
    if not pos or not neg:
        return float("nan")
    return float(np.trapezoid(np.cumsum(y) / pos, np.cumsum(1 - y) / neg))


for tag, conj in CONJUNTOS.items():
    imgs, y = load(conj)
    if not imgs:
        print(f"\n### {tag}: vazio, pulando")
        continue
    print(f"\n### {tag} — {len(imgs)} imagens (pessoa={int(y.sum())}, ruido={int((1-y).sum())})")
    print(f"  {'modelo':24s} {'AUC':>8} | recall 100%: {'corte':>8} {'ruido rej':>10} | recall 95%: {'corte':>8} {'ruido rej':>10}")
    for nome, path in MODELOS.items():
        if not Path(path).exists():
            continue
        s = infer(nome, path, imgs, tag)
        pos, neg = s[y == 1], s[y == 0]
        linha = f"  {nome:24s} {auc(y, s):8.4f} |"
        for alvo in (1.00, 0.95):
            best = (0.0, 0.0)
            for t in np.unique(np.round(np.concatenate([s, [0.0]]), 4)):
                if float((pos >= t).mean()) >= alvo:
                    rej = float((neg < t).mean())
                    if rej > best[1]:
                        best = (t, rej)
            linha += f" {best[0]:19.4f} {best[1]:9.1%} |"
        print(linha)

print("\n\n=== VEREDITO ===")
print("AUC maior = melhor separacao. 'ruido rej' com recall 100% = quanto FP da para cortar")
print("sem perder nenhuma pessoa naquele conjunto.")
