#!/usr/bin/env python3
"""
Strategy 3 Refinada v2: Versão mais agressiva contra falsos positivos.

Problema com v1:
- Detectó forte estava aceitando muitos não_pessoa (216 FP!)
- Resolução de zona cinza muito permissiva
- Taxa de UNCERTAIN muito alta para não_pessoa (41.8%)

Solução:
- Ser mais conservador com detector em não_pessoa
- Usar thresholds mais específicos por classe verdadeira (quando disponível)
- Preferir REJECT/SUPPRESS sobre ACCEPT na dúvida
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
    
    ia2_person_score: float | None
    ia2_not_person_score: float | None
    
    ia3_person_score: float | None
    ia3_not_person_score: float | None
    
    # v1 vs v2
    strategy3_refined_v1_decision: str
    strategy3_refined_v2_decision: str
    strategy3_refined_v2_reason: str
    strategy3_refined_v2_sub_decision: str | None = None
    strategy3_refined_v2_is_correct: bool | None = None
    
    changed_decision_v2: bool = field(default=False, init=False)
    
    def __post_init__(self):
        self.changed_decision_v2 = self.strategy3_refined_v1_decision != self.strategy3_refined_v2_decision


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
            truth_class = row["label"]
            truth_class = "person" if truth_class == "true_positive" else "not_person"
            
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
    if frame_height == 0:
        return 0.0
    original_bbox_height = crop_height / 0.8
    return min(original_bbox_height / frame_height, 1.0)


def validate_event(ia2: PersonCropRevalidator, ia3: FarPersonRevalidator, 
                  snapshot_path: str) -> tuple[float | None, float | None, float | None, float | None, float]:
    frame = cv2.imread(snapshot_path)
    if frame is None:
        return None, None, None, None, 0.0
    
    height, width = frame.shape[:2]
    
    bbox = [0.0, 0.0, float(width), float(height)]
    ia2_result = ia2.validate(frame, bbox)
    
    ia2_person = ia2_result.person_score if ia2_result.applied else None
    ia2_not_person = ia2_result.not_person_score if ia2_result.applied else None
    
    ia3_result = ia3.validate(frame, bbox)
    ia3_person = ia3_result.person_far_score if ia3_result.applied else None
    ia3_not_person = ia3_result.not_person_far_score if ia3_result.applied else None
    
    ratio = calculate_bbox_height_ratio(height, height)
    
    return ia2_person, ia2_not_person, ia3_person, ia3_not_person, ratio


def strategy3_refined_v1(
    ia2_person: float | None,
    ia2_not_person: float | None,
    ia3_person: float | None,
    detector_score: float,
    bbox_ratio: float,
) -> tuple[str, str, str | None]:
    """Strategy 3 Refinada v1 (original - muito permissiva)."""
    if ia2_person is None:
        return "reject", "ia2_not_applied", None
    
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
    
    if ia2_person >= threshold_accept:
        return ValidationDecision.ACCEPT, f"{size_class}_accept_threshold_{threshold_accept}", None
    
    elif ia2_person < threshold_reject:
        return ValidationDecision.REJECT, f"{size_class}_reject_threshold_{threshold_reject}", None
    
    else:
        # ZONA CINZA v1
        if ia3_person is not None and ia3_person >= 0.15:
            return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_ia3_{ia3_person:.3f}"
        
        if ia3_person is not None and ia3_person < 0.05 and ia2_person < 0.05:
            return ValidationDecision.UNCERTAIN, "gray_zone", f"suppress_via_ia3_weak_{ia3_person:.3f}"
        
        if detector_score >= 0.40:
            return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_detector_{detector_score:.3f}"
        
        if ia2_person < 0.02 and ia2_not_person is not None and ia2_not_person >= 0.80:
            return ValidationDecision.UNCERTAIN, "gray_zone", f"reject_via_ia2_not_person_{ia2_not_person:.3f}"
        
        if detector_score >= 0.30:
            return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_detector_moderate_{detector_score:.3f}"
        
        return ValidationDecision.UNCERTAIN, "gray_zone", "audit_ambiguous"


def strategy3_refined_v2(
    ia2_person: float | None,
    ia2_not_person: float | None,
    ia3_person: float | None,
    ia3_not_person: float | None,
    detector_score: float,
    bbox_ratio: float,
) -> tuple[str, str, str | None]:
    """
    Strategy 3 Refinada v2: Mais agressiva contra falsos positivos.
    
    Mudanças:
    1. Detector não aceita sozinho (só em caso de conflito)
    2. Usar IA2 not_person também para rejeitar
    3. Ser mais conservador em zona cinza
    4. Preferir REJECT sobre UNCERTAIN
    """
    if ia2_person is None:
        return "reject", "ia2_not_applied", None
    
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
    
    # Clear ACCEPT case
    if ia2_person >= threshold_accept:
        return ValidationDecision.ACCEPT, f"{size_class}_accept_threshold_{threshold_accept}", None
    
    # Clear REJECT case
    elif ia2_person < threshold_reject:
        return ValidationDecision.REJECT, f"{size_class}_reject_threshold_{threshold_reject}", None
    
    # ZONA CINZA - MUITO CONSERVADORA AGORA
    else:
        return _resolve_gray_zone_v2(
            ia2_person, ia2_not_person, ia3_person, ia3_not_person, detector_score, 
            bbox_ratio, size_class, threshold_accept, threshold_reject
        )


def _resolve_gray_zone_v2(
    ia2_person: float,
    ia2_not_person: float | None,
    ia3_person: float | None,
    ia3_not_person: float | None,
    detector_score: float,
    bbox_ratio: float,
    size_class: str,
    threshold_accept: float,
    threshold_reject: float,
) -> tuple[str, str, str]:
    """
    Resolve UNCERTAIN state com lógica v2 (mais agressiva).
    
    Priority order (MUDA):
    1. IA2 forte não_pessoa (>= 0.80) → REJECT (novo!)
    2. IA3 forte (>= 0.15) → ACCEPT
    3. IA3 fraca (<= 0.02) + IA2 fraca (< 0.03) → SUPPRESS (novo!)
    4. Detector MUITO forte (>= 0.60) + IA2 ok (>= 0.02) → ACCEPT
    5. Tudo ambíguo → AUDIT (muda de accept_via_detector_moderate)
    """
    
    # Sub-regra 1 (NOVA): IA2 strong not_person
    if ia2_not_person is not None and ia2_not_person >= 0.80 and ia2_person < 0.05:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"reject_via_ia2_not_person_{ia2_not_person:.3f}"
    
    # Sub-regra 2: IA3 confirmou pessoa
    if ia3_person is not None and ia3_person >= 0.15:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_ia3_{ia3_person:.3f}"
    
    # Sub-regra 3 (NOVA): IA3 fraca + IA2 fraca → NÃO é pessoa
    if ia3_person is not None and ia3_person <= 0.02 and ia2_person < 0.03:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"suppress_weak_signals_ia3={ia3_person:.3f}_ia2={ia2_person:.3f}"
    
    # Sub-regra 4 (MUDOU): Detector MUITO forte (0.60+, não 0.40)
    if detector_score >= 0.60:
        return ValidationDecision.UNCERTAIN, "gray_zone", f"accept_via_detector_strong_{detector_score:.3f}"
    
    # Sub-regra 5: Default audit (não accept automático)
    return ValidationDecision.UNCERTAIN, "gray_zone", f"audit_ambiguous_ia2={ia2_person:.3f}"


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Valida Strategy 3 Refinada v2 vs v1")
    parser.add_argument("--export-dir", type=Path, required=True, help="Diretório de export")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/strategy3_refined_validation"))
    parser.add_argument("--limit", type=int, default=None, help="Limitar eventos processados")
    args = parser.parse_args()
    
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"[*] Loading events from {args.export_dir}...")
    events = load_events_from_export(args.export_dir)
    if args.limit:
        events = events[:args.limit]
    print(f"[*] Loaded {len(events)} events")
    
    print("[*] Loading revalidators...")
    ia2 = PersonCropRevalidator()
    ia3 = FarPersonRevalidator()
    
    results = []
    summary_v1 = defaultdict(lambda: {"accept": 0, "reject": 0, "uncertain": 0})
    summary_v2 = defaultdict(lambda: {"accept": 0, "reject": 0, "uncertain": 0, "suppress": 0, "audit": 0})
    
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
        
        # Strategy 3 Refinada v1
        s3v1_decision, s3v1_reason, s3v1_sub = strategy3_refined_v1(
            ia2_person, ia2_not_person, ia3_person, event["detector_score"], ratio
        )
        
        # Strategy 3 Refinada v2
        s3v2_decision, s3v2_reason, s3v2_sub = strategy3_refined_v2(
            ia2_person, ia2_not_person, ia3_person, ia3_not_person, event["detector_score"], ratio
        )
        
        s3v2_correct = (s3v2_decision == "accept") == (event["truth_class"] == "person") if s3v2_decision != "uncertain" else None
        
        result = Strategy3Result(
            event_id=event["event_id"],
            truth_class=event["truth_class"],
            detector_score=event["detector_score"],
            bbox_height_ratio=ratio,
            ia2_person_score=ia2_person,
            ia2_not_person_score=ia2_not_person,
            ia3_person_score=ia3_person,
            ia3_not_person_score=ia3_not_person,
            strategy3_refined_v1_decision=s3v1_decision,
            strategy3_refined_v2_decision=s3v2_decision,
            strategy3_refined_v2_reason=s3v2_reason,
            strategy3_refined_v2_sub_decision=s3v2_sub,
            strategy3_refined_v2_is_correct=s3v2_correct,
        )
        results.append(result)
        
        summary_v1[event["truth_class"]][s3v1_decision] += 1
        if s3v2_decision in ("accept", "reject"):
            summary_v2[event["truth_class"]][s3v2_decision] += 1
        else:
            summary_v2[event["truth_class"]][s3v2_decision] += 1
    
    elapsed = time.time() - start_time
    print(f"[*] Processed {len(results)} events in {elapsed:.1f}s")
    
    # Salva resultados completos em CSV
    csv_file = args.output_dir / f"strategy3_refined_v2_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "event_id", "truth_class", "detector_score", "bbox_height_ratio",
            "ia2_person_score", "ia2_not_person_score", "ia3_person_score", "ia3_not_person_score",
            "strategy3_refined_v1_decision", 
            "strategy3_refined_v2_decision", "strategy3_refined_v2_reason", "strategy3_refined_v2_sub_decision", "strategy3_refined_v2_is_correct",
            "changed_decision_v2",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    print(f"[✓] CSV: {csv_file}")
    
    # Calcula métricas
    person_total_v1 = summary_v1["person"]["accept"] + summary_v1["person"]["reject"] + summary_v1["person"]["uncertain"]
    not_person_total_v1 = summary_v1["not_person"]["accept"] + summary_v1["not_person"]["reject"] + summary_v1["not_person"]["uncertain"]
    
    person_total_v2 = sum(summary_v2["person"].values())
    not_person_total_v2 = sum(summary_v2["not_person"].values())
    
    # Sumário JSON
    summary = {
        "generated_at": datetime.now().isoformat(),
        "total_events_processed": len(results),
        "strategy3_refined_v1": {
            "person_recall": f"{summary_v1['person']['accept']/person_total_v1*100:.1f}%",
            "person_accept": summary_v1["person"]["accept"],
            "person_total": person_total_v1,
            "not_person_acceptance_rate": f"{summary_v1['not_person']['accept']/not_person_total_v1*100:.1f}%",
            "not_person_fp_count": summary_v1["not_person"]["accept"],
            "not_person_total": not_person_total_v1,
        },
        "strategy3_refined_v2": {
            "person_recall": f"{summary_v2['person']['accept']/person_total_v2*100:.1f}%",
            "person_accept": summary_v2["person"]["accept"],
            "person_uncertain": summary_v2["person"]["uncertain"],
            "person_total": person_total_v2,
            "not_person_acceptance_rate": f"{summary_v2['not_person']['accept']/not_person_total_v2*100:.1f}%",
            "not_person_fp_count": summary_v2["not_person"]["accept"],
            "not_person_uncertain": summary_v2["not_person"]["uncertain"],
            "not_person_reject": summary_v2["not_person"]["reject"],
            "not_person_total": not_person_total_v2,
        },
        "improvements": {
            "fp_reduction": summary_v1["not_person"]["accept"] - summary_v2["not_person"]["accept"],
            "fp_reduction_percent": f"{(summary_v1['not_person']['accept'] - summary_v2['not_person']['accept'])/summary_v1['not_person']['accept']*100:.1f}%",
            "person_recall_change": f"{summary_v2['person']['accept']/person_total_v2*100 - summary_v1['person']['accept']/person_total_v1*100:.1f}%",
        }
    }
    
    json_file = args.output_dir / f"strategy3_refined_v2_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_file, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"[✓] JSON: {json_file}")
    
    # Print summary
    print("\n" + "="*120)
    print("STRATEGY 3 REFINADA: v1 (permissiva) vs v2 (agressiva)")
    print("="*120)
    
    print("\n📊 STRATEGY 3 REFINADA v1 (Original - Muito Permissiva):")
    print(f"  Person Recall:           {summary_v1['person']['accept']/person_total_v1*100:6.1f}% ({summary_v1['person']['accept']}/{person_total_v1})")
    print(f"  Not_Person FP (Aceitos):  {summary_v1['not_person']['accept']:4d} ({summary_v1['not_person']['accept']/not_person_total_v1*100:5.1f}%) ❌")
    print(f"  Not_Person Uncertain:     {summary_v1['not_person']['uncertain']:4d} ({summary_v1['not_person']['uncertain']/not_person_total_v1*100:5.1f}%)")
    
    print("\n📊 STRATEGY 3 REFINADA v2 (Melhorada - Mais Agressiva):")
    print(f"  Person Recall:           {summary_v2['person']['accept']/person_total_v2*100:6.1f}% ({summary_v2['person']['accept']}/{person_total_v2})")
    print(f"  Not_Person FP (Aceitos):  {summary_v2['not_person']['accept']:4d} ({summary_v2['not_person']['accept']/not_person_total_v2*100:5.1f}%) ✅")
    print(f"  Not_Person Uncertain:     {summary_v2['not_person']['uncertain']:4d} ({summary_v2['not_person']['uncertain']/not_person_total_v2*100:5.1f}%)")
    print(f"  Not_Person Reject:        {summary_v2['not_person']['reject']:4d} ({summary_v2['not_person']['reject']/not_person_total_v2*100:5.1f}%)")
    
    print("\n🎯 MELHORIAS v2 vs v1:")
    print(f"  FP Redução:              {summary_v1['not_person']['accept'] - summary_v2['not_person']['accept']} casos (-{(summary_v1['not_person']['accept'] - summary_v2['not_person']['accept'])/summary_v1['not_person']['accept']*100:.1f}%) ✅")
    print(f"  Person Recall Mudança:   {summary_v2['person']['accept']/person_total_v2*100 - summary_v1['person']['accept']/person_total_v1*100:.1f}%")
    
    print("\n" + "="*120)


if __name__ == "__main__":
    main()
