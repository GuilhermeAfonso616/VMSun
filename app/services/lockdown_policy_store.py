"""Persistencia simples da politica de disparo do Lockdown.

O seletor e global da aplicacao e fica salvo em um JSON pequeno dentro de
runtime_state para que a UI possa ajustar, sem depender de migracao de banco.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Iterable

from app.core.config import settings


LOCKDOWN_TRIGGER_EVENT_CHOICES = [
    "person_entered",
    "person_left",
    "person_entered_roi",
    "person_left_roi",
    "person_loitering",
    "person_crossed_line_a_to_b",
    "person_crossed_line_b_to_a",
]
LOCKDOWN_TRIGGER_EVENT_LABELS = {
    "person_entered": "Pessoa entrou na cena",
    "person_left": "Pessoa saiu da cena",
    "person_entered_roi": "Pessoa entrou na ROI",
    "person_left_roi": "Pessoa saiu da ROI",
    "person_loitering": "Permanência prolongada",
    "person_crossed_line_a_to_b": "Linha cruzada A -> B",
    "person_crossed_line_b_to_a": "Linha cruzada B -> A",
}

_policy_lock = Lock()


def _policy_path() -> Path:
    return Path(settings.lockdown_policy_file)


def _default_policy() -> dict[str, list[str]]:
    return {"allowed_trigger_events": list(LOCKDOWN_TRIGGER_EVENT_CHOICES)}


def _normalize_events(values: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        candidate = str(value).strip()
        if candidate in LOCKDOWN_TRIGGER_EVENT_CHOICES and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def load_lockdown_policy() -> dict[str, list[str]]:
    path = _policy_path()
    if not path.exists():
        return _default_policy()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_policy()

    if not isinstance(raw, dict):
        return _default_policy()

    allowed = raw.get("allowed_trigger_events")
    if not isinstance(allowed, list):
        return _default_policy()

    return {"allowed_trigger_events": _normalize_events(allowed)}


def save_lockdown_policy(allowed_trigger_events: Iterable[str] | None) -> dict[str, list[str]]:
    payload = {"allowed_trigger_events": _normalize_events(allowed_trigger_events)}
    path = _policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with _policy_lock:
        try:
            temp_path.write_text(serialized, encoding="utf-8")
            os.replace(temp_path, path)
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass

    return payload
