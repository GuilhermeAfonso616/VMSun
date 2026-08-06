#!/usr/bin/env python3
"""
Valida Strategy 3 Refinada (3 estados + zona cinza) vs Strategy 3 Original em dados reais.

Strategy 3 Original: 2 estados (ACCEPT/REJECT)
  - Se ia2_person >= threshold: ACCEPT
  - Caso contrário: REJECT

Strategy 3 Refinada: 3+ estados (ACCEPT/REJECT/UNCERTAIN + sub-regras)
  - Se ia2_person >= threshold_accept: ACCEPT
  - Se ia2_person < threshold_reject: REJECT
  - Se threshold_reject <= ia2_person < threshold_accept: UNCERTAIN
    └─ Consulta IA3, detector_score, sub-regras
"""

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import cv2

# Adicionar ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.analytics_v2.revalidation.person_crop_revalidator import PersonCropRevalidator
from app.analytics_v2.revalidation.far_person_revalidator import FarPersonRevalidator


class ValidationDecision(str, Enum):
    """Estados possíveis da validação."""
    ACCEPT = "accept"
    REJECT = "reject"
    UNCERTAIN = "uncertain"
    SUPPRESS = "suppress"
    AUDIT = "audit"


@dataclass
class Strategy3Result:
    """Resultado da Strategy 3 para um evento."""
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
    
    # Strategy 3 Original (2 estados)
    strategy3_original_decision: str
    strategy3_original_reason: str
    strategy3_original_is_correct: bool | None  # Sim/Não/None
    
    # Strategy 3 Refinada (3+ estados)
    strategy3_refined_decision: str
    strategy3_refined_reason: str
    strategy3_refined_sub_decision: str | None = None  # Sub-decisão em UNCERTAIN
    strategy3_refined_is_correct: bool | None = None  # Sim/Não/None
    
    # Comparação
    changed_decision: bool = field(default=False, init=False)
    
    def __post_init__(self):
        self.changed_decision = self.strategy3_original_decision != self.strategy3_refined_decision


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
# STRATEGY 3 ORIGINAL (2 Estados)
# ============================================================================

def strategy3_original(
    ia2_person: float | None,
    ia3_person: float | None,
    bbox_ratio: float,
) -> tuple[str, str]:
    """
    Strategy 3 Original: Adaptive Thresholds (2 estados).
    
    Apenas IA2 com threshold adaptativo por tamanho.
    """
    if ia2_person is None:
        return "reject", "ia2_not_applied"
    
    if bbox_ratio >= 0.20:
        threshold = 0.15
        size_class = "large"
    elif 0.08 <= bbox_ratio < 0.20:
        threshold = 0.08
        size_class = "medium"
    else:
        threshold = 0.02
        size_class = "small"
    
    if ia2_person >= threshold:
        return "accept", f"{size_class}_ia2_passed_{threshold}"
    else:
        return "reject", f"{size_class}_ia2_failed_{threshold}"


# ============================================================================
# STRATEGY 3 REFINADA (3+ Estados com Zona Cinza)
# ============================================================================

def strategy3_refined(
    ia2_person: float | None,
    ia2_not_person: float | None,
    ia3_person: float | None,
    detector_score: float,
    bbox_ratio: float,
) -> tuple[str, str, str | None]:
    """
    Strategy 3 Refinada: 3+ Estados com Zona Cinza.
    
    Retorna: (decision, main_reason, sub_decision_if_uncertain)
    
    - ACCEPT: Confiante que é pessoa
    - REJECT: Confiante que NÃO é pessoa
    - UNCERTAIN: Ambíguo, consulta sub-regras
      └─ Sub-estados: ACCEPT (via IA3), SUPPRESS (sem evid), AUDIT (conflito)
    """
    if ia2_person is None:
        return "reject", "ia2_not_applied", None
    
    # Define thresholds por tamanho
    if bbox_ratio >= 0.20:
        threshold_accept = 0.15
        threshold_reject = 0.03
        size_class = "large"
    elif 0.08 <= bbox_ratio < 0.20:
        threshold_accept = 0.08
        threshold_reject = 0.02
        size_class = "medium"
    else:
        threshold_accept = 0.02
        threshold_reject = 0.005
        size_class = "small"
    
    # Decisão baseada em zona cinza
    if ia2_person >= threshold_accept:
        return ValidationDecision.ACCEPT, f"{size_class}_accept_threshold_{threshold_accept}", None
    
    elif ia2_person < threshold_reject:
        return ValidationDecision.REJECT, f"{size_class}_reject_threshold_{threshold_reject}", None
    
    else:
        # ZONA CINZA! Consulta sub-regras
        return _resolve_gray_zone(
            ia2_person, ia2_not_person, ia3_person, detector_score, 
            bbox_ratio, size_class, threshold_accept, threshold_reject
        )


def _resolve_gray_zone(
    ia2_person: float,
    ia2_not_person: float | None,
    ia3_person: float | None,
    detector_score: float,
    bbox_ratio: float,
    size_class: str,
    threshold_accept: float,
    threshold_reject: float,
) -> tuple[str, str, str]:
    """
    Resolve UNCERTAIN state (zona cinza) usando sub-regras.
    
    Priority order:
    1. IA3 forte (>= 0.15) → ACCEPT
    2. IA3 fraca (< 0.05) + IA2 fraca → SUPPRESS_CANDIDATE
    3. Detector forte (>= 0.40) → ACCEPT
    4. IA2 fraca (< 0.02) + IA2_not_person forte (>= 0.80) → REJECT
    5. Tudo ambíguo → AUDIT_EVENT
    """
    
    # Sub-regra 1: IA3 confirmou
    if ia3_person is not None and ia3_person >= 0.15:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_ia3_{ia3_person:.3f}"
    
    # Sub-regra 2: IA3 rejeitou forte
    if ia3_person is not None and ia3_person < 0.05 and ia2_person < 0.05:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"suppress_via_ia3_weak_{ia3_person:.3f}"
    
    # Sub-regra 3: Detector forte
    if detector_score >= 0.40:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_detector_{detector_score:.3f}"
    
    # Sub-regra 4: IA2 fraca + consenso não_pessoa
    if ia2_person < 0.02 and ia2_not_person is not None and ia2_not_person >= 0.80:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"reject_via_ia2_not_person_{ia2_not_person:.3f}"
    
    # Sub-regra 5: Detector moderado (fallback benéfico)
    if detector_score >= 0.30:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_detector_moderate_{detector_score:.3f}"
    
    # Default: auditar
    return ValidationDecision.UNCERTAIN, "gray_zone", "audit_ambiguous"


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Valida Strategy 3 Refinada vs Original")
    parser.add_argument("--export-dir", type=Path, required=True, help="Diretório de export")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/strategy3_refined_validation"))
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
    summary_original = defaultdict(lambda: {"accept": 0, "reject": 0})
    summary_refined = defaultdict(lambda: {"accept": 0, "reject": 0, "uncertain": 0, "suppress": 0, "audit": 0})
    
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
        
        # Strategy 3 Original
        s3o_decision, s3o_reason = strategy3_original(ia2_person, ia3_person, ratio)
        s3o_correct = (s3o_decision == "accept") == (event["truth_class"] == "person")
        
        # Strategy 3 Refinada
        s3r_decision, s3r_reason, s3r_sub = strategy3_refined(
            ia2_person, ia2_not_person, ia3_person, event["detector_score"], ratio
        )
        s3r_correct = (s3r_decision == "accept") == (event["truth_class"] == "person") if s3r_decision != "uncertain" else None
        
        result = Strategy3Result(
            event_id=event["event_id"],
            truth_class=event["truth_class"],
            detector_score=event["detector_score"],
            bbox_height_ratio=ratio,
            ia2_person_score=ia2_person,
            ia2_not_person_score=ia2_not_person,
            ia3_person_score=ia3_person,
            ia3_not_person_score=ia3_not_person,
            strategy3_original_decision=s3o_decision,
            strategy3_original_reason=s3o_reason,
            strategy3_original_is_correct=s3o_correct,
            strategy3_refined_decision=s3r_decision,
            strategy3_refined_reason=s3r_reason,
            strategy3_refined_sub_decision=s3r_sub,
            strategy3_refined_is_correct=s3r_correct,
        )
        results.append(result)
        
        # Atualiza summary
        summary_original[event["truth_class"]][s3o_decision] += 1
        if s3r_decision in ("accept", "reject"):
            summary_refined[event["truth_class"]][s3r_decision] += 1
        else:
            summary_refined[event["truth_class"]][s3r_decision] += 1
    
    elapsed = time.time() - start_time
    print(f"[*] Processed {len(results)} events in {elapsed:.1f}s")
    
    # Salva resultados completos em CSV
    csv_file = args.output_dir / f"strategy3_refined_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "event_id", "truth_class", "detector_score", "bbox_height_ratio",
            "ia2_person_score", "ia2_not_person_score", "ia3_person_score", "ia3_not_person_score",
            "strategy3_original_decision", "strategy3_original_reason", "strategy3_original_is_correct",
            "strategy3_refined_decision", "strategy3_refined_reason", "strategy3_refined_sub_decision", "strategy3_refined_is_correct",
            "changed_decision",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    print(f"[✓] CSV: {csv_file}")
    
    # Calcula métricas
    metrics_original = {}
    metrics_refined = {}
    
    # Original
    person_total = summary_original["person"]["accept"] + summary_original["person"]["reject"]
    not_person_total = summary_original["not_person"]["accept"] + summary_original["not_person"]["reject"]
    
    metrics_original["person_recall"] = summary_original["person"]["accept"] / person_total if person_total > 0 else 0.0
    metrics_original["not_person_reject_rate"] = summary_original["not_person"]["reject"] / not_person_total if not_person_total > 0 else 0.0
    metrics_original["person_accept_count"] = summary_original["person"]["accept"]
    metrics_original["person_total"] = person_total
    metrics_original["not_person_reject_count"] = summary_original["not_person"]["reject"]
    metrics_original["not_person_total"] = not_person_total
    
    # Refined (calculado só para ACCEPT/REJECT, não UNCERTAIN)
    refined_person_accept = summary_refined["person"]["accept"]
    refined_person_reject = summary_refined["person"]["reject"]
    refined_not_person_accept = summary_refined["not_person"]["accept"]
    refined_not_person_reject = summary_refined["not_person"]["reject"]
    
    # Conta também UNCERTAIN como teste de validação de verdade
    refined_person_uncertain = summary_refined["person"]["uncertain"]
    refined_not_person_uncertain = summary_refined["not_person"]["uncertain"]
    refined_person_suppress = summary_refined["person"]["suppress"]
    refined_not_person_suppress = summary_refined["not_person"]["suppress"]
    refined_person_audit = summary_refined["person"]["audit"]
    refined_not_person_audit = summary_refined["not_person"]["audit"]
    
    refined_person_accept_or_uncertain = refined_person_accept + refined_person_uncertain
    refined_person_total_for_recall = refined_person_accept_or_uncertain + refined_person_reject + refined_person_suppress + refined_person_audit
    
    metrics_refined["person_recall"] = refined_person_accept / refined_person_total_for_recall if refined_person_total_for_recall > 0 else 0.0
    metrics_refined["not_person_reject_rate"] = refined_not_person_reject / (refined_not_person_reject + refined_not_person_accept) if (refined_not_person_reject + refined_not_person_accept) > 0 else 0.0
    metrics_refined["person_accept_count"] = refined_person_accept
    metrics_refined["person_uncertain_count"] = refined_person_uncertain
    metrics_refined["person_suppress_count"] = refined_person_suppress
    metrics_refined["person_audit_count"] = refined_person_audit
    metrics_refined["person_total"] = refined_person_total_for_recall
    metrics_refined["not_person_reject_count"] = refined_not_person_reject
    metrics_refined["not_person_uncertain_count"] = refined_not_person_uncertain
    metrics_refined["not_person_suppress_count"] = refined_not_person_suppress
    metrics_refined["not_person_audit_count"] = refined_not_person_audit
    metrics_refined["not_person_total"] = refined_not_person_reject + refined_not_person_accept
    
    # Conta mudanças
    changed_decisions = sum(1 for r in results if r.changed_decision)
    changed_person_to_better = sum(1 for r in results if r.changed_decision and r.truth_class == "person" and 
                                   r.strategy3_original_decision == "reject" and r.strategy3_refined_decision in ("accept", "uncertain"))
    changed_not_person_to_worse = sum(1 for r in results if r.changed_decision and r.truth_class == "not_person" and 
                                      r.strategy3_original_decision == "reject" and r.strategy3_refined_decision in ("accept", "uncertain"))
    
    # Sumário JSON
    summary = {
        "generated_at": datetime.now().isoformat(),
        "total_events_processed": len(results),
        "strategy3_original": {
            "person_recall": f"{metrics_original['person_recall']*100:.1f}%",
            "person_accept": metrics_original["person_accept_count"],
            "person_total": metrics_original["person_total"],
            "not_person_rejection_rate": f"{metrics_original['not_person_reject_rate']*100:.1f}%",
            "not_person_reject": metrics_original["not_person_reject_count"],
            "not_person_total": metrics_original["not_person_total"],
        },
        "strategy3_refined": {
            "person_recall": f"{metrics_refined['person_recall']*100:.1f}%",
            "person_accept": metrics_refined["person_accept_count"],
            "person_uncertain": metrics_refined["person_uncertain_count"],
            "person_suppress": metrics_refined["person_suppress_count"],
            "person_audit": metrics_refined["person_audit_count"],
            "person_total": metrics_refined["person_total"],
            "not_person_rejection_rate": f"{metrics_refined['not_person_reject_rate']*100:.1f}%",
            "not_person_reject": metrics_refined["not_person_reject_count"],
            "not_person_uncertain": metrics_refined["not_person_uncertain_count"],
            "not_person_suppress": metrics_refined["not_person_suppress_count"],
            "not_person_audit": metrics_refined["not_person_audit_count"],
            "not_person_total": metrics_refined["not_person_total"],
        },
        "changes": {
            "total_decisions_changed": changed_decisions,
            "person_improved": changed_person_to_better,
            "not_person_worsened": changed_not_person_to_worse,
        }
    }
    
    json_file = args.output_dir / f"strategy3_refined_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_file, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"[✓] JSON: {json_file}")
    
    # Print summary
    print("\n" + "="*120)
    print("STRATEGY 3: ORIGINAL vs REFINADA")
    print("="*120)
    
    print("\n📊 STRATEGY 3 ORIGINAL (2 Estados: ACCEPT/REJECT)")
    print(f"  Person Recall:       {metrics_original['person_recall']*100:6.1f}% ({metrics_original['person_accept_count']}/{metrics_original['person_total']})")
    print(f"  Not_Person Rejection: {metrics_original['not_person_reject_rate']*100:6.1f}% ({metrics_original['not_person_reject_count']}/{metrics_original['not_person_total']})")
    print(f"  Manual Review:       ~{int(metrics_original['person_total'] * (1-metrics_original['person_recall']) + metrics_original['not_person_total'] * (1-metrics_original['not_person_reject_rate']))} casos")
    
    print("\n📊 STRATEGY 3 REFINADA (3+ Estados: ACCEPT/REJECT/UNCERTAIN+)")
    print(f"  Person Recall:       {metrics_refined['person_recall']*100:6.1f}% ({metrics_refined['person_accept_count']}/{metrics_refined['person_total']})")
    print(f"  Person Uncertain:    ~{metrics_refined['person_uncertain_count']} ({metrics_refined['person_uncertain_count']/metrics_refined['person_total']*100:.1f}%)")
    print(f"  Person Suppress:     ~{metrics_refined['person_suppress_count']} ({metrics_refined['person_suppress_count']/metrics_refined['person_total']*100:.1f}%)")
    print(f"  Person Audit:        ~{metrics_refined['person_audit_count']} ({metrics_refined['person_audit_count']/metrics_refined['person_total']*100:.1f}%)")
    print(f"  Not_Person Rejection: {metrics_refined['not_person_reject_rate']*100:6.1f}% ({metrics_refined['not_person_reject_count']}/{metrics_refined['not_person_total']})")
    print(f"  Not_Person Uncertain: ~{metrics_refined['not_person_uncertain_count']} ({metrics_refined['not_person_uncertain_count']/(metrics_refined['not_person_total']+1)*100:.1f}%)")
    print(f"  Manual Review:       ~{metrics_refined['person_uncertain_count'] + metrics_refined['person_audit_count'] + metrics_refined['not_person_uncertain_count'] + metrics_refined['not_person_audit_count']} casos")
    
    print("\n🔄 MUDANÇAS")
    print(f"  Decisões Alteradas:   {changed_decisions} ({changed_decisions/len(results)*100:.1f}%)")
    print(f"  Person Melhoradas:    {changed_person_to_better} (foram REJECT → ACCEPT/UNCERTAIN)")
    print(f"  Not_Person Pioradas:  {changed_not_person_to_worse} (foram REJECT → ACCEPT/UNCERTAIN)")
    
    print("\n" + "="*120)


if __name__ == "__main__":
    main()
