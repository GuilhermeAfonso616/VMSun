from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings


VALID_REVALIDATOR_MODES = {"audit", "block"}
DEFAULT_REVALIDATOR_MODE = "audit"


def _policy_path() -> Path:
    return Path(settings.runtime_state_dir) / "revalidator_policy.json"


def normalize_revalidator_mode(mode: Any) -> str:
    normalized = str(mode or "").strip().lower()
    return normalized if normalized in VALID_REVALIDATOR_MODES else DEFAULT_REVALIDATOR_MODE


def load_revalidator_policy() -> dict[str, Any]:
    mode = normalize_revalidator_mode(settings.person_revalidator_mode)
    path = _policy_path()
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                mode = normalize_revalidator_mode(payload.get("mode") or mode)
    except Exception:
        mode = normalize_revalidator_mode(settings.person_revalidator_mode)
    return {"mode": mode}


def save_revalidator_policy(mode: Any) -> dict[str, Any]:
    payload = {"mode": normalize_revalidator_mode(mode)}
    path = _policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    settings.person_revalidator_mode = payload["mode"]
    return payload

