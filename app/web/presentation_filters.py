"""Filtros de apresentacao independentes do agregador de rotas web."""

from __future__ import annotations

from app.web.presentation_constants import EVENT_TYPE_LABELS, SEVERITY_LABELS, STATUS_LABELS


def event_type_label(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    return EVENT_TYPE_LABELS.get(raw, raw.replace("_", " ").title())


def status_label(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    return STATUS_LABELS.get(raw, raw.replace("_", " ").title())


def severity_label(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    return SEVERITY_LABELS.get(raw, raw.replace("_", " ").title())


def lifecycle_label(value: str | None) -> str:
    return status_label(value)
