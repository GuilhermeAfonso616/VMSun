from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..config.schema import RuleConfig
from ..events.models import AlarmEvent, EventEvidence
from ..tracking.types import Track


@dataclass(slots=True)
class RuleResult:
    triggered: bool
    rule_id: str
    event_type: str
    reason: str
    score: float = 0.0
    priority: str = "medium"
    evidence: EventEvidence = field(default_factory=EventEvidence)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuleContext:
    camera_id: int
    now: datetime
    scene: Any
    scoring: Any
    tracker_metrics: Any


class RuleBase(Protocol):
    config: RuleConfig

    def evaluate(self, track: Track, context: RuleContext) -> RuleResult:
        ...


def make_event_signature(camera_id: int, rule_id: str, track_id: int, event_type: str, zone_id: str | None = None, line_id: str | None = None) -> str:
    parts = [str(camera_id), rule_id, str(track_id), event_type]
    if zone_id:
        parts.append(zone_id)
    if line_id:
        parts.append(line_id)
    return ":".join(parts)
