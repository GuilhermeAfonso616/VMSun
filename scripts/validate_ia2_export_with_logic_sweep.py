#!/usr/bin/env python3
r"""Valida export de eventos real (D:\IA2) com variações de IA2/IA3 lógica e thresholds.

Exemplos:

    py -3 -B scripts\validate_ia2_export_with_logic_sweep.py \
      --export-dir D:/IA2/reviewed_events_export_20260504_134833 \
      --output-dir reports/ia2_export_validation

No Linux/Docker:

    python -B scripts/validate_ia2_export_with_logic_sweep.py \
      --export-dir /path/to/export \
      --output-dir reports/ia2_export_validation
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.analytics_v2.revalidation.person_crop_revalidator import PersonCropRevalidator
from app.analytics_v2.revalidation.far_person_revalidator import FarPersonRevalidator
from app.analytics_v2.revalidation.consensus_policy import evaluate_consensus_block_candidate
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("validation_script")

LABEL_TO_CLASS = {
    "true_positive": "person",
    "expected_event": "person",
    "false_positive": "not_person",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida eventos revisados com variações de IA2/IA3 lógica."
    )
    parser.add_argument(
        "--export-dir",
        required=True,
        help="Diretório do export de eventos revisados (com events.csv e subpastas).",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/ia2_export_validation",
        help="Diretório de saída para CSV/JSON.",
    )
    parser.add_argument(
        "--ia2-thresholds",
        default="0.01,0.05,0.10,0.15,0.20,0.25",
        help="Comma-separated IA2 thresholds to sweep.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limita quantidade de eventos processados.",
    )
    return parser.parse_args()


def load_events_from_export(export_dir: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Carrega eventos.csv do export e localiza crops."""
    csv_path = export_dir / "events.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"events.csv não encontrado em {export_dir}")
    
    events = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            if limit and len(events) >= limit:
                break
            
            # Filtra apenas com status "crop_saved"
            export_status = str(row.get("export_status") or "").strip()
            if export_status != "crop_saved":
                continue
            
            label = str(row.get("label") or "").strip().lower()
            truth_class = LABEL_TO_CLASS.get(label)
            if truth_class is None:
                continue
            
            # Procura crop na estrutura do export
            event_id = int(row.get("event_id") or 0)
            class_dir = export_dir / truth_class / "crops"
            
            crop_path = None
            if class_dir.exists():
                # Procura padrão event{id}_*_crop.jpg
                candidates = list(class_dir.glob(f"event{event_id}_*_crop.jpg"))
                if candidates:
                    crop_path = candidates[0]
            
            if not crop_path:
                continue  # Skip se não encontrou crop
            
            events.append({
                "event_id": event_id,
                "feedback_id": int(row.get("feedback_id") or 0),
                "camera_id": int(row.get("camera_id") or 0),
                "camera_name": row.get("camera_name"),
                "label": label,
                "truth_class": truth_class,
                "detector_score": float(row.get("detector_score") or 0),
                "snapshot_path": crop_path,
                "row": row,
            })
    
    return events


def validate_one(
    *,
    event: dict[str, Any],
    ia2_validator: PersonCropRevalidator,
    ia3_validator: FarPersonRevalidator,
    ia2_threshold: float,
) -> dict[str, Any]:
    """Valida um evento com IA2/IA3 e retorna resultado."""
    snapshot_path = event.get("snapshot_path")
    frame = None
    if snapshot_path and snapshot_path.exists():
        frame = cv2.imread(str(snapshot_path))
    
    result = {
        "event_id": event["event_id"],
        "feedback_id": event["feedback_id"],
        "camera_id": event["camera_id"],
        "truth_class": event["truth_class"],
        "detector_score": event["detector_score"],
        "ia2_threshold": ia2_threshold,
        "has_frame": frame is not None,
    }
    
    if frame is None:
        result["ia2_applied"] = False
        result["ia2_reason"] = "no_frame"
        result["ia3_triggered"] = False
        return result
    
    # Cria bbox que cobre a imagem inteira (crops são já recortados)
    height, width = frame.shape[:2]
    full_frame_bbox = [0.0, 0.0, float(width), float(height)]
    
    # IA2
    ia2_result = ia2_validator.validate(frame, full_frame_bbox)
    result["ia2_applied"] = ia2_result.applied
    result["ia2_person_score"] = ia2_result.person_score
    result["ia2_not_person_score"] = ia2_result.not_person_score
    result["ia2_reason"] = ia2_result.reason
    result["ia2_passed"] = ia2_result.person_score >= ia2_threshold if ia2_result.person_score else False
    
    # IA3
    ia3_result = ia3_validator.validate(frame, None) if ia2_result.applied else None
    result["ia3_triggered"] = ia3_result.triggered if ia3_result else False
    result["ia3_applied"] = ia3_result.applied if ia3_result else False
    result["ia3_person_score"] = ia3_result.person_far_score if ia3_result else None
    result["ia3_not_person_score"] = ia3_result.not_person_far_score if ia3_result else None
    
    # Consensus
    if ia2_result.applied and ia3_result:
        consensus = evaluate_consensus_block_candidate(ia2_result, ia3_result)
        result["consensus_block_candidate"] = consensus.get("block_candidate", False)
        result["balanced_block_candidate"] = consensus.get("balanced_block_candidate", False)
        result["ia3_confirmed_candidate"] = consensus.get("ia3_confirmed_dynamic_candidate", False)
        result["operational_decision"] = consensus.get("operational_decision")
    else:
        result["consensus_block_candidate"] = False
        result["balanced_block_candidate"] = False
        result["ia3_confirmed_candidate"] = False
        result["operational_decision"] = "no_consensus"
    
    return result


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Constrói sumário de resultados."""
    comparable = [row for row in rows if row["truth_class"] in {"person", "not_person"}]
    
    # Contadores por threshold
    summary_by_threshold: dict[float, dict[str, Any]] = defaultdict(lambda: {
        "total": 0,
        "person": 0,
        "not_person": 0,
        "ia2_passed_person": 0,
        "ia2_passed_not_person": 0,
        "consensus_person": 0,
        "consensus_not_person": 0,
        "balanced_person": 0,
        "balanced_not_person": 0,
        "ia3_confirmed_person": 0,
        "ia3_confirmed_not_person": 0,
    })
    
    for row in comparable:
        threshold = float(row["ia2_threshold"])
        s = summary_by_threshold[threshold]
        s["total"] += 1
        s[f"{row['truth_class']}"] += 1
        
        if row.get("ia2_passed"):
            s[f"ia2_passed_{row['truth_class']}"] += 1
        
        if row.get("consensus_block_candidate"):
            s[f"consensus_{row['truth_class']}"] += 1
        
        if row.get("balanced_block_candidate"):
            s[f"balanced_{row['truth_class']}"] += 1
        
        if row.get("ia3_confirmed_candidate"):
            s[f"ia3_confirmed_{row['truth_class']}"] += 1
    
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(rows),
        "comparable_rows": len(comparable),
        "by_threshold": dict(summary_by_threshold),
        "truth_counts": dict(Counter(row["truth_class"] for row in comparable)),
    }


def main() -> int:
    args = parse_args()
    export_dir = Path(args.export_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if not export_dir.exists():
        raise FileNotFoundError(f"Export dir não encontrado: {export_dir}")
    
    logger.info(f"Carregando eventos de {export_dir}")
    events = load_events_from_export(export_dir, limit=args.limit)
    logger.info(f"Carregados {len(events)} eventos comparáveis")
    
    ia2_thresholds = [float(x.strip()) for x in args.ia2_thresholds.split(",") if x.strip()]
    
    # Inicializa validadores
    ia2_validator = PersonCropRevalidator(mode="audit")
    ia3_validator = FarPersonRevalidator()
    
    # Processa eventos para cada threshold
    all_results = []
    for threshold in ia2_thresholds:
        ia2_validator.threshold = threshold
        logger.info(f"Processando com IA2 threshold={threshold}")
        
        for idx, event in enumerate(events):
            try:
                result = validate_one(
                    event=event,
                    ia2_validator=ia2_validator,
                    ia3_validator=ia3_validator,
                    ia2_threshold=threshold,
                )
                all_results.append(result)
            except Exception as exc:
                logger.warning(f"Erro evento {event['event_id']}: {exc}")
                continue
    
    # Salva resultados
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_csv = output_dir / f"ia2_export_validation_results_{timestamp}.csv"
    summary_json = output_dir / f"ia2_export_validation_summary_{timestamp}.json"
    
    if all_results:
        fields = sorted({k for row in all_results for k in row.keys()})
        with results_csv.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_results)
    
    summary = build_summary(all_results)
    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    # Log de sumário
    print(f"\nValidação concluída:")
    print(f"- Resultados salvos em: {results_csv}")
    print(f"- Sumário em: {summary_json}")
    print(f"- Total de linhas processadas: {summary['total_rows']}")
    print(f"- Comparáveis (person/not_person): {summary['comparable_rows']}")
    print(f"- Contagem verdade: {summary['truth_counts']}")
    
    for threshold_val, counts in sorted(summary['by_threshold'].items()):
        print(f"\nThreshold {threshold_val}:")
        print(f"  IA2 passou person: {counts['ia2_passed_person']}/{counts['person']}")
        print(f"  IA2 passou not_person: {counts['ia2_passed_not_person']}/{counts['not_person']}")
        print(f"  Consensus block person: {counts['consensus_person']}/{counts['person']}")
        print(f"  Consensus block not_person: {counts['consensus_not_person']}/{counts['not_person']}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
