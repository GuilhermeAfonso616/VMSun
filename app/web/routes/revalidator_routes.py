from __future__ import annotations

import json
from pathlib import Path

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.analytics_v2.revalidation.consensus_policy import evaluate_consensus_block_candidate
from app.analytics_v2.revalidation.far_person_revalidator import get_far_person_revalidator
from app.analytics_v2.revalidation.person_crop_revalidator import get_person_crop_revalidator
from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import Event


router = APIRouter()


def _load_bbox(event: Event) -> list[float]:
    try:
        bbox = json.loads(event.bbox_json or "")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="bbox_json inválido") from exc

    if not isinstance(bbox, list) or len(bbox) != 4:
        raise HTTPException(status_code=400, detail=f"bbox_json inválido: {bbox}")
    try:
        return [float(value) for value in bbox]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bbox_json inválido: {bbox}") from exc


def _build_ia1_payload(event: Event) -> dict:
    ia1_score = getattr(event, "detector_score", None)
    return {
        "name": "IA1",
        "role": "detector_original",
        "applied": ia1_score is not None,
        "person_score": ia1_score,
        "interpretation": "CONFIRMOU_PESSOA" if ia1_score is not None else "SEM_SCORE",
        "summary": (
            f"IA1 confirmou: pessoa ({float(ia1_score):.3f})"
            if ia1_score is not None
            else "IA1 confirmou: pessoa"
        ),
    }


def _build_ia3_payload(far_result) -> dict:
    if far_result.applied:
        far_person_score = float(far_result.person_far_score or 0.0)
        far_not_person_score = float(far_result.not_person_far_score or 0.0)
        far_raw = "PARECE_PESSOA" if far_person_score >= far_not_person_score else "NAO_PARECE_PESSOA"
        if far_raw == "PARECE_PESSOA":
            far_summary = f"IA3 classificou visualmente como pessoa ({far_person_score:.3f})"
        elif far_person_score >= float(far_result.threshold or 0.0):
            far_summary = (
                f"IA3 favorece nao pessoa, mas protegeu por score acima do limiar "
                f"({far_person_score:.3f})"
            )
        else:
            far_summary = f"IA3 foi solicitada e disse que: nao e uma pessoa ({far_person_score:.3f})"
    elif far_result.triggered:
        far_raw = "SOLICITADA_SEM_RESULTADO"
        far_summary = f"IA3 foi solicitada, mas não avaliou: {far_result.reason or 'sem resultado'}"
    else:
        far_raw = "NAO_SOLICITADA"
        far_summary = "IA3 não foi solicitada"

    return {
        "name": "IA3",
        "role": "far_person_revalidator_v1",
        "triggered": far_result.triggered,
        "applied": far_result.applied,
        "person_far_score": far_result.person_far_score,
        "not_person_far_score": far_result.not_person_far_score,
        "threshold": far_result.threshold,
        "raw_model_interpretation": far_raw,
        "trigger_reason": far_result.trigger_reason,
        "reason": far_result.reason,
        "model_path": far_result.model_path,
        "summary": far_summary,
    }


@router.post("/events/{event_id}/revalidate-crop")
def revalidate_event_crop(event_id: int):
    """Reavalia manualmente apenas o crop da bbox salva no evento.

    A resposta simula a decisão atual do revalidador para auditoria; ela não
    altera o status de eventos já persistidos.
    """

    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Evento não encontrado")

        if not event.snapshot_path:
            raise HTTPException(status_code=404, detail="Evento sem snapshot salvo")

        if not event.bbox_json:
            raise HTTPException(status_code=400, detail="Evento sem bbox_json salvo")

        snapshot_file = Path(event.snapshot_path)
        if not snapshot_file.exists():
            raise HTTPException(status_code=404, detail=f"Snapshot não encontrado: {snapshot_file}")

        bbox = _load_bbox(event)
        frame = cv2.imread(str(snapshot_file))
        if frame is None:
            raise HTTPException(status_code=500, detail="Não foi possível abrir o snapshot com OpenCV")

        revalidator = get_person_crop_revalidator()
        result = revalidator.validate(frame, bbox)
        far_revalidator = get_far_person_revalidator()
        far_result = far_revalidator.validate(frame, bbox, base_quality=result.quality, ia2_result=result)
        consensus_result = evaluate_consensus_block_candidate(result, far_result)

        threshold = float(result.threshold or 0.0)
        passed_safety_threshold = bool(
            result.applied
            and result.person_score is not None
            and float(result.person_score) >= threshold
        )
        person_score = float(result.person_score or 0.0)
        not_person_score = float(result.not_person_score or 0.0)
        raw_model_person = bool(result.applied and person_score >= not_person_score and person_score >= 0.5)
        raw_model_interpretation = "PARECE_PESSOA" if raw_model_person else "NAO_PARECE_PESSOA"
        operational_result = "PASSOU_REVALIDADOR" if passed_safety_threshold else "REPROVOU_REVALIDADOR"
        raw_model_margin = person_score - not_person_score
        consensus_would_block = bool(
            settings.consensus_revalidator_block_enabled
            and revalidator.current_mode() == "block"
            and (
                consensus_result.get("block_candidate")
                or (
                    settings.consensus_revalidator_balanced_block_enabled
                    and consensus_result.get("balanced_block_candidate")
                )
                or (
                    settings.consensus_revalidator_ia3_confirmed_block_enabled
                    and consensus_result.get("ia3_confirmed_dynamic_candidate")
                )
                or (
                    settings.consensus_revalidator_ia2_dominant_block_enabled
                    and consensus_result.get("ia2_dominant_ia3_non_person_candidate")
                )
                or (
                    settings.consensus_revalidator_ia2_only_block_enabled
                    and consensus_result.get("ia2_only_balanced_candidate")
                )
            )
        )
        if consensus_would_block:
            consensus_result["mode"] = "block"
            consensus_result["would_block"] = True
            consensus_result["operational_decision"] = "would_block_by_consensus"
            consensus_result["block_profile"] = (
                "ia2_only_balanced"
                if consensus_result.get("ia2_only_balanced_candidate")
                else "ia2_dominant_ia3_non_person"
                if consensus_result.get("ia2_dominant_ia3_non_person_candidate")
                else "ia3_confirmed_dynamic"
                if consensus_result.get("ia3_confirmed_dynamic_candidate")
                else "balanced"
                if consensus_result.get("balanced_block_candidate")
                else "strict"
            )
        block_decision = (
            "BLOQUEARIA_EVENTO_CONSENSO"
            if consensus_would_block
            else "BLOQUEARIA_EVENTO"
            if revalidator.should_block(result)
            else "NAO_BLOQUEOU_POLITICA_CONSERVADORA"
        )

        ia2_payload = {
            "name": "IA2",
            "role": "person_crop_revalidator_v5",
            "applied": result.applied,
            "person_score": result.person_score,
            "not_person_score": result.not_person_score,
            "threshold": result.threshold,
            "raw_model_interpretation": raw_model_interpretation,
            "operational_result": operational_result,
            "block_decision": block_decision,
            "block_eligible": result.block_eligible,
            "block_reason": result.block_reason,
            "reason": result.reason,
            "model_path": result.model_path,
        }
        ia3_payload = _build_ia3_payload(far_result)

        if raw_model_person:
            model_summary = "O modelo bruto acha que PARECE pessoa."
        elif passed_safety_threshold:
            model_summary = "O modelo bruto favorece NÃO pessoa, mas a política conservadora não bloqueou."
        else:
            model_summary = "O modelo bruto acha que NÃO parece pessoa."

        if consensus_would_block:
            block_profile = str(consensus_result.get("block_profile") or "consensus")
            block_summary = f"Bloquearia por consenso ({block_profile}) se fosse um evento novo no fluxo ao vivo."
        elif revalidator.should_block(result):
            block_summary = "Bloquearia este evento."
        else:
            block_summary = f"Não bloqueou: {result.block_reason or result.reason or 'sem motivo informado'}."

        payload = result.to_metadata()
        payload.update(
            {
                "event_id": event.id,
                "camera_id": event.camera_id,
                "event_type": event.event_type,
                "snapshot_path": str(snapshot_file),
                "bbox": bbox,
                "revalidation_evidence": {
                    "version": 1,
                    "source": "saved_event_snapshot_bbox",
                    "frame_source": "event.snapshot_path",
                    "bbox_source": "event.bbox_json",
                    "bbox": bbox,
                    "width": int(frame.shape[1]) if getattr(frame, "shape", None) is not None else None,
                    "height": int(frame.shape[0]) if getattr(frame, "shape", None) is not None else None,
                },
                "conclusion": raw_model_interpretation,
                "raw_model_interpretation": raw_model_interpretation,
                "raw_person_score": result.person_score,
                "raw_not_person_score": result.not_person_score,
                "raw_model_margin": raw_model_margin,
                "operational_revalidator_result": operational_result,
                "operational_block_decision": block_decision,
                "operational_threshold": threshold,
                "ia1": _build_ia1_payload(event),
                "ia2": ia2_payload,
                "ia3": ia3_payload,
                "far_person_revalidator": far_result.to_metadata(),
                "consensus_revalidator": consensus_result,
                "human_summary": {
                    "origem": "Reavaliação atual usando snapshot e bbox salvos no evento.",
                    "modelo_bruto": model_summary,
                    "decisao_operacional": (
                        "Passou pelo revalidador por política conservadora."
                        if passed_safety_threshold
                        else "Reprovou no threshold operacional do revalidador."
                    ),
                    "bloqueio": block_summary,
                },
            }
        )

        get_logger("app.web.revalidator").info(
            "Manual crop revalidation event_id=%s person_score=%s not_person_score=%s conclusion=%s",
            event.id,
            result.person_score,
            result.not_person_score,
            payload["conclusion"],
            extra={
                "camera_id": event.camera_id,
                "event_id": event.id,
                "action": "manual_person_crop_revalidation",
                "status": "running" if result.applied else "degraded",
                "reason": result.reason or "manual_test",
            },
        )

        return JSONResponse(payload)
    finally:
        db.close()
