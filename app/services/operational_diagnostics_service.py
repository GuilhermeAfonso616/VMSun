from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.analytics_v2.revalidation.person_crop_revalidator import _candidate_model_paths
from app.core.config import settings
from app.core.timezone import utc_now_naive
from app.db.models import Event
from app.services.revalidator_policy_store import load_revalidator_policy


def _find_detail_segment(details: Any, marker: str) -> str | None:
    text = str(details or "")
    if marker not in text:
        return None
    for segment in text.split("|"):
        if marker in segment:
            return segment.strip()
    return None


def _extract_segment_value(segment: str | None, marker: str) -> str | None:
    if not segment or marker not in segment:
        return None
    raw = segment.split(marker, 1)[1].split()[0].strip()
    return raw or None


def humanize_revalidator_reason(reason: str | None) -> dict[str, str]:
    raw = str(reason or "").strip()
    normalized = raw.lower()
    if not raw:
        return {"code": "", "title": "Sem detalhe", "detail": "O evento nao trouxe motivo tecnico."}
    if normalized == "disabled":
        return {"code": raw, "title": "Desabilitada", "detail": "A revalidadora estava desativada na configuracao."}
    if normalized == "missing_frame":
        return {"code": raw, "title": "Sem frame", "detail": "Nao havia imagem valida para reavaliar a box."}
    if normalized == "invalid_bbox":
        return {"code": raw, "title": "Box invalida", "detail": "A box salva nao gerou um recorte valido para a IA."}
    if normalized.startswith("model_not_found"):
        return {"code": raw, "title": "Modelo ausente", "detail": "O arquivo do modelo nao foi encontrado no servidor/container."}
    if normalized.startswith("load_failed"):
        parts = raw.split(":", 2)
        if len(parts) >= 3 and parts[2]:
            return {
                "code": raw,
                "title": "Falha ao carregar modelo",
                "detail": f"A biblioteca carregou, mas o modelo nao abriu corretamente: {parts[1]} ({parts[2]}).",
            }
        return {"code": raw, "title": "Falha ao carregar modelo", "detail": "A biblioteca carregou, mas o modelo nao abriu corretamente."}
    if normalized in {"model_unavailable", "not_loaded"}:
        return {"code": raw, "title": "Modelo indisponivel", "detail": "A revalidadora nao tem modelo carregado para inferencia."}
    if normalized.startswith("inference_failed"):
        suffix = raw.split(":", 1)[1] if ":" in raw else "erro interno"
        return {
            "code": raw,
            "title": "Erro de inferencia",
            "detail": f"A IA foi chamada, mas quebrou durante o predict ({suffix}).",
        }
    if normalized == "not_far_candidate":
        return {"code": raw, "title": "Nao era candidata IA3", "detail": "A IA3 nao foi acionada porque a box nao passou o gatilho de distancia/tamanho."}
    if "bbox" in normalized or "crop" in normalized:
        return {"code": raw, "title": "Qualidade do recorte", "detail": f"O recorte nao passou no controle de qualidade: {raw}."}
    return {"code": raw, "title": "Motivo tecnico", "detail": raw}


def _model_status(model_path: str, *, enabled: bool) -> dict[str, Any]:
    candidates = _candidate_model_paths(model_path)
    resolved = next((candidate for candidate in candidates if candidate.exists()), candidates[0] if candidates else Path(model_path))
    exists = bool(resolved.exists())
    if not enabled:
        state = "disabled"
        label = "Desabilitada"
    elif exists:
        state = "ready"
        label = "Modelo encontrado"
    else:
        state = "missing"
        label = "Modelo ausente"
    return {
        "enabled": bool(enabled),
        "state": state,
        "label": label,
        "path": str(resolved),
        "exists": exists,
    }


def _new_empty_stats(name: str, role: str, model: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "model": model,
        "events_seen": 0,
        "ok": 0,
        "skipped": 0,
        "errors": 0,
        "last_issue": None,
        "reason_counts": [],
        "status": "ok" if model.get("state") == "ready" else "attention",
        "status_label": "Operacional" if model.get("state") == "ready" else model.get("label", "Atencao"),
    }


def _register_issue(bucket: dict[str, Any], reason_counter: Counter, event: Event, reason: str | None) -> None:
    human = humanize_revalidator_reason(reason)
    reason_counter[human["title"]] += 1
    bucket["skipped"] += 1
    if str(human["code"]).lower().startswith(("inference_failed", "load_failed", "model_not_found")):
        bucket["errors"] += 1
    bucket["last_issue"] = {
        "event_id": event.id,
        "camera_id": event.camera_id,
        "created_at": event.created_at.isoformat() if getattr(event, "created_at", None) else None,
        **human,
    }


def build_ai_operational_diagnostics(db: Session, *, days: int = 7, limit: int = 250) -> dict[str, Any]:
    cutoff = utc_now_naive() - timedelta(days=max(1, int(days or 7)))
    events = (
        db.query(Event)
        .filter(Event.created_at >= cutoff)
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(max(1, int(limit or 250)))
        .all()
    )

    policy = load_revalidator_policy()
    ia2 = _new_empty_stats(
        "IA2",
        "Revalidadora do crop da box",
        _model_status(settings.person_revalidator_model_path, enabled=bool(settings.person_revalidator_enabled)),
    )
    ia2["mode"] = str(policy.get("mode") or settings.person_revalidator_mode or "audit")
    ia2["threshold"] = float(settings.person_revalidator_threshold)
    ia3 = _new_empty_stats(
        "IA3",
        "Revalidadora de pessoa pequena/distante",
        _model_status(settings.far_person_revalidator_model_path, enabled=bool(settings.far_person_revalidator_enabled)),
    )
    ia3["threshold"] = float(settings.far_person_revalidator_threshold)

    ia2_reasons: Counter = Counter()
    ia3_reasons: Counter = Counter()
    decision_counter: Counter = Counter()

    for event in events:
        details = getattr(event, "details", None)
        text = str(details or "")
        if "revalidator_person=" in text or "revalidator_skipped=" in text:
            ia2["events_seen"] += 1
        if "revalidator_person=" in text:
            ia2["ok"] += 1
        ia2_skip = _extract_segment_value(_find_detail_segment(details, "revalidator_skipped="), "revalidator_skipped=")
        if ia2_skip:
            _register_issue(ia2, ia2_reasons, event, ia2_skip)

        if "far_revalidator_person=" in text or "far_revalidator_skipped=" in text:
            ia3["events_seen"] += 1
        if "far_revalidator_person=" in text:
            ia3["ok"] += 1
        ia3_skip = _extract_segment_value(_find_detail_segment(details, "far_revalidator_skipped="), "far_revalidator_skipped=")
        if ia3_skip:
            _register_issue(ia3, ia3_reasons, event, ia3_skip)

        if "consensus_revalidator_canceled=true" in text or getattr(event, "status", None) == "canceled":
            decision_counter["bloqueado/cancelado"] += 1
        elif "consensus_block_candidate=true" in text or "balanced_block_candidate=true" in text:
            decision_counter["candidato a bloqueio"] += 1
        elif "revalidator_person=" in text or "far_revalidator_person=" in text:
            decision_counter["auditado"] += 1
        else:
            decision_counter["IA1/sem revalidador"] += 1

    for bucket, reasons in ((ia2, ia2_reasons), (ia3, ia3_reasons)):
        bucket["reason_counts"] = [{"reason": reason, "count": count} for reason, count in reasons.most_common(6)]
        if bucket["model"]["state"] != "ready":
            bucket["status"] = "critical" if bucket["model"]["state"] == "missing" and bucket["model"]["enabled"] else "attention"
            bucket["status_label"] = bucket["model"]["label"]
        elif bucket["errors"] > 0:
            bucket["status"] = "degraded"
            bucket["status_label"] = "Falhas recentes"
        elif bucket["events_seen"] == 0:
            bucket["status"] = "idle"
            bucket["status_label"] = "Sem eventos recentes"
        else:
            bucket["status"] = "ok"
            bucket["status_label"] = "Operacional"

    return {
        "window_days": max(1, int(days or 7)),
        "events_sampled": len(events),
        "ia2": ia2,
        "ia3": ia3,
        "decisions": [{"label": label, "count": count} for label, count in decision_counter.most_common()],
    }


__all__ = ["build_ai_operational_diagnostics", "humanize_revalidator_reason"]
