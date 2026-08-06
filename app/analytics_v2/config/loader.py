from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import AnalyticsConfig, config_from_mapping


def _load_yaml_if_available(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise RuntimeError(
            "YAML support not available. Use JSON or install PyYAML."
        ) from exc


def load_analytics_config(path: str | Path | None = None) -> AnalyticsConfig:
    if path is None:
        return config_from_mapping({})

    config_path = Path(path)
    if not config_path.exists():
        return config_from_mapping({})

    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() in {".json"}:
        data = json.loads(text)
    else:
        data = _load_yaml_if_available(text)
    return config_from_mapping(data)
