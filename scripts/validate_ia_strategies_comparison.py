#!/usr/bin/env python3
"""Compara estratégias de combinação IA1 + IA2 + IA3 em dados reais."""

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

# Adicionara ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.analytics_v2.revalidation.person_crop_revalidator import PersonCropRevalidator
from app.analytics_v2.revalidation.far_person_revalidator import FarPersonRevalidator
from app.analytics_v2.revalidation.consensus_policy import evaluate_consensus_block_candidate


@dataclass
class StrategyResult:
    """Resultado de uma estratégia para um evento."""
    event_id: int
    truth_class: str
    detector_score: float
    bbox_height_ratio: float
    
    # IA2 scores
    ia2_person_score: float | None
    ia2_not_person_score: float | None
    
    # IA3 scores
    ia3_person_score: float | None
    ia3_not_person_score: float | None
    
    # Resultado de cada estratégia
    strategy_1_decision: str  # "accept" / "reject"
    strategy_1_reason: str
    
    strategy_2_decision: str
    strategy_2_reason: str
    
    strategy_3_decision: str
    strategy_3_reason: str
    
    strategy_4_decision: str
    strategy_4_reason: str


def load_events_from_export(export_dir: Path) -> list[dict[str, Any]]:
    """Carrega eventos do export directory (filtrados por crop_saved)."""
    events_csv = export_dir / "events.csv"
    if not events_csv.exists():
        raise FileNotFoundError(f"events.csv not found in {export_dir}")
    
    events = []
    with open(events_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("export_status") != "crop_saved":
                continue
            
            event_id = int(row["event_id"])
            truth_class = row["label"]  # true_positive -> person, false_positive -> not_person
            truth_class = "person" if truth_class == "true_positive" else "not_person"
            
            # Procura o arquivo de crop
            crop_glob_person = list((export_dir / "person" / "crops").glob(f"event{event_id}_*_crop.jpg"))
            crop_glob_not_person = list((export_dir / "not_person" / "crops").glob(f"event{event_id}_*_crop.jpg"))
            
            crop_file = crop_glob_person[0] if crop_glob_person else (crop_glob_not_person[0] if crop_glob_not_person else None)
            
            if crop_file and crop_file.exists():
                events.append({
                    "event_id": event_id,
                    "truth_class": truth_class,
                    "detector_score": float(row.get("detector_score", 0.0)),
                    "snapshot_path": str(crop_file),
                })
    
    return events


def calculate_bbox_height_ratio(frame_height: int, crop_height: int) -> float:
    """Estima bbox_height_ratio. Aqui usamos crop_height como proxy."""
    if frame_height == 0:
        return 0.0
    # Crop é extracted, assumindo que foi 80% da altura original
    original_bbox_height = crop_height / 0.8
    return min(original_bbox_height / frame_height, 1.0)


def validate_event(ia2: PersonCropRevalidator, ia3: FarPersonRevalidator, 
                  snapshot_path: str) -> tuple[float | None, float | None, float | None, float | None, float]:
    """Roda IA2 e IA3 em um evento. Retorna (ia2_person, ia2_not_person, ia3_person, ia3_not_person, ratio)."""
    frame = cv2.imread(snapshot_path)
    if frame is None:
        return None, None, None, None, 0.0
    
    height, width = frame.shape[:2]
    
    # IA2: full-frame bbox (crop é pre-extracted)
    bbox = [0.0, 0.0, float(width), float(height)]
    ia2_result = ia2.validate(frame, bbox)
    
    ia2_person = ia2_result.person_score if ia2_result.applied else None
    ia2_not_person = ia2_result.not_person_score if ia2_result.applied else None
    
    # IA3: tentar disparar
    ia3_result = ia3.validate(frame, bbox)
    ia3_person = ia3_result.person_far_score if ia3_result.applied else None
    ia3_not_person = ia3_result.not_person_far_score if ia3_result.applied else None
    
    # Estimar ratio (crop é aproximadamente 80% da altura da bbox original)
    ratio = calculate_bbox_height_ratio(height, height)
    
    return ia2_person, ia2_not_person, ia3_person, ia3_not_person, ratio


# ============================================================================
# ESTRATÉGIAS
# ============================================================================

def strategy_1_weighted_voting(
    detector_score: float,
    ia2_person: float | None,
    ia3_person: float | None,
    weights: tuple[float, float, float] = (0.3, 0.5, 0.2),
) -> tuple[str, str]:
    """Estratégia 1: Ensemble Weighted Voting."""
    if ia2_person is None:
        return "reject", "ia2_not_applied"
    
    # Fallback IA3
    ia3_p = ia3_person if ia3_person is not None else 0.0
    
    final_score = weights[0] * detector_score + weights[1] * ia2_person + weights[2] * ia3_p
    threshold = 0.30
    
    if final_score >= threshold:
        return "accept", f"weighted_score={final_score:.3f}"
    else:
        return "reject", f"weighted_score={final_score:.3f}"


def strategy_2_cascading(
    detector_score: float,
    ia2_person: float | None,
    ia2_not_person: float | None,
    ia3_person: float | None,
    bbox_ratio: float,
) -> tuple[str, str]:
    """Estratégia 2: Cascading Logic."""
    if ia2_person is None:
        return "reject", "ia2_not_applied"
    
    # Stage 1: IA2 clear cases
    if ia2_person >= 0.20:
        return "accept", "ia2_strong_person"
    
    if ia2_person < 0.01 and ia2_not_person is not None and ia2_not_person >= 0.99:
        return "reject", "ia2_strong_not_person"
    
    # Stage 2: Ambiguous - consulta qualidade e tamanho
    if bbox_ratio >= 0.20:
        # Pessoa grande
        if ia2_person >= 0.10:
            return "accept", "large_person_moderate_ia2"
        else:
            return "reject", "large_person_low_ia2"
    
    if 0.08 <= bbox_ratio < 0.20:
        # Pessoa média
        if ia2_person >= 0.08:
            return "accept", "medium_person_moderate_ia2"
        elif ia3_person is not None and ia3_person >= 0.15:
            return "accept", "medium_person_ia3_confirmed"
        else:
            return "reject", "medium_person_ambiguous"
    
    if bbox_ratio < 0.08:
        # Pessoa pequena - critical para IA3
        if ia3_person is not None:
            if ia3_person >= 0.10:
                return "accept", "small_person_ia3_confirmed"
            elif ia2_person >= 0.05 and ia3_person >= 0.02:
                return "accept", "small_person_mixed_weak"
            else:
                return "reject", "small_person_ia3_weak"
        else:
            # Sem IA3, apenas IA2
            if ia2_person >= 0.05:
                return "accept", "small_person_no_ia3_but_ia2_ok"
            else:
                return "reject", "small_person_no_ia3_ia2_weak"
    
    return "reject", "cascading_default"


def strategy_3_adaptive_thresholds(
    detector_score: float,
    ia2_person: float | None,
    ia3_person: float | None,
    bbox_ratio: float,
) -> tuple[str, str]:
    """Estratégia 3: Adaptive Thresholds baseado em bbox size."""
    if ia2_person is None:
        return "reject", "ia2_not_applied"
    
    if bbox_ratio >= 0.20:
        # Pessoa grande: threshold exigente 0.15
        threshold = 0.15
        reason = "large_person"
    elif 0.08 <= bbox_ratio < 0.20:
        # Pessoa média: threshold balanceado 0.08
        threshold = 0.08
        reason = "medium_person"
    else:
        # Pessoa pequena: threshold permissivo 0.02, ativa IA3
        threshold = 0.02
        reason = "small_person"
        
        # Se IA3 disparou, usar score dela também
        if ia3_person is not None and ia3_person >= 0.15:
            return "accept", f"{reason}_ia3_confirmed"
    
    if ia2_person >= threshold:
        return "accept", f"{reason}_ia2_passed_{threshold}"
    else:
        return "reject", f"{reason}_ia2_failed_{threshold}"


def strategy_4_hybrid_consensus(
    detector_score: float,
    ia2_person: float | None,
    ia2_not_person: float | None,
    ia3_person: float | None,
    ia3_triggered: bool,
) -> tuple[str, str]:
    """Estratégia 4: Hybrid Consensus - mais permissivo que o padrão."""
    if ia2_person is None:
        return "reject", "ia2_not_applied"
    
    # REGRA 1: IA2 forte em pessoa
    if ia2_person >= 0.20:
        return "accept", "rule1_ia2_strong_person"
    
    # REGRA 2: IA2 fraco + detector confiante
    if ia2_person >= 0.10 and detector_score >= 0.35:
        return "accept", "rule2_ia2_moderate_detector_confident"
    
    # REGRA 3: IA3 confirmou pessoa
    if ia3_triggered and ia3_person is not None and ia3_person >= 0.15:
        return "accept", "rule3_ia3_confirmed_person"
    
    # REGRA 4: IA2 forte em não_pessoa
    if ia2_not_person is not None and ia2_not_person >= 0.90 and ia2_person <= 0.10:
        return "reject", "rule4_ia2_strong_not_person"
    
    # REGRA 5: Consensus baixo IA2 + IA3
    if ia3_triggered and ia3_person is not None and ia3_person <= 0.05 and ia2_person <= 0.15:
        return "reject", "rule5_consensus_not_person"
    
    # REGRA 6: Detector fraco + IA2 fraco
    if detector_score <= 0.30 and ia2_person <= 0.05:
        return "reject", "rule6_weak_detector_weak_ia2"
    
    # Default: benefício da dúvida em caso de ambiguidade
    if ia2_person >= 0.05:
        return "accept", "rule7_benefit_of_doubt_ia2_ok"
    else:
        return "reject", "rule8_default_reject"


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Compara estratégias de combinação IA1+IA2+IA3")
    parser.add_argument("--export-dir", type=Path, required=True, help="Diretório de export")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/ia_strategies_comparison"))
    parser.add_argument("--limit", type=int, default=None, help="Limitar eventos processados")
    args = parser.parse_args()
    
    # Setup
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Carrega eventos
    print(f"[*] Loading events from {args.export_dir}...")
    events = load_events_from_export(args.export_dir)
    if args.limit:
        events = events[:args.limit]
    print(f"[*] Loaded {len(events)} events")
    
    # Inicializa revalidadores
    print("[*] Loading revalidators...")
    ia2 = PersonCropRevalidator()
    ia3 = FarPersonRevalidator()
    
    # Processa eventos
    results = []
    summary_by_strategy = defaultdict(lambda: {"person_accept": 0, "person_reject": 0, "not_person_accept": 0, "not_person_reject": 0})
    
    print("[*] Processing events...")
    start_time = time.time()
    
    for idx, event in enumerate(events):
        if (idx + 1) % max(1, len(events) // 10) == 0:
            print(f"  {idx + 1}/{len(events)}...")
        
        ia2_person, ia2_not_person, ia3_person, ia3_not_person, ratio = validate_event(
            ia2, ia3, event["snapshot_path"]
        )
        
        if ia2_person is None:
            continue
        
        # Estratégias
        s1_decision, s1_reason = strategy_1_weighted_voting(event["detector_score"], ia2_person, ia3_person)
        s2_decision, s2_reason = strategy_2_cascading(event["detector_score"], ia2_person, ia2_not_person, ia3_person, ratio)
        s3_decision, s3_reason = strategy_3_adaptive_thresholds(event["detector_score"], ia2_person, ia3_person, ratio)
        s4_decision, s4_reason = strategy_4_hybrid_consensus(event["detector_score"], ia2_person, ia2_not_person, ia3_person, ia3_person is not None)
        
        result = StrategyResult(
            event_id=event["event_id"],
            truth_class=event["truth_class"],
            detector_score=event["detector_score"],
            bbox_height_ratio=ratio,
            ia2_person_score=ia2_person,
            ia2_not_person_score=ia2_not_person,
            ia3_person_score=ia3_person,
            ia3_not_person_score=ia3_not_person,
            strategy_1_decision=s1_decision,
            strategy_1_reason=s1_reason,
            strategy_2_decision=s2_decision,
            strategy_2_reason=s2_reason,
            strategy_3_decision=s3_decision,
            strategy_3_reason=s3_reason,
            strategy_4_decision=s4_decision,
            strategy_4_reason=s4_reason,
        )
        results.append(result)
        
        # Atualiza summary
        for strategy_idx, decision in enumerate([s1_decision, s2_decision, s3_decision, s4_decision], 1):
            key = f"strategy_{strategy_idx}"
            if decision == "accept":
                if event["truth_class"] == "person":
                    summary_by_strategy[key]["person_accept"] += 1
                else:
                    summary_by_strategy[key]["not_person_accept"] += 1
            else:
                if event["truth_class"] == "person":
                    summary_by_strategy[key]["person_reject"] += 1
                else:
                    summary_by_strategy[key]["not_person_reject"] += 1
    
    elapsed = time.time() - start_time
    print(f"[*] Processed {len(results)} events in {elapsed:.1f}s")
    
    # Salva resultados
    csv_file = args.output_dir / f"strategies_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "event_id", "truth_class", "detector_score", "bbox_height_ratio",
            "ia2_person_score", "ia2_not_person_score", "ia3_person_score", "ia3_not_person_score",
            "strategy_1_decision", "strategy_1_reason",
            "strategy_2_decision", "strategy_2_reason",
            "strategy_3_decision", "strategy_3_reason",
            "strategy_4_decision", "strategy_4_reason",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    print(f"[✓] CSV: {csv_file}")
    
    # Sumário
    summary = {
        "generated_at": datetime.now().isoformat(),
        "total_events": len(results),
        "strategies": {}
    }
    
    for strategy, metrics in summary_by_strategy.items():
        person_total = metrics["person_accept"] + metrics["person_reject"]
        not_person_total = metrics["not_person_accept"] + metrics["not_person_reject"]
        
        summary["strategies"][strategy] = {
            "person_recall": metrics["person_accept"] / person_total if person_total > 0 else 0.0,
            "person_accept_count": metrics["person_accept"],
            "person_total": person_total,
            "not_person_rejection_rate": 1.0 - (metrics["not_person_accept"] / not_person_total if not_person_total > 0 else 0.0),
            "not_person_reject_count": metrics["not_person_reject"],
            "not_person_total": not_person_total,
        }
    
    json_file = args.output_dir / f"strategies_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_file, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"[✓] JSON: {json_file}")
    
    # Print summary
    print("\n" + "="*100)
    print("ESTRATÉGIAS COMPARADAS")
    print("="*100)
    
    for strategy, metrics in sorted(summary["strategies"].items()):
        print(f"\n{strategy}:")
        print(f"  Person recall:    {metrics['person_recall']*100:6.1f}% ({metrics['person_accept_count']}/{metrics['person_total']})")
        print(f"  Not_person reject: {metrics['not_person_rejection_rate']*100:6.1f}% ({metrics['not_person_reject_count']}/{metrics['not_person_total']})")


if __name__ == "__main__":
    main()
